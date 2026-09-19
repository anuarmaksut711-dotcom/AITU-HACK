import json

import httpx
import pytest
from pydantic import SecretStr

from aimeet_api.core.config import Settings
from aimeet_api.modules.rag.providers import Providers
from aimeet_api.modules.rag.response_schema import strict_response_schema
from aimeet_api.modules.rag.schemas import AssistantDecision, GeneratedAnswer, TaskCreationPlan
from aimeet_api.modules.rag.streaming import stream_json


def assert_strict(node):
    if isinstance(node, list):
        for child in node:
            assert_strict(child)
    elif isinstance(node, dict):
        assert "default" not in node
        if node.get("type") == "object" or "properties" in node:
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node.get("properties", {}))
        for child in node.values():
            assert_strict(child)


@pytest.mark.parametrize("model", [AssistantDecision, GeneratedAnswer, TaskCreationPlan])
def test_all_properties_are_required_recursively_without_changing_local_defaults(model):
    before = model.model_json_schema()
    assert_strict(strict_response_schema(model))
    assert model.model_json_schema() == before
    if model is GeneratedAnswer:
        assert model(status="insufficient_evidence", claims=[]).panel is None


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "model, result",
    [
        (GeneratedAnswer, {"status": "insufficient_evidence", "claims": [], "panel": None}),
        (
            TaskCreationPlan,
            {"action": "clarify", "answer": "Which meeting?", "tasks": [], "panel": None},
        ),
    ],
)
def test_actual_provider_payload_meets_strict_schema_contract(stream, model, result):
    def handler(request):
        payload = json.loads(request.content)
        schema = payload["text"]["format"]["schema"]
        assert_strict(schema)
        assert "panel" in schema["required"]
        assert {"type": "null"} in schema["properties"]["panel"]["anyOf"]
        if stream:
            events = [
                {"type": "response.output_text.delta", "delta": json.dumps(result)},
                {"type": "response.completed", "response": {"status": "completed"}},
            ]
            return httpx.Response(
                200,
                content="".join("data: " + json.dumps(event) + "\n\n" for event in events),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": json.dumps(result)}],
                    }
                ],
            },
        )

    provider = Providers(
        Settings(_env_file=None, openai_api_key=SecretStr("test")), httpx.MockTransport(handler)
    )
    if stream:
        output = list(stream_json(provider, "instructions", "context", model))[-1][1]
    else:
        output = provider._generate("instructions", "context", response_model=model)
    assert output.model_dump(mode="json") == result
