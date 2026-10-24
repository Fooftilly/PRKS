"""Files for Processing wire contract, its OpenAPI artifact, and the Vue client's lockstep keys."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from openapi_core import OpenAPI
from pydantic import ValidationError

from backend.api_contract.openapi import (
    PROCESSING_FILES_CONTRACT_VERSION,
    processing_files_openapi_document,
)
from backend.api_contract.processing_files import (
    ProcessingFile,
    ProcessingFileImported,
    ProcessingFileRole,
    ProcessingFileTag,
)

_ROOT = Path(__file__).resolve().parents[1]
_CLIENT = _ROOT / "frontend-app" / "src" / "api" / "processing-files.ts"
_ARTIFACT = _ROOT / "docs" / "api" / "openapi-processing-files.json"


def _ts_list(name: str) -> list[str]:
    text = _CLIENT.read_text(encoding="utf-8")
    match = re.search(rf"export const {name} = \[(.*?)\] as const", text, re.S)
    if match is None:
        raise AssertionError(f"missing {name} in {_CLIENT}")
    return re.findall(r"'([^']+)'", match.group(1))


def _row(**overrides):
    row = {
        "id": "PF-1",
        "rel_path": "inbox/a.pdf",
        "filename": "a.pdf",
        "folder": "inbox",
        "status": "pending",
        "last_error": None,
        "imported_work_id": None,
        "imported_at": None,
        "discovered_at": "2026-10-03 10:00:00",
        "updated_at": None,
        "exists": True,
        "title": "",
        "status_draft": "Not Started",
        "published_date": "",
        "abstract": "",
        "source_url": "",
        "author_text": "",
        "year": "",
        "publisher": "",
        "location": "",
        "edition": "",
        "journal": "",
        "volume": "",
        "issue": "",
        "pages": "",
        "isbn": "",
        "doi": "",
        "doc_type": "article",
        "private_notes": "",
        "thumb_page": None,
        "target_folder_id": "",
        "roles": [{"person_id": "P-1", "person_name": "Ada", "role_type": "Author", "order_index": 0}],
        "tags": [{"id": "T-1", "name": "logic", "color": "#6d6cf7", "created_at": None}],
    }
    row.update(overrides)
    return row


class ProcessingFilesContractTests(unittest.TestCase):
    def test_typescript_keys_match_pydantic_fields(self):
        pairs = (
            ("PROCESSING_FILE_KEYS", ProcessingFile),
            ("PROCESSING_FILE_ROLE_KEYS", ProcessingFileRole),
            ("PROCESSING_FILE_TAG_KEYS", ProcessingFileTag),
            ("PROCESSING_FILE_IMPORTED_KEYS", ProcessingFileImported),
        )
        for const_name, model in pairs:
            with self.subTest(const_name=const_name):
                self.assertEqual(set(_ts_list(const_name)), set(model.model_fields))

    def test_typescript_statuses_match_the_schema(self):
        schema = ProcessingFile.model_json_schema()
        self.assertEqual(
            set(_ts_list("PROCESSING_FILE_STATUSES")), set(schema["properties"]["status"]["enum"])
        )
        self.assertEqual(
            set(_ts_list("PROCESSING_DRAFT_STATUSES")),
            set(schema["properties"]["status_draft"]["enum"]),
        )

    def test_contract_version_matches_the_typescript_client(self):
        self.assertIn(
            f"PROCESSING_FILES_CONTRACT_VERSION = '{PROCESSING_FILES_CONTRACT_VERSION}'",
            _CLIENT.read_text(encoding="utf-8"),
        )
        self.assertEqual(
            processing_files_openapi_document()["info"]["version"],
            PROCESSING_FILES_CONTRACT_VERSION,
        )

    def test_checked_in_openapi_artifact_matches_generator(self):
        self.assertTrue(_ARTIFACT.is_file(), "commit docs/api/openapi-processing-files.json")
        on_disk = json.loads(_ARTIFACT.read_text(encoding="utf-8"))
        self.assertEqual(on_disk, processing_files_openapi_document())

    def test_openapi_core_accepts_the_document(self):
        document = processing_files_openapi_document()
        OpenAPI.from_dict(document)
        self.assertEqual(
            set(document["paths"]),
            {
                "/api/processing-files",
                "/api/processing-files/{processing_file_id}",
                "/api/processing-files/{processing_file_id}/import",
            },
        )

    def test_response_models_refuse_unknown_keys_and_bad_values(self):
        ProcessingFile.model_validate(_row())
        with self.assertRaises(ValidationError):
            ProcessingFile.model_validate(_row(abs_path="/inbox/a.pdf"))
        with self.assertRaises(ValidationError):
            ProcessingFile.model_validate(_row(status="queued"))
        with self.assertRaises(ValidationError):
            ProcessingFile.model_validate(_row(status_draft="Done"))
        with self.assertRaises(ValidationError):
            ProcessingFile.model_validate({k: v for k, v in _row().items() if k != "tags"})
        with self.assertRaises(ValidationError):
            ProcessingFileImported.model_validate({"processing_file_id": "PF-1"})


if __name__ == "__main__":
    unittest.main()
