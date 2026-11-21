"""Citations wire contract (#41) and its checked-in OpenAPI artifact."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from openapi_core import OpenAPI
from pydantic import ValidationError

from backend.api_contract.citations import CitationsBibtex, CitationsCslJson, CitationsRequest
from backend.api_contract.openapi import CITATIONS_CONTRACT_VERSION, citations_openapi_document
from backend.domain.works.csl_format import format_csl

_ARTIFACT = Path(__file__).resolve().parents[1] / "docs" / "api" / "openapi-citations.json"


class CitationsContractTests(unittest.TestCase):
    def test_checked_in_openapi_artifact_matches_generator(self):
        self.assertTrue(_ARTIFACT.is_file(), "commit docs/api/openapi-citations.json")
        self.assertEqual(json.loads(_ARTIFACT.read_text(encoding="utf-8")), citations_openapi_document())

    def test_openapi_core_accepts_the_document(self):
        document = citations_openapi_document()
        OpenAPI.from_dict(document)
        self.assertEqual(set(document["paths"]), {"/api/citations"})
        self.assertEqual(document["info"]["version"], CITATIONS_CONTRACT_VERSION)

    def test_a_full_csl_item_fits_the_response_model(self):
        work = {"title": "T", "doc_type": "phdthesis", "author_text": "Doe, Jane", "published_date": "2020-05-02",
                "journal": "J", "publisher": "P", "location": "L", "edition": "2", "volume": "1", "issue": "3",
                "pages": "1--2", "isbn": "9", "doi": "10.1/x", "source_url": "https://e.x", "abstract": "A",
                "updated_at": "2026-11-21T10:00:00Z"}
        roles = [{"role_type": "Editor", "last_name": "Ed"}, {"role_type": "Translator", "credit_name": "Tr"}]
        items = [format_csl("W-1", work, roles, video=False), format_csl("W-2", {"author_text": "Chan"}, [], video=True)]
        body = {"format": "csl-json", "items": items, "missing": ["W-3"]}
        self.assertEqual(CitationsCslJson.model_validate(body).model_dump(mode="json", exclude_unset=True), body)
        self.assertEqual(set(items[0]) - set(CitationsCslJson.model_json_schema()["$defs"]["CslItem"]["properties"]),
                         set())

    def test_response_models_refuse_unknown_keys(self):
        with self.assertRaises(ValidationError):
            CitationsCslJson.model_validate({"format": "csl-json", "items": [{"id": "W", "type": "book", "x": 1}],
                                             "missing": []})
        with self.assertRaises(ValidationError):
            CitationsBibtex.model_validate({"format": "bibtex", "text": "", "missing": [], "items": []})
        with self.assertRaises(ValidationError):
            CitationsRequest.model_validate({"work_ids": ["W"], "format": "ris"})


if __name__ == "__main__":
    unittest.main()
