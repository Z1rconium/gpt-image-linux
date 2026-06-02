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
GITHUB_REPO = _validate_github_repo(os.getenv("GITHUB_REPO", "Z1rconium/gpt-image-linux"))
ENABLE_VERSION_CHECK = env_flag("ENABLE_VERSION_CHECK", "true")
VERSION_CHECK_TIMEOUT_SECONDS = float(os.getenv("VERSION_CHECK_TIMEOUT_SECONDS", "3"))
VERSION_CHECK_BRANCH = os.getenv("VERSION_CHECK_BRANCH", "main").strip() or "main"
ENABLE_METRICS = env_flag("ENABLE_METRICS")
SLOW_GALLERY_QUERY_MS = max(1.0, float(os.getenv("SLOW_GALLERY_QUERY_MS", "200")))
ENABLE_NGINX_ACCEL_REDIRECT = env_flag("ENABLE_NGINX_ACCEL_REDIRECT")
ACCESS_KEY = os.getenv("ACCESS_KEY", "").strip()
ALLOW_UNAUTHENTICATED = env_flag("ALLOW_UNAUTHENTICATED")
ACCESS_KEY_SESSION_MINUTES = 180
ACCESS_KEY_COOKIE_NAME = os.getenv("ACCESS_KEY_COOKIE_NAME", "gpt_image_access")
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
WEBHOOK_HOST_ALLOWLIST = os.getenv("WEBHOOK_HOST_ALLOWLIST", "").strip()
WEBHOOK_SIGNING_SECRET = os.getenv("WEBHOOK_SIGNING_SECRET", "").strip()
WEBHOOK_TIMEOUT_SECONDS = float(os.getenv("WEBHOOK_TIMEOUT_SECONDS", "5"))
WEBHOOK_MAX_ATTEMPTS = int(os.getenv("WEBHOOK_MAX_ATTEMPTS", "3"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
MAX_JSON_BODY_MB = max(1, int(os.getenv("MAX_JSON_BODY_MB", "1")))
MAX_UPSTREAM_JSON_MB = max(1, int(os.getenv("MAX_UPSTREAM_JSON_MB", "128")))
MAX_IMAGE_PIXELS = max(1, int(os.getenv("MAX_IMAGE_PIXELS", "100000000")))
MAX_PENDING_EDIT_SOURCE_MB = max(
    0,
    int(os.getenv("MAX_PENDING_EDIT_SOURCE_MB", str(MAX_FILE_SIZE_MB * 4))),
)
IMPORT_ARCHIVE_MAX_MB = int(os.getenv("IMPORT_ARCHIVE_MAX_MB", str(MAX_FILE_SIZE_MB * 20)))
IMPORT_MAX_FILES = int(os.getenv("IMPORT_MAX_FILES", "500"))
IMPORT_MAX_UNCOMPRESSED_MB = int(os.getenv("IMPORT_MAX_UNCOMPRESSED_MB", "1024"))
IMPORT_MAX_METADATA_BYTES = int(os.getenv("IMPORT_MAX_METADATA_BYTES", str(2 * 1024 * 1024)))
IMPORT_MAX_COMPRESSION_RATIO = float(os.getenv("IMPORT_MAX_COMPRESSION_RATIO", "100"))
MAX_ACTIVE_GENERATE_JOBS = max(1, int(os.getenv("MAX_ACTIVE_GENERATE_JOBS", "2")))
MAX_QUEUED_GENERATE_JOBS = max(0, int(os.getenv("MAX_QUEUED_GENERATE_JOBS", "20")))
IMAGES_DIR = os.getenv("IMAGES_DIR", "./images")
THUMBNAILS_DIR = os.getenv("THUMBNAILS_DIR", os.path.join(IMAGES_DIR, "thumbs"))
THUMBNAIL_MAX_SIDE = max(1, int(os.getenv("THUMBNAIL_MAX_SIDE", "512")))
DATA_DIR = os.getenv("DATA_DIR", "./data")
DATABASE_FILE = os.getenv("DATABASE_FILE", os.path.join(DATA_DIR, "app.sqlite3"))

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

# ── Cloudflare R2 gallery backup ─────────────────────────────────
R2_BACKUP_ENABLED = env_flag("R2_BACKUP_ENABLED")
R2_ENDPOINT_URL = os.getenv("R2_ENDPOINT_URL", "").strip()
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "").strip()
R2_REGION = os.getenv("R2_REGION", "auto").strip() or "auto"
R2_KEY_PREFIX = os.getenv("R2_KEY_PREFIX", "gallery/").strip()
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "").strip()
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
R2_SYNC_INTERVAL_HOURS = env_non_negative_int("R2_SYNC_INTERVAL_HOURS", 0)
