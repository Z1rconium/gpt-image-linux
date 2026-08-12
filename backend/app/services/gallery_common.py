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
from ..api.sse_limiter import sse_limiter
from ..core import security as auth
from ..core import settings as config
from ..core.observability import metrics
from ..core.utils import utc_now
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

def granian_worker_count() -> int:
    try:
        return max(1, int(os.getenv("GRANIAN_WORKERS", "1")))
    except (TypeError, ValueError):
        return 1
logger = logging.getLogger(__name__)
PRIVATE_GALLERY_CACHE_CONTROL = "private, no-store"
IMMUTABLE_GALLERY_CACHE_CONTROL = "private, max-age=31536000, immutable"
GALLERY_EXPORT_TERMINAL_STATUSES = {"success", "error"}
GALLERY_SYNC_TERMINAL_STATUSES = {"success", "error"}
GALLERY_IMPORT_TERMINAL_STATUSES = {"success", "error"}
MAX_ACTIVE_EXPORT_JOBS = 5
MAX_ACTIVE_SYNC_JOBS = 1
MAX_ACTIVE_IMPORT_JOBS = 1
EXPORT_JOB_TTL_SECONDS = 1800
EXPORT_JOB_GC_INTERVAL_SECONDS = 300
SYNC_JOB_TTL_SECONDS = 1800
IMPORT_JOB_TTL_SECONDS = 1800
SCHEDULED_R2_SYNC_DISABLED_POLL_SECONDS = 60
GALLERY_JOB_LEASE_SECONDS = 300
GALLERY_JOB_DISPATCH_INTERVAL_SECONDS = 0.5
GALLERY_JOB_DISPATCH_MAX_IDLE_BACKOFF_SECONDS = 5.0
THUMBNAIL_DISPATCH_INTERVAL_SECONDS = 1.0
THUMBNAIL_DISPATCH_MAX_IDLE_BACKOFF_SECONDS = 5.0
GALLERY_FILE_GC_INTERVAL_SECONDS = 300
BACKGROUND_TASK_LEASE_SECONDS = 120
BACKGROUND_TASK_ERROR_BACKOFF_INITIAL_SECONDS = 5.0
BACKGROUND_TASK_ERROR_BACKOFF_MAX_SECONDS = 60.0
GALLERY_PROGRESS_MIN_INTERVAL_SECONDS = 0.5
GALLERY_PROGRESS_MIN_ITEMS = 50
DIRECT_EXPORT_SLOT_LEASE_SECONDS = 6 * 3600
GALLERY_JOB_SSE_IDLE_CHECK_SECONDS = 1.0
GALLERY_JOB_SSE_QUEUE_MAXSIZE = 20
TRACKED_EXPORT_STREAMING_BYTES_THRESHOLD = 64 * 1024 * 1024
EXPORT_FILE_STREAM_CHUNK_SIZE = 1024 * 1024
GALLERY_SELECTION_TOKEN_KIND = "batch_selection"
GALLERY_SELECTION_TOKEN_TTL_SECONDS = 15 * 60
AI_ANALYZE_JOB_KIND = "ai_analyze"
AI_ANALYZE_JOB_TTL_SECONDS = 1800


def _trusted_gallery_job_dir(kind: str) -> Path:
    if kind == "import":
        return Path(config.DATA_DIR) / "imports"
    return Path(config.DATA_DIR) / "exports"


def _resolve_trusted_gallery_job_path(path_value, *, kind: str) -> Path | None:
    if not path_value:
        return None
    try:
        base_dir = _trusted_gallery_job_dir(kind).resolve()
        path = Path(str(path_value)).resolve()
        path.relative_to(base_dir)
    except (OSError, RuntimeError, ValueError):
        return None
    return path


def _unlink_trusted_gallery_job_path(path_value, *, kind: str, job_id: str | None = None) -> bool:
    path = _resolve_trusted_gallery_job_path(path_value, kind=kind)
    if not path:
        logger.warning(
            "Skipped cleanup for gallery %s job %s with untrusted path: %s",
            kind,
            job_id or "<unknown>",
            path_value,
        )
        return False
    path.unlink(missing_ok=True)
    return True


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
    path = safe_image_path(filename)
    if not path or not path.exists():
        return None
    if not is_gallery_filename_referenced(filename):
        return None
    return path


def _resolve_gallery_thumbnail_path(filename: str) -> Path | None:
    if not is_gallery_filename_referenced(filename):
        return None

    thumbnail_filename = ensure_thumbnail_for_image(filename)
    if not thumbnail_filename:
        return None

    path = safe_thumbnail_path(thumbnail_filename)
    if path and path.exists():
        return path

    invalidate_thumbnail_cache(thumbnail_filename)
    thumbnail_filename = ensure_thumbnail_for_image(filename)
    if not thumbnail_filename:
        return None

    path = safe_thumbnail_path(thumbnail_filename)
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
        "Cache-Control": IMMUTABLE_GALLERY_CACHE_CONTROL,
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
        path = safe_image_path(entry.filename)
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
            parsed_date = datetime.strptime(raw_value, "%Y-%m-%d")
        except ValueError as e:
            raise HTTPException(
                status_code=422,
                detail="Gallery date filters must use YYYY-MM-DD or ISO datetime",
            ) from e
        parsed = parsed_date.replace(
            hour=23 if end_of_day else 0,
            minute=59 if end_of_day else 0,
            second=59 if end_of_day else 0,
            microsecond=999999 if end_of_day else 0,
            tzinfo=timezone.utc,
        )
        return parsed.isoformat()

    try:
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail="Gallery date filters must use YYYY-MM-DD or ISO datetime",
        ) from e
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


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


def build_gallery_filters_from_selection_request(req: GallerySelectionTokenRequest) -> dict:
    filters = req.filters
    return build_gallery_filters(
        filters.prompt,
        filters.model,
        filters.preset,
        filters.size,
        filters.date_from,
        filters.date_to,
        filters.favorite,
    )


def _parse_gallery_token_timestamp(value: str | None) -> datetime | None:
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


def _gallery_selection_token_expires_at() -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=GALLERY_SELECTION_TOKEN_TTL_SECONDS)
    ).isoformat()


async def _cleanup_gallery_selection_tokens() -> None:
    await asyncio.to_thread(
        cleanup_stale_gallery_jobs,
        GALLERY_SELECTION_TOKEN_KIND,
        GALLERY_SELECTION_TOKEN_TTL_SECONDS,
    )


async def _gallery_filters_from_selection_token(selection_token: str | None) -> dict:
    token = str(selection_token or "").strip()
    if not token:
        raise HTTPException(status_code=422, detail="selection_token is required")

    job = await asyncio.to_thread(
        get_gallery_job,
        GALLERY_SELECTION_TOKEN_KIND,
        token,
    )
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


def _missing_gallery_ids(
    requested_ids: list[str],
    entries: list[GalleryEntry],
) -> list[str]:
    found_ids = {entry.id for entry in entries}
    return [image_id for image_id in requested_ids if image_id not in found_ids]


async def _resolve_gallery_batch_ids(req: GalleryBatchRequest) -> tuple[list[str], list[GalleryEntry], int, list[str]]:
    if req.ids:
        entries = await asyncio.to_thread(get_gallery_entries_by_ids, req.ids)
        missing_ids = _missing_gallery_ids(req.ids, entries)
        return req.ids, entries, len(req.ids), missing_ids

    filters = await _gallery_filters_from_selection_token(req.selection_token)
    ids = await asyncio.to_thread(get_gallery_ids, filters)
    if not ids:
        return [], [], 0, []
    entries = await asyncio.to_thread(get_gallery_entries_by_ids, ids)
    missing_ids = _missing_gallery_ids(ids, entries)
    return ids, entries, len(ids), missing_ids


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


def _progress_item_count(updates: dict) -> int:
    for key in ("processed_count", "compared_count", "exported_count", "uploaded_count"):
        if key not in updates:
            continue
        try:
            return int(updates.get(key) or 0)
        except (TypeError, ValueError):
            return 0
    return 0



__all__ = [name for name in globals() if not name.startswith("__")]
