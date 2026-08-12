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
    GallerySearchRequest,
    GallerySelectionTokenRequest,
    GallerySelectionTokenResponse,
    GallerySyncRequest,
    GallerySyncJobStatus,
    GalleryThumbnailState,
    GalleryThumbnailStatusRequest,
)
from ...services.gallery_common import *
from ...services.gallery_maintenance import kick_thumbnail_dispatcher

router = APIRouter()

async def _query_gallery(
    *,
    page: int,
    page_size: int,
    prompt: str | None,
    model: str | None,
    preset: str | None,
    size: str | None,
    date_from: str | None,
    date_to: str | None,
    favorite: bool | None,
    include_total_bytes: bool,
    include_counts: bool,
    include_filter_options: bool,
    cursor: str | None,
    direction: str,
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
    try:
        gallery_page = await asyncio.to_thread(
            get_gallery_page,
            page=page,
            page_size=page_size,
            filters=filters,
            include_total_bytes=include_total_bytes,
            include_counts=include_counts,
            include_filter_options=include_filter_options,
            cursor=cursor,
            direction=direction,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    metrics.increment("gallery.requests")
    metrics.observe_ms("gallery.request", elapsed_ms)
    metrics.observe_ms("gallery.db_query", gallery_page.query_elapsed_ms)
    for timing_name, timing_ms in gallery_page.timings_ms.items():
        if not timing_name.endswith("_ms"):
            continue
        metrics.observe_ms(f"gallery.{timing_name.removesuffix('_ms')}", timing_ms)
    if elapsed_ms >= config.SLOW_GALLERY_QUERY_MS:
        metrics.increment("gallery.slow_queries")
        metrics.increment("sqlite.slow_queries")
        logger.warning(
            "Slow /api/gallery query: elapsed_ms=%.2f db_query_ms=%.2f rows_ms=%.2f count_ms=%.2f total_bytes_ms=%.2f filter_options_ms=%.2f page=%s page_size=%s total=%s cursor=%s direction=%s include_counts=%s include_filter_options=%s filters=%s",
            elapsed_ms,
            gallery_page.query_elapsed_ms,
            gallery_page.timings_ms.get("rows_ms", 0.0),
            gallery_page.timings_ms.get("count_ms", 0.0),
            gallery_page.timings_ms.get("total_bytes_ms", 0.0),
            gallery_page.timings_ms.get("filter_options_ms", 0.0),
            gallery_page.page,
            gallery_page.page_size,
            gallery_page.total,
            bool(cursor),
            direction,
            gallery_page.counts_included,
            gallery_page.filter_options_included,
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
        next_cursor=gallery_page.next_cursor,
        prev_cursor=gallery_page.prev_cursor,
        images=gallery_page.images,
        filter_options=gallery_page.filter_options,
    )


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
    include_counts: bool = Query(default=True),
    include_filter_options: bool = Query(default=True),
    cursor: str | None = Query(default=None, max_length=512),
    direction: str = Query(default="next"),
):
    if prompt:
        raise HTTPException(status_code=422, detail="Prompt search requires POST JSON")
    return await _query_gallery(
        page=page,
        page_size=page_size,
        prompt=None,
        model=model,
        preset=preset,
        size=size,
        date_from=date_from,
        date_to=date_to,
        favorite=favorite,
        include_total_bytes=include_total_bytes,
        include_counts=include_counts,
        include_filter_options=include_filter_options,
        cursor=cursor,
        direction=direction,
    )


@router.post("/api/gallery/search", response_model=GalleryResponse)
async def search_gallery_handler(req: GallerySearchRequest):
    return await _query_gallery(
        page=req.page,
        page_size=req.page_size,
        prompt=req.prompt,
        model=req.model,
        preset=req.preset,
        size=req.size,
        date_from=req.date_from,
        date_to=req.date_to,
        favorite=req.favorite,
        include_total_bytes=req.include_total_bytes,
        include_counts=req.include_counts,
        include_filter_options=req.include_filter_options,
        cursor=req.cursor,
        direction=req.direction,
    )


@router.post("/api/gallery/thumbnails/status", response_model=list[GalleryThumbnailState])
async def get_gallery_thumbnail_statuses(req: GalleryThumbnailStatusRequest):
    entries = await asyncio.to_thread(get_gallery_entries_by_ids, req.ids)
    return [
        GalleryThumbnailState(
            id=entry.id,
            thumbnail_filename=entry.thumbnail_filename,
            thumbnail_url=entry.thumbnail_url,
            thumbnail_status=entry.thumbnail_status,
        )
        for entry in entries
    ]


@router.patch("/api/gallery/{image_id}/favorite", response_model=GalleryEntry)
async def update_gallery_favorite(
    image_id: str,
    req: GalleryFavoriteRequest,
):
    entry = await asyncio.to_thread(update_gallery_entry, image_id, {"favorite": req.favorite})
    if not entry:
        raise HTTPException(status_code=404, detail="Gallery entry not found")
    return entry


@router.get("/api/gallery/{image_id}", response_model=GalleryEntry)
async def get_gallery_item(image_id: str):
    entry = await asyncio.to_thread(get_gallery_entry, image_id)
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
            headers={"Cache-Control": IMMUTABLE_GALLERY_CACHE_CONTROL},
        )

    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": IMMUTABLE_GALLERY_CACHE_CONTROL},
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
        kick_thumbnail_dispatcher()
        raise HTTPException(
            status_code=404,
            detail="Thumbnail not found",
            headers={"Cache-Control": PRIVATE_GALLERY_CACHE_CONTROL},
        )

    if config.ENABLE_NGINX_ACCEL_REDIRECT:
        return _x_accel_response(
            path,
            internal_prefix="/_protected/thumbs/",
            media_type=THUMBNAIL_CONTENT_TYPE,
        )

    return FileResponse(
        path,
        media_type=THUMBNAIL_CONTENT_TYPE,
        headers={"Cache-Control": IMMUTABLE_GALLERY_CACHE_CONTROL},
    )


@router.get("/api/download/{filename}")
async def download_image(filename: str):
    return await _image_file_response(filename, download=True)
