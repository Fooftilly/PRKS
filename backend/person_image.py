"""Person portrait URL validation, pinned fetch, and safe transcode.

Remote portrait bytes never leave this module as something callers may serve.
Once DNS resolution returns, no connection/TLS/HTTP/body work happens past the
original total deadline. Synchronous getaddrinfo itself cannot be interrupted.
"""

from __future__ import annotations

import http.client
import ipaddress
import os
import socket
import ssl
import threading
import time
import warnings
from dataclasses import dataclass
from email.message import Message
from io import BytesIO
from urllib.parse import urlsplit

PRKS_PORTRAIT_FETCH_TIMEOUT_SECONDS = 6
PRKS_PORTRAIT_MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
PRKS_PORTRAIT_READ_CHUNK_BYTES = 64 * 1024
PRKS_PORTRAIT_MAX_SOURCE_PIXELS = 25_000_000
PRKS_PORTRAIT_MAX_DIMENSION = 12_000
PRKS_PORTRAIT_MAX_EDGE = 512
PRKS_PORTRAIT_USER_AGENT = "PRKS/1.0 (person portrait cache)"

_ALLOWED_PIL_FORMATS = ("JPEG", "PNG", "WEBP", "GIF")
_ALLOWED_PIL_FORMAT_SET = frozenset(_ALLOWED_PIL_FORMATS)

_REJECT_MEDIA_TYPES = frozenset(
    {
        "text/html",
        "text/plain",
        "text/xml",
        "text/javascript",
        "text/csv",
        "application/json",
        "application/xml",
        "application/javascript",
        "application/xhtml+xml",
        "application/ld+json",
        "image/svg+xml",
    }
)


_getaddrinfo = socket.getaddrinfo


def _create_socket(family, type=socket.SOCK_STREAM, proto=0, fileno=None):
    return socket.socket(family, type, proto, fileno)


class PersonImageUrlError(ValueError):
    """Syntactically invalid or non-global literal person image URL."""


@dataclass(frozen=True)
class PortraitImage:
    body: bytes
    subtype: str


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection that must never open its own TCP connection."""

    def connect(self) -> None:
        raise OSError("portrait fetch socket must be pinned before HTTP")


def normalize_person_image_url(value) -> str:
    """Strip outer whitespace, validate, preserve path/query bytes.

    Empty/whitespace → "". Does not reserialize path or query.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PersonImageUrlError("Invalid image_url")
    trimmed = value.strip()
    if not trimmed:
        return ""
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in trimmed):
        raise PersonImageUrlError("Invalid image_url")
    if any(ch.isspace() for ch in trimmed):
        raise PersonImageUrlError("Invalid image_url")
    scheme_rest = _http_scheme_and_rest(trimmed)
    if scheme_rest is None:
        raise PersonImageUrlError("Invalid image_url")
    _scheme, rest = scheme_rest
    authority = _authority_prefix(rest)
    if "\\" in authority:
        raise PersonImageUrlError("Invalid image_url")
    try:
        parsed = urlsplit(trimmed)
    except ValueError as e:
        raise PersonImageUrlError("Invalid image_url") from e
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise PersonImageUrlError("Invalid image_url")
    if parsed.fragment or "#" in trimmed:
        raise PersonImageUrlError("Invalid image_url")
    if parsed.username is not None or parsed.password is not None:
        raise PersonImageUrlError("Invalid image_url")
    if "@" in (parsed.netloc or ""):
        raise PersonImageUrlError("Invalid image_url")
    host = parsed.hostname
    if not host:
        raise PersonImageUrlError("Invalid image_url")
    if "%" in host:
        raise PersonImageUrlError("Invalid image_url")
    try:
        port = parsed.port
    except ValueError as e:
        raise PersonImageUrlError("Invalid image_url") from e
    if scheme == "http":
        if port not in (None, 80):
            raise PersonImageUrlError("Invalid image_url")
    elif port not in (None, 443):
        raise PersonImageUrlError("Invalid image_url")
    literal = _try_ip_address(host)
    if literal is not None:
        if not _is_public_unicast_address(literal):
            raise PersonImageUrlError("Invalid image_url")
    elif _idna_ascii_host(host) is None:
        raise PersonImageUrlError("Invalid image_url")
    return trimmed


def identify_cached_portrait_subtype(body: bytes) -> str | None:
    """Return 'webp' or 'jpeg' from magic bytes, else None."""
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "webp"
    if len(body) >= 2 and body[:2] == b"\xff\xd8":
        return "jpeg"
    return None


def read_legacy_portrait_bytes(path: str) -> bytes | None:
    """Bounded read of a legacy .bin cache file. Does not decode."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if st.st_size > PRKS_PORTRAIT_MAX_DOWNLOAD_BYTES:
        return None
    try:
        with open(path, "rb") as fp:
            data = fp.read(PRKS_PORTRAIT_MAX_DOWNLOAD_BYTES + 1)
    except OSError:
        return None
    if len(data) > PRKS_PORTRAIT_MAX_DOWNLOAD_BYTES:
        return None
    return data


def decode_and_transcode(
    raw: bytes, content_type: str | None = None, max_edge: int = PRKS_PORTRAIT_MAX_EDGE
) -> tuple[bytes, str] | None:
    """Validate raster bytes and return transcoded (body, subtype) or None."""
    if not raw:
        return None
    if _content_type_rejected(content_type):
        return None
    try:
        from PIL import Image, ImageOps
    except Exception:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(BytesIO(raw), formats=_ALLOWED_PIL_FORMATS)
            fmt = (img.format or "").upper()
            if fmt not in _ALLOWED_PIL_FORMAT_SET:
                return None
            w, h = img.size
            if w <= 0 or h <= 0:
                return None
            if w > PRKS_PORTRAIT_MAX_DIMENSION or h > PRKS_PORTRAIT_MAX_DIMENSION:
                return None
            if w * h > PRKS_PORTRAIT_MAX_SOURCE_PIXELS:
                return None
            if bool(getattr(img, "is_animated", False)):
                return None
            n_frames = int(getattr(img, "n_frames", 1) or 1)
            if n_frames > 1:
                return None
            img.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        return None
    except Exception:
        return None
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    try:
        w, h = img.size
        m = max(w, h)
        if m > max_edge:
            scale = max_edge / float(m)
            nw = max(1, int(w * scale))
            nh = max(1, int(h * scale))
            resample = getattr(Image, "Resampling", Image).LANCZOS
            img = img.resize((nw, nh), resample)
        webp = _encode_webp(img)
        if webp:
            return webp, "webp"
        jpeg = _encode_jpeg(img)
        if jpeg:
            return jpeg, "jpeg"
        return None
    except Exception:
        return None


def fetch_and_prepare(url: str) -> PortraitImage | None:
    """Validate, fetch, and transcode a remote portrait. Never returns raw bytes."""
    try:
        normalized = normalize_person_image_url(url)
    except PersonImageUrlError:
        return None
    if not normalized:
        return None
    fetched = _fetch_remote_response(normalized)
    if fetched is None:
        return None
    raw, content_type = fetched
    encoded = decode_and_transcode(raw, content_type)
    if encoded is None:
        return None
    body, subtype = encoded
    return PortraitImage(body=body, subtype=subtype)


def _http_scheme_and_rest(trimmed: str) -> tuple[str, str] | None:
    lower = trimmed.lower()
    if lower.startswith("https://"):
        return "https", trimmed[8:]
    if lower.startswith("http://"):
        return "http", trimmed[7:]
    return None


def _authority_prefix(rest: str) -> str:
    for i, ch in enumerate(rest):
        if ch in "/?#":
            return rest[:i]
    return rest


def _try_ip_address(host: str):
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _is_public_unicast_address(ip) -> bool:
    """Public Internet unicast only. is_global is necessary but not sufficient."""
    return (
        ip.is_global is True
        and not ip.is_multicast
        and not getattr(ip, "is_site_local", False)
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_unspecified
        and not ip.is_reserved
        and not ip.is_private
    )


def _idna_ascii_host(host: str) -> str | None:
    if _try_ip_address(host) is not None:
        return host
    try:
        return host.encode("idna").decode("ascii")
    except (UnicodeError, LookupError):
        return None


def _content_type_rejected(content_type: str | None) -> bool:
    if not content_type:
        return False
    media = content_type.split(";", 1)[0].strip().lower()
    if not media:
        return False
    if media in _REJECT_MEDIA_TYPES:
        return True
    if media.startswith("text/"):
        return True
    if media.endswith("+json") or media.endswith("+xml"):
        return True
    return False


def _physical_values(msg: Message, name: str) -> list[str]:
    vals = msg.get_all(name)
    if not vals:
        return []
    return list(vals)


def _parse_decimal_length(value: str) -> int | None:
    v = value.strip()
    if not v or not v.isdigit():
        return None
    return int(v)


def _single_token(value: str) -> str | None:
    v = value.strip().lower()
    if not v or "," in v or ";" in v or any(ch.isspace() for ch in v):
        return None
    return v


def _parse_response_framing(msg: Message) -> tuple[int | None, str | None] | None:
    """Fail closed on ambiguous Content-Length/Type/Encoding/Transfer-Encoding."""
    te_vals = _physical_values(msg, "Transfer-Encoding")
    cl_vals = _physical_values(msg, "Content-Length")
    ce_vals = _physical_values(msg, "Content-Encoding")
    ct_vals = _physical_values(msg, "Content-Type")

    if len(ce_vals) > 1:
        return None
    if len(ce_vals) == 1:
        token = _single_token(ce_vals[0])
        if token != "identity":
            return None

    chunked = False
    if len(te_vals) > 1:
        return None
    if len(te_vals) == 1:
        token = _single_token(te_vals[0])
        if token != "chunked":
            return None
        if cl_vals:
            return None
        chunked = True

    content_length = None
    if cl_vals:
        if chunked:
            return None
        if len(cl_vals) != 1:
            return None
        parsed_len = _parse_decimal_length(cl_vals[0])
        if parsed_len is None:
            return None
        if parsed_len > PRKS_PORTRAIT_MAX_DOWNLOAD_BYTES:
            return None
        content_length = parsed_len

    content_type = None
    if len(ct_vals) > 1:
        return None
    if len(ct_vals) == 1:
        content_type = ct_vals[0]

    return content_length, content_type


def _remaining(deadline: float) -> float | None:
    left = deadline - time.monotonic()
    if left <= 0:
        return None
    return left


def _set_sock_timeout(sock, deadline: float) -> bool:
    left = _remaining(deadline)
    if left is None:
        return False
    try:
        sock.settimeout(left)
    except OSError:
        return False
    return True


def _close_socket(sock) -> None:
    if sock is None:
        return
    try:
        sock.close()
    except OSError:
        pass


class _SocketDeadlineWatchdog:
    """Interrupt one portrait socket at an absolute monotonic deadline."""

    def __init__(self, deadline: float):
        self._deadline = deadline
        self._lock = threading.Lock()
        self._sock = None
        self._timer = None
        self._cancelled = False

    def arm(self, sock) -> bool:
        left = _remaining(self._deadline)
        if left is None:
            _interrupt_socket(sock)
            return False
        with self._lock:
            self._cancelled = False
            self._sock = sock
            timer = threading.Timer(left, self._fire)
            timer.daemon = True
            self._timer = timer
        timer.start()
        return True

    def retarget(self, sock) -> None:
        with self._lock:
            self._sock = sock

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            timer = self._timer
            self._timer = None
            self._sock = None
        if timer is not None:
            timer.cancel()

    def _fire(self) -> None:
        with self._lock:
            if self._cancelled:
                return
            sock = self._sock
            self._sock = None
        _interrupt_socket(sock)


def _interrupt_socket(sock) -> None:
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    _close_socket(sock)


def _origin_form_target(parsed) -> str:
    path = parsed.path if parsed.path else "/"
    if parsed.query:
        return f"{path}?{parsed.query}"
    return path


def _host_header_value(host: str, port: int, scheme: str) -> str:
    if ":" in host:
        authority = f"[{host}]"
    else:
        authority = host
    default = 443 if scheme == "https" else 80
    if port == default:
        return authority
    return f"{authority}:{port}"


def _validated_addrinfo_records(host: str, port: int):
    literal = _try_ip_address(host)
    if literal is not None:
        if not _is_public_unicast_address(literal):
            return None
        if isinstance(literal, ipaddress.IPv4Address):
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (str(literal), port),
                )
            ]
        return [
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (str(literal), port, 0, 0),
            )
        ]
    try:
        infos = _getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return None
    records = []
    for fam, typ, proto, canon, sockaddr in infos:
        if fam not in (socket.AF_INET, socket.AF_INET6):
            continue
        if typ != socket.SOCK_STREAM:
            continue
        records.append((fam, typ, proto, canon, sockaddr))
    if not records:
        return None
    for rec in records:
        sockaddr = rec[4]
        addr = sockaddr[0]
        if "%" in str(addr):
            return None
        ip = _try_ip_address(addr)
        if ip is None or not _is_public_unicast_address(ip):
            return None
    return records


def _read_bounded_body(resp, sock, deadline: float) -> bytes | None:
    buf = bytearray()
    while True:
        if not _set_sock_timeout(sock, deadline):
            return None
        remaining_cap = PRKS_PORTRAIT_MAX_DOWNLOAD_BYTES + 1 - len(buf)
        if remaining_cap <= 0:
            return None
        amt = min(PRKS_PORTRAIT_READ_CHUNK_BYTES, remaining_cap)
        try:
            chunk = resp.read(amt)
        except (TimeoutError, OSError, http.client.HTTPException, ValueError):
            return None
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > PRKS_PORTRAIT_MAX_DOWNLOAD_BYTES:
            return None
    return bytes(buf)


def _wrap_tls(sock, server_hostname: str, deadline: float):
    if not _set_sock_timeout(sock, deadline):
        _close_socket(sock)
        return None
    try:
        context = ssl.create_default_context()
        context.set_alpn_protocols(["http/1.1"])
        wrapped = context.wrap_socket(sock, server_hostname=server_hostname)
    except (ssl.SSLError, OSError, ValueError):
        _close_socket(sock)
        return None
    if not _set_sock_timeout(wrapped, deadline):
        _close_socket(wrapped)
        return None
    return wrapped


def _http_exchange(
    sock,
    host_header: str,
    constructor_host: str,
    port: int,
    target: str,
    deadline: float,
) -> tuple[bytes, str | None] | None:
    left = _remaining(deadline)
    if left is None:
        return None
    watchdog = _SocketDeadlineWatchdog(deadline)
    if not watchdog.arm(sock):
        return None
    conn = _PinnedHTTPConnection(constructor_host, port, timeout=left)
    conn.sock = sock
    try:
        if not _set_sock_timeout(sock, deadline):
            return None
        conn.putrequest("GET", target, skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", host_header)
        conn.putheader("User-Agent", PRKS_PORTRAIT_USER_AGENT)
        conn.putheader("Accept", "image/*")
        conn.putheader("Accept-Encoding", "identity")
        conn.putheader("Connection", "close")
        conn.endheaders()
        if not _set_sock_timeout(sock, deadline):
            return None
        resp = conn.getresponse()
        status = int(resp.status)
        if status != 200:
            return None
        framing = _parse_response_framing(resp.msg)
        if framing is None:
            return None
        _content_length, content_type = framing
        body = _read_bounded_body(resp, sock, deadline)
        if body is None:
            return None
        return body, content_type
    except (OSError, TimeoutError, http.client.HTTPException, ValueError):
        return None
    finally:
        watchdog.cancel()
        try:
            conn.close()
        except OSError:
            pass


def _fetch_via_record(
    record,
    scheme: str,
    tls_host: str,
    host_header: str,
    constructor_host: str,
    port: int,
    target: str,
    deadline: float,
) -> tuple[str, tuple[bytes, str | None] | None]:
    """Return ('connect_fail'|'done', payload). 'done' means HTTP ran; do not retry IPs."""
    fam, typ, proto, _canon, sockaddr = record
    if not _remaining(deadline):
        return "connect_fail", None
    sock = _create_socket(fam, typ, proto)
    watchdog = _SocketDeadlineWatchdog(deadline)
    if not watchdog.arm(sock):
        _close_socket(sock)
        return "connect_fail", None
    try:
        if not _set_sock_timeout(sock, deadline):
            return "connect_fail", None
        try:
            sock.connect(sockaddr)
        except OSError:
            return "connect_fail", None
        if not _set_sock_timeout(sock, deadline):
            return "connect_fail", None
        if scheme == "https":
            wrapped = _wrap_tls(sock, tls_host, deadline)
            if wrapped is None:
                return "connect_fail", None
            sock = wrapped
            watchdog.retarget(sock)
        result = _http_exchange(
            sock, host_header, constructor_host, port, target, deadline
        )
        return "done", result
    finally:
        watchdog.cancel()
        _close_socket(sock)


def _fetch_remote_response(url: str) -> tuple[bytes, str | None] | None:
    deadline = time.monotonic() + PRKS_PORTRAIT_FETCH_TIMEOUT_SECONDS
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname
    if not host:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    tls_host = _idna_ascii_host(host)
    if tls_host is None:
        return None
    records = _validated_addrinfo_records(tls_host, port)
    if _remaining(deadline) is None:
        return None
    if not records:
        return None
    target = _origin_form_target(parsed)
    host_header = _host_header_value(tls_host, port, scheme)
    for rec in records:
        if _remaining(deadline) is None:
            return None
        outcome, payload = _fetch_via_record(
            rec, scheme, tls_host, host_header, tls_host, port, target, deadline
        )
        if outcome == "connect_fail":
            continue
        return payload
    return None


def _to_rgb(img):
    from PIL import Image

    if img.mode == "RGB":
        return img
    if img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in getattr(img, "info", {})
    ):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img.convert("RGB")


def _encode_webp(img) -> bytes | None:
    try:
        rgb = _to_rgb(img)
        buf = BytesIO()
        rgb.save(buf, format="WEBP", quality=82, method=4)
        out = buf.getvalue()
        return out or None
    except Exception:
        return None


def _encode_jpeg(img) -> bytes | None:
    try:
        rgb = _to_rgb(img)
        buf = BytesIO()
        rgb.save(buf, format="JPEG", quality=82, optimize=True)
        out = buf.getvalue()
        return out or None
    except Exception:
        return None
