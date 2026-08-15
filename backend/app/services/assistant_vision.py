import asyncio
import base64
import contextvars
import json
import logging
import math
import os
import re
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Body, HTTPException, Request, UploadFile
from starlette.datastructures import UploadFile as StarletteUploadFile

from ..api import presets
from ..api.app_state import app
from ..api.uploads import (
    is_image_upload,
    parse_limited_multipart,
    resolve_upload_content_type,
)
from ..core import settings as config
from ..core import validators as ssrf
from ..core.utils import utc_now
from ..integrations import assistant_client
from ..integrations.upstream.errors import UpstreamApiError
from ..integrations.upstream.generation import call_image_generation_preview_api
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
from ..repositories.image_files import detect_image_format, safe_image_path
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
    AssistantImagePromptOptimizeResponse,
    AssistantTemporaryImage,
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
from ..schemas.generation import GenerateRequest
from ..schemas.settings import AIAssistantSettingsRequest

logger = logging.getLogger(__name__)
from .assistant_runtime import (
    ASSISTANT_IMAGE_UPLOAD_CHUNK_BYTES,
    AssistantRuntime,
    _assistant_json,
    _assistant_request_limit,
    _batch_assistant_runtime,
    _batch_target_language,
    _clamp_text,
    _image_prompt_system_prompt,
    _resolve_runtime_async,
    _string_list,
    _truncate_assistant_data,
    _warnings,
)
from .blocking import run_image_operation

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

    return bytes(image_bytes)


async def prompt_from_uploaded_image(
    request: Request,
):
    form = await parse_limited_multipart(
        request,
        max_files=1,
        max_fields=1,
        allowed_file_fields={"image"},
    )
    image = form.get("image")
    if not isinstance(image, StarletteUploadFile):
        raise HTTPException(status_code=422, detail="Upload image is required")
    target_language = str(form.get("target_language") or "en")
    if target_language not in {"en", "zh-CN"}:
        raise HTTPException(status_code=422, detail="target_language must be 'en' or 'zh-CN'")
    runtime = await _resolve_runtime_async(vision=True)
    try:
        image_bytes = await _read_image_prompt_upload(image)
    finally:
        await image.close()
    try:
        preview = await run_image_operation(
            assistant_client.prepare_vision_preview_bytes,
            image_bytes,
            filename=image.filename or "image",
            content_type=resolve_upload_content_type(image),
            metric_name="prepare_assistant_upload_preview",
        )
    except assistant_client.AssistantError as e:
        raise HTTPException(status_code=400 if e.status == 400 else 502, detail=str(e)) from e
    finally:
        del image_bytes

    data, model, duration_ms = await _assistant_json(
        system_prompt=_image_prompt_system_prompt(target_language),
        user_prompt=json.dumps(
            {
                "target_language": target_language,
                "preview": {key: preview[key] for key in ("bytes", "width", "height", "source_has_alpha")},
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        schema={"prompt": "string", "warnings": ["string"]},
        vision=True,
        image=preview,
        max_tokens=700,
        temperature=0.2,
        runtime=runtime,
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


def calculate_prompt_preview_size(width: int, height: int) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("Target image dimensions must be positive")
    aspect = max(width / height, height / width)
    if aspect > 3:
        raise ValueError("Target image aspect ratio must not exceed 3:1 for prompt optimization")

    target_pixels = 800_000
    ratio = width / height
    ideal_width = math.sqrt(target_pixels * ratio)
    ideal_height = math.sqrt(target_pixels / ratio)
    candidates: list[tuple[float, int, int]] = []
    base_width = max(16, round(ideal_width / 16) * 16)
    base_height = max(16, round(ideal_height / 16) * 16)
    for candidate_width in range(max(16, base_width - 32), base_width + 33, 16):
        for candidate_height in range(max(16, base_height - 32), base_height + 33, 16):
            candidate_aspect = max(
                candidate_width / candidate_height,
                candidate_height / candidate_width,
            )
            if candidate_aspect > 3:
                continue
            ratio_error = abs(math.log((candidate_width / candidate_height) / ratio))
            area_error = abs(candidate_width * candidate_height - target_pixels) / target_pixels
            candidates.append((ratio_error * 4 + area_error, candidate_width, candidate_height))
    if not candidates:
        raise ValueError("Could not calculate a supported preview size")
    _, preview_width, preview_height = min(candidates)
    return preview_width, preview_height


def _prompt_optimization_system_prompt(target_language: Literal["en", "zh-CN"]) -> str:
    language = "Simplified Chinese" if target_language == "zh-CN" else "English"
    return (
        "You compare two images for iterative image-prompt refinement. The first image is the target; "
        "the second is a trial generated from the current prompt. Identify only visible differences "
        "that can be addressed in an image generation prompt. Preserve prompt details that already "
        "match the target. Do not invent unseen facts or discuss hidden generation settings. Return a "
        f"concise comparison summary and the complete refined prompt in {language}."
    )


def _prompt_preview_generation_config() -> tuple[str, str, str, str | None, str | None]:
    active_preset = presets.get_active_preset()
    api_path = str(active_preset.get("api_path") or "").strip()
    if api_path != "/v1/images/generations":
        raise HTTPException(
            status_code=400,
            detail="Prompt optimization preview requires the active preset to use /v1/images/generations",
        )
    api_url = str(active_preset.get("api_url") or "").strip()
    if not api_url:
        raise HTTPException(status_code=400, detail="Active image generation preset API URL is not configured")
    api_key = presets.get_effective_preset_api_key(active_preset)
    model = str(active_preset.get("default_model") or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="Active image generation preset model is not configured")
    response_format = str(active_preset.get("default_response_format") or "").strip() or None
    return api_url, api_key, model, response_format, presets.get_upstream_socks5_proxy() or None


def _generated_image_mime_type(image_bytes: bytes) -> str:
    image_format = detect_image_format(image_bytes)
    mime_types = {
        "avif": "image/avif",
        "bmp": "image/bmp",
        "gif": "image/gif",
        "heif": "image/heif",
        "ico": "image/x-icon",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "tiff": "image/tiff",
        "webp": "image/webp",
    }
    return mime_types.get(str(image_format or ""), "application/octet-stream")


async def optimize_uploaded_image_prompt(
    request: Request,
):
    form = await parse_limited_multipart(
        request,
        max_files=1,
        max_fields=2,
        allowed_file_fields={"image"},
    )
    image = form.get("image")
    if not isinstance(image, StarletteUploadFile):
        raise HTTPException(status_code=422, detail="Upload image is required")
    prompt = str(form.get("prompt") or "")
    if len(prompt) > 4000:
        raise HTTPException(status_code=422, detail="prompt must contain at most 4000 characters")
    target_language = str(form.get("target_language") or "en")
    if target_language not in {"en", "zh-CN"}:
        raise HTTPException(status_code=422, detail="target_language must be 'en' or 'zh-CN'")
    normalized_prompt = prompt.strip()
    if not normalized_prompt:
        raise HTTPException(status_code=422, detail="prompt must not be empty")

    runtime = await _resolve_runtime_async(vision=True)
    api_url, api_key, model, response_format, socks5_proxy = await asyncio.to_thread(
        _prompt_preview_generation_config
    )
    try:
        image_bytes = await _read_image_prompt_upload(image)
    finally:
        await image.close()
    try:
        target_preview = await run_image_operation(
            assistant_client.prepare_vision_preview_bytes,
            image_bytes,
            filename=image.filename or "image",
            content_type=resolve_upload_content_type(image),
            metric_name="prepare_assistant_target_preview",
        )
    except assistant_client.AssistantError as e:
        raise HTTPException(status_code=400 if e.status == 400 else 502, detail=str(e)) from e
    finally:
        del image_bytes

    try:
        preview_width, preview_height = calculate_prompt_preview_size(
            int(target_preview.get("source_width") or target_preview["width"]),
            int(target_preview.get("source_height") or target_preview["height"]),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    generation_payload = GenerateRequest(
        prompt=normalized_prompt,
        size=f"{preview_width}x{preview_height}",
        model=model,
        n=1,
        quality="low",
        output_format="png",
        response_format=response_format,
    )

    generation_started = time.monotonic()
    try:
        generated_bytes = await call_image_generation_preview_api(
            api_url,
            api_key,
            generation_payload,
            socks5_proxy=socks5_proxy,
        )
    except (asyncio.TimeoutError, TimeoutError) as e:
        raise HTTPException(status_code=504, detail="Prompt optimization preview generation timed out") from e
    except UpstreamApiError as e:
        raise HTTPException(status_code=502, detail=f"Prompt optimization preview generation failed: {e}") from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Prompt optimization preview configuration is invalid: {e}") from e
    except Exception as e:
        logger.warning("Prompt optimization preview generation failed", exc_info=True)
        raise HTTPException(status_code=502, detail="Prompt optimization preview generation failed") from e
    generation_duration_ms = int((time.monotonic() - generation_started) * 1000)

    try:
        generated_preview = await run_image_operation(
            assistant_client.prepare_vision_preview_bytes,
            generated_bytes,
            filename="assistant-preview",
            content_type="",
            metric_name="prepare_assistant_generated_preview",
        )
    except assistant_client.AssistantError as e:
        raise HTTPException(status_code=502, detail=f"Generated preview image is invalid: {e}") from e

    data, vision_model, comparison_duration_ms = await _assistant_json(
        system_prompt=_prompt_optimization_system_prompt(target_language),
        user_prompt=json.dumps(
            {
                "target_language": target_language,
                "current_prompt": normalized_prompt,
                "target_dimensions": [
                    target_preview.get("source_width", target_preview["width"]),
                    target_preview.get("source_height", target_preview["height"]),
                ],
                "trial_dimensions": [
                    generated_preview.get("source_width", generated_preview["width"]),
                    generated_preview.get("source_height", generated_preview["height"]),
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        schema={
            "comparison_summary": "string",
            "prompt": "string",
            "warnings": ["string"],
        },
        vision=True,
        images=[
            {**target_preview, "label": "Target image (first image)"},
            {**generated_preview, "label": "Trial generated image (second image)"},
        ],
        max_tokens=900,
        temperature=0.2,
        runtime=runtime,
    )
    optimized_prompt = _clamp_text(data.get("prompt"), 4000)
    if not optimized_prompt:
        raise HTTPException(status_code=502, detail="AI Assistant returned an empty optimized prompt")
    comparison_summary = _clamp_text(data.get("comparison_summary"), 2000)
    if not comparison_summary:
        raise HTTPException(status_code=502, detail="AI Assistant returned an empty comparison summary")

    generated_width = int(generated_preview.get("source_width") or generated_preview["width"])
    generated_height = int(generated_preview.get("source_height") or generated_preview["height"])
    return AssistantImagePromptOptimizeResponse(
        prompt=optimized_prompt,
        comparison_summary=comparison_summary,
        warnings=_warnings(data.get("warnings")),
        model=vision_model,
        duration_ms=comparison_duration_ms,
        temporary_image=AssistantTemporaryImage(
            b64=base64.b64encode(generated_bytes).decode("ascii"),
            mime_type=_generated_image_mime_type(generated_bytes),
            width=generated_width,
            height=generated_height,
            model=model,
            duration_ms=generation_duration_ms,
        ),
    )


async def _gallery_entry_and_preview(image_id: str) -> tuple[Any, dict[str, Any]]:
    entry = await asyncio.to_thread(get_gallery_entry, image_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Gallery entry not found")
    path = await asyncio.to_thread(safe_image_path, entry.filename)
    if not path or not Path(path).is_file():
        raise HTTPException(status_code=404, detail="Gallery image file not found")
    if not await asyncio.to_thread(is_gallery_filename_referenced, entry.filename):
        raise HTTPException(status_code=404, detail="Gallery image file is not referenced")
    preview = await run_image_operation(
        assistant_client.prepare_vision_preview,
        Path(path),
        metric_name="prepare_assistant_gallery_preview",
    )
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
    runtime: AssistantRuntime | None = None,
) -> tuple[Any, dict[str, Any], dict[str, Any], str, int]:
    if runtime is None:
        runtime_holder = _batch_assistant_runtime.get()
        if runtime_holder is not None:
            runtime_holder.runtime = runtime_holder.runtime or await _resolve_runtime_async(vision=True)
            runtime = runtime_holder.runtime
        else:
            runtime = await _resolve_runtime_async(vision=True)
    async with _assistant_request_limit(runtime.timeout_seconds, wait_for_slot=wait_for_slot):
        try:
            entry, preview = await _gallery_entry_and_preview(image_id)
            data, model_used, duration_ms = await assistant_client.request_assistant_json(
                api_url=runtime.api_url,
                api_key=runtime.api_key,
                api_path=runtime.api_path,
                model=runtime.model,
                system_prompt=system_prompt,
                user_prompt=_gallery_vision_user_prompt(
                    entry,
                    preview,
                    include_stored_prompt=include_stored_prompt,
                ),
                schema=schema,
                image=preview,
                timeout_seconds=runtime.timeout_seconds,
                max_tokens=max_tokens,
                temperature=temperature,
                prevalidated_endpoint=runtime.endpoint,
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
        "preview": {key: preview[key] for key in ("bytes", "width", "height", "source_has_alpha")},
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
    target_language: Literal["en", "zh-CN"] | None = None,
    runtime: AssistantRuntime | None = None,
) -> AssistantGalleryImageResponse:
    target_language = target_language or _batch_target_language.get()
    _entry, _preview, data, model, duration_ms = await _assistant_vision_json(
        image_id,
        system_prompt=(
            "You analyze a local gallery image. Return a concise description, a useful reverse-engineered "
            "generation prompt, and structured visual metadata. "
            + _image_prompt_system_prompt(target_language)
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
        runtime=runtime,
    )
    description, prompt, analysis = _prepare_gallery_ai_metadata(
        description=data.get("description"),
        prompt=data.get("prompt"),
        analysis=data.get("analysis"),
    )
    if not prompt:
        raise HTTPException(status_code=502, detail="AI Assistant returned an empty image prompt")
    if persist:
        await asyncio.to_thread(
            upsert_gallery_ai_metadata,
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


async def _prompt_gallery_image(
    image_id: str,
    *,
    target_language: Literal["en", "zh-CN"] | None = None,
    runtime: AssistantRuntime | None = None,
) -> AssistantGalleryImageResponse:
    _entry, _preview, data, model, duration_ms = await _assistant_vision_json(
        image_id,
        system_prompt=_image_prompt_system_prompt(target_language),
        schema={"prompt": "string", "warnings": ["string"]},
        include_stored_prompt=False,
        max_tokens=550,
        temperature=0.2,
        runtime=runtime,
    )
    prompt = _clamp_text(data.get("prompt"), 4000)
    if not prompt:
        raise HTTPException(status_code=502, detail="AI Assistant returned an empty image prompt")
    return AssistantGalleryImageResponse(
        image_id=image_id,
        description="",
        prompt=prompt,
        analysis={},
        warnings=_warnings(data.get("warnings")),
        model=model,
        duration_ms=duration_ms,
    )


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


async def get_gallery_metadata(image_id: str):
    entry = await asyncio.to_thread(get_gallery_entry, image_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Gallery entry not found")
    row = await asyncio.to_thread(get_gallery_ai_metadata, image_id)
    return _assistant_metadata_response(image_id, row)


async def describe_gallery_image(image_id: str):
    return await _describe_gallery_image(image_id)


async def prompt_gallery_image(
    image_id: str,
    target_language: Literal["en", "zh-CN"] = "en",
):
    runtime = await _resolve_runtime_async(vision=True)
    return await _prompt_gallery_image(image_id, target_language=target_language, runtime=runtime)


async def analyze_gallery_image(
    image_id: str,
    target_language: Literal["en", "zh-CN"] = "en",
):
    runtime = await _resolve_runtime_async(vision=True)
    return await _analyze_gallery_image(image_id, persist=True, target_language=target_language, runtime=runtime)


__all__ = [name for name in globals() if not name.startswith("__")]
