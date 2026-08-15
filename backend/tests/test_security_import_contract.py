from backend.tests.support.contract import *  # noqa: F403

def test_import_archive(client):
    resp = _post_import_archive(client, _import_archive_bytes())
    assert resp.status_code == 202
    finished = _wait_for_gallery_import_job(client, resp.json()["job_id"])
    assert finished["status"] == "success"
    assert finished["imported_count"] == 1

    imported = gallery_queries.get_gallery_entry("import-1")
    assert imported is not None
    assert imported.bytes == len(PNG_BYTES)
    assert imported.thumbnail_url.startswith("/api/thumb/")


def test_import_archive_truncates_long_image_filename(client):
    long_stem = "x" * 320
    long_name = f"images/{long_stem}.png"

    resp = _post_import_archive(
        client,
        _import_archive_bytes(image_name=long_name),
    )

    assert resp.status_code == 202
    finished = _wait_for_gallery_import_job(client, resp.json()["job_id"])
    assert finished["status"] == "success"
    imported = gallery_queries.get_gallery_entry("import-1")
    assert imported is not None
    assert imported.filename.endswith(".png")
    assert len(imported.filename.encode("utf-8")) <= 240
    assert len(Path(imported.filename).stem) < len(long_stem)
    path = image_files.safe_image_path(imported.filename)
    assert path is not None
    assert path.exists()


def test_import_archive_async_job_reports_progress_and_terminal_sse(client):
    resp = client.post(
        "/api/import?async_job=true",
        files={"archive": ("archive.zip", _import_archive_bytes(), "application/zip")},
    )
    assert resp.status_code == 202
    job = resp.json()
    assert job["status"] == "queued"
    assert job["requested_count"] == 1

    finished = _wait_for_gallery_import_job(client, job["job_id"])
    assert finished["status"] == "success"
    assert finished["progress"] == 100
    assert finished["imported_count"] == 1
    assert finished["skipped_count"] == 0
    assert gallery_queries.get_gallery_entry("import-1") is not None

    events = client.get(f"/api/gallery/import-jobs/{job['job_id']}/events")
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert "event: import" in events.text
    assert job["job_id"] in events.text


def test_gallery_import_job_rejects_untrusted_db_path(client):
    outside_path = Path(config.DATA_DIR).parent / "outside-import.zip"
    outside_path.write_bytes(_import_archive_bytes())
    now = "2026-01-01T00:00:00Z"
    coordination_repo.create_gallery_job(
        job_id="polluted-import-path",
        kind="import",
        status="queued",
        stage="queued",
        message="Queued gallery ZIP import",
        progress=0,
        path=str(outside_path),
        requested_count=1,
        processed_count=0,
        exported_count=0,
        missing_count=0,
        created_at=now,
        updated_at=now,
        payload={},
    )

    asyncio.run(gallery_jobs._run_gallery_import_job(coordination_repo.get_gallery_job("import", "polluted-import-path")))

    assert outside_path.exists()
    assert gallery_queries.get_gallery_entry("import-1") is None
    job = coordination_repo.get_gallery_job("import", "polluted-import-path")
    assert job["status"] == "error"
    assert job["error"] == "Import archive path is invalid"


def test_import_gallery_entries_dedupes_existing_rows_at_commit(client):
    _fake_gallery_entry("import-1", "existing", "1024x1024", "import-1.png")

    imported_count = gallery_mutations.import_gallery_entries(
        [
            (
                PNG_BYTES,
                {
                    "id": "import-1",
                    "prompt": "late import",
                    "size": "1024x1024",
                    "filename": "import-1.png",
                    "created_at": "2026-01-02T00:00:00Z",
                },
            )
        ]
    )

    assert imported_count == 1
    existing = gallery_queries.get_gallery_entry("import-1")
    assert existing.prompt == "existing"

    imported = next(
        entry for entry in gallery_queries.get_gallery() if entry.prompt == "late import"
    )
    assert imported.id != "import-1"
    assert imported.filename == "import-1_1.png"
    assert imported.thumbnail_filename is None
    assert imported.thumbnail_url == "/api/thumb/import-1_1.png"

    thumb = client.get("/api/thumb/import-1_1.png")
    assert thumb.status_code == 404
    assert thumbnail_jobs_repo.generate_thumbnail_for_image("import-1_1.png")
    thumb = client.get("/api/thumb/import-1_1.png")
    assert thumb.status_code == 200
@pytest.mark.parametrize(
    ("archive_bytes", "expected_detail", "config_updates"),
    [
        (
            lambda: _import_archive_bytes(metadata=None),
            "metadata.json is required",
            {},
        ),
        (
            lambda: _import_archive_bytes(extra_files=2),
            "Import archive contains too many files",
            {"IMPORT_MAX_FILES": 2},
        ),
        (
            lambda: _import_archive_bytes(image_bytes=b"x" * 2048),
            "Imported image is too large",
            {"MAX_FILE_SIZE_MB": 0},
        ),
        (
            lambda: _import_archive_bytes(image_name="../evil.png"),
            "Import archive contains unsafe paths",
            {},
        ),
        (
            lambda: _import_archive_bytes(image_name="/evil.png"),
            "Import archive contains unsafe paths",
            {},
        ),
        (
            lambda: _import_archive_bytes(image_name="images\\evil.png"),
            "Import archive contains unsafe paths",
            {},
        ),
        (
            lambda: _import_archive_bytes(
                image_name="images/import-1.svg",
                image_bytes=b"<svg></svg>",
            ),
            "No importable images found",
            {},
        ),
    ],
)
def test_import_archive_rejects_invalid_content(
    client,
    archive_bytes,
    expected_detail,
    config_updates,
):
    for name, value in config_updates.items():
        setattr(config, name, value)

    resp = _post_import_archive(client, archive_bytes())

    assert resp.status_code == 400
    assert resp.json()["detail"] == expected_detail


def test_import_archive_rejects_uncompressed_size_limit(client):
    config.IMPORT_MAX_UNCOMPRESSED_MB = 0

    resp = _post_import_archive(client, _import_archive_bytes())

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Import archive uncompressed size exceeds limit"


def test_import_archive_rejects_large_metadata(client):
    config.IMPORT_MAX_METADATA_BYTES = 10

    resp = _post_import_archive(client, _import_archive_bytes())

    assert resp.status_code == 400
    assert resp.json()["detail"] == "metadata.json is too large"


def test_import_archive_rejects_duplicate_metadata_member(client):
    metadata = {
        "schema_version": 1,
        "images": [
            {"id": "one", "filename": "images/import-1.png", "prompt": "one", "size": "auto"},
            {"id": "two", "filename": "images/import-1.png", "prompt": "two", "size": "auto"},
        ],
    }
    resp = _post_import_archive(client, _import_archive_bytes(metadata=metadata))
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Import metadata references an image more than once"


def test_import_archive_rejects_metadata_entry_limit(client):
    config.IMPORT_MAX_ENTRIES = 1
    metadata = {
        "schema_version": 1,
        "images": [
            {"id": "one", "filename": "images/import-1.png", "prompt": "one", "size": "auto"},
            {"id": "two", "filename": "images/import-1.png", "prompt": "two", "size": "auto"},
        ],
    }
    resp = _post_import_archive(client, _import_archive_bytes(metadata=metadata))
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Import metadata contains too many entries"
def test_import_archive_rejects_uploaded_archive_size_limit(client):
    config.IMPORT_ARCHIVE_MAX_MB = 0

    resp = _post_import_archive(client, _import_archive_bytes())

    assert resp.status_code == 413
    assert resp.json()["detail"] == "Request body too large"


def test_import_archive_rejects_high_compression_ratio(client):
    config.IMPORT_MAX_COMPRESSION_RATIO = 1

    resp = _post_import_archive(client, _import_archive_bytes(image_bytes=b"0" * 1024))

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Import archive compression ratio exceeds limit"


def test_upload_rejects_svg(client):
    resp = client.post(
        "/api/edits",
        data={
            "prompt": "no svg",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
        files={"image": ("input.svg", b"<svg></svg>", "image/svg+xml")},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Upload must be an image file."




def test_upload_rejects_mismatched_png_content(client):
    resp = client.post(
        "/api/edits",
        data={
            "prompt": "fake png",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
        files={"image": ("input.png", b"<svg></svg>", "image/png")},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Image data must be a supported raster image format"


def test_import_archive_skips_mismatched_png_content(client):
    resp = _post_import_archive(
        client,
        _import_archive_bytes(image_name="images/fake.png", image_bytes=b"<svg></svg>"),
    )

    assert resp.status_code == 202
    finished = _wait_for_gallery_import_job(client, resp.json()["job_id"])
    assert finished["status"] == "error"
    assert finished["error"] == "No importable images found"


def test_safe_image_paths_reject_traversal(client):
    assert image_files.safe_image_path("gallery-zip.png") is not None
    assert image_files.safe_image_path("../secret.png") is None
    assert image_files.safe_image_path("nested/secret.png") is None

    image = client.get("/api/image/..%2Fsecret.png")
    thumb = client.get("/api/thumb/..%2Fsecret.png")
    download = client.get("/api/download/..%2Fsecret.png")

    assert image.status_code == 404
    assert thumb.status_code == 404
    assert download.status_code == 404


def test_image_validation_rejects_magic_only_truncated_image(client):
    with pytest.raises(ValueError, match="fully decodable"):
        image_files.validate_image_bytes(b"\xff\xd8\xff\xd9", filename="truncated.jpg")


def test_download_all_skips_polluted_gallery_filename(client):
    _fake_gallery_entry("safe", "safe", "1024x1024", "safe.png")
    gallery_mutations.add_to_gallery_sync(
        image_id="polluted",
        prompt="polluted",
        size="1024x1024",
        filename="../secret.png",
        image_bytes=None,
    )

    archive = client.get("/api/download-all")

    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
        assert "images/safe.png" in zf.namelist()
        assert all("secret" not in name for name in zf.namelist())


def test_edit_from_gallery_rejects_polluted_filename(client):
    gallery_mutations.add_to_gallery_sync(
        image_id="polluted",
        prompt="polluted",
        size="1024x1024",
        filename="../secret.png",
        image_bytes=None,
    )

    resp = client.post(
        "/api/edits/from-gallery/polluted",
        data={
            "prompt": "edit polluted",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Gallery image file not found"
def test_image_url_download_disables_redirects_and_validates_redirect_target(client):
    session = _FakeSession(
        [
            _FakeResponse(302, {"Location": "http://127.0.0.1/secret.png"}),
        ]
    )

    with pytest.raises(ValueError):
        asyncio.run(_download_with_fake_session(session, "https://example.com/image.png"))

    assert session.requested_urls == ["https://example.com/image.png"]
    assert session.allow_redirects_values == [False]


def test_image_url_download_rejects_plain_http(client):
    session = _FakeSession(
        [
            _FakeResponse(200, {}, [PNG_BYTES], peer_ip="93.184.216.34"),
        ]
    )

    with pytest.raises(ValueError, match="Only HTTPS URLs are allowed"):
        asyncio.run(_download_with_fake_session(session, "http://example.com/image.png"))

    assert session.requested_urls == []


def test_image_url_download_rejects_large_content_length(client):
    config.MAX_FILE_SIZE_MB = 0
    session = _FakeSession(
        [
            _FakeResponse(200, {"Content-Length": "1"}, [b"x"]),
        ]
    )

    with pytest.raises(Exception, match="Image too large"):
        asyncio.run(_download_with_fake_session(session, "https://example.com/image.png"))

    assert session.allow_redirects_values == [False]


def test_image_url_download_rejects_stream_over_limit(client):
    config.MAX_FILE_SIZE_MB = 0
    session = _FakeSession(
        [
            _FakeResponse(200, {}, [b"x"]),
        ]
    )

    with pytest.raises(Exception, match="Image too large"):
        asyncio.run(_download_with_fake_session(session, "https://example.com/image.png"))


def test_upstream_json_response_rejects_stream_over_limit(tmp_path):
    _configure_runtime(tmp_path)
    from backend.app.integrations.upstream import generation as upstream_client

    config.MAX_UPSTREAM_JSON_MB = 1
    too_large_json = b'{"data":"' + (b"x" * (1024 * 1024)) + b'"}'
    resp = _FakeResponse(
        200,
        {"Content-Type": "application/json"},
        [too_large_json],
    )

    with pytest.raises(
        upstream_client.UpstreamApiError,
        match="Upstream JSON response too large",
    ):
        asyncio.run(
            upstream_client.parse_upstream_json_response(
                resp,
                "/v1/images/generations",
                None,
            )
        )


def test_upstream_json_response_accepts_json_body_with_wrong_content_type(tmp_path):
    _configure_runtime(tmp_path)
    from backend.app.integrations.upstream import generation as upstream_client

    image_b64 = base64.b64encode(PNG_BYTES).decode("ascii")
    response_body = json.dumps(
        {"created": 1779943365, "data": [{"b64_json": image_b64}]}
    ).encode("utf-8")
    resp = _FakeResponse(
        200,
        {"Content-Type": "text/plain; charset=utf-8"},
        [response_body],
    )

    result, response_text = asyncio.run(
        upstream_client.parse_upstream_json_response(
            resp,
            "/v1/images/generations",
            None,
        )
    )

    assert result["data"][0]["b64_json"] == image_b64
    assert response_text.startswith('{"created":')


def test_upstream_chat_response_accepts_json_body_with_wrong_content_type(tmp_path):
    _configure_runtime(tmp_path)
    from backend.app.integrations.upstream import generation as upstream_client

    response_body = json.dumps(
        {"choices": [{"message": {"content": "https://example.com/generated.png"}}]}
    ).encode("utf-8")
    resp = _FakeResponse(
        200,
        {"Content-Type": "text/plain"},
        [response_body],
    )

    result, _response_text = asyncio.run(
        upstream_client.parse_upstream_chat_completion_response(
            resp,
            "/v1/chat/completions",
            None,
        )
    )

    assert result["choices"][0]["message"]["content"] == "https://example.com/generated.png"


def test_image_url_download_rejects_private_peer_ip(client):
    session = _FakeSession(
        [
            _FakeResponse(200, {}, [PNG_BYTES], peer_ip="127.0.0.1"),
        ]
    )

    with pytest.raises(ValueError, match="private/internal IP"):
        asyncio.run(_download_with_fake_session(session, "https://example.com/image.png"))


def test_socks5_upstream_private_dns_logs_trust_boundary_warning(tmp_path, monkeypatch, caplog):
    _configure_runtime(tmp_path)
    from backend.app.integrations.upstream import generation as upstream_client
    from backend.app.schemas.generation import GenerateRequest

    monkeypatch.setattr(
        upstream_client.ssrf,
        "resolve_hostname",
        lambda hostname: (hostname, ["10.0.0.5"]),
    )

    caplog.set_level(logging.WARNING, logger="backend.app.integrations.upstream.errors")
    with pytest.raises(ValueError, match="private/internal IP"):
        asyncio.run(
            ORIGINAL_CALL_IMAGE_GENERATION_API(
                "https://api.example.com",
                "key",
                "/v1/images/generations",
                GenerateRequest(prompt="private dns"),
                socks5_proxy="socks5://127.0.0.1:1080",
            )
        )

    assert "SOCKS5 proxy is enabled" in caplog.text
    assert "proxy is the trust boundary" in caplog.text


def test_prepare_upstream_request_centralizes_security_headers_and_peer_check(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    from backend.app.integrations.upstream import generation as upstream_client

    calls: list[tuple] = []

    async def fake_warn(upstream_url, socks5_proxy):
        calls.append(("warn", upstream_url, socks5_proxy))

    async def fake_validate(upstream_url, allowlist):
        calls.append(("validate_url", upstream_url, allowlist))

    def fake_peer(resp, label):
        calls.append(("peer", label))

    class FakeResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSession:
        def post(self, url, **kwargs):
            calls.append(("post", url, kwargs))
            return FakeResponse()

    class FakePool:
        def get(self, timeout_kind="upstream", socks5_proxy=None):
            calls.append(("pool", timeout_kind, socks5_proxy))
            return FakeSession()

    monkeypatch.setattr(upstream_client, "_warn_if_socks5_upstream_resolves_private", fake_warn)
    monkeypatch.setattr(upstream_client.ssrf, "validate_upstream_url_async", fake_validate)
    monkeypatch.setattr(upstream_client.ssrf, "validate_response_peer_ip", fake_peer)
    monkeypatch.setattr(upstream_client, "get_pool", lambda: FakePool())

    prepared = asyncio.run(
        upstream_client._prepare_upstream_request(
            api_url="https://api.example.com",
            api_key="key",
            api_path="/v1/images/generations",
            socks5_proxy=None,
        )
    )
    assert prepared.upstream_url == "https://api.example.com/v1/images/generations"
    assert prepared.headers == {
        "Authorization": "Bearer key",
        "User-Agent": "opencode",
        "Content-Type": "application/json",
    }

    async def post_once():
        async with prepared.post(json={"prompt": "hi"}):
            pass

    asyncio.run(post_once())

    assert ("warn", "https://api.example.com/v1/images/generations", None) in calls
    assert any(call[0] == "validate_url" for call in calls)
    assert ("pool", "upstream", None) in calls
    assert any(call[0] == "post" and call[2]["allow_redirects"] is False for call in calls)
    assert ("peer", "Upstream API") in calls


def test_upstream_returned_image_url_download_stays_direct(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    import importlib
    from backend.app.integrations.upstream import generation as upstream_client_module
    from backend.app.schemas.generation import GenerateRequest

    upstream_client = importlib.reload(upstream_client_module)
    created_sessions: list[str] = []
    session_events: list[tuple[str, str, str]] = []

    class FakeJsonResponse:
        status = 200
        headers = {"Content-Type": "application/json"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return json.dumps(
                {"data": [{"url": "https://example.com/generated.png"}]}
            )

    class FakeApiSession:
        def __init__(self, proxy_url: str):
            self.proxy_url = proxy_url

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, **kwargs):
            session_events.append(("post", self.proxy_url, url))
            return FakeJsonResponse()

    class FakeDownloadSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url, **kwargs):
            session_events.append(("get", "", url))
            return _FakeResponse(200, {}, [PNG_BYTES], peer_ip="93.184.216.34")

    class FakePool:
        def get(self, timeout_kind="upstream", socks5_proxy=None):
            proxy_url = socks5_proxy or ""
            created_sessions.append(proxy_url)
            if proxy_url:
                return FakeApiSession(proxy_url)
            return FakeDownloadSession()

    monkeypatch.setattr(upstream_client, "get_pool", lambda: FakePool())

    entries = asyncio.run(
        upstream_client.call_image_generation_api(
            "https://api.example.com",
            "key",
            "/v1/images/generations",
            GenerateRequest(prompt="url result"),
            socks5_proxy="socks5://127.0.0.1:1080",
        )
    )

    assert entries
    assert created_sessions == ["socks5://127.0.0.1:1080", ""]
    assert session_events[0] == (
        "post",
        "socks5://127.0.0.1:1080",
        "https://api.example.com/v1/images/generations",
    )
    assert session_events[1] == ("get", "", "https://example.com/generated.png")


def test_chat_completions_sse_markdown_image_url_is_saved(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    import importlib
    from backend.app.integrations.upstream import generation as upstream_client_module
    from backend.app.schemas.generation import GenerateRequest

    upstream_client = importlib.reload(upstream_client_module)
    session_events: list[tuple[str, str, dict | None]] = []

    class FakeSseResponse:
        status = 200
        headers = {"Content-Type": "text/event-stream"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return (
                'data: {"choices":[{"index":0,"delta":{"role":"assistant",'
                '"reasoning_content":"image generating"}}]}\n\n'
                'data: {"choices":[{"index":0,"delta":{"role":"assistant",'
                '"content":"![image](https://example.com/generated.jpg)"}}]}\n\n'
                "data: [DONE]\n\n"
            )

    class FakeApiSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, **kwargs):
            session_events.append(("post", url, kwargs.get("json")))
            return FakeSseResponse()

    class FakeDownloadSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url, **kwargs):
            session_events.append(("get", url, None))
            return _FakeResponse(200, {}, [JPEG_BYTES], peer_ip="93.184.216.34")

    class FakeCombinedSession(FakeApiSession, FakeDownloadSession):
        pass

    fake_session = FakeCombinedSession()

    class FakePool:
        def get(self, timeout_kind="upstream", socks5_proxy=None):
            return fake_session

    monkeypatch.setattr(upstream_client, "get_pool", lambda: FakePool())

    entries = asyncio.run(
        upstream_client.call_image_generation_api(
            "https://api.example.com",
            "key",
            "/v1/chat/completions",
            GenerateRequest(
                prompt="draw a red square",
                model="grok-imagine-image-lite",
            ),
        )
    )

    assert entries
    assert entries[0].filename.endswith(".jpg")
    assert entries[0].output_format == "jpeg"
    assert session_events[0] == (
        "post",
        "https://api.example.com/v1/chat/completions",
        {
            "model": "grok-imagine-image-lite",
            "messages": [{"role": "user", "content": "draw a red square"}],
            "stream": False,
        },
    )
    assert session_events[1] == ("get", "https://example.com/generated.jpg", None)
