import base64
import hmac
import ipaddress
import json
import secrets
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Optional

from fastapi import Request

from . import settings as config


def configured_admin_key() -> str:
    # Admin step-up is only ever backed by ADMIN_KEY. Falling back to ACCESS_KEY
    # would let every access-key holder mint an admin session.
    return str(config.ADMIN_KEY or "").strip()


def _signature_secret() -> bytes:
    key = config.ACCESS_KEY
    if not key:
        raise RuntimeError(
            "No signing secret available. Set ACCESS_KEY or DEFAULT_API_KEY."
        )
    return key.encode("utf-8")


def _admin_signature_secret() -> bytes:
    key = configured_admin_key()
    if not key:
        raise RuntimeError("ADMIN_KEY is required for management sessions")
    return key.encode("utf-8")


def _base64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def create_access_token() -> tuple[str, datetime]:
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=config.ACCESS_KEY_SESSION_MINUTES
    )
    payload = {"exp": int(expires_at.timestamp()), "scope": "access"}
    payload_raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_part = _base64url_encode(payload_raw)
    signature = hmac.new(
        _signature_secret(), payload_part.encode("ascii"), sha256
    ).digest()
    return f"{payload_part}.{_base64url_encode(signature)}", expires_at


def verify_access_token(token: Optional[str]) -> Optional[datetime]:
    if not token or "." not in token:
        return None

    payload_part, signature_part = token.split(".", 1)
    expected_signature = hmac.new(
        _signature_secret(), payload_part.encode("ascii"), sha256
    ).digest()

    try:
        actual_signature = _base64url_decode(signature_part)
    except Exception:
        return None

    if not hmac.compare_digest(actual_signature, expected_signature):
        return None

    try:
        payload = json.loads(_base64url_decode(payload_part))
        if payload.get("scope") != "access":
            return None
        expires_at = datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc)
    except Exception:
        return None

    if expires_at <= datetime.now(timezone.utc):
        return None
    return expires_at


def create_admin_token() -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=config.ADMIN_SESSION_MINUTES)
    payload = {
        "exp": int(expires_at.timestamp()),
        "auth_time": int(now.timestamp()),
        "scope": "admin",
        "sid": secrets.token_urlsafe(18),
    }
    payload_part = _base64url_encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(
        _admin_signature_secret(),
        b"admin." + payload_part.encode("ascii"),
        sha256,
    ).digest()
    return f"{payload_part}.{_base64url_encode(signature)}", expires_at


def verify_admin_token(token: Optional[str]) -> Optional[datetime]:
    if (
        not configured_admin_key()
        or not token
        or len(token) > 8192
        or token.count(".") != 1
    ):
        return None
    try:
        payload_part, signature_part = token.split(".", 1)
        expected = hmac.new(
            _admin_signature_secret(),
            b"admin." + payload_part.encode("ascii"),
            sha256,
        ).digest()
        actual = _base64url_decode(signature_part)
        payload = json.loads(_base64url_decode(payload_part))
        if payload.get("scope") != "admin" or not payload.get("sid"):
            return None
        expires_at = datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc)
        auth_time = datetime.fromtimestamp(int(payload["auth_time"]), tz=timezone.utc)
    except Exception:
        return None
    now = datetime.now(timezone.utc)
    if not hmac.compare_digest(actual, expected) or expires_at <= now:
        return None
    if auth_time > now or (now - auth_time) > timedelta(minutes=config.ADMIN_SESSION_MINUTES):
        return None
    return expires_at


def _parse_trusted_proxy_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    raw = config.TRUSTED_PROXY_IPS
    if not raw:
        return []
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for entry in raw.replace(";", ",").split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue
    return networks


_trusted_proxy_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] | None = None


def validate_proxy_config() -> None:
    """Raise if TRUST_PROXY_HEADERS=true but no valid TRUSTED_PROXY_IPS configured."""
    if not config.TRUST_PROXY_HEADERS:
        return
    networks = _parse_trusted_proxy_networks()
    if not networks:
        raise RuntimeError(
            "TRUST_PROXY_HEADERS=true requires a valid TRUSTED_PROXY_IPS setting. "
            "Set TRUSTED_PROXY_IPS to the IP/CIDR of your reverse proxy "
            "(e.g. '172.17.0.0/16' or '10.0.0.1')."
        )


def is_trusted_proxy(client_host: str) -> bool:
    """Return True if proxy headers should be trusted for this direct client."""
    if not config.TRUST_PROXY_HEADERS:
        return False
    global _trusted_proxy_networks
    if _trusted_proxy_networks is None:
        _trusted_proxy_networks = _parse_trusted_proxy_networks()
    if not _trusted_proxy_networks:
        return False
    try:
        addr = ipaddress.ip_address(client_host)
    except ValueError:
        return False
    return any(addr in net for net in _trusted_proxy_networks)


def get_client_ip(request: Request) -> str:
    client_host = request.client.host if request.client else ""
    if is_trusted_proxy(client_host):
        forwarded_for = request.headers.get("x-forwarded-for", "")
        if forwarded_for:
            parts = [p.strip() for p in forwarded_for.split(",") if p.strip()]
            # Walk from right to left; the rightmost non-trusted-proxy entry is the client
            for ip_candidate in reversed(parts):
                if not _is_trusted_proxy_ip(ip_candidate):
                    return ip_candidate
            # All entries are trusted proxies — use leftmost
            if parts:
                return parts[0]

        real_ip = request.headers.get("x-real-ip", "")
        if real_ip:
            return real_ip.strip()

    return client_host


def _is_trusted_proxy_ip(ip_str: str) -> bool:
    global _trusted_proxy_networks
    if _trusted_proxy_networks is None:
        _trusted_proxy_networks = _parse_trusted_proxy_networks()
    if not _trusted_proxy_networks:
        return False
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(addr in net for net in _trusted_proxy_networks)


def _allowlist_entries() -> list[str]:
    return [
        entry.strip()
        for entry in config.IP_ALLOWLIST.replace(";", ",").split(",")
        if entry.strip()
    ]


def is_ip_allowed(ip_text: str) -> bool:
    entries = _allowlist_entries()
    if not entries:
        return True

    try:
        client_ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return False

    for entry in entries:
        try:
            if "/" in entry:
                if client_ip in ipaddress.ip_network(entry, strict=False):
                    return True
            elif client_ip == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue

    return False
