import io
import ipaddress
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
import warnings
import zlib
from email.message import Message
from io import BytesIO
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend import person_image  # noqa: E402
from backend.person_image import (  # noqa: E402
    PersonImageUrlError,
    decode_and_transcode,
    fetch_and_prepare,
    identify_cached_portrait_subtype,
    normalize_person_image_url,
    read_legacy_portrait_bytes,
)


def _png_bytes(width=16, height=16, color=(10, 20, 30)):
    from PIL import Image

    img = Image.new("RGB", (width, height), color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(width=16, height=16):
    from PIL import Image

    img = Image.new("RGB", (width, height), (40, 50, 60))
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _webp_bytes(width=16, height=16):
    from PIL import Image

    img = Image.new("RGB", (width, height), (70, 80, 90))
    buf = BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


def _gif_bytes(width=8, height=8, frames=1):
    from PIL import Image

    imgs = [
        Image.new("RGB", (width, height), (i * 40, 20, 80)) for i in range(max(1, frames))
    ]
    buf = BytesIO()
    first, rest = imgs[0], imgs[1:]
    if rest:
        first.save(
            buf,
            format="GIF",
            save_all=True,
            append_images=rest,
            duration=50,
            loop=0,
        )
    else:
        first.save(buf, format="GIF")
    return buf.getvalue()


def _png_ihdr_only(width: int, height: int) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


def _http_message(**headers) -> Message:
    msg = Message()
    for name, values in headers.items():
        if isinstance(values, str):
            values = [values]
        for v in values:
            msg.add_header(name, v)
    return msg


def _http_bytes(status=200, reason="OK", headers=None, body=b""):
    lines = [f"HTTP/1.1 {status} {reason}"]
    for name, value in headers or []:
        lines.append(f"{name}: {value}")
    head = "\r\n".join(lines).encode("ascii") + b"\r\n\r\n"
    return head + body


def _public_addrinfo(ip="8.8.8.8", port=443, family=socket.AF_INET):
    if family == socket.AF_INET:
        sockaddr = (ip, port)
    else:
        sockaddr = (ip, port, 0, 0)
    return [
        (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)
    ]


class FakeSocket:
    def __init__(self, response: bytes, connect_error=None):
        self._response = response
        self.connected_to = None
        self.sent = bytearray()
        self.timeouts = []
        self.closed = False
        self.connect_error = connect_error
        self.family = None
        self.type = None
        self.proto = None

    def settimeout(self, t):
        self.timeouts.append(t)

    def connect(self, addr):
        if self.connect_error:
            raise self.connect_error
        self.connected_to = addr

    def sendall(self, data):
        self.sent.extend(data)

    def send(self, data):
        self.sent.extend(data)
        return len(data)

    def makefile(self, mode="rb", buffering=None, **kwargs):
        return io.BytesIO(self._response)

    def close(self):
        self.closed = True

    def setsockopt(self, *args, **kwargs):
        pass

    def shutdown(self, how):
        pass


class FakeSSLContext:
    def __init__(self):
        self.alpn = None
        self.wraps = []

    def set_alpn_protocols(self, protocols):
        self.alpn = list(protocols)

    def wrap_socket(self, sock, server_hostname=None, **kwargs):
        self.wraps.append({"sock": sock, "server_hostname": server_hostname})
        sock.server_hostname = server_hostname
        return sock


def _install_network(created, response=b"", addrinfo=None, ssl_ctx=None, connect_error=None):
    def fake_socket(family, type=socket.SOCK_STREAM, proto=0, fileno=None):
        sock = FakeSocket(response, connect_error=connect_error)
        sock.family = family
        sock.type = type
        sock.proto = proto
        created.append(sock)
        return sock

    patches = [
        patch("backend.person_image._create_socket", fake_socket),
    ]
    if addrinfo is not None:
        patches.append(
            patch("backend.person_image._getaddrinfo", return_value=addrinfo)
        )
    if ssl_ctx is not None:
        patches.append(
            patch("backend.person_image.ssl.create_default_context", return_value=ssl_ctx)
        )
    return patches


class TestNormalizePersonImageUrl(unittest.TestCase):
    def test_empty_and_whitespace(self):
        self.assertEqual(normalize_person_image_url(""), "")
        self.assertEqual(normalize_person_image_url("   "), "")
        self.assertEqual(normalize_person_image_url(None), "")

    def test_allowed_urls_preserved(self):
        allowed = [
            "https://example.com/a.jpg",
            "http://example.com/a.jpg",
            "https://example.com/a.jpg?x=1",
            "https://example.com:443/a.jpg",
            "http://example.com:80/a.jpg",
            "https://8.8.8.8/a.jpg",
            "https://[2001:4860:4860::8888]/x.jpg",
        ]
        for url in allowed:
            self.assertEqual(normalize_person_image_url(url), url)

    def test_signed_query_not_reserialized(self):
        url = (
            "https://cdn.example.com/p.jpg?X-Amz-Signature=abc%2Bdef&B=1&A=2&x-id=GetObject"
        )
        self.assertEqual(normalize_person_image_url("  " + url + "  "), url)

    def test_rejected_schemes_and_syntax(self):
        rejected = [
            "file:///etc/passwd",
            "ftp://example.com/a.jpg",
            "data:image/png;base64,aaaa",
            "gopher://example.com/a",
            "https://user:pass@example.com/a.jpg",
            "https://user@example.com/a.jpg",
            "https://example.com:444/a.jpg",
            "http://example.com:8080/a.jpg",
            "https:///a.jpg",
            "https://example.com/a.jpg#fragment",
            "https://example.com/a.jpg#",
            "https://example.com:abc/a.jpg",
            "not-a-url",
        ]
        for url in rejected:
            with self.subTest(url=url):
                with self.assertRaises(PersonImageUrlError):
                    normalize_person_image_url(url)

    def test_control_chars_rejected_before_parse(self):
        with self.assertRaises(PersonImageUrlError):
            normalize_person_image_url("https://example.com/a\x00.jpg")
        with self.assertRaises(PersonImageUrlError):
            normalize_person_image_url("https://example.com/a\x1f.jpg")
        with self.assertRaises(PersonImageUrlError):
            normalize_person_image_url("https://example.com/a\x7f.jpg")

    def test_backslash_in_authority_rejected(self):
        with self.assertRaises(PersonImageUrlError):
            normalize_person_image_url("https://example.com\\evil.example/a.jpg")
        with self.assertRaises(PersonImageUrlError):
            normalize_person_image_url("http://127.0.0.1\\@example.com/a.jpg")

    def test_non_global_literal_ips_rejected(self):
        hosts = [
            "127.0.0.1",
            "0.0.0.0",
            "10.1.2.3",
            "172.16.0.9",
            "192.168.1.10",
            "169.254.1.1",
            "::1",
            "fc00::1",
            "fd12::1",
            "fe80::1",
            "::",
        ]
        for host in hosts:
            ip = ipaddress.ip_address(host)
            self.assertIsNot(ip.is_global, True, host)
            url = f"http://{host}/x.jpg" if ":" not in host else f"http://[{host}]/x.jpg"
            with self.subTest(host=host):
                with self.assertRaises(PersonImageUrlError):
                    normalize_person_image_url(url)

    def test_multicast_and_site_local_literals_rejected(self):
        hosts = [
            "224.0.0.1",
            "239.255.255.250",
            "ff02::1",
            "ff0e::1",
            "fec0::1",
        ]
        for host in hosts:
            ip = ipaddress.ip_address(host)
            self.assertFalse(person_image._is_public_unicast_address(ip), host)
            url = f"http://{host}/x.jpg" if ":" not in host else f"http://[{host}]/x.jpg"
            with self.subTest(host=host):
                with self.assertRaises(PersonImageUrlError):
                    normalize_person_image_url(url)

    def test_public_unicast_literals_still_allowed(self):
        self.assertEqual(
            normalize_person_image_url("http://8.8.8.8/x.jpg"),
            "http://8.8.8.8/x.jpg",
        )
        self.assertEqual(
            normalize_person_image_url("http://[2001:4860:4860::8888]/x.jpg"),
            "http://[2001:4860:4860::8888]/x.jpg",
        )

    def test_non_string_rejected(self):
        with self.assertRaises(PersonImageUrlError):
            normalize_person_image_url(123)


class TestFraming(unittest.TestCase):
    def test_content_length_rules(self):
        self.assertIsNotNone(
            person_image._parse_response_framing(_http_message())
        )
        ok = person_image._parse_response_framing(
            _http_message(**{"Content-Length": "8"})
        )
        self.assertEqual(ok[0], 8)
        self.assertIsNone(
            person_image._parse_response_framing(
                _http_message(**{"Content-Length": ["10", "10"]})
            )
        )
        self.assertIsNone(
            person_image._parse_response_framing(
                _http_message(**{"Content-Length": "10, 11"})
            )
        )
        self.assertIsNone(
            person_image._parse_response_framing(
                _http_message(**{"Content-Length": "-1"})
            )
        )
        self.assertIsNone(
            person_image._parse_response_framing(
                _http_message(**{"Content-Length": "12.0"})
            )
        )
        with patch.object(person_image, "PRKS_PORTRAIT_MAX_DOWNLOAD_BYTES", 16):
            self.assertIsNone(
                person_image._parse_response_framing(
                    _http_message(**{"Content-Length": "17"})
                )
            )
            self.assertIsNotNone(
                person_image._parse_response_framing(
                    _http_message(**{"Content-Length": "16"})
                )
            )

    def test_content_type_and_encoding(self):
        self.assertIsNone(
            person_image._parse_response_framing(
                _http_message(**{"Content-Type": ["image/png", "image/jpeg"]})
            )
        )
        self.assertIsNone(
            person_image._parse_response_framing(
                _http_message(**{"Content-Encoding": "gzip"})
            )
        )
        self.assertIsNone(
            person_image._parse_response_framing(
                _http_message(**{"Content-Encoding": ["identity", "identity"]})
            )
        )
        self.assertIsNotNone(
            person_image._parse_response_framing(
                _http_message(**{"Content-Encoding": "identity"})
            )
        )

    def test_transfer_encoding(self):
        self.assertIsNotNone(
            person_image._parse_response_framing(
                _http_message(**{"Transfer-Encoding": "chunked"})
            )
        )
        self.assertIsNone(
            person_image._parse_response_framing(
                _http_message(
                    **{
                        "Transfer-Encoding": "chunked",
                        "Content-Length": "12",
                    }
                )
            )
        )
        self.assertIsNone(
            person_image._parse_response_framing(
                _http_message(**{"Transfer-Encoding": ["chunked", "chunked"]})
            )
        )
        self.assertIsNone(
            person_image._parse_response_framing(
                _http_message(**{"Transfer-Encoding": "gzip, chunked"})
            )
        )
        self.assertIsNone(
            person_image._parse_response_framing(
                _http_message(**{"Transfer-Encoding": "gzip"})
            )
        )


class TestAddrinfoPolicy(unittest.TestCase):
    def test_literal_loopback_no_dns(self):
        with patch("backend.person_image._getaddrinfo") as g:
            self.assertIsNone(
                person_image._validated_addrinfo_records("127.0.0.1", 80)
            )
            g.assert_not_called()

    def test_dns_public_ok(self):
        info = _public_addrinfo()
        with patch("backend.person_image._getaddrinfo", return_value=info) as g:
            recs = person_image._validated_addrinfo_records("example.com", 443)
            self.assertEqual(recs, info)
            g.assert_called_once()

    def test_dns_mixed_public_private_rejected(self):
        info = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]
        with patch("backend.person_image._getaddrinfo", return_value=info):
            self.assertIsNone(
                person_image._validated_addrinfo_records("example.com", 443)
            )

    def test_dns_private_only_and_empty_rejected(self):
        private = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)),
        ]
        with patch("backend.person_image._getaddrinfo", return_value=private):
            self.assertIsNone(
                person_image._validated_addrinfo_records("example.com", 443)
            )
        with patch("backend.person_image._getaddrinfo", return_value=[]):
            self.assertIsNone(
                person_image._validated_addrinfo_records("example.com", 443)
            )

    def test_unsupported_family_ignored(self):
        unix = (getattr(socket, "AF_UNIX", 1), socket.SOCK_STREAM, 0, "", "/tmp/x")
        inet = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))
        with patch(
            "backend.person_image._getaddrinfo", return_value=[unix, inet]
        ):
            recs = person_image._validated_addrinfo_records("example.com", 443)
            self.assertEqual(recs, [inet])
        with patch("backend.person_image._getaddrinfo", return_value=[unix]):
            self.assertIsNone(
                person_image._validated_addrinfo_records("example.com", 443)
            )

    def test_dns_multicast_and_site_local_rejected(self):
        cases = [
            ("224.0.0.1", socket.AF_INET, ("224.0.0.1", 443)),
            ("239.255.255.250", socket.AF_INET, ("239.255.255.250", 443)),
            ("ff02::1", socket.AF_INET6, ("ff02::1", 443, 0, 0)),
            ("ff0e::1", socket.AF_INET6, ("ff0e::1", 443, 0, 0)),
            ("fec0::1", socket.AF_INET6, ("fec0::1", 443, 0, 0)),
        ]
        for host, family, sockaddr in cases:
            info = [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]
            with self.subTest(host=host):
                with patch("backend.person_image._getaddrinfo", return_value=info):
                    self.assertIsNone(
                        person_image._validated_addrinfo_records("example.com", 443)
                    )
                with patch("backend.person_image._create_socket") as sock_cls:
                    self.assertIsNone(
                        person_image._validated_addrinfo_records(host, 80)
                    )
                    sock_cls.assert_not_called()


class TestPinnedFetch(unittest.TestCase):
    def _fetch(self, url, response, addrinfo, ssl_ctx=None, connect_error=None):
        created = []
        ctx = ssl_ctx if ssl_ctx is not None else FakeSSLContext()
        patches = _install_network(
            created,
            response=response,
            addrinfo=addrinfo,
            ssl_ctx=ctx,
            connect_error=connect_error,
        )
        for p in patches:
            p.start()
        try:
            result = fetch_and_prepare(url)
            return result, created, ctx
        finally:
            for p in reversed(patches):
                p.stop()

    def test_pins_ipv4_sockaddr_and_tls_identity(self):
        png = _png_bytes()
        body = _http_bytes(
            headers=[("Content-Type", "image/png"), ("Content-Length", str(len(png)))],
            body=png,
        )
        sa = ("8.8.8.8", 443)
        info = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sa)]
        result, created, ctx = self._fetch("https://example.com/a.jpg?x=1", body, info)
        self.assertIsNotNone(result)
        self.assertEqual(result.subtype, "webp")
        self.assertEqual(identify_cached_portrait_subtype(result.body), "webp")
        self.assertEqual(len(created), 1)
        sock = created[0]
        self.assertEqual(sock.family, socket.AF_INET)
        self.assertEqual(sock.type, socket.SOCK_STREAM)
        self.assertEqual(sock.proto, socket.IPPROTO_TCP)
        self.assertEqual(sock.connected_to, sa)
        self.assertEqual(ctx.alpn, ["http/1.1"])
        self.assertEqual(ctx.wraps[0]["server_hostname"], "example.com")
        self.assertIs(ctx.wraps[0]["sock"], sock)
        sent = bytes(sock.sent).decode("latin1")
        self.assertIn("GET /a.jpg?x=1 HTTP/1.1", sent)
        self.assertIn("Host: example.com\r\n", sent)
        self.assertIn("Connection: close", sent)
        self.assertIn("Accept-Encoding: identity", sent)
        self.assertNotIn("example.com", sock.connected_to)

    def test_httpconnection_connect_never_used(self):
        png = _png_bytes()
        body = _http_bytes(
            headers=[("Content-Length", str(len(png)))],
            body=png,
        )
        info = _public_addrinfo()
        with patch.object(
            person_image._PinnedHTTPConnection,
            "connect",
            side_effect=AssertionError("HTTPConnection.connect must not run"),
        ):
            result, _, _ = self._fetch("https://example.com/p.jpg", body, info)
        self.assertIsNotNone(result)

    def test_ipv6_full_sockaddr(self):
        png = _png_bytes()
        body = _http_bytes(
            headers=[("Content-Length", str(len(png)))],
            body=png,
        )
        sa = ("2001:4860:4860::8888", 443, 0, 0)
        info = [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sa)]
        result, created, ctx = self._fetch(
            "https://img.example.org/p.jpg", body, info
        )
        self.assertIsNotNone(result)
        self.assertEqual(created[0].family, socket.AF_INET6)
        self.assertEqual(created[0].connected_to, sa)
        self.assertEqual(ctx.wraps[0]["server_hostname"], "img.example.org")
        sent = bytes(created[0].sent).decode("latin1")
        self.assertIn("Host: img.example.org\r\n", sent)

    def test_empty_path_origin_form(self):
        png = _png_bytes()
        body = _http_bytes(headers=[("Content-Length", str(len(png)))], body=png)
        result, created, _ = self._fetch(
            "https://example.com?x=1", body, _public_addrinfo()
        )
        self.assertIsNotNone(result)
        sent = bytes(created[0].sent).decode("latin1")
        self.assertIn("GET /?x=1 HTTP/1.1", sent)

    def test_redirect_not_followed(self):
        body = _http_bytes(
            status=302,
            reason="Found",
            headers=[("Location", "http://127.0.0.1/secret")],
        )
        created = []
        ctx = FakeSSLContext()
        patches = _install_network(
            created, response=body, addrinfo=_public_addrinfo(), ssl_ctx=ctx
        )
        for p in patches:
            p.start()
        try:
            self.assertIsNone(fetch_and_prepare("https://example.com/a.jpg"))
            self.assertEqual(len(created), 1)
        finally:
            for p in reversed(patches):
                p.stop()

    def test_public_redirect_not_followed(self):
        body = _http_bytes(
            status=301,
            reason="Moved",
            headers=[("Location", "https://example.net/b.jpg")],
        )
        self.assertIsNone(
            self._fetch("https://example.com/a.jpg", body, _public_addrinfo())[0]
        )

    def test_status_codes(self):
        png = _png_bytes()
        for status in (204, 206, 404, 500):
            body = _http_bytes(
                status=status,
                headers=[("Content-Length", str(len(png)))],
                body=png,
            )
            with self.subTest(status=status):
                self.assertIsNone(
                    self._fetch("https://example.com/a.jpg", body, _public_addrinfo())[0]
                )

    def test_content_type_gate(self):
        png = _png_bytes()
        for ctype in (
            "text/html",
            "text/plain",
            "application/json",
            "application/xml",
            "image/svg+xml",
        ):
            body = _http_bytes(
                headers=[
                    ("Content-Type", ctype),
                    ("Content-Length", str(len(png))),
                ],
                body=png,
            )
            with self.subTest(ctype=ctype):
                self.assertIsNone(
                    self._fetch("https://example.com/a.jpg", body, _public_addrinfo())[0]
                )
        body = _http_bytes(
            headers=[
                ("Content-Type", "application/octet-stream"),
                ("Content-Length", str(len(png))),
            ],
            body=png,
        )
        self.assertIsNotNone(
            self._fetch("https://example.com/a.jpg", body, _public_addrinfo())[0]
        )
        body = _http_bytes(headers=[("Content-Length", str(len(png)))], body=png)
        self.assertIsNotNone(
            self._fetch("https://example.com/a.jpg", body, _public_addrinfo())[0]
        )

    def test_byte_cap_content_length_and_stream(self):
        with patch.object(person_image, "PRKS_PORTRAIT_MAX_DOWNLOAD_BYTES", 16):
            body = _http_bytes(headers=[("Content-Length", "17")], body=b"x" * 17)
            created = []
            patches = _install_network(
                created,
                response=body,
                addrinfo=_public_addrinfo(),
                ssl_ctx=FakeSSLContext(),
            )
            for p in patches:
                p.start()
            try:
                self.assertIsNone(fetch_and_prepare("https://example.com/a.jpg"))
            finally:
                for p in reversed(patches):
                    p.stop()
            lying = _http_bytes(headers=[], body=b"y" * 17)
            self.assertIsNone(
                self._fetch("https://example.com/a.jpg", lying, _public_addrinfo())[0]
            )

    def test_loopback_url_never_connects(self):
        with patch("backend.person_image._getaddrinfo") as g:
            with patch("backend.person_image._create_socket") as s:
                self.assertIsNone(fetch_and_prepare("http://127.0.0.1/image.jpg"))
                g.assert_not_called()
                s.assert_not_called()

    def test_deadline_after_dns(self):
        clock = [1000.0]

        def fake_mono():
            return clock[0]

        def fake_getaddrinfo(*args, **kwargs):
            clock[0] = 1007.0
            return _public_addrinfo()

        with patch("backend.person_image.time.monotonic", fake_mono):
            with patch("backend.person_image._getaddrinfo", fake_getaddrinfo):
                with patch(
                    "backend.person_image._create_socket",
                    side_effect=AssertionError("socket after deadline"),
                ):
                    self.assertIsNone(
                        fetch_and_prepare("https://example.com/a.jpg")
                    )

    def test_deadline_shared_across_candidates(self):
        clock = [1000.0]
        timeouts = []

        def fake_mono():
            return clock[0]

        info = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        ]
        created = []

        def fake_socket(family, type=socket.SOCK_STREAM, proto=0, fileno=None):
            sock = FakeSocket(b"", connect_error=OSError("down"))
            orig_set = sock.settimeout

            def rec(t):
                timeouts.append(t)
                orig_set(t)

            sock.settimeout = rec
            created.append(sock)
            clock[0] += 2
            return sock

        with patch("backend.person_image.time.monotonic", fake_mono):
            with patch("backend.person_image._getaddrinfo", return_value=info):
                with patch("backend.person_image._create_socket", fake_socket):
                    with patch(
                        "backend.person_image.ssl.create_default_context",
                        return_value=FakeSSLContext(),
                    ):
                        self.assertIsNone(
                            fetch_and_prepare("https://example.com/a.jpg")
                        )
        self.assertGreaterEqual(len(created), 2)
        later = [t for t in timeouts if t is not None]
        self.assertTrue(any(t < 6 for t in later))

    def test_slow_drip_hits_total_deadline(self):
        clock = [1000.0]

        class SlowResp:
            def __init__(self):
                self.n = 0

            def read(self, amt):
                clock[0] += 3
                self.n += 1
                if self.n > 8:
                    return b""
                return b"x" * min(8, amt)

        class DummySock:
            def settimeout(self, t):
                if t is not None and t <= 0:
                    raise TimeoutError("deadline")

        with patch("backend.person_image.time.monotonic", lambda: clock[0]):
            out = person_image._read_bounded_body(SlowResp(), DummySock(), 1006.0)
        self.assertIsNone(out)

    def test_trickle_headers_interrupted_at_absolute_deadline(self):
        header = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: image/png\r\n"
            b"Content-Length: 0\r\n"
            b"\r\n"
        )
        client, peer = socket.socketpair()
        stop = threading.Event()

        def trickle():
            try:
                for byte in header:
                    if stop.is_set():
                        return
                    peer.sendall(bytes([byte]))
                    time.sleep(0.025)
            except OSError:
                return

        thread = threading.Thread(target=trickle, daemon=True)
        thread.start()
        deadline = time.monotonic() + 0.3
        started = time.monotonic()
        try:
            result = person_image._http_exchange(
                client, "example.com", "example.com", 80, "/", deadline
            )
            elapsed = time.monotonic() - started
        finally:
            stop.set()
            try:
                peer.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                peer.close()
            except OSError:
                pass
            thread.join(timeout=1.0)
        self.assertIsNone(result)
        self.assertLess(elapsed, 0.8)
        self.assertGreaterEqual(elapsed, 0.2)

    def test_trickle_body_interrupted_at_absolute_deadline(self):
        header = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/octet-stream\r\n"
            b"Content-Length: 80\r\n"
            b"\r\n"
        )
        payload = b"x" * 80
        client, peer = socket.socketpair()
        stop = threading.Event()

        def trickle():
            try:
                peer.sendall(header)
                for byte in payload:
                    if stop.is_set():
                        return
                    peer.sendall(bytes([byte]))
                    time.sleep(0.025)
            except OSError:
                return

        thread = threading.Thread(target=trickle, daemon=True)
        thread.start()
        deadline = time.monotonic() + 0.3
        started = time.monotonic()
        try:
            result = person_image._http_exchange(
                client, "example.com", "example.com", 80, "/", deadline
            )
            elapsed = time.monotonic() - started
        finally:
            stop.set()
            try:
                peer.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                peer.close()
            except OSError:
                pass
            thread.join(timeout=1.0)
        self.assertIsNone(result)
        self.assertLess(elapsed, 0.8)
        self.assertGreaterEqual(elapsed, 0.2)


class TestDecodeAndTranscode(unittest.TestCase):
    def test_raster_formats_and_output(self):
        for raw in (_png_bytes(), _jpeg_bytes(), _webp_bytes(), _gif_bytes()):
            encoded = decode_and_transcode(raw, None)
            self.assertIsNotNone(encoded)
            out, subtype = encoded
            self.assertIn(subtype, ("webp", "jpeg"))
            self.assertEqual(identify_cached_portrait_subtype(out), subtype)
            from PIL import Image

            img = Image.open(BytesIO(out))
            self.assertLessEqual(max(img.size), 512)

    def test_rejects_non_images(self):
        for raw in (
            b"<!DOCTYPE html><html></html>",
            b'{"a":1}',
            os.urandom(64),
            b"\x89PNG\r\n\x1a\ntruncated",
            b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
        ):
            self.assertIsNone(decode_and_transcode(raw, None))

    def test_content_type_html_rejected_even_if_png(self):
        self.assertIsNone(decode_and_transcode(_png_bytes(), "text/html"))

    def test_dimension_and_pixel_limits(self):
        wide = _png_ihdr_only(12001, 10)
        self.assertIsNone(decode_and_transcode(wide, None))
        huge = _png_ihdr_only(5001, 5001)
        self.assertIsNone(decode_and_transcode(huge, None))

    def test_decompression_bomb_fail_closed(self):
        raw = _png_ihdr_only(40000, 40000)
        before = None
        try:
            from PIL import Image

            before = Image.MAX_IMAGE_PIXELS
        except Exception:
            self.skipTest("Pillow not installed")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.assertIsNone(decode_and_transcode(raw, None))
        from PIL import Image

        self.assertEqual(Image.MAX_IMAGE_PIXELS, before)

    def test_animated_gif_rejected(self):
        self.assertIsNone(decode_and_transcode(_gif_bytes(frames=2), None))

    def test_jpeg_fallback_converts_rgba(self):
        from PIL import Image

        img = Image.new("RGBA", (20, 10), (10, 20, 30, 40))
        buf = BytesIO()
        img.save(buf, format="PNG")
        raw = buf.getvalue()
        with patch.object(person_image, "_encode_webp", return_value=None):
            encoded = decode_and_transcode(raw, None)
        self.assertIsNotNone(encoded)
        out, subtype = encoded
        self.assertEqual(subtype, "jpeg")
        self.assertEqual(identify_cached_portrait_subtype(out), "jpeg")

    def test_both_encoders_fail_returns_none(self):
        with patch.object(person_image, "_encode_webp", return_value=None):
            with patch.object(person_image, "_encode_jpeg", return_value=None):
                self.assertIsNone(decode_and_transcode(_png_bytes(), None))


class TestLegacyBinRead(unittest.TestCase):
    def test_exact_max_allowed_max_plus_one_not_fully_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap = 32
            with patch.object(person_image, "PRKS_PORTRAIT_MAX_DOWNLOAD_BYTES", cap):
                ok_path = os.path.join(tmp, "ok.bin")
                with open(ok_path, "wb") as fp:
                    fp.write(b"a" * cap)
                self.assertEqual(len(read_legacy_portrait_bytes(ok_path)), cap)

                big_path = os.path.join(tmp, "big.bin")
                with open(big_path, "wb") as fp:
                    fp.write(b"b" * (cap + 1))
                opened = []
                real_open = open

                def spy(path, *args, **kwargs):
                    opened.append(path)
                    return real_open(path, *args, **kwargs)

                with patch("builtins.open", spy):
                    self.assertIsNone(read_legacy_portrait_bytes(big_path))
                self.assertNotIn(big_path, opened)


class TestUnverifiedContextNotUsed(unittest.TestCase):
    def test_module_does_not_call_unverified_context(self):
        with open(person_image.__file__, encoding="utf-8") as fp:
            src = fp.read()
        self.assertNotIn("_create_unverified_context", src)
        self.assertIn("ssl.create_default_context()", src)


if __name__ == "__main__":
    unittest.main()
