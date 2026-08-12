from backend.tests.support.contract import *  # noqa: F403

def test_health_and_version(client):
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    version = client.get("/api/version")
    assert version.status_code == 200
    assert version.json()["version"]


def test_version_reads_current_app_version_each_request(client, monkeypatch):
    versions = iter(["v0.4.7", "v0.4.8"])
    monkeypatch.setattr(config, "read_app_version", lambda: next(versions))

    first = client.get("/api/version")
    second = client.get("/api/version")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["version"] == "v0.4.7"
    assert second.json()["version"] == "v0.4.8"


def test_latest_version_uses_process_cache(client, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_VERSION_CHECK", True)
    monkeypatch.setattr(config, "GITHUB_REPO", "test/repo")
    monkeypatch.setattr(config, "read_app_version", lambda: "v0.4.7")

    calls = {"release": 0, "branch": 0}
    async def fake_release(repo: str):
        calls["release"] += 1
        assert repo == "test/repo"
        return "0.4.7"

    async def fake_branch(repo: str):
        calls["branch"] += 1
        return "0.4.7"

    monkeypatch.setattr(static_router, "_fetch_latest_release_version", fake_release)
    monkeypatch.setattr(static_router, "_fetch_branch_version_text", fake_branch)

    first = client.get("/api/version/latest")
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["latest_version"] == "0.4.7"
    assert first_body["has_update"] is False
    assert first_body["checked_at"]

    second = client.get("/api/version/latest")
    assert second.status_code == 200
    assert second.json()["latest_version"] == "0.4.7"
    assert second.json()["has_update"] is False
    assert second.json()["checked_at"] == first_body["checked_at"]
    assert calls == {"release": 1, "branch": 0}


def test_latest_version_falls_back_to_branch_version(client, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_VERSION_CHECK", True)
    monkeypatch.setattr(config, "GITHUB_REPO", "test/repo")
    monkeypatch.setattr(config, "read_app_version", lambda: "v0.4.6")

    async def fake_release(repo: str):
        return None

    async def fake_branch(repo: str):
        return "0.4.8"

    monkeypatch.setattr(static_router, "_fetch_latest_release_version", fake_release)
    monkeypatch.setattr(static_router, "_fetch_branch_version_text", fake_branch)

    response = client.get("/api/version/latest")
    assert response.status_code == 200
    body = response.json()
    assert body["latest_version"] == "0.4.8"
    assert body["has_update"] is True


def test_frontend_index_uses_csp_nonce(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    build_dir = tmp_path / "frontend_build"
    build_dir.mkdir()
    (build_dir / "index.html").write_text(
        """
        <!doctype html>
        <script>
          import("/_app/immutable/entry/start.js");
        </script>
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(backend_main.app.state, "frontend_build_dir", build_dir, raising=False)

    with _test_client() as client:
        resp = client.get("/")

    assert resp.status_code == 200
    nonce = re.search(r'<script nonce="([^"]+)">', resp.text).group(1)
    csp = resp.headers["content-security-policy"]
    assert f"'nonce-{nonce}'" in csp
    assert f"script-src-elem 'self' 'nonce-{nonce}'" in csp
    assert "script-src-attr 'none'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src-elem", 1)[1].split(";", 1)[0]
    assert "style-src 'self'" in csp
    assert "style-src-attr 'unsafe-inline'" in csp


def test_access_cookie_and_status(tmp_path):
    _configure_runtime(tmp_path, access_key="secret", allow_unauthenticated=False)
    with _test_client() as client:
        denied = client.get("/api/settings")
        assert denied.status_code == 401
        assert denied.json()["detail"] == "Access key required"

        version = client.get("/api/version")
        assert version.status_code == 401
        latest_version = client.get("/api/version/latest")
        assert latest_version.status_code == 401

        bad = client.post("/api/access", json={"access_key": "nope"})
        assert bad.status_code == 401
        assert bad.json()["detail"] == "Invalid access key"

        ok = client.post("/api/access", json={"access_key": "secret"})
        assert ok.status_code == 200
        assert ok.json()["authenticated"] is True
        cookie = ok.headers["set-cookie"]
        assert "gpt_image_access=" in cookie
        assert "HttpOnly" in cookie
        assert "samesite=lax" in cookie.lower()
        assert "Secure" not in cookie

        status = client.get("/api/access/status")
        assert status.status_code == 200
        assert status.json()["authenticated"] is True
        assert status.json()["expires_at"]

        version_after_unlock = client.get("/api/version")
        assert version_after_unlock.status_code == 200


def test_access_denied_paths_return_auth_status_codes(tmp_path):
    _configure_runtime(tmp_path, access_key="secret", allow_unauthenticated=False)

    with _test_client(raise_server_exceptions=False) as client:
        responses = [
            client.get("/api/settings"),
            client.post("/api/access", json={"access_key": "wrong"}),
            client.post(
                "/api/settings/presets",
                headers={"Origin": "https://evil.example"},
                json={"name": "Blocked"},
            ),
        ]

    assert [resp.status_code for resp in responses] == [401, 401, 403]
    assert responses[0].json()["detail"] == "Access key required"
    assert responses[1].json()["detail"] == "Invalid access key"
    assert responses[2].json()["detail"] == "CSRF origin check failed"


def test_access_token_signature_requires_configured_secret(monkeypatch):
    from backend.app.core import security as auth_security

    monkeypatch.setattr(config, "ACCESS_KEY", "")
    monkeypatch.setattr(config, "DEFAULT_API_KEY", "")

    with pytest.raises(RuntimeError, match="No signing secret available"):
        auth_security.create_access_token()


def test_allow_unauthenticated_startup_logs_warning(tmp_path, caplog):
    _configure_runtime(tmp_path, access_key="", allow_unauthenticated=True)

    caplog.set_level(logging.WARNING, logger="backend.app.api.app_state")
    with _test_client():
        pass

    assert "ALLOW_UNAUTHENTICATED=true" in caplog.text
    assert "without access-key authentication" in caplog.text


def test_admin_key_fallback_to_access_key_logs_warning(tmp_path, caplog):
    _configure_runtime(tmp_path, access_key="secret", allow_unauthenticated=False)

    caplog.set_level(logging.WARNING, logger="backend.app.api.app_state")
    with _test_client():
        pass

    assert "ADMIN_KEY is equal to ACCESS_KEY" in caplog.text
    assert "not independent" in caplog.text


def test_frontend_build_assets_are_available_before_access_unlock(tmp_path, monkeypatch):
    _configure_runtime(tmp_path, access_key="secret", allow_unauthenticated=False)
    build_dir = tmp_path / "frontend_build"
    asset_path = build_dir / "_app" / "immutable" / "entry" / "app.js"
    asset_path.parent.mkdir(parents=True)
    asset_path.write_text("console.log('ok');", encoding="utf-8")
    favicon_path = build_dir / "favicon.svg"
    favicon_path.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    monkeypatch.setattr(backend_main.app.state, "frontend_build_dir", build_dir, raising=False)

    with _test_client() as client:
        asset = client.get("/_app/immutable/entry/app.js")
        favicon = client.get("/favicon.svg")
        api = client.get("/api/settings")

    assert asset.status_code == 200
    assert "console.log('ok')" in asset.text
    assert favicon.status_code == 200
    assert favicon.headers["content-type"].startswith("image/svg+xml")
    assert api.status_code == 401


def test_access_lockout(tmp_path):
    _configure_runtime(tmp_path, access_key="secret", allow_unauthenticated=False)
    with _test_client(raise_server_exceptions=False) as client:
        for _ in range(config.ACCESS_MAX_FAILURES + 1):
            resp = client.post("/api/access", json={"access_key": "wrong"})
        assert resp.status_code == 429
        assert "Too many failed attempts" in resp.json()["detail"]


def test_access_lockout_persists_across_app_state_reset(tmp_path):
    _configure_runtime(tmp_path, access_key="secret", allow_unauthenticated=False)
    with _test_client(raise_server_exceptions=False) as client:
        for _ in range(config.ACCESS_MAX_FAILURES):
            resp = client.post("/api/access", json={"access_key": "wrong"})
        assert resp.status_code == 401

    backend_main.app.state._state.clear()
    db_repo.close_database_connections()

    with _test_client(raise_server_exceptions=False) as client:
        resp = client.post("/api/access", json={"access_key": "wrong"})

    assert resp.status_code == 429
    assert "Too many failed attempts" in resp.json()["detail"]


def test_access_failures_stays_bounded_under_unique_ips(tmp_path, monkeypatch):
    _configure_runtime(tmp_path, access_key="secret", allow_unauthenticated=False)
    monkeypatch.setattr(access_router, "_ACCESS_FAILURES_MAX_SIZE", 3)
    current_ip = {"value": "10.0.0.1"}
    monkeypatch.setattr(
        access_router.auth,
        "get_client_ip",
        lambda request: current_ip["value"],
    )

    with _test_client(raise_server_exceptions=False) as client:
        for index in range(5):
            current_ip["value"] = f"10.0.0.{index}"
            resp = client.post("/api/access", json={"access_key": "wrong"})
            assert resp.status_code == 401

        failures = coordination_repo.list_access_failures()
        assert [failure["client_ip"] for failure in failures] == [
            "10.0.0.2",
            "10.0.0.3",
            "10.0.0.4",
        ]
        assert len(failures) == 3


def test_session_pool_close_all_closes_retired_sessions(monkeypatch):
    created_sessions = []

    class FakeSession:
        def __init__(self, timeout=None, connector=None):
            self.timeout = timeout
            self.connector = connector
            self.closed = False
            self.close_calls = 0
            created_sessions.append(self)

        async def close(self):
            self.close_calls += 1
            self.closed = True

    monkeypatch.setattr(session_pool.aiohttp, "ClientSession", FakeSession)
    monkeypatch.setattr(session_pool, "_build_socks5_connector", lambda proxy: proxy)

    pool = session_pool.SessionPool()
    first = pool.get(timeout_kind=session_pool.TIMEOUT_UPSTREAM, socks5_proxy="socks5://a")
    second = pool.get(timeout_kind=session_pool.TIMEOUT_UPSTREAM, socks5_proxy="socks5://b")

    assert first is created_sessions[0]
    assert second is created_sessions[1]
    assert not first.closed

    asyncio.run(pool.close_all())

    assert first.closed is True
    assert second.closed is True
    assert first.close_calls == 1
    assert second.close_calls == 1


def test_session_pool_passes_connector_limits(monkeypatch):
    created_sessions = []
    safe_connector_kwargs = {}
    socks_connector_kwargs = {}
    config.AIOHTTP_CONNECTION_LIMIT = 77
    config.AIOHTTP_CONNECTION_LIMIT_PER_HOST = 9

    class FakeSession:
        def __init__(self, timeout=None, connector=None):
            self.timeout = timeout
            self.connector = connector
            self.closed = False
            created_sessions.append(self)

        async def close(self):
            self.closed = True

    class FakeProxyConnector:
        @classmethod
        def from_url(cls, proxy_url, **kwargs):
            socks_connector_kwargs["proxy_url"] = proxy_url
            socks_connector_kwargs.update(kwargs)
            return "socks-connector"

    monkeypatch.setattr(session_pool.aiohttp, "ClientSession", FakeSession)
    monkeypatch.setattr(
        session_pool,
        "create_safe_connector",
        lambda **kwargs: safe_connector_kwargs.update(kwargs) or "safe-connector",
    )
    monkeypatch.setitem(
        sys.modules,
        "aiohttp_socks",
        types.SimpleNamespace(ProxyConnector=FakeProxyConnector),
    )

    pool = session_pool.SessionPool()
    safe_session = pool.get()
    socks_session = pool.get(socks5_proxy="socks5://proxy")

    assert safe_session.connector == "safe-connector"
    assert safe_connector_kwargs == {"limit": 77, "limit_per_host": 9}
    assert socks_session.connector == "socks-connector"
    assert socks_connector_kwargs == {
        "proxy_url": "socks5://proxy",
        "limit": 77,
        "limit_per_host": 9,
    }

    asyncio.run(pool.close_all())


def test_ip_allowlist_blocks_api_but_not_health(tmp_path):
    _configure_runtime(tmp_path)
    config.IP_ALLOWLIST = "10.0.0.1"
    with _test_client(raise_server_exceptions=False) as client:
        health = client.get("/health")
        assert health.status_code == 200
        blocked = client.get("/api/version")
        assert blocked.status_code == 403
        assert blocked.json()["detail"] == "IP address is not allowed"


def test_csrf_origin_check_allows_same_origin_state_changes(client):
    settings = client.get("/api/settings")
    assert settings.status_code == 200
    active_preset_id = settings.json()["active_preset_id"]

    same_origin = client.post(
        "/api/settings",
        headers={"Origin": "http://testserver"},
        json={
            "active_preset_id": active_preset_id,
            "preset_name": "Same Origin",
            "api_url": "https://api.example.com",
            "api_key": "${TEST_OPENAI_API_KEY}",
            "api_path": "/v1/images/generations",
        },
    )

    assert same_origin.status_code == 200
    same_origin_body = same_origin.json()
    active_preset = next(
        preset
        for preset in same_origin_body["presets"]
        if preset["id"] == same_origin_body["active_preset_id"]
    )
    assert active_preset["name"] == "Same Origin"


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        (
            "post",
            "/api/settings",
            {
                "json": {
                    "active_preset_id": "default",
                    "preset_name": "Bad Origin",
                    "api_url": "https://api.example.com",
                    "api_key": "${TEST_OPENAI_API_KEY}",
                    "api_path": "/v1/images/generations",
                }
            },
        ),
        ("patch", "/api/gallery/missing/favorite", {"json": {"favorite": True}}),
        ("delete", "/api/gallery/missing", {}),
    ],
)
def test_csrf_origin_check_blocks_cross_site_state_changes(client, method, path, kwargs):
    request = getattr(client, method)

    resp = request(path, headers={"Origin": "https://evil.example"}, **kwargs)

    assert resp.status_code == 403
    assert resp.json()["detail"] == "CSRF origin check failed"


def test_csrf_origin_check_allows_same_origin_fetch_metadata_through_dev_proxy(tmp_path):
    _configure_runtime(tmp_path, access_key="secret", allow_unauthenticated=False)

    with _test_client() as client:
        resp = client.post(
            "/api/access",
            headers={
                "Host": "127.0.0.1:9090",
                "Origin": "http://localhost:5173",
                "Sec-Fetch-Site": "same-origin",
            },
            json={"access_key": "secret"},
        )

    assert resp.status_code == 200
    assert resp.json()["authenticated"] is True


def test_csrf_origin_check_blocks_missing_source_on_access_unlock(tmp_path):
    _configure_runtime(tmp_path, access_key="secret", allow_unauthenticated=False)

    with TestClient(backend_main.app) as client:
        resp = client.post("/api/access", json={"access_key": "secret"})

    assert resp.status_code == 403
    assert resp.json()["detail"] == "CSRF origin check failed"


def test_host_allowlist_blocks_unknown_host_even_with_same_origin_fetch_metadata(tmp_path):
    _configure_runtime(tmp_path, access_key="secret", allow_unauthenticated=False)
    config.ALLOWED_HOSTS = "panel.example.com"

    with _test_client() as client:
        resp = client.post(
            "/api/access",
            headers={
                "Host": "evil.example",
                "Origin": "https://evil.example",
                "Sec-Fetch-Site": "same-origin",
            },
            json={"access_key": "secret"},
        )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Host is not allowed"


def test_host_allowlist_blocks_untrusted_forwarded_host(client):
    config.PUBLIC_ORIGIN = "https://panel.example.com"
    config.TRUST_PROXY_HEADERS = True
    config.TRUSTED_PROXY_IPS = "127.0.0.0/8"
    import backend.app.core.security as _sec
    _sec._trusted_proxy_networks = None
    original_is_trusted = _sec.is_trusted_proxy
    _sec.is_trusted_proxy = lambda _host: True

    try:
        resp = client.post(
            "/api/settings/presets",
            headers={
                "Host": "panel.example.com",
                "Origin": "https://panel.example.com",
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "evil.example",
            },
            json={"name": "Proxy Preset"},
        )
    finally:
        _sec.is_trusted_proxy = original_is_trusted

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Forwarded host is not allowed"


def test_csrf_origin_check_does_not_block_get(client):
    resp = client.get("/api/settings", headers={"Origin": "https://evil.example"})

    assert resp.status_code == 200


def test_csrf_origin_check_uses_referer_when_origin_is_absent(client):
    resp = client.post(
        "/api/settings/presets",
        headers={"Referer": "https://evil.example/settings"},
        json={"name": "Bad Referer"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "CSRF origin check failed"


def test_csrf_origin_check_respects_trusted_forwarded_proto(client):
    config.TRUST_PROXY_HEADERS = True
    config.TRUSTED_PROXY_IPS = "127.0.0.0/8"
    import backend.app.core.security as _sec
    _sec._trusted_proxy_networks = None
    # TestClient uses "testclient" as client host; patch is_trusted_proxy to accept it
    original_is_trusted = _sec.is_trusted_proxy
    _sec.is_trusted_proxy = lambda _host: True

    try:
        resp = client.post(
            "/api/settings/presets",
            headers={
                "Host": "127.0.0.1:9090",
                "Origin": "https://panel.example.com",
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "panel.example.com",
            },
            json={"name": "Proxy Preset"},
        )

        assert resp.status_code == 200
    finally:
        _sec.is_trusted_proxy = original_is_trusted


def test_json_body_limit_rejects_oversized_json(client):
    config.MAX_JSON_BODY_MB = 1
    resp = client.post(
        "/api/generate",
        content=json.dumps({"prompt": "x" * (1024 * 1024 + 1)}),
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 413
    assert resp.json()["detail"] == "Request body too large"


def test_edits_body_limit_matches_source_image_count(client):
    config.MAX_FILE_SIZE_MB = 2
    config.MAX_ACTIVE_GENERATE_JOBS = 20

    assert body_limit._max_body_for_path("/api/edits", "multipart/form-data") == (
        config.MAX_FILE_SIZE_MB * MAX_EDIT_SOURCE_IMAGES * 1024 * 1024
        + EDIT_MULTIPART_METADATA_OVERHEAD_BYTES
    )


def test_request_models_forbid_extra_fields_and_require_prompt(client):
    extra = client.post(
        "/api/generate",
        json={"prompt": "valid", "unexpected": True},
    )
    empty_prompt = client.post(
        "/api/generate",
        json={"prompt": ""},
    )

    assert extra.status_code == 422
    assert empty_prompt.status_code == 422


def test_settings_rejects_upstream_url_userinfo_query_and_fragment(client):
    settings = client.get("/api/settings").json()
    active_preset_id = settings["active_preset_id"]

    for api_url in (
        "https://user:secret@api.example.com",
        "https://api.example.com?token=secret",
        "https://api.example.com#secret",
    ):
        resp = client.post(
            "/api/settings",
            json={
                "active_preset_id": active_preset_id,
                "preset_name": "Bad URL",
                "api_url": api_url,
                "api_key": "${TEST_OPENAI_API_KEY}",
                "api_path": "/v1/images/generations",
            },
        )
        assert resp.status_code == 422


def test_settings_and_presets(client):
    settings = client.get("/api/settings")
    assert settings.status_code == 200
    body = settings.json()
    assert body["presets"]
    assert body["active_preset_id"]
    assert body["default_model"] == "gpt-image-2"
    assert body["default_response_format"] == "url"
    assert body["presets"][0]["default_model"] == "gpt-image-2"
    assert body["presets"][0]["default_response_format"] == "url"

    updated = client.post(
        "/api/settings",
        json={
            "active_preset_id": body["active_preset_id"],
            "preset_name": "Primary",
            "api_url": "https://api.example.com",
            "api_key": "${TEST_OPENAI_API_KEY}",
            "api_path": "/v1/responses",
            "default_model": "gpt-image-2-preview",
            "default_response_format": "b64_json",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["api_path"] == "/v1/responses"
    assert updated.json()["default_model"] == "gpt-image-2-preview"
    assert updated.json()["default_response_format"] == "b64_json"

    chat_updated = client.post(
        "/api/settings",
        json={
            "active_preset_id": body["active_preset_id"],
            "preset_name": "Primary",
            "api_url": "https://api.example.com",
            "api_key": "${TEST_OPENAI_API_KEY}",
            "api_path": "/v1/chat/completions",
        },
    )
    assert chat_updated.status_code == 200
    assert chat_updated.json()["api_path"] == "/v1/chat/completions"
    assert chat_updated.json()["default_model"] == "gpt-image-2-preview"
    assert chat_updated.json()["default_response_format"] == "b64_json"

    created = client.post("/api/settings/presets", json={"name": "Alt"})
    assert created.status_code == 200
    assert len(created.json()["presets"]) == 2
    assert created.json()["default_model"] == "gpt-image-2-preview"
    assert created.json()["default_response_format"] == "b64_json"

    deleted = client.delete(f"/api/settings/presets/{created.json()['active_preset_id']}")
    assert deleted.status_code == 200
    assert len(deleted.json()["presets"]) == 1
    assert deleted.json()["active_preset_id"] == body["active_preset_id"]
    assert all(preset["name"] != "Alt" for preset in deleted.json()["presets"])

    reloaded = client.get("/api/settings")
    assert reloaded.status_code == 200
    assert len(reloaded.json()["presets"]) == 1
    assert all(preset["name"] != "Alt" for preset in reloaded.json()["presets"])


def test_build_upstream_url_accepts_openai_style_v1_base():
    from backend.app.core.api_paths import build_upstream_url

    assert (
        build_upstream_url("https://api.example.com", "/v1/chat/completions")
        == "https://api.example.com/v1/chat/completions"
    )
    assert (
        build_upstream_url("https://api.example.com/v1", "/v1/chat/completions")
        == "https://api.example.com/v1/chat/completions"
    )
    assert (
        build_upstream_url(
            "https://api.example.com/v1/chat/completions",
            "/v1/chat/completions",
        )
        == "https://api.example.com/v1/chat/completions"
    )


def test_settings_global_socks5_proxy_save_mask_preserve_and_clear(client):
    settings = client.get("/api/settings").json()
    active_preset_id = settings["active_preset_id"]
    base_payload = {
        "active_preset_id": active_preset_id,
        "preset_name": "Proxy preset",
        "api_url": "https://api.example.com",
        "api_key": None,
        "api_path": "/v1/images/generations",
    }

    updated = client.post(
        "/api/settings",
        json={
            **base_payload,
            "upstream_socks5_proxy": "${TEST_UPSTREAM_PROXY_URL}",
        },
    )

    assert updated.status_code == 200
    updated_body = updated.json()
    assert updated_body["has_upstream_socks5_proxy"] is True
    assert updated_body["upstream_socks5_proxy_masked"] == "${TEST_UPSTREAM_PROXY_URL}"
    assert "user:secret" not in json.dumps(updated_body)
    assert settings_repo.load_settings()["upstream_socks5_proxy"] == "${TEST_UPSTREAM_PROXY_URL}"

    preserved = client.post(
        "/api/settings",
        json={
            **base_payload,
            "upstream_socks5_proxy": updated_body["upstream_socks5_proxy_masked"],
        },
    )
    assert preserved.status_code == 200
    assert settings_repo.load_settings()["upstream_socks5_proxy"] == "${TEST_UPSTREAM_PROXY_URL}"

    cleared = client.post(
        "/api/settings",
        json={**base_payload, "upstream_socks5_proxy": ""},
    )
    assert cleared.status_code == 200
    assert cleared.json()["has_upstream_socks5_proxy"] is False
    assert cleared.json()["upstream_socks5_proxy_masked"] == ""
    assert settings_repo.load_settings()["upstream_socks5_proxy"] == ""


def test_settings_global_webhook_url_save_mask_preserve_clear_and_use(client, monkeypatch):
    settings = client.get("/api/settings").json()
    base_payload = {
        "active_preset_id": settings["active_preset_id"],
        "preset_name": "Webhook preset",
        "api_url": "https://api.example.com",
        "api_key": None,
        "api_path": "/v1/images/generations",
    }

    updated = client.post(
        "/api/settings",
        json={
            **base_payload,
            "webhook_url": "${TEST_WEBHOOK_URL}",
        },
    )

    assert updated.status_code == 200
    updated_body = updated.json()
    assert updated_body["has_webhook_url"] is True
    assert updated_body["webhook_url_masked"] == "${TEST_WEBHOOK_URL}"
    assert "top-secret" not in json.dumps(updated_body)
    assert "hidden" not in json.dumps(updated_body)
    assert settings_repo.load_settings()["webhook_url"] == "${TEST_WEBHOOK_URL}"

    preserved = client.post(
        "/api/settings",
        json={**base_payload, "webhook_url": updated_body["webhook_url_masked"]},
    )
    assert preserved.status_code == 200
    assert settings_repo.load_settings()["webhook_url"] == "${TEST_WEBHOOK_URL}"

    created = client.post("/api/settings/presets", json={"name": "Alt webhook preset"})
    assert created.status_code == 200
    assert created.json()["webhook_url_masked"] == updated_body["webhook_url_masked"]

    reactivated = client.post(
        f"/api/settings/presets/{settings['active_preset_id']}/activate"
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["webhook_url_masked"] == updated_body["webhook_url_masked"]

    seen: dict[str, str | None] = {}

    def fake_validate_job_webhook_url(webhook_url: str | None) -> str | None:
        seen["webhook_url"] = webhook_url
        return None

    monkeypatch.setattr(job_queue, "validate_job_webhook_url", fake_validate_job_webhook_url)
    generated = client.post(
        "/api/generate",
        json={"prompt": "global webhook", "model": "gpt-image-2"},
    )
    assert generated.status_code == 202
    assert seen["webhook_url"] == "https://hooks.example.com/services/top-secret?token=hidden"

    cleared = client.post(
        "/api/settings",
        json={**base_payload, "webhook_url": ""},
    )
    assert cleared.status_code == 200
    assert cleared.json()["has_webhook_url"] is False
    assert cleared.json()["webhook_url_masked"] == ""
    assert settings_repo.load_settings()["webhook_url"] == ""


@pytest.mark.parametrize(
    ("payload", "detail"),
    [
        (
            {
                "preset_name": "Plain API key",
                "api_url": "https://api.example.com",
                "api_key": "plain-secret",
                "api_path": "/v1/images/generations",
            },
            "API key must use ${ENV_VAR_NAME} unless ALLOW_PLAINTEXT_SECRETS=true.",
        ),
        (
            {
                "preset_name": "Plain proxy",
                "api_url": "https://api.example.com",
                "api_key": "${TEST_OPENAI_API_KEY}",
                "api_path": "/v1/images/generations",
                "upstream_socks5_proxy": "socks5://user:secret@127.0.0.1:1080",
            },
            "SOCKS5 proxy URL must use ${ENV_VAR_NAME} unless ALLOW_PLAINTEXT_SECRETS=true.",
        ),
        (
            {
                "preset_name": "Plain webhook",
                "api_url": "https://api.example.com",
                "api_key": "${TEST_OPENAI_API_KEY}",
                "api_path": "/v1/images/generations",
                "webhook_url": "https://hooks.example.com/services/top-secret?token=hidden",
            },
            "Webhook URL must use ${ENV_VAR_NAME} unless ALLOW_PLAINTEXT_SECRETS=true.",
        ),
    ],
)
def test_settings_rejects_plaintext_secrets_by_default(client, payload, detail):
    settings = client.get("/api/settings").json()
    resp = client.post(
        "/api/settings",
        json={
            "active_preset_id": settings["active_preset_id"],
            **payload,
        },
    )

    assert resp.status_code == 422
    assert detail in resp.text


def test_settings_can_opt_in_to_plaintext_secret_storage(client):
    config.ALLOW_PLAINTEXT_SECRETS = True
    settings = client.get("/api/settings").json()
    resp = client.post(
        "/api/settings",
        json={
            "active_preset_id": settings["active_preset_id"],
            "preset_name": "Plaintext allowed",
            "api_url": "https://api.example.com",
            "api_key": "plain-secret",
            "api_path": "/v1/images/generations",
            "upstream_socks5_proxy": "socks5://user:secret@127.0.0.1:1080",
            "webhook_url": "https://hooks.example.com/services/top-secret?token=hidden",
        },
    )

    assert resp.status_code == 200
    persisted = settings_repo.load_settings()
    assert persisted["presets"][0]["api_key"] == "plain-secret"
    assert persisted["upstream_socks5_proxy"] == "socks5://user:secret@127.0.0.1:1080"
    assert persisted["webhook_url"] == "https://hooks.example.com/services/top-secret?token=hidden"


def _settings_payload(settings: dict, **overrides):
    payload = {
        "active_preset_id": settings["active_preset_id"],
        "preset_name": "Primary",
        "api_url": "https://api.example.com",
        "api_key": None,
        "api_path": settings["api_path"],
        "default_model": settings["default_model"],
    }
    payload.update(overrides)
    return payload


def _assistant_payload(settings: dict, **overrides):
    payload = _settings_payload(settings)
    payload["ai_assistant"] = {
        "enabled": False,
        "vision_model": "gpt-4o-mini",
    }
    payload.update(overrides)
    return payload


def _assistant_runtime_payload(
    settings: dict,
    *,
    assistant_enabled: bool = True,
    vision_model: str = "assistant-vision-model",
    optimizer_api_url: str = "https://example.com/v1/chat/completions",
    optimizer_model: str = "assistant-model",
    optimizer_timeout_seconds: int = 45,
    optimizer_api_key: str = "${TEST_PROMPT_OPTIMIZER_API_KEY}",
):
    return _settings_payload(
        settings,
        prompt_optimizer={
            "enabled": True,
            "api_url": optimizer_api_url,
            "model": optimizer_model,
            "timeout_seconds": optimizer_timeout_seconds,
            "api_key": optimizer_api_key,
        },
        ai_assistant={
            "enabled": assistant_enabled,
            "vision_model": vision_model,
        },
    )


def test_prompt_optimizer_settings_mask_preserve_and_clear(client):
    settings = client.get("/api/settings").json()
    assert settings["prompt_optimizer"]["enabled"] is False
    assert settings["prompt_optimizer"]["has_api_key"] is False
    assert settings["prompt_optimizer"]["timeout_seconds"] == 60

    updated = client.post(
        "/api/settings",
        json=_settings_payload(
            settings,
            prompt_optimizer={
                "enabled": True,
                "api_url": "https://example.com/v1/chat/completions",
                "model": "gpt-4o-mini",
                "timeout_seconds": 75,
                "api_key": "${TEST_PROMPT_OPTIMIZER_API_KEY}",
            },
        ),
    )

    assert updated.status_code == 200
    body = updated.json()
    optimizer = body["prompt_optimizer"]
    assert optimizer["enabled"] is True
    assert optimizer["api_url"] == "https://example.com/v1/chat/completions"
    assert optimizer["model"] == "gpt-4o-mini"
    assert optimizer["timeout_seconds"] == 75
    assert optimizer["has_api_key"] is True
    assert optimizer["api_key_source"] == "env"
    assert optimizer["api_key_env_var"] == "TEST_PROMPT_OPTIMIZER_API_KEY"
    assert "optimizer-secret" not in json.dumps(body)
    assert settings_repo.load_prompt_optimizer_settings()["api_key"] == "${TEST_PROMPT_OPTIMIZER_API_KEY}"
    assert settings_repo.load_prompt_optimizer_settings()["timeout_seconds"] == 75

    preserved = client.post(
        "/api/settings",
        json=_settings_payload(
            body,
            prompt_optimizer={
                "enabled": True,
                "api_url": "https://example.com/v1/chat/completions",
                "model": "gpt-4o-mini",
                "timeout_seconds": 90,
                "api_key": "${TEST_PROMPT_OPTIMIZER_API_KEY}",
            },
        ),
    )
    assert preserved.status_code == 200
    assert settings_repo.load_prompt_optimizer_settings()["api_key"] == "${TEST_PROMPT_OPTIMIZER_API_KEY}"
    assert settings_repo.load_prompt_optimizer_settings()["timeout_seconds"] == 90

    cleared = client.post(
        "/api/settings",
        json=_settings_payload(
            preserved.json(),
            prompt_optimizer={
                "enabled": False,
                "api_url": "",
                "model": "gpt-4o-mini",
                "timeout_seconds": 60,
                "api_key": "",
            },
        ),
    )
    assert cleared.status_code == 200
    assert cleared.json()["prompt_optimizer"]["has_api_key"] is False
    assert settings_repo.load_prompt_optimizer_settings()["api_key"] == ""

    invalid_timeout = client.post(
        "/api/settings",
        json=_settings_payload(
            cleared.json(),
            prompt_optimizer={
                "enabled": False,
                "api_url": "",
                "model": "gpt-4o-mini",
                "timeout_seconds": 0,
                "api_key": "",
            },
        ),
    )
    assert invalid_timeout.status_code == 422


def test_ai_assistant_settings_mask_preserve_and_clear(client):
    settings = client.get("/api/settings").json()
    configured_optimizer = client.post(
        "/api/settings",
        json=_settings_payload(
            settings,
            prompt_optimizer={
                "enabled": True,
                "api_url": "https://example.com/v1/chat/completions",
                "model": "shared-model",
                "timeout_seconds": 75,
                "api_key": "${TEST_PROMPT_OPTIMIZER_API_KEY}",
            },
        ),
    )
    assert configured_optimizer.status_code == 200
    settings = configured_optimizer.json()
    assistant = settings["ai_assistant"]
    assert assistant["enabled"] is True
    assert assistant["has_api_key"] is True
    assert assistant["api_key_source"] == "env"
    assert assistant["api_key_env_var"] == "TEST_PROMPT_OPTIMIZER_API_KEY"
    assert assistant["api_url"] == "https://example.com"
    assert assistant["model"] == "shared-model"
    assert assistant["vision_model"] == "gpt-4o-mini"
    assert assistant["timeout_seconds"] == 75
    assert assistant["api_path"] == "/v1/chat/completions"

    updated = client.post(
        "/api/settings",
        json=_assistant_payload(
            settings,
            ai_assistant={
                "enabled": True,
                "vision_model": "assistant-vision-model",
            },
        ),
    )

    assert updated.status_code == 200
    body = updated.json()
    assistant = body["ai_assistant"]
    assert assistant["enabled"] is True
    assert assistant["api_url"] == "https://example.com"
    assert assistant["model"] == "shared-model"
    assert assistant["vision_model"] == "assistant-vision-model"
    assert assistant["timeout_seconds"] == 75
    assert assistant["api_path"] == "/v1/chat/completions"
    assert assistant["has_api_key"] is True
    assert assistant["api_key_source"] == "env"
    assert assistant["api_key_env_var"] == "TEST_PROMPT_OPTIMIZER_API_KEY"
    assert "optimizer-secret" not in json.dumps(body)
    assert settings_repo.load_ai_assistant_settings()["vision_model"] == "assistant-vision-model"
    assert "api_key" not in settings_repo.load_ai_assistant_settings()

    preserved = client.post(
        "/api/settings",
        json=_assistant_payload(
            body,
            ai_assistant={
                "enabled": True,
                "vision_model": "assistant-vision-model",
            },
        ),
    )
    assert preserved.status_code == 200
    assert settings_repo.load_ai_assistant_settings()["vision_model"] == "assistant-vision-model"

    cleared = client.post(
        "/api/settings",
        json=_assistant_payload(
            preserved.json(),
            ai_assistant={
                "enabled": False,
                "vision_model": "gpt-4o-mini",
            },
        ),
    )
    assert cleared.status_code == 200
    assert cleared.json()["ai_assistant"]["has_api_key"] is True
    assert "api_key" not in settings_repo.load_ai_assistant_settings()

    invalid_timeout = client.post(
        "/api/settings",
        json=_assistant_payload(
            cleared.json(),
            ai_assistant={
                "enabled": False,
                "vision_model": "gpt-4o-mini",
            },
        ),
    )
    assert invalid_timeout.status_code == 200


def test_prompt_optimizer_rejects_plaintext_api_key_by_default(client):
    settings = client.get("/api/settings").json()
    resp = client.post(
        "/api/settings",
        json=_settings_payload(
            settings,
            prompt_optimizer={
                "enabled": True,
                "api_url": "https://example.com/v1/chat/completions",
                "model": "gpt-4o-mini",
                "timeout_seconds": 75,
                "api_key": "optimizer-secret",
            },
        ),
    )

    assert resp.status_code == 422
    assert "Prompt optimizer API key must use ${ENV_VAR_NAME} unless ALLOW_PLAINTEXT_SECRETS=true." in resp.text
def test_r2_backup_settings_mask_preserve_and_clear(client):
    settings = client.get("/api/settings").json()
    assert settings["r2_backup"]["enabled"] is False
    assert settings["r2_backup"]["key_prefix"] == "gallery/"
    assert settings["r2_backup"]["sync_interval_hours"] == 0

    updated = client.post(
        "/api/settings",
        json=_settings_payload(
            settings,
            r2_backup={
                "enabled": True,
                "endpoint_url": "https://account.r2.cloudflarestorage.com",
                "bucket_name": "image-backups",
                "region": "auto",
                "key_prefix": "gallery-test",
                "sync_interval_hours": 6,
                "access_key_id": "${TEST_R2_ACCESS_KEY_ID}",
                "secret_access_key": "${TEST_R2_SECRET_ACCESS_KEY}",
            },
        ),
    )

    assert updated.status_code == 200
    body = updated.json()
    r2 = body["r2_backup"]
    assert r2["enabled"] is True
    assert r2["endpoint_url"] == "https://account.r2.cloudflarestorage.com"
    assert r2["bucket_name"] == "image-backups"
    assert r2["key_prefix"] == "gallery-test/"
    assert r2["sync_interval_hours"] == 6
    assert r2["has_access_key_id"] is True
    assert r2["access_key_id_source"] == "env"
    assert r2["access_key_id_env_var"] == "TEST_R2_ACCESS_KEY_ID"
    assert r2["has_secret_access_key"] is True
    assert r2["secret_access_key_source"] == "env"
    assert r2["secret_access_key_env_var"] == "TEST_R2_SECRET_ACCESS_KEY"
    assert "r2-secret-key" not in json.dumps(body)
    assert settings_repo.load_r2_backup_settings()["access_key_id"] == "${TEST_R2_ACCESS_KEY_ID}"
    assert settings_repo.load_r2_backup_settings()["secret_access_key"] == "${TEST_R2_SECRET_ACCESS_KEY}"

    preserved = client.post(
        "/api/settings",
        json=_settings_payload(
            body,
            r2_backup={
                "enabled": True,
                "endpoint_url": "https://account.r2.cloudflarestorage.com",
                "bucket_name": "image-backups",
                "region": "auto",
                "key_prefix": "gallery-test/",
                "sync_interval_hours": 6,
                "access_key_id": "********",
                "secret_access_key": "********",
            },
        ),
    )
    assert preserved.status_code == 200
    assert settings_repo.load_r2_backup_settings()["access_key_id"] == "${TEST_R2_ACCESS_KEY_ID}"
    assert settings_repo.load_r2_backup_settings()["secret_access_key"] == "${TEST_R2_SECRET_ACCESS_KEY}"

    cleared = client.post(
        "/api/settings",
        json=_settings_payload(
            preserved.json(),
            r2_backup={
                "enabled": False,
                "endpoint_url": "",
                "bucket_name": "",
                "region": "auto",
                "key_prefix": "gallery/",
                "sync_interval_hours": 0,
                "access_key_id": "",
                "secret_access_key": "",
            },
        ),
    )
    assert cleared.status_code == 200
    assert cleared.json()["r2_backup"]["has_access_key_id"] is False
    assert cleared.json()["r2_backup"]["has_secret_access_key"] is False
    assert cleared.json()["r2_backup"]["sync_interval_hours"] == 0
    assert settings_repo.load_r2_backup_settings()["access_key_id"] == ""
    assert settings_repo.load_r2_backup_settings()["secret_access_key"] == ""


def test_r2_backup_settings_rejects_invalid_sync_interval(client):
    settings = client.get("/api/settings").json()

    negative = client.post(
        "/api/settings",
        json=_settings_payload(settings, r2_backup={"sync_interval_hours": -1}),
    )
    assert negative.status_code == 422

    non_numeric = client.post(
        "/api/settings",
        json=_settings_payload(settings, r2_backup={"sync_interval_hours": "6"}),
    )
    assert non_numeric.status_code == 422


def test_r2_backup_settings_rejects_private_endpoint_before_probe(client, monkeypatch):
    settings = client.get("/api/settings").json()

    def fail_probe(_draft):
        raise AssertionError("probe_r2_settings should not run for invalid endpoints")

    monkeypatch.setattr(settings_router, "probe_r2_settings", fail_probe)
    health = client.post(
        "/api/settings/r2/health",
        json={
            "enabled": True,
            "endpoint_url": "https://127.0.0.1",
            "bucket_name": "image-backups",
            "region": "auto",
            "key_prefix": "gallery/",
            "access_key_id": "${TEST_R2_ACCESS_KEY_ID}",
            "secret_access_key": "${TEST_R2_SECRET_ACCESS_KEY}",
            "use_credentials": True,
        },
    )
    assert health.status_code == 422
    assert "R2 endpoint URL must use a hostname" in health.text

    save = client.post(
        "/api/settings",
        json=_settings_payload(
            settings,
            r2_backup={
                "enabled": True,
                "endpoint_url": "https://127.0.0.1",
                "bucket_name": "image-backups",
                "region": "auto",
                "key_prefix": "gallery/",
                "access_key_id": "${TEST_R2_ACCESS_KEY_ID}",
                "secret_access_key": "${TEST_R2_SECRET_ACCESS_KEY}",
            },
        ),
    )
    assert save.status_code == 422
    assert "R2 endpoint URL must use a hostname" in save.text


def test_r2_backup_settings_allows_custom_endpoint_only_with_admin_allowlist(
    client,
    monkeypatch,
):
    monkeypatch.setattr(config, "R2_ENDPOINT_HOST_ALLOWLIST", "")
    blocked = client.post(
        "/api/settings/r2/health",
        json={
            "enabled": True,
            "endpoint_url": "https://storage.example.com",
            "bucket_name": "image-backups",
            "region": "auto",
            "key_prefix": "gallery/",
            "access_key_id": "${TEST_R2_ACCESS_KEY_ID}",
            "secret_access_key": "${TEST_R2_SECRET_ACCESS_KEY}",
            "use_credentials": True,
        },
    )
    assert blocked.status_code == 422
    assert "R2_ENDPOINT_HOST_ALLOWLIST" in blocked.text

    monkeypatch.setattr(config, "R2_ENDPOINT_HOST_ALLOWLIST", "storage.example.com")
    monkeypatch.setattr(
        "backend.app.core.validators.resolve_hostname",
        lambda hostname: (hostname, ["93.184.216.34"]),
    )

    seen: dict[str, dict] = {}

    def fake_probe(draft):
        seen["draft"] = draft
        return {
            "status": "ok",
            "checks": [{"name": "configuration", "status": "ok", "message": "ok"}],
        }

    monkeypatch.setattr(settings_router, "probe_r2_settings", fake_probe)
    allowed = client.post(
        "/api/settings/r2/health",
        json={
            "enabled": True,
            "endpoint_url": "https://storage.example.com",
            "bucket_name": "image-backups",
            "region": "auto",
            "key_prefix": "gallery/",
            "access_key_id": "${TEST_R2_ACCESS_KEY_ID}",
            "secret_access_key": "${TEST_R2_SECRET_ACCESS_KEY}",
            "use_credentials": True,
        },
    )
    assert allowed.status_code == 200
    assert seen["draft"]["endpoint_url"] == "https://storage.example.com"


def test_r2_env_defaults_fill_empty_persisted_settings(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    settings_repo.save_r2_backup_settings(
        {
            "enabled": False,
            "endpoint_url": "",
            "bucket_name": "",
            "region": "auto",
            "key_prefix": "gallery/",
            "access_key_id": "",
            "secret_access_key": "",
        }
    )

    monkeypatch.setenv("R2_ACCESS_KEY_ID", "env-r2-access")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "env-r2-secret")
    config.R2_BACKUP_ENABLED = True
    config.R2_ENDPOINT_URL = "https://account.r2.cloudflarestorage.com"
    config.R2_BUCKET_NAME = "env-image-backups"
    config.R2_ACCESS_KEY_ID = "env-r2-access"
    config.R2_SECRET_ACCESS_KEY = "env-r2-secret"

    settings = settings_repo.load_r2_backup_settings()
    assert settings["enabled"] is True
    assert settings["endpoint_url"] == "https://account.r2.cloudflarestorage.com"
    assert settings["bucket_name"] == "env-image-backups"
    assert settings["access_key_id"] == "${R2_ACCESS_KEY_ID}"
    assert settings["secret_access_key"] == "${R2_SECRET_ACCESS_KEY}"

    with db_repo._connect() as conn:
        raw = db_repo._get_setting_value(conn, db_repo.R2_BACKUP_SETTINGS_KEY)
    assert raw
    persisted = json.loads(raw)
    assert persisted["enabled"] is True
    assert persisted["endpoint_url"] == "https://account.r2.cloudflarestorage.com"
    assert persisted["bucket_name"] == "env-image-backups"
    assert persisted["access_key_id"] == "${R2_ACCESS_KEY_ID}"
    assert persisted["secret_access_key"] == "${R2_SECRET_ACCESS_KEY}"


def test_r2_sync_interval_env_default_and_invalid_normalization(tmp_path):
    _configure_runtime(tmp_path)
    config.R2_SYNC_INTERVAL_HOURS = 4

    settings = settings_repo.load_r2_backup_settings()
    assert settings["sync_interval_hours"] == 4

    settings_repo.save_r2_backup_settings({"sync_interval_hours": "bad"})
    assert settings_repo.load_r2_backup_settings()["sync_interval_hours"] == 0

    settings_repo.save_r2_backup_settings({"sync_interval_hours": -2})
    assert settings_repo.load_r2_backup_settings()["sync_interval_hours"] == 0

    settings_repo.save_r2_backup_settings({"sync_interval_hours": 1.5})
    assert settings_repo.load_r2_backup_settings()["sync_interval_hours"] == 0


def test_missing_r2_settings_key_persists_env_defaults_to_sqlite(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    settings_repo.save_settings(
        {
            "active_preset_id": "default",
            "upstream_socks5_proxy": "",
            "webhook_url": "",
            "presets": [
                {
                    "id": "default",
                    "name": "Default",
                    "api_url": "https://api.example.com",
                    "api_key": "${TEST_DEFAULT_API_KEY}",
                    "api_path": "/v1/images/generations",
                    "default_model": "gpt-image-2",
                    "default_response_format": "url",
                }
            ],
            "prompt_optimizer": {
                "enabled": False,
                "api_url": "",
                "api_key": "",
                "model": "gpt-4o-mini",
                "timeout_seconds": 60,
            },
        }
    )
    with db_repo._connect() as conn:
        conn.execute(
            "DELETE FROM settings_kv WHERE key = ?",
            (db_repo.R2_BACKUP_SETTINGS_KEY,),
        )
        conn.commit()

    monkeypatch.setenv("R2_ACCESS_KEY_ID", "env-r2-access")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "env-r2-secret")
    config.R2_BACKUP_ENABLED = True
    config.R2_ENDPOINT_URL = "https://account.r2.cloudflarestorage.com"
    config.R2_BUCKET_NAME = "env-image-backups"
    config.R2_REGION = "auto"
    config.R2_KEY_PREFIX = "gallery-env/"
    config.R2_ACCESS_KEY_ID = "env-r2-access"
    config.R2_SECRET_ACCESS_KEY = "env-r2-secret"
    config.R2_SYNC_INTERVAL_HOURS = 8

    settings = settings_repo.load_settings()
    assert settings["r2_backup"]["enabled"] is True
    assert settings["r2_backup"]["bucket_name"] == "env-image-backups"
    assert settings["r2_backup"]["access_key_id"] == "${R2_ACCESS_KEY_ID}"
    assert settings["r2_backup"]["secret_access_key"] == "${R2_SECRET_ACCESS_KEY}"
    assert settings["r2_backup"]["sync_interval_hours"] == 8

    with db_repo._connect() as conn:
        raw = db_repo._get_setting_value(conn, db_repo.R2_BACKUP_SETTINGS_KEY)
    assert raw
    persisted = json.loads(raw)
    assert persisted["enabled"] is True
    assert persisted["bucket_name"] == "env-image-backups"
    assert persisted["sync_interval_hours"] == 8
    assert persisted["key_prefix"] == "gallery-env/"
    assert persisted["access_key_id"] == "${R2_ACCESS_KEY_ID}"
    assert persisted["secret_access_key"] == "${R2_SECRET_ACCESS_KEY}"


def test_r2_health_uses_draft_settings_and_preserves_masked_credentials(client, monkeypatch):
    settings = client.get("/api/settings").json()
    saved = client.post(
        "/api/settings",
        json=_settings_payload(
            settings,
            r2_backup={
                "enabled": True,
                "endpoint_url": "https://account.r2.cloudflarestorage.com",
                "bucket_name": "image-backups",
                "region": "auto",
                "key_prefix": "gallery/",
                "access_key_id": "${TEST_R2_ACCESS_KEY_ID}",
                "secret_access_key": "${TEST_R2_SECRET_ACCESS_KEY}",
            },
        ),
    )
    assert saved.status_code == 200

    seen: dict[str, dict] = {}

    def fake_probe(draft):
        seen["draft"] = draft
        return {
            "status": "ok",
            "checks": [{"name": "configuration", "status": "ok", "message": "ok"}],
        }

    monkeypatch.setattr(settings_router, "probe_r2_settings", fake_probe)
    health = client.post(
        "/api/settings/r2/health",
        json={
            "enabled": True,
            "endpoint_url": "https://draft.r2.cloudflarestorage.com",
            "bucket_name": "draft-backups",
            "region": "auto",
            "key_prefix": "draft/",
            "access_key_id": "********",
            "secret_access_key": "********",
        },
    )

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["checks"][0]["name"] == "configuration"
    assert seen["draft"]["endpoint_url"] == "https://draft.r2.cloudflarestorage.com"
    assert seen["draft"]["bucket_name"] == "draft-backups"
    assert seen["draft"]["access_key_id"] == "${TEST_R2_ACCESS_KEY_ID}"
    assert seen["draft"]["secret_access_key"] == "${TEST_R2_SECRET_ACCESS_KEY}"


def test_storage_secures_data_directory_and_database_permissions(client):
    client.get("/api/settings")

    data_dir_mode = stat.S_IMODE(Path(config.DATA_DIR).stat().st_mode)
    database_mode = stat.S_IMODE(Path(config.DATABASE_FILE).stat().st_mode)

    assert data_dir_mode == 0o700
    assert database_mode == 0o600


def test_prompt_optimizer_system_prompt_file_roundtrip(client):
    from backend.app.integrations.prompt_optimizer_client import (
        PROMPT_OPTIMIZER_SYSTEM_PROMPT,
        load_prompt_optimizer_system_prompt,
        prompt_optimizer_system_prompt_path,
    )

    initial = client.get("/api/prompt/optimizer-system-prompt")

    assert initial.status_code == 200
    assert initial.json() == {
        "system_prompt": PROMPT_OPTIMIZER_SYSTEM_PROMPT,
        "default_system_prompt": PROMPT_OPTIMIZER_SYSTEM_PROMPT,
        "customized": False,
    }

    updated = client.post(
        "/api/prompt/optimizer-system-prompt",
        json={"system_prompt": "  Custom optimizer prompt\n"},
    )

    assert updated.status_code == 200
    assert updated.json()["system_prompt"] == "Custom optimizer prompt"
    assert updated.json()["customized"] is True
    assert (
        prompt_optimizer_system_prompt_path().read_text(encoding="utf-8")
        == "Custom optimizer prompt\n"
    )
    assert load_prompt_optimizer_system_prompt() == "Custom optimizer prompt"

    empty = client.post(
        "/api/prompt/optimizer-system-prompt",
        json={"system_prompt": "   "},
    )
    assert empty.status_code == 422
    assert load_prompt_optimizer_system_prompt() == "Custom optimizer prompt"


def test_prompt_optimize_disabled_returns_400(client):
    resp = client.post("/api/prompt/optimize", json={"prompt": "tiny robot"})

    assert resp.status_code == 400
    assert "not enabled" in resp.json()["detail"]


def test_prompt_optimize_success_uses_configured_upstream(client, monkeypatch):
    from backend.app.api.routers import prompt as prompt_router

    monkeypatch.setattr(prompt_router, "validate_optimizer_endpoint", lambda _url: None)
    settings = client.get("/api/settings").json()
    configured = client.post(
        "/api/settings",
        json=_settings_payload(
            settings,
            prompt_optimizer={
                "enabled": True,
                "api_url": "https://example.com/v1/chat/completions",
                "model": "prompt-model",
                "timeout_seconds": 45,
                "api_key": "${TEST_PROMPT_OPTIMIZER_API_KEY}",
            },
        ),
    )
    assert configured.status_code == 200
    custom_system_prompt = client.post(
        "/api/prompt/optimizer-system-prompt",
        json={"system_prompt": "Custom optimizer prompt"},
    )
    assert custom_system_prompt.status_code == 200
    seen: dict[str, object] = {}

    async def fake_optimize_prompt(**kwargs):
        seen.update(kwargs)
        return ("Optimized prompt text", "prompt-model", 42)

    monkeypatch.setattr(prompt_router, "optimize_prompt", fake_optimize_prompt)

    resp = client.post(
        "/api/prompt/optimize",
        json={
            "prompt": "tiny robot",
            "target_language": "en",
            "api_path": "/v1/responses",
            "model": "gpt-image-2",
            "size": "1024x1024",
            "quality": "high",
        },
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "optimized_prompt": "Optimized prompt text",
        "model": "prompt-model",
        "duration_ms": 42,
    }
    assert seen["api_url"] == "https://example.com/v1/chat/completions"
    assert seen["api_key"] == "optimizer-key"
    assert seen["model"] == "prompt-model"
    assert seen["timeout_seconds"] == 45
    assert seen["prompt"] == "tiny robot"
    assert seen["intent"] is None
    assert seen["target_language"] == "en"
    assert seen["image_api_path"] == "/v1/responses"
    assert seen["system_prompt"] == "Custom optimizer prompt"


def test_prompt_optimize_accepts_structured_intent(client, monkeypatch):
    from backend.app.api.routers import prompt as prompt_router

    monkeypatch.setattr(prompt_router, "validate_optimizer_endpoint", lambda _url: None)
    settings = client.get("/api/settings").json()
    configured = client.post(
        "/api/settings",
        json=_settings_payload(
            settings,
            prompt_optimizer={
                "enabled": True,
                "api_url": "https://example.com/v1/chat/completions",
                "model": "prompt-model",
                "api_key": "${TEST_PROMPT_OPTIMIZER_API_KEY}",
            },
        ),
    )
    assert configured.status_code == 200

    seen: dict[str, object] = {}

    async def fake_optimize_prompt(**kwargs):
        seen.update(kwargs)
        return ("Optimized prompt text", "prompt-model", 42)

    monkeypatch.setattr(prompt_router, "optimize_prompt", fake_optimize_prompt)

    resp = client.post(
        "/api/prompt/optimize",
        json={
            "prompt": "tiny robot",
            "intent": "make it rainy at dusk",
            "target_language": "zh-CN",
            "api_path": "/v1/images/generations",
            "model": "gpt-image-2",
            "size": "1024x1024",
            "quality": "high",
        },
    )

    assert resp.status_code == 200
    assert seen["prompt"] == "tiny robot"
    assert seen["intent"] == "make it rainy at dusk"
    assert seen["target_language"] == "zh-CN"


def test_prompt_optimize_upstream_error_and_timeout(client, monkeypatch):
    from backend.app.api.routers import prompt as prompt_router

    monkeypatch.setattr(prompt_router, "validate_optimizer_endpoint", lambda _url: None)
    settings = client.get("/api/settings").json()
    configured = client.post(
        "/api/settings",
        json=_settings_payload(
            settings,
            prompt_optimizer={
                "enabled": True,
                "api_url": "https://example.com/v1/chat/completions",
                "model": "prompt-model",
                "api_key": "${TEST_PROMPT_OPTIMIZER_API_KEY}",
            },
        ),
    )
    assert configured.status_code == 200

    async def fake_upstream_error(**_kwargs):
        raise prompt_router.UpstreamOptimizerError("bad optimizer response")

    monkeypatch.setattr(prompt_router, "optimize_prompt", fake_upstream_error)
    upstream_error = client.post("/api/prompt/optimize", json={"prompt": "tiny robot"})
    assert upstream_error.status_code == 502
    assert "bad optimizer response" in upstream_error.json()["detail"]

    async def fake_timeout(**_kwargs):
        raise prompt_router.OptimizerTimeoutError("optimizer timeout")

    monkeypatch.setattr(prompt_router, "optimize_prompt", fake_timeout)
    timeout = client.post("/api/prompt/optimize", json={"prompt": "tiny robot"})
    assert timeout.status_code == 504
    assert "optimizer timeout" in timeout.json()["detail"]


def test_prompt_optimizer_health_reports_connectivity_and_errors(client, monkeypatch):
    from backend.app.api.routers import prompt as prompt_router

    settings = client.get("/api/settings").json()
    configured = client.post(
        "/api/settings",
        json=_settings_payload(
            settings,
            prompt_optimizer={
                "enabled": True,
                "api_url": "https://example.com/v1/chat/completions",
                "model": "prompt-model",
                "api_key": "${TEST_PROMPT_OPTIMIZER_API_KEY}",
            },
        ),
    )
    assert configured.status_code == 200

    async def fake_probe(**kwargs):
        return {
            "status": "ok",
            "message": "Prompt optimizer responded successfully with model prompt-model",
            "model": kwargs["model"],
            "duration_ms": 12,
            "status_code": 200,
        }

    monkeypatch.setattr(prompt_router, "probe_prompt_optimizer_endpoint", fake_probe)
    healthy = client.post("/api/prompt/optimizer-health")
    assert healthy.status_code == 200
    assert healthy.json() == {
        "status": "ok",
        "message": "Prompt optimizer responded successfully with model prompt-model",
        "model": "prompt-model",
        "duration_ms": 12,
        "status_code": 200,
    }

    async def fake_error(**_kwargs):
        return {
            "status": "error",
            "message": "Optimizer upstream returned HTTP 500",
            "model": "prompt-model",
            "duration_ms": 8,
            "status_code": 500,
        }

    monkeypatch.setattr(prompt_router, "probe_prompt_optimizer_endpoint", fake_error)
    failed = client.post("/api/prompt/optimizer-health")
    assert failed.status_code == 200
    assert failed.json()["status"] == "error"
    assert failed.json()["status_code"] == 500


def test_prompt_snippets_crud_search_and_validation(client):
    empty = client.get("/api/prompt-snippets")
    assert empty.status_code == 200
    assert empty.json() == {"snippets": []}

    invalid = client.post(
        "/api/prompt-snippets",
        json={"title": "   ", "prompt": "usable prompt"},
    )
    assert invalid.status_code == 422

    first = client.post(
        "/api/prompt-snippets",
        json={"title": "Portrait base", "prompt": "cinematic portrait prompt"},
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["title"] == "Portrait base"
    assert first_body["prompt"] == "cinematic portrait prompt"
    assert first_body["favorite"] is False
    assert first_body["created_at"]
    assert first_body["updated_at"]

    second = client.post(
        "/api/prompt-snippets",
        json={
            "title": "Product hero",
            "prompt": "studio product photography",
            "favorite": True,
        },
    )
    assert second.status_code == 200
    second_body = second.json()

    listed = client.get("/api/prompt-snippets")
    assert listed.status_code == 200
    assert [snippet["id"] for snippet in listed.json()["snippets"]] == [
        second_body["id"],
        first_body["id"],
    ]

    searched = client.get("/api/prompt-snippets", params={"query": "portrait"})
    assert searched.status_code == 200
    assert [snippet["id"] for snippet in searched.json()["snippets"]] == [
        first_body["id"],
    ]

    updated = client.patch(
        f"/api/prompt-snippets/{first_body['id']}",
        json={"title": "Portrait closeup", "favorite": True},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Portrait closeup"
    assert updated.json()["favorite"] is True

    missing_update = client.patch(
        "/api/prompt-snippets/missing",
        json={"favorite": True},
    )
    assert missing_update.status_code == 404

    deleted = client.delete(f"/api/prompt-snippets/{second_body['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "ok"

    after_delete = client.get("/api/prompt-snippets")
    assert [snippet["id"] for snippet in after_delete.json()["snippets"]] == [
        first_body["id"],
    ]


def test_settings_rejects_invalid_socks5_proxy(client):
    config.ALLOW_PLAINTEXT_SECRETS = True
    settings = client.get("/api/settings").json()

    resp = client.post(
        "/api/settings",
        json={
            "active_preset_id": settings["active_preset_id"],
            "preset_name": "Bad proxy",
            "api_url": "https://api.example.com",
            "api_key": None,
            "api_path": "/v1/images/generations",
            "upstream_socks5_proxy": "http://127.0.0.1:1080",
        },
    )

    assert resp.status_code == 422
    assert "socks5://" in json.dumps(resp.json())


def test_settings_rejects_invalid_global_webhook_url(client):
    config.ALLOW_PLAINTEXT_SECRETS = True
    settings = client.get("/api/settings").json()

    resp = client.post(
        "/api/settings",
        json={
            "active_preset_id": settings["active_preset_id"],
            "preset_name": "Bad webhook",
            "api_url": "https://api.example.com",
            "api_key": None,
            "api_path": "/v1/images/generations",
            "webhook_url": "http://hooks.example.com/callback",
        },
    )

    assert resp.status_code == 422
    assert "https://" in json.dumps(resp.json())


def test_socks5_proxy_only_flows_to_generation_and_edit(client, monkeypatch):
    settings = client.get("/api/settings").json()
    active_preset_id = settings["active_preset_id"]
    seen: dict[str, str | bool] = {}

    updated = client.post(
        "/api/settings",
        json={
            "active_preset_id": active_preset_id,
            "preset_name": "Proxy preset",
            "api_url": "https://api.example.com",
            "api_key": None,
            "api_path": "/v1/images/generations",
            "upstream_socks5_proxy": "${TEST_UPSTREAM_PROXY_URL}",
        },
    )
    assert updated.status_code == 200

    async def fake_probe(api_url, api_path, api_key=""):
        seen["health_probe"] = True
        return {
            "status": "ok",
            "message": "OPTIONS probe succeeded with HTTP 204",
        }

    async def fake_generation_api(
        api_url,
        api_key,
        api_path,
        payload,
        api_preset_name=None,
        progress=None,
        socks5_proxy=None,
    ):
        seen["generation_proxy"] = socks5_proxy or ""
        image_id = image_files.generate_image_id()
        filename = f"{image_id}.png"
        entry = await gallery_mutations.add_to_gallery_async(
            image_bytes=PNG_BYTES,
            image_id=image_id,
            prompt=payload.prompt,
            size=payload.size,
            filename=filename,
            metadata={"api_path": api_path, "api_preset_name": api_preset_name},
        )
        return [entry]

    async def fake_edit_api(
        api_url,
        api_key,
        payload,
        image_sources,
        api_preset_name=None,
        progress=None,
        socks5_proxy=None,
    ):
        assert len(image_sources) == 1
        assert image_sources[0].temp_path.exists()
        seen["edit_proxy"] = socks5_proxy or ""
        image_id = image_files.generate_image_id()
        filename = f"{image_id}.png"
        entry = await gallery_mutations.add_to_gallery_async(
            image_bytes=PNG_BYTES,
            image_id=image_id,
            prompt=payload.prompt,
            size=payload.size,
            filename=filename,
            metadata={"api_path": "/v1/images/edits", "api_preset_name": api_preset_name},
        )
        return [entry]

    monkeypatch.setattr(backend_main.proxy, "probe_upstream_endpoint", fake_probe)
    monkeypatch.setattr(backend_main.proxy, "call_image_generation_api", fake_generation_api)
    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", fake_edit_api)

    health = client.post(f"/api/settings/presets/{active_preset_id}/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert seen["health_probe"] is True

    generate = client.post(
        "/api/generate",
        json={"prompt": "uses socks5", "model": "gpt-image-2"},
    )
    assert generate.status_code == 202
    assert _wait_for_job(client, generate.json()["job_id"])["status"] == "success"

    edit = client.post(
        "/api/edits",
        data={
            "prompt": "edit through socks5",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
        files={"image": ("input.png", PNG_BYTES, "image/png")},
    )
    assert edit.status_code == 202
    assert _wait_for_job(client, edit.json()["job_id"])["status"] == "success"

    assert seen["generation_proxy"] == "socks5://user:secret@127.0.0.1:1080"
    assert seen["edit_proxy"] == "socks5://user:secret@127.0.0.1:1080"


def test_preset_health_and_env_api_key_resolution(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-secret")
    settings = client.get("/api/settings").json()
    active_preset_id = settings["active_preset_id"]
    seen: dict[str, str] = {}

    async def fake_probe(api_url, api_path, api_key=""):
        seen["probe_url"] = api_url
        seen["probe_path"] = api_path
        seen["probe_key"] = api_key
        return {
            "status": "ok",
            "message": "OPTIONS probe succeeded with HTTP 204",
        }

    async def fake_generation_api(
        api_url,
        api_key,
        api_path,
        payload,
        api_preset_name=None,
        progress=None,
        socks5_proxy=None,
    ):
        seen["generation_key"] = api_key
        image_id = image_files.generate_image_id()
        filename = f"{image_id}.png"
        entry = await gallery_mutations.add_to_gallery_async(
            image_bytes=PNG_BYTES,
            image_id=image_id,
            prompt=payload.prompt,
            size=payload.size,
            filename=filename,
            metadata={
                "model": payload.model,
                "quality": payload.quality,
                "output_format": payload.output_format,
                "output_compression": payload.output_compression,
                "response_format": payload.response_format,
                "n": payload.n,
                "api_path": api_path,
                "api_preset_name": api_preset_name,
            },
        )
        return [entry]

    monkeypatch.setattr(backend_main.proxy, "probe_upstream_endpoint", fake_probe)
    monkeypatch.setattr(backend_main.proxy, "call_image_generation_api", fake_generation_api)

    updated = client.post(
        "/api/settings",
        json={
            "active_preset_id": active_preset_id,
            "preset_name": "Env preset",
            "api_url": "https://api.example.com",
            "api_key": "${OPENAI_API_KEY}",
            "api_path": "/v1/images/generations",
        },
    )
    assert updated.status_code == 200
    updated_body = updated.json()
    assert updated_body["api_key_source"] == "env"
    assert updated_body["api_key_env_var"] == "OPENAI_API_KEY"
    assert updated_body["api_key_masked"] == "${OPENAI_API_KEY}"
    assert "env-secret" not in json.dumps(updated_body)

    health = client.post(f"/api/settings/presets/{active_preset_id}/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert seen["probe_key"] == "env-secret"

    resp = client.post(
        "/api/generate",
        json={"prompt": "uses env", "model": "gpt-image-2"},
    )
    assert resp.status_code == 202
    job = _wait_for_job(client, resp.json()["job_id"])
    assert job["status"] == "success"
    assert seen["generation_key"] == "env-secret"


def _overall_config_item(client, name: str):
    response = client.get("/api/settings/overall-config")
    assert response.status_code == 200
    items = response.json()["items"]
    return next(item for item in items if item["name"] == name)


def test_overall_config_syncs_env_and_hot_override(client, monkeypatch):
    github_repo = _overall_config_item(client, "GITHUB_REPO")
    assert github_repo["value"] == "Z1rconium/gpt-image-linux"
    assert github_repo["source"] == "env"

    response = client.put(
        "/api/settings/overall-config",
        json={"updates": [{"name": "ENABLE_METRICS", "value": True}]},
    )
    assert response.status_code == 200
    item = next(i for i in response.json()["items"] if i["name"] == "ENABLE_METRICS")
    assert item["value"] is True
    assert item["source"] == "override"
    assert config.ENABLE_METRICS is True

    rows = settings_repo.sync_overall_config_env_values(
        {"ENABLE_METRICS": ("false", True), "ALLOW_UNAUTHENTICATED": ("true", True)}
    )
    assert rows["ENABLE_METRICS"]["override_value"] == "true"

    response = client.put(
        "/api/settings/overall-config",
        json={"updates": [{"name": "ENABLE_METRICS", "clear_override": True}]},
    )
    assert response.status_code == 200
    item = next(i for i in response.json()["items"] if i["name"] == "ENABLE_METRICS")
    assert item["source"] == "env"
    assert item["value"] is False


def test_overall_config_secret_mask_and_preserve(client):
    response = client.put(
        "/api/settings/overall-config",
        json={"updates": [{"name": "WEBHOOK_SIGNING_SECRET", "value": "super-secret"}]},
    )
    assert response.status_code == 422
    assert "ALLOW_PLAINTEXT_SECRETS" in response.text

    config.ALLOW_PLAINTEXT_SECRETS = True
    response = client.put(
        "/api/settings/overall-config",
        json={"updates": [{"name": "WEBHOOK_SIGNING_SECRET", "value": "super-secret"}]},
    )
    assert response.status_code == 200
    item = next(i for i in response.json()["items"] if i["name"] == "WEBHOOK_SIGNING_SECRET")
    assert item["value"] == "********"
    assert item["value_masked"] == "********"
    assert "super-secret" not in response.text

    response = client.put(
        "/api/settings/overall-config",
        json={"updates": [{"name": "WEBHOOK_SIGNING_SECRET", "value": "********"}]},
    )
    assert response.status_code == 200
    rows = settings_repo.list_overall_config_values()
    assert rows["WEBHOOK_SIGNING_SECRET"]["override_value"] == "super-secret"


def test_overall_config_secret_env_value_is_not_persisted(client, monkeypatch):
    monkeypatch.setenv("WEBHOOK_SIGNING_SECRET", "never-store-this")
    rows = settings_repo.sync_overall_config_env_values(
        overall_config.current_env_snapshot()
    )
    assert rows["WEBHOOK_SIGNING_SECRET"]["is_env_set"] is True
    assert rows["WEBHOOK_SIGNING_SECRET"]["env_value"] == ""


def test_overall_config_validation_errors(client):
    unknown = client.put(
        "/api/settings/overall-config",
        json={"updates": [{"name": "NO_SUCH_ENV", "value": "x"}]},
    )
    assert unknown.status_code == 422

    invalid_bool = client.put(
        "/api/settings/overall-config",
        json={"updates": [{"name": "ENABLE_METRICS", "value": "maybe"}]},
    )
    assert invalid_bool.status_code == 422

    invalid_repo = client.put(
        "/api/settings/overall-config",
        json={"updates": [{"name": "GITHUB_REPO", "value": "not a repo"}]},
    )
    assert invalid_repo.status_code == 422

    invalid_proxy = client.put(
        "/api/settings/overall-config",
        json={"updates": [{"name": "TRUST_PROXY_HEADERS", "value": True}]},
    )
    assert invalid_proxy.status_code == 422


def test_overall_config_restart_and_build_only_badges(client):
    response = client.put(
        "/api/settings/overall-config",
        json={
            "updates": [
                {"name": "ACCESS_KEY_COOKIE_NAME", "value": "custom_access"},
                {"name": "IP_ALLOWLIST", "value": "192.0.2.1"},
                {"name": "PYTHON_BASE_IMAGE", "value": "python:3.12-slim"},
            ]
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body["restart_required_names"]) == {
        "ACCESS_KEY_COOKIE_NAME",
        "IP_ALLOWLIST",
        "PYTHON_BASE_IMAGE",
    }
    items = {item["name"]: item for item in body["items"]}
    assert items["ACCESS_KEY_COOKIE_NAME"]["restart_required"] is True
    assert items["IP_ALLOWLIST"]["restart_required"] is True
    assert items["PYTHON_BASE_IMAGE"]["build_only"] is True


def test_options_404_probe_is_warning():
    status, message = classify_probe_status("OPTIONS", 404)
    assert status == "warning"
    assert "may only support POST" in message


def test_preset_health_ignores_upstream_probe_error_for_overall_status(client, monkeypatch):
    settings = client.get("/api/settings").json()
    active_preset_id = settings["active_preset_id"]

    async def fake_probe(api_url, api_path, api_key=""):
        return {
            "status": "error",
            "message": "HEAD probe returned HTTP 404; check API URL/path",
        }

    monkeypatch.setattr(backend_main.proxy, "probe_upstream_endpoint", fake_probe)
    updated = client.post(
        "/api/settings",
        json={
            "active_preset_id": active_preset_id,
            "preset_name": "Probe-only failure",
            "api_url": "https://api.example.com",
            "api_key": "${TEST_DEFAULT_API_KEY}",
            "api_path": "/v1/images/generations",
        },
    )
    assert updated.status_code == 200

    health = client.post(f"/api/settings/presets/{active_preset_id}/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert any(
        check["name"] == "upstream_probe" and check["status"] == "error"
        for check in body["checks"]
    )


def test_missing_env_api_key_is_reported(client, monkeypatch):
    monkeypatch.delenv("MISSING_IMAGE_KEY", raising=False)
    settings = client.get("/api/settings").json()
    active_preset_id = settings["active_preset_id"]

    async def fake_probe(api_url, api_path, api_key=""):
        return {
            "status": "ok",
            "message": "OPTIONS probe reached upstream with HTTP 401",
        }

    monkeypatch.setattr(backend_main.proxy, "probe_upstream_endpoint", fake_probe)
    updated = client.post(
        "/api/settings",
        json={
            "active_preset_id": active_preset_id,
            "preset_name": "Missing env",
            "api_url": "https://api.example.com",
            "api_key": "${MISSING_IMAGE_KEY}",
            "api_path": "/v1/images/generations",
        },
    )
    assert updated.status_code == 200

    health = client.post(f"/api/settings/presets/{active_preset_id}/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "error"
    assert any(
        check["name"] == "api_key" and check["status"] == "error"
        for check in body["checks"]
    )

    resp = client.post(
        "/api/generate",
        json={"prompt": "missing env", "model": "gpt-image-2"},
    )
    assert resp.status_code == 400
    assert "MISSING_IMAGE_KEY" in resp.json()["detail"]
