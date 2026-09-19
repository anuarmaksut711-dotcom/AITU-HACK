import csv
import io
import json
import uuid
from datetime import timedelta

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, update

from aimeet_api.db.models import utcnow
from aimeet_api.modules.intelligence.models import MeetingBoard
from aimeet_api.modules.intelligence.provider import ProtocolProvider
from aimeet_api.modules.intelligence.schemas import CardInput, GeneratedProtocol
from aimeet_api.modules.intelligence.worker import ProtocolWorker, chunks

TEXT = (
    "Алия: подготовлю смету к пятнице.\n"
    "Данияр: проверю интеграцию.\n"
    "Решили запускать пилот.\n"
    "Пока нет доступа к данным.\n"
    "Бюджет ещё не согласовали."
)


def meeting(client, text=TEXT):
    response = client.post("/api/v1/meetings", json={"title": "Пример встречи", "transcript": text})
    assert response.status_code == 201
    return response.json()["id"]


def path(meeting_id):
    return f"/api/v1/meetings/{meeting_id}/board"


def result(quote="Алия: подготовлю смету к пятнице."):
    return GeneratedProtocol.model_validate(
        {
            "summary": [{"text": "Алия подготовит смету.", "quote": quote}],
            "cards": [
                {
                    "kind": "task",
                    "title": "Подготовить смету",
                    "description": "",
                    "assignee": "Алия",
                    "due_text": "к пятнице",
                    "priority": "high",
                    "priority_evidence": None,
                    "quote": quote,
                }
            ],
        }
    )


class FakeProvider:
    def __init__(self, generated=None, callback=None):
        self.generated = generated or result()
        self.callback = callback
        self.calls = 0

    def generate(self, text, **kwargs):
        self.calls += 1
        if self.callback:
            self.callback()
        return self.generated


def test_board_lifecycle_conflict_and_archive(authenticated_client, app):
    client = authenticated_client
    mid = meeting(client)
    assert client.get(path(mid)).json()["status"] == "idle"
    created = client.post(path(mid) + "/cards", json={"version": 0, "title": "Проверить бюджет"})
    assert created.status_code == 201
    card = created.json()["cards"][0]
    assert card["origin"] == "manual"
    assert card["assignee"] is None and card["due_date"] is None
    values = {k: v for k, v in card.items() if k in CardInput.model_fields}
    edited = client.post(
        path(mid) + "/cards/" + card["id"],
        json={
            **values,
            "version": 1,
            "status": "doing",
            "reviewed": True,
        },
    )
    assert edited.status_code == 200
    assert edited.json()["cards"][0]["status"] == "doing"
    stale = client.post(path(mid) + "/cards", json={"version": 1, "title": "Старая правка"})
    assert stale.status_code == 409
    assert client.get(path(mid)).json()["version"] == 2
    archived = client.post(
        path(mid) + "/cards/" + card["id"],
        json={
            **values,
            "version": 2,
            "status": "dismissed",
        },
    )
    assert archived.status_code == 200
    assert archived.json()["cards"][0]["status"] == "dismissed"
    # Deletion cascades to protocols/cards as well as existing transcript data.
    assert client.delete(f"/api/v1/meetings/{mid}").status_code == 204
    with app.state.session_factory() as db:
        assert db.get(MeetingBoard, uuid.UUID(mid)) is None


def test_workspace_and_csrf_boundaries(authenticated_client, client, bob):
    mid = meeting(authenticated_client)
    assert client.post("/api/v1/auth/login", json=bob.credentials).status_code == 200
    for suffix in ["", "/export/json", "/export/csv", "/export/ics"]:
        assert client.get(path(mid) + suffix).status_code == 404
    assert client.post(path(mid) + "/generate").status_code == 404
    assert client.post(path(mid) + "/cards", json={"version": 0, "title": "x"}).status_code == 404
    other = meeting(client)
    response = client.post(
        path(other) + "/cards",
        json={"version": 0, "title": "x"},
        headers={"Origin": "https://untrusted.example"},
    )
    assert response.status_code == 403


def test_quote_validation_and_blank_input(authenticated_client):
    client = authenticated_client
    mid = meeting(client)
    for payload in [
        {"version": 0, "title": "   "},
        {"version": 0, "title": "Не было", "quote": "Выдуманная цитата"},
    ]:
        assert client.post(path(mid) + "/cards", json=payload).status_code == 422
    assert client.get(path(mid)).json()["cards"] == []


def test_generation_idempotent_and_preserves_manual_edits(authenticated_client, app):
    client = authenticated_client
    mid = meeting(client)
    assert client.post(path(mid) + "/generate").json()["status"] == "queued"
    assert client.post(path(mid) + "/generate").json()["status"] == "queued"

    def during_generation():
        response = client.post(
            path(mid) + "/cards", json={"version": 0, "title": "Ручное поручение"}
        )
        assert response.status_code == 201

    provider = FakeProvider(callback=during_generation)
    worker = ProtocolWorker(app.state.session_factory, app.state.settings, provider)
    assert worker.run_once()
    board = client.get(path(mid)).json()
    assert board["status"] == "ready" and board["progress"] == 100
    assert len(board["cards"]) == 2
    generated = next(c for c in board["cards"] if c["origin"] == "ai")
    assert generated["assignee"] == "Алия"
    assert generated["due_text"] == "к пятнице"
    assert generated["due_date"] is None
    assert generated["priority"] == "unspecified" and not generated["reviewed"]
    assert TEXT[generated["start_char"] : generated["end_char"]] == generated["quote"]
    assert client.post(path(mid) + "/generate").json()["cards"] == board["cards"]
    assert not worker.run_once()
    assert provider.calls == 1


def test_unsupported_metadata_is_not_published(authenticated_client, app):
    client = authenticated_client
    mid = meeting(client)
    generated = result()
    generated.cards[0].assignee = "Несуществующий исполнитель"
    generated.cards[0].due_text = "31 декабря"
    provider = FakeProvider(generated)
    client.post(path(mid) + "/generate")
    ProtocolWorker(app.state.session_factory, app.state.settings, provider).run_once()
    card = client.get(path(mid)).json()["cards"][0]
    assert card["assignee"] is None and card["due_text"] is None


def test_bad_citation_fails_without_partial_output_and_can_retry(authenticated_client, app):
    client = authenticated_client
    mid = meeting(client)
    client.post(path(mid) + "/generate")
    worker = ProtocolWorker(
        app.state.session_factory,
        app.state.settings,
        FakeProvider(result("Цитата которой не было")),
    )
    worker.run_once()
    board = client.get(path(mid)).json()
    assert board["status"] == "failed" and board["error_code"] == "UNGROUNDED_MODEL_RESPONSE"
    assert board["cards"] == [] and board["summary"] == []
    client.post(path(mid) + "/generate")
    ProtocolWorker(app.state.session_factory, app.state.settings, FakeProvider()).run_once()
    assert client.get(path(mid)).json()["status"] == "ready"


def test_expired_lease_is_reclaimed_and_old_worker_cannot_publish(authenticated_client, app):
    client = authenticated_client
    mid = meeting(client)
    client.post(path(mid) + "/generate")
    worker = ProtocolWorker(app.state.session_factory, app.state.settings, FakeProvider())
    meeting_id, token = worker.claim()
    with app.state.session_factory() as db:
        db.execute(
            update(MeetingBoard)
            .where(MeetingBoard.meeting_id == meeting_id)
            .values(lease_until=utcnow() - timedelta(seconds=1))
        )
        db.commit()
    assert not worker.heartbeat(meeting_id, token, 40)
    worker.run_once()
    worker.fail(meeting_id, token, "STALE_WORKER")
    assert client.get(path(mid)).json()["status"] == "ready"


def test_export_formula_neutralization_and_calendar(authenticated_client):
    client = authenticated_client
    mid = meeting(client)
    response = client.post(
        path(mid) + "/cards",
        json={
            "version": 0,
            "title": '=HYPERLINK("https://example.com")',
            "assignee": "+actor",
            "due_date": "2026-09-18",
            "reviewed": True,
            "agreement": "confirmed",
            "description": "Текст; с, запятой\n" * 12,
        },
    )
    assert response.status_code == 201
    assert (
        client.post(
            path(mid) + "/cards",
            json={
                "version": 1,
                "title": "Не проверено",
                "due_date": "2026-09-19",
            },
        ).status_code
        == 201
    )
    data = client.get(path(mid) + "/export/json").json()
    assert data["format_version"] == 1 and len(data["cards"]) == 2
    response = client.get(path(mid) + "/export/csv")
    rows = list(csv.reader(io.StringIO(response.text.lstrip("\ufeff"))))
    assert rows[1][1].startswith("'=") and rows[1][3].startswith("'+")
    ics = client.get(path(mid) + "/export/ics").text
    assert ics.count("BEGIN:VEVENT") == 1
    assert "DTSTART;VALUE=DATE:20260918" in ics
    assert "DURATION:P1D" in ics
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))


def test_long_transcript_chunks_cover_all_characters():
    source = ("Начало строки\n" * 18000)[:200000]
    covered = bytearray(len(source))
    for offset, text in chunks(source):
        assert 0 < len(text) <= 12000
        assert text == source[offset : offset + len(text)]
        covered[offset : offset + len(text)] = b"1" * len(text)
    assert all(covered)


def test_local_provider_never_uses_cloud_even_with_cloud_rag(app):
    settings = app.state.settings
    seen = []

    def transport(request):
        seen.append(str(request.url))
        assert request.url.host == "host.docker.internal"
        assert request.url.path == "/api/chat"
        payload = json.loads(request.content)
        assert payload["format"]["properties"]["cards"]
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {
                    "content": result().model_dump_json(),
                },
            },
        )

    provider = ProtocolProvider(settings, httpx.MockTransport(transport))
    assert provider.generate(TEXT).cards[0].assignee == "Алия"
    assert len(seen) == 1
    settings.intelligence_local_url = "https://public.example.com"
    with pytest.raises(ValueError):
        ProtocolProvider(settings)


def test_migration_roundtrip(tmp_path, monkeypatch):
    from pathlib import Path

    from sqlalchemy import create_engine

    url = f"sqlite:///{tmp_path / 'migrations.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    api_root = Path(__file__).parents[1]
    config = Config(str(api_root / "alembic.ini"))
    config.set_main_option("script_location", str(api_root / "migrations"))
    command.upgrade(config, "0004_meeting_boards")
    engine = create_engine(url)
    assert "meeting_boards" in inspect(engine).get_table_names()
    command.downgrade(config, "0003_meeting_rag")
    assert "meeting_boards" not in inspect(engine).get_table_names()
    command.upgrade(config, "0004_meeting_boards")
    assert "meeting_boards" in inspect(engine).get_table_names()
    engine.dispose()


def test_grounded_summaries_are_visible_before_final_publication(authenticated_client, app):
    client = authenticated_client
    mid = meeting(client, TEXT + "\n" + "Детали обсуждения. " * 900)
    client.post(path(mid) + "/generate")
    observed = []

    class StreamingProvider:
        def generate(self, text, **kwargs):
            observed.append(client.get(path(mid)).json())
            quote = text.strip().split("\n")[0][:100]
            return GeneratedProtocol.model_validate(
                {"summary": [{"text": "Промежуточный вывод", "quote": quote}], "cards": []}
            )

    worker = ProtocolWorker(app.state.session_factory, app.state.settings, StreamingProvider())
    assert worker.run_once()
    assert len(observed) >= 2
    assert observed[0]["status"] == "running" and observed[0]["summary"] == []
    assert observed[1]["status"] == "running" and observed[1]["summary"]
    assert client.get(path(mid)).json()["status"] == "ready"


def test_partial_summary_cannot_be_published_after_lease_loss(authenticated_client, app):
    mid = meeting(authenticated_client)
    authenticated_client.post(path(mid) + "/generate")
    worker = ProtocolWorker(app.state.session_factory, app.state.settings, FakeProvider())
    meeting_id, token = worker.claim()
    with app.state.session_factory() as db:
        db.execute(update(MeetingBoard).values(lease_until=utcnow() - timedelta(seconds=1)))
        db.commit()
    assert not worker.publish_summary(meeting_id, token, [{"text": "STALE", "quote": TEXT}], 50)
    assert authenticated_client.get(path(mid)).json()["summary"] == []


def test_openai_protocol_uses_explicit_model_and_strict_schema(app):
    from pydantic import SecretStr

    from aimeet_api.modules.rag.response_schema import strict_response_schema

    settings = app.state.settings
    settings.intelligence_provider = "openai"
    settings.intelligence_openai_model = "gpt-5.6-luna"
    settings.openai_api_key = SecretStr("test")

    def transport(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        payload = json.loads(request.content)
        assert payload["model"] == "gpt-5.6-luna"
        assert payload["reasoning"] == {"effort": "low"}
        assert payload["input"][0]["content"] == TEXT
        assert payload["text"]["format"]["schema"] == strict_response_schema(GeneratedProtocol)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": result().model_dump_json()}],
                    }
                ],
            },
        )

    assert ProtocolProvider(settings, httpx.MockTransport(transport)).generate(TEXT).cards
