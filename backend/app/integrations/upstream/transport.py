import aiohttp
import asyncio
import base64
import json
import logging
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

from ...core import settings as config
from ...core.api_paths import (
    CHAT_COMPLETIONS_API_PATH,
    RESPONSES_API_PATH,
    build_upstream_url,
    normalize_api_path,
)
from ...core.observability import observe_job_stage
from ...core import validators as ssrf
from ...repositories.gallery.mutations import add_to_gallery_async
from ...repositories.image_files import (
    detect_image_format,
    generate_image_id,
    validate_image_bytes,
)
from ...schemas.gallery import GalleryEntry
from ...schemas.generation import EditRequest, GenerateRequest
from ..session_pool import TIMEOUT_PROBE, TIMEOUT_UPSTREAM, get_pool

ProgressCallback = Callable[[str, str], None]
logger = logging.getLogger(__name__)


from .errors import *
from .payloads import *

MAX_IMAGE_REDIRECTS = 3
IMAGE_DOWNLOAD_CHUNK_SIZE = 1024 * 1024
UPSTREAM_RESPONSE_CHUNK_SIZE = 1024 * 1024


async def read_limited_response(response: aiohttp.ClientResponse, max_bytes: int) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise UpstreamImageDownloadError(
                    f"Image too large: {content_length} bytes (max {max_bytes})"
                )
        except ValueError:
            pass

    chunks: list[bytes] = []
    total = 0
    async for chunk in response.content.iter_chunked(IMAGE_DOWNLOAD_CHUNK_SIZE):
        total += len(chunk)
        if total > max_bytes:
            raise UpstreamImageDownloadError(f"Image too large: {total} bytes (max {max_bytes})")
        chunks.append(chunk)
    return b"".join(chunks)


def get_response_charset(response: aiohttp.ClientResponse) -> str:
    charset = getattr(response, "charset", None)
    if charset:
        return charset

    content_type = response.headers.get("Content-Type", "")
    match = re.search(r"charset=([^;]+)", content_type, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return "utf-8"


async def read_limited_text_response(
    response: aiohttp.ClientResponse,
    max_bytes: int,
    *,
    label: str = "Upstream response",
) -> str:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise UpstreamApiError(
                    f"{label} too large: {content_length} bytes (max {max_bytes})"
                )
        except ValueError:
            pass

    content = getattr(response, "content", None)
    if content is None or not hasattr(content, "iter_chunked"):
        response_text = await response.text()
        response_size = len(response_text.encode("utf-8"))
        if response_size > max_bytes:
            raise UpstreamApiError(
                f"{label} too large: {response_size} bytes (max {max_bytes})"
            )
        return response_text

    chunks: list[bytes] = []
    total = 0
    async for chunk in content.iter_chunked(UPSTREAM_RESPONSE_CHUNK_SIZE):
        total += len(chunk)
        if total > max_bytes:
            raise UpstreamApiError(f"{label} too large: {total} bytes (max {max_bytes})")
        chunks.append(chunk)

    body = b"".join(chunks)
    encoding = get_response_charset(response)
    try:
        return body.decode(encoding)
    except (LookupError, UnicodeDecodeError) as e:
        raise UpstreamApiError(f"{label} is not valid {encoding} text") from e


async def download_image_url(
    session: aiohttp.ClientSession,
    image_url: str,
    *,
    max_redirects: int = MAX_IMAGE_REDIRECTS,
) -> bytes:
    current_url = image_url
    max_bytes = config.MAX_FILE_SIZE_MB * 1024 * 1024

    for _ in range(max_redirects + 1):
        ssrf.validate_image_url(current_url)
        async with session.get(
            current_url,
            headers={"User-Agent": "opencode"},
            allow_redirects=False,
        ) as img_resp:
            if 300 <= img_resp.status < 400:
                location = img_resp.headers.get("Location")
                if not location:
                    raise UpstreamImageDownloadError(f"Image URL redirect missing Location: {current_url}")
                current_url = urljoin(current_url, location)
                continue
            if img_resp.status != 200:
                raise UpstreamImageDownloadError(
                    f"Failed to download image from {current_url}: {img_resp.status}"
                )
            ssrf.validate_response_peer_ip(img_resp, "Image URL")
            return await read_limited_response(img_resp, max_bytes)

    raise UpstreamImageDownloadError("Image URL redirected too many times")


async def extract_image_bytes(
    download_session: aiohttp.ClientSession,
    image_data: dict,
    response_preview: str,
    max_bytes: int,
) -> bytes:
    if "b64_json" in image_data and image_data["b64_json"]:
        b64_json = str(image_data.pop("b64_json"))
        max_b64_chars = ((max_bytes + 2) // 3) * 4 + 4
        if len(b64_json) > max_b64_chars:
            raise UpstreamImageDownloadError(
                f"Image too large: base64 payload is {len(b64_json)} chars "
                f"(max {max_b64_chars})"
            )
        return base64.b64decode(b64_json)

    if "url" in image_data and image_data["url"]:
        return await download_image_url(download_session, image_data["url"])

    raise UpstreamImageDownloadError(
        f"No image data (b64_json or url) in upstream response: {response_preview}"
    )


def validate_generated_image_bytes(image_bytes: bytes, filename: str) -> None:
    validate_image_bytes(image_bytes, filename=filename)


def classify_probe_status(method: str, status: int) -> tuple[str, str]:
    if status in {200, 204}:
        return "ok", f"{method} probe succeeded with HTTP {status}"
    if status in {401, 403}:
        return "ok", f"{method} probe reached the endpoint and got HTTP {status}"
    if method == "OPTIONS" and status in {404, 410}:
        return "warning", f"{method} probe returned HTTP {status}; upstream may only support POST"
    if status in {404, 410}:
        return "error", f"{method} probe returned HTTP {status}; check API URL/path"
    if status in {405, 501}:
        return "warning", f"{method} probe is not supported by upstream (HTTP {status})"
    if 300 <= status < 400:
        return "error", f"{method} probe returned redirect HTTP {status}; redirects are not followed"
    if status >= 500:
        return "warning", f"{method} probe reached upstream but got HTTP {status}"
    return "ok", f"{method} probe reached upstream with HTTP {status}"


async def probe_upstream_endpoint(
    api_url: str,
    api_path: str,
    api_key: str = "",
) -> dict[str, Any]:
    upstream_url = build_upstream_url(api_url, api_path)
    ssrf.validate_upstream_url(upstream_url, config.UPSTREAM_HOST_ALLOWLIST)

    headers = {"User-Agent": "opencode"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    probe_errors: list[str] = []
    unsupported_method_result: dict[str, Any] | None = None
    session = get_pool().get(timeout_kind=TIMEOUT_PROBE)
    for method in ("OPTIONS", "HEAD"):
        try:
            async with session.request(
                method,
                upstream_url,
                headers=headers,
                allow_redirects=False,
            ) as resp:
                ssrf.validate_response_peer_ip(resp, "Upstream API probe")
                status, message = classify_probe_status(method, resp.status)
                result = {
                    "status": status,
                    "message": message,
                    "method": method,
                    "status_code": resp.status,
                }
                if resp.status in {405, 501}:
                    unsupported_method_result = result
                    continue
                return result
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            probe_errors.append(f"{method}: {e}")

    if unsupported_method_result:
        return unsupported_method_result

    return {
        "status": "error",
        "message": "Upstream probe failed: " + "; ".join(probe_errors),
        "method": None,
        "status_code": None,
    }


def get_upstream_error_message(
    status: int,
    response_text: str,
    is_json_response: bool,
) -> str:
    if is_json_response:
        try:
            error_body = json.loads(response_text)
            if isinstance(error_body, dict):
                error = error_body.get("error")
                if isinstance(error, dict):
                    return _sanitize_upstream_error_text(error.get("message", response_text))
            return _sanitize_upstream_error_text(response_text)
        except Exception:
            return _sanitize_upstream_error_text(response_text)
    return f"HTTP {status}: {_sanitize_upstream_error_text(response_text[:200])}"


def raise_upstream_error(
    status: int,
    response_text: str,
    is_json_response: bool,
    api_path: str,
):
    error_msg = get_upstream_error_message(status, response_text, is_json_response)
    unsupported_markers = (
        "not support",
        "not_supported",
        "unsupported",
        "not found",
        "unknown endpoint",
        "no route",
    )
    if api_path == "/v1/images/edits" and (
        status in {404, 405, 501}
        or any(marker in error_msg.lower() for marker in unsupported_markers)
    ):
        raise UpstreamApiError(
            f"Upstream API does not support /v1/images/edits ({status}): {error_msg}"
        )
    raise UpstreamApiError(f"Upstream API error ({status}): {error_msg}")


async def parse_upstream_json_response(
    resp: aiohttp.ClientResponse,
    api_path: str,
    progress: ProgressCallback | None,
) -> tuple[dict[str, Any], str]:
    status = resp.status
    max_response_bytes = config.MAX_UPSTREAM_JSON_MB * 1024 * 1024
    response_text = await read_limited_text_response(
        resp,
        max_response_bytes,
        label="Upstream JSON response",
    )
    if progress:
        progress("received_api_response", "Received upstream API response")

    content_type = resp.headers.get("Content-Type", "")
    is_json_response = is_json_content_type(content_type) or looks_like_json_body(
        response_text
    )

    if status >= 400:
        raise_upstream_error(status, response_text, is_json_response, api_path)

    if not is_json_response:
        raise UpstreamApiError(
            f"Upstream returned non-JSON content-type ({status}): {response_text[:200]}"
        )

    if progress:
        progress("parsing_json_response", "Parsing JSON response")
    try:
        result = json.loads(response_text)
    except json.JSONDecodeError:
        raise UpstreamApiError(
            f"Upstream returned non-JSON ({status}): {response_text[:200]}"
        )
    if not isinstance(result, dict):
        raise UpstreamApiError("Upstream JSON response must be an object")
    return result, response_text


async def parse_upstream_chat_completion_response(
    resp: aiohttp.ClientResponse,
    api_path: str,
    progress: ProgressCallback | None,
) -> tuple[dict[str, Any], str]:
    status = resp.status
    max_response_bytes = config.MAX_UPSTREAM_JSON_MB * 1024 * 1024
    response_text = await read_limited_text_response(
        resp,
        max_response_bytes,
        label="Upstream chat response",
    )
    if progress:
        progress("received_api_response", "Received upstream API response")

    content_type = resp.headers.get("Content-Type", "")
    is_json_response = is_json_content_type(content_type) or looks_like_json_body(
        response_text
    )
    is_sse_response = "text/event-stream" in content_type or response_text.lstrip().startswith(
        "data:"
    )

    if status >= 400:
        raise_upstream_error(status, response_text, is_json_response, api_path)

    if progress:
        progress("parsing_json_response", "Parsing upstream response")

    if is_json_response:
        try:
            result = json.loads(response_text)
            if not isinstance(result, dict):
                raise UpstreamApiError("Upstream chat response must be an object")
            return result, response_text
        except json.JSONDecodeError as e:
            raise UpstreamApiError(
                f"Upstream returned non-JSON ({status}): {response_text[:200]}"
            ) from e

    if is_sse_response:
        events = parse_sse_events(response_text)
        if not events:
            raise UpstreamApiError(
                f"No SSE chat completion events in upstream response: {response_text[:200]}"
            )
        return {"_sse_events": events}, response_text

    raise UpstreamApiError(
        f"Upstream returned unsupported content-type ({status}): {response_text[:200]}"
    )


__all__ = [name for name in globals() if not name.startswith("__")]


