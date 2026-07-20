from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.api.middleware import register_exception_handlers
from backend.app.core import secrets
from backend.app.core import settings as config
from backend.app.integrations.r2 import config as r2_config
from backend.app.repositories import db as db_repo


def test_default_settings_do_not_emit_missing_builtin_secret_ids(monkeypatch):
    secrets.configure_registry("{}")
    monkeypatch.setattr(config, "DEFAULT_API_KEY", "${OPENAI_API_KEY}")
    monkeypatch.setattr(config, "DEFAULT_UPSTREAM_SOCKS5_PROXY", "${UPSTREAM_PROXY_URL}")
    monkeypatch.setattr(config, "PROMPT_OPTIMIZER_API_KEY", "${PROMPT_OPTIMIZER_API_KEY}")
    monkeypatch.setattr(config, "R2_ENDPOINT_URL", "https://account.r2.cloudflarestorage.com")
    monkeypatch.setattr(config, "R2_ACCESS_KEY_ID", "${R2_ACCESS_KEY_ID}")
    monkeypatch.setattr(config, "R2_SECRET_ACCESS_KEY", "${R2_SECRET_ACCESS_KEY}")

    settings = db_repo._default_settings()

    assert settings["presets"][0]["api_key"] == "${OPENAI_API_KEY}"
    assert settings["upstream_socks5_proxy"] == "${UPSTREAM_PROXY_URL}"
    assert settings["prompt_optimizer"]["api_key"] == "${PROMPT_OPTIMIZER_API_KEY}"
    assert settings["r2_backup"]["access_key_id"] == "${R2_ACCESS_KEY_ID}"
    assert settings["r2_backup"]["secret_access_key"] == "${R2_SECRET_ACCESS_KEY}"
    assert settings["presets"][0]["api_key"] != "builtin-default-api-key"


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


def test_legacy_r2_secret_refs_fail_with_missing_env_hint(monkeypatch):
    secrets.configure_registry("{}")
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY", raising=False)

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

    assert "R2 access key ID environment variable R2_ACCESS_KEY_ID is not set or empty" in message


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
