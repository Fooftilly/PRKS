"""The one OpenAPI document, built from the route table (#1431).

Every route declares its contract on its row in ``backend/api/<family>.py``
and appears in ``docs/api/openapi.json``; the builder refuses a route
without one.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from openapi_core import OpenAPI
from pydantic import create_model

from backend.api.session import epoch_fenced, fills_derived_cache
from backend.api import ROUTES
from backend.api.identity import IDENTITY_ROUTES
from backend.api_contract.openapi import SchemaConflict, build_document, openapi_document, openapi_path
from backend.api_contract.operation import Operation, ok
from backend.web.routing import Body, route

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "docs" / "api" / "openapi.json"



#: Operations that answer only 501 until they are ported to PostgreSQL; a ported
#: one leaves this set and documents its success.
_NOT_PORTED = frozenset({"linearizeExistingPdfs"})



#: The paths admission answers itself: sign-in, session and setup.
_ADMISSION = frozenset((r.method, r.pattern) for r in IDENTITY_ROUTES)


def _operations(document: dict) -> dict[tuple[str, str], dict]:
    return {(method.upper(), path): op for path, item in document["paths"].items()
            for method, op in item.items() if method != "parameters"}


class OpenApiDocumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = openapi_document()

    def test_the_checked_in_document_is_the_generated_one(self) -> None:
        self.assertTrue(ARTIFACT.is_file(), "run python scripts/write_openapi.py")
        self.assertEqual(json.loads(ARTIFACT.read_text(encoding="utf-8")), self.document,
                         "docs/api/openapi.json is stale: run python scripts/write_openapi.py")

    def test_openapi_core_accepts_the_document(self) -> None:
        OpenAPI.from_dict(self.document)

    def test_every_route_is_one_operation(self) -> None:
        self.assertEqual(set(_operations(self.document)),
                         {(r.method, openapi_path(r.pattern)) for r in (*ROUTES, *IDENTITY_ROUTES)})

    def test_the_paths_admission_answers_are_not_routed(self) -> None:
        self.assertFalse(_ADMISSION & {(r.method, r.pattern) for r in ROUTES})
        self.assertEqual({path for _, path in _ADMISSION},
                         {"/api/login", "/api/logout", "/api/session", "/api/setup", "/api/setup/storage"})

    def test_a_route_without_a_contract_is_refused(self) -> None:
        def handler(req):  # pragma: no cover - never called
            raise AssertionError

        with self.assertRaisesRegex(ValueError, "GET /api/a declares no contract"):
            build_document((route("GET", "/api/a", handler),))

    def test_an_api_operation_needs_the_session_cookie_and_a_probe_none(self) -> None:
        scheme = self.document["components"]["securitySchemes"]["session"]
        self.assertEqual(scheme, {"type": "apiKey", "in": "cookie", "name": "prks_session",
                                  "description": scheme["description"]})
        optional = {("POST", "/api/logout"), ("GET", "/api/session")}
        for (method, path), op in _operations(self.document).items():
            with self.subTest(method=method, path=path):
                if (method, path) in optional:
                    self.assertEqual(op["security"], [{"session": []}, {}])
                elif (method, path) in _ADMISSION or not path.startswith("/api/"):
                    self.assertEqual(op["security"], [])
                else:
                    self.assertEqual(op["security"], [{"session": []}])

    def test_each_operation_names_a_success_and_its_path_parameters(self) -> None:
        for (method, path), op in _operations(self.document).items():
            with self.subTest(method=method, path=path):
                if op["operationId"] in _NOT_PORTED:
                    # Not ported to PostgreSQL yet: it answers only 501 (pre-1.0 policy).
                    self.assertIn("501", op["responses"])
                    self.assertFalse([s for s in op["responses"] if s.startswith("2")], op["responses"])
                else:
                    self.assertTrue(any(status.startswith("2") for status in op["responses"]), op["responses"])
                names = {p["name"] for p in self.document["paths"][path].get("parameters", [])}
                self.assertEqual(names, set(re.findall(r"\{(\w+)\}", path)))

    def test_a_json_route_documents_its_body_and_the_body_gate(self) -> None:
        for one in ROUTES:
            if one.contract is None or one.body_kind is not Body.JSON:
                continue
            op = self.document["paths"][openapi_path(one.pattern)][one.method.lower()]
            with self.subTest(route=f"{one.method} {one.pattern}"):
                self.assertIn("application/json", op["requestBody"]["content"])
                self.assertTrue({"400", "413", "415"} <= set(op["responses"]))

    def test_an_api_route_documents_admission(self) -> None:
        for (method, path), op in _operations(self.document).items():
            if not path.startswith("/api/") or (method, path) in _ADMISSION:
                continue
            with self.subTest(method=method, path=path):
                self.assertTrue({"401", "403", "503"} <= set(op["responses"]))
                if epoch_fenced(method.upper(), path):
                    self.assertTrue({"400", "409"} <= set(op["responses"]))
                else:  # a route's own 400 or 409 is fine; the epoch refusals are not
                    self.assertNotIn(op["responses"].get("400"), (
                        {"$ref": "#/components/responses/EpochRequired"},
                        {"$ref": "#/components/responses/BadRequest"}))
                    self.assertNotEqual(op["responses"].get("409"),
                                        {"$ref": "#/components/responses/EpochChanged"})
                    # Only library_command takes the maintenance lock (#1431): an
                    # unfenced read may be refused for maintenance only when it runs
                    # one to fill a cache on a miss.
                    unavailable = op["responses"]["503"]
                    if "$ref" in unavailable:
                        name = unavailable["$ref"].rsplit("/", 1)[-1]
                        unavailable = self.document["components"]["responses"][name]
                    if fills_derived_cache(method.upper(), path):
                        self.assertIn("MAINTENANCE", unavailable["description"])
                    else:
                        self.assertNotIn("MAINTENANCE", unavailable["description"])

    def test_an_epoch_fenced_mutation_declares_the_epoch_header(self) -> None:
        for (method, path), op in _operations(self.document).items():
            if not path.startswith("/api/"):
                continue
            if (method, path) in _ADMISSION:  # answered before admission reads an epoch
                self.assertNotIn("parameters", op)
                continue
            headers = {p["name"]: p for p in op.get("parameters", []) if p["in"] == "header"}
            with self.subTest(method=method, path=path):
                if epoch_fenced(method.upper(), path):
                    self.assertIn("PRKS-Library-Epoch", headers)
                    self.assertEqual(headers["PRKS-Library-Epoch"]["required"], path != "/api/sync/operations")
                else:
                    self.assertNotIn("PRKS-Library-Epoch", headers)

    def test_the_epoch_header_schema_is_parse_epochs_text_form(self) -> None:
        from backend.domain.identity.request_context import EPOCH_MAX, parse_epoch

        header = next(p for p in self.document["paths"]["/api/settings"]["patch"]["parameters"]
                      if p["name"] == "PRKS-Library-Epoch")
        pattern = re.compile(header["schema"]["pattern"])
        for text in ("0", "01", "+1", " 1", "1.0", "1", "42", str(EPOCH_MAX)):
            with self.subTest(text=text):
                self.assertEqual(pattern.search(text) is not None, parse_epoch(text) is not None)

    def test_a_body_the_route_does_not_need_is_optional(self) -> None:
        for method, path in (("post", "/api/backups/progress"), ("patch", "/api/settings"),
                             ("post", "/api/client-errors")):
            with self.subTest(path=path):
                self.assertFalse(self.document["paths"][path][method]["requestBody"]["required"])

    def test_a_stream_publishes_its_line_schema(self) -> None:
        media = self.document["paths"]["/api/backups/progress"]["post"]["responses"]["200"]["content"]
        self.assertEqual(media["application/x-ndjson"]["x-line-schema"],
                         {"$ref": "#/components/schemas/BackupProgressLine"})

    def test_a_read_sent_as_post_documents_no_epoch_refusal(self) -> None:
        for path in ("/api/citations", "/api/diagnostics/performance/reset"):
            responses = self.document["paths"][path]["post"]["responses"]
            with self.subTest(path=path):
                self.assertNotIn("409", responses)
                self.assertNotEqual(responses.get("400"), {"$ref": "#/components/responses/EpochRequired"})
                self.assertNotEqual(responses.get("400"), {"$ref": "#/components/responses/BadRequest"})

    def test_a_route_refusal_of_its_own_shape_also_admits_the_shared_body(self) -> None:
        conflict = self.document["paths"]["/api/works/{work_id}/pdf"]["post"]["responses"]["409"]
        self.assertEqual(conflict["content"]["application/json"]["schema"], {"anyOf": [
            {"$ref": "#/components/schemas/MaterializationStale"},
            {"$ref": "#/components/schemas/ApiErrorEnvelope"}]})
        self.assertIn("LIBRARY_EPOCH_CHANGED", conflict["description"])

    def test_shared_responses_are_published_only_when_used(self) -> None:
        refs = json.dumps(self.document["paths"])
        for name in self.document["components"]["responses"]:
            self.assertIn(f'"#/components/responses/{name}"', refs)

    def test_two_models_with_one_name_are_refused(self) -> None:
        first = create_model("Thing", a=(int, ...))
        second = create_model("Thing", b=(str, ...))

        def handler(req):  # pragma: no cover - never called
            raise AssertionError

        routes = (
            route("GET", "/api/a", handler, contract=Operation("a", "A", "t", {200: ok(first, "A")})),
            route("GET", "/api/b", handler, contract=Operation("b", "B", "t", {200: ok(second, "B")})),
        )
        with self.assertRaises(SchemaConflict):
            build_document(routes)

    def test_an_operation_id_is_used_once(self) -> None:
        def handler(req):  # pragma: no cover - never called
            raise AssertionError

        contract = Operation("same", "S", "t", {200: ok({"type": "object"}, "S")})
        with self.assertRaises(ValueError):
            build_document((route("GET", "/api/a", handler, contract=contract),
                            route("GET", "/api/b", handler, contract=contract)))

    def test_equivalent_templates_and_duplicate_methods_are_refused(self) -> None:
        def handler(req):  # pragma: no cover - never called
            raise AssertionError

        def op(name: str) -> Operation:
            return Operation(name, name, "t", {200: ok({"type": "object"}, name)})

        for second in (route("GET", "/api/a/{other}", handler, contract=op("b")),
                       route("GET", "/api/a/{item}", handler, contract=op("b"))):
            with self.subTest(pattern=second.pattern), self.assertRaises(ValueError):
                build_document((route("GET", "/api/a/{item}", handler, contract=op("a")), second))

    def test_a_declared_shared_status_keeps_the_shared_meaning(self) -> None:
        def handler(req):  # pragma: no cover - never called
            raise AssertionError

        contract = Operation("w", "W", "t", {200: ok({"type": "object"}, "W"),
                                             409: ok({"type": "object"}, "A conflict of its own.")})
        doc = build_document((route("POST", "/api/w", handler, Body.JSON, contract=contract),))
        description = doc["paths"]["/api/w"]["post"]["responses"]["409"]["description"]
        self.assertTrue(description.startswith("A conflict of its own. Also: "), description)

    def test_an_optional_json_body_still_names_its_content_type(self) -> None:
        for path, op in _operations(self.document).items():
            body = op.get("requestBody")
            if body and not body["required"] and "application/json" in body["content"]:
                with self.subTest(path=path, op=op["operationId"]):
                    self.assertIn("Content-Type: application/json", body["description"])

    def test_a_header_may_be_required(self) -> None:
        op = self.document["paths"]["/api/processing-items/{item_id}/pdf"]["get"]
        self.assertTrue(op["responses"]["206"]["headers"]["Content-Range"]["required"])
        self.assertIn({"name": "Range", "in": "header", "required": False,
                       "schema": {"type": "string"}, "description": "One byte range: `bytes=<first>-[<last>]`, or `bytes=-<suffix length>` for the last bytes."},
                      op["parameters"])

    def test_a_rest_parameter_is_one_openapi_parameter(self) -> None:
        self.assertEqual(openapi_path("/api/pdfs/{filename:path}"), "/api/pdfs/{filename}")

    def test_a_head_route_sends_no_body(self) -> None:
        op = self.document["paths"]["/api/processing-items/{item_id}/pdf"]["head"]
        self.assertEqual(op["operationId"], "getProcessingItemPdfHead")
        for status, reply in op["responses"].items():
            with self.subTest(status=status):
                self.assertNotIn("$ref", reply)
                self.assertNotIn("content", reply)


if __name__ == "__main__":
    unittest.main()
