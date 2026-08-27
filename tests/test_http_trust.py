import os
import sys
import tempfile
import unittest
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

import backend.server as server_module
from backend.server import (
    bind_storage,
    is_trusted_request_host,
    parse_request_host,
    parse_request_origin,
    parse_trusted_hosts,
    run_server,
)
from backend.storage.config import StorageConfig
from backend.text_index import get_text_index, reset_text_index


def _capture_bind():
    try:
        previous_index = get_text_index()
    except RuntimeError:
        previous_index = None
    return (
        server_module._bound_storage,
        server_module.pdfs_dir,
        server_module.thumbs_dir,
        server_module.processing_dir,
        server_module.db,
        server_module.text_index,
        previous_index,
    )


def _restore_bind(snapshot):
    (
        server_module._bound_storage,
        server_module.pdfs_dir,
        server_module.thumbs_dir,
        server_module.processing_dir,
        server_module.db,
        server_module.text_index,
        previous_index,
    ) = snapshot
    if previous_index is None:
        reset_text_index()
    else:
        from backend.text_index import replace_text_index

        replace_text_index(previous_index)


class TestParseRequestHost(unittest.TestCase):
    def test_accepted_forms(self):
        cases = {
            "localhost": ("localhost", None),
            "localhost:8080": ("localhost", 8080),
            "LOCALHOST": ("localhost", None),
            "127.0.0.1": ("127.0.0.1", None),
            "127.0.0.1:8080": ("127.0.0.1", 8080),
            "[::1]": ("::1", None),
            "[::1]:8080": ("::1", 8080),
            "prks.home.arpa": ("prks.home.arpa", None),
            "prks.home.arpa:8080": ("prks.home.arpa", 8080),
            "prks.home.arpa.": ("prks.home.arpa", None),
        }
        for raw, expected in cases.items():
            self.assertEqual(parse_request_host(raw), expected, raw)

    def test_malformed_rejected(self):
        for raw in (
            "",
            "   ",
            "evil.example/path",
            "user@evil.example",
            "a,b",
            "localhost:",
            "localhost:99999",
            "localhost:80abc",
        ):
            self.assertIsNone(parse_request_host(raw), raw)


class TestTrustedHostPolicy(unittest.TestCase):
    def test_localhost_and_ip_literals(self):
        extra = frozenset()
        self.assertTrue(
            is_trusted_request_host("localhost", bind_host="0.0.0.0", extra_hosts=extra)
        )
        self.assertTrue(
            is_trusted_request_host("127.0.0.1", bind_host="0.0.0.0", extra_hosts=extra)
        )
        self.assertTrue(
            is_trusted_request_host("192.168.1.25", bind_host="0.0.0.0", extra_hosts=extra)
        )
        self.assertTrue(
            is_trusted_request_host("::1", bind_host="0.0.0.0", extra_hosts=extra)
        )

    def test_concrete_bind_hostname(self):
        self.assertTrue(
            is_trusted_request_host(
                "prks.home.arpa", bind_host="prks.home.arpa", extra_hosts=frozenset()
            )
        )

    def test_unspecified_bind_is_not_an_application_host(self):
        self.assertFalse(
            is_trusted_request_host("0.0.0.0", bind_host="0.0.0.0", extra_hosts=frozenset())
        )
        self.assertFalse(
            is_trusted_request_host("::", bind_host="::", extra_hosts=frozenset())
        )

    def test_exact_trusted_hosts_not_suffix(self):
        extra = parse_trusted_hosts("prks.home.arpa")
        self.assertTrue(
            is_trusted_request_host(
                "prks.home.arpa", bind_host="0.0.0.0", extra_hosts=extra
            )
        )
        self.assertFalse(
            is_trusted_request_host(
                "evil.prks.home.arpa", bind_host="0.0.0.0", extra_hosts=extra
            )
        )

    def test_trusted_hosts_parse_valid_and_blank_slots(self):
        self.assertEqual(parse_trusted_hosts(""), frozenset())
        self.assertEqual(parse_trusted_hosts(None), frozenset())
        self.assertEqual(
            parse_trusted_hosts(" prks.home.arpa , , PRKS.internal. "),
            frozenset({"prks.home.arpa", "prks.internal"}),
        )

    def test_trusted_hosts_fail_closed(self):
        for raw in (
            "https://prks.home.arpa",
            "prks.home.arpa:8080",
            "prks.home.arpa/path",
            "user@prks.home.arpa",
            "*.home.arpa",
            "prks.home.arpa,https://evil.example",
        ):
            with self.assertRaises(ValueError):
                parse_trusted_hosts(raw)

    def test_host_trust_never_resolves_dns(self):
        def boom(*_args, **_kwargs):
            raise AssertionError("DNS must not be used for Host trust")

        with patch("socket.getaddrinfo", side_effect=boom):
            self.assertFalse(
                is_trusted_request_host(
                    "attacker.example", bind_host="127.0.0.1", extra_hosts=frozenset()
                )
            )
            self.assertTrue(
                is_trusted_request_host(
                    "prks.home.arpa",
                    bind_host="0.0.0.0",
                    extra_hosts=parse_trusted_hosts("prks.home.arpa"),
                )
            )


class TestParseOrigin(unittest.TestCase):
    def test_http_origin(self):
        self.assertEqual(
            parse_request_origin("http://localhost:8080"),
            ("http", "localhost", 8080),
        )

    def test_null_rejected(self):
        self.assertIsNone(parse_request_origin("null"))

    def test_path_query_fragment_syntax_rejected(self):
        for raw in (
            "http://localhost:8080/",
            "http://localhost:8080?",
            "http://localhost:8080#",
            "http://localhost:8080/path",
            "http://localhost:8080?q=1",
            "http://localhost:8080#frag",
        ):
            self.assertIsNone(parse_request_origin(raw), raw)


class TestMalformedTrustedHostsAbortListen(unittest.TestCase):
    def setUp(self):
        self._prev = _capture_bind()
        self._tmpdir = tempfile.mkdtemp(prefix="prks-trust-bind-")
        bind_storage(StorageConfig.for_testing(self._tmpdir))

    def tearDown(self):
        _restore_bind(self._prev)
        try:
            import shutil

            shutil.rmtree(self._tmpdir, ignore_errors=True)
        except Exception:
            pass

    def test_malformed_trusted_hosts_never_constructs_server(self):
        with patch.object(server_module.socketserver, "TCPServer") as ctor:
            with patch.dict(
                os.environ, {"PRKS_TRUSTED_HOSTS": "https://evil.example"}, clear=False
            ):
                with self.assertRaises(ValueError):
                    run_server(port=9000)
            ctor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
