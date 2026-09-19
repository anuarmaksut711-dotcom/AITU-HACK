"""Independent speech and text-analysis loops; speech weights load on demand and unload while idle."""

import argparse
import logging
import signal
import time
import uuid
from datetime import UTC, timedelta

from sqlalchemy import and_, func, or_, select, update

from aimeet_api.core.config import Settings
from aimeet_api.db.models import Meeting, utcnow
from aimeet_api.db.session import create_engine_and_session
from aimeet_api.modules.live.analysis import generate_insights
from aimeet_api.modules.live.archive import sync_archived_board
from aimeet_api.modules.live.audio import chunk_path
from aimeet_api.modules.live.models import LiveChunk, LiveParticipant, LiveRecordingStream, LiveRoom
from aimeet_api.modules.live.recordings import process_recording_once
from aimeet_api.modules.live.security import media_room_request
from aimeet_api.modules.rag.providers import RagError
from aimeet_api.modules.transcription.engine import model_metadata

logger = logging.getLogger("soyle.live")


def claim_audio(factory):
    now = utcnow()
    eligible = or_(
        LiveChunk.status == "queued",
        and_(LiveChunk.status == "running", LiveChunk.lease_until < now),
    )
    with factory() as db:
        db.execute(
            update(LiveChunk)
            .where(eligible, LiveChunk.attempts >= 3)
            .values(status="failed", error_code="STT_INTERRUPTED")
        )
        chunk = db.scalar(
            select(LiveChunk)
            .where(eligible, LiveChunk.attempts < 3)
            .order_by(LiveChunk.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if chunk is None:
            db.commit()
            return None
        chunk.status = "running"
        chunk.attempts += 1
        chunk.lease = uuid.uuid4()
        chunk.lease_until = now + timedelta(seconds=120)
        db.commit()
        return chunk


def process_audio(factory, settings, chunk, model):
    import numpy as np

    path = chunk_path(settings, chunk.id)
    with factory() as db:
        room = db.get(LiveRoom, chunk.room_id)
        if room is None:
            path.unlink(missing_ok=True)
            return
        language = room.language
    error = None
    text = ""
    try:
        if not path.is_file() or path.stat().st_size > 300_000:
            raise ValueError("Invalid PCM source")
        audio = np.frombuffer(path.read_bytes(), dtype="<i2").astype(np.float32) / 32768
        segments, _ = model.transcribe(
            audio,
            language=None if language == "auto" else language,
            task="transcribe",
            multilingual=True,
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        if len(text) > 2000:
            raise ValueError("Unbounded transcript")
    except Exception:
        error = "STT_FAILED"
    with factory() as db:
        changed = db.execute(
            update(LiveChunk)
            .where(
                LiveChunk.id == chunk.id,
                LiveChunk.status == "running",
                LiveChunk.lease == chunk.lease,
                LiveChunk.lease_until > utcnow(),
            )
            .values(
                status="failed" if error else "done",
                text=text if not error else "",
                error_code=error,
                lease=None,
                lease_until=None,
            )
        )
        if changed.rowcount == 1:
            db.execute(
                update(LiveRoom)
                .where(LiveRoom.id == chunk.room_id)
                .values(transcript_revision=LiveRoom.transcript_revision + 1, audio_error=error)
            )
        db.commit()
    # Retain original speech for cited playback; it is removed with the meeting.
    # Failed/non-speech chunks have no corresponding transcript excerpt.
    if changed.rowcount == 1 and (error or not text):
        path.unlink(missing_ok=True)


def claim_analysis(factory, settings):
    now = utcnow()
    with factory() as db:
        room = db.scalar(
            select(LiveRoom)
            .where(
                LiveRoom.transcript_revision > LiveRoom.analysis_through,
                or_(LiveRoom.analysis_lease_until.is_(None), LiveRoom.analysis_lease_until < now),
                or_(
                    LiveRoom.analysis_updated_at.is_(None),
                    LiveRoom.analysis_updated_at
                    < now - timedelta(seconds=settings.live_analysis_interval),
                ),
                or_(
                    LiveRoom.analysis_error.is_(None),
                    LiveRoom.analysis_error.not_in(
                        ["OPENAI_KEY_REQUIRED", "PROVIDER_REJECTED", "CLOUD_DISABLED"]
                    ),
                ),
            )
            .order_by(LiveRoom.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if room is None:
            return None
        token = uuid.uuid4()
        room.analysis_lease = token
        room.analysis_lease_until = now + timedelta(seconds=90)
        room.analysis_status = "processing"
        revision = room.transcript_revision
        db.commit()
        return room.id, token, revision


def process_analysis(factory, settings, claim, generator=generate_insights):
    room_id, token, revision = claim
    with factory() as db:
        room = db.get(LiveRoom, room_id)
        if room is None:
            return
        previous = room.insights
        cited = {uuid.UUID(i) for note in previous for i in note["source_ids"]}
        recent = db.execute(
            select(LiveChunk, LiveParticipant.name)
            .join(LiveParticipant)
            .where(LiveChunk.room_id == room_id, LiveChunk.status == "done", LiveChunk.text != "")
            .order_by(LiveChunk.start.desc())
            .limit(80)
        ).all()
        cited_rows = (
            db.execute(
                select(LiveChunk, LiveParticipant.name)
                .join(LiveParticipant)
                .where(LiveChunk.room_id == room_id, LiveChunk.id.in_(cited))
            ).all()
            if cited
            else []
        )
        rows = {c.id: (c, name) for c, name in [*cited_rows, *recent]}
        utterances = [
            {"id": str(c.id), "speaker": name, "text": c.text}
            for c, name in sorted(rows.values(), key=lambda row: row[0].start)
        ]
        # Retain old cited evidence while bounding each provider request.
        if sum(len(u["text"]) for u in utterances) > 30000:
            utterances = utterances[-60:]
    try:
        insights = generator(settings, utterances, previous) if utterances else []
        error = None
    except RagError as exc:
        insights, error = None, exc.code
    except Exception:
        insights, error = None, "ANALYSIS_FAILED"
    with factory() as db:
        values = {
            "analysis_status": "failed" if error else "ready",
            "analysis_error": error,
            "analysis_updated_at": utcnow(),
            "analysis_through": revision,  # Retry soft failures only after new speech.
            "analysis_lease": None,
            "analysis_lease_until": None,
        }
        if insights is not None:
            values.update(insights=insights, analysis_through=revision)
        published = db.execute(
            update(LiveRoom)
            .where(
                LiveRoom.id == room_id,
                LiveRoom.analysis_lease == token,
                LiveRoom.analysis_lease_until > utcnow(),
            )
            .values(**values)
        )
        if published.rowcount == 1:
            sync_archived_board(db, db.get(LiveRoom, room_id))
        db.commit()


def finalize_rooms(factory, settings):
    with factory() as db:
        expired = db.scalars(
            select(LiveRoom).where(
                LiveRoom.status == "active",
                LiveRoom.created_at < utcnow() - timedelta(minutes=settings.live_max_minutes),
            )
        ).all()
        for room in expired:
            try:
                media_room_request(settings, "DeleteRoom", room.id)
            except Exception:
                continue
            room.status = "ending"
            room.ended_at = utcnow()
        db.commit()
    with factory() as db:
        rooms = db.scalars(
            select(LiveRoom)
            .where(LiveRoom.status == "ending", LiveRoom.ended_at < utcnow() - timedelta(seconds=5))
            .with_for_update(skip_locked=True)
        ).all()
        for room in rooms:
            streams = list(
                db.scalars(
                    select(LiveRecordingStream).where(
                        LiveRecordingStream.room_id == room.id,
                    )
                )
            )
            if any(stream.closed_at is None for stream in streams) and (
                room.ended_at.replace(tzinfo=UTC) > utcnow() - timedelta(seconds=10)
            ):
                continue
            pending = db.scalar(
                select(func.count())
                .select_from(LiveChunk)
                .where(LiveChunk.room_id == room.id, LiveChunk.status.in_(["queued", "running"]))
            )
            if pending:
                continue
            rows = db.execute(
                select(LiveChunk, LiveParticipant.name)
                .join(LiveParticipant)
                .where(
                    LiveChunk.room_id == room.id, LiveChunk.status == "done", LiveChunk.text != ""
                )
                .order_by(LiveChunk.start, LiveChunk.id)
            ).all()
            transcript = "\n".join(
                f"[{int(c.start) // 60:02}:{int(c.start) % 60:02}] {name}: {c.text}"
                for c, name in rows
            )
            if (transcript or streams) and room.meeting_id is None:
                meeting = Meeting(
                    workspace_id=room.workspace_id,
                    created_by=room.created_by,
                    title=room.title,
                    language=room.language,
                    status="transcribed",
                    source_type="text",
                    transcript=transcript,
                    transcript_length=len(transcript),
                    segments=[
                        {"start": c.start, "end": c.end, "text": f"{name}: {c.text}"}
                        for c, name in rows
                    ],
                )
                db.add(meeting)
                db.flush()
                room.meeting_id = meeting.id
            room.status = "ended"
            if not transcript:
                # A recorded call without recognized speech has no analysis work;
                # it must not remain in "preparing outcomes" forever.
                room.analysis_status, room.analysis_error = "ready", None
                room.analysis_through = room.transcript_revision
            if streams and room.recording_status != "failed":
                room.recording_status = "queued"
            sync_archived_board(db, room)
        db.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["speech", "analysis", "recording"])
    mode = parser.parse_args().mode
    settings = Settings()
    engine, factory = create_engine_and_session(settings)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    stopped = False
    model = None
    last_audio_at = 0.0

    def stop(_signal, _frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopped:
        try:
            if mode == "speech":
                chunk = claim_audio(factory)
                if chunk is None:
                    if model is not None and time.monotonic() - last_audio_at >= 60:
                        model.model.unload_model()
                        model = None
                    time.sleep(0.3)
                    continue
                if model is None:
                    model_metadata(settings.stt_model_path)
                    import onnxruntime

                    onnxruntime.disable_telemetry_events()
                    from faster_whisper import WhisperModel

                    model = WhisperModel(
                        str(settings.stt_model_path),
                        device=settings.stt_device,
                        compute_type=settings.stt_compute_type,
                        cpu_threads=settings.stt_cpu_threads,
                        local_files_only=True,
                    )
                process_audio(factory, settings, chunk, model)
                last_audio_at = time.monotonic()
            elif mode == "recording":
                if not process_recording_once(factory, settings):
                    time.sleep(1)
            else:
                # This process has media-service access; the speech worker stays on the
                # internal data network so audio cannot leave through an external API.
                finalize_rooms(factory, settings)
                claim = claim_analysis(factory, settings)
                if claim:
                    process_analysis(factory, settings, claim)
                else:
                    time.sleep(1)
        except Exception as exc:
            logger.error("live_%s_failed exception_type=%s", mode, type(exc).__name__)
            time.sleep(3)
    engine.dispose()


if __name__ == "__main__":
    main()
