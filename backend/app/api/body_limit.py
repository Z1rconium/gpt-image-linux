"""
ASGI middleware that enforces request body size limits before Starlette/FastAPI
parses multipart forms, preventing disk/memory exhaustion from oversized uploads.
"""

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..core import settings as config


_DEFAULT_MAX_BODY_BYTES = config.MAX_FILE_SIZE_MB * 1024 * 1024

_PATH_LIMITS: list[tuple[str, int]] = [
    ("/api/import", config.IMPORT_ARCHIVE_MAX_MB * 1024 * 1024),
    ("/api/edits", config.MAX_FILE_SIZE_MB * config.MAX_ACTIVE_GENERATE_JOBS * 16 * 1024 * 1024),
]


def _max_body_for_path(path: str) -> int:
    for prefix, limit in _PATH_LIMITS:
        if path.startswith(prefix):
            return limit
    return _DEFAULT_MAX_BODY_BYTES


class BodyLimitMiddleware:
    """Reject requests whose body exceeds a per-path size limit."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        max_bytes = _max_body_for_path(path)

        # Fast path: check Content-Length header if present
        headers = dict(scope.get("headers", []))
        content_length_raw = headers.get(b"content-length")
        if content_length_raw is not None:
            try:
                content_length = int(content_length_raw)
                if content_length > max_bytes:
                    response = JSONResponse(
                        status_code=413,
                        content={"status": "error", "detail": "Request body too large"},
                    )
                    await response(scope, receive, send)
                    return
            except (ValueError, TypeError):
                pass

        # Wrap receive to count bytes as they stream in
        total_received = 0
        body_exceeded = False

        async def limited_receive() -> Message:
            nonlocal total_received, body_exceeded
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                total_received += len(body)
                if total_received > max_bytes:
                    body_exceeded = True
                    raise _BodyTooLargeError()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLargeError:
            response = JSONResponse(
                status_code=413,
                content={"status": "error", "detail": "Request body too large"},
            )
            await response(scope, receive, send)


class _BodyTooLargeError(Exception):
    pass
