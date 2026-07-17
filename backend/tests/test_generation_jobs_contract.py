from backend.tests.support.contract import *  # noqa: F403

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
    entry = gallery_queries.get_gallery_entry(job["image_id"])
    assert entry is not None
    assert entry.api_path == "/v1/responses"


def test_multi_image_job_returns_all_results(client, monkeypatch):
    calls = []
    calls_lock = threading.Lock()
    upstream_window_started = threading.Event()
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
            if len(calls) == config.MAX_ACTIVE_GENERATE_JOBS:
                upstream_window_started.set()
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
        assert upstream_window_started.wait(2)
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
        gallery_queries.get_gallery_entry(image["image_id"]).n == 3
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
        gallery_queries.get_gallery_entry(image["image_id"]).n == 3
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
    image_jobs_repo.upsert_generate_job(
        {
            "job_id": "interrupted-job",
            "status": "running",
            "stage": "waiting_for_api",
            "message": "Waiting for upstream API response",
            "created_at": "2026-05-18T12:00:00Z",
            "updated_at": "2026-05-18T12:00:01Z",
        }
    )

    assert image_jobs_repo.mark_active_generate_jobs_interrupted() == 1
    job = image_jobs_repo.get_generate_job("interrupted-job")
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
    assert "db_insert" in job["stage_timings"]

    metrics_resp = client.get("/api/metrics")
    assert metrics_resp.status_code == 200
    body = metrics_resp.json()
    assert body["enabled"] is True
    assert body["worker_id"]
    assert body["counters"]["image_jobs.generation.queued"] >= 1
    assert body["counters"]["image_jobs.generation.succeeded"] >= 1
    assert body["gauges"]["image_jobs.active"] == 0
    assert body["gauges"]["image_jobs.running_capacity"] == 2
    assert body["gauges"]["sse.active_connections"] >= 0
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


def test_metrics_snapshot_reads_sqlite_runtime_once(monkeypatch):
    calls = 0
    recorded: list[tuple[str, dict]] = []

    def fake_runtime():
        nonlocal calls
        calls += 1
        return {
            "gauges": {"sse.active_connections": 3},
            "background_leases": [{"name": "lease"}],
            "workers": [
                {
                    "worker_id": "peer-worker",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                    "age_seconds": 1.0,
                    "snapshot": {},
                },
                {
                    "worker_id": "local-worker",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                    "age_seconds": 9.0,
                    "snapshot": {"stale": True},
                },
            ],
        }

    monkeypatch.setattr(
        metrics_router,
        "get_runtime_coordination_metrics",
        fake_runtime,
    )
    monkeypatch.setattr(
        metrics_router,
        "snapshot_queue_metrics",
        lambda: {"image_jobs.active": 0},
    )
    monkeypatch.setattr(
        metrics_router,
        "record_worker_metrics_snapshot",
        lambda worker_id, payload: recorded.append((worker_id, payload)),
    )
    monkeypatch.setattr(
        metrics_router.app.state,
        "worker_id",
        "local-worker",
        raising=False,
    )

    snapshot = metrics_router._metrics_snapshot()

    assert calls == 1
    assert recorded and recorded[0][0] == "local-worker"
    assert snapshot["gauges"]["sse.active_connections"] == 3
    assert snapshot["workers"][0]["worker_id"] == "local-worker"
    assert snapshot["workers"][0]["snapshot"] == recorded[0][1]
    assert [worker["worker_id"] for worker in snapshot["workers"]] == [
        "local-worker",
        "peer-worker",
    ]
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
    generated_entry = gallery_queries.get_gallery_entry(generate_job["image_id"])
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
        image_id = image_files.generate_image_id()
        filename = f"{image_id}.png"
        entry = await gallery_mutations.add_to_gallery_async(
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
        image_id = image_files.generate_image_id()
        filename = f"{image_id}.png"
        entry = await gallery_mutations.add_to_gallery_async(
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
    from backend.app.integrations.upstream import generation as upstream_client

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
        image_id = image_files.generate_image_id()
        filename = f"{image_id}.png"
        entry = await gallery_mutations.add_to_gallery_async(
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
        if all(not path.exists() for path in seen["paths"]) and job_queue.get_pending_edit_source_bytes() == 0:
            break
        time.sleep(0.05)

    assert all(not path.exists() for path in seen["paths"])
    assert job_queue.get_pending_edit_source_bytes() == 0


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

    with _test_client() as test_client:
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
        assert job_queue.get_pending_edit_source_bytes() == len(PNG_BYTES)

        cancelled = test_client.delete(f"/api/generate/{edit.json()['job_id']}")
        assert cancelled.status_code == 200
        release_event.set()

        deadline = time.time() + 5
        while time.time() < deadline:
            if not seen["path"].exists() and job_queue.get_pending_edit_source_bytes() == 0:
                break
            time.sleep(0.05)

        job = test_client.get(f"/api/generate/{edit.json()['job_id']}").json()
        assert job["status"] == "cancelled"
        assert job["stage"] == "cancelled"
        assert not seen["path"].exists()
        assert job_queue.get_pending_edit_source_bytes() == 0


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
        image_id = image_files.generate_image_id()
        filename = f"{image_id}.png"
        entry = await gallery_mutations.add_to_gallery_async(
            image_bytes=PNG_BYTES,
            image_id=image_id,
            prompt=payload.prompt,
            size=payload.size,
            filename=filename,
            metadata={"api_path": "/v1/images/edits", "api_preset_name": api_preset_name},
        )
        return [entry]

    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", blocking_edit_api)

    with _test_client() as test_client:
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
        assert job_queue.get_pending_edit_source_bytes() == 0


def test_edit_queue_capacity_counts_multiple_source_bytes(tmp_path):
    _configure_runtime(tmp_path)
    config.MAX_PENDING_EDIT_SOURCE_MB = 1
    large_png = PNG_BYTES + (b"\0" * (600 * 1024))

    with _test_client() as test_client:
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
        assert job_queue.get_pending_edit_source_bytes() == 0
        assert not list((tmp_path / "data" / "edit-sources").glob("edit-source-*"))
def test_generate_job_webhook_is_sqlite_backed_and_consumed_once(tmp_path):
    _configure_runtime(tmp_path)
    image_jobs_repo.upsert_generate_job(
        {
            "job_id": "webhook-job",
            "status": "queued",
            "webhook_url": "https://hooks.example.com/callback",
        }
    )
    assert image_jobs_repo.pop_generate_job_webhook("webhook-job") == "https://hooks.example.com/callback"
    assert image_jobs_repo.pop_generate_job_webhook("webhook-job") == ""


def test_upstream_image_data_is_bounded_and_schema_checked():
    from backend.app.integrations.upstream.errors import UpstreamApiError, validate_upstream_image_data

    assert validate_upstream_image_data([{"url": "a"}, {"url": "b"}], 1) == [{"url": "a"}]
    with pytest.raises(UpstreamApiError, match="must be an array"):
        validate_upstream_image_data("not-an-array", 1)
    with pytest.raises(UpstreamApiError, match="entries must be objects"):
        validate_upstream_image_data(["not-an-object"], 1)
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
        return [entry]

    monkeypatch.setattr(backend_main.proxy, "call_image_generation_api", blocking_generation_api)

    with _test_client() as client:
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


def test_batch_generate_counts_image_units_and_bounds_upstream_calls(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    config.MAX_ACTIVE_GENERATE_JOBS = 1
    config.MAX_QUEUED_GENERATE_JOBS = 3
    calls = []
    calls_lock = threading.Lock()
    batch_started = threading.Event()
    release_event = threading.Event()
    active_calls = 0
    max_active_calls = 0

    async def blocking_generation_api(
        api_url,
        api_key,
        api_path,
        payload,
        api_preset_name=None,
        progress=None,
        socks5_proxy=None,
    ):
        nonlocal active_calls, max_active_calls
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        with calls_lock:
            calls.append(payload)
            batch_calls = [call for call in calls if call.prompt == "batch"]
            if len(batch_calls) == 1:
                batch_started.set()
        try:
            if payload.prompt == "batch":
                await asyncio.to_thread(release_event.wait)
            return [
                await _add_generated_gallery_entry(
                    payload,
                    api_path,
                    api_preset_name,
                )
            ]
        finally:
            active_calls -= 1

    monkeypatch.setattr(backend_main.proxy, "call_image_generation_api", blocking_generation_api)

    with _test_client() as client:
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
    assert max_active_calls == 1


def test_edit_jobs_share_queue_capacity(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    config.MAX_ACTIVE_GENERATE_JOBS = 1
    config.MAX_QUEUED_GENERATE_JOBS = 1
    release_event = threading.Event()

    async def blocking_generation_api(*args, **kwargs):
        await asyncio.to_thread(release_event.wait)
        payload = args[3]
        image_id = image_files.generate_image_id()
        filename = f"{image_id}.png"
        entry = await gallery_mutations.add_to_gallery_async(
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
        image_id = image_files.generate_image_id()
        filename = f"{image_id}.png"
        entry = await gallery_mutations.add_to_gallery_async(
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

    with _test_client() as client:
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
def test_running_progress_persists_only_terminal_states(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    upserted: list[dict] = []
    real_enqueue = image_jobs_repo.enqueue_image_job
    real_upsert = image_jobs_repo.upsert_generate_job

    def tracking_enqueue(**kwargs):
        upserted.append(kwargs["parent_job"].copy())
        return real_enqueue(**kwargs)

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
        image_id = image_files.generate_image_id()
        filename = f"{image_id}.png"
        entry = await gallery_mutations.add_to_gallery_async(
            image_bytes=PNG_BYTES,
            image_id=image_id,
            prompt=payload.prompt,
            size=payload.size,
            filename=filename,
            metadata={"api_path": api_path, "api_preset_name": api_preset_name},
        )
        return [entry]

    monkeypatch.setattr(job_queue, "enqueue_image_job", tracking_enqueue)
    monkeypatch.setattr(job_events, "upsert_generate_job", tracking_upsert)
    monkeypatch.setattr(backend_main.proxy, "call_image_generation_api", noisy_generation_api)

    with _test_client() as client:
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

    monkeypatch.setattr(job_events, "list_generate_jobs", tracking_list_generate_jobs)

    async def run_updates():
        queue: asyncio.Queue = asyncio.Queue(maxsize=20)
        subscribers = job_events.get_jobs_subscribers()
        subscribers.add(queue)
        try:
            for index in range(3):
                job_events.store_generate_job(
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
        image_jobs_repo.upsert_generate_job(
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


def test_generate_jobs_history_supports_seek_pagination(client):
    for index in range(4):
        image_jobs_repo.upsert_generate_job(
            {
                "job_id": f"seek-history-{index}",
                "status": "success",
                "operation": "generation",
                "prompt": f"seek history prompt {index}",
                "size": "1024x1024",
                "created_at": f"2026-01-01T00:01:0{index}+00:00",
                "updated_at": f"2026-01-01T00:01:0{index}+00:00",
                "completed_at": f"2026-01-01T00:01:0{index}+00:00",
            }
        )

    first = client.get("/api/generate/jobs?include_finished=true&limit=2")
    assert first.status_code == 200
    first_jobs = first.json()
    assert [job["job_id"] for job in first_jobs] == ["seek-history-3", "seek-history-2"]

    cursor = first_jobs[-1]
    second = client.get(
        "/api/generate/jobs",
        params={
            "include_finished": "true",
            "limit": "2",
            "before_updated_at": cursor["updated_at"],
            "before_job_id": cursor["job_id"],
        },
    )

    assert second.status_code == 200
    assert [job["job_id"] for job in second.json()] == ["seek-history-1", "seek-history-0"]


def test_generate_jobs_history_failed_only_filters_error_statuses(client):
    for job_id, status in [
        ("history-success", "success"),
        ("history-cancelled", "cancelled"),
        ("history-error", "error"),
        ("history-upstream", "upstream_error"),
    ]:
        image_jobs_repo.upsert_generate_job(
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


def test_clear_generate_jobs_history_deletes_only_terminal_jobs(client):
    for job_id, status in [
        ("history-success", "success"),
        ("history-error", "error"),
        ("active-running", "running"),
    ]:
        image_jobs_repo.upsert_generate_job(
            {
                "job_id": job_id,
                "status": status,
                "operation": "generation",
                "prompt": job_id,
                "size": "1024x1024",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "completed_at": "2026-01-01T00:00:00+00:00" if status != "running" else None,
                "error": "failed" if status == "error" else None,
            }
        )

    resp = client.delete("/api/generate/jobs/history")

    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    assert image_jobs_repo.get_generate_job("history-success") is None
    assert image_jobs_repo.get_generate_job("history-error") is None
    assert image_jobs_repo.get_generate_job("active-running") is not None


def test_validation_422_and_global_500(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    with _test_client(raise_server_exceptions=False) as client:
        bad = client.post(
            "/api/generate",
            json={"prompt": "x", "size": "1025x1025"},
        )
        assert bad.status_code == 422

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(gallery_queries_router, "get_gallery_page", boom)
        broken = client.get("/api/gallery")
        assert broken.status_code == 500
        assert broken.json()["detail"] == "Internal Server Error"


def test_responses_request_uses_payload_model_with_default_fallback(tmp_path):
    _configure_runtime(tmp_path)
    from backend.app.integrations.upstream import generation as upstream_client_module
    from backend.app.schemas.generation import GenerateRequest

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
            "api_key": "${TEST_OPENAI_API_KEY}",
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
    from backend.app.integrations.upstream import generation as upstream_client_module
    from backend.app.schemas.generation import GenerateRequest

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
