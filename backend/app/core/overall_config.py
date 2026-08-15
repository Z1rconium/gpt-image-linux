import os
import re
from dataclasses import dataclass
from typing import Any, Literal

from . import settings as config
from .validators import (
    get_env_var_ref_name,
    is_malformed_env_var_ref,
    resolve_env_var_ref,
)


OverallConfigType = Literal["string", "secret", "bool", "int", "float"]
OverallConfigSource = Literal["override", "env", "default"]

MASKED_OVERALL_SECRET_VALUE = "********"


@dataclass(frozen=True)
class OverallConfigSpec:
    name: str
    type: OverallConfigType
    default: str
    group: str
    description: str
    secret: bool = False
    exposed_in_settings: bool = False
    hot_reload: bool = True
    restart_required: bool = False
    build_only: bool = False
    min_value: float | None = None
    validator: str | None = None
    startup_only: bool = False


def _spec(
    name: str,
    type_: OverallConfigType,
    default: str,
    group: str,
    description: str,
    **kwargs: Any,
) -> OverallConfigSpec:
    return OverallConfigSpec(
        name=name,
        type=type_,
        default=default,
        group=group,
        description=description,
        **kwargs,
    )


OVERALL_CONFIG_REGISTRY: tuple[OverallConfigSpec, ...] = (
    _spec("DEFAULT_API_URL", "string", "", "Upstream API", "Default API base URL.", exposed_in_settings=True),
    _spec("DEFAULT_API_KEY", "secret", "", "Upstream API", "Default API key.", secret=True, exposed_in_settings=True),
    _spec("DEFAULT_API_PATH", "string", "/v1/images/generations", "Upstream API", "Default upstream route.", exposed_in_settings=True),
    _spec("DEFAULT_RESPONSES_MODEL", "string", "gpt-5.4", "Upstream API", "Fallback Responses API model.", exposed_in_settings=True),
    _spec("DEFAULT_UPSTREAM_SOCKS5_PROXY", "secret", "", "Upstream API", "Global SOCKS5 proxy.", secret=True, exposed_in_settings=True),
    _spec("AIOHTTP_CONNECTION_LIMIT", "int", "100", "Upstream API", "Global aiohttp connector connection limit.", min_value=1),
    _spec("AIOHTTP_CONNECTION_LIMIT_PER_HOST", "int", "20", "Upstream API", "Per-host aiohttp connector connection limit; 0 disables the per-host cap.", min_value=0),
    _spec("APP_VERSION", "string", "", "App / Version", "Version override shown by /api/version.", restart_required=True),
    _spec("GITHUB_REPO", "string", "Z1rconium/gpt-image-linux", "App / Version", "GitHub owner/repo used for update checks.", validator="github_repo"),
    _spec("ENABLE_VERSION_CHECK", "bool", "true", "App / Version", "Enable latest version checks."),
    _spec("VERSION_CHECK_TIMEOUT_SECONDS", "float", "3", "App / Version", "Version-check request timeout.", min_value=0.1),
    _spec("VERSION_CHECK_BRANCH", "string", "main", "App / Version", "Fallback VERSION branch."),
    _spec("ENABLE_METRICS", "bool", "false", "Observability", "Enable /api/metrics."),
    _spec("SLOW_GALLERY_QUERY_MS", "float", "200", "Observability", "Slow gallery query log threshold.", min_value=1),
    _spec("ENABLE_NGINX_ACCEL_REDIRECT", "bool", "false", "Observability", "Return X-Accel-Redirect for authorized image files when an nginx internal alias is in front.", restart_required=True),
    _spec("PUBLIC_IMAGE_BASE_URL", "string", "", "Observability", "Optional public/CDN base URL for gallery image bytes.", restart_required=True),
    _spec("PUBLIC_THUMBNAIL_BASE_URL", "string", "", "Observability", "Optional public/CDN base URL for generated gallery thumbnail bytes.", restart_required=True),
    _spec("GRANIAN_WORKERS", "int", "1", "Granian Runtime", "Granian worker process count; keep at or below MAX_ACTIVE_GENERATE_JOBS for generation throughput.", restart_required=True, min_value=1),
    _spec("GRANIAN_RUNTIME_THREADS", "int", "2", "Granian Runtime", "Rust runtime threads per worker; try 1 when using multiple workers behind nginx/HTTP/1.", restart_required=True, min_value=1),
    _spec("GRANIAN_LOOP", "string", "uvloop", "Granian Runtime", "Granian event loop implementation.", restart_required=True),
    _spec("GRANIAN_BACKPRESSURE", "int", "100", "Granian Runtime", "Per-worker request backpressure before Granian stops accepting more slow requests into Python.", restart_required=True, min_value=1),
    _spec("GRANIAN_BACKLOG", "int", "2048", "Granian Runtime", "Socket listen backlog for pending connections.", restart_required=True, min_value=1),
    _spec("GRANIAN_STATIC_PATH_ROUTE", "string", "/_app/immutable", "Granian Runtime", "Direct-Docker public immutable asset route served by Granian.", restart_required=True),
    _spec("GRANIAN_STATIC_PATH_MOUNT", "string", "/app/frontend/build/_app/immutable", "Granian Runtime", "Direct-Docker immutable asset directory mounted into Granian static serving.", restart_required=True),
    _spec("GRANIAN_STATIC_PATH_EXPIRES", "int", "31536000", "Granian Runtime", "Granian static asset max-age seconds.", restart_required=True, min_value=0),
    _spec("PROMPT_OPTIMIZER_ENABLED", "bool", "false", "Prompt Optimizer", "Enable prompt optimizer.", exposed_in_settings=True),
    _spec("PROMPT_OPTIMIZER_API_URL", "string", "", "Prompt Optimizer", "Optimizer endpoint URL.", exposed_in_settings=True),
    _spec("PROMPT_OPTIMIZER_API_KEY", "secret", "", "Prompt Optimizer", "Optimizer API key.", secret=True, exposed_in_settings=True),
    _spec("PROMPT_OPTIMIZER_MODEL", "string", "gpt-4o-mini", "Prompt Optimizer", "Optimizer model.", exposed_in_settings=True),
    _spec("PROMPT_OPTIMIZER_TIMEOUT_SECONDS", "int", "60", "Prompt Optimizer", "Optimizer timeout.", exposed_in_settings=True, min_value=1),
    _spec("PROMPT_OPTIMIZER_MAX_OUTPUT_CHARS", "int", "4000", "Prompt Optimizer", "Max optimized prompt characters.", min_value=1),
    _spec("PROMPT_OPTIMIZER_MAX_RESPONSE_MB", "int", "8", "Prompt Optimizer", "Max optimizer response body size.", min_value=1),
    _spec("PROMPT_OPTIMIZER_HOST_ALLOWLIST", "string", "", "Prompt Optimizer", "Optimizer endpoint host allowlist.", validator="host_list", startup_only=True, restart_required=True),
    _spec("AI_ASSISTANT_ENABLED", "bool", "true", "AI Assistant", "Enable AI Assistant tools.", exposed_in_settings=True),
    _spec("AI_ASSISTANT_VISION_MODEL", "string", "gpt-4o-mini", "AI Assistant", "Assistant vision model.", exposed_in_settings=True),
    _spec("AI_ASSISTANT_MAX_RESPONSE_MB", "int", "8", "AI Assistant", "Max assistant response body size.", min_value=1),
    _spec("AI_ASSISTANT_MAX_CONCURRENCY", "int", "2", "AI Assistant", "Global assistant upstream request concurrency.", min_value=1),
    _spec("AI_ASSISTANT_BATCH_MAX_IMAGES", "int", "200", "AI Assistant", "Max images per gallery AI batch analysis job.", min_value=1),
    _spec("AI_ASSISTANT_IMAGE_MAX_SIDE", "int", "1024", "AI Assistant", "Max assistant vision preview side.", min_value=256),
    _spec("AI_ASSISTANT_IMAGE_MAX_BYTES", "int", "1048576", "AI Assistant", "Max assistant vision preview bytes.", min_value=65536),
    _spec("R2_BACKUP_ENABLED", "bool", "false", "R2 Backup", "Enable R2 backup.", exposed_in_settings=True),
    _spec("R2_ENDPOINT_URL", "string", "", "R2 Backup", "R2 endpoint URL.", exposed_in_settings=True),
    _spec("R2_ENDPOINT_HOST_ALLOWLIST", "string", "", "R2 Backup", "Allowed R2 endpoint hostnames.", validator="host_list", startup_only=True, restart_required=True),
    _spec("R2_BUCKET_NAME", "string", "", "R2 Backup", "R2 bucket name.", exposed_in_settings=True),
    _spec("R2_REGION", "string", "auto", "R2 Backup", "R2 region.", exposed_in_settings=True),
    _spec("R2_KEY_PREFIX", "string", "gallery/", "R2 Backup", "R2 key prefix.", exposed_in_settings=True),
    _spec("R2_ACCESS_KEY_ID", "secret", "", "R2 Backup", "R2 access key ID.", secret=True, exposed_in_settings=True),
    _spec("R2_SECRET_ACCESS_KEY", "secret", "", "R2 Backup", "R2 secret access key.", secret=True, exposed_in_settings=True),
    _spec("R2_SYNC_INTERVAL_HOURS", "int", "0", "R2 Backup", "Scheduled R2 sync interval in hours; 0 disables automatic sync.", exposed_in_settings=True, min_value=0),
    _spec("R2_SYNC_CONCURRENCY", "int", "4", "R2 Backup", "Concurrent R2 HEAD/upload workers.", min_value=1),
    _spec("NODEIMAGE_API_KEY", "secret", "", "NodeImage", "NodeImage API key.", secret=True, exposed_in_settings=True),
    _spec("ACCESS_KEY", "secret", "", "Access / Security", "Access gate key.", secret=True, restart_required=True, startup_only=True),
    _spec("ALLOW_UNAUTHENTICATED", "bool", "false", "Access / Security", "Allow startup without access key; logs a warning because non-health APIs are unauthenticated.", restart_required=True),
    _spec("ACCESS_KEY_COOKIE_NAME", "string", "gpt_image_access", "Access / Security", "Access cookie name.", restart_required=True),
    _spec("ACCESS_COOKIE_SECURE", "bool", "true", "Access / Security", "Set Secure on access cookie.", restart_required=True),
    _spec("ACCESS_MAX_FAILURES", "int", "5", "Access / Security", "Failed access attempts before lockout.", min_value=1, restart_required=True),
    _spec("ACCESS_LOCKOUT_SECONDS", "int", "300", "Access / Security", "Access lockout duration.", min_value=1, restart_required=True),
    _spec("ADMIN_MAX_FAILURES", "int", "5", "Access / Security", "Failed administrator attempts before the independent admin lockout.", min_value=1, restart_required=True),
    _spec("ADMIN_LOCKOUT_SECONDS", "int", "300", "Access / Security", "Administrator lockout duration.", min_value=1, restart_required=True),
    _spec("IP_ALLOWLIST", "string", "", "Access / Security", "Client IP/CIDR allowlist.", validator="ip_list", restart_required=True, startup_only=True),
    _spec("TRUST_PROXY_HEADERS", "bool", "false", "Access / Security", "Trust reverse-proxy headers.", restart_required=True),
    _spec("TRUSTED_PROXY_IPS", "string", "", "Access / Security", "Trusted reverse proxy IP/CIDR list.", validator="ip_list", restart_required=True, startup_only=True),
    _spec("PUBLIC_ORIGIN", "string", "", "Access / Security", "Canonical browser origin.", validator="origin", restart_required=True, startup_only=True),
    _spec("ALLOWED_HOSTS", "string", "", "Access / Security", "Host / X-Forwarded-Host allowlist.", validator="host_or_origin_list", restart_required=True, startup_only=True),
    _spec("CSRF_ORIGIN_CHECK_ENABLED", "bool", "true", "Access / Security", "Reject unsafe requests without valid Origin, Referer, or same-origin fetch metadata.", restart_required=True),
    _spec("UPSTREAM_HOST_ALLOWLIST", "string", "", "Access / Security", "Upstream API host allowlist.", validator="host_list", restart_required=True, startup_only=True),
    _spec("UPSTREAM_PROXY_HOST_ALLOWLIST", "string", "", "Access / Security", "SOCKS5 proxy host allowlist.", validator="host_list", restart_required=True, startup_only=True),
    _spec("WEBHOOK_HOST_ALLOWLIST", "string", "", "Webhooks", "Webhook callback host allowlist.", validator="host_list", restart_required=True, startup_only=True),
    _spec("ALLOW_PLAINTEXT_SECRETS", "bool", "false", "Secret Persistence", "Deprecated; web-managed literal secrets are disabled.", restart_required=True, startup_only=True),
    _spec("WEBHOOK_SIGNING_SECRET", "secret", "", "Webhooks", "Webhook signing secret.", secret=True, restart_required=True, startup_only=True),
    _spec("WEBHOOK_TIMEOUT_SECONDS", "float", "5", "Webhooks", "Webhook timeout per attempt.", min_value=0.1),
    _spec("WEBHOOK_MAX_ATTEMPTS", "int", "3", "Webhooks", "Webhook delivery attempts.", min_value=1),
    _spec("WEBHOOK_MAX_CONCURRENCY", "int", "4", "Webhooks", "Concurrent webhook deliveries per worker process.", min_value=1, restart_required=True),
    _spec("WEBHOOK_QUEUE_MAX_SIZE", "int", "100", "Webhooks", "Queued webhook deliveries per worker process before new deliveries are dropped.", min_value=1, restart_required=True),
    _spec("MAX_FILE_SIZE_MB", "int", "50", "Limits", "Max uploaded/downloaded image size.", min_value=1),
    _spec("EDIT_UPLOAD_MAX_MB", "int", "200", "Limits", "Max total multipart request size for an edit upload.", min_value=1, restart_required=True),
    _spec("UPLOAD_INFLIGHT_MAX_MB", "int", "512", "Limits", "Global SQLite-reserved in-flight upload bytes.", min_value=1, restart_required=True),
    _spec("UPLOAD_INFLIGHT_PER_IP_MAX_MB", "int", "256", "Limits", "Per-client-IP SQLite-reserved in-flight upload bytes.", min_value=1, restart_required=True),
    _spec("UPLOAD_RESERVATION_TTL_SECONDS", "int", "1800", "Limits", "Expired in-flight upload reservation cleanup TTL.", min_value=60, restart_required=True),
    _spec("NODEIMAGE_UPLOAD_CONCURRENCY", "int", "4", "NodeImage", "Concurrent NodeImage uploads per worker process.", min_value=1),
    _spec("MAX_JSON_BODY_MB", "int", "1", "Limits", "Max JSON request body size.", min_value=1),
    _spec("MAX_UPSTREAM_JSON_MB", "int", "128", "Limits", "Max upstream JSON/SSE response size.", min_value=1),
    _spec("MAX_IMAGE_PIXELS", "int", "100000000", "Limits", "Max decoded image pixels.", min_value=1),
    _spec("IMPORT_ARCHIVE_MAX_MB", "int", "256", "Limits", "Max uploaded import ZIP size.", min_value=1),
    _spec("IMPORT_MAX_FILES", "int", "500", "Limits", "Max files inside one import archive.", min_value=1),
    _spec("IMPORT_MAX_UNCOMPRESSED_MB", "int", "1024", "Limits", "Max uncompressed import archive size.", min_value=1),
    _spec("IMPORT_MAX_METADATA_BYTES", "int", "2097152", "Limits", "Max import metadata.json bytes.", min_value=1),
    _spec("IMPORT_MAX_ENTRIES", "int", "500", "Limits", "Max metadata image records in one import.", min_value=1),
    _spec("IMPORT_MAX_OUTPUT_MB", "int", "1024", "Limits", "Max total image bytes written by one import.", min_value=1),
    _spec("IMPORT_MAX_COMPRESSION_RATIO", "float", "100", "Limits", "Max import compression ratio.", min_value=1),
    _spec("MAX_ACTIVE_GENERATE_JOBS", "int", "2", "Job Queue / SSE", "Concurrent generation/edit jobs.", restart_required=True, min_value=1),
    _spec("MAX_QUEUED_GENERATE_JOBS", "int", "20", "Job Queue / SSE", "Additional queued jobs before 429.", restart_required=True, min_value=0),
    _spec("MAX_PENDING_EDIT_SOURCE_MB", "int", "200", "Job Queue / SSE", "SQLite global pending edit source byte cap.", min_value=0),
    _spec("MAX_SSE_SUBSCRIBERS_GLOBAL", "int", "200", "Job Queue / SSE", "Global active SSE subscriber cap across Granian workers via SQLite slot leases.", min_value=1),
    _spec("MAX_SSE_SUBSCRIBERS_PER_IP", "int", "10", "Job Queue / SSE", "Per-IP active SSE subscriber cap.", min_value=1),
    _spec("SSE_CONNECTION_TTL_SECONDS", "int", "3600", "Job Queue / SSE", "Maximum SSE connection lifetime.", min_value=60),
    _spec("IMAGES_DIR", "string", "./images", "Runtime Paths", "Generated image directory.", restart_required=True),
    _spec("THUMBNAILS_DIR", "string", "./images/thumbs", "Runtime Paths", "Thumbnail directory.", restart_required=True),
    _spec("THUMBNAIL_MAX_SIDE", "int", "512", "Runtime Paths", "Max thumbnail side.", restart_required=True, min_value=1),
    _spec("THUMBNAIL_CPU_CONCURRENCY", "int", "1", "Runtime Paths", "Global SQLite-limited thumbnail generation concurrency across workers.", min_value=1),
    _spec("DATA_DIR", "string", "./data", "Runtime Paths", "Runtime data directory.", restart_required=True),
    _spec("DATABASE_FILE", "string", "./data/app.sqlite3", "Runtime Paths", "SQLite database path.", restart_required=True),
    _spec("PYTHON_BASE_IMAGE", "string", "python:3.11-slim", "Docker Build", "Python base image for Docker builds.", build_only=True),
    _spec("NODE_BASE_IMAGE", "string", "node:24-alpine", "Docker Build", "Node base image for Docker builds.", build_only=True),
)

OVERALL_CONFIG_BY_NAME = {spec.name: spec for spec in OVERALL_CONFIG_REGISTRY}
STARTUP_RUNTIME_PATH_CONFIG_NAMES = frozenset(
    {
        "DATA_DIR",
        "DATABASE_FILE",
        "IMAGES_DIR",
        "THUMBNAILS_DIR",
        "THUMBNAIL_MAX_SIDE",
    }
)


def current_env_snapshot() -> dict[str, tuple[str, bool]]:
    return {
        spec.name: (
            "" if spec.secret else os.getenv(spec.name, ""),
            os.getenv(spec.name) is not None,
        )
        for spec in OVERALL_CONFIG_REGISTRY
    }


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("must be a boolean: true/false, 1/0, yes/no, on/off")


def coerce_value(spec: OverallConfigSpec, value: str) -> str:
    raw = str(value if value is not None else "").strip()
    if spec.type in {"string", "secret"}:
        normalized = raw
    elif spec.type == "bool":
        normalized = "true" if parse_bool(raw) else "false"
    elif spec.type == "int":
        try:
            parsed_int = int(raw)
        except ValueError as e:
            raise ValueError("must be an integer") from e
        if spec.min_value is not None and parsed_int < spec.min_value:
            raise ValueError(f"must be >= {int(spec.min_value)}")
        normalized = str(parsed_int)
    elif spec.type == "float":
        try:
            parsed_float = float(raw)
        except ValueError as e:
            raise ValueError("must be a number") from e
        if spec.min_value is not None and parsed_float < spec.min_value:
            raise ValueError(f"must be >= {spec.min_value:g}")
        normalized = f"{parsed_float:g}"
    else:
        raise ValueError("unsupported config type")

    validate_named_value(spec, normalized)
    return normalized


def validate_named_value(spec: OverallConfigSpec, value: str) -> None:
    if spec.validator == "github_repo" and value:
        if not re.match(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$", value):
            raise ValueError("must be formatted as owner/repo")
    elif spec.validator == "ip_list":
        _validate_ip_list(value)
    elif spec.validator == "host_list":
        _validate_host_list(value)
    elif spec.validator == "host_or_origin_list":
        _validate_host_or_origin_list(value)
    elif spec.validator == "origin" and value:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("must be an http(s) origin")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("must not include path, query, or fragment")


def _split_list(value: str) -> list[str]:
    return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]


def _validate_ip_list(value: str) -> None:
    import ipaddress

    for entry in _split_list(value):
        try:
            if "/" in entry:
                ipaddress.ip_network(entry, strict=False)
            else:
                ipaddress.ip_address(entry)
        except ValueError as e:
            raise ValueError(f"invalid IP/CIDR entry: {entry}") from e


def _validate_host_list(value: str) -> None:
    for entry in _split_list(value):
        if "://" in entry or "/" in entry or "@" in entry or any(ch.isspace() for ch in entry):
            raise ValueError(f"invalid hostname entry: {entry}")


def _validate_host_or_origin_list(value: str) -> None:
    from urllib.parse import urlsplit

    for entry in _split_list(value):
        if "://" in entry:
            parsed = urlsplit(entry)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError(f"invalid origin entry: {entry}")
            if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise ValueError(f"origin must not include path/query/fragment: {entry}")
        elif "/" in entry or "@" in entry or any(ch.isspace() for ch in entry):
            raise ValueError(f"invalid host entry: {entry}")


def effective_value(spec: OverallConfigSpec, row: dict[str, Any] | None) -> tuple[str, OverallConfigSource]:
    if not spec.startup_only and row and row.get("override_value") is not None:
        return str(row.get("override_value") or ""), "override"
    if row and row.get("is_env_set"):
        if spec.secret:
            return os.getenv(spec.name, ""), "env"
        return str(row.get("env_value") or ""), "env"
    return spec.default, "default"


def typed_value(spec: OverallConfigSpec, value: str) -> Any:
    normalized = coerce_value(spec, value)
    if spec.type == "bool":
        return parse_bool(normalized)
    if spec.type == "int":
        return int(normalized)
    if spec.type == "float":
        return float(normalized)
    return normalized


def apply_rows_to_config(
    rows: dict[str, dict[str, Any]],
    *,
    include_restart_required: bool = False,
    overrides_only: bool = False,
) -> None:
    secret_cache_dirty = False
    for spec in OVERALL_CONFIG_REGISTRY:
        if spec.build_only:
            continue
        if spec.name in STARTUP_RUNTIME_PATH_CONFIG_NAMES:
            continue
        if spec.exposed_in_settings:
            continue
        if spec.restart_required and not include_restart_required:
            continue
        row = rows.get(spec.name)
        if overrides_only and (not row or row.get("override_value") is None):
            continue
        value, _source = effective_value(spec, row)
        setattr(config, spec.name, typed_value(spec, value))
        if spec.secret or spec.name in {"ACCESS_KEY", "WEBHOOK_SIGNING_SECRET"}:
            secret_cache_dirty = True

    if secret_cache_dirty:
        try:
            from .secrets import invalidate_active_secret_values_cache

            invalidate_active_secret_values_cache()
        except Exception:
            pass

    try:
        import backend.app.core.security as security

        security._trusted_proxy_networks = None
    except Exception:
        pass


def validate_effective_security(rows: dict[str, dict[str, Any]]) -> None:
    def get_bool(name: str) -> bool:
        spec = OVERALL_CONFIG_BY_NAME[name]
        value, _ = effective_value(spec, rows.get(name))
        return parse_bool(coerce_value(spec, value))

    def get_string(name: str) -> str:
        spec = OVERALL_CONFIG_BY_NAME[name]
        value, _ = effective_value(spec, rows.get(name))
        return coerce_value(spec, value)

    if get_bool("TRUST_PROXY_HEADERS") and not _split_list(get_string("TRUSTED_PROXY_IPS")):
        raise ValueError("TRUST_PROXY_HEADERS=true requires TRUSTED_PROXY_IPS")
    if not get_bool("ALLOW_UNAUTHENTICATED") and not get_string("ACCESS_KEY"):
        raise ValueError("ACCESS_KEY is required when ALLOW_UNAUTHENTICATED=false")


def normalize_secret_override(spec: OverallConfigSpec, value: str) -> str:
    if spec.startup_only:
        raise ValueError(f"{spec.name} can only be configured at process startup")
    normalized = coerce_value(spec, value)
    if not spec.secret or not normalized:
        return normalized
    raise ValueError(f"{spec.name} can only be configured at process startup")
