import ipaddress
import socket
from urllib.parse import urlparse, urlsplit, urlunsplit


def _get_private_ip_ranges() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    return (
        ipaddress.IPv4Network("127.0.0.0/8"),  # loopback
        ipaddress.IPv4Network("10.0.0.0/8"),  # private Class A
        ipaddress.IPv4Network("172.16.0.0/12"),  # private Class B
        ipaddress.IPv4Network("192.168.0.0/16"),  # private Class C
        ipaddress.IPv4Network("169.254.0.0/16"),  # link-local
        ipaddress.IPv4Network("0.0.0.0/8"),  # current network
        ipaddress.IPv4Network("224.0.0.0/4"),  # multicast IPv4
        ipaddress.IPv4Network("255.255.255.255/32"),  # broadcast
        ipaddress.IPv6Network("::1/128"),  # loopback
        ipaddress.IPv6Network("fc00::/7"),  # unique local
        ipaddress.IPv6Network("fe80::/10"),  # link-local
        ipaddress.IPv6Network("::ffff:0:0/96"),  # IPv4-mapped
        ipaddress.IPv6Network("ff00::/8"),  # multicast IPv6
    )


PRIVATE_RANGES = _get_private_ip_ranges()

BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.google.com",
    "169.254.169.254",
    "metadata.azure.com",
    "metadata.internal",
    "detectportal.safari.com",
    "captive.apple.com",
}


def is_private_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True

    for network in PRIVATE_RANGES:
        candidate_ip = ip
        if isinstance(network, ipaddress.IPv6Network):
            if isinstance(candidate_ip, ipaddress.IPv6Address) and candidate_ip.ipv4_mapped:
                candidate_ip = ipaddress.IPv4Address(str(candidate_ip.ipv4_mapped))
        if candidate_ip in network:
            return True
    return False


def resolve_hostname(hostname: str) -> tuple[str, list[str]]:
    try:
        family = socket.AF_UNSPEC
        addrs = socket.getaddrinfo(hostname, None, family, socket.SOCK_STREAM)
    except socket.gaierror:
        return hostname, []

    resolved_ips = []
    for addr in addrs:
        ip = addr[4][0]
        resolved_ips.append(ip)
    return hostname, list(dict.fromkeys(resolved_ips))


def response_peer_ip(response: object) -> str | None:
    connection = getattr(response, "connection", None)
    transport = getattr(connection, "transport", None)
    if transport is None:
        protocol = getattr(response, "_protocol", None)
        transport = getattr(protocol, "transport", None)
    if transport is None:
        return None

    peername = transport.get_extra_info("peername")
    if isinstance(peername, tuple) and peername:
        return str(peername[0])
    if isinstance(peername, str):
        return peername
    return None


def validate_response_peer_ip(response: object, context: str) -> None:
    peer_ip = response_peer_ip(response)
    if peer_ip and is_private_ip(peer_ip):
        raise ValueError(f"{context} connected to private/internal IP: {peer_ip}")


def _validate_url_base(
    url: str,
    *,
    allowed_schemes: set[str],
    scheme_error: str,
    missing_hostname_error: str,
    blocked_hostname_error: str,
    private_ip_error: str,
    allowlist: str = "",
    allowlist_error_prefix: str = "Hostname",
    reject_userinfo: bool = False,
    reject_query_fragment: bool = False,
) -> None:
    parsed = urlparse(url)

    if parsed.scheme not in allowed_schemes:
        raise ValueError(scheme_error)

    if not parsed.hostname:
        raise ValueError(missing_hostname_error)

    if reject_userinfo and (parsed.username is not None or parsed.password is not None):
        raise ValueError("URL must not include username or password")

    if reject_query_fragment and (parsed.query or parsed.fragment):
        raise ValueError("URL must not include query strings or fragments")

    hostname = parsed.hostname.lower()

    if hostname in BLOCKED_HOSTNAMES:
        raise ValueError(blocked_hostname_error.format(hostname=hostname))

    if allowlist:
        allowed_hosts = [h.strip().lower() for h in allowlist.split(",") if h.strip()]
        if hostname not in allowed_hosts:
            raise ValueError(
                f"{allowlist_error_prefix} '{hostname}' is not in the allowlist. Allowed: {', '.join(allowed_hosts)}"
            )

    _, resolved_ips = resolve_hostname(hostname)
    for ip in resolved_ips:
        if is_private_ip(ip):
            resolved_info = ", ".join(f"'{resolved_ip}'" for resolved_ip in resolved_ips)
            raise ValueError(private_ip_error.format(hostname=hostname, resolved_info=resolved_info))


def validate_upstream_url(url: str, allowlist: str) -> None:
    _validate_url_base(
        url,
        allowed_schemes={"https"},
        scheme_error="Only HTTPS URLs are allowed for upstream API",
        missing_hostname_error="Invalid URL: no hostname",
        blocked_hostname_error="Hostname '{hostname}' is not allowed",
        private_ip_error="Hostname '{hostname}' resolves to private/internal IP(s): {resolved_info}",
        allowlist=allowlist,
        reject_userinfo=True,
        reject_query_fragment=True,
    )


def normalize_upstream_base_url(url: str | None) -> str:
    value = str(url or "").strip()
    if not value:
        raise ValueError("API URL must not be empty")

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as e:
        raise ValueError("API URL must include a valid port") from e

    if parsed.scheme.lower() != "https":
        raise ValueError("API URL must use https://")
    if not parsed.hostname:
        raise ValueError("API URL must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("API URL must not include username or password")
    if parsed.query or parsed.fragment:
        raise ValueError("API URL must not include query strings or fragments")

    hostname = parsed.hostname.lower()
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    if port is not None:
        host = f"{host}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit(("https", host, path, "", ""))


def redact_url(url: str | None) -> str:
    value = str(url or "").strip()
    if not value:
        return ""

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return "***"

    if not parsed.scheme or not parsed.hostname:
        return "***"

    hostname = parsed.hostname
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    port_part = f":{port}" if port is not None else ""
    userinfo = "***@" if parsed.username is not None or parsed.password is not None else ""
    path = "/***" if parsed.path and parsed.path != "/" else parsed.path
    query = "***" if parsed.query else ""
    fragment = "***" if parsed.fragment else ""
    return urlunsplit((parsed.scheme, f"{userinfo}{host}{port_part}", path, query, fragment))


def validate_image_url(url: str) -> None:
    _validate_url_base(
        url,
        allowed_schemes={"http", "https"},
        scheme_error="Only HTTP/HTTPS URLs are allowed for image URLs",
        missing_hostname_error="Invalid URL: no hostname",
        blocked_hostname_error="Hostname '{hostname}' is not allowed",
        private_ip_error="Image URL hostname '{hostname}' resolves to private/internal IP(s): {resolved_info}",
    )


def validate_webhook_url(url: str, allowlist: str = "") -> None:
    _validate_url_base(
        url,
        allowed_schemes={"https"},
        scheme_error="Only HTTPS URLs are allowed for webhook callbacks",
        missing_hostname_error="Invalid webhook URL: no hostname",
        blocked_hostname_error="Webhook hostname '{hostname}' is not allowed",
        private_ip_error="Webhook hostname '{hostname}' resolves to private/internal IP(s): {resolved_info}",
        allowlist=allowlist,
        allowlist_error_prefix="Webhook hostname",
    )


def normalize_webhook_url(url: str | None) -> str:
    value = str(url or "").strip()
    if not value:
        return ""

    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https":
        raise ValueError("Webhook URL must use https://")
    if not parsed.hostname:
        raise ValueError("Webhook URL must include a hostname")
    return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, parsed.fragment))


def mask_webhook_url(url: str | None) -> str:
    value = str(url or "").strip()
    if not value:
        return ""

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return "***"

    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return "***"

    hostname = parsed.hostname
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    port_part = f":{port}" if port is not None else ""
    user_info = "***@" if parsed.username is not None else ""
    path = "/***" if parsed.path and parsed.path != "/" else parsed.path
    query = "***" if parsed.query else ""
    fragment = "***" if parsed.fragment else ""
    return urlunsplit((parsed.scheme, f"{user_info}{host}{port_part}", path, query, fragment))


def normalize_socks5_proxy_url(url: str | None) -> str:
    value = str(url or "").strip()
    if not value:
        return ""

    parsed = urlsplit(value)
    if parsed.scheme.lower() != "socks5":
        raise ValueError("SOCKS5 proxy URL must use socks5://")
    if not parsed.hostname:
        raise ValueError("SOCKS5 proxy URL must include a hostname")

    try:
        port = parsed.port
    except ValueError as e:
        raise ValueError("SOCKS5 proxy URL must include a valid port") from e
    if port is None:
        raise ValueError("SOCKS5 proxy URL must include a port")

    if parsed.query or parsed.fragment:
        raise ValueError("SOCKS5 proxy URL must not include query strings or fragments")
    if parsed.path not in {"", "/"}:
        raise ValueError("SOCKS5 proxy URL must not include a path")

    return urlunsplit(("socks5", parsed.netloc, "", "", ""))


def mask_socks5_proxy_url(url: str | None) -> str:
    value = str(url or "").strip()
    if not value:
        return ""

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return value

    if parsed.scheme.lower() != "socks5" or not parsed.hostname or port is None:
        return value

    hostname = parsed.hostname
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    user_info = ""
    if parsed.username is not None:
        user_info = parsed.username
        if parsed.password is not None:
            user_info += ":***"
        user_info += "@"

    return f"socks5://{user_info}{host}:{port}"
