from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator

from ..core.validators import (
    normalize_r2_endpoint_url,
    normalize_secret_env_ref_or_plaintext,
    normalize_socks5_proxy_url,
    normalize_upstream_base_url,
    normalize_webhook_url,
)
from .common import (
    ApiKeySource,
    ApiPath,
    AssistantApiPath,
    MASKED_SECRET_VALUE,
    OverallConfigValueSource,
    OverallConfigValueType,
    PresetHealthStatus,
    ResponseFormatDefault,
    StrictRequestModel,
)

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
    api_key_secret_id: Optional[str] = None


class PresetCreateRequest(StrictRequestModel):
    name: Optional[str] = Field(default=None, max_length=160)
    api_url: Optional[str] = Field(default=None, max_length=2048)
    api_key: Optional[str] = Field(
        default=None,
        max_length=8192,
        description=(
            "Opaque secret_id predeclared in the startup Secret Registry."
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
            "Opaque secret_id predeclared in the startup Secret Registry. "
            "Omit/null to keep the current binding when the origin is unchanged."
        ),
    )
    api_path: ApiPath = "/v1/images/generations"
    default_model: Optional[str] = Field(default=None, max_length=200)
    default_response_format: Optional[ResponseFormatDefault] = None
    upstream_socks5_proxy: Optional[str] = Field(
        default=None,
        max_length=2048,
        description=(
            "Secret Registry ID for an optional global SOCKS5 proxy URL. "
            "Null keeps the current value; "
            "an empty string clears it."
        ),
    )
    webhook_url: Optional[str] = Field(
        default=None,
        max_length=2048,
        description=(
            "Secret Registry ID for an optional HTTPS webhook callback URL. "
            "Null keeps the current value; "
            "an empty string clears it."
        ),
    )
    prompt_optimizer: Optional["PromptOptimizerSettingsRequest"] = None
    ai_assistant: Optional["AIAssistantSettingsRequest"] = None
    r2_backup: Optional["R2BackupSettingsRequest"] = None
    nodeimage: Optional["NodeImageSettingsRequest"] = None

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


class ImageUploadLimitsResponse(BaseModel):
    max_file_size_bytes: int
    max_image_pixels: int


class SettingsResponse(BaseModel):
    active_preset_id: str
    api_url: str
    api_key_masked: str
    has_api_key: bool
    api_key_source: ApiKeySource = "empty"
    api_key_env_var: Optional[str] = None
    api_key_secret_id: Optional[str] = None
    api_path: ApiPath
    default_model: str
    default_response_format: ResponseFormatDefault = "url"
    has_upstream_socks5_proxy: bool = False
    upstream_socks5_proxy_masked: str = ""
    has_webhook_url: bool = False
    webhook_url_masked: str = ""
    presets: list[ApiPresetResponse]
    prompt_optimizer: "PromptOptimizerSettingsResponse" = Field(default_factory=lambda: PromptOptimizerSettingsResponse())
    ai_assistant: "AIAssistantSettingsResponse" = Field(default_factory=lambda: AIAssistantSettingsResponse())
    r2_backup: "R2BackupSettingsResponse" = Field(default_factory=lambda: R2BackupSettingsResponse())
    nodeimage: "NodeImageSettingsResponse" = Field(default_factory=lambda: NodeImageSettingsResponse())
    image_upload_limits: ImageUploadLimitsResponse


class PresetHealthCheck(BaseModel):
    name: str
    status: PresetHealthStatus
    message: str


class PresetHealthResponse(BaseModel):
    status: PresetHealthStatus
    checks: list[PresetHealthCheck]


class CredentialProbeRequest(StrictRequestModel):
    use_credentials: bool = False


class R2HealthResponse(PresetHealthResponse):
    pass


class OverallConfigItem(BaseModel):
    name: str
    type: OverallConfigValueType
    group: str
    description: str
    value: str | bool | int | float
    value_masked: str
    env_value_masked: str
    override_value_masked: Optional[str] = None
    source: OverallConfigValueSource
    is_env_set: bool
    has_override: bool
    secret: bool = False
    hot_reload: bool = True
    restart_required: bool = False
    build_only: bool = False
    startup_only: bool = False
    updated_at: Optional[str] = None
    override_updated_at: Optional[str] = None


class OverallConfigResponse(BaseModel):
    items: list[OverallConfigItem]
    restart_required_names: list[str] = Field(default_factory=list)


class OverallConfigUpdateItem(StrictRequestModel):
    name: str = Field(..., min_length=1, max_length=128)
    value: str | bool | int | float | None = None
    clear_override: bool = False

    @model_validator(mode="after")
    def validate_action(self):
        if self.clear_override and self.value is not None:
            raise ValueError("clear_override cannot be combined with value")
        return self


class OverallConfigUpdateRequest(StrictRequestModel):
    updates: list[OverallConfigUpdateItem] = Field(default_factory=list, max_length=128)


class PromptOptimizerSettingsResponse(BaseModel):
    enabled: bool = False
    api_url: str = ""
    model: str = "gpt-4o-mini"
    timeout_seconds: int = 60
    api_key_masked: str = "***"
    has_api_key: bool = False
    api_key_source: ApiKeySource = "empty"
    api_key_env_var: Optional[str] = None
    api_key_secret_id: Optional[str] = None


class PromptOptimizerHealthResponse(BaseModel):
    status: Literal["ok", "warning", "error"]
    message: str
    model: str = ""
    duration_ms: int = 0
    status_code: Optional[int] = None


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


class AIAssistantSettingsResponse(BaseModel):
    enabled: bool = False
    api_url: str = ""
    model: str = "gpt-4o-mini"
    vision_model: str = "gpt-4o-mini"
    timeout_seconds: int = 60
    api_path: AssistantApiPath = "/v1/chat/completions"
    api_key_masked: str = "***"
    has_api_key: bool = False
    api_key_source: ApiKeySource = "empty"
    api_key_env_var: Optional[str] = None
    api_key_secret_id: Optional[str] = None


class AIAssistantSettingsRequest(StrictRequestModel):
    enabled: Optional[bool] = None
    api_url: Optional[str] = Field(
        default=None,
        max_length=2048,
        description="Deprecated no-op. AI Assistant reuses the Prompt Optimizer API URL.",
    )
    model: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Deprecated no-op. AI Assistant reuses the Prompt Optimizer text model.",
    )
    vision_model: Optional[str] = Field(default=None, max_length=200)
    timeout_seconds: Optional[int] = Field(
        default=None,
        ge=1,
        description="Deprecated no-op. AI Assistant reuses the Prompt Optimizer timeout.",
    )
    api_path: Optional[AssistantApiPath] = Field(
        default=None,
        description="Deprecated no-op. AI Assistant derives the route from the Prompt Optimizer API URL.",
    )
    api_key: Optional[str] = Field(
        default=None,
        max_length=8192,
        description="Deprecated no-op. AI Assistant reuses the Prompt Optimizer API key.",
    )
    use_credentials: bool = False

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
        if value.strip() == MASKED_SECRET_VALUE:
            return MASKED_SECRET_VALUE
        return normalize_secret_env_ref_or_plaintext(
            value,
            field_name="deprecated AI Assistant API key",
        )


class R2BackupSettingsResponse(BaseModel):
    enabled: bool = False
    endpoint_url: str = ""
    bucket_name: str = ""
    region: str = "auto"
    key_prefix: str = "gallery/"
    sync_interval_hours: int = 0
    access_key_id_masked: str = "***"
    has_access_key_id: bool = False
    access_key_id_source: ApiKeySource = "empty"
    access_key_id_env_var: Optional[str] = None
    access_key_id_secret_id: Optional[str] = None
    secret_access_key_masked: str = "***"
    has_secret_access_key: bool = False
    secret_access_key_source: ApiKeySource = "empty"
    secret_access_key_env_var: Optional[str] = None
    secret_access_key_secret_id: Optional[str] = None


class R2BackupSettingsRequest(StrictRequestModel):
    enabled: Optional[bool] = None
    endpoint_url: Optional[str] = Field(default=None, max_length=2048)
    bucket_name: Optional[str] = Field(default=None, max_length=255)
    region: Optional[str] = Field(default=None, max_length=100)
    key_prefix: Optional[str] = Field(default=None, max_length=1024)
    sync_interval_hours: Optional[Annotated[StrictInt, Field(ge=0)]] = None
    access_key_id: Optional[str] = Field(default=None, max_length=8192)
    secret_access_key: Optional[str] = Field(default=None, max_length=8192)
    use_credentials: bool = False

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


class NodeImageSettingsResponse(BaseModel):
    enabled: bool = False
    api_key_masked: str = "***"
    has_api_key: bool = False
    api_key_source: ApiKeySource = "empty"
    api_key_env_var: Optional[str] = None
    api_key_secret_id: Optional[str] = None


class NodeImageSettingsRequest(StrictRequestModel):
    enabled: Optional[bool] = None
    api_key: Optional[str] = Field(default=None, max_length=8192)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if value.strip() == MASKED_SECRET_VALUE:
            return MASKED_SECRET_VALUE
        return normalize_secret_env_ref_or_plaintext(
            value,
            field_name="NodeImage API key",
        )
