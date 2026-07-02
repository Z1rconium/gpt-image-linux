import asyncio
import base64
import io
import json
import logging
import re
import time
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
from ..repositories.image_files import validate_image_bytes
from .session_pool import TIMEOUT_PROMPT_OPTIMIZER, get_pool
from .upstream_client import UpstreamApiError, classify_probe_status, read_limited_text_response

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
except ImportError:  # pragma: no cover - Pillow is a runtime dependency
    Image = None
    ImageOps = None
    UnidentifiedImageError = OSError

logger = logging.getLogger(__name__)

ASSISTANT_ALLOWED_API_PATHS = {CHAT_COMPLETIONS_API_PATH, RESPONSES_API_PATH}
ASSISTANT_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


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
    image: dict[str, str] | None,
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    content: str | list[dict[str, Any]]
    if image:
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
    image: dict[str, str] | None,
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "input_text", "text": user_prompt}]
    if image:
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
    image: dict[str, str] | None = None,
    timeout_seconds: float | None = None,
    max_tokens: int = 900,
    temperature: float = 0.2,
) -> tuple[dict[str, Any], str, int]:
    normalized_api_path = normalize_assistant_api_path(api_path)
    endpoint = validate_assistant_endpoint(api_url, normalized_api_path)
    timeout_seconds = float(timeout_seconds or config.PROMPT_OPTIMIZER_TIMEOUT_SECONDS)
    model = str(model or config.PROMPT_OPTIMIZER_MODEL).strip() or config.PROMPT_OPTIMIZER_MODEL
    system = "\n\n".join([system_prompt.strip(), _json_schema_instruction(schema)])
    if normalized_api_path == RESPONSES_API_PATH:
        payload = _build_responses_payload(
            model=model,
            system_prompt=system,
            user_prompt=user_prompt,
            image=image,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    else:
        payload = _build_chat_payload(
            model=model,
            system_prompt=system,
            user_prompt=user_prompt,
            image=image,
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


def prepare_vision_preview(path: Path) -> dict[str, str | int]:
    if Image is None or ImageOps is None:
        raise AssistantError("Pillow is required for AI Assistant image analysis", status=400)
    width = 0
    height = 0
    data = b""
    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            image.thumbnail(
                (config.AI_ASSISTANT_IMAGE_MAX_SIDE, config.AI_ASSISTANT_IMAGE_MAX_SIDE)
            )
            output = io.BytesIO()
            quality = 85
            while True:
                output.seek(0)
                output.truncate(0)
                image.save(output, format="JPEG", quality=quality, optimize=True)
                data = output.getvalue()
                if len(data) <= config.AI_ASSISTANT_IMAGE_MAX_BYTES or quality <= 45:
                    break
                quality -= 10
            width, height = image.size
    except (OSError, UnidentifiedImageError) as e:
        raise AssistantError("Gallery image could not be decoded for AI analysis", status=400) from e

    if len(data) > config.AI_ASSISTANT_IMAGE_MAX_BYTES:
        raise AssistantError(
            "Gallery image preview exceeds AI Assistant byte limit",
            status=400,
        )
    validate_image_bytes(data, filename="assistant-preview.jpg", content_type="image/jpeg")
    return {
        "b64": base64.b64encode(data).decode("ascii"),
        "mime_type": "image/jpeg",
        "bytes": len(data),
        "width": width,
        "height": height,
    }
