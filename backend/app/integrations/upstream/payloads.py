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


__all__ = [name for name in globals() if not name.startswith("__")]


