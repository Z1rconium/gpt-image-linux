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
    sizes = ("1024x1024", "1536x1024", "1024x1536")
    models = ("gpt-image-2", "gpt-image-1")
    presets = ("Default", "Studio", "Draft")
    with storage._connect() as conn:
        for start in range(0, row_count, 5_000):
            rows = [
                {
                    "id": f"img-{index:06d}",
                    "prompt": f"benchmark prompt {index % 100} p{index % 10}",
                    "size": sizes[index % len(sizes)],
                    "filename": f"img-{index:06d}.png",
                    "created_at": f"{now_prefix}{index % 60:02d}:{index % 60:02d}",
                    "model": models[index % len(models)],
                    "quality": "auto",
                    "output_format": "png",
                    "n": 1,
                    "api_path": "/v1/images/generations",
                    "api_preset_name": presets[index % len(presets)],
                    "favorite": index % 7 == 0,
                    "bytes": 128 + (index % 8192),
                }
                for index in range(start, min(start + 5_000, row_count))
            ]
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


@pytest.mark.parametrize("row_count", [50_000, 100_000])
@pytest.mark.parametrize(
    ("case_name", "page", "filters", "include_total_bytes", "include_counts", "include_filter_options"),
    [
        ("first_page_no_prompt", 1, {}, False, True, True),
        ("deep_page_no_prompt", 5_000, {}, False, True, False),
        (
            "combined_filters",
            1,
            {
                "model": "gpt-image-2",
                "preset": "Default",
                "size": "1024x1024",
                "favorite": True,
            },
            False,
            True,
            False,
        ),
        ("short_prompt_like", 1, {"prompt": "p4"}, False, True, False),
        ("total_bytes", 1, {}, True, True, False),
        ("lightweight_cursor_page", 2, {}, False, False, False),
    ],
)
def test_gallery_large_query_baselines(
    tmp_path,
    row_count,
    case_name,
    page,
    filters,
    include_total_bytes,
    include_counts,
    include_filter_options,
    record_property,
):
    _configure_runtime(tmp_path)
    _seed_gallery_rows(row_count)
    cursor = None
    direction = "next"
    if case_name == "lightweight_cursor_page":
        first_page = storage.get_gallery_page(
            page=1,
            page_size=9,
            filters=filters,
            include_counts=False,
            include_filter_options=False,
        )
        assert first_page.next_cursor
        cursor = first_page.next_cursor

    def query():
        gallery_page = storage.get_gallery_page(
            page=page,
            page_size=9,
            filters=filters,
            include_total_bytes=include_total_bytes,
            include_counts=include_counts,
            include_filter_options=include_filter_options,
            cursor=cursor,
            direction=direction,
        )
        assert gallery_page.images or page > gallery_page.total_pages
        if include_counts:
            assert gallery_page.total >= 0
        if include_total_bytes:
            assert gallery_page.total_bytes > 0

    p50, p95 = _measure_ms(query, iterations=12)
    prefix = f"gallery_{row_count}_rows_{case_name}"
    record_property(f"{prefix}_p50_ms", round(p50, 2))
    record_property(f"{prefix}_p95_ms", round(p95, 2))
    assert p95 < 3000


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
