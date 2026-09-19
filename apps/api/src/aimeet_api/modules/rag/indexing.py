"""Durable jobs. Provider IO occurs outside transactions; publication uses lease fencing."""

import logging
import uuid
from datetime import timedelta

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from aimeet_api.core.config import Settings
from aimeet_api.db.models import Meeting, utcnow
from aimeet_api.modules.rag.chunking import build_graph, embedding_profile, source_hash
from aimeet_api.modules.rag.models import RagIndex, RagNode
from aimeet_api.modules.rag.providers import Providers, RagError

logger = logging.getLogger(__name__)


def enqueue(db: Session, meeting: Meeting, settings: Settings) -> RagIndex:
    meeting_id = meeting.id
    profile = embedding_profile(settings)
    digest = source_hash(meeting.transcript)
    row = db.scalar(
        select(RagIndex).where(
            RagIndex.meeting_id == meeting_id,
            RagIndex.profile == profile,
            RagIndex.source_hash == digest,
        )
    )
    if row:
        if row.status == "failed":
            db.execute(
                update(RagIndex)
                .execution_options(synchronize_session=False)
                .where(
                    RagIndex.id == row.id,
                    RagIndex.status == "failed",
                )
                .values(status="queued", attempts=0, error_code=None, lease_until=None)
            )
            db.commit()
            db.refresh(row)
        return row
    row = RagIndex(meeting_id=meeting_id, profile=profile, source_hash=digest)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        row = db.scalar(
            select(RagIndex).where(
                RagIndex.meeting_id == meeting_id,
                RagIndex.profile == profile,
                RagIndex.source_hash == digest,
            )
        )
        if row is None:
            raise RagError("MEETING_DELETED", 404) from None
    return row


class IndexWorker:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        settings: Settings,
        providers: Providers | None = None,
    ):
        self.sessions = sessions
        self.settings = settings
        self.providers = providers or Providers(settings)

    def claim(self) -> tuple[uuid.UUID, uuid.UUID] | None:
        now = utcnow()
        with self.sessions() as db:
            eligible = and_(
                RagIndex.profile == embedding_profile(self.settings),
                or_(
                    and_(
                        RagIndex.status == "queued",
                        or_(RagIndex.lease_until.is_(None), RagIndex.lease_until < now),
                    ),
                    and_(RagIndex.status == "running", RagIndex.lease_until < now),
                ),
            )
            db.execute(
                update(RagIndex)
                .execution_options(synchronize_session=False)
                .where(
                    eligible,
                    RagIndex.attempts >= self.settings.rag_job_max_attempts,
                )
                .values(status="failed", error_code="RETRY_EXHAUSTED", lease_token=None)
            )
            row_id = db.scalar(
                select(RagIndex.id)
                .where(
                    eligible,
                    RagIndex.attempts < self.settings.rag_job_max_attempts,
                )
                .order_by(RagIndex.created_at)
                .limit(1)
            )
            if row_id is None:
                db.commit()
                return None
            token = uuid.uuid4()
            claimed = db.execute(
                update(RagIndex)
                .execution_options(synchronize_session=False)
                .where(
                    RagIndex.id == row_id,
                    eligible,
                    RagIndex.attempts < self.settings.rag_job_max_attempts,
                )
                .values(
                    status="running",
                    lease_token=token,
                    lease_until=now + timedelta(seconds=self.settings.rag_job_lease_seconds),
                    attempts=RagIndex.attempts + 1,
                    error_code=None,
                )
            )
            db.commit()
            return (row_id, token) if claimed.rowcount == 1 else None

    def _owned(self, row_id, token):
        return and_(
            RagIndex.id == row_id,
            RagIndex.status == "running",
            RagIndex.lease_token == token,
            RagIndex.lease_until > utcnow(),
        )

    def heartbeat(self, row_id, token) -> bool:
        with self.sessions() as db:
            result = db.execute(
                update(RagIndex)
                .execution_options(synchronize_session=False)
                .where(self._owned(row_id, token))
                .values(
                    lease_until=utcnow() + timedelta(seconds=self.settings.rag_job_lease_seconds),
                )
            )
            db.commit()
            return result.rowcount == 1

    def run_once(self) -> bool:
        claim = self.claim()
        if claim is None:
            return False
        row_id, token = claim
        try:
            with self.sessions() as db:
                row = db.get(RagIndex, row_id)
                if row is None:
                    return True
                meeting = db.get(Meeting, row.meeting_id)
                if meeting is None:
                    return True
                transcript, expected_hash = meeting.transcript, row.source_hash
            if source_hash(transcript) != expected_hash:
                raise RagError("SOURCE_CHANGED", 409)
            parents, children, edges = build_graph(row_id, transcript, self.settings)
            # Both levels carry embeddings: parent hits recover broad thematic questions.
            nodes = parents + children
            for start in range(0, len(nodes), 16):
                if not self.heartbeat(row_id, token):
                    return True
                batch = nodes[start : start + 16]
                vectors = self.providers.embed([node.text for node in batch])
                for node, vector in zip(batch, vectors, strict=True):
                    node.embedding = vector
            with self.sessions() as db:
                published = db.execute(
                    update(RagIndex)
                    .execution_options(synchronize_session=False)
                    .where(self._owned(row_id, token))
                    .values(
                        status="ready",
                        node_count=len(nodes),
                        completed_at=utcnow(),
                        lease_token=None,
                        lease_until=None,
                        error_code=None,
                    )
                )
                if published.rowcount != 1:
                    db.rollback()
                    return True
                db.execute(delete(RagNode).where(RagNode.index_id == row_id))
                db.add_all(parents)
                db.flush()
                db.add_all(children)
                db.flush()
                db.add_all(edges)
                db.commit()
        except RagError as exc:
            self.fail(row_id, token, exc)
        except Exception:
            # Never log exception bodies: they can contain provider content or SQL parameters.
            logger.error("RAG indexing failed index=%s code=INDEXING_FAILED", row_id)
            self.fail(row_id, token, RagError("INDEXING_FAILED", retryable=True))
        return True

    def fail(self, row_id, token, error: RagError):
        with self.sessions() as db:
            row = db.get(RagIndex, row_id)
            if row is None:
                return
            retry = error.retryable and row.attempts < self.settings.rag_job_max_attempts
            db.execute(
                update(RagIndex)
                .execution_options(synchronize_session=False)
                .where(self._owned(row_id, token))
                .values(
                    status="queued" if retry else "failed",
                    error_code=error.code,
                    lease_token=None,
                    lease_until=utcnow() + timedelta(seconds=10 * 2**row.attempts)
                    if retry
                    else None,
                )
            )
            db.commit()
