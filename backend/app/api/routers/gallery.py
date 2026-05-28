import asyncio
import logging
import mimetypes
import time
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Body, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
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
    MessageResponse,
)


router = APIRouter()
logger = logging.getLogger(__name__)
GALLERY_EXPORT_TERMINAL_STATUSES = {"success", "error"}
MAX_ACTIVE_EXPORT_JOBS = 5
EXPORT_JOB_TTL_SECONDS = 1800
EXPORT_JOB_GC_INTERVAL_SECONDS = 300


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


async def _gallery_zip_response(
    entries,
    filename_prefix: str,
    skipped: list[dict] | None = None,
    extra_headers: dict[str, str] | None = None,
    reserve_export_slot: bool = False,
) -> StreamingResponse:
    if reserve_export_slot:
        await _reserve_gallery_export_direct_slot()

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
                _release_gallery_export_direct_slot()

    return StreamingResponse(
        zip_chunks(),
        media_type="application/zip",
        headers=headers,
    )


def _missing_gallery_ids(requested_ids: list[str], entries: list[GalleryEntry]) -> list[str]:
    found_ids = {entry.id for entry in entries}
    return [image_id for image_id in requested_ids if image_id not in found_ids]


def _gallery_export_jobs() -> dict[str, dict]:
    if not hasattr(app.state, "gallery_export_jobs"):
        app.state.gallery_export_jobs = {}
    return app.state.gallery_export_jobs


def _gallery_export_tasks() -> dict[str, asyncio.Task]:
    if not hasattr(app.state, "gallery_export_tasks"):
        app.state.gallery_export_tasks = {}
    return app.state.gallery_export_tasks


def _gallery_export_subscribers() -> dict[str, set[asyncio.Queue]]:
    if not hasattr(app.state, "gallery_export_subscribers"):
        app.state.gallery_export_subscribers = {}
    return app.state.gallery_export_subscribers


def _gallery_export_lock() -> asyncio.Lock:
    if not hasattr(app.state, "gallery_export_lock"):
        app.state.gallery_export_lock = asyncio.Lock()
    return app.state.gallery_export_lock


def _gallery_export_direct_downloads() -> int:
    return max(0, int(getattr(app.state, "gallery_export_direct_downloads", 0) or 0))


def _active_gallery_export_count() -> int:
    active_jobs = sum(
        1
        for job in _gallery_export_jobs().values()
        if job.get("status") not in GALLERY_EXPORT_TERMINAL_STATUSES
    )
    return active_jobs + _gallery_export_direct_downloads()


def _release_gallery_export_direct_slot() -> None:
    app.state.gallery_export_direct_downloads = max(
        0,
        _gallery_export_direct_downloads() - 1,
    )


async def _reserve_gallery_export_direct_slot() -> None:
    async with _gallery_export_lock():
        active_count = _active_gallery_export_count()
        if active_count >= MAX_ACTIVE_EXPORT_JOBS:
            raise HTTPException(
                status_code=429,
                detail=f"Too many active export jobs ({active_count}). "
                "Please wait for existing exports to complete.",
            )
        app.state.gallery_export_direct_downloads = _gallery_export_direct_downloads() + 1


async def _create_reserved_gallery_export_job(
    filename_prefix: str,
    requested_count: int,
) -> dict:
    async with _gallery_export_lock():
        active_count = _active_gallery_export_count()
        if active_count >= MAX_ACTIVE_EXPORT_JOBS:
            raise HTTPException(
                status_code=429,
                detail=f"Too many active export jobs ({active_count}). "
                "Please wait for existing exports to complete.",
            )
        return _create_gallery_export_job(filename_prefix, requested_count)


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


def _publish_gallery_export_job(job_id: str, updates: dict) -> dict | None:
    job = _gallery_export_jobs().get(job_id)
    if not job:
        return None
    job.update(updates)
    job["updated_at"] = utc_now()
    payload = _gallery_export_payload(job)
    event = {"event": "export", "data": payload}
    for queue in list(_gallery_export_subscribers().get(job_id, set())):
        publish_queue(queue, event)
    return payload


def _cleanup_gallery_export_job(job_id: str) -> None:
    job = _gallery_export_jobs().pop(job_id, None)
    _gallery_export_tasks().pop(job_id, None)
    _gallery_export_subscribers().pop(job_id, None)
    if not job:
        return
    path = job.get("path")
    if path:
        Path(path).unlink(missing_ok=True)


async def gc_gallery_export_jobs() -> None:
    """Periodically clean up completed/errored export jobs and orphan ZIP files."""
    while True:
        try:
            await asyncio.sleep(EXPORT_JOB_GC_INTERVAL_SECONDS)
            now_ts = time.time()
            jobs = _gallery_export_jobs()
            stale_ids = []
            for job_id, job in jobs.items():
                if job.get("status") not in GALLERY_EXPORT_TERMINAL_STATUSES:
                    continue
                updated_at = job.get("updated_at", "")
                try:
                    job_ts = datetime.fromisoformat(
                        str(updated_at).replace("Z", "+00:00")
                    ).timestamp()
                except (ValueError, AttributeError):
                    job_ts = 0
                if now_ts - job_ts >= EXPORT_JOB_TTL_SECONDS:
                    stale_ids.append(job_id)
            for job_id in stale_ids:
                _cleanup_gallery_export_job(job_id)
            if stale_ids:
                logger.info(
                    "GC cleaned up %d stale gallery export job(s)", len(stale_ids)
                )
            # Remove orphan ZIP files with no matching in-memory job
            exports_dir = Path(config.DATA_DIR) / "exports"
            if exports_dir.exists():
                known_ids = set(jobs.keys())
                for zip_path in exports_dir.glob("*.zip"):
                    if zip_path.stem not in known_ids:
                        zip_path.unlink(missing_ok=True)
                        logger.info(
                            "GC removed orphan export file: %s", zip_path.name
                        )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Gallery export GC error", exc_info=True)


def _create_gallery_export_job(filename_prefix: str, requested_count: int) -> dict:
    job_id = uuid.uuid4().hex
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{filename_prefix}-{timestamp}.zip"
    path = Path(config.DATA_DIR) / "exports" / f"{job_id}.zip"
    now = utc_now()
    job = {
        "job_id": job_id,
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
        "path": path,
    }
    _gallery_export_jobs()[job_id] = job
    return job


async def _run_gallery_export_job(
    job_id: str,
    entries: Iterable[GalleryEntry | dict],
    *,
    requested_count: int,
    skipped: list[dict] | None = None,
) -> None:
    job = _gallery_export_jobs().get(job_id)
    if not job:
        return

    loop = asyncio.get_running_loop()

    def progress(updates: dict):
        loop.call_soon_threadsafe(_publish_gallery_export_job, job_id, updates)

    try:
        _publish_gallery_export_job(
            job_id,
            {
                "status": "running",
                "stage": "preparing",
                "message": "Preparing gallery ZIP entries",
                "progress": 0,
            },
        )
        result: GalleryZipFileResult = await asyncio.to_thread(
            write_gallery_zip_file,
            entries,
            job["path"],
            requested_count=requested_count,
            skipped=skipped,
            progress=progress,
        )
        _publish_gallery_export_job(
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
            },
        )
    except asyncio.CancelledError:
        Path(job["path"]).unlink(missing_ok=True)
        raise
    except Exception as e:
        logger.warning("Failed to build gallery export ZIP job %s", job_id, exc_info=True)
        Path(job["path"]).unlink(missing_ok=True)
        _publish_gallery_export_job(
            job_id,
            {
                "status": "error",
                "stage": "error",
                "message": "Failed to build ZIP archive",
                "error": str(e),
            },
        )
    finally:
        _gallery_export_tasks().pop(job_id, None)


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
            {
                key: value
                for key, value in filters.items()
                if value not in (None, "", False)
            },
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
    exportable_entries: list[GalleryEntry] = []
    skipped_entries = [
        {
            "id": image_id,
            "reason": "gallery_entry_missing",
        }
        for image_id in missing_ids
    ]
    for entry in entries:
        path = await asyncio.to_thread(storage.safe_image_path, entry.filename)
        if path and await asyncio.to_thread(path.exists):
            exportable_entries.append(entry)
            continue
        skipped_entries.append(
            {
                "id": entry.id,
                "filename": entry.filename,
                "reason": "image_file_missing",
            }
        )

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
        missing_ids = _missing_gallery_ids(ids, entries)
        skipped_entries = [
            {
                "id": image_id,
                "reason": "gallery_entry_missing",
            }
            for image_id in missing_ids
        ]
        requested_count = len(ids)
        filename_prefix = "gpt-images-selected"
    else:
        gallery_count = await asyncio.to_thread(storage.get_gallery_count)
        if gallery_count == 0:
            raise HTTPException(status_code=404, detail="No images in gallery")
        entries = storage.iter_gallery_export_rows()
        skipped_entries = []
        requested_count = gallery_count
        filename_prefix = "gpt-images"

    job = await _create_reserved_gallery_export_job(filename_prefix, requested_count)
    task = asyncio.create_task(
        _run_gallery_export_job(
            job["job_id"],
            entries,
            requested_count=requested_count,
            skipped=skipped_entries,
        )
    )
    _gallery_export_tasks()[job["job_id"]] = task
    return GalleryExportJobStatus(**_gallery_export_payload(job))


@router.get("/api/gallery/export-jobs/{job_id}", response_model=GalleryExportJobStatus)
async def get_gallery_export_job(job_id: str):
    job = _gallery_export_jobs().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Gallery export job not found")
    return GalleryExportJobStatus(**_gallery_export_payload(job))


@router.get("/api/gallery/export-jobs/{job_id}/events")
async def stream_gallery_export_job(job_id: str, request: Request):
    job = _gallery_export_jobs().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Gallery export job not found")

    client_ip = auth.get_client_ip(request)
    if not await sse_limiter.acquire(client_ip):
        raise HTTPException(status_code=429, detail="Too many SSE connections")

    queue: asyncio.Queue = asyncio.Queue(maxsize=20)
    subscribers_by_job = _gallery_export_subscribers()
    subscribers = subscribers_by_job.setdefault(job_id, set())
    subscribers.add(queue)
    publish_queue(queue, {"event": "export", "data": _gallery_export_payload(job)})

    async def event_stream():
        start = time.monotonic()
        try:
            while True:
                if await request.is_disconnected():
                    break
                if time.monotonic() - start > config.SSE_CONNECTION_TTL_SECONDS:
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue

                data = item["data"]
                yield serialize_sse_event(item["event"], data)
                if data.get("status") in GALLERY_EXPORT_TERMINAL_STATUSES:
                    break
        finally:
            subscribers.discard(queue)
            if not subscribers:
                subscribers_by_job.pop(job_id, None)
            await sse_limiter.release(client_ip)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/gallery/export-jobs/{job_id}/download")
async def download_gallery_export_job(job_id: str):
    job = _gallery_export_jobs().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Gallery export job not found")
    if job.get("status") != "success":
        raise HTTPException(status_code=409, detail="Gallery export job is not ready")

    path = Path(job["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Gallery export archive not found")

    return FileResponse(
        path,
        media_type="application/zip",
        filename=job["filename"],
        headers={
            "Content-Encoding": "identity",
            "X-Content-Type-Options": "nosniff",
            "X-Gallery-Requested-Count": str(job.get("requested_count") or 0),
            "X-Gallery-Exported-Count": str(job.get("exported_count") or 0),
            "X-Gallery-Missing-Count": str(job.get("missing_count") or 0),
        },
        background=BackgroundTask(_cleanup_gallery_export_job, job_id),
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
    path = await asyncio.to_thread(storage.safe_image_path, filename)
    if not path or not await asyncio.to_thread(path.exists):
        raise HTTPException(status_code=404, detail="Image not found")

    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
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
    thumbnail_filename = await asyncio.to_thread(
        storage.ensure_thumbnail_for_image,
        filename,
    )
    if not thumbnail_filename:
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    path = storage.safe_thumbnail_path(thumbnail_filename)
    if not path or not path.exists():
        await asyncio.to_thread(storage.invalidate_thumbnail_cache, thumbnail_filename)
        thumbnail_filename = await asyncio.to_thread(
            storage.ensure_thumbnail_for_image,
            filename,
        )
        if not thumbnail_filename:
            raise HTTPException(status_code=404, detail="Thumbnail not found")
        path = storage.safe_thumbnail_path(thumbnail_filename)
        if not path or not path.exists():
            raise HTTPException(status_code=404, detail="Thumbnail not found")

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
