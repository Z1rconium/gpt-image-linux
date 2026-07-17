from collections import OrderedDict
import hmac
import time

from fastapi import APIRouter, HTTPException, Request, Response

from ..app_state import app
from ...core import security as auth
from ...core import settings as config
from ...schemas.access import AccessRequest, AccessStatusResponse


router = APIRouter()
_ACCESS_FAILURES_MAX_SIZE = 10_000


def _prune_expired_access_failures(
    failures: OrderedDict[str, tuple[int, float]],
    now: float,
) -> None:
    expired_ips = [
        ip
        for ip, (_, failed_at) in failures.items()
        if now - failed_at >= config.ACCESS_LOCKOUT_SECONDS
    ]
    for ip in expired_ips:
        failures.pop(ip, None)


def _evict_oldest_access_failures(
    failures: OrderedDict[str, tuple[int, float]],
) -> None:
    while len(failures) > _ACCESS_FAILURES_MAX_SIZE:
        try:
            failures.popitem(last=False)
        except TypeError:
            oldest_ip = min(failures.items(), key=lambda item: item[1][1])[0]
            del failures[oldest_ip]


def _record_access_failure(
    failures: OrderedDict[str, tuple[int, float]],
    client_ip: str,
    now: float,
) -> None:
    previous = failures.pop(client_ip, None)
    count = 1 if previous is None else previous[0] + 1
    failures[client_ip] = (count, now)
    _evict_oldest_access_failures(failures)


@router.get("/api/access/status", response_model=AccessStatusResponse)
async def get_access_status(request: Request):
    if not config.ACCESS_KEY:
        return AccessStatusResponse(authenticated=True)

    expires_at = auth.verify_access_token(
        request.cookies.get(config.ACCESS_KEY_COOKIE_NAME)
    )
    return AccessStatusResponse(
        authenticated=bool(expires_at),
        expires_at=expires_at.isoformat() if expires_at else None,
    )


@router.post("/api/access", response_model=AccessStatusResponse)
async def unlock_access(req: AccessRequest, request: Request, response: Response):
    if not config.ACCESS_KEY:
        return AccessStatusResponse(authenticated=True)

    client_ip = auth.get_client_ip(request)
    failures = app.state.access_failures

    now = time.time()
    if failures:
        _prune_expired_access_failures(failures, now)
    if client_ip in failures:
        count, first_failure_time = failures[client_ip]
        if now - first_failure_time < config.ACCESS_LOCKOUT_SECONDS:
            if count >= config.ACCESS_MAX_FAILURES:
                remaining = int(config.ACCESS_LOCKOUT_SECONDS - (now - first_failure_time))
                raise HTTPException(
                    status_code=429,
                    detail=f"Too many failed attempts. Try again in {remaining} seconds.",
                )
        else:
            failures.pop(client_ip, None)

    if not hmac.compare_digest(req.access_key, config.ACCESS_KEY):
        _record_access_failure(failures, client_ip, now)
        raise HTTPException(status_code=401, detail="Invalid access key")

    if client_ip in failures:
        failures.pop(client_ip, None)

    token, expires_at = auth.create_access_token()
    response.set_cookie(
        key=config.ACCESS_KEY_COOKIE_NAME,
        value=token,
        max_age=config.ACCESS_KEY_SESSION_MINUTES * 60,
        expires=config.ACCESS_KEY_SESSION_MINUTES * 60,
        httponly=True,
        samesite="lax",
        secure=config.ACCESS_COOKIE_SECURE,
    )
    return AccessStatusResponse(
        authenticated=True,
        expires_at=expires_at.isoformat(),
    )
