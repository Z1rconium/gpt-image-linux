"""
aiohttp connector that blocks connections to private/internal IPs at DNS resolution time,
preventing DNS rebinding / TOCTOU attacks.
"""

import ipaddress
import socket
from typing import Any

import aiohttp
from aiohttp.resolver import DefaultResolver

from .validators import BLOCKED_HOSTNAMES, PRIVATE_RANGES


class _SSRFGuardResolver(DefaultResolver):
    """Custom resolver that rejects hostnames resolving to private IPs."""

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[dict[str, Any]]:
        if host.lower() in BLOCKED_HOSTNAMES:
            raise ValueError(f"Blocked hostname: {host}")

        results = await super().resolve(host, port, family)
        if not results:
            raise ValueError(f"DNS resolution returned no results for: {host}")

        safe_results = []
        for entry in results:
            ip_str = entry.get("host", "")
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if _is_private(ip):
                continue
            safe_results.append(entry)

        if not safe_results:
            raise ValueError(
                f"All resolved IPs for '{host}' are private/internal — connection blocked"
            )
        return safe_results


def _is_private(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in network for network in PRIVATE_RANGES)


def create_safe_connector(**kwargs: Any) -> aiohttp.TCPConnector:
    """Create a TCPConnector that blocks private/internal IP connections."""
    return aiohttp.TCPConnector(resolver=_SSRFGuardResolver(), **kwargs)
