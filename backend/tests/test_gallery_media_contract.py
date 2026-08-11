from backend.tests.support.contract import *  # noqa: F403

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
    assert "'prompt_present': True" in caplog.text
    assert "'prompt_len': 4" in caplog.text
    assert "slow query prompt" not in caplog.text
    assert "'prompt_hash':" in caplog.text
    assert metrics.snapshot()["counters"]["sqlite.slow_queries"] == 1
def test_gallery_image_download_and_zip(client, monkeypatch):
    original_generate_thumbnail = thumbnail_jobs_repo.generate_thumbnail_for_image
    monkeypatch.setattr(gallery_maintenance, "generate_thumbnail_for_image", lambda filename: None)
    entry = _fake_gallery_entry("gallery-zip", "zip me", "1024x1024", "gallery-zip.png")
    assert entry.bytes == len(PNG_BYTES)
    assert entry.thumbnail_filename is None
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
    assert image.headers["content-type"].startswith("image/png")
    assert image.headers["content-length"] == str(len(PNG_BYTES))
    assert image.headers["accept-ranges"] == "bytes"
    assert image.headers.get("content-encoding") is None
    assert image.headers["cache-control"] == "public, max-age=31536000, immutable"

    image_range = client.get(
        "/api/image/gallery-zip.png",
        headers={"Accept-Encoding": "gzip", "Range": "bytes=0-7"},
    )
    assert image_range.status_code == 206
    assert image_range.content == PNG_BYTES[:8]
    assert image_range.headers["content-length"] == "8"
    assert image_range.headers["content-range"] == f"bytes 0-7/{len(PNG_BYTES)}"
    assert image_range.headers.get("content-encoding") is None

    thumb = client.get("/api/thumb/gallery-zip.png")
    assert thumb.status_code == 404
    assert thumb.headers["cache-control"] == "no-cache"
    assert gallery_queries.get_gallery_entry("gallery-zip").thumbnail_filename is None

    monkeypatch.setattr(gallery_maintenance, "generate_thumbnail_for_image", original_generate_thumbnail)
    assert thumbnail_jobs_repo.generate_thumbnail_for_image("gallery-zip.png")
    thumb = client.get("/api/thumb/gallery-zip.png")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"].startswith("image/webp")
    assert thumb.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert thumb.headers.get("content-encoding") is None

    download = client.get("/api/download/gallery-zip.png")
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    assert download.headers["content-length"] == str(len(PNG_BYTES))
    assert download.headers["accept-ranges"] == "bytes"
    assert download.headers.get("content-encoding") is None
    assert download.headers["cache-control"] == "public, max-age=31536000, immutable"

    archive = client.get("/api/download-all")
    assert archive.status_code == 200
    assert archive.headers["content-type"].startswith("application/zip")
    assert archive.headers.get("content-encoding") != "gzip"
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


def test_gallery_thumbnail_statuses_are_fetched_in_one_batch(client, monkeypatch):
    monkeypatch.setattr(gallery_maintenance, "generate_thumbnail_for_image", lambda filename: None)
    _fake_gallery_entry("batch-thumb-1", "one", "1024x1024", "batch-thumb-1.png")
    _fake_gallery_entry("batch-thumb-2", "two", "1024x1024", "batch-thumb-2.png")

    response = client.post(
        "/api/gallery/thumbnails/status",
        json={"ids": ["batch-thumb-2", "missing-thumb", "batch-thumb-1"]},
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == ["batch-thumb-2", "batch-thumb-1"]
    assert all(
        set(item) == {"id", "thumbnail_filename", "thumbnail_url", "thumbnail_status"}
        for item in response.json()
    )

    duplicate = client.post(
        "/api/gallery/thumbnails/status",
        json={"ids": ["batch-thumb-1", "batch-thumb-1"]},
    )
    assert duplicate.status_code == 422


def test_gzip_compresses_text_but_bypasses_large_image_responses(client):
    large_image_bytes = PNG_BYTES * 64
    _fake_gallery_entry("gzip-image", "gzip bypass " * 120, "1024x1024", "gzip-image.png")
    path = image_files.safe_image_path("gzip-image.png")
    assert path is not None
    path.write_bytes(large_image_bytes)

    image = client.get(
        "/api/image/gzip-image.png",
        headers={"Accept-Encoding": "gzip"},
    )
    assert image.status_code == 200
    assert image.content == large_image_bytes
    assert image.headers["content-length"] == str(len(large_image_bytes))
    assert image.headers.get("content-encoding") is None

    gallery = client.get(
        "/api/gallery?page_size=100",
        headers={"Accept-Encoding": "gzip"},
    )
    assert gallery.status_code == 200
    assert gallery.headers["content-type"].startswith("application/json")
    assert gallery.headers["content-encoding"] == "gzip"


def test_gallery_image_responses_use_x_accel_redirect_when_enabled(client):
    config.ENABLE_NGINX_ACCEL_REDIRECT = True
    entry = _fake_gallery_entry("gallery-accel", "accel", "1024x1024", "gallery accel.png")
    assert entry.thumbnail_filename is None

    image = client.get("/api/image/gallery%20accel.png")
    assert image.status_code == 200
    assert image.headers["x-accel-redirect"] == "/_protected/images/gallery%20accel.png"
    assert image.headers["cache-control"].startswith("public")
    assert image.headers["content-type"].startswith("image/png")
    assert image.content == b""

    generated_thumbnail = thumbnail_jobs_repo.generate_thumbnail_for_image("gallery accel.png")
    assert generated_thumbnail
    thumb = client.get("/api/thumb/gallery%20accel.png")
    updated = gallery_queries.get_gallery_entry("gallery-accel")
    assert updated.thumbnail_filename
    thumbnail_path = image_files.safe_thumbnail_path(updated.thumbnail_filename)
    assert thumbnail_path is not None
    assert thumb.status_code == 200
    assert thumb.headers["x-accel-redirect"] == f"/_protected/thumbs/{updated.thumbnail_filename}"
    assert thumb.headers["cache-control"].startswith("public")
    assert thumb.headers["content-type"].startswith("image/webp")
    assert thumbnail_path.exists()

    download = client.get("/api/download/gallery%20accel.png")
    assert download.status_code == 200
    assert download.headers["x-accel-redirect"] == "/_protected/images/gallery%20accel.png"
    assert "attachment" in download.headers["content-disposition"]
    assert download.headers["content-type"].startswith("image/png")


def test_gallery_cursor_pagination_and_invalid_cursor(client):
    for index in range(5):
        _fake_gallery_entry(
            f"cursor-{index}",
            f"cursor prompt {index}",
            "1024x1024",
            f"cursor-{index}.png",
        )

    first = client.get("/api/gallery?page=1&page_size=2")
    assert first.status_code == 200
    first_data = first.json()
    assert first_data["page"] == 1
    assert first_data["has_next"] is True
    assert first_data["has_prev"] is False
    assert first_data["next_cursor"]
    first_ids = [image["id"] for image in first_data["images"]]

    second = client.get(
        f"/api/gallery?page=2&page_size=2&cursor={first_data['next_cursor']}&direction=next"
    )
    assert second.status_code == 200
    second_data = second.json()
    assert second_data["page"] == 2
    assert second_data["has_prev"] is True
    assert second_data["prev_cursor"]
    second_ids = [image["id"] for image in second_data["images"]]
    assert not set(first_ids) & set(second_ids)

    previous = client.get(
        f"/api/gallery?page=1&page_size=2&cursor={second_data['prev_cursor']}&direction=prev"
    )
    assert previous.status_code == 200
    assert [image["id"] for image in previous.json()["images"]] == first_ids

    invalid = client.get("/api/gallery?cursor=not-a-valid-cursor")
    assert invalid.status_code == 400


def test_gallery_page_overflow_clamps_before_fetching_rows(client):
    for index in range(3):
        _fake_gallery_entry(
            f"overflow-{index}",
            f"overflow {index}",
            "1024x1024",
            f"overflow-{index}.png",
        )

    resp = client.get("/api/gallery?page=999&page_size=2")

    assert resp.status_code == 200
    data = resp.json()
    assert data["page"] == 2
    assert data["total_pages"] == 2
    assert data["has_prev"] is True
    assert data["has_next"] is False
    assert [image["id"] for image in data["images"]] == ["overflow-0"]


def test_gallery_deep_page_uses_persisted_anchor(client, monkeypatch):
    monkeypatch.setattr(gallery_queries, "GALLERY_PAGE_ANCHOR_SMALL_OFFSET_THRESHOLD", 0)
    monkeypatch.setattr(gallery_queries, "GALLERY_PAGE_ANCHOR_INTERVAL_PAGES", 2)
    for index in range(12):
        _fake_gallery_entry(
            f"anchor-{index}",
            f"anchor {index}",
            "1024x1024",
            f"anchor-{index}.png",
        )

    first = gallery_queries.get_gallery_page(
        page=4,
        page_size=2,
        include_filter_options=False,
    )
    assert [image.id for image in first.images] == ["anchor-5", "anchor-4"]
    assert first.timings_ms["anchor_seeded_by_offset"] == 1.0

    with db_repo._connect() as conn:
        anchors = conn.execute(
            "SELECT page FROM gallery_page_anchors ORDER BY page"
        ).fetchall()
    assert [row["page"] for row in anchors] == [2, 4]

    gallery_mutations.update_gallery_entry(
        "anchor-0",
        {
            "duration": "1.23s",
            "completed_at": "2026-01-01T00:00:00+00:00",
            "n": 2,
        },
    )

    second = gallery_queries.get_gallery_page(
        page=4,
        page_size=2,
        include_filter_options=False,
    )
    assert [image.id for image in second.images] == ["anchor-5", "anchor-4"]
    assert second.timings_ms.get("anchor_seeded_by_offset", 0.0) == 0.0
    assert second.timings_ms["anchor_scan_rows"] <= 3


def test_gallery_first_page_without_counts_preserves_the_prefetched_has_next_sentinel(
    client,
):
    for index in range(9):
        _fake_gallery_entry(
            f"boundary-{index}",
            f"boundary {index}",
            "1024x1024",
            f"boundary-{index}.png",
        )

    resp = client.get("/api/gallery?page=1&page_size=9&include_counts=false")

    assert resp.status_code == 200
    data = resp.json()
    assert data["page"] == 1
    assert data["total_pages"] == 1
    assert data["has_prev"] is False
    assert data["has_next"] is False
    assert len(data["images"]) == 9


def test_gallery_filter_options_are_materialized_and_incremental(client):
    first = _fake_gallery_entry("filter-1", "filter one", "1024x1024", "filter-1.png")
    second = _fake_gallery_entry("filter-2", "filter two", "1536x1024", "filter-2.png")
    assert first is not None and second is not None

    options = gallery_filters.get_gallery_filter_options()
    assert options.models == ["gpt-image-2"]
    assert options.presets == ["Default"]
    assert options.sizes == ["1024x1024", "1536x1024"]

    gallery_mutations.update_gallery_entry("filter-2", {"model": "alt-model", "api_preset_name": "Alt"})
    options = gallery_filters.get_gallery_filter_options()
    assert options.models == ["alt-model", "gpt-image-2"]
    assert options.presets == ["Alt", "Default"]

    deleted, _files = gallery_mutations.delete_gallery_images(["filter-2"])
    assert deleted == 1
    options = gallery_filters.get_gallery_filter_options()
    assert options.models == ["gpt-image-2"]
    assert options.presets == ["Default"]
    assert options.sizes == ["1024x1024"]


def test_public_image_and_thumbnail_base_urls_are_returned(client):
    config.PUBLIC_IMAGE_BASE_URL = "https://cdn.example.com/images"
    config.PUBLIC_THUMBNAIL_BASE_URL = "https://cdn.example.com/thumbs"

    entry = _fake_gallery_entry("public-url", "public", "1024x1024", "public url.png")
    assert entry.image_url == "https://cdn.example.com/images/public%20url.png"
    assert entry.thumbnail_url.startswith("https://cdn.example.com/thumbs/")
    assert entry.thumbnail_url.endswith(".webp")

    gallery = client.get("/api/gallery")
    assert gallery.status_code == 200
    image = gallery.json()["images"][0]
    assert image["image_url"] == "https://cdn.example.com/images/public%20url.png"
    assert image["thumbnail_url"].startswith("https://cdn.example.com/thumbs/")

    resp = client.post(
        "/api/generate",
        json={
            "prompt": "public job",
            "size": "1024x1024",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
    )
    assert resp.status_code == 202
    job = _wait_for_job(client, resp.json()["job_id"])
    assert job["image_url"].startswith("https://cdn.example.com/images/")
    assert job["images"][0]["image_url"].startswith("https://cdn.example.com/images/")


def test_orphan_gallery_files_are_not_directly_served(client):
    orphan_path = Path(config.IMAGES_DIR) / "orphan.png"
    orphan_path.write_bytes(PNG_BYTES)

    image = client.get("/api/image/orphan.png")
    thumb = client.get("/api/thumb/orphan.png")
    download = client.get("/api/download/orphan.png")

    assert image.status_code == 404
    assert thumb.status_code == 404
    assert download.status_code == 404


def test_orphan_gallery_file_gc_removes_unreferenced_files(client):
    kept = _fake_gallery_entry("kept-gc", "kept", "1024x1024", "kept.png")
    kept_path = Path(config.IMAGES_DIR) / kept.filename
    orphan_path = Path(config.IMAGES_DIR) / "orphan-gc.png"
    orphan_path.write_bytes(PNG_BYTES)

    thumbnail_filename = thumbnail_jobs_repo.generate_thumbnail_for_image("orphan-gc.png")
    assert thumbnail_filename
    orphan_thumbnail_path = image_files.safe_thumbnail_path(thumbnail_filename)
    assert orphan_thumbnail_path is not None
    assert orphan_thumbnail_path.exists()

    result = gallery_mutations.cleanup_orphan_gallery_files(ttl_seconds=0, batch_size=20)

    assert result["removed_images"] == 1
    assert result["removed_thumbnails"] == 1
    assert not orphan_path.exists()
    assert not orphan_thumbnail_path.exists()
    assert kept_path.exists()
    assert gallery_queries.get_gallery_entry("kept-gc") is not None


def test_download_all_deduplicates_shared_filenames(client):
    _fake_gallery_entry("dup-1", "first", "1024x1024", "dup.png")
    gallery_mutations.add_to_gallery_sync(
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
        assert "metadata.ndjson" in zf.namelist()
        assert "images/export-job-1.png" in zf.namelist()
        assert "images/export-job-2.png" in zf.namelist()


def test_gallery_direct_export_job_tracks_streaming_download(client):
    _fake_gallery_entry("direct-export-1", "one", "1024x1024", "direct-export-1.png")
    _fake_gallery_entry("direct-export-2", "two", "1024x1024", "direct-export-2.png")

    created = client.post("/api/gallery/direct-export-jobs")
    assert created.status_code == 202
    job = created.json()
    assert job["job_id"].startswith("direct-")
    assert job["status"] == "running"
    assert job["stage"] == "queued"
    assert job["progress"] == 0
    assert job["requested_count"] == 2
    assert job["download_url"].endswith(f"export_job_id={job['job_id']}")

    archive = client.get(job["download_url"])
    assert archive.status_code == 200
    assert archive.headers["content-type"].startswith("application/zip")
    assert archive.headers["x-gallery-export-job-id"] == job["job_id"]
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
        assert "metadata.json" in zf.namelist()
        assert "metadata.ndjson" in zf.namelist()
        assert "images/direct-export-1.png" in zf.namelist()
        assert "images/direct-export-2.png" in zf.namelist()

    finished = _wait_for_gallery_direct_export_job(client, job["job_id"])
    assert finished["status"] == "success"
    assert finished["stage"] == "ready"
    assert finished["progress"] == 100
    assert finished["processed_count"] == 2
    assert finished["requested_count"] == 2
    assert finished["exported_count"] == 2
    assert finished["missing_count"] == 0
    assert finished["bytes_total"] > 0
    assert finished["bytes_written"] == finished["bytes_total"]

    events = client.get(f"/api/gallery/direct-export-jobs/{job['job_id']}/events")
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert "event: export" in events.text
    assert job["job_id"] in events.text


def test_download_all_without_direct_job_keeps_legacy_streaming_behavior(client):
    _fake_gallery_entry("legacy-direct-export", "one", "1024x1024", "legacy-direct-export.png")

    archive = client.get("/api/download-all")

    assert archive.status_code == 200
    direct_job_id = archive.headers.get("x-gallery-export-job-id")
    assert direct_job_id and direct_job_id.startswith("direct-")
    assert coordination_repo.get_gallery_job("export_direct", direct_job_id) is None
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
        assert "images/legacy-direct-export.png" in zf.namelist()
        assert "metadata.ndjson" in zf.namelist()


def test_gallery_direct_export_jobs_count_against_export_capacity(client):
    _fake_gallery_entry("direct-capacity", "one", "1024x1024", "direct-capacity.png")

    for _ in range(gallery_common.MAX_ACTIVE_EXPORT_JOBS):
        created = client.post("/api/gallery/direct-export-jobs")
        assert created.status_code == 202

    blocked = client.post("/api/gallery/export-jobs")
    assert blocked.status_code == 429
    assert "Too many active export jobs" in blocked.json()["detail"]


def test_gallery_export_job_persists_across_cleared_app_state(client):
    _fake_gallery_entry("export-persist-1", "one", "1024x1024", "export-persist-1.png")

    created = client.post("/api/gallery/export-jobs")
    assert created.status_code == 202
    finished = _wait_for_gallery_export_job(client, created.json()["job_id"])
    assert finished["status"] == "success"

    backend_main.app.state._state.clear()

    status = client.get(f"/api/gallery/export-jobs/{finished['job_id']}")
    assert status.status_code == 200
    assert status.json()["status"] == "success"

    archive = client.get(finished["download_url"])
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
        assert "images/export-persist-1.png" in zf.namelist()


def test_gallery_export_startup_cleanup_preserves_tracked_finished_zip(client):
    _fake_gallery_entry("export-cleanup-1", "one", "1024x1024", "export-cleanup-1.png")

    created = client.post("/api/gallery/export-jobs")
    assert created.status_code == 202
    finished = _wait_for_gallery_export_job(client, created.json()["job_id"])
    assert finished["status"] == "success"

    export_path = Path(config.DATA_DIR) / "exports" / f"{finished['job_id']}.zip"
    orphan_path = Path(config.DATA_DIR) / "exports" / "orphan-export.zip"
    orphan_path.write_bytes(b"orphan")

    app_state.cleanup_stale_gallery_export_files()

    assert export_path.exists()
    assert not orphan_path.exists()

    archive = client.get(finished["download_url"])
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
        assert "images/export-cleanup-1.png" in zf.namelist()


def test_gallery_export_download_rejects_untrusted_db_path(client):
    outside_path = Path(config.DATA_DIR).parent / "outside-export.zip"
    outside_path.write_bytes(b"outside archive")
    now = "2026-01-01T00:00:00Z"
    coordination_repo.create_gallery_job(
        job_id="polluted-export-path",
        kind="export",
        status="success",
        stage="ready",
        message="ZIP archive ready",
        progress=100,
        filename="polluted.zip",
        path=str(outside_path),
        requested_count=1,
        processed_count=1,
        exported_count=1,
        missing_count=0,
        bytes_total=outside_path.stat().st_size,
        bytes_written=outside_path.stat().st_size,
        created_at=now,
        updated_at=now,
        payload={},
    )

    archive = client.get("/api/gallery/export-jobs/polluted-export-path/download")

    assert archive.status_code == 404
    assert archive.json()["detail"] == "Gallery export archive not found"
    assert outside_path.read_bytes() == b"outside archive"
    assert coordination_repo.get_gallery_job("export", "polluted-export-path") is not None


def test_gallery_export_cleanup_skips_untrusted_db_path(client):
    outside_path = Path(config.DATA_DIR).parent / "outside-cleanup.zip"
    outside_path.write_bytes(b"outside cleanup target")
    now = "2026-01-01T00:00:00Z"
    coordination_repo.create_gallery_job(
        job_id="polluted-export-cleanup",
        kind="export",
        status="success",
        stage="ready",
        message="ZIP archive ready",
        progress=100,
        filename="polluted.zip",
        path=str(outside_path),
        created_at=now,
        updated_at=now,
        payload={},
    )

    gallery_tasks._cleanup_downloaded_gallery_export_job("polluted-export-cleanup")

    assert outside_path.read_bytes() == b"outside cleanup target"
    assert coordination_repo.get_gallery_job("export", "polluted-export-cleanup") is None


def test_gallery_tracked_jobs_allow_granian_multi_worker(client, monkeypatch):
    _fake_gallery_entry("multi-export", "one", "1024x1024", "multi-export.png")
    settings_repo.save_r2_backup_settings(
        {
            "enabled": True,
            "endpoint_url": "https://account.r2.cloudflarestorage.com",
            "bucket_name": "image-backups",
            "region": "auto",
            "key_prefix": "gallery-test/",
            "access_key_id": "${TEST_R2_ACCESS_KEY_ID}",
            "secret_access_key": "${TEST_R2_SECRET_ACCESS_KEY}",
        }
    )

    def fake_sync(settings, entries, *, total_count, progress_cb=None, client_factory=None):
        return r2_sync.R2SyncResult(total_count=total_count, compared_count=1)

    monkeypatch.setenv("GRANIAN_WORKERS", "2")
    monkeypatch.setattr(r2_sync, "sync_gallery_to_r2", fake_sync)

    export_created = client.post("/api/gallery/export-jobs")
    assert export_created.status_code == 202
    sync_created = client.post("/api/gallery/sync-jobs")
    assert sync_created.status_code == 202
    assert _wait_for_gallery_export_job(client, export_created.json()["job_id"])["status"] == "success"
    assert _wait_for_gallery_sync_job(client, sync_created.json()["job_id"])["status"] == "success"


def test_gallery_sync_job_reports_progress_and_terminal_sse(client, monkeypatch):
    _fake_gallery_entry("sync-job-1", "one", "1024x1024", "sync-job-1.png")
    _fake_gallery_entry("sync-job-2", "two", "1024x1024", "sync-job-2.png")
    settings_repo.save_r2_backup_settings(
        {
            "enabled": True,
            "endpoint_url": "https://account.r2.cloudflarestorage.com",
            "bucket_name": "image-backups",
            "region": "auto",
            "key_prefix": "gallery-test/",
            "access_key_id": "${TEST_R2_ACCESS_KEY_ID}",
            "secret_access_key": "${TEST_R2_SECRET_ACCESS_KEY}",
        }
    )

    def fake_sync(settings, entries, *, total_count, progress_cb=None, client_factory=None):
        assert settings["bucket_name"] == "image-backups"
        assert len(list(entries)) == 2
        result = r2_sync.R2SyncResult(
            total_count=total_count,
            compared_count=2,
            uploaded_count=1,
            skipped_existing_count=1,
            missing_local_count=0,
            failed_count=0,
            bytes_total=len(PNG_BYTES) * 2,
            bytes_uploaded=len(PNG_BYTES),
        )
        if progress_cb:
            progress_cb(
                {
                    "stage": "uploading",
                    "message": "Compared 2 gallery image(s)",
                    "progress": 100,
                    **result.to_updates(),
                }
            )
        return result

    monkeypatch.setattr(r2_sync, "sync_gallery_to_r2", fake_sync)
    created = client.post("/api/gallery/sync-jobs")
    assert created.status_code == 202
    job = created.json()
    assert job["status"] == "queued"
    assert job["total_count"] == 2

    finished = _wait_for_gallery_sync_job(client, job["job_id"])
    assert finished["status"] == "success"
    assert finished["progress"] == 100
    assert finished["uploaded_count"] == 1
    assert finished["skipped_existing_count"] == 1
    assert finished["bytes_uploaded"] == len(PNG_BYTES)

    events = client.get(f"/api/gallery/sync-jobs/{job['job_id']}/events")
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert "event: sync" in events.text
    assert job["job_id"] in events.text


def test_gallery_sync_job_supports_dry_run_preflight(client, monkeypatch):
    _fake_gallery_entry("sync-dry-run-1", "one", "1024x1024", "sync-dry-run-1.png")
    settings_repo.save_r2_backup_settings(
        {
            "enabled": True,
            "endpoint_url": "https://account.r2.cloudflarestorage.com",
            "bucket_name": "image-backups",
            "region": "auto",
            "key_prefix": "gallery-test/",
            "access_key_id": "${TEST_R2_ACCESS_KEY_ID}",
            "secret_access_key": "${TEST_R2_SECRET_ACCESS_KEY}",
        }
    )

    seen: dict[str, object] = {}

    def fake_sync(settings, entries, *, total_count, progress_cb=None, dry_run=False, state_recorder=None):
        seen["dry_run"] = dry_run
        seen["state_recorder"] = state_recorder
        assert len(list(entries)) == 1
        result = r2_sync.R2SyncResult(
            total_count=total_count,
            compared_count=1,
            pending_upload_count=1,
        )
        if progress_cb:
            progress_cb(
                {
                    "stage": "preflight",
                    "message": "Compared 1 gallery image(s)",
                    "progress": 100,
                    **result.to_updates(),
                }
            )
        return result

    monkeypatch.setattr(r2_sync, "sync_gallery_to_r2", fake_sync)
    created = client.post("/api/gallery/sync-jobs", json={"dry_run": True})
    assert created.status_code == 202
    job = created.json()
    assert job["dry_run"] is True

    finished = _wait_for_gallery_sync_job(client, job["job_id"])
    assert finished["status"] == "success"
    assert finished["dry_run"] is True
    assert finished["pending_upload_count"] == 1
    assert finished["uploaded_count"] == 0
    assert seen == {"dry_run": True, "state_recorder": None}


def test_gallery_sync_job_accepts_enabled_r2_env_defaults(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "env-r2-access")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "env-r2-secret")
    config.R2_BACKUP_ENABLED = True
    config.R2_ENDPOINT_URL = "https://account.r2.cloudflarestorage.com"
    config.R2_BUCKET_NAME = "env-image-backups"
    config.R2_REGION = "auto"
    config.R2_KEY_PREFIX = "gallery-env/"
    config.R2_ACCESS_KEY_ID = "env-r2-access"
    config.R2_SECRET_ACCESS_KEY = "env-r2-secret"

    _fake_gallery_entry("sync-env-config", "one", "1024x1024", "sync-env-config.png")

    def fake_sync(settings, entries, *, total_count, progress_cb=None, client_factory=None):
        assert settings["enabled"] is True
        assert settings["bucket_name"] == "env-image-backups"
        assert settings["access_key_id"] == "${R2_ACCESS_KEY_ID}"
        assert settings["secret_access_key"] == "${R2_SECRET_ACCESS_KEY}"
        assert len(list(entries)) == 1
        return r2_sync.R2SyncResult(total_count=total_count, compared_count=1)

    monkeypatch.setattr(r2_sync, "sync_gallery_to_r2", fake_sync)

    with _test_client() as client:
        created = client.post("/api/gallery/sync-jobs")
        assert created.status_code == 202, created.json()
        finished = _wait_for_gallery_sync_job(client, created.json()["job_id"])
        assert finished["status"] == "success"
        assert finished["total_count"] == 1


def test_scheduled_gallery_sync_skips_when_disabled(client):
    _fake_gallery_entry("scheduled-disabled", "one", "1024x1024", "scheduled-disabled.png")

    async def run_once():
        backend_main.app.state.gallery_sync_lock = asyncio.Lock()
        return await gallery_maintenance._run_scheduled_gallery_r2_sync_once()

    outcome = asyncio.run(run_once())
    assert outcome == {"started": False, "reason": "disabled"}


def test_scheduled_gallery_sync_creates_regular_sync_job(client, monkeypatch):
    _fake_gallery_entry("scheduled-sync", "one", "1024x1024", "scheduled-sync.png")
    settings_repo.save_r2_backup_settings(
        {
            "enabled": True,
            "endpoint_url": "https://account.r2.cloudflarestorage.com",
            "bucket_name": "image-backups",
            "region": "auto",
            "key_prefix": "gallery-test/",
            "sync_interval_hours": 1,
            "access_key_id": "${TEST_R2_ACCESS_KEY_ID}",
            "secret_access_key": "${TEST_R2_SECRET_ACCESS_KEY}",
        }
    )
    seen = {}

    def fake_sync(settings, entries, *, total_count, progress_cb=None, client_factory=None):
        seen["settings"] = settings
        seen["entries"] = list(entries)
        return r2_sync.R2SyncResult(total_count=total_count, compared_count=1)

    monkeypatch.setattr(r2_sync, "sync_gallery_to_r2", fake_sync)

    async def run_once():
        outcome = await gallery_maintenance._run_scheduled_gallery_r2_sync_once()
        assert outcome["started"] is True
        deadline = time.time() + 5
        while time.time() < deadline:
            job = coordination_repo.get_gallery_job("sync", outcome["job_id"])
            if job and job["status"] in {"success", "error"}:
                return job
            await asyncio.sleep(0.05)
        raise AssertionError("scheduled gallery sync job did not finish")

    job = asyncio.run(run_once())
    assert job["status"] == "success"
    assert job["total_count"] == 1
    assert seen["settings"]["sync_interval_hours"] == 1
    assert [entry["id"] for entry in seen["entries"]] == ["scheduled-sync"]


def test_scheduled_gallery_sync_skips_active_sync(client):
    _fake_gallery_entry("scheduled-active", "one", "1024x1024", "scheduled-active.png")
    settings_repo.save_r2_backup_settings(
        {
            "enabled": True,
            "endpoint_url": "https://account.r2.cloudflarestorage.com",
            "bucket_name": "image-backups",
            "region": "auto",
            "key_prefix": "gallery-test/",
            "sync_interval_hours": 1,
            "access_key_id": "${TEST_R2_ACCESS_KEY_ID}",
            "secret_access_key": "${TEST_R2_SECRET_ACCESS_KEY}",
        }
    )

    async def run_once():
        now = "2026-05-18T12:00:00Z"
        coordination_repo.create_gallery_job(
            job_id="active-sync",
            kind="sync",
            status="running",
            stage="listing_remote",
            message="Running",
            progress=1,
            total_count=1,
            created_at=now,
            updated_at=now,
            lease_owner="test-worker",
            lease_expires_at="2099-01-01T00:00:00Z",
            payload={},
        )
        return await gallery_maintenance._run_scheduled_gallery_r2_sync_once()

    outcome = asyncio.run(run_once())
    assert outcome == {"started": False, "reason": "active_sync"}


def test_gallery_sync_job_rejects_empty_gallery_and_missing_r2_config(client):
    empty = client.post("/api/gallery/sync-jobs")
    assert empty.status_code == 404
    assert empty.json()["detail"] == "No images in gallery"

    _fake_gallery_entry("sync-missing-config", "one", "1024x1024", "sync-missing-config.png")
    missing_config = client.post("/api/gallery/sync-jobs")
    assert missing_config.status_code == 400
    assert "R2 backup is disabled" in missing_config.json()["detail"]


def test_gallery_total_bytes_uses_sql_aggregate_without_disk_backfill(client, monkeypatch):
    db_repo._ensure_database()
    image_path = image_files.safe_image_path("legacy-bytes.png")
    assert image_path is not None
    image_path.write_bytes(PNG_BYTES)

    gallery_mutations.add_to_gallery_sync(
        image_id="legacy-bytes",
        prompt="legacy",
        size="1024x1024",
        filename="legacy-bytes.png",
        metadata={"model": "gpt-image-2"},
    )

    stat_calls: list[str] = []
    real_stat = gallery_mutations._stat_image_bytes

    def tracked_stat(filename: str):
        stat_calls.append(filename)
        return real_stat(filename)

    monkeypatch.setattr(gallery_mutations, "_stat_image_bytes", tracked_stat)

    with db_repo._connect() as conn:
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

    with db_repo._connect() as conn:
        row = conn.execute(
            "SELECT bytes FROM gallery_entries WHERE id = ?",
            ("legacy-bytes",),
        ).fetchone()
        assert row["bytes"] is None

    updated = gallery_mutations.backfill_missing_gallery_bytes()
    assert updated == 1
    assert stat_calls == ["legacy-bytes.png"]

    gallery_stats = client.get("/api/gallery?include_total_bytes=true")
    assert gallery_stats.status_code == 200
    assert gallery_stats.json()["total_bytes"] == len(PNG_BYTES)

    with db_repo._connect() as conn:
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

    with db_repo._connect() as conn:
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


def test_gallery_date_filters_are_normalized_to_utc(client, monkeypatch):
    monkeypatch.setattr(gallery_mutations, "utc_now", lambda: "2025-12-31T23:30:00+00:00")
    gallery_mutations.add_to_gallery_sync(
        image_id="date-1",
        prompt="date one",
        size="1024x1024",
        filename="date-1.png",
        metadata={"model": "gpt-image-2"},
    )
    monkeypatch.setattr(gallery_mutations, "utc_now", lambda: "2026-01-01T01:30:00+00:00")
    gallery_mutations.add_to_gallery_sync(
        image_id="date-2",
        prompt="date two",
        size="1024x1024",
        filename="date-2.png",
        metadata={"model": "gpt-image-2"},
    )

    resp = client.get("/api/gallery", params={"date_from": "2026-01-01T02:00:00+02:00"})
    assert resp.status_code == 200
    data = resp.json()
    assert [image["id"] for image in data["images"]] == ["date-2"]

    tz_resp = client.get("/api/gallery", params={"date_to": "2026-01-01T03:00:00+02:00"})
    assert tz_resp.status_code == 200
    assert [image["id"] for image in tz_resp.json()["images"]] == ["date-1"]


def test_gallery_favorite_filter_normalizes_string_booleans(client):
    _fake_gallery_entry("favorite-bool-1", "one", "1024x1024", "favorite-bool-1.png")
    _fake_gallery_entry("favorite-bool-2", "two", "1024x1024", "favorite-bool-2.png")
    gallery_mutations.update_gallery_entry("favorite-bool-1", {"favorite": True})

    unfavorited = gallery_queries.get_gallery_page(
        filters={"favorite": "false"},
        include_filter_options=False,
    )
    favorited = gallery_queries.get_gallery_page(
        filters={"favorite": "true"},
        include_filter_options=False,
    )
    ignored = gallery_queries.get_gallery_page(
        filters={"favorite": "maybe"},
        include_filter_options=False,
    )

    assert [image.id for image in unfavorited.images] == ["favorite-bool-2"]
    assert [image.id for image in favorited.images] == ["favorite-bool-1"]
    assert [image.id for image in ignored.images] == [
        "favorite-bool-2",
        "favorite-bool-1",
    ]


def test_gallery_batch_operations_chunk_sqlite_in_clauses(client, monkeypatch):
    monkeypatch.setattr(db_repo, "SQLITE_IN_CLAUSE_CHUNK_SIZE", 2)
    for index in range(5):
        _fake_gallery_entry(f"chunk-{index}", f"chunk {index}", "1024x1024", f"chunk-{index}.png")

    fetched = gallery_queries.get_gallery_entries_by_ids(["chunk-3", "chunk-1", "chunk-3", "chunk-4", "missing"])
    assert [entry.id for entry in fetched] == ["chunk-3", "chunk-1", "chunk-4"]

    favorite = client.patch(
        "/api/gallery/batch/favorite",
        json={"ids": [f"chunk-{index}" for index in range(5)], "favorite": True},
    )
    assert favorite.status_code == 200
    assert favorite.json()["count"] == 5
    assert all(gallery_queries.get_gallery_entry(f"chunk-{index}").favorite for index in range(5))

    deleted = client.post(
        "/api/gallery/batch/delete",
        json={"ids": [f"chunk-{index}" for index in range(5)]},
    )
    assert deleted.status_code == 200
    assert deleted.json()["count"] == 5
    assert gallery_queries.get_gallery_count() == 0


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
    assert gallery_queries.get_gallery_entry("batch-delete-1") is None
    assert gallery_queries.get_gallery_entry("batch-delete-2") is not None
    assert gallery_queries.get_gallery_entry("batch-delete-3") is None
    assert not image_files.safe_image_path("batch-delete-1.png").exists()
    assert image_files.safe_image_path("batch-delete-2.png").exists()


def test_gallery_batch_delete_preserves_shared_filename(client):
    _fake_gallery_entry("shared-1", "one", "1024x1024", "shared.png")
    gallery_mutations.add_to_gallery_sync(
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
    assert image_files.safe_image_path("shared.png") is not None

    second = client.post("/api/gallery/batch/delete", json={"ids": ["shared-2"]})
    assert second.status_code == 200
    assert second.json()["file_count"] == 1
    assert not image_files.safe_image_path("shared.png").exists()


def test_delete_all_gallery_commits_rows_when_file_delete_fails(client, monkeypatch, caplog):
    _fake_gallery_entry("delete-all-1", "one", "1024x1024", "delete-all-1.png")
    _fake_gallery_entry("delete-all-2", "two", "1024x1024", "delete-all-2.png")
    original_delete = gallery_mutations._delete_image_unlocked

    def flaky_delete(filename: str):
        if filename == "delete-all-1.png":
            raise OSError("locked")
        return original_delete(filename)

    monkeypatch.setattr(gallery_mutations, "_delete_image_unlocked", flaky_delete)

    with caplog.at_level(logging.WARNING):
        total, deleted_files = gallery_mutations.delete_all_gallery_images()

    assert total == 2
    assert deleted_files == 1
    assert gallery_queries.get_gallery_count() == 0
    assert image_files.safe_image_path("delete-all-1.png").exists()
    assert not image_files.safe_image_path("delete-all-2.png").exists()
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
    assert gallery_queries.get_gallery_entry("batch-fav-1").favorite is True
    assert gallery_queries.get_gallery_entry("batch-fav-2").favorite is False
    assert gallery_queries.get_gallery_entry("batch-fav-3").favorite is True

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
    assert gallery_queries.get_gallery_entry("batch-fav-1").favorite is False
    assert gallery_queries.get_gallery_entry("batch-fav-3").favorite is False


def test_gallery_batch_selection_token_favorite_download_and_export(client):
    _fake_gallery_entry("token-fav-1", "token match one", "1024x1024", "token-fav-1.png")
    _fake_gallery_entry("token-fav-2", "outside prompt", "1024x1024", "token-fav-2.png")
    _fake_gallery_entry("token-fav-3", "token match three", "1024x1024", "token-fav-3.png")

    token_resp = client.post(
        "/api/gallery/batch/selection-tokens",
        json={"filters": {"prompt": "token match"}},
    )
    assert token_resp.status_code == 201
    token_body = token_resp.json()
    assert token_body["count"] == 2
    token = token_body["selection_token"]

    favorite = client.patch(
        "/api/gallery/batch/favorite",
        json={"selection_token": token, "favorite": True},
    )
    assert favorite.status_code == 200
    assert favorite.json()["requested_count"] == 2
    assert favorite.json()["updated_count"] == 2
    assert gallery_queries.get_gallery_entry("token-fav-1").favorite is True
    assert gallery_queries.get_gallery_entry("token-fav-2").favorite is False
    assert gallery_queries.get_gallery_entry("token-fav-3").favorite is True

    archive = client.post(
        "/api/gallery/batch/download",
        json={"selection_token": token},
    )
    assert archive.status_code == 200
    assert archive.headers["x-gallery-requested-count"] == "2"
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
        assert "images/token-fav-1.png" in zf.namelist()
        assert "images/token-fav-2.png" not in zf.namelist()
        assert "images/token-fav-3.png" in zf.namelist()

    export_created = client.post("/api/gallery/export-jobs", json={"selection_token": token})
    assert export_created.status_code == 202
    finished = _wait_for_gallery_export_job(client, export_created.json()["job_id"])
    assert finished["status"] == "success"
    assert finished["requested_count"] == 2
    export_archive = client.get(finished["download_url"])
    assert export_archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(export_archive.content)) as zf:
        assert "images/token-fav-1.png" in zf.namelist()
        assert "images/token-fav-2.png" not in zf.namelist()
        assert "images/token-fav-3.png" in zf.namelist()


def test_gallery_batch_selection_token_delete_only_filtered_entries(client):
    _fake_gallery_entry("token-delete-1", "delete token keep", "1024x1024", "token-delete-1.png")
    _fake_gallery_entry("token-delete-2", "unmatched keep", "1024x1024", "token-delete-2.png")
    _fake_gallery_entry("token-delete-3", "delete token remove", "1024x1024", "token-delete-3.png")

    token_resp = client.post(
        "/api/gallery/batch/selection-tokens",
        json={"filters": {"prompt": "delete token"}},
    )
    assert token_resp.status_code == 201

    deleted = client.post(
        "/api/gallery/batch/delete",
        json={"selection_token": token_resp.json()["selection_token"]},
    )
    assert deleted.status_code == 200
    assert deleted.json()["requested_count"] == 2
    assert deleted.json()["updated_count"] == 2
    assert gallery_queries.get_gallery_entry("token-delete-1") is None
    assert gallery_queries.get_gallery_entry("token-delete-2") is not None
    assert gallery_queries.get_gallery_entry("token-delete-3") is None


def test_gallery_batch_selection_token_avoids_full_id_materialization(client, monkeypatch):
    _fake_gallery_entry("token-stream-1", "stream token match one", "1024x1024", "token-stream-1.png")
    _fake_gallery_entry("token-stream-2", "outside stream", "1024x1024", "token-stream-2.png")
    _fake_gallery_entry("token-stream-3", "stream token match three", "1024x1024", "token-stream-3.png")

    token_resp = client.post(
        "/api/gallery/batch/selection-tokens",
        json={"filters": {"prompt": "stream token"}},
    )
    assert token_resp.status_code == 201
    token = token_resp.json()["selection_token"]

    def fail_materialized_ids(*args, **kwargs):
        raise AssertionError("selection-token batch path should not materialize all ids")

    monkeypatch.setattr(gallery_common, "get_gallery_ids", fail_materialized_ids)
    monkeypatch.setattr(gallery_common, "get_gallery_entries_by_ids", fail_materialized_ids)

    original_connect = db_repo._connect
    original_transaction = db_repo._transaction

    class NoExecutemanyForFavoriteConnection:
        def __init__(self, conn: sqlite3.Connection):
            self._conn = conn

        def __getattr__(self, name: str):
            return getattr(self._conn, name)

        def executemany(self, sql: str, params):
            if "UPDATE gallery_entries SET favorite" in str(sql):
                raise AssertionError(
                    "selection-token favorite should use set-based UPDATE"
                )
            return self._conn.executemany(sql, params)

    @contextmanager
    def no_favorite_executemany_connect():
        with original_connect() as conn:
            yield NoExecutemanyForFavoriteConnection(conn)

    monkeypatch.setattr(gallery_mutations, "_connect", no_favorite_executemany_connect)
    monkeypatch.setattr(
        gallery_mutations,
        "_transaction",
        lambda conn: original_transaction(conn._conn),
    )

    favorite = client.patch(
        "/api/gallery/batch/favorite",
        json={"selection_token": token, "favorite": True},
    )
    assert favorite.status_code == 200
    assert favorite.json()["requested_count"] == 2
    assert favorite.json()["updated_count"] == 2
    assert gallery_queries.get_gallery_entry("token-stream-1").favorite is True
    assert gallery_queries.get_gallery_entry("token-stream-2").favorite is False
    assert gallery_queries.get_gallery_entry("token-stream-3").favorite is True

    favorite_again = client.patch(
        "/api/gallery/batch/favorite",
        json={"selection_token": token, "favorite": True},
    )
    assert favorite_again.status_code == 200
    assert favorite_again.json()["requested_count"] == 2
    assert favorite_again.json()["updated_count"] == 2

    archive = client.post("/api/gallery/batch/download", json={"selection_token": token})
    assert archive.status_code == 200
    assert archive.headers["x-gallery-requested-count"] == "2"
    assert archive.headers["x-gallery-exported-count"] == "2"
    assert archive.headers["x-gallery-missing-count"] == "0"
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
        assert "images/token-stream-1.png" in zf.namelist()
        assert "images/token-stream-2.png" not in zf.namelist()
        assert "images/token-stream-3.png" in zf.namelist()

    deleted = client.post("/api/gallery/batch/delete", json={"selection_token": token})
    assert deleted.status_code == 200
    assert deleted.json()["requested_count"] == 2
    assert deleted.json()["updated_count"] == 2
    assert gallery_queries.get_gallery_entry("token-stream-1") is None
    assert gallery_queries.get_gallery_entry("token-stream-2") is not None
    assert gallery_queries.get_gallery_entry("token-stream-3") is None


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
    image_files.safe_image_path("batch-download-missing-file.png").unlink()

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
def test_thumbnail_endpoint_enqueues_missing_thumbnail_job(client, monkeypatch):
    original_generate_thumbnail = thumbnail_jobs_repo.generate_thumbnail_for_image
    monkeypatch.setattr(gallery_queries_router, "kick_thumbnail_dispatcher", lambda: None)
    monkeypatch.setattr(gallery_maintenance, "generate_thumbnail_for_image", lambda filename: None)
    entry = _fake_gallery_entry("lazy-thumb", "lazy", "1024x1024", "lazy-thumb.png")
    assert entry.thumbnail_filename is None

    resp = client.get("/api/thumb/lazy-thumb.png")
    updated = gallery_queries.get_gallery_entry("lazy-thumb")
    assert updated.thumbnail_filename is None
    assert resp.status_code == 404
    with db_repo._connect() as conn:
        row = conn.execute(
            "SELECT status FROM thumbnail_jobs WHERE filename = ?",
            ("lazy-thumb.png",),
        ).fetchone()
    assert row is not None

    monkeypatch.setattr(gallery_maintenance, "generate_thumbnail_for_image", original_generate_thumbnail)
    thumbnail_filename = thumbnail_jobs_repo.generate_thumbnail_for_image("lazy-thumb.png")
    assert thumbnail_filename
    thumbnail_path = image_files.safe_thumbnail_path(thumbnail_filename)
    assert thumbnail_path is not None
    assert thumbnail_path.exists()

    resp = client.get("/api/thumb/lazy-thumb.png")
    assert resp.status_code == 200


def test_thumbnail_repository_requeues_running_job(tmp_path):
    _configure_runtime(tmp_path)
    _fake_gallery_entry(
        "running-thumb",
        "running",
        "1024x1024",
        "running-thumb.png",
    )
    owner = "thumbnail-running-worker"
    job = thumbnail_jobs_repo.claim_next_thumbnail_job(
        owner=owner,
        lease_expires_at="2999-01-01T00:10:00+00:00",
        now="2999-01-01T00:00:00+00:00",
    )
    assert job
    assert job["filename"] == "running-thumb.png"

    assert thumbnail_jobs_repo.enqueue_thumbnail_job("running-thumb.png", force=True)

    with db_repo._connect() as conn:
        row = conn.execute(
            """
            SELECT status, attempts, lease_owner, lease_expires_at
            FROM thumbnail_jobs
            WHERE filename = ?
            """,
            ("running-thumb.png",),
        ).fetchone()
    assert dict(row) == {
        "status": "queued",
        "attempts": 1,
        "lease_owner": None,
        "lease_expires_at": None,
    }


def test_thumbnail_job_queue_claims_and_completes(tmp_path):
    _configure_runtime(tmp_path)
    entry = _fake_gallery_entry("queue-thumb", "queue", "1024x1024", "queue-thumb.png")
    assert entry.thumbnail_filename is None
    assert thumbnail_jobs_repo.get_pending_thumbnail_job_count() == 1

    owner = "thumbnail-test-worker"
    job = thumbnail_jobs_repo.claim_next_thumbnail_job(
        owner=owner,
        lease_expires_at="2026-01-01T00:10:00+00:00",
        now="2026-01-01T00:00:00+00:00",
    )
    assert job
    assert job["filename"] == "queue-thumb.png"
    thumbnail_filename = thumbnail_jobs_repo.generate_thumbnail_for_image(job["filename"])
    assert thumbnail_filename
    assert thumbnail_jobs_repo.complete_thumbnail_job(job["filename"], owner=owner)
    assert thumbnail_jobs_repo.get_pending_thumbnail_job_count() == 0


def test_gallery_delete_and_auxiliary_gc_remove_stale_rows(tmp_path):
    _configure_runtime(tmp_path)
    _fake_gallery_entry("aux-row", "aux", "auto", "aux-row.png")
    thumbnail_jobs_repo.enqueue_thumbnail_job("aux-row.png", force=True)
    gallery_sync_state.mark_gallery_r2_sync_state(
        [{"filename": "aux-row.png", "key": "gallery/aux-row.png", "bytes": len(PNG_BYTES)}]
    )

    deleted, _ = gallery_mutations.delete_gallery_image("aux-row")
    assert deleted is True
    with db_repo._connect() as conn:
        assert conn.execute(
            "SELECT 1 FROM thumbnail_jobs WHERE filename = 'aux-row.png'"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM r2_sync_state WHERE filename = 'aux-row.png'"
        ).fetchone() is None
        conn.execute(
            "INSERT INTO worker_heartbeats VALUES ('old-worker', '2000-01-01T00:00:00+00:00', 0)"
        )
        conn.execute(
            "INSERT INTO worker_metric_snapshots VALUES ('old-worker', '{}', '2000-01-01T00:00:00+00:00')"
        )
        conn.commit()

    cleaned = thumbnail_jobs_repo.cleanup_auxiliary_state()
    assert cleaned["worker_heartbeats"] == 1
    assert cleaned["worker_metric_snapshots"] == 1
