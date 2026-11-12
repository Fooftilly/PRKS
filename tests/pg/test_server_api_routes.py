"""Static paths, PDF payloads, Folder filing and Publishers over HTTP on the PostgreSQL Library (PG-C2 #641).

A static path never climbs out of the served tree. A PDF replace with a body
that is not base64 is a 400. A Work is filed in Uncategorized unless told
otherwise, can be filed in one Folder only, and moves or leaves its Folder
through ``PATCH``. Publishers are listed, created (a second create answers
the first), aliased and deleted, every request and response checked against
the Publishers OpenAPI slice, and a search by publisher finds the Work.
These replace the matching ``tests/test_server_api.py`` cases.
"""

from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import parse_qsl, quote, urlsplit

from tests.pg.server_api_fixtures import ServerApiTestCase


class RouteTests(ServerApiTestCase):
    def test_a_static_path_never_leaves_the_served_tree(self) -> None:
        self.assertEqual(self.call("GET", "/../../backend/server.py", cookies=self.cookies).status, 404)

    def test_a_pdf_body_that_is_not_base64_is_refused(self) -> None:
        work_id = self.create_work("bad b64", status="Planned")["id"]
        self.assertIn("error", self.refused(self.send("POST", f"/api/works/{work_id}/pdf",
                                                      {"file_b64": "!!not-base64!!"}), 400))

    def test_a_work_is_filed_in_uncategorized_by_default(self) -> None:
        work = self.ok(self.get(f"/api/works/{quote(self.create_work('Default Folder Work')['id'])}"))
        self.assertEqual(work.get("folder_title"), "Uncategorized")
        self.assertTrue(work.get("folder_id"))

    def test_a_work_is_in_one_folder_and_moves_or_leaves_it_by_patch(self) -> None:
        alpha = self.ok(self.send("POST", "/api/folders", {"title": "API Folder Alpha", "description": ""}))["id"]
        beta = self.ok(self.send("POST", "/api/folders", {"title": "API Folder Beta", "description": ""}))["id"]
        work_id = self.create_work("Folderable Work", status="Planned")["id"]
        path = f"/api/works/{quote(work_id)}"
        self.ok(self.send("PATCH", path, {"folder_id": None}))
        self.ok(self.send("POST", f"/api/folders/{quote(alpha)}/works", {"work_id": work_id}))
        self.refused(self.send("POST", f"/api/folders/{quote(beta)}/works", {"work_id": work_id}), 409)
        listed = next(row for row in self.ok(self.get("/api/works")) if row.get("id") == work_id)
        self.assertEqual(listed.get("folder_id"), alpha)
        detail = self.ok(self.get(path))
        self.assertEqual((detail.get("folder_id"), detail.get("folder_title")), (alpha, "API Folder Alpha"))
        self.ok(self.send("PATCH", path, {"folder_id": None}))
        detail = self.ok(self.get(path))
        self.assertEqual((detail.get("folder_id"), detail.get("folder_title")), (None, None))
        self.ok(self.send("PATCH", path, {"folder_id": beta}))
        detail = self.ok(self.get(path))
        self.assertEqual((detail.get("folder_id"), detail.get("folder_title")), (beta, "API Folder Beta"))


class PublisherTests(ServerApiTestCase):
    def publishers(self, method: str, path: str, payload: Optional[Any] = None, *,
                   schema_valid: bool = True) -> tuple[int, Any]:
        """Send one Publishers request and check it against the OpenAPI slice.

        ``schema_valid=False`` sends a body the schema refuses; only the
        response is checked then.
        """
        from openapi_core import OpenAPI
        from openapi_core.testing import MockRequest, MockResponse

        from backend.api_contract.openapi import publishers_openapi_document

        res = self.get(path) if method == "GET" else self.send(method, path, payload)
        data = json.dumps(payload).encode() if payload is not None else None
        parsed = urlsplit(path)
        api = OpenAPI.from_dict(publishers_openapi_document())
        request = MockRequest(host_url="http://127.0.0.1", method=method.lower(), path=parsed.path,
                              args=dict(parse_qsl(parsed.query)), data=data)
        if schema_valid:
            api.validate_request(request)
        api.validate_response(request, MockResponse(data=res.raw, status_code=res.status,
                                                    content_type="application/json"))
        return res.status, json.loads(res.raw.decode())

    def test_a_search_by_publisher_finds_the_work(self) -> None:
        work_id = self.create_work("PubSearchApiWork", publisher="MegaPublisher House")["id"]
        self.assertIn(work_id, self.search_ids(f"/api/search?publisher={quote('MegaPublisher')}"))

    def test_a_publisher_is_listed_created_aliased_and_deleted(self) -> None:
        status, before = self.publishers("GET", "/api/publishers?used=1")
        self.assertEqual(status, 200)
        self.assertIsInstance(before, list)
        status, body = self.publishers("POST", "/api/publishers", {"name": "ApiCanonPublisher"})
        self.assertEqual((status, body["name"], body["existed"]), (200, "ApiCanonPublisher", False))
        publisher_id = body["id"]
        status, again = self.publishers("POST", "/api/publishers", {"name": "apicanonpublisher"})
        self.assertEqual((status, again["id"], again["existed"]), (200, publisher_id, True))
        quoted = quote(publisher_id)
        self.assertEqual(self.publishers("POST", f"/api/publishers/{quoted}/aliases", {"alias": "ACP Alias"}),
                         (200, {"status": "added"}))
        _, after = self.publishers("GET", "/api/publishers?used=1")
        self.assertEqual(next(row for row in after if row.get("id") == publisher_id),
                         {"id": publisher_id, "name": "ApiCanonPublisher", "aliases": ["ACP Alias"], "work_count": 0})
        alias = quote("ACP Alias")
        self.assertEqual(self.publishers("DELETE", f"/api/publishers/{quoted}/aliases?alias={alias}"),
                         (200, {"status": "deleted"}))
        status, missing = self.publishers("DELETE", f"/api/publishers/{quoted}/aliases?alias={alias}")
        self.assertEqual((status, missing["error"]), (404, "alias not found"))
        self.assertEqual(self.publishers("DELETE", f"/api/publishers/{quoted}"), (200, {"status": "deleted"}))

    def test_wrong_types_are_refused_and_domain_messages_are_kept(self) -> None:
        for payload in ({"name": 7}, {}):
            status, body = self.publishers("POST", "/api/publishers", payload, schema_valid=False)
            self.assertEqual((status, body["code"]), (400, "invalid_request"))
        self.assertEqual(self.publishers("POST", "/api/publishers", {"name": "   "}),
                         (400, {"error": "publisher name is empty"}))
        _, created = self.publishers("POST", "/api/publishers", {"name": "TypedAliasPublisher"})
        quoted = quote(created["id"])
        status, body = self.publishers("POST", f"/api/publishers/{quoted}/aliases", {"alias": ["x"]},
                                       schema_valid=False)
        self.assertEqual((status, body["code"]), (400, "invalid_request"))
        self.assertEqual(self.publishers("POST", f"/api/publishers/{quoted}/aliases", {"alias": "typedaliaspublisher"}),
                         (400, {"error": "alias matches canonical publisher name"}))
