import socket
import sys
from typing import Any

import botocore.awsrequest
from botocore.httpsession import URLLib3Session

from ...core.validators import is_private_ip


class R2UnsafeEndpointError(OSError):
    pass


def resolve_public_socket_addresses(
    hostname: str,
    port: int,
) -> list[tuple[int, int, int, tuple[Any, ...]]]:
    try:
        addresses = socket.getaddrinfo(
            hostname,
            port,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
        )
    except socket.gaierror as e:
        raise R2UnsafeEndpointError("R2 endpoint hostname could not be resolved") from e

    public_addresses: list[tuple[int, int, int, tuple[Any, ...]]] = []
    seen: set[tuple[int, str, int]] = set()
    for family, socktype, protocol, _canonical_name, sockaddr in addresses:
        numeric_ip = str(sockaddr[0])
        if is_private_ip(numeric_ip):
            continue
        key = (family, numeric_ip, int(sockaddr[1]))
        if key in seen:
            continue
        seen.add(key)
        public_addresses.append((family, socktype, protocol, sockaddr))
    if not public_addresses:
        raise R2UnsafeEndpointError(
            "R2 endpoint resolved only to private, reserved, or otherwise non-public addresses"
        )
    return public_addresses


class SafeR2AWSHTTPSConnection(botocore.awsrequest.AWSHTTPSConnection):
    """Resolve and pin each new R2 socket while retaining hostname-based TLS."""

    def _new_conn(self) -> socket.socket:
        addresses = resolve_public_socket_addresses(self.host, int(self.port or 443))
        last_error: OSError | None = None
        for family, socktype, protocol, sockaddr in addresses:
            sock = socket.socket(family, socktype, protocol)
            try:
                sock.settimeout(self.timeout)
                if self.source_address:
                    sock.bind(self.source_address)
                for option in self.socket_options or ():
                    sock.setsockopt(*option)
                # sockaddr already contains the numeric address returned by the
                # guarded lookup, so connect does not perform another DNS query.
                sock.connect(sockaddr)
                sys.audit("http.client.connect", self, self.host, self.port)
                return sock
            except OSError as e:
                last_error = e
                sock.close()
        raise R2UnsafeEndpointError("Failed to connect to a public R2 endpoint address") from last_error


class SafeR2AWSHTTPSConnectionPool(botocore.awsrequest.AWSHTTPSConnectionPool):
    ConnectionCls = SafeR2AWSHTTPSConnection


class SafeR2URLLib3Session(URLLib3Session):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["proxies"] = {}
        super().__init__(*args, **kwargs)
        self._pool_classes_by_scheme = {
            "http": botocore.awsrequest.AWSHTTPConnectionPool,
            "https": SafeR2AWSHTTPSConnectionPool,
        }
        self._manager.pool_classes_by_scheme = self._pool_classes_by_scheme


def install_safe_r2_http_session(client: Any) -> None:
    endpoint = client._endpoint
    previous = endpoint.http_session
    client_config = client.meta.config
    endpoint.http_session = SafeR2URLLib3Session(
        verify=getattr(previous, "_verify", True),
        proxies={},
        timeout=(client_config.connect_timeout, client_config.read_timeout),
        max_pool_connections=client_config.max_pool_connections,
        socket_options=getattr(previous, "_socket_options", None),
    )
    previous.close()
