"""
ASGI middleware that enforces request body size limits before Starlette/FastAPI
parses multipart forms, preventing disk/memory exhaustion from oversized uploads.
"""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..core import settings as config
from .edit_limits import (
    EDIT_MULTIPART_METADATA_OVERHEAD_BYTES,
    MAX_EDIT_SOURCE_IMAGES,
)


def _is_json_content_type(content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type == "application/json" or media_type.endswith("+json")


def _max_body_for_path(path: str, content_type: str = "") -> int:
    path_limits: list[tuple[str, int]] = [
        ("/api/import", config.IMPORT_ARCHIVE_MAX_MB * 1024 * 1024),
        (
            "/api/edits",
            config.MAX_FILE_SIZE_MB
            * MAX_EDIT_SOURCE_IMAGES
            * 1024
            * 1024
            + EDIT_MULTIPART_METADATA_OVERHEAD_BYTES,
        ),
    ]
    for prefix, limit in path_limits:
        if path.startswith(prefix):
            return limit
    if _is_json_content_type(content_type):
        return config.MAX_JSON_BODY_MB * 1024 * 1024
    return config.MAX_FILE_SIZE_MB * 1024 * 1024


class BodyLimitMiddleware:
    """Reject requests whose body exceeds a per-path size limit."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Fast path: check Content-Length header if present
        headers = dict(scope.get("headers", []))
        content_type = headers.get(b"content-type", b"").decode("latin1")
        max_bytes = _max_body_for_path(path, content_type)
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

        async def limited_receive() -> Message:
            nonlocal total_received
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                total_received += len(body)
                if total_received > max_bytes:
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
