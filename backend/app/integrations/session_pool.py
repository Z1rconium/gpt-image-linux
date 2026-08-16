import asyncio
import aiohttp
import logging
import socket
from contextlib import suppress
from typing import Any

from ..core import settings as config
from ..core import validators as ssrf
from ..core.safe_connector import create_safe_connector, create_safe_resolver

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
_NODEIMAGE_TIMEOUT = aiohttp.ClientTimeout(
    total=120,
    connect=10,
    sock_connect=10,
    sock_read=120,
)

TIMEOUT_UPSTREAM = "upstream"
TIMEOUT_PROBE = "probe"
TIMEOUT_PROMPT_OPTIMIZER = "prompt_optimizer"
TIMEOUT_VERSION_CHECK = "version_check"
TIMEOUT_NODEIMAGE = "nodeimage"

_TIMEOUTS = {
    TIMEOUT_UPSTREAM: _UPSTREAM_TIMEOUT,
    TIMEOUT_PROBE: _PROBE_TIMEOUT,
    TIMEOUT_NODEIMAGE: _NODEIMAGE_TIMEOUT,
}


def _prompt_optimizer_timeout() -> aiohttp.ClientTimeout:
    total = max(float(config.PROMPT_OPTIMIZER_TIMEOUT_SECONDS or 60), 0.1)
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
    if timeout_kind == TIMEOUT_VERSION_CHECK:
        total = max(float(config.VERSION_CHECK_TIMEOUT_SECONDS or 3), 0.1)
        return aiohttp.ClientTimeout(total=total, connect=total, sock_connect=total)
    return _TIMEOUTS.get(timeout_kind, _UPSTREAM_TIMEOUT)


def _build_socks5_connector(socks5_proxy: str | None):
    proxy_url = str(socks5_proxy or "").strip()
    if not proxy_url:
        return None
    proxy_url = ssrf.validate_socks5_proxy_url(
        proxy_url,
        config.UPSTREAM_PROXY_HOST_ALLOWLIST,
    )
    try:
        from aiohttp_socks import ProxyConnector
    except ImportError as e:
        raise RuntimeError(
            "SOCKS5 proxy support requires aiohttp-socks. "
            "Install backend requirements and restart the server."
        ) from e

    class SafeSocks5Connector(ProxyConnector):
        """Bind SOCKS CONNECT to a locally resolved public IP."""

        async def _connect_via_proxy(
            self,
            host: str,
            port: int,
            ssl=None,
            timeout: float | None = None,
        ):
            from aiohttp_socks.connector import Proxy, _ResponseHandler

            resolver = create_safe_resolver()
            try:
                addresses = await resolver.resolve(host, port, socket.AF_UNSPEC)
            finally:
                await resolver.close()
            if not addresses:
                raise ValueError(f"DNS resolution returned no public addresses for: {host}")
            destination_ip = str(addresses[0]["host"])

            proxy = Proxy(
                proxy_type=self._proxy_type,
                host=self._proxy_host,
                port=self._proxy_port,
                username=self._proxy_username,
                password=self._proxy_password,
                rdns=False,
                proxy_ssl=self._proxy_ssl,
            )
            stream = await proxy.connect(
                dest_host=destination_ip,
                dest_port=port,
                dest_ssl=None,
                timeout=timeout,
            )
            try:
                if ssl is not None:
                    stream = await stream.start_tls(
                        hostname=host,
                        ssl_context=ssl,
                    )

                transport = stream.writer.transport
                protocol = _ResponseHandler(loop=self._loop, writer=stream.writer)
                transport.set_protocol(protocol)
                protocol.connection_made(transport)
                return transport, protocol
            except BaseException:
                with suppress(Exception):
                    await stream.close()
                raise

    return SafeSocks5Connector.from_url(
        proxy_url,
        rdns=False,
        limit=config.AIOHTTP_CONNECTION_LIMIT,
        limit_per_host=config.AIOHTTP_CONNECTION_LIMIT_PER_HOST,
    )


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
        if connector is None:
            connector = create_safe_connector(
                limit=config.AIOHTTP_CONNECTION_LIMIT,
                limit_per_host=config.AIOHTTP_CONNECTION_LIMIT_PER_HOST,
            )
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
