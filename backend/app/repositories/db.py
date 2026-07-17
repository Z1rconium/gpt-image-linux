import asyncio
import base64
import binascii
import hashlib
import json
import logging
import os
import re
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
    is_malformed_env_var_ref,
    normalize_secret_env_ref_or_plaintext,
    normalize_r2_endpoint_url,
    normalize_socks5_proxy_url,
    normalize_webhook_url,
)
from ..schemas.gallery import GalleryEntry, GalleryFilterOptions
from ..schemas.snippets import PromptSnippet
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
    "webhook_url",
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
AI_ASSISTANT_SETTINGS_KEY = "ai_assistant_settings"
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
GALLERY_IMPORT_BATCH_SIZE = 50
MAX_PERSISTED_JOB_TEXT_CHARS = 2000
_GALLERY_COUNT_CACHE_MAX_SIZE = 512
_GALLERY_BYTES_CACHE_MAX_SIZE = 512
THUMBNAIL_CPU_SLOT_LEASE_SECONDS = 600
THUMBNAIL_JOB_LEASE_SECONDS = 600
THUMBNAIL_JOB_MAX_ATTEMPTS = 3
WORKER_METRIC_SNAPSHOT_TTL_SECONDS = 300

_initialized_database_file: Path | None = None
_db_init_lock = threading.RLock()
_storage_lock = threading.RLock()
_gallery_file_write_lock = threading.RLock()
_thread_local = threading.local()
_initialized_storage_paths: tuple[Path, Path, Path, Path] | None = None
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
        "ai_assistant": _default_ai_assistant_settings(),
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


def _default_ai_assistant_settings() -> dict:
    return {
        "enabled": config.AI_ASSISTANT_ENABLED,
        "vision_model": config.AI_ASSISTANT_VISION_MODEL or config.PROMPT_OPTIMIZER_MODEL,
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


def _normalize_ai_assistant_settings(settings: dict | None) -> dict:
    default = _default_ai_assistant_settings()
    if not isinstance(settings, dict):
        return default
    vision_model = (
        str(settings.get("vision_model") or default["vision_model"] or config.PROMPT_OPTIMIZER_MODEL).strip()
        or config.PROMPT_OPTIMIZER_MODEL
    )
    return {
        "enabled": _coerce_bool(settings.get("enabled"), default["enabled"]),
        "vision_model": vision_model,
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
    global _initialized_storage_paths
    storage_paths = (
        Path(config.IMAGES_DIR).resolve(),
        Path(config.THUMBNAILS_DIR).resolve(),
        Path(config.DATA_DIR).resolve(),
        Path(config.DATABASE_FILE).resolve().parent,
    )
    if _initialized_storage_paths == storage_paths:
        return
    for path in storage_paths:
        path.mkdir(parents=True, exist_ok=True)
    _secure_data_storage_permissions(force=True)
    _initialized_storage_paths = storage_paths


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
    global _gallery_fts_available, _initialized_database_file
    database_file = Path(config.DATABASE_FILE).resolve()
    if _initialized_database_file == database_file and database_file.exists():
        return

    with _db_init_lock:
        database_file = Path(config.DATABASE_FILE).resolve()
        if _initialized_database_file == database_file and database_file.exists():
            return
        if _initialized_database_file != database_file:
            _gallery_fts_available = None
            _invalidate_gallery_query_caches()
            _clear_verified_thumbnails()

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
                    error TEXT,
                    webhook_url TEXT
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

                CREATE TABLE IF NOT EXISTS gallery_ai_metadata (
                    image_id TEXT PRIMARY KEY,
                    description TEXT NOT NULL DEFAULT '',
                    prompt TEXT NOT NULL DEFAULT '',
                    analysis_json TEXT NOT NULL DEFAULT '{}',
                    model TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(image_id) REFERENCES gallery_entries(id) ON DELETE CASCADE
                );

                """
            )
            _migrate_api_presets_schema(conn)
            _migrate_gallery_schema(conn)
            _migrate_generate_jobs_schema(conn)
            _migrate_gallery_jobs_schema(conn)
            _migrate_r2_sync_state_schema(conn)
            _migrate_prompt_snippets_schema(conn)
            _migrate_gallery_ai_metadata_schema(conn)
            _run_schema_migrations(conn)
            _ensure_gallery_fts(conn)
            conn.commit()

        _initialized_database_file = database_file
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
    if "webhook_url" not in columns:
        conn.execute("ALTER TABLE generate_jobs ADD COLUMN webhook_url TEXT")


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


def _migrate_gallery_ai_metadata_schema(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gallery_ai_metadata (
            image_id TEXT PRIMARY KEY,
            description TEXT NOT NULL DEFAULT '',
            prompt TEXT NOT NULL DEFAULT '',
            analysis_json TEXT NOT NULL DEFAULT '{}',
            model TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(image_id) REFERENCES gallery_entries(id) ON DELETE CASCADE
        )
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
    from ..core.overall_config import OVERALL_CONFIG_BY_NAME

    _ensure_database()
    now = utc_now()
    scrubbed_plaintext_overrides: list[str] = []
    with _connect() as conn:
        with _transaction(conn):
            for name, (env_value, is_env_set) in env_values.items():
                spec = OVERALL_CONFIG_BY_NAME.get(name)
                stored_env_value = "" if spec and spec.secret else str(env_value or "")
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
                    (name, stored_env_value, 1 if is_env_set else 0, now),
                )
                if (
                    spec
                    and spec.secret
                    and not config.ALLOW_PLAINTEXT_SECRETS
                ):
                    row = conn.execute(
                        "SELECT override_value FROM overall_config_values WHERE name = ?",
                        (name,),
                    ).fetchone()
                    override = str(row["override_value"] or "") if row else ""
                    if override and not get_env_var_ref_name(override):
                        conn.execute(
                            """
                            UPDATE overall_config_values
                            SET override_value = NULL, override_updated_at = NULL
                            WHERE name = ?
                            """,
                            (name,),
                        )
                        scrubbed_plaintext_overrides.append(name)
            rows = _overall_config_rows(conn)
    if scrubbed_plaintext_overrides:
        logger.warning(
            "Removed legacy plaintext secret overrides from SQLite for %s; "
            "rotate any affected credentials and replace them with environment references",
            ", ".join(sorted(scrubbed_plaintext_overrides)),
        )
    _secure_data_storage_permissions()
    return rows


def list_overall_config_values() -> dict[str, dict[str, Any]]:
    _ensure_database()
    with _connect() as conn:
        return _overall_config_rows(conn)


def save_overall_config_overrides(
    updates: dict[str, str | None],
) -> dict[str, dict[str, Any]]:
    from ..core.overall_config import OVERALL_CONFIG_BY_NAME

    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            for name, value in updates.items():
                spec = OVERALL_CONFIG_BY_NAME.get(name)
                if (
                    spec
                    and spec.secret
                    and value
                    and is_malformed_env_var_ref(value)
                ):
                    raise ValueError(
                        f"{name} env ref must be formatted as ${{ENV_VAR_NAME}}"
                    )
                if (
                    spec
                    and spec.secret
                    and value
                    and not get_env_var_ref_name(value)
                    and not config.ALLOW_PLAINTEXT_SECRETS
                ):
                    raise ValueError(
                        f"{name} must use ${{ENV_VAR_NAME}} unless "
                        "ALLOW_PLAINTEXT_SECRETS=true"
                    )
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
    ai_assistant = (
        _normalize_ai_assistant_settings(settings.get("ai_assistant"))
        if "ai_assistant" in settings
        else _default_ai_assistant_settings()
    )

    raw_presets = settings.get("presets")
    if not isinstance(raw_presets, list):
        default_settings = _default_settings()
        default_settings["upstream_socks5_proxy"] = upstream_socks5_proxy
        default_settings["webhook_url"] = webhook_url
        default_settings["ai_assistant"] = ai_assistant
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
        default_settings["ai_assistant"] = ai_assistant
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
        "ai_assistant": ai_assistant,
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
    ai_assistant = normalized.get("ai_assistant")
    if ai_assistant is not None:
        _set_setting_value(conn, AI_ASSISTANT_SETTINGS_KEY, json.dumps(ai_assistant))
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

    assistant_json = _get_setting_value(conn, AI_ASSISTANT_SETTINGS_KEY)
    ai_assistant = None
    if assistant_json:
        try:
            ai_assistant = _normalize_ai_assistant_settings(json.loads(assistant_json))
        except (json.JSONDecodeError, TypeError):
            ai_assistant = _default_ai_assistant_settings()
    else:
        ai_assistant = _default_ai_assistant_settings()

    r2_backup = _load_r2_backup_settings_from_conn(conn)

    return _normalize_settings(
        {
            "active_preset_id": active_preset_id,
            "upstream_socks5_proxy": upstream_socks5_proxy,
            "webhook_url": webhook_url,
            "presets": presets,
            "prompt_optimizer": optimizer,
            "ai_assistant": ai_assistant,
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
            text = str(value)
            if column in {"message", "error"}:
                text = _sanitize_persisted_job_text(text)
            normalized[column] = text

    return normalized


def _sanitize_persisted_job_text(value: str) -> str:
    text = str(value or "")[:MAX_PERSISTED_JOB_TEXT_CHARS]
    text = re.sub(
        r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+",
        r"\1[REDACTED]",
        text,
    )
    return re.sub(
        r'''(?i)(["']?(?:api[_-]?key|access[_-]?key|secret|token)["']?\s*[:=]\s*["']?)[^"',\s}]+''',
        r"\1[REDACTED]",
        text,
    )


def _generate_job_values(job: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(job.get(column) for column in GENERATE_JOB_COLUMNS)


def _upsert_generate_job_on_conn(conn: sqlite3.Connection, job: dict[str, Any]) -> None:
    columns_sql = ", ".join(GENERATE_JOB_COLUMNS)
    placeholders_sql = ", ".join("?" for _ in GENERATE_JOB_COLUMNS)
    updates_sql = ", ".join(
        (
            f"{column} = COALESCE(excluded.{column}, generate_jobs.{column})"
            if column == "webhook_url"
            else f"{column} = excluded.{column}"
        )
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


def _attach_gallery_thumbnail_url(entry: dict[str, Any]) -> dict[str, Any]:
    if "image_url" not in entry:
        entry["image_url"] = image_url_for_filename(str(entry.get("filename") or ""))
    if "thumbnail_url" not in entry:
        entry["thumbnail_url"] = _thumbnail_url_for_filename(
            str(entry.get("filename") or "")
        )
    return entry


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


# Internal repository modules explicitly import private SQL helpers from here.
__all__ = [name for name in globals() if not name.startswith("__")]
