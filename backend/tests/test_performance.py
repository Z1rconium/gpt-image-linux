import os
import statistics
import time
from pathlib import Path

import pytest

from backend.app.core import settings as config
from backend.app.core.observability import metrics
from backend.app.repositories import storage


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_PERFORMANCE_TESTS") != "true",
    reason="set RUN_PERFORMANCE_TESTS=true to run performance baselines",
)


def _configure_runtime(tmp_path: Path):
    images_dir = tmp_path / "images"
    data_dir = tmp_path / "data"
    images_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    config.IMAGES_DIR = str(images_dir)
    config.THUMBNAILS_DIR = str(images_dir / "thumbs")
    config.DATA_DIR = str(data_dir)
    config.DATABASE_FILE = str(data_dir / "app.sqlite3")
    config.DEFAULT_UPSTREAM_SOCKS5_PROXY = ""

    storage.close_database_connections()
    storage._db_initialized = False
    storage._dirs_initialized = False
    metrics.reset()
    storage.verify_storage_writable()


def _seed_gallery_rows(row_count: int):
    now_prefix = "2026-05-18T12:"
    rows = [
        {
            "id": f"img-{index:05d}",
            "prompt": f"benchmark prompt {index % 100}",
            "size": "1024x1024",
            "filename": f"img-{index:05d}.png",
            "created_at": f"{now_prefix}{index % 60:02d}:{index % 60:02d}",
            "model": "gpt-image-2",
            "quality": "auto",
            "output_format": "png",
            "n": 1,
            "api_path": "/v1/images/generations",
            "api_preset_name": "Default",
            "favorite": index % 7 == 0,
            "bytes": 128,
        }
        for index in range(row_count)
    ]
    with storage._connect() as conn:
        with storage._transaction(conn):
            storage._insert_gallery_entries_on_conn(conn, rows)


def _seed_job_rows(row_count: int):
    for index in range(row_count):
        storage.upsert_generate_job(
            {
                "job_id": f"job-{index:04d}",
                "status": "success",
                "stage": "completed",
                "message": "completed",
                "operation": "generation",
                "prompt": f"history prompt {index}",
                "size": "1024x1024",
                "created_at": f"2026-05-18T12:{index % 60:02d}:00",
                "updated_at": f"2026-05-18T12:{index % 60:02d}:01",
                "completed_at": f"2026-05-18T20:{index % 60:02d}:01+08:00",
                "model": "gpt-image-2",
                "duration": "1.00s",
            }
        )


def _measure_ms(callback, iterations: int = 30) -> tuple[float, float]:
    durations = []
    for _ in range(iterations):
        started_at = time.perf_counter()
        callback()
        durations.append((time.perf_counter() - started_at) * 1000)
    p50 = statistics.median(durations)
    p95 = statistics.quantiles(durations, n=100, method="inclusive")[94]
    return p50, p95


@pytest.mark.parametrize("row_count", [1_000, 10_000])
def test_gallery_page_query_baseline(tmp_path, row_count, record_property):
    _configure_runtime(tmp_path)
    _seed_gallery_rows(row_count)

    def query():
        page = storage.get_gallery_page(
            page=1,
            page_size=9,
            filters={"prompt": "benchmark prompt 4"},
            include_total_bytes=True,
        )
        assert page.total > 0

    p50, p95 = _measure_ms(query)
    record_property(f"gallery_{row_count}_rows_p50_ms", round(p50, 2))
    record_property(f"gallery_{row_count}_rows_p95_ms", round(p95, 2))
    assert p95 < 500


def test_gallery_cursor_query_baseline(tmp_path, record_property):
    _configure_runtime(tmp_path)
    _seed_gallery_rows(10_000)
    first_page = storage.get_gallery_page(
        page=1,
        page_size=9,
        filters={"prompt": "benchmark prompt 4"},
    )
    assert first_page.next_cursor

    def query():
        page = storage.get_gallery_page(
            page=2,
            page_size=9,
            filters={"prompt": "benchmark prompt 4"},
            cursor=first_page.next_cursor,
            direction="next",
        )
        assert page.total > 0

    p50, p95 = _measure_ms(query)
    record_property("gallery_10000_rows_cursor_p50_ms", round(p50, 2))
    record_property("gallery_10000_rows_cursor_p95_ms", round(p95, 2))
    assert p95 < 250


def test_job_history_query_baseline(tmp_path, record_property):
    _configure_runtime(tmp_path)
    _seed_job_rows(500)

    def query():
        rows = storage.list_generate_jobs(limit=50, offset=0)
        assert len(rows) == 50

    p50, p95 = _measure_ms(query)
    record_property("job_history_500_rows_p50_ms", round(p50, 2))
    record_property("job_history_500_rows_p95_ms", round(p95, 2))
    assert p95 < 200
