import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.tests.support.contract import *  # noqa: F403
from backend.app.api import body_limit, middleware as api_middleware
from backend.app.api.routers import edits as edits_router
from backend.app.services import webhook_service


def test_admin_lockout_is_independent_and_success_clears_only_admin(tmp_path):
    _configure_runtime(tmp_path, access_key="", allow_unauthenticated=True)
    config.ADMIN_KEY = "admin-secret"
    coordination_repo.record_access_failure(
        "testclient",
        lockout_seconds=300,
        max_entries=100,
    )

    with _test_client(raise_server_exceptions=False) as client:
        for _ in range(config.ADMIN_MAX_FAILURES):
            response = client.post(
                "/api/access/admin",
                json={"admin_key": "wrong"},
            )
            assert response.status_code == 403

        locked = client.post(
            "/api/access/admin",
            json={"admin_key": "admin-secret"},
        )
        assert locked.status_code == 429

        coordination_repo.clear_admin_failure("testclient")
        unlocked = client.post(
            "/api/access/admin",
            json={"admin_key": "admin-secret"},
        )
        assert unlocked.status_code == 200

    assert coordination_repo.list_admin_failures() == []
    assert len(coordination_repo.list_access_failures()) == 1


def test_admin_lockout_expires_and_is_sqlite_persistent(tmp_path):
    _configure_runtime(tmp_path)
    for index in range(5):
        assert coordination_repo.record_admin_failure(
            "2001:0db8::1",
            lockout_seconds=300,
            max_entries=100,
            now=1000 + index,
        ) == index + 1
    db_repo.close_database_connections()
    assert coordination_repo.get_admin_lockout(
        "2001:db8::1",
        max_failures=5,
        lockout_seconds=300,
        now=1100,
    ) > 0
    assert coordination_repo.get_admin_lockout(
        "2001:db8::1",
        max_failures=5,
        lockout_seconds=300,
        now=1400,
    ) == 0


def _reserve_upload(
    reservation_id: str,
    client_ip: str,
    byte_count: int,
    *,
    now: float = 1000,
    total: int = 100,
    per_ip: int = 100,
):
    return coordination_repo.reserve_upload_capacity(
        reservation_id=reservation_id,
        client_ip=client_ip,
        route="edit",
        byte_count=byte_count,
        max_total_bytes=total,
        max_per_ip_bytes=per_ip,
        lease_ttl_seconds=30,
        now=now,
    )


def test_upload_reservations_are_atomic_per_ip_and_global(tmp_path):
    _configure_runtime(tmp_path)
    assert _reserve_upload("one", "192.0.2.1", 60, per_ip=70)[0]
    assert _reserve_upload("two", "192.0.2.1", 20, per_ip=70) == (
        False,
        "ip_bytes",
    )
    assert _reserve_upload("three", "192.0.2.2", 50) == (
        False,
        "global_bytes",
    )
    assert coordination_repo.release_upload_reservation("one")
    assert _reserve_upload("three", "192.0.2.2", 50)[0]

    coordination_repo.release_upload_reservation("three")
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda identifier: _reserve_upload(identifier, identifier, 60),
                ("worker-a", "worker-b"),
            )
        )
    assert sorted(result[0] for result in results) == [False, True]


def test_upload_reservation_expiry_and_import_rate_events(tmp_path):
    _configure_runtime(tmp_path)
    assert _reserve_upload("expired", "192.0.2.1", 100, now=1000)[0]
    assert _reserve_upload("replacement", "192.0.2.2", 100, now=1031)[0]
    assert [row["reservation_id"] for row in coordination_repo.list_upload_reservations(now=1031)] == [
        "replacement"
    ]

    for index in range(3):
        reservation_id = f"import-{index}"
        assert coordination_repo.reserve_upload_capacity(
            reservation_id=reservation_id,
            client_ip="192.0.2.3",
            route="import",
            byte_count=1,
            max_total_bytes=100,
            max_per_ip_bytes=100,
            lease_ttl_seconds=30,
            import_rate_limit=3,
            now=2000 + index,
        )[0]
        coordination_repo.release_upload_reservation(reservation_id)
    assert coordination_repo.reserve_upload_capacity(
        reservation_id="import-blocked",
        client_ip="192.0.2.3",
        route="import",
        byte_count=1,
        max_total_bytes=100,
        max_per_ip_bytes=100,
        lease_ttl_seconds=30,
        import_rate_limit=3,
        now=2003,
    ) == (False, "ip_rate")


def test_active_upload_reservation_can_be_renewed(tmp_path):
    _configure_runtime(tmp_path)
    assert _reserve_upload("active", "192.0.2.1", 100, now=1000)[0]
    assert coordination_repo.renew_upload_reservation(
        "active",
        lease_ttl_seconds=30,
        now=1020,
    )
    assert _reserve_upload("blocked", "192.0.2.2", 100, now=1031) == (
        False,
        "global_bytes",
    )
    rows = coordination_repo.list_upload_reservations(now=1031)
    assert rows[0]["reservation_id"] == "active"
    assert rows[0]["lease_expires_at"] == 1050
    assert not coordination_repo.renew_upload_reservation(
        "missing",
        lease_ttl_seconds=30,
        now=1031,
    )


def test_unauthenticated_upload_does_not_reserve_capacity(tmp_path):
    _configure_runtime(tmp_path, access_key="secret", allow_unauthenticated=False)
    with _test_client(raise_server_exceptions=False) as client:
        response = client.post(
            "/api/edits",
            data={"prompt": "blocked"},
            files={"image": ("input.png", PNG_BYTES, "image/png")},
        )
    assert response.status_code == 401
    assert coordination_repo.list_upload_reservations() == []


def test_edit_multipart_file_boundary_and_unexpected_field(client):
    files = [
        ("image", (f"input-{index}.png", PNG_BYTES, "image/png"))
        for index in range(8)
    ]
    accepted = client.post(
        "/api/edits",
        data={"prompt": "eight inputs", "model": "gpt-image-2"},
        files=files,
    )
    assert accepted.status_code == 202

    rejected = client.post(
        "/api/edits",
        data={"prompt": "nine inputs", "model": "gpt-image-2"},
        files=files + [("image", ("ninth.png", PNG_BYTES, "image/png"))],
    )
    assert rejected.status_code == 400

    unexpected = client.post(
        "/api/edits",
        data={"prompt": "unexpected field", "model": "gpt-image-2"},
        files={"attachment": ("input.png", PNG_BYTES, "image/png")},
    )
    assert unexpected.status_code == 400
    assert unexpected.json()["detail"] == "Unexpected upload field: attachment"


def test_upload_reservation_precedes_parsing_and_releases_after_response(
    client,
    monkeypatch,
):
    original_parse = edits_router.parse_limited_multipart
    observed = []

    async def checked_parse(*args, **kwargs):
        rows = coordination_repo.list_upload_reservations()
        observed.extend(rows)
        assert len(rows) == 1
        assert rows[0]["route"] == "edit"
        return await original_parse(*args, **kwargs)

    monkeypatch.setattr(edits_router, "parse_limited_multipart", checked_parse)
    response = client.post(
        "/api/edits",
        data={"prompt": "reserved before parse", "model": "gpt-image-2"},
        files={"image": ("input.png", PNG_BYTES, "image/png")},
    )

    assert response.status_code == 202
    assert observed
    assert coordination_repo.list_upload_reservations() == []


def test_upload_reservation_is_renewed_while_request_is_active(client, monkeypatch):
    original_parse = edits_router.parse_limited_multipart
    renewal_started = asyncio.Event()

    async def tracked_renewal(*args, **kwargs):
        renewal_started.set()
        await asyncio.Event().wait()

    async def checked_parse(*args, **kwargs):
        await asyncio.wait_for(renewal_started.wait(), timeout=1)
        return await original_parse(*args, **kwargs)

    monkeypatch.setattr(
        api_middleware,
        "_maintain_upload_reservation",
        tracked_renewal,
    )
    monkeypatch.setattr(edits_router, "parse_limited_multipart", checked_parse)
    response = client.post(
        "/api/edits",
        data={"prompt": "renew active reservation", "model": "gpt-image-2"},
        files={"image": ("input.png", PNG_BYTES, "image/png")},
    )

    assert response.status_code == 202
    assert coordination_repo.list_upload_reservations() == []


def test_upload_reservation_releases_when_parser_fails(client, monkeypatch):
    async def failed_parse(*args, **kwargs):
        assert len(coordination_repo.list_upload_reservations()) == 1
        raise RuntimeError("parser failed")

    monkeypatch.setattr(edits_router, "parse_limited_multipart", failed_parse)
    with pytest.raises(RuntimeError, match="parser failed"):
        client.post(
            "/api/edits",
            data={"prompt": "parser failure"},
            files={"image": ("input.png", PNG_BYTES, "image/png")},
        )

    assert coordination_repo.list_upload_reservations() == []


def _multipart_edit_body(boundary: str, image_bytes: bytes) -> bytes:
    return (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="prompt"\r\n\r\n'
        "chunked upload\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="image"; filename="input.png"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ).encode("ascii") + image_bytes + f"\r\n--{boundary}--\r\n".encode("ascii")


def test_unknown_length_upload_reserves_route_max(client, monkeypatch):
    config.EDIT_UPLOAD_MAX_MB = 1
    boundary = "security-boundary"
    body = _multipart_edit_body(boundary, PNG_BYTES)
    captured = []
    original_reserve = api_middleware.reserve_upload_capacity

    def tracked_reserve(**kwargs):
        captured.append(kwargs["byte_count"])
        return original_reserve(**kwargs)

    monkeypatch.setattr(api_middleware, "reserve_upload_capacity", tracked_reserve)
    response = client.post(
        "/api/edits",
        content=(chunk for chunk in (body[:32], body[32:])),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    assert response.status_code == 202
    assert captured == [1024 * 1024]
    assert coordination_repo.list_upload_reservations() == []


def test_import_reservation_excludes_bounded_multipart_overhead():
    config.IMPORT_ARCHIVE_MAX_MB = 256
    content_type = "multipart/form-data; boundary=security-boundary"

    assert body_limit.upload_reservation_policy(
        "/api/import",
        content_type,
    ) == ("import", 256 * 1024 * 1024)
    assert body_limit._max_body_for_path(
        "/api/import",
        content_type,
    ) == 256 * 1024 * 1024 + 64 * 1024


def test_chunked_and_declared_oversized_uploads_return_413(client):
    config.EDIT_UPLOAD_MAX_MB = 1
    boundary = "oversized-boundary"
    oversized_body = _multipart_edit_body(boundary, b"x" * (1024 * 1024))
    content_type = f"multipart/form-data; boundary={boundary}"

    chunked = client.post(
        "/api/edits",
        content=(
            oversized_body[index : index + 64 * 1024]
            for index in range(0, len(oversized_body), 64 * 1024)
        ),
        headers={"Content-Type": content_type},
    )
    declared = client.post(
        "/api/edits",
        content=b"small",
        headers={
            "Content-Type": content_type,
            "Content-Length": str(1024 * 1024 + 1),
        },
    )

    assert chunked.status_code == 413
    assert declared.status_code == 413
    assert coordination_repo.list_upload_reservations() == []


def test_manual_upload_routes_keep_multipart_openapi_contract():
    spec = backend_main.app.openapi()
    paths = (
        "/api/edits",
        "/api/edits/from-gallery/{image_id}",
        "/api/import",
        "/api/assistant/image/prompt",
        "/api/assistant/image/prompt/optimize",
    )
    for path in paths:
        request_body = spec["paths"][path]["post"]["requestBody"]
        assert request_body["required"] is True
        assert "multipart/form-data" in request_body["content"]

    edit_schema = spec["paths"]["/api/edits"]["post"]["requestBody"]["content"][
        "multipart/form-data"
    ]["schema"]
    gallery_edit_schema = spec["paths"]["/api/edits/from-gallery/{image_id}"][
        "post"
    ]["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert edit_schema["properties"]["image"]["maxItems"] == MAX_EDIT_SOURCE_IMAGES
    assert gallery_edit_schema["properties"]["image"]["maxItems"] == (
        MAX_EDIT_SOURCE_IMAGES - 1
    )


def test_webhook_shutdown_drops_without_logging_secrets(monkeypatch, caplog):
    async def scenario():
        state = types.SimpleNamespace()
        started = asyncio.Event()
        secret_url = "https://hooks.example.com/callback?token=top-secret"
        monkeypatch.setattr(config, "WEBHOOK_MAX_CONCURRENCY", 1)
        monkeypatch.setattr(config, "WEBHOOK_QUEUE_MAX_SIZE", 1)

        async def blocked_delivery(webhook_url, job):
            assert webhook_url == secret_url
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(webhook_service, "deliver_webhook", blocked_delivery)
        webhook_service.start_webhook_workers(state)
        assert webhook_service.enqueue_webhook(
            state,
            secret_url,
            {"job_id": "active", "webhook_url": secret_url},
        )
        await started.wait()
        assert webhook_service.enqueue_webhook(
            state,
            secret_url,
            {"job_id": "queued", "webhook_url": secret_url},
        )
        assert not webhook_service.enqueue_webhook(
            state,
            secret_url,
            {"job_id": "dropped", "webhook_url": secret_url},
        )
        await webhook_service.stop_webhook_workers(state)
        assert state.webhook_delivery_accepting is False
        assert state.webhook_delivery_workers == []
        assert state.webhook_delivery_queue.empty()
        assert secret_url not in caplog.text
        assert "top-secret" not in caplog.text

    asyncio.run(scenario())
