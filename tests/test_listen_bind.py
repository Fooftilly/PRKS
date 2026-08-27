import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest.mock import MagicMock, patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

import backend.server as server_module
from backend.server import PORT, bind_storage, run_server
from backend.storage.config import StorageConfig
from backend.text_index import get_text_index, reset_text_index
from prks_app import build_parser


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


def _parse_host_argv(*argv):
    return build_parser().parse_args(list(argv))


def _assert_host_parse_exits(testcase, *argv):
    with testcase.assertRaises(SystemExit) as ctx, redirect_stderr(io.StringIO()):
        _parse_host_argv(*argv)
    testcase.assertEqual(ctx.exception.code, 2)


class TestListenCli(unittest.TestCase):
    def test_default_host_is_loopback_ipv4(self):
        args = _parse_host_argv()
        self.assertEqual(args.host, "127.0.0.1")
        self.assertIsNone(args.port)

    def test_explicit_wildcard_host_preserved(self):
        args = _parse_host_argv("--host", "0.0.0.0")
        self.assertEqual(args.host, "0.0.0.0")

    def test_explicit_loopback_host_preserved(self):
        args = _parse_host_argv("--host", "127.0.0.1")
        self.assertEqual(args.host, "127.0.0.1")

    def test_explicit_localhost_host_preserved(self):
        args = _parse_host_argv("--host", "localhost")
        self.assertEqual(args.host, "localhost")

    def test_padded_host_is_stripped(self):
        args = _parse_host_argv("--host", "  127.0.0.1  ")
        self.assertEqual(args.host, "127.0.0.1")

    def test_empty_host_rejected(self):
        _assert_host_parse_exits(self, "--host", "")

    def test_whitespace_host_rejected(self):
        _assert_host_parse_exits(self, "--host", "   ")

    def test_port_override_leaves_default_host(self):
        args = _parse_host_argv("--port", "9000")
        self.assertEqual(args.port, 9000)
        self.assertEqual(args.host, "127.0.0.1")


class TestListenServerBind(unittest.TestCase):
    def setUp(self):
        self._prev = _capture_bind()
        self._tmpdir = tempfile.mkdtemp(prefix="prks-listen-bind-")
        bind_storage(StorageConfig.for_testing(self._tmpdir))

    def tearDown(self):
        _restore_bind(self._prev)
        try:
            import shutil

            shutil.rmtree(self._tmpdir, ignore_errors=True)
        except Exception:
            pass

    def _run_patched(self, **kwargs):
        httpd = MagicMock()
        httpd.serve_forever.side_effect = KeyboardInterrupt
        with patch.object(server_module.socketserver, "TCPServer") as ctor:
            ctor.return_value.__enter__.return_value = httpd
            run_server(**kwargs)
        return ctor

    def test_default_bind_is_loopback_and_default_port(self):
        ctor = self._run_patched()
        ctor.assert_called_once()
        self.assertEqual(ctor.call_args[0][0], ("127.0.0.1", PORT))

    def test_custom_port_keeps_loopback(self):
        ctor = self._run_patched(port=9000)
        ctor.assert_called_once()
        self.assertEqual(ctor.call_args[0][0], ("127.0.0.1", 9000))

    def test_wildcard_host_and_custom_port(self):
        ctor = self._run_patched(host="0.0.0.0", port=9000)
        ctor.assert_called_once()
        self.assertEqual(ctor.call_args[0][0], ("0.0.0.0", 9000))

    def test_empty_host_rejected_before_server_construction(self):
        with patch.object(server_module.socketserver, "TCPServer") as ctor:
            with self.assertRaises(ValueError) as ctx:
                run_server(host="")
            self.assertEqual(str(ctx.exception), "host must not be empty")
            ctor.assert_not_called()

    def test_whitespace_host_rejected_before_server_construction(self):
        with patch.object(server_module.socketserver, "TCPServer") as ctor:
            with self.assertRaises(ValueError) as ctx:
                run_server(host="   ")
            self.assertEqual(str(ctx.exception), "host must not be empty")
            ctor.assert_not_called()


class TestListenDockerConfig(unittest.TestCase):
    def test_entrypoint_binds_all_container_interfaces(self):
        path = os.path.join(_PROJECT_DIR, "docker-entrypoint.sh")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("python /app/prks_app.py --host 0.0.0.0", text)
        self.assertNotIn('"$@"', text)

    def test_compose_publishes_host_loopback_by_default(self):
        path = os.path.join(_PROJECT_DIR, "docker-compose.yml")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("${PRKS_PUBLISH_HOST:-127.0.0.1}:8080:8080", text)


if __name__ == "__main__":
    unittest.main()
