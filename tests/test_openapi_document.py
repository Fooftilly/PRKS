"""The one OpenAPI document, built from the route table (#1431).

Every route declares its contract on its row in ``backend/api/<family>.py``
and appears in ``docs/api/openapi.json``; ``UNTYPED`` lists the routes not
typed yet. It only shrinks: a new route declares its contract, and a route
that gains one leaves the list.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from openapi_core import OpenAPI
from pydantic import create_model

from backend.api.session import epoch_fenced
from backend.api import ROUTES
from backend.api_contract.openapi import SchemaConflict, build_document, openapi_document, openapi_path, untyped
from backend.api_contract.operation import Operation, ok
from backend.web.routing import Body, route

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "docs" / "api" / "openapi.json"

UNTYPED = frozenset({
    ("GET", "/api/argument-verdicts"),
    ("GET", "/api/arguments"),
    ("POST", "/api/arguments"),
    ("DELETE", "/api/arguments/{argument_id}"),
    ("GET", "/api/arguments/{argument_id}"),
    ("PATCH", "/api/arguments/{argument_id}"),
    ("PUT", "/api/arguments/{argument_id}/sources"),
    ("GET", "/api/arguments/{argument_id}/sync-state"),
    ("PUT", "/api/arguments/{argument_id}/targets"),
    ("GET", "/api/bibtex/{work_id}"),
    ("GET", "/api/concepts"),
    ("POST", "/api/concepts"),
    ("DELETE", "/api/concepts/{concept_id}"),
    ("GET", "/api/concepts/{concept_id}"),
    ("PATCH", "/api/concepts/{concept_id}"),
    ("PUT", "/api/concepts/{concept_id}/aliases"),
    ("PUT", "/api/concepts/{concept_id}/parents"),
    ("GET", "/api/concepts/{concept_id}/sync-state"),
    ("GET", "/api/folders"),
    ("POST", "/api/folders"),
    ("DELETE", "/api/folders/{folder_id}"),
    ("GET", "/api/folders/{folder_id}"),
    ("PATCH", "/api/folders/{folder_id}"),
    ("GET", "/api/folders/{folder_id}/sync-state"),
    ("POST", "/api/folders/{folder_id}/works"),
    ("GET", "/api/folders/{owner_id}/tag-options"),
    ("POST", "/api/folders/{owner_id}/tags"),
    ("DELETE", "/api/folders/{owner_id}/tags/{tag_id}"),
    ("GET", "/api/pdfs/{filename:path}"),
    ("HEAD", "/api/pdfs/{filename:path}"),
    ("GET", "/api/person-groups"),
    ("POST", "/api/person-groups"),
    ("DELETE", "/api/person-groups/{group_id}"),
    ("GET", "/api/person-groups/{group_id}"),
    ("PATCH", "/api/person-groups/{group_id}"),
    ("POST", "/api/person-groups/{group_id}/members"),
    ("DELETE", "/api/person-groups/{group_id}/members/{person_id}"),
    ("GET", "/api/person-groups/{group_id}/sync-state"),
    ("GET", "/api/persons"),
    ("POST", "/api/persons"),
    ("DELETE", "/api/persons/{person_id}"),
    ("GET", "/api/persons/{person_id}"),
    ("PATCH", "/api/persons/{person_id}"),
    ("GET", "/api/persons/{person_id}/group-state"),
    ("GET", "/api/persons/{person_id}/metadata-state"),
    ("GET", "/api/persons/{person_id}/profile-image"),
    ("GET", "/api/playlists"),
    ("POST", "/api/playlists"),
    ("DELETE", "/api/playlists/{playlist_id}"),
    ("GET", "/api/playlists/{playlist_id}"),
    ("PATCH", "/api/playlists/{playlist_id}"),
    ("POST", "/api/playlists/{playlist_id}/items"),
    ("DELETE", "/api/playlists/{playlist_id}/items/{work_id}"),
    ("POST", "/api/playlists/{playlist_id}/reorder"),
    ("GET", "/api/playlists/{playlist_id}/sync-state"),
    ("GET", "/api/recent"),
    ("GET", "/api/recently-added"),
    ("GET", "/api/research-graph"),
    ("POST", "/api/roles"),
    ("GET", "/api/search"),
    ("GET", "/api/tags"),
    ("POST", "/api/tags"),
    ("POST", "/api/tags/merge"),
    ("DELETE", "/api/tags/{tag_id}"),
    ("DELETE", "/api/tags/{tag_id}/aliases"),
    ("POST", "/api/tags/{tag_id}/aliases"),
    ("GET", "/api/works"),
    ("POST", "/api/works"),
    ("POST", "/api/works/bulk"),
    ("POST", "/api/works/linearize-existing-pdfs"),
    ("POST", "/api/works/reindex-pdf-text"),
    ("GET", "/api/works/{owner_id}/tag-options"),
    ("POST", "/api/works/{owner_id}/tags"),
    ("DELETE", "/api/works/{owner_id}/tags/{tag_id}"),
    ("DELETE", "/api/works/{work_id}"),
    ("GET", "/api/works/{work_id}"),
    ("PATCH", "/api/works/{work_id}"),
    ("GET", "/api/works/{work_id}/annotations"),
    ("POST", "/api/works/{work_id}/annotations"),
    ("GET", "/api/works/{work_id}/annotations-snapshot"),
    ("GET", "/api/works/{work_id}/annotations-state"),
    ("POST", "/api/works/{work_id}/annotations/adopt"),
    ("GET", "/api/works/{work_id}/folder-state"),
    ("GET", "/api/works/{work_id}/metadata-state"),
    ("GET", "/api/works/{work_id}/notes-state"),
    ("POST", "/api/works/{work_id}/opened"),
    ("POST", "/api/works/{work_id}/pdf"),
    ("GET", "/api/works/{work_id}/pdf-materialization"),
    ("GET", "/api/works/{work_id}/people-state"),
    ("GET", "/api/works/{work_id}/playlist-state"),
    ("GET", "/api/works/{work_id}/related_folders"),
    ("DELETE", "/api/works/{work_id}/roles"),
    ("PATCH", "/api/works/{work_id}/roles"),
    ("GET", "/api/works/{work_id}/source-state"),
    ("GET", "/api/works/{work_id}/thumbnail"),
})


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

    def test_every_typed_route_is_one_operation_and_the_untyped_list_only_shrinks(self) -> None:
        self.assertEqual(set(untyped(ROUTES)), set(UNTYPED),
                         "a new route declares its contract; a route that gained one leaves UNTYPED")
        typed = {(r.method, openapi_path(r.pattern)) for r in ROUTES if r.contract is not None}
        self.assertEqual(set(_operations(self.document)), typed)

    def test_each_operation_names_a_success_and_its_path_parameters(self) -> None:
        for (method, path), op in _operations(self.document).items():
            with self.subTest(method=method, path=path):
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
            if not path.startswith("/api/"):
                continue
            with self.subTest(method=method, path=path):
                self.assertTrue({"401", "403", "503"} <= set(op["responses"]))
                if epoch_fenced(method.upper(), path):
                    self.assertTrue({"400", "409"} <= set(op["responses"]))
                else:
                    self.assertNotIn("409", op["responses"])

    def test_a_read_sent_as_post_documents_no_epoch_refusal(self) -> None:
        for path in ("/api/citations", "/api/diagnostics/performance/reset"):
            responses = self.document["paths"][path]["post"]["responses"]
            with self.subTest(path=path):
                self.assertNotIn("409", responses)
                self.assertNotEqual(responses.get("400"), {"$ref": "#/components/responses/EpochRequired"})
                self.assertNotEqual(responses.get("400"), {"$ref": "#/components/responses/BadRequest"})

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
