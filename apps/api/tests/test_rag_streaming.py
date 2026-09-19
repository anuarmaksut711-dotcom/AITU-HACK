import json

import httpx
import pytest
from pydantic import SecretStr
from test_rag import FakeProviders, create_meeting, index_meeting

from aimeet_api.core.config import Settings
from aimeet_api.modules.rag.assistant import stream_reply
from aimeet_api.modules.rag.providers import Providers, RagError
from aimeet_api.modules.rag.router import get_providers
from aimeet_api.modules.rag.schemas import AssistantQuestion, GeneratedAnswer
from aimeet_api.modules.rag.streaming import completed_claims, event_stream, string_field_prefix
from aimeet_api.modules.rag.workspace import workspace_events


def event(data):
    return ("data: " + json.dumps(data, ensure_ascii=False) + "\n\n").encode()


def test_real_provider_stream_yields_before_completion():
    seen = []

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield event(
                {
                    "type": "response.output_text.delta",
                    "delta": '{"action":"reply","answer":"Привет',
                }
            )
            seen.append("second")
            yield event(
                {"type": "response.output_text.delta", "delta": ', Алия!","search_query":""}'}
            )
            seen.append("terminal")
            yield event({"type": "response.completed", "response": {"status": "completed"}})

    def handler(request):
        payload = json.loads(request.content)
        assert payload["stream"] is True
        assert payload["reasoning"]["effort"] == "max"
        assert payload["max_output_tokens"] == 16384
        return httpx.Response(200, stream=Stream())

    provider = Providers(
        Settings(_env_file=None, openai_api_key=SecretStr("test-not-real")),
        httpx.MockTransport(handler),
    )
    stream = stream_reply(AssistantQuestion(question="Привет"), provider)
    assert next(stream) == {"type": "delta", "text": "Привет"}
    assert seen == []
    assert next(stream) == {"type": "delta", "text": ", Алия!"}
    assert seen == ["second"]
    assert next(stream)["data"]["answer"] == "Привет, Алия!"
    assert seen == ["second", "terminal"]


def test_incomplete_stream_is_error_not_completed_partial_answer():
    content = event(
        {"type": "response.output_text.delta", "delta": '{"action":"reply","answer":"Часть'}
    ) + event(
        {
            "type": "response.incomplete",
            "response": {
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
            },
        }
    )
    provider = Providers(
        Settings(_env_file=None, openai_api_key=SecretStr("test")),
        httpx.MockTransport(lambda _: httpx.Response(200, content=content)),
    )
    events = list(
        event_stream(stream_reply(AssistantQuestion(question="Вопрос"), provider), "test-request")
    )
    assert any("Часть" in data for data in events)
    assert "MODEL_OUTPUT_LIMIT" in events[-1]
    assert not any('"type": "result"' in data for data in events)


def test_partial_json_strings_and_claim_boundaries():
    assert string_field_prefix('{"answer":"Hello\\nwo', "answer") == "Hello\nwo"
    assert string_field_prefix('{"answer":"\\u04', "answer") == ""
    assert string_field_prefix('{"answer":"\\u043f', "answer") == "п"
    assert string_field_prefix('{"answer":"hello\\', "answer") == "hello"
    assert string_field_prefix('{"answer":"\\ud83d', "answer") == ""
    assert string_field_prefix('{"answer":"\\ud83d\\ude00', "answer") == "😀"
    assert completed_claims('{"claims":[{"text":"a","evidence":[') == []
    assert completed_claims('{"claims":[{"text":"a","evidence":[]},{"text":') == [
        {"text": "a", "evidence": []}
    ]


@pytest.mark.parametrize("valid", [True, False])
def test_workspace_stream_waits_for_validated_citation(
    app, authenticated_client, monkeypatch, valid
):
    fake = FakeProviders()
    app.state.settings.rag_embedding_dimensions = 3
    app.dependency_overrides[get_providers] = lambda: fake
    mid = create_meeting(authenticated_client)
    index_meeting(app, authenticated_client, fake, mid)
    seen = []

    def generated(providers, instructions, context, response_model):
        source = json.loads(context)["untrusted_transcript_sources"][0]
        claim = {
            "text": "Бюджет согласовали.",
            "evidence": [
                {
                    "source_id": source["source_id"],
                    "quote": source["text"] if valid else "Несуществующая цитата",
                }
            ],
        }
        encoded = json.dumps({"status": "answered", "claims": [claim]}, ensure_ascii=False)
        split = encoded.index('"evidence"')
        seen.append("text")
        yield "delta", encoded[:split]
        seen.append("citation")
        yield "delta", encoded[split:]
        seen.append("terminal")
        yield "result", GeneratedAnswer.model_validate({"status": "answered", "claims": [claim]})

    monkeypatch.setattr("aimeet_api.modules.rag.streaming.stream_json", generated)
    with app.state.session_factory() as db:
        from aimeet_api.db.models import User

        user = db.query(User).first()
        events = workspace_events(db, user.workspace_id, "Бюджет?", app.state.settings, fake)
        if valid:
            assert next(events) == {"type": "delta", "text": "Бюджет согласовали."}
            assert seen == ["text", "citation"]
            assert next(events)["type"] == "result"
        else:
            with pytest.raises(RagError, match="UNGROUNDED_MODEL_RESPONSE"):
                next(events)
    app.dependency_overrides.clear()


def test_stream_route_requires_auth_and_disables_buffering(app, client, authenticated_client):
    provider = Providers(
        app.state.settings,
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=event(
                    {
                        "type": "response.output_text.delta",
                        "delta": '{"action":"reply","answer":"Привет","search_query":""}',
                    }
                )
                + event({"type": "response.completed", "response": {"status": "completed"}}),
            )
        ),
    )
    app.state.settings.openai_api_key = SecretStr("test")
    app.dependency_overrides[get_providers] = lambda: provider
    response = authenticated_client.post(
        "/api/v1/assistant/chat/stream", json={"question": "Привет"}
    )
    assert response.status_code == 200
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"type": "result"' in response.text
    authenticated_client.post("/api/v1/auth/logout")
    assert (
        client.post("/api/v1/assistant/chat/stream", json={"question": "Привет"}).status_code == 401
    )
    app.dependency_overrides.clear()
