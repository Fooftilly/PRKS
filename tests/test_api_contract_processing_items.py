"""Processing queue wire contract (§11.5); the PostgreSQL behavior is in tests/pg/test_http_processing.py."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from openapi_core import OpenAPI
from pydantic import ValidationError

from backend.api_contract.openapi import processing_items_openapi_document
from backend.api_contract.processing_items import ProcessingImportRequest, ProcessingItem, ProcessingItemRefusal
from backend.concurrency import request_access_mode
from backend.log_safety import safe_route

_ROOT = Path(__file__).resolve().parents[1]
_ARTIFACT = _ROOT / "docs" / "api" / "openapi-processing-items.json"
_ID = "PI-" + "0" * 32


def _item(**overrides):
    item = {
        "id": _ID, "original_filename": "a.pdf", "sha256": "0" * 64, "byte_size": 10, "status": "deleted",
        "revision": 1, "created_at": "2026-10-30 12:00:00", "updated_at": "2026-10-30 12:00:00",
        "imported_at": None, "imported_work_id": None, "deleted_at": "2026-10-30 12:00:00", "draft": None,
    }
    item.update(overrides)
    return item


class ProcessingItemsContractTests(unittest.TestCase):
    def test_checked_in_openapi_artifact_matches_generator(self):
        self.assertTrue(_ARTIFACT.is_file(), "commit docs/api/openapi-processing-items.json")
        self.assertEqual(json.loads(_ARTIFACT.read_text(encoding="utf-8")), processing_items_openapi_document())

    def test_openapi_core_accepts_the_document(self):
        document = processing_items_openapi_document()
        OpenAPI.from_dict(document)
        self.assertEqual(set(document["paths"]), {
            "/api/processing-items",
            "/api/processing-items/{item_id}",
            "/api/processing-items/{item_id}/pdf",
            "/api/processing-items/{item_id}/import",
            "/api/processing-items/import",
        })

    def test_models_refuse_unknown_keys_and_bad_values(self):
        ProcessingItem.model_validate(_item())
        for bad in (_item(status="pending"), _item(path="/inbox/a.pdf"), _item(byte_size="big")):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                ProcessingItem.model_validate(bad)
        for bad in ({"item_ids": []}, {"item_ids": [_ID] * 501}, {"item_ids": [_ID], "all": True}):
            with self.subTest(bad=str(bad)[:40]), self.assertRaises(ValidationError):
                ProcessingImportRequest.model_validate(bad)

    def test_a_refusal_before_admission_carries_error_alone(self):
        ProcessingItemRefusal.model_validate({"error": "origin_not_allowed"})
        ProcessingItemRefusal.model_validate({"error": "REVISION_CONFLICT", "code": "REVISION_CONFLICT",
                                              "message": "m", "item": _item()})
        with self.assertRaises(ValidationError):
            ProcessingItemRefusal.model_validate({"code": "X", "message": "m"})

    def test_routes_are_logged_without_ids(self):
        for route, logged in (
            ("/api/processing-items", "/api/processing-items"),
            ("/api/processing-items/import", "/api/processing-items/import"),
            (f"/api/processing-items/{_ID}", "/api/processing-items/:id"),
            (f"/api/processing-items/{_ID}/pdf", "/api/processing-items/:id/pdf"),
            (f"/api/processing-items/{_ID}/import", "/api/processing-items/:id/import"),
            ("/api/openapi/processing-items.json", "/api/openapi/processing-items.json"),
        ):
            with self.subTest(route=route):
                self.assertEqual(safe_route(route), logged)

    def test_an_upload_streams_under_the_read_gate(self):
        self.assertEqual(request_access_mode("POST", "/api/processing-items"), "read")
        self.assertEqual(request_access_mode("POST", "/api/processing-items/import"), "mutation")
        self.assertEqual(request_access_mode("DELETE", f"/api/processing-items/{_ID}"), "mutation")


if __name__ == "__main__":
    unittest.main()
