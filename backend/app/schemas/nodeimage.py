from typing import Literal, Optional

from pydantic import BaseModel, Field


class NodeImageUploadResponse(BaseModel):
    url: str
    markdown: str


class NodeImageBatchUploadItem(BaseModel):
    image_id: str
    filename: Optional[str] = None
    status: Literal["ok", "error", "cancelled"]
    url: Optional[str] = None
    markdown: Optional[str] = None
    error: Optional[str] = None


class NodeImageBatchUploadResponse(BaseModel):
    requested_count: int
    uploaded_count: int
    failed_count: int
    results: list[NodeImageBatchUploadItem] = Field(default_factory=list)


class NodeImageUploadJobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "running", "success", "partial_failure", "cancelled", "error"]
    stage: Optional[str] = None
    message: Optional[str] = None
    progress: int = 0
    requested_count: int = 0
    processed_count: int = 0
    uploaded_count: int = 0
    failed_count: int = 0
    cancelled_count: int = 0
    results: list[NodeImageBatchUploadItem] = Field(default_factory=list)
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    updated_at: Optional[str] = None
    error: Optional[str] = None
    status_url: Optional[str] = None
    events_url: Optional[str] = None
    cancel_url: Optional[str] = None


NodeImageBatchUploadCreateResponse = NodeImageUploadJobStatus
