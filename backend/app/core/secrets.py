import hmac
import json
import os
import re
from dataclasses import dataclass
from threading import RLock
from typing import Literal
from urllib.parse import urlsplit


SecretPurpose = Literal[
    "upstream_api",
    "prompt_optimizer",
    "upstream_proxy",
    "webhook_url",
    "r2_access_key_id",
    "r2_secret_access_key",
    "nodeimage_api_key",
]

ALLOWED_SECRET_PURPOSES: frozenset[str] = frozenset(
    {
        "upstream_api",
        "prompt_optimizer",
        "upstream_proxy",
        "webhook_url",
        "r2_access_key_id",
        "r2_secret_access_key",
        "nodeimage_api_key",
    }
)
SECRET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SecretRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class SecretEntry:
    secret_id: str
    purpose: str
    origin: str
    env_name: str | None = None
    value: str | None = None

    def resolve(self) -> str:
        if self.env_name:
            return os.getenv(self.env_name, "").strip()
        return str(self.value or "").strip()


_registry: dict[str, SecretEntry] = {}
_registry_lock = RLock()
_registry_generation = 0
_ACTIVE_SECRET_CONFIG_NAMES = (
    "ACCESS_KEY",
    "ADMIN_KEY",
    "CDN_SIGNING_SECRET",
    "WEBHOOK_SIGNING_SECRET",
    "DEFAULT_API_KEY",
    "PROMPT_OPTIMIZER_API_KEY",
    "DEFAULT_UPSTREAM_SOCKS5_PROXY",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "NODEIMAGE_API_KEY",
)
_active_secret_values_cache: tuple[
    int,
    tuple[str, ...],
    tuple[str, ...],
] | None = None


def invalidate_active_secret_values_cache() -> None:
    global _active_secret_values_cache
    with _registry_lock:
        _active_secret_values_cache = None


def canonical_origin(url: str | None) -> str:
    value = str(url or "").strip()
    if not value:
        raise SecretRegistryError("target URL is required")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise SecretRegistryError("target URL has an invalid port") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"https", "http", "socks5"}:
        raise SecretRegistryError("target URL has an unsupported scheme")
    if not parsed.hostname:
        raise SecretRegistryError("target URL has no hostname")
    if port is None:
        if scheme == "https":
            port = 443
        elif scheme == "http":
            port = 80
        else:
            raise SecretRegistryError("SOCKS5 target URL must declare a port")
    hostname = parsed.hostname.lower().rstrip(".")
    host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{scheme}://{host}:{port}"


def same_origin(left: str | None, right: str | None) -> bool:
    try:
        return canonical_origin(left) == canonical_origin(right)
    except SecretRegistryError:
        return False


def _validate_entry(secret_id: str, raw: object) -> SecretEntry:
    if not SECRET_ID_RE.fullmatch(secret_id):
        raise SecretRegistryError(
            "secret_id must be 3-128 ASCII letters, digits, dots, underscores, or hyphens"
        )
    if not isinstance(raw, dict):
        raise SecretRegistryError(f"Secret registry entry '{secret_id}' must be an object")
    purpose = str(raw.get("purpose") or "").strip()
    if purpose not in ALLOWED_SECRET_PURPOSES:
        raise SecretRegistryError(f"Secret registry entry '{secret_id}' has an invalid purpose")
    origin = canonical_origin(str(raw.get("origin") or ""))
    env_name = str(raw.get("env") or "").strip()
    if not ENV_NAME_RE.fullmatch(env_name):
        raise SecretRegistryError(f"Secret registry entry '{secret_id}' has an invalid env name")
    return SecretEntry(
        secret_id=secret_id,
        purpose=purpose,
        origin=origin,
        env_name=env_name,
    )


def _builtin_entries() -> dict[str, SecretEntry]:
    # Direct startup secrets never pass through the web settings API. Stable IDs
    # let defaults use the same purpose/origin checks as operator-declared entries.
    from . import settings as config

    candidates = (
        (
            "builtin-default-api-key",
            "upstream_api",
            config.DEFAULT_API_URL,
            config.DEFAULT_API_KEY,
        ),
        (
            "builtin-prompt-optimizer-key",
            "prompt_optimizer",
            config.PROMPT_OPTIMIZER_API_URL,
            config.PROMPT_OPTIMIZER_API_KEY,
        ),
        (
            "builtin-upstream-proxy",
            "upstream_proxy",
            config.DEFAULT_UPSTREAM_SOCKS5_PROXY,
            config.DEFAULT_UPSTREAM_SOCKS5_PROXY,
        ),
        (
            "builtin-r2-access-key-id",
            "r2_access_key_id",
            config.R2_ENDPOINT_URL,
            config.R2_ACCESS_KEY_ID,
        ),
        (
            "builtin-r2-secret-access-key",
            "r2_secret_access_key",
            config.R2_ENDPOINT_URL,
            config.R2_SECRET_ACCESS_KEY,
        ),
        (
            "builtin-nodeimage-api-key",
            "nodeimage_api_key",
            "https://api.nodeimage.com",
            config.NODEIMAGE_API_KEY,
        ),
    )
    from .validators import get_env_var_ref_name

    entries: dict[str, SecretEntry] = {}
    for secret_id, purpose, target_url, value in candidates:
        normalized_value = str(value or "").strip()
        if not normalized_value or not str(target_url or "").strip():
            continue
        # A startup value may be a literal or a well-formed ${ENV_VAR} reference.
        # Both are operator-declared at process start, so both get a builtin
        # entry bound to the startup target origin. Malformed placeholders are
        # never resolved.
        env_name = get_env_var_ref_name(normalized_value)
        if not env_name and ("${" in normalized_value or "}" in normalized_value):
            continue
        try:
            origin = canonical_origin(target_url)
        except SecretRegistryError:
            continue
        entries[secret_id] = SecretEntry(
            secret_id=secret_id,
            purpose=purpose,
            origin=origin,
            env_name=env_name,
            value=None if env_name else normalized_value,
        )
    return entries


def configure_registry(raw_json: str | None = None) -> None:
    raw = str(raw_json if raw_json is not None else os.getenv("SECRET_REGISTRY_JSON", "")).strip()
    declared: dict[str, SecretEntry] = {}
    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SecretRegistryError("SECRET_REGISTRY_JSON must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise SecretRegistryError("SECRET_REGISTRY_JSON must be a JSON object")
        for raw_id, raw_entry in payload.items():
            secret_id = str(raw_id).strip()
            declared[secret_id] = _validate_entry(secret_id, raw_entry)

    entries = _builtin_entries()
    overlap = entries.keys() & declared.keys()
    if overlap:
        raise SecretRegistryError(f"Reserved secret_id cannot be overridden: {sorted(overlap)[0]}")
    entries.update(declared)
    with _registry_lock:
        global _registry, _registry_generation, _active_secret_values_cache
        _registry = entries
        _registry_generation += 1
        _active_secret_values_cache = None


def configured_secret_ids() -> tuple[str, ...]:
    with _registry_lock:
        return tuple(sorted(_registry))


def normalize_secret_id(value: str | None, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if "${" in normalized or "}" in normalized:
        raise SecretRegistryError(f"{field_name} must use a predeclared secret_id")
    if not SECRET_ID_RE.fullmatch(normalized):
        raise SecretRegistryError(f"{field_name} must be a valid predeclared secret_id")
    with _registry_lock:
        if normalized not in _registry:
            raise SecretRegistryError(f"{field_name} references an unknown secret_id")
    return normalized


def secret_entry(secret_id: str) -> SecretEntry:
    with _registry_lock:
        entry = _registry.get(str(secret_id or "").strip())
    if entry is None:
        raise SecretRegistryError("unknown secret_id")
    return entry


def validate_secret_binding(
    secret_id: str,
    *,
    purpose: SecretPurpose,
    target_url: str,
    host_allowlist: str,
) -> SecretEntry:
    entry = secret_entry(secret_id)
    if entry.purpose != purpose:
        raise SecretRegistryError("secret_id is not permitted for this purpose")
    if entry.origin != canonical_origin(target_url):
        raise SecretRegistryError("secret_id is not bound to the target origin")

    allowed_hosts = {
        item.strip().lower().rstrip(".")
        for item in str(host_allowlist or "").replace(";", ",").split(",")
        if item.strip()
    }
    if not allowed_hosts:
        raise SecretRegistryError("credentials require a non-empty startup host allowlist")
    target_host = (urlsplit(target_url).hostname or "").lower().rstrip(".")
    if target_host not in allowed_hosts:
        raise SecretRegistryError("credential target is not in the startup host allowlist")
    return entry


def resolve_secret(
    secret_id: str,
    *,
    purpose: SecretPurpose,
    target_url: str,
    host_allowlist: str,
) -> str:
    entry = validate_secret_binding(
        secret_id,
        purpose=purpose,
        target_url=target_url,
        host_allowlist=host_allowlist,
    )
    value = entry.resolve()
    if not value:
        raise SecretRegistryError("secret_id resolves to an empty value")
    return value


def match_secret_id_for_value(
    value: object,
    *,
    purpose: SecretPurpose,
    target_url: str,
    host_allowlist: str,
) -> str:
    """Return the deterministic registry ID for an exact legacy value match."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    raw_bytes = raw.encode("utf-8")
    matches: list[str] = []
    with _registry_lock:
        entries = tuple(sorted(_registry.values(), key=lambda item: item.secret_id))
    for entry in entries:
        try:
            validate_secret_binding(
                entry.secret_id,
                purpose=purpose,
                target_url=target_url,
                host_allowlist=host_allowlist,
            )
        except SecretRegistryError:
            continue
        candidate = entry.resolve().encode("utf-8")
        if hmac.compare_digest(candidate, raw_bytes):
            matches.append(entry.secret_id)
    return matches[0] if matches else ""


def _entries_for_env_name(env_name: str, purpose: str) -> list[SecretEntry]:
    with _registry_lock:
        return [
            entry
            for entry in _registry.values()
            if entry.env_name == env_name and entry.purpose == purpose
        ]


def resolve_env_reference(
    env_name: str,
    *,
    purpose: SecretPurpose,
    target_url: str,
    host_allowlist: str,
    field_name: str,
) -> str:
    """Resolve a legacy ``${ENV_VAR}`` reference through the registry only.

    The environment variable is read only when a registry entry declares it for
    this purpose and is bound to the target origin. Without that binding an
    ``${ENV_VAR}`` reference could name any process secret (``ACCESS_KEY``,
    cloud credentials, ...) and ship it to an arbitrary destination.
    """

    for entry in _entries_for_env_name(env_name, purpose):
        try:
            validate_secret_binding(
                entry.secret_id,
                purpose=purpose,
                target_url=target_url,
                host_allowlist=host_allowlist,
            )
        except SecretRegistryError:
            continue
        resolved = entry.resolve()
        if not resolved:
            raise SecretRegistryError(
                f"{field_name} environment variable {env_name} is not set or empty."
            )
        return resolved

    raise SecretRegistryError(
        f"{field_name} references ${{{env_name}}}, which no Secret Registry entry "
        "declares for this purpose and target origin. Declare it in "
        "SECRET_REGISTRY_JSON and reference it by secret_id."
    )


def resolve_secret_reference(
    value: object,
    *,
    purpose: SecretPurpose,
    target_url: str,
    host_allowlist: str,
    field_name: str,
) -> str:
    """Resolve a settings secret reference without exposing its source details.

    Settings may contain an empty value, a predeclared registry ID, or a legacy
    ``${ENV_VAR}`` reference. Registry IDs and environment references are always
    validated for purpose, origin, and the startup host allowlist. Any literal
    found after startup migration is rejected and can never become outbound data.
    """

    raw = str(value or "").strip()
    if not raw:
        return ""

    if raw in configured_secret_ids():
        try:
            return resolve_secret(
                raw,
                purpose=purpose,
                target_url=target_url,
                host_allowlist=host_allowlist,
            )
        except SecretRegistryError as exc:
            raise SecretRegistryError(f"{field_name}: {exc}") from exc

    # Keep the parser local to avoid making the validators module import the
    # secrets registry during application startup.
    from .validators import get_env_var_ref_name

    env_var = get_env_var_ref_name(raw)
    if env_var:
        return resolve_env_reference(
            env_var,
            purpose=purpose,
            target_url=target_url,
            host_allowlist=host_allowlist,
            field_name=field_name,
        )

    if "${" in raw or "}" in raw:
        raise SecretRegistryError(
            f"{field_name} env ref must be formatted as ${{ENV_VAR_NAME}}."
        )

    raise SecretRegistryError(
        f"{field_name} must reference a secret_id declared in SECRET_REGISTRY_JSON"
    )


def resolve_url_secret_reference(
    value: object,
    *,
    purpose: SecretPurpose,
    host_allowlist: str,
    field_name: str,
) -> str:
    """Resolve a secret whose value is itself the target URL (proxy, webhook)."""

    raw = str(value or "").strip()
    if not raw:
        return ""

    if raw in configured_secret_ids():
        entry = secret_entry(raw)
        return resolve_secret(
            raw,
            purpose=purpose,
            target_url=entry.resolve(),
            host_allowlist=host_allowlist,
        )

    from .validators import get_env_var_ref_name

    env_var = get_env_var_ref_name(raw)
    if env_var:
        for entry in _entries_for_env_name(env_var, purpose):
            resolved = entry.resolve()
            if not resolved:
                continue
            try:
                validate_secret_binding(
                    entry.secret_id,
                    purpose=purpose,
                    target_url=resolved,
                    host_allowlist=host_allowlist,
                )
            except SecretRegistryError:
                continue
            return resolved
        raise SecretRegistryError(
            f"{field_name} references ${{{env_var}}}, which no Secret Registry entry "
            "declares for this purpose. Declare it in SECRET_REGISTRY_JSON and "
            "reference it by secret_id."
        )

    if "${" in raw or "}" in raw:
        raise SecretRegistryError(
            f"{field_name} env ref must be formatted as ${{ENV_VAR_NAME}}."
        )

    raise SecretRegistryError(
        f"{field_name} must reference a secret_id declared in SECRET_REGISTRY_JSON"
    )


def active_secret_values() -> tuple[str, ...]:
    global _active_secret_values_cache

    from . import settings as config

    config_key = tuple(
        str(getattr(config, name, "") or "").strip()
        for name in _ACTIVE_SECRET_CONFIG_NAMES
    )
    with _registry_lock:
        cache = _active_secret_values_cache
        if cache and cache[0] == _registry_generation and cache[1] == config_key:
            return cache[2]
        generation = _registry_generation
        entries = tuple(_registry.values())

    values: set[str] = set()
    for entry in entries:
        value = entry.resolve()
        if value:
            values.add(value)

    for normalized in config_key:
        if normalized:
            values.add(normalized)
    result = tuple(sorted(values, key=len, reverse=True))
    with _registry_lock:
        if _registry_generation == generation:
            _active_secret_values_cache = (generation, config_key, result)
    return result
