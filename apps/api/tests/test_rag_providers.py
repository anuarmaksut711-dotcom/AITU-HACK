import json
import math

import httpx
import pytest
from pydantic import ValidationError

from aimeet_api.core.config import Settings
from aimeet_api.modules.rag.providers import Providers, RagError, validate_vectors


def settings(**kwargs):
    return Settings(
        _env_file=None, openai_api_key="test-key-never-real", rag_embedding_dimensions=3, **kwargs
    )


@pytest.mark.parametrize(
    "vector", [[0, 0, 0], [1, 2], [1, math.nan, 0], [1, math.inf, 0], [1, True, 0], ["1", 0, 0]]
)
def test_invalid_vectors(vector):
    with pytest.raises(RagError):
        validate_vectors([vector], 1, 3)


def test_vector_order_and_openai_embedding_body():
    def handler(request):
        assert str(request.url) == "https://api.openai.com/v1/embeddings"
        body = json.loads(request.content)
        assert body["dimensions"] == 3
        assert body["encoding_format"] == "float"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0, 5, 0]},
                    {"index": 0, "embedding": [4, 0, 0]},
                ]
            },
        )

    provider = Providers(settings(), httpx.MockTransport(handler))
    assert provider.embed(["a", "b"]) == [[1, 0, 0], [0, 1, 0]]


def test_duplicate_embedding_indices_rejected():
    provider = Providers(
        settings(),
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "data": [{"index": 0, "embedding": [1, 0, 0]}] * 2,
                },
            )
        ),
    )
    with pytest.raises(RagError, match="INVALID_EMBEDDINGS"):
        provider.embed(["a", "b"])


@pytest.mark.parametrize(
    "provider,endpoint", [("ollama", "/api/embed"), ("local_openai", "/v1/embeddings")]
)
def test_offline_embed_and_no_cloud_auth(provider, endpoint):
    config = settings(
        rag_offline=True,
        rag_llm_provider=provider,
        rag_embedding_provider=provider,
        rag_local_url="http://127.0.0.1:11434",
    )

    def handler(request):
        assert request.url.host == "127.0.0.1"
        assert request.url.path == endpoint
        assert "authorization" not in request.headers
        body = json.loads(request.content)
        if provider == "ollama":
            assert body["truncate"] is False
            return httpx.Response(200, json={"embeddings": [[1, 0, 0]]})
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 0, 0]}]})

    assert Providers(config, httpx.MockTransport(handler)).embed(["a"]) == [[1, 0, 0]]


@pytest.mark.parametrize(
    "config",
    [
        {"rag_offline": True},
        {"rag_offline": True, "rag_llm_provider": "ollama"},
        {
            "rag_offline": True,
            "rag_llm_provider": "ollama",
            "rag_embedding_provider": "ollama",
            "rag_local_url": "https://example.com",
        },
        {"rag_local_url": "http://user:password@localhost"},
        {"rag_local_url": "http://localhost?redirect=https://example.com"},
        {"rag_job_lease_seconds": 60, "rag_provider_timeout": 60},
    ],
)
def test_invalid_configuration_fails_closed(config):
    with pytest.raises(ValidationError):
        settings(**config)


def test_luna_max_responses_contract():
    def handler(request):
        assert request.url.path == "/v1/responses"
        body = json.loads(request.content)
        assert body["model"] == "gpt-5.6-luna"
        assert body["reasoning"] == {"effort": "max"}
        assert body["store"] is False
        assert "temperature" not in body and "tools" not in body
        assert body["text"]["format"]["strict"] is True
        assert body["text"]["format"]["schema"]["additionalProperties"] is False
        assert body["instructions"] == "TRUSTED"
        assert body["input"] == [{"role": "user", "content": "UNTRUSTED"}]
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"status":"insufficient_evidence","claims":[]}',
                            },
                        ],
                    }
                ],
            },
        )

    result = Providers(settings(), httpx.MockTransport(handler)).generate("TRUSTED", "UNTRUSTED")
    assert result.status == "insufficient_evidence"


@pytest.mark.parametrize("provider", ["ollama", "local_openai"])
def test_local_structured_generation(provider):
    def handler(request):
        body = json.loads(request.content)
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][1]["role"] == "user"
        assert body["stream"] is False
        assert "reasoning_effort" not in body
        raw = '{"status":"insufficient_evidence","claims":[]}'
        if provider == "ollama":
            assert body["format"]["type"] == "object"
            return httpx.Response(
                200, json={"done": True, "done_reason": "stop", "message": {"content": raw}}
            )
        assert body["response_format"]["type"] == "json_schema"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": raw}},
                ]
            },
        )

    result = Providers(settings(rag_llm_provider=provider), httpx.MockTransport(handler)).generate(
        "s", "q"
    )
    assert result.status == "insufficient_evidence"


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"status": "incomplete", "output": []}, "INCOMPLETE_MODEL_RESPONSE"),
        (
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "refusal", "refusal": "no"},
                        ],
                    }
                ],
            },
            "MODEL_REFUSAL",
        ),
        ({"status": "completed", "output": []}, "INVALID_MODEL_RESPONSE"),
    ],
)
def test_incomplete_or_refused_is_not_success(payload, expected):
    provider = Providers(
        settings(), httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )
    with pytest.raises(RagError, match=expected):
        provider.generate("s", "q")


@pytest.mark.parametrize("status,retry", [(302, False), (401, False), (429, True), (500, True)])
def test_provider_error_redaction_and_no_redirect(status, retry):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            text="SENSITIVE upstream secret",
            headers={"Location": "https://example.com/stolen"},
        )

    provider = Providers(settings(), httpx.MockTransport(handler))
    with pytest.raises(RagError) as error:
        provider.embed(["private transcript"])
    assert len(calls) == 1
    assert error.value.retryable == retry
    assert "SENSITIVE" not in str(error.value)


@pytest.mark.parametrize(
    "provider,payload",
    [
        ("openai", {"status": "completed", "output": None}),
        ("openai", {"status": "completed", "output": [None]}),
        ("openai", {"status": "completed", "output": [{"type": "message", "content": [None]}]}),
        ("ollama", {"done": True, "message": None}),
        ("local_openai", {"choices": [None]}),
    ],
)
def test_malformed_generation_envelope_returns_controlled_error(provider, payload):
    client = Providers(
        settings(rag_llm_provider=provider),
        httpx.MockTransport(
            lambda _: httpx.Response(200, json=payload),
        ),
    )
    with pytest.raises(RagError, match="INVALID_MODEL_RESPONSE"):
        client.generate("s", "q")


def test_separate_local_embedding_endpoint():
    config = settings(
        rag_embedding_provider="local_openai",
        rag_local_url="http://127.0.0.1:8001",
        rag_embedding_local_url="http://127.0.0.1:8002/v1",
    )

    def handler(request):
        assert request.url.port == 8002
        assert request.url.path == "/v1/embeddings"
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 0, 0]}]})

    assert Providers(config, httpx.MockTransport(handler)).embed(["a"]) == [[1, 0, 0]]


def test_reasoning_output_limit_is_distinguished_from_other_incomplete_responses():
    provider = Providers(
        settings(),
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "output": [],
                    "usage": {
                        "output_tokens": 8192,
                        "output_tokens_details": {"reasoning_tokens": 8192},
                    },
                },
            )
        ),
    )
    with pytest.raises(RagError, match="MODEL_OUTPUT_LIMIT"):
        provider.generate("s", "q")
