import asyncio
import hashlib
import inspect
import logging
import mimetypes
import os
import time
import uuid
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Body, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from ..api.app_state import app
from .gallery_archive_export import (
    iter_gallery_zip_chunks,
    prepare_gallery_zip_chunks,
    write_gallery_zip_file,
)
from .gallery_archive_import import (
    count_import_gallery_entries,
    iter_import_gallery_entries,
    stream_upload_to_tempfile,
)
from .gallery_archive_shared import (
    GalleryZipFileResult,
    import_archive_max_bytes,
)
from .job_events import publish_queue, serialize_sse_event
from .job_queue import kick_thumbnail_dispatcher
from ..api.sse_limiter import sse_limiter
from ..core import security as auth
from ..core import settings as config
from ..core.observability import metrics
from ..core.utils import utc_now
from ..integrations.r2 import config as r2_config
from ..integrations.r2 import sync as r2_algorithm
from ..repositories.coordination import (
    acquire_background_lease,
    claim_next_gallery_job,
    cleanup_expired_gallery_jobs,
    cleanup_stale_gallery_jobs,
    count_active_gallery_jobs,
    create_gallery_job,
    delete_gallery_job,
    get_gallery_job,
    get_gallery_jobs_updated_at_edges,
    list_gallery_job_ids_with_files,
    release_background_lease,
    release_import_upload_reservation,
    reserve_gallery_job_capacity,
    update_gallery_job,
    update_gallery_job_progress,
)
from ..repositories.gallery.mutations import (
    cleanup_orphan_gallery_files,
    delete_all_gallery_images,
    delete_gallery_image,
    delete_gallery_images,
    delete_gallery_images_by_filters,
    import_gallery_entries,
    invalidate_thumbnail_cache,
    is_gallery_filename_referenced,
    update_gallery_entries_favorite,
    update_gallery_entries_favorite_by_filters,
    update_gallery_entry,
)
from ..repositories.gallery.queries import (
    get_gallery_count,
    get_gallery_entries_by_ids,
    get_gallery_entry,
    get_gallery_ids,
    get_gallery_page,
    iter_gallery_export_rows,
)
from ..repositories.gallery.sync_state import (
    count_gallery_r2_sync_rows,
    iter_gallery_r2_sync_rows,
    mark_gallery_r2_sync_state,
)
from ..repositories.image_files import (
    THUMBNAIL_CONTENT_TYPE,
    safe_image_path,
    safe_thumbnail_path,
)
from ..repositories.settings import load_r2_backup_settings
from ..repositories.thumbnail_jobs import (
    THUMBNAIL_JOB_LEASE_SECONDS,
    claim_next_thumbnail_job,
    complete_thumbnail_job,
    ensure_thumbnail_for_image,
    fail_thumbnail_job,
    generate_thumbnail_for_image,
)
from ..schemas.common import MessageResponse
from ..schemas.gallery import (
    GalleryBatchFavoriteRequest,
    GalleryBatchRequest,
    GalleryBatchResponse,
    GalleryEntry,
    GalleryExportJobStatus,
    GalleryExportRequest,
    GalleryFavoriteRequest,
    GalleryImportJobStatus,
    GalleryResponse,
    GallerySelectionTokenRequest,
    GallerySelectionTokenResponse,
    GallerySyncRequest,
    GallerySyncJobStatus,
)
from .gallery_common import *

async def _gallery_zip_response(
    entries,
    filename_prefix: str,
    skipped: list[dict] | None = None,
    extra_headers: dict[str, str] | None = None,
    reserve_export_slot: bool = False,
    direct_export_job: dict | None = None,
    requested_count: int = 0,
    prepare_before_response: bool = False,
) -> StreamingResponse:
    cleanup_direct_job = False
    if direct_export_job:
        active_direct_job = direct_export_job
    elif reserve_export_slot:
        active_direct_job = await _reserve_gallery_export_direct_slot(
            filename_prefix=filename_prefix,
            requested_count=requested_count,
            stage="streaming",
            message="Streaming direct gallery ZIP download",
        )
        cleanup_direct_job = True
    else:
        active_direct_job = None

    direct_job_id = str(active_direct_job.get("job_id")) if active_direct_job else None

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = str(active_direct_job.get("filename") or "") if active_direct_job else ""
    if not filename:
        filename = f"{filename_prefix}-{timestamp}.zip"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Encoding": "identity",
        "Cache-Control": PRIVATE_GALLERY_CACHE_CONTROL,
        "X-Content-Type-Options": "nosniff",
    }
    if direct_job_id:
        headers["X-Gallery-Export-Job-Id"] = direct_job_id
    if extra_headers:
        headers.update(extra_headers)

    def progress(updates: dict):
        if not direct_job_id or not throttler:
            return
        force = updates.get("stage") in {"preparing", "streaming"} and (
            updates.get("progress") in {0, 20, 100}
            or updates.get("status") in GALLERY_EXPORT_TERMINAL_STATUSES
        )
        throttler.emit(
            {
                **updates,
                "filename": filename,
                "download_url": f"/api/download-all?export_job_id={quote(direct_job_id)}",
                "lease_expires_at": _direct_export_slot_expires_at(),
            },
            force=force,
        )

    def mark_direct_job(updates: dict) -> None:
        if direct_job_id:
            update_gallery_job(direct_job_id, updates)

    throttler = GalleryProgressThrottler(
        lambda updates: update_gallery_job_progress(direct_job_id, updates)
    ) if direct_job_id else None

    prepared_chunks = None
    prepared_result: GalleryZipFileResult | None = None
    if prepare_before_response:
        try:
            if direct_job_id:
                mark_direct_job(
                    {
                        "status": "running",
                        "stage": "preparing",
                        "message": "Preparing gallery ZIP entries",
                        "progress": 0,
                        "filename": filename,
                        "download_url": f"/api/download-all?export_job_id={quote(direct_job_id)}",
                        "requested_count": requested_count,
                        "started_at": utc_now(),
                        "lease_expires_at": _direct_export_slot_expires_at(),
                        "error": None,
                    }
                )
            prepared_chunks, prepared_result = prepare_gallery_zip_chunks(
                entries,
                skipped=skipped,
                requested_count=requested_count,
                progress=progress if direct_job_id else None,
            )
            headers.setdefault("X-Gallery-Requested-Count", str(prepared_result.requested_count))
            headers.setdefault("X-Gallery-Exported-Count", str(prepared_result.exported_count))
            headers.setdefault("X-Gallery-Missing-Count", str(prepared_result.missing_count))
        except Exception as e:
            if direct_job_id and not cleanup_direct_job:
                mark_direct_job(
                    {
                        "status": "error",
                        "stage": "error",
                        "message": "Failed to prepare ZIP archive",
                        "error": str(e),
                        "completed_at": utc_now(),
                        "lease_owner": None,
                        "lease_expires_at": None,
                    }
                )
            if cleanup_direct_job and direct_job_id:
                _release_gallery_export_direct_slot(direct_job_id)
            raise

    def zip_chunks():
        try:
            if direct_job_id and prepared_result is None:
                mark_direct_job(
                    {
                        "status": "running",
                        "stage": "preparing",
                        "message": "Preparing gallery ZIP entries",
                        "progress": 0,
                        "filename": filename,
                        "download_url": f"/api/download-all?export_job_id={quote(direct_job_id)}",
                        "requested_count": requested_count,
                        "started_at": utc_now(),
                        "lease_expires_at": _direct_export_slot_expires_at(),
                        "error": None,
                    }
                )
            if prepared_result is not None and prepared_chunks is not None:
                yield from prepared_chunks
                result = prepared_result
            else:
                result = yield from iter_gallery_zip_chunks(
                    entries,
                    skipped=skipped,
                    requested_count=requested_count,
                    progress=progress if direct_job_id else None,
                )
            if direct_job_id:
                mark_direct_job(
                    {
                        "status": "success",
                        "stage": "ready",
                        "message": "ZIP archive streamed",
                        "progress": 100,
                        "processed_count": result.requested_count,
                        "requested_count": result.requested_count,
                        "exported_count": result.exported_count,
                        "missing_count": result.missing_count,
                        "bytes_total": result.bytes_total,
                        "bytes_written": result.bytes_total,
                        "completed_at": utc_now(),
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "error": None,
                    }
                )
        except (GeneratorExit, asyncio.CancelledError):
            if direct_job_id and not cleanup_direct_job:
                mark_direct_job(
                    {
                        "status": "error",
                        "stage": "error",
                        "message": "Direct ZIP download interrupted",
                        "error": "Client disconnected before ZIP streaming completed",
                        "completed_at": utc_now(),
                        "lease_owner": None,
                        "lease_expires_at": None,
                    }
                )
            raise
        except Exception as e:
            if direct_job_id and not cleanup_direct_job:
                mark_direct_job(
                    {
                        "status": "error",
                        "stage": "error",
                        "message": "Failed to stream ZIP archive",
                        "error": str(e),
                        "completed_at": utc_now(),
                        "lease_owner": None,
                        "lease_expires_at": None,
                    }
                )
            raise
        finally:
            if cleanup_direct_job and direct_job_id:
                _release_gallery_export_direct_slot(direct_job_id)

    return StreamingResponse(
        zip_chunks(),
        media_type="application/zip",
        headers=headers,
    )


def _missing_gallery_ids(requested_ids: list[str], entries: list[GalleryEntry]) -> list[str]:
    found_ids = {entry.id for entry in entries}
    return [image_id for image_id in requested_ids if image_id not in found_ids]



def _gallery_export_lock() -> asyncio.Lock:
    if not hasattr(app.state, "gallery_export_lock"):
        app.state.gallery_export_lock = asyncio.Lock()
    return app.state.gallery_export_lock


def _create_gallery_export_direct_slot(
    *,
    filename_prefix: str = "gpt-images",
    requested_count: int = 0,
    status: str = "running",
    stage: str = "queued",
    message: str = "Waiting for direct gallery ZIP download",
    payload: dict | None = None,
) -> dict:
    job_id = f"direct-{os.urandom(16).hex()}"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{filename_prefix}-{timestamp}.zip"
    now = utc_now()
    return {
        "job_id": job_id,
        "kind": "export_direct",
        "status": status,
        "stage": stage,
        "message": message,
        "progress": 0,
        "filename": filename,
        "download_url": f"/api/download-all?export_job_id={quote(job_id)}",
        "requested_count": requested_count,
        "processed_count": 0,
        "exported_count": 0,
        "missing_count": 0,
        "bytes_total": 0,
        "bytes_written": 0,
        "created_at": now,
        "started_at": now,
        "updated_at": now,
        "lease_expires_at": _direct_export_slot_expires_at(),
        "payload": payload or {},
    }


def _release_gallery_export_direct_slot(job_id: str | None) -> None:
    if job_id:
        delete_gallery_job("export_direct", job_id)


async def _reserve_gallery_export_direct_slot(
    *,
    filename_prefix: str = "gpt-images",
    requested_count: int = 0,
    stage: str = "queued",
    message: str = "Waiting for direct gallery ZIP download",
    payload: dict | None = None,
) -> dict:
    async with _gallery_export_lock():
        slot = await asyncio.to_thread(
            reserve_gallery_job_capacity,
            job=_create_gallery_export_direct_slot(
                filename_prefix=filename_prefix,
                requested_count=requested_count,
                stage=stage,
                message=message,
                payload=payload,
            ),
            counted_kinds=("export", "export_direct"),
            max_active=MAX_ACTIVE_EXPORT_JOBS,
        )
        if not slot:
            active_count = await asyncio.to_thread(count_active_gallery_jobs, "export")
            active_count += await asyncio.to_thread(
                count_active_gallery_jobs,
                "export_direct",
            )
            raise HTTPException(
                status_code=429,
                detail=f"Too many active export jobs ({active_count}). "
                "Please wait for existing exports to complete.",
            )
        return slot


def _gallery_export_payload(job: dict) -> dict:
    keys = (
        "job_id",
        "status",
        "stage",
        "message",
        "progress",
        "filename",
        "download_url",
        "requested_count",
        "processed_count",
        "exported_count",
        "missing_count",
        "bytes_total",
        "bytes_written",
        "created_at",
        "updated_at",
        "error",
    )
    return {key: job.get(key) for key in keys}


def _gallery_sync_payload(job: dict) -> dict:
    payload = job.get("payload") or {}
    keys = (
        "job_id",
        "status",
        "stage",
        "message",
        "progress",
        "created_at",
        "updated_at",
        "error",
        "total_count",
        "compared_count",
        "uploaded_count",
        "pending_upload_count",
        "skipped_existing_count",
        "missing_local_count",
        "failed_count",
        "bytes_total",
        "bytes_uploaded",
    )
    data = {key: job.get(key) for key in keys}
    data["dry_run"] = bool(payload.get("dry_run"))
    data["checkpoint_filename"] = str(payload.get("start_after_filename") or "") or None
    return data


def _gallery_import_payload(job: dict) -> dict:
    payload = {
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "stage": job.get("stage"),
        "message": job.get("message"),
        "progress": job.get("progress"),
        "requested_count": job.get("requested_count") or 0,
        "processed_count": job.get("processed_count") or 0,
        "imported_count": job.get("exported_count") or 0,
        "skipped_count": job.get("missing_count") or 0,
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "error": job.get("error"),
    }
    return payload


def _gallery_job_event_name(kind: str) -> str:
    if kind == "sync":
        return "sync"
    if kind == "import":
        return "import"
    if kind == "ai_analyze":
        return "analysis"
    return "export"


def _gallery_ai_analyze_payload(job: dict) -> dict:
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


def _gallery_job_payload(kind: str, job: dict) -> dict:
    if kind == "sync":
        return _gallery_sync_payload(job)
    if kind == "import":
        return _gallery_import_payload(job)
    if kind == "ai_analyze":
        return _gallery_ai_analyze_payload(job)
    return _gallery_export_payload(job)


def _get_gallery_job_subscribers(kind: str) -> dict[str, set[asyncio.Queue]]:
    all_subscribers = getattr(app.state, "gallery_job_subscribers", None)
    if not isinstance(all_subscribers, dict):
        all_subscribers = {}
        app.state.gallery_job_subscribers = all_subscribers
    subscribers = all_subscribers.get(kind)
    if not isinstance(subscribers, dict):
        subscribers = {}
        all_subscribers[kind] = subscribers
    return subscribers


def _get_gallery_job_sse_poller_tasks() -> dict[str, asyncio.Task]:
    tasks = getattr(app.state, "gallery_job_sse_poller_tasks", None)
    if not isinstance(tasks, dict):
        tasks = {}
        app.state.gallery_job_sse_poller_tasks = tasks
    return tasks


def _publish_gallery_job_sse(job: dict) -> None:
    kind = str(job.get("kind") or "")
    job_id = str(job.get("job_id") or "")
    if not kind or not job_id:
        return
    subscribers = _get_gallery_job_subscribers(kind).get(job_id, set())
    if not subscribers:
        return
    event = {
        "event": _gallery_job_event_name(kind),
        "data": _gallery_job_payload(kind, job),
    }
    for queue in list(subscribers):
        publish_queue(queue, event)


def _start_gallery_job_sse_poller(kind: str) -> None:
    tasks = _get_gallery_job_sse_poller_tasks()
    task = tasks.get(kind)
    if task and not task.done():
        return
    tasks[kind] = asyncio.create_task(_poll_gallery_job_sse(kind))


async def _poll_gallery_job_sse(kind: str) -> None:
    last_edges: dict[str, str] = {}
    try:
        while True:
            subscribers_by_job = {
                job_id: list(subscribers)
                for job_id, subscribers in _get_gallery_job_subscribers(kind).items()
                if subscribers
            }
            if not subscribers_by_job:
                break

            current_edges = await asyncio.to_thread(
                get_gallery_jobs_updated_at_edges,
                kind,
                set(subscribers_by_job),
            )
            metrics.increment(f"sse.poll_queries.gallery_{kind}")
            for job_id in set(subscribers_by_job) - set(current_edges):
                for queue in subscribers_by_job[job_id]:
                    publish_queue(queue, {"event": "_missing", "data": None})
                last_edges.pop(job_id, None)

            changed_job_ids = [
                job_id
                for job_id, updated_at in current_edges.items()
                if last_edges.get(job_id) != updated_at
            ]
            for job_id in changed_job_ids:
                job = await asyncio.to_thread(get_gallery_job, kind, job_id)
                event = (
                    {
                        "event": _gallery_job_event_name(kind),
                        "data": _gallery_job_payload(kind, job),
                    }
                    if job
                    else {"event": "_missing", "data": None}
                )
                for queue in subscribers_by_job.get(job_id, []):
                    publish_queue(queue, event)
            last_edges = current_edges

            await asyncio.sleep(GALLERY_JOB_DISPATCH_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Gallery %s SSE poller stopped after error", kind, exc_info=True)
    finally:
        tasks = _get_gallery_job_sse_poller_tasks()
        if tasks.get(kind) is asyncio.current_task():
            tasks.pop(kind, None)


async def stream_gallery_job(
    *,
    kind: str,
    job_id: str,
    request: Request,
    event_name: str,
    terminal_statuses: set[str],
    payload_builder,
    not_found_detail: str,
):
    job = await asyncio.to_thread(get_gallery_job, kind, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=not_found_detail)

    client_ip = auth.get_client_ip(request)
    sse_lease = await sse_limiter.acquire(client_ip)
    if not sse_lease:
        raise HTTPException(status_code=429, detail="Too many SSE connections")

    async def event_stream():
        start = time.monotonic()
        last_refresh_at = start
        last_updated_at: str | None = None
        last_sent = 0.0
        queue: asyncio.Queue = asyncio.Queue(maxsize=GALLERY_JOB_SSE_QUEUE_MAXSIZE)
        subscribers = _get_gallery_job_subscribers(kind).setdefault(job_id, set())
        subscribers.add(queue)
        _start_gallery_job_sse_poller(kind)
        try:
            current_job = await asyncio.to_thread(get_gallery_job, kind, job_id)
            if not current_job:
                return
            payload = payload_builder(current_job)
            last_updated_at = str(payload.get("updated_at") or "")
            last_sent = time.monotonic()
            yield serialize_sse_event(event_name, payload)
            if payload.get("status") in terminal_statuses:
                return

            while True:
                if await request.is_disconnected():
                    break
                now = time.monotonic()
                refreshed_at = await sse_limiter.refresh_if_needed(
                    sse_lease,
                    last_refresh_at,
                )
                if refreshed_at is None:
                    break
                last_refresh_at = refreshed_at
                if now - start > config.SSE_CONNECTION_TTL_SECONDS:
                    break
                if now - last_sent >= 15:
                    last_sent = now
                    yield ": keep-alive\n\n"
                    continue
                wait_seconds = min(
                    GALLERY_JOB_SSE_IDLE_CHECK_SECONDS,
                    max(0.1, 15 - (now - last_sent)),
                    max(0.1, config.SSE_CONNECTION_TTL_SECONDS - (now - start)),
                )
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=wait_seconds)
                except asyncio.TimeoutError:
                    continue
                if event.get("event") == "_missing":
                    break
                if event.get("event") != event_name:
                    continue
                payload = event.get("data")
                if not isinstance(payload, dict):
                    break
                updated_at = str(payload.get("updated_at") or "")
                if updated_at == last_updated_at:
                    continue
                last_updated_at = updated_at
                last_sent = time.monotonic()
                yield serialize_sse_event(event_name, payload)
                if payload.get("status") in terminal_statuses:
                    break
        finally:
            subscribers.discard(queue)
            if not subscribers:
                _get_gallery_job_subscribers(kind).pop(job_id, None)
            await sse_limiter.release(sse_lease)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": PRIVATE_GALLERY_CACHE_CONTROL,
            "X-Accel-Buffering": "no",
        },
    )


def _gallery_job_lease_expires_at() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=GALLERY_JOB_LEASE_SECONDS)).isoformat()


def _direct_export_slot_expires_at() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=DIRECT_EXPORT_SLOT_LEASE_SECONDS)).isoformat()


def _background_task_lease_expires_at() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=BACKGROUND_TASK_LEASE_SECONDS)).isoformat()


def _next_background_task_error_backoff(current: float) -> float:
    if current <= 0:
        return BACKGROUND_TASK_ERROR_BACKOFF_INITIAL_SECONDS
    return min(current * 2, BACKGROUND_TASK_ERROR_BACKOFF_MAX_SECONDS)


async def _sleep_while_renewing_background_lease(
    *,
    name: str,
    owner: str,
    delay_seconds: float,
) -> bool:
    deadline = time.monotonic() + max(0.0, float(delay_seconds or 0))
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        await asyncio.sleep(min(remaining, max(1.0, BACKGROUND_TASK_LEASE_SECONDS / 2)))
        renewed = await asyncio.to_thread(
            acquire_background_lease,
            name=name,
            owner=owner,
            lease_expires_at=_background_task_lease_expires_at(),
        )
        if not renewed:
            return False


def _claim_counted_gallery_kinds(kind: str) -> tuple[str, ...]:
    if kind == "export":
        return ("export", "export_direct")
    return (kind,)


def _progress_item_count(updates: dict) -> int:
    for key in ("processed_count", "compared_count", "exported_count", "uploaded_count"):
        if key not in updates:
            continue
        try:
            return int(updates.get(key) or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _publish_gallery_job(job_id: str, updates: dict) -> dict | None:
    job = update_gallery_job(job_id, updates)
    if job:
        _publish_gallery_job_sse(job)
    return job


def _publish_gallery_job_progress(job_id: str, updates: dict) -> bool:
    return update_gallery_job_progress(job_id, updates)


def _call_gallery_r2_sync(
    r2_settings: dict,
    entries,
    *,
    total_count: int,
    progress_cb,
    state_recorder,
    full_reconcile: bool,
    dry_run: bool,
    concurrency: int,
):
    kwargs = {
        "total_count": total_count,
        "progress_cb": progress_cb,
        "state_recorder": state_recorder,
        "full_reconcile": full_reconcile,
        "dry_run": dry_run,
        "concurrency": concurrency,
    }
    try:
        signature = inspect.signature(r2_algorithm.sync_gallery_to_r2)
    except (TypeError, ValueError):
        supported_kwargs = kwargs
    else:
        accepts_kwargs = any(
            param.kind == inspect.Parameter.VAR_KEYWORD
            for param in signature.parameters.values()
        )
        supported_kwargs = (
            kwargs
            if accepts_kwargs
            else {key: value for key, value in kwargs.items() if key in signature.parameters}
        )
    return r2_algorithm.sync_gallery_to_r2(r2_settings, entries, **supported_kwargs)


def _build_export_job_entries(job: dict) -> tuple[Iterable[GalleryEntry | dict], int, list[dict]]:
    payload = job.get("payload") or {}
    filters = payload.get("filters")
    if isinstance(filters, dict):
        requested_count = get_gallery_count(filters)
        return iter_gallery_export_rows(filters), requested_count, []
    ids = payload.get("ids")
    if ids:
        entries = get_gallery_entries_by_ids(ids)
        missing_ids = _missing_gallery_ids(ids, entries)
        skipped = [
            {
                "id": image_id,
                "reason": "gallery_entry_missing",
            }
            for image_id in missing_ids
        ]
        return entries, len(ids), skipped
    requested_count = get_gallery_count()
    return iter_gallery_export_rows(), requested_count, []


def _build_gallery_export_job(
    filename_prefix: str,
    requested_count: int,
    payload: dict,
) -> dict:
    job_id = payload.get("job_id") or os.urandom(16).hex()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{filename_prefix}-{timestamp}.zip"
    path = Path(config.DATA_DIR) / "exports" / f"{job_id}.zip"
    now = utc_now()
    return {
        "job_id": job_id,
        "kind": "export",
        "status": "queued",
        "stage": "queued",
        "message": "Queued gallery ZIP export",
        "progress": 0,
        "filename": filename,
        "download_url": None,
        "requested_count": requested_count,
        "processed_count": 0,
        "exported_count": 0,
        "missing_count": 0,
        "bytes_total": 0,
        "bytes_written": 0,
        "created_at": now,
        "updated_at": now,
        "error": None,
        "path": str(path),
        "payload": payload,
    }


def _create_gallery_sync_job(total_count: int, payload: dict | None = None) -> dict:
    job_id = os.urandom(16).hex()
    now = utc_now()
    return create_gallery_job(
        job_id=job_id,
        kind="sync",
        status="queued",
        stage="queued",
        message="Queued R2 gallery sync",
        progress=0,
        created_at=now,
        updated_at=now,
        error=None,
        total_count=total_count,
        compared_count=0,
        uploaded_count=0,
        pending_upload_count=0,
        skipped_existing_count=0,
        missing_local_count=0,
        failed_count=0,
        bytes_total=0,
        bytes_uploaded=0,
        payload=payload or {},
    )


def _create_gallery_import_job(
    zip_path: Path,
    total_count: int,
    payload: dict | None = None,
) -> dict:
    job_id = os.urandom(16).hex()
    now = utc_now()
    return create_gallery_job(
        job_id=job_id,
        kind="import",
        status="queued",
        stage="queued",
        message="Queued gallery ZIP import",
        progress=0,
        created_at=now,
        updated_at=now,
        error=None,
        path=str(zip_path),
        requested_count=total_count,
        processed_count=0,
        exported_count=0,
        missing_count=0,
        payload=payload or {},
    )


async def _create_reserved_gallery_export_job(
    filename_prefix: str,
    requested_count: int,
    payload: dict,
) -> dict:
    async with _gallery_export_lock():
        job = await asyncio.to_thread(
            reserve_gallery_job_capacity,
            job=_build_gallery_export_job(filename_prefix, requested_count, payload),
            counted_kinds=("export", "export_direct"),
            max_active=MAX_ACTIVE_EXPORT_JOBS,
        )
        if not job:
            active_count = await asyncio.to_thread(count_active_gallery_jobs, "export")
            active_count += await asyncio.to_thread(
                count_active_gallery_jobs,
                "export_direct",
            )
            raise HTTPException(
                status_code=429,
                detail=f"Too many active export jobs ({active_count}). "
                "Please wait for existing exports to complete.",
            )
        return job


async def _create_reserved_gallery_sync_job(
    total_count: int,
    payload: dict | None = None,
) -> dict:
    active_count = await asyncio.to_thread(count_active_gallery_jobs, "sync")
    if active_count >= MAX_ACTIVE_SYNC_JOBS:
        raise HTTPException(
            status_code=429,
            detail="A gallery R2 sync job is already queued or running.",
        )
    return await asyncio.to_thread(_create_gallery_sync_job, total_count, payload)


async def _create_reserved_gallery_import_job(
    zip_path: Path,
    total_count: int,
    payload: dict | None = None,
) -> dict:
    job = await asyncio.to_thread(
        reserve_gallery_job_capacity,
        job=_create_gallery_import_job(zip_path, total_count, payload),
        counted_kinds=("import",),
        max_active=MAX_ACTIVE_IMPORT_JOBS,
    )
    if not job:
        raise HTTPException(
            status_code=429,
            detail="A gallery import job is already queued or running.",
        )
    return job


async def _run_gallery_export_job(job: dict) -> None:
    job_id = job["job_id"]
    export_path = _resolve_trusted_gallery_job_path(job.get("path"), kind="export")
    loop = asyncio.get_running_loop()

    def publish_progress(updates: dict):
        updates = {**updates, "lease_expires_at": _gallery_job_lease_expires_at()}
        loop.call_soon_threadsafe(_publish_gallery_job_progress, job_id, updates)

    throttler = GalleryProgressThrottler(publish_progress)

    def progress(updates: dict):
        force = updates.get("stage") in {"preparing", "packing"} and (
            updates.get("progress") in {0, 20, 100}
            or updates.get("status") in GALLERY_EXPORT_TERMINAL_STATUSES
        )
        throttler.emit(updates, force=force)

    try:
        if not export_path:
            raise ValueError("Export archive path is invalid")
        entries, requested_count, skipped = await asyncio.to_thread(_build_export_job_entries, job)
        _publish_gallery_job(
            job_id,
            {
                "status": "running",
                "stage": "preparing",
                "message": "Preparing gallery ZIP entries",
                "progress": 0,
                "requested_count": requested_count,
                "lease_expires_at": _gallery_job_lease_expires_at(),
            },
        )
        result: GalleryZipFileResult = await asyncio.to_thread(
            write_gallery_zip_file,
            entries,
            export_path,
            requested_count=requested_count,
            skipped=skipped,
            progress=progress,
        )
        _publish_gallery_job(
            job_id,
            {
                "status": "success",
                "stage": "ready",
                "message": "ZIP archive ready",
                "progress": 100,
                "processed_count": result.requested_count,
                "requested_count": result.requested_count,
                "exported_count": result.exported_count,
                "missing_count": result.missing_count,
                "bytes_total": result.bytes_total,
                "bytes_written": result.bytes_total,
                "download_url": f"/api/gallery/export-jobs/{job_id}/download",
                "completed_at": utc_now(),
                "lease_owner": None,
                "lease_expires_at": None,
                "error": None,
            },
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("Failed to build gallery export ZIP job %s", job_id, exc_info=True)
        _unlink_trusted_gallery_job_path(job.get("path"), kind="export", job_id=job_id)
        _publish_gallery_job(
            job_id,
            {
                "status": "error",
                "stage": "error",
                "message": "Failed to build ZIP archive",
                "error": str(e),
                "completed_at": utc_now(),
                "lease_owner": None,
                "lease_expires_at": None,
            },
        )


async def _run_gallery_sync_job(job: dict) -> None:
    job_id = job["job_id"]
    payload = job.get("payload") or {}
    full_reconcile = bool(payload.get("full_reconcile"))
    dry_run = bool(payload.get("dry_run"))
    start_after_filename = str(payload.get("start_after_filename") or "")
    loop = asyncio.get_running_loop()

    def publish_progress(updates: dict):
        updates = {**updates, "lease_expires_at": _gallery_job_lease_expires_at()}
        loop.call_soon_threadsafe(_publish_gallery_job_progress, job_id, updates)

    throttler = GalleryProgressThrottler(publish_progress)

    def progress(updates: dict):
        last_filename = str(updates.pop("last_filename", "") or "")
        if full_reconcile and last_filename:
            payload["start_after_filename"] = last_filename
            updates["payload"] = payload
        force = updates.get("stage") in {"preparing", "listing_remote", "completed"}
        throttler.emit(updates, force=force)

    try:
        r2_settings = await asyncio.to_thread(load_r2_backup_settings)
        effective = await asyncio.to_thread(
            r2_config.resolve_r2_backup_settings,
            r2_settings,
            require_enabled=True,
        )
        total_count = await asyncio.to_thread(
            count_gallery_r2_sync_rows,
            key_prefix=effective.key_prefix,
            full_reconcile=full_reconcile,
            start_after_filename=start_after_filename,
        )
        _publish_gallery_job(
            job_id,
            {
                "status": "running",
                "stage": "preparing",
                "message": "Preparing R2 gallery sync dry run" if dry_run else "Preparing R2 gallery sync",
                "progress": 0,
                "total_count": total_count,
                "lease_expires_at": _gallery_job_lease_expires_at(),
            },
        )
        result = await asyncio.to_thread(
            _call_gallery_r2_sync,
            r2_settings,
            iter_gallery_r2_sync_rows(
                key_prefix=effective.key_prefix,
                full_reconcile=full_reconcile,
                start_after_filename=start_after_filename,
            ),
            total_count=total_count,
            progress_cb=progress,
            state_recorder=None if dry_run else mark_gallery_r2_sync_state,
            full_reconcile=full_reconcile,
            dry_run=dry_run,
            concurrency=config.R2_SYNC_CONCURRENCY,
        )
        payload.pop("start_after_filename", None)
        _publish_gallery_job(
            job_id,
            {
                "status": "success",
                "stage": "completed",
                "message": "R2 gallery sync dry run complete" if dry_run else "R2 gallery sync complete",
                "progress": 100,
                "error": None,
                "completed_at": utc_now(),
                "lease_owner": None,
                "lease_expires_at": None,
                "payload": payload,
                **result.to_updates(),
            },
        )
    except asyncio.CancelledError:
        raise
    except r2_config.R2SyncError as e:
        logger.warning("Gallery R2 sync job %s finished with upload errors", job_id)
        _publish_gallery_job(
            job_id,
            {
                "status": "error",
                "stage": "error",
                "message": "R2 gallery sync failed",
                "progress": 100,
                "error": str(e),
                "completed_at": utc_now(),
                "lease_owner": None,
                "lease_expires_at": None,
                **e.result.to_updates(),
            },
        )
    except Exception as e:
        logger.warning("Gallery R2 sync job %s failed", job_id, exc_info=True)
        _publish_gallery_job(
            job_id,
            {
                "status": "error",
                "stage": "error",
                "message": "R2 gallery sync failed",
                "error": str(e),
                "completed_at": utc_now(),
                "lease_owner": None,
                "lease_expires_at": None,
            },
        )


async def _run_gallery_import_job(job: dict) -> None:
    job_id = job["job_id"]
    zip_path = _resolve_trusted_gallery_job_path(job.get("path"), kind="import")
    payload = job.get("payload") or {}
    reservation_id = str(payload.get("reservation_id") or "")
    requested_count = int(job.get("requested_count") or 0)
    loop = asyncio.get_running_loop()
    last_counts = {
        "processed_count": 0,
        "exported_count": 0,
        "missing_count": 0,
    }

    def publish_progress(updates: dict):
        updates = {**updates, "lease_expires_at": _gallery_job_lease_expires_at()}
        loop.call_soon_threadsafe(_publish_gallery_job_progress, job_id, updates)

    throttler = GalleryProgressThrottler(publish_progress)

    def progress(updates: dict):
        for key in last_counts:
            if key in updates:
                last_counts[key] = int(updates.get(key) or 0)
        denominator = max(requested_count, last_counts["processed_count"], 1)
        progress_value = min(
            90,
            5 + round((min(last_counts["processed_count"], denominator) / denominator) * 85),
        )
        force = updates.get("stage") in {"validating", "committing"} and (
            updates.get("processed_count") in {0, requested_count}
            or updates.get("status") in GALLERY_IMPORT_TERMINAL_STATUSES
        )
        throttler.emit(
            {
                **updates,
                "progress": progress_value,
                "requested_count": denominator,
            },
            force=force,
        )

    try:
        if not zip_path:
            raise ValueError("Import archive path is invalid")
        if not zip_path.exists():
            raise FileNotFoundError("Import archive file is missing")

        _publish_gallery_job(
            job_id,
            {
                "status": "running",
                "stage": "validating",
                "message": "Validating import archive entries",
                "progress": 0,
                "requested_count": requested_count,
                "lease_expires_at": _gallery_job_lease_expires_at(),
                "error": None,
            },
        )

        def run_import() -> int:
            return import_gallery_entries(
                iter_import_gallery_entries(zip_path, progress=progress)
            )

        imported_count = await asyncio.to_thread(run_import)
        if imported_count == 0:
            raise ValueError("No importable images found")

        processed_count = max(last_counts["processed_count"], requested_count)
        skipped_count = max(last_counts["missing_count"], processed_count - imported_count)
        _publish_gallery_job(
            job_id,
            {
                "status": "success",
                "stage": "completed",
                "message": "Gallery import complete",
                "progress": 100,
                "requested_count": max(requested_count, processed_count),
                "processed_count": processed_count,
                "exported_count": imported_count,
                "missing_count": skipped_count,
                "completed_at": utc_now(),
                "lease_owner": None,
                "lease_expires_at": None,
                "error": None,
            },
        )
        kick_thumbnail_dispatcher()
    except asyncio.CancelledError:
        raise
    except HTTPException as e:
        detail = str(e.detail)
        logger.warning("Gallery import job %s failed: %s", job_id, detail)
        _publish_gallery_job(
            job_id,
            {
                "status": "error",
                "stage": "error",
                "message": "Gallery import failed",
                "progress": 100,
                "error": detail,
                "completed_at": utc_now(),
                "lease_owner": None,
                "lease_expires_at": None,
            },
        )
    except Exception as e:
        logger.warning("Gallery import job %s failed", job_id, exc_info=True)
        _publish_gallery_job(
            job_id,
            {
                "status": "error",
                "stage": "error",
                "message": "Gallery import failed",
                "progress": 100,
                "error": str(e),
                "completed_at": utc_now(),
                "lease_owner": None,
                "lease_expires_at": None,
            },
        )
    finally:
        _unlink_trusted_gallery_job_path(job.get("path"), kind="import", job_id=job_id)
        if reservation_id:
            await asyncio.to_thread(release_import_upload_reservation, reservation_id)


async def _run_gallery_job_dispatcher(kind: str, worker_id: str, running_limit: int) -> None:
    active_tasks: set[asyncio.Task] = set()
    runner_by_kind = {
        "export": _run_gallery_export_job,
        "sync": _run_gallery_sync_job,
        "import": _run_gallery_import_job,
    }
    runner = runner_by_kind[kind]
    idle_delay = GALLERY_JOB_DISPATCH_INTERVAL_SECONDS
    while True:
        try:
            active_tasks = {task for task in active_tasks if not task.done()}
            claimed_count = 0
            while len(active_tasks) < running_limit:
                now = utc_now()
                job = await asyncio.to_thread(
                    claim_next_gallery_job,
                    kind=kind,
                    worker_id=worker_id,
                    lease_expires_at=_gallery_job_lease_expires_at(),
                    now=now,
                    running_limit=running_limit,
                    counted_kinds=_claim_counted_gallery_kinds(kind),
                )
                if not job:
                    break
                active_tasks.add(asyncio.create_task(runner(job)))
                claimed_count += 1
            if claimed_count:
                idle_delay = GALLERY_JOB_DISPATCH_INTERVAL_SECONDS
            elif len(active_tasks) < running_limit:
                metrics.increment(f"gallery.{kind}.claim_miss")
                idle_delay = min(
                    GALLERY_JOB_DISPATCH_MAX_IDLE_BACKOFF_SECONDS,
                    max(GALLERY_JOB_DISPATCH_INTERVAL_SECONDS, idle_delay * 2),
                )
            await asyncio.sleep(idle_delay)
        except asyncio.CancelledError:
            for task in active_tasks:
                task.cancel()
            if active_tasks:
                await asyncio.gather(*active_tasks, return_exceptions=True)
            raise
        except Exception:
            logger.warning("Gallery %s dispatcher error", kind, exc_info=True)
            await asyncio.sleep(GALLERY_JOB_DISPATCH_INTERVAL_SECONDS)


async def run_gallery_export_dispatcher(worker_id: str) -> None:
    await _run_gallery_job_dispatcher("export", worker_id, MAX_ACTIVE_EXPORT_JOBS)


async def run_gallery_sync_dispatcher(worker_id: str) -> None:
    await _run_gallery_job_dispatcher("sync", worker_id, MAX_ACTIVE_SYNC_JOBS)


async def run_gallery_import_dispatcher(worker_id: str) -> None:
    await _run_gallery_job_dispatcher("import", worker_id, MAX_ACTIVE_IMPORT_JOBS)



__all__ = [name for name in globals() if not name.startswith("__")]
