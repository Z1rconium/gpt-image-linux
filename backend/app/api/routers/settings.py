import asyncio
import os
import uuid

from fastapi import APIRouter, Body, HTTPException

from ..app_state import app
from ..presets import (
    apply_api_preset,
    apply_ai_assistant_settings,
    apply_r2_backup_settings,
    apply_nodeimage_settings,
    apply_upstream_socks5_proxy,
    apply_webhook_url,
    build_settings_response,
    get_active_preset,
    get_api_key_env_var,
    get_api_presets,
    get_effective_preset_api_key,
    get_exception_message,
    get_nodeimage_settings,
    get_preset_by_id,
    get_upstream_socks5_proxy,
    get_webhook_url,
    is_malformed_api_key_env_ref,
    load_api_settings,
    mask_socks5_proxy_url,
    mask_webhook_url,
    persist_api_settings,
)
from ...core import settings as config
from ...core import secrets
from ...core import overall_config
from ...core import validators as ssrf
from ...core.api_paths import (
    ALLOWED_API_PATHS,
    build_upstream_url,
    normalize_api_path,
    normalize_default_model,
    normalize_default_response_format,
)
from ...integrations.upstream import generation as proxy
from ...integrations.r2.client import probe_r2_settings
from ...repositories.settings import (
    list_overall_config_values,
    save_ai_assistant_settings,
    save_overall_config_overrides,
    save_prompt_optimizer_settings,
    save_r2_backup_settings,
    save_nodeimage_settings,
)
from ...schemas.settings import (
    OverallConfigItem,
    OverallConfigResponse,
    OverallConfigUpdateRequest,
    CredentialProbeRequest,
    PresetCreateRequest,
    PresetHealthResponse,
    R2BackupSettingsRequest,
    R2HealthResponse,
    SettingsRequest,
    SettingsResponse,
)


router = APIRouter()


def _validate_preset_secret_binding(preset: dict) -> None:
    secret_id = str(preset.get("api_key") or "").strip()
    if not secret_id or secret_id not in secrets.configured_secret_ids():
        return
    try:
        secrets.validate_secret_binding(
            secret_id,
            purpose="upstream_api",
            target_url=str(preset.get("api_url") or ""),
            host_allowlist=config.UPSTREAM_HOST_ALLOWLIST,
        )
    except secrets.SecretRegistryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _validate_optimizer_secret_binding(settings: dict) -> None:
    secret_id = str(settings.get("api_key") or "").strip()
    if not secret_id or secret_id not in secrets.configured_secret_ids():
        return
    try:
        secrets.validate_secret_binding(
            secret_id,
            purpose="prompt_optimizer",
            target_url=str(settings.get("api_url") or ""),
            host_allowlist=config.PROMPT_OPTIMIZER_HOST_ALLOWLIST,
        )
    except secrets.SecretRegistryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _validate_r2_secret_bindings(settings: dict) -> None:
    endpoint_url = str(settings.get("endpoint_url") or "")
    for field, purpose in (
        ("access_key_id", "r2_access_key_id"),
        ("secret_access_key", "r2_secret_access_key"),
    ):
        secret_id = str(settings.get(field) or "").strip()
        if not secret_id or secret_id not in secrets.configured_secret_ids():
            continue
        try:
            secrets.validate_secret_binding(
                secret_id,
                purpose=purpose,
                target_url=endpoint_url,
                host_allowlist=config.R2_ENDPOINT_HOST_ALLOWLIST,
            )
        except secrets.SecretRegistryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


def _validate_nodeimage_secret_binding(settings: dict) -> None:
    secret_id = str(settings.get("api_key") or "").strip()
    if not secret_id or secret_id not in secrets.configured_secret_ids():
        return
    try:
        secrets.validate_secret_binding(
            secret_id,
            purpose="nodeimage_api_key",
            target_url="https://api.nodeimage.com",
            host_allowlist="api.nodeimage.com",
        )
    except secrets.SecretRegistryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _validate_webhook_security() -> None:
    webhook_url = get_webhook_url()
    if webhook_url and len(config.WEBHOOK_SIGNING_SECRET.encode("utf-8")) < 32:
        raise HTTPException(
            status_code=422,
            detail="Webhook signing secret must contain at least 32 bytes",
        )


def _mask_overall_config_value(value: str, *, secret: bool) -> str:
    if not secret:
        return value
    return overall_config.MASKED_OVERALL_SECRET_VALUE if value else ""


def _serialize_overall_config(
    rows: dict[str, dict],
    *,
    restart_required_names: list[str] | None = None,
) -> OverallConfigResponse:
    items: list[OverallConfigItem] = []
    for spec in overall_config.OVERALL_CONFIG_REGISTRY:
        if spec.exposed_in_settings or not spec.exposed_in_overall_config:
            continue
        row = rows.get(spec.name, {})
        raw_value, source = overall_config.effective_value(spec, row)
        typed = overall_config.typed_value(spec, raw_value)
        env_value = str(row.get("env_value") or "")
        override_value = row.get("override_value")
        items.append(
            OverallConfigItem(
                name=spec.name,
                type=spec.type,
                group=spec.group,
                description=spec.description,
                value=overall_config.MASKED_OVERALL_SECRET_VALUE if spec.secret and raw_value else typed,
                value_masked=_mask_overall_config_value(raw_value, secret=spec.secret),
                env_value_masked=_mask_overall_config_value(env_value, secret=spec.secret),
                override_value_masked=(
                    _mask_overall_config_value(str(override_value or ""), secret=spec.secret)
                    if override_value is not None
                    else None
                ),
                source=source,
                is_env_set=bool(row.get("is_env_set")),
                has_override=override_value is not None,
                secret=spec.secret,
                hot_reload=spec.hot_reload and not spec.restart_required and not spec.build_only,
                restart_required=spec.restart_required,
                build_only=spec.build_only,
                startup_only=spec.startup_only,
                updated_at=row.get("updated_at"),
                override_updated_at=row.get("override_updated_at"),
            )
        )
    return OverallConfigResponse(
        items=items,
        restart_required_names=restart_required_names or [],
    )


@router.get("/api/settings/overall-config", response_model=OverallConfigResponse)
async def get_overall_config():
    rows = await asyncio.to_thread(list_overall_config_values)
    return _serialize_overall_config(rows)


@router.put("/api/settings/overall-config", response_model=OverallConfigResponse)
async def update_overall_config(req: OverallConfigUpdateRequest):
    current_rows = await asyncio.to_thread(list_overall_config_values)
    updates: dict[str, str | None] = {}
    seen_names: set[str] = set()
    for item in req.updates:
        if item.name in seen_names:
            raise HTTPException(status_code=422, detail=f"Duplicate config name: {item.name}")
        seen_names.add(item.name)
        spec = overall_config.OVERALL_CONFIG_BY_NAME.get(item.name)
        if spec is None or spec.exposed_in_settings or not spec.exposed_in_overall_config:
            raise HTTPException(status_code=422, detail=f"Unknown config name: {item.name}")
        if spec.startup_only:
            raise HTTPException(
                status_code=422,
                detail=f"{item.name} can only be configured at process startup",
            )

        if item.clear_override:
            updates[item.name] = None
            continue

        value = item.value
        if spec.secret and value == overall_config.MASKED_OVERALL_SECRET_VALUE:
            continue
        if value is None:
            raise HTTPException(status_code=422, detail=f"value is required for {item.name}")
        try:
            updates[item.name] = overall_config.normalize_secret_override(spec, str(value))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"{item.name}: {e}") from e

    projected_rows = {
        name: dict(row)
        for name, row in current_rows.items()
    }
    for name, value in updates.items():
        row = projected_rows.setdefault(
            name,
            {
                "name": name,
                "env_value": "",
                "override_value": None,
                "is_env_set": False,
                "updated_at": None,
                "override_updated_at": None,
            },
        )
        row["override_value"] = value
    try:
        overall_config.validate_effective_security(projected_rows)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    rows = await asyncio.to_thread(save_overall_config_overrides, updates)
    overall_config.apply_rows_to_config(rows)
    restart_required_names = [
        name
        for name in updates
        if (
            overall_config.OVERALL_CONFIG_BY_NAME[name].restart_required
            or overall_config.OVERALL_CONFIG_BY_NAME[name].build_only
        )
    ]
    return _serialize_overall_config(rows, restart_required_names=restart_required_names)


@router.post("/api/settings", response_model=SettingsResponse)
async def update_settings(req: SettingsRequest):
    await asyncio.to_thread(load_api_settings)
    preset = (
        get_preset_by_id(req.active_preset_id)
        if req.active_preset_id
        else get_active_preset()
    )
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")

    preset["name"] = (req.preset_name or preset.get("name") or "Untitled preset").strip()
    previous_api_url = str(preset.get("api_url") or "")
    next_api_url = req.api_url.rstrip("/")
    if preset.get("api_key") and not secrets.same_origin(previous_api_url, next_api_url):
        preset["api_key"] = ""
    preset["api_url"] = next_api_url
    if req.api_key is not None:
        preset["api_key"] = req.api_key.strip()
    preset["api_path"] = normalize_api_path(req.api_path)
    if req.default_model is not None:
        preset["default_model"] = normalize_default_model(
            req.default_model,
            preset["api_path"],
        )
    if req.default_response_format is not None:
        preset["default_response_format"] = normalize_default_response_format(
            req.default_response_format
        )
    if req.upstream_socks5_proxy is not None:
        current_proxy = get_upstream_socks5_proxy(raw=True)
        requested_proxy = req.upstream_socks5_proxy.strip()
        if current_proxy and requested_proxy == mask_socks5_proxy_url(current_proxy):
            app.state.upstream_socks5_proxy = current_proxy
        else:
            apply_upstream_socks5_proxy(requested_proxy)
    if req.webhook_url is not None:
        current_webhook_url = get_webhook_url(raw=True)
        requested_webhook_url = req.webhook_url.strip()
        if current_webhook_url and requested_webhook_url == mask_webhook_url(current_webhook_url):
            app.state.webhook_url = current_webhook_url
        else:
            apply_webhook_url(requested_webhook_url)
        _validate_webhook_security()
    _validate_preset_secret_binding(preset)
    apply_api_preset(preset)
    await asyncio.to_thread(persist_api_settings)
    if req.prompt_optimizer is not None:
        from ..presets import (
            apply_prompt_optimizer_settings,
            get_prompt_optimizer_settings,
        )
        current_optimizer = get_prompt_optimizer_settings()
        updated_optimizer = apply_prompt_optimizer_settings(current_optimizer, req.prompt_optimizer)
        _validate_optimizer_secret_binding(updated_optimizer)
        await asyncio.to_thread(save_prompt_optimizer_settings, updated_optimizer)
    if req.ai_assistant is not None:
        from ..presets import get_ai_assistant_settings
        current_assistant = get_ai_assistant_settings()
        updated_assistant = apply_ai_assistant_settings(current_assistant, req.ai_assistant)
        await asyncio.to_thread(save_ai_assistant_settings, updated_assistant)
    if req.r2_backup is not None:
        from ..presets import get_r2_backup_settings
        current_r2 = get_r2_backup_settings()
        updated_r2 = apply_r2_backup_settings(current_r2, req.r2_backup)
        if updated_r2.get("endpoint_url"):
            try:
                await ssrf.validate_r2_endpoint_url_async(
                    updated_r2["endpoint_url"],
                    config.R2_ENDPOINT_HOST_ALLOWLIST,
                )
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e)) from e
        _validate_r2_secret_bindings(updated_r2)
        await asyncio.to_thread(save_r2_backup_settings, updated_r2)
    if req.nodeimage is not None:
        current_nodeimage = get_nodeimage_settings()
        updated_nodeimage = apply_nodeimage_settings(
            current_nodeimage,
            req.nodeimage,
        )
        _validate_nodeimage_secret_binding(updated_nodeimage)
        await asyncio.to_thread(save_nodeimage_settings, updated_nodeimage)
    return await asyncio.to_thread(build_settings_response)


@router.get("/api/settings", response_model=SettingsResponse)
async def get_settings():
    await asyncio.to_thread(load_api_settings)
    return await asyncio.to_thread(build_settings_response)


@router.post("/api/settings/r2/health", response_model=R2HealthResponse)
async def check_r2_settings_health(req: R2BackupSettingsRequest):
    from ..presets import get_r2_backup_settings

    current = await asyncio.to_thread(get_r2_backup_settings)
    draft = apply_r2_backup_settings(current, req)
    if draft.get("endpoint_url"):
        try:
            await ssrf.validate_r2_endpoint_url_async(
                draft["endpoint_url"],
                config.R2_ENDPOINT_HOST_ALLOWLIST,
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
    _validate_r2_secret_bindings(draft)
    if not req.use_credentials:
        return R2HealthResponse(
            status="warning",
            checks=[
                {
                    "name": "configuration",
                    "status": "ok",
                    "message": "R2 configuration and secret bindings are valid",
                },
                {
                    "name": "remote_probe",
                    "status": "warning",
                    "message": "Credentialed remote probe was not requested",
                },
            ],
        )
    result = await asyncio.to_thread(probe_r2_settings, draft)
    return R2HealthResponse(**result)


@router.post("/api/settings/presets", response_model=SettingsResponse)
async def create_settings_preset(req: PresetCreateRequest):
    await asyncio.to_thread(load_api_settings)
    source = get_preset_by_id(req.source_preset_id) if req.source_preset_id else None
    source = source or get_active_preset()
    presets = get_api_presets()
    next_number = len(presets) + 1
    preset = {
        "id": uuid.uuid4().hex,
        "name": (req.name or f"Preset {next_number}").strip() or f"Preset {next_number}",
        "api_url": (
            req.api_url if req.api_url is not None else source.get("api_url", "")
        ).rstrip("/"),
        "api_key": (
            req.api_key.strip()
            if req.api_key is not None
            else source.get("api_key", "")
        ),
        "api_path": normalize_api_path(
            req.api_path or source.get("api_path", "/v1/images/generations")
        ),
    }
    if (
        req.api_key is None
        and source.get("api_key")
        and not secrets.same_origin(source.get("api_url"), preset["api_url"])
    ):
        preset["api_key"] = ""
    _validate_preset_secret_binding(preset)
    preset["default_model"] = normalize_default_model(
        req.default_model if req.default_model is not None else source.get("default_model"),
        preset["api_path"],
    )
    preset["default_response_format"] = normalize_default_response_format(
        req.default_response_format
        if req.default_response_format is not None
        else source.get("default_response_format")
    )
    presets.append(preset)
    apply_api_preset(preset)
    await asyncio.to_thread(persist_api_settings)
    return await asyncio.to_thread(build_settings_response)


@router.post("/api/settings/presets/{preset_id}/activate", response_model=SettingsResponse)
async def activate_settings_preset(preset_id: str):
    await asyncio.to_thread(load_api_settings)
    preset = get_preset_by_id(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")

    apply_api_preset(preset)
    await asyncio.to_thread(persist_api_settings)
    return await asyncio.to_thread(build_settings_response)


@router.delete("/api/settings/presets/{preset_id}", response_model=SettingsResponse)
async def delete_settings_preset(preset_id: str):
    await asyncio.to_thread(load_api_settings)
    presets = get_api_presets()
    if len(presets) <= 1:
        raise HTTPException(status_code=400, detail="At least one preset is required")

    delete_index = next(
        (index for index, preset in enumerate(presets) if preset["id"] == preset_id),
        None,
    )
    if delete_index is None:
        raise HTTPException(status_code=404, detail="Preset not found")

    active_preset_id = getattr(app.state, "active_preset_id", presets[0]["id"])
    presets.pop(delete_index)
    app.state.api_presets = presets
    if not any(preset["id"] == active_preset_id for preset in presets):
        fallback = presets[min(delete_index, len(presets) - 1)]
        apply_api_preset(fallback)
    else:
        app.state.active_preset_id = active_preset_id

    await asyncio.to_thread(persist_api_settings)

    return await asyncio.to_thread(build_settings_response)


HEALTH_STATUS_RANK = {"ok": 0, "warning": 1, "error": 2}


def add_health_check(checks: list[dict], name: str, status: str, message: str):
    checks.append({"name": name, "status": status, "message": message})


def health_status(checks: list[dict]) -> str:
    if not checks:
        return "error"
    return max(
        (check["status"] for check in checks),
        key=lambda status: HEALTH_STATUS_RANK[status],
    )


def preset_health_status(checks: list[dict]) -> str:
    blocking_checks = [check for check in checks if check["name"] != "upstream_probe"]
    if not blocking_checks:
        return "ok"
    return health_status(blocking_checks)


async def validate_health_api_url(
    api_url: str,
    api_path: str,
    checks: list[dict],
) -> bool:
    if not api_url:
        add_health_check(checks, "api_url", "error", "API URL is not configured")
        return False

    try:
        normalized_api_url = ssrf.normalize_upstream_base_url(api_url)
    except ValueError as e:
        add_health_check(checks, "api_url", "error", str(e))
        return False

    try:
        await ssrf.validate_upstream_url_async(
            build_upstream_url(normalized_api_url, api_path),
            config.UPSTREAM_HOST_ALLOWLIST,
        )
    except ValueError as e:
        add_health_check(checks, "ssrf", "error", str(e))
        return False

    add_health_check(
        checks,
        "ssrf",
        "ok",
        "API URL passed scheme, host allowlist, DNS, and private-IP checks",
    )
    return True


def validate_health_api_path(api_path: str, checks: list[dict]) -> bool:
    if api_path not in ALLOWED_API_PATHS:
        add_health_check(
            checks,
            "api_path",
            "error",
            (
                "API path is not supported. Allowed paths: "
                + ", ".join(sorted(ALLOWED_API_PATHS))
            ),
        )
        return False

    add_health_check(checks, "api_path", "ok", f"API path {api_path} is supported")
    return True


def validate_health_api_key(api_key: str, checks: list[dict]) -> str:
    raw_key = str(api_key or "").strip()
    if raw_key:
        add_health_check(checks, "api_key", "ok", "A Secret Registry credential is configured")
        return raw_key

    add_health_check(checks, "api_key", "error", "API key is not configured")
    return ""


@router.post(
    "/api/settings/presets/{preset_id}/health",
    response_model=PresetHealthResponse,
)
async def check_settings_preset_health(
    preset_id: str,
    req: CredentialProbeRequest | None = Body(default=None),
):
    preset = get_preset_by_id(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")

    checks: list[dict] = []
    api_url = str(preset.get("api_url") or "").rstrip("/")
    api_path = str(preset.get("api_path") or "")

    api_path_ok = validate_health_api_path(api_path, checks)
    url_ok = (
        await validate_health_api_url(api_url, api_path, checks)
        if api_path_ok
        else False
    )
    validate_health_api_key(preset.get("api_key", ""), checks)
    effective_api_key = get_effective_preset_api_key(preset) if req and req.use_credentials else ""

    if api_path_ok and url_ok:
        try:
            probe_result = await proxy.probe_upstream_endpoint(
                api_url,
                api_path,
                effective_api_key,
            )
        except Exception as e:
            probe_result = {
                "status": "error",
                "message": f"Upstream probe failed: {get_exception_message(e)}",
            }
        add_health_check(
            checks,
            "upstream_probe",
            str(probe_result["status"]),
            str(probe_result["message"]),
        )
    else:
        add_health_check(
            checks,
            "upstream_probe",
            "warning",
            "Skipped upstream probe because local URL/path validation failed",
        )

    return PresetHealthResponse(status=preset_health_status(checks), checks=checks)
