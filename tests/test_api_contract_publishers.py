"""Publishers wire contract, its OpenAPI artifact, and the Vue client's lockstep keys."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from openapi_core import OpenAPI
from pydantic import ValidationError

from backend.api_contract.boundary import dump_response
from backend.api_contract.openapi import (
    PUBLISHERS_CONTRACT_VERSION,
    publishers_openapi_document,
)
from backend.api_contract.publishers import (
    PublisherAliasAdded,
    PublisherCreated,
    PublisherDeleted,
    PublisherInUse,
)

_ROOT = Path(__file__).resolve().parents[1]
_CLIENT = _ROOT / "frontend-app" / "src" / "api" / "publishers.ts"
_ARTIFACT = _ROOT / "docs" / "api" / "openapi-publishers.json"


def _ts_keys(name: str) -> set[str]:
    text = _CLIENT.read_text(encoding="utf-8")
    match = re.search(rf"export const {name} = \[(.*?)\] as const", text, re.S)
    if match is None:
        raise AssertionError(f"missing {name} in {_CLIENT}")
    return set(re.findall(r"'([^']+)'", match.group(1)))


class PublishersContractTests(unittest.TestCase):
    def test_typescript_keys_match_pydantic_fields(self):
        pairs = (
            ("PUBLISHER_IN_USE_KEYS", PublisherInUse),
            ("PUBLISHER_CREATED_KEYS", PublisherCreated),
            ("PUBLISHER_ALIAS_ADDED_KEYS", PublisherAliasAdded),
            ("PUBLISHER_DELETED_KEYS", PublisherDeleted),
        )
        for const_name, model in pairs:
            with self.subTest(const_name=const_name):
                self.assertEqual(_ts_keys(const_name), set(model.model_fields))

    def test_contract_version_matches_the_typescript_client(self):
        self.assertIn(
            f"PUBLISHERS_CONTRACT_VERSION = '{PUBLISHERS_CONTRACT_VERSION}'",
            _CLIENT.read_text(encoding="utf-8"),
        )
        self.assertEqual(
            publishers_openapi_document()["info"]["version"], PUBLISHERS_CONTRACT_VERSION
        )

    def test_checked_in_openapi_artifact_matches_generator(self):
        self.assertTrue(_ARTIFACT.is_file(), "commit docs/api/openapi-publishers.json")
        on_disk = json.loads(_ARTIFACT.read_text(encoding="utf-8"))
        self.assertEqual(on_disk, publishers_openapi_document())

    def test_openapi_core_accepts_the_document(self):
        document = publishers_openapi_document()
        OpenAPI.from_dict(document)
        self.assertEqual(
            set(document["paths"]),
            {
                "/api/publishers",
                "/api/publishers/{publisher_id}",
                "/api/publishers/{publisher_id}/aliases",
            },
        )

    def test_response_models_refuse_unknown_keys_and_bad_counts(self):
        row = {"id": "R-1", "name": "OUP", "aliases": ["Oxford UP"], "work_count": 2}
        self.assertEqual(dump_response(PublisherInUse, [row]), [row])
        with self.assertRaises(ValidationError):
            PublisherInUse.model_validate({**row, "created_at": "2026"})
        with self.assertRaises(ValidationError):
            PublisherInUse.model_validate({**row, "work_count": -1})
        with self.assertRaises(ValidationError):
            PublisherDeleted.model_validate({"status": "added"})


if __name__ == "__main__":
    unittest.main()
