"""Requests the Schemathesis mutation scan found answered with a 500 or no answer.

``scripts/ci/schemathesis_mutations.py`` fuzzes the positions, saved views and
publishers families. Before the fixes in the HTTP adapter it found:

* a body that is not UTF-8, or JSON nested past the decoder's recursion
  limit, escaped the body reader and the connection closed with no answer;
* a ``"\\ud800"`` escape decoded to a lone surrogate, and psycopg failed to
  encode it when the route stored the name (a 500);
* ``%00`` in a query reached a PostgreSQL bind, which refuses NUL (a 500).
  The alias routes decoded their value a second time; they now decode it
  once, so ``%2500`` is the literal text ``%00`` everywhere.

Each is now a 400 before any route runs. Every case is sent with the owner's
session, Origin and Library epoch, so the refusal is the body or URL check and
not admission.
"""

from __future__ import annotations

import http.client
import socket

from tests.pg.http_fixtures import Response
from tests.pg.server_api_fixtures import ServerApiTestCase


class RequestFuzzRegressionTests(ServerApiTestCase):
    def raw_post(self, path: str, payload: bytes) -> Response:
        """A mutation whose body is exactly ``payload``, valid JSON or not."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.putrequest("POST", path, skip_host=True, skip_accept_encoding=True)
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

    def position_names(self) -> list[str]:
        return [row["name"] for row in self.sql("SELECT name FROM positions")]

    def test_a_body_that_is_not_utf8_is_invalid_json(self) -> None:
        res = self.raw_post("/api/positions", b'{"name": "\xff\xfe"}')
        self.assertEqual(self.refused(res, 400), {"error": "invalid_json", "code": "invalid_request"})
        self.assertEqual(self.position_names(), [])

    def test_json_nested_past_the_recursion_limit_is_invalid_json(self) -> None:
        res = self.raw_post("/api/positions", b"[" * 200_000)
        self.assertEqual(self.refused(res, 400), {"error": "invalid_json", "code": "invalid_request"})

    def test_a_lone_surrogate_escape_is_invalid_json_and_nothing_is_stored(self) -> None:
        for payload in (b'{"name": "a\\ud800"}', b'{"name": "a", "\\udfff": 1}',
                        b'{"name": "a", "tags": [["\\udc00"]]}'):
            with self.subTest(payload=payload):
                res = self.raw_post("/api/positions", payload)
                self.assertEqual(self.refused(res, 400), {"error": "invalid_json", "code": "invalid_request"})
        self.assertEqual(self.position_names(), [])

    def test_a_surrogate_pair_is_still_text(self) -> None:
        res = self.raw_post("/api/positions", b'{"name": "a\\ud83d\\ude00"}')
        self.ok(res, 201)
        self.assertEqual(self.position_names(), ["a\U0001f600"])

    def test_nul_in_the_request_target_is_refused_before_routing(self) -> None:
        publisher = self.ok(self.send("POST", "/api/publishers", {"name": "Fuzz Press"}))
        for method, path in (("GET", "/api/search?q=%00"),
                             ("GET", "/api/publishers/%00"),
                             ("DELETE", f"/api/publishers/{publisher['id']}/aliases?alias=a%00b"),
                             # A static file path: os.path refuses NUL.
                             ("GET", "/%00")):
            with self.subTest(method=method, path=path):
                res = (self.get(path) if method == "GET" else self.send(method, path))
                self.assertEqual(self.refused(res, 400), {"error": "invalid_request"})
        self.assertEqual([row["name"] for row in self.sql("SELECT name FROM publishers")], ["Fuzz Press"])

    def test_an_encoded_percent_is_literal_text_not_a_nul(self) -> None:
        """``%2500`` decodes once, to the text ``%00``: a valid search, not a refusal."""
        for query in ("%2500", "hello%2500world"):
            with self.subTest(query=query):
                self.assertEqual(self.ok(self.get(f"/api/search?q={query}")), [])

    def test_an_alias_with_a_percent_sign_is_removed_by_its_own_text(self) -> None:
        """The alias routes decode the value once, like every other query value."""
        publisher = self.ok(self.send("POST", "/api/publishers", {"name": "Percent Press"}))
        aliases = f"/api/publishers/{publisher['id']}/aliases"
        for alias in ("50%", "a%00b", "a%41"):
            self.ok(self.send("POST", aliases, {"alias": alias}))
        # encodeURIComponent, as the client sends it: "%" is "%25".
        for encoded, alias in (("50%25", "50%"), ("a%2500b", "a%00b"), ("a%2541", "a%41")):
            with self.subTest(alias=alias):
                self.assertEqual(self.ok(self.send("DELETE", f"{aliases}?alias={encoded}")),
                                 {"status": "deleted"})
        tag = self.ok(self.send("POST", "/api/tags", {"name": "percent-tag"}))
        tag_aliases = f"/api/tags/{tag['id']}/aliases"
        self.ok(self.send("POST", tag_aliases, {"alias": "100%"}))
        self.assertEqual(self.ok(self.send("DELETE", f"{tag_aliases}?alias=100%25"))["status"], "deleted")

    def test_a_raw_nul_in_the_request_line_is_refused(self) -> None:
        with socket.create_connection(("127.0.0.1", self.port), timeout=30) as sock:
            sock.sendall(b"GET /api/search?q=a\x00b HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                         b"Connection: close\r\n\r\n" % self.port)
            answer = b""
            while chunk := sock.recv(65536):
                answer += chunk
        self.assertTrue(answer.startswith(b"HTTP/1.1 400") or answer.startswith(b"HTTP/1.0 400"),
                        answer[:200])
