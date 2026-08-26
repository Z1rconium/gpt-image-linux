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
    turnstile_token: str = Field(
        default="",
        min_length=0,
        max_length=4096,
        description="Cloudflare Turnstile token when human verification is enabled",
    )


class AccessStatusResponse(BaseModel):
    authenticated: bool
    expires_at: Optional[str] = None
    turnstile_enabled: bool = False
    turnstile_site_key: Optional[str] = None


class VersionResponse(BaseModel):
    version: str
    github_repo: str = ""
    release_url: Optional[str] = None


class LatestVersionResponse(BaseModel):
    latest_version: Optional[str] = None
    has_update: bool = False
    checked_at: Optional[str] = None

