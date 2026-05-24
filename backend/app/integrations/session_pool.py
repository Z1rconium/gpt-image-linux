import asyncio
import aiohttp
import logging
from typing import Any

from ..core import settings as config

logger = logging.getLogger(__name__)

_UPSTREAM_TIMEOUT = aiohttp.ClientTimeout(
    total=600,
    connect=30,
    sock_connect=30,
    sock_read=600,
)
_PROBE_TIMEOUT = aiohttp.ClientTimeout(
    total=10,
    connect=5,
    sock_connect=5,
    sock_read=10,
)

TIMEOUT_UPSTREAM = "upstream"
TIMEOUT_PROBE = "probe"
TIMEOUT_PROMPT_OPTIMIZER = "prompt_optimizer"

_TIMEOUTS = {
    TIMEOUT_UPSTREAM: _UPSTREAM_TIMEOUT,
    TIMEOUT_PROBE: _PROBE_TIMEOUT,
}


def _prompt_optimizer_timeout() -> aiohttp.ClientTimeout:
    total = max(float(config.PROMPT_OPTIMIZER_TIMEOUT_SECONDS or 20), 0.1)
    connect = min(total, 10.0)
    return aiohttp.ClientTimeout(
        total=total,
        connect=connect,
        sock_connect=connect,
        sock_read=total,
    )


def _timeout_for_kind(timeout_kind: str) -> aiohttp.ClientTimeout:
    if timeout_kind == TIMEOUT_PROMPT_OPTIMIZER:
        return _prompt_optimizer_timeout()
    return _TIMEOUTS.get(timeout_kind, _UPSTREAM_TIMEOUT)


def _build_socks5_connector(socks5_proxy: str | None):
    proxy_url = str(socks5_proxy or "").strip()
    if not proxy_url:
        return None
    try:
        from aiohttp_socks import ProxyConnector
    except ImportError as e:
        raise RuntimeError(
            "SOCKS5 proxy support requires aiohttp-socks. "
            "Install backend requirements and restart the server."
        ) from e
    return ProxyConnector.from_url(proxy_url)


class SessionPool:
    """Reusable aiohttp session pool keyed by (timeout_kind, socks5_proxy)."""

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], aiohttp.ClientSession] = {}
        self._retired_sessions: list[aiohttp.ClientSession] = []
        self._close_tasks: set[asyncio.Task] = set()

    def _schedule_retired_session_closes(self) -> None:
        if not self._retired_sessions:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        retired_sessions = self._retired_sessions
        self._retired_sessions = []
        for session in retired_sessions:
            if session.closed:
                continue
            task = loop.create_task(session.close())
            self._close_tasks.add(task)
            task.add_done_callback(self._close_tasks.discard)

    def get(
        self,
        timeout_kind: str = TIMEOUT_UPSTREAM,
        socks5_proxy: str | None = None,
    ) -> aiohttp.ClientSession:
        self._schedule_retired_session_closes()
        proxy_key = (socks5_proxy or "").strip()
        key = (timeout_kind, proxy_key)
        session = self._sessions.get(key)
        if session is not None and not session.closed:
            return session
        # Close stale sessions for same timeout_kind with different proxy
        stale_keys = [k for k in self._sessions if k[0] == timeout_kind and k != key]
        for stale_key in stale_keys:
            old_session = self._sessions.pop(stale_key, None)
            if old_session and not old_session.closed:
                self._retired_sessions.append(old_session)
        self._schedule_retired_session_closes()
        timeout = _timeout_for_kind(timeout_kind)
        connector = _build_socks5_connector(proxy_key or None)
        session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        self._sessions[key] = session
        return session

    async def close_all(self) -> None:
        sessions = list(self._sessions.values())
        retired_sessions = list(self._retired_sessions)
        close_tasks = list(self._close_tasks)
        self._sessions.clear()
        self._retired_sessions.clear()
        self._close_tasks.clear()
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
        for session in [*sessions, *retired_sessions]:
            if not session.closed:
                await session.close()
        logger.info(
            "Closed %d pooled HTTP session(s)",
            len(sessions) + len(retired_sessions),
        )


_pool: SessionPool | None = None


def get_pool() -> SessionPool:
    global _pool
    if _pool is None:
        _pool = SessionPool()
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close_all()
        _pool = None
