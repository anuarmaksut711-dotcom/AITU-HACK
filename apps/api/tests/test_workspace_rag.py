import json
import uuid

import pytest
from sqlalchemy import update
from test_rag import FakeProviders, create_meeting, index_meeting

from aimeet_api.db.models import Meeting
from aimeet_api.modules.rag.indexing import IndexWorker
from aimeet_api.modules.rag.router import get_providers
from aimeet_api.modules.rag.schemas import GeneratedAnswer


@pytest.fixture
def rag(app):
    app.state.settings.rag_embedding_dimensions = 3
    fake = FakeProviders()
    app.dependency_overrides[get_providers] = lambda: fake
    yield fake
    app.dependency_overrides.clear()


def test_workspace_search_keeps_distinct_meetings_and_citations(app, authenticated_client, rag):
    client = authenticated_client
    texts = ["Алия: Бюджет рекламы — 400 тысяч.", "Марат: Бюджет разработки — 900 тысяч."]
    ids = [create_meeting(client, text) for text in texts]
    for number, meeting_id in enumerate(ids):
        with app.state.session_factory() as db:
            db.execute(
                update(Meeting)
                .where(Meeting.id == uuid.UUID(meeting_id))
                .values(title=f"Встреча {number}")
            )
            db.commit()
        index_meeting(app, client, rag, meeting_id)

    def generate(instructions, context):
        assert "workspace meetings" in instructions
        sources = json.loads(context)["untrusted_transcript_sources"]
        assert {source["meeting_id"] for source in sources} == set(ids)
        assert {source["meeting_title"] for source in sources} == {"Встреча 0", "Встреча 1"}
        return GeneratedAnswer.model_validate(
            {
                "status": "answered",
                "claims": [
                    {
                        "text": source["meeting_title"],
                        "evidence": [{"source_id": source["source_id"], "quote": source["text"]}],
                    }
                    for source in sources
                ],
            }
        )

    rag.generate = generate
    response = client.post("/api/v1/rag/chat", json={"question": "На какой встрече бюджет?"})
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["coverage"]["ready"] == answer["coverage"]["total"] == 2
    sources = {source["source_id"]: source for source in answer["sources"]}
    assert len(sources) == 2
    for claim in answer["claims"]:
        for citation in claim["citations"]:
            source = sources[citation["source_id"]]
            transcript = texts[ids.index(source["meeting_id"])]
            assert transcript[citation["start_char"] : citation["end_char"]] == citation["quote"]
    assert (
        sum(len(source["text"]) + 160 for source in sources.values())
        <= app.state.settings.rag_context_chars
    )


def test_workspace_preparation_and_search_are_tenant_scoped(app, authenticated_client, bob, rag):
    client = authenticated_client
    alice_id = create_meeting(client)
    index_meeting(app, client, rag, alice_id)
    client.post("/api/v1/auth/logout")
    client.post("/api/v1/auth/login", json=bob.credentials)
    assert client.get("/api/v1/rag/index").json()["total"] == 0
    calls = rag.calls
    empty = client.post("/api/v1/rag/chat", json={"question": "Бюджет?"})
    assert empty.status_code == 200
    assert empty.json()["sources"] == []
    assert empty.json()["coverage"]["total"] == 0
    assert rag.calls == calls
    own_id = create_meeting(client, "Наш бюджет — 300 тенге.")
    response = client.post("/api/v1/rag/index")
    assert response.status_code == 202
    assert response.json()["pending"] == 1
    assert IndexWorker(app.state.session_factory, app.state.settings, rag).run_once()
    response = client.post("/api/v1/rag/chat", json={"question": "Бюджет?"})
    assert response.status_code == 200, response.text
    assert {s["meeting_id"] for s in response.json()["sources"]} == {own_id}
    assert response.json()["coverage"]["total"] == 1


def test_workspace_coverage_excludes_stale_and_unavailable_sources(app, authenticated_client, rag):
    client = authenticated_client
    meeting_id = create_meeting(client)
    index_meeting(app, client, rag, meeting_id)
    with app.state.session_factory() as db:
        db.execute(
            update(Meeting)
            .where(Meeting.id == uuid.UUID(meeting_id))
            .values(transcript="Бюджет изменён.", transcript_length=15)
        )
        db.commit()
    coverage = client.get("/api/v1/rag/index").json()
    assert coverage["ready"] == 0
    assert coverage["not_indexed"] == 1
    assert client.post("/api/v1/rag/index").json()["pending"] == 1
    assert client.post("/api/v1/rag/index").json()["pending"] == 1
    assert IndexWorker(app.state.session_factory, app.state.settings, rag).run_once()
    assert client.get("/api/v1/rag/index").json()["ready"] == 1
    with app.state.session_factory() as db:
        db.execute(
            update(Meeting)
            .where(Meeting.id == uuid.UUID(meeting_id))
            .values(transcript="", transcript_length=0)
        )
        db.commit()
    coverage = client.get("/api/v1/rag/index").json()
    assert coverage["unavailable"] == 1
    assert coverage["ready"] == 0


def test_workspace_rechecks_sources_after_provider_io(app, authenticated_client, rag):
    client = authenticated_client
    meeting_id = create_meeting(client)
    index_meeting(app, client, rag, meeting_id)
    generate = rag.generate

    def mutate(*args):
        with app.state.session_factory() as db:
            db.execute(
                update(Meeting)
                .where(Meeting.id == uuid.UUID(meeting_id))
                .values(transcript="Бюджет отменён.")
            )
            db.commit()
        return generate(*args)

    rag.generate = mutate
    response = client.post("/api/v1/rag/chat", json={"question": "Бюджет?"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SOURCE_CHANGED"


def test_workspace_empty_evidence_and_partial_coverage(app, authenticated_client, rag):
    client = authenticated_client
    meeting_id = create_meeting(client)
    index_meeting(app, client, rag, meeting_id)
    create_meeting(client, "Ещё не подготовленная встреча.")
    rag.embed = lambda _: [[0.0, 0.0, 1.0]]

    def no_topic_evidence(instructions, context):
        payload = json.loads(context)
        assert payload["untrusted_transcript_sources"] == []
        assert payload["untrusted_catalog_sources"]
        return GeneratedAnswer(status="insufficient_evidence", claims=[])

    rag.generate = no_topic_evidence
    response = client.post("/api/v1/rag/chat", json={"question": "Марсианские корабли?"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "insufficient_evidence"
    assert response.json()["sources"] == []
    assert response.json()["coverage"]["ready"] == 1
    assert response.json()["coverage"]["not_indexed"] == 1


def test_workspace_rejects_fabricated_evidence(app, authenticated_client, rag):
    client = authenticated_client
    index_meeting(app, client, rag, create_meeting(client))
    rag.generate = lambda *_: GeneratedAnswer.model_validate(
        {
            "status": "answered",
            "claims": [
                {
                    "text": "False",
                    "evidence": [{"source_id": "S1", "quote": "Несуществующая цитата"}],
                }
            ],
        }
    )
    response = client.post("/api/v1/rag/chat", json={"question": "Бюджет?"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UNGROUNDED_MODEL_RESPONSE"


def test_workspace_rechecks_access_after_embedding(app, authenticated_client, bob, rag):
    client = authenticated_client
    meeting_id = create_meeting(client)
    index_meeting(app, client, rag, meeting_id)
    embed = rag.embed

    def remove(texts):
        assert client.delete(f"/api/v1/meetings/{meeting_id}").status_code == 204
        return embed(texts)

    rag.embed = remove
    response = client.post("/api/v1/rag/chat", json={"question": "Бюджет?"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SOURCE_CHANGED"


def test_workspace_routes_require_auth_and_csrf(client, authenticated_client, rag):
    client = authenticated_client
    client.headers.pop("X-Requested-With")
    for path in ("index", "chat"):
        response = client.post(f"/api/v1/rag/{path}", json={"question": "Бюджет?"})
        assert response.status_code == 403
    client.headers["X-Requested-With"] = "aimeet"
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/rag/index").status_code == 401
    assert client.post("/api/v1/rag/chat", json={"question": "Бюджет?"}).status_code == 401


def test_workspace_catalog_answers_existence_without_transcript_hits(
    app, authenticated_client, rag
):
    client = authenticated_client
    meeting_id = create_meeting(client)

    # No prepared transcript: archive facts must still reach the model.
    def catalog_answer(instructions, context):
        payload = json.loads(context)
        assert payload["untrusted_transcript_sources"] == []
        catalog = payload["untrusted_catalog_sources"]
        assert str(meeting_id) == catalog[1]["meeting_id"]
        assert "Сохранено встреч в рабочем пространстве: 1." in catalog[0]["text"]
        return GeneratedAnswer.model_validate(
            {
                "status": "answered",
                "claims": [
                    {
                        "text": "Да, в архиве сохранена одна встреча.",
                        "evidence": [
                            {
                                "source_id": "M0",
                                "quote": "Сохранено встреч в рабочем пространстве: 1.",
                            }
                        ],
                    }
                ],
            }
        )

    rag.generate = catalog_answer
    response = client.post("/api/v1/rag/chat", json={"question": "у нас вообще встречи были?"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "answered"
    assert response.json()["claims"][0]["citations"][0]["kind"] == "catalog"
    assert response.json()["sources"] == []
    assert rag.calls == 0


def test_workspace_catalog_rechecked_after_generation(app, authenticated_client, rag):
    client = authenticated_client
    index_meeting(app, client, rag, create_meeting(client))
    generate = rag.generate

    def change_catalog(*args):
        create_meeting(client, "Другая встреча")
        return generate(*args)

    rag.generate = change_catalog
    response = client.post("/api/v1/rag/chat", json={"question": "Бюджет?"})
    assert response.status_code == 409


@pytest.mark.parametrize("view", ["kanban", "insights", "conversation", None, "foreign"])
def test_model_panel_action_is_optional_and_scoped(authenticated_client, rag, view):
    mid = create_meeting(authenticated_client)

    def answer(instructions, context):
        assert "Do not open a panel merely because a board source was cited" in instructions
        catalog = json.loads(context)["untrusted_catalog_sources"]
        source = next(source for source in catalog if source["meeting_id"] == mid)
        panel = None if view is None else {
            "meeting_id": str(uuid.uuid4()) if view == "foreign" else mid,
            "view": "kanban" if view == "foreign" else view,
        }
        return GeneratedAnswer.model_validate({
            "status": "answered",
            "claims": [{"text": "Meeting found.", "evidence": [
                {"source_id": source["source_id"], "quote": source["text"]}
            ]}],
            "panel": panel,
        })

    rag.generate = answer
    response = authenticated_client.post("/api/v1/rag/chat", json={"question": "Покажи канбан"})
    assert response.status_code == 200, response.text
    assert response.json()["answer"] == "Meeting found."
    assert response.json()["panel"] == (
        None if view in {None, "foreign"} else {"meeting_id": mid, "view": view}
    )
