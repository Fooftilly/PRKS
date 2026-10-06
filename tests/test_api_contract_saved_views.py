"""Saved Views wire contract, its OpenAPI artifact, and the Vue client's lockstep keys."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from openapi_core import OpenAPI
from pydantic import ValidationError

from backend.api_contract.boundary import dump_response
from backend.api_contract.openapi import (
    SAVED_VIEWS_CONTRACT_VERSION,
    saved_views_openapi_document,
)
from backend.api_contract.saved_views import (
    SavedView,
    SavedViewDeleted,
    SavedViewSearch,
)
from backend.db_manager import saved_view_api_row

_ROOT = Path(__file__).resolve().parents[1]
_CLIENT = _ROOT / "frontend-app" / "src" / "api" / "saved-views.ts"
_ARTIFACT = _ROOT / "docs" / "api" / "openapi-saved-views.json"


def _ts_list(name: str) -> list[str]:
    text = _CLIENT.read_text(encoding="utf-8")
    match = re.search(rf"export const {name} = \[(.*?)\] as const", text, re.S)
    if match is None:
        raise AssertionError(f"missing {name} in {_CLIENT}")
    return re.findall(r"'([^']+)'", match.group(1))


class SavedViewsContractTests(unittest.TestCase):
    def test_typescript_keys_match_pydantic_fields(self):
        pairs = (
            ("SAVED_VIEW_KEYS", SavedView),
            ("SAVED_VIEW_SEARCH_KEYS", SavedViewSearch),
            ("SAVED_VIEW_DELETED_KEYS", SavedViewDeleted),
        )
        for const_name, model in pairs:
            with self.subTest(const_name=const_name):
                self.assertEqual(set(_ts_list(const_name)), set(model.model_fields))

    def test_typescript_modes_match_the_schema(self):
        schema = SavedViewSearch.model_json_schema()
        self.assertEqual(set(_ts_list("SAVED_VIEW_MODES")), set(schema["properties"]["mode"]["enum"]))

    def test_contract_version_matches_the_typescript_client(self):
        self.assertIn(
            f"SAVED_VIEWS_CONTRACT_VERSION = '{SAVED_VIEWS_CONTRACT_VERSION}'",
            _CLIENT.read_text(encoding="utf-8"),
        )
        self.assertEqual(
            saved_views_openapi_document()["info"]["version"], SAVED_VIEWS_CONTRACT_VERSION
        )

    def test_checked_in_openapi_artifact_matches_generator(self):
        self.assertTrue(_ARTIFACT.is_file(), "commit docs/api/openapi-saved-views.json")
        on_disk = json.loads(_ARTIFACT.read_text(encoding="utf-8"))
        self.assertEqual(on_disk, saved_views_openapi_document())

    def test_openapi_core_accepts_the_document(self):
        document = saved_views_openapi_document()
        OpenAPI.from_dict(document)
        self.assertEqual(
            set(document["paths"]),
            {"/api/saved-views", "/api/saved-views/{view_id}"},
        )

    def test_domain_row_is_the_wire_row(self):
        row = saved_view_api_row(
            {
                "id": "SV-1",
                "name": "Adorno",
                "mode": "advanced",
                "search_q": "culture",
                "search_tag": "",
                "search_author": "Adorno",
                "search_publisher": "",
                "created_at": "2026-10-03 10:00:00",
                "updated_at": None,
            }
        )
        self.assertEqual(dump_response(SavedView, [row]), [row])

    def test_response_models_refuse_unknown_keys_and_bad_modes(self):
        search = {"mode": "all", "q": "x", "tag": "", "author": "", "publisher": ""}
        row = {"id": "SV-1", "name": "x", "search": search, "created_at": None, "updated_at": None}
        with self.assertRaises(ValidationError):
            SavedView.model_validate({**row, "mode": "all"})
        with self.assertRaises(ValidationError):
            SavedView.model_validate({**row, "search": {**search, "mode": "regex"}})
        with self.assertRaises(ValidationError):
            SavedView.model_validate({k: v for k, v in row.items() if k != "updated_at"})
        with self.assertRaises(ValidationError):
            SavedViewDeleted.model_validate({"status": "added"})


if __name__ == "__main__":
    unittest.main()
