from urllib.parse import urlsplit
import re

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from .csp import CONTENT_SECURITY_POLICY
from ..core import security as auth
from ..core import settings as config


AUTH_EXEMPT_PATHS = {
    "/",
    "/api/access",
    "/api/access/status",
    "/favicon.ico",
    "/health",
}
AUTH_EXEMPT_PREFIXES = ("/_app/",)
NO_CACHE_PATHS = {"/"}
NO_CACHE_PREFIXES: tuple[str, ...] = ()
CSRF_PROTECTED_METHODS = {"POST", "PATCH", "DELETE"}
_INVALID_HOST_RE = re.compile(r"[\x00-\x1f\x7f]")


def apply_security_headers(response: Response) -> Response:
    if "Content-Security-Policy" not in response.headers:
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


def normalize_origin(value: str | None) -> str | None:
    if not value:
        return None

    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None

    try:
        port = parts.port
    except ValueError:
        return None

    host = parts.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    if port and not (
        (parts.scheme == "http" and port == 80)
        or (parts.scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"

    return f"{parts.scheme.lower()}://{host}"


def _is_valid_forwarded_host(value: str) -> bool:
    """Reject empty, control-char-containing, or overly long forwarded host values."""
    if not value or len(value) > 255:
        return False
    if _INVALID_HOST_RE.search(value):
        return False
    return True


def normalize_host(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    if not _is_valid_forwarded_host(candidate):
        return None

    try:
        parts = urlsplit(f"//{candidate}")
        port = parts.port
    except ValueError:
        return None

    if (
        not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        return None

    host = parts.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if port is not None:
        host = f"{host}:{port}"
    return host


def _configured_public_origin() -> str | None:
    public_origin = str(getattr(config, "PUBLIC_ORIGIN", "") or "").strip()
    if not public_origin:
        return None
    return normalize_origin(public_origin)


def _has_invalid_public_origin() -> bool:
    return bool(str(getattr(config, "PUBLIC_ORIGIN", "") or "").strip()) and (
        _configured_public_origin() is None
    )


def _allowed_hosts_configured() -> bool:
    return any(
        raw_host.strip()
        for raw_host in str(getattr(config, "ALLOWED_HOSTS", "") or "").split(",")
    )


def _host_has_port(host: str) -> bool:
    try:
        return urlsplit(f"//{host}").port is not None
    except ValueError:
        return False


def _add_allowed_host(allowed: set[str], host: str | None, scheme: str | None = None) -> None:
    if not host:
        return
    allowed.add(host)
    if _host_has_port(host):
        return
    if scheme == "https":
        allowed.add(f"{host}:443")
    elif scheme == "http":
        allowed.add(f"{host}:80")


def _configured_allowed_hosts() -> set[str]:
    allowed: set[str] = set()
    for raw_host in str(getattr(config, "ALLOWED_HOSTS", "") or "").split(","):
        value = raw_host.strip()
        if not value:
            continue
        if "://" in value:
            origin = normalize_origin(value)
            if origin:
                origin_parts = urlsplit(origin)
                normalized = normalize_host(origin_parts.netloc)
                scheme = origin_parts.scheme
            else:
                normalized = None
                scheme = None
        else:
            normalized = normalize_host(value)
            scheme = None
        _add_allowed_host(allowed, normalized, scheme)

    public_origin = _configured_public_origin()
    if public_origin:
        public_parts = urlsplit(public_origin)
        public_host = normalize_host(public_parts.netloc)
        _add_allowed_host(allowed, public_host, public_parts.scheme)
    return allowed


def _host_allowed(host: str) -> bool:
    allowed_hosts = _configured_allowed_hosts()
    if not allowed_hosts and _allowed_hosts_configured():
        return False
    return not allowed_hosts or host in allowed_hosts


def _is_trusted_proxy_request(request: Request) -> bool:
    return bool(
        config.TRUST_PROXY_HEADERS
        and auth.is_trusted_proxy(request.client.host if request.client else "")
    )


def _first_forwarded_host(request: Request) -> str | None:
    forwarded_host = request.headers.get("x-forwarded-host")
    if not forwarded_host:
        return None
    return forwarded_host.split(",", 1)[0].strip()


def request_host_allowed(request: Request) -> tuple[bool, str]:
    if _has_invalid_public_origin():
        return False, "PUBLIC_ORIGIN is invalid"

    host = normalize_host(request.headers.get("host") or request.url.netloc)
    if not host or not _host_allowed(host):
        return False, "Host is not allowed"

    if _is_trusted_proxy_request(request):
        forwarded_host = _first_forwarded_host(request)
        if forwarded_host:
            normalized_forwarded_host = normalize_host(forwarded_host)
            if (
                not normalized_forwarded_host
                or not _host_allowed(normalized_forwarded_host)
            ):
                return False, "Forwarded host is not allowed"

    return True, ""


def get_request_origin(request: Request) -> str | None:
    public_origin = _configured_public_origin()
    if public_origin:
        return public_origin

    scheme = request.url.scheme
    host = normalize_host(request.headers.get("host") or request.url.netloc)
    if not host:
        return None

    if _is_trusted_proxy_request(request):
        forwarded_proto = request.headers.get("x-forwarded-proto")
        if forwarded_proto:
            trusted_scheme = forwarded_proto.split(",", 1)[0].strip().lower()
            if trusted_scheme in {"http", "https"}:
                scheme = trusted_scheme
        forwarded_host = _first_forwarded_host(request)
        if forwarded_host:
            normalized_forwarded_host = normalize_host(forwarded_host)
            if not normalized_forwarded_host:
                return None
            host = normalized_forwarded_host

    return normalize_origin(f"{scheme}://{host}")


def csrf_origin_allowed(request: Request) -> bool:
    if (
        not config.CSRF_ORIGIN_CHECK_ENABLED
        or request.method.upper() not in CSRF_PROTECTED_METHODS
    ):
        return True

    host_allowed, _detail = request_host_allowed(request)
    if not host_allowed:
        return False

    expected_origin = get_request_origin(request)
    if not expected_origin:
        return False

    # Browser fetch metadata reflects the page-visible request target before a
    # local/dev proxy rewrites the upstream Host header.
    sec_fetch_site = request.headers.get("sec-fetch-site", "").strip().lower()
    if sec_fetch_site == "same-origin":
        return True
    if sec_fetch_site == "cross-site":
        return False

    origin = request.headers.get("origin")
    if origin is not None:
        return normalize_origin(origin) == expected_origin

    referer = request.headers.get("referer")
    if referer:
        return normalize_origin(referer) == expected_origin

    # Auth-exempt mutation endpoints still need a browser source signal. If the
    # request has no Origin, Referer, or same-origin fetch metadata, we cannot
    # distinguish it from a cross-site form/request.
    return False



def register_middleware(app):
    @app.middleware("http")
    async def access_control_middleware(request: Request, call_next):
        host_allowed, host_detail = request_host_allowed(request)
        if not host_allowed:
            return apply_security_headers(
                JSONResponse(
                    status_code=400,
                    content={"status": "error", "detail": host_detail},
                )
            )

        if request.url.path != "/health":
            client_ip = auth.get_client_ip(request)
            if not auth.is_ip_allowed(client_ip):
                return apply_security_headers(
                    JSONResponse(
                        status_code=403,
                        content={"status": "error", "detail": "IP address is not allowed"},
                    )
                )

        if not csrf_origin_allowed(request):
            return apply_security_headers(
                JSONResponse(
                    status_code=403,
                    content={"status": "error", "detail": "CSRF origin check failed"},
                )
            )

        if (
            config.ACCESS_KEY
            and request.url.path not in AUTH_EXEMPT_PATHS
            and not request.url.path.startswith(AUTH_EXEMPT_PREFIXES)
        ):
            token = request.cookies.get(config.ACCESS_KEY_COOKIE_NAME)
            if not auth.verify_access_token(token):
                return apply_security_headers(
                    JSONResponse(
                        status_code=401,
                        content={"status": "error", "detail": "Access key required"},
                    )
                )

        response = await call_next(request)

        if request.url.path in NO_CACHE_PATHS or request.url.path.startswith(
            NO_CACHE_PREFIXES
        ):
            response.headers["Cache-Control"] = "no-cache"

        return apply_security_headers(response)


def register_exception_handlers(app):
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": "Internal Server Error"},
        )
