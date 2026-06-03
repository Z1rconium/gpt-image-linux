import os

from fastapi import HTTPException

from .app_state import app
from ..core import settings as config
from ..core.api_paths import (
    normalize_api_path,
    normalize_api_preset,
    normalize_default_model,
    normalize_default_response_format,
)
from ..core.validators import (
    get_env_var_ref_name,
    is_malformed_env_var_ref,
    mask_socks5_proxy_url,
    mask_webhook_url,
    normalize_secret_env_ref_or_plaintext,
    normalize_socks5_proxy_url,
    normalize_webhook_url,
    resolve_env_var_ref,
)
from ..repositories import storage
from ..schemas.models import (
    ApiPresetResponse,
    PromptOptimizerSettingsResponse,
    R2BackupSettingsResponse,
    SettingsResponse,
)

MASKED_API_KEY_VALUE = "********"


def get_exception_message(error: Exception) -> str:
    return str(error) or repr(error) or error.__class__.__name__


def mask_key(key: str) -> str:
    if not key or len(key) <= 8:
        return "***"
    return key[:4] + "***" + key[-4:]


def get_api_key_env_var(api_key: str) -> str | None:
    return get_env_var_ref_name(api_key)


def is_malformed_api_key_env_ref(api_key: str) -> bool:
    return is_malformed_env_var_ref(api_key)


def resolve_api_key(api_key: str) -> str:
    return resolve_env_var_ref(api_key)


def api_key_response_fields(api_key: str) -> dict:
    value = str(api_key or "").strip()
    env_var = get_api_key_env_var(value)
    if env_var:
        return {
            "api_key_masked": f"${{{env_var}}}",
            "has_api_key": bool(value),
            "api_key_source": "env",
            "api_key_env_var": env_var,
        }
    if value:
        return {
            "api_key_masked": mask_key(value),
            "has_api_key": True,
            "api_key_source": "stored",
            "api_key_env_var": None,
        }
    return {
        "api_key_masked": "***",
        "has_api_key": False,
        "api_key_source": "empty",
        "api_key_env_var": None,
    }


def secret_response_fields(value: str, prefix: str) -> dict:
    fields = api_key_response_fields(value)
    return {
        f"{prefix}_masked": fields["api_key_masked"],
        f"has_{prefix}": fields["has_api_key"],
        f"{prefix}_source": fields["api_key_source"],
        f"{prefix}_env_var": fields["api_key_env_var"],
    }


def get_effective_preset_api_key(preset: dict) -> str:
    api_key = str(preset.get("api_key") or "").strip()
    env_var = get_api_key_env_var(api_key)
    if is_malformed_api_key_env_ref(api_key):
        raise HTTPException(
            status_code=400,
            detail="API Key env ref must be formatted as ${ENV_VAR_NAME}.",
        )

    resolved_key = resolve_api_key(api_key)
    if resolved_key:
        return resolved_key

    if env_var:
        raise HTTPException(
            status_code=400,
            detail=(
                f"API Key environment variable {env_var} is not set or empty. "
                "Set it in the server environment."
            ),
        )
    raise HTTPException(
        status_code=400,
        detail="API Key not configured. Please set it in Settings.",
    )


def _default_prompt_optimizer_settings() -> dict:
    return {
        "enabled": config.PROMPT_OPTIMIZER_ENABLED,
        "api_url": config.PROMPT_OPTIMIZER_API_URL,
        "api_key": config.PROMPT_OPTIMIZER_API_KEY,
        "model": config.PROMPT_OPTIMIZER_MODEL,
        "timeout_seconds": config.PROMPT_OPTIMIZER_TIMEOUT_SECONDS,
    }


def _coerce_positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _coerce_non_negative_int(value, default: int = 0) -> int:
    if isinstance(value, float) and not value.is_integer():
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def normalize_prompt_optimizer_settings(raw: dict | None) -> dict:
    defaults = _default_prompt_optimizer_settings()
    if not isinstance(raw, dict):
        return defaults
    return {
        "enabled": bool(raw.get("enabled", defaults["enabled"])),
        "api_url": str(raw.get("api_url") or "").strip(),
        "api_key": str(raw.get("api_key") or "").strip(),
        "model": str(raw.get("model") or defaults["model"]).strip()
        or defaults["model"],
        "timeout_seconds": _coerce_positive_int(
            raw.get("timeout_seconds"),
            defaults["timeout_seconds"],
        ),
    }


def persist_api_settings():
    storage.save_settings(
        {
            "active_preset_id": getattr(app.state, "active_preset_id", "default"),
            "upstream_socks5_proxy": get_upstream_socks5_proxy(raw=True),
            "webhook_url": get_webhook_url(raw=True),
            "presets": get_api_presets(),
            "prompt_optimizer": get_prompt_optimizer_settings(),
            "r2_backup": get_r2_backup_settings(),
        }
    )


def load_api_settings():
    data = storage.load_settings()
    presets = data["presets"]
    app.state.api_presets = presets
    app.state.active_preset_id = data["active_preset_id"]
    apply_upstream_socks5_proxy(data.get("upstream_socks5_proxy"))
    apply_webhook_url(data.get("webhook_url"))
    apply_api_preset(get_active_preset())


def get_api_presets() -> list[dict]:
    presets = getattr(app.state, "api_presets", None)
    if presets:
        normalized = [
            normalize_api_preset(preset, str(preset.get("id") or f"preset-{index + 1}"))
            for index, preset in enumerate(presets)
        ]
        app.state.api_presets = normalized
        return normalized

    preset = normalize_api_preset(
        {
            "id": "default",
            "name": "Default",
            "api_url": getattr(app.state, "api_url", config.DEFAULT_API_URL),
            "api_key": getattr(app.state, "api_key", config.DEFAULT_API_KEY),
            "api_path": getattr(app.state, "api_path", config.DEFAULT_API_PATH),
            "default_model": getattr(app.state, "default_model", ""),
            "default_response_format": getattr(
                app.state,
                "default_response_format",
                "url",
            ),
        }
    )
    app.state.api_presets = [preset]
    app.state.active_preset_id = preset["id"]
    return app.state.api_presets


def get_active_preset() -> dict:
    presets = get_api_presets()
    active_id = getattr(app.state, "active_preset_id", presets[0]["id"])
    for preset in presets:
        if preset["id"] == active_id:
            return preset

    app.state.active_preset_id = presets[0]["id"]
    return presets[0]


def get_preset_by_id(preset_id: str) -> dict | None:
    for preset in get_api_presets():
        if preset["id"] == preset_id:
            return preset
    return None


def apply_api_preset(preset: dict):
    app.state.api_url = preset.get("api_url", "").rstrip("/")
    app.state.api_key = preset.get("api_key", "")
    app.state.api_path = normalize_api_path(
        preset.get("api_path", "/v1/images/generations")
    )
    app.state.default_model = normalize_default_model(
        preset.get("default_model"),
        app.state.api_path,
    )
    app.state.default_response_format = normalize_default_response_format(
        preset.get("default_response_format")
    )
    app.state.active_preset_id = preset["id"]


def get_upstream_socks5_proxy(*, raw: bool = False) -> str:
    value = str(getattr(app.state, "upstream_socks5_proxy", "") or "").strip()
    if raw:
        return value
    resolved = resolve_env_var_ref(value)
    return normalize_socks5_proxy_url(resolved) if resolved else ""


def apply_upstream_socks5_proxy(value: str | None):
    app.state.upstream_socks5_proxy = normalize_secret_env_ref_or_plaintext(
        value,
        field_name="SOCKS5 proxy URL",
        normalizer=normalize_socks5_proxy_url,
    )


def get_webhook_url(*, raw: bool = False) -> str:
    value = str(getattr(app.state, "webhook_url", "") or "").strip()
    if raw:
        return value
    resolved = resolve_env_var_ref(value)
    return normalize_webhook_url(resolved) if resolved else ""


def apply_webhook_url(value: str | None):
    app.state.webhook_url = normalize_secret_env_ref_or_plaintext(
        value,
        field_name="Webhook URL",
        normalizer=normalize_webhook_url,
    )


def upstream_socks5_proxy_response_fields() -> dict:
    value = get_upstream_socks5_proxy(raw=True)
    return {
        "has_upstream_socks5_proxy": bool(value),
        "upstream_socks5_proxy_masked": mask_socks5_proxy_url(value),
    }


def webhook_url_response_fields() -> dict:
    value = get_webhook_url(raw=True)
    return {
        "has_webhook_url": bool(value),
        "webhook_url_masked": mask_webhook_url(value),
    }


def serialize_api_preset(preset: dict) -> ApiPresetResponse:
    key_fields = api_key_response_fields(preset.get("api_key", ""))
    return ApiPresetResponse(
        id=preset["id"],
        name=preset.get("name") or "Untitled preset",
        api_url=preset.get("api_url", ""),
        api_path=normalize_api_path(
            preset.get("api_path", "/v1/images/generations")
        ),
        default_model=normalize_default_model(
            preset.get("default_model"),
            preset.get("api_path", "/v1/images/generations"),
        ),
        default_response_format=normalize_default_response_format(
            preset.get("default_response_format")
        ),
        **key_fields,
    )


def build_settings_response() -> SettingsResponse:
    active_preset = get_active_preset()
    key_fields = api_key_response_fields(active_preset.get("api_key", ""))
    optimizer_raw = get_prompt_optimizer_settings()
    return SettingsResponse(
        active_preset_id=active_preset["id"],
        api_url=active_preset.get("api_url", ""),
        **key_fields,
        api_path=normalize_api_path(
            active_preset.get("api_path", "/v1/images/generations")
        ),
        default_model=normalize_default_model(
            active_preset.get("default_model"),
            active_preset.get("api_path", "/v1/images/generations"),
        ),
        default_response_format=normalize_default_response_format(
            active_preset.get("default_response_format")
        ),
        **upstream_socks5_proxy_response_fields(),
        **webhook_url_response_fields(),
        presets=[serialize_api_preset(preset) for preset in get_api_presets()],
        prompt_optimizer=build_prompt_optimizer_settings_response(optimizer_raw),
        r2_backup=build_r2_backup_settings_response(get_r2_backup_settings()),
    )


def build_prompt_optimizer_settings_response(raw: dict | None) -> PromptOptimizerSettingsResponse:
    raw = normalize_prompt_optimizer_settings(raw)
    key_fields = api_key_response_fields(raw.get("api_key", ""))
    return PromptOptimizerSettingsResponse(
        enabled=bool(raw.get("enabled", False)),
        api_url=str(raw.get("api_url", "")).strip(),
        model=str(raw.get("model") or config.PROMPT_OPTIMIZER_MODEL).strip()
        or config.PROMPT_OPTIMIZER_MODEL,
        timeout_seconds=_coerce_positive_int(
            raw.get("timeout_seconds"),
            config.PROMPT_OPTIMIZER_TIMEOUT_SECONDS,
        ),
        **key_fields,
    )


def build_r2_backup_settings_response(raw: dict | None) -> R2BackupSettingsResponse:
    settings = storage.load_r2_backup_settings() if raw is None else raw
    access_key_fields = secret_response_fields(
        str(settings.get("access_key_id") or ""),
        "access_key_id",
    )
    secret_key_fields = secret_response_fields(
        str(settings.get("secret_access_key") or ""),
        "secret_access_key",
    )
    return R2BackupSettingsResponse(
        enabled=bool(settings.get("enabled", False)),
        endpoint_url=str(settings.get("endpoint_url") or "").strip(),
        bucket_name=str(settings.get("bucket_name") or "").strip(),
        region=str(settings.get("region") or "auto").strip() or "auto",
        key_prefix=str(settings.get("key_prefix") or "").strip(),
        sync_interval_hours=_coerce_non_negative_int(
            settings.get("sync_interval_hours"),
            0,
        ),
        **access_key_fields,
        **secret_key_fields,
    )


def resolve_prompt_optimizer_api_key(raw: dict | None) -> str:
    settings = normalize_prompt_optimizer_settings(raw)
    api_key = str(settings.get("api_key") or "").strip()
    env_var = get_api_key_env_var(api_key)
    if is_malformed_api_key_env_ref(api_key):
        raise HTTPException(
            status_code=400,
            detail="Prompt optimizer API Key env ref must be formatted as ${ENV_VAR_NAME}.",
        )
    resolved_key = resolve_api_key(api_key)
    if resolved_key:
        return resolved_key
    if env_var:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Prompt optimizer API Key environment variable {env_var} is not set or empty. "
                "Set it in the server environment."
            ),
        )
    if not api_key:
        return ""
    return api_key


def get_prompt_optimizer_settings() -> dict:
    return storage.load_prompt_optimizer_settings()


def get_r2_backup_settings() -> dict:
    return storage.load_r2_backup_settings()


def apply_prompt_optimizer_settings(
    current: dict | None, req_optimizer: object
) -> dict:
    current = normalize_prompt_optimizer_settings(current)
    if req_optimizer is None:
        return current
    if hasattr(req_optimizer, "enabled") and req_optimizer.enabled is not None:
        current["enabled"] = req_optimizer.enabled
    if hasattr(req_optimizer, "api_url") and req_optimizer.api_url is not None:
        current["api_url"] = req_optimizer.api_url.strip()
    if hasattr(req_optimizer, "model") and req_optimizer.model is not None:
        current["model"] = req_optimizer.model.strip()
    if (
        hasattr(req_optimizer, "timeout_seconds")
        and req_optimizer.timeout_seconds is not None
    ):
        current["timeout_seconds"] = int(req_optimizer.timeout_seconds)
    if hasattr(req_optimizer, "api_key") and req_optimizer.api_key is not None:
        key = req_optimizer.api_key.strip()
        if key and key != MASKED_API_KEY_VALUE:
            current["api_key"] = key
        elif key == "":
            current["api_key"] = ""
    return current


def apply_r2_backup_settings(current: dict | None, req_r2: object) -> dict:
    current = storage.load_r2_backup_settings() if current is None else dict(current)
    if req_r2 is None:
        return current
    if hasattr(req_r2, "enabled") and req_r2.enabled is not None:
        current["enabled"] = bool(req_r2.enabled)
    if hasattr(req_r2, "endpoint_url") and req_r2.endpoint_url is not None:
        current["endpoint_url"] = req_r2.endpoint_url.strip()
    if hasattr(req_r2, "bucket_name") and req_r2.bucket_name is not None:
        current["bucket_name"] = req_r2.bucket_name.strip()
    if hasattr(req_r2, "region") and req_r2.region is not None:
        current["region"] = req_r2.region.strip() or "auto"
    if hasattr(req_r2, "key_prefix") and req_r2.key_prefix is not None:
        current["key_prefix"] = req_r2.key_prefix.strip()
    if hasattr(req_r2, "sync_interval_hours") and req_r2.sync_interval_hours is not None:
        current["sync_interval_hours"] = int(req_r2.sync_interval_hours)
    for field in ("access_key_id", "secret_access_key"):
        if not hasattr(req_r2, field):
            continue
        value = getattr(req_r2, field)
        if value is None:
            continue
        key = value.strip()
        if key and key != MASKED_API_KEY_VALUE:
            current[field] = key
        elif key == "":
            current[field] = ""
    return current
