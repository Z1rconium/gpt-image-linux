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
from .assistant_vision import *

async def _analyze_gallery_image_with_lease_renewal(
    image_id: str,
    *,
    job_id: str,
    lease_owner: str,
    target_language: Literal["en", "zh-CN"] | None = None,
    runtime: AssistantRuntime | None = None,
) -> AssistantGalleryImageResponse:
    stop_event = asyncio.Event()
    analyze_task = asyncio.create_task(
        _analyze_gallery_image(
            image_id,
            persist=True,
            wait_for_slot=True,
            target_language=target_language,
            runtime=runtime,
        )
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


async def batch_analyze_gallery(req: AssistantGalleryBatchRequest):
    if req.selection_token:
        filters = await _gallery_filters_from_selection_token(req.selection_token)
        snapshot = await asyncio.to_thread(get_gallery_selection_snapshot, filters)
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
            "target_language": req.target_language,
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
        entries = await asyncio.to_thread(get_gallery_entries_by_ids, ids)
        found_ids = {entry.id for entry in entries}
        requested_ids = [image_id for image_id in ids if image_id in found_ids]
        if not requested_ids:
            raise HTTPException(status_code=404, detail="Gallery entries not found")
        payload = {"ids": requested_ids, "target_language": req.target_language}
        job_requested_count = len(ids)
        missing_count = max(0, len(ids) - len(requested_ids))
    await _resolve_runtime_async(vision=True)
    job = await asyncio.to_thread(
        reserve_gallery_job_capacity,
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


async def get_batch_analyze_job(job_id: str):
    job = await asyncio.to_thread(get_gallery_job, AI_ANALYZE_JOB_KIND, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Gallery AI analysis job not found")
    return AssistantGalleryBatchJobStatus(**_ai_analyze_payload(job))


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
    job = await asyncio.to_thread(get_gallery_job, GALLERY_SELECTION_TOKEN_KIND, token)
    if not job:
        raise HTTPException(status_code=404, detail="Gallery selection token not found")
    payload = job.get("payload") or {}
    expires_at = _parse_gallery_token_timestamp(payload.get("expires_at"))
    if not expires_at or expires_at <= datetime.now(timezone.utc):
        await asyncio.to_thread(delete_gallery_job, GALLERY_SELECTION_TOKEN_KIND, token)
        raise HTTPException(status_code=404, detail="Gallery selection token expired")
    filters = payload.get("filters")
    if not isinstance(filters, dict):
        await asyncio.to_thread(delete_gallery_job, GALLERY_SELECTION_TOKEN_KIND, token)
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
                    renew_gallery_job_lease,
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
        get_gallery_id_batch,
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
        update_gallery_job,
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
        update_gallery_job_progress,
        job_id,
        updates,
        lease_owner=lease_owner,
    )
    if not updated:
        raise AIAnalyzeJobLeaseLost(f"Lost AI analysis job lease for {job_id}")


async def _wait_for_ai_analyze_backpressure(job_id: str, lease_owner: str) -> None:
    if lease_owner:
        renewed = await asyncio.to_thread(
            renew_gallery_job_lease,
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
    target_language = payload.get("target_language")
    if target_language not in {"en", "zh-CN"}:
        target_language = "en"
    _batch_assistant_runtime.set(AssistantRuntimeHolder())
    _batch_target_language.set(target_language)
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
                claim_next_gallery_job,
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
    from ..api.app_state import app

    task = getattr(app.state, "gallery_ai_analyze_dispatcher_task", None)
    if task and not task.done():
        return
    worker_id = getattr(app.state, "worker_id", f"{os.getpid()}-{id(app)}")
    app.state.gallery_ai_analyze_dispatcher_task = asyncio.create_task(
        run_ai_analyze_dispatcher(worker_id)
    )

