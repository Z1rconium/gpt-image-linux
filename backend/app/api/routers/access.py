import hmac

from fastapi import APIRouter, HTTPException, Request, Response

from ...core import security as auth
from ...core import settings as config
from ...repositories.coordination import (
    check_access_attempt,
    check_admin_attempt,
)
from ...schemas.access import AdminAccessRequest, AccessRequest, AccessStatusResponse
from ...services.blocking import run_db_operation


router = APIRouter()
_ACCESS_FAILURES_MAX_SIZE = 10_000


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
    correct = hmac.compare_digest(req.access_key, config.ACCESS_KEY)
    remaining, _failure_count = await run_db_operation(
        check_access_attempt,
        client_ip,
        correct=correct,
        max_failures=config.ACCESS_MAX_FAILURES,
        lockout_seconds=config.ACCESS_LOCKOUT_SECONDS,
        max_entries=_ACCESS_FAILURES_MAX_SIZE,
        metric_name="check_access_attempt",
    )
    if remaining:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again in {remaining} seconds.",
        )
    if not correct:
        raise HTTPException(status_code=401, detail="Invalid access key")

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


@router.get("/api/access/admin/status", response_model=AccessStatusResponse)
async def get_admin_access_status(request: Request):
    expires_at = auth.verify_admin_token(request.cookies.get(config.ADMIN_COOKIE_NAME))
    return AccessStatusResponse(
        authenticated=bool(expires_at),
        expires_at=expires_at.isoformat() if expires_at else None,
    )


@router.post("/api/access/admin", response_model=AccessStatusResponse)
async def unlock_admin_access(
    req: AdminAccessRequest,
    request: Request,
    response: Response,
):
    client_ip = auth.get_client_ip(request)
    admin_key = auth.configured_admin_key()
    correct = bool(admin_key) and hmac.compare_digest(req.admin_key, admin_key)
    remaining, _failure_count = await run_db_operation(
        check_admin_attempt,
        client_ip,
        correct=correct,
        max_failures=config.ADMIN_MAX_FAILURES,
        lockout_seconds=config.ADMIN_LOCKOUT_SECONDS,
        max_entries=_ACCESS_FAILURES_MAX_SIZE,
        metric_name="check_admin_attempt",
    )
    if remaining:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed admin attempts. Try again in {remaining} seconds.",
        )
    if not correct:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    token, expires_at = auth.create_admin_token()
    response.set_cookie(
        key=config.ADMIN_COOKIE_NAME,
        value=token,
        max_age=config.ADMIN_SESSION_MINUTES * 60,
        expires=config.ADMIN_SESSION_MINUTES * 60,
        httponly=True,
        samesite="strict",
        secure=config.ACCESS_COOKIE_SECURE,
    )
    return AccessStatusResponse(authenticated=True, expires_at=expires_at.isoformat())


@router.delete("/api/access/admin", status_code=204)
async def lock_admin_access(response: Response):
    response.delete_cookie(
        config.ADMIN_COOKIE_NAME,
        httponly=True,
        samesite="strict",
        secure=config.ACCESS_COOKIE_SECURE,
    )
