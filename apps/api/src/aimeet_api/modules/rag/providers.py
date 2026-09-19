"""Bounded provider adapters; no network calls during startup, no cloud fallback."""

import json
import logging
import math
from collections.abc import Sequence

import httpx
from pydantic import ValidationError

from aimeet_api.core.config import Settings
from aimeet_api.modules.rag.response_schema import strict_response_schema
from aimeet_api.modules.rag.schemas import AssistantDecision, GeneratedAnswer, TaskCreationPlan

logger = logging.getLogger("aimeet.requests")


class RagError(Exception):
    def __init__(self, code: str, status_code: int = 503, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable


def validate_vectors(vectors, count: int, dimensions: int) -> list[list[float]]:
    if not isinstance(vectors, list) or len(vectors) != count:
        raise RagError("INVALID_EMBEDDINGS", 502)
    result = []
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != dimensions:
            raise RagError("EMBEDDING_DIMENSION_MISMATCH", 502)
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in vector):
            raise RagError("INVALID_EMBEDDINGS", 502)
        norm = math.hypot(*vector)
        if norm == 0 or not math.isfinite(norm):
            raise RagError("INVALID_EMBEDDINGS", 502)
        result.append([value / norm for value in vector])
    return result


class Providers:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.config = settings
        self.transport = transport

    def ensure_configured(self, *, generation: bool = False, generation_only: bool = False):
        kinds = [
            self.config.rag_llm_provider if generation_only else self.config.rag_embedding_provider
        ]
        if generation:
            kinds.append(self.config.rag_llm_provider)
        if "openai" in kinds and not self.config.openai_api_key.get_secret_value():
            raise RagError("OPENAI_KEY_REQUIRED")

    def _post(self, provider: str, endpoint: str, body: dict, *, embedding=False) -> dict:
        config = self.config
        if config.rag_offline and provider == "openai":
            raise RagError("CLOUD_DISABLED")
        if provider == "openai":
            base = "https://api.openai.com/v1"
            key = config.openai_api_key.get_secret_value()
            if not key:
                raise RagError("OPENAI_KEY_REQUIRED")
        else:
            base = (
                (config.rag_embedding_local_url or config.rag_local_url)
                if embedding
                else config.rag_local_url
            ).rstrip("/")
            if provider == "local_openai" and not base.endswith("/v1"):
                base += "/v1"
            if provider == "ollama" and base.endswith("/v1"):
                base = base[:-3]
            key = config.rag_local_api_key.get_secret_value()
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            # No ambient HTTP proxies or redirects, and never return provider bodies to clients.
            with (
                httpx.Client(
                    timeout=httpx.Timeout(config.rag_provider_timeout, connect=10),
                    trust_env=False,
                    follow_redirects=False,
                    transport=self.transport,
                ) as client,
                client.stream("POST", base + endpoint, json=body, headers=headers) as response,
            ):
                if response.status_code >= 300:
                    code = response.status_code
                    raise RagError(
                        "PROVIDER_RATE_LIMIT" if code == 429 else "PROVIDER_REJECTED",
                        503 if code == 429 or code >= 500 else 502,
                        retryable=code == 429 or code >= 500,
                    )
                payload = bytearray()
                for part in response.iter_bytes():
                    payload.extend(part)
                    if len(payload) > 8_000_000:
                        raise RagError("PROVIDER_RESPONSE_TOO_LARGE", 502)
                data = json.loads(payload)
                if not isinstance(data, dict):
                    raise RagError("INVALID_PROVIDER_RESPONSE", 502)
                return data
        except httpx.TimeoutException as exc:
            raise RagError("PROVIDER_TIMEOUT", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise RagError("PROVIDER_UNAVAILABLE", retryable=True) from exc
        except (ValueError, UnicodeError) as exc:
            raise RagError("INVALID_PROVIDER_RESPONSE", 502) from exc

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        config = self.config
        provider = config.rag_embedding_provider
        if provider == "ollama":
            data = self._post(
                provider,
                "/api/embed",
                {
                    "model": config.rag_embedding_model,
                    "input": list(texts),
                    "truncate": False,
                },
                embedding=True,
            )
            vectors = data.get("embeddings")
        else:
            body = {
                "model": config.rag_embedding_model,
                "input": list(texts),
                "encoding_format": "float",
            }
            if provider == "openai":
                body["dimensions"] = config.rag_embedding_dimensions
            data = self._post(provider, "/embeddings", body, embedding=True)
            try:
                rows = sorted(data["data"], key=lambda row: row["index"])
                if [row["index"] for row in rows] != list(range(len(texts))):
                    raise ValueError("Embedding indices must be unique and complete")
                vectors = [row["embedding"] for row in rows]
            except (KeyError, TypeError, ValueError) as exc:
                raise RagError("INVALID_EMBEDDINGS", 502) from exc
        return validate_vectors(vectors, len(texts), config.rag_embedding_dimensions)

    def generate(self, instructions: str, context: str) -> GeneratedAnswer:
        try:
            return self._generate(instructions, context)
        except (KeyError, TypeError, AttributeError, IndexError, ValueError) as exc:
            raise RagError("INVALID_MODEL_RESPONSE", 502) from exc

    def decide(self, instructions: str, context: str) -> AssistantDecision:
        try:
            result = self._generate(instructions, context, response_model=AssistantDecision)
        except (KeyError, TypeError, AttributeError, IndexError, ValueError) as exc:
            raise RagError("INVALID_MODEL_RESPONSE", 502) from exc
        if result.action == "reply" and not result.answer.strip():
            raise RagError("INVALID_MODEL_RESPONSE", 502)
        if result.action == "search_meetings" and not result.search_query.strip():
            raise RagError("INVALID_MODEL_RESPONSE", 502)
        return result

    def plan_tasks(self, instructions: str, context: str) -> TaskCreationPlan:
        try:
            return self._generate(instructions, context, response_model=TaskCreationPlan)
        except (KeyError, TypeError, AttributeError, IndexError, ValueError) as exc:
            raise RagError("INVALID_MODEL_RESPONSE", 502) from exc

    def _generate(self, instructions: str, context: str, response_model=GeneratedAnswer):
        config = self.config
        provider = config.rag_llm_provider
        schema = strict_response_schema(response_model)
        messages = [
            {"role": "system", "content": instructions},
            {"role": "user", "content": context},
        ]
        if provider == "openai":
            data = self._post(
                provider,
                "/responses",
                {
                    "model": config.rag_llm_model,
                    "instructions": instructions,
                    "input": [{"role": "user", "content": context}],
                    "store": False,
                    "reasoning": {"effort": config.rag_reasoning_effort},
                    "max_output_tokens": config.rag_max_output_tokens,
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": "meeting_answer",
                            "strict": True,
                            "schema": schema,
                        }
                    },
                },
            )
            usage = data.get("usage") or {}
            reason = (data.get("incomplete_details") or {}).get("reason")
            logger.info(
                json.dumps(
                    {
                        "event": "rag_generation_finished",
                        "model": config.rag_llm_model,
                        "schema": response_model.__name__,
                        "status": data.get("status"),
                        "incomplete_reason": reason
                        if reason in {"max_output_tokens", "max_tokens", "content_filter"}
                        else None,
                        "output_tokens": usage.get("output_tokens"),
                        "reasoning_tokens": (usage.get("output_tokens_details") or {}).get(
                            "reasoning_tokens"
                        ),
                    }
                )
            )
            if reason in {"max_output_tokens", "max_tokens"} and data.get("status") == "incomplete":
                raise RagError("MODEL_OUTPUT_LIMIT", 502)
            if data.get("status") != "completed":
                raise RagError("INCOMPLETE_MODEL_RESPONSE", 502)
            parts = [
                part
                for item in data.get("output", [])
                if item.get("type") == "message"
                for part in item.get("content", [])
            ]
            if any(part.get("type") == "refusal" for part in parts):
                raise RagError("MODEL_REFUSAL", 422)
            raw = "".join(p.get("text", "") for p in parts if p.get("type") == "output_text")
        elif provider == "ollama":
            data = self._post(
                provider,
                "/api/chat",
                {
                    "model": config.rag_llm_model,
                    "messages": messages,
                    "stream": False,
                    "format": schema,
                    "options": {
                        "temperature": 0,
                        "num_predict": config.rag_max_output_tokens,
                        "num_ctx": 32768,
                    },
                },
            )
            if data.get("done") is not True or data.get("done_reason") == "length":
                raise RagError("INCOMPLETE_MODEL_RESPONSE", 502)
            raw = data.get("message", {}).get("content", "")
        else:
            data = self._post(
                provider,
                "/chat/completions",
                {
                    "model": config.rag_llm_model,
                    "messages": messages,
                    "stream": False,
                    "temperature": 0,
                    "max_tokens": config.rag_max_output_tokens,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "meeting_answer",
                            "strict": True,
                            "schema": schema,
                        },
                    },
                },
            )
            choices = data.get("choices", [])
            if not choices or choices[0].get("finish_reason") != "stop":
                raise RagError("INCOMPLETE_MODEL_RESPONSE", 502)
            raw = choices[0].get("message", {}).get("content", "")
        try:
            return response_model.model_validate_json(raw)
        except (ValidationError, TypeError) as exc:
            logger.warning(
                json.dumps(
                    {
                        "event": "rag_invalid_response",
                        "schema": response_model.__name__,
                        "error_types": [e["type"] for e in exc.errors(include_input=False)]
                        if isinstance(exc, ValidationError)
                        else ["type_error"],
                    }
                )
            )
            raise RagError("INVALID_MODEL_RESPONSE", 502) from exc
