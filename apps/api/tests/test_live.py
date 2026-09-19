import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import select, update

from aimeet_api.db.models import utcnow
from aimeet_api.modules.live.analysis import generate_insights
from aimeet_api.modules.live.audio import SpeechBuffer, chunk_path, persist_chunk
from aimeet_api.modules.live.models import LiveChunk, LiveParticipant, LiveRoom
from aimeet_api.modules.live.security import decode_member
from aimeet_api.modules.live.worker import (
    claim_audio,
    finalize_rooms,
    process_analysis,
    process_audio,
)
from aimeet_api.modules.rag.providers import RagError


@pytest.fixture(autouse=True)
def live_settings(app, tmp_path, monkeypatch):
    app.state.settings.livekit_api_secret = SecretStr("test-live-room-secret-" * 3)
    app.state.settings.audio_dir = tmp_path / "audio"
    app.state.settings.openai_api_key = SecretStr("test-provider-key")
    monkeypatch.setattr("aimeet_api.modules.live.router.media_room_request", lambda *args: None)


def create(client):
    response = client.post("/api/v1/live/rooms", json={"title": "План запуска", "language": "ru"})
    assert response.status_code == 201, response.text
    return response.json()


def auth(grant):
    return {"Authorization": "Bearer " + grant["member_token"]}


def invitation(client, host):
    return client.get(f"/api/v1/live/rooms/{host['room_id']}/invite", headers=auth(host)).json()[
        "invite"
    ]


def test_room_creation_and_voice_only_grants(app, authenticated_client):
    host = create(authenticated_client)
    state = authenticated_client.get(
        f"/api/v1/live/rooms/{host['room_id']}", headers=auth(host)
    ).json()
    assert state["status"] == "active"
    assert state["insights"] == state["utterances"] == []
    assert state["can_end"] is True
    claims = jwt.decode(
        host["media_token"],
        app.state.settings.livekit_api_secret.get_secret_value(),
        algorithms=["HS256"],
    )
    assert claims["video"]["canPublishSources"] == ["microphone"]
    assert claims["video"]["canPublishData"] is True
    assert not claims["video"].get("roomAdmin")
    with pytest.raises(HTTPException):
        decode_member(app.state.settings, host["media_token"], UUID(host["room_id"]))
    assert len(authenticated_client.get("/api/v1/live/rooms").json()) == 1


def test_guest_invite_is_scoped_and_cannot_control_host(app, client, alice):
    client.post("/api/v1/auth/login", json=alice.credentials)
    host = create(client)
    token = invitation(client, host)
    client.post("/api/v1/auth/logout")
    base = f"/api/v1/live/rooms/{host['room_id']}"
    assert client.post(base + "/invitation", json={"invite": "x" * 64}).status_code == 404
    guest = client.post(base + "/join", json={"invite": token, "name": "Данияр"}).json()
    assert client.get(base, headers=auth(guest)).status_code == 200
    assert client.get("/api/v1/live/rooms").status_code == 401
    assert client.get(base + "/invite", headers=auth(guest)).status_code == 403
    assert client.post(base + "/end", headers=auth(guest)).status_code == 403
    assert client.post(base + "/analysis/retry", headers=auth(guest)).status_code == 403
    assert client.get(f"/api/v1/live/rooms/{uuid4()}", headers=auth(guest)).status_code == 401
    assert client.get("/api/v1/meetings", headers=auth(guest)).status_code == 401


def test_host_grant_requires_current_account_session(client, alice, bob):
    client.post("/api/v1/auth/login", json=alice.credentials)
    host = create(client)
    base = f"/api/v1/live/rooms/{host['room_id']}"
    client.post("/api/v1/auth/logout")
    assert client.get(base, headers=auth(host)).status_code == 401
    client.post("/api/v1/auth/login", json=bob.credentials)
    assert client.get(base, headers=auth(host)).status_code == 401
    assert client.post(base + "/host").status_code == 404


def test_end_blocks_new_joins_and_archives_speech(app, authenticated_client):
    host = create(authenticated_client)
    token = invitation(authenticated_client, host)
    room_id = UUID(host["room_id"])
    with app.state.session_factory() as db:
        db.add(
            LiveChunk(
                room_id=room_id,
                participant_id=UUID(host["participant_id"]),
                status="done",
                start=1,
                end=3,
                text="Запустим пилот к пятнице.",
            )
        )
        db.commit()
    base = f"/api/v1/live/rooms/{room_id}"
    assert authenticated_client.post(base + "/end", headers=auth(host)).status_code == 200
    assert (
        authenticated_client.post(
            base + "/join", json={"invite": token, "name": "Гость"}
        ).status_code
        == 409
    )
    assert authenticated_client.post(base + "/token", headers=auth(host)).status_code == 409
    with app.state.session_factory() as db:
        db.execute(update(LiveRoom).values(ended_at=utcnow() - timedelta(seconds=10)))
        db.commit()
    finalize_rooms(app.state.session_factory, app.state.settings)
    result = authenticated_client.get(base, headers=auth(host)).json()
    assert result["status"] == "ended" and result["meeting_id"]
    assert authenticated_client.post(base + "/host").json()["media_token"] == ""
    meeting_id = result["meeting_id"]
    archived = authenticated_client.get(f"/api/v1/meetings/{meeting_id}").json()
    assert "Запустим пилот к пятнице." in archived["transcript"]
    assert archived["status"] == "transcribed"
    assert authenticated_client.delete(f"/api/v1/meetings/{meeting_id}").status_code == 204
    with app.state.session_factory() as db:
        assert db.get(LiveRoom, room_id) is None
        assert db.scalar(select(LiveChunk)) is None


def test_media_provision_failure_does_not_create_successful_room(
    app, authenticated_client, monkeypatch
):
    def fail(*args):
        raise HTTPException(503, "Voice unavailable")

    monkeypatch.setattr("aimeet_api.modules.live.router.media_room_request", fail)
    assert (
        authenticated_client.post("/api/v1/live/rooms", json={"title": "Test"}).status_code == 503
    )
    assert authenticated_client.get("/api/v1/live/rooms").json() == []


def test_stream_fencing_and_local_pcm_storage(app, authenticated_client):
    host = create(authenticated_client)
    room_id, member_id, token = UUID(host["room_id"]), UUID(host["participant_id"]), uuid4()
    with app.state.session_factory() as db:
        db.get(LiveParticipant, member_id).stream_token = token
        db.commit()
    args = (app.state.session_factory, app.state.settings, room_id, member_id)
    assert not persist_chunk(*args, uuid4(), 0, (0, 1, b"\x00\x00" * 16000))
    assert persist_chunk(*args, token, 5, (0, 1, b"\x00\x00" * 16000))
    claim = claim_audio(app.state.session_factory)
    assert claim.start == 5 and claim.end == 6
    assert chunk_path(app.state.settings, claim.id).is_file()
    assert claim_audio(app.state.session_factory) is None

    class Model:
        def transcribe(self, audio, **kwargs):
            assert kwargs["task"] == "transcribe" and kwargs["language"] == "ru"
            assert kwargs["multilingual"] is True
            return iter([SimpleNamespace(text="Бүгін обсуждаем release plan.")]), None

    process_audio(app.state.session_factory, app.state.settings, claim, Model())
    state = authenticated_client.get(f"/api/v1/live/rooms/{room_id}", headers=auth(host)).json()
    assert state["utterances"][0]["participant_id"] == host["participant_id"]
    assert state["utterances"][0]["text"] == "Бүгін обсуждаем release plan."
    assert chunk_path(app.state.settings, claim.id).read_bytes() == b"\x00\x00" * 16000


def test_stale_audio_worker_cannot_publish_or_delete_replacement_source(app, authenticated_client):
    host = create(authenticated_client)
    with app.state.session_factory() as db:
        chunk = LiveChunk(
            room_id=UUID(host["room_id"]),
            participant_id=UUID(host["participant_id"]),
            start=0,
            end=1,
        )
        db.add(chunk)
        db.commit()
    old = claim_audio(app.state.session_factory)
    path = chunk_path(app.state.settings, old.id)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\x00\x00" * 16000)
    with app.state.session_factory() as db:
        db.execute(update(LiveChunk).values(lease=uuid4()))
        db.commit()

    class Model:
        def transcribe(self, *args, **kwargs):
            return iter([SimpleNamespace(text="STALE")]), None

    process_audio(app.state.session_factory, app.state.settings, old, Model())
    assert path.exists()
    with app.state.session_factory() as db:
        assert db.get(LiveChunk, old.id).text == ""


def test_vad_bounds_speech_and_flushes_after_silence():
    class Vad:
        def is_speech(self, data, rate):
            return data != b"\0" * 960

    buffer = SpeechBuffer(vad=Vad())
    assert buffer.feed(b"\0" * 960 * 8) == []
    assert buffer.feed(b"\x01" * 960 * 12) == []
    chunks = buffer.feed(b"\0" * 960 * 20)
    assert len(chunks) == 1 and 0 <= chunks[0][0] < chunks[0][1]
    assert len(buffer.speech) == 0
    chunks = buffer.feed(b"\x01" * 960 * 600)
    assert len(chunks) == 2 and all(len(c[2]) <= 257000 for c in chunks)


def test_openai_receives_text_only_and_sources_are_checked(app):
    identifier = str(uuid4())
    sources = [
        {"id": identifier, "speaker": "Алия", "text": "Наша цель — запустить пилот к пятнице."}
    ]
    note = {
        "kind": "goal",
        "text": "Запустить пилот к пятнице.",
        "source_ids": ["u1"],
    }

    def respond(request):
        body = json.loads(request.content)
        assert body["store"] is False and "audio" not in body
        assert body["text"]["format"]["strict"] is True
        assert body["reasoning"]["effort"] == "low"
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": json.dumps({"insights": [note]})}
                        ],
                    }
                ],
            },
        )

    transport = httpx.MockTransport(respond)
    result = generate_insights(app.state.settings, sources, [], transport=transport)
    assert result == [{**note, "source_ids": [identifier], "quotes": [sources[0]["text"]]}]
    note["source_ids"] = ["invented-source"]
    with pytest.raises(RagError, match="UNSUPPORTED_INSIGHT"):
        generate_insights(app.state.settings, sources, [], transport=transport)


def test_stale_analysis_cannot_overwrite_current_insights(app, authenticated_client):
    host = create(authenticated_client)
    identifier, token = UUID(host["room_id"]), uuid4()
    with app.state.session_factory() as db:
        room = db.get(LiveRoom, identifier)
        room.analysis_lease = uuid4()
        room.analysis_lease_until = utcnow() + timedelta(seconds=90)
        room.insights = [{"kind": "goal", "text": "Current", "source_ids": [], "quotes": []}]
        db.commit()
    process_analysis(app.state.session_factory, app.state.settings, (identifier, token, 1))
    with app.state.session_factory() as db:
        assert db.get(LiveRoom, identifier).insights[0]["text"] == "Current"


def test_soft_analysis_failure_retries_only_on_new_speech(app, authenticated_client):
    from aimeet_api.modules.live.worker import claim_analysis

    host = create(authenticated_client)
    room_id = UUID(host["room_id"])
    with app.state.session_factory() as db:
        room = db.get(LiveRoom, room_id)
        room.transcript_revision = 1
        room.analysis_status = "failed"
        room.analysis_error = "UNSUPPORTED_INSIGHT"
        room.analysis_through = 1
        room.analysis_updated_at = utcnow() - timedelta(seconds=60)
        db.commit()
    assert claim_analysis(app.state.session_factory, app.state.settings) is None
    with app.state.session_factory() as db:
        db.get(LiveRoom, room_id).transcript_revision = 2
        db.commit()
    claim = claim_analysis(app.state.session_factory, app.state.settings)
    assert claim is not None and claim[2] == 2


def test_live_archive_carries_outcomes_and_preserves_existing_edits(app, authenticated_client):
    from aimeet_api.modules.intelligence.models import MeetingBoard
    from aimeet_api.modules.live.archive import sync_archived_board

    host = create(authenticated_client)
    room_id = UUID(host["room_id"])
    first_id, task_id = uuid4(), uuid4()
    with app.state.session_factory() as db:
        room = db.get(LiveRoom, room_id)
        room.status = "ending"
        room.ended_at = utcnow() - timedelta(seconds=10)
        room.transcript_revision = room.analysis_through = 2
        room.analysis_status = "ready"
        room.insights = [
            {
                "kind": "goal",
                "text": "Проверить пилот",
                "source_ids": [str(first_id)],
                "quotes": ["Проверим пилот."],
            },
            {
                "kind": "task",
                "text": "Подготовить смету",
                "source_ids": [str(task_id)],
                "quotes": ["Подготовим смету."],
            },
        ]
        for identifier, start, text in [
            (first_id, 0, "Проверим пилот."),
            (task_id, 10, "Подготовим смету."),
        ]:
            db.add(
                LiveChunk(
                    id=identifier,
                    room_id=room_id,
                    participant_id=UUID(host["participant_id"]),
                    status="done",
                    start=start,
                    end=start + 2,
                    text=text,
                )
            )
        db.commit()
    finalize_rooms(app.state.session_factory, app.state.settings)
    with app.state.session_factory() as db:
        room = db.get(LiveRoom, room_id)
        mid = room.meeting_id
        board = db.get(MeetingBoard, mid)
        assert board.status == "ready" and len(board.summary) == 2
        assert [card["kind"] for card in board.cards] == ["topic", "task"]
        assert board.cards[1]["assignee"] is None and board.cards[1]["due_date"] is None
        edited = {**board.cards[1], "title": "Моя правка", "reviewed": True}
        board.cards = [board.cards[0], edited]
        db.commit()
        sync_archived_board(db, room)
        db.commit()
        assert len(board.cards) == 2 and board.cards[1] == edited
    output = authenticated_client.get(f"/api/v1/meetings/{mid}/board").json()
    assert output["cards"][1]["evidence"]["start_seconds"] == 10
    assert len(output["summary"]) == 2


def test_archive_waits_for_final_live_analysis_without_second_generation(app, authenticated_client):
    from aimeet_api.modules.intelligence.models import MeetingBoard

    host = create(authenticated_client)
    room_id, chunk_id, token = UUID(host["room_id"]), uuid4(), uuid4()
    with app.state.session_factory() as db:
        room = db.get(LiveRoom, room_id)
        room.status = "ending"
        room.ended_at = utcnow() - timedelta(seconds=10)
        room.transcript_revision = 1
        room.analysis_status = "processing"
        room.analysis_lease, room.analysis_lease_until = token, utcnow() + timedelta(seconds=90)
        db.add(
            LiveChunk(
                id=chunk_id,
                room_id=room_id,
                participant_id=UUID(host["participant_id"]),
                status="done",
                start=0,
                end=2,
                text="Решили запустить пилот.",
            )
        )
        db.commit()
    finalize_rooms(app.state.session_factory, app.state.settings)
    with app.state.session_factory() as db:
        mid = db.get(LiveRoom, room_id).meeting_id
        board = db.get(MeetingBoard, mid)
        assert board.status == "running" and board.lease_token is None and board.cards == []
    process_analysis(
        app.state.session_factory,
        app.state.settings,
        (room_id, token, 1),
        generator=lambda *_: [
            {
                "kind": "decision",
                "text": "Запустить пилот",
                "source_ids": [str(chunk_id)],
                "quotes": ["Решили запустить пилот."],
            }
        ],
    )
    with app.state.session_factory() as db:
        board = db.get(MeetingBoard, mid)
        assert board.status == "ready" and len(board.cards) == 1
        assert board.cards[0]["kind"] == "decision"
