import asyncio
import hashlib
import inspect
import logging
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
from ...services.job_events import publish_queue, serialize_sse_event
from ..sse_limiter import sse_limiter
from ...core import security as auth
from ...core import settings as config
from ...core.observability import metrics
from ...core.utils import utc_now
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
    release_background_lease,
    reserve_gallery_job_capacity,
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
from ...repositories.settings import load_nodeimage_settings, load_r2_backup_settings
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
from ...services.gallery_maintenance import kick_gallery_job_dispatchers
from ...services.gallery_common import *
from ...services.gallery_jobs import *
from ...services.gallery_maintenance import kick_gallery_file_gc
from ...schemas.nodeimage import NodeImageUploadJobStatus

router = APIRouter()
logger = logging.getLogger(__name__)
NODEIMAGE_BATCH_MAX = 200


def _raise_nodeimage_batch_too_large(requested_count: int) -> None:
    raise HTTPException(
        status_code=422,
        detail=(
            "NodeImage batch upload supports at most "
            f"{NODEIMAGE_BATCH_MAX} images per request; selected "
            f"{requested_count}. Please split the selection into smaller batches."
        ),
    )


async def _resolve_nodeimage_batch_ids(
    req: GalleryBatchRequest,
) -> tuple[list[str], list[GalleryEntry], int, list[str]]:
    if req.selection_token:
        filters = await _gallery_filters_from_selection_token(req.selection_token)
        requested_count = await asyncio.to_thread(get_gallery_count, filters)
        if requested_count > NODEIMAGE_BATCH_MAX:
            _raise_nodeimage_batch_too_large(requested_count)
        if requested_count <= 0:
            return [], [], 0, []
        ids = await asyncio.to_thread(get_gallery_ids, filters)
        if len(ids) > NODEIMAGE_BATCH_MAX:
            _raise_nodeimage_batch_too_large(len(ids))
        entries = await asyncio.to_thread(get_gallery_entries_by_ids, ids)
        missing_ids = _missing_gallery_ids(ids, entries)
        return ids, entries, len(ids), missing_ids

    ids, entries, requested_count, missing_ids = await _resolve_gallery_batch_ids(req)
    if requested_count > NODEIMAGE_BATCH_MAX:
        _raise_nodeimage_batch_too_large(requested_count)
    return ids, entries, requested_count, missing_ids

@router.post("/api/gallery/batch/selection-tokens", response_model=GallerySelectionTokenResponse, status_code=201)
async def create_gallery_batch_selection_token(req: GallerySelectionTokenRequest):
    filters = build_gallery_filters_from_selection_request(req)
    count = await asyncio.to_thread(get_gallery_count, filters)
    if count <= 0:
        raise HTTPException(status_code=404, detail="No images match selection")

    await _cleanup_gallery_selection_tokens()
    token = f"sel-{os.urandom(16).hex()}"
    expires_at = _gallery_selection_token_expires_at()
    await asyncio.to_thread(
        create_gallery_job,
        job_id=token,
        kind=GALLERY_SELECTION_TOKEN_KIND,
        status="success",
        stage="ready",
        message="Gallery batch selection token",
        progress=100,
        requested_count=count,
        completed_at=utc_now(),
        payload={"filters": filters, "expires_at": expires_at},
    )
    return GallerySelectionTokenResponse(
        selection_token=token,
        count=count,
        expires_at=expires_at,
    )


@router.post("/api/gallery/batch/delete", response_model=GalleryBatchResponse)
async def delete_gallery_batch(req: GalleryBatchRequest):
    if req.selection_token:
        filters = await _gallery_filters_from_selection_token(req.selection_token)
        deleted_entries, deleted_files = await asyncio.to_thread(
            delete_gallery_images_by_filters,
            filters,
        )
        requested_count = deleted_entries
        missing_ids: list[str] = []
    else:
        ids, entries, requested_count, missing_ids = await _resolve_gallery_batch_ids(req)
        deleted_entries, deleted_files = await asyncio.to_thread(delete_gallery_images, ids)
    if deleted_entries == 0:
        raise HTTPException(status_code=404, detail="Gallery entries not found")
    kick_gallery_file_gc()
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
    if req.selection_token:
        filters = await _gallery_filters_from_selection_token(req.selection_token)
        updated_entries = await asyncio.to_thread(
            update_gallery_entries_favorite_by_filters,
            filters,
            req.favorite,
        )
        requested_count = updated_entries
        missing_ids: list[str] = []
    else:
        ids, entries, requested_count, missing_ids = await _resolve_gallery_batch_ids(req)
        updated_entries = await asyncio.to_thread(update_gallery_entries_favorite, ids, req.favorite)
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
    if req.selection_token:
        filters = await _gallery_filters_from_selection_token(req.selection_token)
        requested_count = await asyncio.to_thread(get_gallery_count, filters)
        if requested_count <= 0:
            raise HTTPException(status_code=404, detail="Gallery entries not found")
        return await _gallery_zip_response(
            iter_gallery_export_rows(filters),
            "gpt-images-selected",
            extra_headers={"X-Gallery-Requested-Count": str(requested_count)},
            reserve_export_slot=True,
            requested_count=requested_count,
            prepare_before_response=True,
        )

    ids, entries, requested_count, missing_ids = await _resolve_gallery_batch_ids(req)
    if not entries:
        raise HTTPException(status_code=404, detail="Gallery entries not found")

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
            "X-Gallery-Requested-Count": str(requested_count),
            "X-Gallery-Exported-Count": str(len(exportable_entries)),
            "X-Gallery-Missing-Count": str(len(skipped_entries)),
        },
        reserve_export_slot=True,
        requested_count=requested_count,
    )


@router.post(
    "/api/gallery/batch/nodeimage-upload",
    response_model=NodeImageUploadJobStatus,
    status_code=202,
)
async def upload_gallery_batch_to_nodeimage(req: GalleryBatchRequest):
    ids, entries, requested_count, missing_ids = await _resolve_nodeimage_batch_ids(req)
    if not entries:
        raise HTTPException(status_code=404, detail="Gallery entries not found")

    try:
        resolve_nodeimage_settings(
            await asyncio.to_thread(load_nodeimage_settings)
        )
    except NodeImageConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job = await _create_reserved_nodeimage_upload_job(
        ids,
        requested_count,
        missing_ids,
    )
    kick_gallery_job_dispatchers()
    return NodeImageUploadJobStatus(**_nodeimage_upload_payload(job))


@router.delete("/api/gallery", response_model=MessageResponse)
async def delete_all_gallery_handler():
    total, deleted_count = await asyncio.to_thread(delete_all_gallery_images)
    kick_gallery_file_gc()
    return MessageResponse(
        status="ok",
        message=f"Deleted {deleted_count} image file(s) and {total} gallery entries",
    )


@router.delete("/api/gallery/{image_id}", response_model=MessageResponse)
async def delete_gallery_item(image_id: str):
    deleted_entry, deleted_file_count = await asyncio.to_thread(delete_gallery_image, image_id)

    if not deleted_entry:
        raise HTTPException(status_code=404, detail="Gallery entry not found")

    kick_gallery_file_gc()
    return MessageResponse(
        status="ok",
        message=f"Deleted gallery entry and {deleted_file_count} image file(s)",
    )
