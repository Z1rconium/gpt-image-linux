"""
SQLite-backed SSE connection limiter.

The cap is intentionally global across Granian workers: each live connection owns
one short lease row and refreshes it until the HTTP stream exits.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..core import settings as config
from ..core.observability import metrics
from ..repositories.coordination import (
    acquire_sse_slot,
    count_active_sse_slots,
    refresh_sse_slot,
    release_sse_slot,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SSELease:
    client_ip: str
    connection_id: str


class SSELimiter:
    """Track active SSE connections and enforce global/per-IP limits through SQLite."""

    @property
    def lease_seconds(self) -> int:
        return max(15, min(60, int(config.SSE_CONNECTION_TTL_SECONDS)))

    @property
    def refresh_interval_seconds(self) -> float:
        return max(5.0, self.lease_seconds / 2)

    @property
    def global_count(self) -> int:
        return self._count_active_slots()

    def per_ip_count(self, ip: str) -> int:
        return self._count_active_slots(ip)

    async def acquire(self, client_ip: str) -> SSELease | None:
        lease = SSELease(
            client_ip=str(client_ip or "unknown"),
            connection_id=str(uuid.uuid4()),
        )
        try:
            acquired, reason = await asyncio.to_thread(
                acquire_sse_slot,
                client_ip=lease.client_ip,
                connection_id=lease.connection_id,
                lease_expires_at=self._lease_expires_at(),
                max_global=config.MAX_SSE_SUBSCRIBERS_GLOBAL,
                max_per_ip=config.MAX_SSE_SUBSCRIBERS_PER_IP,
            )
        except Exception:
            metrics.increment("sse.acquire.sqlite_errors")
            logger.warning("Failed to acquire SSE slot", exc_info=True)
            return None
        if not acquired:
            metrics.increment(f"sse.rejected.{reason}")
            return None
        metrics.increment("sse.acquired")
        return lease

    async def refresh(self, lease: SSELease) -> bool:
        try:
            refreshed = await asyncio.to_thread(
                refresh_sse_slot,
                connection_id=lease.connection_id,
                lease_expires_at=self._lease_expires_at(),
            )
        except Exception:
            metrics.increment("sse.refresh.sqlite_errors")
            logger.warning("Failed to refresh SSE slot", exc_info=True)
            return False
        if refreshed:
            metrics.increment("sse.refreshed")
        else:
            metrics.increment("sse.refresh_missed")
        return refreshed

    async def refresh_if_needed(self, lease: SSELease, last_refresh_at: float) -> float | None:
        now = time.monotonic()
        if now - last_refresh_at < self.refresh_interval_seconds:
            return last_refresh_at
        if not await self.refresh(lease):
            return None
        return now

    async def release(self, lease: SSELease | str) -> None:
        connection_id = lease.connection_id if isinstance(lease, SSELease) else str(lease)
        try:
            released = await asyncio.to_thread(
                release_sse_slot,
                connection_id,
            )
        except Exception:
            metrics.increment("sse.release.sqlite_errors")
            logger.warning("Failed to release SSE slot", exc_info=True)
            return
        metrics.increment("sse.released" if released else "sse.release_missed")

    def _lease_expires_at(self) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=self.lease_seconds)).isoformat()

    def _count_active_slots(self, client_ip: str | None = None) -> int:
        try:
            return count_active_sse_slots(client_ip)
        except Exception:
            return 0


sse_limiter = SSELimiter()
