"""Durable local protocol jobs with bounded chunks and lease-fenced publication."""

import json
import logging
import signal
import threading
import uuid
from datetime import timedelta

from sqlalchemy import and_, or_, select, update

from aimeet_api.core.config import Settings
from aimeet_api.db.models import Meeting, utcnow
from aimeet_api.db.session import create_engine_and_session
from aimeet_api.modules.intelligence.evidence import locate
from aimeet_api.modules.intelligence.models import MeetingBoard
from aimeet_api.modules.intelligence.provider import ProtocolProvider
from aimeet_api.modules.intelligence.schemas import Card, Revision
from aimeet_api.modules.intelligence.service import digest
from aimeet_api.modules.intelligence.timeline import reconcile
from aimeet_api.modules.rag.providers import RagError


def chunks(text, size=12000, overlap=800):
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start + size // 2, end)
            if boundary > start:
                end = boundary + 1
        yield start, text[start:end]
        if end == len(text):
            break
        start = end - overlap


def grounded(result, transcript, *, offset=0):
    for sentence in result.summary:
        if sentence.quote not in transcript:
            raise RagError("UNGROUNDED_MODEL_RESPONSE", 502)
    cards = []
    for item in result.cards:
        try:
            source = locate(transcript, item.quote, item.quote_start, offset=offset)
        except RagError as exc:
            if exc.code == "AMBIGUOUS_QUOTE":
                raise
            raise RagError("UNGROUNDED_MODEL_RESPONSE", 502) from exc
        revisions = []
        for revision in item.revisions:
            before = locate(
                transcript, revision.before.quote, revision.before.quote_start, offset=offset
            )
            after = locate(
                transcript, revision.after.quote, revision.after.quote_start, offset=offset
            )
            if (
                revision.before_value not in before.quote
                or revision.after_value not in after.quote
                or before.start_char >= after.start_char
                or item.agreement != "confirmed"
            ):
                raise RagError("INVALID_CORRECTION", 502)
            revisions.append(
                Revision(
                    field=revision.field,
                    before_value=revision.before_value,
                    after_value=revision.after_value,
                    before=before,
                    after=after,
                )
            )
        revisions.sort(key=lambda r: r.after.start_char)
        for field in {r.field for r in revisions}:
            chain = [r for r in revisions if r.field == field]
            if chain[-1].after_value != getattr(item, field) or any(
                a.after_value != b.before_value for a, b in zip(chain, chain[1:], strict=False)
            ):
                raise RagError("INVALID_CORRECTION", 502)

        # Unsupported field values remain unknown, rather than becoming fabricated facts.
        def supported(field, item=item, revisions=revisions):
            value = getattr(item, field)
            sources = [item.quote, *(r.after.quote for r in revisions if r.field == field)]
            return value if value and any(value in quote for quote in sources) else None

        assignee, due_text = supported("assignee"), supported("due_text")
        priority = (
            item.priority
            if (item.priority_evidence and item.priority_evidence in item.quote)
            else "unspecified"
        )
        cards.append(
            Card(
                id=uuid.uuid4(),
                kind=item.kind,
                title=item.title,
                description=item.description,
                assignee=assignee,
                due_text=due_text,
                priority=priority,
                quote=item.quote,
                quote_start=source.start_char,
                start_char=source.start_char,
                end_char=source.end_char,
                agreement=item.agreement,
                revisions=revisions,
                origin="ai",
            ).model_dump(mode="json")
        )
    return cards


class ProtocolWorker:
    def __init__(self, sessions, settings, provider=None):
        self.sessions = sessions
        self.settings = settings
        self.provider = provider or ProtocolProvider(settings)

    def owned(self, meeting_id, token):
        return and_(
            MeetingBoard.meeting_id == meeting_id,
            MeetingBoard.status == "running",
            MeetingBoard.lease_token == token,
            MeetingBoard.lease_until > utcnow(),
        )

    def claim(self):
        now = utcnow()
        with self.sessions() as db:
            eligible = or_(
                MeetingBoard.status == "queued",
                and_(MeetingBoard.status == "running", MeetingBoard.lease_until < now),
            )
            db.execute(
                update(MeetingBoard)
                .execution_options(synchronize_session=False)
                .where(
                    eligible,
                    MeetingBoard.attempts >= 3,
                )
                .values(status="failed", error_code="RETRY_EXHAUSTED", lease_token=None)
            )
            meeting_id = db.scalar(
                select(MeetingBoard.meeting_id)
                .where(
                    eligible,
                    MeetingBoard.attempts < 3,
                )
                .order_by(MeetingBoard.created_at)
                .limit(1)
            )
            if meeting_id is None:
                db.commit()
                return None
            token = uuid.uuid4()
            result = db.execute(
                update(MeetingBoard)
                .execution_options(synchronize_session=False)
                .where(
                    MeetingBoard.meeting_id == meeting_id,
                    eligible,
                    MeetingBoard.attempts < 3,
                )
                .values(
                    status="running",
                    lease_token=token,
                    error_code=None,
                    attempts=MeetingBoard.attempts + 1,
                    lease_until=now + timedelta(seconds=self.settings.rag_job_lease_seconds),
                )
            )
            db.commit()
            return (meeting_id, token) if result.rowcount == 1 else None

    def heartbeat(self, meeting_id, token, progress):
        with self.sessions() as db:
            result = db.execute(
                update(MeetingBoard)
                .execution_options(synchronize_session=False)
                .where(self.owned(meeting_id, token))
                .values(
                    progress=progress,
                    lease_until=utcnow() + timedelta(seconds=self.settings.rag_job_lease_seconds),
                )
            )
            db.commit()
            return result.rowcount == 1

    def publish_summary(self, meeting_id, token, summaries, progress):
        # Only grounded chunk summaries are exposed during analysis. Final cards
        # still wait for cross-chunk reconciliation, preserving edit semantics.
        with self.sessions() as db:
            changed = db.execute(
                update(MeetingBoard)
                .execution_options(synchronize_session=False)
                .where(self.owned(meeting_id, token))
                .values(summary=summaries, progress=progress, version=MeetingBoard.version + 1)
            )
            db.commit()
            return changed.rowcount == 1

    def run_once(self):
        claim = self.claim()
        if claim is None:
            return False
        meeting_id, token = claim
        try:
            with self.sessions() as db:
                meeting = db.get(Meeting, meeting_id)
                board = db.get(MeetingBoard, meeting_id)
                if not meeting or not board:
                    return True
                transcript, expected_hash = meeting.transcript, board.source_hash
            if digest(transcript) != expected_hash:
                raise RagError("SOURCE_CHANGED", 409)
            parts = list(chunks(transcript))
            cards, summaries = [], []
            for i, (offset, part) in enumerate(parts):
                if not self.heartbeat(meeting_id, token, int(i / len(parts) * 85)):
                    return True
                result = self.provider.generate(part)
                cards.extend(grounded(result, part, offset=offset))
                summaries.extend(s.model_dump() for s in result.summary)
                if not self.publish_summary(
                    meeting_id, token, summaries, int((i + 1) / len(parts) * 85)
                ):
                    return True
            # Hierarchical reduction also bounds synthesis for the longest accepted transcript.
            while len(summaries) > 5:
                reduced = []
                for start in range(0, len(summaries), 10):
                    if not self.heartbeat(meeting_id, token, 90):
                        return True
                    result = self.provider.generate(
                        json.dumps(summaries[start : start + 10], ensure_ascii=False),
                        synthesis=True,
                    )
                    grounded(result, transcript)
                    reduced.extend(s.model_dump() for s in result.summary)
                summaries = reduced
            # Overlap can yield the same finding twice: merge only equal kind and quote.
            unique = {}
            for card in cards:
                key = (card["kind"], card["quote"], card["title"].strip().casefold())
                unique.setdefault(key, card)

            def checkpoint():
                if not self.heartbeat(meeting_id, token, 92):
                    raise RagError("LEASE_LOST", 409)

            final_cards, corrected = reconcile(
                list(unique.values()),
                transcript,
                self.provider,
                checkpoint,
            )
            if corrected:
                # Rebuild the summary from current findings, not superseded chunk summaries.
                summaries = []
                for start in range(0, len(final_cards), 8):
                    checkpoint()
                    result = self.provider.generate(
                        json.dumps(
                            [
                                {
                                    k: c[k]
                                    for k in ("title", "assignee", "due_text", "agreement", "quote")
                                }
                                for c in final_cards[start : start + 8]
                            ],
                            ensure_ascii=False,
                        ),
                        synthesis=True,
                    )
                    grounded(result, transcript)
                    summaries.extend(s.model_dump() for s in result.summary)
                while len(summaries) > 5:
                    reduced = []
                    for start in range(0, len(summaries), 10):
                        checkpoint()
                        result = self.provider.generate(
                            json.dumps(summaries[start : start + 10], ensure_ascii=False),
                            synthesis=True,
                        )
                        grounded(result, transcript)
                        reduced.extend(s.model_dump() for s in result.summary)
                    summaries = reduced
            if not self.heartbeat(meeting_id, token, 95):
                return True
            for _ in range(10):
                with self.sessions() as db:
                    board = db.get(MeetingBoard, meeting_id)
                    meeting = db.get(Meeting, meeting_id)
                    if not board or not meeting:
                        return True
                    if digest(meeting.transcript) != expected_hash:
                        raise RagError("SOURCE_CHANGED", 409)
                    merged = [*board.cards, *final_cards]
                    if len(merged) > 1500:
                        raise RagError("BOARD_FULL", 422)
                    published = db.execute(
                        update(MeetingBoard)
                        .execution_options(synchronize_session=False)
                        .where(
                            self.owned(meeting_id, token),
                            MeetingBoard.version == board.version,
                        )
                        .values(
                            cards=merged,
                            summary=summaries,
                            status="ready",
                            progress=100,
                            version=MeetingBoard.version + 1,
                            lease_token=None,
                            lease_until=None,
                        )
                    )
                    if published.rowcount == 1:
                        db.commit()
                        return True
                    db.rollback()
            raise RagError("BOARD_CONFLICT", 409)
        except RagError as exc:
            self.fail(meeting_id, token, exc.code)
        except Exception:
            logging.error("Protocol generation failed meeting=%s", meeting_id)
            self.fail(meeting_id, token, "GENERATION_FAILED")
        return True

    def fail(self, meeting_id, token, code):
        with self.sessions() as db:
            db.execute(
                update(MeetingBoard)
                .execution_options(synchronize_session=False)
                .where(self.owned(meeting_id, token))
                .values(
                    status="failed",
                    error_code=code,
                    lease_until=None,
                    lease_token=None,
                )
            )
            db.commit()


def main():
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    settings = Settings()
    engine, sessions = create_engine_and_session(settings)
    worker = ProtocolWorker(sessions, settings)
    try:
        while not stop.is_set():
            try:
                worked = worker.run_once()
            except Exception:
                logging.error("Protocol worker unavailable; retrying")
                worked = False
            if not worked:
                stop.wait(2)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
