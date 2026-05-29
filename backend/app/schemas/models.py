from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Annotated, Literal, Optional
from datetime import datetime

from ..core.api_paths import DEFAULT_IMAGE_MODEL
from ..core.validators import (
    normalize_secret_env_ref_or_plaintext,
    normalize_r2_endpoint_url,
    normalize_socks5_proxy_url,
    normalize_upstream_base_url,
    normalize_webhook_url,
)

ApiPath = Literal["/v1/images/generations", "/v1/responses", "/v1/chat/completions"]
ApiKeySource = Literal["empty", "stored", "env"]
ResponseFormatDefault = Literal["", "url", "b64_json"]
PresetHealthStatus = Literal["ok", "warning", "error"]
MASKED_SECRET_VALUE = "********"
GenerateJobStatusValue = Literal[
    "queued",
    "running",
    "success",
    "error",
    "cancelled",
    "interrupted",
    "upstream_error",
]
GalleryExportJobStatusValue = Literal["queued", "running", "success", "error"]
GallerySyncJobStatusValue = Literal["queued", "running", "success", "error"]
ShortId = Annotated[str, Field(min_length=1, max_length=128)]


class StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApiPresetResponse(BaseModel):
    id: str
    name: str
    api_url: str
    api_path: ApiPath
    default_model: str
    default_response_format: ResponseFormatDefault = "url"
    api_key_masked: str
    has_api_key: bool
    api_key_source: ApiKeySource = "empty"
    api_key_env_var: Optional[str] = None


class PresetCreateRequest(StrictRequestModel):
    name: Optional[str] = Field(default=None, max_length=160)
    api_url: Optional[str] = Field(default=None, max_length=2048)
    api_key: Optional[str] = Field(
        default=None,
        max_length=8192,
        description=(
            "API key for authentication, or ${ENV_VAR_NAME} to resolve from "
            "the server environment. Literal keys require "
            "ALLOW_PLAINTEXT_SECRETS=true."
        ),
    )
    api_path: Optional[ApiPath] = None
    default_model: Optional[str] = Field(default=None, max_length=200)
    default_response_format: Optional[ResponseFormatDefault] = None
    source_preset_id: Optional[str] = Field(default=None, max_length=128)

    @field_validator("api_url")
    @classmethod
    def validate_optional_api_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_upstream_base_url(value)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_secret_env_ref_or_plaintext(
            value,
            field_name="API key",
        )


class SettingsRequest(StrictRequestModel):
    active_preset_id: Optional[str] = Field(default=None, max_length=128)
    preset_name: Optional[str] = Field(default=None, max_length=160)
    api_url: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="Base API URL, e.g. https://api.example.com",
    )
    api_key: Optional[str] = Field(
        default=None,
        max_length=8192,
        description=(
            "API key for authentication, or ${ENV_VAR_NAME} to resolve from "
            "the server environment. Literal keys require "
            "ALLOW_PLAINTEXT_SECRETS=true. Omit/null to keep the current key."
        ),
    )
    api_path: ApiPath = "/v1/images/generations"
    default_model: Optional[str] = Field(default=None, max_length=200)
    default_response_format: Optional[ResponseFormatDefault] = None
    upstream_socks5_proxy: Optional[str] = Field(
        default=None,
        max_length=2048,
        description=(
            "Optional global SOCKS5 proxy for upstream generation/edit API calls. "
            "Use ${ENV_VAR_NAME} by default; literal values require "
            "ALLOW_PLAINTEXT_SECRETS=true. Null keeps the current value; "
            "an empty string clears it."
        ),
    )
    webhook_url: Optional[str] = Field(
        default=None,
        max_length=2048,
        description=(
            "Optional global HTTPS webhook callback URL for completed generation/edit jobs. "
            "Use ${ENV_VAR_NAME} by default; literal values require "
            "ALLOW_PLAINTEXT_SECRETS=true. Null keeps the current value; "
            "an empty string clears it."
        ),
    )
    prompt_optimizer: Optional["PromptOptimizerSettingsRequest"] = None
    r2_backup: Optional["R2BackupSettingsRequest"] = None

    @field_validator("api_url")
    @classmethod
    def validate_api_url(cls, value: str) -> str:
        return normalize_upstream_base_url(value)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_secret_env_ref_or_plaintext(
            value,
            field_name="API key",
        )

    @field_validator("upstream_socks5_proxy")
    @classmethod
    def validate_upstream_socks5_proxy(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_secret_env_ref_or_plaintext(
            value,
            field_name="SOCKS5 proxy URL",
            normalizer=normalize_socks5_proxy_url,
        )

    @field_validator("webhook_url")
    @classmethod
    def validate_settings_webhook_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_secret_env_ref_or_plaintext(
            value,
            field_name="Webhook URL",
            normalizer=normalize_webhook_url,
        )


class SettingsResponse(BaseModel):
    active_preset_id: str
    api_url: str
    api_key_masked: str
    has_api_key: bool
    api_key_source: ApiKeySource = "empty"
    api_key_env_var: Optional[str] = None
    api_path: ApiPath
    default_model: str
    default_response_format: ResponseFormatDefault = "url"
    has_upstream_socks5_proxy: bool = False
    upstream_socks5_proxy_masked: str = ""
    has_webhook_url: bool = False
    webhook_url_masked: str = ""
    presets: list[ApiPresetResponse]
    prompt_optimizer: "PromptOptimizerSettingsResponse" = Field(default_factory=lambda: PromptOptimizerSettingsResponse())
    r2_backup: "R2BackupSettingsResponse" = Field(default_factory=lambda: R2BackupSettingsResponse())


class PresetHealthCheck(BaseModel):
    name: str
    status: PresetHealthStatus
    message: str


class PresetHealthResponse(BaseModel):
    status: PresetHealthStatus
    checks: list[PresetHealthCheck]


class R2HealthResponse(PresetHealthResponse):
    pass


class AccessRequest(StrictRequestModel):
    access_key: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="Access key for site access",
    )


class AccessStatusResponse(BaseModel):
    authenticated: bool
    expires_at: Optional[str] = None


class VersionResponse(BaseModel):
    version: str
    github_repo: str = ""
    release_url: Optional[str] = None


class LatestVersionResponse(BaseModel):
    latest_version: Optional[str] = None
    has_update: bool = False
    checked_at: Optional[str] = None


def validate_image_size(size: str) -> str:
    if size == "auto":
        return size

    try:
        width_text, height_text = size.lower().split("x", 1)
        width = int(width_text)
        height = int(height_text)
    except (AttributeError, ValueError):
        raise ValueError("size must be 'auto' or formatted as WIDTHxHEIGHT")

    pixels = width * height
    aspect = max(width / height, height / width)

    if width % 16 != 0 or height % 16 != 0:
        raise ValueError("size width and height must be multiples of 16")
    if width <= 0 or height <= 0 or max(width, height) > 3840:
        raise ValueError("size width and height must be positive, with max side <= 3840")
    if aspect > 3:
        raise ValueError("size aspect ratio must not exceed 3:1")
    if pixels < 655360 or pixels > 8294400:
        raise ValueError("size total pixels must be between 655360 and 8294400")

    return f"{width}x{height}"


class PromptOptimizerSettingsResponse(BaseModel):
    enabled: bool = False
    api_url: str = ""
    model: str = "gpt-4o-mini"
    timeout_seconds: int = 60
    api_key_masked: str = "***"
    has_api_key: bool = False
    api_key_source: ApiKeySource = "empty"
    api_key_env_var: Optional[str] = None


class PromptOptimizerSettingsRequest(StrictRequestModel):
    enabled: Optional[bool] = None
    api_url: Optional[str] = Field(default=None, max_length=2048)
    model: Optional[str] = Field(default=None, max_length=200)
    timeout_seconds: Optional[int] = Field(default=None, ge=1)
    api_key: Optional[str] = Field(default=None, max_length=8192)

    @field_validator("api_url")
    @classmethod
    def validate_optional_api_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not value.strip():
            return ""
        return normalize_upstream_base_url(value)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_secret_env_ref_or_plaintext(
            value,
            field_name="Prompt optimizer API key",
        )


class R2BackupSettingsResponse(BaseModel):
    enabled: bool = False
    endpoint_url: str = ""
    bucket_name: str = ""
    region: str = "auto"
    key_prefix: str = "gallery/"
    access_key_id_masked: str = "***"
    has_access_key_id: bool = False
    access_key_id_source: ApiKeySource = "empty"
    access_key_id_env_var: Optional[str] = None
    secret_access_key_masked: str = "***"
    has_secret_access_key: bool = False
    secret_access_key_source: ApiKeySource = "empty"
    secret_access_key_env_var: Optional[str] = None


class R2BackupSettingsRequest(StrictRequestModel):
    enabled: Optional[bool] = None
    endpoint_url: Optional[str] = Field(default=None, max_length=2048)
    bucket_name: Optional[str] = Field(default=None, max_length=255)
    region: Optional[str] = Field(default=None, max_length=100)
    key_prefix: Optional[str] = Field(default=None, max_length=1024)
    access_key_id: Optional[str] = Field(default=None, max_length=8192)
    secret_access_key: Optional[str] = Field(default=None, max_length=8192)

    @field_validator("endpoint_url")
    @classmethod
    def validate_endpoint_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_r2_endpoint_url(value)

    @field_validator("access_key_id")
    @classmethod
    def validate_access_key_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if value.strip() == MASKED_SECRET_VALUE:
            return MASKED_SECRET_VALUE
        return normalize_secret_env_ref_or_plaintext(
            value,
            field_name="R2 access key ID",
        )

    @field_validator("secret_access_key")
    @classmethod
    def validate_secret_access_key(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if value.strip() == MASKED_SECRET_VALUE:
            return MASKED_SECRET_VALUE
        return normalize_secret_env_ref_or_plaintext(
            value,
            field_name="R2 secret access key",
        )


class PromptOptimizerSystemPromptResponse(BaseModel):
    system_prompt: str
    default_system_prompt: str
    customized: bool = False


class PromptOptimizerSystemPromptRequest(StrictRequestModel):
    system_prompt: str = Field(..., min_length=1, max_length=20000)

    @field_validator("system_prompt")
    @classmethod
    def validate_system_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("system_prompt must not be empty")
        return value


class PromptOptimizeRequest(StrictRequestModel):
    prompt: str = Field(..., min_length=1, max_length=4000)
    target_language: Literal["en", "zh-CN", "same"] = "en"
    api_path: Optional[ApiPath] = None
    model: Optional[str] = Field(default=None, max_length=200)
    size: Optional[str] = Field(default=None, max_length=40)
    quality: Optional[Literal["auto", "low", "medium", "high"]] = None


class PromptOptimizeResponse(BaseModel):
    optimized_prompt: str
    model: str
    duration_ms: int


class PromptSnippet(BaseModel):
    id: str
    title: str
    prompt: str
    favorite: bool = False
    created_at: str
    updated_at: str


class PromptSnippetCreateRequest(StrictRequestModel):
    title: str = Field(..., min_length=1, max_length=160)
    prompt: str = Field(..., min_length=1, max_length=4000)
    favorite: bool = False

    @field_validator("title", "prompt")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized


class PromptSnippetUpdateRequest(StrictRequestModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=160)
    prompt: Optional[str] = Field(default=None, min_length=1, max_length=4000)
    favorite: Optional[bool] = None

    @field_validator("title", "prompt")
    @classmethod
    def strip_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized


class PromptSnippetListResponse(BaseModel):
    snippets: list[PromptSnippet]


class GenerateRequest(StrictRequestModel):
    prompt: str = Field(..., min_length=1, max_length=4000)
    size: str = Field(default="auto", max_length=40)
    model: str = Field(default=DEFAULT_IMAGE_MODEL, max_length=200)
    n: int = Field(default=1, ge=1, le=10)
    quality: Literal["auto", "low", "medium", "high"] = "auto"
    output_format: Literal["png", "jpeg", "webp"] = "png"
    output_compression: Optional[int] = Field(default=None, ge=0, le=100)
    response_format: Optional[Literal["url", "b64_json"]] = None
    webhook_url: Optional[str] = Field(default=None, max_length=2048)
    api_path: Optional[ApiPath] = None

    @field_validator("size")
    @classmethod
    def validate_size(cls, value: str) -> str:
        return validate_image_size(value)

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, value: Optional[str]) -> Optional[str]:
        normalized = normalize_webhook_url(value)
        return normalized or None

    @model_validator(mode="after")
    def validate_output_options(self) -> "GenerateRequest":
        if self.output_format == "png":
            self.output_compression = None
        elif self.output_compression is None:
            self.output_compression = 100
        return self


class EditRequest(GenerateRequest):
    pass


class GalleryEntry(BaseModel):
    id: str
    prompt: str
    size: str
    filename: str
    thumbnail_filename: Optional[str] = None
    thumbnail_url: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    model: Optional[str] = None
    quality: Optional[str] = None
    output_format: Optional[str] = None
    output_compression: Optional[int] = None
    response_format: Optional[str] = None
    n: Optional[int] = None
    api_path: Optional[str] = None
    api_preset_name: Optional[str] = None
    duration: Optional[str] = None
    favorite: bool = False
    bytes: Optional[int] = None


class GalleryFavoriteRequest(StrictRequestModel):
    favorite: bool


class GalleryBatchRequest(StrictRequestModel):
    ids: list[ShortId] = Field(..., min_length=1, max_length=1000)

    @field_validator("ids")
    @classmethod
    def validate_ids(cls, value: list[str]) -> list[str]:
        ids = [image_id.strip() for image_id in value if image_id.strip()]
        if not ids:
            raise ValueError("ids must include at least one gallery entry id")
        if len(set(ids)) != len(ids):
            raise ValueError("ids must not contain duplicates")
        return ids


class GalleryBatchFavoriteRequest(GalleryBatchRequest):
    favorite: bool


class GalleryExportRequest(StrictRequestModel):
    ids: Optional[list[ShortId]] = Field(default=None, max_length=1000)

    @field_validator("ids")
    @classmethod
    def validate_ids(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is None:
            return None
        ids = [image_id.strip() for image_id in value if image_id.strip()]
        if not ids:
            raise ValueError("ids must include at least one gallery entry id")
        if len(set(ids)) != len(ids):
            raise ValueError("ids must not contain duplicates")
        return ids


class GalleryExportJobStatus(BaseModel):
    job_id: str
    status: GalleryExportJobStatusValue
    stage: Optional[str] = None
    message: Optional[str] = None
    progress: int = 0
    filename: Optional[str] = None
    download_url: Optional[str] = None
    requested_count: int = 0
    processed_count: int = 0
    exported_count: int = 0
    missing_count: int = 0
    bytes_total: int = 0
    bytes_written: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    error: Optional[str] = None


class GallerySyncJobStatus(BaseModel):
    job_id: str
    status: GallerySyncJobStatusValue
    stage: Optional[str] = None
    message: Optional[str] = None
    progress: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    error: Optional[str] = None
    total_count: int = 0
    compared_count: int = 0
    uploaded_count: int = 0
    skipped_existing_count: int = 0
    missing_local_count: int = 0
    failed_count: int = 0
    bytes_total: int = 0
    bytes_uploaded: int = 0


class GalleryBatchResponse(BaseModel):
    status: str
    count: int
    file_count: int = 0
    requested_count: int = 0
    updated_count: int = 0
    missing_count: int = 0
    missing_ids: list[str] = Field(default_factory=list)


class GalleryFilterOptions(BaseModel):
    models: list[str] = Field(default_factory=list)
    presets: list[str] = Field(default_factory=list)
    sizes: list[str] = Field(default_factory=list)


class GenerateJobResponse(BaseModel):
    job_id: str
    status: GenerateJobStatusValue
    message: Optional[str] = None
    stage: Optional[str] = None
    operation: Optional[Literal["generation", "edit"]] = None


class GenerateJobImage(BaseModel):
    image_id: str
    image_url: str
    filename: str
    image_width: Optional[int] = None
    image_height: Optional[int] = None


class GenerateJobStatus(GenerateJobResponse):
    id: Optional[str] = None
    image_id: Optional[str] = None
    image_url: Optional[str] = None
    images: list[GenerateJobImage] = Field(default_factory=list)
    prompt: Optional[str] = None
    size: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    updated_at: Optional[str] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    model: Optional[str] = None
    quality: Optional[str] = None
    output_format: Optional[str] = None
    output_compression: Optional[int] = None
    response_format: Optional[str] = None
    n: Optional[int] = None
    api_path: Optional[str] = None
    api_preset_name: Optional[str] = None
    duration: Optional[str] = None
    stage_timings: dict[str, float] = Field(default_factory=dict)
    error: Optional[str] = None


class GalleryResponse(BaseModel):
    total: int
    total_bytes: int = 0
    page: int
    page_size: int
    total_pages: int
    has_prev: bool
    has_next: bool
    images: list[GalleryEntry]
    filter_options: GalleryFilterOptions = Field(default_factory=GalleryFilterOptions)


class MessageResponse(BaseModel):
    status: str
    message: str
