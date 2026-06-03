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

from ..core import settings as config
from ..core.api_paths import (
    CHAT_COMPLETIONS_API_PATH,
    RESPONSES_API_PATH,
    build_upstream_url,
    normalize_api_path,
)
from ..core.observability import observe_job_stage
from ..core import validators as ssrf
from ..repositories import storage
from ..schemas.models import EditRequest, GenerateRequest
from .session_pool import TIMEOUT_PROBE, TIMEOUT_UPSTREAM, get_pool

ProgressCallback = Callable[[str, str], None]
logger = logging.getLogger(__name__)


class ImageEditSource(Protocol):
    temp_path: Path
    filename: str
    content_type: str


class UpstreamApiError(Exception):
    pass


class UpstreamImageDownloadError(UpstreamApiError):
    pass


OUTPUT_FORMATS = {
    "png": {"extension": "png", "media_type": "image/png"},
    "jpeg": {"extension": "jpg", "media_type": "image/jpeg"},
    "webp": {"extension": "webp", "media_type": "image/webp"},
}
DETECTED_FORMAT_EXTENSIONS = {
    "avif": "avif",
    "bmp": "bmp",
    "gif": "gif",
    "heif": "heif",
    "ico": "ico",
    "jpeg": "jpg",
    "png": "png",
    "tiff": "tiff",
    "webp": "webp",
}
DATA_IMAGE_URL_RE = re.compile(
    r"data:image/(?:png|jpe?g|webp|gif|avif|bmp);base64,(?P<data>[A-Za-z0-9+/=\s]+)",
    re.IGNORECASE,
)
MARKDOWN_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\((?P<target><[^>]+>|[^\s)]+)(?:\s+[\"'][^\"']*[\"'])?\)"
)
HTTP_IMAGE_URL_RE = re.compile(r"https?://[^\s<>'\")]+")


DOWNLOAD_CONCURRENCY = 3


def _warn_if_socks5_upstream_resolves_private(
    upstream_url: str,
    socks5_proxy: str | None,
) -> None:
    if not socks5_proxy:
        return

    parsed = urlsplit(upstream_url)
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return

    _resolved_host, resolved_ips = ssrf.resolve_hostname(hostname)
    private_ips = [ip for ip in resolved_ips if ssrf.is_private_ip(ip)]
    if not private_ips:
        return

    logger.warning(
        "SOCKS5 proxy is enabled and upstream host '%s' resolved locally to "
        "private/internal IP(s): %s. Pre-connection SSRF validation still blocks "
        "private local resolutions, but the SOCKS5 proxy is the trust boundary "
        "for remote DNS and upstream network reachability.",
        hostname,
        ", ".join(private_ips),
    )


def get_output_format_info(output_format: str) -> dict[str, str]:
    return OUTPUT_FORMATS.get(output_format, OUTPUT_FORMATS["png"])


def extract_response_image_result(value: Any) -> dict[str, str] | None:
    if isinstance(value, str) and value:
        if value.startswith(("http://", "https://")):
            return {"url": value}
        return {"b64_json": value}

    if isinstance(value, dict):
        for key in ("url", "b64_json", "base64", "data", "result"):
            image = extract_response_image_result(value.get(key))
            if image:
                return image

    if isinstance(value, list):
        for item in value:
            image = extract_response_image_result(item)
            if image:
                return image

    return None


def extract_response_image_results(result: dict[str, Any]) -> list[dict[str, str]]:
    image_results: list[dict[str, str]] = []

    for item in result.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "image_generation_call":
            continue

        image = extract_response_image_result(item.get("result"))
        if image:
            image_results.append(image)

    return image_results


def build_chat_completions_request_data(payload: GenerateRequest) -> dict[str, Any]:
    return {
        "model": payload.model,
        "messages": [{"role": "user", "content": payload.prompt}],
        "stream": False,
    }


def parse_sse_events(response_text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    data_lines: list[str] = []

    def flush_data_lines():
        if not data_lines:
            return
        data = "\n".join(data_lines).strip()
        data_lines.clear()
        if not data or data == "[DONE]":
            return
        try:
            events.append(json.loads(data))
        except json.JSONDecodeError as e:
            raise UpstreamApiError(f"Upstream returned malformed SSE JSON: {data[:200]}") from e

    for raw_line in response_text.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            flush_data_lines()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())

    flush_data_lines()
    return events


def is_json_content_type(content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type == "application/json" or media_type.endswith("+json")


def looks_like_json_body(response_text: str) -> bool:
    stripped = response_text.lstrip()
    return stripped.startswith("{") or stripped.startswith("[")


def append_unique_image_result(
    images: list[dict[str, str]],
    seen: set[tuple[str, str]],
    image: dict[str, str] | None,
) -> None:
    if not image:
        return
    key_name = "url" if image.get("url") else "b64_json"
    key_value = image.get(key_name, "")
    if not key_value:
        return
    dedupe_key = (key_name, key_value)
    if dedupe_key in seen:
        return
    seen.add(dedupe_key)
    images.append(image)


def normalize_chat_image_reference(value: str) -> dict[str, str] | None:
    text = value.strip().strip("<>")
    if not text:
        return None

    data_url_match = DATA_IMAGE_URL_RE.fullmatch(text)
    if data_url_match:
        return {"b64_json": re.sub(r"\s+", "", data_url_match.group("data"))}

    if text.startswith(("http://", "https://")):
        return {"url": text}

    return None


def extract_chat_image_references_from_text(text: str) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for match in DATA_IMAGE_URL_RE.finditer(text):
        append_unique_image_result(
            images,
            seen,
            {"b64_json": re.sub(r"\s+", "", match.group("data"))},
        )

    for match in MARKDOWN_IMAGE_RE.finditer(text):
        append_unique_image_result(
            images,
            seen,
            normalize_chat_image_reference(match.group("target")),
        )

    for match in HTTP_IMAGE_URL_RE.finditer(text):
        append_unique_image_result(
            images,
            seen,
            normalize_chat_image_reference(match.group(0)),
        )

    return images


def collect_chat_completion_text(result: dict[str, Any]) -> list[str]:
    events = result.get("_sse_events")
    items = events if isinstance(events, list) else [result]
    chunks_by_choice: dict[int, list[str]] = {}

    for item in items:
        if not isinstance(item, dict):
            continue
        for choice in item.get("choices", []):
            if not isinstance(choice, dict):
                continue
            index = int(choice.get("index") or 0)
            chunks = chunks_by_choice.setdefault(index, [])
            for key in ("message", "delta"):
                container = choice.get(key)
                if not isinstance(container, dict):
                    continue
                content = container.get("content")
                if isinstance(content, str):
                    chunks.append(content)

    return ["".join(chunks) for chunks in chunks_by_choice.values() if chunks]


def collect_chat_image_results(
    value: Any,
    images: list[dict[str, str]],
    seen: set[tuple[str, str]],
    key_hint: str = "",
) -> None:
    if isinstance(value, str):
        if key_hint in {"url", "image_url"}:
            append_unique_image_result(images, seen, normalize_chat_image_reference(value))
            return
        if key_hint in {"b64_json", "base64"}:
            append_unique_image_result(images, seen, {"b64_json": value.strip()})
            return
        for image in extract_chat_image_references_from_text(value):
            append_unique_image_result(images, seen, image)
        return

    if isinstance(value, list):
        for item in value:
            collect_chat_image_results(item, images, seen, key_hint)
        return

    if isinstance(value, dict):
        for key, child in value.items():
            collect_chat_image_results(child, images, seen, str(key))


def extract_chat_completion_image_results(result: dict[str, Any]) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for text in collect_chat_completion_text(result):
        for image in extract_chat_image_references_from_text(text):
            append_unique_image_result(images, seen, image)

    collect_chat_image_results(result, images, seen)
    return images


def get_image_transfer_stage(image_data: dict) -> tuple[str, str]:
    if image_data.get("b64_json"):
        return ("decoding_b64_json", "Decoding b64_json image")
    if image_data.get("url"):
        return ("downloading_image_url", "Downloading image URL")
    return ("extracting_image_bytes", "Extracting image bytes")


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
    storage.validate_image_bytes(image_bytes, filename=filename)


def _build_image_params(payload: GenerateRequest) -> dict[str, Any]:
    request_data: dict[str, Any] = {
        "model": payload.model,
        "prompt": payload.prompt,
        "size": payload.size,
        "n": payload.n,
        "quality": payload.quality,
        "output_format": payload.output_format,
    }
    if payload.response_format is not None:
        request_data["response_format"] = payload.response_format
    if payload.output_format != "png" and payload.output_compression is not None:
        request_data["output_compression"] = payload.output_compression
    return request_data


def build_gallery_metadata(
    payload: GenerateRequest,
    api_path: str,
    api_preset_name: str | None,
) -> dict[str, Any]:
    return {
        "model": payload.model,
        "quality": payload.quality,
        "output_format": payload.output_format,
        "output_compression": payload.output_compression,
        "response_format": payload.response_format,
        "n": payload.n,
        "api_path": api_path,
        "api_preset_name": api_preset_name,
    }


def build_responses_request_data(payload: GenerateRequest) -> dict[str, Any]:
    model = (payload.model or config.DEFAULT_RESPONSES_MODEL or "").strip()
    return {"prompt": payload.prompt, "model": model or payload.model}


async def save_gallery_entries_from_upstream_data(
    *,
    download_session: aiohttp.ClientSession,
    data: list[dict[str, Any]],
    response_preview: str,
    payload: GenerateRequest,
    format_extension: str,
    gallery_metadata: dict[str, Any],
    save_message: str,
    progress: ProgressCallback | None,
) -> list[storage.GalleryEntry]:
    max_bytes = config.MAX_FILE_SIZE_MB * 1024 * 1024
    total = len(data)

    async def process_one(image_index: int, image_data: dict) -> storage.GalleryEntry:
        transfer_stage, transfer_message = get_image_transfer_stage(image_data)
        if progress:
            progress(
                transfer_stage,
                f"{transfer_message} ({image_index + 1}/{total})",
            )
        with observe_job_stage("download_decode"):
            image_bytes = await extract_image_bytes(
                download_session,
                image_data,
                response_preview,
                max_bytes,
            )
        try:
            if progress:
                progress(
                    "validating_image_bytes",
                    f"Validating decoded image ({image_index + 1}/{total})",
                )
            with observe_job_stage("validate"):
                if len(image_bytes) > max_bytes:
                    raise UpstreamImageDownloadError(
                        f"Image too large: {len(image_bytes)} bytes (max {max_bytes})"
                    )

                detected_format = storage.detect_image_format(image_bytes)
                detected_extension = DETECTED_FORMAT_EXTENSIONS.get(
                    detected_format or "",
                    format_extension,
                )
                image_id = storage.generate_image_id()
                filename = f"{image_id}.{detected_extension}"
                validate_generated_image_bytes(image_bytes, filename)
            entry_metadata = {**gallery_metadata}
            if detected_format:
                entry_metadata["output_format"] = detected_format

            if progress:
                progress(
                    "saving_images",
                    f"{save_message} ({image_index + 1}/{total})",
                )
            entry = await storage.add_to_gallery_async(
                image_bytes=image_bytes,
                image_id=image_id,
                prompt=payload.prompt,
                size=payload.size,
                filename=filename,
                metadata=entry_metadata,
            )
            return entry
        finally:
            del image_bytes

    if total <= 1:
        entries = [await process_one(0, data[0])]
    else:
        sem = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)

        async def bounded(idx: int, img: dict) -> storage.GalleryEntry:
            async with sem:
                return await process_one(idx, img)

        entries = list(
            await asyncio.gather(*(bounded(i, d) for i, d in enumerate(data)))
        )
    return entries


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
            return error_body.get("error", {}).get("message", response_text)
        except Exception:
            return response_text
    return f"HTTP {status}: {response_text[:200]}"


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
            return json.loads(response_text), response_text
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


async def call_image_generation_api(
    api_url: str,
    api_key: str,
    api_path: str,
    payload: GenerateRequest,
    api_preset_name: str | None = None,
    progress: ProgressCallback | None = None,
    socks5_proxy: str | None = None,
) -> list[storage.GalleryEntry]:
    api_path = normalize_api_path(api_path)
    upstream_url = build_upstream_url(api_url, api_path)

    _warn_if_socks5_upstream_resolves_private(upstream_url, socks5_proxy)
    ssrf.validate_upstream_url(upstream_url, config.UPSTREAM_HOST_ALLOWLIST)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "opencode",
    }

    if api_path == RESPONSES_API_PATH:
        if progress:
            progress("building_responses_payload", "Building Responses API payload")
        request_data = build_responses_request_data(payload)
    elif api_path == CHAT_COMPLETIONS_API_PATH:
        if progress:
            progress(
                "building_chat_completions_payload",
                "Building Chat Completions API payload",
            )
        request_data = build_chat_completions_request_data(payload)
    else:
        if progress:
            progress("building_generation_payload", "Building image generation payload")
        request_data = _build_image_params(payload)

    format_info = get_output_format_info(payload.output_format)
    gallery_metadata = build_gallery_metadata(payload, api_path, api_preset_name)

    pool = get_pool()
    upstream_session = pool.get(timeout_kind=TIMEOUT_UPSTREAM, socks5_proxy=socks5_proxy)
    download_session = pool.get(timeout_kind=TIMEOUT_UPSTREAM)

    if progress:
        progress("waiting_for_api", "Waiting for upstream API response")
    with observe_job_stage("upstream_wait"):
        async with upstream_session.post(
            upstream_url,
            json=request_data,
            headers=headers,
            allow_redirects=False,
        ) as resp:
            if not socks5_proxy:
                ssrf.validate_response_peer_ip(resp, "Upstream API")
            if api_path == CHAT_COMPLETIONS_API_PATH:
                result, response_text = await parse_upstream_chat_completion_response(
                    resp, api_path, progress
                )
            else:
                result, response_text = await parse_upstream_json_response(
                    resp, api_path, progress
                )

    if api_path == RESPONSES_API_PATH:
        if progress:
            progress(
                "extracting_response_image_output",
                "Extracting image_generation_call output",
            )
        data = extract_response_image_results(result)
    elif api_path == CHAT_COMPLETIONS_API_PATH:
        if progress:
            progress(
                "extracting_chat_completion_image_output",
                "Extracting Chat Completions image output",
            )
        data = extract_chat_completion_image_results(result)
    else:
        if progress:
            progress("extracting_generation_data", "Extracting image data array")
        data = result.get("data", [])
    if not data:
        text_preview = response_text[:200] if isinstance(response_text, str) else str(response_text)[:200]
        raise UpstreamApiError(f"No image data in upstream response: {text_preview}")

    response_preview = response_text[:200]
    del response_text
    del result
    entries = await save_gallery_entries_from_upstream_data(
        download_session=download_session,
        data=data,
        response_preview=response_preview,
        payload=payload,
        format_extension=format_info["extension"],
        gallery_metadata=gallery_metadata,
        save_message="Saving generated images",
        progress=progress,
    )

    return entries


async def call_image_edit_api(
    api_url: str,
    api_key: str,
    payload: EditRequest,
    image_sources: Sequence[ImageEditSource],
    api_preset_name: str | None = None,
    progress: ProgressCallback | None = None,
    socks5_proxy: str | None = None,
) -> list[storage.GalleryEntry]:
    if not image_sources:
        raise UpstreamApiError("At least one edit source image is required")

    api_path = "/v1/images/edits"
    upstream_url = build_upstream_url(api_url, api_path)

    _warn_if_socks5_upstream_resolves_private(upstream_url, socks5_proxy)
    ssrf.validate_upstream_url(upstream_url, config.UPSTREAM_HOST_ALLOWLIST)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "opencode",
    }
    format_info = get_output_format_info(payload.output_format)
    gallery_metadata = build_gallery_metadata(payload, api_path, api_preset_name)

    if progress:
        progress("building_edit_form", "Building multipart edit request")
    image_files = []
    try:
        form = aiohttp.FormData()
        image_field_name = "image" if len(image_sources) == 1 else "image[]"
        for source in image_sources:
            image_file = source.temp_path.open("rb")
            image_files.append(image_file)
            form.add_field(
                image_field_name,
                image_file,
                filename=source.filename or "image.png",
                content_type=source.content_type or "application/octet-stream",
            )
        for key, value in _build_image_params(payload).items():
            form.add_field(key, str(value))

        pool = get_pool()
        upstream_session = pool.get(timeout_kind=TIMEOUT_UPSTREAM, socks5_proxy=socks5_proxy)
        if progress:
            upload_message = (
                "Uploading source image and edit parameters"
                if len(image_sources) == 1
                else "Uploading source images and edit parameters"
            )
            progress("uploading_edit_image", upload_message)
        with observe_job_stage("upstream_wait"):
            async with upstream_session.post(
                upstream_url,
                data=form,
                headers=headers,
                allow_redirects=False,
            ) as resp:
                if not socks5_proxy:
                    ssrf.validate_response_peer_ip(resp, "Upstream API")
                result, response_text = await parse_upstream_json_response(
                    resp, api_path, progress
                )

        if progress:
            progress("extracting_edit_data", "Extracting edited image data array")
        data = result.get("data", [])
        if not data:
            raise UpstreamApiError(f"No image data in upstream response: {response_text[:200]}")

        response_preview = response_text[:200]
        del response_text
        del result
        download_session = pool.get(timeout_kind=TIMEOUT_UPSTREAM)
        return await save_gallery_entries_from_upstream_data(
            download_session=download_session,
            data=data,
            response_preview=response_preview,
            payload=payload,
            format_extension=format_info["extension"],
            gallery_metadata=gallery_metadata,
            save_message="Saving edited images",
            progress=progress,
        )
    finally:
        for image_file in image_files:
            image_file.close()
