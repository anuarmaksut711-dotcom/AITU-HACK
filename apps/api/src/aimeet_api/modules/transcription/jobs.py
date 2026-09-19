import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, or_, select, update

from aimeet_api.core.config import Settings
from aimeet_api.db.models import Meeting, TranscriptionJob, utcnow
from aimeet_api.modules.intelligence.models import MeetingBoard
from aimeet_api.modules.intelligence.service import digest


@dataclass(frozen=True)
class Claim:
    id: uuid.UUID
    meeting_id: uuid.UUID
    token: uuid.UUID


def claim_next(factory, settings: Settings) -> Claim | None:
    now = utcnow()
    eligible = or_(
        TranscriptionJob.status == "queued",
        and_(
            TranscriptionJob.status == "running",
            TranscriptionJob.lease_until < now,
        ),
    )
    with factory() as db:
        # Exhausted jobs must not prevent the next queued recording from progressing.
        db.execute(
            update(TranscriptionJob)
            .execution_options(synchronize_session=False)
            .where(
                eligible,
                TranscriptionJob.attempts >= settings.job_max_attempts,
            )
            .values(
                status="failed", error_code="worker_interrupted", lease_token=None, lease_until=None
            )
        )
        job = db.scalar(
            select(TranscriptionJob)
            .where(
                eligible,
                TranscriptionJob.attempts < settings.job_max_attempts,
            )
            .order_by(TranscriptionJob.created_at, TranscriptionJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            db.commit()
            return None
        token = uuid.uuid4()
        result = db.execute(
            update(TranscriptionJob)
            .execution_options(synchronize_session=False)
            .where(
                TranscriptionJob.id == job.id,
                eligible,
                TranscriptionJob.attempts == job.attempts,
            )
            .values(
                status="running",
                attempts=TranscriptionJob.attempts + 1,
                progress=0,
                lease_token=token,
                lease_until=now + timedelta(seconds=settings.job_lease_seconds),
                error_code=None,
                detected_language=None,
                duration_seconds=None,
            )
        )
        if result.rowcount == 1:
            db.execute(
                update(Meeting)
                .where(Meeting.id == job.meeting_id)
                .values(transcript="", transcript_length=0, segments=None, status="draft")
            )
        db.commit()
        return Claim(job.id, job.meeting_id, token) if result.rowcount == 1 else None


def owns(claim: Claim):
    return and_(
        TranscriptionJob.id == claim.id,
        TranscriptionJob.status == "running",
        TranscriptionJob.lease_token == claim.token,
        TranscriptionJob.lease_until > utcnow(),
    )


def heartbeat(
    factory, settings: Settings, claim: Claim, progress: int, *, partial: dict | None = None
) -> bool:
    with factory() as db:
        values = {
            "lease_until": utcnow() + timedelta(seconds=settings.job_lease_seconds),
            "progress": min(99, max(0, progress)),
        }
        if partial is not None:
            values.update(
                detected_language=partial.get("detected_language"),
                duration_seconds=partial.get("duration_seconds"),
            )
        result = db.execute(
            update(TranscriptionJob)
            .execution_options(synchronize_session=False)
            .where(owns(claim))
            .values(**values)
        )
        if result.rowcount != 1:
            db.rollback()
            return False
        if partial is not None:
            db.execute(
                update(Meeting)
                .where(Meeting.id == claim.meeting_id)
                .values(
                    transcript=partial["transcript"],
                    transcript_length=len(partial["transcript"]),
                    segments=partial["segments"],
                )
            )
        db.commit()
        return result.rowcount == 1


def finish(factory, claim: Claim, *, result: dict | None = None, error: str | None = None) -> bool:
    with factory() as db:
        values = {
            "status": "failed" if error else "succeeded",
            "error_code": error,
            "lease_token": None,
            "lease_until": None,
        }
        if result is not None:
            values.update(
                progress=100,
                detected_language=result["detected_language"],
                duration_seconds=result["duration_seconds"],
            )
        changed = db.execute(
            update(TranscriptionJob)
            .execution_options(synchronize_session=False)
            .where(owns(claim))
            .values(**values)
        )
        if changed.rowcount != 1:
            db.rollback()
            return False
        if result is not None:
            db.execute(
                update(Meeting)
                .where(Meeting.id == claim.meeting_id)
                .values(
                    transcript=result["transcript"],
                    transcript_length=len(result["transcript"]),
                    segments=result["segments"],
                    status="transcribed",
                )
            )
            # Queue analysis in the same transaction as the final transcript. It must
            # continue even when the browser closes before recognition completes.
            board = db.get(MeetingBoard, claim.meeting_id)
            if board is None:
                db.add(
                    MeetingBoard(
                        meeting_id=claim.meeting_id,
                        status="queued",
                        source_hash=digest(result["transcript"]),
                    )
                )
            elif board.status == "idle":
                db.execute(
                    update(MeetingBoard)
                    .where(
                        MeetingBoard.meeting_id == claim.meeting_id,
                        MeetingBoard.status == "idle",
                    )
                    .values(status="queued", source_hash=digest(result["transcript"]))
                )
        db.commit()
        return True
