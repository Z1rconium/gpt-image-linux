import asyncio
import base64
import io
import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from backend.app.api.routers import metrics as metrics_router
from backend.app.api.routers import gallery_queries as gallery_queries_router
from backend.app.core import redaction, secrets
from backend.app.core import settings as config
from backend.app.integrations.upstream import transport as upstream_transport
from backend.app.repositories import db as db_repo
from backend.app.repositories.coordination import mark_worker_heartbeat
from backend.app.repositories.image_files import validate_image_bytes
from backend.app.repositories import thumbnail_jobs as thumbnail_jobs_repo
from backend.app.services.blocking import (
    close_blocking_executors,
    run_db_operation,
    run_db_operation_in_current_thread,
    run_image_operation,
    upstream_memory_lease,
)
from backend.app.services import gallery_jobs, job_events


def _configure_runtime(tmp_path: Path) -> None:
    images_dir = tmp_path / "images"
    data_dir = tmp_path / "data"
    images_dir.mkdir()
    data_dir.mkdir()
    config.IMAGES_DIR = str(images_dir)
    config.THUMBNAILS_DIR = str(images_dir / "thumbs")
    config.DATA_DIR = str(data_dir)
    config.DATABASE_FILE = str(data_dir / "app.sqlite3")
    db_repo.close_database_connections()
    db_repo.verify_storage_writable()


def test_sqlite_lock_wait_does_not_block_event_loop(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    monkeypatch.setattr(config, "SQLITE_BUSY_TIMEOUT_MS", 30)
    monkeypatch.setattr(config, "SQLITE_BUSY_RETRY_ATTEMPTS", 8)
    monkeypatch.setattr(config, "SQLITE_BUSY_RETRY_BASE_MS", 5)

    async def scenario() -> list[float]:
        blocker = sqlite3.connect(config.DATABASE_FILE, timeout=0.1)
        blocker.execute("BEGIN IMMEDIATE")
        write_task = asyncio.create_task(
            run_db_operation(
                mark_worker_heartbeat,
                "locked-worker",
                0,
                metric_name="lock_contention_test",
            )
        )
        lags: list[float] = []
        expected = time.monotonic() + 0.01
        for index in range(20):
            await asyncio.sleep(0.01)
            now = time.monotonic()
            lags.append(max(0.0, now - expected))
            expected = now + 0.01
            if index == 10:
                blocker.rollback()
                blocker.close()
        await write_task
        return lags

    lags = asyncio.run(scenario())
    assert max(lags) < 0.05


def test_full_image_decode_runs_off_event_loop(tmp_path):
    _configure_runtime(tmp_path)
    buffer = io.BytesIO()
    Image.new("RGB", (3000, 3000), (20, 40, 60)).save(buffer, format="PNG")
    image_bytes = buffer.getvalue()

    async def scenario() -> tuple[str, list[float]]:
        validation = asyncio.create_task(
            run_image_operation(
                validate_image_bytes,
                image_bytes,
                filename="large.png",
                metric_name="event_loop_image_validation_test",
            )
        )
        lags: list[float] = []
        expected = time.monotonic() + 0.005
        while not validation.done():
            await asyncio.sleep(0.005)
            now = time.monotonic()
            lags.append(max(0.0, now - expected))
            expected = now + 0.005
        return await validation, lags

    detected_format, lags = asyncio.run(scenario())
    assert detected_format == "png"
    assert not lags or max(lags) < 0.05


def test_base64_decode_runs_off_event_loop(monkeypatch):
    encoded = base64.b64encode(b"image-bytes").decode("ascii")
    original_decode = upstream_transport.base64.b64decode

    def slow_decode(value):
        time.sleep(0.08)
        return original_decode(value)

    monkeypatch.setattr(upstream_transport.base64, "b64decode", slow_decode)

    async def scenario() -> tuple[bytes, float]:
        expected = time.monotonic() + 0.01
        decoding = asyncio.create_task(
            upstream_transport.extract_image_bytes(
                None,
                {"b64_json": encoded},
                "",
                1024,
            )
        )
        await asyncio.sleep(0.01)
        lag = max(0.0, time.monotonic() - expected)
        return await decoding, lag

    decoded, lag = asyncio.run(scenario())
    assert decoded == b"image-bytes"
    assert lag < 0.05


def test_db_executor_reuses_worker_connection(tmp_path, monkeypatch):
    asyncio.run(close_blocking_executors())
    _configure_runtime(tmp_path)
    monkeypatch.setattr(config, "DB_EXECUTOR_WORKERS", 1)
    opened = 0
    original_open = db_repo._open_connection

    def tracked_open(*args, **kwargs):
        nonlocal opened
        opened += 1
        return original_open(*args, **kwargs)

    monkeypatch.setattr(db_repo, "_open_connection", tracked_open)

    async def scenario():
        await run_db_operation(
            mark_worker_heartbeat,
            "reuse-worker",
            0,
            metric_name="connection_reuse_first",
        )
        await run_db_operation(
            mark_worker_heartbeat,
            "reuse-worker",
            1,
            metric_name="connection_reuse_second",
        )

    asyncio.run(scenario())
    assert opened == 1
    asyncio.run(close_blocking_executors())


def test_current_thread_db_operation_uses_short_timeout_without_persisting_connection(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    monkeypatch.setattr(config, "SQLITE_BUSY_TIMEOUT_MS", 17)
    seen: dict[str, object] = {}
    original_open = db_repo._open_connection

    def tracked_open(*args, **kwargs):
        seen["busy_timeout_ms"] = kwargs.get("busy_timeout_ms")
        return original_open(*args, **kwargs)

    monkeypatch.setattr(db_repo, "_open_connection", tracked_open)

    run_db_operation_in_current_thread(
        mark_worker_heartbeat,
        "current-thread-worker",
        0,
        metric_name="current_thread_busy_timeout_test",
    )

    assert seen["busy_timeout_ms"] == 17
    assert getattr(db_repo._thread_local, "conn", None) is None


def test_gallery_job_publish_uses_db_executor(monkeypatch):
    calls: list[tuple[object, tuple[object, ...], str | None]] = []
    published: list[dict] = []

    async def fake_run_db_operation(callback, *args, metric_name=None, **kwargs):
        calls.append((callback, args, metric_name))
        return {"kind": "export", "job_id": "gallery-job-1"}

    monkeypatch.setattr(gallery_jobs, "run_db_operation", fake_run_db_operation)
    monkeypatch.setattr(gallery_jobs, "_publish_gallery_job_sse", published.append)

    result = asyncio.run(
        gallery_jobs._publish_gallery_job("gallery-job-1", {"status": "running"})
    )

    assert result == {"kind": "export", "job_id": "gallery-job-1"}
    assert calls == [
        (
            gallery_jobs.update_gallery_job,
            ("gallery-job-1", {"status": "running"}),
            "update_gallery_job",
        )
    ]
    assert published == [{"kind": "export", "job_id": "gallery-job-1"}]


def test_gallery_job_worker_progress_stays_in_worker_db_path(monkeypatch):
    calls: list[tuple[object, tuple[object, ...], str | None]] = []

    def fake_run_db_operation_in_current_thread(callback, *args, metric_name=None, **kwargs):
        calls.append((callback, args, metric_name))
        return True

    monkeypatch.setattr(
        gallery_jobs,
        "run_db_operation_in_current_thread",
        fake_run_db_operation_in_current_thread,
    )

    assert gallery_jobs._publish_gallery_job_progress_from_worker(
        "gallery-job-1",
        {"progress": 50},
    ) is True
    assert calls == [
        (
            gallery_jobs.update_gallery_job_progress,
            ("gallery-job-1", {"progress": 50}),
            "update_gallery_job_progress",
        )
    ]


def test_gallery_job_worker_progress_failure_is_best_effort(monkeypatch):
    def failing_current_thread_db_operation(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        gallery_jobs,
        "run_db_operation_in_current_thread",
        failing_current_thread_db_operation,
    )

    assert gallery_jobs._publish_gallery_job_progress_from_worker(
        "gallery-job-1",
        {"progress": 50},
    ) is False


def test_gallery_page_query_uses_db_executor(monkeypatch):
    calls: list[tuple[object, str | None]] = []
    page = SimpleNamespace(
        total=0,
        total_bytes=0,
        page=1,
        page_size=9,
        total_pages=0,
        has_prev=False,
        has_next=False,
        next_cursor=None,
        prev_cursor=None,
        images=[],
        filter_options={},
        query_elapsed_ms=0.0,
        timings_ms={},
        counts_included=True,
        filter_options_included=True,
    )

    async def fake_run_db_operation(callback, *args, metric_name=None, **kwargs):
        calls.append((callback, metric_name))
        return page

    monkeypatch.setattr(gallery_queries_router, "run_db_operation", fake_run_db_operation)

    response = asyncio.run(
        gallery_queries_router._query_gallery(
            page=1,
            page_size=9,
            prompt=None,
            model=None,
            preset=None,
            size=None,
            date_from=None,
            date_to=None,
            favorite=None,
            include_total_bytes=False,
            include_counts=True,
            include_filter_options=True,
            cursor=None,
            direction="next",
        )
    )

    assert response.total == 0
    assert calls == [(gallery_queries_router.get_gallery_page, "get_gallery_page")]


def test_webhook_delivery_tasks_are_strongly_tracked(monkeypatch):
    async def scenario() -> None:
        job_events.app.state.webhook_delivery_tasks = set()
        started = asyncio.Event()

        async def fake_deliver_webhook(webhook_url, job):
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(job_events.webhooks, "deliver_webhook", fake_deliver_webhook)

        task = job_events._create_webhook_delivery_task(
            "https://hooks.example.com/job",
            {"job_id": "job-1", "status": "success", "webhook_url": "secret"},
        )
        await started.wait()
        assert task in job_events.get_webhook_delivery_tasks()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        assert task not in job_events.get_webhook_delivery_tasks()

    asyncio.run(scenario())


def test_thread_connection_reuse_does_not_stat_database_path(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    conn = db_repo._get_thread_connection()

    def fail_path(*args, **kwargs):
        raise AssertionError("database Path() should not be checked when reusing a connection")

    try:
        monkeypatch.setattr(db_repo, "Path", fail_path)
        assert db_repo._get_thread_connection() is conn
    finally:
        db_repo._close_thread_connection()


def test_thumbnail_cpu_slot_uses_exponential_backoff(monkeypatch):
    attempts = iter([None, None, "thumbnail_cpu_0"])
    sleeps: list[float] = []
    released: list[tuple[str, str]] = []

    def fake_acquire_background_slot(**kwargs):
        return next(attempts)

    def fake_release_background_slot(*, name, owner):
        released.append((name, owner))
        return True

    monkeypatch.setattr(
        thumbnail_jobs_repo,
        "acquire_background_slot",
        fake_acquire_background_slot,
    )
    monkeypatch.setattr(
        thumbnail_jobs_repo,
        "release_background_slot",
        fake_release_background_slot,
    )
    monkeypatch.setattr(thumbnail_jobs_repo.time, "sleep", sleeps.append)

    with thumbnail_jobs_repo._thumbnail_cpu_slot():
        pass

    assert sleeps == [0.05, 0.1]
    assert released[0][0] == "thumbnail_cpu_0"


def test_redaction_skips_regex_for_plain_text_but_still_masks_cached_secret(monkeypatch):
    class ExplodingRegex:
        def sub(self, *args, **kwargs):
            raise AssertionError("regex redaction should be skipped for plain text")

    monkeypatch.setattr(redaction, "_URL_RE", ExplodingRegex())
    monkeypatch.setattr(redaction, "_AUTH_RE", ExplodingRegex())
    monkeypatch.setattr(redaction, "_BEARER_RE", ExplodingRegex())
    monkeypatch.setattr(redaction, "_CREDENTIAL_RE", ExplodingRegex())
    monkeypatch.setattr(secrets, "active_secret_values", lambda: ("needle-value",))

    assert redaction.redact_sensitive_text("ordinary message") == "ordinary message"
    assert redaction.redact_sensitive_text("contains needle-value") == "contains [REDACTED]"


def test_active_secret_values_cache_reuses_registry_resolution(monkeypatch):
    monkeypatch.setenv("CACHED_SECRET_VALUE", "cached-secret")
    payload = {
        "cached-secret-id": {
            "purpose": "upstream_api",
            "origin": "https://api.example.com",
            "env": "CACHED_SECRET_VALUE",
        }
    }
    secrets.configure_registry(json.dumps(payload))
    original_getenv = secrets.os.getenv
    calls = 0

    def tracked_getenv(name, default=None):
        nonlocal calls
        if name == "CACHED_SECRET_VALUE":
            calls += 1
        return original_getenv(name, default)

    monkeypatch.setattr(secrets.os, "getenv", tracked_getenv)
    try:
        assert "cached-secret" in secrets.active_secret_values()
        assert "cached-secret" in secrets.active_secret_values()
        assert calls == 1
    finally:
        secrets.configure_registry("{}")


def test_active_secret_values_includes_admin_and_cdn_runtime_secrets(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_KEY", "admin-runtime-secret")
    monkeypatch.setattr(config, "CDN_SIGNING_SECRET", "cdn-runtime-secret")
    secrets.invalidate_active_secret_values_cache()
    try:
        values = secrets.active_secret_values()
        assert "admin-runtime-secret" in values
        assert "cdn-runtime-secret" in values
    finally:
        secrets.invalidate_active_secret_values_cache()


def test_thumbnail_cpu_concurrency_override_is_applied(monkeypatch):
    original = config.THUMBNAIL_CPU_CONCURRENCY
    monkeypatch.setattr(config, "THUMBNAIL_CPU_CONCURRENCY", 1)
    try:
        from backend.app.core.overall_config import apply_rows_to_config

        apply_rows_to_config(
            {
                "THUMBNAIL_CPU_CONCURRENCY": {
                    "override_value": "3",
                    "env_value": "",
                    "is_env_set": False,
                }
            },
            overrides_only=True,
        )
        assert config.THUMBNAIL_CPU_CONCURRENCY == 3
    finally:
        config.THUMBNAIL_CPU_CONCURRENCY = original


def test_metrics_snapshot_is_database_free(monkeypatch):
    metrics_router.app.state.generate_jobs = {}
    metrics_router.app.state.generate_job_tasks = {}
    metrics_router.app.state.image_queue_runtime_metrics = {}
    metrics_router.app.state.runtime_coordination_metrics = {
        "gauges": {},
        "background_leases": [],
        "workers": [],
    }
    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("metrics scrape opened SQLite")
        ),
    )
    snapshot = metrics_router._metrics_snapshot()
    assert snapshot["gauges"]["image_jobs.active"] == 0


def test_upstream_memory_budget_serializes_weighted_tasks(monkeypatch):
    monkeypatch.setattr(config, "UPSTREAM_MEMORY_BUDGET_MB", 1)

    async def scenario() -> int:
        active = 0
        max_active = 0

        async def worker():
            nonlocal active, max_active
            async with upstream_memory_lease(1024 * 1024):
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.02)
                active -= 1

        await asyncio.gather(worker(), worker())
        return max_active

    assert asyncio.run(scenario()) == 1
