import asyncio
import json
import logging
import os
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from .. import presets
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

AI_ANALYZE_JOB_KIND = "ai_analyze"
GALLERY_SELECTION_TOKEN_KIND = "batch_selection"
MAX_ACTIVE_AI_ANALYZE_JOBS = 1
AI_ANALYZE_JOB_LEASE_SECONDS = 300
AI_ANALYZE_JOB_LEASE_RENEW_SECONDS = 60


class AIAnalyzeJobLeaseLost(RuntimeError):
    pass


def _warnings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:10]


def _string_list(value: Any, *, max_items: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:max_items]


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
        return await assistant_client.request_assistant_json(
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
        "rationale": str(data.get("rationale") or "").strip(),
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
    result = {key: job[key] for key in allowed if key in job and job[key] is not None}
    if include_prompt and job.get("prompt"):
        result["prompt"] = str(job["prompt"])[:1000]
    return result


@router.post("/api/assistant/jobs/{job_id}/diagnose", response_model=AssistantJobDiagnoseResponse)
async def diagnose_job(job_id: str, req: AssistantJobDiagnoseRequest | None = None):
    job = await asyncio.to_thread(storage.get_generate_job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    safe_job = _safe_job_snapshot(job, include_prompt=(req.include_prompt if req else True))
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
        summary=str(data.get("summary") or "").strip(),
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


async def _analyze_gallery_image(image_id: str, *, persist: bool) -> AssistantGalleryImageResponse:
    entry, preview = await _gallery_entry_and_preview(image_id)
    data, model, duration_ms = await _assistant_json(
        system_prompt=(
            "You analyze a local gallery image. Return a concise description, a useful reverse-engineered "
            "generation prompt, and structured visual metadata."
        ),
        user_prompt=json.dumps(
            {
                "image_id": entry.id,
                "stored_prompt": entry.prompt,
                "size": entry.size,
                "model": entry.model,
                "api_path": entry.api_path,
                "preview": {key: preview[key] for key in ("bytes", "width", "height")},
            },
            ensure_ascii=False,
            sort_keys=True,
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
        vision=True,
        image=preview,  # type: ignore[arg-type]
        max_tokens=900,
        temperature=0.2,
    )
    description = str(data.get("description") or "").strip()
    prompt = str(data.get("prompt") or "").strip()
    analysis = data.get("analysis") if isinstance(data.get("analysis"), dict) else {}
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


async def _analyze_gallery_image_with_lease_renewal(
    image_id: str,
    *,
    job_id: str,
    lease_owner: str,
) -> AssistantGalleryImageResponse:
    stop_event = asyncio.Event()
    analyze_task = asyncio.create_task(_analyze_gallery_image(image_id, persist=True))
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
        ids = await asyncio.to_thread(storage.get_gallery_ids, filters)
    else:
        ids = req.ids or []
    entries = await asyncio.to_thread(storage.get_gallery_entries_by_ids, ids)
    found_ids = {entry.id for entry in entries}
    requested_ids = [image_id for image_id in ids if image_id in found_ids]
    if not requested_ids:
        raise HTTPException(status_code=404, detail="Gallery entries not found")
    _resolve_runtime(vision=True)
    job = await asyncio.to_thread(
        storage.reserve_gallery_job_capacity,
        job=_build_ai_analyze_job(requested_ids, len(ids)),
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
    result = await _analyze_gallery_image(image_id, persist=False)
    result.prompt = ""
    result.analysis = {}
    return result


@router.post("/api/assistant/gallery/{image_id}/prompt", response_model=AssistantGalleryImageResponse)
async def prompt_gallery_image(image_id: str):
    result = await _analyze_gallery_image(image_id, persist=False)
    result.description = ""
    result.analysis = {}
    return result


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


def _build_ai_analyze_job(ids: list[str], requested_count: int) -> dict[str, Any]:
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
        "missing_count": 0,
        "failed_count": 0,
        "payload": {"ids": ids},
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


async def _run_ai_analyze_job(job: dict[str, Any]) -> None:
    job_id = str(job["job_id"])
    lease_owner = str(job.get("lease_owner") or "")
    payload = job.get("payload") or {}
    ids = [str(image_id) for image_id in payload.get("ids") or [] if str(image_id)]
    requested_count = int(job.get("requested_count") or len(ids))
    analyzed = 0
    missing = max(0, requested_count - len(ids))
    failed = 0
    await asyncio.to_thread(
        storage.update_gallery_job,
        job_id,
        {
            "status": "running",
            "stage": "analyzing",
            "message": "Analyzing gallery images",
            "progress": 0,
            "lease_expires_at": _gallery_job_lease_expires_at(),
            "error": None,
        },
    )
    for index, image_id in enumerate(ids, start=1):
        try:
            await _analyze_gallery_image_with_lease_renewal(
                image_id,
                job_id=job_id,
                lease_owner=lease_owner,
            )
            analyzed += 1
        except AIAnalyzeJobLeaseLost:
            logger.warning("Stopping gallery AI analysis job %s after losing its lease", job_id)
            return
        except HTTPException as e:
            if e.status_code == 404:
                missing += 1
            else:
                failed += 1
        except Exception:
            logger.warning("Gallery AI analysis failed for %s", image_id, exc_info=True)
            failed += 1
        progress = round((index / max(len(ids), 1)) * 95)
        await asyncio.to_thread(
            storage.update_gallery_job_progress,
            job_id,
            {
                "stage": "analyzing",
                "message": "Analyzing gallery images",
                "progress": progress,
                "processed_count": index,
                "exported_count": analyzed,
                "missing_count": missing,
                "failed_count": failed,
                "lease_expires_at": _gallery_job_lease_expires_at(),
            },
        )
    skipped = missing + failed
    status = "success" if skipped == 0 else "error"
    await asyncio.to_thread(
        storage.update_gallery_job,
        job_id,
        {
            "status": status,
            "stage": "completed" if status == "success" else "error",
            "message": "Gallery AI analysis complete" if status == "success" else "Gallery AI analysis finished with skipped images",
            "progress": 100,
            "processed_count": len(ids),
            "exported_count": analyzed,
            "missing_count": missing,
            "failed_count": failed,
            "completed_at": utc_now(),
            "lease_owner": None,
            "lease_expires_at": None,
            "error": None if status == "success" else f"{missing} image(s) missing, {failed} image(s) failed",
        },
    )


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
