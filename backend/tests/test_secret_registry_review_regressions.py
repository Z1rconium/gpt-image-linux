import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.api import presets
from backend.app.api.middleware import register_exception_handlers
from backend.app.core import secrets
from backend.app.core import security
from backend.app.core import settings as config
from backend.app.core.validators import mask_socks5_proxy_url
from backend.app.integrations.r2 import config as r2_config
from backend.app.repositories import db as db_repo
from backend.app.repositories import settings as settings_repo


def test_resolve_secret_reference_supports_all_setting_sources(monkeypatch):
    target_url = "https://api.nodeimage.com"
    options = {
        "purpose": "nodeimage_api_key",
        "target_url": target_url,
        "host_allowlist": "api.nodeimage.com",
        "field_name": "NodeImage API key",
    }

    secrets.configure_registry("{}")
    assert secrets.resolve_secret_reference("", **options) == ""
    with pytest.raises(secrets.SecretRegistryError, match="must reference a secret_id"):
        secrets.resolve_secret_reference("literal-key", **options)

    # An undeclared ${ENV_VAR} reference must never be read from the process env.
    monkeypatch.setenv("NODEIMAGE_REFERENCE_KEY", "env-key")
    with pytest.raises(
        secrets.SecretRegistryError,
        match="no Secret Registry entry declares",
    ):
        secrets.resolve_secret_reference("${NODEIMAGE_REFERENCE_KEY}", **options)

    secrets.configure_registry(
        json.dumps(
            {
                "nodeimage-declared-env": {
                    "purpose": "nodeimage_api_key",
                    "origin": target_url,
                    "env": "NODEIMAGE_REFERENCE_KEY",
                }
            }
        )
    )
    assert (
        secrets.resolve_secret_reference("${NODEIMAGE_REFERENCE_KEY}", **options)
        == "env-key"
    )
    # A declared entry bound to another origin does not unlock the reference.
    with pytest.raises(
        secrets.SecretRegistryError,
        match="no Secret Registry entry declares",
    ):
        secrets.resolve_secret_reference(
            "${NODEIMAGE_REFERENCE_KEY}",
            **{**options, "target_url": "https://evil.example"},
        )
    monkeypatch.delenv("NODEIMAGE_REFERENCE_KEY")
    with pytest.raises(
        secrets.SecretRegistryError,
        match="NodeImage API key environment variable NODEIMAGE_REFERENCE_KEY",
    ):
        secrets.resolve_secret_reference("${NODEIMAGE_REFERENCE_KEY}", **options)

    monkeypatch.setenv("NODEIMAGE_REGISTRY_KEY", "registry-key")
    secrets.configure_registry(
        json.dumps(
            {
                "nodeimage-registry-key": {
                    "purpose": "nodeimage_api_key",
                    "origin": target_url,
                    "env": "NODEIMAGE_REGISTRY_KEY",
                }
            }
        )
    )
    assert (
        secrets.resolve_secret_reference("nodeimage-registry-key", **options)
        == "registry-key"
    )

    with pytest.raises(secrets.SecretRegistryError, match="not permitted"):
        secrets.resolve_secret_reference(
            "nodeimage-registry-key",
            **{**options, "purpose": "upstream_api"},
        )


def test_api_key_response_fields_masks_plaintext_for_all_consumers(monkeypatch):
    monkeypatch.setenv("RESPONSE_REGISTRY_KEY", "registry-value")
    secrets.configure_registry(
        json.dumps(
            {
                "response-registry-key": {
                    "purpose": "upstream_api",
                    "origin": "https://api.example.com",
                    "env": "RESPONSE_REGISTRY_KEY",
                }
            }
        )
    )

    fields = presets.api_key_response_fields("plain-secret-value")
    assert fields == {
        "api_key_masked": "plai***alue",
        "has_api_key": True,
        "api_key_source": "stored",
        "api_key_env_var": None,
        "api_key_secret_id": None,
    }
    assert "plain-secret-value" not in str(fields)

    assert presets.api_key_response_fields("${RESPONSE_ENV_KEY}")["api_key_source"] == "env"
    registry_fields = presets.api_key_response_fields("response-registry-key")
    assert registry_fields["api_key_source"] == "registry"
    assert registry_fields["api_key_secret_id"] == "response-registry-key"

    r2 = presets.build_r2_backup_settings_response(
        {
            "enabled": True,
            "endpoint_url": "https://account.r2.cloudflarestorage.com",
            "bucket_name": "images",
            "access_key_id": "plain-access-key",
            "secret_access_key": "plain-secret-key",
        }
    ).model_dump()
    assert r2["access_key_id_source"] == "stored"
    assert r2["access_key_id_secret_id"] is None
    assert r2["secret_access_key_source"] == "stored"
    assert r2["secret_access_key_secret_id"] is None
    assert "plain-access-key" not in str(r2)
    assert "plain-secret-key" not in str(r2)


def test_default_settings_bind_startup_env_refs_to_builtin_secret_ids(monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_API_URL", "https://api.example.com")
    monkeypatch.setattr(config, "DEFAULT_API_KEY", "${OPENAI_API_KEY}")
    monkeypatch.setattr(config, "DEFAULT_UPSTREAM_SOCKS5_PROXY", "${UPSTREAM_PROXY_URL}")
    monkeypatch.setattr(config, "PROMPT_OPTIMIZER_API_URL", "")
    monkeypatch.setattr(config, "PROMPT_OPTIMIZER_API_KEY", "${PROMPT_OPTIMIZER_API_KEY}")
    monkeypatch.setattr(config, "R2_ENDPOINT_URL", "https://account.r2.cloudflarestorage.com")
    monkeypatch.setattr(config, "R2_ACCESS_KEY_ID", "${R2_ACCESS_KEY_ID}")
    monkeypatch.setattr(config, "R2_SECRET_ACCESS_KEY", "${R2_SECRET_ACCESS_KEY}")
    secrets.configure_registry("{}")

    settings = db_repo._default_settings()

    # Startup values with a startup target URL become origin-bound builtin entries.
    assert settings["presets"][0]["api_key"] == "builtin-default-api-key"
    assert settings["r2_backup"]["access_key_id"] == "builtin-r2-access-key-id"
    assert settings["r2_backup"]["secret_access_key"] == "builtin-r2-secret-access-key"
    # Without a startup target URL there is nothing to bind, so the legacy
    # reference is kept verbatim and fails with an actionable message at use time.
    assert settings["prompt_optimizer"]["api_key"] == "${PROMPT_OPTIMIZER_API_KEY}"
    assert settings["upstream_socks5_proxy"] == "${UPSTREAM_PROXY_URL}"


def test_stored_legacy_secret_refs_are_preserved_on_load():
    secrets.configure_registry("{}")

    settings = db_repo._normalize_settings(
        {
            "active_preset_id": "default",
            "upstream_socks5_proxy": "${UPSTREAM_PROXY_URL}",
            "webhook_url": "${WEBHOOK_URL}",
            "presets": [
                {
                    "id": "default",
                    "name": "Default",
                    "api_url": "https://api.example.com",
                    "api_key": "${OPENAI_API_KEY}",
                    "api_path": "/v1/images/generations",
                    "default_model": "gpt-image-2",
                    "default_response_format": "url",
                }
            ],
            "r2_backup": {
                "enabled": True,
                "endpoint_url": "https://account.r2.cloudflarestorage.com",
                "bucket_name": "images",
                "access_key_id": "${R2_ACCESS_KEY_ID}",
                "secret_access_key": "${R2_SECRET_ACCESS_KEY}",
            },
        }
    )

    assert settings["presets"][0]["api_key"] == "${OPENAI_API_KEY}"
    assert settings["upstream_socks5_proxy"] == "${UPSTREAM_PROXY_URL}"
    assert settings["webhook_url"] == "${WEBHOOK_URL}"
    assert settings["r2_backup"]["access_key_id"] == "${R2_ACCESS_KEY_ID}"
    assert settings["r2_backup"]["secret_access_key"] == "${R2_SECRET_ACCESS_KEY}"


def test_legacy_r2_secret_refs_require_registry_declaration(monkeypatch):
    secrets.configure_registry("{}")
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.setattr(
        "backend.app.core.validators.resolve_hostname",
        lambda hostname: (hostname, ["104.18.0.1"]),
    )

    try:
        r2_config.resolve_r2_backup_settings(
            {
                "enabled": True,
                "endpoint_url": "https://account.r2.cloudflarestorage.com",
                "bucket_name": "images",
                "access_key_id": "${R2_ACCESS_KEY_ID}",
                "secret_access_key": "${R2_SECRET_ACCESS_KEY}",
            }
        )
    except r2_config.R2ConfigurationError as exc:
        message = str(exc)
    else:
        raise AssertionError("legacy R2 env refs should be rejected at use time")

    assert "R2 access key ID references ${R2_ACCESS_KEY_ID}" in message
    assert "SECRET_REGISTRY_JSON" in message


def test_http_exception_envelope_preserves_safe_detail():
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/bad-secret")
    def bad_secret():
        raise HTTPException(status_code=422, detail="API key references an unknown secret_id")

    response = TestClient(app).get("/bad-secret")

    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "error"
    assert body["error_code"] == "validation_error"
    assert body["detail"] == "API key references an unknown secret_id"
    assert body["message"] == body["detail"]
    assert body["error"] == body["detail"]
    assert body["correlation_id"]
    assert response.headers["X-Correlation-ID"] == body["correlation_id"]


def test_socks5_mask_never_exposes_literal_credentials():
    secrets.configure_registry("{}")

    masked = mask_socks5_proxy_url("socks5://literal-user:literal-pass@proxy.example:1080")
    assert masked == "socks5://***:***@proxy.example:1080"
    assert "literal-user" not in masked
    assert "literal-pass" not in masked
    assert mask_socks5_proxy_url("socks5://literal-user@proxy.example:1080") == (
        "socks5://***@proxy.example:1080"
    )
    assert mask_socks5_proxy_url("socks5://proxy.example:1080") == (
        "socks5://proxy.example:1080"
    )
    assert mask_socks5_proxy_url("https://literal-user:literal-pass@proxy.example") == "***"
    assert mask_socks5_proxy_url("socks5://literal-user:literal-pass@proxy.example:bad") == "***"
    assert mask_socks5_proxy_url("not-a-proxy-literal") == "***"
    assert mask_socks5_proxy_url("${LEGACY_PROXY_URL}") == "${LEGACY_PROXY_URL}"


def test_admin_token_verification_fails_closed_for_missing_key_and_malformed_cookies(
    monkeypatch,
):
    malformed = (
        "",
        "no-dot",
        "too.many.dots",
        "not_base64.not_base64",
        "\N{SNOWMAN}.signature",
        "a" * 9000 + ".signature",
    )

    monkeypatch.setattr(config, "ADMIN_KEY", "")
    for token in malformed + ("payload.signature",):
        assert security.verify_admin_token(token) is None

    monkeypatch.setattr(config, "ADMIN_KEY", "admin-key")
    for token in malformed:
        assert security.verify_admin_token(token) is None


def test_legacy_credential_migration_is_atomic_idempotent_and_redacted(
    tmp_path,
    monkeypatch,
    caplog,
):
    database_file = tmp_path / "data" / "app.sqlite3"
    monkeypatch.setattr(config, "DATA_DIR", str(database_file.parent))
    monkeypatch.setattr(config, "DATABASE_FILE", str(database_file))
    monkeypatch.setattr(config, "DEFAULT_API_URL", "https://api.example.com")
    monkeypatch.setattr(config, "DEFAULT_API_KEY", "")
    monkeypatch.setattr(config, "DEFAULT_UPSTREAM_SOCKS5_PROXY", "")
    monkeypatch.setattr(config, "PROMPT_OPTIMIZER_API_KEY", "")
    monkeypatch.setattr(config, "R2_ACCESS_KEY_ID", "")
    monkeypatch.setattr(config, "R2_SECRET_ACCESS_KEY", "")
    monkeypatch.setattr(config, "NODEIMAGE_API_KEY", "")
    monkeypatch.setattr(config, "R2_BACKUP_ENABLED", False)
    monkeypatch.setattr(config, "UPSTREAM_HOST_ALLOWLIST", "api.example.com")
    monkeypatch.setattr(config, "UPSTREAM_PROXY_HOST_ALLOWLIST", "proxy.example")
    monkeypatch.setattr(config, "WEBHOOK_HOST_ALLOWLIST", "hooks.example.com")
    monkeypatch.setattr(config, "PROMPT_OPTIMIZER_HOST_ALLOWLIST", "optimizer.example")
    monkeypatch.setattr(
        config,
        "R2_ENDPOINT_HOST_ALLOWLIST",
        "account.r2.cloudflarestorage.com",
    )
    monkeypatch.setenv("MIGRATION_API_A", "matched-api-literal")
    monkeypatch.setenv("MIGRATION_API_Z", "matched-api-literal")
    monkeypatch.setenv("MIGRATION_R2_ACCESS", "matched-r2-access")
    secrets.configure_registry(
        json.dumps(
            {
                "aaa-api-secret": {
                    "purpose": "upstream_api",
                    "origin": "https://api.example.com",
                    "env": "MIGRATION_API_A",
                },
                "zzz-api-secret": {
                    "purpose": "upstream_api",
                    "origin": "https://api.example.com",
                    "env": "MIGRATION_API_Z",
                },
                "r2-access-secret": {
                    "purpose": "r2_access_key_id",
                    "origin": "https://account.r2.cloudflarestorage.com",
                    "env": "MIGRATION_R2_ACCESS",
                },
            }
        )
    )

    legacy_values = {
        "proxy": "socks5://legacy-user:legacy-pass@proxy.example:1080",
        "webhook": "https://hooks.example.com/private/legacy-webhook",
        "optimizer": "legacy-optimizer-secret",
        "r2": "legacy-r2-secret",
        "nodeimage": "legacy-nodeimage-secret",
    }
    settings_repo.save_settings(
        {
            "active_preset_id": "legacy-preset",
            "presets": [
                {
                    "id": "legacy-preset",
                    "name": "Sensitive preset name",
                    "api_url": "https://api.example.com",
                    "api_key": "matched-api-literal",
                    "api_path": "/v1/images/generations",
                    "default_model": "gpt-image-2",
                    "default_response_format": "url",
                }
            ],
            "upstream_socks5_proxy": legacy_values["proxy"],
            "webhook_url": legacy_values["webhook"],
            "prompt_optimizer": {
                "enabled": True,
                "api_url": "https://optimizer.example",
                "api_key": legacy_values["optimizer"],
                "model": "text-model",
            },
            "ai_assistant": {"enabled": True, "vision_model": "vision-model"},
            "r2_backup": {
                "enabled": True,
                "endpoint_url": "https://account.r2.cloudflarestorage.com",
                "bucket_name": "images",
                "access_key_id": "matched-r2-access",
                "secret_access_key": legacy_values["r2"],
            },
            "nodeimage": {"enabled": True, "api_key": legacy_values["nodeimage"]},
        }
    )
    caplog.clear()

    with ThreadPoolExecutor(max_workers=2) as executor:
        concurrent_results = list(
            executor.map(
                lambda _: settings_repo.migrate_legacy_secret_references(),
                range(2),
            )
        )
    first = next(result for result in concurrent_results if result)
    second = settings_repo.migrate_legacy_secret_references()
    migrated = settings_repo.load_settings()

    assert first["api_presets"]["replaced"] == 1
    assert first["r2"] == {"replaced": 1, "cleared": 1, "disabled": 1}
    assert second == {}
    assert sum(bool(result) for result in concurrent_results) == 1
    assert migrated["presets"][0]["api_key"] == "aaa-api-secret"
    assert migrated["presets"][0]["name"] == "Sensitive preset name"
    assert migrated["upstream_socks5_proxy"] == ""
    assert migrated["webhook_url"] == ""
    assert migrated["prompt_optimizer"]["enabled"] is False
    assert migrated["prompt_optimizer"]["api_key"] == ""
    assert migrated["ai_assistant"]["enabled"] is False
    assert migrated["r2_backup"]["enabled"] is False
    assert migrated["r2_backup"]["access_key_id"] == "r2-access-secret"
    assert migrated["r2_backup"]["secret_access_key"] == ""
    assert migrated["nodeimage"]["enabled"] is False
    assert migrated["nodeimage"]["api_key"] == ""
    log_text = caplog.text
    for forbidden in (
        *legacy_values.values(),
        "Sensitive preset name",
        "api.example.com",
        "MIGRATION_API_A",
    ):
        assert forbidden not in log_text
