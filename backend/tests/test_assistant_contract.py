from backend.tests.support.contract import *  # noqa: F403


def test_edit_plan_rejects_more_sources_than_submission_api(client):
    response = client.post(
        "/api/assistant/edit/plan",
        json={"goal": "combine sources", "source_count": MAX_EDIT_SOURCE_IMAGES + 1},
    )

    assert response.status_code == 422


def test_ai_assistant_health_reports_success_and_config_errors(client, monkeypatch):
    settings = client.get("/api/settings").json()

    missing_initial_config = client.post("/api/assistant/health")
    assert missing_initial_config.status_code == 200
    assert missing_initial_config.json()["status"] == "error"
    assert "Prompt Optimizer endpoint URL is not configured" in missing_initial_config.json()["message"]

    configured = client.post(
        "/api/settings",
        json=_settings_payload(
            settings,
            prompt_optimizer={
                "enabled": True,
                "api_url": "https://example.com/v1/chat/completions",
                "model": "shared-model",
                "timeout_seconds": 45,
                "api_key": "test-prompt-optimizer-key",
            },
        ),
    )
    assert configured.status_code == 200
    enabled = client.post(
        "/api/settings",
        json=_assistant_payload(
            configured.json(),
            ai_assistant={
                "enabled": True,
                "vision_model": "assistant-vision-model",
            },
        ),
    )
    assert enabled.status_code == 200

    probe_calls: list[dict[str, object]] = []

    async def fake_probe(*, api_url, api_key, api_path, model, timeout_seconds):
        probe_calls.append(
            {
                "api_url": api_url,
                "api_key": api_key,
                "api_path": api_path,
                "model": model,
                "timeout_seconds": timeout_seconds,
            }
        )
        return {
            "status": "ok",
            "message": f"AI Assistant responded successfully with model {model}",
            "model": model,
            "duration_ms": 12,
            "status_code": 200,
        }

    monkeypatch.setattr(assistant_router.assistant_client, "probe_assistant_endpoint", fake_probe)
    healthy = client.post("/api/assistant/health")
    assert healthy.status_code == 200
    assert healthy.json() == {
        "status": "ok",
        "message": "AI Assistant responded successfully with model shared-model",
        "model": "shared-model",
        "duration_ms": 12,
        "status_code": 200,
    }
    assert probe_calls[-1]["api_url"] == "https://example.com"
    assert probe_calls[-1]["api_key"] == "optimizer-key"
    assert probe_calls[-1]["api_path"] == "/v1/chat/completions"
    assert probe_calls[-1]["timeout_seconds"] == 45

    draft_healthy = client.post(
        "/api/assistant/health",
        json={
            "enabled": True,
            "vision_model": "draft-assistant-vision-model",
            "api_path": "/v1/responses",
        },
    )
    assert draft_healthy.status_code == 200
    assert draft_healthy.json()["status"] == "ok"
    assert draft_healthy.json()["model"] == "shared-model"
    assert probe_calls[-1]["api_path"] == "/v1/chat/completions"
    assert "model" not in settings_repo.load_ai_assistant_settings()

    missing_url_settings = client.post(
        "/api/settings",
        json=_settings_payload(
            enabled.json(),
            prompt_optimizer={
                "enabled": True,
                "api_url": "",
                "model": "shared-model",
                "timeout_seconds": 45,
                # A registry credential cannot stay bound once the URL is cleared.
                "api_key": "",
            },
        ),
    )
    assert missing_url_settings.status_code == 200
    missing_url = client.post("/api/assistant/health")
    assert missing_url.status_code == 200
    assert missing_url.json()["status"] == "error"
    assert "not configured" in missing_url.json()["message"]


def test_ai_assistant_health_timeout_returns_structured_504(client, monkeypatch):
    settings = client.get("/api/settings").json()
    configured = client.post(
        "/api/settings",
        json=_settings_payload(
            settings,
            prompt_optimizer={
                "enabled": True,
                "api_url": "https://example.com/v1/chat/completions",
                "model": "shared-model",
                "timeout_seconds": 7,
                "api_key": "test-prompt-optimizer-key",
            },
        ),
    )
    assert configured.status_code == 200
    enabled = client.post(
        "/api/settings",
        json=_assistant_payload(
            configured.json(),
            ai_assistant={"enabled": True},
        ),
    )
    assert enabled.status_code == 200

    async def timeout_probe(**kwargs):
        raise assistant_router.assistant_client.AssistantTimeoutError("AI Assistant request timed out")

    monkeypatch.setattr(assistant_router.assistant_client, "probe_assistant_endpoint", timeout_probe)

    resp = client.post("/api/assistant/health")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "error",
        "message": "AI Assistant request timed out",
        "model": "shared-model",
        "duration_ms": 7000,
        "status_code": 504,
    }


def test_ai_assistant_prompt_tools_and_param_recommendations(client, monkeypatch):
    settings = client.get("/api/settings").json()
    configured = client.post(
        "/api/settings",
        json=_assistant_runtime_payload(settings, optimizer_api_url="https://example.com/v1/responses"),
    )
    assert configured.status_code == 200

    seen: dict[str, object] = {}

    async def fake_request_assistant_json(**kwargs):
        seen["kwargs"] = kwargs
        schema = kwargs["schema"]
        if "rewritten_prompt" in schema:
            return ({"rewritten_prompt": "expanded prompt", "warnings": ["warn"]}, "assistant-model", 11)
        if "score" in schema:
            return (
                {
                    "score": 83,
                    "summary": "Clear enough",
                    "issues": [
                        {"severity": "warning", "message": "Too vague", "suggestion": "Add lighting"}
                    ],
                    "warnings": [],
                },
                "assistant-model",
                12,
            )
        if "variants" in schema:
            return (
                {
                    "variants": [
                        {"title": "Variant 1", "prompt": "one", "angle": "alt"},
                        {"title": "Variant 2", "prompt": "two"},
                    ],
                    "warnings": ["useful"],
                },
                "assistant-model",
                13,
            )
        if "model_name" in schema:
            return (
                {
                    "model_name": "assistant-model",
                    "size": "1024x1024",
                    "quality": "high",
                    "output_format": "png",
                    "n": 8,
                    "rationale": "good fit",
                    "warnings": ["responses path"],
                },
                "assistant-model",
                14,
            )
        raise AssertionError(f"Unexpected schema {schema}")

    monkeypatch.setattr(assistant_router.assistant_client, "request_assistant_json", fake_request_assistant_json)

    rewrite = client.post(
        "/api/assistant/prompt/rewrite",
        json={
            "prompt": "tiny robot",
            "instruction": "make it cinematic",
            "target_language": "en",
            "api_path": "/v1/images/generations",
            "model": "gpt-image-2",
            "size": "1024x1024",
            "quality": "high",
        },
    )
    assert rewrite.status_code == 200
    assert rewrite.json()["rewritten_prompt"] == "expanded prompt"
    assert rewrite.json()["warnings"] == ["warn"]

    check = client.post(
        "/api/assistant/prompt/check",
        json={
            "prompt": "tiny robot",
            "api_path": "/v1/responses",
            "model": "gpt-image-2",
            "size": "1024x1024",
            "quality": "high",
        },
    )
    assert check.status_code == 200
    assert check.json()["score"] == 83
    assert check.json()["issues"][0]["severity"] == "warning"

    variants = client.post(
        "/api/assistant/prompt/variants",
        json={
            "prompt": "tiny robot",
            "instruction": "make it cinematic",
            "count": 3,
            "target_language": "same",
            "api_path": "/v1/chat/completions",
            "model": "gpt-image-2",
            "size": "1024x1024",
            "quality": "high",
        },
    )
    assert variants.status_code == 200
    assert [item["title"] for item in variants.json()["variants"]] == ["Variant 1", "Variant 2"]

    recommend_chat = client.post(
        "/api/assistant/generate/recommend-params",
        json={
            "prompt": "tiny robot",
            "api_path": "/v1/chat/completions",
            "current_model": "gpt-image-2",
            "current_size": "1024x1024",
            "current_quality": "high",
            "current_output_format": "png",
            "current_n": 1,
        },
    )
    assert recommend_chat.status_code == 200
    assert recommend_chat.json()["size"] is None
    assert any("does not support" in warning for warning in recommend_chat.json()["warnings"])

    recommend_images = client.post(
        "/api/assistant/generate/recommend-params",
        json={
            "prompt": "tiny robot",
            "api_path": "/v1/images/generations",
            "current_model": "gpt-image-2",
            "current_size": "1024x1024",
            "current_quality": "high",
            "current_output_format": "png",
            "current_n": 1,
        },
    )
    assert recommend_images.status_code == 200
    assert recommend_images.json()["size"] == "1024x1024"
    assert recommend_images.json()["n"] == 8
    assert recommend_images.json()["model_name"] == "assistant-model"

    assert seen["kwargs"]["api_url"] == "https://example.com"
    assert seen["kwargs"]["api_key"] == "optimizer-key"
    assert seen["kwargs"]["api_path"] == "/v1/responses"


def test_ai_assistant_request_concurrency_limit_returns_429(client, monkeypatch):
    settings = client.get("/api/settings").json()
    configured = client.post(
        "/api/settings",
        json=_assistant_runtime_payload(settings),
    )
    assert configured.status_code == 200

    config.AI_ASSISTANT_MAX_CONCURRENCY = 1
    acquired_once = False

    def fake_acquire_background_slot(**kwargs):
        nonlocal acquired_once
        assert kwargs["slot_count"] == 1
        if acquired_once:
            return None
        acquired_once = True
        return "ai_assistant_request:0"

    async def fake_request_assistant_json(**kwargs):
        return ({"rewritten_prompt": "limited prompt", "warnings": []}, "assistant-model", 10)

    monkeypatch.setattr(assistant_runtime, "acquire_background_slot", fake_acquire_background_slot)
    monkeypatch.setattr(assistant_runtime, "release_background_slot", lambda **kwargs: True)
    monkeypatch.setattr(assistant_router.assistant_client, "request_assistant_json", fake_request_assistant_json)

    ok = client.post("/api/assistant/prompt/rewrite", json={"prompt": "tiny robot", "target_language": "en"})
    assert ok.status_code == 200
    limited = client.post("/api/assistant/prompt/rewrite", json={"prompt": "tiny robot", "target_language": "en"})
    assert limited.status_code == 429
    assert "concurrency limit" in limited.json()["detail"]


def test_ai_assistant_response_fields_are_truncated_before_persistence(client, monkeypatch):
    settings = client.get("/api/settings").json()
    configured = client.post(
        "/api/settings",
        json=_assistant_runtime_payload(settings),
    )
    assert configured.status_code == 200
    entry = _fake_gallery_entry("assistant-long-fields", "prompt", "1024x1024", "assistant-long-fields.png")
    assert entry is not None

    async def fake_request_assistant_json(**kwargs):
        return (
            {
                "description": "d" * 5000,
                "prompt": "p" * 9000,
                "analysis": {
                    "subjects": ["s" * 500 for _ in range(20)],
                    "style": "x" * 2000,
                    "composition": "c" * 2000,
                    "lighting": "l" * 2000,
                    "colors": ["red" * 80 for _ in range(20)],
                },
                "warnings": ["w" * 900 for _ in range(20)],
            },
            "assistant-vision-model",
            11,
        )

    monkeypatch.setattr(assistant_router.assistant_client, "request_assistant_json", fake_request_assistant_json)
    resp = client.post("/api/assistant/gallery/assistant-long-fields/analyze")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["description"]) == 2000
    assert len(body["prompt"]) == 4000
    assert len(body["analysis"]["style"]) == 500
    assert len(body["analysis"]["composition"]) == 800
    assert len(body["warnings"]) == 10
    assert all(len(warning) == 500 for warning in body["warnings"])

    metadata = settings_repo.get_gallery_ai_metadata("assistant-long-fields")
    assert metadata is not None
    assert len(metadata["description"]) == 2000
    assert len(metadata["prompt"]) == 4000


def test_ai_assistant_job_diagnose_does_not_expose_secrets(client, monkeypatch):
    settings = client.get("/api/settings").json()
    configured = client.post(
        "/api/settings",
        json=_assistant_runtime_payload(settings),
    )
    assert configured.status_code == 200

    image_jobs_repo.upsert_generate_job(
        {
            "job_id": "diagnose-job",
            "status": "error",
            "stage": "generation_failed",
            "message": "Failed upstream with Authorization: Bearer env-secret and https://api.example.com/v1/path?api_key=env-secret",
            "operation": "generation",
            "prompt": "secret prompt",
            "size": "1024x1024",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "completed_at": "2026-01-01T00:00:00+00:00",
            "api_path": "/v1/images/generations",
            "api_preset_name": "Default",
            "error": "upstream failed api_key=env-secret",
        }
    )

    async def fake_request_assistant_json(**kwargs):
        assert "secret prompt" not in kwargs["user_prompt"]
        assert "env-secret" not in kwargs["user_prompt"]
        assert "[REDACTED]" in kwargs["user_prompt"]
        return (
            {
                "summary": "Upstream failure",
                "likely_causes": ["invalid prompt"],
                "recommended_actions": ["retry"],
                "warnings": ["no secrets"],
            },
            "assistant-model",
            15,
        )

    monkeypatch.setattr(assistant_router.assistant_client, "request_assistant_json", fake_request_assistant_json)
    resp = client.post("/api/assistant/jobs/diagnose-job/diagnose", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"] == "Upstream failure"
    assert body["safe_job"]["job_id"] == "diagnose-job"
    assert "prompt" not in body["safe_job"]
    assert "env-secret" not in json.dumps(body)


def test_ai_assistant_gallery_metadata_and_analysis_flow(client, monkeypatch):
    settings = client.get("/api/settings").json()
    configured = client.post(
        "/api/settings",
        json=_assistant_runtime_payload(settings),
    )
    assert configured.status_code == 200

    seeded = _fake_gallery_entry("assistant-gallery", "gallery prompt", "1024x1024", "assistant-gallery.png")
    assert seeded is not None

    async def fake_request_assistant_json(**kwargs):
        if "preview" in kwargs["user_prompt"]:
            assert kwargs["image"]["mime_type"] == "image/png"
            assert kwargs["image"]["source_has_alpha"] is True
            assert kwargs["image"]["bytes"] <= config.AI_ASSISTANT_IMAGE_MAX_BYTES
            schema = kwargs["schema"]
            if "analysis" not in schema and "description" in schema:
                return (
                    {
                        "description": "A small red square",
                        "warnings": [],
                    },
                    "assistant-vision-model",
                    15,
                )
            if "analysis" not in schema and "prompt" in schema:
                assert "stored_prompt" not in kwargs["user_prompt"]
                assert "gallery prompt" not in kwargs["user_prompt"]
                assert "visible pixels" in kwargs["system_prompt"]
                assert "negative prompt" in kwargs["system_prompt"]
                return (
                    {
                        "prompt": "red square on white background",
                        "warnings": [],
                    },
                    "assistant-vision-model",
                    16,
                )
            return (
                {
                    "description": "A small red square",
                    "prompt": "red square on white background",
                    "analysis": {
                        "subjects": ["square"],
                        "style": "minimal",
                        "composition": "centered",
                        "lighting": "flat",
                        "colors": ["red", "white"],
                    },
                    "warnings": [],
                },
                "assistant-vision-model",
                17,
            )
        raise AssertionError("unexpected assistant request")

    monkeypatch.setattr(assistant_router.assistant_client, "request_assistant_json", fake_request_assistant_json)

    describe = client.post("/api/assistant/gallery/assistant-gallery/describe")
    assert describe.status_code == 200
    assert describe.json()["description"] == "A small red square"
    assert describe.json()["prompt"] == ""
    assert describe.json()["analysis"] == {}

    prompt = client.post("/api/assistant/gallery/assistant-gallery/prompt")
    assert prompt.status_code == 200
    assert prompt.json()["description"] == ""
    assert prompt.json()["prompt"] == "red square on white background"

    analyze = client.post("/api/assistant/gallery/assistant-gallery/analyze")
    assert analyze.status_code == 200
    assert analyze.json()["description"] == "A small red square"
    metadata = client.get("/api/assistant/gallery/assistant-gallery/metadata")
    assert metadata.status_code == 200
    assert metadata.json()["prompt"] == "red square on white background"
    assert metadata.json()["model"] == "assistant-vision-model"


def test_ai_assistant_uploaded_image_prompt_is_bounded_language_aware_and_not_persisted(client, monkeypatch):
    settings = client.get("/api/settings").json()
    configured = client.post("/api/settings", json=_assistant_runtime_payload(settings))
    assert configured.status_code == 200
    seen: dict[str, object] = {}

    async def fake_request_assistant_json(**kwargs):
        seen.update(kwargs)
        return (
            {
                "prompt": "p" * 5000,
                "warnings": ["w" * 700 for _ in range(12)],
            },
            "assistant-vision-model",
            23,
        )

    monkeypatch.setattr(assistant_router.assistant_client, "request_assistant_json", fake_request_assistant_json)
    gallery_count = gallery_queries.get_gallery_count()
    response = client.post(
        "/api/assistant/image/prompt",
        data={"target_language": "zh-CN"},
        files={"image": ("local.png", PNG_BYTES, "image/png")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["prompt"] == "p" * 4000
    assert body["model"] == "assistant-vision-model"
    assert body["duration_ms"] == 23
    assert len(body["warnings"]) == 10
    assert all(len(warning) == 500 for warning in body["warnings"])
    assert gallery_queries.get_gallery_count() == gallery_count

    assert seen["image"]["mime_type"] == "image/png"
    assert seen["image"]["source_has_alpha"] is True
    assert seen["image"]["bytes"] <= config.AI_ASSISTANT_IMAGE_MAX_BYTES
    assert json.loads(seen["user_prompt"])["target_language"] == "zh-CN"
    assert "Write the prompt in Simplified Chinese" in seen["system_prompt"]
    assert "subject identity and action" in seen["system_prompt"]
    assert "overall color palette and color relationships" in seen["system_prompt"]
    assert "dominant and accent colors" in seen["system_prompt"]
    assert "artistic medium and rendering style" in seen["system_prompt"]
    assert "line quality" in seen["system_prompt"]
    assert "named character, mascot, or public figure" in seen["system_prompt"]
    assert "anime, manga, comics, games, film, or television" in seen["system_prompt"]
    assert "the source work or franchise" in seen["system_prompt"]
    assert "not as attribution of the input image's source" in seen["system_prompt"]
    assert "If the identity is uncertain, do not guess" in seen["system_prompt"]
    assert "Use high information density" in seen["system_prompt"]
    assert "spatial relationships" in seen["system_prompt"]
    assert "single prompt in the requested language" in seen["system_prompt"]
    assert "Do not invent unseen brands" in seen["system_prompt"]
    assert "artist names, exact camera or lens settings, or background stories" in seen["system_prompt"]
    assert "Do not create a separate negative prompt" in seen["system_prompt"]


@pytest.mark.parametrize(
    ("filename", "image_bytes", "content_type"),
    [
        ("not-image.txt", b"plain text", "text/plain"),
        ("damaged.png", PNG_BYTES[:32], "image/png"),
        ("vector.svg", b"<svg></svg>", "image/svg+xml"),
    ],
)
def test_ai_assistant_uploaded_image_prompt_rejects_unsafe_or_damaged_files(
    client,
    monkeypatch,
    filename,
    image_bytes,
    content_type,
):
    settings = client.get("/api/settings").json()
    configured = client.post("/api/settings", json=_assistant_runtime_payload(settings))
    assert configured.status_code == 200

    async def unexpected_request(**kwargs):
        raise AssertionError("invalid uploads must not reach the AI Assistant")

    monkeypatch.setattr(assistant_router.assistant_client, "request_assistant_json", unexpected_request)
    response = client.post(
        "/api/assistant/image/prompt",
        files={"image": (filename, image_bytes, content_type)},
    )
    assert response.status_code == 400


def test_ai_assistant_uploaded_image_prompt_rejects_pixel_and_request_size_limits(client, monkeypatch):
    settings = client.get("/api/settings").json()
    configured = client.post("/api/settings", json=_assistant_runtime_payload(settings))
    assert configured.status_code == 200

    monkeypatch.setattr(config, "MAX_IMAGE_PIXELS", 0)
    pixel_limited = client.post(
        "/api/assistant/image/prompt",
        files={"image": ("pixel-limit.png", PNG_BYTES, "image/png")},
    )
    assert pixel_limited.status_code == 400
    assert "fully decodable" in pixel_limited.json()["detail"]

    monkeypatch.setattr(config, "MAX_FILE_SIZE_MB", 0)
    byte_limited = client.post(
        "/api/assistant/image/prompt",
        files={"image": ("byte-limit.png", PNG_BYTES, "image/png")},
    )
    assert byte_limited.status_code == 413
    assert "too large" in byte_limited.json()["detail"]


def test_ai_assistant_uploaded_image_prompt_rejects_decompression_bomb_warning(client, monkeypatch):
    from PIL import Image

    settings = client.get("/api/settings").json()
    configured = client.post("/api/settings", json=_assistant_runtime_payload(settings))
    assert configured.status_code == 200

    image = io.BytesIO()
    Image.new("RGB", (5, 4)).save(image, format="PNG")
    monkeypatch.setattr(config, "MAX_IMAGE_PIXELS", 16)

    async def unexpected_request(**kwargs):
        raise AssertionError("decompression-bomb upload must not reach the AI Assistant")

    monkeypatch.setattr(assistant_router.assistant_client, "request_assistant_json", unexpected_request)
    response = client.post(
        "/api/assistant/image/prompt",
        files={"image": ("pixel-warning.png", image.getvalue(), "image/png")},
    )

    assert response.status_code == 400
    assert "fully decodable" in response.json()["detail"]


def test_ai_assistant_uploaded_image_prompt_validates_language_and_maps_upstream_errors(client, monkeypatch):
    settings = client.get("/api/settings").json()
    configured = client.post("/api/settings", json=_assistant_runtime_payload(settings))
    assert configured.status_code == 200

    invalid_language = client.post(
        "/api/assistant/image/prompt",
        data={"target_language": "same"},
        files={"image": ("local.png", PNG_BYTES, "image/png")},
    )
    assert invalid_language.status_code == 422

    async def failing_request(**kwargs):
        raise assistant_router.assistant_client.AssistantError("vision upstream failed", status=503)

    monkeypatch.setattr(assistant_router.assistant_client, "request_assistant_json", failing_request)
    upstream_failure = client.post(
        "/api/assistant/image/prompt",
        files={"image": ("local.png", PNG_BYTES, "image/png")},
    )
    assert upstream_failure.status_code == 502
    assert "vision upstream failed" in upstream_failure.json()["detail"]


def test_ai_assistant_uploaded_image_prompt_rejects_animations(client, monkeypatch):
    from PIL import Image

    settings = client.get("/api/settings").json()
    assert client.post("/api/settings", json=_assistant_runtime_payload(settings)).status_code == 200
    animated = io.BytesIO()
    Image.new("RGB", (1, 1), "red").save(
        animated,
        format="GIF",
        save_all=True,
        append_images=[Image.new("RGB", (1, 1), "blue")],
    )

    async def unexpected_request(**kwargs):
        raise AssertionError("animated upload must not reach the AI Assistant")

    monkeypatch.setattr(assistant_router.assistant_client, "request_assistant_json", unexpected_request)
    response = client.post(
        "/api/assistant/image/prompt",
        files={"image": ("animated.gif", animated.getvalue(), "image/gif")},
    )
    assert response.status_code == 400
    assert "Animated images are not supported" in response.json()["detail"]


def test_ai_assistant_gallery_prompt_language_and_empty_prompt_do_not_persist(client, monkeypatch):
    settings = client.get("/api/settings").json()
    assert client.post("/api/settings", json=_assistant_runtime_payload(settings)).status_code == 200
    assert _fake_gallery_entry("assistant-empty-prompt", "", "1024x1024", "assistant-empty-prompt.png")
    seen_system_prompts: list[str] = []

    async def empty_prompt(**kwargs):
        seen_system_prompts.append(kwargs["system_prompt"])
        return ({"description": "square", "prompt": "", "warnings": []}, "vision-model", 3)

    monkeypatch.setattr(assistant_router.assistant_client, "request_assistant_json", empty_prompt)
    prompt_response = client.post("/api/assistant/gallery/assistant-empty-prompt/prompt?target_language=zh-CN")
    analyze_response = client.post("/api/assistant/gallery/assistant-empty-prompt/analyze?target_language=zh-CN")
    metadata = client.get("/api/assistant/gallery/assistant-empty-prompt/metadata")

    assert prompt_response.status_code == 502
    assert analyze_response.status_code == 502
    assert metadata.status_code == 200
    assert metadata.json()["prompt"] == ""
    assert all("Write the prompt in Simplified Chinese" in prompt for prompt in seen_system_prompts)


def test_ai_assistant_gallery_batch_analysis_jobs_and_limits(client, monkeypatch):
    settings = client.get("/api/settings").json()
    configured = client.post(
        "/api/settings",
        json=_assistant_runtime_payload(settings),
    )
    assert configured.status_code == 200

    first = _fake_gallery_entry("assistant-batch-1", "prompt 1", "1024x1024", "assistant-batch-1.png")
    second = _fake_gallery_entry("assistant-batch-2", "prompt 2", "1024x1024", "assistant-batch-2.png")
    assert first is not None and second is not None

    seen: list[str] = []

    async def fake_request_assistant_json(**kwargs):
        seen.append(kwargs["user_prompt"])
        return (
            {
                "description": "batch",
                "prompt": "batch prompt",
                "analysis": {"subjects": ["batch"]},
                "warnings": [],
            },
            "assistant-vision-model",
            9,
        )

    monkeypatch.setattr(assistant_router.assistant_client, "request_assistant_json", fake_request_assistant_json)

    submitted = client.post(
        "/api/assistant/gallery/batch/analyze",
        json={"ids": ["assistant-batch-1", "assistant-batch-2"]},
    )
    assert submitted.status_code == 202
    job_id = submitted.json()["job_id"]

    job = _wait_for_gallery_batch_analyze_job(client, job_id)
    assert job["status"] == "success"
    assert job["requested_count"] == 2
    assert job["analyzed_count"] == 2
    assert settings_repo.get_gallery_ai_metadata("assistant-batch-1") is not None
    assert settings_repo.get_gallery_ai_metadata("assistant-batch-2") is not None
    assert len(seen) == 2

    events = client.get(f"/api/assistant/gallery/batch/analyze/{job_id}/events")
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert "event: analysis" in events.text
    assert job_id in events.text

    coordination_repo.create_gallery_job(
        job_id="assistant-batch-running",
        kind="ai_analyze",
        status="running",
        stage="analyzing",
        message="busy",
        progress=5,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        lease_expires_at="2999-01-01T00:00:00+00:00",
        requested_count=1,
        payload={"ids": ["assistant-batch-1"]},
    )
    limited = client.post(
        "/api/assistant/gallery/batch/analyze",
        json={"ids": ["assistant-batch-1"]},
    )
    assert limited.status_code == 429
    assert "already queued or running" in limited.json()["detail"]


def test_ai_assistant_gallery_batch_analysis_limits_selection_and_uses_filter_payload(client, monkeypatch):
    settings = client.get("/api/settings").json()
    configured = client.post(
        "/api/settings",
        json=_assistant_runtime_payload(settings),
    )
    assert configured.status_code == 200
    first = _fake_gallery_entry("assistant-filter-1", "filter prompt 1", "1024x1024", "assistant-filter-1.png")
    second = _fake_gallery_entry("assistant-filter-2", "filter prompt 2", "1024x1024", "assistant-filter-2.png")
    assert first is not None and second is not None

    token_resp = client.post("/api/gallery/batch/selection-tokens", json={"filters": {"prompt": "filter prompt"}})
    assert token_resp.status_code == 201
    token = token_resp.json()["selection_token"]

    original_limit = config.AI_ASSISTANT_BATCH_MAX_IMAGES
    config.AI_ASSISTANT_BATCH_MAX_IMAGES = 1
    try:
        too_many = client.post("/api/assistant/gallery/batch/analyze", json={"selection_token": token})
    finally:
        config.AI_ASSISTANT_BATCH_MAX_IMAGES = original_limit
    assert too_many.status_code == 413

    async def fake_request_assistant_json(**kwargs):
        return (
            {
                "description": "filter batch",
                "prompt": "filter prompt",
                "analysis": {"subjects": ["filter"]},
                "warnings": [],
            },
            "assistant-vision-model",
            9,
        )

    monkeypatch.setattr(assistant_router.assistant_client, "request_assistant_json", fake_request_assistant_json)
    submitted = client.post("/api/assistant/gallery/batch/analyze", json={"selection_token": token})
    assert submitted.status_code == 202
    job_id = submitted.json()["job_id"]
    queued_job = coordination_repo.get_gallery_job("ai_analyze", job_id)
    assert queued_job is not None
    assert "filters" in queued_job["payload"]
    assert "ids" not in queued_job["payload"]

    job = _wait_for_gallery_batch_analyze_job(client, job_id)
    assert job["status"] == "success"
    assert job["requested_count"] == 2
    assert job["analyzed_count"] == 2


def test_ai_assistant_gallery_batch_analysis_selection_snapshot_excludes_later_rows(client, monkeypatch):
    settings = client.get("/api/settings").json()
    configured = client.post("/api/settings", json=_assistant_runtime_payload(settings))
    assert configured.status_code == 200
    _fake_gallery_entry("assistant-snapshot-1", "snapshot prompt 1", "1024x1024", "assistant-snapshot-1.png")
    _fake_gallery_entry("assistant-snapshot-2", "snapshot prompt 2", "1024x1024", "assistant-snapshot-2.png")

    token_resp = client.post("/api/gallery/batch/selection-tokens", json={"filters": {"prompt": "snapshot prompt"}})
    assert token_resp.status_code == 201
    monkeypatch.setattr(assistant_router, "_kick_ai_analyze_dispatcher", lambda: None)

    submitted = client.post(
        "/api/assistant/gallery/batch/analyze",
        json={"selection_token": token_resp.json()["selection_token"]},
    )
    assert submitted.status_code == 202
    job_id = submitted.json()["job_id"]
    queued = coordination_repo.get_gallery_job("ai_analyze", job_id)
    assert queued is not None
    assert queued["payload"]["snapshot"]

    _fake_gallery_entry("assistant-snapshot-3", "snapshot prompt 3", "1024x1024", "assistant-snapshot-3.png")
    analyzed_ids: list[str] = []

    async def fake_analyze_with_lease(image_id, **kwargs):
        analyzed_ids.append(image_id)

    monkeypatch.setattr(assistant_router, "_analyze_gallery_image_with_lease_renewal", fake_analyze_with_lease)
    claimed = coordination_repo.claim_next_gallery_job(
        kind="ai_analyze",
        worker_id="owner-a",
        lease_expires_at="2999-01-01T00:00:00+00:00",
        now="2026-01-01T00:00:00+00:00",
        running_limit=1,
    )
    assert claimed is not None

    asyncio.run(assistant_router._run_ai_analyze_job(claimed))
    stored = coordination_repo.get_gallery_job("ai_analyze", job_id)
    assert stored is not None
    assert stored["status"] == "success"
    assert stored["requested_count"] == 2
    assert stored["processed_count"] == 2
    assert stored["exported_count"] == 2
    assert set(analyzed_ids) == {"assistant-snapshot-1", "assistant-snapshot-2"}


def test_ai_assistant_gallery_batch_analysis_selection_snapshot_counts_deleted_rows(client, monkeypatch):
    settings = client.get("/api/settings").json()
    configured = client.post("/api/settings", json=_assistant_runtime_payload(settings))
    assert configured.status_code == 200
    _fake_gallery_entry("assistant-snapshot-delete-1", "snapshot delete 1", "1024x1024", "assistant-snapshot-delete-1.png")
    _fake_gallery_entry("assistant-snapshot-delete-2", "snapshot delete 2", "1024x1024", "assistant-snapshot-delete-2.png")

    token_resp = client.post("/api/gallery/batch/selection-tokens", json={"filters": {"prompt": "snapshot delete"}})
    assert token_resp.status_code == 201
    monkeypatch.setattr(assistant_router, "_kick_ai_analyze_dispatcher", lambda: None)
    submitted = client.post(
        "/api/assistant/gallery/batch/analyze",
        json={"selection_token": token_resp.json()["selection_token"]},
    )
    assert submitted.status_code == 202
    job_id = submitted.json()["job_id"]
    deleted, _deleted_files = gallery_mutations.delete_gallery_image("assistant-snapshot-delete-2")
    assert deleted is True

    analyzed_ids: list[str] = []

    async def fake_analyze_with_lease(image_id, **kwargs):
        analyzed_ids.append(image_id)

    monkeypatch.setattr(assistant_router, "_analyze_gallery_image_with_lease_renewal", fake_analyze_with_lease)
    claimed = coordination_repo.claim_next_gallery_job(
        kind="ai_analyze",
        worker_id="owner-a",
        lease_expires_at="2999-01-01T00:00:00+00:00",
        now="2026-01-01T00:00:00+00:00",
        running_limit=1,
    )
    assert claimed is not None

    asyncio.run(assistant_router._run_ai_analyze_job(claimed))
    stored = coordination_repo.get_gallery_job("ai_analyze", job_id)
    assert stored is not None
    assert stored["status"] == "error"
    assert stored["requested_count"] == 2
    assert stored["processed_count"] == 1
    assert stored["exported_count"] == 1
    assert stored["missing_count"] == 1
    assert stored["failed_count"] == 0
    assert analyzed_ids == ["assistant-snapshot-delete-1"]


def test_ai_assistant_gallery_batch_analysis_validates_runtime_before_queueing(client, monkeypatch):
    settings = client.get("/api/settings").json()
    configured = client.post(
        "/api/settings",
        json=_assistant_runtime_payload(settings),
    )
    assert configured.status_code == 200
    # The registry entry stays configured, but its environment variable is gone.
    monkeypatch.delenv("TEST_PROMPT_OPTIMIZER_API_KEY", raising=False)

    entry = _fake_gallery_entry("assistant-batch-config-error", "prompt", "1024x1024", "assistant-batch-config.png")
    assert entry is not None

    submitted = client.post(
        "/api/assistant/gallery/batch/analyze",
        json={"ids": ["assistant-batch-config-error"]},
    )

    assert submitted.status_code == 422
    assert "resolves to an empty value" in submitted.json()["detail"]
    assert coordination_repo.count_active_gallery_jobs("ai_analyze") == 0


def test_ai_assistant_gallery_batch_analysis_renews_lease_during_image_calls(client, monkeypatch):
    settings = client.get("/api/settings").json()
    configured = client.post(
        "/api/settings",
        json=_assistant_runtime_payload(settings),
    )
    assert configured.status_code == 200
    entry = _fake_gallery_entry("assistant-batch-renew", "prompt", "1024x1024", "assistant-batch-renew.png")
    assert entry is not None

    original_interval = assistant_router.AI_ANALYZE_JOB_LEASE_RENEW_SECONDS
    assistant_router.AI_ANALYZE_JOB_LEASE_RENEW_SECONDS = 0.01
    renewals: list[tuple[str, str]] = []
    original_renew = coordination_repo.renew_gallery_job_lease

    def fake_renew_gallery_job_lease(**kwargs):
        renewals.append((kwargs["job_id"], kwargs["lease_owner"]))
        return original_renew(**kwargs)

    async def fake_request_assistant_json(**kwargs):
        await asyncio.sleep(0.05)
        return (
            {
                "description": "renewed",
                "prompt": "renewed prompt",
                "analysis": {"subjects": ["renewed"]},
                "warnings": [],
            },
            "assistant-vision-model",
            9,
        )

    monkeypatch.setattr(assistant_router, "renew_gallery_job_lease", fake_renew_gallery_job_lease)
    monkeypatch.setattr(assistant_router.assistant_client, "request_assistant_json", fake_request_assistant_json)
    try:
        submitted = client.post(
            "/api/assistant/gallery/batch/analyze",
            json={"ids": ["assistant-batch-renew"]},
        )
        assert submitted.status_code == 202
        job_id = submitted.json()["job_id"]
        job = _wait_for_gallery_batch_analyze_job(client, job_id)
    finally:
        assistant_router.AI_ANALYZE_JOB_LEASE_RENEW_SECONDS = original_interval

    assert job["status"] == "success"
    assert renewals
    assert all(item[0] == job_id for item in renewals)
    assert all(item[1] for item in renewals)


def test_ai_assistant_gallery_batch_analysis_retries_assistant_slot_backpressure(client, monkeypatch):
    calls = 0

    async def fake_analyze_with_lease(image_id, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise assistant_router.HTTPException(status_code=429, detail="AI Assistant is at its concurrency limit")

    monkeypatch.setattr(assistant_router, "AI_ASSISTANT_SLOT_RETRY_SECONDS", 0)
    monkeypatch.setattr(assistant_router, "_analyze_gallery_image_with_lease_renewal", fake_analyze_with_lease)
    job = coordination_repo.create_gallery_job(
        job_id="assistant-batch-backpressure",
        kind="ai_analyze",
        status="running",
        stage="analyzing",
        message="running",
        progress=0,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        lease_owner="owner-a",
        lease_expires_at="2999-01-01T00:00:00+00:00",
        requested_count=1,
        payload={"ids": ["assistant-backpressure-1"]},
    )

    asyncio.run(assistant_router._run_ai_analyze_job(job))
    stored = coordination_repo.get_gallery_job("ai_analyze", "assistant-batch-backpressure")
    assert stored is not None
    assert stored["status"] == "success"
    assert stored["processed_count"] == 1
    assert stored["exported_count"] == 1
    assert stored["failed_count"] == 0
    assert calls == 2


def test_ai_assistant_gallery_batch_analysis_stops_when_lease_owner_changes(client, monkeypatch):
    settings = client.get("/api/settings").json()
    configured = client.post(
        "/api/settings",
        json=_assistant_runtime_payload(settings),
    )
    assert configured.status_code == 200
    entry = _fake_gallery_entry("assistant-batch-owner", "prompt", "1024x1024", "assistant-batch-owner.png")
    assert entry is not None

    async def fake_analyze_with_lease(*args, **kwargs):
        return None

    progress_calls = 0
    original_update_progress = coordination_repo.update_gallery_job_progress

    def fake_update_progress(job_id, updates, **kwargs):
        nonlocal progress_calls
        progress_calls += 1
        if progress_calls == 1:
            return False
        return original_update_progress(job_id, updates, **kwargs)

    monkeypatch.setattr(assistant_router, "_analyze_gallery_image_with_lease_renewal", fake_analyze_with_lease)
    monkeypatch.setattr(assistant_router, "update_gallery_job_progress", fake_update_progress)

    job = coordination_repo.create_gallery_job(
        job_id="assistant-batch-owner-job",
        kind="ai_analyze",
        status="running",
        stage="analyzing",
        message="running",
        progress=0,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        lease_owner="owner-a",
        lease_expires_at="2999-01-01T00:00:00+00:00",
        requested_count=1,
        payload={"ids": ["assistant-batch-owner"]},
    )

    asyncio.run(assistant_router._run_ai_analyze_job(job))
    stored = coordination_repo.get_gallery_job("ai_analyze", "assistant-batch-owner-job")
    assert stored is not None
    assert stored["status"] == "running"
    assert stored["progress"] == 0
    assert progress_calls == 1


def test_ai_assistant_gallery_batch_analysis_resumes_id_jobs_after_stored_progress(client, monkeypatch):
    analyzed_ids: list[str] = []

    async def fake_analyze_with_lease(image_id, **kwargs):
        analyzed_ids.append(image_id)

    monkeypatch.setattr(assistant_router, "_analyze_gallery_image_with_lease_renewal", fake_analyze_with_lease)

    job = coordination_repo.create_gallery_job(
        job_id="assistant-batch-resume-ids",
        kind="ai_analyze",
        status="running",
        stage="analyzing",
        message="running",
        progress=32,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        lease_owner="owner-a",
        lease_expires_at="2999-01-01T00:00:00+00:00",
        requested_count=3,
        processed_count=1,
        exported_count=1,
        payload={"ids": ["assistant-resume-1", "assistant-resume-2", "assistant-resume-3"]},
    )

    asyncio.run(assistant_router._run_ai_analyze_job(job))
    stored = coordination_repo.get_gallery_job("ai_analyze", "assistant-batch-resume-ids")
    assert stored is not None
    assert stored["status"] == "success"
    assert stored["processed_count"] == 3
    assert stored["exported_count"] == 3
    assert analyzed_ids == ["assistant-resume-2", "assistant-resume-3"]


def test_ai_assistant_gallery_batch_analysis_checkpoints_completed_selection_rows(client, monkeypatch):
    def fake_get_gallery_id_batch(*args, **kwargs):
        return [
            {"id": "assistant-select-1", "sort_seq": 30},
            {"id": "assistant-select-2", "sort_seq": 20},
            {"id": "assistant-select-3", "sort_seq": 10},
        ]

    analyzed_ids: list[str] = []

    async def fake_analyze_with_lease(image_id, **kwargs):
        analyzed_ids.append(image_id)
        if image_id == "assistant-select-2":
            raise assistant_router.AIAnalyzeJobLeaseLost("lost lease")

    monkeypatch.setattr(assistant_router, "get_gallery_id_batch", fake_get_gallery_id_batch)
    monkeypatch.setattr(assistant_router, "_analyze_gallery_image_with_lease_renewal", fake_analyze_with_lease)

    job = coordination_repo.create_gallery_job(
        job_id="assistant-batch-selection-checkpoint",
        kind="ai_analyze",
        status="running",
        stage="analyzing",
        message="running",
        progress=0,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        lease_owner="owner-a",
        lease_expires_at="2999-01-01T00:00:00+00:00",
        requested_count=3,
        payload={"filters": {}, "checkpoint": None},
    )

    asyncio.run(assistant_router._run_ai_analyze_job(job))
    stored = coordination_repo.get_gallery_job("ai_analyze", "assistant-batch-selection-checkpoint")
    assert stored is not None
    assert stored["status"] == "running"
    assert stored["processed_count"] == 1
    assert stored["exported_count"] == 1
    assert stored["payload"]["checkpoint"] == {"id": "assistant-select-1", "sort_seq": 30}
    assert analyzed_ids == ["assistant-select-1", "assistant-select-2"]


def test_ai_assistant_gallery_batch_analysis_treats_missing_images_as_error(client, monkeypatch):
    settings = client.get("/api/settings").json()
    configured = client.post(
        "/api/settings",
        json=_assistant_runtime_payload(settings),
    )
    assert configured.status_code == 200

    entry = _fake_gallery_entry("assistant-batch-missing", "prompt", "1024x1024", "assistant-batch-missing.png")
    assert entry is not None
    image_path = image_files.safe_image_path(entry.filename)
    assert image_path is not None
    image_path.unlink()

    async def fake_request_assistant_json(**kwargs):
        raise AssertionError("missing image should not call assistant")

    monkeypatch.setattr(assistant_router.assistant_client, "request_assistant_json", fake_request_assistant_json)

    submitted = client.post(
        "/api/assistant/gallery/batch/analyze",
        json={"ids": ["assistant-batch-missing"]},
    )
    assert submitted.status_code == 202

    job = _wait_for_gallery_batch_analyze_job(client, submitted.json()["job_id"])
    assert job["status"] == "error"
    assert job["stage"] == "error"
    assert job["requested_count"] == 1
    assert job["processed_count"] == 1
    assert job["analyzed_count"] == 0
    assert job["missing_count"] == 1
    assert job["failed_count"] == 0
    assert "missing" in job["error"]


def test_ai_assistant_gallery_batch_jobs_are_cleaned_by_gallery_job_gc(client):
    coordination_repo.create_gallery_job(
        job_id="assistant-batch-stale-success",
        kind="ai_analyze",
        status="success",
        stage="completed",
        message="done",
        progress=100,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:00:00+00:00",
        requested_count=1,
        exported_count=1,
        payload={"ids": ["assistant-batch-1"]},
    )

    stale = coordination_repo.cleanup_stale_gallery_jobs(
        gallery_common.AI_ANALYZE_JOB_KIND,
        gallery_common.AI_ANALYZE_JOB_TTL_SECONDS,
    )

    assert [job["job_id"] for job in stale] == ["assistant-batch-stale-success"]
    assert coordination_repo.get_gallery_job("ai_analyze", "assistant-batch-stale-success") is None
