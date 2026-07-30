import asyncio
import base64
import io
import sqlite3
import time
from pathlib import Path

from PIL import Image

from backend.app.api.routers import metrics as metrics_router
from backend.app.core import settings as config
from backend.app.integrations.upstream import transport as upstream_transport
from backend.app.repositories import db as db_repo
from backend.app.repositories.coordination import mark_worker_heartbeat
from backend.app.repositories.image_files import validate_image_bytes
from backend.app.services.blocking import (
    close_blocking_executors,
    run_db_operation,
    run_image_operation,
    upstream_memory_lease,
)


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
