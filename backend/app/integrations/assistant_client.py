import asyncio
import base64
import io
import json
import logging
import re
import time
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import aiohttp

from ..core import settings as config
from ..core import validators as ssrf
from ..core.api_paths import (
    CHAT_COMPLETIONS_API_PATH,
    RESPONSES_API_PATH,
    build_upstream_url,
)
from ..repositories.image_files import configure_pillow_image_limits, validate_image_header_bytes
from .session_pool import TIMEOUT_PROMPT_OPTIMIZER, get_pool
from .upstream.errors import UpstreamApiError
from .upstream.transport import classify_probe_status, read_limited_text_response

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
except ImportError:  # pragma: no cover - Pillow is a runtime dependency
    Image = None
    ImageOps = None
    UnidentifiedImageError = OSError

DecompressionBombWarning = (
    getattr(Image, "DecompressionBombWarning", Warning) if Image is not None else Warning
)

logger = logging.getLogger(__name__)

ASSISTANT_ALLOWED_API_PATHS = {CHAT_COMPLETIONS_API_PATH, RESPONSES_API_PATH}
ASSISTANT_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
ASSISTANT_DECODE_BYTES_PER_PIXEL = 8


class AssistantError(Exception):
    def __init__(self, message: str, status: int = 502):
        self.status = status
        super().__init__(message)


class AssistantTimeoutError(Exception):
    pass


def normalize_assistant_api_path(value: str | None) -> str:
    normalized = str(value or CHAT_COMPLETIONS_API_PATH).strip()
    return normalized if normalized in ASSISTANT_ALLOWED_API_PATHS else CHAT_COMPLETIONS_API_PATH


def validate_assistant_endpoint(api_url: str, api_path: str) -> str:
    if not api_url:
        raise ValueError("Prompt Optimizer endpoint URL is not configured for AI Assistant")
    normalized_api_url = ssrf.normalize_upstream_base_url(api_url)
    ssrf.validate_upstream_url(normalized_api_url, config.PROMPT_OPTIMIZER_HOST_ALLOWLIST)
    return build_upstream_url(normalized_api_url, normalize_assistant_api_path(api_path))


async def validate_assistant_endpoint_async(api_url: str, api_path: str) -> str:
    return await asyncio.to_thread(validate_assistant_endpoint, api_url, api_path)


def _json_schema_instruction(schema: dict[str, Any]) -> str:
    return (
        "Return only valid JSON matching this shape. Do not wrap in markdown.\n"
        f"{json.dumps(schema, ensure_ascii=False, sort_keys=True)}"
    )


def _build_chat_payload(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    image: dict[str, str | int | bool] | None,
    images: Sequence[dict[str, str | int | bool]] | None = None,
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    content: str | list[dict[str, Any]]
    if images:
        content = [{"type": "text", "text": user_prompt}]
        for index, item in enumerate(images):
            label = str(item.get("label") or f"Image {index + 1}")
            content.extend(
                [
                    {"type": "text", "text": label},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{item['mime_type']};base64,{item['b64']}",
                            "detail": "low",
                        },
                    },
                ]
            )
    elif image:
        content = [
            {"type": "text", "text": user_prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{image['mime_type']};base64,{image['b64']}",
                    "detail": "low",
                },
            },
        ]
    else:
        content = user_prompt
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
        "response_format": {"type": "json_object"},
    }


def _build_responses_payload(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    image: dict[str, str | int | bool] | None,
    images: Sequence[dict[str, str | int | bool]] | None = None,
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "input_text", "text": user_prompt}]
    if images:
        for index, item in enumerate(images):
            label = str(item.get("label") or f"Image {index + 1}")
            content.extend(
                [
                    {"type": "input_text", "text": label},
                    {
                        "type": "input_image",
                        "image_url": f"data:{item['mime_type']};base64,{item['b64']}",
                    },
                ]
            )
    elif image:
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:{image['mime_type']};base64,{image['b64']}",
            }
        )
    return {
        "model": model,
        "instructions": system_prompt,
        "input": [{"role": "user", "content": content}],
        "temperature": temperature,
        "max_output_tokens": max_tokens,
        "text": {"format": {"type": "json_object"}},
        "stream": False,
    }


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        chunks = [_extract_text(item) for item in value]
        return "".join(chunk for chunk in chunks if chunk)
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        if isinstance(value.get("content"), str):
            return value["content"]
        return "".join(_extract_text(child) for child in value.values())
    return ""


def _extract_assistant_text(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message")
        if isinstance(message, dict):
            content = _extract_text(message.get("content"))
            if content:
                return content
        delta = first.get("delta")
        if isinstance(delta, dict):
            content = _extract_text(delta.get("content"))
            if content:
                return content

    output_text = data.get("output_text")
    if isinstance(output_text, str):
        return output_text

    output = data.get("output")
    if isinstance(output, list):
        chunks: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                for child in content:
                    if isinstance(child, dict) and child.get("type") in {"output_text", "text"}:
                        chunks.append(_extract_text(child))
                    else:
                        chunks.append(_extract_text(child))
            else:
                chunks.append(_extract_text(content))
        text = "".join(chunks)
        if text:
            return text
    return ""


def parse_json_text(text: str) -> dict[str, Any]:
    cleaned = ASSISTANT_JSON_FENCE_RE.sub("", str(text or "").strip()).strip()
    if not cleaned:
        raise AssistantError("Assistant returned empty content")
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise AssistantError("Assistant returned non-JSON response") from e
    if not isinstance(parsed, dict):
        raise AssistantError("Assistant JSON response must be an object")
    return parsed


async def request_assistant_json(
    *,
    api_url: str,
    api_key: str,
    api_path: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    image: dict[str, str | int | bool] | None = None,
    images: Sequence[dict[str, str | int | bool]] | None = None,
    timeout_seconds: float | None = None,
    max_tokens: int = 900,
    temperature: float = 0.2,
    prevalidated_endpoint: str | None = None,
) -> tuple[dict[str, Any], str, int]:
    normalized_api_path = normalize_assistant_api_path(api_path)
    endpoint = prevalidated_endpoint or await validate_assistant_endpoint_async(
        api_url,
        normalized_api_path,
    )
    timeout_seconds = float(timeout_seconds or config.PROMPT_OPTIMIZER_TIMEOUT_SECONDS)
    model = str(model or config.PROMPT_OPTIMIZER_MODEL).strip() or config.PROMPT_OPTIMIZER_MODEL
    system = "\n\n".join([system_prompt.strip(), _json_schema_instruction(schema)])
    if normalized_api_path == RESPONSES_API_PATH:
        payload = _build_responses_payload(
            model=model,
            system_prompt=system,
            user_prompt=user_prompt,
            image=image,
            images=images,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    else:
        payload = _build_chat_payload(
            model=model,
            system_prompt=system,
            user_prompt=user_prompt,
            image=image,
            images=images,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    start = time.monotonic()
    try:
        session = get_pool().get(timeout_kind=TIMEOUT_PROMPT_OPTIMIZER)
        async with session.post(
            endpoint,
            json=payload,
            headers=headers,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(
                total=timeout_seconds,
                connect=min(timeout_seconds, 10.0),
                sock_connect=min(timeout_seconds, 10.0),
                sock_read=timeout_seconds,
            ),
        ) as resp:
            ssrf.validate_response_peer_ip(resp, "AI Assistant endpoint")
            if resp.status != 200:
                raise AssistantError(
                    f"AI Assistant upstream returned HTTP {resp.status}",
                    status=resp.status,
                )
            try:
                response_text = await read_limited_text_response(
                    resp,
                    config.AI_ASSISTANT_MAX_RESPONSE_MB * 1024 * 1024,
                    label="AI Assistant response",
                )
                data = json.loads(response_text)
            except UpstreamApiError as e:
                raise AssistantError(str(e)) from e
            except Exception as e:
                raise AssistantError("AI Assistant returned non-JSON response") from e
    except (aiohttp.ServerTimeoutError, TimeoutError, asyncio.TimeoutError) as e:
        raise AssistantTimeoutError("AI Assistant request timed out") from e
    except aiohttp.ClientError as e:
        raise AssistantError(f"AI Assistant connection error: {e}") from e

    content = _extract_assistant_text(data)
    parsed = parse_json_text(content)
    model_used = str(data.get("model") or model)
    duration_ms = int((time.monotonic() - start) * 1000)
    return parsed, model_used, duration_ms


async def probe_assistant_endpoint(
    *,
    api_url: str,
    api_key: str,
    api_path: str,
    model: str,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    start = time.monotonic()
    try:
        _data, model_used, duration_ms = await request_assistant_json(
            api_url=api_url,
            api_key=api_key,
            api_path=api_path,
            model=model,
            system_prompt="You are a connectivity probe.",
            user_prompt="Return JSON with ok true.",
            schema={"ok": True},
            timeout_seconds=timeout_seconds,
            max_tokens=20,
            temperature=0.0,
        )
        return {
            "status": "ok",
            "message": f"AI Assistant responded successfully with model {model_used}",
            "model": model_used,
            "duration_ms": duration_ms,
            "status_code": 200,
        }
    except AssistantTimeoutError:
        raise
    except AssistantError as e:
        status, message = classify_probe_status("POST", getattr(e, "status", 502))
        return {
            "status": status,
            "message": str(e) if status == "error" else message,
            "model": model,
            "duration_ms": int((time.monotonic() - start) * 1000),
            "status_code": e.status,
        }


def _image_has_alpha(image) -> bool:
    return "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info)


def _fit_preview(image, *, has_alpha: bool, byte_limit_error: str) -> tuple[bytes, str]:
    scale = 1.0
    while True:
        candidate = image.copy()
        if scale < 1.0:
            candidate.thumbnail(
                (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                Image.Resampling.LANCZOS,
            )
        output = io.BytesIO()
        if has_alpha:
            candidate.save(output, format="PNG", optimize=True, compress_level=9)
            mime_type = "image/png"
        else:
            if candidate.mode not in {"RGB", "L"}:
                candidate = candidate.convert("RGB")
            quality = 85
            while True:
                output.seek(0)
                output.truncate(0)
                candidate.save(output, format="JPEG", quality=quality, optimize=True)
                if output.tell() <= config.AI_ASSISTANT_IMAGE_MAX_BYTES or quality <= 45:
                    break
                quality -= 10
            mime_type = "image/jpeg"
        data = output.getvalue()
        if len(data) <= config.AI_ASSISTANT_IMAGE_MAX_BYTES:
            return data, mime_type
        if min(candidate.size) <= 1:
            raise AssistantError(byte_limit_error, status=400)
        scale *= 0.75


def _enforce_vision_decode_budget(image, *, resident_input_bytes: int) -> None:
    width, height = image.size
    pixels = int(width) * int(height)
    if width <= 0 or height <= 0 or pixels > config.MAX_IMAGE_PIXELS:
        raise AssistantError("Image exceeds the configured pixel limit for AI analysis", status=400)

    preview_pixels = config.AI_ASSISTANT_IMAGE_MAX_SIDE**2
    estimated_bytes = (
        max(0, int(resident_input_bytes))
        + pixels * ASSISTANT_DECODE_BYTES_PER_PIXEL
        + preview_pixels * ASSISTANT_DECODE_BYTES_PER_PIXEL
        + config.AI_ASSISTANT_IMAGE_MAX_BYTES * 3
    )
    memory_limit = config.AI_ASSISTANT_IMAGE_DECODE_MAX_MB * 1024 * 1024
    if estimated_bytes > memory_limit:
        raise AssistantError(
            "Image exceeds the decoded-memory limit for AI analysis",
            status=400,
        )


def _prepare_vision_preview(
    opener,
    *,
    header: bytes,
    filename: str,
    content_type: str,
    resident_input_bytes: int,
    decode_error: str,
    byte_limit_error: str,
) -> dict[str, str | int | bool]:
    if Image is None or ImageOps is None:
        raise AssistantError("Pillow is required for AI Assistant image analysis", status=400)
    try:
        expected_format = validate_image_header_bytes(header, filename=filename, content_type=content_type)
        configure_pillow_image_limits()
        with warnings.catch_warnings():
            warnings.simplefilter("error", DecompressionBombWarning)
            with opener() as image:
                _enforce_vision_decode_budget(
                    image,
                    resident_input_bytes=resident_input_bytes,
                )
                if str(getattr(image, "format", "")).lower().replace("jpg", "jpeg") != expected_format:
                    raise ValueError("Image decoder format does not match image data")
                image.verify()
            with opener() as image:
                _enforce_vision_decode_budget(
                    image,
                    resident_input_bytes=resident_input_bytes,
                )
                if getattr(image, "is_animated", False):
                    raise AssistantError("Animated images are not supported for AI analysis", status=400)
                if str(getattr(image, "format", "")).lower().replace("jpg", "jpeg") != expected_format:
                    raise ValueError("Image decoder format does not match image data")
                if expected_format == "jpeg":
                    image.draft("RGB", (config.AI_ASSISTANT_IMAGE_MAX_SIDE, config.AI_ASSISTANT_IMAGE_MAX_SIDE))
                image.load()
                image = ImageOps.exif_transpose(image)
                source_width, source_height = image.size
                source_has_alpha = _image_has_alpha(image)
                image.thumbnail((config.AI_ASSISTANT_IMAGE_MAX_SIDE, config.AI_ASSISTANT_IMAGE_MAX_SIDE))
                data, mime_type = _fit_preview(image, has_alpha=source_has_alpha, byte_limit_error=byte_limit_error)
                width, height = image.size
    except AssistantError:
        raise
    except (
        OSError,
        UnidentifiedImageError,
        SyntaxError,
        ValueError,
        DecompressionBombWarning,
        getattr(Image, "DecompressionBombError", OSError),
    ) as e:
        raise AssistantError(decode_error, status=400) from e
    return {
        "b64": base64.b64encode(data).decode("ascii"),
        "mime_type": mime_type,
        "bytes": len(data),
        "width": width,
        "height": height,
        "source_width": source_width,
        "source_height": source_height,
        "source_has_alpha": source_has_alpha,
    }


def prepare_vision_preview(path: Path) -> dict[str, str | int | bool]:
    try:
        with path.open("rb") as file:
            header = file.read(512)
    except OSError as e:
        raise AssistantError("Gallery image could not be decoded for AI analysis", status=400) from e
    return _prepare_vision_preview(
        lambda: Image.open(path),
        header=header,
        filename=path.name,
        content_type="",
        resident_input_bytes=0,
        decode_error="Gallery image could not be decoded for AI analysis",
        byte_limit_error="Gallery image preview exceeds AI Assistant byte limit",
    )


def prepare_vision_preview_bytes(
    image_bytes: bytes,
    *,
    filename: str = "",
    content_type: str = "",
) -> dict[str, str | int | bool]:
    return _prepare_vision_preview(
        lambda: Image.open(io.BytesIO(image_bytes)),
        header=image_bytes[:512],
        filename=filename,
        content_type=content_type,
        resident_input_bytes=len(image_bytes),
        decode_error="Image data must be a fully decodable supported raster image",
        byte_limit_error="Uploaded image preview exceeds AI Assistant byte limit",
    )
