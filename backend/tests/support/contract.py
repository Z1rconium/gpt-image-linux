import asyncio
import base64
import io
import json
import logging
import os
import re
import sqlite3
import stat
import sys
import threading
import time
import types
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import main as backend_main
from backend.app.api import app_state
from backend.app.api import body_limit
from backend.app.services import assistant_batch as assistant_router
from backend.app.services import assistant_runtime
from backend.app.services import job_events, job_queue
from backend.app.api.edit_limits import (
    EDIT_MULTIPART_METADATA_OVERHEAD_BYTES,
    MAX_EDIT_SOURCE_IMAGES,
)
from backend.app.api.routers import access as access_router
from backend.app.api.routers import gallery_tasks
from backend.app.api.routers import gallery_queries as gallery_queries_router
from backend.app.services import gallery_common, gallery_jobs, gallery_maintenance
from backend.app.api.routers import metrics as metrics_router
from backend.app.api.routers import settings as settings_router
from backend.app.api.routers import static as static_router
from backend.app.services.job_queue import EditImageSource
from backend.app.core import overall_config
from backend.app.core import settings as config
from backend.app.integrations.r2 import sync as r2_sync
from backend.app.core.observability import metrics, record_job_stage_timing
from backend.app.integrations import session_pool
from backend.app.integrations.upstream.generation import (
    call_image_generation_api as ORIGINAL_CALL_IMAGE_GENERATION_API,
    call_image_edit_api as ORIGINAL_CALL_IMAGE_EDIT_API,
    classify_probe_status,
)
from backend.app.repositories import coordination as coordination_repo
from backend.app.repositories import db as db_repo
from backend.app.repositories import image_files
from backend.app.repositories import image_jobs as image_jobs_repo
from backend.app.repositories import settings as settings_repo
from backend.app.repositories import thumbnail_jobs as thumbnail_jobs_repo
from backend.app.repositories.gallery import filters as gallery_filters
from backend.app.repositories.gallery import mutations as gallery_mutations
from backend.app.repositories.gallery import queries as gallery_queries
from backend.app.repositories.gallery import sync_state as gallery_sync_state
from backend.app.schemas.generation import EditRequest


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=="
)
JPEG_BYTES = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDi6KKK+ZP3E//Z"
)
CSRF_SOURCE_HEADERS = {"origin", "referer", "sec-fetch-site"}
CSRF_PROTECTED_TEST_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class _CsrfTestClient:
    def __init__(self, client: TestClient):
        self._client = client

    def __getattr__(self, name):
        return getattr(self._client, name)

    def _with_default_origin(self, method: str, kwargs: dict):
        if method.upper() not in CSRF_PROTECTED_TEST_METHODS:
            return kwargs

        headers = dict(kwargs.get("headers") or {})
        if not any(name.lower() in CSRF_SOURCE_HEADERS for name in headers):
            headers["Origin"] = "http://testserver"
            kwargs["headers"] = headers
        return kwargs

    def request(self, method: str, url: str, **kwargs):
        return self._client.request(
            method,
            url,
            **self._with_default_origin(method, kwargs),
        )

    def get(self, url: str, **kwargs):
        return self._client.get(url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs):
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs):
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs):
        return self.request("DELETE", url, **kwargs)


@contextmanager
def _test_client(**kwargs):
    with TestClient(backend_main.app, **kwargs) as test_client:
        yield _CsrfTestClient(test_client)


def _configure_runtime(tmp_path: Path, *, access_key: str = "", allow_unauthenticated: bool = True):
    images_dir = tmp_path / "images"
    data_dir = tmp_path / "data"
    images_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    config.IMAGES_DIR = str(images_dir)
    config.DATA_DIR = str(data_dir)
    config.DATABASE_FILE = str(data_dir / "app.sqlite3")
    os.environ["TEST_DEFAULT_API_KEY"] = "default-key"
    os.environ["TEST_OPENAI_API_KEY"] = "env-secret"
    os.environ["TEST_PROMPT_OPTIMIZER_API_KEY"] = "optimizer-key"
    os.environ["TEST_UPSTREAM_PROXY_URL"] = "socks5://user:secret@127.0.0.1:1080"
    os.environ["TEST_WEBHOOK_URL"] = "https://hooks.example.com/services/top-secret?token=hidden"
    os.environ["TEST_R2_ACCESS_KEY_ID"] = "r2-access-key"
    os.environ["TEST_R2_SECRET_ACCESS_KEY"] = "r2-secret-key"
    os.environ["TEST_NODEIMAGE_API_KEY"] = "nodeimage-api-key"
    os.environ["ALLOW_UNAUTHENTICATED"] = "true" if allow_unauthenticated else "false"
    os.environ["ACCESS_KEY"] = access_key
    os.environ["ADMIN_KEY"] = ""
    os.environ["ENABLE_METRICS"] = "false"
    os.environ["GITHUB_REPO"] = "Z1rconium/gpt-image-linux"
    os.environ["TRUSTED_PROXY_IPS"] = ""
    os.environ["TRUST_PROXY_HEADERS"] = "false"
    os.environ["PUBLIC_IMAGE_BASE_URL"] = ""
    os.environ["PUBLIC_THUMBNAIL_BASE_URL"] = ""

    config.DEFAULT_API_URL = "https://api.example.com"
    config.DEFAULT_API_KEY = "${TEST_DEFAULT_API_KEY}"
    config.DEFAULT_API_PATH = "/v1/images/generations"
    config.DEFAULT_RESPONSES_MODEL = "gpt-5.4"
    config.DEFAULT_UPSTREAM_SOCKS5_PROXY = ""
    config.AIOHTTP_CONNECTION_LIMIT = 100
    config.AIOHTTP_CONNECTION_LIMIT_PER_HOST = 20
    config.ALLOW_PLAINTEXT_SECRETS = False
    config.ACCESS_KEY = access_key
    config.ADMIN_KEY = ""
    config.ALLOW_UNAUTHENTICATED = allow_unauthenticated
    config.ACCESS_KEY_COOKIE_NAME = "gpt_image_access"
    config.ACCESS_COOKIE_SECURE = False
    config.ACCESS_KEY_SESSION_MINUTES = 180
    config.ACCESS_MAX_FAILURES = 5
    config.ACCESS_LOCKOUT_SECONDS = 300
    config.IP_ALLOWLIST = ""
    config.TRUST_PROXY_HEADERS = False
    config.TRUSTED_PROXY_IPS = ""
    config.PUBLIC_ORIGIN = ""
    config.PUBLIC_IMAGE_BASE_URL = ""
    config.PUBLIC_THUMBNAIL_BASE_URL = ""
    config.ALLOWED_HOSTS = ""
    config.CSRF_ORIGIN_CHECK_ENABLED = True
    config.UPSTREAM_HOST_ALLOWLIST = ""
    config.WEBHOOK_HOST_ALLOWLIST = ""
    config.WEBHOOK_SIGNING_SECRET = "webhook-secret-for-tests-32-bytes!!"
    config.WEBHOOK_TIMEOUT_SECONDS = 1
    config.WEBHOOK_MAX_ATTEMPTS = 1
    config.MAX_FILE_SIZE_MB = 50
    config.MAX_JSON_BODY_MB = 1
    config.MAX_UPSTREAM_JSON_MB = 128
    config.MAX_IMAGE_PIXELS = 100000000
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
    config.ENABLE_NGINX_ACCEL_REDIRECT = False
    config.THUMBNAILS_DIR = str(images_dir / "thumbs")
    config.THUMBNAIL_MAX_SIDE = 512
    config.THUMBNAIL_CPU_CONCURRENCY = 1
    config.PROMPT_OPTIMIZER_ENABLED = False
    config.PROMPT_OPTIMIZER_API_URL = ""
    config.PROMPT_OPTIMIZER_API_KEY = ""
    config.PROMPT_OPTIMIZER_MODEL = "gpt-4o-mini"
    config.PROMPT_OPTIMIZER_TIMEOUT_SECONDS = 60
    config.PROMPT_OPTIMIZER_MAX_OUTPUT_CHARS = 4000
    config.PROMPT_OPTIMIZER_MAX_RESPONSE_MB = 8
    config.PROMPT_OPTIMIZER_HOST_ALLOWLIST = ""
    config.AI_ASSISTANT_ENABLED = True
    config.AI_ASSISTANT_VISION_MODEL = "gpt-4o-mini"
    config.AI_ASSISTANT_MAX_RESPONSE_MB = 8
    config.AI_ASSISTANT_MAX_CONCURRENCY = 2
    config.AI_ASSISTANT_BATCH_MAX_IMAGES = 200
    config.AI_ASSISTANT_IMAGE_MAX_SIDE = 1024
    config.AI_ASSISTANT_IMAGE_MAX_BYTES = 1048576
    config.R2_BACKUP_ENABLED = False
    config.R2_ENDPOINT_URL = ""
    config.R2_ENDPOINT_HOST_ALLOWLIST = ""
    config.R2_BUCKET_NAME = ""
    config.R2_REGION = "auto"
    config.R2_KEY_PREFIX = "gallery/"
    config.R2_ACCESS_KEY_ID = ""
    config.R2_SECRET_ACCESS_KEY = ""
    config.R2_SYNC_INTERVAL_HOURS = 0
    config.NODEIMAGE_API_KEY = ""
    config.NODEIMAGE_UPLOAD_CONCURRENCY = 4
    config.MAX_SSE_SUBSCRIBERS_GLOBAL = 200
    config.MAX_SSE_SUBSCRIBERS_PER_IP = 10
    config.SSE_CONNECTION_TTL_SECONDS = 3600

    import backend.app.core.security as _sec
    _sec._trusted_proxy_networks = None

    db_repo.close_database_connections()
    metrics.reset()
    backend_main.app.state._state.clear()


@pytest.fixture()
def client(tmp_path):
    _configure_runtime(tmp_path)
    with _test_client() as test_client:
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


def _wait_for_gallery_direct_export_job(client: TestClient, job_id: str, timeout: float = 5.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        resp = client.get(f"/api/gallery/direct-export-jobs/{job_id}")
        assert resp.status_code == 200
        last = resp.json()
        if last["status"] in {"success", "error"}:
            return last
        time.sleep(0.05)
    raise AssertionError(f"direct gallery export job {job_id} did not finish: {last}")


def _wait_for_gallery_sync_job(client: TestClient, job_id: str, timeout: float = 5.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        resp = client.get(f"/api/gallery/sync-jobs/{job_id}")
        assert resp.status_code == 200
        last = resp.json()
        if last["status"] in {"success", "error"}:
            return last
        time.sleep(0.05)
    raise AssertionError(f"gallery sync job {job_id} did not finish: {last}")


def _wait_for_gallery_import_job(client: TestClient, job_id: str, timeout: float = 5.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        resp = client.get(f"/api/gallery/import-jobs/{job_id}")
        assert resp.status_code == 200
        last = resp.json()
        if last["status"] in {"success", "error"}:
            return last
        time.sleep(0.05)
    raise AssertionError(f"gallery import job {job_id} did not finish: {last}")


def _wait_for_gallery_batch_analyze_job(client: TestClient, job_id: str, timeout: float = 5.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        resp = client.get(f"/api/assistant/gallery/batch/analyze/{job_id}")
        assert resp.status_code == 200
        last = resp.json()
        if last["status"] in {"success", "error"}:
            return last
        time.sleep(0.05)
    raise AssertionError(f"assistant gallery batch job {job_id} did not finish: {last}")


def _fake_gallery_entry(image_id: str, prompt: str, size: str, filename: str):
    gallery_mutations.add_to_gallery_sync(
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
    return gallery_queries.get_gallery_entry(image_id)


async def _add_generated_gallery_entry(payload, api_path, api_preset_name):
    image_id = image_files.generate_image_id()
    filename = f"{image_id}.png"
    return await gallery_mutations.add_to_gallery_async(
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
    from backend.app.integrations.upstream import generation as upstream_client

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
                    "api_path": "/v1/images/edits",
                    "api_preset_name": api_preset_name,
                },
            )
            entries.append(entry)
        return entries

    monkeypatch.setattr(backend_main.proxy, "call_image_generation_api", fake_generation_api)
    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", fake_edit_api)

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

def _overall_config_item(client, name: str):
    response = client.get("/api/settings/overall-config")
    assert response.status_code == 200
    items = response.json()["items"]
    return next(item for item in items if item["name"] == name)

__all__ = [name for name in globals() if not name.startswith("__")]
