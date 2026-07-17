from typing import Optional

from pydantic import BaseModel, Field

from .common import StrictRequestModel

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



