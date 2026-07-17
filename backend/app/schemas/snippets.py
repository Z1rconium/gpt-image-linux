from typing import Optional

from pydantic import BaseModel, Field, field_validator

from .common import StrictRequestModel

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



