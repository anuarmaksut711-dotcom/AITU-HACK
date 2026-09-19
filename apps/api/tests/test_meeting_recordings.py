"""Verify actual PCM/WAV bytes, capture, preservation, playback and authorization."""

import io
import wave
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import numpy as np
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from aimeet_api.db.models import Meeting, User, utcnow
from aimeet_api.modules.live.audio import chunk_path
from aimeet_api.modules.live.models import LiveChunk, LiveParticipant, LiveRecordingStream, LiveRoom
from aimeet_api.modules.live.recordings import (
    begin_recording,
    close_recording,
    mix_recording,
    process_recording_once,
    recording_path,
)
from aimeet_api.modules.live.worker import finalize_rooms
from aimeet_api.modules.transcription.storage import audio_path


@pytest.fixture
def archive(app, alice, tmp_path):
    app.state.settings.audio_dir = tmp_path / "audio"
    app.state.settings.livekit_api_secret = SecretStr("test-live-secret-" * 4)
    rid, pid, mid, cid = uuid4(), uuid4(), uuid4(), uuid4()
    quote = "Подготовлю смету."
    transcript = "[00:02] Алия: " + quote
    with app.state.session_factory() as db:
        user = db.get(User, UUID(alice.id))
        meeting = Meeting(
            id=mid,
            workspace_id=user.workspace_id,
            created_by=user.id,
            title="Тест оригинального аудио",
            status="transcribed",
            source_type="text",
            transcript=transcript,
            transcript_length=len(transcript),
            segments=[{"start": 2, "end": 3, "text": "Алия: " + quote}],
        )
        db.add(meeting)
        db.flush()
        room = LiveRoom(
            id=rid,
            workspace_id=user.workspace_id,
            created_by=user.id,
            title=meeting.title,
            status="ended",
            meeting_id=mid,
            created_at=utcnow() - timedelta(seconds=4),
            ended_at=utcnow(),
            recording_status="queued",
            analysis_status="ready",
        )
        db.add(room)
        db.flush()
        db.add(LiveParticipant(id=pid, room_id=rid, user_id=user.id, name="Алия", is_host=True))
        db.flush()
        db.add(
            LiveChunk(
                id=cid, room_id=rid, participant_id=pid, start=2, end=3, text=quote, status="done"
            )
        )
        db.commit()
    pcm = np.full(16000, 1200, dtype="<i2").tobytes()
    path = chunk_path(app.state.settings, cid)
    path.parent.mkdir(parents=True)
    path.write_bytes(pcm)
    return SimpleNamespace(room=rid, participant=pid, meeting=mid, clip=cid, pcm=pcm, quote=quote)


def wav_samples(content):
    with wave.open(io.BytesIO(content), "rb") as audio:
        assert (
            audio.getnchannels() == 1
            and audio.getsampwidth() == 2
            and audio.getframerate() == 16000
        )
        return np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")


def test_mix_preserves_room_timeline_pauses_and_overlapping_speakers(app, tmp_path):
    app.state.settings.audio_dir = tmp_path
    streams = [
        SimpleNamespace(id=uuid4(), start=0),
        SimpleNamespace(id=uuid4(), start=2),
        SimpleNamespace(id=uuid4(), start=2),
    ]
    for row, level in zip(streams, [1000, 20000, 20000], strict=True):
        path = recording_path(app.state.settings, row.id)
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(np.full(16000, level, dtype="<i2").tobytes())
    target = tmp_path / "mixed.wav"
    size, digest = mix_recording(app.state.settings, streams, target, duration_seconds=4)
    result = wav_samples(target.read_bytes())
    assert len(result) == 64000 and size == len(target.read_bytes()) and len(digest) == 64
    assert np.all(result[:16000] == 1000)
    assert np.all(result[16000:32000] == 0)
    assert np.all(result[32000:48000] == 32767)
    assert np.all(result[48000:] == 0)


def test_full_recording_published_with_original_clips_and_deleted_together(
    app, authenticated_client, archive
):
    stream_id = uuid4()
    handle = begin_recording(
        app.state.session_factory,
        app.state.settings,
        archive.room,
        archive.participant,
        stream_id,
        0,
    )
    # Two seconds of silence followed by the actual utterance and another pause.
    handle.write(b"\x00\x00" * 32000 + archive.pcm + b"\x00\x00" * 16000)
    close_recording(app.state.session_factory, stream_id, handle)
    with app.state.session_factory() as db:
        db.get(LiveRoom, archive.room).recording_status = "queued"
        db.commit()
    assert process_recording_once(app.state.session_factory, app.state.settings)
    assert not process_recording_once(app.state.session_factory, app.state.settings)
    mid = archive.meeting
    meeting = authenticated_client.get(f"/api/v1/meetings/{mid}").json()
    assert meeting["source_type"] == "audio" and meeting["audio_filename"] == "Беседа.wav"
    full = authenticated_client.get(f"/api/v1/meetings/{mid}/audio")
    assert full.status_code == 200
    samples = wav_samples(full.content)
    assert np.all(samples[32000:48000] == 1200)
    metadata = authenticated_client.get(f"/api/v1/meetings/{mid}/audio-clips").json()
    assert metadata["full_audio_available"] and metadata["recording_status"] == "ready"
    assert metadata["clips"][0]["available"]
    segment = authenticated_client.get(f"/api/v1/meetings/{mid}/audio-clips/{archive.clip}")
    assert segment.status_code == 200 and wav_samples(segment.content).tobytes() == archive.pcm
    assert "no-store" in segment.headers["cache-control"]
    assert authenticated_client.delete(f"/api/v1/meetings/{mid}").status_code == 204
    assert not recording_path(app.state.settings, stream_id).exists()
    assert not chunk_path(app.state.settings, archive.clip).exists()
    assert not audio_path(app.state.settings, mid).exists()
    assert (
        authenticated_client.get(f"/api/v1/meetings/{mid}/audio-clips/{archive.clip}").status_code
        == 404
    )


def test_clip_scope_missing_audio_and_nonfabricated_metadata(
    app, authenticated_client, archive, bob
):
    endpoint = f"/api/v1/meetings/{archive.meeting}/audio-clips"
    metadata = authenticated_client.get(endpoint).json()
    clip = metadata["clips"][0]
    assert clip["start"] == 2 and clip["speaker"] == "Алия"
    with app.state.session_factory() as db:
        text = db.get(Meeting, archive.meeting).transcript
    assert text[clip["start_char"] : clip["end_char"]] == archive.quote
    assert authenticated_client.get(endpoint + "/" + str(uuid4())).status_code == 404
    chunk_path(app.state.settings, archive.clip).unlink()
    assert not authenticated_client.get(endpoint).json()["clips"][0]["available"]
    assert authenticated_client.get(endpoint + "/" + str(archive.clip)).status_code == 404
    authenticated_client.post("/api/v1/auth/login", json=bob.credentials)
    assert authenticated_client.get(endpoint).status_code == 404
    assert authenticated_client.get(endpoint + "/" + str(archive.clip)).status_code == 404
    assert (
        authenticated_client.post(
            f"/api/v1/meetings/{archive.meeting}/audio-recording/retry"
        ).status_code
        == 404
    )


def test_missing_stream_does_not_publish_fake_complete_recording(
    app, authenticated_client, archive
):
    with app.state.session_factory() as db:
        db.add(
            LiveRecordingStream(
                id=uuid4(),
                room_id=archive.room,
                participant_id=archive.participant,
                start=0,
                closed_at=utcnow(),
            )
        )
        db.commit()
    assert process_recording_once(app.state.session_factory, app.state.settings)
    assert not audio_path(app.state.settings, archive.meeting).exists()
    response = authenticated_client.get(f"/api/v1/meetings/{archive.meeting}/audio-clips").json()
    assert response["recording_status"] == "failed" and not response["full_audio_available"]
    assert response["clips"][0]["available"]
    assert (
        authenticated_client.post(
            f"/api/v1/meetings/{archive.meeting}/audio-recording/retry",
            headers={"Origin": "https://untrusted.example"},
        ).status_code
        == 403
    )
    assert (
        authenticated_client.post(
            f"/api/v1/meetings/{archive.meeting}/audio-recording/retry"
        ).status_code
        == 200
    )


def test_ingress_records_silence_before_vad_and_archives_no_speech(
    app, authenticated_client, monkeypatch
):
    app.state.settings.livekit_api_secret = SecretStr("test-live-secret-" * 4)
    monkeypatch.setattr("aimeet_api.modules.live.router.media_room_request", lambda *args: None)
    host = authenticated_client.post(
        "/api/v1/live/rooms", json={"title": "Проверка полной записи"}
    ).json()

    class SilentBuffer:
        def feed(self, data):
            return []

        def flush(self):
            return None

    monkeypatch.setattr("aimeet_api.modules.live.audio.SpeechBuffer", SilentBuffer)
    pcm = b"\x00\x00" * 8000
    with authenticated_client.websocket_connect(
        f"wss://testserver/api/v1/live/rooms/{host['room_id']}/audio",
        headers={"Origin": "https://testserver"},
        subprotocols=["soyle-live", host["member_token"]],
    ) as ws:
        ws.send_bytes(pcm)
        ws.send_text("stop")
        message = ws.receive()
        assert message["type"] == "websocket.close"
    with app.state.session_factory() as db:
        stream = db.scalar(select(LiveRecordingStream))
        assert recording_path(app.state.settings, stream.id).read_bytes() == pcm
        assert stream.closed_at is not None
        room = db.get(LiveRoom, UUID(host["room_id"]))
        room.status = "ending"
        room.created_at = utcnow() - timedelta(seconds=12)
        room.ended_at = utcnow() - timedelta(seconds=6)
        db.commit()
    finalize_rooms(app.state.session_factory, app.state.settings)
    assert process_recording_once(app.state.session_factory, app.state.settings)
    with app.state.session_factory() as db:
        room = db.get(LiveRoom, UUID(host["room_id"]))
        assert room.meeting_id is not None and room.recording_status == "ready"
        assert room.analysis_status == "ready"
        assert db.get(Meeting, room.meeting_id).source_type == "audio"


def test_transcription_backlog_does_not_stop_full_capture(app, authenticated_client, monkeypatch):
    from fastapi import HTTPException

    app.state.settings.livekit_api_secret = SecretStr("test-live-secret-" * 4)
    monkeypatch.setattr("aimeet_api.modules.live.router.media_room_request", lambda *args: None)
    host = authenticated_client.post(
        "/api/v1/live/rooms", json={"title": "Запись при очереди ASR"}
    ).json()

    class Speech:
        def feed(self, data):
            return [(0, 0.5, data)]

        def flush(self):
            return None

    def overloaded(*args):
        raise HTTPException(429, "Transcription cannot keep up")

    monkeypatch.setattr("aimeet_api.modules.live.audio.SpeechBuffer", Speech)
    monkeypatch.setattr("aimeet_api.modules.live.audio.persist_chunk", overloaded)
    pcm = b"\x10\x00" * 8000
    with authenticated_client.websocket_connect(
        f"wss://testserver/api/v1/live/rooms/{host['room_id']}/audio",
        headers={"Origin": "https://testserver"},
        subprotocols=["soyle-live", host["member_token"]],
    ) as ws:
        ws.send_bytes(pcm)
        assert ws.receive_json()["code"] == "TRANSCRIPTION_BACKLOG"
        ws.send_bytes(pcm)
        assert ws.receive_json()["code"] == "TRANSCRIPTION_BACKLOG"
        ws.send_text("stop")
        assert ws.receive()["type"] == "websocket.close"
    with app.state.session_factory() as db:
        stream = db.scalar(select(LiveRecordingStream))
        assert stream.closed_at is not None
        assert recording_path(app.state.settings, stream.id).read_bytes() == pcm * 2
