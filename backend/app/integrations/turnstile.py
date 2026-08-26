"""Cloudflare Turnstile server-side verification client."""

from dataclasses import dataclass

import httpx

from ..core import settings as config


@dataclass(frozen=True)
class TurnstileVerification:
    ok: bool
    error_codes: tuple[str, ...] = ()


def turnstile_active() -> bool:
    return bool(
        config.TURNSTILE_ENABLED and config.TURNSTILE_SITE_KEY and config.TURNSTILE_SECRET_KEY
    )


async def verify_turnstile_token(token: str, client_ip: str | None = None) -> TurnstileVerification:
    """Validate a Turnstile token against the siteverify endpoint.

    Network failures are reported as failed verification so that an outage of
    the verification endpoint never unlocks the panel.
    """
    payload = {"secret": config.TURNSTILE_SECRET_KEY, "response": token}
    if client_ip:
        payload["remoteip"] = client_ip

    try:
        async with httpx.AsyncClient(timeout=config.TURNSTILE_TIMEOUT_SECONDS) as client:
            resp = await client.post(config.TURNSTILE_VERIFY_URL, data=payload)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError, OSError) as exc:
        return TurnstileVerification(ok=False, error_codes=(f"verification_request_failed:{exc}",))

    errors = tuple(str(code) for code in data.get("error-codes", []) if isinstance(code, str))
    return TurnstileVerification(ok=bool(data.get("success")), error_codes=errors)
