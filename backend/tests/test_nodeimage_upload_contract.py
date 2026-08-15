import asyncio
import io
import json
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace

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
from backend.app.repositories.coordination import create_gallery_job, get_gallery_job
from backend.app.schemas.gallery import GalleryEntry
from backend.app.services import gallery_jobs


def _enable_nodeimage() -> None:
    settings_repo.save_nodeimage_settings(
        {
            "enabled": True,
            "api_key": "${TEST_NODEIMAGE_API_KEY}",
        }
    )


def _wait_for_nodeimage_job(test_client, job_id: str, timeout: float = 5.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        response = test_client.get(
            f"/api/gallery/nodeimage-upload-jobs/{job_id}"
        )
        assert response.status_code == 200
        last = response.json()
        if last["status"] in {
            "success",
            "partial_failure",
            "cancelled",
            "error",
        }:
            return last
        time.sleep(0.05)
    raise AssertionError(f"NodeImage job {job_id} did not finish: {last}")


def _start_nodeimage_batch(test_client, ids: list[str]):
    response = test_client.post(
        "/api/gallery/batch/nodeimage-upload",
        json={"ids": ids},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status_url"].endswith(
        f"/api/gallery/nodeimage-upload-jobs/{body['job_id']}"
    )
    assert body["events_url"].endswith(
        f"/api/gallery/nodeimage-upload-jobs/{body['job_id']}/events"
    )
    assert body["cancel_url"].endswith(
        f"/api/gallery/nodeimage-upload-jobs/{body['job_id']}/cancel"
    )
    return body


def test_single_nodeimage_upload_success_and_missing_entry(client, monkeypatch):
    _fake_gallery_entry("node-single", "single", "1024x1024", "node-single.png")
    _enable_nodeimage()
    seen = {}

    async def fake_upload(path, filename, effective):
        seen.update(
            path=path,
            size=path.stat().st_size,
            filename=filename,
            api_key=effective.api_key,
        )
        return nodeimage_client.NodeImageUploadResult(
            url="https://cdn.nodeimage.com/single.png",
            markdown="![single](https://cdn.nodeimage.com/single.png)",
        )

    monkeypatch.setattr(gallery_queries_router, "upload_image_file", fake_upload)
    monkeypatch.setattr(
        gallery_queries_router,
        "_resolve_gallery_image_path",
        lambda _filename: pytest.fail("single upload must not re-query gallery references"),
    )

    response = client.post("/api/gallery/node-single/nodeimage-upload")

    assert response.status_code == 200
    assert response.json() == {
        "url": "https://cdn.nodeimage.com/single.png",
        "markdown": "![single](https://cdn.nodeimage.com/single.png)",
    }
    assert seen == {
        "path": Path(config.IMAGES_DIR) / "node-single.png",
        "size": len(PNG_BYTES),
        "filename": "node-single.png",
        "api_key": "nodeimage-api-key",
    }
    assert client.post("/api/gallery/missing/nodeimage-upload").status_code == 404


def test_single_nodeimage_upload_preserves_missing_and_unreadable_file_errors(
    client,
    monkeypatch,
):
    missing = _fake_gallery_entry(
        "node-missing-file",
        "missing",
        "1024x1024",
        "node-missing-file.png",
    )
    unreadable = _fake_gallery_entry(
        "node-unreadable-file",
        "unreadable",
        "1024x1024",
        "node-unreadable-file.png",
    )
    _enable_nodeimage()
    (Path(config.IMAGES_DIR) / missing.filename).unlink()

    missing_response = client.post(
        "/api/gallery/node-missing-file/nodeimage-upload"
    )
    assert missing_response.status_code == 404
    assert missing_response.json()["detail"] == "Image file not found"

    async def unreadable_upload(*_args):
        raise OSError("permission denied")

    monkeypatch.setattr(
        gallery_queries_router,
        "upload_image_file",
        unreadable_upload,
    )
    unreadable_response = client.post(
        "/api/gallery/node-unreadable-file/nodeimage-upload"
    )
    assert unreadable_response.status_code == 404
    assert unreadable_response.json()["detail"] == "Image file could not be read"


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

    monkeypatch.setattr(gallery_queries_router, "upload_image_file", fail_upload)
    failed = client.post("/api/gallery/node-failure/nodeimage-upload")
    assert failed.status_code == 502
    assert failed.json()["detail"] == "NodeImage quota exceeded"


def test_batch_nodeimage_upload_reports_partial_results(client, monkeypatch, caplog):
    _fake_gallery_entry("node-batch-1", "one", "1024x1024", "node-batch-1.png")
    _fake_gallery_entry("node-batch-2", "two", "1024x1024", "node-batch-2.png")
    _fake_gallery_entry("node-batch-3", "three", "1024x1024", "node-batch-3.png")
    _enable_nodeimage()

    async def fake_upload(_path, filename, _effective):
        if filename == "node-batch-2.png":
            raise nodeimage_client.NodeImageUploadError("upstream unavailable")
        if filename == "node-batch-3.png":
            raise RuntimeError("unexpected upstream shape")
        return nodeimage_client.NodeImageUploadResult(
            url=f"https://cdn.nodeimage.com/{filename}",
            markdown=f"![image](https://cdn.nodeimage.com/{filename})",
        )

    monkeypatch.setattr(gallery_jobs, "upload_image_file", fake_upload)
    caplog.set_level(logging.ERROR, logger=gallery_jobs.__name__)
    created = _start_nodeimage_batch(
        client,
        [
            "node-batch-1",
            "missing-node",
            "node-batch-2",
            "node-batch-3",
        ],
    )
    body = _wait_for_nodeimage_job(client, created["job_id"])

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
    assert "unexpected upstream shape" not in json.dumps(body)
    unexpected_record = next(
        record
        for record in caplog.records
        if "Unexpected NodeImage upload failure" in record.getMessage()
    )
    assert unexpected_record.exc_info is not None


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

    async def reject_upload(_path, filename, _effective):
        calls.append(filename)
        raise nodeimage_client.NodeImageAuthError(
            "NodeImage API key was rejected."
        )

    unreadable_path = Path(config.IMAGES_DIR) / "node-unreadable.png"
    unreadable_path.unlink()
    unreadable_path.mkdir()

    def fail_read_bytes(_path):
        raise AssertionError("NodeImage batch upload must not read the whole file")

    monkeypatch.setattr(gallery_jobs, "upload_image_file", reject_upload)
    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)

    created = _start_nodeimage_batch(
        client,
        [
            "node-missing-file",
            "missing-node",
            "node-auth-probe",
            "node-unreadable",
            "node-not-attempted",
        ],
    )
    body = _wait_for_nodeimage_job(client, created["job_id"])
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

    async def fake_upload(_path, filename, _effective):
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

    config.NODEIMAGE_UPLOAD_CONCURRENCY = 4
    monkeypatch.setattr(gallery_jobs, "upload_image_file", fake_upload)
    created = _start_nodeimage_batch(client, ids)
    body = _wait_for_nodeimage_job(client, created["job_id"])
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

    async def fake_upload(_path, filename, _effective):
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

    config.NODEIMAGE_UPLOAD_CONCURRENCY = 4
    monkeypatch.setattr(gallery_jobs, "upload_image_file", fake_upload)
    created = _start_nodeimage_batch(client, ids)
    body = _wait_for_nodeimage_job(client, created["job_id"])
    assert calls[0] == "node-late-auth-0.png"
    assert set(calls) == {
        f"node-late-auth-{index}.png" for index in range(5)
    }
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

    async def fake_upload(_path, filename, _effective):
        return nodeimage_client.NodeImageUploadResult(
            url=f"https://cdn.nodeimage.com/{filename}",
            markdown=f"![image](https://cdn.nodeimage.com/{filename})",
        )

    monkeypatch.setattr(gallery_jobs, "upload_image_file", fake_upload)
    token_response = client.post(
        "/api/gallery/batch/selection-tokens",
        json={"filters": {"prompt": "nodeimage token match"}},
    )
    assert token_response.status_code == 201
    assert token_response.json()["count"] == 2

    created = client.post(
        "/api/gallery/batch/nodeimage-upload",
        json={"selection_token": token_response.json()["selection_token"]},
    )
    assert created.status_code == 202
    body = _wait_for_nodeimage_job(client, created.json()["job_id"])
    assert body["requested_count"] == 2
    assert body["uploaded_count"] == 2
    assert body["failed_count"] == 0
    assert {item["image_id"] for item in body["results"]} == {
        "node-token-1",
        "node-token-3",
    }


def test_batch_nodeimage_upload_rejects_selection_token_over_limit(
    client,
    monkeypatch,
):
    for index in range(3):
        _fake_gallery_entry(
            f"node-token-limit-{index}",
            "nodeimage token over limit",
            "1024x1024",
            f"node-token-limit-{index}.png",
        )

    monkeypatch.setattr(gallery_batch, "NODEIMAGE_BATCH_MAX", 2)
    token_response = client.post(
        "/api/gallery/batch/selection-tokens",
        json={"filters": {"prompt": "nodeimage token over limit"}},
    )
    assert token_response.status_code == 201
    assert token_response.json()["count"] == 3

    response = client.post(
        "/api/gallery/batch/nodeimage-upload",
        json={"selection_token": token_response.json()["selection_token"]},
    )

    assert response.status_code == 422
    assert "at most 2 images" in response.json()["detail"]
    assert "selected 3" in response.json()["detail"]


def test_nodeimage_batch_rejects_oversized_files_before_upload(client, monkeypatch):
    entry = _fake_gallery_entry(
        "node-oversized",
        "oversized",
        "1024x1024",
        "node-oversized.png",
    )
    _enable_nodeimage()
    config.MAX_FILE_SIZE_MB = 1
    oversized_path = Path(config.IMAGES_DIR) / entry.filename
    oversized_path.write_bytes(b"x" * (config.MAX_FILE_SIZE_MB * 1024 * 1024 + 1))
    calls = []

    async def fail_upload(_path, filename, _effective):
        calls.append(filename)
        raise AssertionError("oversized files must fail before upload")

    monkeypatch.setattr(gallery_jobs, "upload_image_file", fail_upload)
    created = _start_nodeimage_batch(client, [entry.id])
    body = _wait_for_nodeimage_job(client, created["job_id"])

    assert calls == []
    assert body["status"] == "partial_failure"
    assert body["failed_count"] == 1
    assert body["results"][0]["error"] == "Image file is too large. Max size is 1 MB"


def test_nodeimage_batch_cancellation_preserves_inflight_results(client, monkeypatch):
    ids = [f"node-cancel-{index}" for index in range(3)]
    for image_id in ids:
        _fake_gallery_entry(image_id, image_id, "1024x1024", f"{image_id}.png")
    _enable_nodeimage()
    config.NODEIMAGE_UPLOAD_CONCURRENCY = 1
    started = threading.Event()
    release = threading.Event()
    calls = []

    async def slow_upload(_path, filename, _effective):
        calls.append(filename)
        started.set()
        await asyncio.to_thread(release.wait, 3)
        return nodeimage_client.NodeImageUploadResult(
            url=f"https://cdn.nodeimage.com/{filename}",
            markdown=f"![image](https://cdn.nodeimage.com/{filename})",
        )

    monkeypatch.setattr(gallery_jobs, "upload_image_file", slow_upload)
    created = _start_nodeimage_batch(client, ids)
    assert started.wait(2)

    cancel_response = client.delete(
        f"/api/gallery/nodeimage-upload-jobs/{created['job_id']}"
    )
    assert cancel_response.status_code == 200
    assert cancel_response.json()["stage"] == "cancelling"
    release.set()

    body = _wait_for_nodeimage_job(client, created["job_id"])
    assert body["status"] == "cancelled"
    assert body["uploaded_count"] == 1
    assert body["failed_count"] == 0
    assert body["cancelled_count"] == 2
    assert [item["status"] for item in body["results"]] == [
        "ok",
        "cancelled",
        "cancelled",
    ]
    assert calls == [f"{ids[0]}.png"]


def test_nodeimage_batch_capacity_is_reserved_before_dispatch(client, monkeypatch):
    ids = ["node-capacity-0", "node-capacity-1"]
    for image_id in ids:
        _fake_gallery_entry(image_id, image_id, "1024x1024", f"{image_id}.png")
    _enable_nodeimage()
    started = threading.Event()
    release = threading.Event()

    async def slow_upload(_path, filename, _effective):
        started.set()
        await asyncio.to_thread(release.wait, 3)
        return nodeimage_client.NodeImageUploadResult(
            url=f"https://cdn.nodeimage.com/{filename}",
            markdown=f"![image](https://cdn.nodeimage.com/{filename})",
        )

    monkeypatch.setattr(gallery_jobs, "upload_image_file", slow_upload)
    created = _start_nodeimage_batch(client, ids)
    assert started.wait(2)

    rejected = client.post(
        "/api/gallery/batch/nodeimage-upload",
        json={"ids": [ids[0]]},
    )
    assert rejected.status_code == 429
    assert "active NodeImage upload jobs" in rejected.json()["detail"]
    release.set()
    _wait_for_nodeimage_job(client, created["job_id"])


def test_nodeimage_batch_sse_replays_persisted_terminal_results(client, monkeypatch):
    _fake_gallery_entry("node-sse", "sse", "1024x1024", "node-sse.png")
    _enable_nodeimage()

    async def fake_upload(_path, filename, _effective):
        return nodeimage_client.NodeImageUploadResult(
            url=f"https://cdn.nodeimage.com/{filename}",
            markdown=f"![image](https://cdn.nodeimage.com/{filename})",
        )

    monkeypatch.setattr(gallery_jobs, "upload_image_file", fake_upload)
    created = _start_nodeimage_batch(client, ["node-sse"])
    body = _wait_for_nodeimage_job(client, created["job_id"])
    assert body["status"] == "success"

    events = client.get(body["events_url"])
    assert events.status_code == 200
    assert "event: nodeimage_upload" in events.text
    assert '"status":"success"' in events.text
    assert "https://cdn.nodeimage.com/node-sse.png" in events.text


def test_nodeimage_batch_recovery_skips_persisted_completed_items(client, monkeypatch):
    ids = ["node-recovery-0", "node-recovery-1"]
    for image_id in ids:
        _fake_gallery_entry(image_id, image_id, "1024x1024", f"{image_id}.png")
    _enable_nodeimage()
    calls = []
    first_result = gallery_jobs._nodeimage_result_item(
        ids[0],
        f"{ids[0]}.png",
        "ok",
        url="https://cdn.nodeimage.com/recovered.png",
        markdown="![recovered](https://cdn.nodeimage.com/recovered.png)",
    )
    job = gallery_jobs._build_nodeimage_upload_job(ids, 2, [])
    job.update(
        status="running",
        stage="uploading",
        lease_owner="recovery-worker",
        lease_expires_at=gallery_jobs._gallery_job_lease_expires_at(),
        payload={"ids": ids, "results": [first_result], "cancel_requested": False},
    )
    stored_job = create_gallery_job(**job)

    async def fake_upload(_path, filename, _effective):
        calls.append(filename)
        return nodeimage_client.NodeImageUploadResult(
            url=f"https://cdn.nodeimage.com/{filename}",
            markdown=f"![image](https://cdn.nodeimage.com/{filename})",
        )

    monkeypatch.setattr(gallery_jobs, "upload_image_file", fake_upload)
    asyncio.run(gallery_jobs._run_nodeimage_upload_job(stored_job))
    recovered = get_gallery_job("nodeimage_upload", stored_job["job_id"])

    assert recovered is not None
    assert recovered["status"] == "success"
    assert calls == [f"{ids[1]}.png"]
    assert [item["image_id"] for item in recovered["payload"]["results"]] == ids


def test_nodeimage_settings_round_trip_and_secret_origin_binding(client):
    current = client.get("/api/settings").json()
    assert current["nodeimage"]["enabled"] is False
    assert current["nodeimage"]["has_api_key"] is False
    assert current["nodeimage"]["api_key_resolvable"] is False

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
        "api_key_resolvable": True,
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


def test_nodeimage_settings_reports_whether_saved_key_can_be_resolved(
    client,
    monkeypatch,
    request,
):
    settings_repo.save_nodeimage_settings(
        {
            "enabled": True,
            "api_key": "${UNSET_NODEIMAGE_API_KEY}",
        }
    )
    monkeypatch.delenv("UNSET_NODEIMAGE_API_KEY", raising=False)

    unresolved = client.get("/api/settings")
    assert unresolved.status_code == 200
    assert unresolved.json()["nodeimage"] == {
        "enabled": True,
        "api_key_masked": "${UNSET_NODEIMAGE_API_KEY}",
        "has_api_key": True,
        "api_key_resolvable": False,
        "api_key_source": "env",
        "api_key_env_var": "UNSET_NODEIMAGE_API_KEY",
        "api_key_secret_id": None,
    }

    monkeypatch.setenv("UNSET_NODEIMAGE_API_KEY", "resolved-nodeimage-key")
    resolved = client.get("/api/settings")
    assert resolved.status_code == 200
    assert resolved.json()["nodeimage"]["api_key_resolvable"] is True

    request.addfinalizer(lambda: secrets.configure_registry(""))
    secrets.configure_registry(
        json.dumps(
            {
                "nodeimage-invalid-binding": {
                    "purpose": "nodeimage_api_key",
                    "origin": "https://example.com",
                    "env": "UNSET_NODEIMAGE_API_KEY",
                }
            }
        )
    )
    settings_repo.save_nodeimage_settings(
        {
            "enabled": True,
            "api_key": "nodeimage-invalid-binding",
        }
    )
    invalid_binding = client.get("/api/settings")
    assert invalid_binding.status_code == 200
    assert invalid_binding.json()["nodeimage"]["has_api_key"] is True
    assert invalid_binding.json()["nodeimage"]["api_key_source"] == "registry"
    assert invalid_binding.json()["nodeimage"]["api_key_resolvable"] is False
    secrets.configure_registry("")


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
            if isinstance(payload, (bytes, Exception))
            else json.dumps(payload).encode("utf-8")
        )

    async def iter_chunked(self, _size):
        if isinstance(self.payload, Exception):
            raise self.payload
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


class _TrackingFile(io.BytesIO):
    pass


def test_nodeimage_client_reopens_and_closes_streaming_source_on_retry(monkeypatch):
    effective = nodeimage_client.NodeImageEffectiveSettings(True, "test-key")
    session = _Session(
        [
            _Response(503, {"success": False, "error": "temporary failure"}),
            _Response(
                200,
                {
                    "success": True,
                    "links": {
                        "direct": "https://cdn.nodeimage.com/stream.png",
                        "markdown": "![stream](https://cdn.nodeimage.com/stream.png)",
                    },
                },
            ),
        ]
    )
    opened = []

    def source():
        handle = _TrackingFile(PNG_BYTES)
        opened.append(handle)
        return handle

    monkeypatch.setattr(nodeimage_client, "get_pool", lambda: _Pool(session))

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(nodeimage_client.asyncio, "sleep", no_sleep)
    result = asyncio.run(
        nodeimage_client.upload_image_source(source, "stream.png", effective)
    )

    assert result.url.endswith("stream.png")
    assert len(opened) == 2
    assert all(handle.closed for handle in opened)
    assert all(
        getattr(call[1]["data"], "_fields", [({}, {}, handle)])[0][2] is handle
        for call, handle in zip(session.calls, opened)
    )


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


def test_nodeimage_client_retries_connect_error_once(monkeypatch):
    effective = nodeimage_client.NodeImageEffectiveSettings(True, "test-key")
    session = _Session(
        [
            aiohttp.ClientConnectorError(None, OSError("temporary")),
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


def test_nodeimage_client_retries_unreadable_5xx_response_once(monkeypatch):
    effective = nodeimage_client.NodeImageEffectiveSettings(True, "test-key")
    session = _Session(
        [
            _Response(503, aiohttp.ClientConnectionError("response interrupted")),
            _Response(
                200,
                {
                    "success": True,
                    "links": {
                        "direct": "https://cdn.nodeimage.com/recovered.png",
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
        nodeimage_client.upload_image_bytes(PNG_BYTES, "recovered.png", effective)
    )

    assert result.url.endswith("recovered.png")
    assert len(session.calls) == 2


def test_nodeimage_client_generates_markdown_from_validated_direct_url(monkeypatch):
    effective = nodeimage_client.NodeImageEffectiveSettings(True, "test-key")
    direct = "https://cdn.nodeimage.com/image(1).png?caption=]()<>"
    session = _Session(
        [
            _Response(
                200,
                {
                    "success": True,
                    "links": {
                        "direct": direct,
                        "markdown": "![attacker](javascript:alert(1))",
                    },
                },
            )
        ]
    )
    monkeypatch.setattr(nodeimage_client, "get_pool", lambda: _Pool(session))

    result = asyncio.run(
        nodeimage_client.upload_image_bytes(
            PNG_BYTES,
            "../bad](*).png",
            effective,
        )
    )

    assert result.url == direct
    assert result.markdown == (
        r"![bad\]\(\*\).png](https://cdn.nodeimage.com/"
        r"image%281%29.png?caption=]%28%29%3C%3E)"
    )
    assert "javascript" not in result.markdown


@pytest.mark.parametrize(
    ("filename", "expected_content_type"),
    [
        ("image.webp", "image/webp"),
        ("image.avif", "image/avif"),
        ("image.heif", "image/heif"),
        ("image.unknown-extension", "application/octet-stream"),
    ],
)
def test_nodeimage_client_uses_stable_content_types(
    monkeypatch,
    filename,
    expected_content_type,
):
    effective = nodeimage_client.NodeImageEffectiveSettings(True, "test-key")
    session = _Session(
        [
            _Response(
                200,
                {
                    "success": True,
                    "links": {"direct": f"https://cdn.nodeimage.com/{filename}"},
                },
            )
        ]
    )
    monkeypatch.setattr(nodeimage_client, "get_pool", lambda: _Pool(session))

    asyncio.run(nodeimage_client.upload_image_bytes(PNG_BYTES, filename, effective))

    form = session.calls[0][1]["data"]
    assert form._fields[0][1]["Content-Type"] == expected_content_type


def test_nodeimage_file_inspection_returns_path_or_structured_error(
    tmp_path,
    monkeypatch,
):
    entry = GalleryEntry(
        id="inspection",
        prompt="inspection",
        size="1x1",
        filename="inspection.png",
        created_at="2026-01-01T00:00:00Z",
    )

    monkeypatch.setattr(gallery_jobs, "safe_image_path", lambda _filename: None)
    missing = gallery_jobs._inspect_nodeimage_file(entry)
    assert isinstance(missing, dict)
    assert missing["error"] == "Image file not found"

    missing_path = tmp_path / "missing.png"
    monkeypatch.setattr(
        gallery_jobs,
        "safe_image_path",
        lambda _filename: missing_path,
    )
    assert gallery_jobs._inspect_nodeimage_file(entry)["error"] == "Image file not found"

    empty_path = tmp_path / "empty.png"
    empty_path.write_bytes(b"")
    monkeypatch.setattr(
        gallery_jobs,
        "safe_image_path",
        lambda _filename: empty_path,
    )
    assert gallery_jobs._inspect_nodeimage_file(entry)["error"] == "Image file is empty"

    class UnreadablePath:
        def stat(self):
            return SimpleNamespace(st_size=1)

        def open(self, _mode):
            raise OSError("unreadable")

    monkeypatch.setattr(
        gallery_jobs,
        "safe_image_path",
        lambda _filename: UnreadablePath(),
    )
    unreadable = gallery_jobs._inspect_nodeimage_file(entry)
    assert unreadable["error"] == "Image file could not be read"

    valid_path = tmp_path / "valid.png"
    valid_path.write_bytes(PNG_BYTES)
    original_limit = config.MAX_FILE_SIZE_MB
    monkeypatch.setattr(config, "MAX_FILE_SIZE_MB", 0)
    monkeypatch.setattr(
        gallery_jobs,
        "safe_image_path",
        lambda _filename: valid_path,
    )
    oversized = gallery_jobs._inspect_nodeimage_file(entry)
    assert "too large" in oversized["error"]

    monkeypatch.setattr(config, "MAX_FILE_SIZE_MB", original_limit)
    assert gallery_jobs._inspect_nodeimage_file(entry) == valid_path


def test_nodeimage_client_does_not_retry_ambiguous_network_errors(monkeypatch):
    effective = nodeimage_client.NodeImageEffectiveSettings(True, "test-key")

    async def no_sleep(_delay):
        raise AssertionError("ambiguous network failures must not be retried")

    monkeypatch.setattr(nodeimage_client.asyncio, "sleep", no_sleep)

    client_error_session = _Session([aiohttp.ClientConnectionError("temporary")])
    monkeypatch.setattr(
        nodeimage_client,
        "get_pool",
        lambda: _Pool(client_error_session),
    )
    with pytest.raises(
        nodeimage_client.NodeImageUploadError,
        match="upload request failed",
    ):
        asyncio.run(
            nodeimage_client.upload_image_bytes(PNG_BYTES, "client-error.png", effective)
        )
    assert len(client_error_session.calls) == 1

    timeout_session = _Session([_Response(200, asyncio.TimeoutError())])
    monkeypatch.setattr(
        nodeimage_client,
        "get_pool",
        lambda: _Pool(timeout_session),
    )
    with pytest.raises(
        nodeimage_client.NodeImageUploadError,
        match="unreadable response",
    ):
        asyncio.run(
            nodeimage_client.upload_image_bytes(PNG_BYTES, "timeout.png", effective)
        )
    assert len(timeout_session.calls) == 1


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
