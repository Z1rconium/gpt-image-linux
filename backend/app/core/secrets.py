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
]

ALLOWED_SECRET_PURPOSES: frozenset[str] = frozenset(
    {
        "upstream_api",
        "prompt_optimizer",
        "upstream_proxy",
        "webhook_url",
        "r2_access_key_id",
        "r2_secret_access_key",
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
    )
    entries: dict[str, SecretEntry] = {}
    for secret_id, purpose, target_url, value in candidates:
        normalized_value = str(value or "").strip()
        if not normalized_value or not str(target_url or "").strip():
            continue
        # Legacy placeholders are deliberately not resolved. Operators must
        # declare them in SECRET_REGISTRY_JSON under an opaque ID instead.
        if "${" in normalized_value or "}" in normalized_value:
            continue
        try:
            origin = canonical_origin(target_url)
        except SecretRegistryError:
            continue
        entries[secret_id] = SecretEntry(
            secret_id=secret_id,
            purpose=purpose,
            origin=origin,
            value=normalized_value,
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
        global _registry
        _registry = entries


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


def active_secret_values() -> tuple[str, ...]:
    values: set[str] = set()
    with _registry_lock:
        entries = tuple(_registry.values())
    for entry in entries:
        value = entry.resolve()
        if value:
            values.add(value)
    from . import settings as config

    for value in (config.ACCESS_KEY, config.WEBHOOK_SIGNING_SECRET):
        normalized = str(value or "").strip()
        if normalized:
            values.add(normalized)
    return tuple(sorted(values, key=len, reverse=True))
