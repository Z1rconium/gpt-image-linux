from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from ..api.edit_limits import MAX_EDIT_SOURCE_IMAGES
from .common import ApiPath, GalleryImportJobStatusValue, StrictRequestModel
from .gallery import GalleryBatchRequest

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
    intent: Optional[str] = Field(default=None, max_length=1200)
    target_language: Literal["en", "zh-CN", "same"] = "en"
    api_path: Optional[ApiPath] = None
    model: Optional[str] = Field(default=None, max_length=200)
    size: Optional[str] = Field(default=None, max_length=40)
    quality: Optional[Literal["auto", "low", "medium", "high"]] = None

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("prompt must not be empty")
        return normalized

    @field_validator("intent")
    @classmethod
    def validate_intent(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized


class PromptOptimizeResponse(BaseModel):
    optimized_prompt: str
    model: str
    duration_ms: int


class AssistantBaseResponse(BaseModel):
    model: str
    duration_ms: int
    warnings: list[str] = Field(default_factory=list)


class AssistantHealthResponse(BaseModel):
    status: Literal["ok", "warning", "error"]
    message: str
    model: str = ""
    duration_ms: int = 0
    status_code: Optional[int] = None


class AssistantPromptRewriteRequest(StrictRequestModel):
    prompt: str = Field(..., min_length=1, max_length=4000)
    instruction: Optional[str] = Field(default=None, max_length=1200)
    target_language: Literal["en", "zh-CN", "same"] = "en"
    api_path: Optional[ApiPath] = None
    model: Optional[str] = Field(default=None, max_length=200)
    size: Optional[str] = Field(default=None, max_length=40)
    quality: Optional[Literal["auto", "low", "medium", "high"]] = None

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("prompt must not be empty")
        return normalized

    @field_validator("instruction")
    @classmethod
    def validate_instruction(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class AssistantPromptRewriteResponse(AssistantBaseResponse):
    rewritten_prompt: str


class AssistantPromptCheckRequest(StrictRequestModel):
    prompt: str = Field(..., min_length=1, max_length=4000)
    api_path: Optional[ApiPath] = None
    model: Optional[str] = Field(default=None, max_length=200)
    size: Optional[str] = Field(default=None, max_length=40)
    quality: Optional[Literal["auto", "low", "medium", "high"]] = None

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("prompt must not be empty")
        return normalized


class AssistantPromptIssue(BaseModel):
    severity: Literal["info", "warning", "error"] = "info"
    message: str
    suggestion: Optional[str] = None


class AssistantPromptCheckResponse(AssistantBaseResponse):
    score: int = Field(ge=0, le=100)
    summary: str
    issues: list[AssistantPromptIssue] = Field(default_factory=list)


class AssistantPromptVariantsRequest(AssistantPromptRewriteRequest):
    count: int = Field(default=3, ge=1, le=6)


class AssistantPromptVariant(BaseModel):
    title: str
    prompt: str
    angle: Optional[str] = None


class AssistantPromptVariantsResponse(AssistantBaseResponse):
    variants: list[AssistantPromptVariant] = Field(default_factory=list)


class AssistantRecommendParamsRequest(StrictRequestModel):
    prompt: str = Field(..., min_length=1, max_length=4000)
    api_path: ApiPath
    current_model: Optional[str] = Field(default=None, max_length=200)
    current_size: Optional[str] = Field(default=None, max_length=40)
    current_quality: Optional[Literal["auto", "low", "medium", "high"]] = None
    current_output_format: Optional[Literal["png", "jpeg", "webp"]] = None
    current_n: Optional[int] = Field(default=None, ge=1, le=10)


class AssistantRecommendParamsResponse(AssistantBaseResponse):
    model_name: Optional[str] = None
    size: Optional[str] = None
    quality: Optional[Literal["auto", "low", "medium", "high"]] = None
    output_format: Optional[Literal["png", "jpeg", "webp"]] = None
    n: Optional[int] = Field(default=None, ge=1, le=10)
    rationale: str = ""


class AssistantJobDiagnoseRequest(StrictRequestModel):
    include_prompt: bool = False


class AssistantJobDiagnoseResponse(AssistantBaseResponse):
    summary: str
    likely_causes: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    safe_job: dict[str, object] = Field(default_factory=dict)


class AssistantEditPlanRequest(StrictRequestModel):
    goal: str = Field(..., min_length=1, max_length=2000)
    source_count: int = Field(default=0, ge=0, le=MAX_EDIT_SOURCE_IMAGES)
    current_prompt: Optional[str] = Field(default=None, max_length=4000)
    target_size: Optional[str] = Field(default=None, max_length=40)

    @field_validator("goal")
    @classmethod
    def validate_goal(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("goal must not be empty")
        return normalized


class AssistantEditPlanResponse(AssistantBaseResponse):
    edit_prompt: str
    source_requirements: list[str] = Field(default_factory=list)
    suggested_size: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    next_action: Literal["confirm", "revise", "add_sources"] = "confirm"


class AssistantImagePromptResponse(AssistantBaseResponse):
    prompt: str


class AssistantTemporaryImage(BaseModel):
    b64: str
    mime_type: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    model: str
    duration_ms: int = Field(ge=0)


class AssistantImagePromptOptimizeResponse(AssistantBaseResponse):
    prompt: str
    comparison_summary: str
    temporary_image: AssistantTemporaryImage


class AssistantGalleryImageResponse(AssistantBaseResponse):
    image_id: str
    description: str = ""
    prompt: str = ""
    analysis: dict[str, object] = Field(default_factory=dict)


class AssistantGalleryMetadataResponse(BaseModel):
    image_id: str
    description: str = ""
    prompt: str = ""
    analysis: dict[str, object] = Field(default_factory=dict)
    model: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AssistantGalleryBatchRequest(GalleryBatchRequest):
    target_language: Literal["en", "zh-CN"] = "en"


class AssistantGalleryBatchJobStatus(BaseModel):
    job_id: str
    status: GalleryImportJobStatusValue
    stage: Optional[str] = None
    message: Optional[str] = None
    progress: int = 0
    requested_count: int = 0
    processed_count: int = 0
    analyzed_count: int = 0
    missing_count: int = 0
    failed_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    error: Optional[str] = None

