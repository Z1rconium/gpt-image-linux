import hmac

from fastapi import APIRouter, HTTPException, Request, Response

from ...core import security as auth
from ...core import settings as config
from ...repositories.coordination import (
    clear_admin_failure,
    clear_access_failure,
    get_admin_lockout,
    get_access_lockout,
    record_admin_failure,
    record_access_failure,
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
    remaining = await run_db_operation(
        get_access_lockout,
        client_ip,
        max_failures=config.ACCESS_MAX_FAILURES,
        lockout_seconds=config.ACCESS_LOCKOUT_SECONDS,
        metric_name="get_access_lockout",
    )
    if remaining:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again in {remaining} seconds.",
        )

    if not hmac.compare_digest(req.access_key, config.ACCESS_KEY):
        await run_db_operation(
            record_access_failure,
            client_ip,
            lockout_seconds=config.ACCESS_LOCKOUT_SECONDS,
            max_entries=_ACCESS_FAILURES_MAX_SIZE,
            metric_name="record_access_failure",
        )
        raise HTTPException(status_code=401, detail="Invalid access key")

    await run_db_operation(
        clear_access_failure,
        client_ip,
        metric_name="clear_access_failure",
    )

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
    remaining = await run_db_operation(
        get_admin_lockout,
        client_ip,
        max_failures=config.ADMIN_MAX_FAILURES,
        lockout_seconds=config.ADMIN_LOCKOUT_SECONDS,
        metric_name="get_admin_lockout",
    )
    if remaining:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed admin attempts. Try again in {remaining} seconds.",
        )

    admin_key = auth.configured_admin_key()
    if not admin_key or not hmac.compare_digest(req.admin_key, admin_key):
        await run_db_operation(
            record_admin_failure,
            client_ip,
            lockout_seconds=config.ADMIN_LOCKOUT_SECONDS,
            max_entries=_ACCESS_FAILURES_MAX_SIZE,
            metric_name="record_admin_failure",
        )
        raise HTTPException(status_code=403, detail="Invalid admin key")
    await run_db_operation(
        clear_admin_failure,
        client_ip,
        metric_name="clear_admin_failure",
    )
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
