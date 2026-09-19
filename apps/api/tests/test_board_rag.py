import json
import uuid

import pytest
from sqlalchemy import select
from test_rag import FakeProviders, create_meeting, index_meeting

from aimeet_api.db.models import Meeting
from aimeet_api.modules.intelligence.models import MeetingBoard
from aimeet_api.modules.intelligence.schemas import Card, CardInput
from aimeet_api.modules.intelligence.service import digest
from aimeet_api.modules.rag.board_retrieval import retrieve_board_sources
from aimeet_api.modules.rag.models import RagIndex
from aimeet_api.modules.rag.router import get_providers
from aimeet_api.modules.rag.schemas import GeneratedAnswer


@pytest.fixture
def rag(app):
    app.state.settings.rag_embedding_dimensions = 3
    fake = FakeProviders()
    app.dependency_overrides[get_providers] = lambda: fake
    yield fake
    app.dependency_overrides.clear()


def add_card(client, mid, **values):
    response = client.post(
        f"/api/v1/meetings/{mid}/board/cards",
        json={
            "version": 0,
            "title": "Разработать фронтенд",
            "assignee": "Команда 1",
            "status": "doing",
            "reviewed": True,
            "agreement": "confirmed",
            **values,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def from_board(instructions, context):
    sources = json.loads(context)["untrusted_board_sources"]
    source = next(s for s in sources if s["kind"] == "kanban")
    return GeneratedAnswer.model_validate(
        {
            "status": "answered",
            "claims": [
                {
                    "text": source["text"],
                    "evidence": [{"source_id": source["source_id"], "quote": source["text"]}],
                }
            ],
        }
    )


def test_board_and_summary_supplement_transcript(app, authenticated_client, rag):
    client = authenticated_client
    transcript = "Марат: фронтанчат должна взять первая команда."
    mid = create_meeting(client, transcript)
    add_card(client, mid, due_date="2026-09-15", quote=transcript)
    index_meeting(app, client, rag, mid)
    with app.state.session_factory() as db:
        board = db.get(MeetingBoard, uuid.UUID(mid))
        board.status = "ready"
        board.source_hash = digest(transcript)
        board.summary = [
            {"text": "Цель — разработать фронтенд силами первой команды.", "quote": transcript}
        ]
        db.commit()

    def generate(instructions, context):
        data = json.loads(context)
        assert "current kanban fields" in instructions
        assert data["untrusted_transcript_sources"][0]["text"] == transcript
        sources = data["untrusted_board_sources"]
        assert {s["kind"] for s in sources} == {"kanban", "summary"}
        card = next(s for s in sources if s["kind"] == "kanban")
        assert "Название: Разработать фронтенд" in card["text"]
        assert "Статус: В работе" in card["text"]
        assert "2026-09-15" in card["text"]
        assert card["transcript_quote"] == transcript
        return GeneratedAnswer.model_validate(
            {
                "status": "answered",
                "claims": [
                    {
                        "text": "Команда 1 разрабатывает фронтенд.",
                        "evidence": [
                            {"source_id": s["source_id"], "quote": s["text"]} for s in sources
                        ],
                    }
                ],
            }
        )

    rag.generate = generate
    response = client.post("/api/v1/rag/chat", json={"question": "Какие задачи по фронтенду?"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result["board_sources"]) == 2
    assert all(c["kind"] == "board" for c in result["claims"][0]["citations"])
    assert result["sources"][0]["text"] == transcript


def test_kanban_edit_is_visible_without_reindexing(app, authenticated_client, rag):
    client = authenticated_client
    mid = create_meeting(client)
    board = add_card(client, mid)
    index_id = index_meeting(app, client, rag, mid)
    rag.generate = from_board
    before = client.post("/api/v1/rag/chat", json={"question": "Какие задачи?"})
    assert before.status_code == 200, before.text
    assert "Статус: В работе" in before.json()["answer"]
    card = board["cards"][0]
    values = {k: v for k, v in card.items() if k in CardInput.model_fields}
    edited = client.post(
        f"/api/v1/meetings/{mid}/board/cards/{card['id']}",
        json={
            **values,
            "version": board["version"],
            "status": "done",
            "assignee": "Команда 2",
        },
    )
    assert edited.status_code == 200, edited.text
    after = client.post("/api/v1/rag/chat", json={"question": "Что с фронтендом?"})
    assert after.status_code == 200, after.text
    assert "Статус: Готово" in after.json()["answer"]
    assert "Ответственный: Команда 2" in after.json()["answer"]
    with app.state.session_factory() as db:
        assert [str(row.id) for row in db.scalars(select(RagIndex))] == [index_id]


def test_boards_are_workspace_scoped(app, authenticated_client, bob, rag):
    client = authenticated_client
    foreign = create_meeting(client)
    add_card(client, foreign, title="Чужая секретная задача")
    client.post("/api/v1/auth/logout")
    client.post("/api/v1/auth/login", json=bob.credentials)
    own = create_meeting(client)
    add_card(client, own, title="Своя задача")
    rag.generate = from_board
    response = client.post("/api/v1/rag/chat", json={"question": "Какие задачи?"})
    assert response.status_code == 200, response.text
    assert {s["meeting_id"] for s in response.json()["board_sources"]} == {own}
    assert "секретная" not in response.text


def test_changed_board_is_not_returned(app, authenticated_client, rag):
    client = authenticated_client
    mid = create_meeting(client)
    add_card(client, mid)

    def generate(*args):
        with app.state.session_factory() as db:
            board = db.get(MeetingBoard, uuid.UUID(mid))
            board.cards = []
            board.version += 1
            db.commit()
        return from_board(*args)

    rag.generate = generate
    response = client.post("/api/v1/rag/chat", json={"question": "Какие задачи?"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SOURCE_CHANGED"


def test_stale_ai_extraction_excluded_but_manual_cards_retained(app, authenticated_client):
    mid = create_meeting(authenticated_client, "Новый текст")
    add_card(authenticated_client, mid)
    with app.state.session_factory() as db:
        board = db.get(MeetingBoard, uuid.UUID(mid))
        board.source_hash = digest("Старый текст")
        board.status = "ready"
        board.summary = [{"text": "Старый вывод", "quote": "Старый текст"}]
        board.cards = [
            *board.cards,
            Card(
                id=uuid.uuid4(),
                title="Устаревшая задача",
                origin="ai",
                reviewed=False,
                quote="Старый текст",
            ).model_dump(mode="json"),
        ]
        db.commit()
        workspace_id = db.get(Meeting, uuid.UUID(mid)).workspace_id
        sources, _, _ = retrieve_board_sources(db, workspace_id, "задачи")
        assert len(sources) == 1
        assert sources[0].title == "Разработать фронтенд"
        assert sources[0].transcript_current is False
        assert sources[0].transcript_quote is None


def test_corrected_term_is_ranked_under_context_budget(app, authenticated_client):
    mid = create_meeting(authenticated_client, "Распознано с ошибками")
    add_card(authenticated_client, mid)
    with app.state.session_factory() as db:
        board = db.get(MeetingBoard, uuid.UUID(mid))
        board.cards = [
            Card(id=uuid.uuid4(), title=f"Задача {i}", origin="manual").model_dump(mode="json")
            for i in range(50)
        ] + [
            Card(id=uuid.uuid4(), title="Фронтенд Орбита", origin="manual").model_dump(mode="json")
        ]
        db.commit()
        workspace_id = db.get(Meeting, uuid.UUID(mid)).workspace_id
        sources, coverage, _ = retrieve_board_sources(db, workspace_id, "Орбита", budget=1400)
        assert sources[0].title == "Фронтенд Орбита"
        assert sum(len(s.model_dump_json()) for s in sources) <= 1400
        assert coverage.available_sources == 51
        assert coverage.selected_sources < coverage.available_sources
