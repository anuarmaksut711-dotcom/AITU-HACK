"""Record all PCM, including pauses; mix original participant streams on the room timeline."""

import hashlib
import os
import uuid
import wave
from contextlib import ExitStack
from datetime import UTC, timedelta

from sqlalchemy import and_, case, or_, select, update

from aimeet_api.db.models import Meeting, utcnow
from aimeet_api.modules.live.models import LiveRecordingStream, LiveRoom
from aimeet_api.modules.transcription.storage import audio_path

RATE = 16000


def recording_path(settings, stream_id):
    return settings.audio_dir / "live-recordings" / f"{stream_id}.pcm"


def begin_recording(factory, settings, room_id, participant_id, stream_id, start):
    path = recording_path(settings, stream_id)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    stream = os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb", buffering=0
    )
    try:
        with factory() as db:
            db.add(
                LiveRecordingStream(
                    id=stream_id, room_id=room_id, participant_id=participant_id, start=start
                )
            )
            db.execute(
                update(LiveRoom)
                .where(LiveRoom.id == room_id)
                .values(
                    recording_status=case(
                        (LiveRoom.recording_status == "failed", "failed"), else_="recording"
                    )
                )
            )
            db.commit()
    except BaseException:
        stream.close()
        path.unlink(missing_ok=True)
        raise
    return stream


def close_recording(factory, stream_id, stream):
    if stream is not None:
        stream.close()
    with factory() as db:
        db.execute(
            update(LiveRecordingStream)
            .where(LiveRecordingStream.id == stream_id)
            .values(closed_at=utcnow())
        )
        db.commit()


def mix_recording(settings, streams, target, *, duration_seconds=0, heartbeat=lambda: True):
    import numpy as np

    sources = []
    total = round(duration_seconds * RATE)
    max_frames = (settings.live_max_minutes * 60 + 15) * RATE
    for row in streams:
        path = recording_path(settings, row.id)
        size = path.stat().st_size
        if size % 2:
            raise ValueError("Invalid PCM recording")
        start = round(row.start * RATE)
        end = start + size // 2
        if start < 0 or end > max_frames:
            raise ValueError("Recording exceeds room limit")
        sources.append((path, start, end))
        total = max(total, end)
    if not sources or total <= 0 or total > max_frames:
        raise ValueError("No complete recording")
    target.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        handles = [
            (stack.enter_context(path.open("rb")), start, end) for path, start, end in sources
        ]
        output = stack.enter_context(target.open("xb"))
        os.chmod(target, 0o600)
        wav = stack.enter_context(wave.open(output, "wb"))
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        # Fixed memory independent of recording length. Silence and cross-talk
        # stay on their original timeline instead of concatenating speech snippets.
        for position in range(0, total, RATE * 5):
            if position % (RATE * 30) == 0 and not heartbeat():
                raise RuntimeError("Recording lease lost")
            end = min(total, position + RATE * 5)
            mixed = np.zeros(end - position, dtype=np.int32)
            for handle, source_start, source_end in handles:
                left, right = max(position, source_start), min(end, source_end)
                if left >= right:
                    continue
                handle.seek((left - source_start) * 2)
                raw = handle.read((right - left) * 2)
                if len(raw) != (right - left) * 2:
                    raise ValueError("Recording source changed")
                mixed[left - position : right - position] += np.frombuffer(raw, dtype="<i2")
            wav.writeframesraw(np.clip(mixed, -32768, 32767).astype("<i2").tobytes())
        wav.close()
        output.flush()
        os.fsync(output.fileno())
    digest = hashlib.sha256()
    with target.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return target.stat().st_size, digest.hexdigest()


def process_recording_once(factory, settings):
    now, token = utcnow(), uuid.uuid4()
    eligible = or_(
        LiveRoom.recording_status == "queued",
        and_(
            LiveRoom.recording_status == "processing",
            LiveRoom.recording_lease_until < now,
        ),
    )
    with factory() as db:
        room_id = db.scalar(
            select(LiveRoom.id)
            .where(
                LiveRoom.status == "ended",
                LiveRoom.meeting_id.is_not(None),
                eligible,
            )
            .order_by(LiveRoom.created_at)
            .limit(1)
        )
        if room_id is None:
            return False
        claimed = db.execute(
            update(LiveRoom)
            .where(LiveRoom.id == room_id, eligible)
            .values(
                recording_status="processing",
                recording_lease=token,
                recording_lease_until=now + timedelta(minutes=5),
                recording_attempts=LiveRoom.recording_attempts + 1,
            )
        )
        db.commit()
        if claimed.rowcount != 1:
            return False
        room = db.get(LiveRoom, room_id)
        meeting_id, attempts = room.meeting_id, room.recording_attempts

        def aware(value):
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value

        duration = max(0, (aware(room.ended_at) - aware(room.created_at)).total_seconds())
        streams = list(
            db.scalars(select(LiveRecordingStream).where(LiveRecordingStream.room_id == room_id))
        )
        for stream in streams:
            db.expunge(stream)
    target = audio_path(settings, meeting_id)
    temporary = target.with_name(f"{meeting_id}.{token}.recording")

    def ownership():
        return (
            LiveRoom.id == room_id,
            LiveRoom.recording_lease == token,
            LiveRoom.recording_status == "processing",
            LiveRoom.recording_lease_until > utcnow(),
        )

    def heartbeat():
        with factory() as db:
            result = db.execute(
                update(LiveRoom)
                .where(*ownership())
                .values(
                    recording_lease_until=utcnow() + timedelta(minutes=5),
                )
            )
            db.commit()
            return result.rowcount == 1

    try:
        if attempts > 3:
            raise ValueError("Recording attempts exhausted")
        size, sha256 = mix_recording(
            settings, streams, temporary, duration_seconds=duration, heartbeat=heartbeat
        )
        with factory() as db:
            room = db.scalar(select(LiveRoom).where(*ownership()).with_for_update())
            meeting = db.get(Meeting, meeting_id)
            if room is None or meeting is None or room.meeting_id != meeting_id:
                return True
            # Never replace an independently attached recording.
            if meeting.source_type == "audio" and meeting.audio_filename:
                raise ValueError("Recording already attached")
            temporary.replace(target)
            meeting.source_type, meeting.audio_filename = "audio", "Беседа.wav"
            meeting.audio_bytes, meeting.audio_sha256 = size, sha256
            room.recording_status, room.recording_error = "ready", None
            room.recording_lease = room.recording_lease_until = None
            db.commit()
    except Exception:
        with factory() as db:
            db.execute(
                update(LiveRoom)
                .where(*ownership())
                .values(
                    recording_status="failed",
                    recording_error="RECORDING_UNAVAILABLE",
                    recording_lease=None,
                    recording_lease_until=None,
                )
            )
            db.commit()
    finally:
        temporary.unlink(missing_ok=True)
    return True
