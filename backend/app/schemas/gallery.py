from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .common import (
    GalleryExportJobStatusValue,
    GalleryImportJobStatusValue,
    GallerySyncJobStatusValue,
    GalleryThumbnailStatus,
    ShortId,
    StrictRequestModel,
)

class GalleryEntry(BaseModel):
    id: str
    prompt: str
    size: str
    filename: str
    image_url: Optional[str] = None
    thumbnail_filename: Optional[str] = None
    thumbnail_url: Optional[str] = None
    thumbnail_status: GalleryThumbnailStatus = "missing"
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


class GalleryThumbnailStatusRequest(StrictRequestModel):
    ids: list[ShortId] = Field(min_length=1, max_length=100)

    @field_validator("ids")
    @classmethod
    def validate_ids(cls, value: list[str]) -> list[str]:
        ids = [image_id.strip() for image_id in value if image_id.strip()]
        if len(set(ids)) != len(ids):
            raise ValueError("ids must not contain duplicates")
        return ids


class GalleryThumbnailState(BaseModel):
    id: str
    thumbnail_filename: Optional[str] = None
    thumbnail_url: Optional[str] = None
    thumbnail_status: GalleryThumbnailStatus = "missing"


class GallerySelectionFilterRequest(StrictRequestModel):
    prompt: Optional[str] = Field(default="", max_length=4000)
    model: Optional[str] = Field(default="", max_length=200)
    preset: Optional[str] = Field(default="", max_length=200)
    size: Optional[str] = Field(default="", max_length=64)
    date_from: Optional[str] = Field(default="", max_length=64)
    date_to: Optional[str] = Field(default="", max_length=64)
    favorite: Optional[bool] = None


class GallerySearchRequest(GallerySelectionFilterRequest):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=9, ge=1, le=100)
    include_total_bytes: bool = False
    include_counts: bool = True
    include_filter_options: bool = True
    cursor: Optional[str] = Field(default=None, max_length=512)
    direction: str = Field(default="next", max_length=8)


class GallerySelectionTokenRequest(StrictRequestModel):
    filters: GallerySelectionFilterRequest = Field(default_factory=GallerySelectionFilterRequest)


class GallerySelectionTokenResponse(BaseModel):
    selection_token: str
    count: int
    expires_at: str


class GalleryBatchRequest(StrictRequestModel):
    ids: Optional[list[ShortId]] = Field(default=None, max_length=1000)
    selection_token: Optional[ShortId] = None

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

    @field_validator("selection_token")
    @classmethod
    def validate_selection_token(cls, value: Optional[str]) -> Optional[str]:
        token = str(value or "").strip()
        return token or None

    @model_validator(mode="after")
    def validate_batch_target(self):
        if bool(self.ids) == bool(self.selection_token):
            raise ValueError("Provide exactly one of ids or selection_token")
        return self


class GalleryBatchFavoriteRequest(GalleryBatchRequest):
    favorite: bool


class GalleryExportRequest(StrictRequestModel):
    ids: Optional[list[ShortId]] = Field(default=None, max_length=1000)
    selection_token: Optional[ShortId] = None

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

    @field_validator("selection_token")
    @classmethod
    def validate_selection_token(cls, value: Optional[str]) -> Optional[str]:
        token = str(value or "").strip()
        return token or None

    @model_validator(mode="after")
    def validate_export_target(self):
        if self.ids and self.selection_token:
            raise ValueError("Provide ids or selection_token, not both")
        return self


class GallerySyncRequest(StrictRequestModel):
    full_reconcile: bool = False
    dry_run: bool = False


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
    pending_upload_count: int = 0
    skipped_existing_count: int = 0
    missing_local_count: int = 0
    failed_count: int = 0
    bytes_total: int = 0
    bytes_uploaded: int = 0
    dry_run: bool = False
    checkpoint_filename: Optional[str] = None


class GalleryImportJobStatus(BaseModel):
    job_id: str
    status: GalleryImportJobStatusValue
    stage: Optional[str] = None
    message: Optional[str] = None
    progress: int = 0
    requested_count: int = 0
    processed_count: int = 0
    imported_count: int = 0
    skipped_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    error: Optional[str] = None


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


class GalleryResponse(BaseModel):
    total: int
    total_bytes: int = 0
    page: int
    page_size: int
    total_pages: int
    has_prev: bool
    has_next: bool
    next_cursor: Optional[str] = None
    prev_cursor: Optional[str] = None
    images: list[GalleryEntry]
    filter_options: GalleryFilterOptions = Field(default_factory=GalleryFilterOptions)

