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
from .assistant_runtime import *

async def assistant_health(req: AIAssistantSettingsRequest | None = Body(default=None)):
    settings = presets.effective_ai_assistant_settings(_assistant_settings(req))
    model = str(settings.get("model") or "").strip() or "gpt-4o-mini"
    try:
        runtime = await _resolve_runtime_async(settings=settings)
        model = runtime.model
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
            api_url=runtime.api_url,
            api_key=runtime.api_key,
            api_path=runtime.api_path,
            model=model,
            timeout_seconds=runtime.timeout_seconds,
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


async def diagnose_job(job_id: str, req: AssistantJobDiagnoseRequest | None = None):
    job = await asyncio.to_thread(get_generate_job, job_id)
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



