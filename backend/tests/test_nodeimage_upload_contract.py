import asyncio
import json
from pathlib import Path

import aiohttp
import pytest

from backend.tests.support.contract import (
    PNG_BYTES,
    _fake_gallery_entry,
    client,
    config,
    gallery_queries_router,
    settings_repo,
)
from backend.app.api.routers import gallery_batch
from backend.app.core import secrets
from backend.app.integrations.nodeimage import client as nodeimage_client
from backend.app.services import gallery_common


def _enable_nodeimage() -> None:
    settings_repo.save_nodeimage_settings(
        {
            "enabled": True,
            "api_key": "${TEST_NODEIMAGE_API_KEY}",
        }
    )


def test_single_nodeimage_upload_success_and_missing_entry(client, monkeypatch):
    _fake_gallery_entry("node-single", "single", "1024x1024", "node-single.png")
    _enable_nodeimage()
    seen = {}

    async def fake_upload(image_bytes, filename, effective):
        seen.update(
            image_bytes=image_bytes,
            filename=filename,
            api_key=effective.api_key,
        )
        return nodeimage_client.NodeImageUploadResult(
            url="https://cdn.nodeimage.com/single.png",
            markdown="![single](https://cdn.nodeimage.com/single.png)",
        )

    monkeypatch.setattr(gallery_queries_router, "upload_image_bytes", fake_upload)

    response = client.post("/api/gallery/node-single/nodeimage-upload")

    assert response.status_code == 200
    assert response.json() == {
        "url": "https://cdn.nodeimage.com/single.png",
        "markdown": "![single](https://cdn.nodeimage.com/single.png)",
    }
    assert seen == {
        "image_bytes": PNG_BYTES,
        "filename": "node-single.png",
        "api_key": "nodeimage-api-key",
    }
    assert client.post("/api/gallery/missing/nodeimage-upload").status_code == 404


def test_single_nodeimage_upload_rejects_missing_config_and_upstream_failure(
    client,
    monkeypatch,
):
    _fake_gallery_entry("node-failure", "failure", "1024x1024", "node-failure.png")

    disabled = client.post("/api/gallery/node-failure/nodeimage-upload")
    assert disabled.status_code == 400
    assert "disabled" in disabled.json()["detail"]

    _enable_nodeimage()

    async def fail_upload(*_args):
        raise nodeimage_client.NodeImageUploadError("NodeImage quota exceeded")

    monkeypatch.setattr(gallery_queries_router, "upload_image_bytes", fail_upload)
    failed = client.post("/api/gallery/node-failure/nodeimage-upload")
    assert failed.status_code == 502
    assert failed.json()["detail"] == "NodeImage quota exceeded"


def test_batch_nodeimage_upload_reports_partial_results(client, monkeypatch):
    _fake_gallery_entry("node-batch-1", "one", "1024x1024", "node-batch-1.png")
    _fake_gallery_entry("node-batch-2", "two", "1024x1024", "node-batch-2.png")
    _fake_gallery_entry("node-batch-3", "three", "1024x1024", "node-batch-3.png")
    _enable_nodeimage()

    async def fake_upload(_image_bytes, filename, _effective):
        if filename == "node-batch-2.png":
            raise nodeimage_client.NodeImageUploadError("upstream unavailable")
        if filename == "node-batch-3.png":
            raise RuntimeError("unexpected upstream shape")
        return nodeimage_client.NodeImageUploadResult(
            url=f"https://cdn.nodeimage.com/{filename}",
            markdown=f"![image](https://cdn.nodeimage.com/{filename})",
        )

    monkeypatch.setattr(gallery_batch, "upload_image_bytes", fake_upload)
    response = client.post(
        "/api/gallery/batch/nodeimage-upload",
        json={
            "ids": [
                "node-batch-1",
                "missing-node",
                "node-batch-2",
                "node-batch-3",
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["requested_count"] == 4
    assert body["uploaded_count"] == 1
    assert body["failed_count"] == 3
    assert [item["image_id"] for item in body["results"]] == [
        "node-batch-1",
        "missing-node",
        "node-batch-2",
        "node-batch-3",
    ]
    assert body["results"][0]["status"] == "ok"
    assert body["results"][0]["filename"] == "node-batch-1.png"
    assert body["results"][1]["error"] == "Gallery entry not found"
    assert body["results"][1]["filename"] is None
    assert body["results"][2]["error"] == "upstream unavailable"
    assert body["results"][2]["filename"] == "node-batch-2.png"
    assert body["results"][3]["error"] == "Unexpected upload failure"
    assert body["results"][3]["filename"] == "node-batch-3.png"


def test_batch_nodeimage_auth_probe_stops_uploads_and_preserves_file_errors(
    client,
    monkeypatch,
):
    missing = _fake_gallery_entry(
        "node-missing-file",
        "missing file",
        "1024x1024",
        "node-missing-file.png",
    )
    _fake_gallery_entry(
        "node-auth-probe",
        "auth probe",
        "1024x1024",
        "node-auth-probe.png",
    )
    _fake_gallery_entry(
        "node-unreadable",
        "unreadable",
        "1024x1024",
        "node-unreadable.png",
    )
    _fake_gallery_entry(
        "node-not-attempted",
        "not attempted",
        "1024x1024",
        "node-not-attempted.png",
    )
    (Path(config.IMAGES_DIR) / missing.filename).unlink()
    _enable_nodeimage()

    calls = []

    async def reject_upload(_image_bytes, filename, _effective):
        calls.append(filename)
        raise nodeimage_client.NodeImageAuthError(
            "NodeImage API key was rejected."
        )

    original_read_bytes = Path.read_bytes

    def read_bytes(path):
        if path.name == "node-unreadable.png":
            raise PermissionError("not readable")
        return original_read_bytes(path)

    def fail_reference_check(_filename):
        raise AssertionError("batch upload performed a gallery reference query")

    monkeypatch.setattr(gallery_batch, "upload_image_bytes", reject_upload)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(
        gallery_common,
        "is_gallery_filename_referenced",
        fail_reference_check,
    )

    response = client.post(
        "/api/gallery/batch/nodeimage-upload",
        json={
            "ids": [
                "node-missing-file",
                "missing-node",
                "node-auth-probe",
                "node-unreadable",
                "node-not-attempted",
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert calls == ["node-auth-probe.png"]
    assert body["requested_count"] == 5
    assert body["uploaded_count"] == 0
    assert body["failed_count"] == 5
    assert [item["image_id"] for item in body["results"]] == [
        "node-missing-file",
        "missing-node",
        "node-auth-probe",
        "node-unreadable",
        "node-not-attempted",
    ]
    assert [item["error"] for item in body["results"]] == [
        "Image file not found",
        "Gallery entry not found",
        "NodeImage API key was rejected.",
        "Image file could not be read",
        "NodeImage API key was rejected.",
    ]
    assert [item["filename"] for item in body["results"]] == [
        "node-missing-file.png",
        None,
        "node-auth-probe.png",
        "node-unreadable.png",
        "node-not-attempted.png",
    ]


def test_batch_nodeimage_probe_then_limits_concurrency(client, monkeypatch):
    ids = [f"node-concurrent-{index}" for index in range(7)]
    for image_id in ids:
        _fake_gallery_entry(
            image_id,
            image_id,
            "1024x1024",
            f"{image_id}.png",
        )
    _enable_nodeimage()

    active = 0
    max_active = 0
    calls = []

    async def fake_upload(_image_bytes, filename, _effective):
        nonlocal active, max_active
        calls.append(filename)
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return nodeimage_client.NodeImageUploadResult(
            url=f"https://cdn.nodeimage.com/{filename}",
            markdown=f"![image](https://cdn.nodeimage.com/{filename})",
        )

    monkeypatch.setattr(gallery_batch, "upload_image_bytes", fake_upload)
    response = client.post(
        "/api/gallery/batch/nodeimage-upload",
        json={"ids": ids},
    )

    assert response.status_code == 200
    body = response.json()
    assert calls[0] == "node-concurrent-0.png"
    assert len(calls) == 7
    assert max_active == 4
    assert body["uploaded_count"] == 7
    assert body["failed_count"] == 0
    assert body["requested_count"] == body["uploaded_count"] + body["failed_count"]


def test_batch_nodeimage_stops_queued_uploads_after_late_auth_failure(
    client,
    monkeypatch,
):
    ids = [f"node-late-auth-{index}" for index in range(7)]
    for image_id in ids:
        _fake_gallery_entry(
            image_id,
            image_id,
            "1024x1024",
            f"{image_id}.png",
        )
    _enable_nodeimage()

    calls = []

    async def fake_upload(_image_bytes, filename, _effective):
        calls.append(filename)
        if filename == "node-late-auth-0.png":
            return nodeimage_client.NodeImageUploadResult(
                url="https://cdn.nodeimage.com/probe.png",
                markdown="![probe](https://cdn.nodeimage.com/probe.png)",
            )
        if filename == "node-late-auth-1.png":
            await asyncio.sleep(0.01)
            raise nodeimage_client.NodeImageAuthError(
                "NodeImage API key was rejected."
            )
        await asyncio.sleep(0.02)
        return nodeimage_client.NodeImageUploadResult(
            url=f"https://cdn.nodeimage.com/{filename}",
            markdown=f"![image](https://cdn.nodeimage.com/{filename})",
        )

    monkeypatch.setattr(gallery_batch, "upload_image_bytes", fake_upload)
    response = client.post(
        "/api/gallery/batch/nodeimage-upload",
        json={"ids": ids},
    )

    assert response.status_code == 200
    body = response.json()
    assert calls == [f"node-late-auth-{index}.png" for index in range(5)]
    assert body["uploaded_count"] == 4
    assert body["failed_count"] == 3
    assert [item["error"] for item in body["results"][5:]] == [
        "NodeImage API key was rejected.",
        "NodeImage API key was rejected.",
    ]


def test_batch_nodeimage_upload_accepts_filtered_selection_token(client, monkeypatch):
    _fake_gallery_entry(
        "node-token-1",
        "nodeimage token match one",
        "1024x1024",
        "node-token-1.png",
    )
    _fake_gallery_entry(
        "node-token-2",
        "outside selection",
        "1024x1024",
        "node-token-2.png",
    )
    _fake_gallery_entry(
        "node-token-3",
        "nodeimage token match three",
        "1024x1024",
        "node-token-3.png",
    )
    _enable_nodeimage()

    async def fake_upload(_image_bytes, filename, _effective):
        return nodeimage_client.NodeImageUploadResult(
            url=f"https://cdn.nodeimage.com/{filename}",
            markdown=f"![image](https://cdn.nodeimage.com/{filename})",
        )

    monkeypatch.setattr(gallery_batch, "upload_image_bytes", fake_upload)
    token_response = client.post(
        "/api/gallery/batch/selection-tokens",
        json={"filters": {"prompt": "nodeimage token match"}},
    )
    assert token_response.status_code == 201
    assert token_response.json()["count"] == 2

    response = client.post(
        "/api/gallery/batch/nodeimage-upload",
        json={"selection_token": token_response.json()["selection_token"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["requested_count"] == 2
    assert body["uploaded_count"] == 2
    assert body["failed_count"] == 0
    assert {item["image_id"] for item in body["results"]} == {
        "node-token-1",
        "node-token-3",
    }


def test_nodeimage_settings_round_trip_and_secret_origin_binding(client):
    current = client.get("/api/settings").json()
    assert current["nodeimage"]["enabled"] is False
    assert current["nodeimage"]["has_api_key"] is False

    payload = {
        "active_preset_id": current["active_preset_id"],
        "preset_name": "Primary",
        "api_url": current["api_url"],
        "api_key": None,
        "api_path": current["api_path"],
        "default_model": current["default_model"],
        "nodeimage": {
            "enabled": True,
            "api_key": "${TEST_NODEIMAGE_API_KEY}",
        },
    }
    updated = client.post("/api/settings", json=payload)
    assert updated.status_code == 200
    nodeimage = updated.json()["nodeimage"]
    assert nodeimage == {
        "enabled": True,
        "api_key_masked": "${TEST_NODEIMAGE_API_KEY}",
        "has_api_key": True,
        "api_key_source": "env",
        "api_key_env_var": "TEST_NODEIMAGE_API_KEY",
        "api_key_secret_id": None,
    }
    assert settings_repo.load_nodeimage_settings()["api_key"] == "${TEST_NODEIMAGE_API_KEY}"

    secrets.configure_registry(
        json.dumps(
            {
                "nodeimage-wrong-origin": {
                    "purpose": "nodeimage_api_key",
                    "origin": "https://example.com",
                    "env": "TEST_NODEIMAGE_API_KEY",
                }
            }
        )
    )
    payload["nodeimage"]["api_key"] = "nodeimage-wrong-origin"
    rejected = client.post("/api/settings", json=payload)
    assert rejected.status_code == 422
    assert "not bound to the target origin" in rejected.json()["detail"]


def test_nodeimage_settings_plaintext_path_is_opt_in_and_masked(client):
    current = client.get("/api/settings").json()
    payload = {
        "active_preset_id": current["active_preset_id"],
        "preset_name": "Primary",
        "api_url": current["api_url"],
        "api_key": None,
        "api_path": current["api_path"],
        "default_model": current["default_model"],
        "nodeimage": {"enabled": True, "api_key": "plain-nodeimage-key"},
    }

    rejected = client.post("/api/settings", json=payload)
    assert rejected.status_code == 422
    assert "NodeImage API key must use ${ENV_VAR_NAME}" in rejected.text

    config.ALLOW_PLAINTEXT_SECRETS = True
    accepted = client.post("/api/settings", json=payload)
    assert accepted.status_code == 200
    nodeimage = accepted.json()["nodeimage"]
    assert nodeimage["has_api_key"] is True
    assert nodeimage["api_key_source"] == "stored"
    assert nodeimage["api_key_masked"] == "plai***-key"
    assert nodeimage["api_key_secret_id"] is None
    assert settings_repo.load_nodeimage_settings()["api_key"] == "plain-nodeimage-key"


class _ResponseContent:
    def __init__(self, payload):
        self.payload = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload).encode("utf-8")
        )

    async def iter_chunked(self, _size):
        yield self.payload


class _Response:
    def __init__(self, status: int, payload):
        self.status = status
        self.content = _ResponseContent(payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _Pool:
    def __init__(self, session):
        self.session = session

    def get(self, **_kwargs):
        return self.session


def test_nodeimage_client_retries_5xx_once_and_does_not_retry_auth(monkeypatch):
    effective = nodeimage_client.NodeImageEffectiveSettings(True, "test-key")
    session = _Session(
        [
            _Response(503, {"success": False, "error": "temporary failure"}),
            _Response(
                200,
                {
                    "success": True,
                    "links": {
                        "direct": "https://cdn.nodeimage.com/retry.png",
                        "markdown": "![retry](https://cdn.nodeimage.com/retry.png)",
                    },
                },
            ),
        ]
    )
    monkeypatch.setattr(nodeimage_client, "get_pool", lambda: _Pool(session))
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(nodeimage_client.asyncio, "sleep", no_sleep)

    result = asyncio.run(
        nodeimage_client.upload_image_bytes(PNG_BYTES, "retry.png", effective)
    )
    assert result.url.endswith("retry.png")
    assert len(session.calls) == 2
    assert session.calls[0][1]["headers"] == {"X-API-Key": "test-key"}

    auth_session = _Session(
        [_Response(401, {"success": False, "error": "invalid api key"})]
    )
    monkeypatch.setattr(nodeimage_client, "get_pool", lambda: _Pool(auth_session))
    with pytest.raises(nodeimage_client.NodeImageAuthError):
        asyncio.run(
            nodeimage_client.upload_image_bytes(PNG_BYTES, "auth.png", effective)
        )
    assert len(auth_session.calls) == 1

    non_json_auth_session = _Session([_Response(403, b"Forbidden")])
    monkeypatch.setattr(
        nodeimage_client,
        "get_pool",
        lambda: _Pool(non_json_auth_session),
    )
    with pytest.raises(nodeimage_client.NodeImageAuthError):
        asyncio.run(
            nodeimage_client.upload_image_bytes(PNG_BYTES, "auth.png", effective)
        )
    assert len(non_json_auth_session.calls) == 1

    text_auth_session = _Session(
        [_Response(400, {"success": False, "error": "unauthorized"})]
    )
    monkeypatch.setattr(
        nodeimage_client,
        "get_pool",
        lambda: _Pool(text_auth_session),
    )
    with pytest.raises(nodeimage_client.NodeImageAuthError):
        asyncio.run(
            nodeimage_client.upload_image_bytes(PNG_BYTES, "auth.png", effective)
        )
    assert len(text_auth_session.calls) == 1

    invalid_key_session = _Session(
        [_Response(400, {"success": False, "error": "invalid api key"})]
    )
    monkeypatch.setattr(
        nodeimage_client,
        "get_pool",
        lambda: _Pool(invalid_key_session),
    )
    with pytest.raises(nodeimage_client.NodeImageAuthError):
        asyncio.run(
            nodeimage_client.upload_image_bytes(PNG_BYTES, "auth.png", effective)
        )
    assert len(invalid_key_session.calls) == 1


def test_nodeimage_client_retries_5xx_with_auth_text(monkeypatch):
    effective = nodeimage_client.NodeImageEffectiveSettings(True, "test-key")
    session = _Session(
        [
            _Response(503, {"success": False, "error": "unauthorized"}),
            _Response(
                200,
                {
                    "success": True,
                    "links": {
                        "direct": "https://cdn.nodeimage.com/retry-auth-text.png",
                        "markdown": "![retry](https://cdn.nodeimage.com/retry-auth-text.png)",
                    },
                },
            ),
        ]
    )
    monkeypatch.setattr(nodeimage_client, "get_pool", lambda: _Pool(session))

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(nodeimage_client.asyncio, "sleep", no_sleep)

    result = asyncio.run(
        nodeimage_client.upload_image_bytes(
            PNG_BYTES,
            "retry-auth-text.png",
            effective,
        )
    )
    assert result.url.endswith("retry-auth-text.png")
    assert len(session.calls) == 2


def test_nodeimage_client_retries_network_error_once(monkeypatch):
    effective = nodeimage_client.NodeImageEffectiveSettings(True, "test-key")
    session = _Session(
        [
            aiohttp.ClientConnectionError("temporary"),
            _Response(
                200,
                {
                    "success": True,
                    "links": {
                        "direct": "https://cdn.nodeimage.com/network.png",
                        "markdown": "![network](https://cdn.nodeimage.com/network.png)",
                    },
                },
            ),
        ]
    )
    monkeypatch.setattr(nodeimage_client, "get_pool", lambda: _Pool(session))
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(nodeimage_client.asyncio, "sleep", no_sleep)

    result = asyncio.run(
        nodeimage_client.upload_image_bytes(PNG_BYTES, "network.png", effective)
    )
    assert result.url.endswith("network.png")
    assert len(session.calls) == 2


def test_nodeimage_client_rejects_unsafe_direct_link(monkeypatch):
    effective = nodeimage_client.NodeImageEffectiveSettings(True, "test-key")
    session = _Session(
        [
            _Response(
                200,
                {
                    "success": True,
                    "links": {
                        "direct": "javascript:alert(1)",
                        "markdown": "![unsafe](javascript:alert(1))",
                    },
                },
            )
        ]
    )
    monkeypatch.setattr(nodeimage_client, "get_pool", lambda: _Pool(session))

    with pytest.raises(
        nodeimage_client.NodeImageUploadError,
        match="invalid direct link",
    ):
        asyncio.run(
            nodeimage_client.upload_image_bytes(PNG_BYTES, "unsafe.png", effective)
        )


def test_nodeimage_client_redacts_effective_key_from_upstream_error(monkeypatch):
    effective = nodeimage_client.NodeImageEffectiveSettings(
        True,
        "literal-nodeimage-key",
    )
    session = _Session(
        [
            _Response(
                400,
                {
                    "success": False,
                    "error": "Rejected literal-nodeimage-key",
                },
            )
        ]
    )
    monkeypatch.setattr(nodeimage_client, "get_pool", lambda: _Pool(session))

    with pytest.raises(nodeimage_client.NodeImageUploadError) as exc_info:
        asyncio.run(
            nodeimage_client.upload_image_bytes(PNG_BYTES, "error.png", effective)
        )
    assert "literal-nodeimage-key" not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


def test_nodeimage_client_redacts_key_before_truncating_error(monkeypatch):
    api_key = "boundary-secret-value"
    effective = nodeimage_client.NodeImageEffectiveSettings(True, api_key)
    session = _Session(
        [
            _Response(
                400,
                {
                    "success": False,
                    "error": f"{'x' * 495}{api_key}",
                },
            )
        ]
    )
    monkeypatch.setattr(nodeimage_client, "get_pool", lambda: _Pool(session))

    with pytest.raises(nodeimage_client.NodeImageUploadError) as exc_info:
        asyncio.run(
            nodeimage_client.upload_image_bytes(PNG_BYTES, "error.png", effective)
        )

    message = str(exc_info.value)
    assert len(message) == 500
    assert api_key not in message
    assert api_key[:5] not in message
