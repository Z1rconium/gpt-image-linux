import asyncio
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from ..core import settings as config
from ..core.api_paths import default_model_for_api_path, normalize_api_preset
from ..core.constants import ACTIVE_GENERATE_JOB_STATUSES
from ..core.observability import observe_job_stage
from ..core.utils import utc_now
from ..core.validators import (
    normalize_secret_env_ref_or_plaintext,
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
    "backfill_missing_gallery_bytes",
    "close_database_connections",
    "clear_generate_job_history",
    "delete_all_gallery_images",
    "delete_gallery_image",
    "delete_gallery_images",
    "detect_image_format",
    "ensure_thumbnail_for_image",
    "generate_image_id",
    "get_all_filenames",
    "get_all_gallery_ids",
    "get_gallery",
    "get_gallery_count",
    "get_gallery_entry",
    "get_gallery_entries_by_ids",
    "get_gallery_filter_options",
    "is_gallery_filename_referenced",
    "get_gallery_page",
    "get_gallery_total_bytes",
    "get_generate_job",
    "get_image_dimensions",
    "import_gallery_entries",
    "iter_gallery_export_rows",
    "list_generate_jobs",
    "load_prompt_optimizer_settings",
    "load_settings",
    "list_prompt_snippets",
    "mark_active_generate_jobs_interrupted",
    "create_prompt_snippet",
    "delete_prompt_snippet",
    "safe_image_path",
    "safe_thumbnail_path",
    "save_prompt_optimizer_settings",
    "save_settings",
    "invalidate_thumbnail_cache",
    "sync_gallery_with_image_files",
    "trim_generate_jobs",
    "update_gallery_entries_favorite",
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
SQLITE_TIMEOUT_SECONDS = 30.0
DATA_DIR_MODE = 0o700
DATA_FILE_MODE = 0o600
GALLERY_FTS_VERSION_KEY = "gallery_fts_version"
GALLERY_FTS_VERSION = "trigram-v1"
GALLERY_FTS_MIN_QUERY_LENGTH = 3
GALLERY_TOTAL_BYTES_CACHE_SECONDS = 2.0
_GALLERY_BYTES_CACHE_MAX_SIZE = 512

_db_initialized = False
_db_init_lock = threading.RLock()
_storage_lock = threading.RLock()
_dirs_initialized = False

_filter_options_cache: "GalleryFilterOptions | None" = None
_filter_options_cache_lock = threading.RLock()
_gallery_total_bytes_cache: dict[tuple[str, str, tuple[Any, ...]], tuple[float, int]] = {}
_gallery_total_bytes_cache_lock = threading.RLock()
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


def _chmod_path(path: Path, mode: int) -> None:
    if os.name == "nt" or not path.exists():
        return
    try:
        os.chmod(path, mode)
    except OSError as e:
        logger.warning("Failed to chmod %s to %#o: %s", path, mode, e)


def _secure_data_storage_permissions() -> None:
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
    images: list[GalleryEntry]
    filter_options: GalleryFilterOptions
    query_elapsed_ms: float = 0.0


@dataclass
class _PreparedGalleryFile:
    filename: str
    image_temp_path: Path
    thumbnail_filename: str | None = None
    thumbnail_temp_path: Path | None = None


def _invalidate_filter_options_cache():
    global _filter_options_cache
    with _filter_options_cache_lock:
        _filter_options_cache = None


def _invalidate_gallery_total_bytes_cache():
    with _gallery_total_bytes_cache_lock:
        _gallery_total_bytes_cache.clear()


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


def _coerce_positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


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


def _ensure_directories():
    global _dirs_initialized
    if _dirs_initialized:
        return
    Path(config.IMAGES_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.THUMBNAILS_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.DATABASE_FILE).parent.mkdir(parents=True, exist_ok=True)
    _secure_data_storage_permissions()
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


def _open_connection() -> sqlite3.Connection:
    _ensure_directories()
    conn = sqlite3.connect(config.DATABASE_FILE, timeout=SQLITE_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = NORMAL")
    _secure_data_storage_permissions()
    return conn


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = _open_connection()
    try:
        yield conn
    finally:
        conn.close()


def close_database_connections():
    """No-op kept for API compatibility; connections are now per-call."""
    _clear_verified_thumbnails()



@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
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
        _secure_data_storage_permissions()
        return

    with _db_init_lock:
        if _db_initialized and Path(config.DATABASE_FILE).exists():
            _secure_data_storage_permissions()
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

                CREATE TABLE IF NOT EXISTS prompt_snippets (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                """
            )
            _migrate_api_presets_schema(conn)
            _migrate_gallery_schema(conn)
            _migrate_generate_jobs_schema(conn)
            _migrate_prompt_snippets_schema(conn)
            _ensure_gallery_fts(conn)
            conn.commit()

        _db_initialized = True
        _secure_data_storage_permissions()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


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
    if "favorite" in _table_columns(conn, "gallery_entries"):
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_favorite_created_at
                ON gallery_entries(favorite, created_at DESC, sort_seq DESC)
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
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_preset_created_at
            ON gallery_entries(api_preset_name, created_at DESC, sort_seq DESC)
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
        CREATE INDEX IF NOT EXISTS idx_gallery_entries_created_at
            ON gallery_entries(created_at DESC, sort_seq DESC)
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

    raw_presets = settings.get("presets")
    if not isinstance(raw_presets, list):
        default_settings = _default_settings()
        default_settings["upstream_socks5_proxy"] = upstream_socks5_proxy
        default_settings["webhook_url"] = webhook_url
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

    return _normalize_settings(
        {
            "active_preset_id": active_preset_id,
            "upstream_socks5_proxy": upstream_socks5_proxy,
            "webhook_url": webhook_url,
            "presets": presets,
            "prompt_optimizer": optimizer,
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


def _gallery_row_values(entry: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(entry.get(column) for column in GALLERY_COLUMNS)


def _gallery_entry_from_row(row: sqlite3.Row) -> dict[str, Any]:
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

    favorite = filters.get("favorite")
    if favorite is not None:
        clauses.append("favorite = ?")
        params.append(1 if favorite else 0)

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


def _generate_prompt_snippet_id() -> str:
    return f"ps_{secrets.token_urlsafe(12)}"


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
    _invalidate_filter_options_cache()
    _invalidate_gallery_total_bytes_cache()


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
    if "thumbnail_url" not in entry:
        entry["thumbnail_url"] = _thumbnail_url_for_filename(
            str(entry.get("filename") or "")
        )
    return entry


def _prepare_gallery_file(image_bytes: bytes, filename: str) -> _PreparedGalleryFile:
    image_temp_path = _save_image_temp_unlocked(image_bytes, filename)
    try:
        with observe_job_stage("thumbnail"):
            prepared_thumbnail = _create_thumbnail_temp_unlocked(image_bytes, filename)
    except BaseException:
        image_temp_path.unlink(missing_ok=True)
        raise

    if not prepared_thumbnail:
        return _PreparedGalleryFile(filename=filename, image_temp_path=image_temp_path)

    thumbnail_filename, thumbnail_temp_path = prepared_thumbnail
    return _PreparedGalleryFile(
        filename=filename,
        image_temp_path=image_temp_path,
        thumbnail_filename=thumbnail_filename,
        thumbnail_temp_path=thumbnail_temp_path,
    )


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
    conflicts_possible = conn.execute(
        "SELECT COUNT(*) FROM gallery_entries LIMIT 1"
    ).fetchone()[0] > 0

    if conflicts_possible:
        incoming_filenames = {str(e["filename"]) for e in entries}
        incoming_ids = {str(e["id"]) for e in entries}
        placeholders_fn = ", ".join("?" for _ in incoming_filenames)
        rows = conn.execute(
            f"SELECT DISTINCT filename FROM gallery_entries WHERE filename IN ({placeholders_fn})",
            tuple(incoming_filenames),
        ).fetchall()
        used_filenames = {row["filename"] for row in rows if row["filename"]}
        placeholders_id = ", ".join("?" for _ in incoming_ids)
        rows = conn.execute(
            f"SELECT id FROM gallery_entries WHERE id IN ({placeholders_id})",
            tuple(incoming_ids),
        ).fetchall()
        used_ids = {row["id"] for row in rows if row["id"]}

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
        return thumbnail_filename

    image_path = safe_image_path(filename)
    if not image_path or not image_path.is_file():
        return None

    for _ in range(3):
        if thumbnail_path and thumbnail_path.is_file():
            _add_verified_thumbnail(thumbnail_filename)
            return thumbnail_filename
        try:
            image_stat = image_path.stat()
        except OSError as e:
            logger.warning("Failed to stat image for thumbnail %s: %s", filename, e)
            return None

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

        with _storage_lock:
            with _connect() as conn:
                _dedupe_import_entries_on_conn(conn, normalized_entries, prepared_files)
                _promote_prepared_images(prepared_files)
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


def _backfill_gallery_bytes_from_filenames_on_conn(
    conn: sqlite3.Connection,
) -> int:
    total_updated = 0
    batch_size = 200
    last_filename = ""
    while True:
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
            filename = str(row["filename"] or "").strip()
            if not filename:
                continue
            last_filename = filename
            size = _stat_image_bytes(filename)
            if size is None:
                continue
            backfills.append((size, filename))

        if backfills:
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
        updated += _backfill_gallery_bytes_from_filenames_on_conn(conn)
    if updated:
        _invalidate_gallery_total_bytes_cache()
    return updated


def _get_gallery_count_on_conn(
    conn: sqlite3.Connection,
    where_sql: str,
    params: Sequence[Any],
) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) FROM gallery_entries{where_sql}",
        tuple(params),
    ).fetchone()
    return int(row[0]) if row else 0


def get_gallery_count(filters: dict[str, Any] | None = None) -> int:
    _ensure_database()
    with _connect() as conn:
        where_sql, params = _build_gallery_filter_where(filters)
        return _get_gallery_count_on_conn(conn, where_sql, params)


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
            return cached[1]

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
        if len(_gallery_total_bytes_cache) >= _GALLERY_BYTES_CACHE_MAX_SIZE:
            _gallery_total_bytes_cache.clear()
        _gallery_total_bytes_cache[cache_key] = (now, total_bytes)

    return total_bytes


def get_gallery_total_bytes(filters: dict[str, Any] | None = None) -> int:
    _ensure_database()
    with _connect() as conn:
        where_sql, params = _build_gallery_filter_where(filters)
        return _get_gallery_total_bytes_on_conn(conn, where_sql, params)


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
        ORDER BY created_at DESC, sort_seq DESC
    """
    query_params: list[Any] = list(params)
    if limit is not None:
        sql += " LIMIT ?"
        query_params.append(limit)
        if offset is not None:
            sql += " OFFSET ?"
            query_params.append(offset)
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
    return [GalleryEntry(**_gallery_entry_from_row(row)) for row in rows]


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
    last_created_at: str | None = None
    last_sort_seq: int | None = None
    while True:
        with _connect() as conn:
            if last_created_at is None:
                sql = f"""
                    SELECT {", ".join(GALLERY_COLUMNS)}
                    FROM gallery_entries
                    {where_sql}
                    ORDER BY created_at DESC, sort_seq DESC
                    LIMIT ?
                """
                query_params = list(params) + [batch_size]
            else:
                cursor_clause = "(created_at < ? OR (created_at = ? AND sort_seq < ?))"
                if where_sql:
                    combined_where = f"{where_sql} AND {cursor_clause}"
                else:
                    combined_where = f" WHERE {cursor_clause}"
                sql = f"""
                    SELECT {", ".join(GALLERY_COLUMNS)}
                    FROM gallery_entries
                    {combined_where}
                    ORDER BY created_at DESC, sort_seq DESC
                    LIMIT ?
                """
                query_params = list(params) + [last_created_at, last_created_at, last_sort_seq, batch_size]
            rows = conn.execute(sql, query_params).fetchall()
        if not rows:
            return
        for row in rows:
            yield _gallery_entry_from_row(row)
        if len(rows) < batch_size:
            return
        last_row = rows[-1]
        last_created_at = last_row["created_at"]
        last_sort_seq = last_row["sort_seq"]


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
    with _filter_options_cache_lock:
        cached = _filter_options_cache
        if cached is not None:
            return cached

    options: dict[str, list[str]] = {}
    for key, column in (
        ("models", "model"),
        ("presets", "api_preset_name"),
        ("sizes", "size"),
    ):
        rows = conn.execute(
            f"""
            SELECT DISTINCT {column} AS value
            FROM gallery_entries
            WHERE {column} IS NOT NULL AND TRIM({column}) != ''
            ORDER BY LOWER({column}) ASC
            """
        ).fetchall()
        options[key] = [row["value"] for row in rows if row["value"]]

    result = GalleryFilterOptions(**options)
    with _filter_options_cache_lock:
        _filter_options_cache = result
    return result


def get_gallery_filter_options() -> GalleryFilterOptions:
    _ensure_database()
    with _connect() as conn:
        return _get_gallery_filter_options_on_conn(conn)


def get_gallery_page(
    *,
    page: int = 1,
    page_size: int = 9,
    filters: dict[str, Any] | None = None,
    include_total_bytes: bool = False,
) -> GalleryPage:
    _ensure_database()
    requested_page = max(int(page), 1)
    page_size = max(int(page_size), 1)
    offset = (requested_page - 1) * page_size

    query_started_at = time.perf_counter()
    with _connect() as conn:
        where_sql, params = _build_gallery_filter_where(filters)

        rows = _get_gallery_rows_on_conn(
            conn,
            where_sql,
            params,
            limit=page_size + 1,
            offset=offset,
        )

        has_next = len(rows) > page_size
        if has_next:
            rows = rows[:page_size]

        has_prev = requested_page > 1

        total = _get_gallery_count_on_conn(conn, where_sql, params)
        total_pages = max((total + page_size - 1) // page_size, 1)
        effective_page = min(requested_page, total_pages)

        total_bytes = (
            _get_gallery_total_bytes_on_conn(conn, where_sql, params)
            if include_total_bytes
            else 0
        )
        filter_options = _get_gallery_filter_options_on_conn(conn)
    query_elapsed_ms = (time.perf_counter() - query_started_at) * 1000

    return GalleryPage(
        total=total,
        total_bytes=total_bytes,
        page=effective_page,
        page_size=page_size,
        total_pages=total_pages,
        has_prev=has_prev,
        has_next=has_next,
        images=[GalleryEntry(**_gallery_entry_from_row(row)) for row in rows],
        filter_options=filter_options,
        query_elapsed_ms=round(query_elapsed_ms, 2),
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
    if not row:
        return None
    return GalleryEntry(**_gallery_entry_from_row(row))


def get_gallery_entries_by_ids(image_ids: Sequence[str]) -> list[GalleryEntry]:
    """Fetch gallery entries for many ids in one query, preserving input order.

    Duplicate or missing ids are dropped.
    """
    _ensure_database()
    unique_ids = [image_id for image_id in dict.fromkeys(image_ids) if image_id]
    if not unique_ids:
        return []

    placeholders = ", ".join("?" for _ in unique_ids)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {", ".join(GALLERY_COLUMNS)}
            FROM gallery_entries
            WHERE id IN ({placeholders})
            """,
            tuple(unique_ids),
        ).fetchall()

    by_id = {row["id"]: row for row in rows}
    return [
        GalleryEntry(**_gallery_entry_from_row(by_id[image_id]))
        for image_id in unique_ids
        if image_id in by_id
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
                assignments = ", ".join(f"{key} = ?" for key in allowed_updates)
                conn.execute(
                    f"UPDATE gallery_entries SET {assignments} WHERE id = ?",
                    (*allowed_updates.values(), image_id),
                )
                _invalidate_gallery_total_bytes_cache()
                if allowed_updates.keys() & {"model", "api_preset_name", "size"}:
                    _invalidate_filter_options_cache()
                row = conn.execute(
                    f"""
                    SELECT {", ".join(GALLERY_COLUMNS)}
                    FROM gallery_entries
                    WHERE id = ?
                    """,
                    (image_id,),
                ).fetchone()

    return GalleryEntry(**_gallery_entry_from_row(row))


def update_gallery_entries_favorite(image_ids: list[str], favorite: bool) -> int:
    _ensure_database()
    if not image_ids:
        return 0

    placeholders = ", ".join("?" for _ in image_ids)
    with _connect() as conn:
        with _transaction(conn):
            rows = conn.execute(
                f"SELECT id FROM gallery_entries WHERE id IN ({placeholders})",
                tuple(image_ids),
            ).fetchall()
            found_ids = {row["id"] for row in rows}
            if not found_ids:
                return 0
            update_placeholders = ", ".join("?" for _ in found_ids)
            conn.execute(
                f"UPDATE gallery_entries SET favorite = ? WHERE id IN ({update_placeholders})",
                (_normalize_gallery_favorite(favorite), *found_ids),
            )
            _invalidate_gallery_total_bytes_cache()
            return len(found_ids)


def upsert_generate_job(job: dict[str, Any]) -> dict[str, Any]:
    _ensure_database()
    normalized = _normalize_generate_job(job)
    columns_sql = ", ".join(GENERATE_JOB_COLUMNS)
    placeholders_sql = ", ".join("?" for _ in GENERATE_JOB_COLUMNS)
    updates_sql = ", ".join(
        f"{column} = excluded.{column}"
        for column in GENERATE_JOB_COLUMNS
        if column != "job_id"
    )

    with _connect() as conn:
        with _transaction(conn):
            conn.execute(
                f"""
                INSERT INTO generate_jobs ({columns_sql})
                VALUES ({placeholders_sql})
                ON CONFLICT(job_id) DO UPDATE SET {updates_sql}
                """,
                _generate_job_values(normalized),
            )
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


def list_generate_jobs(
    statuses: set[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    _ensure_database()
    with _connect() as conn:
        params: list[Any] = []
        sql = f"""
            SELECT {", ".join(GENERATE_JOB_COLUMNS)}
            FROM generate_jobs
        """
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            sql += f" WHERE status IN ({placeholders})"
            params.extend(sorted(statuses))
        sql += " ORDER BY updated_at DESC, created_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
            if offset > 0:
                sql += " OFFSET ?"
                params.append(offset)
        elif offset > 0:
            sql += " LIMIT -1 OFFSET ?"
            params.append(offset)
        rows = conn.execute(sql, params).fetchall()
    return [_generate_job_from_row(row) for row in rows]


def clear_generate_job_history() -> int:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            placeholders = ", ".join("?" for _ in ACTIVE_GENERATE_JOB_STATUSES)
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
                "DELETE FROM generate_jobs WHERE job_id = ?",
                [(row["job_id"],) for row in rows],
            )


def sync_gallery_with_image_files() -> int:
    _ensure_database()
    with _storage_lock:
        image_filenames = _scan_image_files()
        with _connect() as conn:
            with _transaction(conn):
                rows = conn.execute("SELECT id, filename FROM gallery_entries").fetchall()
                stale_ids = [
                    row["id"]
                    for row in rows
                    if row["filename"] and row["filename"] not in image_filenames
                ]
                if stale_ids:
                    conn.executemany(
                        "DELETE FROM gallery_entries WHERE id = ?",
                        [(entry_id,) for entry_id in stale_ids],
                    )
                    _invalidate_filter_options_cache()
                    _invalidate_gallery_total_bytes_cache()
                    _clear_verified_thumbnails()
                return len(stale_ids)


def _delete_gallery_entries_by_ids(
    conn: sqlite3.Connection,
    image_ids: Sequence[str],
) -> tuple[list[str], set[str]]:
    """Delete gallery entries and return (removed_ids, filenames_to_delete).

    File deletion is NOT performed here — caller handles it after commit.
    """
    unique_ids = [image_id for image_id in dict.fromkeys(image_ids) if image_id]
    if not unique_ids:
        return [], set()

    placeholders = ", ".join("?" for _ in unique_ids)
    rows = conn.execute(
        f"SELECT id, filename FROM gallery_entries WHERE id IN ({placeholders})",
        tuple(unique_ids),
    ).fetchall()
    if not rows:
        return [], set()

    removed_ids = [row["id"] for row in rows]
    removed_filenames = {row["filename"] for row in rows if row["filename"]}
    delete_placeholders = ", ".join("?" for _ in removed_ids)
    conn.execute(
        f"DELETE FROM gallery_entries WHERE id IN ({delete_placeholders})",
        tuple(removed_ids),
    )
    _invalidate_filter_options_cache()
    _invalidate_gallery_total_bytes_cache()

    remaining_filenames: set[str] = set()
    if removed_filenames:
        filename_placeholders = ", ".join("?" for _ in removed_filenames)
        remaining_rows = conn.execute(
            f"""
            SELECT DISTINCT filename
            FROM gallery_entries
            WHERE filename IN ({filename_placeholders})
            """,
            tuple(removed_filenames),
        ).fetchall()
        remaining_filenames = {
            row["filename"] for row in remaining_rows if row["filename"]
        }

    filenames_to_delete = removed_filenames - remaining_filenames
    return removed_ids, filenames_to_delete


def delete_gallery_image(image_id: str) -> tuple[bool, int]:
    deleted_entries, deleted_files = delete_gallery_images([image_id])
    return deleted_entries > 0, deleted_files


def delete_gallery_images(image_ids: Sequence[str]) -> tuple[int, int]:
    _ensure_database()
    if not image_ids:
        return 0, 0

    with _storage_lock:
        with _connect() as conn:
            with _transaction(conn):
                removed_ids, filenames_to_delete = _delete_gallery_entries_by_ids(conn, image_ids)

    deleted_count = 0
    for filename in filenames_to_delete:
        try:
            if _delete_image_unlocked(filename):
                deleted_count += 1
        except OSError as e:
            logger.warning("Failed to delete image file %s: %s", filename, e)
        try:
            _delete_thumbnail_unlocked(filename)
        except OSError as e:
            logger.warning("Failed to delete thumbnail for %s: %s", filename, e)
        thumbnail_filename = _thumbnail_filename_for_image(filename)
        if thumbnail_filename:
            _remove_verified_thumbnail(thumbnail_filename)

    return len(removed_ids), deleted_count


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
            logger.warning("Failed to delete gallery image file %s: %s", filename, e)

        try:
            _delete_thumbnail_unlocked(filename)
            thumbnail_filename = _thumbnail_filename_for_image(filename)
            if thumbnail_filename:
                _remove_verified_thumbnail(thumbnail_filename)
        except OSError as e:
            logger.warning("Failed to delete gallery thumbnail for %s: %s", filename, e)

        return deleted


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
                _invalidate_filter_options_cache()
                _invalidate_gallery_total_bytes_cache()

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
