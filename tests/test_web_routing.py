"""The route table and the dispatcher's JSON body rules (#1424), without a server."""

from __future__ import annotations

import os
import sys
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env  # noqa: E402

apply_isolated_test_env(_PROJECT_DIR)

from backend.web.app import App, served_routes  # noqa: E402
from backend.web.dispatch import BodyRefused, decode_json, read_json_object  # noqa: E402
from backend.web.http import Headers, Request, Response  # noqa: E402
from backend.web.routing import Body, Match, NoRoute, RouteTable, route  # noqa: E402


def _answer(_req: Request) -> Response:
    return Response(200)


class RouteTableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.table = RouteTable((
            route("GET", "/api/works/{work_id}", _answer),
            route("PATCH", "/api/works/{work_id}", _answer),
            route("GET", "/api/works/recent", _answer),
            route("GET", "/api/pdfs/{filename:path}", _answer),
        ))

    def test_a_literal_segment_wins_over_a_parameter(self) -> None:
        found = self.table.resolve("GET", "/api/works/recent")
        assert isinstance(found, Match)
        self.assertEqual((found.route.pattern, found.params), ("/api/works/recent", {}))
        found = self.table.resolve("GET", "/api/works/W-1")
        assert isinstance(found, Match)
        self.assertEqual(found.params, {"work_id": "W-1"})

    def test_a_rest_parameter_takes_the_whole_tail(self) -> None:
        found = self.table.resolve("GET", "/api/pdfs/nested/a.pdf")
        assert isinstance(found, Match)
        self.assertEqual(found.params, {"filename": "nested/a.pdf"})
        self.assertEqual(self.table.resolve("GET", "/api/pdfs/"), NoRoute())

    def test_an_unknown_path_is_404_and_a_known_one_names_its_methods(self) -> None:
        self.assertEqual(self.table.resolve("GET", "/api/nothing"), NoRoute())
        self.assertEqual(self.table.resolve("GET", "/api/works/W-1/extra"), NoRoute())
        self.assertEqual(self.table.resolve("DELETE", "/api/works/W-1"), NoRoute(("GET", "PATCH")))

    def test_an_empty_segment_is_no_parameter(self) -> None:
        self.assertEqual(self.table.resolve("GET", "/api/works/"), NoRoute())

    def test_a_duplicate_route_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            RouteTable((route("GET", "/api/a/{x}", _answer), route("GET", "/api/a/{y}", _answer)))

    def test_a_write_reads_json_unless_it_says_otherwise(self) -> None:
        self.assertIs(route("POST", "/a", _answer).body_kind, Body.JSON)
        self.assertIs(route("GET", "/a", _answer).body_kind, Body.NONE)
        self.assertIs(route("POST", "/a", _answer, Body.STREAM).body_kind, Body.STREAM)

    def test_the_served_table_builds(self) -> None:
        # Building it refuses a duplicate; every route lives under /api/ or is a probe.
        table = served_routes()
        for one in table.routes:
            self.assertTrue(one.pattern.startswith(("/api/", "/healthz", "/readyz")), one.pattern)


class PublicPathTests(unittest.TestCase):
    """A path outside ``/api/`` never reaches an API handler without admission."""

    def setUp(self) -> None:
        from backend.api import health

        self.calls: list[str] = []

        def secret(req: Request) -> Response:
            self.calls.append(req.path)
            return Response(200, [("Content-Length", "6")], b"secret")

        routes = RouteTable((route("GET", "/api/storage-status", secret), *health.ROUTES))
        self.app = App(routes=routes, bind_host="127.0.0.1", frontend_dir=_PROJECT_DIR + "/no-such-frontend")

    def get(self, path: str) -> Response:
        from backend.web.dispatch import dispatch

        return dispatch(Request("GET", path, "", Headers([("Host", "127.0.0.1")]), self.app))

    def test_an_encoded_api_prefix_is_not_routed(self) -> None:
        for path in ("/%61pi/storage-status", "/%61%70%69/storage-status", "/%2Fapi/storage-status",
                     "//api/storage-status", "/%61pi/storage-status/"):
            with self.subTest(path=path):
                response = self.get(path)
                self.assertNotEqual(response.body, b"secret")
        self.assertEqual(self.calls, [])

    def test_an_api_path_without_identity_is_refused(self) -> None:
        self.assertEqual(self.get("/api/storage-status").status, 503)
        self.assertEqual(self.calls, [])

    def test_an_encoded_probe_is_still_a_probe(self) -> None:
        self.assertEqual(self.get("/%68ealthz/").body, b"ok")


class _Body:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.consumed = False

    def read(self, size: int = -1) -> bytes:
        self.consumed = True
        return self.payload

    def read1(self, size: int = -1) -> bytes:
        return self.read(size)

    def settimeout(self, value: object) -> None:
        pass

    def gettimeout(self) -> None:
        return None


def _request(payload: bytes, content_type: str = "application/json") -> Request:
    headers = [("Content-Type", content_type), ("Content-Length", str(len(payload)))]
    return Request("POST", "/api/x", "", Headers(headers), App(), body=_Body(payload))


class JsonBodyTests(unittest.TestCase):
    def refusal(self, request: Request) -> tuple[int, bytes]:
        with self.assertRaises(BodyRefused) as caught:
            read_json_object(request)
        return caught.exception.response.status, caught.exception.response.body

    def test_an_object_is_read_once(self) -> None:
        request = _request(b'{"a": [1, 2.5, "\\ud83d\\ude00"]}')
        read_json_object(request)
        self.assertEqual(request.data, {"a": [1, 2.5, "\U0001f600"]})
        self.assertFalse(request.json_absent)

    def test_no_body_is_an_empty_object(self) -> None:
        request = _request(b"")
        read_json_object(request)
        self.assertEqual((request.data, request.json_absent), ({}, True))

    def test_json_that_is_not_an_object_is_400(self) -> None:
        for payload in (b"[]", b"null", b'"a"', b"1"):
            with self.subTest(payload=payload):
                self.assertEqual(self.refusal(_request(payload)),
                                 (400, b'{"error": "JSON object body required", "code": "invalid_request"}'))

    def test_text_that_is_not_json_is_400(self) -> None:
        for payload in (b"{", b'{"a": NaN}', b'{"a": -Infinity}', b'{"a": 1e309}', b'{"a": "\\udc00"}',
                        b"\xff", b"[" * 100_000):
            with self.subTest(payload=payload[:20]):
                self.assertEqual(self.refusal(_request(payload)), (400, b'{"error": "invalid_json", "code": "invalid_request"}'))

    def test_another_media_type_is_415(self) -> None:
        self.assertEqual(self.refusal(_request(b"{}", "text/plain"))[0], 415)

    def test_decode_json_refuses_non_finite_numbers(self) -> None:
        self.assertEqual(decode_json(b"1e308"), 1e308)
        for payload in (b"NaN", b"Infinity", b"1e309", b"-1e400"):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                decode_json(payload)


if __name__ == "__main__":
    unittest.main()
