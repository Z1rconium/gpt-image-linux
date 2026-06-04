import asyncio
import hashlib
import logging
import mimetypes
import os
import time
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Body, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from ..app_state import app
from ..gallery_archive import (
    GalleryZipFileResult,
    import_archive_max_bytes,
    iter_gallery_zip_chunks,
    iter_import_gallery_entries,
    stream_upload_to_tempfile,
    write_gallery_zip_file,
)
from ..jobs import publish_queue, serialize_sse_event
from ..sse_limiter import sse_limiter
from ...core import security as auth
from ...core import settings as config
from ...core.observability import metrics
from ...core.utils import utc_now
from ...integrations import r2_sync
from ...repositories import storage
from ...schemas.models import (
    GalleryBatchFavoriteRequest,
    GalleryBatchRequest,
    GalleryBatchResponse,
    GalleryEntry,
    GalleryExportJobStatus,
    GalleryExportRequest,
    GalleryFavoriteRequest,
    GalleryResponse,
    GallerySyncJobStatus,
    MessageResponse,
)


router = APIRouter()


def granian_worker_count() -> int:
    try:
        return max(1, int(os.getenv("GRANIAN_WORKERS", "1")))
    except (TypeError, ValueError):
        return 1
logger = logging.getLogger(__name__)
GALLERY_EXPORT_TERMINAL_STATUSES = {"success", "error"}
GALLERY_SYNC_TERMINAL_STATUSES = {"success", "error"}
MAX_ACTIVE_EXPORT_JOBS = 5
MAX_ACTIVE_SYNC_JOBS = 1
EXPORT_JOB_TTL_SECONDS = 1800
EXPORT_JOB_GC_INTERVAL_SECONDS = 300
SYNC_JOB_TTL_SECONDS = 1800
SCHEDULED_R2_SYNC_DISABLED_POLL_SECONDS = 60
GALLERY_JOB_LEASE_SECONDS = 300
GALLERY_JOB_DISPATCH_INTERVAL_SECONDS = 0.5
GALLERY_JOB_DISPATCH_MAX_IDLE_BACKOFF_SECONDS = 5.0
BACKGROUND_TASK_LEASE_SECONDS = 120
GALLERY_PROGRESS_MIN_INTERVAL_SECONDS = 0.5
GALLERY_PROGRESS_MIN_ITEMS = 50
DIRECT_EXPORT_SLOT_LEASE_SECONDS = 6 * 3600
GALLERY_JOB_SSE_IDLE_CHECK_SECONDS = 1.0
GALLERY_JOB_SSE_QUEUE_MAXSIZE = 20


class GalleryProgressThrottler:
    def __init__(
        self,
        publish,
        *,
        min_interval_seconds: float = GALLERY_PROGRESS_MIN_INTERVAL_SECONDS,
        min_items: int = GALLERY_PROGRESS_MIN_ITEMS,
    ) -> None:
        self.publish = publish
        self.min_interval_seconds = min_interval_seconds
        self.min_items = max(1, min_items)
        self.last_emit_at = 0.0
        self.last_item_count = 0
        self.last_stage: str | None = None

    def emit(self, updates: dict, *, force: bool = False) -> None:
        stage = str(updates.get("stage") or "")
        item_count = _progress_item_count(updates)
        now = time.monotonic()
        if not force:
            if (
                stage == self.last_stage
                and item_count - self.last_item_count < self.min_items
                and now - self.last_emit_at < self.min_interval_seconds
            ):
                return
        self.last_emit_at = now
        self.last_stage = stage
        self.last_item_count = item_count
        self.publish(updates)


def _resolve_gallery_image_path(filename: str) -> Path | None:
    path = storage.safe_image_path(filename)
    if not path or not path.exists():
        return None
    if not storage.is_gallery_filename_referenced(filename):
        return None
    return path


def _resolve_gallery_thumbnail_path(filename: str) -> Path | None:
    if not storage.is_gallery_filename_referenced(filename):
        return None

    thumbnail_filename = storage.ensure_thumbnail_for_image(filename)
    if not thumbnail_filename:
        return None

    path = storage.safe_thumbnail_path(thumbnail_filename)
    if path and path.exists():
        return path

    storage.invalidate_thumbnail_cache(thumbnail_filename)
    thumbnail_filename = storage.ensure_thumbnail_for_image(filename)
    if not thumbnail_filename:
        return None

    path = storage.safe_thumbnail_path(thumbnail_filename)
    if not path or not path.exists():
        return None
    return path


def _x_accel_response(
    path: Path,
    *,
    internal_prefix: str,
    media_type: str,
    download: bool = False,
) -> Response:
    headers = {
        "X-Accel-Redirect": f"{internal_prefix}{quote(path.name, safe='')}",
        "Cache-Control": "public, max-age=31536000",
    }
    if download:
        extension = path.suffix.lstrip(".") or "png"
        filename = f"gpt-image-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.{extension}"
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return Response(status_code=200, media_type=media_type, headers=headers)


def _resolve_batch_download_entries(
    entries: list[GalleryEntry],
) -> tuple[list[GalleryEntry], list[dict[str, str]]]:
    exportable_entries: list[GalleryEntry] = []
    skipped_entries: list[dict[str, str]] = []
    for entry in entries:
        path = storage.safe_image_path(entry.filename)
        if path and path.exists():
            exportable_entries.append(entry)
            continue
        skipped_entries.append(
            {
                "id": entry.id,
                "filename": entry.filename,
                "reason": "image_file_missing",
            }
        )
    return exportable_entries, skipped_entries


def normalize_gallery_date_filter(value: str | None, end_of_day: bool = False) -> str | None:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None

    if len(raw_value) == 10:
        try:
            datetime.strptime(raw_value, "%Y-%m-%d")
        except ValueError as e:
            raise HTTPException(
                status_code=422,
                detail="Gallery date filters must use YYYY-MM-DD or ISO datetime",
            ) from e
        return f"{raw_value}T{'23:59:59.999999' if end_of_day else '00:00:00'}"

    try:
        datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail="Gallery date filters must use YYYY-MM-DD or ISO datetime",
        ) from e
    return raw_value


def build_gallery_filters(
    prompt: str | None,
    model: str | None,
    preset: str | None,
    size: str | None,
    date_from: str | None,
    date_to: str | None,
    favorite: bool | None,
) -> dict:
    return {
        "prompt": str(prompt or "").strip(),
        "model": str(model or "").strip(),
        "preset": str(preset or "").strip(),
        "size": str(size or "").strip(),
        "date_from": normalize_gallery_date_filter(date_from),
        "date_to": normalize_gallery_date_filter(date_to, end_of_day=True),
        "favorite": favorite,
    }


def _gallery_filters_for_log(filters: dict) -> dict:
    prompt = str(filters.get("prompt") or "")
    return {
        "prompt_present": bool(prompt),
        "prompt_len": len(prompt),
        "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if prompt
        else None,
        **{
            key: value
            for key, value in filters.items()
            if key != "prompt" and value not in (None, "", False)
        },
    }


async def _gallery_zip_response(
    entries,
    filename_prefix: str,
    skipped: list[dict] | None = None,
    extra_headers: dict[str, str] | None = None,
    reserve_export_slot: bool = False,
) -> StreamingResponse:
    direct_slot_id: str | None = None
    if reserve_export_slot:
        direct_slot_id = await _reserve_gallery_export_direct_slot()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{filename_prefix}-{timestamp}.zip"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Encoding": "identity",
        "X-Content-Type-Options": "nosniff",
    }
    if extra_headers:
        headers.update(extra_headers)

    def zip_chunks():
        try:
            yield from iter_gallery_zip_chunks(entries, skipped=skipped)
        finally:
            if reserve_export_slot:
                _release_gallery_export_direct_slot(direct_slot_id)

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


def _create_gallery_export_direct_slot() -> dict:
    now = utc_now()
    return {
        "job_id": f"direct-{os.urandom(16).hex()}",
        "kind": "export_direct",
        "status": "running",
        "stage": "streaming",
        "message": "Streaming direct gallery ZIP download",
        "progress": 0,
        "created_at": now,
        "started_at": now,
        "updated_at": now,
        "lease_expires_at": _direct_export_slot_expires_at(),
        "payload": {},
    }


def _release_gallery_export_direct_slot(job_id: str | None) -> None:
    if job_id:
        storage.delete_gallery_job("export_direct", job_id)


async def _reserve_gallery_export_direct_slot() -> str:
    async with _gallery_export_lock():
        slot = await asyncio.to_thread(
            storage.reserve_gallery_job_capacity,
            job=_create_gallery_export_direct_slot(),
            counted_kinds=("export", "export_direct"),
            max_active=MAX_ACTIVE_EXPORT_JOBS,
        )
        if not slot:
            active_count = await asyncio.to_thread(storage.count_active_gallery_jobs, "export")
            active_count += await asyncio.to_thread(
                storage.count_active_gallery_jobs,
                "export_direct",
            )
            raise HTTPException(
                status_code=429,
                detail=f"Too many active export jobs ({active_count}). "
                "Please wait for existing exports to complete.",
            )
        return str(slot["job_id"])


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
        "skipped_existing_count",
        "missing_local_count",
        "failed_count",
        "bytes_total",
        "bytes_uploaded",
    )
    return {key: job.get(key) for key in keys}


def _gallery_job_event_name(kind: str) -> str:
    return "sync" if kind == "sync" else "export"


def _gallery_job_payload(kind: str, job: dict) -> dict:
    if kind == "sync":
        return _gallery_sync_payload(job)
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
                storage.get_gallery_jobs_updated_at_edges,
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
                job = await asyncio.to_thread(storage.get_gallery_job, kind, job_id)
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


def _gallery_job_lease_expires_at() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=GALLERY_JOB_LEASE_SECONDS)).isoformat()


def _direct_export_slot_expires_at() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=DIRECT_EXPORT_SLOT_LEASE_SECONDS)).isoformat()


def _background_task_lease_expires_at() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=BACKGROUND_TASK_LEASE_SECONDS)).isoformat()


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
            storage.acquire_background_lease,
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
    job = storage.update_gallery_job(job_id, updates)
    if job:
        _publish_gallery_job_sse(job)
    return job


def _publish_gallery_job_progress(job_id: str, updates: dict) -> bool:
    return storage.update_gallery_job_progress(job_id, updates)


def _build_export_job_entries(job: dict) -> tuple[Iterable[GalleryEntry | dict], int, list[dict]]:
    payload = job.get("payload") or {}
    ids = payload.get("ids")
    if ids:
        entries = storage.get_gallery_entries_by_ids(ids)
        missing_ids = _missing_gallery_ids(ids, entries)
        skipped = [
            {
                "id": image_id,
                "reason": "gallery_entry_missing",
            }
            for image_id in missing_ids
        ]
        return entries, len(ids), skipped
    requested_count = storage.get_gallery_count()
    return storage.iter_gallery_export_rows(), requested_count, []


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


def _create_gallery_sync_job(total_count: int) -> dict:
    job_id = os.urandom(16).hex()
    now = utc_now()
    return storage.create_gallery_job(
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
        skipped_existing_count=0,
        missing_local_count=0,
        failed_count=0,
        bytes_total=0,
        bytes_uploaded=0,
        payload={},
    )


async def _create_reserved_gallery_export_job(
    filename_prefix: str,
    requested_count: int,
    payload: dict,
) -> dict:
    async with _gallery_export_lock():
        job = await asyncio.to_thread(
            storage.reserve_gallery_job_capacity,
            job=_build_gallery_export_job(filename_prefix, requested_count, payload),
            counted_kinds=("export", "export_direct"),
            max_active=MAX_ACTIVE_EXPORT_JOBS,
        )
        if not job:
            active_count = await asyncio.to_thread(storage.count_active_gallery_jobs, "export")
            active_count += await asyncio.to_thread(
                storage.count_active_gallery_jobs,
                "export_direct",
            )
            raise HTTPException(
                status_code=429,
                detail=f"Too many active export jobs ({active_count}). "
                "Please wait for existing exports to complete.",
            )
        return job


async def _create_reserved_gallery_sync_job(total_count: int) -> dict:
    active_count = await asyncio.to_thread(storage.count_active_gallery_jobs, "sync")
    if active_count >= MAX_ACTIVE_SYNC_JOBS:
        raise HTTPException(
            status_code=429,
            detail="A gallery R2 sync job is already queued or running.",
        )
    return await asyncio.to_thread(_create_gallery_sync_job, total_count)


async def _run_gallery_export_job(job: dict) -> None:
    job_id = job["job_id"]
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
            Path(str(job["path"])),
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
        Path(str(job.get("path") or "")).unlink(missing_ok=True)
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
    loop = asyncio.get_running_loop()

    def publish_progress(updates: dict):
        updates = {**updates, "lease_expires_at": _gallery_job_lease_expires_at()}
        loop.call_soon_threadsafe(_publish_gallery_job_progress, job_id, updates)

    throttler = GalleryProgressThrottler(publish_progress)

    def progress(updates: dict):
        force = updates.get("stage") in {"listing_remote", "completed"}
        throttler.emit(updates, force=force)

    try:
        r2_settings = await asyncio.to_thread(storage.load_r2_backup_settings)
        await asyncio.to_thread(
            r2_sync.resolve_r2_backup_settings,
            r2_settings,
            require_enabled=True,
        )
        total_count = await asyncio.to_thread(storage.get_gallery_count)
        _publish_gallery_job(
            job_id,
            {
                "status": "running",
                "stage": "listing_remote",
                "message": "Listing existing R2 objects",
                "progress": 0,
                "total_count": total_count,
                "lease_expires_at": _gallery_job_lease_expires_at(),
            },
        )
        result = await asyncio.to_thread(
            r2_sync.sync_gallery_to_r2,
            r2_settings,
            storage.iter_gallery_export_rows(),
            total_count=total_count,
            progress_cb=progress,
        )
        _publish_gallery_job(
            job_id,
            {
                "status": "success",
                "stage": "completed",
                "message": "R2 gallery sync complete",
                "progress": 100,
                "error": None,
                "completed_at": utc_now(),
                "lease_owner": None,
                "lease_expires_at": None,
                **result.to_updates(),
            },
        )
    except asyncio.CancelledError:
        raise
    except r2_sync.R2SyncError as e:
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


async def _run_gallery_job_dispatcher(kind: str, worker_id: str, running_limit: int) -> None:
    active_tasks: set[asyncio.Task] = set()
    runner = _run_gallery_export_job if kind == "export" else _run_gallery_sync_job
    idle_delay = GALLERY_JOB_DISPATCH_INTERVAL_SECONDS
    while True:
        try:
            active_tasks = {task for task in active_tasks if not task.done()}
            claimed_count = 0
            while len(active_tasks) < running_limit:
                now = utc_now()
                job = await asyncio.to_thread(
                    storage.claim_next_gallery_job,
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


def kick_gallery_job_dispatchers() -> None:
    for name, starter in (
        ("gallery_export_dispatcher_task", run_gallery_export_dispatcher),
        ("gallery_sync_dispatcher_task", run_gallery_sync_dispatcher),
    ):
        task = getattr(app.state, name, None)
        if task and not task.done():
            continue
        worker_id = getattr(app.state, "worker_id", f"{os.getpid()}-{id(app)}")
        setattr(app.state, name, asyncio.create_task(starter(worker_id)))


def _r2_sync_interval_hours(r2_settings: dict | None) -> int:
    if not isinstance(r2_settings, dict):
        return 0
    try:
        interval_hours = int(r2_settings.get("sync_interval_hours") or 0)
    except (TypeError, ValueError):
        return 0
    return interval_hours if interval_hours > 0 else 0


async def _scheduled_gallery_r2_sync_delay_seconds() -> int:
    try:
        r2_settings = await asyncio.to_thread(storage.load_r2_backup_settings)
    except Exception:
        logger.warning("Failed to load R2 settings for scheduled sync", exc_info=True)
        return SCHEDULED_R2_SYNC_DISABLED_POLL_SECONDS
    if not r2_settings.get("enabled"):
        return SCHEDULED_R2_SYNC_DISABLED_POLL_SECONDS
    interval_hours = _r2_sync_interval_hours(r2_settings)
    if interval_hours <= 0:
        return SCHEDULED_R2_SYNC_DISABLED_POLL_SECONDS
    return interval_hours * 3600


async def _run_scheduled_gallery_r2_sync_once() -> dict[str, object]:
    r2_settings = await asyncio.to_thread(storage.load_r2_backup_settings)
    interval_hours = _r2_sync_interval_hours(r2_settings)
    if not r2_settings.get("enabled") or interval_hours <= 0:
        return {"started": False, "reason": "disabled"}

    try:
        await asyncio.to_thread(
            r2_sync.resolve_r2_backup_settings,
            r2_settings,
            require_enabled=True,
        )
    except r2_sync.R2ConfigurationError as e:
        logger.info("Skipping scheduled R2 gallery sync: %s", e)
        return {"started": False, "reason": "invalid_config"}

    gallery_count = await asyncio.to_thread(storage.get_gallery_count)
    if gallery_count <= 0:
        return {"started": False, "reason": "empty_gallery"}

    active_count = await asyncio.to_thread(storage.count_active_gallery_jobs, "sync")
    if active_count >= MAX_ACTIVE_SYNC_JOBS:
        return {"started": False, "reason": "active_sync"}
    job = await asyncio.to_thread(_create_gallery_sync_job, gallery_count)
    kick_gallery_job_dispatchers()
    logger.info("Queued scheduled R2 gallery sync job %s", job["job_id"])
    return {"started": True, "job_id": job["job_id"]}


async def run_gallery_r2_scheduled_sync(worker_id: str) -> None:
    lease_name = "gallery_r2_scheduled_sync"
    while True:
        try:
            acquired = await asyncio.to_thread(
                storage.acquire_background_lease,
                name=lease_name,
                owner=worker_id,
                lease_expires_at=_background_task_lease_expires_at(),
            )
            if not acquired:
                await asyncio.sleep(SCHEDULED_R2_SYNC_DISABLED_POLL_SECONDS)
                continue
            delay_seconds = await _scheduled_gallery_r2_sync_delay_seconds()
            try:
                still_leader = await _sleep_while_renewing_background_lease(
                    name=lease_name,
                    owner=worker_id,
                    delay_seconds=delay_seconds,
                )
                if not still_leader:
                    continue
                await _run_scheduled_gallery_r2_sync_once()
            finally:
                await asyncio.to_thread(
                    storage.release_background_lease,
                    name=lease_name,
                    owner=worker_id,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Scheduled R2 gallery sync failed before job creation", exc_info=True)


async def gc_gallery_export_jobs(worker_id: str) -> None:
    """Periodically clean up completed export/sync jobs and orphan ZIP files."""
    lease_name = "gallery_export_gc"
    while True:
        try:
            acquired = await asyncio.to_thread(
                storage.acquire_background_lease,
                name=lease_name,
                owner=worker_id,
                lease_expires_at=_background_task_lease_expires_at(),
            )
            if not acquired:
                await asyncio.sleep(EXPORT_JOB_GC_INTERVAL_SECONDS)
                continue
            try:
                still_leader = await _sleep_while_renewing_background_lease(
                    name=lease_name,
                    owner=worker_id,
                    delay_seconds=EXPORT_JOB_GC_INTERVAL_SECONDS,
                )
                if not still_leader:
                    continue
                stale_exports = await asyncio.to_thread(
                    storage.cleanup_stale_gallery_jobs,
                    "export",
                    EXPORT_JOB_TTL_SECONDS,
                )
                stale_syncs = await asyncio.to_thread(
                    storage.cleanup_stale_gallery_jobs,
                    "sync",
                    SYNC_JOB_TTL_SECONDS,
                )
                expired_direct_slots = await asyncio.to_thread(
                    storage.cleanup_expired_gallery_jobs,
                    "export_direct",
                )
                for job in stale_exports:
                    path = job.get("path")
                    if path:
                        Path(str(path)).unlink(missing_ok=True)
                if stale_exports or stale_syncs or expired_direct_slots:
                    logger.info(
                        "GC cleaned up %d gallery export job(s), %d sync job(s), and %d direct export slot(s)",
                        len(stale_exports),
                        len(stale_syncs),
                        len(expired_direct_slots),
                    )
                exports_dir = Path(config.DATA_DIR) / "exports"
                if exports_dir.exists():
                    known_ids = await asyncio.to_thread(storage.list_gallery_job_ids_with_files, "export")
                    for zip_path in exports_dir.glob("*.zip"):
                        if zip_path.stem not in known_ids:
                            zip_path.unlink(missing_ok=True)
                            logger.info("GC removed orphan export file: %s", zip_path.name)
            finally:
                await asyncio.to_thread(
                    storage.release_background_lease,
                    name=lease_name,
                    owner=worker_id,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Gallery export GC error", exc_info=True)


@router.get("/api/gallery", response_model=GalleryResponse)
async def get_gallery_handler(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=9, ge=1, le=100),
    prompt: str | None = Query(default=None, max_length=4000),
    model: str | None = Query(default=None, max_length=200),
    preset: str | None = Query(default=None, max_length=200),
    size: str | None = Query(default=None, max_length=40),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    favorite: bool | None = Query(default=None),
    include_total_bytes: bool = Query(default=False),
):
    filters = build_gallery_filters(
        prompt=prompt,
        model=model,
        preset=preset,
        size=size,
        date_from=date_from,
        date_to=date_to,
        favorite=favorite,
    )
    started_at = time.perf_counter()
    gallery_page = await asyncio.to_thread(
        storage.get_gallery_page,
        page=page,
        page_size=page_size,
        filters=filters,
        include_total_bytes=include_total_bytes,
    )
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    metrics.increment("gallery.requests")
    metrics.observe_ms("gallery.request", elapsed_ms)
    metrics.observe_ms("gallery.db_query", gallery_page.query_elapsed_ms)
    if elapsed_ms >= config.SLOW_GALLERY_QUERY_MS:
        metrics.increment("gallery.slow_queries")
        metrics.increment("sqlite.slow_queries")
        logger.warning(
            "Slow /api/gallery query: elapsed_ms=%.2f db_query_ms=%.2f page=%s page_size=%s total=%s filters=%s",
            elapsed_ms,
            gallery_page.query_elapsed_ms,
            gallery_page.page,
            gallery_page.page_size,
            gallery_page.total,
            _gallery_filters_for_log(filters),
        )

    return GalleryResponse(
        total=gallery_page.total,
        total_bytes=gallery_page.total_bytes,
        page=gallery_page.page,
        page_size=gallery_page.page_size,
        total_pages=gallery_page.total_pages,
        has_prev=gallery_page.has_prev,
        has_next=gallery_page.has_next,
        images=gallery_page.images,
        filter_options=gallery_page.filter_options,
    )


@router.post("/api/gallery/batch/delete", response_model=GalleryBatchResponse)
async def delete_gallery_batch(req: GalleryBatchRequest):
    requested_count = len(req.ids)
    entries = await asyncio.to_thread(storage.get_gallery_entries_by_ids, req.ids)
    missing_ids = _missing_gallery_ids(req.ids, entries)
    deleted_entries, deleted_files = await asyncio.to_thread(storage.delete_gallery_images, req.ids)
    if deleted_entries == 0:
        raise HTTPException(status_code=404, detail="Gallery entries not found")
    return GalleryBatchResponse(
        status="ok",
        count=deleted_entries,
        file_count=deleted_files,
        requested_count=requested_count,
        updated_count=deleted_entries,
        missing_count=len(missing_ids),
        missing_ids=missing_ids,
    )


@router.patch("/api/gallery/batch/favorite", response_model=GalleryBatchResponse)
async def update_gallery_batch_favorite(req: GalleryBatchFavoriteRequest):
    requested_count = len(req.ids)
    entries = await asyncio.to_thread(storage.get_gallery_entries_by_ids, req.ids)
    missing_ids = _missing_gallery_ids(req.ids, entries)
    updated_entries = await asyncio.to_thread(storage.update_gallery_entries_favorite, req.ids, req.favorite)
    if updated_entries == 0:
        raise HTTPException(status_code=404, detail="Gallery entries not found")
    return GalleryBatchResponse(
        status="ok",
        count=updated_entries,
        requested_count=requested_count,
        updated_count=updated_entries,
        missing_count=len(missing_ids),
        missing_ids=missing_ids,
    )


@router.post("/api/gallery/batch/download")
async def download_gallery_batch(req: GalleryBatchRequest):
    entries = await asyncio.to_thread(storage.get_gallery_entries_by_ids, req.ids)
    if not entries:
        raise HTTPException(status_code=404, detail="Gallery entries not found")

    missing_ids = _missing_gallery_ids(req.ids, entries)
    skipped_entries = [
        {
            "id": image_id,
            "reason": "gallery_entry_missing",
        }
        for image_id in missing_ids
    ]
    exportable_entries, file_skipped_entries = await asyncio.to_thread(
        _resolve_batch_download_entries,
        entries,
    )
    skipped_entries.extend(file_skipped_entries)

    return await _gallery_zip_response(
        exportable_entries,
        "gpt-images-selected",
        skipped=skipped_entries,
        extra_headers={
            "X-Gallery-Requested-Count": str(len(req.ids)),
            "X-Gallery-Exported-Count": str(len(exportable_entries)),
            "X-Gallery-Missing-Count": str(len(skipped_entries)),
        },
        reserve_export_slot=True,
    )


@router.post("/api/gallery/export-jobs", response_model=GalleryExportJobStatus, status_code=202)
async def create_gallery_export_job(req: GalleryExportRequest | None = Body(default=None)):
    ids = req.ids if req else None
    if ids:
        entries = await asyncio.to_thread(storage.get_gallery_entries_by_ids, ids)
        if not entries:
            raise HTTPException(status_code=404, detail="Gallery entries not found")
        requested_count = len(ids)
        filename_prefix = "gpt-images-selected"
        payload = {"ids": ids, "filename_prefix": filename_prefix}
    else:
        gallery_count = await asyncio.to_thread(storage.get_gallery_count)
        if gallery_count == 0:
            raise HTTPException(status_code=404, detail="No images in gallery")
        requested_count = gallery_count
        filename_prefix = "gpt-images"
        payload = {"ids": None, "filename_prefix": filename_prefix}

    job = await _create_reserved_gallery_export_job(filename_prefix, requested_count, payload)
    kick_gallery_job_dispatchers()
    return GalleryExportJobStatus(**_gallery_export_payload(job))


@router.get("/api/gallery/export-jobs/{job_id}", response_model=GalleryExportJobStatus)
async def get_gallery_export_job(job_id: str):
    job = await asyncio.to_thread(storage.get_gallery_job, "export", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Gallery export job not found")
    return GalleryExportJobStatus(**_gallery_export_payload(job))


async def _stream_gallery_job(
    *,
    kind: str,
    job_id: str,
    request: Request,
    event_name: str,
    terminal_statuses: set[str],
    payload_builder,
    not_found_detail: str,
):
    job = await asyncio.to_thread(storage.get_gallery_job, kind, job_id)
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
            current_job = await asyncio.to_thread(storage.get_gallery_job, kind, job_id)
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
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/gallery/export-jobs/{job_id}/events")
async def stream_gallery_export_job(job_id: str, request: Request):
    return await _stream_gallery_job(
        kind="export",
        job_id=job_id,
        request=request,
        event_name="export",
        terminal_statuses=GALLERY_EXPORT_TERMINAL_STATUSES,
        payload_builder=_gallery_export_payload,
        not_found_detail="Gallery export job not found",
    )


def _cleanup_downloaded_gallery_export_job(job_id: str) -> None:
    job = storage.delete_gallery_job("export", job_id)
    if job and job.get("path"):
        Path(str(job["path"])).unlink(missing_ok=True)


@router.get("/api/gallery/export-jobs/{job_id}/download")
async def download_gallery_export_job(job_id: str):
    job = await asyncio.to_thread(storage.get_gallery_job, "export", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Gallery export job not found")
    if job.get("status") != "success":
        raise HTTPException(status_code=409, detail="Gallery export job is not ready")

    path = Path(str(job.get("path") or ""))
    if not path.exists():
        raise HTTPException(status_code=404, detail="Gallery export archive not found")

    return FileResponse(
        path,
        media_type="application/zip",
        filename=str(job.get("filename") or f"gpt-images-{job_id}.zip"),
        headers={
            "Content-Encoding": "identity",
            "X-Content-Type-Options": "nosniff",
            "X-Gallery-Requested-Count": str(job.get("requested_count") or 0),
            "X-Gallery-Exported-Count": str(job.get("exported_count") or 0),
            "X-Gallery-Missing-Count": str(job.get("missing_count") or 0),
        },
        background=BackgroundTask(_cleanup_downloaded_gallery_export_job, job_id),
    )


@router.post("/api/gallery/sync-jobs", response_model=GallerySyncJobStatus, status_code=202)
async def create_gallery_sync_job():
    gallery_count = await asyncio.to_thread(storage.get_gallery_count)
    if gallery_count == 0:
        raise HTTPException(status_code=404, detail="No images in gallery")

    r2_settings = await asyncio.to_thread(storage.load_r2_backup_settings)
    try:
        await asyncio.to_thread(
            r2_sync.resolve_r2_backup_settings,
            r2_settings,
            require_enabled=True,
        )
    except r2_sync.R2ConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    job = await _create_reserved_gallery_sync_job(gallery_count)
    kick_gallery_job_dispatchers()
    return GallerySyncJobStatus(**_gallery_sync_payload(job))


@router.get("/api/gallery/sync-jobs/{job_id}", response_model=GallerySyncJobStatus)
async def get_gallery_sync_job(job_id: str):
    job = await asyncio.to_thread(storage.get_gallery_job, "sync", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Gallery sync job not found")
    return GallerySyncJobStatus(**_gallery_sync_payload(job))


@router.get("/api/gallery/sync-jobs/{job_id}/events")
async def stream_gallery_sync_job(job_id: str, request: Request):
    return await _stream_gallery_job(
        kind="sync",
        job_id=job_id,
        request=request,
        event_name="sync",
        terminal_statuses=GALLERY_SYNC_TERMINAL_STATUSES,
        payload_builder=_gallery_sync_payload,
        not_found_detail="Gallery sync job not found",
    )


@router.patch("/api/gallery/{image_id}/favorite", response_model=GalleryEntry)
async def update_gallery_favorite(
    image_id: str,
    req: GalleryFavoriteRequest,
):
    entry = await asyncio.to_thread(storage.update_gallery_entry, image_id, {"favorite": req.favorite})
    if not entry:
        raise HTTPException(status_code=404, detail="Gallery entry not found")
    return entry


@router.get("/api/gallery/{image_id}", response_model=GalleryEntry)
async def get_gallery_item(image_id: str):
    entry = await asyncio.to_thread(storage.get_gallery_entry, image_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Gallery entry not found")
    return entry


async def _image_file_response(filename: str, *, download: bool = False):
    path = await asyncio.to_thread(_resolve_gallery_image_path, filename)
    if not path:
        raise HTTPException(status_code=404, detail="Image not found")

    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if config.ENABLE_NGINX_ACCEL_REDIRECT:
        return _x_accel_response(
            path,
            internal_prefix="/_protected/images/",
            media_type=media_type,
            download=download,
        )

    if download:
        extension = path.suffix.lstrip(".") or "png"
        return FileResponse(
            path,
            media_type=media_type,
            filename=f"gpt-image-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.{extension}",
        )

    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=31536000"},
    )


@router.get("/api/image/{filename}")
async def serve_image(filename: str):
    return await _image_file_response(filename)


@router.get("/api/thumb/{filename}")
async def serve_thumbnail(filename: str):
    path = await asyncio.to_thread(
        _resolve_gallery_thumbnail_path,
        filename,
    )
    if not path:
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    if config.ENABLE_NGINX_ACCEL_REDIRECT:
        return _x_accel_response(
            path,
            internal_prefix="/_protected/thumbs/",
            media_type=storage.THUMBNAIL_CONTENT_TYPE,
        )

    return FileResponse(
        path,
        media_type=storage.THUMBNAIL_CONTENT_TYPE,
        headers={"Cache-Control": "public, max-age=31536000"},
    )


@router.get("/api/download/{filename}")
async def download_image(filename: str):
    return await _image_file_response(filename, download=True)


@router.get("/api/download-all")
async def download_all_images():
    gallery_count = await asyncio.to_thread(storage.get_gallery_count)
    if gallery_count == 0:
        raise HTTPException(status_code=404, detail="No images in gallery")

    return await _gallery_zip_response(
        storage.iter_gallery_export_rows(),
        "gpt-images",
        reserve_export_slot=True,
    )


@router.post("/api/import")
async def import_gallery_archive(archive: UploadFile = File(...)):
    temp_path = await stream_upload_to_tempfile(archive, import_archive_max_bytes())
    try:
        imported_count = await asyncio.to_thread(
            storage.import_gallery_entries,
            iter_import_gallery_entries(temp_path),
        )
    finally:
        temp_path.unlink(missing_ok=True)

    if imported_count == 0:
        raise HTTPException(status_code=400, detail="No importable images found")
    return {
        "status": "success",
        "imported": imported_count,
    }


@router.delete("/api/gallery", response_model=MessageResponse)
async def delete_all_gallery_images():
    total, deleted_count = await asyncio.to_thread(storage.delete_all_gallery_images)
    return MessageResponse(
        status="ok",
        message=f"Deleted {deleted_count} image file(s) and {total} gallery entries",
    )


@router.delete("/api/gallery/{image_id}", response_model=MessageResponse)
async def delete_gallery_item(image_id: str):
    deleted_entry, deleted_file_count = await asyncio.to_thread(storage.delete_gallery_image, image_id)

    if not deleted_entry:
        raise HTTPException(status_code=404, detail="Gallery entry not found")

    return MessageResponse(
        status="ok",
        message=f"Deleted gallery entry and {deleted_file_count} image file(s)",
    )
