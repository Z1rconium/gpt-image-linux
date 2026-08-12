import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
VERSION_FILE = PROJECT_ROOT / "VERSION"


def read_app_version() -> str:
    env_version = os.getenv("APP_VERSION", "").strip()
    if env_version:
        return env_version

    try:
        file_version = VERSION_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return "v0.0.0"

    return file_version or "v0.0.0"


def env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def env_non_negative_int(name: str, default: int = 0) -> int:
    try:
        parsed = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _validate_github_repo(value: str) -> str:
    val = value.strip()
    if not val:
        return ""
    if not re.match(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$", val):
        raise ValueError(f"Invalid GITHUB_REPO format: '{val}'. Expected 'owner/repo'.")
    return val


DEFAULT_API_URL = os.getenv("DEFAULT_API_URL", "")
DEFAULT_API_KEY = os.getenv("DEFAULT_API_KEY", "")
DEFAULT_API_PATH = os.getenv("DEFAULT_API_PATH", "/v1/images/generations")
DEFAULT_RESPONSES_MODEL = os.getenv("DEFAULT_RESPONSES_MODEL", "gpt-5.4")
DEFAULT_UPSTREAM_SOCKS5_PROXY = os.getenv("DEFAULT_UPSTREAM_SOCKS5_PROXY", "").strip()
AIOHTTP_CONNECTION_LIMIT = max(1, int(os.getenv("AIOHTTP_CONNECTION_LIMIT", "100")))
AIOHTTP_CONNECTION_LIMIT_PER_HOST = max(
    0,
    int(os.getenv("AIOHTTP_CONNECTION_LIMIT_PER_HOST", "20")),
)
ALLOW_PLAINTEXT_SECRETS = env_flag("ALLOW_PLAINTEXT_SECRETS")
SECRET_REGISTRY_JSON = os.getenv("SECRET_REGISTRY_JSON", "").strip()
GITHUB_REPO = _validate_github_repo(os.getenv("GITHUB_REPO", "Z1rconium/gpt-image-linux"))
ENABLE_VERSION_CHECK = env_flag("ENABLE_VERSION_CHECK", "true")
VERSION_CHECK_TIMEOUT_SECONDS = float(os.getenv("VERSION_CHECK_TIMEOUT_SECONDS", "3"))
VERSION_CHECK_BRANCH = os.getenv("VERSION_CHECK_BRANCH", "main").strip() or "main"
VERSION_CHECK_CACHE_SECONDS = max(
    60,
    int(os.getenv("VERSION_CHECK_CACHE_SECONDS", "600")),
)
ENABLE_METRICS = env_flag("ENABLE_METRICS")
SLOW_GALLERY_QUERY_MS = max(1.0, float(os.getenv("SLOW_GALLERY_QUERY_MS", "200")))
ENABLE_NGINX_ACCEL_REDIRECT = env_flag("ENABLE_NGINX_ACCEL_REDIRECT")
PUBLIC_IMAGE_BASE_URL = os.getenv("PUBLIC_IMAGE_BASE_URL", "").strip().rstrip("/")
PUBLIC_THUMBNAIL_BASE_URL = os.getenv("PUBLIC_THUMBNAIL_BASE_URL", "").strip().rstrip("/")
CDN_SIGNING_SECRET = os.getenv("CDN_SIGNING_SECRET", "").strip()
CDN_URL_TTL_SECONDS = max(30, min(3600, int(os.getenv("CDN_URL_TTL_SECONDS", "300"))))
ACCESS_KEY = os.getenv("ACCESS_KEY", "").strip()
ADMIN_KEY = os.getenv("ADMIN_KEY", ACCESS_KEY).strip()
ALLOW_UNAUTHENTICATED = env_flag("ALLOW_UNAUTHENTICATED")
ACCESS_KEY_SESSION_MINUTES = 180
ACCESS_KEY_COOKIE_NAME = os.getenv("ACCESS_KEY_COOKIE_NAME", "gpt_image_access")
ADMIN_COOKIE_NAME = os.getenv("ADMIN_COOKIE_NAME", "gpt_image_admin")
ADMIN_SESSION_MINUTES = max(1, int(os.getenv("ADMIN_SESSION_MINUTES", "10")))
ACCESS_COOKIE_SECURE = env_flag("ACCESS_COOKIE_SECURE", "true")
ACCESS_MAX_FAILURES = int(os.getenv("ACCESS_MAX_FAILURES", "5"))
ACCESS_LOCKOUT_SECONDS = int(os.getenv("ACCESS_LOCKOUT_SECONDS", "300"))
IP_ALLOWLIST = os.getenv("IP_ALLOWLIST", "")
TRUST_PROXY_HEADERS = env_flag("TRUST_PROXY_HEADERS")
TRUSTED_PROXY_IPS = os.getenv("TRUSTED_PROXY_IPS", "").strip()
PUBLIC_ORIGIN = os.getenv("PUBLIC_ORIGIN", "").strip()
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "").strip()
CSRF_ORIGIN_CHECK_ENABLED = env_flag("CSRF_ORIGIN_CHECK_ENABLED", "true")
UPSTREAM_HOST_ALLOWLIST = os.getenv("UPSTREAM_HOST_ALLOWLIST", "").strip()
UPSTREAM_PROXY_HOST_ALLOWLIST = os.getenv("UPSTREAM_PROXY_HOST_ALLOWLIST", "").strip()
WEBHOOK_HOST_ALLOWLIST = os.getenv("WEBHOOK_HOST_ALLOWLIST", "").strip()
WEBHOOK_SIGNING_SECRET = os.getenv("WEBHOOK_SIGNING_SECRET", "").strip()
WEBHOOK_TIMEOUT_SECONDS = float(os.getenv("WEBHOOK_TIMEOUT_SECONDS", "5"))
WEBHOOK_MAX_ATTEMPTS = int(os.getenv("WEBHOOK_MAX_ATTEMPTS", "3"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
MAX_JSON_BODY_MB = max(1, int(os.getenv("MAX_JSON_BODY_MB", "1")))
MAX_UPSTREAM_JSON_MB = max(1, int(os.getenv("MAX_UPSTREAM_JSON_MB", "128")))
MAX_UPSTREAM_IMAGE_BYTES_PER_TASK_MB = max(
    1,
    int(os.getenv("MAX_UPSTREAM_IMAGE_BYTES_PER_TASK_MB", str(MAX_FILE_SIZE_MB))),
)
UPSTREAM_MEMORY_BUDGET_MB = max(
    MAX_UPSTREAM_IMAGE_BYTES_PER_TASK_MB,
    int(os.getenv("UPSTREAM_MEMORY_BUDGET_MB", "256")),
)
MAX_IMAGE_PIXELS = max(1, int(os.getenv("MAX_IMAGE_PIXELS", "100000000")))
IMAGE_CPU_CONCURRENCY = max(1, int(os.getenv("IMAGE_CPU_CONCURRENCY", "2")))
FILE_IO_CONCURRENCY = max(1, int(os.getenv("FILE_IO_CONCURRENCY", "4")))
DB_EXECUTOR_WORKERS = max(1, int(os.getenv("DB_EXECUTOR_WORKERS", "4")))
SQLITE_BUSY_TIMEOUT_MS = max(10, int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "250")))
SQLITE_BUSY_RETRY_ATTEMPTS = max(0, int(os.getenv("SQLITE_BUSY_RETRY_ATTEMPTS", "5")))
SQLITE_BUSY_RETRY_BASE_MS = max(1, int(os.getenv("SQLITE_BUSY_RETRY_BASE_MS", "20")))
IMAGE_JOB_PROGRESS_PERSIST_INTERVAL_SECONDS = max(
    0.1,
    float(os.getenv("IMAGE_JOB_PROGRESS_PERSIST_INTERVAL_SECONDS", "1")),
)
RUNTIME_METRICS_REFRESH_SECONDS = max(
    5.0,
    float(os.getenv("RUNTIME_METRICS_REFRESH_SECONDS", "15")),
)
EVENT_LOOP_LAG_SAMPLE_SECONDS = max(
    0.1,
    float(os.getenv("EVENT_LOOP_LAG_SAMPLE_SECONDS", "0.5")),
)
MAX_PENDING_EDIT_SOURCE_MB = max(
    0,
    int(os.getenv("MAX_PENDING_EDIT_SOURCE_MB", str(MAX_FILE_SIZE_MB * 4))),
)
IMPORT_ARCHIVE_MAX_MB = int(os.getenv("IMPORT_ARCHIVE_MAX_MB", str(MAX_FILE_SIZE_MB * 20)))
IMPORT_MAX_FILES = int(os.getenv("IMPORT_MAX_FILES", "500"))
IMPORT_MAX_UNCOMPRESSED_MB = int(os.getenv("IMPORT_MAX_UNCOMPRESSED_MB", "1024"))
IMPORT_MAX_METADATA_BYTES = int(os.getenv("IMPORT_MAX_METADATA_BYTES", str(2 * 1024 * 1024)))
IMPORT_MAX_ENTRIES = max(1, int(os.getenv("IMPORT_MAX_ENTRIES", "500")))
IMPORT_MAX_OUTPUT_MB = max(1, int(os.getenv("IMPORT_MAX_OUTPUT_MB", "1024")))
IMPORT_MAX_COMPRESSION_RATIO = float(os.getenv("IMPORT_MAX_COMPRESSION_RATIO", "100"))
IMPORT_TEMP_RESERVATION_MAX_MB = max(
    1,
    int(os.getenv("IMPORT_TEMP_RESERVATION_MAX_MB", str(IMPORT_ARCHIVE_MAX_MB * 2))),
)
IMPORT_UPLOAD_RESERVATION_TTL_SECONDS = max(
    60,
    int(os.getenv("IMPORT_UPLOAD_RESERVATION_TTL_SECONDS", "1800")),
)
IMPORT_UPLOADS_PER_IP_PER_MINUTE = max(
    1,
    int(os.getenv("IMPORT_UPLOADS_PER_IP_PER_MINUTE", "3")),
)
MAX_ACTIVE_GENERATE_JOBS = max(1, int(os.getenv("MAX_ACTIVE_GENERATE_JOBS", "2")))
MAX_QUEUED_GENERATE_JOBS = max(0, int(os.getenv("MAX_QUEUED_GENERATE_JOBS", "20")))
IMAGE_JOB_UNIT_LEASE_SECONDS = max(30, int(os.getenv("IMAGE_JOB_UNIT_LEASE_SECONDS", "120")))
IMAGE_JOB_UNIT_POLL_INTERVAL_SECONDS = max(
    0.1,
    float(os.getenv("IMAGE_JOB_UNIT_POLL_INTERVAL_SECONDS", "0.35")),
)
IMAGES_DIR = os.getenv("IMAGES_DIR", "./images")
THUMBNAILS_DIR = os.getenv("THUMBNAILS_DIR", os.path.join(IMAGES_DIR, "thumbs"))
THUMBNAIL_MAX_SIDE = max(1, int(os.getenv("THUMBNAIL_MAX_SIDE", "512")))
THUMBNAIL_CPU_CONCURRENCY = max(1, int(os.getenv("THUMBNAIL_CPU_CONCURRENCY", "1")))
DATA_DIR = os.getenv("DATA_DIR", "./data")
DATABASE_FILE = os.getenv("DATABASE_FILE", os.path.join(DATA_DIR, "app.sqlite3"))
LOG_DIR = os.getenv("LOG_DIR", os.path.join(DATA_DIR, "logs"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
LOG_RETENTION_HOURS = max(1, int(os.getenv("LOG_RETENTION_HOURS", "24")))

# ── SSE connection limits ────────────────────────────────────────
MAX_SSE_SUBSCRIBERS_GLOBAL = max(1, int(os.getenv("MAX_SSE_SUBSCRIBERS_GLOBAL", "200")))
MAX_SSE_SUBSCRIBERS_PER_IP = max(1, int(os.getenv("MAX_SSE_SUBSCRIBERS_PER_IP", "10")))
SSE_CONNECTION_TTL_SECONDS = max(60, int(os.getenv("SSE_CONNECTION_TTL_SECONDS", "3600")))

# ── Prompt optimizer ────────────────────────────────────────────
PROMPT_OPTIMIZER_ENABLED = env_flag("PROMPT_OPTIMIZER_ENABLED")
PROMPT_OPTIMIZER_API_URL = os.getenv("PROMPT_OPTIMIZER_API_URL", "").strip()
PROMPT_OPTIMIZER_API_KEY = os.getenv("PROMPT_OPTIMIZER_API_KEY", "").strip()
PROMPT_OPTIMIZER_MODEL = os.getenv("PROMPT_OPTIMIZER_MODEL", "gpt-4o-mini").strip()
PROMPT_OPTIMIZER_TIMEOUT_SECONDS = max(
    1,
    int(os.getenv("PROMPT_OPTIMIZER_TIMEOUT_SECONDS", "60")),
)
PROMPT_OPTIMIZER_MAX_OUTPUT_CHARS = int(os.getenv("PROMPT_OPTIMIZER_MAX_OUTPUT_CHARS", "4000"))
PROMPT_OPTIMIZER_MAX_RESPONSE_MB = max(
    1,
    int(os.getenv("PROMPT_OPTIMIZER_MAX_RESPONSE_MB", "8")),
)
PROMPT_OPTIMIZER_HOST_ALLOWLIST = os.getenv("PROMPT_OPTIMIZER_HOST_ALLOWLIST", "").strip()

# ── AI Assistant ────────────────────────────────────────────────
AI_ASSISTANT_ENABLED = env_flag("AI_ASSISTANT_ENABLED", "true")
AI_ASSISTANT_VISION_MODEL = os.getenv("AI_ASSISTANT_VISION_MODEL", "gpt-4o-mini").strip()
AI_ASSISTANT_MAX_RESPONSE_MB = max(
    1,
    int(os.getenv("AI_ASSISTANT_MAX_RESPONSE_MB", "8")),
)
AI_ASSISTANT_MAX_CONCURRENCY = max(
    1,
    int(os.getenv("AI_ASSISTANT_MAX_CONCURRENCY", str(MAX_ACTIVE_GENERATE_JOBS))),
)
AI_ASSISTANT_BATCH_MAX_IMAGES = max(
    1,
    int(os.getenv("AI_ASSISTANT_BATCH_MAX_IMAGES", "200")),
)
AI_ASSISTANT_IMAGE_MAX_SIDE = max(
    256,
    int(os.getenv("AI_ASSISTANT_IMAGE_MAX_SIDE", "1024")),
)
AI_ASSISTANT_IMAGE_MAX_BYTES = max(
    65536,
    int(os.getenv("AI_ASSISTANT_IMAGE_MAX_BYTES", str(1024 * 1024))),
)

# ── Cloudflare R2 gallery backup ─────────────────────────────────
R2_BACKUP_ENABLED = env_flag("R2_BACKUP_ENABLED")
R2_ENDPOINT_URL = os.getenv("R2_ENDPOINT_URL", "").strip()
R2_ENDPOINT_HOST_ALLOWLIST = os.getenv("R2_ENDPOINT_HOST_ALLOWLIST", "").strip()
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "").strip()
R2_REGION = os.getenv("R2_REGION", "auto").strip() or "auto"
R2_KEY_PREFIX = os.getenv("R2_KEY_PREFIX", "gallery/").strip()
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "").strip()
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
R2_SYNC_INTERVAL_HOURS = env_non_negative_int("R2_SYNC_INTERVAL_HOURS", 0)
R2_SYNC_CONCURRENCY = max(1, int(os.getenv("R2_SYNC_CONCURRENCY", "4")))

# ── NodeImage gallery upload ────────────────────────────────────
NODEIMAGE_API_KEY = os.getenv("NODEIMAGE_API_KEY", "").strip()
