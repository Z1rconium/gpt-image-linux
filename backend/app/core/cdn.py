import hashlib
import hmac
import time
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from . import settings as config


def signed_media_url(base_url: str, filename: str) -> str:
    secret = config.CDN_SIGNING_SECRET.encode("utf-8")
    if len(secret) < 32:
        raise RuntimeError("CDN_SIGNING_SECRET must contain at least 32 bytes")
    parsed = urlsplit(str(base_url or "").strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise RuntimeError("Public CDN media base URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise RuntimeError("Public CDN media base URL must not include credentials, query, or fragment")
    path = f"{parsed.path.rstrip('/')}/{quote(filename, safe='')}"
    expires = int(time.time()) + config.CDN_URL_TTL_SECONDS
    signature = hmac.new(
        secret,
        f"{path}\n{expires}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return urlunsplit(
        (parsed.scheme, parsed.netloc, path, urlencode({"exp": expires, "sig": signature}), "")
    )
