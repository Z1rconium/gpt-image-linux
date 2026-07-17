from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

ApiPath = Literal["/v1/images/generations", "/v1/responses", "/v1/chat/completions"]
AssistantApiPath = Literal["/v1/chat/completions", "/v1/responses"]
ApiKeySource = Literal["empty", "stored", "env"]
OverallConfigValueType = Literal["string", "secret", "bool", "int", "float"]
OverallConfigValueSource = Literal["override", "env", "default"]
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
GalleryImportJobStatusValue = Literal["queued", "running", "success", "error"]
GalleryThumbnailStatus = Literal["ready", "queued", "missing"]
ShortId = Annotated[str, Field(min_length=1, max_length=128)]


class StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MessageResponse(BaseModel):
    status: str
    message: str

