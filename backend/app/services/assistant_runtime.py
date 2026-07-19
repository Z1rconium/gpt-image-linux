import asyncio
import contextvars
import json
import logging
import os
import re
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile

from ..api import presets
from ..api.app_state import app
from ..api.uploads import is_image_upload, resolve_upload_content_type
from ..core import settings as config
from ..core import validators as ssrf
from ..core.utils import utc_now
from ..integrations import assistant_client
from ..repositories.coordination import (
    acquire_background_slot,
    claim_next_gallery_job,
    delete_gallery_job,
    get_gallery_job,
    release_background_slot,
    renew_gallery_job_lease,
    reserve_gallery_job_capacity,
    update_gallery_job,
    update_gallery_job_progress,
)
from ..repositories.gallery.mutations import is_gallery_filename_referenced
from ..repositories.gallery.queries import (
    get_gallery_entries_by_ids,
    get_gallery_entry,
    get_gallery_id_batch,
    get_gallery_selection_snapshot,
)
from ..repositories.image_files import safe_image_path
from ..repositories.image_jobs import get_generate_job
from ..repositories.settings import (
    get_gallery_ai_metadata,
    upsert_gallery_ai_metadata,
)
from ..schemas.common import ApiPath
from ..schemas.assistant import (
    AssistantEditPlanRequest,
    AssistantEditPlanResponse,
    AssistantGalleryBatchJobStatus,
    AssistantGalleryBatchRequest,
    AssistantGalleryImageResponse,
    AssistantGalleryMetadataResponse,
    AssistantHealthResponse,
    AssistantImagePromptResponse,
    AssistantJobDiagnoseRequest,
    AssistantJobDiagnoseResponse,
    AssistantPromptCheckRequest,
    AssistantPromptCheckResponse,
    AssistantPromptIssue,
    AssistantPromptRewriteRequest,
    AssistantPromptRewriteResponse,
    AssistantPromptVariant,
    AssistantPromptVariantsRequest,
    AssistantPromptVariantsResponse,
    AssistantRecommendParamsRequest,
    AssistantRecommendParamsResponse,
)
from ..schemas.settings import AIAssistantSettingsRequest

logger = logging.getLogger(__name__)

ASSISTANT_IMAGE_UPLOAD_CHUNK_BYTES = 1024 * 1024
IMAGE_PROMPT_SYSTEM_PROMPT = """You reconstruct one directly usable image-generation prompt from visible pixels.
Organize the prompt in this order when the information is present: subject identity and action; overall color palette and color relationships; artistic medium and rendering style; environment; composition and viewpoint; lighting; materials and surface qualities; reasonably inferable lens and depth of field; clearly legible text.
Give particular attention to the image's overall color language and artistic style. Describe dominant and accent colors, temperature, saturation, value range, contrast, color harmony, and how colors are distributed across the image. Characterize style through visible evidence such as medium, rendering technique, line quality, shape language, shading, texture, and level of detail, without attributing the work to an artist.
When a widely recognizable named character, mascot, or public figure is clearly supported by distinctive visible evidence, including characters from anime, manga, comics, games, film, or television, explicitly name the identity and, for fictional characters, the source work or franchise. Treat this grounded identification as part of the subject description, not as attribution of the input image's source. If the identity is uncertain, do not guess; describe the visible identifying features neutrally instead.
Use high information density. Faithfully preserve spatial relationships, scale, pose, framing, and visual emphasis.
The prompt field must contain only the single prompt in the requested language. Do not put analysis, reasoning, a title, Markdown, source attribution, or meta-language such as \"this image\" in that field.
Do not invent unseen brands, artist names, exact camera or lens settings, or background stories. Use neutral descriptions for details that cannot be determined confidently.
When preview metadata says source_has_alpha is true, preserve transparency in the reconstruction and do not infer an opaque matte background.
Do not create a separate negative prompt. Keep safety warnings out of the prompt and return them only in the warnings field."""


@dataclass(frozen=True)
class AssistantRuntime:
    api_url: str
    api_key: str
    api_path: str
    endpoint: str
    model: str
    timeout_seconds: int


@dataclass
class AssistantRuntimeHolder:
    runtime: AssistantRuntime | None = None


_batch_assistant_runtime: contextvars.ContextVar[AssistantRuntimeHolder | None] = contextvars.ContextVar(
    "batch_assistant_runtime", default=None
)
_batch_target_language: contextvars.ContextVar[Literal["en", "zh-CN"]] = contextvars.ContextVar(
    "batch_target_language", default="en"
)

AI_ANALYZE_JOB_KIND = "ai_analyze"
GALLERY_SELECTION_TOKEN_KIND = "batch_selection"
MAX_ACTIVE_AI_ANALYZE_JOBS = 1
AI_ANALYZE_JOB_LEASE_SECONDS = 300
AI_ANALYZE_JOB_LEASE_RENEW_SECONDS = 60
AI_ANALYZE_JOB_BATCH_SIZE = 25
AI_ASSISTANT_SLOT_RETRY_SECONDS = 1.0
AI_ASSISTANT_TEXT_FIELD_LIMITS = {
    "summary": 1200,
    "rationale": 1200,
    "message": 1200,
    "suggestion": 1200,
    "angle": 1200,
    "title": 120,
    "description": 2000,
    "prompt": 4000,
    "rewritten_prompt": 4000,
    "edit_prompt": 4000,
    "style": 500,
    "composition": 800,
    "lighting": 500,
}
AI_ASSISTANT_STRING_LIST_FIELD_LIMITS = {
    "warnings": (10, 500),
    "likely_causes": (8, 800),
    "recommended_actions": (8, 800),
    "source_requirements": (8, 800),
    "subjects": (12, 200),
    "colors": (12, 100),
}
SENSITIVE_FIELD_NAMES = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "secret",
    "token",
    "password",
    "credential",
}
SECRET_VALUE_RE = re.compile(
    r"(?i)\b(api[-_ ]?key|authorization|bearer|token|secret|password|cookie)\b"
    r"([^\n\r]{0,24})([:=]\s*| +)([^\s,;]+)"
)
BEARER_VALUE_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")


class AIAnalyzeJobLeaseLost(RuntimeError):
    pass


def _clamp_text(value: Any, max_chars: int) -> str:
    text = str(value or "").strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()


def _truncate_assistant_data(value: Any, *, key: str = "") -> Any:
    if isinstance(value, dict):
        return {
            str(child_key): _truncate_assistant_data(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        item_limit, char_limit = AI_ASSISTANT_STRING_LIST_FIELD_LIMITS.get(key, (20, 1000))
        return [
            _truncate_assistant_data(item, key=key if isinstance(item, str) else "")
            for item in value[:item_limit]
        ][:item_limit] if not all(isinstance(item, str) for item in value) else [
            _clamp_text(item, char_limit) for item in value if _clamp_text(item, char_limit)
        ][:item_limit]
    if isinstance(value, str):
        return _clamp_text(value, AI_ASSISTANT_TEXT_FIELD_LIMITS.get(key, 2000))
    return value


def _assistant_request_semaphore() -> asyncio.Semaphore:
    limit = max(1, int(config.AI_ASSISTANT_MAX_CONCURRENCY or 1))
    state = getattr(app.state, "assistant_request_semaphore_state", None)
    if not state or state[0] != limit:
        semaphore = asyncio.Semaphore(limit)
        app.state.assistant_request_semaphore_state = (limit, semaphore)
        return semaphore
    return state[1]


def _assistant_slot_expires_at(timeout_seconds: int) -> str:
    lease_seconds = max(30, int(timeout_seconds or config.PROMPT_OPTIMIZER_TIMEOUT_SECONDS) + 30)
    return (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()


def _assistant_slot_owner() -> str:
    worker_id = getattr(app.state, "worker_id", f"{os.getpid()}-{id(app)}")
    return f"{worker_id}-{os.urandom(8).hex()}"


async def _acquire_assistant_slot(timeout_seconds: int) -> tuple[str, str]:
    owner = _assistant_slot_owner()
    slot_name = await asyncio.to_thread(
        acquire_background_slot,
        name_prefix="ai_assistant_request",
        owner=owner,
        slot_count=config.AI_ASSISTANT_MAX_CONCURRENCY,
        lease_expires_at=_assistant_slot_expires_at(timeout_seconds),
        now=utc_now(),
    )
    if not slot_name:
        raise HTTPException(status_code=429, detail="AI Assistant is at its concurrency limit")
    return slot_name, owner


async def _release_assistant_slot(slot_name: str, owner: str) -> None:
    try:
        await asyncio.to_thread(
            release_background_slot,
            name=slot_name,
            owner=owner,
            now=utc_now(),
        )
    except Exception:
        logger.warning("Failed to release AI Assistant concurrency slot %s", slot_name, exc_info=True)


@asynccontextmanager
async def _assistant_request_limit(timeout_seconds: int, *, wait_for_slot: bool = False):
    while True:
        semaphore = _assistant_request_semaphore()
        slot_name = ""
        slot_owner = ""
        async with semaphore:
            try:
                slot_name, slot_owner = await _acquire_assistant_slot(timeout_seconds)
            except HTTPException as e:
                if not wait_for_slot or e.status_code != 429:
                    raise
            else:
                try:
                    yield
                finally:
                    if slot_name and slot_owner:
                        await _release_assistant_slot(slot_name, slot_owner)
                return
        await asyncio.sleep(AI_ASSISTANT_SLOT_RETRY_SECONDS)


def _warnings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_clamp_text(item, 500) for item in value if _clamp_text(item, 500)][:10]


def _image_prompt_system_prompt(target_language: Literal["en", "zh-CN"] | None = None) -> str:
    if target_language == "zh-CN":
        language_instruction = "Write the prompt in Simplified Chinese."
    elif target_language == "en":
        language_instruction = "Write the prompt in English."
    else:
        language_instruction = "Use the language you would normally use for this API."
    return f"{IMAGE_PROMPT_SYSTEM_PROMPT}\n{language_instruction}"


def _string_list(value: Any, *, max_items: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_clamp_text(item, 800) for item in value if _clamp_text(item, 800)][:max_items]


def _assistant_settings(draft: AIAssistantSettingsRequest | None = None) -> dict:
    settings = presets.get_ai_assistant_settings()
    if draft is not None:
        settings = presets.apply_ai_assistant_settings(settings, draft)
    return settings


def _resolve_runtime(*, vision: bool = False, settings: dict | None = None) -> AssistantRuntime:
    settings = presets.effective_ai_assistant_settings(settings if settings is not None else _assistant_settings())
    if not settings.get("enabled"):
        raise HTTPException(status_code=400, detail="AI Assistant is not enabled")
    api_url = str(settings.get("api_url") or "").strip()
    if not api_url:
        raise HTTPException(status_code=400, detail="Prompt Optimizer endpoint URL is not configured for AI Assistant")
    api_key = presets.resolve_ai_assistant_api_key(settings)
    if not api_key:
        raise HTTPException(status_code=400, detail="Prompt Optimizer API key is not configured for AI Assistant")
    api_path = assistant_client.normalize_assistant_api_path(settings.get("api_path"))
    try:
        endpoint = assistant_client.validate_assistant_endpoint(api_url, api_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    model_key = "vision_model" if vision else "model"
    model = str(settings.get(model_key) or settings.get("model") or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="AI Assistant model is not configured")
    timeout_seconds = int(settings.get("timeout_seconds") or 60)
    return AssistantRuntime(api_url, api_key, api_path, endpoint, model, timeout_seconds)


async def _resolve_runtime_async(*, vision: bool = False, settings: dict | None = None) -> AssistantRuntime:
    return await asyncio.to_thread(_resolve_runtime, vision=vision, settings=settings)


async def _assistant_json(
    *,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    vision: bool = False,
    image: dict[str, str | int | bool] | None = None,
    images: list[dict[str, str | int | bool]] | None = None,
    max_tokens: int = 900,
    temperature: float = 0.2,
    runtime: AssistantRuntime | None = None,
) -> tuple[dict[str, Any], str, int]:
    runtime = runtime or await _resolve_runtime_async(vision=vision)
    try:
        async with _assistant_request_limit(runtime.timeout_seconds):
            data, model_used, duration_ms = await assistant_client.request_assistant_json(
                api_url=runtime.api_url,
                api_key=runtime.api_key,
                api_path=runtime.api_path,
                model=runtime.model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                image=image,
                images=images,
                timeout_seconds=runtime.timeout_seconds,
                max_tokens=max_tokens,
                temperature=temperature,
                prevalidated_endpoint=runtime.endpoint,
            )
            return _truncate_assistant_data(data), model_used, duration_ms
    except assistant_client.AssistantTimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e)) from e
    except assistant_client.AssistantError as e:
        status_code = 400 if e.status == 400 else 502
        raise HTTPException(status_code=status_code, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e



__all__ = [name for name in globals() if not name.startswith("__")]
