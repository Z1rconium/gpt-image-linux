from typing import Any

from . import settings as config

DEFAULT_API_PATH = "/v1/images/generations"
RESPONSES_API_PATH = "/v1/responses"
CHAT_COMPLETIONS_API_PATH = "/v1/chat/completions"
ALLOWED_API_PATHS = {DEFAULT_API_PATH, RESPONSES_API_PATH, CHAT_COMPLETIONS_API_PATH}
DEFAULT_IMAGE_MODEL = "gpt-image-2"
DEFAULT_RESPONSE_FORMAT = "url"
ALLOWED_RESPONSE_FORMATS = {"", "url", "b64_json"}


def normalize_api_path(api_path: str | None) -> str:
    value = str(api_path or config.DEFAULT_API_PATH or DEFAULT_API_PATH)
    return value if value in ALLOWED_API_PATHS else DEFAULT_API_PATH


def default_model_for_api_path(api_path: str | None) -> str:
    normalized_api_path = normalize_api_path(api_path)
    if normalized_api_path == RESPONSES_API_PATH:
        responses_model = str(config.DEFAULT_RESPONSES_MODEL or "").strip()
        if responses_model:
            return responses_model
    return DEFAULT_IMAGE_MODEL


def normalize_default_model(default_model: str | None, api_path: str | None = None) -> str:
    value = str(default_model or "").strip()
    return value or default_model_for_api_path(api_path)


def normalize_default_response_format(response_format: str | None) -> str:
    if response_format is None:
        return DEFAULT_RESPONSE_FORMAT
    value = str(response_format).strip()
    return value if value in ALLOWED_RESPONSE_FORMATS else DEFAULT_RESPONSE_FORMAT


def build_upstream_url(api_url: str, api_path: str) -> str:
    base_url = str(api_url or "").rstrip("/")
    path = "/" + str(api_path or "").lstrip("/")

    if base_url.endswith(path):
        return base_url
    if base_url.endswith("/v1") and path.startswith("/v1/"):
        return f"{base_url}{path[3:]}"
    return f"{base_url}{path}"


def normalize_api_preset(raw: dict[str, Any] | None, fallback_id: str = "default") -> dict[str, str]:
    preset = raw if isinstance(raw, dict) else {}
    preset_id = str(preset.get("id") or fallback_id)
    api_path = normalize_api_path(str(preset.get("api_path") or config.DEFAULT_API_PATH))
    return {
        "id": preset_id,
        "name": str(preset.get("name") or "Untitled preset").strip() or "Untitled preset",
        "api_url": str(preset.get("api_url") or "").rstrip("/"),
        "api_key": str(preset.get("api_key") or "").strip(),
        "api_path": api_path,
        "default_model": normalize_default_model(preset.get("default_model"), api_path),
        "default_response_format": normalize_default_response_format(
            preset.get("default_response_format")
        ),
    }
