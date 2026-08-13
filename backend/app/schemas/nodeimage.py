from typing import Literal, Optional

from pydantic import BaseModel, Field


class NodeImageUploadResponse(BaseModel):
    url: str
    markdown: str


class NodeImageBatchUploadItem(BaseModel):
    image_id: str
    filename: Optional[str] = None
    status: Literal["ok", "error"]
    url: Optional[str] = None
    markdown: Optional[str] = None
    error: Optional[str] = None


class NodeImageBatchUploadResponse(BaseModel):
    requested_count: int
    uploaded_count: int
    failed_count: int
    results: list[NodeImageBatchUploadItem] = Field(default_factory=list)
