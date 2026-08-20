import asyncio
import hashlib
import inspect
import logging
import os
import uuid
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Body, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from ..app_state import app
from ...services.gallery_archive_export import (
    iter_gallery_zip_chunks,
    prepare_gallery_zip_chunks,
    write_gallery_zip_file,
)
from ...services.gallery_archive_import import (
    count_import_gallery_entries,
    iter_import_gallery_entries,
    stream_upload_to_tempfile,
)
from ...services.gallery_archive_shared import (
    GalleryZipFileResult,
    import_archive_max_bytes,
)
from ...core import security as auth
from ...core import settings as config
from ...core.observability import metrics
from ...core.utils import utc_now
from ...integrations.r2 import config as r2_config
from ...repositories.coordination import (
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
    request_gallery_job_cancellation,
    release_background_lease,
    release_import_upload_reservation,
    reserve_gallery_job_capacity,
    reserve_import_upload_capacity,
    resize_import_upload_reservation,
    update_gallery_job,
    update_gallery_job_progress,
)
from ...repositories.gallery.mutations import (
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
from ...repositories.gallery.queries import (
    get_gallery_count,
    get_gallery_entries_by_ids,
    get_gallery_entry,
    get_gallery_ids,
    get_gallery_page,
    iter_gallery_export_rows,
)
from ...repositories.gallery.sync_state import (
    count_gallery_r2_sync_rows,
    iter_gallery_r2_sync_rows,
    mark_gallery_r2_sync_state,
)
from ...repositories.image_files import (
    THUMBNAIL_CONTENT_TYPE,
    safe_image_path,
    safe_thumbnail_path,
)
from ...repositories.settings import load_r2_backup_settings
from ...repositories.thumbnail_jobs import (
    THUMBNAIL_JOB_LEASE_SECONDS,
    claim_next_thumbnail_job,
    complete_thumbnail_job,
    ensure_thumbnail_for_image,
    fail_thumbnail_job,
    generate_thumbnail_for_image,
)
from ...schemas.common import MessageResponse
from ...schemas.gallery import (
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
from ...schemas.nodeimage import NodeImageUploadJobStatus
from ...services.gallery_common import *
from ...services.gallery_jobs import *
from ...services.gallery_maintenance import (
    kick_gallery_job_dispatchers,
    kick_thumbnail_dispatcher,
)

router = APIRouter()

@router.post("/api/gallery/direct-export-jobs", response_model=GalleryExportJobStatus, status_code=202)
async def create_gallery_direct_export_job():
    gallery_count = await asyncio.to_thread(get_gallery_count)
    if gallery_count == 0:
        raise HTTPException(status_code=404, detail="No images in gallery")

    job = await _reserve_gallery_export_direct_slot(
        filename_prefix="gpt-images",
        requested_count=gallery_count,
        stage="queued",
        message="Waiting for direct gallery ZIP download",
        payload={"ids": None, "filename_prefix": "gpt-images"},
    )
    return GalleryExportJobStatus(**_gallery_export_payload(job))


@router.get("/api/gallery/direct-export-jobs/{job_id}", response_model=GalleryExportJobStatus)
async def get_gallery_direct_export_job(job_id: str):
    job = await asyncio.to_thread(get_gallery_job, "export_direct", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Direct gallery export job not found")
    return GalleryExportJobStatus(**_gallery_export_payload(job))


@router.post("/api/gallery/export-jobs", response_model=GalleryExportJobStatus, status_code=202)
async def create_gallery_export_job(req: GalleryExportRequest | None = Body(default=None)):
    ids = req.ids if req else None
    selection_token = req.selection_token if req else None
    if selection_token:
        filters = await _gallery_filters_from_selection_token(selection_token)
        requested_count = await asyncio.to_thread(get_gallery_count, filters)
        if requested_count <= 0:
            raise HTTPException(status_code=404, detail="Gallery entries not found")
        filename_prefix = "gpt-images-selected"
        payload = {
            "ids": None,
            "selection_token": selection_token,
            "filters": filters,
            "filename_prefix": filename_prefix,
        }
    elif ids:
        entries = await asyncio.to_thread(get_gallery_entries_by_ids, ids)
        if not entries:
            raise HTTPException(status_code=404, detail="Gallery entries not found")
        requested_count = len(ids)
        filename_prefix = "gpt-images-selected"
        payload = {"ids": ids, "filename_prefix": filename_prefix}
    else:
        gallery_count = await asyncio.to_thread(get_gallery_count)
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
    job = await asyncio.to_thread(get_gallery_job, "export", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Gallery export job not found")
    return GalleryExportJobStatus(**_gallery_export_payload(job))


@router.get("/api/gallery/export-jobs/{job_id}/events")
async def stream_gallery_export_job(job_id: str, request: Request):
    return await stream_gallery_job(
        kind="export",
        job_id=job_id,
        request=request,
        event_name="export",
        terminal_statuses=GALLERY_EXPORT_TERMINAL_STATUSES,
        payload_builder=_gallery_export_payload,
        not_found_detail="Gallery export job not found",
    )


@router.get("/api/gallery/direct-export-jobs/{job_id}/events")
async def stream_gallery_direct_export_job(job_id: str, request: Request):
    return await stream_gallery_job(
        kind="export_direct",
        job_id=job_id,
        request=request,
        event_name="export",
        terminal_statuses=GALLERY_EXPORT_TERMINAL_STATUSES,
        payload_builder=_gallery_export_payload,
        not_found_detail="Direct gallery export job not found",
    )


def _cleanup_downloaded_gallery_export_job(job_id: str) -> None:
    job = delete_gallery_job("export", job_id)
    if job and job.get("path"):
        _unlink_trusted_gallery_job_path(job["path"], kind="export", job_id=job_id)


def _gallery_export_download_headers(job: dict) -> dict[str, str]:
    return {
        "Content-Encoding": "identity",
        "Cache-Control": PRIVATE_GALLERY_CACHE_CONTROL,
        "X-Content-Type-Options": "nosniff",
        "X-Gallery-Requested-Count": str(job.get("requested_count") or 0),
        "X-Gallery-Exported-Count": str(job.get("exported_count") or 0),
        "X-Gallery-Missing-Count": str(job.get("missing_count") or 0),
    }


def _iter_tracked_export_file(path: Path, job_id: str) -> Iterator[bytes]:
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(EXPORT_FILE_STREAM_CHUNK_SIZE), b""):
                yield chunk
    finally:
        _cleanup_downloaded_gallery_export_job(job_id)


@router.get("/api/gallery/export-jobs/{job_id}/download")
async def download_gallery_export_job(job_id: str):
    job = await asyncio.to_thread(get_gallery_job, "export", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Gallery export job not found")
    if job.get("status") != "success":
        raise HTTPException(status_code=409, detail="Gallery export job is not ready")

    path = _resolve_trusted_gallery_job_path(job.get("path"), kind="export")
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Gallery export archive not found")

    filename = str(job.get("filename") or f"gpt-images-{job_id}.zip")
    headers = _gallery_export_download_headers(job)
    try:
        file_size = path.stat().st_size
    except OSError:
        file_size = 0

    if file_size >= TRACKED_EXPORT_STREAMING_BYTES_THRESHOLD:
        return StreamingResponse(
            _iter_tracked_export_file(path, job_id),
            media_type="application/zip",
            headers={
                **headers,
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    return FileResponse(
        path,
        media_type="application/zip",
        filename=filename,
        headers=headers,
        background=BackgroundTask(_cleanup_downloaded_gallery_export_job, job_id),
    )


@router.post("/api/gallery/sync-jobs", response_model=GallerySyncJobStatus, status_code=202)
async def create_gallery_sync_job(req: GallerySyncRequest | None = Body(default=None)):
    gallery_count = await asyncio.to_thread(get_gallery_count)
    if gallery_count == 0:
        raise HTTPException(status_code=404, detail="No images in gallery")

    r2_settings = await asyncio.to_thread(load_r2_backup_settings)
    try:
        effective = await asyncio.to_thread(
            r2_config.resolve_r2_backup_settings,
            r2_settings,
            require_enabled=True,
        )
    except r2_config.R2ConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    full_reconcile = bool(req.full_reconcile) if req else False
    dry_run = bool(req.dry_run) if req else False
    total_count = await asyncio.to_thread(
        count_gallery_r2_sync_rows,
        key_prefix=effective.key_prefix,
        full_reconcile=full_reconcile,
    )
    job = await _create_reserved_gallery_sync_job(
        total_count,
        {"full_reconcile": full_reconcile, "dry_run": dry_run},
    )
    kick_gallery_job_dispatchers()
    return GallerySyncJobStatus(**_gallery_sync_payload(job))


@router.get("/api/gallery/sync-jobs/{job_id}", response_model=GallerySyncJobStatus)
async def get_gallery_sync_job(job_id: str):
    job = await asyncio.to_thread(get_gallery_job, "sync", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Gallery sync job not found")
    return GallerySyncJobStatus(**_gallery_sync_payload(job))


@router.get("/api/gallery/sync-jobs/{job_id}/events")
async def stream_gallery_sync_job(job_id: str, request: Request):
    return await stream_gallery_job(
        kind="sync",
        job_id=job_id,
        request=request,
        event_name="sync",
        terminal_statuses=GALLERY_SYNC_TERMINAL_STATUSES,
        payload_builder=_gallery_sync_payload,
        not_found_detail="Gallery sync job not found",
    )


@router.get("/api/gallery/import-jobs/{job_id}", response_model=GalleryImportJobStatus)
async def get_gallery_import_job(job_id: str):
    job = await asyncio.to_thread(get_gallery_job, "import", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Gallery import job not found")
    return GalleryImportJobStatus(**_gallery_import_payload(job))


@router.get("/api/gallery/import-jobs/{job_id}/events")
async def stream_gallery_import_job(job_id: str, request: Request):
    return await stream_gallery_job(
        kind="import",
        job_id=job_id,
        request=request,
        event_name="import",
        terminal_statuses=GALLERY_IMPORT_TERMINAL_STATUSES,
        payload_builder=_gallery_import_payload,
        not_found_detail="Gallery import job not found",
    )


@router.get("/api/gallery/nodeimage-upload-jobs/{job_id}", response_model=NodeImageUploadJobStatus)
async def get_gallery_nodeimage_upload_job(job_id: str):
    job = await asyncio.to_thread(get_gallery_job, NODEIMAGE_UPLOAD_JOB_KIND, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="NodeImage upload job not found")
    return NodeImageUploadJobStatus(**_nodeimage_upload_payload(job))


@router.get("/api/gallery/nodeimage-upload-jobs/{job_id}/events")
async def stream_gallery_nodeimage_upload_job(job_id: str, request: Request):
    return await stream_gallery_job(
        kind=NODEIMAGE_UPLOAD_JOB_KIND,
        job_id=job_id,
        request=request,
        event_name="nodeimage_upload",
        terminal_statuses=NODEIMAGE_UPLOAD_TERMINAL_STATUSES,
        payload_builder=_nodeimage_upload_payload,
        not_found_detail="NodeImage upload job not found",
    )


async def _cancel_gallery_nodeimage_upload_job(job_id: str):
    job = await asyncio.to_thread(
        request_gallery_job_cancellation,
        NODEIMAGE_UPLOAD_JOB_KIND,
        job_id,
    )
    if not job:
        raise HTTPException(status_code=404, detail="NodeImage upload job not found")
    _publish_gallery_job_sse(job)
    return NodeImageUploadJobStatus(**_nodeimage_upload_payload(job))


@router.delete("/api/gallery/nodeimage-upload-jobs/{job_id}", response_model=NodeImageUploadJobStatus)
async def cancel_gallery_nodeimage_upload_job(job_id: str):
    return await _cancel_gallery_nodeimage_upload_job(job_id)


@router.post("/api/gallery/nodeimage-upload-jobs/{job_id}/cancel", response_model=NodeImageUploadJobStatus)
async def request_gallery_nodeimage_upload_cancellation(job_id: str):
    return await _cancel_gallery_nodeimage_upload_job(job_id)


@router.get("/api/download-all")
async def download_all_images(export_job_id: str = Query(...)):
    gallery_count = await asyncio.to_thread(get_gallery_count)
    direct_job = await asyncio.to_thread(
        get_gallery_job,
        "export_direct",
        export_job_id,
    )
    if not direct_job:
        raise HTTPException(status_code=404, detail="Direct gallery export job not found")
    if direct_job.get("status") != "running" or direct_job.get("stage") != "queued":
        raise HTTPException(status_code=409, detail="Direct gallery export job is already active or finished")

    if gallery_count == 0:
        await asyncio.to_thread(
            update_gallery_job,
            export_job_id,
            {
                "status": "error",
                "stage": "error",
                "message": "No images in gallery",
                "error": "No images in gallery",
                "completed_at": utc_now(),
                "lease_owner": None,
                "lease_expires_at": None,
            },
        )
        raise HTTPException(status_code=404, detail="No images in gallery")

    updated_direct_job = await asyncio.to_thread(
        update_gallery_job,
        export_job_id,
        {
            "status": "running",
            "stage": "preparing",
            "message": "Preparing gallery ZIP entries",
            "progress": 0,
            "requested_count": gallery_count,
            "started_at": utc_now(),
            "lease_expires_at": _direct_export_slot_expires_at(),
            "error": None,
        },
    )
    direct_job = updated_direct_job or direct_job

    return await _gallery_zip_response(
        iter_gallery_export_rows(),
        "gpt-images",
        reserve_export_slot=False,
        direct_export_job=direct_job,
        requested_count=gallery_count,
    )


@router.post("/api/import")
async def import_gallery_archive(
    request: Request,
    archive: UploadFile = File(...),
    async_job: bool = Query(default=False),
):
    del async_job
    reservation_id = uuid.uuid4().hex
    reservation_bytes = import_archive_max_bytes()
    lease_expires_at = (
        datetime.now(timezone.utc)
        + timedelta(seconds=config.IMPORT_UPLOAD_RESERVATION_TTL_SECONDS)
    ).isoformat()
    reserved, reason = await asyncio.to_thread(
        reserve_import_upload_capacity,
        reservation_id=reservation_id,
        client_ip=auth.get_client_ip(request),
        byte_count=reservation_bytes,
        max_total_bytes=config.IMPORT_TEMP_RESERVATION_MAX_MB * 1024 * 1024,
        per_ip_limit=config.IMPORT_UPLOADS_PER_IP_PER_MINUTE,
        lease_expires_at=lease_expires_at,
    )
    if not reserved:
        if reason == "ip_rate":
            raise HTTPException(status_code=429, detail="Too many import uploads from this IP")
        raise HTTPException(status_code=429, detail="Import temporary storage is full")

    import_dir = Path(config.DATA_DIR) / "imports"
    temp_path: Path | None = None
    try:
        temp_path = await stream_upload_to_tempfile(
            archive,
            reservation_bytes,
            directory=import_dir,
        )
        try:
            await asyncio.to_thread(
                resize_import_upload_reservation,
                reservation_id,
                temp_path.stat().st_size,
            )
        except OSError:
            pass
        total_count = await asyncio.to_thread(count_import_gallery_entries, temp_path)
        if total_count <= 0:
            raise HTTPException(status_code=400, detail="No importable images found")
        job = await _create_reserved_gallery_import_job(
            temp_path,
            total_count,
            {
                "filename": archive.filename or "",
                "reservation_id": reservation_id,
            },
        )
    except BaseException:
        await asyncio.to_thread(release_import_upload_reservation, reservation_id)
        if temp_path:
            temp_path.unlink(missing_ok=True)
        raise
    kick_gallery_job_dispatchers()
    return JSONResponse(
        status_code=202,
        content=GalleryImportJobStatus(**_gallery_import_payload(job)).model_dump(),
    )
