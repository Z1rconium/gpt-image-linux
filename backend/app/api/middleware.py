from urllib.parse import urlsplit
import re
import logging
import uuid

from fastapi import HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware, GZipResponder
from starlette.types import Message, Receive, Scope, Send

from .csp import CONTENT_SECURITY_POLICY
from ..core.redaction import redact_sensitive_text
from ..core import security as auth
from ..core import settings as config


logger = logging.getLogger(__name__)

AUTH_EXEMPT_PATHS = {
    "/",
    "/api/access",
    "/api/access/status",
    "/favicon.ico",
    "/health",
}
AUTH_EXEMPT_PREFIXES = ("/_app/",)
CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
_INVALID_HOST_RE = re.compile(r"[\x00-\x1f\x7f]")
_GZIP_BYPASS_PATHS = {"/api/download-all"}
_GZIP_BYPASS_PREFIXES = ("/api/image/", "/api/thumb/", "/api/download/")
_GZIP_TEXT_CONTENT_TYPES = {
    "application/javascript",
    "application/json",
    "application/manifest+json",
    "application/problem+json",
    "application/xhtml+xml",
    "application/xml",
    "image/svg+xml",
}
_GZIP_BINARY_CONTENT_TYPES = {
    "application/gzip",
    "application/octet-stream",
    "application/pdf",
    "application/zip",
}


def _is_gzip_text_content_type(value: str) -> bool:
    content_type = value.partition(";")[0].strip().lower()
    if not content_type or content_type == "text/event-stream":
        return False
    if content_type in _GZIP_BINARY_CONTENT_TYPES:
        return False
    if content_type == "image/svg+xml":
        return True
    if content_type.startswith(("image/", "audio/", "video/", "font/")):
        return False
    return (
        content_type.startswith("text/")
        or content_type in _GZIP_TEXT_CONTENT_TYPES
        or content_type.endswith("+json")
        or content_type.endswith("+xml")
    )


class _TextGZipResponder(GZipResponder):
    async def send_with_compression(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.initial_message = message
            headers = Headers(raw=message["headers"])
            self.content_encoding_set = "content-encoding" in headers
            self.content_type_is_excluded = not _is_gzip_text_content_type(
                headers.get("content-type", "")
            )
            return
        await super().send_with_compression(message)


class TextOnlyGZipMiddleware(GZipMiddleware):
    """Compress text responses while letting image and download streams pass through."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # pragma: no cover
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in _GZIP_BYPASS_PATHS or path.startswith(_GZIP_BYPASS_PREFIXES):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if "gzip" not in headers.get("Accept-Encoding", ""):
            await self.app(scope, receive, send)
            return

        responder = _TextGZipResponder(
            self.app,
            self.minimum_size,
            compresslevel=self.compresslevel,
        )
        await responder(scope, receive, send)


def apply_security_headers(response: Response) -> Response:
    if "Content-Security-Policy" not in response.headers:
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


def _correlation_id(request: Request) -> str:
    correlation_id = getattr(request.state, "correlation_id", None)
    if not correlation_id:
        correlation_id = uuid.uuid4().hex
        request.state.correlation_id = correlation_id
    return correlation_id


def _safe_error_detail(detail: object) -> str:
    if isinstance(detail, str):
        return redact_sensitive_text(detail)
    return redact_sensitive_text(str(detail or ""))


def error_response(
    request: Request,
    status_code: int,
    error_code: str,
    detail: object | None = None,
) -> JSONResponse:
    correlation_id = _correlation_id(request)
    safe_detail = _safe_error_detail(detail) if detail is not None else None
    content = {
        "status": "error",
        "error_code": error_code,
        "correlation_id": correlation_id,
    }
    if safe_detail:
        content["detail"] = safe_detail
        content["message"] = safe_detail
        content["error"] = safe_detail
    response = JSONResponse(
        status_code=status_code,
        content=content,
    )
    response.headers["X-Correlation-ID"] = correlation_id
    response.headers["Cache-Control"] = "private, no-store"
    return apply_security_headers(response)


def _request_validation_detail(exc: RequestValidationError) -> str:
    messages: list[str] = []
    for error in exc.errors():
        raw_loc = error.get("loc") or ()
        loc_parts = [str(part) for part in raw_loc if part != "body"]
        location = ".".join(loc_parts)
        message = str(error.get("msg") or "Invalid input")
        if message.startswith("Value error, "):
            message = message.removeprefix("Value error, ")
        messages.append(f"{location} - {message}" if location else message)
    return "; ".join(messages) or "Request validation failed"


def _error_code_for_status(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "authentication_required",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        413: "request_too_large",
        422: "validation_error",
        429: "rate_limited",
        502: "upstream_error",
        503: "service_unavailable",
        504: "upstream_timeout",
    }.get(status_code, "internal_error" if status_code >= 500 else "request_rejected")


def _requires_admin_session(request: Request) -> bool:
    if (
        config.ALLOW_UNAUTHENTICATED
        and not config.ACCESS_KEY
        and not auth.configured_admin_key()
    ):
        return False
    path = request.url.path
    if path == "/api/settings/overall-config":
        return True
    if path == "/api/prompt/optimizer-system-prompt":
        return True
    if path in {"/api/prompt/optimizer-health", "/api/assistant/health"}:
        return True
    return path.startswith("/api/settings") and request.method.upper() not in {"GET", "HEAD"}


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
        or request.method.upper() in CSRF_SAFE_METHODS
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
        correlation_id = _correlation_id(request)
        host_allowed, host_detail = request_host_allowed(request)
        if not host_allowed:
            logger.warning(
                "Rejected request: host check failed path=%s client=%s detail=%s",
                request.url.path,
                auth.get_client_ip(request),
                host_detail,
            )
            return error_response(request, 400, "host_not_allowed", host_detail)

        if request.url.path != "/health":
            client_ip = auth.get_client_ip(request)
            if not auth.is_ip_allowed(client_ip):
                logger.warning(
                    "Rejected request: ip not allowed path=%s client=%s",
                    request.url.path,
                    client_ip,
                )
                return error_response(
                    request,
                    403,
                    "ip_not_allowed",
                    "IP address is not allowed",
                )

        if not csrf_origin_allowed(request):
            logger.warning(
                "Rejected request: csrf check failed path=%s client=%s",
                request.url.path,
                auth.get_client_ip(request),
            )
            return error_response(
                request,
                403,
                "csrf_rejected",
                "CSRF origin check failed",
            )

        if (
            config.ACCESS_KEY
            and request.url.path not in AUTH_EXEMPT_PATHS
            and not request.url.path.startswith(AUTH_EXEMPT_PREFIXES)
        ):
            token = request.cookies.get(config.ACCESS_KEY_COOKIE_NAME)
            if not auth.verify_access_token(token):
                logger.warning(
                    "Rejected request: access key required path=%s client=%s",
                    request.url.path,
                    auth.get_client_ip(request),
                )
                return error_response(
                    request,
                    401,
                    "authentication_required",
                    "Access key required",
                )

        if _requires_admin_session(request):
            admin_token = request.cookies.get(config.ADMIN_COOKIE_NAME)
            if not auth.verify_admin_token(admin_token):
                return error_response(
                    request,
                    403,
                    "admin_reauth_required",
                    "Admin re-authentication required",
                )

        response = await call_next(request)

        if request.url.path.startswith("/api/") or response.status_code >= 400:
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Vary"] = "Cookie"
        elif request.url.path == "/":
            response.headers["Cache-Control"] = "no-cache"

        response.headers["X-Correlation-ID"] = correlation_id

        return apply_security_headers(response)


def register_exception_handlers(app):
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        logger.warning(
            "Request rejected path=%s client=%s status=%s detail=%s correlation_id=%s",
            request.url.path,
            auth.get_client_ip(request),
            exc.status_code,
            exc.detail,
            _correlation_id(request),
        )
        return error_response(
            request,
            exc.status_code,
            _error_code_for_status(exc.status_code),
            exc.detail,
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, exc: RequestValidationError):
        logger.warning(
            "Request validation failed path=%s client=%s correlation_id=%s",
            request.url.path,
            auth.get_client_ip(request),
            _correlation_id(request),
        )
        return error_response(
            request,
            422,
            "validation_error",
            _request_validation_detail(exc),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception(
            "Unhandled request error path=%s client=%s",
            request.url.path,
            auth.get_client_ip(request),
        )
        return error_response(request, 500, "internal_error", "Internal Server Error")
