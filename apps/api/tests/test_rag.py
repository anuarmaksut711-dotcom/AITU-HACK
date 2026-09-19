import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from aimeet_api.core.config import Settings
from aimeet_api.db.models import Meeting, utcnow
from aimeet_api.modules.rag.chunking import build_graph, embedding_profile
from aimeet_api.modules.rag.indexing import IndexWorker
from aimeet_api.modules.rag.models import RagEdge, RagIndex, RagNode
from aimeet_api.modules.rag.providers import RagError
from aimeet_api.modules.rag.router import get_providers
from aimeet_api.modules.rag.schemas import GeneratedAnswer


class FakeProviders:
    """Deterministic test double, never selectable by runtime configuration."""

    calls = 0

    def ensure_configured(self, **_):
        pass

    def embed(self, texts):
        self.calls += 1
        return [[1.0, 0.0, 0.0] if "бюджет" in text.lower() else [0.0, 1.0, 0.0] for text in texts]

    def generate(self, instructions, context):
        import json

        source = json.loads(context)["untrusted_transcript_sources"][0]
        return GeneratedAnswer.model_validate(
            {
                "status": "answered",
                "claims": [
                    {
                        "text": "Обсудили бюджет.",
                        "evidence": [
                            {
                                "source_id": source["source_id"],
                                "quote": source["text"][:100],
                            }
                        ],
                    }
                ],
            }
        )


@pytest.fixture
def rag(app):
    app.state.settings.rag_embedding_dimensions = 3
    fake = FakeProviders()
    app.dependency_overrides[get_providers] = lambda: fake
    yield fake
    app.dependency_overrides.clear()


def create_meeting(client, text="Алия: Бюджет 500 тысяч.\nМарат: Нет, согласовали 400 тысяч."):
    response = client.post("/api/v1/meetings", json={"title": "Бюджет", "transcript": text})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def index_meeting(app, client, fake, meeting_id):
    response = client.post(f"/api/v1/meetings/{meeting_id}/rag/index")
    assert response.status_code == 202, response.text
    worker = IndexWorker(app.state.session_factory, app.state.settings, fake)
    assert worker.run_once()
    status = client.get(f"/api/v1/meetings/{meeting_id}/rag/index").json()
    assert status["status"] == "ready", status
    return status["index_id"]


def test_graph_lossless_and_deterministic():
    text = ("Алия: Бюджет 🧪 400 мың теңге.\nBob: Agreed.\n" * 300) + "x" * 1900
    settings = Settings(_env_file=None)
    index_id = uuid.uuid4()
    parents, children, edges = build_graph(index_id, text, settings)
    assert "".join(node.text for node in parents) == text
    assert "".join(node.text for node in children) == text
    for node in parents + children:
        assert node.text == text[node.start_char : node.end_char]
    parent_map = {node.id: node for node in parents}
    for child in children:
        parent = parent_map[child.parent_id]
        assert parent.start_char <= child.start_char < child.end_char <= parent.end_char
    ids = {node.id for node in parents + children}
    assert all(edge.source_id in ids and edge.target_id in ids for edge in edges)
    assert {edge.relation for edge in edges} == {"parent", "child", "next", "previous"}
    assert [node.id for node in children] == [
        node.id for node in build_graph(index_id, text, settings)[1]
    ]


def test_chat_sources_and_delete_cascade(app, authenticated_client, rag):
    client = authenticated_client
    text = "Алия: Бюджет 500 тысяч.\nМарат: Нет, согласовали 400 тысяч."
    meeting_id = create_meeting(client, text)
    index_id = index_meeting(app, client, rag, meeting_id)
    again = client.post(f"/api/v1/meetings/{meeting_id}/rag/index").json()
    assert again["index_id"] == index_id
    graph = client.get(f"/api/v1/meetings/{meeting_id}/rag/graph").json()
    assert len(graph["nodes"]) == 2
    response = client.post(f"/api/v1/meetings/{meeting_id}/rag/chat", json={"question": "Бюджет?"})
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["status"] == "answered"
    for claim in answer["claims"]:
        for citation in claim["citations"]:
            assert text[citation["start_char"] : citation["end_char"]] == citation["quote"]
    assert client.delete(f"/api/v1/meetings/{meeting_id}").status_code == 204
    with app.state.session_factory() as db:
        for model in (RagIndex, RagNode, RagEdge):
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_other_workspace_cannot_index_search_or_read(app, authenticated_client, bob, rag):
    client = authenticated_client
    meeting_id = create_meeting(client)
    index_meeting(app, client, rag, meeting_id)
    calls = rag.calls
    client.post("/api/v1/auth/logout")
    assert client.post("/api/v1/auth/login", json=bob.credentials).status_code == 200
    for endpoint in ("index", "search", "chat"):
        response = client.post(
            f"/api/v1/meetings/{meeting_id}/rag/{endpoint}",
            json={"question": "Бюджет?"} if endpoint != "index" else None,
        )
        assert response.status_code == 404
    for endpoint in ("index", "graph"):
        assert client.get(f"/api/v1/meetings/{meeting_id}/rag/{endpoint}").status_code == 404
    assert rag.calls == calls


def test_missing_index_and_key_are_explicit(app, authenticated_client):
    client = authenticated_client
    meeting_id = create_meeting(client)
    app.state.settings.openai_api_key = Settings().openai_api_key
    assert client.get(f"/api/v1/meetings/{meeting_id}/rag/index").json()["status"] == "not_indexed"
    response = client.post(f"/api/v1/meetings/{meeting_id}/rag/index")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "OPENAI_KEY_REQUIRED"
    response = client.post(f"/api/v1/meetings/{meeting_id}/rag/search", json={"question": "test"})
    assert response.status_code == 409


def test_lease_fencing_and_recovery(app, authenticated_client, rag):
    meeting_id = create_meeting(authenticated_client)
    response = authenticated_client.post(f"/api/v1/meetings/{meeting_id}/rag/index").json()
    index_id = uuid.UUID(response["index_id"])
    worker = IndexWorker(app.state.session_factory, app.state.settings, rag)
    first = worker.claim()
    assert first and worker.claim() is None
    with app.state.session_factory() as db:
        db.execute(
            update(RagIndex)
            .where(RagIndex.id == index_id)
            .values(
                lease_until=utcnow() - timedelta(seconds=1),
            )
        )
        db.commit()
    second = worker.claim()
    assert second and first[1] != second[1]
    assert not worker.heartbeat(*first)
    worker.fail(*first, RagError("STALE_WORKER"))
    with app.state.session_factory() as db:
        assert db.get(RagIndex, index_id).lease_token == second[1]
        db.execute(
            update(RagIndex)
            .where(RagIndex.id == index_id)
            .values(
                lease_until=utcnow() - timedelta(seconds=1),
            )
        )
        db.commit()
    assert worker.run_once()
    assert (
        authenticated_client.get(f"/api/v1/meetings/{meeting_id}/rag/index").json()["status"]
        == "ready"
    )


def test_failed_embedding_is_not_partial_index(app, authenticated_client, rag):
    meeting_id = create_meeting(authenticated_client)
    authenticated_client.post(f"/api/v1/meetings/{meeting_id}/rag/index")

    def fail(_):
        raise RagError("EMBEDDING_DIMENSION_MISMATCH", 502)

    rag.embed = fail
    assert IndexWorker(app.state.session_factory, app.state.settings, rag).run_once()
    status = authenticated_client.get(f"/api/v1/meetings/{meeting_id}/rag/index").json()
    assert status["status"] == "failed"
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(RagNode)) == 0
    assert (
        authenticated_client.post(f"/api/v1/meetings/{meeting_id}/rag/index").json()["status"]
        == "queued"
    )


def test_changed_profile_and_transcript_require_new_index(app, authenticated_client, rag):
    client = authenticated_client
    meeting_id = create_meeting(client)
    first = index_meeting(app, client, rag, meeting_id)
    profile = embedding_profile(app.state.settings)
    app.state.settings.rag_llm_model = "some-local-model"
    assert embedding_profile(app.state.settings) == profile
    app.state.settings.rag_embedding_revision = "2"
    assert client.get(f"/api/v1/meetings/{meeting_id}/rag/index").json()["status"] == "not_indexed"
    second = index_meeting(app, client, rag, meeting_id)
    assert first != second
    with app.state.session_factory() as db:
        meeting = db.get(Meeting, uuid.UUID(meeting_id))
        meeting.transcript = "Новый бюджет: 100 тенге."
        db.commit()
    assert client.get(f"/api/v1/meetings/{meeting_id}/rag/index").json()["status"] == "not_indexed"
    third = index_meeting(app, client, rag, meeting_id)
    assert third != second


def test_invented_citation_rejected(app, authenticated_client, rag):
    meeting_id = create_meeting(authenticated_client)
    index_meeting(app, authenticated_client, rag, meeting_id)
    rag.generate = lambda *_: GeneratedAnswer.model_validate(
        {
            "status": "answered",
            "claims": [
                {
                    "text": "False claim",
                    "evidence": [
                        {"source_id": "S1", "quote": "Invented quote"},
                    ],
                }
            ],
        }
    )
    response = authenticated_client.post(
        f"/api/v1/meetings/{meeting_id}/rag/chat", json={"question": "Бюджет?"}
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UNGROUNDED_MODEL_RESPONSE"


def test_no_evidence_does_not_call_generation(app, authenticated_client, rag):
    meeting_id = create_meeting(authenticated_client)
    index_meeting(app, authenticated_client, rag, meeting_id)
    rag.embed = lambda _: [[0.0, 0.0, 1.0]]

    def forbidden(*_):
        raise AssertionError("No generation without evidence")

    rag.generate = forbidden
    response = authenticated_client.post(
        f"/api/v1/meetings/{meeting_id}/rag/chat", json={"question": "Марсианские корабли?"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "insufficient_evidence"
    assert response.json()["sources"] == []


def test_graph_expansion_crosses_parent_boundary_and_obeys_budget(app, authenticated_client, rag):
    from aimeet_api.modules.rag.retrieval import retrieve

    config = app.state.settings
    config.rag_parent_chars = 800
    config.rag_child_chars = 200
    config.rag_top_k = 1
    config.rag_context_chars = 4000
    # No whitespace splitting: the match ends exactly at a parent boundary.
    text = "a" * 770 + " бюджет " + "a" * 22 + "ОТМЕНА: решение пересмотрели." + "z" * 10000
    assert text.index("ОТМЕНА") == 800
    meeting_id = create_meeting(authenticated_client, text)
    index_id = index_meeting(app, authenticated_client, rag, meeting_id)
    with app.state.session_factory() as db:
        sources = retrieve(db, uuid.UUID(index_id), "бюджет", [1, 0, 0], config)
    assert any("ОТМЕНА" in source.text for source in sources)
    assert sum(len(source.text) + 160 for source in sources) <= config.rag_context_chars
    assert all(
        left.end_char <= right.start_char for left, right in zip(sources, sources[1:], strict=False)
    )
    assert all(source.text == text[source.start_char : source.end_char] for source in sources)


def test_lost_lease_cannot_publish_after_provider_returns(app, authenticated_client, rag):
    meeting_id = create_meeting(authenticated_client)
    response = authenticated_client.post(f"/api/v1/meetings/{meeting_id}/rag/index").json()
    index_id = uuid.UUID(response["index_id"])
    replacement_token = uuid.uuid4()
    embed = rag.embed

    def steal(texts):
        with app.state.session_factory() as db:
            db.execute(
                update(RagIndex)
                .where(RagIndex.id == index_id)
                .values(
                    lease_token=replacement_token,
                )
            )
            db.commit()
        return embed(texts)

    rag.embed = steal
    assert IndexWorker(app.state.session_factory, app.state.settings, rag).run_once()
    with app.state.session_factory() as db:
        assert db.get(RagIndex, index_id).status == "running"
        assert db.get(RagIndex, index_id).lease_token == replacement_token
        assert db.scalar(select(func.count()).select_from(RagNode)) == 0


def test_concurrent_claim_has_one_winner(app, authenticated_client, rag):
    from concurrent.futures import ThreadPoolExecutor

    meeting_id = create_meeting(authenticated_client)
    authenticated_client.post(f"/api/v1/meetings/{meeting_id}/rag/index")
    worker = IndexWorker(app.state.session_factory, app.state.settings, rag)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: worker.claim(), range(2)))
    assert len([claim for claim in claims if claim is not None]) == 1


def test_source_changed_during_generation_is_not_returned(app, authenticated_client, rag):
    meeting_id = create_meeting(authenticated_client)
    index_meeting(app, authenticated_client, rag, meeting_id)
    generate = rag.generate

    def change_source(*args):
        with app.state.session_factory() as db:
            db.execute(
                update(Meeting)
                .where(Meeting.id == uuid.UUID(meeting_id))
                .values(
                    transcript="Бюджет полностью отменён.",
                )
            )
            db.commit()
        return generate(*args)

    rag.generate = change_source
    response = authenticated_client.post(
        f"/api/v1/meetings/{meeting_id}/rag/chat", json={"question": "Бюджет?"}
    )
    assert response.status_code == 409
