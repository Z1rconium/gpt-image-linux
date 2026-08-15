"""
ASGI middleware that enforces request body size limits before Starlette/FastAPI
parses multipart forms, preventing disk/memory exhaustion from oversized uploads.
"""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..core import settings as config
ASSISTANT_IMAGE_MULTIPART_OVERHEAD_BYTES = 64 * 1024
IMPORT_MULTIPART_OVERHEAD_BYTES = 64 * 1024


def _is_json_content_type(content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type == "application/json" or media_type.endswith("+json")


def _max_body_for_path(path: str, content_type: str = "") -> int:
    path_limits: list[tuple[str, int]] = [
        (
            "/api/import",
            config.IMPORT_ARCHIVE_MAX_MB * 1024 * 1024
            + (
                IMPORT_MULTIPART_OVERHEAD_BYTES
                if config.IMPORT_ARCHIVE_MAX_MB > 0
                else 0
            ),
        ),
        (
            "/api/assistant/image/prompt",
            config.MAX_FILE_SIZE_MB * 1024 * 1024 + ASSISTANT_IMAGE_MULTIPART_OVERHEAD_BYTES,
        ),
        (
            "/api/edits",
            config.EDIT_UPLOAD_MAX_MB * 1024 * 1024,
        ),
    ]
    for prefix, limit in path_limits:
        if path.startswith(prefix):
            if prefix == "/api/edits" and not content_type.lower().startswith(
                "multipart/form-data"
            ):
                return config.MAX_JSON_BODY_MB * 1024 * 1024
            return limit
    if _is_json_content_type(content_type):
        return config.MAX_JSON_BODY_MB * 1024 * 1024
    return config.MAX_FILE_SIZE_MB * 1024 * 1024


def upload_reservation_policy(path: str, content_type: str) -> tuple[str, int] | None:
    if content_type.split(";", 1)[0].strip().lower() != "multipart/form-data":
        return None
    if path == "/api/import":
        # Reserve the bounded ZIP payload, not the small multipart envelope.
        # Otherwise the default 256 MiB per-IP cap would reject a valid
        # 256 MiB archive before parsing; BodyLimitMiddleware independently
        # enforces the additional fixed envelope allowance.
        return "import", config.IMPORT_ARCHIVE_MAX_MB * 1024 * 1024
    if path == "/api/edits" or path.startswith("/api/edits/from-gallery/"):
        return "edit", _max_body_for_path(path, content_type)
    if path in {
        "/api/assistant/image/prompt",
        "/api/assistant/image/prompt/optimize",
    }:
        return "assistant", _max_body_for_path(path, content_type)
    return None


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
        body_too_large = False
        response_started = False

        async def limited_receive() -> Message:
            nonlocal total_received, body_too_large
            if body_too_large:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                total_received += len(body)
                if total_received > max_bytes:
                    body_too_large = True
                    return {"type": "http.disconnect"}
            return message

        async def limited_send(message: Message) -> None:
            nonlocal response_started
            if body_too_large:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, limited_send)
        except BaseException:
            if not body_too_large:
                raise
        if body_too_large and not response_started:
            response = JSONResponse(
                status_code=413,
                content={"status": "error", "detail": "Request body too large"},
            )
            await response(scope, receive, send)
