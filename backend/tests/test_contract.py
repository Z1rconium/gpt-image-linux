import asyncio
import base64
import io
import json
import logging
import re
import sqlite3
import threading
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import main as backend_main
from backend.app.api import jobs
from backend.app.api.routers import access as access_router
from backend.app.api.routers import static as static_router
from backend.app.api.jobs import EditImageSource
from backend.app.core import settings as config
from backend.app.core.observability import metrics, record_job_stage_timing
from backend.app.integrations import session_pool
from backend.app.integrations.upstream_client import call_image_edit_api as ORIGINAL_CALL_IMAGE_EDIT_API
from backend.app.repositories import storage
from backend.app.schemas.models import EditRequest


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4//8/AwAI/AL+X1N6AAAAAElFTkSuQmCC"
)
JPEG_BYTES = b"\xff\xd8\xff\xd9"


def _configure_runtime(tmp_path: Path, *, access_key: str = "", allow_unauthenticated: bool = True):
    images_dir = tmp_path / "images"
    data_dir = tmp_path / "data"
    images_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    config.IMAGES_DIR = str(images_dir)
    config.DATA_DIR = str(data_dir)
    config.DATABASE_FILE = str(data_dir / "app.sqlite3")
    config.DEFAULT_API_URL = "https://api.example.com"
    config.DEFAULT_API_KEY = "default-key"
    config.DEFAULT_API_PATH = "/v1/images/generations"
    config.DEFAULT_RESPONSES_MODEL = "gpt-5.4"
    config.DEFAULT_UPSTREAM_SOCKS5_PROXY = ""
    config.ACCESS_KEY = access_key
    config.ALLOW_UNAUTHENTICATED = allow_unauthenticated
    config.ACCESS_KEY_COOKIE_NAME = "gpt_image_access"
    config.ACCESS_COOKIE_SECURE = False
    config.ACCESS_KEY_SESSION_MINUTES = 180
    config.ACCESS_MAX_FAILURES = 5
    config.ACCESS_LOCKOUT_SECONDS = 300
    config.IP_ALLOWLIST = ""
    config.TRUST_PROXY_HEADERS = False
    config.CSRF_ORIGIN_CHECK_ENABLED = True
    config.UPSTREAM_HOST_ALLOWLIST = ""
    config.WEBHOOK_HOST_ALLOWLIST = ""
    config.WEBHOOK_SIGNING_SECRET = "webhook-secret"
    config.WEBHOOK_TIMEOUT_SECONDS = 1
    config.WEBHOOK_MAX_ATTEMPTS = 1
    config.MAX_FILE_SIZE_MB = 50
    config.MAX_UPSTREAM_JSON_MB = 128
    config.MAX_PENDING_EDIT_SOURCE_MB = config.MAX_FILE_SIZE_MB * 4
    config.IMPORT_ARCHIVE_MAX_MB = config.MAX_FILE_SIZE_MB * 20
    config.IMPORT_MAX_FILES = 500
    config.IMPORT_MAX_UNCOMPRESSED_MB = 1024
    config.IMPORT_MAX_METADATA_BYTES = 2 * 1024 * 1024
    config.IMPORT_MAX_COMPRESSION_RATIO = 100
    config.MAX_ACTIVE_GENERATE_JOBS = 2
    config.MAX_QUEUED_GENERATE_JOBS = 20
    config.ENABLE_METRICS = False
    config.SLOW_GALLERY_QUERY_MS = 200
    config.THUMBNAILS_DIR = str(images_dir / "thumbs")
    config.THUMBNAIL_MAX_SIDE = 512
    config.PROMPT_OPTIMIZER_ENABLED = False
    config.PROMPT_OPTIMIZER_API_URL = ""
    config.PROMPT_OPTIMIZER_API_KEY = ""
    config.PROMPT_OPTIMIZER_MODEL = "gpt-4o-mini"
    config.PROMPT_OPTIMIZER_TIMEOUT_SECONDS = 20
    config.PROMPT_OPTIMIZER_MAX_OUTPUT_CHARS = 4000
    config.PROMPT_OPTIMIZER_HOST_ALLOWLIST = ""

    storage.close_database_connections()
    storage._db_initialized = False
    storage._dirs_initialized = False
    metrics.reset()
    backend_main.app.state._state.clear()


@pytest.fixture()
def client(tmp_path):
    _configure_runtime(tmp_path)
    with TestClient(backend_main.app) as test_client:
        yield test_client


def _wait_for_job(client: TestClient, job_id: str, timeout: float = 5.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        resp = client.get(f"/api/generate/{job_id}")
        assert resp.status_code == 200
        last = resp.json()
        if last["status"] in {
            "success",
            "error",
            "cancelled",
            "interrupted",
            "upstream_error",
        }:
            return last
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {last}")


def _wait_for_gallery_export_job(client: TestClient, job_id: str, timeout: float = 5.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        resp = client.get(f"/api/gallery/export-jobs/{job_id}")
        assert resp.status_code == 200
        last = resp.json()
        if last["status"] in {"success", "error"}:
            return last
        time.sleep(0.05)
    raise AssertionError(f"gallery export job {job_id} did not finish: {last}")


def _fake_gallery_entry(image_id: str, prompt: str, size: str, filename: str):
    storage.add_to_gallery_sync(
        image_id=image_id,
        prompt=prompt,
        size=size,
        filename=filename,
        metadata={
            "model": "gpt-image-2",
            "quality": "auto",
            "output_format": "png",
            "n": 1,
            "api_path": "/v1/images/generations",
            "api_preset_name": "Default",
        },
        image_bytes=PNG_BYTES,
    )
    return storage.get_gallery_entry(image_id)


async def _add_generated_gallery_entry(payload, api_path, api_preset_name):
    image_id = storage.generate_image_id()
    filename = f"{image_id}.png"
    return await storage.add_to_gallery_async(
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


DEFAULT_IMPORT_METADATA = object()


def _import_archive_bytes(
    *,
    metadata: dict | object | None = DEFAULT_IMPORT_METADATA,
    image_name: str = "images/import-1.png",
    image_bytes: bytes = PNG_BYTES,
    compression: int = zipfile.ZIP_DEFLATED,
    extra_files: int = 0,
) -> bytes:
    if metadata is DEFAULT_IMPORT_METADATA:
        metadata = {
            "schema_version": 1,
            "exported_at": "2026-01-01T00:00:00Z",
            "app": {"name": "gpt-image-linux", "version": "v0.0.0"},
            "images": [
                {
                    "id": "import-1",
                    "prompt": "imported",
                    "size": "1024x1024",
                    "filename": image_name,
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
        }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        if metadata is not None:
            zf.writestr("metadata.json", json.dumps(metadata))
        zf.writestr(image_name, image_bytes)
        for index in range(extra_files):
            zf.writestr(f"extra/{index}.txt", "x")
    return buf.getvalue()


def _post_import_archive(client: TestClient, archive_bytes: bytes):
    return client.post(
        "/api/import",
        files={"archive": ("archive.zip", archive_bytes, "application/zip")},
    )


class _FakeStreamContent:
    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    async def iter_chunked(self, _size: int):
        for chunk in self._chunks:
            yield chunk


class _FakeTransport:
    def __init__(self, peer_ip: str):
        self.peer_ip = peer_ip

    def get_extra_info(self, name: str):
        if name == "peername":
            return (self.peer_ip, 443)
        return None


class _FakeConnection:
    def __init__(self, peer_ip: str):
        self.transport = _FakeTransport(peer_ip)


class _FakeResponse:
    def __init__(
        self,
        status: int,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
        peer_ip: str | None = None,
    ):
        self.status = status
        self.headers = headers or {}
        self.content = _FakeStreamContent(chunks or [])
        self.connection = _FakeConnection(peer_ip) if peer_ip else None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]):
        self.responses = responses
        self.requested_urls: list[str] = []
        self.allow_redirects_values: list[bool | None] = []

    def get(self, url, **kwargs):
        self.requested_urls.append(url)
        self.allow_redirects_values.append(kwargs.get("allow_redirects"))
        return self.responses.pop(0)


class _FakePostSession:
    def __init__(self, response: _FakeResponse):
        self.response = response
        self.data = None
        self.requested_url = ""
        self.headers = {}
        self.allow_redirects = None

    def post(self, url, **kwargs):
        self.requested_url = url
        self.data = kwargs.get("data")
        self.headers = kwargs.get("headers") or {}
        self.allow_redirects = kwargs.get("allow_redirects")
        return self.response


class _FakeOptimizerResponse:
    def __init__(
        self,
        status: int = 200,
        payload: dict | None = None,
        json_error: Exception | None = None,
        peer_ip: str | None = "93.184.216.34",
    ):
        self.status = status
        self.payload = payload or {}
        self.json_error = json_error
        self.connection = _FakeConnection(peer_ip) if peer_ip else None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, **_kwargs):
        if self.json_error:
            raise self.json_error
        return self.payload


class _FakeOptimizerSession:
    def __init__(self, response):
        self.response = response
        self.requested_url = ""
        self.json_payload = None
        self.headers = {}
        self.allow_redirects = None
        self.timeout = None

    def post(self, url, **kwargs):
        self.requested_url = url
        self.json_payload = kwargs.get("json")
        self.headers = kwargs.get("headers") or {}
        self.allow_redirects = kwargs.get("allow_redirects")
        self.timeout = kwargs.get("timeout")
        return self.response


class _FakePool:
    def __init__(self, session):
        self.session = session

    def get(self, **kwargs):
        return self.session


async def _download_with_fake_session(session: _FakeSession, image_url: str):
    from backend.app.integrations import upstream_client

    return await upstream_client.download_image_url(session, image_url)


@pytest.fixture(autouse=True)
def patch_upstream(monkeypatch):
    async def fake_generation_api(
        api_url,
        api_key,
        api_path,
        payload,
        api_preset_name=None,
        progress=None,
        socks5_proxy=None,
    ):
        if progress:
            progress("building_generation_payload", "Building generation payload")
            progress("waiting_for_api", "Waiting for upstream API response")
            record_job_stage_timing("upstream_wait", 1.25)
            progress("received_api_response", "Received upstream API response")
            progress("extracting_generation_data", "Extracting image data array")
            progress("decoding_b64_json", "Decoding b64_json image")
            record_job_stage_timing("download_decode", 2.5)
            progress("validating_image_bytes", "Validating decoded image")
            record_job_stage_timing("validate", 0.75)
            progress("saving_image_file", "Saving image file and gallery metadata")
        entries = []
        for _index in range(payload.n):
            image_id = storage.generate_image_id()
            filename = f"{image_id}.png"
            entry = await storage.add_to_gallery_async(
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
            entries.append(entry)
        return entries

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
        source_path = image_sources[0].temp_path
        assert source_path.exists()
        assert source_path.read_bytes() == PNG_BYTES
        if progress:
            progress("building_edit_form", "Building multipart edit request")
            progress("uploading_edit_image", "Uploading source image and edit parameters")
            record_job_stage_timing("upstream_wait", 1.0)
            progress("received_api_response", "Received upstream API response")
            progress("extracting_edit_data", "Extracting edited image data array")
            progress("decoding_b64_json", "Decoding b64_json image")
            record_job_stage_timing("download_decode", 2.0)
            progress("validating_image_bytes", "Validating decoded image")
            record_job_stage_timing("validate", 0.5)
            progress("saving_images", "Saving edited images")
        entries = []
        for _index in range(payload.n):
            image_id = storage.generate_image_id()
            filename = f"{image_id}.png"
            entry = await storage.add_to_gallery_async(
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
                    "api_path": "/v1/images/edits",
                    "api_preset_name": api_preset_name,
                },
            )
            entries.append(entry)
        return entries

    monkeypatch.setattr(backend_main.proxy, "call_image_generation_api", fake_generation_api)
    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", fake_edit_api)


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


def test_latest_version_fetches_release_api_each_request(client, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_VERSION_CHECK", True)
    monkeypatch.setattr(config, "GITHUB_REPO", "test/repo")
    monkeypatch.setattr(config, "read_app_version", lambda: "v0.4.7")

    calls = {"release": 0, "branch": 0}
    release_versions = iter(["0.4.7", "0.4.8"])

    async def fake_release(repo: str):
        calls["release"] += 1
        assert repo == "test/repo"
        return next(release_versions)

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
    assert second.json()["latest_version"] == "0.4.8"
    assert second.json()["has_update"] is True
    assert calls == {"release": 2, "branch": 0}


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

    with TestClient(backend_main.app) as client:
        resp = client.get("/")

    assert resp.status_code == 200
    nonce = re.search(r'<script nonce="([^"]+)">', resp.text).group(1)
    csp = resp.headers["content-security-policy"]
    assert f"'nonce-{nonce}'" in csp
    assert f"script-src-elem 'self' 'nonce-{nonce}'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src-elem", 1)[1].split(";", 1)[0]


def test_access_cookie_and_status(tmp_path):
    _configure_runtime(tmp_path, access_key="secret", allow_unauthenticated=False)
    with TestClient(backend_main.app) as client:
        denied = client.get("/api/settings")
        assert denied.status_code == 401
        assert denied.json()["detail"] == "Access key required"

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


def test_frontend_build_assets_are_available_before_access_unlock(tmp_path, monkeypatch):
    _configure_runtime(tmp_path, access_key="secret", allow_unauthenticated=False)
    build_dir = tmp_path / "frontend_build"
    asset_path = build_dir / "_app" / "immutable" / "entry" / "app.js"
    asset_path.parent.mkdir(parents=True)
    asset_path.write_text("console.log('ok');", encoding="utf-8")
    monkeypatch.setattr(backend_main.app.state, "frontend_build_dir", build_dir, raising=False)

    with TestClient(backend_main.app) as client:
        asset = client.get("/_app/immutable/entry/app.js")
        api = client.get("/api/settings")

    assert asset.status_code == 200
    assert "console.log('ok')" in asset.text
    assert api.status_code == 401


def test_access_lockout(tmp_path):
    _configure_runtime(tmp_path, access_key="secret", allow_unauthenticated=False)
    with TestClient(backend_main.app, raise_server_exceptions=False) as client:
        for _ in range(config.ACCESS_MAX_FAILURES + 1):
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

    with TestClient(backend_main.app, raise_server_exceptions=False) as client:
        for index in range(5):
            current_ip["value"] = f"10.0.0.{index}"
            resp = client.post("/api/access", json={"access_key": "wrong"})
            assert resp.status_code == 401

        failures = backend_main.app.state.access_failures
        assert list(failures.keys()) == ["10.0.0.2", "10.0.0.3", "10.0.0.4"]
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


def test_ip_allowlist_blocks_api_but_not_health(tmp_path):
    _configure_runtime(tmp_path)
    config.IP_ALLOWLIST = "10.0.0.1"
    with TestClient(backend_main.app, raise_server_exceptions=False) as client:
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
            "api_key": "same-origin-key",
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
                    "api_key": "bad-origin-key",
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

    with TestClient(backend_main.app) as client:
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


def test_settings_and_presets(client):
    settings = client.get("/api/settings")
    assert settings.status_code == 200
    body = settings.json()
    assert body["presets"]
    assert body["active_preset_id"]
    assert body["default_model"] == "gpt-image-2"
    assert body["presets"][0]["default_model"] == "gpt-image-2"

    updated = client.post(
        "/api/settings",
        json={
            "active_preset_id": body["active_preset_id"],
            "preset_name": "Primary",
            "api_url": "https://api.example.com",
            "api_key": "new-key",
            "api_path": "/v1/responses",
            "default_model": "gpt-image-2-preview",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["api_path"] == "/v1/responses"
    assert updated.json()["default_model"] == "gpt-image-2-preview"

    chat_updated = client.post(
        "/api/settings",
        json={
            "active_preset_id": body["active_preset_id"],
            "preset_name": "Primary",
            "api_url": "https://api.example.com",
            "api_key": "new-key",
            "api_path": "/v1/chat/completions",
        },
    )
    assert chat_updated.status_code == 200
    assert chat_updated.json()["api_path"] == "/v1/chat/completions"
    assert chat_updated.json()["default_model"] == "gpt-image-2-preview"

    created = client.post("/api/settings/presets", json={"name": "Alt"})
    assert created.status_code == 200
    assert len(created.json()["presets"]) == 2
    assert created.json()["default_model"] == "gpt-image-2-preview"


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
            "upstream_socks5_proxy": "socks5://user:secret@127.0.0.1:1080/",
        },
    )

    assert updated.status_code == 200
    updated_body = updated.json()
    assert updated_body["has_upstream_socks5_proxy"] is True
    assert (
        updated_body["upstream_socks5_proxy_masked"]
        == "socks5://user:***@127.0.0.1:1080"
    )
    assert "secret" not in json.dumps(updated_body)
    assert (
        storage.load_settings()["upstream_socks5_proxy"]
        == "socks5://user:secret@127.0.0.1:1080"
    )

    preserved = client.post(
        "/api/settings",
        json={
            **base_payload,
            "upstream_socks5_proxy": updated_body["upstream_socks5_proxy_masked"],
        },
    )
    assert preserved.status_code == 200
    assert (
        storage.load_settings()["upstream_socks5_proxy"]
        == "socks5://user:secret@127.0.0.1:1080"
    )

    cleared = client.post(
        "/api/settings",
        json={**base_payload, "upstream_socks5_proxy": ""},
    )
    assert cleared.status_code == 200
    assert cleared.json()["has_upstream_socks5_proxy"] is False
    assert cleared.json()["upstream_socks5_proxy_masked"] == ""
    assert storage.load_settings()["upstream_socks5_proxy"] == ""


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
            "webhook_url": "https://hooks.example.com/services/top-secret?token=hidden",
        },
    )

    assert updated.status_code == 200
    updated_body = updated.json()
    assert updated_body["has_webhook_url"] is True
    assert updated_body["webhook_url_masked"] == "https://hooks.example.com/***?***"
    assert "top-secret" not in json.dumps(updated_body)
    assert "hidden" not in json.dumps(updated_body)
    assert (
        storage.load_settings()["webhook_url"]
        == "https://hooks.example.com/services/top-secret?token=hidden"
    )

    preserved = client.post(
        "/api/settings",
        json={**base_payload, "webhook_url": updated_body["webhook_url_masked"]},
    )
    assert preserved.status_code == 200
    assert (
        storage.load_settings()["webhook_url"]
        == "https://hooks.example.com/services/top-secret?token=hidden"
    )

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

    monkeypatch.setattr(jobs, "validate_job_webhook_url", fake_validate_job_webhook_url)
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
    assert storage.load_settings()["webhook_url"] == ""


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


def test_prompt_optimizer_settings_mask_preserve_and_clear(client):
    settings = client.get("/api/settings").json()
    assert settings["prompt_optimizer"]["enabled"] is False
    assert settings["prompt_optimizer"]["has_api_key"] is False

    updated = client.post(
        "/api/settings",
        json=_settings_payload(
            settings,
            prompt_optimizer={
                "enabled": True,
                "api_url": "https://example.com/v1/chat/completions",
                "model": "gpt-4o-mini",
                "api_key": "optimizer-secret",
            },
        ),
    )

    assert updated.status_code == 200
    body = updated.json()
    optimizer = body["prompt_optimizer"]
    assert optimizer["enabled"] is True
    assert optimizer["api_url"] == "https://example.com/v1/chat/completions"
    assert optimizer["model"] == "gpt-4o-mini"
    assert optimizer["has_api_key"] is True
    assert optimizer["api_key_source"] == "stored"
    assert "optimizer-secret" not in json.dumps(body)
    assert storage.load_prompt_optimizer_settings()["api_key"] == "optimizer-secret"

    preserved = client.post(
        "/api/settings",
        json=_settings_payload(
            body,
            prompt_optimizer={
                "enabled": True,
                "api_url": "https://example.com/v1/chat/completions",
                "model": "gpt-4o-mini",
                "api_key": "********",
            },
        ),
    )
    assert preserved.status_code == 200
    assert storage.load_prompt_optimizer_settings()["api_key"] == "optimizer-secret"

    cleared = client.post(
        "/api/settings",
        json=_settings_payload(
            preserved.json(),
            prompt_optimizer={
                "enabled": False,
                "api_url": "",
                "model": "gpt-4o-mini",
                "api_key": "",
            },
        ),
    )
    assert cleared.status_code == 200
    assert cleared.json()["prompt_optimizer"]["has_api_key"] is False
    assert storage.load_prompt_optimizer_settings()["api_key"] == ""


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
                "api_key": "optimizer-key",
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
    assert seen["image_api_path"] == "/v1/responses"


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
                "api_key": "optimizer-key",
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
            "upstream_socks5_proxy": "socks5://127.0.0.1:1080",
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
        image_id = storage.generate_image_id()
        filename = f"{image_id}.png"
        entry = await storage.add_to_gallery_async(
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
        image_id = storage.generate_image_id()
        filename = f"{image_id}.png"
        entry = await storage.add_to_gallery_async(
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

    assert seen["generation_proxy"] == "socks5://127.0.0.1:1080"
    assert seen["edit_proxy"] == "socks5://127.0.0.1:1080"


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
        image_id = storage.generate_image_id()
        filename = f"{image_id}.png"
        entry = await storage.add_to_gallery_async(
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


def test_generate_and_sse_contract(client):
    resp = client.post(
        "/api/generate",
        json={
            "prompt": "a red cube",
            "size": "1024x1024",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    job = _wait_for_job(client, job_id)
    assert job["status"] == "success"
    assert job["image_url"].startswith("/api/image/")

    events = client.get(f"/api/generate/{job_id}/events")
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert "event: job" in events.text
    assert job_id in events.text


def test_generate_request_api_path_overrides_active_preset(client):
    resp = client.post(
        "/api/generate",
        json={
            "prompt": "responses override",
            "size": "1024x1024",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
            "api_path": "/v1/responses",
        },
    )

    assert resp.status_code == 202
    job = _wait_for_job(client, resp.json()["job_id"])
    assert job["status"] == "success"
    assert job["api_path"] == "/v1/responses"
    entry = storage.get_gallery_entry(job["image_id"])
    assert entry is not None
    assert entry.api_path == "/v1/responses"


def test_multi_image_job_returns_all_results(client, monkeypatch):
    calls = []
    calls_lock = threading.Lock()
    all_started = threading.Event()
    release_event = threading.Event()

    async def blocking_generation_api(
        api_url,
        api_key,
        api_path,
        payload,
        api_preset_name=None,
        progress=None,
        socks5_proxy=None,
    ):
        with calls_lock:
            calls.append(payload)
            if len(calls) == 3:
                all_started.set()
        await asyncio.to_thread(release_event.wait)
        return [
            await _add_generated_gallery_entry(
                payload,
                api_path,
                api_preset_name,
            )
        ]

    monkeypatch.setattr(
        backend_main.proxy,
        "call_image_generation_api",
        blocking_generation_api,
    )

    resp = client.post(
        "/api/generate",
        json={
            "prompt": "three red cubes",
            "size": "1024x1024",
            "model": "gpt-image-2",
            "n": 3,
            "quality": "auto",
            "output_format": "png",
        },
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    try:
        assert all_started.wait(2)
        active_jobs = client.get("/api/generate/jobs")
        assert active_jobs.status_code == 200
        assert len(active_jobs.json()) == 1
        assert active_jobs.json()[0]["job_id"] == job_id
        assert active_jobs.json()[0]["status"] == "running"
        assert active_jobs.json()[0]["n"] == 3
        assert active_jobs.json()[0]["message"].startswith("Generating images (")
    finally:
        release_event.set()

    job = _wait_for_job(client, job_id)
    assert job["status"] == "success"
    assert job["n"] == 3
    assert len(job["images"]) == 3
    assert job["image_id"] == job["images"][0]["image_id"]
    assert job["image_url"] == job["images"][0]["image_url"]
    assert {image["filename"] for image in job["images"]} == {
        f"{image['image_id']}.png" for image in job["images"]
    }
    assert all(image["image_url"].startswith("/api/image/") for image in job["images"])

    gallery = client.get("/api/gallery")
    assert gallery.status_code == 200
    assert gallery.json()["total"] == 3
    with calls_lock:
        assert len(calls) == 3
        assert all(payload.n == 1 for payload in calls)
    assert all(
        storage.get_gallery_entry(image["image_id"]).n == 3
        for image in job["images"]
    )


def test_multi_image_job_succeeds_with_partial_upstream_failures(client, monkeypatch):
    calls = []

    async def flaky_generation_api(
        api_url,
        api_key,
        api_path,
        payload,
        api_preset_name=None,
        progress=None,
        socks5_proxy=None,
    ):
        calls.append(payload)
        if len(calls) == 2:
            raise backend_main.proxy.UpstreamApiError("one shard failed")
        return [
            await _add_generated_gallery_entry(
                payload,
                api_path,
                api_preset_name,
            )
        ]

    monkeypatch.setattr(
        backend_main.proxy,
        "call_image_generation_api",
        flaky_generation_api,
    )

    resp = client.post(
        "/api/generate",
        json={
            "prompt": "partially flaky batch",
            "size": "1024x1024",
            "model": "gpt-image-2",
            "n": 3,
            "quality": "auto",
            "output_format": "png",
        },
    )
    assert resp.status_code == 202

    job = _wait_for_job(client, resp.json()["job_id"])
    assert job["status"] == "success"
    assert job["message"] == "Generated 2 of 3 requested images; 1 failed"
    assert "1 of 3 image generation requests failed" in job["error"]
    assert "one shard failed" in job["error"]
    assert len(job["images"]) == 2
    assert len(calls) == 3
    assert all(payload.n == 1 for payload in calls)
    assert all(
        storage.get_gallery_entry(image["image_id"]).n == 3
        for image in job["images"]
    )

    gallery = client.get("/api/gallery")
    assert gallery.status_code == 200
    assert gallery.json()["total"] == 2


def test_multi_image_job_reports_upstream_error_when_all_children_fail(client, monkeypatch):
    calls = []

    async def failing_generation_api(
        api_url,
        api_key,
        api_path,
        payload,
        api_preset_name=None,
        progress=None,
        socks5_proxy=None,
    ):
        calls.append(payload)
        raise backend_main.proxy.UpstreamApiError("quota exhausted")

    monkeypatch.setattr(
        backend_main.proxy,
        "call_image_generation_api",
        failing_generation_api,
    )

    resp = client.post(
        "/api/generate",
        json={
            "prompt": "fully flaky batch",
            "size": "1024x1024",
            "model": "gpt-image-2",
            "n": 3,
            "quality": "auto",
            "output_format": "png",
        },
    )
    assert resp.status_code == 202

    job = _wait_for_job(client, resp.json()["job_id"])
    assert job["status"] == "upstream_error"
    assert job["stage"] == "generation_failed"
    assert job["images"] == []
    assert "3 of 3 image generation requests failed" in job["error"]
    assert "quota exhausted" in job["error"]
    assert len(calls) == 3
    assert all(payload.n == 1 for payload in calls)

    gallery = client.get("/api/gallery")
    assert gallery.status_code == 200
    assert gallery.json()["total"] == 0


def test_upstream_errors_are_reported_as_detailed_job_status(client, monkeypatch):
    async def failing_generation_api(
        api_url,
        api_key,
        api_path,
        payload,
        api_preset_name=None,
        progress=None,
        socks5_proxy=None,
    ):
        raise backend_main.proxy.UpstreamApiError("upstream quota exhausted")

    monkeypatch.setattr(
        backend_main.proxy,
        "call_image_generation_api",
        failing_generation_api,
    )

    resp = client.post(
        "/api/generate",
        json={"prompt": "quota test", "model": "gpt-image-2"},
    )
    assert resp.status_code == 202
    job = _wait_for_job(client, resp.json()["job_id"])
    assert job["status"] == "upstream_error"
    assert job["stage"] == "generation_failed"
    assert job["error"] == "upstream quota exhausted"


def test_active_jobs_mark_interrupted_with_detailed_status(client):
    storage.upsert_generate_job(
        {
            "job_id": "interrupted-job",
            "status": "running",
            "stage": "waiting_for_api",
            "message": "Waiting for upstream API response",
            "created_at": "2026-05-18T12:00:00Z",
            "updated_at": "2026-05-18T12:00:01Z",
        }
    )

    assert storage.mark_active_generate_jobs_interrupted() == 1
    job = storage.get_generate_job("interrupted-job")
    assert job is not None
    assert job["status"] == "interrupted"
    assert job["stage"] == "interrupted"


def test_job_stage_timings_and_optional_metrics(client):
    disabled = client.get("/api/metrics")
    assert disabled.status_code == 404

    config.ENABLE_METRICS = True
    resp = client.post(
        "/api/generate",
        json={
            "prompt": "timed job",
            "size": "1024x1024",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
    )
    assert resp.status_code == 202
    job = _wait_for_job(client, resp.json()["job_id"])
    assert job["status"] == "success"
    assert job["stage_timings"]["upstream_wait"] == 1.25
    assert job["stage_timings"]["download_decode"] == 2.5
    assert job["stage_timings"]["validate"] == 0.75
    assert "thumbnail" in job["stage_timings"]
    assert "db_insert" in job["stage_timings"]

    metrics_resp = client.get("/api/metrics")
    assert metrics_resp.status_code == 200
    body = metrics_resp.json()
    assert body["enabled"] is True
    assert body["counters"]["image_jobs.generation.queued"] >= 1
    assert body["counters"]["image_jobs.generation.succeeded"] >= 1
    assert body["gauges"]["image_jobs.active"] == 0
    assert body["gauges"]["image_jobs.running_capacity"] == 2
    assert body["rates"]["image_jobs.generation.failure_ratio"] == 0
    assert body["timings_ms"]["job_stage.upstream_wait"]["count"] >= 1

    text_resp = client.get("/api/metrics", headers={"accept": "text/plain"})
    assert text_resp.status_code == 200
    assert "gpt_image_panel_image_jobs_generation_queued_total" in text_resp.text
    assert "gpt_image_panel_image_jobs_active" in text_resp.text
    assert "gpt_image_panel_job_stage_upstream_wait_p95_ms" in text_resp.text

    prometheus_resp = client.get("/api/metrics/prometheus")
    assert prometheus_resp.status_code == 200
    assert prometheus_resp.headers["content-type"].startswith("text/plain")
    assert "gpt_image_panel_image_jobs_generation_failure_ratio" in prometheus_resp.text


def test_gallery_slow_query_logs_filters_page_and_total(client, caplog):
    _fake_gallery_entry("gallery-slow", "slow query prompt", "1024x1024", "gallery-slow.png")
    config.SLOW_GALLERY_QUERY_MS = 0

    with caplog.at_level(logging.WARNING, logger="backend.app.api.routers.gallery"):
        resp = client.get("/api/gallery?prompt=slow&page=1&page_size=1&include_total_bytes=true")

    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert "Slow /api/gallery query" in caplog.text
    assert "page=1" in caplog.text
    assert "total=1" in caplog.text
    assert "'prompt': 'slow'" in caplog.text
    assert metrics.snapshot()["counters"]["sqlite.slow_queries"] == 1


def test_storage_connect_closes_sqlite_handle(client, monkeypatch):
    closed_paths: list[str] = []
    real_connect = sqlite3.connect

    class TrackedConnection(sqlite3.Connection):
        def close(self):
            closed_paths.append(config.DATABASE_FILE)
            super().close()

    def tracked_connect(*args, **kwargs):
        kwargs["factory"] = TrackedConnection
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(storage.sqlite3, "connect", tracked_connect)

    with storage._connect() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1

    assert closed_paths == [config.DATABASE_FILE]


def test_generate_and_edit_default_size_is_auto(client):
    generate = client.post(
        "/api/generate",
        json={
            "prompt": "default size",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
    )
    assert generate.status_code == 202
    generate_job = _wait_for_job(client, generate.json()["job_id"])
    assert generate_job["size"] == "auto"
    assert generate_job["completed_at"].endswith("+08:00")
    generated_entry = storage.get_gallery_entry(generate_job["image_id"])
    assert generated_entry is not None
    assert generated_entry.completed_at == generate_job["completed_at"]

    edit = client.post(
        "/api/edits",
        data={
            "prompt": "default edit size",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
        files={"image": ("input.png", PNG_BYTES, "image/png")},
    )
    assert edit.status_code == 202
    edit_job = _wait_for_job(client, edit.json()["job_id"])
    assert edit_job["size"] == "auto"


def test_edit_upload_and_gallery_flow(client):
    seeded = _fake_gallery_entry("gallery-1", "seed image", "1024x1024", "gallery-1.png")
    assert seeded is not None

    edit_upload = client.post(
        "/api/edits",
        data={
            "prompt": "make it blue",
            "size": "1024x1024",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
        files={"image": ("input.png", PNG_BYTES, "image/png")},
    )
    assert edit_upload.status_code == 202
    upload_job_id = edit_upload.json()["job_id"]
    upload_job = _wait_for_job(client, upload_job_id)
    assert upload_job["status"] == "success"

    edit_gallery = client.post(
        "/api/edits/from-gallery/gallery-1",
        data={
            "prompt": "make it green",
            "size": "1024x1024",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
    )
    assert edit_gallery.status_code == 202
    gallery_job = _wait_for_job(client, edit_gallery.json()["job_id"])
    assert gallery_job["status"] == "success"


def test_edit_upload_accepts_multiple_sources(client, monkeypatch):
    seen: dict[str, list[str]] = {}

    async def fake_edit_api(
        api_url,
        api_key,
        payload,
        image_sources,
        api_preset_name=None,
        progress=None,
        socks5_proxy=None,
    ):
        seen["filenames"] = [source.filename for source in image_sources]
        for source in image_sources:
            assert source.temp_path.exists()
            assert source.temp_path.read_bytes() == PNG_BYTES
        image_id = storage.generate_image_id()
        filename = f"{image_id}.png"
        entry = await storage.add_to_gallery_async(
            image_bytes=PNG_BYTES,
            image_id=image_id,
            prompt=payload.prompt,
            size=payload.size,
            filename=filename,
            metadata={"api_path": "/v1/images/edits", "api_preset_name": api_preset_name},
        )
        return [entry]

    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", fake_edit_api)

    edit = client.post(
        "/api/edits",
        data={
            "prompt": "combine references",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
        files=[
            ("image[]", ("first.png", PNG_BYTES, "image/png")),
            ("image[]", ("second.png", PNG_BYTES, "image/png")),
        ],
    )

    assert edit.status_code == 202
    assert _wait_for_job(client, edit.json()["job_id"])["status"] == "success"
    assert seen["filenames"] == ["first.png", "second.png"]


def test_edit_from_gallery_combines_uploaded_sources(client, monkeypatch):
    seeded = _fake_gallery_entry("gallery-combo", "seed image", "1024x1024", "gallery-combo.png")
    assert seeded is not None
    seen: dict[str, list[str]] = {}

    async def fake_edit_api(
        api_url,
        api_key,
        payload,
        image_sources,
        api_preset_name=None,
        progress=None,
        socks5_proxy=None,
    ):
        seen["filenames"] = [source.filename for source in image_sources]
        assert len(image_sources) == 2
        assert image_sources[0].temp_path.read_bytes() == PNG_BYTES
        assert image_sources[1].temp_path.read_bytes() == PNG_BYTES
        image_id = storage.generate_image_id()
        filename = f"{image_id}.png"
        entry = await storage.add_to_gallery_async(
            image_bytes=PNG_BYTES,
            image_id=image_id,
            prompt=payload.prompt,
            size=payload.size,
            filename=filename,
            metadata={"api_path": "/v1/images/edits", "api_preset_name": api_preset_name},
        )
        return [entry]

    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", fake_edit_api)

    edit = client.post(
        "/api/edits/from-gallery/gallery-combo",
        data={
            "prompt": "combine gallery and upload",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
        files={"image": ("upload.png", PNG_BYTES, "image/png")},
    )

    assert edit.status_code == 202
    assert _wait_for_job(client, edit.json()["job_id"])["status"] == "success"
    assert seen["filenames"] == ["gallery-combo.png", "upload.png"]


def test_edit_rejects_more_than_16_sources(client):
    resp = client.post(
        "/api/edits",
        data={
            "prompt": "too many sources",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
        files=[
            ("image", (f"source-{index}.png", PNG_BYTES, "image/png"))
            for index in range(17)
        ],
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "At most 16 edit source images are supported."


def test_upstream_edit_api_sends_multiple_sources_as_image_array(client, tmp_path, monkeypatch):
    from backend.app.integrations import upstream_client

    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    first_path.write_bytes(PNG_BYTES)
    second_path.write_bytes(PNG_BYTES)
    sources = [
        EditImageSource(first_path, len(PNG_BYTES), "first.png", "image/png"),
        EditImageSource(second_path, len(PNG_BYTES), "second.png", "image/png"),
    ]
    response_body = json.dumps(
        {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]}
    ).encode("utf-8")
    session = _FakePostSession(
        _FakeResponse(
            200,
            headers={"Content-Type": "application/json"},
            chunks=[response_body],
            peer_ip="93.184.216.34",
        )
    )

    monkeypatch.setattr(upstream_client, "get_pool", lambda: _FakePool(session))
    monkeypatch.setattr(upstream_client.ssrf, "validate_upstream_url", lambda *args, **kwargs: None)
    monkeypatch.setattr(upstream_client.ssrf, "validate_response_peer_ip", lambda *args, **kwargs: None)

    entries = asyncio.run(
        ORIGINAL_CALL_IMAGE_EDIT_API(
            "https://api.example.com",
            "test-key",
            EditRequest(prompt="field test", model="gpt-image-2"),
            sources,
        )
    )

    assert len(entries) == 1
    assert session.requested_url == "https://api.example.com/v1/images/edits"
    fields = [(options["name"], options.get("filename")) for options, _headers, _value in session.data._fields]
    image_fields = [field for field in fields if field[0] == "image[]"]
    assert image_fields == [("image[]", "first.png"), ("image[]", "second.png")]
    assert ("image", "first.png") not in fields


def test_edit_source_temp_path_is_cleaned_after_success(client, monkeypatch):
    seen: dict[str, list[Path]] = {}

    async def fake_edit_api(
        api_url,
        api_key,
        payload,
        image_sources,
        api_preset_name=None,
        progress=None,
        socks5_proxy=None,
    ):
        assert len(image_sources) == 2
        seen["paths"] = [source.temp_path for source in image_sources]
        for source in image_sources:
            assert source.temp_path.exists()
            assert source.temp_path.read_bytes() == PNG_BYTES
        image_id = storage.generate_image_id()
        filename = f"{image_id}.png"
        entry = await storage.add_to_gallery_async(
            image_bytes=PNG_BYTES,
            image_id=image_id,
            prompt=payload.prompt,
            size=payload.size,
            filename=filename,
            metadata={"api_path": "/v1/images/edits", "api_preset_name": api_preset_name},
        )
        return [entry]

    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", fake_edit_api)

    edit = client.post(
        "/api/edits",
        data={
            "prompt": "cleanup source",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
        files=[
            ("image", ("input-1.png", PNG_BYTES, "image/png")),
            ("image", ("input-2.png", PNG_BYTES, "image/png")),
        ],
    )

    assert edit.status_code == 202
    assert _wait_for_job(client, edit.json()["job_id"])["status"] == "success"
    assert "paths" in seen

    deadline = time.time() + 5
    while time.time() < deadline:
        if all(not path.exists() for path in seen["paths"]) and jobs.get_pending_edit_source_bytes() == 0:
            break
        time.sleep(0.05)

    assert all(not path.exists() for path in seen["paths"])
    assert jobs.get_pending_edit_source_bytes() == 0


def test_cancelled_edit_job_cleans_temp_source(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    config.MAX_ACTIVE_GENERATE_JOBS = 1
    config.MAX_QUEUED_GENERATE_JOBS = 20
    seen: dict[str, Path] = {}
    started = threading.Event()
    release_event = threading.Event()

    async def blocking_edit_api(
        api_url,
        api_key,
        payload,
        image_sources,
        api_preset_name=None,
        progress=None,
        socks5_proxy=None,
    ):
        assert len(image_sources) == 1
        source_path = image_sources[0].temp_path
        seen["path"] = source_path
        assert source_path.exists()
        started.set()
        await asyncio.to_thread(release_event.wait)
        raise AssertionError("cancelled edit should not finish upstream call")

    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", blocking_edit_api)

    with TestClient(backend_main.app) as test_client:
        edit = test_client.post(
            "/api/edits",
            data={
                "prompt": "cancel source",
                "model": "gpt-image-2",
                "n": 1,
                "quality": "auto",
                "output_format": "png",
            },
            files={"image": ("input.png", PNG_BYTES, "image/png")},
        )

        assert edit.status_code == 202
        assert started.wait(timeout=5)
        assert jobs.get_pending_edit_source_bytes() == len(PNG_BYTES)

        cancelled = test_client.delete(f"/api/generate/{edit.json()['job_id']}")
        assert cancelled.status_code == 200
        release_event.set()

        deadline = time.time() + 5
        while time.time() < deadline:
            if not seen["path"].exists() and jobs.get_pending_edit_source_bytes() == 0:
                break
            time.sleep(0.05)

        job = test_client.get(f"/api/generate/{edit.json()['job_id']}").json()
        assert job["status"] == "cancelled"
        assert job["stage"] == "cancelled"
        assert not seen["path"].exists()
        assert jobs.get_pending_edit_source_bytes() == 0


def test_edit_queue_capacity_uses_pending_source_bytes(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    config.MAX_ACTIVE_GENERATE_JOBS = 1
    config.MAX_QUEUED_GENERATE_JOBS = 20
    config.MAX_PENDING_EDIT_SOURCE_MB = 1
    release_event = threading.Event()
    large_png = PNG_BYTES + (b"\0" * (600 * 1024))

    async def blocking_edit_api(
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
        await asyncio.to_thread(release_event.wait)
        image_id = storage.generate_image_id()
        filename = f"{image_id}.png"
        entry = await storage.add_to_gallery_async(
            image_bytes=PNG_BYTES,
            image_id=image_id,
            prompt=payload.prompt,
            size=payload.size,
            filename=filename,
            metadata={"api_path": "/v1/images/edits", "api_preset_name": api_preset_name},
        )
        return [entry]

    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", blocking_edit_api)

    with TestClient(backend_main.app) as test_client:
        first = test_client.post(
            "/api/edits",
            data={
                "prompt": "first large source",
                "model": "gpt-image-2",
                "n": 1,
                "quality": "auto",
                "output_format": "png",
            },
            files={"image": ("input.png", large_png, "image/png")},
        )
        second = test_client.post(
            "/api/edits",
            data={
                "prompt": "second large source",
                "model": "gpt-image-2",
                "n": 1,
                "quality": "auto",
                "output_format": "png",
            },
            files={"image": ("input.png", large_png, "image/png")},
        )

        assert first.status_code == 202
        assert second.status_code == 429
        assert second.json()["detail"] == "Edit source queue is full"

        release_event.set()
        assert _wait_for_job(test_client, first.json()["job_id"])["status"] == "success"
        assert jobs.get_pending_edit_source_bytes() == 0


def test_edit_queue_capacity_counts_multiple_source_bytes(tmp_path):
    _configure_runtime(tmp_path)
    config.MAX_PENDING_EDIT_SOURCE_MB = 1
    large_png = PNG_BYTES + (b"\0" * (600 * 1024))

    with TestClient(backend_main.app) as test_client:
        edit = test_client.post(
            "/api/edits",
            data={
                "prompt": "too much combined source data",
                "model": "gpt-image-2",
                "n": 1,
                "quality": "auto",
                "output_format": "png",
            },
            files=[
                ("image", ("first.png", large_png, "image/png")),
                ("image", ("second.png", large_png, "image/png")),
            ],
        )

        assert edit.status_code == 429
        assert edit.json()["detail"] == "Edit source queue is full"
        assert jobs.get_pending_edit_source_bytes() == 0
        assert not list((tmp_path / "data" / "edit-sources").glob("edit-source-*"))


def test_gallery_image_download_and_zip(client):
    entry = _fake_gallery_entry("gallery-zip", "zip me", "1024x1024", "gallery-zip.png")
    assert entry.bytes == len(PNG_BYTES)
    assert entry.thumbnail_filename
    assert entry.thumbnail_url == "/api/thumb/gallery-zip.png"

    gallery = client.get("/api/gallery")
    assert gallery.status_code == 200
    gallery_data = gallery.json()
    assert gallery_data["images"][0]["bytes"] == len(PNG_BYTES)
    assert gallery_data["images"][0]["thumbnail_url"] == "/api/thumb/gallery-zip.png"
    assert gallery_data["total_bytes"] == 0

    gallery_stats = client.get("/api/gallery?include_total_bytes=true")
    assert gallery_stats.status_code == 200
    assert gallery_stats.json()["total_bytes"] == len(PNG_BYTES)

    detail = client.get("/api/gallery/gallery-zip")
    assert detail.status_code == 200
    assert detail.json()["id"] == "gallery-zip"
    assert detail.json()["filename"] == "gallery-zip.png"

    missing_detail = client.get("/api/gallery/missing")
    assert missing_detail.status_code == 404
    assert missing_detail.json()["detail"] == "Gallery entry not found"

    image = client.get("/api/image/gallery-zip.png")
    assert image.status_code == 200
    assert image.headers["cache-control"].startswith("public")

    thumb = client.get("/api/thumb/gallery-zip.png")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"].startswith("image/webp")
    assert thumb.headers["cache-control"].startswith("public")

    download = client.get("/api/download/gallery-zip.png")
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]

    archive = client.get("/api/download-all")
    assert archive.status_code == 200
    assert archive.headers["content-type"].startswith("application/zip")
    assert "attachment" in archive.headers["content-disposition"]
    assert archive.headers.get("x-content-type-options") == "nosniff"
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
        assert "metadata.json" in zf.namelist()
        assert "images/gallery-zip.png" in zf.namelist()
        metadata = json.loads(zf.read("metadata.json"))
        assert metadata["images"]
        assert "thumbnail_filename" not in metadata["images"][0]
        assert "thumbnail_url" not in metadata["images"][0]
        assert metadata["images"][0]["sha256"]


def test_download_all_deduplicates_shared_filenames(client):
    _fake_gallery_entry("dup-1", "first", "1024x1024", "dup.png")
    storage.add_to_gallery_sync(
        image_id="dup-2",
        prompt="second",
        size="1024x1024",
        filename="dup.png",
        metadata={"model": "gpt-image-2"},
    )

    gallery_stats = client.get("/api/gallery?include_total_bytes=true")
    assert gallery_stats.status_code == 200
    assert gallery_stats.json()["total_bytes"] == len(PNG_BYTES)

    archive = client.get("/api/download-all")
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
        image_names = [n for n in zf.namelist() if n.startswith("images/")]
        assert len(image_names) == 2
        assert len(image_names) == len(set(image_names))
        assert "images/dup.png" in image_names
        assert "images/dup_1.png" in image_names


def test_gallery_export_job_reports_progress_and_downloads_zip(client):
    _fake_gallery_entry("export-job-1", "one", "1024x1024", "export-job-1.png")
    _fake_gallery_entry("export-job-2", "two", "1024x1024", "export-job-2.png")

    created = client.post("/api/gallery/export-jobs")
    assert created.status_code == 202
    job = created.json()
    assert job["status"] == "queued"
    assert job["progress"] == 0

    finished = _wait_for_gallery_export_job(client, job["job_id"])
    assert finished["status"] == "success"
    assert finished["progress"] == 100
    assert finished["bytes_total"] > 0
    assert finished["bytes_written"] == finished["bytes_total"]
    assert finished["download_url"].endswith(f"/{job['job_id']}/download")

    archive = client.get(finished["download_url"])
    assert archive.status_code == 200
    assert archive.headers["content-type"].startswith("application/zip")
    assert archive.headers["content-length"] == str(len(archive.content))
    assert archive.headers["x-gallery-requested-count"] == "2"
    assert archive.headers["x-gallery-exported-count"] == "2"
    assert archive.headers["x-gallery-missing-count"] == "0"
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
        assert "metadata.json" in zf.namelist()
        assert "images/export-job-1.png" in zf.namelist()
        assert "images/export-job-2.png" in zf.namelist()


def test_gallery_total_bytes_uses_sql_aggregate_without_disk_backfill(client, monkeypatch):
    storage._ensure_database()
    image_path = storage.safe_image_path("legacy-bytes.png")
    assert image_path is not None
    image_path.write_bytes(PNG_BYTES)

    storage.add_to_gallery_sync(
        image_id="legacy-bytes",
        prompt="legacy",
        size="1024x1024",
        filename="legacy-bytes.png",
        metadata={"model": "gpt-image-2"},
    )

    stat_calls: list[str] = []
    real_stat = storage._stat_image_bytes

    def tracked_stat(filename: str):
        stat_calls.append(filename)
        return real_stat(filename)

    monkeypatch.setattr(storage, "_stat_image_bytes", tracked_stat)

    with storage._connect() as conn:
        row = conn.execute(
            "SELECT bytes FROM gallery_entries WHERE id = ?",
            ("legacy-bytes",),
        ).fetchone()
        assert row["bytes"] is None

    gallery = client.get("/api/gallery")
    assert gallery.status_code == 200
    assert gallery.json()["total_bytes"] == 0
    assert stat_calls == []

    gallery_stats_before_backfill = client.get("/api/gallery?include_total_bytes=true")
    assert gallery_stats_before_backfill.status_code == 200
    assert gallery_stats_before_backfill.json()["total_bytes"] == 0
    assert stat_calls == []

    with storage._connect() as conn:
        row = conn.execute(
            "SELECT bytes FROM gallery_entries WHERE id = ?",
            ("legacy-bytes",),
        ).fetchone()
        assert row["bytes"] is None

    updated = storage.backfill_missing_gallery_bytes()
    assert updated == 1
    assert stat_calls == ["legacy-bytes.png"]

    gallery_stats = client.get("/api/gallery?include_total_bytes=true")
    assert gallery_stats.status_code == 200
    assert gallery_stats.json()["total_bytes"] == len(PNG_BYTES)

    with storage._connect() as conn:
        row = conn.execute(
            "SELECT bytes FROM gallery_entries WHERE id = ?",
            ("legacy-bytes",),
        ).fetchone()
        assert row["bytes"] == len(PNG_BYTES)


def test_gallery_prompt_search_uses_fts_and_short_like_fallback(client):
    _fake_gallery_entry("fts-1", "alpha needle beta", "1024x1024", "fts-1.png")
    _fake_gallery_entry("fts-2", "unrelated prompt", "1024x1024", "fts-2.png")

    fts = client.get("/api/gallery", params={"prompt": "needle"})
    assert fts.status_code == 200
    assert [image["id"] for image in fts.json()["images"]] == ["fts-1"]

    with storage._connect() as conn:
        rows = conn.execute(
            """
            SELECT rowid
            FROM gallery_entries_fts
            WHERE gallery_entries_fts MATCH ?
            """,
            ('"needle"',),
        ).fetchall()
        assert rows

    short_fallback = client.get("/api/gallery", params={"prompt": "al"})
    assert short_fallback.status_code == 200
    assert [image["id"] for image in short_fallback.json()["images"]] == ["fts-1"]


def test_gallery_batch_delete_only_selected_entries(client):
    _fake_gallery_entry("batch-delete-1", "one", "1024x1024", "batch-delete-1.png")
    _fake_gallery_entry("batch-delete-2", "two", "1024x1024", "batch-delete-2.png")
    _fake_gallery_entry("batch-delete-3", "three", "1024x1024", "batch-delete-3.png")

    resp = client.post(
        "/api/gallery/batch/delete",
        json={"ids": ["batch-delete-1", "batch-delete-3"]},
    )

    assert resp.status_code == 200
    assert resp.json()["count"] == 2
    assert resp.json()["file_count"] == 2
    assert resp.json()["requested_count"] == 2
    assert resp.json()["updated_count"] == 2
    assert resp.json()["missing_count"] == 0
    assert resp.json()["missing_ids"] == []
    assert storage.get_gallery_entry("batch-delete-1") is None
    assert storage.get_gallery_entry("batch-delete-2") is not None
    assert storage.get_gallery_entry("batch-delete-3") is None
    assert not storage.safe_image_path("batch-delete-1.png").exists()
    assert storage.safe_image_path("batch-delete-2.png").exists()


def test_gallery_batch_delete_preserves_shared_filename(client):
    _fake_gallery_entry("shared-1", "one", "1024x1024", "shared.png")
    storage.add_to_gallery_sync(
        image_id="shared-2",
        prompt="two",
        size="1024x1024",
        filename="shared.png",
        metadata={"model": "gpt-image-2"},
    )

    first = client.post("/api/gallery/batch/delete", json={"ids": ["shared-1"]})
    assert first.status_code == 200
    assert first.json()["count"] == 1
    assert first.json()["file_count"] == 0
    assert storage.safe_image_path("shared.png") is not None

    second = client.post("/api/gallery/batch/delete", json={"ids": ["shared-2"]})
    assert second.status_code == 200
    assert second.json()["file_count"] == 1
    assert not storage.safe_image_path("shared.png").exists()


def test_delete_all_gallery_commits_rows_when_file_delete_fails(client, monkeypatch, caplog):
    _fake_gallery_entry("delete-all-1", "one", "1024x1024", "delete-all-1.png")
    _fake_gallery_entry("delete-all-2", "two", "1024x1024", "delete-all-2.png")
    original_delete = storage._delete_image_unlocked

    def flaky_delete(filename: str):
        if filename == "delete-all-1.png":
            raise OSError("locked")
        return original_delete(filename)

    monkeypatch.setattr(storage, "_delete_image_unlocked", flaky_delete)

    with caplog.at_level(logging.WARNING):
        total, deleted_files = storage.delete_all_gallery_images()

    assert total == 2
    assert deleted_files == 1
    assert storage.get_gallery_count() == 0
    assert storage.safe_image_path("delete-all-1.png").exists()
    assert not storage.safe_image_path("delete-all-2.png").exists()
    assert "Failed to delete gallery image file delete-all-1.png" in caplog.text


def test_gallery_batch_favorite_and_download(client):
    _fake_gallery_entry("batch-fav-1", "one", "1024x1024", "batch-fav-1.png")
    _fake_gallery_entry("batch-fav-2", "two", "1024x1024", "batch-fav-2.png")
    _fake_gallery_entry("batch-fav-3", "three", "1024x1024", "batch-fav-3.png")

    favorite = client.patch(
        "/api/gallery/batch/favorite",
        json={"ids": ["batch-fav-1", "batch-fav-3"], "favorite": True},
    )
    assert favorite.status_code == 200
    assert favorite.json()["count"] == 2
    assert storage.get_gallery_entry("batch-fav-1").favorite is True
    assert storage.get_gallery_entry("batch-fav-2").favorite is False
    assert storage.get_gallery_entry("batch-fav-3").favorite is True

    archive = client.post(
        "/api/gallery/batch/download",
        json={"ids": ["batch-fav-1", "batch-fav-3"]},
    )
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
        assert "images/batch-fav-1.png" in zf.namelist()
        assert "images/batch-fav-2.png" not in zf.namelist()
        assert "images/batch-fav-3.png" in zf.namelist()

    unfavorite = client.patch(
        "/api/gallery/batch/favorite",
        json={"ids": ["batch-fav-1", "batch-fav-3"], "favorite": False},
    )
    assert unfavorite.status_code == 200
    assert storage.get_gallery_entry("batch-fav-1").favorite is False
    assert storage.get_gallery_entry("batch-fav-3").favorite is False


def test_gallery_batch_operations_report_partial_missing(client):
    _fake_gallery_entry("batch-partial-1", "one", "1024x1024", "batch-partial-1.png")

    favorite = client.patch(
        "/api/gallery/batch/favorite",
        json={"ids": ["batch-partial-1", "batch-partial-missing"], "favorite": True},
    )
    assert favorite.status_code == 200
    assert favorite.json()["count"] == 1
    assert favorite.json()["requested_count"] == 2
    assert favorite.json()["updated_count"] == 1
    assert favorite.json()["missing_count"] == 1
    assert favorite.json()["missing_ids"] == ["batch-partial-missing"]

    delete = client.post(
        "/api/gallery/batch/delete",
        json={"ids": ["batch-partial-1", "batch-partial-missing"]},
    )
    assert delete.status_code == 200
    assert delete.json()["count"] == 1
    assert delete.json()["requested_count"] == 2
    assert delete.json()["updated_count"] == 1
    assert delete.json()["missing_count"] == 1
    assert delete.json()["missing_ids"] == ["batch-partial-missing"]


def test_gallery_batch_download_records_skipped_entries(client):
    _fake_gallery_entry("batch-download-1", "one", "1024x1024", "batch-download-1.png")
    _fake_gallery_entry("batch-download-missing-file", "two", "1024x1024", "batch-download-missing-file.png")
    storage.safe_image_path("batch-download-missing-file.png").unlink()

    archive = client.post(
        "/api/gallery/batch/download",
        json={"ids": ["batch-download-1", "batch-download-missing-file", "batch-download-missing-row"]},
    )
    assert archive.status_code == 200
    assert archive.headers["x-gallery-requested-count"] == "3"
    assert archive.headers["x-gallery-exported-count"] == "1"
    assert archive.headers["x-gallery-missing-count"] == "2"
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
        assert "images/batch-download-1.png" in zf.namelist()
        assert "images/batch-download-missing-file.png" not in zf.namelist()
        metadata = json.loads(zf.read("metadata.json"))
        assert metadata["skipped"] == [
            {"id": "batch-download-missing-row", "reason": "gallery_entry_missing"},
            {
                "id": "batch-download-missing-file",
                "filename": "batch-download-missing-file.png",
                "reason": "image_file_missing",
            },
        ]


def test_import_archive(client):
    resp = _post_import_archive(client, _import_archive_bytes())
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    assert resp.json()["imported"] == 1

    imported = storage.get_gallery_entry("import-1")
    assert imported is not None
    assert imported.bytes == len(PNG_BYTES)
    assert imported.thumbnail_filename
    assert imported.thumbnail_url == "/api/thumb/import-1.png"


def test_import_gallery_entries_dedupes_existing_rows_at_commit(client):
    _fake_gallery_entry("import-1", "existing", "1024x1024", "import-1.png")

    imported_count = storage.import_gallery_entries(
        [
            (
                PNG_BYTES,
                {
                    "id": "import-1",
                    "prompt": "late import",
                    "size": "1024x1024",
                    "filename": "import-1.png",
                    "created_at": "2026-01-02T00:00:00Z",
                },
            )
        ]
    )

    assert imported_count == 1
    existing = storage.get_gallery_entry("import-1")
    assert existing.prompt == "existing"

    imported = next(
        entry for entry in storage.get_gallery() if entry.prompt == "late import"
    )
    assert imported.id != "import-1"
    assert imported.filename == "import-1_1.png"
    assert imported.thumbnail_filename is None
    assert imported.thumbnail_url == "/api/thumb/import-1_1.png"

    thumb = client.get("/api/thumb/import-1_1.png")
    assert thumb.status_code == 200


def test_thumbnail_endpoint_lazily_rebuilds_missing_file(client):
    entry = _fake_gallery_entry("lazy-thumb", "lazy", "1024x1024", "lazy-thumb.png")
    assert entry.thumbnail_filename
    thumbnail_path = storage.safe_thumbnail_path(entry.thumbnail_filename)
    assert thumbnail_path is not None
    thumbnail_path.unlink()

    resp = client.get("/api/thumb/lazy-thumb.png")

    assert resp.status_code == 200
    assert thumbnail_path.exists()


@pytest.mark.parametrize(
    ("archive_bytes", "expected_detail", "config_updates"),
    [
        (
            lambda: _import_archive_bytes(metadata=None),
            "metadata.json is required",
            {},
        ),
        (
            lambda: _import_archive_bytes(extra_files=2),
            "Import archive contains too many files",
            {"IMPORT_MAX_FILES": 2},
        ),
        (
            lambda: _import_archive_bytes(image_bytes=b"x" * 2048),
            "Imported image is too large",
            {"MAX_FILE_SIZE_MB": 0},
        ),
        (
            lambda: _import_archive_bytes(image_name="../evil.png"),
            "Import archive contains unsafe paths",
            {},
        ),
        (
            lambda: _import_archive_bytes(image_name="/evil.png"),
            "Import archive contains unsafe paths",
            {},
        ),
        (
            lambda: _import_archive_bytes(image_name="images\\evil.png"),
            "Import archive contains unsafe paths",
            {},
        ),
        (
            lambda: _import_archive_bytes(
                image_name="images/import-1.svg",
                image_bytes=b"<svg></svg>",
            ),
            "No importable images found",
            {},
        ),
    ],
)
def test_import_archive_rejects_invalid_content(
    client,
    archive_bytes,
    expected_detail,
    config_updates,
):
    for name, value in config_updates.items():
        setattr(config, name, value)

    resp = _post_import_archive(client, archive_bytes())

    assert resp.status_code == 400
    assert resp.json()["detail"] == expected_detail


def test_import_archive_rejects_uncompressed_size_limit(client):
    config.IMPORT_MAX_UNCOMPRESSED_MB = 0

    resp = _post_import_archive(client, _import_archive_bytes())

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Import archive uncompressed size exceeds limit"


def test_import_archive_rejects_large_metadata(client):
    config.IMPORT_MAX_METADATA_BYTES = 10

    resp = _post_import_archive(client, _import_archive_bytes())

    assert resp.status_code == 400
    assert resp.json()["detail"] == "metadata.json is too large"


def test_import_archive_rejects_uploaded_archive_size_limit(client):
    config.IMPORT_ARCHIVE_MAX_MB = 0

    resp = _post_import_archive(client, _import_archive_bytes())

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Uploaded archive is too large"


def test_import_archive_rejects_high_compression_ratio(client):
    config.IMPORT_MAX_COMPRESSION_RATIO = 1

    resp = _post_import_archive(client, _import_archive_bytes(image_bytes=b"0" * 1024))

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Import archive compression ratio exceeds limit"


def test_upload_rejects_svg(client):
    resp = client.post(
        "/api/edits",
        data={
            "prompt": "no svg",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
        files={"image": ("input.svg", b"<svg></svg>", "image/svg+xml")},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Upload must be an image file."




def test_upload_rejects_mismatched_png_content(client):
    resp = client.post(
        "/api/edits",
        data={
            "prompt": "fake png",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
        files={"image": ("input.png", b"<svg></svg>", "image/png")},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Image data must be a supported raster image format"


def test_import_archive_skips_mismatched_png_content(client):
    resp = _post_import_archive(
        client,
        _import_archive_bytes(image_name="images/fake.png", image_bytes=b"<svg></svg>"),
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "No importable images found"


def test_safe_image_paths_reject_traversal(client):
    assert storage.safe_image_path("gallery-zip.png") is not None
    assert storage.safe_image_path("../secret.png") is None
    assert storage.safe_image_path("nested/secret.png") is None

    image = client.get("/api/image/..%2Fsecret.png")
    thumb = client.get("/api/thumb/..%2Fsecret.png")
    download = client.get("/api/download/..%2Fsecret.png")

    assert image.status_code == 404
    assert thumb.status_code == 404
    assert download.status_code == 404


def test_download_all_skips_polluted_gallery_filename(client):
    _fake_gallery_entry("safe", "safe", "1024x1024", "safe.png")
    storage.add_to_gallery_sync(
        image_id="polluted",
        prompt="polluted",
        size="1024x1024",
        filename="../secret.png",
        image_bytes=None,
    )

    archive = client.get("/api/download-all")

    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
        assert "images/safe.png" in zf.namelist()
        assert all("secret" not in name for name in zf.namelist())


def test_edit_from_gallery_rejects_polluted_filename(client):
    storage.add_to_gallery_sync(
        image_id="polluted",
        prompt="polluted",
        size="1024x1024",
        filename="../secret.png",
        image_bytes=None,
    )

    resp = client.post(
        "/api/edits/from-gallery/polluted",
        data={
            "prompt": "edit polluted",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Gallery image file not found"


def test_generate_queue_capacity_and_concurrency_limit(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    config.MAX_ACTIVE_GENERATE_JOBS = 1
    config.MAX_QUEUED_GENERATE_JOBS = 1
    active_calls = 0
    max_active_calls = 0
    release_event = threading.Event()

    async def blocking_generation_api(*args, **kwargs):
        nonlocal active_calls, max_active_calls
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        try:
            await asyncio.to_thread(release_event.wait)
        finally:
            active_calls -= 1
        payload = args[3]
        api_path = args[2]
        api_preset_name = args[4]
        image_id = storage.generate_image_id()
        filename = f"{image_id}.png"
        entry = await storage.add_to_gallery_async(
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

    monkeypatch.setattr(backend_main.proxy, "call_image_generation_api", blocking_generation_api)

    with TestClient(backend_main.app) as client:
        first = client.post(
            "/api/generate",
            json={"prompt": "one", "model": "gpt-image-2"},
        )
        second = client.post(
            "/api/generate",
            json={"prompt": "two", "model": "gpt-image-2"},
        )
        third = client.post(
            "/api/generate",
            json={"prompt": "three", "model": "gpt-image-2"},
        )

        assert first.status_code == 202
        assert second.status_code == 202
        assert third.status_code == 429
        assert third.json()["detail"] == "Generation job queue is full"

        release_event.set()
        assert _wait_for_job(client, first.json()["job_id"])["status"] == "success"
        assert _wait_for_job(client, second.json()["job_id"])["status"] == "success"

    assert max_active_calls == 1


def test_batch_generate_counts_as_one_public_queue_slot(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    config.MAX_ACTIVE_GENERATE_JOBS = 1
    config.MAX_QUEUED_GENERATE_JOBS = 1
    calls = []
    calls_lock = threading.Lock()
    batch_started = threading.Event()
    release_event = threading.Event()

    async def blocking_generation_api(
        api_url,
        api_key,
        api_path,
        payload,
        api_preset_name=None,
        progress=None,
        socks5_proxy=None,
    ):
        with calls_lock:
            calls.append(payload)
            batch_calls = [call for call in calls if call.prompt == "batch"]
            if len(batch_calls) == 3:
                batch_started.set()
        if payload.prompt == "batch":
            await asyncio.to_thread(release_event.wait)
        return [
            await _add_generated_gallery_entry(
                payload,
                api_path,
                api_preset_name,
            )
        ]

    monkeypatch.setattr(backend_main.proxy, "call_image_generation_api", blocking_generation_api)

    with TestClient(backend_main.app) as client:
        first = client.post(
            "/api/generate",
            json={"prompt": "batch", "model": "gpt-image-2", "n": 3},
        )
        assert first.status_code == 202
        try:
            assert batch_started.wait(2)
            second = client.post(
                "/api/generate",
                json={"prompt": "second", "model": "gpt-image-2"},
            )
            third = client.post(
                "/api/generate",
                json={"prompt": "third", "model": "gpt-image-2"},
            )

            assert second.status_code == 202
            assert third.status_code == 429
            assert third.json()["detail"] == "Generation job queue is full"

            active_jobs = client.get("/api/generate/jobs")
            assert active_jobs.status_code == 200
            assert len(active_jobs.json()) == 2
            assert {job["job_id"] for job in active_jobs.json()} == {
                first.json()["job_id"],
                second.json()["job_id"],
            }
        finally:
            release_event.set()

        assert _wait_for_job(client, first.json()["job_id"])["status"] == "success"
        assert _wait_for_job(client, second.json()["job_id"])["status"] == "success"

    with calls_lock:
        batch_calls = [call for call in calls if call.prompt == "batch"]
    assert len(batch_calls) == 3
    assert all(payload.n == 1 for payload in batch_calls)


def test_edit_jobs_share_queue_capacity(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    config.MAX_ACTIVE_GENERATE_JOBS = 1
    config.MAX_QUEUED_GENERATE_JOBS = 1
    release_event = threading.Event()

    async def blocking_generation_api(*args, **kwargs):
        await asyncio.to_thread(release_event.wait)
        payload = args[3]
        image_id = storage.generate_image_id()
        filename = f"{image_id}.png"
        entry = await storage.add_to_gallery_async(
            image_bytes=PNG_BYTES,
            image_id=image_id,
            prompt=payload.prompt,
            size=payload.size,
            filename=filename,
            metadata={"api_path": args[2], "api_preset_name": args[4]},
        )
        return [entry]

    async def blocking_edit_api(*args, **kwargs):
        await asyncio.to_thread(release_event.wait)
        payload = args[2]
        assert args[3][0].temp_path.exists()
        image_id = storage.generate_image_id()
        filename = f"{image_id}.png"
        entry = await storage.add_to_gallery_async(
            image_bytes=PNG_BYTES,
            image_id=image_id,
            prompt=payload.prompt,
            size=payload.size,
            filename=filename,
            metadata={"api_path": "/v1/images/edits", "api_preset_name": args[4]},
        )
        return [entry]

    monkeypatch.setattr(backend_main.proxy, "call_image_generation_api", blocking_generation_api)
    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", blocking_edit_api)

    with TestClient(backend_main.app) as client:
        generate = client.post(
            "/api/generate",
            json={"prompt": "one", "model": "gpt-image-2"},
        )
        edit = client.post(
            "/api/edits",
            data={
                "prompt": "two",
                "model": "gpt-image-2",
                "n": 1,
                "quality": "auto",
                "output_format": "png",
            },
            files={"image": ("input.png", PNG_BYTES, "image/png")},
        )
        overflow = client.post(
            "/api/edits",
            data={
                "prompt": "three",
                "model": "gpt-image-2",
                "n": 1,
                "quality": "auto",
                "output_format": "png",
            },
            files={"image": ("input.png", PNG_BYTES, "image/png")},
        )

        assert generate.status_code == 202
        assert edit.status_code == 202
        assert overflow.status_code == 429
        release_event.set()
        assert _wait_for_job(client, generate.json()["job_id"])["status"] == "success"
        assert _wait_for_job(client, edit.json()["job_id"])["status"] == "success"



def test_image_url_download_disables_redirects_and_validates_redirect_target(client):
    session = _FakeSession(
        [
            _FakeResponse(302, {"Location": "http://127.0.0.1/secret.png"}),
        ]
    )

    with pytest.raises(ValueError):
        asyncio.run(_download_with_fake_session(session, "https://example.com/image.png"))

    assert session.requested_urls == ["https://example.com/image.png"]
    assert session.allow_redirects_values == [False]


def test_image_url_download_rejects_large_content_length(client):
    config.MAX_FILE_SIZE_MB = 0
    session = _FakeSession(
        [
            _FakeResponse(200, {"Content-Length": "1"}, [b"x"]),
        ]
    )

    with pytest.raises(Exception, match="Image too large"):
        asyncio.run(_download_with_fake_session(session, "https://example.com/image.png"))

    assert session.allow_redirects_values == [False]


def test_image_url_download_rejects_stream_over_limit(client):
    config.MAX_FILE_SIZE_MB = 0
    session = _FakeSession(
        [
            _FakeResponse(200, {}, [b"x"]),
        ]
    )

    with pytest.raises(Exception, match="Image too large"):
        asyncio.run(_download_with_fake_session(session, "https://example.com/image.png"))


def test_upstream_json_response_rejects_stream_over_limit(tmp_path):
    _configure_runtime(tmp_path)
    from backend.app.integrations import upstream_client

    config.MAX_UPSTREAM_JSON_MB = 1
    too_large_json = b'{"data":"' + (b"x" * (1024 * 1024)) + b'"}'
    resp = _FakeResponse(
        200,
        {"Content-Type": "application/json"},
        [too_large_json],
    )

    with pytest.raises(
        upstream_client.UpstreamApiError,
        match="Upstream JSON response too large",
    ):
        asyncio.run(
            upstream_client.parse_upstream_json_response(
                resp,
                "/v1/images/generations",
                None,
            )
        )


def test_image_url_download_rejects_private_peer_ip(client):
    session = _FakeSession(
        [
            _FakeResponse(200, {}, [PNG_BYTES], peer_ip="127.0.0.1"),
        ]
    )

    with pytest.raises(ValueError, match="private/internal IP"):
        asyncio.run(_download_with_fake_session(session, "https://example.com/image.png"))


def test_upstream_returned_image_url_download_stays_direct(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    import importlib
    from backend.app.integrations import upstream_client as upstream_client_module
    from backend.app.schemas.models import GenerateRequest

    upstream_client = importlib.reload(upstream_client_module)
    created_sessions: list[str] = []
    session_events: list[tuple[str, str, str]] = []

    class FakeJsonResponse:
        status = 200
        headers = {"Content-Type": "application/json"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return json.dumps(
                {"data": [{"url": "https://example.com/generated.png"}]}
            )

    class FakeApiSession:
        def __init__(self, proxy_url: str):
            self.proxy_url = proxy_url

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, **kwargs):
            session_events.append(("post", self.proxy_url, url))
            return FakeJsonResponse()

    class FakeDownloadSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url, **kwargs):
            session_events.append(("get", "", url))
            return _FakeResponse(200, {}, [PNG_BYTES], peer_ip="93.184.216.34")

    class FakePool:
        def get(self, timeout_kind="upstream", socks5_proxy=None):
            proxy_url = socks5_proxy or ""
            created_sessions.append(proxy_url)
            if proxy_url:
                return FakeApiSession(proxy_url)
            return FakeDownloadSession()

    monkeypatch.setattr(upstream_client, "get_pool", lambda: FakePool())

    entries = asyncio.run(
        upstream_client.call_image_generation_api(
            "https://api.example.com",
            "key",
            "/v1/images/generations",
            GenerateRequest(prompt="url result"),
            socks5_proxy="socks5://127.0.0.1:1080",
        )
    )

    assert entries
    assert created_sessions == ["socks5://127.0.0.1:1080", ""]
    assert session_events[0] == (
        "post",
        "socks5://127.0.0.1:1080",
        "https://api.example.com/v1/images/generations",
    )
    assert session_events[1] == ("get", "", "https://example.com/generated.png")


def test_chat_completions_sse_markdown_image_url_is_saved(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    import importlib
    from backend.app.integrations import upstream_client as upstream_client_module
    from backend.app.schemas.models import GenerateRequest

    upstream_client = importlib.reload(upstream_client_module)
    session_events: list[tuple[str, str, dict | None]] = []

    class FakeSseResponse:
        status = 200
        headers = {"Content-Type": "text/event-stream"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return (
                'data: {"choices":[{"index":0,"delta":{"role":"assistant",'
                '"reasoning_content":"image generating"}}]}\n\n'
                'data: {"choices":[{"index":0,"delta":{"role":"assistant",'
                '"content":"![image](https://example.com/generated.jpg)"}}]}\n\n'
                "data: [DONE]\n\n"
            )

    class FakeApiSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, **kwargs):
            session_events.append(("post", url, kwargs.get("json")))
            return FakeSseResponse()

    class FakeDownloadSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url, **kwargs):
            session_events.append(("get", url, None))
            return _FakeResponse(200, {}, [JPEG_BYTES], peer_ip="93.184.216.34")

    class FakeCombinedSession(FakeApiSession, FakeDownloadSession):
        pass

    fake_session = FakeCombinedSession()

    class FakePool:
        def get(self, timeout_kind="upstream", socks5_proxy=None):
            return fake_session

    monkeypatch.setattr(upstream_client, "get_pool", lambda: FakePool())

    entries = asyncio.run(
        upstream_client.call_image_generation_api(
            "https://api.example.com",
            "key",
            "/v1/chat/completions",
            GenerateRequest(
                prompt="draw a red square",
                model="grok-imagine-image-lite",
            ),
        )
    )

    assert entries
    assert entries[0].filename.endswith(".jpg")
    assert entries[0].output_format == "jpeg"
    assert session_events[0] == (
        "post",
        "https://api.example.com/v1/chat/completions",
        {
            "model": "grok-imagine-image-lite",
            "messages": [{"role": "user", "content": "draw a red square"}],
            "stream": False,
        },
    )
    assert session_events[1] == ("get", "https://example.com/generated.jpg", None)


def test_running_progress_persists_only_terminal_states(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    upserted: list[dict] = []
    real_upsert = storage.upsert_generate_job

    def tracking_upsert(job):
        upserted.append(job.copy())
        return real_upsert(job)

    async def noisy_generation_api(
        api_url,
        api_key,
        api_path,
        payload,
        api_preset_name=None,
        progress=None,
        socks5_proxy=None,
    ):
        if progress:
            for index in range(5):
                progress(f"stage_{index}", f"Stage {index}")
        image_id = storage.generate_image_id()
        filename = f"{image_id}.png"
        entry = await storage.add_to_gallery_async(
            image_bytes=PNG_BYTES,
            image_id=image_id,
            prompt=payload.prompt,
            size=payload.size,
            filename=filename,
            metadata={"api_path": api_path, "api_preset_name": api_preset_name},
        )
        return [entry]

    monkeypatch.setattr(storage, "upsert_generate_job", tracking_upsert)
    monkeypatch.setattr(backend_main.proxy, "call_image_generation_api", noisy_generation_api)

    with TestClient(backend_main.app) as client:
        resp = client.post("/api/generate", json={"prompt": "noisy", "model": "gpt-image-2"})
        assert resp.status_code == 202
        job = _wait_for_job(client, resp.json()["job_id"])

    assert job["status"] == "success"
    assert [item["status"] for item in upserted].count("queued") == 1
    assert [item["status"] for item in upserted].count("success") == 1
    running_upserts = [item for item in upserted if item["status"] == "running"]
    assert len(running_upserts) <= 2
    assert any(item.get("stage") == "starting_generation" for item in running_upserts)


def test_generate_jobs_list_broadcast_debounces_without_db_reads(client, monkeypatch):
    list_calls = []

    def tracking_list_generate_jobs(*args, **kwargs):
        list_calls.append((args, kwargs))
        return []

    monkeypatch.setattr(storage, "list_generate_jobs", tracking_list_generate_jobs)

    async def run_updates():
        queue: asyncio.Queue = asyncio.Queue(maxsize=20)
        subscribers = jobs.get_jobs_subscribers()
        subscribers.add(queue)
        try:
            for index in range(3):
                jobs.store_generate_job(
                    "job-memory-broadcast",
                    {
                        "status": "running",
                        "stage": f"stage_{index}",
                        "message": f"Stage {index}",
                        "operation": "generation",
                        "prompt": "memory broadcast",
                        "size": "1024x1024",
                    },
                    persist=False,
                )

            await asyncio.sleep(0.45)

            events = []
            while not queue.empty():
                events.append(queue.get_nowait())
            return events
        finally:
            subscribers.discard(queue)

    events = asyncio.run(run_updates())

    assert list_calls == []
    assert len(events) == 1
    assert events[0]["event"] == "jobs"
    assert events[0]["data"][0]["job_id"] == "job-memory-broadcast"
    assert events[0]["data"][0]["stage"] == "stage_2"


def test_generate_jobs_history_supports_offset_pagination(client):
    for index in range(4):
        storage.upsert_generate_job(
            {
                "job_id": f"history-{index}",
                "status": "success",
                "operation": "generation",
                "prompt": f"history prompt {index}",
                "size": "1024x1024",
                "created_at": f"2026-01-01T00:00:0{index}+00:00",
                "updated_at": f"2026-01-01T00:00:0{index}+00:00",
                "completed_at": f"2026-01-01T00:00:0{index}+00:00",
            }
        )

    resp = client.get("/api/generate/jobs?include_finished=true&limit=2&offset=1")

    assert resp.status_code == 200
    assert [job["job_id"] for job in resp.json()] == ["history-2", "history-1"]


def test_generate_jobs_history_failed_only_filters_error_statuses(client):
    for job_id, status in [
        ("history-success", "success"),
        ("history-cancelled", "cancelled"),
        ("history-error", "error"),
        ("history-upstream", "upstream_error"),
    ]:
        storage.upsert_generate_job(
            {
                "job_id": job_id,
                "status": status,
                "operation": "generation",
                "prompt": job_id,
                "size": "1024x1024",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "completed_at": "2026-01-01T00:00:00+00:00",
                "error": "failed" if status in {"error", "upstream_error"} else None,
            }
        )

    resp = client.get("/api/generate/jobs?include_finished=true&failed_only=true")

    assert resp.status_code == 200
    assert {job["job_id"] for job in resp.json()} == {"history-error", "history-upstream"}


def test_validation_422_and_global_500(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    with TestClient(backend_main.app, raise_server_exceptions=False) as client:
        bad = client.post(
            "/api/generate",
            json={"prompt": "x", "size": "1025x1025"},
        )
        assert bad.status_code == 422

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(backend_main.storage, "get_gallery_page", boom)
        broken = client.get("/api/gallery")
        assert broken.status_code == 500
        assert broken.json()["detail"] == "Internal Server Error"


def test_responses_request_uses_payload_model_with_default_fallback(tmp_path):
    _configure_runtime(tmp_path)
    from backend.app.integrations import upstream_client as upstream_client_module
    from backend.app.schemas.models import GenerateRequest

    config.DEFAULT_RESPONSES_MODEL = "gpt-5.4"
    payload = GenerateRequest(prompt="hello", model="gpt-image-2", size="1024x1024")
    request_data = upstream_client_module.build_responses_request_data(payload)
    assert request_data["model"] == "gpt-image-2"
    assert request_data["prompt"] == "hello"

    omitted = GenerateRequest(prompt="hello", model="", size="1024x1024")
    fallback = upstream_client_module.build_responses_request_data(omitted)
    assert fallback["model"] == "gpt-5.4"


def test_generate_uses_active_preset_default_model_when_model_is_omitted(client):
    settings = client.get("/api/settings").json()
    update = client.post(
        "/api/settings",
        json={
            "active_preset_id": settings["active_preset_id"],
            "preset_name": "Primary",
            "api_url": settings["api_url"],
            "api_key": "new-key",
            "api_path": settings["api_path"],
            "default_model": "gpt-image-3",
        },
    )
    assert update.status_code == 200

    resp = client.post(
        "/api/generate",
        json={
            "prompt": "preset default model",
            "size": "1024x1024",
        },
    )
    assert resp.status_code == 202
    job = _wait_for_job(client, resp.json()["job_id"])
    assert job["status"] == "success"
    assert job["model"] == "gpt-image-3"


def test_chat_completions_request_uses_prompt_and_model(tmp_path):
    _configure_runtime(tmp_path)
    from backend.app.integrations import upstream_client as upstream_client_module
    from backend.app.schemas.models import GenerateRequest

    payload = GenerateRequest(
        prompt="hello",
        model="grok-imagine-image-lite",
        size="1024x1024",
    )
    request_data = upstream_client_module.build_chat_completions_request_data(payload)

    assert request_data == {
        "model": "grok-imagine-image-lite",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }
