"""Actual Responses API streaming and bounded incremental structured-output parsing."""

import json
import re

import httpx

from aimeet_api.modules.rag.providers import RagError, logger
from aimeet_api.modules.rag.response_schema import strict_response_schema


def string_field_prefix(raw, name):
    match = re.search(r'(?<!\\)"' + re.escape(name) + r'"\s*:\s*"', raw)
    if not match:
        return ""
    start = end = match.end()
    while end < len(raw):
        if raw[end] == '"':
            break
        if raw[end] == "\\":
            size = 6 if raw[end : end + 2] == "\\u" else 2
            if end + size > len(raw):
                break
            end += size
        else:
            end += 1
    try:
        return (
            json.loads('"' + raw[start:end] + '"').encode("utf-8", errors="ignore").decode("utf-8")
        )
    except (ValueError, UnicodeError):
        return ""


def completed_claims(raw):
    match = re.search(r'(?<!\\)"claims"\s*:\s*\[', raw)
    if not match:
        return []
    position = match.end()
    claims = []
    while position < len(raw):
        while position < len(raw) and raw[position] in " \n\r\t,":
            position += 1
        if position >= len(raw) or raw[position] != "{":
            break
        try:
            claim, position = json.JSONDecoder().raw_decode(raw, position)
        except ValueError:
            break
        claims.append(claim)
    return claims


def stream_json(providers, instructions, context, response_model):
    config = providers.config
    if config.rag_llm_provider != "openai":
        # Existing local providers keep their validated non-streaming behavior.
        yield "result", providers._generate(instructions, context, response_model=response_model)
        return
    providers.ensure_configured(generation_only=True)
    if config.rag_offline:
        raise RagError("CLOUD_DISABLED")
    body = {
        "model": config.rag_llm_model,
        "instructions": instructions,
        "input": [{"role": "user", "content": context}],
        "stream": True,
        "store": False,
        "reasoning": {"effort": config.rag_reasoning_effort},
        "max_output_tokens": config.rag_max_output_tokens,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "meeting_answer",
                "strict": True,
                "schema": strict_response_schema(response_model),
            }
        },
    }
    raw, final, refused, used = "", None, False, 0
    try:
        with httpx.Client(
            timeout=httpx.Timeout(config.rag_provider_timeout, connect=10),
            trust_env=False,
            follow_redirects=False,
            transport=providers.transport,
        ) as client:
            with client.stream(
                "POST",
                "https://api.openai.com/v1/responses",
                json=body,
                headers={"Authorization": f"Bearer {config.openai_api_key.get_secret_value()}"},
            ) as response:
                if response.status_code >= 300:
                    raise RagError(
                        "PROVIDER_RATE_LIMIT"
                        if response.status_code == 429
                        else "PROVIDER_REJECTED",
                        503 if response.status_code == 429 or response.status_code >= 500 else 502,
                    )
                data_lines = []
                for line in response.iter_lines():
                    used += len(line.encode("utf-8"))
                    if used > 8_000_000:
                        raise RagError("PROVIDER_RESPONSE_TOO_LARGE", 502)
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif not line and data_lines:
                        data = "\n".join(data_lines)
                        data_lines = []
                        if data == "[DONE]":
                            continue
                        event = json.loads(data)
                        event_type = event.get("type")
                        if event_type == "response.output_text.delta":
                            delta = event.get("delta")
                            if not isinstance(delta, str):
                                raise RagError("INVALID_PROVIDER_RESPONSE", 502)
                            raw += delta
                            yield "delta", delta
                        elif event_type in {"response.refusal.delta", "response.refusal.done"}:
                            refused = True
                        elif event_type in {
                            "response.completed",
                            "response.incomplete",
                            "response.failed",
                        }:
                            final = event.get("response", {})
                            break
                        elif event_type == "error":
                            raise RagError("PROVIDER_UNAVAILABLE", 502)
    except httpx.TimeoutException as exc:
        raise RagError("PROVIDER_TIMEOUT", retryable=True) from exc
    except httpx.HTTPError as exc:
        raise RagError("PROVIDER_UNAVAILABLE", retryable=True) from exc
    except (ValueError, TypeError) as exc:
        raise RagError("INVALID_PROVIDER_RESPONSE", 502) from exc
    final = final or {}
    reason = (final.get("incomplete_details") or {}).get("reason")
    usage = final.get("usage") or {}
    logger.info(
        json.dumps(
            {
                "event": "rag_stream_finished",
                "model": config.rag_llm_model,
                "status": final.get("status"),
                "incomplete_reason": reason,
                "output_tokens": usage.get("output_tokens"),
                "reasoning_tokens": (usage.get("output_tokens_details") or {}).get(
                    "reasoning_tokens"
                ),
            }
        )
    )
    if final.get("status") != "completed":
        raise RagError(
            "MODEL_OUTPUT_LIMIT"
            if reason in {"max_output_tokens", "max_tokens"}
            else "INCOMPLETE_MODEL_RESPONSE",
            502,
        )
    if refused:
        raise RagError("MODEL_REFUSAL", 422)
    final_text = "".join(
        part.get("text", "")
        for item in final.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    )
    try:
        result = response_model.model_validate_json(final_text or raw)
    except ValueError as exc:
        raise RagError("INVALID_MODEL_RESPONSE", 502) from exc
    yield "result", result


def event_stream(events, request_id):
    yield 'data: {"type":"started"}\n\n'
    try:
        for event in events:
            yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
    except RagError as exc:
        logger.warning(
            json.dumps(
                {
                    "event": "rag_error",
                    "request_id": request_id,
                    "code": exc.code,
                    "status": exc.status_code,
                }
            )
        )
        yield (
            "data: "
            + json.dumps(
                {
                    "type": "error",
                    "code": exc.code,
                    "status": exc.status_code,
                    "request_id": request_id,
                }
            )
            + "\n\n"
        )
    except Exception as exc:
        logger.error(
            json.dumps(
                {
                    "event": "rag_stream_error",
                    "request_id": request_id,
                    "error_type": type(exc).__name__,
                }
            )
        )
        yield (
            "data: "
            + json.dumps(
                {"type": "error", "code": "STREAM_FAILED", "status": 500, "request_id": request_id}
            )
            + "\n\n"
        )
