"""Server-side NodeImage API client."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import aiohttp

from ...core import secrets
from ...core.redaction import redact_sensitive_text
from ...repositories.image_files import image_content_type_for_filename
from ..session_pool import TIMEOUT_NODEIMAGE, get_pool

NODEIMAGE_API_URL = "https://api.nodeimage.com"
NODEIMAGE_UPLOAD_URL = f"{NODEIMAGE_API_URL}/api/upload"
NODEIMAGE_HOST_ALLOWLIST = "api.nodeimage.com"
MAX_NODEIMAGE_RESPONSE_BYTES = 2 * 1024 * 1024
NODEIMAGE_MAX_ATTEMPTS = 2
NODEIMAGE_RETRY_BACKOFF_SECONDS = 0.2
RETRYABLE_CONNECT_ERRORS = (
    aiohttp.ClientConnectorError,
    aiohttp.ClientProxyConnectionError,
)
NodeImageFileSource = Path | str | Callable[[], Any]


class NodeImageConfigurationError(ValueError):
    pass


class NodeImageAuthError(RuntimeError):
    pass


class NodeImageUploadError(RuntimeError):
    pass


class NodeImageTransientError(NodeImageUploadError):
    """An upload failure that is safe to retry before a response is complete."""


class _NodeImageResponseReadError(NodeImageUploadError):
    pass


@dataclass(frozen=True)
class NodeImageEffectiveSettings:
    enabled: bool
    api_key: str


@dataclass(frozen=True)
class NodeImageUploadResult:
    url: str
    markdown: str


def _resolve_secret(value: Any) -> str:
    try:
        return secrets.resolve_secret_reference(
            value,
            purpose="nodeimage_api_key",
            target_url=NODEIMAGE_API_URL,
            host_allowlist=NODEIMAGE_HOST_ALLOWLIST,
            field_name="NodeImage API key",
        )
    except secrets.SecretRegistryError as exc:
        raise NodeImageConfigurationError(str(exc)) from exc


def resolve_nodeimage_settings(
    settings: dict[str, Any] | None,
    *,
    require_enabled: bool = True,
) -> NodeImageEffectiveSettings:
    raw = settings or {}
    enabled = bool(raw.get("enabled", False))
    if require_enabled and not enabled:
        raise NodeImageConfigurationError("NodeImage upload is disabled.")
    api_key = _resolve_secret(raw.get("api_key"))
    if not api_key:
        raise NodeImageConfigurationError("NodeImage API key is not configured.")
    return NodeImageEffectiveSettings(enabled=enabled, api_key=api_key)


def _is_auth_error(message: str) -> bool:
    normalized = message.lower()
    return "unauthorized" in normalized or "invalid api key" in normalized


async def _read_response_json(response: aiohttp.ClientResponse) -> dict[str, Any]:
    chunks: list[bytes] = []
    total = 0
    try:
        async for chunk in response.content.iter_chunked(64 * 1024):
            total += len(chunk)
            if total > MAX_NODEIMAGE_RESPONSE_BYTES:
                raise NodeImageUploadError("NodeImage response was too large.")
            chunks.append(chunk)
    except NodeImageUploadError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        raise _NodeImageResponseReadError(
            "NodeImage returned an unreadable response."
        ) from exc
    try:
        parsed = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _NodeImageResponseReadError(
            "NodeImage returned an invalid JSON response."
        ) from exc
    if not isinstance(parsed, dict):
        raise _NodeImageResponseReadError("NodeImage returned an invalid response.")
    return parsed


def _error_text(payload: dict[str, Any], status: int, api_key: str) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        error = error.get("message") or error.get("detail")
    message = str(
        error or payload.get("message") or f"NodeImage returned HTTP {status}"
    ).strip()
    if api_key:
        message = message.replace(api_key, "[REDACTED]")
    return redact_sensitive_text(message)[:500]


def _validate_direct_url(value: Any) -> str:
    direct = str(value or "").strip()
    try:
        parsed = urlsplit(direct)
    except ValueError as exc:
        raise NodeImageUploadError(
            "NodeImage returned an invalid direct link."
        ) from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise NodeImageUploadError("NodeImage returned an invalid direct link.")
    return direct


def _markdown_escape_alt(value: str) -> str:
    # Preserve ordinary filename punctuation while escaping inline constructs.
    escaped = str(value or "image.png").replace("\\", "\\\\")
    for character in "`*_[]()<>":
        escaped = escaped.replace(character, f"\\{character}")
    return escaped


def _markdown_for_direct_url(direct: str, filename: str) -> str:
    safe_filename = Path(str(filename or "image.png")).name or "image.png"
    alt = _markdown_escape_alt(safe_filename)
    encoded_url = quote(direct, safe=":/?#[]@!$&'*+,;=%-._~")
    return f"![{alt}]({encoded_url})"


def _open_file_source(source: NodeImageFileSource):
    if isinstance(source, (str, Path)):
        return Path(source).open("rb")
    handle = source()
    if not hasattr(handle, "read") or not hasattr(handle, "close"):
        raise NodeImageUploadError("NodeImage file source was not readable.")
    return handle


async def _upload_image_source_once(
    image_source: bytes | NodeImageFileSource,
    safe_filename: str,
    effective: NodeImageEffectiveSettings,
) -> NodeImageUploadResult:
    file_handle = None
    if not isinstance(image_source, bytes):
        try:
            file_handle = _open_file_source(image_source)
        except OSError:
            raise
        except NodeImageUploadError:
            raise
        except Exception as exc:
            raise NodeImageUploadError("Image file could not be read") from exc

    try:
        form_source = image_source if isinstance(image_source, bytes) else file_handle
        form = aiohttp.FormData()
        form.add_field(
            "image",
            form_source,
            filename=safe_filename,
            content_type=image_content_type_for_filename(safe_filename),
        )
        session = get_pool().get(timeout_kind=TIMEOUT_NODEIMAGE)
        async with session.post(
            NODEIMAGE_UPLOAD_URL,
            data=form,
            headers={"X-API-Key": effective.api_key},
            allow_redirects=False,
        ) as response:
            if response.status in {401, 403}:
                raise NodeImageAuthError("NodeImage API key was rejected.")
            try:
                payload = await _read_response_json(response)
            except _NodeImageResponseReadError as exc:
                if response.status >= 500:
                    raise NodeImageTransientError(str(exc)) from exc
                raise

            message = _error_text(payload, response.status, effective.api_key)
            if response.status >= 500:
                raise NodeImageTransientError(message)
            if _is_auth_error(message):
                raise NodeImageAuthError("NodeImage API key was rejected.")
            if response.status >= 400 or payload.get("success") is False:
                raise NodeImageUploadError(message)

            links = payload.get("links")
            if not isinstance(links, dict):
                raise NodeImageUploadError(
                    "NodeImage response did not include upload links."
                )
            direct = _validate_direct_url(links.get("direct"))
            return NodeImageUploadResult(
                url=direct,
                markdown=_markdown_for_direct_url(direct, safe_filename),
            )
    except NodeImageAuthError:
        raise
    except NodeImageTransientError:
        raise
    except NodeImageUploadError:
        raise
    except RETRYABLE_CONNECT_ERRORS as exc:
        raise NodeImageTransientError("NodeImage upload request failed.") from exc
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        raise NodeImageUploadError("NodeImage upload request failed.") from exc
    finally:
        if file_handle is not None:
            file_handle.close()


async def upload_image_source(
    image_source: bytes | NodeImageFileSource,
    filename: str,
    effective: NodeImageEffectiveSettings,
) -> NodeImageUploadResult:
    if not effective.enabled:
        raise NodeImageConfigurationError("NodeImage upload is disabled.")
    if isinstance(image_source, bytes) and not image_source:
        raise NodeImageUploadError("Image file is empty.")
    if (
        isinstance(image_source, (str, Path))
        and Path(image_source).stat().st_size <= 0
    ):
        raise NodeImageUploadError("Image file is empty.")

    safe_filename = Path(str(filename or "image.png")).name or "image.png"
    attempt = 1
    while True:
        try:
            return await _upload_image_source_once(
                image_source,
                safe_filename,
                effective,
            )
        except NodeImageTransientError:
            if attempt >= NODEIMAGE_MAX_ATTEMPTS:
                raise
            attempt += 1
            await asyncio.sleep(NODEIMAGE_RETRY_BACKOFF_SECONDS)


async def upload_image_file(
    path: Path | str,
    filename: str,
    effective: NodeImageEffectiveSettings,
) -> NodeImageUploadResult:
    return await upload_image_source(path, filename, effective)


async def upload_image_bytes(
    image_bytes: bytes,
    filename: str,
    effective: NodeImageEffectiveSettings,
) -> NodeImageUploadResult:
    return await upload_image_source(image_bytes, filename, effective)
