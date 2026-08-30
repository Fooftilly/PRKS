import json
import logging
import os
import re
import sys
import tempfile
import unittest
from contextlib import contextmanager
from email.message import Message
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse


_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.log_safety import (
    PrivacySafeFormatter,
    client_error_log_fields,
    format_client_error_log,
    safe_bind_scope,
    safe_error_type,
    safe_log_id,
    safe_route,
)
from backend.pdf_linearize import maybe_linearize_pdf_in_place
from backend.server import PRKSHandler
from backend.work_deletion import delete_work


SECRET_TITLE = "PRIVATE_RESEARCH_TITLE_X9Q7"
SECRET_QUERY = "SECRET_SEARCH_QUERY_X9Q7"
SECRET_PATH = "/home/private-user/PRIVATE_BOOK_X9Q7.pdf"
SECRET_URL = "https://example.invalid/private/X9Q7"
SECRET_STACK = "SECRET_BROWSER_STACK_X9Q7"
SECRET_QPDF = "SECRET_QPDF_STDERR_X9Q7"
SECRET_UA = "SECRET_UA_X9Q7 Mozilla/5.0"
SECRET_CLIENT = "SECRET_CLIENT_IP_X9Q7"
SECRET_CLEANUP = "SECRET_PATH_X9Q7 /home/private/file.pdf"
ALL_SECRETS = (
    SECRET_TITLE,
    SECRET_QUERY,
    SECRET_PATH,
    SECRET_URL,
    SECRET_STACK,
    SECRET_QPDF,
    SECRET_UA,
    SECRET_CLIENT,
    "SECRET_PATH_X9Q7",
    "PRIVATE_BOOK_X9Q7",
    "private-user",
)


def _formatted_text(records: list[str]) -> str:
    return "\n".join(records)


@contextmanager
def _capture_formatted(logger_name: str, level: int = logging.DEBUG):
    logger = logging.getLogger(logger_name)
    records: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    handler = _Handler()
    handler.setLevel(level)
    handler.setFormatter(
        PrivacySafeFormatter("%(levelname)s %(name)s %(message)s")
    )
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(level)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)


def _bare_handler(**kwargs):
    handler = object.__new__(PRKSHandler)
    handler._prks_request_id = kwargs.get("request_id", "aabbccddeeff")
    handler.command = kwargs.get("method", "GET")
    handler.path = kwargs.get("path", "/")
    handler.client_address = kwargs.get("client_address", (SECRET_CLIENT, 54321))
    handler.headers = Message()
    handler.send_json = lambda *a, **k: None
    return handler


class TestSafeRoute(unittest.TestCase):
    def test_search_drops_query(self):
        self.assertEqual(
            safe_route(f"/api/search?q={SECRET_QUERY}"),
            "/api/search",
        )
        self.assertNotIn(SECRET_QUERY, safe_route(f"/api/search?q={SECRET_QUERY}"))

    def test_pdf_filename_is_templated(self):
        route = safe_route(f"/api/pdfs/{SECRET_TITLE}.pdf")
        self.assertEqual(route, "/api/pdfs/:pdf")
        self.assertNotIn(SECRET_TITLE, route)

    def test_work_nested_drops_id_and_query(self):
        route = safe_route(f"/api/works/W-ABC/annotations?x={SECRET_QUERY}")
        self.assertEqual(route, "/api/works/:id/annotations")
        self.assertNotIn("W-ABC", route)
        self.assertNotIn(SECRET_QUERY, route)

    def test_known_nested_families(self):
        self.assertEqual(
            safe_route("/api/persons/P-123/profile-image"),
            "/api/persons/:id/profile-image",
        )
        self.assertEqual(
            safe_route("/api/processing-files/abc/pdf"),
            "/api/processing-files/:id/pdf",
        )
        self.assertEqual(
            safe_route("/api/playlists/private-playlist/items/work-id"),
            "/api/playlists/:id/items/:id",
        )

    def test_unknown_api_fails_closed(self):
        self.assertEqual(safe_route("/api/not-a-real-family/secret"), "/api/:unknown")
        self.assertEqual(safe_route("/api/works/W-ABC/secret-suffix"), "/api/:unknown")
        self.assertNotIn("secret", safe_route("/api/not-a-real-family/secret"))

    def test_static_first_party_paths_kept(self):
        self.assertEqual(safe_route("/"), "/")
        self.assertEqual(safe_route("/index.html"), "/index.html")
        self.assertEqual(safe_route("/js/app.js"), "/js/app.js")
        self.assertEqual(
            safe_route("/vendor/prks-pdf-viewer/prks-pdf-viewer.js"),
            "/vendor/prks-pdf-viewer/prks-pdf-viewer.js",
        )
        self.assertEqual(safe_route("/css/style.css"), "/css/style.css")
        self.assertEqual(safe_route("/icons/foo.svg"), "/icons/foo.svg")

    def test_unknown_static_path_fails_closed(self):
        self.assertEqual(safe_route(f"/{SECRET_TITLE}"), "/:static")
        self.assertNotIn(SECRET_TITLE, safe_route(f"/{SECRET_TITLE}"))
        self.assertEqual(safe_route("/some/arbitrary/user/value"), "/:static")


class TestSafeIdentifiers(unittest.TestCase):
    def test_rejects_newlines_and_paths(self):
        self.assertEqual(safe_log_id("W-ABC123"), "W-ABC123")
        self.assertEqual(safe_log_id("id\ninjected"), "invalid")
        self.assertEqual(safe_log_id(SECRET_PATH), "invalid")
        self.assertEqual(safe_log_id(None), "unknown")

    def test_error_type_is_class_only(self):
        self.assertEqual(
            safe_error_type(RuntimeError(SECRET_TITLE)),
            "RuntimeError",
        )

    def test_bind_scope(self):
        self.assertEqual(safe_bind_scope("127.0.0.1"), "loopback")
        self.assertEqual(safe_bind_scope("localhost"), "loopback")
        self.assertEqual(safe_bind_scope("::1"), "loopback")
        self.assertEqual(safe_bind_scope("0.0.0.0"), "all_interfaces")
        self.assertEqual(safe_bind_scope("::"), "all_interfaces")
        self.assertEqual(safe_bind_scope("192.168.1.50"), "custom")
        self.assertNotIn("192.168.1.50", safe_bind_scope("192.168.1.50"))


class TestPrivacySafeFormatter(unittest.TestCase):
    def test_strips_exception_value_and_absolute_paths(self):
        formatter = PrivacySafeFormatter("%(message)s")

        def _raise_secret():
            raise RuntimeError(f"{SECRET_TITLE} {SECRET_PATH}")

        try:
            _raise_secret()
        except RuntimeError:
            text = formatter.formatException(sys.exc_info())
        self.assertIn("RuntimeError", text)
        self.assertIn("tests/test_logging_privacy.py", text)
        self.assertNotIn(SECRET_TITLE, text)
        self.assertNotIn("/home/private-user", text)
        self.assertNotIn("PRIVATE_BOOK_X9Q7", text)
        self.assertNotIn(_PROJECT_DIR, text)

        try:
            json.loads("{")
        except json.JSONDecodeError:
            external = formatter.formatException(sys.exc_info())
        self.assertIn("<external>/", external)
        self.assertNotIn(os.path.abspath(json.__file__), external)

    def test_handler_format_matches_implementation(self):
        logger = logging.getLogger("prks.privacy_formatter_probe")
        with _capture_formatted(logger.name) as records:
            try:
                raise PermissionError(f"{SECRET_TITLE} {SECRET_PATH}")
            except PermissionError:
                logger.exception("unhandled_api_error error_type=%s", "PermissionError")
        text = _formatted_text(records)
        self.assertIn("PermissionError", text)
        self.assertNotIn(SECRET_TITLE, text)
        self.assertNotIn(SECRET_PATH, text)


class TestAccessLogging(unittest.TestCase):
    def test_access_log_is_debug_and_drops_secrets(self):
        handler = _bare_handler(
            path=f"/api/search?q={SECRET_QUERY}",
            method="GET",
        )
        with _capture_formatted("prks.server") as records:
            handler.log_request(200, 12)
        text = _formatted_text(records)
        self.assertIn("request_access", text)
        self.assertIn("method=GET", text)
        self.assertIn("route=/api/search", text)
        self.assertIn("status=200", text)
        self.assertIn("request_id=aabbccddeeff", text)
        for secret in ALL_SECRETS:
            self.assertNotIn(secret, text)

    def test_pdf_access_omits_filename(self):
        handler = _bare_handler(path=f"/api/pdfs/{SECRET_TITLE}.pdf")
        with _capture_formatted("prks.server") as records:
            handler.log_request(200)
        text = _formatted_text(records)
        self.assertIn("route=/api/pdfs/:pdf", text)
        self.assertNotIn(SECRET_TITLE, text)
        self.assertNotIn(".pdf", text)

    def test_stdlib_log_message_does_not_interpolate(self):
        handler = _bare_handler()
        with _capture_formatted("prks.server") as records:
            handler.log_message('"%s" %s %s', f"GET /api/search?q={SECRET_QUERY} HTTP/1.1", "200", "-")
        text = _formatted_text(records)
        self.assertIn("stdlib_http_message", text)
        self.assertNotIn(SECRET_QUERY, text)

    def test_request_access_omits_arbitrary_non_api_path(self):
        handler = _bare_handler(path=f"/{SECRET_TITLE}")
        with _capture_formatted("prks.server") as records:
            handler.log_request(404)
        text = _formatted_text(records)
        self.assertIn("request_access", text)
        self.assertIn("route=/:static", text)
        self.assertNotIn(SECRET_TITLE, text)


class TestInternalErrorLogging(unittest.TestCase):
    def test_unhandled_api_error_omits_query_and_exception_text(self):
        handler = _bare_handler(path=f"/api/search?q={SECRET_QUERY}")
        with _capture_formatted("prks.server") as records:
            try:
                raise RuntimeError(f"{SECRET_TITLE} {SECRET_PATH}")
            except RuntimeError as exc:
                handler._send_internal_error(exc)
        text = _formatted_text(records)
        self.assertIn("unhandled_api_error", text)
        self.assertIn("RuntimeError", text)
        self.assertIn("route=/api/search", text)
        self.assertIn("request_id=aabbccddeeff", text)
        self.assertNotIn("query=", text)
        self.assertNotIn("client=", text)
        for secret in (SECRET_TITLE, SECRET_QUERY, SECRET_PATH, "/home/private-user"):
            self.assertNotIn(secret, text)
        self.assertIn("tests/test_logging_privacy.py", text)
        self.assertNotIn(_PROJECT_DIR, text)


class TestClientErrorPrivacy(unittest.TestCase):
    def test_legacy_payload_is_not_logged(self):
        payload = {
            "kind": "window_error",
            "message": SECRET_TITLE,
            "stack": SECRET_STACK,
            "route": f"#/works/{SECRET_TITLE}",
            "source": "https://host/private/SECRET_X9Q7.js",
            "request_id": "abc123",
        }
        handler = _bare_handler()
        handler.headers["User-Agent"] = SECRET_UA
        handler._read_json_body = lambda: payload
        with _capture_formatted("prks.server") as records:
            handler.handle_api_post(urlparse("/api/client-errors"))
        text = _formatted_text(records)
        self.assertIn("client_error", text)
        self.assertIn("kind=window_error", text)
        self.assertIn("request_id=aabbccddeeff", text)
        self.assertIn("source=external", text)
        self.assertNotIn(SECRET_TITLE, text)
        self.assertNotIn(SECRET_STACK, text)
        self.assertNotIn("SECRET_X9Q7", text)
        self.assertNotIn(SECRET_UA, text)
        self.assertNotIn("#/works/", text)
        self.assertNotIn("user_agent=", text)
        self.assertNotIn("stack=", text)
        self.assertNotIn("message=", text)

    def test_normalizer_ignores_legacy_fields(self):
        fields = client_error_log_fields(
            {
                "kind": "not a real kind",
                "message": SECRET_TITLE,
                "stack": SECRET_STACK,
                "source": f"https://localhost:8080/js/components/works-pdf.js",
                "error_name": "TypeError",
                "line": 412,
                "column": 18,
            }
        )
        self.assertEqual(fields["kind"], "client_error")
        self.assertEqual(fields["source"], "works-pdf.js")
        self.assertEqual(fields["error_name"], "TypeError")
        log_line = format_client_error_log(fields, request_id="req1")
        self.assertNotIn(SECRET_TITLE, log_line)
        self.assertNotIn(SECRET_STACK, log_line)


class TestQpdfPrivacy(unittest.TestCase):
    def test_qpdf_stderr_and_path_are_not_logged(self):
        with tempfile.TemporaryDirectory(prefix="prks-qpdf-") as tmp:
            pdf_path = os.path.join(tmp, f"{SECRET_TITLE}.pdf")
            with open(pdf_path, "wb") as fh:
                fh.write(b"%PDF-1.4\n")
            completed = SimpleNamespace(
                returncode=2,
                stdout="",
                stderr=f"{SECRET_QPDF} {SECRET_PATH}",
            )
            with patch("backend.pdf_linearize._linearize_enabled", return_value=True):
                with patch("backend.pdf_linearize.shutil.which", return_value="/usr/bin/qpdf"):
                    with patch(
                        "backend.pdf_linearize.subprocess.run",
                        return_value=completed,
                    ):
                        with _capture_formatted("prks.pdf") as records:
                            changed, reason = maybe_linearize_pdf_in_place(
                                pdf_path,
                                context="work-create-upload",
                            )
        self.assertEqual((changed, reason), (False, "qpdf-failed"))
        text = _formatted_text(records)
        self.assertIn("pdf_linearize_failed", text)
        self.assertIn("context=work-create-upload", text)
        self.assertIn("exit_code=2", text)
        self.assertNotIn(SECRET_QPDF, text)
        self.assertNotIn(SECRET_PATH, text)
        self.assertNotIn(SECRET_TITLE, text)
        self.assertNotIn(pdf_path, text)


class TestCleanupPrivacy(unittest.TestCase):
    def test_pdf_cleanup_logs_error_type_not_path(self):
        class _Storage:
            pdfs_dir = "/tmp"
            thumbs_dir = "/tmp"

        class _Db:
            storage = _Storage()

            def delete_work_record(self, work_id):
                return SimpleNamespace(
                    file_path="/api/pdfs/book.pdf",
                    managed_pdf_still_referenced=False,
                )

        class _Index:
            def remove_work(self, work_id):
                return 0

        def _raise_secret(path, *args, **kwargs):
            raise OSError(SECRET_CLEANUP)

        with patch(
            "backend.work_deletion.prks_delete_pdf_thumbnails_for_work_id",
            return_value=(),
        ):
            with patch("backend.work_deletion.os.remove", side_effect=_raise_secret):
                with _capture_formatted("prks.work_deletion") as records:
                    result = delete_work(_Db(), _Index(), "W-ABC123")
        self.assertEqual(result.cleanup_failures, ("pdf",))
        text = _formatted_text(records)
        self.assertIn("work_delete_pdf_cleanup_failed", text)
        self.assertIn("error_type=OSError", text)
        self.assertIn("work_id=W-ABC123", text)
        self.assertNotIn("SECRET_PATH_X9Q7", text)
        self.assertNotIn("/home/private/file.pdf", text)

    def test_thumbnail_cleanup_logs_failed_count_not_paths(self):
        class _Storage:
            pdfs_dir = "/tmp"
            thumbs_dir = "/tmp"

        class _Db:
            storage = _Storage()

            def delete_work_record(self, work_id):
                return None

        class _Index:
            def remove_work(self, work_id):
                return 0

        failed = (
            f"/home/private/{SECRET_TITLE}.webp",
            "/home/private/file.pdf",
        )
        with patch(
            "backend.work_deletion.prks_delete_pdf_thumbnails_for_work_id",
            return_value=failed,
        ):
            with _capture_formatted("prks.work_deletion") as records:
                result = delete_work(_Db(), _Index(), "W-ABC123")
        self.assertEqual(result.cleanup_failures, ("thumbnails",))
        text = _formatted_text(records)
        self.assertIn("failed_count=2", text)
        self.assertNotIn("failed=(", text)
        self.assertNotIn(SECRET_TITLE, text)
        self.assertNotIn("/home/private", text)


def _js_function(src: str, name: str) -> str:
    marker = f"function {name}("
    start = src.index(marker)
    rest = src[start + 1 :]
    nxt = re.search(r"\n(?:async )?function ", rest)
    if nxt is None:
        return src[start:]
    return src[start : start + 1 + nxt.start()]


class TestClientErrorReportingOwnership(unittest.TestCase):
    def test_one_diagnostic_event_per_api_failure_owner(self):
        path = os.path.join(_PROJECT_DIR, "frontend", "js", "api.js")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()

        set_err = _js_function(src, "prksSetApiError")
        self.assertIn("__prksLastApiError", set_err)
        self.assertNotIn("prksReportClientError", set_err)
        self.assertNotIn("api_client_error", set_err)

        parse = _js_function(src, "prksParseJsonResponse")
        self.assertIn("'api_http_error'", parse)
        self.assertIn("'api_parse_error'", parse)
        self.assertNotIn("api_client_error", parse)
        self.assertEqual(parse.count("prksReportClientError("), 2)
        self.assertEqual(parse.count("prksSetApiError("), 2)

        report_client = _js_function(src, "prksReportApiClientError")
        self.assertIn("'api_client_error'", report_client)
        self.assertEqual(
            src.count("prksSetApiError('"),
            src.count("prksReportApiClientError('"),
        )


class TestSourcePolicy(unittest.TestCase):
    def test_first_party_log_format_strings_avoid_known_unsafe_fields(self):
        forbidden = (
            "query=%s",
            "user_agent=%s",
            "stack=%s",
            "stderr=%s",
            "log_file=%s",
        )
        hits = []
        backend_dir = os.path.join(_PROJECT_DIR, "backend")
        for root, _dirs, files in os.walk(backend_dir):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
                for token in forbidden:
                    if token in text:
                        rel = os.path.relpath(path, _PROJECT_DIR)
                        hits.append(f"{rel}: {token}")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
