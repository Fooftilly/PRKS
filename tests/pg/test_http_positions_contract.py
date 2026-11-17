"""The Positions HTTP contract served from the PostgreSQL Library (PG-C2 #641).

Every live request and response of the Positions family matches its
response model and the OpenAPI slice: create, list, read, update, sync state
(with a ``304`` on a matching ETag) and delete; the domain's refusal codes
for empty, null and wrongly typed fields; the common envelope for a body
that is not an object; ``415`` and ``413`` before the body is read; and
``409 position_in_use`` while an Argument targets the Position.
These replace ``tests/test_api_contract_positions.py``'s live SQLite cases.
"""

from __future__ import annotations

import json
from typing import Any, Optional
from unittest import mock

from backend.api_contract.openapi import positions_openapi_document
from backend.api_contract.positions import PositionDetail, PositionSummary
from tests.pg.server_api_fixtures import ServerApiTestCase
from tests.test_api_contract_positions_boundary import _validate_http_against_openapi as validate

DETAIL = "/api/positions/{position_id}"
SYNC = "/api/positions/{position_id}/sync-state"


class PositionHttpContractTests(ServerApiTestCase):
    def call_json(self, method: str, path: str, body: Any = None, *, status: int,
                  headers: Optional[list[tuple[str, str]]] = None) -> tuple[Any, bytes, Optional[bytes], Any]:
        res = self.get(path, headers=headers) if method == "GET" else self.send(method, path, body, headers=headers)
        self.assertEqual(res.status, status, res.raw)
        request = None if body is None else json.dumps(body).encode("utf-8")
        return res.body, res.raw, request, res

    def create(self, name: str, description: str = "") -> dict[str, Any]:
        return self.call_json("POST", "/api/positions", {"name": name, "description": description}, status=201)[0]

    def test_the_openapi_endpoint_serves_the_positions_slice(self) -> None:
        doc = self.call_json("GET", "/api/openapi.json", status=200)[0]
        self.assertEqual(doc["info"]["title"], "PRKS API — Positions slice")
        self.assertEqual(doc, positions_openapi_document())

    def test_create_list_get_patch_sync_and_delete(self) -> None:
        created, raw, request, _ = self.call_json("POST", "/api/positions",
                                                  {"name": "  Typed   boundary  ", "description": "hello"}, status=201)
        self.assertEqual((created["name"], created["description"]), ("Typed boundary", "hello"))
        self.assertIn("arguments", created)
        PositionDetail.model_validate(created)
        validate(method="POST", path="/api/positions", status=201, response_body=raw, request_body=request)

        rows, raw, _, _ = self.call_json("GET", "/api/positions", status=200)
        self.assertIn(created["id"], [row["id"] for row in rows])
        for row in rows:
            PositionSummary.model_validate(row)
        validate(method="GET", path="/api/positions", status=200, response_body=raw)

        path, args = f"/api/positions/{created['id']}", {"position_id": created["id"]}
        detail, raw, _, _ = self.call_json("GET", path, status=200)
        PositionDetail.model_validate(detail)
        validate(method="GET", path=path, path_pattern=DETAIL, view_args=args, status=200, response_body=raw)

        updated, raw, request, _ = self.call_json("PATCH", path, {"description": "updated"}, status=200)
        self.assertEqual((updated["description"], updated["name"]), ("updated", "Typed boundary"))
        validate(method="PATCH", path=path, path_pattern=DETAIL, view_args=args, status=200, response_body=raw,
                 request_body=request)

        sync, raw, _, _ = self.call_json("GET", f"{path}/sync-state", status=200)
        self.assertEqual(sync["position_id"], created["id"])
        self.assertIn("name", sync["fields"])
        self.assertIn("description", sync["fields"])
        validate(method="GET", path=f"{path}/sync-state", path_pattern=SYNC, view_args=args, status=200,
                 response_body=raw)

        deleted, raw, _, _ = self.call_json("DELETE", path, status=200)
        self.assertEqual(deleted, {"status": "deleted"})
        validate(method="DELETE", path=path, path_pattern=DETAIL, view_args=args, status=200, response_body=raw)
        self.assertEqual(self.call_json("GET", path, status=404)[0]["error"], "Position not found.")

    def test_a_body_that_is_not_an_object_uses_the_common_envelope(self) -> None:
        body = self.call_json("POST", "/api/positions", ["not", "an", "object"], status=400)[0]
        self.assertEqual(body["code"], "invalid_request")

    def test_the_domain_refuses_an_empty_or_null_field(self) -> None:
        self.assertEqual(self.call_json("POST", "/api/positions", {"name": "   "}, status=400)[0].get("code"),
                         "invalid_name")
        self.assertEqual(self.call_json("POST", "/api/positions", {"name": "Null create", "description": None},
                                        status=400)[0].get("code"), "invalid_text")

    def test_wrong_types_on_create_keep_the_domain_codes(self) -> None:
        cases = (({"name": 12}, "invalid_name", "Name must be a string."),
                 ({"name": "ok", "description": ["nope"]}, "invalid_text", "Text must be a string."),
                 ({"name": "Falsy false", "description": False}, "invalid_text", "Text must be a string."),
                 ({"name": "Falsy zero", "description": 0}, "invalid_text", "Text must be a string."))
        for payload, code, message in cases:
            with self.subTest(payload=payload):
                body, raw, request, _ = self.call_json("POST", "/api/positions", payload, status=400)
                self.assertEqual((body["code"], body["error"]), (code, message))
                validate(method="POST", path="/api/positions", status=400, response_body=raw, request_body=request,
                         check_request=False)

    def test_wrong_types_on_update_keep_the_domain_codes(self) -> None:
        created = self.create("Patch type target")
        path, args = f"/api/positions/{created['id']}", {"position_id": created["id"]}
        for payload, code, message in (({"name": 99}, "invalid_name", "Name must be a string."),
                                       ({"description": True}, "invalid_text", "Text must be a string.")):
            with self.subTest(payload=payload):
                body, raw, request, _ = self.call_json("PATCH", path, payload, status=400)
                self.assertEqual((body["code"], body["error"]), (code, message))
                validate(method="PATCH", path=path, path_pattern=DETAIL, view_args=args, status=400,
                         response_body=raw, request_body=request, check_request=False)

    def test_a_null_update_is_refused_and_an_empty_description_clears(self) -> None:
        created = self.create("Null desc target", "keep me")
        path = f"/api/positions/{created['id']}"
        self.assertEqual(self.call_json("PATCH", path, {"description": None}, status=400)[0].get("code"),
                         "invalid_text")
        self.assertEqual(self.call_json("GET", path, status=200)[0]["description"], "keep me")
        self.assertEqual(self.call_json("PATCH", path, {"name": None}, status=400)[0].get("code"), "invalid_name")
        updated = self.call_json("PATCH", path, {"name": "Null desc renamed", "description": ""}, status=200)[0]
        self.assertEqual((updated["name"], updated["description"]), ("Null desc renamed", ""))

    def test_the_sync_state_is_304_when_the_etag_matches(self) -> None:
        created = self.create("ETag position")
        path, args = f"/api/positions/{created['id']}/sync-state", {"position_id": created["id"]}
        _, raw, _, res = self.call_json("GET", path, status=200)
        etag = res.header("ETag")[0]
        validate(method="GET", path=path, path_pattern=SYNC, view_args=args, status=200, response_body=raw,
                 response_headers={"ETag": etag})
        _, raw, _, res = self.call_json("GET", path, status=304, headers=[("If-None-Match", etag)])
        self.assertEqual(raw, b"")
        etag304 = res.header("ETag")[0]
        validate(method="GET", path=path, path_pattern=SYNC, view_args=args, status=304, response_body=raw,
                 request_headers={"If-None-Match": etag}, response_headers={"ETag": etag304})

    def test_415_and_413_are_answered_as_documented(self) -> None:
        res = self.send_as("POST", "/api/positions", {"name": "x"}, content_type="text/plain")
        self.assertEqual((res.status, res.body["error"]), (415, "unsupported_media_type"))
        validate(method="POST", path="/api/positions", status=415, response_body=res.raw,
                 request_body=b'{"name": "x"}', check_request=False)
        created = self.create("Body read limits")
        path, args = f"/api/positions/{created['id']}", {"position_id": created["id"]}
        res = self.send_as("PATCH", path, {"description": "x"}, content_type="text/plain")
        self.assertEqual(res.status, 415, res.raw)
        validate(method="PATCH", path=path, path_pattern=DETAIL, view_args=args, status=415, response_body=res.raw,
                 request_body=b'{"description": "x"}', check_request=False)
        oversized = {"name": "overflow-body-name"}
        with mock.patch.object(self.server_module, "_PRKS_MAX_JSON_BODY_BYTES", 8):
            post = self.send("POST", "/api/positions", oversized)
            patch = self.send("PATCH", path, oversized)
        self.assertEqual((post.status, post.body["error"]), (413, "request_too_large"))
        self.assertEqual(patch.status, 413, patch.raw)
        body = json.dumps(oversized).encode("utf-8")
        validate(method="POST", path="/api/positions", status=413, response_body=post.raw, request_body=body,
                 check_request=False)
        validate(method="PATCH", path=path, path_pattern=DETAIL, view_args=args, status=413, response_body=patch.raw,
                 request_body=body, check_request=False)
        self.assertEqual(self.call_json("GET", path, status=200)[0]["name"], "Body read limits")

    def test_a_position_an_argument_targets_is_in_use(self) -> None:
        position = self.create("Targeted claim")
        argument = self.call_json("POST", "/api/arguments", {
            "name": "Uses position", "kind": "argument", "main_text": "body",
            "targets": [{"type": "position", "id": position["id"], "verdict_id": "supports"}]}, status=201)[0]
        path, args = f"/api/positions/{position['id']}", {"position_id": position["id"]}
        body, raw, _, _ = self.call_json("DELETE", path, status=409)
        self.assertEqual(body["code"], "position_in_use")
        validate(method="DELETE", path=path, path_pattern=DETAIL, view_args=args, status=409, response_body=raw)
        self.call_json("DELETE", f"/api/arguments/{argument['id']}", status=200)
        self.call_json("DELETE", path, status=200)

    def test_openapi_core_validates_a_create(self) -> None:
        created, raw, request, _ = self.call_json("POST", "/api/positions",
                                                  {"name": "OpenAPI core check", "description": ""}, status=201)
        validate(method="POST", path="/api/positions", status=201, response_body=raw, request_body=request)
        PositionDetail.model_validate(created)
