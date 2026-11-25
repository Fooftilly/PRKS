"""Every JSON route refuses a body that is not a JSON object with 400 (#1424).

The dispatcher reads and checks a JSON route's body once, before the route
runs, so no route can answer 500 for ``[]``, ``null`` or text that is not
JSON (#1421, #1272, #1304). This walks the served route table, so a route
added later is covered without being listed here. Each request carries the
owner's session, Origin and Library epoch: the refusal is the body check,
not admission.
"""

from __future__ import annotations

import http.client
import json
import re

from backend.web.app import served_routes
from backend.web.routing import Body
from tests.pg.http_fixtures import Response
from tests.pg.server_api_fixtures import ServerApiTestCase

_PARAM = re.compile(r"\{[^}]+\}")

# Bodies a route must never see: valid JSON that is not an object, and text
# that is not JSON (non-finite numbers and lone surrogates included).
NOT_OBJECTS = (b"[]", b"null", b'"text"', b"7", b"true", b"[{}]")
NOT_JSON = (b"{", b"{'a': 1}", b'{"a": NaN}', b'{"a": 1e309}', b'{"a": "\\ud800"}', b"\xff")


class RouteTableBodyTests(ServerApiTestCase):
    def raw(self, method: str, path: str, payload: bytes) -> Response:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", f"127.0.0.1:{self.port}")
            conn.putheader("Origin", self.origin)
            conn.putheader("Cookie", f"prks_session={self.cookies['prks_session']}")
            conn.putheader("PRKS-Library-Epoch", str(self.library_epoch()))
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(payload)))
            conn.endheaders(payload)
            res = conn.getresponse()
            return Response(res.status, list(res.getheaders()), res.read())
        finally:
            conn.close()

    def json_routes(self) -> list[tuple[str, str]]:
        routes = [(r.method, _PARAM.sub("x", r.pattern)) for r in served_routes().routes if r.body_kind is Body.JSON]
        self.assertGreater(len(routes), 50, "the walk no longer sees the route table")
        return routes

    def test_every_json_route_refuses_a_body_that_is_not_an_object(self) -> None:
        for method, path in self.json_routes():
            for payload in NOT_OBJECTS:
                with self.subTest(method=method, path=path, payload=payload):
                    res = self.raw(method, path, payload)
                    self.assertEqual(res.status, 400, res.raw[:500])
                    self.assertEqual(json.loads(res.raw), {"error": "JSON object body required", "code": "invalid_request"})

    def test_every_json_route_refuses_text_that_is_not_json(self) -> None:
        for method, path in self.json_routes():
            for payload in NOT_JSON:
                with self.subTest(method=method, path=path, payload=payload):
                    res = self.raw(method, path, payload)
                    self.assertEqual(res.status, 400, res.raw[:500])
                    self.assertEqual(json.loads(res.raw), {"error": "invalid_json", "code": "invalid_request"})

    def test_an_unknown_path_is_404_and_a_known_path_with_another_method_is_405(self) -> None:
        res = self.get("/api/no-such-family")
        self.assertEqual(res.status, 404)
        res = self.send("PUT", "/api/tags", {})
        self.assertEqual(res.status, 405)
        self.assertEqual(res.header("Allow"), ["GET, POST"])
