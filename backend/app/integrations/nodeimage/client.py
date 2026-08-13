"""Server-side NodeImage API client."""

from __future__ import annotations

import asyncio
import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from ...core import secrets
from ...core.redaction import redact_sensitive_text
from ...core.validators import get_env_var_ref_name, resolve_env_var_ref
from ..session_pool import TIMEOUT_NODEIMAGE, get_pool

NODEIMAGE_API_URL = "https://api.nodeimage.com"
NODEIMAGE_UPLOAD_URL = f"{NODEIMAGE_API_URL}/api/upload"
NODEIMAGE_HOST_ALLOWLIST = "api.nodeimage.com"
MAX_NODEIMAGE_RESPONSE_BYTES = 2 * 1024 * 1024


class NodeImageConfigurationError(ValueError):
    pass


class NodeImageAuthError(RuntimeError):
    pass


class NodeImageUploadError(RuntimeError):
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
    raw = str(value or "").strip()
    if not raw:
        return ""
    env_var = get_env_var_ref_name(raw)
    if env_var:
        resolved = resolve_env_var_ref(raw)
        if resolved:
            return resolved
        raise NodeImageConfigurationError(
            f"NodeImage API key environment variable {env_var} is not set or empty."
        )
    if raw not in secrets.configured_secret_ids():
        return raw
    try:
        return secrets.resolve_secret(
            raw,
            purpose="nodeimage_api_key",
            target_url=NODEIMAGE_API_URL,
            host_allowlist=NODEIMAGE_HOST_ALLOWLIST,
        )
    except secrets.SecretRegistryError as exc:
        raise NodeImageConfigurationError(f"NodeImage API key: {exc}") from exc


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
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise NodeImageUploadError("NodeImage returned an unreadable response.") from exc
    try:
        parsed = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NodeImageUploadError("NodeImage returned an invalid JSON response.") from exc
    if not isinstance(parsed, dict):
        raise NodeImageUploadError("NodeImage returned an invalid response.")
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


async def upload_image_bytes(
    image_bytes: bytes,
    filename: str,
    effective: NodeImageEffectiveSettings,
) -> NodeImageUploadResult:
    if not effective.enabled:
        raise NodeImageConfigurationError("NodeImage upload is disabled.")
    if not image_bytes:
        raise NodeImageUploadError("Image file is empty.")

    safe_filename = Path(str(filename or "image.png")).name or "image.png"
    content_type = mimetypes.guess_type(safe_filename)[0] or "application/octet-stream"
    headers = {"X-API-Key": effective.api_key}
    last_error: Exception | None = None

    for attempt in range(2):
        form = aiohttp.FormData()
        form.add_field(
            "image",
            image_bytes,
            filename=safe_filename,
            content_type=content_type,
        )
        try:
            session = get_pool().get(timeout_kind=TIMEOUT_NODEIMAGE)
            async with session.post(
                NODEIMAGE_UPLOAD_URL,
                data=form,
                headers=headers,
                allow_redirects=False,
            ) as response:
                if response.status in {401, 403}:
                    raise NodeImageAuthError("NodeImage API key was rejected.")
                try:
                    payload = await _read_response_json(response)
                except NodeImageUploadError as exc:
                    last_error = exc
                    if response.status >= 500 and attempt == 0:
                        await asyncio.sleep(0.2)
                        continue
                    raise

                message = _error_text(payload, response.status, effective.api_key)
                if response.status >= 500:
                    last_error = NodeImageUploadError(message)
                    if attempt == 0:
                        await asyncio.sleep(0.2)
                        continue
                    raise last_error
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
                markdown = str(links.get("markdown") or "").strip()
                if not markdown:
                    raise NodeImageUploadError(
                        "NodeImage response did not include upload links."
                    )
                return NodeImageUploadResult(url=direct, markdown=markdown)
        except NodeImageAuthError:
            raise
        except NodeImageUploadError as exc:
            last_error = exc
            if attempt == 0 and str(exc) == "NodeImage returned an unreadable response.":
                await asyncio.sleep(0.2)
                continue
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            last_error = exc
            if attempt == 0:
                await asyncio.sleep(0.2)
                continue
            raise NodeImageUploadError("NodeImage upload request failed.") from exc

    raise NodeImageUploadError("NodeImage upload request failed.") from last_error
