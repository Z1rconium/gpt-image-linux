"""
SSE connection limiter: enforces global and per-IP subscriber caps.
"""

import asyncio
from collections import defaultdict

from ..core import settings as config


class SSELimiter:
    """Track active SSE connections and enforce limits."""

    def __init__(self) -> None:
        self._global_count: int = 0
        self._per_ip: defaultdict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    @property
    def global_count(self) -> int:
        return self._global_count

    def per_ip_count(self, ip: str) -> int:
        return self._per_ip.get(ip, 0)

    async def acquire(self, client_ip: str) -> bool:
        async with self._lock:
            if self._global_count >= config.MAX_SSE_SUBSCRIBERS_GLOBAL:
                return False
            if self._per_ip[client_ip] >= config.MAX_SSE_SUBSCRIBERS_PER_IP:
                return False
            self._global_count += 1
            self._per_ip[client_ip] += 1
            return True

    async def release(self, client_ip: str) -> None:
        async with self._lock:
            self._global_count = max(0, self._global_count - 1)
            count = self._per_ip.get(client_ip, 0)
            if count <= 1:
                self._per_ip.pop(client_ip, None)
            else:
                self._per_ip[client_ip] = count - 1


sse_limiter = SSELimiter()
