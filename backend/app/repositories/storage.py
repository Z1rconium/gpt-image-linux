import asyncio
import base64
import binascii
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from urllib.parse import quote

from ..core import settings as config
from ..core.api_paths import default_model_for_api_path, normalize_api_preset
from ..core.constants import ACTIVE_GENERATE_JOB_STATUSES
from ..core.observability import metrics, observe_job_stage
from ..core.utils import utc_now
from ..core.validators import (
    get_env_var_ref_name,
    normalize_secret_env_ref_or_plaintext,
    normalize_r2_endpoint_url,
    normalize_socks5_proxy_url,
    normalize_webhook_url,
)
from ..schemas.models import GalleryEntry, GalleryFilterOptions, PromptSnippet
from .image_files import (
    IMAGE_CONTENT_TYPE_FORMATS,
    IMAGE_EXTENSION_FORMATS,
    IMAGE_FILE_EXTENSIONS,
    IMAGE_FORMAT_CONTENT_TYPES,
    THUMBNAIL_CONTENT_TYPE,
    THUMBNAIL_EXTENSION,
    delete_image_from_disk as _delete_image_unlocked,
    detect_image_format,
    generate_image_id,
    get_image_dimensions,
    image_dimension_metadata as _image_dimension_metadata,
    promote_image_temp as _promote_image_temp_unlocked,
    safe_image_path,
    safe_thumbnail_path,
    save_image_to_temp as _save_image_temp_unlocked,
    scan_image_files as _scan_image_files,
    validate_image_file,
    validate_image_header_bytes,
    validate_image_bytes,
)
from .thumbnails import (
    create_thumbnail_temp as _create_thumbnail_temp_unlocked,
    create_thumbnail_temp_from_path as _create_thumbnail_temp_from_path_unlocked,
    delete_thumbnail as _delete_thumbnail_unlocked,
    promote_thumbnail_temp as _promote_thumbnail_temp_unlocked,
    thumbnail_filename_for_image as _thumbnail_filename_for_image,
    thumbnail_url_for_filename as _thumbnail_url_for_filename,
)

logger = logging.getLogger(__name__)


class ImageJobQueueFullError(RuntimeError):
    """Raised when the SQLite-backed image unit queue has no remaining capacity."""


class EditSourceQueueFullError(RuntimeError):
    """Raised when pending edit source byte reservations exceed the configured cap."""


__all__ = [
    "IMAGE_CONTENT_TYPE_FORMATS",
    "IMAGE_EXTENSION_FORMATS",
    "IMAGE_FILE_EXTENSIONS",
    "IMAGE_FORMAT_CONTENT_TYPES",
    "THUMBNAIL_CONTENT_TYPE",
    "THUMBNAIL_EXTENSION",
    "GalleryEntry",
    "GalleryFilterOptions",
    "GalleryPage",
    "add_to_gallery_async",
    "add_to_gallery_sync",
    "acquire_background_lease",
    "acquire_background_slot",
    "acquire_sse_slot",
    "backfill_missing_gallery_bytes",
    "close_database_connections",
    "complete_background_lease",
    "clear_generate_job_history",
    "delete_all_gallery_images",
    "delete_gallery_image",
    "delete_gallery_images",
    "delete_gallery_images_by_filters",
    "detect_image_format",
    "ensure_thumbnail_for_image",
    "generate_image_id",
    "get_all_filenames",
    "get_all_gallery_ids",
    "get_gallery",
    "get_gallery_count",
    "get_gallery_ids",
    "get_gallery_entry",
    "get_gallery_entries_by_ids",
    "get_gallery_filter_options",
    "is_gallery_filename_referenced",
    "get_gallery_page",
    "decode_gallery_cursor",
    "encode_gallery_cursor",
    "get_gallery_total_bytes",
    "cleanup_orphan_gallery_files",
    "get_runtime_coordination_metrics",
    "create_gallery_job",
    "get_gallery_job",
    "get_gallery_jobs_updated_at_edges",
    "update_gallery_job",
    "update_gallery_job_progress",
    "count_active_gallery_jobs",
    "reserve_gallery_job_capacity",
    "claim_next_gallery_job",
    "cleanup_expired_gallery_jobs",
    "cleanup_stale_gallery_jobs",
    "delete_gallery_job",
    "count_active_sse_slots",
    "list_gallery_job_ids_with_files",
    "get_generate_job",
    "get_generate_job_updated_at_edge",
    "get_generate_jobs_list_updated_at_edge",
    "get_generate_jobs_updated_at_edges",
    "aggregate_image_job_units",
    "cancel_image_job_units",
    "claim_next_image_job_unit",
    "complete_image_job_unit",
    "create_image_job_units",
    "count_active_image_job_units",
    "count_pending_image_job_units",
    "enqueue_image_job",
    "EditSourceQueueFullError",
    "fail_image_job_unit",
    "get_pending_edit_source_bytes",
    "get_image_job_unit",
    "get_image_dimensions",
    "import_gallery_entries",
    "ImageJobQueueFullError",
    "iter_gallery_export_rows",
    "iter_gallery_r2_sync_rows",
    "count_gallery_r2_sync_rows",
    "mark_gallery_r2_sync_state",
    "list_generate_jobs",
    "load_prompt_optimizer_settings",
    "list_overall_config_values",
    "load_r2_backup_settings",
    "load_settings",
    "list_prompt_snippets",
    "mark_active_generate_jobs_interrupted",
    "mark_worker_heartbeat",
    "record_worker_metrics_snapshot",
    "refresh_sse_slot",
    "release_background_lease",
    "release_edit_source_reservation",
    "release_sse_slot",
    "release_background_slot",
    "claim_next_thumbnail_job",
    "complete_thumbnail_job",
    "create_prompt_snippet",
    "delete_prompt_snippet",
    "safe_image_path",
    "safe_thumbnail_path",
    "save_prompt_optimizer_settings",
    "save_overall_config_overrides",
    "save_r2_backup_settings",
    "save_settings",
    "sync_overall_config_env_values",
    "invalidate_thumbnail_cache",
    "enqueue_thumbnail_job",
    "fail_thumbnail_job",
    "generate_thumbnail_for_image",
    "get_pending_thumbnail_job_count",
    "image_url_for_filename",
    "rebuild_gallery_filter_options",
    "sync_gallery_with_image_files",
    "trim_generate_jobs",
    "update_gallery_entries_favorite",
    "update_gallery_entries_favorite_by_filters",
    "update_gallery_entry",
    "update_gallery_entry_hash",
    "update_prompt_snippet",
    "upsert_generate_job",
    "validate_image_file",
    "validate_image_header_bytes",
    "validate_image_bytes",
    "verify_storage_writable",
]

GALLERY_COLUMNS = (
    "id",
    "prompt",
    "size",
    "filename",
    "thumbnail_filename",
    "created_at",
    "completed_at",
    "image_width",
    "image_height",
    "model",
    "quality",
    "output_format",
    "output_compression",
    "response_format",
    "n",
    "api_path",
    "api_preset_name",
    "duration",
    "favorite",
    "bytes",
    "sha256",
    "sort_seq",
)
REQUIRED_GALLERY_COLUMNS = {"id", "prompt", "size", "filename", "created_at"}
_GALLERY_INTERNAL_COLUMNS = {"sort_seq"}
INTEGER_GALLERY_COLUMNS = {
    "image_width",
    "image_height",
    "output_compression",
    "n",
    "favorite",
    "bytes",
    "sort_seq",
}
GENERATE_JOB_COLUMNS = (
    "job_id",
    "status",
    "stage",
    "message",
    "operation",
    "prompt",
    "size",
    "created_at",
    "started_at",
    "completed_at",
    "updated_at",
    "model",
    "quality",
    "output_format",
    "output_compression",
    "response_format",
    "n",
    "api_path",
    "api_preset_name",
    "duration",
    "stage_timings_json",
    "image_id",
    "image_url",
    "images_json",
    "image_width",
    "image_height",
    "error",
)
IMAGE_JOB_UNIT_COLUMNS = (
    "unit_id",
    "parent_job_id",
    "operation",
    "unit_index",
    "status",
    "claimed_by",
    "claim_expires_at",
    "stage",
    "message",
    "error",
    "result_json",
    "stage_timings_json",
    "duration",
    "created_at",
    "started_at",
    "completed_at",
    "updated_at",
    "request_json",
    "edit_sources_json",
    "api_preset_id",
    "api_preset_name",
    "api_path",
)
GALLERY_JOB_COLUMNS = (
    "job_id",
    "kind",
    "status",
    "stage",
    "message",
    "progress",
    "filename",
    "download_url",
    "path",
    "requested_count",
    "processed_count",
    "exported_count",
    "missing_count",
    "total_count",
    "compared_count",
    "uploaded_count",
    "pending_upload_count",
    "skipped_existing_count",
    "missing_local_count",
    "failed_count",
    "bytes_total",
    "bytes_written",
    "bytes_uploaded",
    "created_at",
    "started_at",
    "completed_at",
    "updated_at",
    "error",
    "lease_owner",
    "lease_expires_at",
    "payload_json",
)
PROMPT_SNIPPET_COLUMNS = (
    "id",
    "title",
    "prompt",
    "favorite",
    "created_at",
    "updated_at",
)
INTEGER_GENERATE_JOB_COLUMNS = {
    "output_compression",
    "n",
    "image_width",
    "image_height",
}
SETTINGS_ACTIVE_PRESET_KEY = "active_preset_id"
UPSTREAM_SOCKS5_PROXY_KEY = "upstream_socks5_proxy"
WEBHOOK_URL_KEY = "webhook_url"
PROMPT_OPTIMIZER_SETTINGS_KEY = "prompt_optimizer_settings"
R2_BACKUP_SETTINGS_KEY = "r2_backup_settings"
SQLITE_TIMEOUT_SECONDS = 30.0
DATA_DIR_MODE = 0o700
DATA_FILE_MODE = 0o600
DATA_PERMISSION_CHECK_INTERVAL_SECONDS = 60.0
GALLERY_SYNC_BATCH_SIZE = 500
GALLERY_FTS_VERSION_KEY = "gallery_fts_version"
GALLERY_FTS_VERSION = "trigram-v1"
GALLERY_FTS_MIN_QUERY_LENGTH = 3
SQLITE_IN_CLAUSE_CHUNK_SIZE = 900
GALLERY_COUNT_CACHE_SECONDS = 2.0
GALLERY_TOTAL_BYTES_CACHE_SECONDS = 2.0
GALLERY_PAGE_ANCHOR_INTERVAL_PAGES = 100
GALLERY_PAGE_ANCHOR_SMALL_OFFSET_THRESHOLD = 10_000
GALLERY_PAGE_ANCHOR_MAX_PER_QUERY = 256
GALLERY_PAGE_ANCHOR_INVALIDATING_UPDATE_FIELDS = {
    "prompt",
    "model",
    "api_preset_name",
    "size",
    "favorite",
    "created_at",
    "sort_seq",
}
GALLERY_ORPHAN_FILE_TTL_SECONDS = 300
GALLERY_ORPHAN_GC_BATCH_SIZE = 500
_GALLERY_COUNT_CACHE_MAX_SIZE = 512
_GALLERY_BYTES_CACHE_MAX_SIZE = 512
THUMBNAIL_CPU_SLOT_LEASE_SECONDS = 600
THUMBNAIL_JOB_LEASE_SECONDS = 600
THUMBNAIL_JOB_MAX_ATTEMPTS = 3
WORKER_METRIC_SNAPSHOT_TTL_SECONDS = 300

_db_initialized = False
_db_init_lock = threading.RLock()
_storage_lock = threading.RLock()
_gallery_file_write_lock = threading.RLock()
_thread_local = threading.local()
_dirs_initialized = False
_last_permissions_check = -DATA_PERMISSION_CHECK_INTERVAL_SECONDS
_permissions_check_lock = threading.RLock()

_filter_options_cache: "_GalleryFilterOptionsCacheEntry | None" = None
_filter_options_cache_lock = threading.RLock()
_filter_options_cache_version: int = 0
_gallery_total_bytes_cache: OrderedDict[
    tuple[str, str, tuple[Any, ...]],
    tuple[float, int],
] = OrderedDict()
_gallery_total_bytes_cache_lock = threading.RLock()
_gallery_count_cache: OrderedDict[
    tuple[str, str, tuple[Any, ...]],
    tuple[float, int],
] = OrderedDict()
_gallery_count_cache_lock = threading.RLock()
_gallery_fts_available: bool | None = None

_verified_thumbnails: set[str] = set()
_verified_thumbnails_lock = threading.RLock()


def _add_verified_thumbnail(filename: str):
    with _verified_thumbnails_lock:
        _verified_thumbnails.add(filename)


def _remove_verified_thumbnail(filename: str):
    with _verified_thumbnails_lock:
        _verified_thumbnails.discard(filename)


def _clear_verified_thumbnails():
    with _verified_thumbnails_lock:
        _verified_thumbnails.clear()


def _unique_sqlite_values(values: Iterable[Any]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = "" if value is None else str(value)
        if not normalized.strip() or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _iter_sqlite_in_chunks(
    values: Iterable[Any],
    *,
    chunk_size: int | None = None,
) -> Iterator[list[str]]:
    unique_values = _unique_sqlite_values(values)
    normalized_chunk_size = max(1, int(chunk_size or SQLITE_IN_CLAUSE_CHUNK_SIZE))
    for start in range(0, len(unique_values), normalized_chunk_size):
        yield unique_values[start : start + normalized_chunk_size]


def _normalize_stored_api_key(value: str | None) -> str:
    return normalize_secret_env_ref_or_plaintext(
        value,
        field_name="API key",
    )


def _normalize_stored_socks5_proxy(value: str | None) -> str:
    return normalize_secret_env_ref_or_plaintext(
        value,
        field_name="SOCKS5 proxy URL",
        normalizer=normalize_socks5_proxy_url,
    )


def _normalize_stored_webhook_url(value: str | None) -> str:
    return normalize_secret_env_ref_or_plaintext(
        value,
        field_name="Webhook URL",
        normalizer=normalize_webhook_url,
    )


def _normalize_stored_r2_access_key_id(value: str | None) -> str:
    return normalize_secret_env_ref_or_plaintext(
        value,
        field_name="R2 access key ID",
    )


def _normalize_stored_r2_secret_access_key(value: str | None) -> str:
    return normalize_secret_env_ref_or_plaintext(
        value,
        field_name="R2 secret access key",
    )


def _chmod_path(path: Path, mode: int) -> None:
    if os.name == "nt" or not path.exists():
        return
    try:
        os.chmod(path, mode)
    except OSError as e:
        logger.warning("Failed to chmod %s to %#o: %s", path, mode, e)


def _secure_data_storage_permissions(*, force: bool = False) -> None:
    global _last_permissions_check
    if os.name == "nt":
        return

    now = time.monotonic()
    with _permissions_check_lock:
        if (
            not force
            and now - _last_permissions_check < DATA_PERMISSION_CHECK_INTERVAL_SECONDS
        ):
            return
        _last_permissions_check = now

    data_dir = Path(config.DATA_DIR)
    database_path = Path(config.DATABASE_FILE)
    for directory in {data_dir, database_path.parent}:
        _chmod_path(directory, DATA_DIR_MODE)
    for suffix in ("", "-wal", "-shm"):
        _chmod_path(Path(f"{database_path}{suffix}"), DATA_FILE_MODE)



@dataclass(frozen=True)
class GalleryPage:
    total: int
    total_bytes: int
    page: int
    page_size: int
    total_pages: int
    has_prev: bool
    has_next: bool
    next_cursor: str | None
    prev_cursor: str | None
    images: list[GalleryEntry]
    filter_options: GalleryFilterOptions
    query_elapsed_ms: float = 0.0
    timings_ms: dict[str, float] = field(default_factory=dict)
    counts_included: bool = True
    filter_options_included: bool = True


@dataclass
class _PreparedGalleryFile:
    filename: str
    image_temp_path: Path
    thumbnail_filename: str | None = None
    thumbnail_temp_path: Path | None = None


@dataclass(frozen=True)
class _GalleryFilterOptionsCacheEntry:
    version: int
    options: GalleryFilterOptions


@dataclass(frozen=True)
class _GalleryPaginationState:
    rows: list[sqlite3.Row]
    has_prev: bool
    has_next: bool
    effective_page: int
    total: int
    total_pages: int


@dataclass(frozen=True)
class _GalleryQueryComponents:
    where_sql: str
    params: list[Any]
    query_key: str
    requested_page: int
    page_size: int
    include_counts: bool
    include_filter_options: bool
    include_total_bytes: bool
    decoded_cursor: tuple[int, str] | None
    direction: str
    has_filters: bool


def _invalidate_filter_options_cache():
    global _filter_options_cache, _filter_options_cache_version
    with _filter_options_cache_lock:
        _filter_options_cache = None
        _filter_options_cache_version += 1


def _bump_filter_options_cache_version():
    global _filter_options_cache_version
    with _filter_options_cache_lock:
        _filter_options_cache_version += 1


def _get_filter_options_cache_version() -> int:
    with _filter_options_cache_lock:
        return _filter_options_cache_version


def _invalidate_gallery_total_bytes_cache():
    with _gallery_total_bytes_cache_lock:
        _gallery_total_bytes_cache.clear()


def _invalidate_gallery_count_cache():
    with _gallery_count_cache_lock:
        _gallery_count_cache.clear()


def _get_gallery_version_on_conn(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM gallery_meta WHERE key = 'gallery_version'"
    ).fetchone()
    if row:
        return int(row["value"] or 0)
    started_transaction = not conn.in_transaction
    if started_transaction:
        conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """
        INSERT OR IGNORE INTO gallery_meta (key, value)
        VALUES ('gallery_version', 0)
        """
    )
    if started_transaction:
        conn.commit()
    return 0


def _bump_gallery_version_on_conn(conn: sqlite3.Connection) -> int:
    conn.execute(
        """
        INSERT INTO gallery_meta (key, value)
        VALUES ('gallery_version', 1)
        ON CONFLICT(key) DO UPDATE SET value = value + 1
        """
    )
    row = conn.execute(
        "SELECT value FROM gallery_meta WHERE key = 'gallery_version'"
    ).fetchone()
    gallery_version = int(row["value"] or 0) if row else 0
    conn.execute(
        "DELETE FROM gallery_page_anchors WHERE gallery_version != ?",
        (gallery_version,),
    )
    return gallery_version


def _invalidate_gallery_query_caches():
    _invalidate_gallery_count_cache()
    _invalidate_gallery_total_bytes_cache()


def _invalidate_gallery_query_caches_on_conn(conn: sqlite3.Connection):
    _bump_gallery_version_on_conn(conn)
    _invalidate_gallery_query_caches()


def _default_settings() -> dict:
    return {
        "active_preset_id": "default",
        "upstream_socks5_proxy": _normalize_stored_socks5_proxy(
            config.DEFAULT_UPSTREAM_SOCKS5_PROXY
        ),
        "webhook_url": "",
        "presets": [
            {
                "id": "default",
                "name": "Default",
                "api_url": config.DEFAULT_API_URL.rstrip("/"),
                "api_key": _normalize_stored_api_key(config.DEFAULT_API_KEY),
                "api_path": config.DEFAULT_API_PATH,
                "default_model": default_model_for_api_path(config.DEFAULT_API_PATH),
                "default_response_format": "url",
            }
        ],
        "prompt_optimizer": _default_prompt_optimizer_settings(),
        "r2_backup": _default_r2_backup_settings(),
    }


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _default_prompt_optimizer_settings() -> dict:
    return {
        "enabled": config.PROMPT_OPTIMIZER_ENABLED,
        "api_url": config.PROMPT_OPTIMIZER_API_URL,
        "api_key": _normalize_stored_api_key(config.PROMPT_OPTIMIZER_API_KEY),
        "model": config.PROMPT_OPTIMIZER_MODEL,
        "timeout_seconds": config.PROMPT_OPTIMIZER_TIMEOUT_SECONDS,
    }


def _default_r2_secret_ref(env_var: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    env_ref = get_env_var_ref_name(normalized)
    if env_ref:
        return f"${{{env_ref}}}"
    return f"${{{env_var}}}"


def _normalize_r2_key_prefix(value: Any, default: str = "gallery/") -> str:
    raw = str(value if value is not None else default).strip()
    if not raw:
        return ""
    parts = [part for part in raw.strip("/").split("/") if part]
    return f"{'/'.join(parts)}/" if parts else ""


def _default_r2_backup_settings() -> dict:
    return {
        "enabled": config.R2_BACKUP_ENABLED,
        "endpoint_url": normalize_r2_endpoint_url(config.R2_ENDPOINT_URL),
        "bucket_name": config.R2_BUCKET_NAME,
        "region": config.R2_REGION or "auto",
        "key_prefix": _normalize_r2_key_prefix(config.R2_KEY_PREFIX),
        "access_key_id": _default_r2_secret_ref(
            "R2_ACCESS_KEY_ID",
            config.R2_ACCESS_KEY_ID,
        ),
        "secret_access_key": _default_r2_secret_ref(
            "R2_SECRET_ACCESS_KEY",
            config.R2_SECRET_ACCESS_KEY,
        ),
        "sync_interval_hours": config.R2_SYNC_INTERVAL_HOURS,
    }


def _coerce_positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _coerce_non_negative_int(value, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, float) and not value.is_integer():
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed >= 0 else 0


def _coerce_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_prompt_optimizer_settings(settings: dict | None) -> dict:
    default = _default_prompt_optimizer_settings()
    if not isinstance(settings, dict):
        return default
    return {
        "enabled": _coerce_bool(settings.get("enabled"), default["enabled"]),
        "api_url": str(settings.get("api_url") or "").strip(),
        "api_key": _normalize_stored_api_key(settings.get("api_key")),
        "model": str(settings.get("model") or default["model"]).strip()
        or default["model"],
        "timeout_seconds": _coerce_positive_int(
            settings.get("timeout_seconds"),
            default["timeout_seconds"],
        ),
    }


def _normalize_r2_backup_settings(settings: dict | None) -> dict:
    default = _default_r2_backup_settings()
    if not isinstance(settings, dict):
        return default
    endpoint_url = normalize_r2_endpoint_url(settings.get("endpoint_url") or "")
    bucket_name = str(settings.get("bucket_name") or "").strip()
    access_key_id = _normalize_stored_r2_access_key_id(settings.get("access_key_id"))
    secret_access_key = _normalize_stored_r2_secret_access_key(
        settings.get("secret_access_key")
    )
    return {
        "enabled": default["enabled"]
        or _coerce_bool(settings.get("enabled"), default["enabled"]),
        "endpoint_url": endpoint_url or default["endpoint_url"],
        "bucket_name": bucket_name or default["bucket_name"],
        "region": str(settings.get("region") or default["region"]).strip()
        or default["region"],
        "key_prefix": _normalize_r2_key_prefix(
            settings.get("key_prefix"),
            default["key_prefix"],
        ),
        "access_key_id": access_key_id or default["access_key_id"],
        "secret_access_key": secret_access_key or default["secret_access_key"],
        "sync_interval_hours": _coerce_non_negative_int(
            settings.get("sync_interval_hours"),
            default["sync_interval_hours"],
        ),
    }


def _has_r2_backup_storage_values(settings: dict | None) -> bool:
    if not isinstance(settings, dict):
        return False
    if _coerce_bool(settings.get("enabled"), False):
        return True
    if "sync_interval_hours" in settings:
        try:
            if int(settings.get("sync_interval_hours") or 0) > 0:
                return True
        except (TypeError, ValueError):
            return True
    return any(
        str(settings.get(key) or "").strip()
        for key in (
            "endpoint_url",
            "bucket_name",
            "access_key_id",
            "secret_access_key",
        )
    )


def _store_r2_backup_settings_on_conn(conn: sqlite3.Connection, settings: dict):
    _set_setting_value(conn, R2_BACKUP_SETTINGS_KEY, json.dumps(settings))
    conn.commit()
    _secure_data_storage_permissions()


def _load_r2_backup_settings_from_conn(conn: sqlite3.Connection) -> dict:
    raw = _get_setting_value(conn, R2_BACKUP_SETTINGS_KEY)
    if raw:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return _default_r2_backup_settings()

        settings = _normalize_r2_backup_settings(parsed)
        if (
            not _has_r2_backup_storage_values(parsed)
            and _has_r2_backup_storage_values(settings)
        ):
            _store_r2_backup_settings_on_conn(conn, settings)
        return settings

    settings = _default_r2_backup_settings()
    if _has_r2_backup_storage_values(settings):
        _store_r2_backup_settings_on_conn(conn, settings)
    return settings


def _ensure_directories():
    global _dirs_initialized
    if _dirs_initialized:
        return
    Path(config.IMAGES_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.THUMBNAILS_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.DATABASE_FILE).parent.mkdir(parents=True, exist_ok=True)
    _secure_data_storage_permissions(force=True)
    _dirs_initialized = True


def _check_directory_writable(path: Path):
    test_file = path / ".write-test"
    try:
        with open(test_file, "wb") as f:
            f.write(b"ok")
        test_file.unlink()
    except OSError as e:
        uid = os.getuid()
        gid = os.getgid()
        absolute_path = path.resolve()
        raise PermissionError(
            f"Directory is not writable: {absolute_path} "
            f"(process uid={uid}, gid={gid}). Original error: {e}"
        ) from e


def verify_storage_writable():
    _ensure_directories()
    _check_directory_writable(Path(config.IMAGES_DIR))
    _check_directory_writable(Path(config.THUMBNAILS_DIR))
    _check_directory_writable(Path(config.DATA_DIR))
    _ensure_database()


def _open_connection(
    *,
    timeout: float = SQLITE_TIMEOUT_SECONDS,
    busy_timeout_ms: int = 30000,
) -> sqlite3.Connection:
    _ensure_directories()
    conn = sqlite3.connect(config.DATABASE_FILE, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = _get_thread_connection()
    depth = int(getattr(_thread_local, "connection_depth", 0))
    _thread_local.connection_depth = depth + 1
    try:
        yield conn
    except Exception:
        if depth == 0 and conn.in_transaction:
            conn.rollback()
        raise
    finally:
        _thread_local.connection_depth = depth
        if depth == 0:
            _close_thread_connection()


def _get_thread_connection() -> sqlite3.Connection:
    database_file = str(config.DATABASE_FILE)
    conn = getattr(_thread_local, "conn", None)
    conn_database_file = getattr(_thread_local, "database_file", None)
    if (
        conn is not None
        and conn_database_file == database_file
        and Path(database_file).exists()
    ):
        return conn

    _close_thread_connection()
    conn = _open_connection()
    _thread_local.conn = conn
    _thread_local.database_file = database_file
    _thread_local.connection_depth = 0
    return conn


def _close_thread_connection():
    conn = getattr(_thread_local, "conn", None)
    if conn is None:
        return
    try:
        if conn.in_transaction:
            conn.rollback()
        conn.close()
    finally:
        _thread_local.conn = None
        _thread_local.database_file = None
        _thread_local.connection_depth = 0


def close_database_connections():
    """Close this thread's active SQLite connection and clear storage caches."""
    _close_thread_connection()
    _clear_verified_thumbnails()
    _invalidate_filter_options_cache()
    _invalidate_gallery_query_caches()



@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as e:
        message = str(e).lower()
        if "locked" in message or "busy" in message:
            metrics.increment("sqlite.busy")
        raise
    try:
        yield
    except Exception as e:
        if isinstance(e, sqlite3.OperationalError):
            message = str(e).lower()
            if "locked" in message or "busy" in message:
                metrics.increment("sqlite.busy")
        conn.rollback()
        raise
    else:
        conn.commit()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _reset_gallery_fts_on_conn(conn: sqlite3.Connection):
    conn.executescript(
        """
        DROP TRIGGER IF EXISTS gallery_entries_fts_ai;
        DROP TRIGGER IF EXISTS gallery_entries_fts_ad;
        DROP TRIGGER IF EXISTS gallery_entries_fts_au;
        DROP TABLE IF EXISTS gallery_entries_fts;
        """
    )


def _ensure_gallery_fts(conn: sqlite3.Connection):
    global _gallery_fts_available

    fts_exists = _table_exists(conn, "gallery_entries_fts")
    needs_rebuild = (
        not fts_exists
        or _get_setting_value(conn, GALLERY_FTS_VERSION_KEY) != GALLERY_FTS_VERSION
    )

    try:
        if needs_rebuild:
            _reset_gallery_fts_on_conn(conn)

        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS gallery_entries_fts
            USING fts5(
                prompt,
                content='gallery_entries',
                content_rowid='rowid',
                tokenize='trigram'
            )
            """
        )
        conn.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS gallery_entries_fts_ai
            AFTER INSERT ON gallery_entries BEGIN
                INSERT INTO gallery_entries_fts(rowid, prompt)
                VALUES (new.rowid, new.prompt);
            END;

            CREATE TRIGGER IF NOT EXISTS gallery_entries_fts_ad
            AFTER DELETE ON gallery_entries BEGIN
                INSERT INTO gallery_entries_fts(gallery_entries_fts, rowid, prompt)
                VALUES ('delete', old.rowid, old.prompt);
            END;

            CREATE TRIGGER IF NOT EXISTS gallery_entries_fts_au
            AFTER UPDATE OF prompt ON gallery_entries BEGIN
                INSERT INTO gallery_entries_fts(gallery_entries_fts, rowid, prompt)
                VALUES ('delete', old.rowid, old.prompt);
                INSERT INTO gallery_entries_fts(rowid, prompt)
                VALUES (new.rowid, new.prompt);
            END;
            """
        )
        if needs_rebuild:
            conn.execute("INSERT INTO gallery_entries_fts(gallery_entries_fts) VALUES ('rebuild')")
            _set_setting_value(conn, GALLERY_FTS_VERSION_KEY, GALLERY_FTS_VERSION)
        _gallery_fts_available = True
    except sqlite3.OperationalError as e:
        _gallery_fts_available = False
        logger.warning("SQLite FTS5 prompt search unavailable; falling back to LIKE: %s", e)


def _ensure_database():
    global _db_initialized
    if _db_initialized and Path(config.DATABASE_FILE).exists():
        return

    with _db_init_lock:
        if _db_initialized and Path(config.DATABASE_FILE).exists():
            return

        with _connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings_kv (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS api_presets (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    api_url TEXT NOT NULL,
                    api_key TEXT NOT NULL,
                    api_path TEXT NOT NULL,
                    default_model TEXT NOT NULL,
                    default_response_format TEXT NOT NULL DEFAULT 'url',
                    position INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS gallery_entries (
                    id TEXT PRIMARY KEY,
                    prompt TEXT NOT NULL,
                    size TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    thumbnail_filename TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    image_width INTEGER,
                    image_height INTEGER,
                    model TEXT,
                    quality TEXT,
                    output_format TEXT,
                    output_compression INTEGER,
                    response_format TEXT,
                    n INTEGER,
                    api_path TEXT,
                    api_preset_name TEXT,
                    duration TEXT,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    bytes INTEGER,
                    sha256 TEXT,
                    sort_seq INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_gallery_entries_filename
                    ON gallery_entries(filename);

                CREATE TABLE IF NOT EXISTS gallery_filter_options (
                    kind TEXT NOT NULL,
                    value TEXT NOT NULL,
                    ref_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(kind, value)
                );

                CREATE TABLE IF NOT EXISTS gallery_meta (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS gallery_page_anchors (
                    query_key TEXT NOT NULL,
                    page_size INTEGER NOT NULL,
                    page INTEGER NOT NULL,
                    sort_seq INTEGER NOT NULL,
                    image_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    gallery_version INTEGER NOT NULL,
                    PRIMARY KEY(query_key, page_size, page)
                );

                CREATE INDEX IF NOT EXISTS idx_gallery_page_anchors_lookup
                    ON gallery_page_anchors(query_key, page_size, gallery_version, page);

                CREATE TABLE IF NOT EXISTS thumbnail_jobs (
                    filename TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_thumbnail_jobs_claim
                    ON thumbnail_jobs(status, lease_expires_at, created_at);

                CREATE TABLE IF NOT EXISTS r2_sync_state (
                    filename TEXT PRIMARY KEY,
                    sha256 TEXT,
                    bytes INTEGER NOT NULL DEFAULT 0,
                    key TEXT NOT NULL,
                    etag TEXT,
                    last_remote_seen_at TEXT,
                    synced_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_r2_sync_state_key
                    ON r2_sync_state(key);

                CREATE TABLE IF NOT EXISTS generate_jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    stage TEXT,
                    message TEXT,
                    operation TEXT,
                    prompt TEXT,
                    size TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL,
                    model TEXT,
                    quality TEXT,
                    output_format TEXT,
                    output_compression INTEGER,
                    response_format TEXT,
                    n INTEGER,
                    api_path TEXT,
                    api_preset_name TEXT,
                    duration TEXT,
                    stage_timings_json TEXT,
                    image_id TEXT,
                    image_url TEXT,
                    images_json TEXT,
                    image_width INTEGER,
                    image_height INTEGER,
                    error TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_generate_jobs_status_updated_at
                    ON generate_jobs(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_generate_jobs_updated_at
                    ON generate_jobs(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_generate_jobs_seek
                    ON generate_jobs(updated_at DESC, job_id DESC);

                CREATE TABLE IF NOT EXISTS image_job_units (
                    unit_id TEXT PRIMARY KEY,
                    parent_job_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    unit_index INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    claimed_by TEXT,
                    claim_expires_at TEXT,
                    stage TEXT,
                    message TEXT,
                    error TEXT,
                    result_json TEXT,
                    stage_timings_json TEXT,
                    duration TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    edit_sources_json TEXT,
                    api_preset_id TEXT,
                    api_preset_name TEXT,
                    api_path TEXT,
                    UNIQUE(parent_job_id, unit_index)
                );

                DROP INDEX IF EXISTS idx_image_job_units_claim;
                CREATE INDEX IF NOT EXISTS idx_image_job_units_claim_queued
                    ON image_job_units(created_at, unit_index)
                    WHERE status = 'queued';
                CREATE INDEX IF NOT EXISTS idx_image_job_units_claim_running_expired
                    ON image_job_units(claim_expires_at, created_at, unit_index)
                    WHERE status = 'running' AND claim_expires_at IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_image_job_units_running_count
                    ON image_job_units(status)
                    WHERE status = 'running';
                CREATE INDEX IF NOT EXISTS idx_image_job_units_parent
                    ON image_job_units(parent_job_id, unit_index);
                CREATE INDEX IF NOT EXISTS idx_image_job_units_worker
                    ON image_job_units(claimed_by, status);

                CREATE TABLE IF NOT EXISTS gallery_jobs (
                    job_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT,
                    message TEXT,
                    progress INTEGER NOT NULL DEFAULT 0,
                    filename TEXT,
                    download_url TEXT,
                    path TEXT,
                    requested_count INTEGER NOT NULL DEFAULT 0,
                    processed_count INTEGER NOT NULL DEFAULT 0,
                    exported_count INTEGER NOT NULL DEFAULT 0,
                    missing_count INTEGER NOT NULL DEFAULT 0,
                    total_count INTEGER NOT NULL DEFAULT 0,
                    compared_count INTEGER NOT NULL DEFAULT 0,
                    uploaded_count INTEGER NOT NULL DEFAULT 0,
                    pending_upload_count INTEGER NOT NULL DEFAULT 0,
                    skipped_existing_count INTEGER NOT NULL DEFAULT 0,
                    missing_local_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    bytes_total INTEGER NOT NULL DEFAULT 0,
                    bytes_written INTEGER NOT NULL DEFAULT 0,
                    bytes_uploaded INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL,
                    error TEXT,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    payload_json TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_gallery_jobs_claim
                    ON gallery_jobs(kind, status, lease_expires_at, created_at);
                CREATE INDEX IF NOT EXISTS idx_gallery_jobs_active_count
                    ON gallery_jobs(kind, status, lease_expires_at);
                CREATE INDEX IF NOT EXISTS idx_gallery_jobs_terminal_gc
                    ON gallery_jobs(kind, status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_gallery_jobs_kind_updated
                    ON gallery_jobs(kind, updated_at DESC);

                CREATE TABLE IF NOT EXISTS worker_heartbeats (
                    worker_id TEXT PRIMARY KEY,
                    last_seen_at TEXT NOT NULL,
                    active_units INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS sse_slots (
                    connection_id TEXT PRIMARY KEY,
                    client_ip TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    lease_expires_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_sse_slots_lease
                    ON sse_slots(lease_expires_at);
                CREATE INDEX IF NOT EXISTS idx_sse_slots_client
                    ON sse_slots(client_ip, lease_expires_at);

                CREATE TABLE IF NOT EXISTS background_leases (
                    name TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    lease_expires_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_background_leases_expires
                    ON background_leases(lease_expires_at);

                CREATE TABLE IF NOT EXISTS worker_metric_snapshots (
                    worker_id TEXT PRIMARY KEY,
                    snapshot_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_worker_metric_snapshots_updated_at
                    ON worker_metric_snapshots(updated_at DESC);

                CREATE TABLE IF NOT EXISTS edit_source_reservations (
                    job_id TEXT PRIMARY KEY,
                    byte_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS prompt_snippets (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS overall_config_values (
                    name TEXT PRIMARY KEY,
                    env_value TEXT NOT NULL DEFAULT '',
                    override_value TEXT,
                    is_env_set INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    override_updated_at TEXT
                );

                """
            )
            _migrate_api_presets_schema(conn)
            _migrate_gallery_schema(conn)
            _migrate_generate_jobs_schema(conn)
            _migrate_gallery_jobs_schema(conn)
            _migrate_r2_sync_state_schema(conn)
            _migrate_prompt_snippets_schema(conn)
            _run_schema_migrations(conn)
            _ensure_gallery_fts(conn)
            conn.commit()

        _db_initialized = True
        _secure_data_storage_permissions(force=True)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def _migration_baseline_legacy_schema(conn: sqlite3.Connection):
    # v1 marks databases that already passed the historical inline schema setup.
    return


def _migration_gallery_filter_options(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gallery_filter_options (
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            ref_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(kind, value)
        )
        """
    )
    _rebuild_gallery_filter_options_on_conn(conn)


def _migration_thumbnail_jobs(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS thumbnail_jobs (
            filename TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            lease_owner TEXT,
            lease_expires_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            error TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_thumbnail_jobs_claim
            ON thumbnail_jobs(status, lease_expires_at, created_at)
        """
    )


def _migration_gallery_keyset_index(conn: sqlite3.Connection):
    conn.execute(
        """
        UPDATE gallery_entries
        SET sort_seq = rowid
        WHERE sort_seq IS NULL
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_sort_seq_id
            ON gallery_entries(sort_seq DESC, id DESC)
        """
    )


def _migration_gallery_sort_filter_indexes(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_favorite_sort_seq_id
            ON gallery_entries(favorite, sort_seq DESC, id DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_model_sort_seq_id
            ON gallery_entries(model, sort_seq DESC, id DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_preset_sort_seq_id
            ON gallery_entries(api_preset_name, sort_seq DESC, id DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_size_sort_seq_id
            ON gallery_entries(size, sort_seq DESC, id DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_created_at_sort_seq_id
            ON gallery_entries(created_at DESC, sort_seq DESC, id DESC)
        """
    )


def _migration_gallery_page_anchors(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gallery_meta (
            key TEXT PRIMARY KEY,
            value INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO gallery_meta (key, value)
        VALUES ('gallery_version', 0)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gallery_page_anchors (
            query_key TEXT NOT NULL,
            page_size INTEGER NOT NULL,
            page INTEGER NOT NULL,
            sort_seq INTEGER NOT NULL,
            image_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            gallery_version INTEGER NOT NULL,
            PRIMARY KEY(query_key, page_size, page)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_page_anchors_lookup
            ON gallery_page_anchors(query_key, page_size, gallery_version, page)
        """
    )


SCHEMA_MIGRATIONS = (
    (1, "baseline_legacy_schema", _migration_baseline_legacy_schema),
    (2, "gallery_filter_options", _migration_gallery_filter_options),
    (3, "thumbnail_jobs", _migration_thumbnail_jobs),
    (4, "gallery_keyset_index", _migration_gallery_keyset_index),
    (5, "gallery_sort_filter_indexes", _migration_gallery_sort_filter_indexes),
    (6, "gallery_page_anchors", _migration_gallery_page_anchors),
)


def _run_schema_migrations(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    applied_versions = {int(row["version"]) for row in rows}
    now = utc_now()

    if not applied_versions:
        conn.execute(
            """
            INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
            VALUES (?, ?, ?)
            """,
            (1, "baseline_legacy_schema", now),
        )
        applied_versions.add(1)

    for version, name, migration in SCHEMA_MIGRATIONS:
        if version in applied_versions:
            continue
        migration(conn)
        conn.execute(
            """
            INSERT INTO schema_migrations (version, name, applied_at)
            VALUES (?, ?, ?)
            """,
            (version, name, utc_now()),
        )


def _migrate_gallery_schema(conn: sqlite3.Connection):
    columns = _table_columns(conn, "gallery_entries")
    if "favorite" not in columns:
        conn.execute(
            "ALTER TABLE gallery_entries ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0"
        )
    if "bytes" not in columns:
        conn.execute("ALTER TABLE gallery_entries ADD COLUMN bytes INTEGER")
    if "thumbnail_filename" not in columns:
        conn.execute("ALTER TABLE gallery_entries ADD COLUMN thumbnail_filename TEXT")
    if "completed_at" not in columns:
        conn.execute("ALTER TABLE gallery_entries ADD COLUMN completed_at TEXT")
    if "sha256" not in columns:
        conn.execute("ALTER TABLE gallery_entries ADD COLUMN sha256 TEXT")
    if "sort_seq" not in columns:
        conn.execute("ALTER TABLE gallery_entries ADD COLUMN sort_seq INTEGER")
        conn.execute(
            """
            UPDATE gallery_entries
            SET sort_seq = rowid
            WHERE sort_seq IS NULL
            """
        )
        conn.execute("DROP INDEX IF EXISTS idx_gallery_entries_created_at")
        conn.execute("DROP INDEX IF EXISTS idx_gallery_entries_model_created_at")
        conn.execute("DROP INDEX IF EXISTS idx_gallery_entries_preset_created_at")
        conn.execute("DROP INDEX IF EXISTS idx_gallery_entries_size_created_at")
        conn.execute("DROP INDEX IF EXISTS idx_gallery_entries_favorite_created_at")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_sort_seq
            ON gallery_entries(sort_seq DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_sort_seq_id
            ON gallery_entries(sort_seq DESC, id DESC)
        """
    )
    if "favorite" in _table_columns(conn, "gallery_entries"):
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_favorite_created_at
                ON gallery_entries(favorite, created_at DESC, sort_seq DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_favorite_sort_seq_id
                ON gallery_entries(favorite, sort_seq DESC, id DESC)
            """
        )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_model_created_at
            ON gallery_entries(model, created_at DESC, sort_seq DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_model_sort_seq_id
            ON gallery_entries(model, sort_seq DESC, id DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_preset_created_at
            ON gallery_entries(api_preset_name, created_at DESC, sort_seq DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_preset_sort_seq_id
            ON gallery_entries(api_preset_name, sort_seq DESC, id DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_size_created_at
            ON gallery_entries(size, created_at DESC, sort_seq DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_size_sort_seq_id
            ON gallery_entries(size, sort_seq DESC, id DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_created_at
            ON gallery_entries(created_at DESC, sort_seq DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_created_at_sort_seq_id
            ON gallery_entries(created_at DESC, sort_seq DESC, id DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_missing_bytes_filename
            ON gallery_entries(filename) WHERE bytes IS NULL
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_filename_bytes
            ON gallery_entries(filename, bytes) WHERE bytes IS NOT NULL
        """
    )



def _migrate_api_presets_schema(conn: sqlite3.Connection):
    columns = _table_columns(conn, "api_presets")
    if "default_model" not in columns:
        conn.execute("ALTER TABLE api_presets ADD COLUMN default_model TEXT")
    if "default_response_format" not in columns:
        conn.execute(
            "ALTER TABLE api_presets ADD COLUMN default_response_format TEXT NOT NULL DEFAULT 'url'"
        )
    conn.execute(
        """
        UPDATE api_presets
        SET default_model = CASE
            WHEN api_path = ? AND ? != '' THEN ?
            ELSE ?
        END
        WHERE default_model IS NULL OR trim(default_model) = ''
        """,
        (
            "/v1/responses",
            str(config.DEFAULT_RESPONSES_MODEL or "").strip(),
            str(config.DEFAULT_RESPONSES_MODEL or "").strip(),
            default_model_for_api_path("/v1/images/generations"),
        ),
    )
    conn.execute(
        """
        UPDATE api_presets
        SET default_response_format = ?
        WHERE default_response_format IS NULL
            OR trim(default_response_format) NOT IN ('', 'url', 'b64_json')
        """,
        ("url",),
    )


def _migrate_generate_jobs_schema(conn: sqlite3.Connection):
    columns = _table_columns(conn, "generate_jobs")
    if "stage_timings_json" not in columns:
        conn.execute("ALTER TABLE generate_jobs ADD COLUMN stage_timings_json TEXT")
    if "images_json" not in columns:
        conn.execute("ALTER TABLE generate_jobs ADD COLUMN images_json TEXT")


def _migrate_gallery_jobs_schema(conn: sqlite3.Connection):
    columns = _table_columns(conn, "gallery_jobs")
    if "pending_upload_count" not in columns:
        conn.execute(
            "ALTER TABLE gallery_jobs ADD COLUMN pending_upload_count INTEGER NOT NULL DEFAULT 0"
        )


def _migrate_r2_sync_state_schema(conn: sqlite3.Connection):
    columns = _table_columns(conn, "r2_sync_state")
    if "etag" not in columns:
        conn.execute("ALTER TABLE r2_sync_state ADD COLUMN etag TEXT")
    if "last_remote_seen_at" not in columns:
        conn.execute("ALTER TABLE r2_sync_state ADD COLUMN last_remote_seen_at TEXT")


def _migrate_prompt_snippets_schema(conn: sqlite3.Connection):
    columns = _table_columns(conn, "prompt_snippets")
    if "favorite" not in columns:
        conn.execute(
            "ALTER TABLE prompt_snippets ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0"
        )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_prompt_snippets_favorite_updated_at
            ON prompt_snippets(favorite DESC, updated_at DESC)
        """
    )


def _get_setting_value(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM settings_kv WHERE key = ?",
        (key,),
    ).fetchone()
    return row["value"] if row else None


def _set_setting_value(conn: sqlite3.Connection, key: str, value: str):
    conn.execute(
        """
        INSERT INTO settings_kv (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def _overall_config_rows(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT name, env_value, override_value, is_env_set, updated_at, override_updated_at
        FROM overall_config_values
        """
    ).fetchall()
    return {
        row["name"]: {
            "name": row["name"],
            "env_value": row["env_value"],
            "override_value": row["override_value"],
            "is_env_set": bool(row["is_env_set"]),
            "updated_at": row["updated_at"],
            "override_updated_at": row["override_updated_at"],
        }
        for row in rows
    }


def sync_overall_config_env_values(env_values: dict[str, tuple[str, bool]]) -> dict[str, dict[str, Any]]:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            for name, (env_value, is_env_set) in env_values.items():
                conn.execute(
                    """
                    INSERT INTO overall_config_values (
                        name,
                        env_value,
                        override_value,
                        is_env_set,
                        updated_at,
                        override_updated_at
                    )
                    VALUES (?, ?, NULL, ?, ?, NULL)
                    ON CONFLICT(name) DO UPDATE SET
                        env_value = excluded.env_value,
                        is_env_set = excluded.is_env_set,
                        updated_at = excluded.updated_at
                    """,
                    (name, str(env_value or ""), 1 if is_env_set else 0, now),
                )
            rows = _overall_config_rows(conn)
    _secure_data_storage_permissions()
    return rows


def list_overall_config_values() -> dict[str, dict[str, Any]]:
    _ensure_database()
    with _connect() as conn:
        return _overall_config_rows(conn)


def save_overall_config_overrides(
    updates: dict[str, str | None],
) -> dict[str, dict[str, Any]]:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            for name, value in updates.items():
                conn.execute(
                    """
                    INSERT INTO overall_config_values (
                        name,
                        env_value,
                        override_value,
                        is_env_set,
                        updated_at,
                        override_updated_at
                    )
                    VALUES (?, '', ?, 0, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        override_value = excluded.override_value,
                        override_updated_at = excluded.override_updated_at
                    """,
                    (
                        name,
                        value,
                        now,
                        now if value is not None else None,
                    ),
                )
            rows = _overall_config_rows(conn)
    _secure_data_storage_permissions()
    return rows


def _normalize_settings(settings: dict | None) -> dict:
    if not isinstance(settings, dict):
        return _default_settings()

    upstream_socks5_proxy = (
        _normalize_stored_socks5_proxy(settings.get("upstream_socks5_proxy"))
        if settings.get("upstream_socks5_proxy") is not None
        else _normalize_stored_socks5_proxy(config.DEFAULT_UPSTREAM_SOCKS5_PROXY)
    )
    webhook_url = (
        _normalize_stored_webhook_url(settings.get("webhook_url"))
        if settings.get("webhook_url") is not None
        else ""
    )
    r2_backup = (
        _normalize_r2_backup_settings(settings.get("r2_backup"))
        if "r2_backup" in settings
        else _default_r2_backup_settings()
    )

    raw_presets = settings.get("presets")
    if not isinstance(raw_presets, list):
        default_settings = _default_settings()
        default_settings["upstream_socks5_proxy"] = upstream_socks5_proxy
        default_settings["webhook_url"] = webhook_url
        default_settings["r2_backup"] = r2_backup
        return default_settings

    presets: list[dict] = []
    seen_ids: set[str] = set()
    for index, preset in enumerate(raw_presets):
        if not isinstance(preset, dict):
            continue

        normalized_preset = normalize_api_preset(preset, f"preset-{index + 1}")
        normalized_preset["api_key"] = _normalize_stored_api_key(
            normalized_preset.get("api_key")
        )
        preset_id = normalized_preset["id"]
        if preset_id in seen_ids:
            continue
        seen_ids.add(preset_id)
        presets.append(normalized_preset)

    if not presets:
        default_settings = _default_settings()
        default_settings["upstream_socks5_proxy"] = upstream_socks5_proxy
        default_settings["webhook_url"] = webhook_url
        default_settings["r2_backup"] = r2_backup
        return default_settings

    active_preset_id = str(settings.get("active_preset_id") or presets[0]["id"])
    if not any(preset["id"] == active_preset_id for preset in presets):
        active_preset_id = presets[0]["id"]

    return {
        "active_preset_id": active_preset_id,
        "upstream_socks5_proxy": upstream_socks5_proxy,
        "webhook_url": webhook_url,
        "presets": presets,
        "prompt_optimizer": (
            _normalize_prompt_optimizer_settings(settings.get("prompt_optimizer"))
            if "prompt_optimizer" in settings
            else None
        ),
        "r2_backup": r2_backup,
    }


def _replace_settings_on_conn(conn: sqlite3.Connection, settings: dict):
    normalized = _normalize_settings(settings)
    now = utc_now()

    conn.execute("DELETE FROM api_presets")
    for position, preset in enumerate(normalized["presets"]):
        conn.execute(
            """
            INSERT INTO api_presets (
                id,
                name,
                api_url,
                api_key,
                api_path,
                default_model,
                default_response_format,
                position,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                preset["id"],
                preset["name"],
                preset["api_url"],
                preset["api_key"],
                preset["api_path"],
                preset["default_model"],
                preset["default_response_format"],
                position,
                now,
                now,
            ),
        )

    _set_setting_value(
        conn,
        SETTINGS_ACTIVE_PRESET_KEY,
        normalized["active_preset_id"],
    )
    _set_setting_value(
        conn,
        UPSTREAM_SOCKS5_PROXY_KEY,
        normalized.get("upstream_socks5_proxy", ""),
    )
    _set_setting_value(
        conn,
        WEBHOOK_URL_KEY,
        normalized.get("webhook_url", ""),
    )
    optimizer = normalized.get("prompt_optimizer")
    if optimizer is not None:
        _set_setting_value(conn, PROMPT_OPTIMIZER_SETTINGS_KEY, json.dumps(optimizer))
    r2_backup = normalized.get("r2_backup")
    if r2_backup is not None:
        _set_setting_value(conn, R2_BACKUP_SETTINGS_KEY, json.dumps(r2_backup))


def _load_settings_from_conn(conn: sqlite3.Connection) -> dict | None:
    rows = conn.execute(
        """
        SELECT id, name, api_url, api_key, api_path, default_model, default_response_format
        FROM api_presets
        ORDER BY position ASC, id ASC
        """
    ).fetchall()
    if not rows:
        return None

    presets = [
        {
            "id": row["id"],
            "name": row["name"],
            "api_url": row["api_url"],
            "api_key": row["api_key"],
            "api_path": row["api_path"],
            "default_model": row["default_model"],
            "default_response_format": row["default_response_format"],
        }
        for row in rows
    ]
    active_preset_id = _get_setting_value(conn, SETTINGS_ACTIVE_PRESET_KEY)
    if not active_preset_id:
        active_preset_id = presets[0]["id"]
    upstream_socks5_proxy = _get_setting_value(conn, UPSTREAM_SOCKS5_PROXY_KEY)
    if upstream_socks5_proxy is None:
        upstream_socks5_proxy = config.DEFAULT_UPSTREAM_SOCKS5_PROXY
    webhook_url = _get_setting_value(conn, WEBHOOK_URL_KEY) or ""

    optimizer_json = _get_setting_value(conn, PROMPT_OPTIMIZER_SETTINGS_KEY)
    optimizer = None
    if optimizer_json:
        try:
            optimizer = _normalize_prompt_optimizer_settings(json.loads(optimizer_json))
        except (json.JSONDecodeError, TypeError):
            optimizer = _default_prompt_optimizer_settings()
    else:
        optimizer = _default_prompt_optimizer_settings()

    r2_backup = _load_r2_backup_settings_from_conn(conn)

    return _normalize_settings(
        {
            "active_preset_id": active_preset_id,
            "upstream_socks5_proxy": upstream_socks5_proxy,
            "webhook_url": webhook_url,
            "presets": presets,
            "prompt_optimizer": optimizer,
            "r2_backup": r2_backup,
        }
    )


def _normalize_gallery_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None

    entry_id = entry.get("id")
    filename = entry.get("filename")
    if not entry_id or not filename:
        return None

    normalized: dict[str, Any] = {
        "id": str(entry_id),
        "prompt": str(entry.get("prompt") or ""),
        "size": str(entry.get("size") or ""),
        "filename": str(filename),
        "created_at": str(entry.get("created_at") or utc_now()),
        "favorite": _normalize_gallery_favorite(entry.get("favorite")),
    }

    for column in GALLERY_COLUMNS:
        if column in REQUIRED_GALLERY_COLUMNS or column == "favorite":
            continue
        value = entry.get(column)
        if value is None:
            continue
        if column in INTEGER_GALLERY_COLUMNS:
            try:
                normalized[column] = int(value)
            except (TypeError, ValueError):
                continue
        elif column == "thumbnail_filename":
            thumbnail_filename = str(value)
            if safe_thumbnail_path(thumbnail_filename):
                normalized[column] = thumbnail_filename
        else:
            normalized[column] = str(value)

    return normalized


def _normalize_gallery_favorite(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return 1 if value else 0
    text = str(value or "").strip().lower()
    return 1 if text in {"1", "true", "yes", "on", "favorite", "favorited"} else 0


def _normalize_gallery_filter_bool(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return 1 if value else 0
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    if normalized in {"1", "true", "yes", "y", "on", "favorite", "favorited"}:
        return 1
    if normalized in {"0", "false", "no", "n", "off", "unfavorite", "unfavorited"}:
        return 0
    return None


def _gallery_row_values(entry: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(entry.get(column) for column in GALLERY_COLUMNS)


def _gallery_thumbnail_status_for_row(
    row: sqlite3.Row,
    thumbnail_status_map: dict[str, str] | None = None,
) -> str:
    filename = str(row["filename"] or "")
    thumbnail_filename = str(row["thumbnail_filename"] or "").strip()
    if not thumbnail_filename:
        thumbnail_filename = _thumbnail_filename_for_image(filename) or ""

    thumbnail_path = safe_thumbnail_path(thumbnail_filename) if thumbnail_filename else None
    if thumbnail_path and thumbnail_path.is_file():
        return "ready"

    if filename and thumbnail_status_map:
        status = thumbnail_status_map.get(filename)
        if status:
            return status

    return "missing"


def _gallery_entry_from_row(
    row: sqlite3.Row,
    thumbnail_status_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    entry = {
        column: row[column]
        for column in GALLERY_COLUMNS
        if column not in _GALLERY_INTERNAL_COLUMNS
        and (column in REQUIRED_GALLERY_COLUMNS or row[column] is not None)
    }
    entry["favorite"] = bool(entry.get("favorite"))
    if entry.get("thumbnail_filename") and not safe_thumbnail_path(
        str(entry["thumbnail_filename"])
    ):
        entry.pop("thumbnail_filename", None)
    entry["thumbnail_status"] = _gallery_thumbnail_status_for_row(
        row, thumbnail_status_map
    )
    return _attach_gallery_thumbnail_url(entry)


def _like_contains_param(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _fts_phrase_query(value: str) -> str:
    escaped = value.replace('"', '""')
    return f'"{escaped}"'


def _use_prompt_fts(prompt: str) -> bool:
    return bool(
        _gallery_fts_available
        and len(prompt) >= GALLERY_FTS_MIN_QUERY_LENGTH
    )


def _build_gallery_filter_where(filters: dict[str, Any] | None) -> tuple[str, list[Any]]:
    if not filters:
        return "", []

    clauses: list[str] = []
    params: list[Any] = []

    prompt = str(filters.get("prompt") or "").strip()
    if prompt:
        if _use_prompt_fts(prompt):
            clauses.append(
                """
                rowid IN (
                    SELECT rowid
                    FROM gallery_entries_fts
                    WHERE gallery_entries_fts MATCH ?
                )
                """
            )
            params.append(_fts_phrase_query(prompt))
        else:
            clauses.append("prompt COLLATE NOCASE LIKE ? ESCAPE '\\'")
            params.append(_like_contains_param(prompt))

    for key, column in (
        ("model", "model"),
        ("preset", "api_preset_name"),
        ("size", "size"),
    ):
        value = str(filters.get(key) or "").strip()
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)

    favorite = _normalize_gallery_filter_bool(filters.get("favorite"))
    if favorite is not None:
        clauses.append("favorite = ?")
        params.append(favorite)

    date_from = str(filters.get("date_from") or "").strip()
    if date_from:
        clauses.append("created_at >= ?")
        params.append(date_from)

    date_to = str(filters.get("date_to") or "").strip()
    if date_to:
        clauses.append("created_at <= ?")
        params.append(date_to)

    if not clauses:
        return "", []
    return " WHERE " + " AND ".join(clauses), params


def _gallery_query_key_from_components(
    where_sql: str,
    params: Sequence[Any],
) -> str:
    payload = {
        "sort": "sort_seq_desc_id_desc",
        "where": " ".join(where_sql.split()),
        "params": ["" if value is None else str(value) for value in params],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalize_generate_job(job: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    normalized: dict[str, Any] = {
        "job_id": str(job["job_id"]),
        "status": str(job.get("status") or "queued"),
        "created_at": str(job.get("created_at") or now),
        "updated_at": str(job.get("updated_at") or now),
    }

    for column in GENERATE_JOB_COLUMNS:
        if column in {"job_id", "status", "created_at", "updated_at"}:
            continue
        if column == "stage_timings_json":
            value = job.get("stage_timings_json")
            if value is None:
                value = job.get("stage_timings")
            if value is None:
                continue
            if isinstance(value, str):
                try:
                    json.loads(value)
                except json.JSONDecodeError:
                    continue
                normalized[column] = value
            else:
                try:
                    normalized[column] = json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                except TypeError:
                    continue
            continue
        if column == "images_json":
            value = job.get("images_json")
            if value is None:
                value = job.get("images")
            if value is None:
                continue
            if isinstance(value, str):
                try:
                    json.loads(value)
                except json.JSONDecodeError:
                    continue
                normalized[column] = value
            else:
                try:
                    normalized[column] = json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                except TypeError:
                    continue
            continue
        value = job.get(column)
        if value is None:
            continue
        if column in INTEGER_GENERATE_JOB_COLUMNS:
            try:
                normalized[column] = int(value)
            except (TypeError, ValueError):
                continue
        else:
            normalized[column] = str(value)

    return normalized


def _generate_job_values(job: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(job.get(column) for column in GENERATE_JOB_COLUMNS)


def _upsert_generate_job_on_conn(conn: sqlite3.Connection, job: dict[str, Any]) -> None:
    columns_sql = ", ".join(GENERATE_JOB_COLUMNS)
    placeholders_sql = ", ".join("?" for _ in GENERATE_JOB_COLUMNS)
    updates_sql = ", ".join(
        f"{column} = excluded.{column}"
        for column in GENERATE_JOB_COLUMNS
        if column != "job_id"
    )
    conn.execute(
        f"""
        INSERT INTO generate_jobs ({columns_sql})
        VALUES ({placeholders_sql})
        ON CONFLICT(job_id) DO UPDATE SET {updates_sql}
        """,
        _generate_job_values(job),
    )
    if job.get("status") not in ACTIVE_GENERATE_JOB_STATUSES:
        conn.execute(
            "DELETE FROM edit_source_reservations WHERE job_id = ?",
            (job["job_id"],),
        )


def _generate_job_from_row(row: sqlite3.Row) -> dict[str, Any]:
    job = {
        column: row[column]
        for column in GENERATE_JOB_COLUMNS
        if row[column] is not None
    }
    stage_timings_json = job.pop("stage_timings_json", None)
    if stage_timings_json:
        try:
            job["stage_timings"] = json.loads(stage_timings_json)
        except json.JSONDecodeError:
            job["stage_timings"] = {}
    images_json = job.pop("images_json", None)
    if images_json:
        try:
            job["images"] = json.loads(images_json)
        except json.JSONDecodeError:
            job["images"] = []
    if job.get("image_id"):
        job["id"] = job["image_id"]
    if not job.get("images") and job.get("image_id") and job.get("image_url"):
        image_url = str(job["image_url"])
        image: dict[str, Any] = {
            "image_id": str(job["image_id"]),
            "image_url": image_url,
            "filename": image_url.rsplit("/", 1)[-1],
        }
        if job.get("image_width") is not None:
            image["image_width"] = job["image_width"]
        if job.get("image_height") is not None:
            image["image_height"] = job["image_height"]
        job["images"] = [image]
    return job


def _json_loads_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_loads_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _coerce_nonnegative_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _image_job_unit_from_row(row: sqlite3.Row) -> dict[str, Any]:
    unit = {
        column: row[column]
        for column in IMAGE_JOB_UNIT_COLUMNS
        if row[column] is not None
    }
    unit["request"] = _json_loads_dict(unit.pop("request_json", None))
    unit["edit_sources"] = _json_loads_list(unit.pop("edit_sources_json", None))
    unit["result"] = _json_loads_dict(unit.pop("result_json", None))
    unit["stage_timings"] = _json_loads_dict(unit.pop("stage_timings_json", None))
    return unit


def _image_job_unit_values(unit: dict[str, Any]) -> tuple[Any, ...]:
    normalized = dict(unit)
    for source_key, json_key in (
        ("request", "request_json"),
        ("edit_sources", "edit_sources_json"),
        ("result", "result_json"),
        ("stage_timings", "stage_timings_json"),
    ):
        if json_key not in normalized and source_key in normalized:
            normalized[json_key] = json.dumps(
                normalized[source_key],
                ensure_ascii=False,
                sort_keys=True,
            )
    return tuple(normalized.get(column) for column in IMAGE_JOB_UNIT_COLUMNS)


def _normalize_gallery_job(job: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    normalized: dict[str, Any] = {
        "job_id": str(job["job_id"]),
        "kind": str(job["kind"]),
        "status": str(job.get("status") or "queued"),
        "progress": _coerce_nonnegative_int(job.get("progress"), 0),
        "created_at": str(job.get("created_at") or now),
        "updated_at": str(job.get("updated_at") or now),
    }
    integer_columns = {
        "requested_count",
        "processed_count",
        "exported_count",
        "missing_count",
        "total_count",
        "compared_count",
        "uploaded_count",
        "pending_upload_count",
        "skipped_existing_count",
        "missing_local_count",
        "failed_count",
        "bytes_total",
        "bytes_written",
        "bytes_uploaded",
    }
    for column in GALLERY_JOB_COLUMNS:
        if column in normalized:
            continue
        if column == "payload_json":
            value = job.get("payload_json")
            if value is None:
                value = job.get("payload")
            if value is None:
                continue
            if isinstance(value, str):
                try:
                    json.loads(value)
                except json.JSONDecodeError:
                    continue
                normalized[column] = value
            else:
                try:
                    normalized[column] = json.dumps(value, ensure_ascii=False, sort_keys=True)
                except TypeError:
                    continue
            continue
        if column in integer_columns:
            value = job.get(column, 0)
            normalized[column] = _coerce_nonnegative_int(value, 0)
        else:
            value = job.get(column)
            if value is None:
                continue
            normalized[column] = str(value)
    return normalized


def _gallery_job_values(job: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(job.get(column) for column in GALLERY_JOB_COLUMNS)


def _gallery_job_from_row(row: sqlite3.Row) -> dict[str, Any]:
    job = {
        column: row[column]
        for column in GALLERY_JOB_COLUMNS
        if row[column] is not None
    }
    job["payload"] = _json_loads_dict(job.pop("payload_json", None))
    return job


def _normalize_prompt_snippet_favorite(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return 1 if value else 0
    text = str(value or "").strip().lower()
    return 1 if text in {"1", "true", "yes", "on", "favorite", "favorited"} else 0


def _prompt_snippet_from_row(row: sqlite3.Row) -> PromptSnippet:
    return PromptSnippet(
        id=str(row["id"]),
        title=str(row["title"]),
        prompt=str(row["prompt"]),
        favorite=bool(row["favorite"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _like_prompt_snippet_query(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _public_file_url(base_url: str, filename: str) -> str:
    return f"{base_url.rstrip('/')}/{quote(filename, safe='')}"


def image_url_for_filename(filename: str) -> str | None:
    if not safe_image_path(filename):
        return None
    if config.PUBLIC_IMAGE_BASE_URL:
        return _public_file_url(config.PUBLIC_IMAGE_BASE_URL, filename)
    return f"/api/image/{quote(filename, safe='')}"


def _generate_prompt_snippet_id() -> str:
    return f"ps_{secrets.token_urlsafe(12)}"


GALLERY_FILTER_OPTION_FIELDS = (
    ("model", "model"),
    ("preset", "api_preset_name"),
    ("size", "size"),
)


def _filter_option_values_from_mapping(
    mapping: dict[str, Any] | sqlite3.Row,
) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for kind, column in GALLERY_FILTER_OPTION_FIELDS:
        value = str(
            mapping[column]
            if isinstance(mapping, sqlite3.Row)
            else mapping.get(column) or ""
        ).strip()
        if value:
            values.append((kind, value))
    return values


def _add_gallery_filter_option_deltas(
    deltas: dict[tuple[str, str], int],
    mapping: dict[str, Any] | sqlite3.Row,
    delta: int,
) -> None:
    for key in _filter_option_values_from_mapping(mapping):
        deltas[key] = deltas.get(key, 0) + delta


def _apply_gallery_filter_option_deltas_on_conn(
    conn: sqlite3.Connection,
    deltas: dict[tuple[str, str], int],
) -> None:
    deltas = {key: delta for key, delta in deltas.items() if delta}
    if not deltas:
        return

    now = utc_now()
    conn.executemany(
        """
        INSERT INTO gallery_filter_options (kind, value, ref_count, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(kind, value) DO UPDATE SET
            ref_count = ref_count + excluded.ref_count,
            updated_at = excluded.updated_at
        """,
        [(kind, value, delta, now) for (kind, value), delta in deltas.items()],
    )
    conn.execute("DELETE FROM gallery_filter_options WHERE ref_count <= 0")
    _bump_filter_options_cache_version()


def _increment_gallery_filter_options_on_conn(
    conn: sqlite3.Connection,
    mapping: dict[str, Any] | sqlite3.Row,
    delta: int,
):
    deltas: dict[tuple[str, str], int] = {}
    _add_gallery_filter_option_deltas(deltas, mapping, delta)
    _apply_gallery_filter_option_deltas_on_conn(conn, deltas)


def _rebuild_gallery_filter_options_on_conn(conn: sqlite3.Connection):
    conn.execute("DELETE FROM gallery_filter_options")
    now = utc_now()
    for kind, column in GALLERY_FILTER_OPTION_FIELDS:
        conn.execute(
            f"""
            INSERT INTO gallery_filter_options (kind, value, ref_count, updated_at)
            SELECT ?, TRIM({column}) AS value, COUNT(*) AS ref_count, ?
            FROM gallery_entries
            WHERE {column} IS NOT NULL AND TRIM({column}) != ''
            GROUP BY TRIM({column})
            """,
            (kind, now),
        )
    _bump_filter_options_cache_version()


def rebuild_gallery_filter_options() -> GalleryFilterOptions:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            _rebuild_gallery_filter_options_on_conn(conn)
        return _get_gallery_filter_options_on_conn(conn)


def _insert_gallery_entries_on_conn(
    conn: sqlite3.Connection,
    entries: list[dict[str, Any]],
):
    normalized_entries = [
        normalized
        for entry in entries
        if (normalized := _normalize_gallery_entry(entry)) is not None
    ]
    if not normalized_entries:
        return

    incoming_ids = [entry["id"] for entry in normalized_entries]
    existing_by_id: dict[str, sqlite3.Row] = {}
    for chunk in _iter_sqlite_in_chunks(incoming_ids):
        placeholders = ", ".join("?" for _ in chunk)
        existing_rows = conn.execute(
            f"""
            SELECT id, model, api_preset_name, size
            FROM gallery_entries
            WHERE id IN ({placeholders})
            """,
            tuple(chunk),
        ).fetchall()
        existing_by_id.update({row["id"]: row for row in existing_rows})

    row = conn.execute(
        "SELECT COALESCE(MAX(sort_seq), 0) FROM gallery_entries"
    ).fetchone()
    next_seq = int(row[0]) + 1 if row else 1
    for entry in normalized_entries:
        if entry.get("sort_seq") is None:
            entry["sort_seq"] = next_seq
            next_seq += 1

    columns_sql = ", ".join(GALLERY_COLUMNS)
    placeholders_sql = ", ".join("?" for _ in GALLERY_COLUMNS)
    updates_sql = ", ".join(
        f"{column} = excluded.{column}"
        for column in GALLERY_COLUMNS
        if column != "id"
    )
    conn.executemany(
        f"""
        INSERT INTO gallery_entries ({columns_sql})
        VALUES ({placeholders_sql})
        ON CONFLICT(id) DO UPDATE SET {updates_sql}
        """,
        [_gallery_row_values(entry) for entry in normalized_entries],
    )
    filter_option_deltas: dict[tuple[str, str], int] = {}
    for entry in normalized_entries:
        existing = existing_by_id.get(entry["id"])
        if existing is not None:
            _add_gallery_filter_option_deltas(filter_option_deltas, existing, -1)
        _add_gallery_filter_option_deltas(filter_option_deltas, entry, 1)
        _enqueue_thumbnail_job_on_conn(conn, str(entry.get("filename") or ""))
    _apply_gallery_filter_option_deltas_on_conn(conn, filter_option_deltas)
    _invalidate_gallery_query_caches_on_conn(conn)


def load_settings() -> dict:
    _ensure_database()
    with _connect() as conn:
        settings = _load_settings_from_conn(conn)
        if settings:
            return settings

        settings = _default_settings()
        with _transaction(conn):
            _replace_settings_on_conn(conn, settings)
        _secure_data_storage_permissions()
        return settings


def save_settings(settings: dict):
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            _replace_settings_on_conn(conn, settings)
    _secure_data_storage_permissions()


def load_prompt_optimizer_settings() -> dict:
    _ensure_database()
    with _connect() as conn:
        raw = _get_setting_value(conn, PROMPT_OPTIMIZER_SETTINGS_KEY)
        if raw:
            try:
                return _normalize_prompt_optimizer_settings(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                return _default_prompt_optimizer_settings()
        return _default_prompt_optimizer_settings()


def save_prompt_optimizer_settings(settings: dict):
    _ensure_database()
    normalized = _normalize_prompt_optimizer_settings(settings)
    with _connect() as conn:
        _set_setting_value(conn, PROMPT_OPTIMIZER_SETTINGS_KEY, json.dumps(normalized))
        conn.commit()
    _secure_data_storage_permissions()


def load_r2_backup_settings() -> dict:
    _ensure_database()
    with _connect() as conn:
        return _load_r2_backup_settings_from_conn(conn)


def save_r2_backup_settings(settings: dict):
    _ensure_database()
    normalized = _normalize_r2_backup_settings(settings)
    with _connect() as conn:
        _set_setting_value(conn, R2_BACKUP_SETTINGS_KEY, json.dumps(normalized))
        conn.commit()
    _secure_data_storage_permissions()


def list_prompt_snippets(query: str = "") -> list[PromptSnippet]:
    _ensure_database()
    normalized_query = str(query or "").strip()
    with _connect() as conn:
        params: list[Any] = []
        where_sql = ""
        if normalized_query:
            where_sql = """
                WHERE title COLLATE NOCASE LIKE ? ESCAPE '\\'
                   OR prompt COLLATE NOCASE LIKE ? ESCAPE '\\'
            """
            like_query = _like_prompt_snippet_query(normalized_query)
            params.extend([like_query, like_query])
        rows = conn.execute(
            f"""
            SELECT {", ".join(PROMPT_SNIPPET_COLUMNS)}
            FROM prompt_snippets
            {where_sql}
            ORDER BY favorite DESC, updated_at DESC, rowid DESC
            """,
            tuple(params),
        ).fetchall()
    return [_prompt_snippet_from_row(row) for row in rows]


def create_prompt_snippet(
    *,
    title: str,
    prompt: str,
    favorite: bool = False,
) -> PromptSnippet:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            for _ in range(5):
                snippet_id = _generate_prompt_snippet_id()
                try:
                    conn.execute(
                        """
                        INSERT INTO prompt_snippets (
                            id,
                            title,
                            prompt,
                            favorite,
                            created_at,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            snippet_id,
                            title,
                            prompt,
                            _normalize_prompt_snippet_favorite(favorite),
                            now,
                            now,
                        ),
                    )
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                raise RuntimeError("Failed to generate a unique prompt snippet id")

            row = conn.execute(
                f"""
                SELECT {", ".join(PROMPT_SNIPPET_COLUMNS)}
                FROM prompt_snippets
                WHERE id = ?
                """,
                (snippet_id,),
            ).fetchone()

    return _prompt_snippet_from_row(row)


def update_prompt_snippet(
    snippet_id: str,
    updates: dict[str, Any],
) -> PromptSnippet | None:
    _ensure_database()
    allowed_updates = {
        key: _normalize_prompt_snippet_favorite(value) if key == "favorite" else value
        for key, value in updates.items()
        if key in {"title", "prompt", "favorite"} and value is not None
    }

    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                f"""
                SELECT {", ".join(PROMPT_SNIPPET_COLUMNS)}
                FROM prompt_snippets
                WHERE id = ?
                """,
                (snippet_id,),
            ).fetchone()
            if not row:
                return None

            if allowed_updates:
                now = utc_now()
                assignments = ", ".join(f"{key} = ?" for key in allowed_updates)
                conn.execute(
                    f"""
                    UPDATE prompt_snippets
                    SET {assignments}, updated_at = ?
                    WHERE id = ?
                    """,
                    (*allowed_updates.values(), now, snippet_id),
                )
                row = conn.execute(
                    f"""
                    SELECT {", ".join(PROMPT_SNIPPET_COLUMNS)}
                    FROM prompt_snippets
                    WHERE id = ?
                    """,
                    (snippet_id,),
                ).fetchone()

    return _prompt_snippet_from_row(row)


def delete_prompt_snippet(snippet_id: str) -> bool:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                "DELETE FROM prompt_snippets WHERE id = ?",
                (snippet_id,),
            )
            return cursor.rowcount > 0


def _attach_gallery_thumbnail_url(entry: dict[str, Any]) -> dict[str, Any]:
    if "image_url" not in entry:
        entry["image_url"] = image_url_for_filename(str(entry.get("filename") or ""))
    if "thumbnail_url" not in entry:
        entry["thumbnail_url"] = _thumbnail_url_for_filename(
            str(entry.get("filename") or "")
        )
    return entry


def _prepare_gallery_file(image_bytes: bytes, filename: str) -> _PreparedGalleryFile:
    image_temp_path = _save_image_temp_unlocked(image_bytes, filename)
    return _PreparedGalleryFile(filename=filename, image_temp_path=image_temp_path)


def _cleanup_prepared_gallery_files(prepared_files: Iterable[_PreparedGalleryFile]):
    for prepared in prepared_files:
        prepared.image_temp_path.unlink(missing_ok=True)
        if prepared.thumbnail_temp_path:
            prepared.thumbnail_temp_path.unlink(missing_ok=True)


def _promote_prepared_images(prepared_files: Sequence[_PreparedGalleryFile]):
    for prepared in prepared_files:
        _promote_image_temp_unlocked(prepared.filename, prepared.image_temp_path)


def _promote_prepared_thumbnails(prepared_files: Sequence[_PreparedGalleryFile]):
    for prepared in prepared_files:
        if prepared.thumbnail_filename and prepared.thumbnail_temp_path:
            if _promote_thumbnail_temp_unlocked(
                prepared.thumbnail_filename,
                prepared.thumbnail_temp_path,
            ):
                _add_verified_thumbnail(prepared.thumbnail_filename)


@contextmanager
def _thumbnail_cpu_slot() -> Iterator[None]:
    owner = f"thumbnail-{os.getpid()}-{threading.get_ident()}-{uuid.uuid4()}"
    slot_name: str | None = None
    try:
        while slot_name is None:
            now_dt = datetime.now(timezone.utc)
            slot_name = acquire_background_slot(
                name_prefix="thumbnail_cpu",
                owner=owner,
                slot_count=config.THUMBNAIL_CPU_CONCURRENCY,
                lease_expires_at=(
                    now_dt + timedelta(seconds=THUMBNAIL_CPU_SLOT_LEASE_SECONDS)
                ).isoformat(),
                now=now_dt.isoformat(),
            )
            if slot_name is None:
                time.sleep(0.05)
        yield
    finally:
        if slot_name is not None:
            release_background_slot(name=slot_name, owner=owner)


def _enqueue_thumbnail_job_on_conn(
    conn: sqlite3.Connection,
    filename: str,
    *,
    force: bool = False,
) -> bool:
    normalized = str(filename or "").strip()
    image_path = safe_image_path(normalized)
    if not normalized or not image_path or not image_path.is_file():
        return False

    now = utc_now()
    existing = conn.execute(
        """
        SELECT status, lease_expires_at
        FROM thumbnail_jobs
        WHERE filename = ?
        """,
        (normalized,),
    ).fetchone()
    if existing and not force:
        if existing["status"] == "success":
            return False
        if (
            existing["status"] == "running"
            and str(existing["lease_expires_at"] or "") > now
        ):
            return False

    conn.execute(
        """
        INSERT INTO thumbnail_jobs (
            filename,
            status,
            attempts,
            lease_owner,
            lease_expires_at,
            created_at,
            updated_at,
            error
        )
        VALUES (?, 'queued', 0, NULL, NULL, ?, ?, NULL)
        ON CONFLICT(filename) DO UPDATE SET
            status = 'queued',
            lease_owner = NULL,
            lease_expires_at = NULL,
            updated_at = excluded.updated_at,
            error = NULL
        """,
        (normalized, now, now),
    )
    return True


def enqueue_thumbnail_job(filename: str, *, force: bool = False) -> bool:
    _ensure_database()
    image_path = safe_image_path(filename)
    if not image_path or not image_path.is_file():
        return False
    with _connect() as conn:
        with _transaction(conn):
            return _enqueue_thumbnail_job_on_conn(conn, filename, force=force)


def get_pending_thumbnail_job_count() -> int:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM thumbnail_jobs
            WHERE status = 'queued'
               OR (status = 'running' AND lease_expires_at <= ?)
            """,
            (utc_now(),),
        ).fetchone()
    return int(row[0] or 0) if row else 0


def claim_next_thumbnail_job(
    *,
    owner: str,
    lease_expires_at: str,
    now: str | None = None,
) -> dict[str, Any] | None:
    _ensure_database()
    current_time = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                """
                SELECT filename, status, attempts, lease_owner, lease_expires_at,
                    created_at, updated_at, error
                FROM thumbnail_jobs
                WHERE status = 'queued'
                   OR (status = 'running' AND lease_expires_at <= ?)
                ORDER BY created_at ASC, filename ASC
                LIMIT 1
                """,
                (current_time,),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                """
                UPDATE thumbnail_jobs
                SET status = 'running',
                    attempts = attempts + 1,
                    lease_owner = ?,
                    lease_expires_at = ?,
                    updated_at = ?,
                    error = NULL
                WHERE filename = ?
                """,
                (owner, lease_expires_at, current_time, row["filename"]),
            )
            updated = conn.execute(
                """
                SELECT filename, status, attempts, lease_owner, lease_expires_at,
                    created_at, updated_at, error
                FROM thumbnail_jobs
                WHERE filename = ?
                """,
                (row["filename"],),
            ).fetchone()
    return dict(updated) if updated else None


def complete_thumbnail_job(filename: str, *, owner: str) -> bool:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                """
                UPDATE thumbnail_jobs
                SET status = 'success',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = ?,
                    error = NULL
                WHERE filename = ? AND lease_owner = ?
                """,
                (utc_now(), filename, owner),
            )
            return cursor.rowcount > 0


def fail_thumbnail_job(
    filename: str,
    *,
    owner: str,
    error: str,
    max_attempts: int = THUMBNAIL_JOB_MAX_ATTEMPTS,
) -> bool:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                """
                SELECT attempts
                FROM thumbnail_jobs
                WHERE filename = ? AND lease_owner = ?
                """,
                (filename, owner),
            ).fetchone()
            if not row:
                return False
            next_status = (
                "error"
                if int(row["attempts"] or 0) >= max_attempts
                else "queued"
            )
            conn.execute(
                """
                UPDATE thumbnail_jobs
                SET status = ?,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = ?,
                    error = ?
                WHERE filename = ? AND lease_owner = ?
                """,
                (next_status, utc_now(), str(error or "")[:1000], filename, owner),
            )
            return True


def _dedupe_gallery_filename(filename: str, used_filenames: set[str]) -> str:
    if filename not in used_filenames:
        return filename

    path_name = Path(filename)
    base = path_name.stem
    ext = path_name.suffix
    counter = 1
    while True:
        candidate = f"{base}_{counter}{ext}"
        if candidate not in used_filenames:
            return candidate
        counter += 1


def _dedupe_import_entries_on_conn(
    conn: sqlite3.Connection,
    entries: list[dict[str, Any]],
    prepared_files: list[_PreparedGalleryFile],
):
    used_filenames: set[str] = set()
    used_ids: set[str] = set()
    conflicts_possible = (
        conn.execute("SELECT 1 FROM gallery_entries LIMIT 1").fetchone() is not None
    )

    if conflicts_possible:
        incoming_filenames = [str(e["filename"]) for e in entries]
        incoming_ids = [str(e["id"]) for e in entries]
        for chunk in _iter_sqlite_in_chunks(incoming_filenames):
            placeholders_fn = ", ".join("?" for _ in chunk)
            rows = conn.execute(
                f"""
                SELECT DISTINCT filename
                FROM gallery_entries
                WHERE filename IN ({placeholders_fn})
                """,
                tuple(chunk),
            ).fetchall()
            used_filenames.update(row["filename"] for row in rows if row["filename"])
        for chunk in _iter_sqlite_in_chunks(incoming_ids):
            placeholders_id = ", ".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT id FROM gallery_entries WHERE id IN ({placeholders_id})",
                tuple(chunk),
            ).fetchall()
            used_ids.update(row["id"] for row in rows if row["id"])

    seen_filenames: set[str] = set()
    seen_ids: set[str] = set()

    for entry, prepared in zip(entries, prepared_files):
        image_id = str(entry["id"])
        while image_id in used_ids or image_id in seen_ids:
            image_id = generate_image_id()
        entry["id"] = image_id
        seen_ids.add(image_id)

        filename = str(entry["filename"])
        deduped_filename = _dedupe_gallery_filename(filename, used_filenames | seen_filenames)
        entry["filename"] = deduped_filename
        prepared.filename = deduped_filename
        seen_filenames.add(deduped_filename)

        if deduped_filename != filename:
            entry.pop("thumbnail_filename", None)
            if prepared.thumbnail_temp_path:
                prepared.thumbnail_temp_path.unlink(missing_ok=True)
            prepared.thumbnail_filename = None
            prepared.thumbnail_temp_path = None


def ensure_thumbnail_for_image(filename: str) -> str | None:
    thumbnail_filename = _thumbnail_filename_for_image(filename)
    if not thumbnail_filename:
        return None

    with _verified_thumbnails_lock:
        if thumbnail_filename in _verified_thumbnails:
            return thumbnail_filename

    thumbnail_path = safe_thumbnail_path(thumbnail_filename)
    if thumbnail_path and thumbnail_path.is_file():
        _add_verified_thumbnail(thumbnail_filename)
        _set_thumbnail_filename_for_image(filename, thumbnail_filename)
        return thumbnail_filename

    image_path = safe_image_path(filename)
    if not image_path or not image_path.is_file():
        return None

    enqueue_thumbnail_job(filename, force=True)
    return None


def generate_thumbnail_for_image(filename: str) -> str | None:
    thumbnail_filename = _thumbnail_filename_for_image(filename)
    if not thumbnail_filename:
        return None

    thumbnail_path = safe_thumbnail_path(thumbnail_filename)
    if thumbnail_path and thumbnail_path.is_file():
        _add_verified_thumbnail(thumbnail_filename)
        _set_thumbnail_filename_for_image(filename, thumbnail_filename)
        return thumbnail_filename

    image_path = safe_image_path(filename)
    if not image_path or not image_path.is_file():
        return None

    for _ in range(3):
        if thumbnail_path and thumbnail_path.is_file():
            _add_verified_thumbnail(thumbnail_filename)
            _set_thumbnail_filename_for_image(filename, thumbnail_filename)
            return thumbnail_filename
        try:
            image_stat = image_path.stat()
        except OSError as e:
            logger.warning("Failed to stat image for thumbnail %s: %s", filename, e)
            return None

        with _thumbnail_cpu_slot():
            prepared_thumbnail = _create_thumbnail_temp_from_path_unlocked(image_path, filename)
        if not prepared_thumbnail:
            return None

        created_thumbnail, temp_path = prepared_thumbnail
        if thumbnail_path and thumbnail_path.is_file():
            temp_path.unlink(missing_ok=True)
            _add_verified_thumbnail(thumbnail_filename)
            return thumbnail_filename

        with _storage_lock:
            if thumbnail_path and thumbnail_path.is_file():
                temp_path.unlink(missing_ok=True)
                _add_verified_thumbnail(thumbnail_filename)
                return thumbnail_filename
            try:
                current_stat = image_path.stat()
            except OSError:
                temp_path.unlink(missing_ok=True)
                return None
            if (
                current_stat.st_mtime_ns != image_stat.st_mtime_ns
                or current_stat.st_size != image_stat.st_size
            ):
                temp_path.unlink(missing_ok=True)
                continue

            if _promote_thumbnail_temp_unlocked(created_thumbnail, temp_path):
                _set_thumbnail_filename_for_image(filename, created_thumbnail)
                _add_verified_thumbnail(created_thumbnail)
                return created_thumbnail
            return None

    return None


def _set_thumbnail_filename_for_image(filename: str, thumbnail_filename: str):
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            conn.execute(
                """
                UPDATE gallery_entries
                SET thumbnail_filename = ?
                WHERE filename = ?
                """,
                (thumbnail_filename, filename),
            )


def _build_gallery_entry(
    image_id: str,
    prompt: str,
    size: str,
    filename: str,
    metadata: dict[str, Any] | None = None,
    image_bytes: bytes | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": image_id,
        "prompt": prompt,
        "size": size,
        "filename": filename,
        "created_at": utc_now(),
    }
    if image_bytes:
        entry.update(_image_dimension_metadata(image_bytes))
    if image_bytes is not None:
        entry["bytes"] = len(image_bytes)
        entry["sha256"] = hashlib.sha256(image_bytes).hexdigest()
    if metadata:
        entry.update(
            {
                key: value
                for key, value in metadata.items()
                if key in GALLERY_COLUMNS
                and key not in REQUIRED_GALLERY_COLUMNS
                and value is not None
            }
        )
    return entry


def _save_images_and_insert_gallery_entries(
    entries_data: list[tuple[bytes, str]],
    gallery_entries: list[dict[str, Any]],
):
    _ensure_database()
    prepared_files: list[_PreparedGalleryFile] = []
    try:
        for index, (image_bytes, filename) in enumerate(entries_data):
            prepared = _prepare_gallery_file(image_bytes, filename)
            prepared_files.append(prepared)
            if prepared.thumbnail_filename and index < len(gallery_entries):
                gallery_entries[index]["thumbnail_filename"] = (
                    prepared.thumbnail_filename
                )

        with _gallery_file_write_lock:
            with _storage_lock:
                _promote_prepared_images(prepared_files)
                with _connect() as conn:
                    with _transaction(conn):
                        with observe_job_stage("db_insert"):
                            _insert_gallery_entries_on_conn(conn, gallery_entries)
                _promote_prepared_thumbnails(prepared_files)
    except BaseException:
        _cleanup_prepared_gallery_files(prepared_files)
        raise


def import_gallery_entries(
    entries_data: Iterable[tuple[bytes, dict[str, Any]]],
) -> int:
    _ensure_database()
    prepared_files: list[_PreparedGalleryFile] = []
    normalized_entries: list[dict[str, Any]] = []
    try:
        for image_bytes, entry in entries_data:
            normalized = _normalize_gallery_entry(entry)
            if not normalized:
                continue
            normalized["bytes"] = len(image_bytes)
            normalized["sha256"] = hashlib.sha256(image_bytes).hexdigest()
            normalized.pop("thumbnail_filename", None)

            prepared = _prepare_gallery_file(image_bytes, normalized["filename"])
            prepared_files.append(prepared)
            if prepared.thumbnail_filename:
                normalized["thumbnail_filename"] = prepared.thumbnail_filename
            normalized_entries.append(normalized)

        if not normalized_entries:
            return 0

        with _gallery_file_write_lock:
            with _connect() as conn:
                _dedupe_import_entries_on_conn(conn, normalized_entries, prepared_files)

            _promote_prepared_images(prepared_files)

            with _storage_lock:
                with _connect() as conn:
                    with _transaction(conn):
                        with observe_job_stage("db_insert"):
                            _insert_gallery_entries_on_conn(conn, normalized_entries)

            _promote_prepared_thumbnails(prepared_files)
        return len(normalized_entries)
    except BaseException:
        _cleanup_prepared_gallery_files(prepared_files)
        raise


async def add_to_gallery_async(
    image_bytes: bytes,
    image_id: str,
    prompt: str,
    size: str,
    filename: str,
    metadata: dict[str, Any] | None = None,
) -> GalleryEntry:
    entry = _build_gallery_entry(
        image_id=image_id,
        prompt=prompt,
        size=size,
        filename=filename,
        metadata=metadata,
        image_bytes=image_bytes,
    )
    await asyncio.to_thread(
        _save_images_and_insert_gallery_entries,
        [(image_bytes, filename)],
        [entry],
    )
    return GalleryEntry(**_attach_gallery_thumbnail_url(entry))


def _stat_image_bytes(filename: str) -> int | None:
    path = safe_image_path(filename)
    if not path:
        return None
    try:
        return path.stat().st_size
    except OSError:
        return None


def _backfill_gallery_bytes_from_known_rows_on_conn(conn: sqlite3.Connection) -> int:
    before_changes = conn.total_changes
    with _transaction(conn):
        conn.execute(
            """
            UPDATE gallery_entries
            SET bytes = (
                SELECT MAX(known.bytes)
                FROM gallery_entries AS known
                WHERE known.filename = gallery_entries.filename
                  AND known.bytes IS NOT NULL
            )
            WHERE bytes IS NULL
              AND filename IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM gallery_entries AS known
                  WHERE known.filename = gallery_entries.filename
                    AND known.bytes IS NOT NULL
              )
            """
        )
    return conn.total_changes - before_changes


def _backfill_gallery_bytes_from_filenames() -> int:
    total_updated = 0
    batch_size = 200
    last_filename = ""
    while True:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT filename
                FROM gallery_entries
                WHERE filename IS NOT NULL
                  AND TRIM(filename) != ''
                  AND bytes IS NULL
                  AND filename > ?
                GROUP BY filename
                ORDER BY filename ASC
                LIMIT ?
                """,
                (last_filename, batch_size),
            ).fetchall()
        if not rows:
            break

        backfills: list[tuple[int, str]] = []
        for row in rows:
            stored_filename = str(row["filename"] or "")
            filename = stored_filename.strip()
            if not filename:
                continue
            last_filename = stored_filename
            size = _stat_image_bytes(filename)
            if size is None:
                continue
            backfills.append((size, stored_filename))

        if backfills:
            with _connect() as conn:
                before_changes = conn.total_changes
                with _transaction(conn):
                    conn.executemany(
                        """
                        UPDATE gallery_entries
                        SET bytes = ?
                        WHERE filename = ? AND bytes IS NULL
                        """,
                        backfills,
                    )
                total_updated += conn.total_changes - before_changes

        if len(rows) < batch_size:
            break

    return total_updated


def backfill_missing_gallery_bytes() -> int:
    """Backfill missing gallery byte sizes from disk.

    This is intentionally separated from the gallery request path so
    /api/gallery?include_total_bytes=true can stay SQL-only.
    """
    _ensure_database()
    with _connect() as conn:
        updated = _backfill_gallery_bytes_from_known_rows_on_conn(conn)
    updated += _backfill_gallery_bytes_from_filenames()
    if updated:
        _invalidate_gallery_total_bytes_cache()
    return updated


def _get_gallery_count_on_conn(
    conn: sqlite3.Connection,
    where_sql: str,
    params: Sequence[Any],
) -> int:
    cache_key = (config.DATABASE_FILE, where_sql, tuple(params))
    now = time.monotonic()
    with _gallery_count_cache_lock:
        cached = _gallery_count_cache.get(cache_key)
        if cached and (now - cached[0]) < GALLERY_COUNT_CACHE_SECONDS:
            _gallery_count_cache.move_to_end(cache_key)
            return cached[1]
        if cached:
            _gallery_count_cache.pop(cache_key, None)

    row = conn.execute(
        f"SELECT COUNT(*) FROM gallery_entries{where_sql}",
        tuple(params),
    ).fetchone()
    total = int(row[0]) if row else 0

    with _gallery_count_cache_lock:
        _gallery_count_cache[cache_key] = (now, total)
        _gallery_count_cache.move_to_end(cache_key)
        while len(_gallery_count_cache) > _GALLERY_COUNT_CACHE_MAX_SIZE:
            _gallery_count_cache.popitem(last=False)

    return total


def get_gallery_count(filters: dict[str, Any] | None = None) -> int:
    _ensure_database()
    with _connect() as conn:
        where_sql, params = _build_gallery_filter_where(filters)
        return _get_gallery_count_on_conn(conn, where_sql, params)


def get_gallery_ids(filters: dict[str, Any] | None = None) -> list[str]:
    _ensure_database()
    with _connect() as conn:
        where_sql, params = _build_gallery_filter_where(filters)
        rows = conn.execute(
            f"""
            SELECT id
            FROM gallery_entries
            {where_sql}
            ORDER BY sort_seq DESC, id DESC
            """,
            tuple(params),
        ).fetchall()
    return [str(row["id"]) for row in rows if row["id"]]


def _get_gallery_total_bytes_on_conn(
    conn: sqlite3.Connection,
    where_sql: str,
    params: Sequence[Any],
) -> int:
    cache_key = (config.DATABASE_FILE, where_sql, tuple(params))
    now = time.monotonic()
    with _gallery_total_bytes_cache_lock:
        cached = _gallery_total_bytes_cache.get(cache_key)
        if cached and (now - cached[0]) < GALLERY_TOTAL_BYTES_CACHE_SECONDS:
            _gallery_total_bytes_cache.move_to_end(cache_key)
            return cached[1]
        if cached:
            _gallery_total_bytes_cache.pop(cache_key, None)

    row = conn.execute(
        f"""
        SELECT COALESCE(SUM(bytes), 0) AS total_bytes
        FROM (
            SELECT filename, MAX(bytes) AS bytes
            FROM gallery_entries
            {where_sql}
            GROUP BY filename
        )
        WHERE bytes IS NOT NULL
        """,
        tuple(params),
    ).fetchone()
    total_bytes = int(row["total_bytes"] or 0) if row else 0

    with _gallery_total_bytes_cache_lock:
        _gallery_total_bytes_cache[cache_key] = (now, total_bytes)
        _gallery_total_bytes_cache.move_to_end(cache_key)
        while len(_gallery_total_bytes_cache) > _GALLERY_BYTES_CACHE_MAX_SIZE:
            _gallery_total_bytes_cache.popitem(last=False)

    return total_bytes


def get_gallery_total_bytes(filters: dict[str, Any] | None = None) -> int:
    _ensure_database()
    with _connect() as conn:
        where_sql, params = _build_gallery_filter_where(filters)
        return _get_gallery_total_bytes_on_conn(conn, where_sql, params)


def encode_gallery_cursor(sort_seq: int, image_id: str) -> str:
    payload = json.dumps(
        {"sort_seq": int(sort_seq), "id": str(image_id)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_gallery_cursor(cursor: str) -> tuple[int, str]:
    raw_cursor = str(cursor or "").strip()
    if not raw_cursor:
        raise ValueError("Gallery cursor is required")
    try:
        padded = raw_cursor + ("=" * (-len(raw_cursor) % 4))
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
        sort_seq = int(payload["sort_seq"])
        image_id = str(payload["id"])
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        binascii.Error,
    ) as e:
        raise ValueError("Invalid gallery cursor") from e
    if not image_id:
        raise ValueError("Invalid gallery cursor")
    return sort_seq, image_id


def _gallery_cursor_from_row(row: sqlite3.Row) -> str:
    return encode_gallery_cursor(int(row["sort_seq"] or 0), str(row["id"]))


def _combine_gallery_where(where_sql: str, clause: str) -> str:
    if where_sql:
        return f"{where_sql} AND {clause}"
    return f" WHERE {clause}"


def _gallery_has_row_before_cursor(
    conn: sqlite3.Connection,
    where_sql: str,
    params: Sequence[Any],
    row: sqlite3.Row,
) -> bool:
    sort_seq = int(row["sort_seq"] or 0)
    image_id = str(row["id"])
    cursor_where = _combine_gallery_where(
        where_sql,
        "(sort_seq > ? OR (sort_seq = ? AND id > ?))",
    )
    found = conn.execute(
        f"SELECT 1 FROM gallery_entries{cursor_where} LIMIT 1",
        (*params, sort_seq, sort_seq, image_id),
    ).fetchone()
    return found is not None


def _gallery_has_row_after_cursor(
    conn: sqlite3.Connection,
    where_sql: str,
    params: Sequence[Any],
    row: sqlite3.Row,
) -> bool:
    sort_seq = int(row["sort_seq"] or 0)
    image_id = str(row["id"])
    cursor_where = _combine_gallery_where(
        where_sql,
        "(sort_seq < ? OR (sort_seq = ? AND id < ?))",
    )
    found = conn.execute(
        f"SELECT 1 FROM gallery_entries{cursor_where} LIMIT 1",
        (*params, sort_seq, sort_seq, image_id),
    ).fetchone()
    return found is not None


def _get_gallery_thumbnail_status_map_on_conn(
    conn: sqlite3.Connection,
    rows: Sequence[sqlite3.Row],
) -> dict[str, str]:
    filenames = _unique_sqlite_values(row["filename"] for row in rows if row["filename"])
    if not filenames:
        return {}

    now = utc_now()
    queued_filenames: set[str] = set()
    for chunk in _iter_sqlite_in_chunks(filenames):
        placeholders = ", ".join("?" for _ in chunk)
        jobs = conn.execute(
            f"""
            SELECT filename, status, lease_expires_at
            FROM thumbnail_jobs
            WHERE filename IN ({placeholders})
            """,
            tuple(chunk),
        ).fetchall()
        for job in jobs:
            status = str(job["status"] or "")
            if status == "queued" or (
                status == "running" and str(job["lease_expires_at"] or "") > now
            ):
                queued_filenames.add(str(job["filename"]))

    return {filename: "queued" for filename in queued_filenames}


def _get_gallery_rows_on_conn(
    conn: sqlite3.Connection,
    where_sql: str,
    params: Sequence[Any],
    *,
    limit: int | None = None,
    offset: int | None = None,
) -> list[sqlite3.Row]:
    sql = f"""
        SELECT {", ".join(GALLERY_COLUMNS)}
        FROM gallery_entries
        {where_sql}
        ORDER BY sort_seq DESC, id DESC
    """
    query_params: list[Any] = list(params)
    if limit is not None:
        sql += " LIMIT ?"
        query_params.append(limit)
        if offset is not None:
            sql += " OFFSET ?"
            query_params.append(offset)
    return conn.execute(sql, query_params).fetchall()


def _get_gallery_page_rows_by_offset_on_conn(
    conn: sqlite3.Connection,
    components: _GalleryQueryComponents,
    *,
    page: int,
    limit: int,
) -> list[sqlite3.Row]:
    offset = (page - 1) * components.page_size
    return _get_gallery_rows_on_conn(
        conn,
        components.where_sql,
        components.params,
        limit=limit,
        offset=offset,
    )


def _get_gallery_anchor_for_page_on_conn(
    conn: sqlite3.Connection,
    components: _GalleryQueryComponents,
    *,
    gallery_version: int,
    page: int,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT page, sort_seq, image_id
        FROM gallery_page_anchors
        WHERE query_key = ?
          AND page_size = ?
          AND gallery_version = ?
          AND page <= ?
        ORDER BY page DESC
        LIMIT 1
        """,
        (components.query_key, components.page_size, gallery_version, page),
    ).fetchone()


def _store_gallery_page_anchor_best_effort(
    components: _GalleryQueryComponents,
    *,
    gallery_version: int,
    page: int,
    row: sqlite3.Row,
):
    if page < 1:
        return
    conn = _open_connection(timeout=0.0, busy_timeout_ms=0)
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as e:
            message = str(e).lower()
            if "locked" in message or "busy" in message:
                return
            raise
        current_version = _get_gallery_version_on_conn(conn)
        if current_version != gallery_version:
            conn.commit()
            return
        now = utc_now()
        conn.execute(
            """
            INSERT INTO gallery_page_anchors (
                query_key,
                page_size,
                page,
                sort_seq,
                image_id,
                created_at,
                updated_at,
                gallery_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(query_key, page_size, page) DO UPDATE SET
                sort_seq = excluded.sort_seq,
                image_id = excluded.image_id,
                updated_at = excluded.updated_at,
                gallery_version = excluded.gallery_version
            """,
            (
                components.query_key,
                components.page_size,
                page,
                int(row["sort_seq"] or 0),
                str(row["id"]),
                now,
                now,
                gallery_version,
            ),
        )
        stale_rows = conn.execute(
            """
            SELECT page
            FROM gallery_page_anchors
            WHERE query_key = ?
              AND page_size = ?
              AND gallery_version = ?
            ORDER BY updated_at DESC, page DESC
            LIMIT -1 OFFSET ?
            """,
            (
                components.query_key,
                components.page_size,
                gallery_version,
                GALLERY_PAGE_ANCHOR_MAX_PER_QUERY,
            ),
        ).fetchall()
        stale_pages = [int(stale["page"]) for stale in stale_rows]
        if stale_pages:
            placeholders = ", ".join("?" for _ in stale_pages)
            conn.execute(
                f"""
                DELETE FROM gallery_page_anchors
                WHERE query_key = ?
                  AND page_size = ?
                  AND gallery_version = ?
                  AND page IN ({placeholders})
                """,
                (
                    components.query_key,
                    components.page_size,
                    gallery_version,
                    *stale_pages,
                ),
            )
        conn.commit()
    except sqlite3.Error as e:
        if conn.in_transaction:
            conn.rollback()
        logger.debug("Failed to store gallery page anchor: %s", e)
    finally:
        conn.close()


def _get_gallery_rows_after_anchor_on_conn(
    conn: sqlite3.Connection,
    components: _GalleryQueryComponents,
    *,
    sort_seq: int,
    image_id: str,
    limit: int,
) -> list[sqlite3.Row]:
    cursor_where = _combine_gallery_where(
        components.where_sql,
        "(sort_seq < ? OR (sort_seq = ? AND id < ?))",
    )
    return conn.execute(
        f"""
        SELECT {", ".join(GALLERY_COLUMNS)}
        FROM gallery_entries
        {cursor_where}
        ORDER BY sort_seq DESC, id DESC
        LIMIT ?
        """,
        (*components.params, sort_seq, sort_seq, image_id, limit),
    ).fetchall()


def _get_gallery_page_rows_by_anchor_on_conn(
    conn: sqlite3.Connection,
    components: _GalleryQueryComponents,
    *,
    page: int,
    limit: int,
    timings_ms: dict[str, float],
) -> list[sqlite3.Row]:
    anchor_started_at = time.perf_counter()
    gallery_version = _get_gallery_version_on_conn(conn)
    anchor = _get_gallery_anchor_for_page_on_conn(
        conn,
        components,
        gallery_version=gallery_version,
        page=page,
    )
    interval = max(1, int(GALLERY_PAGE_ANCHOR_INTERVAL_PAGES))
    anchored_by_offset = False
    anchor_gap = page - int(anchor["page"]) if anchor is not None else interval + 1
    if anchor is None or anchor_gap > interval:
        anchor_page = max(1, ((page - 1) // interval) * interval)
        if anchor_page >= page:
            anchor_page = max(1, page - 1)
        anchor_rows = _get_gallery_page_rows_by_offset_on_conn(
            conn,
            components,
            page=anchor_page,
            limit=1,
        )
        if not anchor_rows:
            timings_ms["anchor_ms"] = round(
                (time.perf_counter() - anchor_started_at) * 1000,
                2,
            )
            timings_ms["anchor_scan_rows"] = 0.0
            return []
        _store_gallery_page_anchor_best_effort(
            components,
            gallery_version=gallery_version,
            page=anchor_page,
            row=anchor_rows[0],
        )
        anchor_page = int(anchor_page)
        anchor_sort_seq = int(anchor_rows[0]["sort_seq"] or 0)
        anchor_id = str(anchor_rows[0]["id"])
        anchored_by_offset = True
    else:
        anchor_page = int(anchor["page"])
        anchor_sort_seq = int(anchor["sort_seq"] or 0)
        anchor_id = str(anchor["image_id"])

    page_delta = max(0, page - anchor_page)
    if page_delta == 0:
        anchor_row = conn.execute(
            f"""
            SELECT {", ".join(GALLERY_COLUMNS)}
            FROM gallery_entries
            {_combine_gallery_where(components.where_sql, "sort_seq = ? AND id = ?")}
            LIMIT 1
            """,
            (*components.params, anchor_sort_seq, anchor_id),
        ).fetchone()
        scanned_rows = ([anchor_row] if anchor_row else []) + _get_gallery_rows_after_anchor_on_conn(
            conn,
            components,
            sort_seq=anchor_sort_seq,
            image_id=anchor_id,
            limit=max(0, limit - (1 if anchor_row else 0)),
        )
        result_rows = scanned_rows[:limit]
    else:
        rows_to_skip = page_delta * components.page_size - 1
        scan_limit = rows_to_skip + limit
        scanned_rows = _get_gallery_rows_after_anchor_on_conn(
            conn,
            components,
            sort_seq=anchor_sort_seq,
            image_id=anchor_id,
            limit=scan_limit,
        )
        result_rows = scanned_rows[rows_to_skip : rows_to_skip + limit]

    if result_rows:
        _store_gallery_page_anchor_best_effort(
            components,
            gallery_version=gallery_version,
            page=page,
            row=result_rows[0],
        )

    timings_ms["anchor_ms"] = round(
        (time.perf_counter() - anchor_started_at) * 1000,
        2,
    )
    timings_ms["anchor_scan_rows"] = float(len(scanned_rows))
    if anchored_by_offset:
        timings_ms["anchor_seeded_by_offset"] = 1.0
    return result_rows


def _get_gallery_row_batch_after_cursor_on_conn(
    conn: sqlite3.Connection,
    where_sql: str,
    params: Sequence[Any],
    *,
    last_sort_seq: int | None,
    last_id: str | None,
    limit: int,
    columns: Sequence[str] = GALLERY_COLUMNS,
) -> list[sqlite3.Row]:
    if last_sort_seq is None or last_id is None:
        sql = f"""
            SELECT {", ".join(columns)}
            FROM gallery_entries
            {where_sql}
            ORDER BY sort_seq DESC, id DESC
            LIMIT ?
        """
        query_params = list(params) + [limit]
    else:
        combined_where = _combine_gallery_where(
            where_sql,
            "(sort_seq < ? OR (sort_seq = ? AND id < ?))",
        )
        sql = f"""
            SELECT {", ".join(columns)}
            FROM gallery_entries
            {combined_where}
            ORDER BY sort_seq DESC, id DESC
            LIMIT ?
        """
        query_params = list(params) + [last_sort_seq, last_sort_seq, last_id, limit]
    return conn.execute(sql, query_params).fetchall()


def get_gallery(
    limit: int | None = None,
    offset: int | None = None,
    filters: dict[str, Any] | None = None,
) -> list[GalleryEntry]:
    _ensure_database()
    with _connect() as conn:
        where_sql, params = _build_gallery_filter_where(filters)
        rows = _get_gallery_rows_on_conn(
            conn,
            where_sql,
            params,
            limit=limit,
            offset=offset,
        )
        thumbnail_status_map = _get_gallery_thumbnail_status_map_on_conn(conn, rows)
    return [
        GalleryEntry(**_gallery_entry_from_row(row, thumbnail_status_map))
        for row in rows
    ]


def iter_gallery_export_rows(
    filters: dict[str, Any] | None = None,
    *,
    batch_size: int = 200,
) -> Iterator[dict[str, Any]]:
    """Yield gallery entries as plain dicts for export use cases.

    Uses cursor-based (keyset) pagination to avoid O(n^2) OFFSET scanning.
    """
    _ensure_database()
    where_sql, params = _build_gallery_filter_where(filters)
    last_sort_seq: int | None = None
    last_id: str | None = None
    while True:
        with _connect() as conn:
            rows = _get_gallery_row_batch_after_cursor_on_conn(
                conn,
                where_sql,
                params,
                last_sort_seq=last_sort_seq,
                last_id=last_id,
                limit=batch_size,
            )
            thumbnail_status_map = _get_gallery_thumbnail_status_map_on_conn(conn, rows)
        if not rows:
            return
        for row in rows:
            yield _gallery_entry_from_row(row, thumbnail_status_map)
        if len(rows) < batch_size:
            return
        last_row = rows[-1]
        last_sort_seq = int(last_row["sort_seq"] or 0)
        last_id = str(last_row["id"])


def _gallery_r2_sync_changed_condition() -> str:
    return """
        (
            state.filename IS NULL
            OR (local.sha256 IS NOT NULL AND COALESCE(state.sha256, '') != local.sha256)
            OR (local.bytes IS NOT NULL AND COALESCE(state.bytes, 0) != local.bytes)
            OR state.key != (? || local.filename)
        )
    """


def count_gallery_r2_sync_rows(
    *,
    key_prefix: str = "",
    full_reconcile: bool = False,
    start_after_filename: str = "",
) -> int:
    """Count unique local filenames that R2 sync should compare."""
    _ensure_database()
    start_after = str(start_after_filename or "")
    with _connect() as conn:
        if full_reconcile:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT filename
                    FROM gallery_entries
                    WHERE filename IS NOT NULL
                        AND trim(filename) != ''
                        AND filename > ?
                    GROUP BY filename
                )
                """,
                (start_after,),
            ).fetchone()
        else:
            row = conn.execute(
                f"""
                WITH local AS (
                    SELECT
                        MIN(id) AS id,
                        filename,
                        MAX(bytes) AS bytes,
                        MAX(NULLIF(sha256, '')) AS sha256
                    FROM gallery_entries
                    WHERE filename IS NOT NULL
                        AND trim(filename) != ''
                        AND filename > ?
                    GROUP BY filename
                )
                SELECT COUNT(*)
                FROM local
                LEFT JOIN r2_sync_state state ON state.filename = local.filename
                WHERE {_gallery_r2_sync_changed_condition()}
                """,
                (start_after, key_prefix),
            ).fetchone()
    return int(row[0] or 0) if row else 0


def iter_gallery_r2_sync_rows(
    *,
    key_prefix: str = "",
    full_reconcile: bool = False,
    start_after_filename: str = "",
    batch_size: int = GALLERY_SYNC_BATCH_SIZE,
) -> Iterator[dict[str, Any]]:
    """Yield minimal, filename-unique rows for R2 sync.

    The default path only yields local filenames that are new or changed relative
    to r2_sync_state. full_reconcile ignores that cache so remote deletions can
    be detected without using export-shaped rows.
    """
    _ensure_database()
    normalized_batch_size = max(1, int(batch_size or GALLERY_SYNC_BATCH_SIZE))
    last_filename = str(start_after_filename or "")
    while True:
        with _connect() as conn:
            if full_reconcile:
                rows = conn.execute(
                    """
                    SELECT
                        MIN(id) AS id,
                        filename,
                        MAX(bytes) AS bytes,
                        MAX(NULLIF(sha256, '')) AS sha256
                    FROM gallery_entries
                    WHERE filename IS NOT NULL
                        AND trim(filename) != ''
                        AND filename > ?
                    GROUP BY filename
                    ORDER BY filename ASC
                    LIMIT ?
                    """,
                    (last_filename, normalized_batch_size),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    WITH local AS (
                        SELECT
                            MIN(id) AS id,
                            filename,
                            MAX(bytes) AS bytes,
                            MAX(NULLIF(sha256, '')) AS sha256
                        FROM gallery_entries
                        WHERE filename IS NOT NULL
                            AND trim(filename) != ''
                            AND filename > ?
                        GROUP BY filename
                    )
                    SELECT local.id, local.filename, local.bytes, local.sha256
                    FROM local
                    LEFT JOIN r2_sync_state state ON state.filename = local.filename
                    WHERE {_gallery_r2_sync_changed_condition()}
                    ORDER BY local.filename ASC
                    LIMIT ?
                    """,
                    (last_filename, key_prefix, normalized_batch_size),
                ).fetchall()
        if not rows:
            return
        for row in rows:
            yield {
                "id": row["id"],
                "filename": row["filename"],
                "bytes": row["bytes"],
                "sha256": row["sha256"],
            }
        if len(rows) < normalized_batch_size:
            return
        last_filename = str(rows[-1]["filename"] or "")


def mark_gallery_r2_sync_state(rows: Iterable[dict[str, Any]]) -> None:
    """Mark local filenames as confirmed in R2."""
    prepared: list[tuple[str, str | None, int, str, str | None, str, str]] = []
    synced_at = utc_now()
    for row in rows:
        filename = str(row.get("filename") or "").strip()
        key = str(row.get("key") or "").strip()
        if not filename or not key:
            continue
        sha256 = str(row.get("sha256") or "").strip() or None
        byte_size = _coerce_nonnegative_int(row.get("bytes"), 0)
        etag = str(row.get("etag") or "").strip() or None
        last_remote_seen_at = str(row.get("last_remote_seen_at") or "").strip() or synced_at
        prepared.append((filename, sha256, byte_size, key, etag, last_remote_seen_at, synced_at))
    if not prepared:
        return

    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            conn.executemany(
                """
                INSERT INTO r2_sync_state (
                    filename, sha256, bytes, key, etag, last_remote_seen_at, synced_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(filename) DO UPDATE SET
                    sha256 = excluded.sha256,
                    bytes = excluded.bytes,
                    key = excluded.key,
                    etag = excluded.etag,
                    last_remote_seen_at = excluded.last_remote_seen_at,
                    synced_at = excluded.synced_at
                """,
                prepared,
            )


def update_gallery_entry_hash(filename: str, sha256: str, byte_size: int) -> None:
    """Backfill sha256/bytes for entries sharing a filename. Best-effort."""
    if not filename or not sha256:
        return
    _ensure_database()
    try:
        with _connect() as conn:
            with _transaction(conn):
                conn.execute(
                    """
                    UPDATE gallery_entries
                    SET sha256 = CASE
                            WHEN sha256 IS NULL OR sha256 = '' THEN ?
                            ELSE sha256
                        END,
                        bytes = COALESCE(bytes, ?)
                    WHERE filename = ?
                      AND (
                          sha256 IS NULL OR sha256 = ''
                          OR bytes IS NULL
                      )
                    """,
                    (sha256, byte_size, filename),
                )
                _invalidate_gallery_total_bytes_cache()
    except sqlite3.Error as e:
        logger.warning("Failed to persist sha256 for %s: %s", filename, e)


def _get_gallery_filter_options_on_conn(conn: sqlite3.Connection) -> GalleryFilterOptions:
    global _filter_options_cache
    cache_version = _get_filter_options_cache_version()
    with _filter_options_cache_lock:
        cached = _filter_options_cache
        if cached is not None and cached.version == cache_version:
            return cached.options

    options: dict[str, list[str]] = {}
    for key, kind in (
        ("models", "model"),
        ("presets", "preset"),
        ("sizes", "size"),
    ):
        rows = conn.execute(
            """
            SELECT value
            FROM gallery_filter_options
            WHERE kind = ? AND ref_count > 0
            ORDER BY LOWER(value) ASC
            """,
            (kind,),
        ).fetchall()
        options[key] = [row["value"] for row in rows if row["value"]]

    result = GalleryFilterOptions(**options)
    with _filter_options_cache_lock:
        _filter_options_cache = _GalleryFilterOptionsCacheEntry(
            version=cache_version,
            options=result,
        )
    return result


def get_gallery_filter_options() -> GalleryFilterOptions:
    _ensure_database()
    with _connect() as conn:
        return _get_gallery_filter_options_on_conn(conn)


def _normalize_gallery_page_components(
    *,
    page: int,
    page_size: int,
    filters: dict[str, Any] | None,
    include_total_bytes: bool,
    include_counts: bool,
    include_filter_options: bool,
    cursor: str | None,
    direction: str,
) -> _GalleryQueryComponents:
    requested_page = max(int(page), 1)
    normalized_page_size = max(int(page_size), 1)
    normalized_cursor = str(cursor or "").strip()
    normalized_direction = str(direction or "next").strip().lower()
    if normalized_direction not in {"next", "prev"}:
        raise ValueError("Invalid gallery cursor direction")

    decoded_cursor = (
        decode_gallery_cursor(normalized_cursor) if normalized_cursor else None
    )
    where_sql, params = _build_gallery_filter_where(filters)
    query_key = _gallery_query_key_from_components(where_sql, params)
    return _GalleryQueryComponents(
        where_sql=where_sql,
        params=params,
        query_key=query_key,
        requested_page=requested_page,
        page_size=normalized_page_size,
        include_counts=include_counts,
        include_filter_options=include_filter_options,
        include_total_bytes=include_total_bytes,
        decoded_cursor=decoded_cursor,
        direction=normalized_direction,
        has_filters=bool(where_sql),
    )


def _get_gallery_page_rows_on_conn(
    conn: sqlite3.Connection,
    components: _GalleryQueryComponents,
    timings_ms: dict[str, float],
) -> _GalleryPaginationState:
    effective_page = components.requested_page
    total = 0
    total_pages = 1
    page_has_sentinel = False

    if components.decoded_cursor is None and components.include_counts:
        count_started_at = time.perf_counter()
        total = _get_gallery_count_on_conn(
            conn, components.where_sql, components.params
        )
        timings_ms["count_ms"] = round(
            (time.perf_counter() - count_started_at) * 1000,
            2,
        )
        total_pages = max((total + components.page_size - 1) // components.page_size, 1)
        effective_page = min(components.requested_page, total_pages)

    rows_started_at = time.perf_counter()
    if components.decoded_cursor is None:
        offset = (effective_page - 1) * components.page_size
        if (
            effective_page > 1
            and offset > GALLERY_PAGE_ANCHOR_SMALL_OFFSET_THRESHOLD
        ):
            rows = _get_gallery_page_rows_by_anchor_on_conn(
                conn,
                components,
                page=effective_page,
                limit=components.page_size + 1,
                timings_ms=timings_ms,
            )
        else:
            rows = _get_gallery_page_rows_by_offset_on_conn(
                conn,
                components,
                page=effective_page,
                limit=components.page_size + 1,
            )
        page_has_sentinel = len(rows) > components.page_size
        has_next = page_has_sentinel
        if has_next:
            rows = rows[: components.page_size]
        if components.include_counts:
            has_next = effective_page < total_pages
        has_prev = effective_page > 1
    else:
        cursor_sort_seq, cursor_id = components.decoded_cursor
        if components.direction == "prev":
            cursor_where = _combine_gallery_where(
                components.where_sql,
                "(sort_seq > ? OR (sort_seq = ? AND id > ?))",
            )
            raw_rows = conn.execute(
                f"""
                SELECT {", ".join(GALLERY_COLUMNS)}
                FROM gallery_entries
                {cursor_where}
                ORDER BY sort_seq ASC, id ASC
                LIMIT ?
                """,
                (
                    *components.params,
                    cursor_sort_seq,
                    cursor_sort_seq,
                    cursor_id,
                    components.page_size + 1,
                ),
            ).fetchall()
            has_prev = len(raw_rows) > components.page_size
            if has_prev:
                raw_rows = raw_rows[: components.page_size]
            rows = list(reversed(raw_rows))
            has_next = (
                False
                if not rows
                else _gallery_has_row_after_cursor(
                    conn, components.where_sql, components.params, rows[-1]
                )
            )
        else:
            cursor_where = _combine_gallery_where(
                components.where_sql,
                "(sort_seq < ? OR (sort_seq = ? AND id < ?))",
            )
            rows = conn.execute(
                f"""
                SELECT {", ".join(GALLERY_COLUMNS)}
                FROM gallery_entries
                {cursor_where}
                ORDER BY sort_seq DESC, id DESC
                LIMIT ?
                """,
                (
                    *components.params,
                    cursor_sort_seq,
                    cursor_sort_seq,
                    cursor_id,
                    components.page_size + 1,
                ),
            ).fetchall()
            has_next = len(rows) > components.page_size
            if has_next:
                rows = rows[: components.page_size]
            has_prev = (
                False
                if not rows
                else _gallery_has_row_before_cursor(
                    conn, components.where_sql, components.params, rows[0]
                )
            )
    timings_ms["rows_ms"] = round((time.perf_counter() - rows_started_at) * 1000, 2)

    if components.include_counts and components.decoded_cursor is not None:
        count_started_at = time.perf_counter()
        total = _get_gallery_count_on_conn(conn, components.where_sql, components.params)
        timings_ms["count_ms"] = round(
            (time.perf_counter() - count_started_at) * 1000,
            2,
        )
        total_pages = max((total + components.page_size - 1) // components.page_size, 1)
        effective_page = min(components.requested_page, total_pages)
    elif not components.include_counts:
        total_pages = max(components.requested_page + (1 if has_next else 0), 1)
        effective_page = components.requested_page

    if (
        components.decoded_cursor is None
        and components.requested_page == 1
        and not components.has_filters
    ):
        has_prev = False
        if not components.include_counts:
            has_next = page_has_sentinel

    return _GalleryPaginationState(
        rows=rows,
        has_prev=has_prev,
        has_next=has_next,
        effective_page=effective_page,
        total=total,
        total_pages=total_pages,
    )


def _get_gallery_page_total_bytes_on_conn(
    conn: sqlite3.Connection,
    components: _GalleryQueryComponents,
    timings_ms: dict[str, float],
) -> int:
    if not components.include_total_bytes:
        return 0
    total_bytes_started_at = time.perf_counter()
    total_bytes = _get_gallery_total_bytes_on_conn(
        conn, components.where_sql, components.params
    )
    timings_ms["total_bytes_ms"] = round(
        (time.perf_counter() - total_bytes_started_at) * 1000,
        2,
    )
    return total_bytes


def _get_gallery_page_filter_options_on_conn(
    conn: sqlite3.Connection,
    components: _GalleryQueryComponents,
    timings_ms: dict[str, float],
) -> GalleryFilterOptions:
    if not components.include_filter_options:
        return GalleryFilterOptions()
    filter_options_started_at = time.perf_counter()
    filter_options = _get_gallery_filter_options_on_conn(conn)
    timings_ms["filter_options_ms"] = round(
        (time.perf_counter() - filter_options_started_at) * 1000,
        2,
    )
    return filter_options


def get_gallery_page(
    *,
    page: int = 1,
    page_size: int = 9,
    filters: dict[str, Any] | None = None,
    include_total_bytes: bool = False,
    include_counts: bool = True,
    include_filter_options: bool = True,
    cursor: str | None = None,
    direction: str = "next",
) -> GalleryPage:
    _ensure_database()
    query_started_at = time.perf_counter()
    timings_ms: dict[str, float] = {
        "rows_ms": 0.0,
        "count_ms": 0.0,
        "total_bytes_ms": 0.0,
        "filter_options_ms": 0.0,
    }
    with _connect() as conn:
        components = _normalize_gallery_page_components(
            page=page,
            page_size=page_size,
            filters=filters,
            include_total_bytes=include_total_bytes,
            include_counts=include_counts,
            include_filter_options=include_filter_options,
            cursor=cursor,
            direction=direction,
        )
        pagination = _get_gallery_page_rows_on_conn(conn, components, timings_ms)
        total_bytes = _get_gallery_page_total_bytes_on_conn(conn, components, timings_ms)
        filter_options = _get_gallery_page_filter_options_on_conn(conn, components, timings_ms)
        thumbnail_status_map = _get_gallery_thumbnail_status_map_on_conn(
            conn, pagination.rows
        )
        prev_cursor = (
            _gallery_cursor_from_row(pagination.rows[0]) if pagination.rows and pagination.has_prev else None
        )
        next_cursor = (
            _gallery_cursor_from_row(pagination.rows[-1]) if pagination.rows and pagination.has_next else None
        )
        images = [
            GalleryEntry(**_gallery_entry_from_row(row, thumbnail_status_map))
            for row in pagination.rows
        ]
    query_elapsed_ms = (time.perf_counter() - query_started_at) * 1000

    return GalleryPage(
        total=pagination.total,
        total_bytes=total_bytes,
        page=pagination.effective_page,
        page_size=components.page_size,
        total_pages=pagination.total_pages,
        has_prev=pagination.has_prev,
        has_next=pagination.has_next,
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
        images=images,
        filter_options=filter_options,
        query_elapsed_ms=round(query_elapsed_ms, 2),
        timings_ms=timings_ms,
        counts_included=include_counts,
        filter_options_included=include_filter_options,
    )


def get_gallery_entry(image_id: str) -> GalleryEntry | None:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT {", ".join(GALLERY_COLUMNS)}
            FROM gallery_entries
            WHERE id = ?
            """,
            (image_id,),
        ).fetchone()
        thumbnail_status_map = _get_gallery_thumbnail_status_map_on_conn(
            conn, [row] if row else []
        )
    if not row:
        return None
    return GalleryEntry(**_gallery_entry_from_row(row, thumbnail_status_map))


def get_gallery_entries_by_ids(image_ids: Sequence[str]) -> list[GalleryEntry]:
    """Fetch gallery entries for many ids in one query, preserving input order.

    Duplicate or missing ids are dropped.
    """
    _ensure_database()
    unique_ids = _unique_sqlite_values(image_ids)
    if not unique_ids:
        return []

    rows_by_id: dict[str, sqlite3.Row] = {}
    with _connect() as conn:
        for chunk in _iter_sqlite_in_chunks(unique_ids):
            placeholders = ", ".join("?" for _ in chunk)
            rows = conn.execute(
                f"""
                SELECT {", ".join(GALLERY_COLUMNS)}
                FROM gallery_entries
                WHERE id IN ({placeholders})
                """,
                tuple(chunk),
            ).fetchall()
            rows_by_id.update({row["id"]: row for row in rows})
        thumbnail_status_map = _get_gallery_thumbnail_status_map_on_conn(
            conn, rows_by_id.values()
        )

    return [
        GalleryEntry(**_gallery_entry_from_row(rows_by_id[image_id], thumbnail_status_map))
        for image_id in unique_ids
        if image_id in rows_by_id
    ]


def _get_all_filenames_on_conn(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT filename FROM gallery_entries WHERE filename IS NOT NULL"
    ).fetchall()
    return [row["filename"] for row in rows if row["filename"]]


def get_all_filenames() -> list[str]:
    """Return all filenames in the gallery without loading full entry objects."""
    _ensure_database()
    with _connect() as conn:
        return _get_all_filenames_on_conn(conn)


def get_all_gallery_ids() -> list[str]:
    _ensure_database()
    with _connect() as conn:
        rows = conn.execute("SELECT id FROM gallery_entries").fetchall()
        return [row["id"] for row in rows if row["id"]]


def add_to_gallery_sync(
    image_id: str,
    prompt: str,
    size: str,
    filename: str,
    metadata: dict[str, Any] | None = None,
    image_bytes: bytes | None = None,
) -> GalleryEntry:
    """Synchronous gallery insert — used only in tests."""
    entry = _build_gallery_entry(
        image_id=image_id,
        prompt=prompt,
        size=size,
        filename=filename,
        metadata=metadata,
        image_bytes=image_bytes,
    )
    if image_bytes is not None:
        _save_images_and_insert_gallery_entries([(image_bytes, filename)], [entry])
    else:
        _ensure_database()
        with _connect() as conn:
            with _transaction(conn):
                _insert_gallery_entries_on_conn(conn, [entry])
    return GalleryEntry(**_attach_gallery_thumbnail_url(entry))


def update_gallery_entry(image_id: str, updates: dict[str, Any]) -> GalleryEntry | None:
    allowed_updates = {
        key: _normalize_gallery_favorite(value) if key == "favorite" else value
        for key, value in updates.items()
        if key in GALLERY_COLUMNS and key != "id" and value is not None
    }

    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                f"""
                SELECT {", ".join(GALLERY_COLUMNS)}
                FROM gallery_entries
                WHERE id = ?
                """,
                (image_id,),
            ).fetchone()
            if not row:
                return None

            if allowed_updates:
                previous_filter_values = {
                    "model": row["model"],
                    "api_preset_name": row["api_preset_name"],
                    "size": row["size"],
                }
                assignments = ", ".join(f"{key} = ?" for key in allowed_updates)
                conn.execute(
                    f"UPDATE gallery_entries SET {assignments} WHERE id = ?",
                    (*allowed_updates.values(), image_id),
                )
                if allowed_updates.keys() & GALLERY_PAGE_ANCHOR_INVALIDATING_UPDATE_FIELDS:
                    _invalidate_gallery_query_caches_on_conn(conn)
                elif "bytes" in allowed_updates:
                    _invalidate_gallery_total_bytes_cache()
                row = conn.execute(
                    f"""
                    SELECT {", ".join(GALLERY_COLUMNS)}
                    FROM gallery_entries
                    WHERE id = ?
                    """,
                    (image_id,),
                ).fetchone()
                if allowed_updates.keys() & {"model", "api_preset_name", "size"}:
                    _increment_gallery_filter_options_on_conn(
                        conn,
                        previous_filter_values,
                        -1,
                    )
                    _increment_gallery_filter_options_on_conn(conn, row, 1)

    with _connect() as conn:
        thumbnail_status_map = _get_gallery_thumbnail_status_map_on_conn(conn, [row])
    return GalleryEntry(**_gallery_entry_from_row(row, thumbnail_status_map))


def update_gallery_entries_favorite(image_ids: list[str], favorite: bool) -> int:
    _ensure_database()
    unique_ids = _unique_sqlite_values(image_ids)
    if not unique_ids:
        return 0

    with _connect() as conn:
        with _transaction(conn):
            found_ids: list[str] = []
            for chunk in _iter_sqlite_in_chunks(unique_ids):
                select_placeholders = ", ".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT id FROM gallery_entries WHERE id IN ({select_placeholders})",
                    tuple(chunk),
                ).fetchall()
                found_ids.extend(str(row["id"]) for row in rows if row["id"])

            if not found_ids:
                return 0

            normalized_favorite = _normalize_gallery_favorite(favorite)
            for chunk in _iter_sqlite_in_chunks(found_ids):
                update_placeholders = ", ".join("?" for _ in chunk)
                conn.execute(
                    f"UPDATE gallery_entries SET favorite = ? WHERE id IN ({update_placeholders})",
                    (normalized_favorite, *chunk),
                )
            _invalidate_gallery_query_caches_on_conn(conn)
            return len(found_ids)


def _update_gallery_entries_favorite_by_where_on_conn(
    conn: sqlite3.Connection,
    where_sql: str,
    params: Sequence[Any],
    normalized_favorite: int,
) -> tuple[int, int]:
    count_row = conn.execute(
        f"SELECT COUNT(*) FROM gallery_entries{where_sql}",
        tuple(params),
    ).fetchone()
    matched_count = int(count_row[0] or 0) if count_row else 0
    if matched_count <= 0:
        return 0, 0

    update_where_sql = _combine_gallery_where(where_sql, "favorite != ?")
    conn.execute(
        f"""
        UPDATE gallery_entries
        SET favorite = ?
        {update_where_sql}
        """,
        (normalized_favorite, *params, normalized_favorite),
    )
    row = conn.execute("SELECT changes()").fetchone()
    updated_count = int(row[0] or 0) if row else 0
    return matched_count, updated_count


def update_gallery_entries_favorite_by_filters(
    filters: dict[str, Any] | None,
    favorite: bool,
    *,
    batch_size: int = 500,
) -> int:
    _ensure_database()
    normalized_favorite = _normalize_gallery_favorite(favorite)
    where_sql, params = _build_gallery_filter_where(filters)

    with _connect() as conn:
        with _transaction(conn):
            matched_count, updated_count = _update_gallery_entries_favorite_by_where_on_conn(
                conn,
                where_sql,
                params,
                normalized_favorite,
            )
            if updated_count:
                _invalidate_gallery_query_caches_on_conn(conn)

    return matched_count


def upsert_generate_job(job: dict[str, Any]) -> dict[str, Any]:
    _ensure_database()
    normalized = _normalize_generate_job(job)

    with _connect() as conn:
        with _transaction(conn):
            _upsert_generate_job_on_conn(conn, normalized)
    return normalized


def get_generate_job(job_id: str) -> dict[str, Any] | None:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT {", ".join(GENERATE_JOB_COLUMNS)}
            FROM generate_jobs
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
    if not row:
        return None
    return _generate_job_from_row(row)


def get_generate_job_updated_at_edge(job_id: str) -> str | None:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            "SELECT updated_at FROM generate_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    return str(row["updated_at"]) if row else None


def get_generate_jobs_list_updated_at_edge(
    *,
    statuses: set[str] | None = None,
) -> tuple[int, str]:
    _ensure_database()
    params: list[Any] = []
    sql = "SELECT COUNT(*) AS row_count, MAX(updated_at) AS updated_at FROM generate_jobs"
    if statuses:
        placeholders = ", ".join("?" for _ in statuses)
        sql += f" WHERE status IN ({placeholders})"
        params.extend(sorted(statuses))
    with _connect() as conn:
        row = conn.execute(sql, params).fetchone()
    if not row:
        return 0, ""
    return int(row["row_count"] or 0), str(row["updated_at"] or "")


def get_generate_jobs_updated_at_edges(
    *,
    statuses: set[str] | None = None,
    job_ids: set[str] | None = None,
) -> dict[str, str]:
    _ensure_database()
    params: list[Any] = []
    where: list[str] = []
    unique_statuses = _unique_sqlite_values(statuses or []) if statuses else []
    if unique_statuses:
        placeholders = ", ".join("?" for _ in unique_statuses)
        where.append(f"status IN ({placeholders})")
        params.extend(unique_statuses)

    if job_ids is not None:
        unique_job_ids = _unique_sqlite_values(job_ids)
        if not unique_job_ids:
            return {}
        rows_by_job_id: dict[str, str] = {}
        base_sql = "SELECT job_id, updated_at FROM generate_jobs"
        if where:
            base_sql += " WHERE " + " AND ".join(where)
        with _connect() as conn:
            for chunk in _iter_sqlite_in_chunks(unique_job_ids):
                placeholders = ", ".join("?" for _ in chunk)
                sql = f"{base_sql}{' AND' if where else ' WHERE'} job_id IN ({placeholders})"
                rows = conn.execute(sql, [*params, *chunk]).fetchall()
                rows_by_job_id.update(
                    {str(row["job_id"]): str(row["updated_at"]) for row in rows}
                )
        return rows_by_job_id

    sql = "SELECT job_id, updated_at FROM generate_jobs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {str(row["job_id"]): str(row["updated_at"]) for row in rows}


def create_gallery_job(**job: Any) -> dict[str, Any]:
    _ensure_database()
    normalized = _normalize_gallery_job(job)
    columns_sql = ", ".join(GALLERY_JOB_COLUMNS)
    placeholders_sql = ", ".join("?" for _ in GALLERY_JOB_COLUMNS)
    with _connect() as conn:
        with _transaction(conn):
            conn.execute(
                f"INSERT INTO gallery_jobs ({columns_sql}) VALUES ({placeholders_sql})",
                _gallery_job_values(normalized),
            )
    return normalized | {"payload": _json_loads_dict(normalized.get("payload_json"))}


def get_gallery_job(kind: str, job_id: str) -> dict[str, Any] | None:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT {", ".join(GALLERY_JOB_COLUMNS)}
            FROM gallery_jobs
            WHERE kind = ? AND job_id = ?
            """,
            (kind, job_id),
        ).fetchone()
    return _gallery_job_from_row(row) if row else None


def get_gallery_jobs_updated_at_edges(kind: str, job_ids: set[str]) -> dict[str, str]:
    _ensure_database()
    unique_job_ids = _unique_sqlite_values(job_ids)
    if not unique_job_ids:
        return {}
    rows_by_job_id: dict[str, str] = {}
    with _connect() as conn:
        for chunk in _iter_sqlite_in_chunks(unique_job_ids):
            placeholders = ", ".join("?" for _ in chunk)
            rows = conn.execute(
                f"""
                SELECT job_id, updated_at
                FROM gallery_jobs
                WHERE kind = ? AND job_id IN ({placeholders})
                """,
                (kind, *chunk),
            ).fetchall()
            rows_by_job_id.update({str(row["job_id"]): str(row["updated_at"]) for row in rows})
    return rows_by_job_id


def update_gallery_job(job_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    _ensure_database()
    if not updates:
        with _connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(GALLERY_JOB_COLUMNS)} FROM gallery_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return _gallery_job_from_row(row) if row else None

    allowed = set(GALLERY_JOB_COLUMNS) - {"job_id", "kind", "created_at", "payload_json"}
    normalized: dict[str, Any] = {}
    integer_columns = {
        "progress",
        "requested_count",
        "processed_count",
        "exported_count",
        "missing_count",
        "total_count",
        "compared_count",
        "uploaded_count",
        "pending_upload_count",
        "skipped_existing_count",
        "missing_local_count",
        "failed_count",
        "bytes_total",
        "bytes_written",
        "bytes_uploaded",
    }
    for key, value in updates.items():
        if key == "payload":
            key = "payload_json"
        if key not in allowed and key != "payload_json":
            continue
        if key == "payload_json":
            try:
                normalized[key] = (
                    value
                    if isinstance(value, str)
                    else json.dumps(value or {}, ensure_ascii=False, sort_keys=True)
                )
            except TypeError:
                continue
            continue
        if key in integer_columns:
            normalized[key] = _coerce_nonnegative_int(value, 0)
        else:
            normalized[key] = None if value is None else str(value)
    normalized["updated_at"] = str(updates.get("updated_at") or utc_now())

    assignments = ", ".join(f"{key} = ?" for key in normalized)
    with _connect() as conn:
        with _transaction(conn):
            conn.execute(
                f"UPDATE gallery_jobs SET {assignments} WHERE job_id = ?",
                (*normalized.values(), job_id),
            )
            row = conn.execute(
                f"SELECT {', '.join(GALLERY_JOB_COLUMNS)} FROM gallery_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
    return _gallery_job_from_row(row) if row else None


def _normalize_gallery_job_updates(updates: dict[str, Any]) -> dict[str, Any]:
    allowed = set(GALLERY_JOB_COLUMNS) - {"job_id", "kind", "created_at", "payload_json"}
    normalized: dict[str, Any] = {}
    integer_columns = {
        "progress",
        "requested_count",
        "processed_count",
        "exported_count",
        "missing_count",
        "total_count",
        "compared_count",
        "uploaded_count",
        "pending_upload_count",
        "skipped_existing_count",
        "missing_local_count",
        "failed_count",
        "bytes_total",
        "bytes_written",
        "bytes_uploaded",
    }
    for key, value in updates.items():
        if key == "payload":
            key = "payload_json"
        if key not in allowed and key != "payload_json":
            continue
        if key == "payload_json":
            try:
                normalized[key] = (
                    value
                    if isinstance(value, str)
                    else json.dumps(value or {}, ensure_ascii=False, sort_keys=True)
                )
            except TypeError:
                continue
            continue
        if key in integer_columns:
            normalized[key] = _coerce_nonnegative_int(value, 0)
        else:
            normalized[key] = None if value is None else str(value)
    normalized["updated_at"] = str(updates.get("updated_at") or utc_now())
    return normalized


def update_gallery_job_progress(job_id: str, updates: dict[str, Any]) -> bool:
    """Update a gallery job without fetching the row back.

    Used for high-frequency export/sync progress writes where SSE only needs the
    updated_at edge and will read the full row on its own poll.
    """
    _ensure_database()
    normalized = _normalize_gallery_job_updates(updates)
    if not normalized:
        return False
    assignments = ", ".join(f"{key} = ?" for key in normalized)
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                f"UPDATE gallery_jobs SET {assignments} WHERE job_id = ?",
                (*normalized.values(), job_id),
            )
    return cursor.rowcount > 0


def count_active_gallery_jobs(kind: str) -> int:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM gallery_jobs
            WHERE kind = ?
                AND (
                    status = 'queued'
                    OR (
                        status = 'running'
                        AND (lease_expires_at IS NULL OR lease_expires_at > ?)
                    )
                )
            """,
            (kind, now),
        ).fetchone()
    return int(row[0] or 0) if row else 0


def reserve_gallery_job_capacity(
    *,
    job: dict[str, Any],
    counted_kinds: Sequence[str],
    max_active: int,
) -> dict[str, Any] | None:
    _ensure_database()
    normalized = _normalize_gallery_job(job)
    kinds = [str(kind) for kind in counted_kinds if str(kind)]
    if normalized["kind"] not in kinds:
        kinds.append(normalized["kind"])
    placeholders = ", ".join("?" for _ in kinds)
    now = utc_now()
    columns_sql = ", ".join(GALLERY_JOB_COLUMNS)
    placeholders_sql = ", ".join("?" for _ in GALLERY_JOB_COLUMNS)
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM gallery_jobs
                WHERE kind IN ({placeholders})
                    AND (
                        status = 'queued'
                        OR (
                            status = 'running'
                            AND (lease_expires_at IS NULL OR lease_expires_at > ?)
                        )
                    )
                """,
                (*kinds, now),
            ).fetchone()
            active_count = int(row[0] or 0) if row else 0
            if active_count >= max(1, int(max_active or 1)):
                return None
            conn.execute(
                f"INSERT INTO gallery_jobs ({columns_sql}) VALUES ({placeholders_sql})",
                _gallery_job_values(normalized),
            )
    return normalized | {"payload": _json_loads_dict(normalized.get("payload_json"))}


def claim_next_gallery_job(
    *,
    kind: str,
    worker_id: str,
    lease_expires_at: str,
    now: str,
    running_limit: int,
    counted_kinds: Sequence[str] | None = None,
) -> dict[str, Any] | None:
    _ensure_database()
    active_kinds = [str(value) for value in (counted_kinds or (kind,)) if str(value)]
    if kind not in active_kinds:
        active_kinds.append(kind)
    active_placeholders = ", ".join("?" for _ in active_kinds)
    with _connect() as conn:
        with _transaction(conn):
            running_row = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM gallery_jobs
                WHERE kind IN ({active_placeholders}) AND status = 'running'
                    AND (lease_expires_at IS NULL OR lease_expires_at > ?)
                """,
                (*active_kinds, now),
            ).fetchone()
            if int(running_row[0] or 0) >= max(1, int(running_limit or 1)):
                return None

            row = conn.execute(
                f"""
                SELECT {", ".join(GALLERY_JOB_COLUMNS)}
                FROM gallery_jobs
                WHERE kind = ?
                    AND (
                        status = 'queued'
                        OR (status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?)
                    )
                ORDER BY
                    CASE WHEN status = 'running' THEN 0 ELSE 1 END,
                    created_at ASC
                LIMIT 1
                """,
                (kind, now),
            ).fetchone()
            if not row:
                return None

            started_at = row["started_at"] or now
            conn.execute(
                """
                UPDATE gallery_jobs
                SET status = 'running',
                    lease_owner = ?,
                    lease_expires_at = ?,
                    started_at = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (worker_id, lease_expires_at, started_at, now, row["job_id"]),
            )
            claimed = conn.execute(
                f"SELECT {', '.join(GALLERY_JOB_COLUMNS)} FROM gallery_jobs WHERE job_id = ?",
                (row["job_id"],),
            ).fetchone()
    return _gallery_job_from_row(claimed) if claimed else None


def cleanup_expired_gallery_jobs(kind: str) -> list[dict[str, Any]]:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            rows = conn.execute(
                f"""
                SELECT {", ".join(GALLERY_JOB_COLUMNS)}
                FROM gallery_jobs
                WHERE kind = ?
                    AND status = 'running'
                    AND lease_expires_at IS NOT NULL
                    AND lease_expires_at <= ?
                """,
                (kind, now),
            ).fetchall()
            if rows:
                conn.executemany(
                    "DELETE FROM gallery_jobs WHERE job_id = ?",
                    [(row["job_id"],) for row in rows],
                )
    return [_gallery_job_from_row(row) for row in rows]


def cleanup_stale_gallery_jobs(kind: str, ttl_seconds: int) -> list[dict[str, Any]]:
    _ensure_database()
    cutoff = datetime.fromtimestamp(
        time.time() - max(0, int(ttl_seconds or 0)),
        tz=timezone.utc,
    ).isoformat()
    with _connect() as conn:
        with _transaction(conn):
            rows = conn.execute(
                f"""
                SELECT {", ".join(GALLERY_JOB_COLUMNS)}
                FROM gallery_jobs
                WHERE kind = ? AND status IN ('success', 'error')
                    AND updated_at <= ?
                """,
                (kind, cutoff),
            ).fetchall()
            if rows:
                conn.executemany(
                    "DELETE FROM gallery_jobs WHERE job_id = ?",
                    [(row["job_id"],) for row in rows],
                )
    return [_gallery_job_from_row(row) for row in rows]


def delete_gallery_job(kind: str, job_id: str) -> dict[str, Any] | None:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                f"""
                SELECT {", ".join(GALLERY_JOB_COLUMNS)}
                FROM gallery_jobs
                WHERE kind = ? AND job_id = ?
                """,
                (kind, job_id),
            ).fetchone()
            if row:
                conn.execute("DELETE FROM gallery_jobs WHERE job_id = ?", (job_id,))
    return _gallery_job_from_row(row) if row else None


def list_gallery_job_ids_with_files(kind: str) -> set[str]:
    _ensure_database()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT job_id
            FROM gallery_jobs
            WHERE kind = ? AND path IS NOT NULL
            """,
            (kind,),
        ).fetchall()
    return {str(row["job_id"]) for row in rows}


def count_active_image_job_units() -> int:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM image_job_units WHERE status IN ('queued', 'running')"
        ).fetchone()
    return int(row[0] or 0) if row else 0


def count_pending_image_job_units() -> tuple[int, int]:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running_count,
                SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued_count
            FROM image_job_units
            WHERE status IN ('queued', 'running')
            """
        ).fetchone()
    if not row:
        return 0, 0
    return int(row["running_count"] or 0), int(row["queued_count"] or 0)


def get_pending_edit_source_bytes() -> int:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(byte_count), 0) FROM edit_source_reservations"
        ).fetchone()
    return int(row[0] or 0) if row else 0


def release_edit_source_reservation(job_id: str) -> int:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                "SELECT byte_count FROM edit_source_reservations WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not row:
                return 0
            conn.execute(
                "DELETE FROM edit_source_reservations WHERE job_id = ?",
                (job_id,),
            )
    return int(row["byte_count"] or 0)


def _cleanup_expired_sse_slots_on_conn(conn: sqlite3.Connection, now: str) -> int:
    cursor = conn.execute(
        "DELETE FROM sse_slots WHERE lease_expires_at <= ?",
        (now,),
    )
    return int(cursor.rowcount if cursor.rowcount is not None else 0)


def acquire_sse_slot(
    *,
    client_ip: str,
    connection_id: str,
    lease_expires_at: str,
    max_global: int,
    max_per_ip: int,
    now: str | None = None,
) -> tuple[bool, str]:
    _ensure_database()
    normalized_ip = str(client_ip or "unknown")[:256]
    normalized_connection_id = str(connection_id or "").strip()
    if not normalized_connection_id:
        return False, "invalid_connection"
    now = now or utc_now()
    global_limit = max(1, int(max_global or 1))
    ip_limit = max(1, int(max_per_ip or 1))
    with _connect() as conn:
        with _transaction(conn):
            _cleanup_expired_sse_slots_on_conn(conn, now)
            row = conn.execute("SELECT COUNT(*) FROM sse_slots").fetchone()
            global_count = int(row[0] or 0) if row else 0
            if global_count >= global_limit:
                return False, "global_limit"

            row = conn.execute(
                "SELECT COUNT(*) FROM sse_slots WHERE client_ip = ?",
                (normalized_ip,),
            ).fetchone()
            per_ip_count = int(row[0] or 0) if row else 0
            if per_ip_count >= ip_limit:
                return False, "per_ip_limit"

            conn.execute(
                """
                INSERT INTO sse_slots (
                    connection_id,
                    client_ip,
                    acquired_at,
                    lease_expires_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (normalized_connection_id, normalized_ip, now, lease_expires_at),
            )
    return True, "acquired"


def refresh_sse_slot(
    *,
    connection_id: str,
    lease_expires_at: str,
    now: str | None = None,
) -> bool:
    _ensure_database()
    normalized_connection_id = str(connection_id or "").strip()
    if not normalized_connection_id:
        return False
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            _cleanup_expired_sse_slots_on_conn(conn, now)
            cursor = conn.execute(
                """
                UPDATE sse_slots
                SET lease_expires_at = ?
                WHERE connection_id = ?
                """,
                (lease_expires_at, normalized_connection_id),
            )
    return int(cursor.rowcount or 0) > 0


def release_sse_slot(connection_id: str, *, now: str | None = None) -> bool:
    _ensure_database()
    normalized_connection_id = str(connection_id or "").strip()
    if not normalized_connection_id:
        return False
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            _cleanup_expired_sse_slots_on_conn(conn, now)
            cursor = conn.execute(
                "DELETE FROM sse_slots WHERE connection_id = ?",
                (normalized_connection_id,),
            )
    return int(cursor.rowcount or 0) > 0


def _count_active_sse_slots_on_conn(
    conn: sqlite3.Connection,
    *,
    now: str,
    client_ip: str | None = None,
) -> int:
    if client_ip is None:
        row = conn.execute(
            "SELECT COUNT(*) FROM sse_slots WHERE lease_expires_at > ?",
            (now,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM sse_slots
            WHERE client_ip = ? AND lease_expires_at > ?
            """,
            (client_ip, now),
        ).fetchone()
    return int(row[0] or 0) if row else 0


def count_active_sse_slots(client_ip: str | None = None) -> int:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            _cleanup_expired_sse_slots_on_conn(conn, now)
            return _count_active_sse_slots_on_conn(conn, now=now, client_ip=client_ip)


def _acquire_background_lease_on_conn(
    conn: sqlite3.Connection,
    *,
    name: str,
    owner: str,
    lease_expires_at: str,
    now: str,
    completed_ttl_seconds: int | None = None,
) -> bool:
    row = conn.execute(
        """
        SELECT owner, lease_expires_at, completed_at
        FROM background_leases
        WHERE name = ?
        """,
        (name,),
    ).fetchone()
    if row:
        completed_at = str(row["completed_at"] or "")
        if completed_ttl_seconds is not None and completed_at:
            now_dt = _coerce_iso_datetime(now) or datetime.now(timezone.utc)
            cutoff = (now_dt - timedelta(seconds=max(0, int(completed_ttl_seconds or 0)))).isoformat()
            if completed_at > cutoff:
                return False

        active_owner = str(row["owner"] or "")
        active_until = str(row["lease_expires_at"] or "")
        if active_until > now and active_owner != owner:
            return False

    conn.execute(
        """
        INSERT INTO background_leases (
            name,
            owner,
            lease_expires_at,
            updated_at,
            completed_at
        )
        VALUES (?, ?, ?, ?, NULL)
        ON CONFLICT(name) DO UPDATE SET
            owner = excluded.owner,
            lease_expires_at = excluded.lease_expires_at,
            updated_at = excluded.updated_at,
            completed_at = NULL
        """,
        (name, owner, lease_expires_at, now),
    )
    return True


def acquire_background_lease(
    *,
    name: str,
    owner: str,
    lease_expires_at: str,
    now: str | None = None,
    completed_ttl_seconds: int | None = None,
) -> bool:
    _ensure_database()
    normalized_name = str(name or "").strip()
    normalized_owner = str(owner or "").strip()
    if not normalized_name or not normalized_owner:
        return False
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            return _acquire_background_lease_on_conn(
                conn,
                name=normalized_name,
                owner=normalized_owner,
                lease_expires_at=lease_expires_at,
                now=now,
                completed_ttl_seconds=completed_ttl_seconds,
            )


def complete_background_lease(
    *,
    name: str,
    owner: str,
    now: str | None = None,
) -> bool:
    _ensure_database()
    normalized_name = str(name or "").strip()
    normalized_owner = str(owner or "").strip()
    if not normalized_name or not normalized_owner:
        return False
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                """
                UPDATE background_leases
                SET lease_expires_at = ?,
                    updated_at = ?,
                    completed_at = ?
                WHERE name = ? AND owner = ?
                """,
                (now, now, now, normalized_name, normalized_owner),
            )
    return int(cursor.rowcount or 0) > 0


def release_background_lease(
    *,
    name: str,
    owner: str,
    now: str | None = None,
) -> bool:
    _ensure_database()
    normalized_name = str(name or "").strip()
    normalized_owner = str(owner or "").strip()
    if not normalized_name or not normalized_owner:
        return False
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                """
                UPDATE background_leases
                SET lease_expires_at = ?,
                    updated_at = ?
                WHERE name = ? AND owner = ?
                """,
                (now, now, normalized_name, normalized_owner),
            )
    return int(cursor.rowcount or 0) > 0


def acquire_background_slot(
    *,
    name_prefix: str,
    owner: str,
    slot_count: int,
    lease_expires_at: str,
    now: str | None = None,
) -> str | None:
    _ensure_database()
    normalized_prefix = str(name_prefix or "").strip().rstrip(":")
    normalized_owner = str(owner or "").strip()
    if not normalized_prefix or not normalized_owner:
        return None
    slots = max(1, int(slot_count or 1))
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            for index in range(slots):
                name = f"{normalized_prefix}:{index}"
                if _acquire_background_lease_on_conn(
                    conn,
                    name=name,
                    owner=normalized_owner,
                    lease_expires_at=lease_expires_at,
                    now=now,
                ):
                    return name
    return None


def release_background_slot(
    *,
    name: str,
    owner: str,
    now: str | None = None,
) -> bool:
    return release_background_lease(name=name, owner=owner, now=now)


def mark_worker_heartbeat(worker_id: str, active_units: int = 0) -> None:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            conn.execute(
                """
                INSERT INTO worker_heartbeats (worker_id, last_seen_at, active_units)
                VALUES (?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    last_seen_at = excluded.last_seen_at,
                    active_units = excluded.active_units
                """,
                (worker_id, now, max(0, int(active_units or 0))),
            )


def record_worker_metrics_snapshot(worker_id: str, snapshot: dict[str, Any]) -> None:
    _ensure_database()
    normalized_worker_id = str(worker_id or "").strip()
    if not normalized_worker_id:
        return
    now = utc_now()
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    with _connect() as conn:
        with _transaction(conn):
            conn.execute(
                """
                INSERT INTO worker_metric_snapshots (
                    worker_id,
                    snapshot_json,
                    updated_at
                )
                VALUES (?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    snapshot_json = excluded.snapshot_json,
                    updated_at = excluded.updated_at
                """,
                (normalized_worker_id, payload, now),
            )


def _worker_metric_snapshots_on_conn(conn: sqlite3.Connection, now: datetime) -> list[dict[str, Any]]:
    cutoff = (now - timedelta(seconds=WORKER_METRIC_SNAPSHOT_TTL_SECONDS)).isoformat()
    rows = conn.execute(
        """
        SELECT worker_id, snapshot_json, updated_at
        FROM worker_metric_snapshots
        WHERE updated_at > ?
        ORDER BY updated_at DESC
        """,
        (cutoff,),
    ).fetchall()
    workers: list[dict[str, Any]] = []
    for row in rows:
        updated_at = str(row["updated_at"] or "")
        updated_dt = _coerce_iso_datetime(updated_at)
        try:
            snapshot = json.loads(row["snapshot_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            snapshot = {}
        age_seconds = (
            max(0.0, (now - updated_dt).total_seconds())
            if updated_dt is not None
            else None
        )
        workers.append(
            {
                "worker_id": str(row["worker_id"]),
                "updated_at": updated_at,
                "age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
                "snapshot": snapshot if isinstance(snapshot, dict) else {},
            }
        )
    return workers


def get_runtime_coordination_metrics() -> dict[str, Any]:
    _ensure_database()
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    with _connect() as conn:
        with _transaction(conn):
            expired_sse_slots = _cleanup_expired_sse_slots_on_conn(conn, now)

    with _connect() as conn:
        active_sse_slots = _count_active_sse_slots_on_conn(conn, now=now)

        heartbeat_rows = conn.execute(
            """
            SELECT worker_id, last_seen_at, active_units
            FROM worker_heartbeats
            """
        ).fetchall()
        heartbeat_ages = []
        active_worker_count = 0
        worker_active_units = 0
        for row in heartbeat_rows:
            seen_at = _coerce_iso_datetime(str(row["last_seen_at"] or ""))
            if seen_at is None:
                continue
            age_seconds = max(0.0, (now_dt - seen_at).total_seconds())
            heartbeat_ages.append(age_seconds)
            if age_seconds <= WORKER_METRIC_SNAPSHOT_TTL_SECONDS:
                active_worker_count += 1
                worker_active_units += max(0, int(row["active_units"] or 0))

        lease_rows = conn.execute(
            """
            SELECT name, owner, lease_expires_at, updated_at, completed_at
            FROM background_leases
            ORDER BY name
            """
        ).fetchall()
        active_leases = [
            {
                "name": str(row["name"]),
                "owner": str(row["owner"]),
                "lease_expires_at": str(row["lease_expires_at"] or ""),
                "updated_at": str(row["updated_at"] or ""),
                "completed_at": str(row["completed_at"] or "") or None,
            }
            for row in lease_rows
            if str(row["lease_expires_at"] or "") > now
        ]
        worker_snapshots = _worker_metric_snapshots_on_conn(conn, now_dt)

    return {
        "gauges": {
            "sse.active_connections": active_sse_slots,
            "sse.expired_slots_cleaned": expired_sse_slots,
            "workers.active": active_worker_count,
            "workers.heartbeat_age_max_seconds": round(max(heartbeat_ages), 3)
            if heartbeat_ages
            else 0.0,
            "workers.active_units": worker_active_units,
            "background_leases.active": len(active_leases),
        },
        "background_leases": active_leases,
        "workers": worker_snapshots,
    }


def _build_image_job_units(
    *,
    parent_job_id: str,
    operation: str,
    request: dict[str, Any],
    image_units: int,
    api_preset_id: str,
    api_preset_name: str,
    api_path: str,
    edit_sources: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    now = utc_now()
    return [
        {
            "unit_id": str(uuid.uuid4()),
            "parent_job_id": parent_job_id,
            "operation": operation,
            "unit_index": index,
            "status": "queued",
            "stage": "queued",
            "message": "Queued image unit",
            "created_at": now,
            "updated_at": now,
            "request": {**request, "n": 1},
            "edit_sources": edit_sources or [],
            "api_preset_id": api_preset_id,
            "api_preset_name": api_preset_name,
            "api_path": api_path,
        }
        for index in range(max(1, int(image_units or 1)))
    ]


def enqueue_image_job(
    *,
    parent_job: dict[str, Any],
    operation: str,
    request: dict[str, Any],
    image_units: int,
    api_preset_id: str,
    api_preset_name: str,
    api_path: str,
    edit_sources: list[dict[str, Any]] | None = None,
    pending_edit_source_bytes: int = 0,
    max_active_generate_jobs: int,
    max_queued_generate_jobs: int,
    max_pending_edit_source_bytes: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _ensure_database()
    normalized_job = _normalize_generate_job(parent_job)
    units = _build_image_job_units(
        parent_job_id=str(normalized_job["job_id"]),
        operation=operation,
        request=request,
        image_units=image_units,
        api_preset_id=api_preset_id,
        api_preset_name=api_preset_name,
        api_path=api_path,
        edit_sources=edit_sources,
    )
    requested_units = len(units)
    capacity = max(1, int(max_active_generate_jobs or 1)) + max(
        0,
        int(max_queued_generate_jobs or 0),
    )
    reserved_bytes = max(0, int(pending_edit_source_bytes or 0))
    max_reserved_bytes = max(0, int(max_pending_edit_source_bytes or 0))
    unit_columns_sql = ", ".join(IMAGE_JOB_UNIT_COLUMNS)
    unit_placeholders_sql = ", ".join("?" for _ in IMAGE_JOB_UNIT_COLUMNS)
    now = utc_now()

    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END), 0) AS running_count,
                    COALESCE(SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END), 0) AS queued_count
                FROM image_job_units
                WHERE status IN ('queued', 'running')
                """
            ).fetchone()
            running_count = int(row["running_count"] or 0) if row else 0
            queued_count = int(row["queued_count"] or 0) if row else 0
            if running_count + queued_count + requested_units > capacity:
                raise ImageJobQueueFullError("Generation job queue is full")

            existing_reservation = conn.execute(
                "SELECT byte_count FROM edit_source_reservations WHERE job_id = ?",
                (normalized_job["job_id"],),
            ).fetchone()
            existing_bytes = (
                int(existing_reservation["byte_count"] or 0)
                if existing_reservation
                else 0
            )
            pending_row = conn.execute(
                "SELECT COALESCE(SUM(byte_count), 0) AS byte_count FROM edit_source_reservations"
            ).fetchone()
            current_reserved_bytes = (
                int(pending_row["byte_count"] or 0) if pending_row else 0
            )
            if (
                reserved_bytes > 0
                and max_reserved_bytes > 0
                and current_reserved_bytes - existing_bytes + reserved_bytes > max_reserved_bytes
            ):
                raise EditSourceQueueFullError("Edit source queue is full")

            _upsert_generate_job_on_conn(conn, normalized_job)
            if reserved_bytes > 0:
                conn.execute(
                    """
                    INSERT INTO edit_source_reservations (job_id, byte_count, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(job_id) DO UPDATE SET
                        byte_count = excluded.byte_count,
                        updated_at = excluded.updated_at
                    """,
                    (normalized_job["job_id"], reserved_bytes, now, now),
                )
            elif existing_bytes > 0:
                conn.execute(
                    "DELETE FROM edit_source_reservations WHERE job_id = ?",
                    (normalized_job["job_id"],),
                )
            conn.executemany(
                f"""
                INSERT INTO image_job_units ({unit_columns_sql})
                VALUES ({unit_placeholders_sql})
                """,
                [_image_job_unit_values(unit) for unit in units],
            )
    return normalized_job, units


def create_image_job_units(
    *,
    parent_job_id: str,
    operation: str,
    request: dict[str, Any],
    image_units: int,
    api_preset_id: str,
    api_preset_name: str,
    api_path: str,
    edit_sources: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    _ensure_database()
    units = _build_image_job_units(
        parent_job_id=parent_job_id,
        operation=operation,
        request=request,
        image_units=image_units,
        api_preset_id=api_preset_id,
        api_preset_name=api_preset_name,
        api_path=api_path,
        edit_sources=edit_sources,
    )
    columns_sql = ", ".join(IMAGE_JOB_UNIT_COLUMNS)
    placeholders_sql = ", ".join("?" for _ in IMAGE_JOB_UNIT_COLUMNS)
    with _connect() as conn:
        with _transaction(conn):
            conn.executemany(
                f"INSERT INTO image_job_units ({columns_sql}) VALUES ({placeholders_sql})",
                [_image_job_unit_values(unit) for unit in units],
            )
    return units


def get_image_job_unit(unit_id: str) -> dict[str, Any] | None:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT {", ".join(IMAGE_JOB_UNIT_COLUMNS)}
            FROM image_job_units
            WHERE unit_id = ?
            """,
            (unit_id,),
        ).fetchone()
    return _image_job_unit_from_row(row) if row else None


def claim_next_image_job_unit(
    *,
    worker_id: str,
    lease_expires_at: str,
    now: str,
    running_limit: int,
) -> dict[str, Any] | None:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                f"""
                WITH
                    running_count(value) AS (
                        SELECT COUNT(*)
                        FROM image_job_units
                        WHERE status = 'running'
                    ),
                    expired_candidate(unit_id, priority) AS (
                        SELECT unit_id, 0
                        FROM image_job_units
                        WHERE status = 'running'
                            AND claim_expires_at IS NOT NULL
                            AND claim_expires_at <= ?
                        ORDER BY claim_expires_at ASC, created_at ASC, unit_index ASC
                        LIMIT 1
                    ),
                    queued_candidate(unit_id, priority) AS (
                        SELECT unit_id, 1
                        FROM image_job_units
                        WHERE status = 'queued'
                        ORDER BY created_at ASC, unit_index ASC
                        LIMIT 1
                    ),
                    candidate(unit_id, priority) AS (
                        SELECT unit_id, priority FROM expired_candidate
                        UNION ALL
                        SELECT unit_id, priority FROM queued_candidate
                        ORDER BY priority ASC
                        LIMIT 1
                    )
                UPDATE image_job_units
                SET status = 'running',
                    claimed_by = ?,
                    claim_expires_at = ?,
                    stage = COALESCE(NULLIF(stage, 'queued'), stage),
                    message = COALESCE(message, 'Running image unit'),
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?
                WHERE unit_id = (SELECT unit_id FROM candidate)
                    AND (SELECT value FROM running_count) < ?
                RETURNING {", ".join(IMAGE_JOB_UNIT_COLUMNS)}
                """,
                (
                    now,
                    worker_id,
                    lease_expires_at,
                    now,
                    now,
                    max(1, int(running_limit or 1)),
                ),
            ).fetchone()
    return _image_job_unit_from_row(row) if row else None


def update_image_job_unit_progress(
    unit_id: str,
    *,
    stage: str,
    message: str,
    claim_expires_at: str | None = None,
) -> dict[str, Any] | None:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            conn.execute(
                """
                UPDATE image_job_units
                SET stage = ?,
                    message = ?,
                    claim_expires_at = COALESCE(?, claim_expires_at),
                    updated_at = ?
                WHERE unit_id = ? AND status = 'running'
                """,
                (stage, message, claim_expires_at, now, unit_id),
            )
            row = conn.execute(
                f"""
                SELECT {", ".join(IMAGE_JOB_UNIT_COLUMNS)}
                FROM image_job_units
                WHERE unit_id = ?
                """,
                (unit_id,),
            ).fetchone()
    return _image_job_unit_from_row(row) if row else None


def complete_image_job_unit(
    unit_id: str,
    *,
    result: dict[str, Any],
    stage_timings: dict[str, float],
    duration: str,
    completed_at: str,
) -> dict[str, Any] | None:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            conn.execute(
                """
                UPDATE image_job_units
                SET status = 'success',
                    stage = 'completed',
                    message = 'Image unit completed',
                    result_json = ?,
                    stage_timings_json = ?,
                    duration = ?,
                    completed_at = ?,
                    updated_at = ?,
                    claim_expires_at = NULL
                WHERE unit_id = ?
                """,
                (
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    json.dumps(stage_timings, ensure_ascii=False, sort_keys=True),
                    duration,
                    completed_at,
                    now,
                    unit_id,
                ),
            )
            row = conn.execute(
                f"""
                SELECT {", ".join(IMAGE_JOB_UNIT_COLUMNS)}
                FROM image_job_units
                WHERE unit_id = ?
                """,
                (unit_id,),
            ).fetchone()
    return _image_job_unit_from_row(row) if row else None


def fail_image_job_unit(
    unit_id: str,
    *,
    status: str,
    stage: str,
    message: str,
    error: str,
    stage_timings: dict[str, float] | None = None,
    duration: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any] | None:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            conn.execute(
                """
                UPDATE image_job_units
                SET status = ?,
                    stage = ?,
                    message = ?,
                    error = ?,
                    stage_timings_json = ?,
                    duration = ?,
                    completed_at = ?,
                    updated_at = ?,
                    claim_expires_at = NULL
                WHERE unit_id = ?
                """,
                (
                    status,
                    stage,
                    message,
                    error,
                    json.dumps(stage_timings or {}, ensure_ascii=False, sort_keys=True),
                    duration,
                    completed_at or now,
                    now,
                    unit_id,
                ),
            )
            row = conn.execute(
                f"""
                SELECT {", ".join(IMAGE_JOB_UNIT_COLUMNS)}
                FROM image_job_units
                WHERE unit_id = ?
                """,
                (unit_id,),
            ).fetchone()
    return _image_job_unit_from_row(row) if row else None


def cancel_image_job_units(parent_job_id: str) -> int:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                """
                UPDATE image_job_units
                SET status = 'cancelled',
                    stage = 'cancelled',
                    message = 'Generation job cancelled',
                    error = 'Generation job cancelled',
                    completed_at = ?,
                    updated_at = ?,
                    claim_expires_at = NULL
                WHERE parent_job_id = ? AND status IN ('queued', 'running')
                """,
                (now, now, parent_job_id),
            )
            return cursor.rowcount


def aggregate_image_job_units(parent_job_id: str) -> dict[str, Any]:
    _ensure_database()
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {", ".join(IMAGE_JOB_UNIT_COLUMNS)}
            FROM image_job_units
            WHERE parent_job_id = ?
            ORDER BY unit_index ASC
            """,
            (parent_job_id,),
        ).fetchall()
    units = [_image_job_unit_from_row(row) for row in rows]
    total = len(units)
    terminal_statuses = {"success", "error", "upstream_error", "cancelled", "interrupted"}
    completed = sum(1 for unit in units if unit.get("status") in terminal_statuses)
    successes = [unit for unit in units if unit.get("status") == "success"]
    failures = [unit for unit in units if unit.get("status") in {"error", "upstream_error"}]
    cancelled = [unit for unit in units if unit.get("status") == "cancelled"]
    running = [unit for unit in units if unit.get("status") == "running"]
    queued = [unit for unit in units if unit.get("status") == "queued"]
    images: list[dict[str, Any]] = []
    stage_timings: dict[str, float] = {}
    for unit in successes:
        result = unit.get("result") or {}
        unit_images = result.get("images") if isinstance(result, dict) else None
        if isinstance(unit_images, list):
            images.extend(image for image in unit_images if isinstance(image, dict))
        for key, value in (unit.get("stage_timings") or {}).items():
            try:
                stage_timings[key] = stage_timings.get(key, 0.0) + float(value)
            except (TypeError, ValueError):
                continue

    return {
        "total": total,
        "completed": completed,
        "success_count": len(successes),
        "failure_count": len(failures),
        "cancelled_count": len(cancelled),
        "running_count": len(running),
        "queued_count": len(queued),
        "all_terminal": total > 0 and completed == total,
        "all_failed": total > 0 and len(failures) == total,
        "all_cancelled": total > 0 and len(cancelled) == total,
        "images": images,
        "failures": failures,
        "stage_timings": stage_timings,
        "units": units,
    }


def _get_generate_job_rows_on_conn(
    conn: sqlite3.Connection,
    *,
    statuses: set[str] | None = None,
    limit: int | None = None,
    before_updated_at: str | None = None,
    before_job_id: str | None = None,
) -> list[sqlite3.Row]:
    params: list[Any] = []
    where: list[str] = []
    if statuses:
        placeholders = ", ".join("?" for _ in statuses)
        where.append(f"status IN ({placeholders})")
        params.extend(sorted(statuses))
    normalized_before_updated_at = str(before_updated_at or "").strip()
    normalized_before_job_id = str(before_job_id or "").strip()
    if normalized_before_updated_at and normalized_before_job_id:
        where.append("(updated_at < ? OR (updated_at = ? AND job_id < ?))")
        params.extend(
            [
                normalized_before_updated_at,
                normalized_before_updated_at,
                normalized_before_job_id,
            ]
        )

    sql = f"""
        SELECT {", ".join(GENERATE_JOB_COLUMNS)}
        FROM generate_jobs
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC, job_id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def list_generate_jobs(
    statuses: set[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
    before_updated_at: str | None = None,
    before_job_id: str | None = None,
) -> list[dict[str, Any]]:
    _ensure_database()
    normalized_offset = max(0, int(offset or 0))
    seek_updated_at = str(before_updated_at or "").strip() or None
    seek_job_id = str(before_job_id or "").strip() or None
    if bool(seek_updated_at) != bool(seek_job_id):
        seek_updated_at = None
        seek_job_id = None

    with _connect() as conn:
        remaining_offset = normalized_offset
        while remaining_offset > 0:
            skip_limit = min(remaining_offset, 500)
            skipped_rows = _get_generate_job_rows_on_conn(
                conn,
                statuses=statuses,
                limit=skip_limit,
                before_updated_at=seek_updated_at,
                before_job_id=seek_job_id,
            )
            if not skipped_rows:
                return []
            remaining_offset -= len(skipped_rows)
            last_skipped = skipped_rows[-1]
            seek_updated_at = str(last_skipped["updated_at"] or "")
            seek_job_id = str(last_skipped["job_id"] or "")
            if len(skipped_rows) < skip_limit:
                return []

        rows = _get_generate_job_rows_on_conn(
            conn,
            statuses=statuses,
            limit=limit,
            before_updated_at=seek_updated_at,
            before_job_id=seek_job_id,
        )
    return [_generate_job_from_row(row) for row in rows]


def clear_generate_job_history() -> int:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            placeholders = ", ".join("?" for _ in ACTIVE_GENERATE_JOB_STATUSES)
            rows = conn.execute(
                f"""
                SELECT job_id
                FROM generate_jobs
                WHERE status NOT IN ({placeholders})
                """,
                tuple(sorted(ACTIVE_GENERATE_JOB_STATUSES)),
            ).fetchall()
            if not rows:
                return 0
            conn.executemany(
                "DELETE FROM edit_source_reservations WHERE job_id = ?",
                [(row["job_id"],) for row in rows],
            )
            conn.executemany(
                "DELETE FROM image_job_units WHERE parent_job_id = ?",
                [(row["job_id"],) for row in rows],
            )
            cursor = conn.execute(
                f"""
                DELETE FROM generate_jobs
                WHERE status NOT IN ({placeholders})
                """,
                tuple(sorted(ACTIVE_GENERATE_JOB_STATUSES)),
            )
            return cursor.rowcount


def mark_active_generate_jobs_interrupted() -> int:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            placeholders = ", ".join("?" for _ in ACTIVE_GENERATE_JOB_STATUSES)
            rows = conn.execute(
                f"""
                SELECT job_id
                FROM generate_jobs
                WHERE status IN ({placeholders})
                """,
                tuple(sorted(ACTIVE_GENERATE_JOB_STATUSES)),
            ).fetchall()
            if not rows:
                return 0
            job_ids = [row["job_id"] for row in rows]
            conn.executemany(
                "DELETE FROM edit_source_reservations WHERE job_id = ?",
                [(job_id,) for job_id in job_ids],
            )
            conn.executemany(
                "DELETE FROM image_job_units WHERE parent_job_id = ?",
                [(job_id,) for job_id in job_ids],
            )

            conn.execute(
                f"""
                UPDATE generate_jobs
                SET status = 'interrupted',
                    stage = 'interrupted',
                    message = 'Job interrupted by server restart',
                    error = 'Job interrupted by server restart',
                    completed_at = ?,
                    updated_at = ?
                WHERE status IN ({placeholders})
                """,
                (now, now, *tuple(sorted(ACTIVE_GENERATE_JOB_STATUSES))),
            )
            return len(rows)


def trim_generate_jobs(max_jobs: int):
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute("SELECT COUNT(*) FROM generate_jobs").fetchone()
            total = int(row[0]) if row else 0
            if total <= max_jobs:
                return

            removable_count = total - max_jobs
            rows = conn.execute(
                """
                SELECT job_id
                FROM generate_jobs
                WHERE status NOT IN ('queued', 'running')
                ORDER BY updated_at ASC, created_at ASC
                LIMIT ?
                """,
                (removable_count,),
            ).fetchall()
            if not rows:
                return
            conn.executemany(
                "DELETE FROM edit_source_reservations WHERE job_id = ?",
                [(row["job_id"],) for row in rows],
            )
            conn.executemany(
                "DELETE FROM image_job_units WHERE parent_job_id = ?",
                [(row["job_id"],) for row in rows],
            )
            conn.executemany(
                "DELETE FROM generate_jobs WHERE job_id = ?",
                [(row["job_id"],) for row in rows],
            )


def sync_gallery_with_image_files() -> int:
    _ensure_database()
    with _storage_lock:
        image_filenames = _scan_image_files()
        removed_count = 0
        with _connect() as conn:
            with _transaction(conn):
                last_id = ""
                filter_option_deltas: dict[tuple[str, str], int] = {}
                while True:
                    rows = conn.execute(
                        """
                        SELECT id, filename, model, api_preset_name, size
                        FROM gallery_entries
                        WHERE id > ?
                        ORDER BY id
                        LIMIT ?
                        """,
                        (last_id, GALLERY_SYNC_BATCH_SIZE),
                    ).fetchall()
                    if not rows:
                        break

                    last_id = str(rows[-1]["id"])
                    stale_ids = [
                        row["id"]
                        for row in rows
                        if row["filename"] and row["filename"] not in image_filenames
                    ]
                    if not stale_ids:
                        continue

                    conn.executemany(
                        "DELETE FROM gallery_entries WHERE id = ?",
                        [(entry_id,) for entry_id in stale_ids],
                    )
                    for row in rows:
                        if row["id"] in stale_ids:
                            _add_gallery_filter_option_deltas(
                                filter_option_deltas,
                                row,
                                -1,
                            )
                    removed_count += len(stale_ids)

                if removed_count:
                    _apply_gallery_filter_option_deltas_on_conn(
                        conn,
                        filter_option_deltas,
                    )
                    _invalidate_gallery_query_caches_on_conn(conn)
                    _clear_verified_thumbnails()
                return removed_count


def _delete_gallery_entries_by_ids(
    conn: sqlite3.Connection,
    image_ids: Sequence[str],
) -> tuple[list[str], set[str]]:
    """Delete gallery entries and return (removed_ids, filenames_to_delete).

    File deletion is NOT performed here — caller handles it after commit.
    """
    unique_ids = _unique_sqlite_values(image_ids)
    if not unique_ids:
        return [], set()

    rows: list[sqlite3.Row] = []
    for chunk in _iter_sqlite_in_chunks(unique_ids):
        placeholders = ", ".join("?" for _ in chunk)
        rows.extend(
            conn.execute(
                f"""
                SELECT id, filename, model, api_preset_name, size
                FROM gallery_entries
                WHERE id IN ({placeholders})
                """,
                tuple(chunk),
            ).fetchall()
        )
    if not rows:
        return [], set()

    removed_ids = [row["id"] for row in rows]
    removed_filenames = {row["filename"] for row in rows if row["filename"]}
    for chunk in _iter_sqlite_in_chunks(removed_ids):
        delete_placeholders = ", ".join("?" for _ in chunk)
        conn.execute(
            f"DELETE FROM gallery_entries WHERE id IN ({delete_placeholders})",
            tuple(chunk),
        )
    filter_option_deltas: dict[tuple[str, str], int] = {}
    for row in rows:
        _add_gallery_filter_option_deltas(filter_option_deltas, row, -1)
    _apply_gallery_filter_option_deltas_on_conn(conn, filter_option_deltas)
    _invalidate_gallery_query_caches_on_conn(conn)

    remaining_filenames: set[str] = set()
    if removed_filenames:
        for chunk in _iter_sqlite_in_chunks(removed_filenames):
            filename_placeholders = ", ".join("?" for _ in chunk)
            remaining_rows = conn.execute(
                f"""
                SELECT DISTINCT filename
                FROM gallery_entries
                WHERE filename IN ({filename_placeholders})
                """,
                tuple(chunk),
            ).fetchall()
            remaining_filenames.update(
                row["filename"] for row in remaining_rows if row["filename"]
            )

    filenames_to_delete = removed_filenames - remaining_filenames
    return removed_ids, filenames_to_delete


def _delete_gallery_entries_by_filters(
    conn: sqlite3.Connection,
    filters: dict[str, Any] | None,
    *,
    batch_size: int = 500,
) -> tuple[int, set[str]]:
    where_sql, params = _build_gallery_filter_where(filters)
    normalized_batch_size = max(1, int(batch_size or 1))
    last_sort_seq: int | None = None
    last_id: str | None = None
    removed_count = 0
    removed_filenames: set[str] = set()
    filter_option_deltas: dict[tuple[str, str], int] = {}

    while True:
        rows = _get_gallery_row_batch_after_cursor_on_conn(
            conn,
            where_sql,
            params,
            last_sort_seq=last_sort_seq,
            last_id=last_id,
            limit=normalized_batch_size,
            columns=("id", "filename", "model", "api_preset_name", "size", "sort_seq"),
        )
        if not rows:
            break

        ids = [str(row["id"]) for row in rows if row["id"]]
        removed_filenames.update(str(row["filename"]) for row in rows if row["filename"])
        for chunk in _iter_sqlite_in_chunks(ids):
            placeholders = ", ".join("?" for _ in chunk)
            conn.execute(
                f"DELETE FROM gallery_entries WHERE id IN ({placeholders})",
                tuple(chunk),
            )
        for row in rows:
            _add_gallery_filter_option_deltas(filter_option_deltas, row, -1)
        removed_count += len(ids)

        if len(rows) < normalized_batch_size:
            break
        last_row = rows[-1]
        last_sort_seq = int(last_row["sort_seq"] or 0)
        last_id = str(last_row["id"])

    if not removed_count:
        return 0, set()

    _apply_gallery_filter_option_deltas_on_conn(conn, filter_option_deltas)
    _invalidate_gallery_query_caches_on_conn(conn)

    remaining_filenames: set[str] = set()
    if removed_filenames:
        for chunk in _iter_sqlite_in_chunks(removed_filenames):
            filename_placeholders = ", ".join("?" for _ in chunk)
            remaining_rows = conn.execute(
                f"""
                SELECT DISTINCT filename
                FROM gallery_entries
                WHERE filename IN ({filename_placeholders})
                """,
                tuple(chunk),
            ).fetchall()
            remaining_filenames.update(
                row["filename"] for row in remaining_rows if row["filename"]
            )

    return removed_count, removed_filenames - remaining_filenames


def delete_gallery_image(image_id: str) -> tuple[bool, int]:
    deleted_entries, deleted_files = delete_gallery_images([image_id])
    return deleted_entries > 0, deleted_files


def _delete_gallery_files_after_commit(filenames: Iterable[str]) -> int:
    deleted_count = 0
    for filename in filenames:
        try:
            if _delete_image_unlocked(filename):
                deleted_count += 1
        except OSError as e:
            metrics.increment("gallery.orphan_cleanup_pending")
            logger.warning("Failed to delete image file %s: %s", filename, e)
        try:
            _delete_thumbnail_unlocked(filename)
        except OSError as e:
            metrics.increment("gallery.orphan_cleanup_pending")
            logger.warning("Failed to delete thumbnail for %s: %s", filename, e)
        thumbnail_filename = _thumbnail_filename_for_image(filename)
        if thumbnail_filename:
            _remove_verified_thumbnail(thumbnail_filename)
    return deleted_count


def delete_gallery_images(image_ids: Sequence[str]) -> tuple[int, int]:
    _ensure_database()
    if not image_ids:
        return 0, 0

    with _storage_lock:
        with _connect() as conn:
            with _transaction(conn):
                removed_ids, filenames_to_delete = _delete_gallery_entries_by_ids(conn, image_ids)

    deleted_count = _delete_gallery_files_after_commit(filenames_to_delete)
    return len(removed_ids), deleted_count


def delete_gallery_images_by_filters(
    filters: dict[str, Any] | None,
    *,
    batch_size: int = 500,
) -> tuple[int, int]:
    _ensure_database()
    with _storage_lock:
        with _connect() as conn:
            with _transaction(conn):
                removed_count, filenames_to_delete = _delete_gallery_entries_by_filters(
                    conn,
                    filters,
                    batch_size=batch_size,
                )

    deleted_count = _delete_gallery_files_after_commit(filenames_to_delete)
    return removed_count, deleted_count


def _is_gallery_filename_referenced_on_conn(
    conn: sqlite3.Connection,
    filename: str,
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM gallery_entries WHERE filename = ? LIMIT 1",
        (filename,),
    ).fetchone()
    return row is not None


def is_gallery_filename_referenced(filename: str) -> bool:
    _ensure_database()
    normalized = str(filename or "").strip()
    if not normalized or not safe_image_path(normalized):
        return False
    with _connect() as conn:
        return _is_gallery_filename_referenced_on_conn(conn, normalized)


def _delete_gallery_file_if_unreferenced(filename: str) -> bool:
    with _storage_lock:
        with _connect() as conn:
            if _is_gallery_filename_referenced_on_conn(conn, filename):
                return False

        deleted = False
        try:
            deleted = _delete_image_unlocked(filename)
        except OSError as e:
            metrics.increment("gallery.orphan_cleanup_pending")
            logger.warning("Failed to delete gallery image file %s: %s", filename, e)

        try:
            _delete_thumbnail_unlocked(filename)
            thumbnail_filename = _thumbnail_filename_for_image(filename)
            if thumbnail_filename:
                _remove_verified_thumbnail(thumbnail_filename)
        except OSError as e:
            metrics.increment("gallery.orphan_cleanup_pending")
            logger.warning("Failed to delete gallery thumbnail for %s: %s", filename, e)

        return deleted


def _file_older_than(path: Path, cutoff_epoch_seconds: float) -> bool:
    try:
        return path.stat().st_mtime < cutoff_epoch_seconds
    except OSError:
        return False


def cleanup_orphan_gallery_files(
    *,
    ttl_seconds: int = GALLERY_ORPHAN_FILE_TTL_SECONDS,
    batch_size: int = GALLERY_ORPHAN_GC_BATCH_SIZE,
) -> dict[str, int]:
    """Delete unreferenced image and thumbnail files after a short TTL."""
    _ensure_database()
    cutoff = time.time() - max(0, int(ttl_seconds))
    limit = max(1, int(batch_size))
    removed_images = 0
    removed_thumbnails = 0
    failed = 0
    scanned = 0

    with _storage_lock:
        with _connect() as conn:
            referenced_filenames = set(_get_all_filenames_on_conn(conn))
        referenced_thumbnails = {
            thumbnail
            for filename in referenced_filenames
            if (thumbnail := _thumbnail_filename_for_image(filename))
        }

        images_dir = Path(config.IMAGES_DIR)
        if images_dir.exists():
            for path in images_dir.iterdir():
                if scanned >= limit:
                    break
                if not path.is_file() or path.suffix.lower() not in IMAGE_FILE_EXTENSIONS:
                    continue
                scanned += 1
                filename = path.name
                if filename in referenced_filenames or not _file_older_than(path, cutoff):
                    continue
                try:
                    if _delete_image_unlocked(filename):
                        removed_images += 1
                except OSError as e:
                    failed += 1
                    metrics.increment("gallery.orphan_cleanup_pending")
                    logger.warning("Failed to GC orphan gallery image %s: %s", filename, e)

        thumbnails_dir = Path(config.THUMBNAILS_DIR)
        same_dir_as_images = False
        try:
            same_dir_as_images = thumbnails_dir.resolve() == images_dir.resolve()
        except OSError:
            same_dir_as_images = False

        if thumbnails_dir.exists() and scanned < limit:
            protected_thumbnail_names = set(referenced_thumbnails)
            if same_dir_as_images:
                protected_thumbnail_names.update(referenced_filenames)
            for path in thumbnails_dir.iterdir():
                if scanned >= limit:
                    break
                if not path.is_file() or path.suffix.lower() != THUMBNAIL_EXTENSION:
                    continue
                scanned += 1
                thumbnail_filename = path.name
                if (
                    thumbnail_filename in protected_thumbnail_names
                    or not safe_thumbnail_path(thumbnail_filename)
                    or not _file_older_than(path, cutoff)
                ):
                    continue
                try:
                    path.unlink()
                    _remove_verified_thumbnail(thumbnail_filename)
                    removed_thumbnails += 1
                except OSError as e:
                    failed += 1
                    metrics.increment("gallery.orphan_cleanup_pending")
                    logger.warning("Failed to GC orphan gallery thumbnail %s: %s", thumbnail_filename, e)

    if removed_images or removed_thumbnails or failed:
        logger.info(
            "Gallery file GC scanned=%d removed_images=%d removed_thumbnails=%d failed=%d",
            scanned,
            removed_images,
            removed_thumbnails,
            failed,
        )
    return {
        "scanned": scanned,
        "removed_images": removed_images,
        "removed_thumbnails": removed_thumbnails,
        "failed": failed,
    }


def delete_all_gallery_images() -> tuple[int, int]:
    """Delete all gallery entries and their image files.

    Returns (total_deleted, file_count) where total_deleted is the number of
    gallery entries removed and file_count is the number of image files deleted.
    The SQLite delete is committed before files are removed, keeping the write
    transaction short; failed file deletes are logged for later cleanup.
    """
    _ensure_database()
    with _storage_lock:
        disk_filenames = _scan_image_files()
        with _connect() as conn:
            with _transaction(conn):
                row = conn.execute(
                    "SELECT COUNT(*) FROM gallery_entries"
                ).fetchone()
                total = int(row[0]) if row else 0

                referenced_filenames = set(_get_all_filenames_on_conn(conn))

                conn.execute("DELETE FROM gallery_entries")
                conn.execute("DELETE FROM gallery_filter_options")
                _invalidate_filter_options_cache()
                _invalidate_gallery_query_caches_on_conn(conn)

    filenames_to_delete = referenced_filenames | disk_filenames
    deleted_count = 0
    for filename in filenames_to_delete:
        if _delete_gallery_file_if_unreferenced(filename):
            deleted_count += 1
    _clear_verified_thumbnails()
    return total, deleted_count


def invalidate_thumbnail_cache(thumbnail_filename: str) -> None:
    """从内存缩略图验证缓存中移除指定文件名，供路由层在检测到磁盘文件丢失时调用。"""
    _remove_verified_thumbnail(thumbnail_filename)
