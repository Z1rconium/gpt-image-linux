import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.api import presets
from backend.app.api.middleware import register_exception_handlers
from backend.app.core import secrets
from backend.app.core import settings as config
from backend.app.integrations.r2 import config as r2_config
from backend.app.repositories import db as db_repo


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
    # Legacy stored literals stay usable; the settings API no longer accepts them.
    assert secrets.resolve_secret_reference("literal-key", **options) == "literal-key"

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
