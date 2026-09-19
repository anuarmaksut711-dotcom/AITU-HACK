import hashlib
import io
import wave
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select, update

from aimeet_api.db.models import TranscriptionJob, utcnow
from aimeet_api.modules.transcription.engine import TranscriptionError, decode_audio, model_metadata
from aimeet_api.modules.transcription.jobs import claim_next, finish, heartbeat
from aimeet_api.modules.transcription.storage import audio_path
from aimeet_api.modules.transcription.worker import run_job


@pytest.fixture(autouse=True)
def audio_settings(app, tmp_path):
    app.state.settings.audio_dir = tmp_path / "audio"
    app.state.settings.stt_model_path = tmp_path / "missing-model"


def wav_bytes(seconds=1):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\0\0" * 16000 * seconds)
    return stream.getvalue()


def upload(client, **kwargs):
    return client.post(
        "/api/v1/meetings/audio",
        params={
            "title": "Совещание",
            "language": "ru",
            "filename": "recording.wav",
            **kwargs,
        },
        content=wav_bytes(),
        headers={"Content-Type": "application/octet-stream"},
    )


def completed():
    return {
        "transcript": "Согласовали план.",
        "segments": [
            {"start": 0.0, "end": 1.0, "text": "Согласовали план.", "speaker": None},
        ],
        "detected_language": "ru",
        "duration_seconds": 1.0,
    }


def test_upload_streams_source_without_exposing_paths(app, authenticated_client):
    response = upload(authenticated_client, filename="../../ЗАПИСЬ.WAV")
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["transcript"] == ""
    assert body["status"] == "draft"
    assert body["transcription"]["status"] == "queued"
    assert body["audio_filename"] == "ЗАПИСЬ.WAV"
    assert "config" not in body["transcription"]
    assert "audio_sha256" not in body
    stored = audio_path(app.state.settings, UUID(body["id"]))
    assert stored.read_bytes() == wav_bytes()
    assert stored.stat().st_mode & 0o777 == 0o600
    assert not list(stored.parent.glob("*.partial"))
    with app.state.session_factory() as db:
        job = db.scalar(select(TranscriptionJob))
        assert job.meeting_id == UUID(body["id"])


def test_unsupported_empty_large_and_unauthed_uploads_do_not_leave_files(app, client, alice):
    assert upload(client).status_code == 401
    client.post("/api/v1/auth/login", json=alice.credentials)
    assert upload(client, filename="playlist.m3u").status_code == 415
    assert upload(client, title=" ").status_code == 422
    assert upload(client, language="xx").status_code == 422
    assert (
        client.post("/api/v1/meetings/audio?title=t&filename=t.wav", content=b"").status_code == 422
    )
    # The route's bound is also checked if runtime limits are lowered after app construction.
    app.state.settings.max_audio_bytes = 1024
    assert upload(client).status_code == 413
    client.headers.pop("X-Requested-With")
    assert upload(client).status_code == 403
    assert client.get("/api/v1/meetings").json()["total"] == 0
    assert not list(app.state.settings.audio_dir.glob("*"))


def test_chunked_upload_limit_cleans_partial_file(app, authenticated_client):
    app.state.settings.max_audio_bytes = 1024
    chunks = (b"a" * 512 for _ in range(3))
    response = authenticated_client.post(
        "/api/v1/meetings/audio?title=t&filename=t.wav",
        content=chunks,
    )
    assert response.status_code == 413
    assert not list(app.state.settings.audio_dir.glob("*"))


def test_speaker_count_hint_is_validated_and_survives_retry(app, authenticated_client):
    for count in (0, -1, 33):
        assert upload(authenticated_client, num_speakers=count).status_code == 422
    body = upload(authenticated_client, num_speakers=2).json()
    with app.state.session_factory() as db:
        job = db.scalar(
            select(TranscriptionJob).where(TranscriptionJob.meeting_id == UUID(body["id"]))
        )
        assert job.config["num_speakers"] == 2
    base = f"/api/v1/meetings/{body['id']}/transcription"
    authenticated_client.post(base + "/cancel")
    assert authenticated_client.post(base + "/retry").status_code == 200
    with app.state.session_factory() as db:
        job = db.scalar(
            select(TranscriptionJob).where(TranscriptionJob.meeting_id == UUID(body["id"]))
        )
        assert job.config["num_speakers"] == 2


def test_claim_completion_is_atomic_and_single_use(app, authenticated_client):
    meeting = upload(authenticated_client).json()
    factory, settings = app.state.session_factory, app.state.settings
    claim = claim_next(factory, settings)
    assert claim is not None
    assert claim_next(factory, settings) is None
    assert heartbeat(factory, settings, claim, 40)
    result = completed()
    assert finish(factory, claim, result=result)
    assert not finish(factory, claim, result={**result, "transcript": "STALE"})
    body = authenticated_client.get(f"/api/v1/meetings/{meeting['id']}").json()
    assert body["transcript"] == result["transcript"]
    assert body["segments"] == result["segments"]
    assert body["status"] == "transcribed"
    assert body["transcription"]["progress"] == 100
    assert (
        authenticated_client.post(
            f"/api/v1/meetings/{meeting['id']}/transcription/retry"
        ).status_code
        == 409
    )


def test_cancel_retry_and_expired_lease_fence_stale_results(app, authenticated_client):
    meeting = upload(authenticated_client).json()
    base = f"/api/v1/meetings/{meeting['id']}/transcription"
    factory, settings = app.state.session_factory, app.state.settings
    old = claim_next(factory, settings)
    assert (
        authenticated_client.post(base + "/cancel").json()["transcription"]["status"] == "cancelled"
    )
    assert not heartbeat(factory, settings, old, 30)
    assert not finish(factory, old, result=completed())
    assert authenticated_client.post(base + "/retry").json()["transcription"]["status"] == "queued"
    second = claim_next(factory, settings)
    with factory() as db:
        db.execute(update(TranscriptionJob).values(lease_until=utcnow() - timedelta(seconds=1)))
        db.commit()
    third = claim_next(factory, settings)
    assert third.token != second.token
    assert not finish(factory, second, result=completed())
    assert finish(factory, third, result=completed())


def test_exhausted_job_does_not_block_next_upload(app, authenticated_client):
    first = upload(authenticated_client).json()
    factory, settings = app.state.session_factory, app.state.settings
    claim_next(factory, settings)
    with factory() as db:
        db.execute(
            update(TranscriptionJob).values(
                attempts=settings.job_max_attempts,
                lease_until=utcnow() - timedelta(seconds=1),
            )
        )
        db.commit()
    second = upload(authenticated_client).json()
    next_claim = claim_next(factory, settings)
    assert str(next_claim.meeting_id) == second["id"]
    first_body = authenticated_client.get(f"/api/v1/meetings/{first['id']}").json()
    assert first_body["transcription"]["error_code"] == "worker_interrupted"


def test_delete_cancels_work_and_removes_recording(app, authenticated_client):
    meeting = upload(authenticated_client).json()
    claim = claim_next(app.state.session_factory, app.state.settings)
    assert authenticated_client.delete(f"/api/v1/meetings/{meeting['id']}").status_code == 204
    assert not audio_path(app.state.settings, UUID(meeting["id"])).exists()
    assert not finish(app.state.session_factory, claim, result=completed())
    with app.state.session_factory() as db:
        assert db.scalar(select(TranscriptionJob)) is None


def test_audio_actions_cannot_cross_workspace_boundaries(app, client, alice, bob):
    client.post("/api/v1/auth/login", json=alice.credentials)
    meeting = upload(client).json()
    client.post("/api/v1/auth/logout")
    client.post("/api/v1/auth/login", json=bob.credentials)
    base = f"/api/v1/meetings/{meeting['id']}"
    for path in (base + "/transcription/cancel", base + "/transcription/retry"):
        assert client.post(path).status_code == 404
    assert client.get(base).status_code == 404
    assert client.delete(base).status_code == 404
    assert audio_path(app.state.settings, UUID(meeting["id"])).is_file()


def test_missing_model_is_actionable_and_does_not_download(app, authenticated_client, monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("Inference attempted network access")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    meeting = upload(authenticated_client).json()
    claim = claim_next(app.state.session_factory, app.state.settings)
    run_job(app.state.session_factory, app.state.settings, claim)
    body = authenticated_client.get(f"/api/v1/meetings/{meeting['id']}").json()
    assert body["transcription"]["error_code"] == "model_missing"
    assert body["transcript"] == ""


def test_decoder_accepts_wav_and_enforces_decoded_duration(tmp_path):
    path = tmp_path / "source.audio"
    path.write_bytes(wav_bytes(2))
    assert len(decode_audio(path, ".wav", 3)) == 32000
    with pytest.raises(TranscriptionError, match="audio_too_long"):
        decode_audio(path, ".wav", 1)
    path.write_bytes(b"#EXTM3U\nhttp://127.0.0.1/private.wav")
    with pytest.raises(TranscriptionError, match="invalid_audio"):
        decode_audio(path, ".mp3", 3)


def test_model_requires_local_tokenizer(tmp_path):
    (tmp_path / "model.bin").touch()
    (tmp_path / "config.json").write_text("{}")
    with pytest.raises(TranscriptionError, match="model_missing"):
        model_metadata(tmp_path)


def test_adapter_passes_offline_settings_and_preserves_timestamps(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from aimeet_api.modules.transcription.engine import transcribe

    path = tmp_path / "audio.wav"
    path.write_bytes(wav_bytes())
    model_path = tmp_path / "model"
    model_path.mkdir()
    for name in ("model.bin", "config.json", "tokenizer.json"):
        (model_path / name).write_text("{}")
    captured = {}

    class Model:
        def __init__(self, path, **kwargs):
            assert Path(path) == model_path
            captured.update(kwargs)

        def transcribe(self, audio, **kwargs):
            captured.update(kwargs)
            return iter([SimpleNamespace(start=0.1, end=0.8, text=" Сәлем! ")]), SimpleNamespace(
                language="kk"
            )

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    config = {
        "model_path": str(model_path),
        "model": model_metadata(model_path),
        "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "extension": ".wav",
        "max_audio_seconds": 2,
        "device": "cpu",
        "compute_type": "int8",
        "cpu_threads": 2,
        "language": "auto",
    }
    emitted = []
    result = transcribe(path, config, emitted.append)
    assert captured["local_files_only"] is True
    assert captured["task"] == "transcribe"
    assert captured["language"] is None
    assert captured["vad_filter"] is True
    assert result["segments"] == [{"start": 0.1, "end": 0.8, "text": "Сәлем!"}]
    assert {"segment": result["segments"][0]} in emitted
    assert {"duration_seconds": 1.0} in emitted
    assert {"detected_language": "kk"} in emitted
    path.write_bytes(b"changed")
    with pytest.raises(TranscriptionError, match="source_changed"):
        transcribe(path, config, lambda _: None)


def test_postgres_workers_claim_distinct_jobs(app, authenticated_client):
    from concurrent.futures import ThreadPoolExecutor

    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("SKIP LOCKED is a PostgreSQL concurrency contract")
    for _ in range(4):
        assert upload(authenticated_client).status_code == 202
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(
            pool.map(
                lambda _: claim_next(app.state.session_factory, app.state.settings),
                range(4),
            )
        )
    assert all(claim is not None for claim in claims)
    assert len({claim.id for claim in claims}) == 4


def test_partial_transcript_is_visible_and_stale_writers_are_fenced(app, authenticated_client):
    client = authenticated_client
    mid = upload(client).json()["id"]
    factory, settings = app.state.session_factory, app.state.settings
    claim = claim_next(factory, settings)
    assert heartbeat(factory, settings, claim, 45, partial=completed())
    body = client.get(f"/api/v1/meetings/{mid}").json()
    assert body["transcript"] == completed()["transcript"]
    assert body["segments"] == completed()["segments"]
    assert body["status"] == "draft"
    assert body["transcription"]["status"] == "running"
    assert body["transcription"]["duration_seconds"] == 1.0
    assert client.post(f"/api/v1/meetings/{mid}/board/generate").status_code == 409
    assert client.post(f"/api/v1/meetings/{mid}/rag/index").status_code == 409
    coverage = client.get("/api/v1/rag/index").json()
    assert coverage["unavailable"] == 1
    assert coverage["not_indexed"] == 0
    assert client.post(f"/api/v1/meetings/{mid}/transcription/cancel").status_code == 200
    assert not heartbeat(
        factory, settings, claim, 90, partial={**completed(), "transcript": "STALE"}
    )
    assert client.get(f"/api/v1/meetings/{mid}").json()["transcript"] == completed()["transcript"]
    retry = client.post(f"/api/v1/meetings/{mid}/transcription/retry").json()
    assert retry["transcript"] == ""
    assert retry["segments"] is None
    assert retry["transcription"]["duration_seconds"] is None
    replacement = claim_next(factory, settings)
    assert not heartbeat(factory, settings, claim, 90, partial=completed())
    assert finish(factory, replacement, result=completed())
    board = client.get(f"/api/v1/meetings/{mid}/board").json()
    assert board["status"] == "queued"
    assert not finish(factory, claim, result=completed())


def test_expired_lease_restarts_partial_transcript_and_failed_job_does_not_queue_analysis(
    app, authenticated_client
):
    client = authenticated_client
    mid = upload(client).json()["id"]
    factory, settings = app.state.session_factory, app.state.settings
    claim = claim_next(factory, settings)
    assert heartbeat(factory, settings, claim, 45, partial=completed())
    with factory() as db:
        db.execute(update(TranscriptionJob).values(lease_until=utcnow() - timedelta(seconds=1)))
        db.commit()
    replacement = claim_next(factory, settings)
    assert client.get(f"/api/v1/meetings/{mid}").json()["transcript"] == ""
    assert not heartbeat(factory, settings, claim, 50, partial=completed())
    assert finish(factory, replacement, error="processing_failed")
    assert client.get(f"/api/v1/meetings/{mid}/board").json()["status"] == "idle"
