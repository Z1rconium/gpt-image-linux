import aiohttp
import asyncio
import base64
import json
import logging
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

from ...core import settings as config
from ...core.api_paths import (
    CHAT_COMPLETIONS_API_PATH,
    RESPONSES_API_PATH,
    build_upstream_url,
    normalize_api_path,
)
from ...core.observability import observe_job_stage
from ...core import validators as ssrf
from ...repositories.gallery.mutations import add_to_gallery_async
from ...repositories.image_files import (
    detect_image_format,
    generate_image_id,
    validate_image_bytes,
)
from ...schemas.gallery import GalleryEntry
from ...schemas.generation import EditRequest, GenerateRequest
from ..session_pool import TIMEOUT_PROBE, TIMEOUT_UPSTREAM, get_pool

ProgressCallback = Callable[[str, str], None]
logger = logging.getLogger(__name__)



class ImageEditSource(Protocol):
    temp_path: Path
    filename: str
    content_type: str


class UpstreamApiError(Exception):
    pass


class UpstreamImageDownloadError(UpstreamApiError):
    pass


OUTPUT_FORMATS = {
    "png": {"extension": "png", "media_type": "image/png"},
    "jpeg": {"extension": "jpg", "media_type": "image/jpeg"},
    "webp": {"extension": "webp", "media_type": "image/webp"},
}
DETECTED_FORMAT_EXTENSIONS = {
    "avif": "avif",
    "bmp": "bmp",
    "gif": "gif",
    "heif": "heif",
    "ico": "ico",
    "jpeg": "jpg",
    "png": "png",
    "tiff": "tiff",
    "webp": "webp",
}
DATA_IMAGE_URL_RE = re.compile(
    r"data:image/(?:png|jpe?g|webp|gif|avif|bmp);base64,(?P<data>[A-Za-z0-9+/=\s]+)",
    re.IGNORECASE,
)
MARKDOWN_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\((?P<target><[^>]+>|[^\s)]+)(?:\s+[\"'][^\"']*[\"'])?\)"
)
HTTP_IMAGE_URL_RE = re.compile(r"https?://[^\s<>'\")]+")


DOWNLOAD_CONCURRENCY = 3
MAX_PERSISTABLE_UPSTREAM_ERROR_CHARS = 2000


def _sanitize_upstream_error_text(value: Any) -> str:
    text = str(value or "")[:MAX_PERSISTABLE_UPSTREAM_ERROR_CHARS]
    text = re.sub(
        r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+",
        r"\1[REDACTED]",
        text,
    )
    return re.sub(
        r'''(?i)(["']?(?:api[_-]?key|access[_-]?key|secret|token)["']?\s*[:=]\s*["']?)[^"',\s}]+''',
        r"\1[REDACTED]",
        text,
    )


def validate_upstream_image_data(value: Any, requested_n: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise UpstreamApiError("Upstream image data must be an array")
    bounded = value[: max(1, int(requested_n))]
    if any(not isinstance(item, dict) for item in bounded):
        raise UpstreamApiError("Upstream image data entries must be objects")
    return bounded


def _warn_if_socks5_upstream_resolves_private(
    upstream_url: str,
    socks5_proxy: str | None,
) -> None:
    if not socks5_proxy:
        return

    parsed = urlsplit(upstream_url)
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return

    _resolved_host, resolved_ips = ssrf.resolve_hostname(hostname)
    private_ips = [ip for ip in resolved_ips if ssrf.is_private_ip(ip)]
    if not private_ips:
        return

    logger.warning(
        "SOCKS5 proxy is enabled and upstream host '%s' resolved locally to "
        "private/internal IP(s): %s. Pre-connection SSRF validation still blocks "
        "private local resolutions, but the SOCKS5 proxy is the trust boundary "
        "for remote DNS and upstream network reachability.",
        hostname,
        ", ".join(private_ips),
    )


__all__ = [name for name in globals() if not name.startswith("__")]


