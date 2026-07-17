import asyncio
import logging
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..app_state import FRONTEND_BUILD_DIR, app
from ..csp import frontend_index_response
from ...core import settings as config
from ...core.utils import utc_now
from ...schemas.access import LatestVersionResponse, VersionResponse


logger = logging.getLogger(__name__)
router = APIRouter()


_LATEST_VERSION_URL_TEMPLATE = "https://raw.githubusercontent.com/{repo}/{branch}/VERSION"
_LATEST_RELEASE_URL_TEMPLATE = "https://api.github.com/repos/{repo}/releases/latest"
_VERSION_CHECK_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "gpt-image-linux-version-check",
}


def _normalize_version(value: str) -> str:
    return str(value or "").strip().lstrip("vV")


def _compare_versions(a: str, b: str) -> int:
    def parts(text: str) -> list[int]:
        out: list[int] = []
        for piece in _normalize_version(text).split("."):
            try:
                out.append(int(piece))
            except ValueError:
                out.append(0)
        return out

    left = parts(a)
    right = parts(b)
    width = max(len(left), len(right))
    left.extend([0] * (width - len(left)))
    right.extend([0] * (width - len(right)))
    for l, r in zip(left, right):
        if l != r:
            return 1 if l > r else -1
    return 0


async def _fetch_latest_release_version(repo: str) -> str | None:
    url = _LATEST_RELEASE_URL_TEMPLATE.format(repo=repo)
    timeout = aiohttp.ClientTimeout(
        total=config.VERSION_CHECK_TIMEOUT_SECONDS,
        connect=config.VERSION_CHECK_TIMEOUT_SECONDS,
    )
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=_VERSION_CHECK_HEADERS) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return None
                payload: Any = await response.json(content_type=None)
                if not isinstance(payload, dict):
                    return None
                tag_name = str(payload.get("tag_name") or "").strip()
                return _normalize_version(tag_name) or None
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        logger.debug("Latest-release fetch failed: %s", e)
        return None


async def _fetch_branch_version_text(repo: str) -> str | None:
    url = _LATEST_VERSION_URL_TEMPLATE.format(
        repo=repo,
        branch=config.VERSION_CHECK_BRANCH,
    )
    timeout = aiohttp.ClientTimeout(
        total=config.VERSION_CHECK_TIMEOUT_SECONDS,
        connect=config.VERSION_CHECK_TIMEOUT_SECONDS,
    )
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=_VERSION_CHECK_HEADERS) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return None
                text = (await response.text()).strip()
                return _normalize_version(text) or None
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.debug("Latest-version fetch failed: %s", e)
        return None


async def _fetch_latest_version_text(repo: str) -> str | None:
    """Fetch latest version: prefer GitHub Release API, fallback to raw VERSION file."""
    latest = await _fetch_latest_release_version(repo)
    if latest:
        return latest
    return await _fetch_branch_version_text(repo)


def _current_app_version() -> str:
    return config.read_app_version()


@router.get("/favicon.ico")
async def favicon():
    frontend_favicon = get_frontend_build_dir() / "favicon.ico"
    if frontend_favicon.exists():
        return FileResponse(frontend_favicon)
    raise HTTPException(status_code=404, detail="Frontend favicon not found")


def get_frontend_build_dir():
    return getattr(app.state, "frontend_build_dir", FRONTEND_BUILD_DIR)


@router.get("/")
async def index():
    frontend_index = get_frontend_build_dir() / "index.html"
    if frontend_index.exists():
        return frontend_index_response(frontend_index)
    raise HTTPException(
        status_code=500,
        detail="Frontend build not found. Run `npm --prefix frontend run build`.",
    )


@router.get("/health")
async def health():
    return {"status": "ok", "time": utc_now()}


@router.get("/api/version", response_model=VersionResponse)
async def version():
    release_url = (
        f"https://github.com/{config.GITHUB_REPO}/releases/latest"
        if config.GITHUB_REPO
        else None
    )
    return VersionResponse(
        version=_current_app_version(),
        github_repo=config.GITHUB_REPO,
        release_url=release_url,
    )


@router.get("/api/version/latest", response_model=LatestVersionResponse)
async def latest_version():
    if not config.ENABLE_VERSION_CHECK or not config.GITHUB_REPO:
        return LatestVersionResponse(latest_version=None, has_update=False, checked_at=None)

    repo = config.GITHUB_REPO
    current_version = _current_app_version()
    latest = await _fetch_latest_version_text(repo)

    if not latest:
        return LatestVersionResponse(latest_version=None, has_update=False, checked_at=None)

    has_update = _compare_versions(latest, current_version) > 0
    return LatestVersionResponse(
        latest_version=latest,
        has_update=has_update,
        checked_at=utc_now(),
    )


@router.get("/{full_path:path}", include_in_schema=False)
async def frontend_asset_or_spa(full_path: str):
    if full_path.startswith("api/") or full_path == "health":
        raise HTTPException(status_code=404, detail="Not found")

    frontend_build_dir = get_frontend_build_dir()
    if frontend_build_dir.exists():
        requested_path = (frontend_build_dir / full_path).resolve()
        try:
            requested_path.relative_to(frontend_build_dir.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="Not found")

        if requested_path.is_file():
            headers: dict[str, str] | None = None
            if full_path.startswith("_app/immutable/"):
                headers = {"Cache-Control": "public, max-age=31536000, immutable"}
            return FileResponse(requested_path, headers=headers)

        index_path = frontend_build_dir / "index.html"
        if index_path.exists():
            return frontend_index_response(index_path)

    raise HTTPException(status_code=404, detail="Not found")
