"""Participant-scoped PCM ingress; no remote speech APIs and no unbounded audio buffering."""

import os
import time
import uuid
from collections import deque
from datetime import UTC, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select, update

from aimeet_api.db.models import utcnow
from aimeet_api.modules.live.models import LiveChunk, LiveParticipant, LiveRoom
from aimeet_api.modules.live.recordings import begin_recording, close_recording
from aimeet_api.modules.live.router import check_host_session
from aimeet_api.modules.live.security import decode_member

router = APIRouter(prefix="/live", tags=["Live audio"])
FRAME_BYTES = 960  # 30ms, signed 16-bit mono PCM at 16kHz


class SpeechBuffer:
    def __init__(self, vad=None):
        if vad is None:
            import webrtcvad

            vad = webrtcvad.Vad(2)
        self.vad = vad
        self.pending = bytearray()
        self.preroll = deque(maxlen=8)
        self.speech = bytearray()
        self.position = 0
        self.start = 0
        self.voiced = 0
        self.silence = 0

    def feed(self, data: bytes):
        self.pending.extend(data)
        result = []
        while len(self.pending) >= FRAME_BYTES:
            frame = bytes(self.pending[:FRAME_BYTES])
            del self.pending[:FRAME_BYTES]
            voiced = self.vad.is_speech(frame, 16000)
            if voiced and not self.speech:
                self.start = max(0, self.position - len(self.preroll) * 480)
                self.speech.extend(b"".join(self.preroll))
                self.preroll.clear()
            if self.speech or voiced:
                self.speech.extend(frame)
                self.voiced += int(voiced)
                self.silence = 0 if voiced else self.silence + 1
            else:
                self.preroll.append(frame)
            self.position += 480
            if self.speech and (self.silence >= 20 or len(self.speech) >= 256_000):
                chunk = self.flush()
                if chunk:
                    result.append(chunk)
        return result

    def flush(self):
        result = None
        if self.speech and self.voiced >= 3:
            result = (self.start / 16000, self.position / 16000, bytes(self.speech))
        self.speech.clear()
        self.voiced = self.silence = 0
        return result


def chunk_path(settings, chunk_id: uuid.UUID) -> Path:
    return settings.audio_dir / "live" / f"{chunk_id}.pcm"


def persist_chunk(factory, settings, room_id, participant_id, stream_token, offset, chunk):
    start, end, data = chunk
    identifier = uuid.uuid4()
    path = chunk_path(settings, identifier)
    with factory() as db:
        room = db.get(LiveRoom, room_id)
        participant = db.get(LiveParticipant, participant_id)
        if room is None or room.status == "ended" or participant is None or participant.revoked:
            return False
        if participant.stream_token != stream_token:
            return False
        pending = db.scalar(
            select(func.count())
            .select_from(LiveChunk)
            .where(LiveChunk.room_id == room_id, LiveChunk.status.in_(["queued", "running"]))
        )
        if pending >= 60:
            room.audio_error = "TRANSCRIPTION_BACKLOG"
            db.commit()
            raise HTTPException(429, "Transcription cannot keep up")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data)
            db.add(
                LiveChunk(
                    id=identifier,
                    room_id=room_id,
                    participant_id=participant_id,
                    start=offset + start,
                    end=offset + end,
                )
            )
            participant.last_seen_at = utcnow()
            db.commit()
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    return True


@router.websocket("/rooms/{room_id}/audio")
async def audio_stream(websocket: WebSocket, room_id: uuid.UUID):
    settings = websocket.app.state.settings
    factory = websocket.app.state.session_factory
    protocols = [
        value.strip() for value in websocket.headers.get("sec-websocket-protocol", "").split(",")
    ]
    try:
        if (
            websocket.headers.get("origin") not in settings.origins
            or len(protocols) != 2
            or protocols[0] != "soyle-live"
        ):
            raise HTTPException(403, "Invalid stream origin")
        participant_id = decode_member(settings, protocols[1], room_id)
        stream_token = uuid.uuid4()
        with factory() as db:
            participant = db.get(LiveParticipant, participant_id)
            room = db.get(LiveRoom, room_id)
            if (
                participant is None
                or participant.room_id != room_id
                or participant.revoked
                or room is None
                or room.status != "active"
            ):
                raise HTTPException(403, "Room is unavailable")
            created = (
                room.created_at.replace(tzinfo=UTC)
                if room.created_at.tzinfo is None
                else room.created_at
            )
            check_host_session(db, participant, websocket.cookies.get(settings.cookie_name))
            offset = max(0.0, (utcnow() - created).total_seconds())
            participant.stream_token = stream_token
            participant.last_seen_at = utcnow()
            db.commit()
    except HTTPException:
        await websocket.close(code=1008)
        return
    await websocket.accept(subprotocol="soyle-live")
    speech = SpeechBuffer()
    opened = time.monotonic()
    samples = 0
    last_check = 0.0
    recording = None
    try:
        recording = begin_recording(
            factory, settings, room_id, participant_id, stream_token, offset
        )
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("text") == "stop":
                break
            data = message.get("bytes")
            if data is None or len(data) > 32768 or len(data) % 2:
                await websocket.close(code=1008)
                break
            samples += len(data) // 2
            elapsed = time.monotonic() - opened
            if samples > (elapsed + 3) * 32000 or elapsed > settings.live_max_minutes * 60:
                await websocket.close(code=1008)
                break
            if elapsed - last_check >= 1:
                with factory() as db:
                    participant = db.get(LiveParticipant, participant_id)
                    room = db.get(LiveRoom, room_id)
                    if (
                        participant is None
                        or participant.revoked
                        or participant.stream_token != stream_token
                        or room is None
                        or room.status not in {"active", "ending"}
                        or (
                            room.status == "ending"
                            and room.ended_at is not None
                            and room.ended_at.replace(tzinfo=UTC) < utcnow() - timedelta(seconds=5)
                        )
                    ):
                        break
                    check_host_session(db, participant, websocket.cookies.get(settings.cookie_name))
                last_check = elapsed
            # Write the whole microphone stream before VAD; pauses and speech
            # rejected by recognition must still exist in the full recording.
            recording.write(data)
            for chunk in speech.feed(data):
                try:
                    if not persist_chunk(
                        factory, settings, room_id, participant_id, stream_token, offset, chunk
                    ):
                        break
                except HTTPException as exc:
                    if exc.status_code != 429:
                        raise
                    # A slow recognizer must not cut off the full recording.
                    await websocket.send_json({"type": "error", "code": "TRANSCRIPTION_BACKLOG"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    except OSError:
        with factory() as db:
            db.execute(
                update(LiveRoom)
                .where(LiveRoom.id == room_id)
                .values(
                    recording_status="failed",
                    recording_error="RECORDING_UNAVAILABLE",
                    audio_error="RECORDING_UNAVAILABLE",
                )
            )
            db.commit()
        await websocket.close(code=1011)
    except HTTPException as exc:
        await websocket.send_json(
            {
                "type": "error",
                "code": "TRANSCRIPTION_BACKLOG" if exc.status_code == 429 else "ROOM_ACCESS",
            }
        )
    finally:
        close_recording(factory, stream_token, recording)
        chunk = speech.flush()
        if chunk:
            try:
                persist_chunk(
                    factory, settings, room_id, participant_id, stream_token, offset, chunk
                )
            except HTTPException:
                pass
        with factory() as db:
            db.execute(
                update(LiveParticipant)
                .where(
                    LiveParticipant.id == participant_id,
                    LiveParticipant.stream_token == stream_token,
                )
                .values(stream_token=None)
            )
            db.commit()
        try:
            await websocket.close()
        except RuntimeError:
            pass


# Served from the same origin so AudioWorklet works under script-src 'self'; no blob/eval exception.
WORKLET = r"""
class SoylePCM extends AudioWorkletProcessor {
  constructor() {
    super(); this.phase = 0; this.sum = 0; this.count = 0; this.pcm = []; this.stopped = false;
    this.port.onmessage = (event) => {
      if (event.data === 'flush') { this.stopped = true; this.emit(this.pcm.length); }
    };
  }
  emit(size) {
    if (!size) return;
    const values = this.pcm.splice(0, size);
    const buffer = new ArrayBuffer(values.length * 2);
    const view = new DataView(buffer);
    values.forEach((value, i) => view.setInt16(i * 2, value, true));
    this.port.postMessage(buffer, [buffer]);
  }
  process(inputs, outputs) {
    if (this.stopped) return false;
    const input = inputs[0]?.[0];
    if (!input) return true;
    const ratio = sampleRate / 16000;
    for (const sample of input) {
      this.sum += sample; this.count++; this.phase++;
      if (this.phase >= ratio) {
        const value = Math.max(-1, Math.min(1, this.sum / this.count));
        this.pcm.push(Math.round(value < 0 ? value * 32768 : value * 32767));
        this.phase -= ratio; this.sum = 0; this.count = 0;
      }
    }
    if (this.pcm.length >= 8000) {
      this.emit(8000);
    }
    return true;
  }
}
registerProcessor('soyle-pcm', SoylePCM);
"""


@router.get("/audio-processor.js", include_in_schema=False)
def audio_processor():
    return Response(WORKLET, media_type="application/javascript")
