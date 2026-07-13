import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile

from .. import presets
from ..app_state import app
from ..uploads import is_image_upload, resolve_upload_content_type
from ...core import settings as config
from ...core import validators as ssrf
from ...core.utils import utc_now
from ...integrations import assistant_client
from ...repositories import storage
from ...schemas.models import (
    ApiPath,
    AssistantEditPlanRequest,
    AssistantEditPlanResponse,
    AssistantGalleryBatchJobStatus,
    AssistantGalleryBatchRequest,
    AssistantGalleryImageResponse,
    AssistantGalleryMetadataResponse,
    AssistantHealthResponse,
    AssistantImagePromptResponse,
    AIAssistantSettingsRequest,
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

logger = logging.getLogger(__name__)
router = APIRouter()

ASSISTANT_IMAGE_UPLOAD_CHUNK_BYTES = 1024 * 1024
IMAGE_PROMPT_SYSTEM_PROMPT = """You reconstruct one directly usable image-generation prompt from visible pixels.
Organize the prompt in this order when the information is present: subject identity and action; overall color palette and color relationships; artistic medium and rendering style; environment; composition and viewpoint; lighting; materials and surface qualities; reasonably inferable lens and depth of field; clearly legible text.
Give particular attention to the image's overall color language and artistic style. Describe dominant and accent colors, temperature, saturation, value range, contrast, color harmony, and how colors are distributed across the image. Characterize style through visible evidence such as medium, rendering technique, line quality, shape language, shading, texture, and level of detail, without attributing the work to an artist.
When a widely recognizable named character, mascot, or public figure is clearly supported by distinctive visible evidence, including characters from anime, manga, comics, games, film, or television, explicitly name the identity and, for fictional characters, the source work or franchise. Treat this grounded identification as part of the subject description, not as attribution of the input image's source. If the identity is uncertain, do not guess; describe the visible identifying features neutrally instead.
Use high information density. Faithfully preserve spatial relationships, scale, pose, framing, and visual emphasis.
The prompt field must contain only the single prompt in the requested language. Do not put analysis, reasoning, a title, Markdown, source attribution, or meta-language such as \"this image\" in that field.
Do not invent unseen brands, artist names, exact camera or lens settings, or background stories. Use neutral descriptions for details that cannot be determined confidently.
Do not create a separate negative prompt. Keep safety warnings out of the prompt and return them only in the warnings field."""

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
        storage.acquire_background_slot,
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
            storage.release_background_slot,
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


def _resolve_runtime(*, vision: bool = False, settings: dict | None = None) -> tuple[str, str, str, str, int]:
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
        assistant_client.validate_assistant_endpoint(api_url, api_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    model_key = "vision_model" if vision else "model"
    model = str(settings.get(model_key) or settings.get("model") or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="AI Assistant model is not configured")
    timeout_seconds = int(settings.get("timeout_seconds") or 60)
    return api_url, api_key, api_path, model, timeout_seconds


async def _assistant_json(
    *,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    vision: bool = False,
    image: dict[str, str] | None = None,
    max_tokens: int = 900,
    temperature: float = 0.2,
) -> tuple[dict[str, Any], str, int]:
    api_url, api_key, api_path, model, timeout_seconds = _resolve_runtime(vision=vision)
    try:
        async with _assistant_request_limit(timeout_seconds):
            data, model_used, duration_ms = await assistant_client.request_assistant_json(
                api_url=api_url,
                api_key=api_key,
                api_path=api_path,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                image=image,
                timeout_seconds=timeout_seconds,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return _truncate_assistant_data(data), model_used, duration_ms
    except assistant_client.AssistantTimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e)) from e
    except assistant_client.AssistantError as e:
        status_code = 400 if e.status == 400 else 502
        raise HTTPException(status_code=status_code, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/api/assistant/health", response_model=AssistantHealthResponse)
async def assistant_health(req: AIAssistantSettingsRequest | None = Body(default=None)):
    settings = presets.effective_ai_assistant_settings(_assistant_settings(req))
    model = str(settings.get("model") or "").strip() or "gpt-4o-mini"
    try:
        api_url, api_key, api_path, model, timeout_seconds = _resolve_runtime(settings=settings)
    except HTTPException as e:
        return AssistantHealthResponse(
            status="error",
            message=str(e.detail),
            model=model,
            duration_ms=0,
            status_code=e.status_code,
        )

    try:
        result = await assistant_client.probe_assistant_endpoint(
            api_url=api_url,
            api_key=api_key,
            api_path=api_path,
            model=model,
            timeout_seconds=timeout_seconds,
        )
    except assistant_client.AssistantTimeoutError as e:
        return AssistantHealthResponse(
            status="error",
            message=str(e),
            model=model,
            duration_ms=timeout_seconds * 1000,
            status_code=504,
        )
    except Exception:
        logger.exception("AI Assistant health probe unexpected error")
        return AssistantHealthResponse(
            status="error",
            message="AI Assistant health check failed",
            model=model,
            duration_ms=0,
            status_code=502,
        )
    return AssistantHealthResponse(**result)


def _prompt_context(req: AssistantPromptRewriteRequest | AssistantPromptCheckRequest) -> str:
    return "\n".join(
        [
            f"Image API path: {req.api_path or 'unspecified'}",
            f"Image model: {req.model or 'unspecified'}",
            f"Size: {req.size or 'unspecified'}",
            f"Quality: {req.quality or 'unspecified'}",
        ]
    )


@router.post("/api/assistant/prompt/rewrite", response_model=AssistantPromptRewriteResponse)
async def rewrite_prompt(req: AssistantPromptRewriteRequest):
    data, model, duration_ms = await _assistant_json(
        system_prompt=(
            "You improve image generation prompts while preserving the user's core subject, "
            "composition, and intent. Keep the result directly usable as a prompt."
        ),
        user_prompt="\n\n".join(
            [
                _prompt_context(req),
                f"Target language: {req.target_language}",
                f"Instruction: {req.instruction or 'improve clarity and visual specificity'}",
                "Prompt:",
                req.prompt,
            ]
        ),
        schema={"rewritten_prompt": "string", "warnings": ["string"]},
        max_tokens=900,
        temperature=0.35,
    )
    rewritten = str(data.get("rewritten_prompt") or "").strip()
    if not rewritten:
        raise HTTPException(status_code=502, detail="AI Assistant returned no rewritten prompt")
    return AssistantPromptRewriteResponse(
        rewritten_prompt=rewritten[:4000],
        warnings=_warnings(data.get("warnings")),
        model=model,
        duration_ms=duration_ms,
    )


@router.post("/api/assistant/prompt/check", response_model=AssistantPromptCheckResponse)
async def check_prompt(req: AssistantPromptCheckRequest):
    data, model, duration_ms = await _assistant_json(
        system_prompt="You review image generation prompts for clarity, contradictions, and generation risks.",
        user_prompt="\n\n".join([_prompt_context(req), "Prompt:", req.prompt]),
        schema={
            "score": 0,
            "summary": "string",
            "issues": [{"severity": "info|warning|error", "message": "string", "suggestion": "string"}],
            "warnings": ["string"],
        },
        max_tokens=700,
        temperature=0.1,
    )
    issues = []
    for issue in data.get("issues") if isinstance(data.get("issues"), list) else []:
        if not isinstance(issue, dict):
            continue
        severity = str(issue.get("severity") or "info").strip()
        if severity not in {"info", "warning", "error"}:
            severity = "info"
        message = str(issue.get("message") or "").strip()
        if not message:
            continue
        suggestion = str(issue.get("suggestion") or "").strip() or None
        issues.append(AssistantPromptIssue(severity=severity, message=message, suggestion=suggestion))
    score = int(data.get("score") or 0)
    score = min(100, max(0, score))
    return AssistantPromptCheckResponse(
        score=score,
        summary=str(data.get("summary") or "").strip(),
        issues=issues[:10],
        warnings=_warnings(data.get("warnings")),
        model=model,
        duration_ms=duration_ms,
    )


@router.post("/api/assistant/prompt/variants", response_model=AssistantPromptVariantsResponse)
async def prompt_variants(req: AssistantPromptVariantsRequest):
    data, model, duration_ms = await _assistant_json(
        system_prompt="You create distinct, directly usable image prompt variants without changing the user's subject.",
        user_prompt="\n\n".join(
            [
                _prompt_context(req),
                f"Target language: {req.target_language}",
                f"Variant count: {req.count}",
                f"Instruction: {req.instruction or 'create useful creative alternatives'}",
                "Prompt:",
                req.prompt,
            ]
        ),
        schema={"variants": [{"title": "string", "prompt": "string", "angle": "string"}], "warnings": ["string"]},
        max_tokens=1200,
        temperature=0.45,
    )
    variants: list[AssistantPromptVariant] = []
    for item in data.get("variants") if isinstance(data.get("variants"), list) else []:
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt") or "").strip()
        if not prompt:
            continue
        variants.append(
            AssistantPromptVariant(
                title=str(item.get("title") or f"Variant {len(variants) + 1}").strip()[:120],
                prompt=prompt[:4000],
                angle=str(item.get("angle") or "").strip() or None,
            )
        )
        if len(variants) >= req.count:
            break
    if not variants:
        raise HTTPException(status_code=502, detail="AI Assistant returned no variants")
    return AssistantPromptVariantsResponse(
        variants=variants,
        warnings=_warnings(data.get("warnings")),
        model=model,
        duration_ms=duration_ms,
    )


def _allowed_recommendation(api_path: ApiPath, data: dict[str, Any]) -> dict[str, Any]:
    recommendation: dict[str, Any] = {
        "model_name": str(data.get("model_name") or "").strip() or None,
        "rationale": _clamp_text(data.get("rationale"), 1200),
    }
    warnings = _warnings(data.get("warnings"))
    if api_path == "/v1/images/generations":
        size = str(data.get("size") or "").strip()
        quality = str(data.get("quality") or "").strip()
        output_format = str(data.get("output_format") or "").strip()
        n = data.get("n")
        if size:
            recommendation["size"] = size
        if quality in {"auto", "low", "medium", "high"}:
            recommendation["quality"] = quality
        if output_format in {"png", "jpeg", "webp"}:
            recommendation["output_format"] = output_format
        try:
            parsed_n = int(n)
        except (TypeError, ValueError):
            parsed_n = None
        if parsed_n is not None:
            recommendation["n"] = min(10, max(1, parsed_n))
    else:
        warnings.append(f"{api_path} does not support image size, quality, format, or count controls here")
    recommendation["warnings"] = warnings
    return recommendation


@router.post("/api/assistant/generate/recommend-params", response_model=AssistantRecommendParamsResponse)
async def recommend_generate_params(req: AssistantRecommendParamsRequest):
    data, model, duration_ms = await _assistant_json(
        system_prompt=(
            "You recommend only parameters supported by the selected image API path. "
            "For /v1/responses and /v1/chat/completions, do not recommend size, quality, output_format, or n."
        ),
        user_prompt=json.dumps(req.model_dump(), ensure_ascii=False, sort_keys=True),
        schema={
            "model_name": "string|null",
            "size": "string|null",
            "quality": "auto|low|medium|high|null",
            "output_format": "png|jpeg|webp|null",
            "n": "number|null",
            "rationale": "string",
            "warnings": ["string"],
        },
        max_tokens=500,
        temperature=0.15,
    )
    filtered = _allowed_recommendation(req.api_path, data)
    return AssistantRecommendParamsResponse(
        **filtered,
        model=model,
        duration_ms=duration_ms,
    )


def _safe_job_snapshot(job: dict[str, Any], *, include_prompt: bool) -> dict[str, object]:
    allowed = {
        "job_id",
        "status",
        "stage",
        "message",
        "operation",
        "size",
        "created_at",
        "started_at",
        "completed_at",
        "updated_at",
        "model",
        "quality",
        "output_format",
        "output_compression",
        "response_format",
        "n",
        "api_path",
        "api_preset_name",
        "duration",
        "stage_timings",
        "image_width",
        "image_height",
        "error",
    }
    result = {
        key: _redact_diagnostic_value(key, job[key])
        for key in allowed
        if key in job and job[key] is not None
    }
    if include_prompt and job.get("prompt"):
        result["prompt"] = _redact_diagnostic_value("prompt", str(job["prompt"])[:1000])
    return result


def _redact_text(value: Any) -> str:
    text = str(value or "")
    text = BEARER_VALUE_RE.sub("Bearer [REDACTED]", text)
    text = SECRET_VALUE_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}{match.group(3)}[REDACTED]", text)
    redacted_parts: list[str] = []
    for part in re.split(r"(\s+)", text):
        lower = part.lower()
        if "://" in part:
            redacted_parts.append(ssrf.redact_url(part))
        elif any(marker in lower for marker in ("api_key=", "apikey=", "token=", "secret=", "password=", "authorization=")):
            redacted_parts.append("[REDACTED]")
        else:
            redacted_parts.append(part)
    return "".join(redacted_parts)


def _redact_diagnostic_value(key: str, value: Any) -> object:
    normalized_key = str(key or "").lower()
    if any(marker in normalized_key for marker in SENSITIVE_FIELD_NAMES):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(child_key): _redact_diagnostic_value(str(child_key), child) for child_key, child in value.items()}
    if isinstance(value, list):
        return [_redact_diagnostic_value(normalized_key, item) for item in value[:20]]
    if isinstance(value, str):
        limit = 1000 if normalized_key == "prompt" else 2000
        return _clamp_text(_redact_text(value), limit)
    return value


@router.post("/api/assistant/jobs/{job_id}/diagnose", response_model=AssistantJobDiagnoseResponse)
async def diagnose_job(job_id: str, req: AssistantJobDiagnoseRequest | None = None):
    job = await asyncio.to_thread(storage.get_generate_job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    safe_job = _safe_job_snapshot(job, include_prompt=(req.include_prompt if req else False))
    data, model, duration_ms = await _assistant_json(
        system_prompt="You diagnose failed or slow image generation/edit jobs. Never mention or infer secrets.",
        user_prompt=json.dumps(safe_job, ensure_ascii=False, sort_keys=True),
        schema={
            "summary": "string",
            "likely_causes": ["string"],
            "recommended_actions": ["string"],
            "warnings": ["string"],
        },
        max_tokens=700,
        temperature=0.2,
    )
    return AssistantJobDiagnoseResponse(
        summary=_clamp_text(data.get("summary"), 1200),
        likely_causes=_string_list(data.get("likely_causes")),
        recommended_actions=_string_list(data.get("recommended_actions")),
        safe_job=safe_job,
        warnings=_warnings(data.get("warnings")),
        model=model,
        duration_ms=duration_ms,
    )


@router.post("/api/assistant/edit/plan", response_model=AssistantEditPlanResponse)
async def plan_edit(req: AssistantEditPlanRequest):
    data, model, duration_ms = await _assistant_json(
        system_prompt=(
            "You plan image edits. Produce an edit prompt and source requirements. "
            "The plan is advisory only and should not assume submission."
        ),
        user_prompt=json.dumps(req.model_dump(), ensure_ascii=False, sort_keys=True),
        schema={
            "edit_prompt": "string",
            "source_requirements": ["string"],
            "suggested_size": "string|null",
            "warnings": ["string"],
            "confidence": 0.0,
            "next_action": "confirm|revise|add_sources",
        },
        max_tokens=700,
        temperature=0.25,
    )
    next_action = str(data.get("next_action") or "confirm").strip()
    if next_action not in {"confirm", "revise", "add_sources"}:
        next_action = "confirm"
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    return AssistantEditPlanResponse(
        edit_prompt=str(data.get("edit_prompt") or "").strip()[:4000],
        source_requirements=_string_list(data.get("source_requirements")),
        suggested_size=str(data.get("suggested_size") or "").strip() or None,
        warnings=_warnings(data.get("warnings")),
        confidence=min(1.0, max(0.0, confidence)),
        next_action=next_action,
        model=model,
        duration_ms=duration_ms,
    )


async def _read_image_prompt_upload(image: UploadFile) -> bytes:
    if not is_image_upload(image):
        raise HTTPException(status_code=400, detail="Upload must be a supported raster image file")

    max_bytes = config.MAX_FILE_SIZE_MB * 1024 * 1024
    image_bytes = bytearray()
    while True:
        chunk = await image.read(ASSISTANT_IMAGE_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        if len(image_bytes) + len(chunk) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Uploaded image is too large. Max size is {config.MAX_FILE_SIZE_MB} MB",
            )
        image_bytes.extend(chunk)

    if not image_bytes:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")

    filename = image.filename or "image"
    content_type = resolve_upload_content_type(image)
    try:
        await asyncio.to_thread(
            storage.validate_image_bytes,
            bytes(image_bytes),
            filename=filename,
            content_type=content_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return bytes(image_bytes)


@router.post("/api/assistant/image/prompt", response_model=AssistantImagePromptResponse)
async def prompt_from_uploaded_image(
    image: UploadFile = File(...),
    target_language: Literal["en", "zh-CN"] = Form("en"),
):
    _resolve_runtime(vision=True)
    image_bytes = await _read_image_prompt_upload(image)
    try:
        preview = await asyncio.to_thread(
            assistant_client.prepare_vision_preview_bytes,
            image_bytes,
        )
    except assistant_client.AssistantError as e:
        raise HTTPException(status_code=400 if e.status == 400 else 502, detail=str(e)) from e

    data, model, duration_ms = await _assistant_json(
        system_prompt=_image_prompt_system_prompt(target_language),
        user_prompt=json.dumps(
            {
                "target_language": target_language,
                "preview": {key: preview[key] for key in ("bytes", "width", "height")},
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        schema={"prompt": "string", "warnings": ["string"]},
        vision=True,
        image=preview,  # type: ignore[arg-type]
        max_tokens=700,
        temperature=0.2,
    )
    prompt = _clamp_text(data.get("prompt"), 4000)
    if not prompt:
        raise HTTPException(status_code=502, detail="AI Assistant returned an empty image prompt")
    return AssistantImagePromptResponse(
        prompt=prompt,
        warnings=_warnings(data.get("warnings")),
        model=model,
        duration_ms=duration_ms,
    )


async def _gallery_entry_and_preview(image_id: str) -> tuple[Any, dict[str, str]]:
    entry = await asyncio.to_thread(storage.get_gallery_entry, image_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Gallery entry not found")
    path = await asyncio.to_thread(storage.safe_image_path, entry.filename)
    if not path or not Path(path).is_file():
        raise HTTPException(status_code=404, detail="Gallery image file not found")
    if not await asyncio.to_thread(storage.is_gallery_filename_referenced, entry.filename):
        raise HTTPException(status_code=404, detail="Gallery image file is not referenced")
    preview = await asyncio.to_thread(assistant_client.prepare_vision_preview, Path(path))
    return entry, preview  # type: ignore[return-value]


async def _assistant_vision_json(
    image_id: str,
    *,
    system_prompt: str,
    schema: dict[str, Any],
    include_stored_prompt: bool = True,
    wait_for_slot: bool = False,
    max_tokens: int = 900,
    temperature: float = 0.2,
) -> tuple[Any, dict[str, str], dict[str, Any], str, int]:
    _api_url, _api_key, _api_path, _model, timeout_seconds = _resolve_runtime(vision=True)
    async with _assistant_request_limit(timeout_seconds, wait_for_slot=wait_for_slot):
        entry, preview = await _gallery_entry_and_preview(image_id)
        api_url, api_key, api_path, model, timeout_seconds = _resolve_runtime(vision=True)
        try:
            data, model_used, duration_ms = await assistant_client.request_assistant_json(
                api_url=api_url,
                api_key=api_key,
                api_path=api_path,
                model=model,
                system_prompt=system_prompt,
                user_prompt=_gallery_vision_user_prompt(
                    entry,
                    preview,
                    include_stored_prompt=include_stored_prompt,
                ),
                schema=schema,
                image=preview,  # type: ignore[arg-type]
                timeout_seconds=timeout_seconds,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except assistant_client.AssistantTimeoutError as e:
            raise HTTPException(status_code=504, detail=str(e)) from e
        except assistant_client.AssistantError as e:
            status_code = 400 if e.status == 400 else 502
            raise HTTPException(status_code=status_code, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return entry, preview, _truncate_assistant_data(data), model_used, duration_ms


def _gallery_vision_user_prompt(entry: Any, preview: dict[str, Any], *, include_stored_prompt: bool = True) -> str:
    payload: dict[str, Any] = {
        "image_id": entry.id,
        "size": entry.size,
        "model": entry.model,
        "api_path": entry.api_path,
        "preview": {key: preview[key] for key in ("bytes", "width", "height")},
    }
    if include_stored_prompt:
        payload["stored_prompt"] = entry.prompt
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _normalize_gallery_analysis(value: Any) -> dict[str, object]:
    analysis = _truncate_assistant_data(value if isinstance(value, dict) else {})
    if not isinstance(analysis, dict):
        return {}
    return {
        "subjects": _string_list(analysis.get("subjects"), max_items=12),
        "style": _clamp_text(analysis.get("style"), 500),
        "composition": _clamp_text(analysis.get("composition"), 800),
        "lighting": _clamp_text(analysis.get("lighting"), 500),
        "colors": _string_list(analysis.get("colors"), max_items=12),
    }


def _prepare_gallery_ai_metadata(
    *,
    description: Any = "",
    prompt: Any = "",
    analysis: Any = None,
) -> tuple[str, str, dict[str, object]]:
    return (
        _clamp_text(description, 2000),
        _clamp_text(prompt, 4000),
        _normalize_gallery_analysis(analysis),
    )


async def _analyze_gallery_image(
    image_id: str,
    *,
    persist: bool,
    wait_for_slot: bool = False,
) -> AssistantGalleryImageResponse:
    _entry, _preview, data, model, duration_ms = await _assistant_vision_json(
        image_id,
        system_prompt=(
            "You analyze a local gallery image. Return a concise description, a useful reverse-engineered "
            "generation prompt, and structured visual metadata."
        ),
        schema={
            "description": "string",
            "prompt": "string",
            "analysis": {
                "subjects": ["string"],
                "style": "string",
                "composition": "string",
                "lighting": "string",
                "colors": ["string"],
            },
            "warnings": ["string"],
        },
        wait_for_slot=wait_for_slot,
        max_tokens=900,
        temperature=0.2,
    )
    description, prompt, analysis = _prepare_gallery_ai_metadata(
        description=data.get("description"),
        prompt=data.get("prompt"),
        analysis=data.get("analysis"),
    )
    if persist:
        await asyncio.to_thread(
            storage.upsert_gallery_ai_metadata,
            image_id=image_id,
            description=description,
            prompt=prompt,
            analysis=analysis,
            model=model,
        )
    return AssistantGalleryImageResponse(
        image_id=image_id,
        description=description,
        prompt=prompt,
        analysis=analysis,
        warnings=_warnings(data.get("warnings")),
        model=model,
        duration_ms=duration_ms,
    )


async def _describe_gallery_image(image_id: str) -> AssistantGalleryImageResponse:
    _entry, _preview, data, model, duration_ms = await _assistant_vision_json(
        image_id,
        system_prompt="You describe a local gallery image concisely. Return only the useful visual description.",
        schema={"description": "string", "warnings": ["string"]},
        include_stored_prompt=False,
        max_tokens=350,
        temperature=0.15,
    )
    return AssistantGalleryImageResponse(
        image_id=image_id,
        description=_clamp_text(data.get("description"), 2000),
        prompt="",
        analysis={},
        warnings=_warnings(data.get("warnings")),
        model=model,
        duration_ms=duration_ms,
    )


async def _prompt_gallery_image(image_id: str) -> AssistantGalleryImageResponse:
    _entry, _preview, data, model, duration_ms = await _assistant_vision_json(
        image_id,
        system_prompt=_image_prompt_system_prompt(),
        schema={"prompt": "string", "warnings": ["string"]},
        include_stored_prompt=False,
        max_tokens=550,
        temperature=0.2,
    )
    return AssistantGalleryImageResponse(
        image_id=image_id,
        description="",
        prompt=_clamp_text(data.get("prompt"), 4000),
        analysis={},
        warnings=_warnings(data.get("warnings")),
        model=model,
        duration_ms=duration_ms,
    )


async def _analyze_gallery_image_with_lease_renewal(
    image_id: str,
    *,
    job_id: str,
    lease_owner: str,
) -> AssistantGalleryImageResponse:
    stop_event = asyncio.Event()
    analyze_task = asyncio.create_task(
        _analyze_gallery_image(image_id, persist=True, wait_for_slot=True)
    )
    renewer = asyncio.create_task(_renew_ai_analyze_job_lease(job_id, lease_owner, stop_event))
    try:
        done, _pending = await asyncio.wait(
            {analyze_task, renewer},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if renewer in done:
            renewer.result()
            raise AIAnalyzeJobLeaseLost(f"Lost AI analysis job lease for {job_id}")
        return analyze_task.result()
    finally:
        stop_event.set()
        for task in (renewer, analyze_task):
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


def _assistant_metadata_response(image_id: str, row: dict[str, Any] | None) -> AssistantGalleryMetadataResponse:
    if not row:
        return AssistantGalleryMetadataResponse(image_id=image_id)
    return AssistantGalleryMetadataResponse(
        image_id=image_id,
        description=str(row.get("description") or ""),
        prompt=str(row.get("prompt") or ""),
        analysis=row.get("analysis") if isinstance(row.get("analysis"), dict) else {},
        model=str(row.get("model") or ""),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


@router.get("/api/assistant/gallery/{image_id}/metadata", response_model=AssistantGalleryMetadataResponse)
async def get_gallery_metadata(image_id: str):
    entry = await asyncio.to_thread(storage.get_gallery_entry, image_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Gallery entry not found")
    row = await asyncio.to_thread(storage.get_gallery_ai_metadata, image_id)
    return _assistant_metadata_response(image_id, row)


@router.post("/api/assistant/gallery/batch/analyze", response_model=AssistantGalleryBatchJobStatus, status_code=202)
async def batch_analyze_gallery(req: AssistantGalleryBatchRequest):
    if req.selection_token:
        filters = await _gallery_filters_from_selection_token(req.selection_token)
        snapshot = await asyncio.to_thread(storage.get_gallery_selection_snapshot, filters)
        requested_count = int(snapshot.get("count") or 0)
        if requested_count <= 0:
            raise HTTPException(status_code=404, detail="Gallery entries not found")
        if requested_count > config.AI_ASSISTANT_BATCH_MAX_IMAGES:
            raise HTTPException(
                status_code=413,
                detail=f"Gallery AI analysis is limited to {config.AI_ASSISTANT_BATCH_MAX_IMAGES} images per batch",
            )
        payload = {
            "filters": filters,
            "checkpoint": None,
            "snapshot": snapshot.get("boundary"),
        }
        job_requested_count = requested_count
        missing_count = 0
    else:
        ids = req.ids or []
        if len(ids) > config.AI_ASSISTANT_BATCH_MAX_IMAGES:
            raise HTTPException(
                status_code=413,
                detail=f"Gallery AI analysis is limited to {config.AI_ASSISTANT_BATCH_MAX_IMAGES} images per batch",
            )
        entries = await asyncio.to_thread(storage.get_gallery_entries_by_ids, ids)
        found_ids = {entry.id for entry in entries}
        requested_ids = [image_id for image_id in ids if image_id in found_ids]
        if not requested_ids:
            raise HTTPException(status_code=404, detail="Gallery entries not found")
        payload = {"ids": requested_ids}
        job_requested_count = len(ids)
        missing_count = max(0, len(ids) - len(requested_ids))
    _resolve_runtime(vision=True)
    job = await asyncio.to_thread(
        storage.reserve_gallery_job_capacity,
        job=_build_ai_analyze_job(
            payload=payload,
            requested_count=job_requested_count,
            missing_count=missing_count,
        ),
        counted_kinds=(AI_ANALYZE_JOB_KIND,),
        max_active=MAX_ACTIVE_AI_ANALYZE_JOBS,
    )
    if not job:
        raise HTTPException(status_code=429, detail="A gallery AI analysis job is already queued or running")
    _kick_ai_analyze_dispatcher()
    return AssistantGalleryBatchJobStatus(**_ai_analyze_payload(job))


@router.get("/api/assistant/gallery/batch/analyze/{job_id}", response_model=AssistantGalleryBatchJobStatus)
async def get_batch_analyze_job(job_id: str):
    job = await asyncio.to_thread(storage.get_gallery_job, AI_ANALYZE_JOB_KIND, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Gallery AI analysis job not found")
    return AssistantGalleryBatchJobStatus(**_ai_analyze_payload(job))


@router.post("/api/assistant/gallery/{image_id}/describe", response_model=AssistantGalleryImageResponse)
async def describe_gallery_image(image_id: str):
    return await _describe_gallery_image(image_id)


@router.post("/api/assistant/gallery/{image_id}/prompt", response_model=AssistantGalleryImageResponse)
async def prompt_gallery_image(image_id: str):
    return await _prompt_gallery_image(image_id)


@router.post("/api/assistant/gallery/{image_id}/analyze", response_model=AssistantGalleryImageResponse)
async def analyze_gallery_image(image_id: str):
    return await _analyze_gallery_image(image_id, persist=True)


def _ai_analyze_payload(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "stage": job.get("stage"),
        "message": job.get("message"),
        "progress": job.get("progress") or 0,
        "requested_count": job.get("requested_count") or 0,
        "processed_count": job.get("processed_count") or 0,
        "analyzed_count": job.get("exported_count") or 0,
        "missing_count": job.get("missing_count") or 0,
        "failed_count": job.get("failed_count") or 0,
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "error": job.get("error"),
    }


def _build_ai_analyze_job(
    *,
    payload: dict[str, Any],
    requested_count: int,
    missing_count: int = 0,
) -> dict[str, Any]:
    job_id = os.urandom(16).hex()
    now = utc_now()
    return {
        "job_id": job_id,
        "kind": AI_ANALYZE_JOB_KIND,
        "status": "queued",
        "stage": "queued",
        "message": "Queued gallery AI analysis",
        "progress": 0,
        "created_at": now,
        "updated_at": now,
        "requested_count": requested_count,
        "processed_count": 0,
        "exported_count": 0,
        "missing_count": missing_count,
        "failed_count": 0,
        "payload": payload,
    }


def _parse_gallery_token_timestamp(value: Any) -> datetime | None:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    try:
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


async def _gallery_filters_from_selection_token(selection_token: str | None) -> dict[str, Any]:
    token = str(selection_token or "").strip()
    if not token:
        raise HTTPException(status_code=422, detail="selection_token is required")
    job = await asyncio.to_thread(storage.get_gallery_job, GALLERY_SELECTION_TOKEN_KIND, token)
    if not job:
        raise HTTPException(status_code=404, detail="Gallery selection token not found")
    payload = job.get("payload") or {}
    expires_at = _parse_gallery_token_timestamp(payload.get("expires_at"))
    if not expires_at or expires_at <= datetime.now(timezone.utc):
        await asyncio.to_thread(storage.delete_gallery_job, GALLERY_SELECTION_TOKEN_KIND, token)
        raise HTTPException(status_code=404, detail="Gallery selection token expired")
    filters = payload.get("filters")
    if not isinstance(filters, dict):
        await asyncio.to_thread(storage.delete_gallery_job, GALLERY_SELECTION_TOKEN_KIND, token)
        raise HTTPException(status_code=404, detail="Gallery selection token not found")
    return filters


def _gallery_job_lease_expires_at() -> str:
    from datetime import timedelta

    return (datetime.now(timezone.utc) + timedelta(seconds=AI_ANALYZE_JOB_LEASE_SECONDS)).isoformat()


async def _renew_ai_analyze_job_lease(job_id: str, lease_owner: str, stop_event: asyncio.Event) -> None:
    while True:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=AI_ANALYZE_JOB_LEASE_RENEW_SECONDS)
            return
        except asyncio.TimeoutError:
            try:
                renewed = await asyncio.to_thread(
                    storage.renew_gallery_job_lease,
                    job_id=job_id,
                    lease_owner=lease_owner,
                    lease_expires_at=_gallery_job_lease_expires_at(),
                    now=utc_now(),
                )
            except Exception as e:
                raise AIAnalyzeJobLeaseLost(f"Failed to renew AI analysis job lease for {job_id}") from e
            if not renewed:
                raise AIAnalyzeJobLeaseLost(f"Lost AI analysis job lease for {job_id}")


def _ai_analyze_ids_from_payload(payload: dict[str, Any]) -> list[str]:
    return [str(image_id) for image_id in payload.get("ids") or [] if str(image_id)]


def _ai_analyze_checkpoint_payload(payload: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "checkpoint": {
            "sort_seq": row["sort_seq"],
            "id": row["id"],
        },
    }


def _ai_analyze_snapshot_boundary(payload: dict[str, Any]) -> tuple[int | None, str | None]:
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    raw_sort_seq = snapshot.get("sort_seq") if isinstance(snapshot, dict) else None
    raw_id = snapshot.get("id") if isinstance(snapshot, dict) else None
    snapshot_id = str(raw_id or "").strip()
    if raw_sort_seq is None or not snapshot_id:
        return None, None
    try:
        return int(raw_sort_seq), snapshot_id
    except (TypeError, ValueError):
        return None, None


async def _next_ai_analyze_id_batch(
    payload: dict[str, Any],
    *,
    processed_count: int = 0,
    requested_count: int = 0,
) -> tuple[list[dict[str, Any]], bool]:
    remaining = max(0, int(requested_count or 0) - int(processed_count or 0))
    if requested_count and remaining <= 0:
        return [], True

    ids = _ai_analyze_ids_from_payload(payload)
    if ids:
        start = min(max(0, int(processed_count or 0)), len(ids))
        end = min(len(ids), start + remaining) if remaining else len(ids)
        return [{"id": image_id} for image_id in ids[start:end]], True

    filters = payload.get("filters")
    if not isinstance(filters, dict):
        return [], True
    checkpoint = payload.get("checkpoint") if isinstance(payload.get("checkpoint"), dict) else {}
    raw_sort_seq = checkpoint.get("sort_seq") if isinstance(checkpoint, dict) else None
    raw_id = checkpoint.get("id") if isinstance(checkpoint, dict) else None
    try:
        after_sort_seq = int(raw_sort_seq) if raw_sort_seq is not None else None
    except (TypeError, ValueError):
        after_sort_seq = None
    after_id = str(raw_id or "").strip() or None
    before_or_at_sort_seq, before_or_at_id = _ai_analyze_snapshot_boundary(payload)
    batch_limit = min(AI_ANALYZE_JOB_BATCH_SIZE, remaining) if remaining else AI_ANALYZE_JOB_BATCH_SIZE
    rows = await asyncio.to_thread(
        storage.get_gallery_id_batch,
        filters,
        after_sort_seq=after_sort_seq,
        after_id=after_id,
        before_or_at_sort_seq=before_or_at_sort_seq,
        before_or_at_id=before_or_at_id,
        limit=batch_limit,
    )
    if not rows:
        return [], True
    return rows, len(rows) < batch_limit


async def _update_ai_analyze_job(
    job_id: str,
    lease_owner: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    updated = await asyncio.to_thread(
        storage.update_gallery_job,
        job_id,
        updates,
        lease_owner=lease_owner,
    )
    if not updated:
        raise AIAnalyzeJobLeaseLost(f"Lost AI analysis job lease for {job_id}")
    return updated


async def _update_ai_analyze_progress(
    job_id: str,
    lease_owner: str,
    updates: dict[str, Any],
) -> None:
    updated = await asyncio.to_thread(
        storage.update_gallery_job_progress,
        job_id,
        updates,
        lease_owner=lease_owner,
    )
    if not updated:
        raise AIAnalyzeJobLeaseLost(f"Lost AI analysis job lease for {job_id}")


async def _wait_for_ai_analyze_backpressure(job_id: str, lease_owner: str) -> None:
    if lease_owner:
        renewed = await asyncio.to_thread(
            storage.renew_gallery_job_lease,
            job_id=job_id,
            lease_owner=lease_owner,
            lease_expires_at=_gallery_job_lease_expires_at(),
            now=utc_now(),
        )
        if not renewed:
            raise AIAnalyzeJobLeaseLost(f"Lost AI analysis job lease for {job_id}")
    await asyncio.sleep(AI_ASSISTANT_SLOT_RETRY_SECONDS)


async def _run_ai_analyze_job(job: dict[str, Any]) -> None:
    job_id = str(job["job_id"])
    lease_owner = str(job.get("lease_owner") or "")
    payload = job.get("payload") or {}
    requested_count = int(job.get("requested_count") or len(_ai_analyze_ids_from_payload(payload)))
    processed = int(job.get("processed_count") or 0)
    analyzed = int(job.get("exported_count") or 0)
    missing = int(job.get("missing_count") or 0)
    failed = int(job.get("failed_count") or 0)
    await _update_ai_analyze_job(
        job_id,
        lease_owner,
        {
            "status": "running",
            "stage": "analyzing",
            "message": "Analyzing gallery images",
            "progress": min(95, round((processed / max(requested_count, 1)) * 95)),
            "lease_expires_at": _gallery_job_lease_expires_at(),
            "error": None,
        },
    )
    while True:
        batch, exhausted = await _next_ai_analyze_id_batch(
            payload,
            processed_count=processed,
            requested_count=requested_count,
        )
        if not batch:
            break
        has_stored_ids = bool(_ai_analyze_ids_from_payload(payload))
        for row in batch:
            image_id = str(row["id"])
            while True:
                try:
                    await _analyze_gallery_image_with_lease_renewal(
                        image_id,
                        job_id=job_id,
                        lease_owner=lease_owner,
                    )
                    analyzed += 1
                    break
                except AIAnalyzeJobLeaseLost:
                    logger.warning("Stopping gallery AI analysis job %s after losing its lease", job_id)
                    return
                except HTTPException as e:
                    if e.status_code == 429:
                        try:
                            await _wait_for_ai_analyze_backpressure(job_id, lease_owner)
                        except AIAnalyzeJobLeaseLost:
                            logger.warning("Stopping gallery AI analysis job %s after losing its lease", job_id)
                            return
                        continue
                    if e.status_code == 404:
                        missing += 1
                    else:
                        failed += 1
                    break
                except Exception:
                    logger.warning("Gallery AI analysis failed for %s", image_id, exc_info=True)
                    failed += 1
                    break
            processed += 1
            if not has_stored_ids:
                payload = _ai_analyze_checkpoint_payload(payload, row)
            progress = min(95, round((processed / max(requested_count, 1)) * 95))
            try:
                await _update_ai_analyze_progress(
                    job_id,
                    lease_owner,
                    {
                        "stage": "analyzing",
                        "message": "Analyzing gallery images",
                        "progress": progress,
                        "processed_count": processed,
                        "exported_count": analyzed,
                        "missing_count": missing,
                        "failed_count": failed,
                        "lease_expires_at": _gallery_job_lease_expires_at(),
                        "payload": payload,
                    },
                )
            except AIAnalyzeJobLeaseLost:
                logger.warning("Stopping gallery AI analysis job %s after losing its lease", job_id)
                return
        if exhausted or has_stored_ids:
            break
    if not _ai_analyze_ids_from_payload(payload):
        missing += max(0, requested_count - processed)
    skipped = missing + failed
    status = "success" if skipped == 0 else "error"
    try:
        await _update_ai_analyze_job(
            job_id,
            lease_owner,
            {
                "status": status,
                "stage": "completed" if status == "success" else "error",
                "message": "Gallery AI analysis complete" if status == "success" else "Gallery AI analysis finished with skipped images",
                "progress": 100,
                "processed_count": processed,
                "exported_count": analyzed,
                "missing_count": missing,
                "failed_count": failed,
                "completed_at": utc_now(),
                "lease_owner": None,
                "lease_expires_at": None,
                "error": None if status == "success" else f"{missing} image(s) missing, {failed} image(s) failed",
            },
        )
    except AIAnalyzeJobLeaseLost:
        logger.warning("Could not complete gallery AI analysis job %s after losing its lease", job_id)


async def run_ai_analyze_dispatcher(worker_id: str) -> None:
    while True:
        try:
            job = await asyncio.to_thread(
                storage.claim_next_gallery_job,
                kind=AI_ANALYZE_JOB_KIND,
                worker_id=worker_id,
                lease_expires_at=_gallery_job_lease_expires_at(),
                now=utc_now(),
                running_limit=MAX_ACTIVE_AI_ANALYZE_JOBS,
                counted_kinds=(AI_ANALYZE_JOB_KIND,),
            )
            if not job:
                await asyncio.sleep(1.0)
                continue
            await _run_ai_analyze_job(job)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Gallery AI analysis dispatcher error", exc_info=True)
            await asyncio.sleep(1.0)


def _kick_ai_analyze_dispatcher() -> None:
    from ..app_state import app

    task = getattr(app.state, "gallery_ai_analyze_dispatcher_task", None)
    if task and not task.done():
        return
    worker_id = getattr(app.state, "worker_id", f"{os.getpid()}-{id(app)}")
    app.state.gallery_ai_analyze_dispatcher_task = asyncio.create_task(
        run_ai_analyze_dispatcher(worker_id)
    )
