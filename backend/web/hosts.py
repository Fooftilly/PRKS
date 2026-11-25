"""Host, origin and content-type checks for a request head (#1424).

The Host header must name this server: loopback, an address, the bind
host, or one of ``PRKS_TRUSTED_HOSTS`` (and the public origin's host).
``parse_request_origin`` reads an ``Origin`` header for the CSRF check in
``backend.api.session``.
"""

from __future__ import annotations

import ipaddress
import re
from email.message import Message
from urllib.parse import urlparse


def normalize_listen_host(host: str) -> str:
    host = host.strip()
    if not host:
        raise ValueError("host must not be empty")
    return host


_TRUSTED_HOST_NAME_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)


def _strip_one_trailing_dot(name: str) -> str:
    if name.endswith("."):
        return name[:-1]
    return name


def _normalize_hostname_label(name: str) -> str | None:
    if name != name.strip() or not name:
        return None
    name = _strip_one_trailing_dot(name)
    if not name or name.endswith("."):
        return None
    return name.lower()


def _parse_tcp_port(raw: str) -> int | None:
    if not raw.isdigit():
        return None
    if len(raw) > 1 and raw.startswith("0"):
        return None
    try:
        port = int(raw, 10)
    except ValueError:
        return None
    if port < 0 or port > 65535:
        return None
    return port


def parse_request_host(value: str) -> tuple[str, int | None] | None:
    if value is None or value != value.strip() or not value:
        return None
    if any(ch in value for ch in (",", "/", "@", " ", "\t")):
        return None
    if value.startswith("["):
        close = value.find("]")
        if close < 1:
            return None
        inner = value[1:close]
        rest = value[close + 1 :]
        try:
            ip = ipaddress.ip_address(inner)
        except ValueError:
            return None
        hostname = str(ip)
        if not rest:
            return (hostname, None)
        if not rest.startswith(":") or len(rest) < 2:
            return None
        port = _parse_tcp_port(rest[1:])
        if port is None:
            return None
        return (hostname, port)
    if value.count(":") > 1:
        return None
    if ":" in value:
        hostpart, portpart = value.rsplit(":", 1)
        port = _parse_tcp_port(portpart)
        if port is None:
            return None
    else:
        hostpart = value
        port = None
    try:
        ip = ipaddress.ip_address(hostpart)
        return (str(ip), port)
    except ValueError:
        pass
    hostname = _normalize_hostname_label(hostpart)
    if hostname is None or not _TRUSTED_HOST_NAME_RE.match(hostname):
        return None
    return (hostname, port)


def parse_trusted_hosts(raw: str | None) -> frozenset[str]:
    if not raw:
        return frozenset()
    names = []
    for part in raw.split(","):
        entry = part.strip()
        if not entry:
            continue
        name = _parse_trusted_host_entry(entry)
        if name is None:
            raise ValueError("invalid PRKS_TRUSTED_HOSTS entry")
        names.append(name)
    return frozenset(names)


def _parse_trusted_host_entry(entry: str) -> str | None:
    if any(ch in entry for ch in (":", "/", "@", "*", "?", "#", " ", "\t", "\\")):
        return None
    name = _normalize_hostname_label(entry)
    if name is None or not _TRUSTED_HOST_NAME_RE.match(name):
        return None
    return name


def _is_unspecified_ip(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_unspecified
    except ValueError:
        return False


def _normalized_bind_hostname(bind_host: str) -> str | None:
    if _is_unspecified_ip(bind_host):
        return None
    try:
        return str(ipaddress.ip_address(bind_host))
    except ValueError:
        pass
    return _normalize_hostname_label(bind_host)


def is_trusted_request_host(
    hostname: str, *, bind_host: str, extra_hosts: frozenset[str]
) -> bool:
    if hostname == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(hostname)
        if not ip.is_unspecified:
            return True
    except ValueError:
        pass
    bind_name = _normalized_bind_hostname(bind_host)
    if bind_name is not None and hostname == bind_name:
        return True
    return hostname in extra_hosts


def parse_request_origin(value: str) -> tuple[str, str, int | None] | None:
    if value is None or value != value.strip() or not value:
        return None
    if value == "null":
        return None
    try:
        parsed = urlparse(value)
    except ValueError:  # e.g. an unmatched IPv6 bracket
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    _, _, rest = value.partition("://")
    if any(ch in rest for ch in "/?#"):
        return None
    if parsed.path or parsed.params or parsed.query or parsed.fragment:
        return None
    hostname = parsed.hostname
    if not hostname:
        return None
    try:
        hostname = str(ipaddress.ip_address(hostname))
    except ValueError:
        hostname = _normalize_hostname_label(hostname)
        if hostname is None:
            return None
    try:
        port = parsed.port
    except ValueError:
        return None
    return (parsed.scheme, hostname, port)


def json_content_type_allowed(header_value: str) -> bool:
    msg = Message()
    msg["content-type"] = header_value
    return msg.get_content_type() == "application/json"


def octet_stream_content_type_allowed(header_value: str) -> bool:
    msg = Message()
    msg["content-type"] = header_value
    return msg.get_content_type() in (
        "application/octet-stream",
        "application/zip",
        "application/x-zip-compressed",
    )
