import json
import logging
import time
import uuid

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("aimeet.requests")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
logger.propagate = False


class RequestBoundaryMiddleware:
    """Bound request bodies and attach trace IDs without logging customer content."""

    def __init__(self, app: ASGIApp, max_request_bytes: int, max_audio_bytes: int):
        self.app = app
        self.max_request_bytes = max_request_bytes
        self.max_audio_bytes = max_audio_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.monotonic()
        status = 500
        response_started = False

        async def send_with_headers(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                response_started = True
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-request-id", request_id.encode()),
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                    ]
                )
                message["headers"] = headers
            await send(message)

        async def reject(code: int, message: str) -> None:
            response = JSONResponse(
                {"error": {"code": f"HTTP_{code}", "message": message}, "request_id": request_id},
                status_code=code,
            )
            await response(scope, receive, send_with_headers)

        try:
            limit = (
                self.max_audio_bytes
                if scope.get("path") == "/api/v1/meetings/audio" and scope.get("method") == "POST"
                else self.max_request_bytes
            )
            headers = dict(scope.get("headers", []))
            content_length = headers.get(b"content-length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except ValueError:
                    await reject(400, "Invalid content length")
                    return
                if declared_length < 0:
                    await reject(400, "Invalid content length")
                    return
                if declared_length > limit:
                    await reject(413, "Request body is too large")
                    return
            # Count bytes as consumed: audio must never be buffered in API memory.
            received = 0
            async def bounded_receive() -> Message:
                nonlocal received
                message = await receive()
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(413, "Request body is too large")
                return message

            await self.app(scope, bounded_receive, send_with_headers)
        except Exception as exc:
            # SQL errors can carry bound transcript/password values: never format exc.
            logger.error(
                json.dumps(
                    {
                        "event": "request_failed",
                        "request_id": request_id,
                        "exception_type": type(exc).__name__,
                    }
                )
            )
            if not response_started:
                await reject(500, "Internal server error")
            else:
                raise
        finally:
            route = scope.get("route")
            logger.info(
                json.dumps(
                    {
                        "event": "http_request",
                        "request_id": request_id,
                        "method": scope.get("method"),
                        "route": getattr(route, "path", "unmatched"),
                        "status": status,
                        "duration_ms": round((time.monotonic() - started) * 1000, 2),
                    }
                )
            )
