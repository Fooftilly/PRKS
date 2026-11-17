"""The request trust boundary in front of the PostgreSQL Library (PG-C2 #641).

Before a request reaches the Library, the handler checks its ``Host`` (one,
naming this server, or ``421``/``400``), the ``Origin`` of a mutation, and
the media type of a JSON body (``application/json``, one ``Content-Type``,
charset allowed, or ``415``). A refused request changes nothing. The
PostgreSQL origin rule is stricter than the SQLite one: a mutation needs a
matching ``Origin`` or ``Sec-Fetch-Site: same-origin``, and every variant of
that rule is ``tests/pg/test_http_admission.py``. These replace the SQLite
``tests/test_server_api.py`` trust cases.
"""

from __future__ import annotations

import http.client
import json
from typing import Any, Optional

from tests.pg.http_fixtures import Response
from tests.pg.test_http_processing import ProcessingHttpTestCase

JSON = ("Content-Type", "application/json")


class TrustBoundaryTests(ProcessingHttpTestCase):
    def raw(self, method: str, path: str, headers: list[tuple[str, str]], body: Optional[bytes] = None, *,
            host: Optional[str] = "", origin: Optional[str] = "") -> Response:
        """Send exactly ``headers`` (plus the session and, for a mutation, the epoch).

        ``host``/``origin`` default to this server's own; ``None`` sends none.
        """
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            if host is not None:
                conn.putheader("Host", host or f"127.0.0.1:{self.port}")
            if origin is not None and method not in ("GET", "HEAD"):
                conn.putheader("Origin", origin or self.origin)
            conn.putheader("Cookie", f"prks_session={self.cookies['prks_session']}")
            if method not in ("GET", "HEAD"):
                conn.putheader("PRKS-Library-Epoch", str(self.library_epoch()))
            for name, value in headers:
                conn.putheader(name, value)
            if body is not None:
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body)
            res = conn.getresponse()
            return Response(res.status, list(res.getheaders()), res.read())
        finally:
            conn.close()

    def titles(self) -> list[str]:
        res = self.get("/api/works")
        self.assertEqual(res.status, 200, res.raw)
        return sorted(row["title"] for row in res.body)

    def refused(self, res: Response, status: int, error: str) -> None:  # type: ignore[override]
        self.assertEqual(res.status, status, res.raw)
        self.assertEqual(res.body.get("error"), error, res.raw)

    def body(self, title: str) -> bytes:
        return json.dumps({"title": title}).encode("utf-8")

    def create(self, title: str) -> dict[str, Any]:
        res = self.send("POST", "/api/works", {"title": title})
        self.assertEqual(res.status, 200, res.raw)
        return res.body

    # -- Host --------------------------------------------------------------------------

    def test_a_loopback_host_by_name_or_address_is_served(self) -> None:
        for host in (f"localhost:{self.port}", f"127.0.0.1:{self.port}"):
            with self.subTest(host=host):
                res = self.raw("GET", "/api/works", [], host=host)
                self.assertEqual(res.status, 200, res.raw)
                self.assertIsInstance(res.body, list)

    def test_an_untrusted_or_repeated_host_is_refused_and_changes_nothing(self) -> None:
        self.refused(self.raw("GET", "/api/works", [], host="attacker.example"), 421, "untrusted_host")
        self.refused(self.raw("GET", "/api/works", [("Host", "evil.example")]), 400, "invalid_host")
        res = self.raw("POST", "/api/works", [JSON], self.body("Trust Rebind"), host="attacker.example",
                       origin="http://attacker.example")
        self.refused(res, 421, "untrusted_host")
        self.assertEqual(self.titles(), [])

    # -- Origin ------------------------------------------------------------------------

    def test_a_same_origin_json_post_is_served(self) -> None:
        for host in (f"localhost:{self.port}", f"127.0.0.1:{self.port}"):
            with self.subTest(host=host):
                res = self.raw("POST", "/api/works", [JSON], self.body("Trust Same Origin " + host),
                               host=host, origin="http://" + host)
                self.assertEqual(res.status, 200, res.raw)
                self.assertTrue(res.body.get("id"))

    def test_a_cross_origin_null_or_repeated_origin_is_refused_and_changes_nothing(self) -> None:
        for origins in (["https://evil.example"], ["null"], [self.origin, "https://evil.example"]):
            with self.subTest(origins=origins):
                headers = [("Origin", value) for value in origins[1:]] + [JSON]
                res = self.raw("POST", "/api/works", headers, self.body("Trust Cross Origin"), origin=origins[0])
                self.refused(res, 403, "origin_not_allowed")
        self.assertEqual(self.titles(), [])

    def test_a_cross_origin_delete_leaves_the_work(self) -> None:
        work = self.create("Trust Delete Keep")
        res = self.raw("DELETE", f"/api/works/{work['id']}", [], origin="https://evil.example")
        self.refused(res, 403, "origin_not_allowed")
        self.assertEqual(self.get(f"/api/works/{work['id']}").status, 200)

    # -- Content-Type ------------------------------------------------------------------

    def test_a_json_body_needs_one_json_content_type(self) -> None:
        cases = [
            ([], self.body("Trust Missing CT")),
            ([], None),
            ([("Content-Type", "text/plain")], self.body("Trust Text Plain")),
            ([("Content-Type", "application/x-www-form-urlencoded")], b"title=TrustForm"),
            ([JSON, ("Content-Type", "text/plain")], self.body("Trust Dup CT")),
        ]
        for headers, body in cases:
            with self.subTest(headers=headers, body=body):
                self.refused(self.raw("POST", "/api/works", headers, body), 415, "unsupported_media_type")
        self.assertEqual(self.titles(), [])

    def test_an_empty_json_body_is_an_empty_object(self) -> None:
        res = self.raw("PATCH", "/api/settings", [JSON])
        self.assertEqual(res.status, 200, res.raw)
        self.assertIsInstance(res.body, dict)

    def test_a_json_content_type_with_a_charset_is_served(self) -> None:
        work = self.create("Trust Charset")
        res = self.raw("PATCH", f"/api/works/{work['id']}", [("Content-Type", "application/json; charset=utf-8")],
                       self.body("Trust Charset Patched"))
        self.assertEqual(res.status, 200, res.raw)
        self.assertEqual(self.get(f"/api/works/{work['id']}").body["title"], "Trust Charset Patched")
