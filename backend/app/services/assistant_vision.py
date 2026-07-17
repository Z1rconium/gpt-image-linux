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
    image: UploadFile = File(...),
    target_language: Literal["en", "zh-CN"] = Form("en"),
):
    runtime = await _resolve_runtime_async(vision=True)
    image_bytes = await _read_image_prompt_upload(image)
    try:
        preview = await asyncio.to_thread(
            assistant_client.prepare_vision_preview_bytes,
            image_bytes,
            filename=image.filename or "image",
            content_type=resolve_upload_content_type(image),
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


async def _gallery_entry_and_preview(image_id: str) -> tuple[Any, dict[str, Any]]:
    entry = await asyncio.to_thread(get_gallery_entry, image_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Gallery entry not found")
    path = await asyncio.to_thread(safe_image_path, entry.filename)
    if not path or not Path(path).is_file():
        raise HTTPException(status_code=404, detail="Gallery image file not found")
    if not await asyncio.to_thread(is_gallery_filename_referenced, entry.filename):
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


