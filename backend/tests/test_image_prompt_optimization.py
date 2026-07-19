import base64
import io
import json

import pytest
from PIL import Image

from backend.app.integrations import assistant_client
from backend.app.integrations.upstream.errors import UpstreamApiError
from backend.app.repositories.gallery import queries as gallery_queries
from backend.app.repositories.image_jobs import list_generate_jobs
from backend.app.services import assistant_vision
from backend.tests.support.contract import PNG_BYTES, _assistant_runtime_payload


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ((1024, 1024), (896, 896)),
        ((1600, 900), (1200, 672)),
        ((900, 1600), (672, 1200)),
        ((3000, 1000), (1536, 512)),
    ],
)
def test_prompt_preview_size_is_near_point_eight_megapixels_and_aligned(source, expected):
    width, height = assistant_vision.calculate_prompt_preview_size(*source)
    assert (width, height) == expected
    assert width % 16 == 0
    assert height % 16 == 0
    assert 655_360 <= width * height <= 900_000


@pytest.mark.parametrize("source", [(3001, 1000), (1000, 3001), (0, 100)])
def test_prompt_preview_size_rejects_invalid_or_extreme_aspect_ratios(source):
    with pytest.raises(ValueError):
        assistant_vision.calculate_prompt_preview_size(*source)


def _vision_image(marker: str) -> dict[str, str | int | bool]:
    return {
        "b64": base64.b64encode(marker.encode()).decode(),
        "mime_type": "image/png",
        "width": 16,
        "height": 16,
        "source_has_alpha": False,
        "label": marker,
    }


def test_multi_image_chat_payload_labels_images_in_order_and_keeps_single_image_shape():
    target = _vision_image("Target image (first image)")
    trial = _vision_image("Trial generated image (second image)")
    multi = assistant_client._build_chat_payload(
        model="vision",
        system_prompt="system",
        user_prompt="compare",
        image=None,
        images=[target, trial],
        max_tokens=100,
        temperature=0.2,
    )
    content = multi["messages"][1]["content"]
    assert [item["type"] for item in content] == ["text", "text", "image_url", "text", "image_url"]
    assert content[1]["text"] == target["label"]
    assert content[3]["text"] == trial["label"]
    assert content[2]["image_url"]["url"].endswith(target["b64"])
    assert content[4]["image_url"]["url"].endswith(trial["b64"])

    single = assistant_client._build_chat_payload(
        model="vision",
        system_prompt="system",
        user_prompt="analyze",
        image=target,
        max_tokens=100,
        temperature=0.2,
    )
    assert [item["type"] for item in single["messages"][1]["content"]] == ["text", "image_url"]


def test_multi_image_responses_payload_labels_images_in_order_and_keeps_single_image_shape():
    target = _vision_image("Target image (first image)")
    trial = _vision_image("Trial generated image (second image)")
    multi = assistant_client._build_responses_payload(
        model="vision",
        system_prompt="system",
        user_prompt="compare",
        image=None,
        images=[target, trial],
        max_tokens=100,
        temperature=0.2,
    )
    content = multi["input"][0]["content"]
    assert [item["type"] for item in content] == [
        "input_text",
        "input_text",
        "input_image",
        "input_text",
        "input_image",
    ]
    assert content[1]["text"] == target["label"]
    assert content[3]["text"] == trial["label"]

    single = assistant_client._build_responses_payload(
        model="vision",
        system_prompt="system",
        user_prompt="analyze",
        image=target,
        max_tokens=100,
        temperature=0.2,
    )
    assert [item["type"] for item in single["input"][0]["content"]] == ["input_text", "input_image"]


def test_generated_image_mime_type_uses_original_bytes():
    assert assistant_vision._generated_image_mime_type(PNG_BYTES) == "image/png"


def test_image_prompt_optimization_contract_uses_active_preset_and_does_not_persist(client, monkeypatch):
    settings = client.get("/api/settings").json()
    configured = client.post("/api/settings", json=_assistant_runtime_payload(settings))
    assert configured.status_code == 200
    calls: dict[str, object] = {}

    async def fake_generate(api_url, api_key, payload, *, socks5_proxy=None):
        calls["generation"] = {
            "api_url": api_url,
            "api_key": api_key,
            "payload": payload,
            "socks5_proxy": socks5_proxy,
        }
        return PNG_BYTES

    async def fake_assistant(**kwargs):
        calls["assistant"] = kwargs
        return (
            {
                "prompt": "refined prompt " * 500,
                "comparison_summary": "visible differences " * 200,
                "warnings": ["warning " * 100 for _ in range(12)],
            },
            "assistant-vision-model",
            37,
        )

    monkeypatch.setattr(assistant_vision, "call_image_generation_preview_api", fake_generate)
    monkeypatch.setattr(assistant_vision.assistant_client, "request_assistant_json", fake_assistant)
    gallery_count = gallery_queries.get_gallery_count()
    job_count = len(list_generate_jobs())

    response = client.post(
        "/api/assistant/image/prompt/optimize",
        data={"prompt": "current prompt", "target_language": "zh-CN"},
        files={"image": ("target.png", PNG_BYTES, "image/png")},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["prompt"]) == 4000
    assert 0 < len(body["comparison_summary"]) <= 2000
    assert len(body["warnings"]) == 10
    assert body["model"] == "assistant-vision-model"
    assert body["duration_ms"] == 37
    assert body["temporary_image"]["b64"] == base64.b64encode(PNG_BYTES).decode("ascii")
    assert body["temporary_image"]["mime_type"] == "image/png"
    assert body["temporary_image"]["width"] == 1
    assert body["temporary_image"]["height"] == 1
    assert body["temporary_image"]["model"] == settings["default_model"]

    generation = calls["generation"]
    payload = generation["payload"]
    assert generation["api_url"] == settings["api_url"]
    assert generation["api_key"] == "default-key"
    assert payload.prompt == "current prompt"
    assert payload.size == "896x896"
    assert payload.n == 1
    assert payload.quality == "low"
    assert payload.output_format == "png"
    assert payload.response_format == settings["default_response_format"]

    assistant = calls["assistant"]
    assert assistant["image"] is None
    assert [image["label"] for image in assistant["images"]] == [
        "Target image (first image)",
        "Trial generated image (second image)",
    ]
    assistant_prompt = json.loads(assistant["user_prompt"])
    assert assistant_prompt["current_prompt"] == "current prompt"
    assert assistant_prompt["target_language"] == "zh-CN"
    assert "Simplified Chinese" in assistant["system_prompt"]
    assert gallery_queries.get_gallery_count() == gallery_count
    assert len(list_generate_jobs()) == job_count


def test_image_prompt_optimization_validates_form_and_active_path(client, monkeypatch):
    settings = client.get("/api/settings").json()
    configured = client.post("/api/settings", json=_assistant_runtime_payload(settings))
    assert configured.status_code == 200

    invalid_language = client.post(
        "/api/assistant/image/prompt/optimize",
        data={"prompt": "current", "target_language": "same"},
        files={"image": ("target.png", PNG_BYTES, "image/png")},
    )
    assert invalid_language.status_code == 422
    empty_prompt = client.post(
        "/api/assistant/image/prompt/optimize",
        data={"prompt": "   "},
        files={"image": ("target.png", PNG_BYTES, "image/png")},
    )
    assert empty_prompt.status_code == 422
    long_prompt = client.post(
        "/api/assistant/image/prompt/optimize",
        data={"prompt": "x" * 4001},
        files={"image": ("target.png", PNG_BYTES, "image/png")},
    )
    assert long_prompt.status_code == 422

    incompatible = client.post(
        "/api/settings",
        json={**_assistant_runtime_payload(configured.json()), "api_path": "/v1/responses"},
    )
    assert incompatible.status_code == 200
    response = client.post(
        "/api/assistant/image/prompt/optimize",
        data={"prompt": "current"},
        files={"image": ("target.png", PNG_BYTES, "image/png")},
    )
    assert response.status_code == 400
    assert "/v1/images/generations" in response.json()["detail"]


def test_image_prompt_optimization_rejects_extreme_ratio_and_maps_upstream_error(client, monkeypatch):
    settings = client.get("/api/settings").json()
    assert client.post("/api/settings", json=_assistant_runtime_payload(settings)).status_code == 200
    wide = io.BytesIO()
    Image.new("RGB", (301, 100), "white").save(wide, format="PNG")

    extreme = client.post(
        "/api/assistant/image/prompt/optimize",
        data={"prompt": "current"},
        files={"image": ("wide.png", wide.getvalue(), "image/png")},
    )
    assert extreme.status_code == 400
    assert "3:1" in extreme.json()["detail"]

    async def failing_generate(*args, **kwargs):
        raise UpstreamApiError("custom size unsupported")

    monkeypatch.setattr(assistant_vision, "call_image_generation_preview_api", failing_generate)
    failed = client.post(
        "/api/assistant/image/prompt/optimize",
        data={"prompt": "current"},
        files={"image": ("target.png", PNG_BYTES, "image/png")},
    )
    assert failed.status_code == 502
    assert "custom size unsupported" in failed.json()["detail"]


def test_image_prompt_optimization_maps_assistant_timeout_without_persisting(client, monkeypatch):
    settings = client.get("/api/settings").json()
    assert client.post("/api/settings", json=_assistant_runtime_payload(settings)).status_code == 200

    async def fake_generate(*args, **kwargs):
        return PNG_BYTES

    async def timed_out_assistant(**kwargs):
        raise assistant_client.AssistantTimeoutError("AI Assistant request timed out")

    monkeypatch.setattr(assistant_vision, "call_image_generation_preview_api", fake_generate)
    monkeypatch.setattr(assistant_vision.assistant_client, "request_assistant_json", timed_out_assistant)
    gallery_count = gallery_queries.get_gallery_count()
    job_count = len(list_generate_jobs())

    response = client.post(
        "/api/assistant/image/prompt/optimize",
        data={"prompt": "current"},
        files={"image": ("target.png", PNG_BYTES, "image/png")},
    )

    assert response.status_code == 504
    assert "timed out" in response.json()["detail"]
    assert gallery_queries.get_gallery_count() == gallery_count
    assert len(list_generate_jobs()) == job_count
