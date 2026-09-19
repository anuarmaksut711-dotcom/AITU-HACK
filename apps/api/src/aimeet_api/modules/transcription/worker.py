"""PostgreSQL leased jobs; cancellable, time-bounded, isolated model execution."""

import logging
import multiprocessing
import signal
import time
from pathlib import Path

from sqlalchemy import select, update

from aimeet_api.core.config import Settings
from aimeet_api.db.models import Meeting, TranscriptionJob
from aimeet_api.db.session import create_engine_and_session
from aimeet_api.modules.transcription.engine import (
    TranscriptionError,
    child_main,
    model_metadata,
)
from aimeet_api.modules.transcription.jobs import claim_next, finish, heartbeat, owns
from aimeet_api.modules.transcription.storage import audio_path

logger = logging.getLogger("aimeet.worker")


def run_job(factory, settings, claim, stop=lambda: False):
    with factory() as db:
        meeting = db.scalar(select(Meeting).where(Meeting.id == claim.meeting_id))
        if meeting is None:
            return
        try:
            config = {
                "engine": "faster-whisper",
                "pipeline_version": 2,
                "model_path": str(settings.stt_model_path.resolve()),
                "model": model_metadata(settings.stt_model_path),
                "device": settings.stt_device,
                "compute_type": settings.stt_compute_type,
                "cpu_threads": settings.stt_cpu_threads,
                "diarization_enabled": settings.stt_diarization_enabled,
                "diarization_model_path": str(settings.stt_diarization_model_path.resolve()),
                "diarization_threshold": settings.stt_diarization_threshold,
                "num_speakers": (meeting.transcription.config or {}).get("num_speakers"),
                "language": meeting.language,
                "extension": Path(meeting.audio_filename).suffix.lower(),
                "max_audio_seconds": settings.max_audio_seconds,
                "input_sha256": meeting.audio_sha256,
            }
        except TranscriptionError as exc:
            finish(factory, claim, error=exc.code)
            return
        changed = db.execute(
            update(TranscriptionJob)
            .execution_options(synchronize_session=False)
            .where(owns(claim))
            .values(config=config)
        )
        db.commit()
        if changed.rowcount != 1:
            return
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(
        target=child_main,
        args=(
            writer,
            str(audio_path(settings, claim.meeting_id).resolve()),
            config,
        ),
    )
    process.start()
    writer.close()
    started = time.monotonic()
    last_heartbeat = 0.0
    progress = 0
    partial = {"transcript": "", "segments": []}
    dirty = False
    try:
        while not stop():
            if time.monotonic() - started > settings.stt_timeout_seconds:
                finish(factory, claim, error="processing_timeout")
                return
            if time.monotonic() - last_heartbeat >= 2:
                if not heartbeat(
                    factory, settings, claim, progress, partial=partial if dirty else None
                ):
                    return  # Cancelled, deleted, or another worker owns the replacement lease.
                dirty = False
                last_heartbeat = time.monotonic()
            if reader.poll(0.25):
                try:
                    message = reader.recv()
                except EOFError:
                    finish(factory, claim, error="processing_failed")
                    return
                if "result" in message:
                    finish(factory, claim, result=message["result"])
                    return
                if "error" in message:
                    heartbeat(factory, settings, claim, progress, partial=partial)
                    finish(factory, claim, error=message["error"])
                    return
                progress = message.get("progress", progress)
                if "segment" in message:
                    partial["segments"].append(message["segment"])
                    partial["transcript"] = "\n".join(row["text"] for row in partial["segments"])
                    dirty = True
                for key in ("detected_language", "duration_seconds"):
                    if key in message:
                        partial[key] = message[key]
                        dirty = True
            elif not process.is_alive():
                finish(factory, claim, error="processing_failed")
                return
        # Leave a stopped job leased. The next worker recovers it after lease expiry.
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        reader.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = Settings()
    engine, factory = create_engine_and_session(settings)
    stopped = False

    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopped:
            try:
                claim = claim_next(factory, settings)
                if claim is None:
                    time.sleep(1)
                    continue
                logger.info("transcription_started job_id=%s", claim.id)
                run_job(factory, settings, claim, lambda: stopped)
            except Exception as exc:
                logger.error("worker_iteration_failed exception_type=%s", type(exc).__name__)
                time.sleep(2)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
