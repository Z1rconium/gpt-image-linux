import asyncio
import threading

import pytest

from backend.app.integrations.upstream import generation as upstream_generation
from backend.app.schemas.generation import GenerateRequest


def test_image_generation_preview_ssrf_validation_runs_off_event_loop(monkeypatch):
    event_loop_thread = threading.get_ident()
    seen: dict[str, int | str] = {}

    def fake_validate_upstream_url(url: str, allowlist: str) -> None:
        seen["thread"] = threading.get_ident()
        seen["url"] = url
        raise ValueError("stop before upstream request")

    monkeypatch.setattr(
        upstream_generation.ssrf,
        "validate_upstream_url",
        fake_validate_upstream_url,
    )

    with pytest.raises(ValueError, match="stop before upstream request"):
        asyncio.run(
            upstream_generation.call_image_generation_preview_api(
                "https://api.example.com",
                "test-key",
                GenerateRequest(prompt="threaded ssrf validation"),
            )
        )

    assert seen["url"] == "https://api.example.com/v1/images/generations"
    assert seen["thread"] != event_loop_thread
