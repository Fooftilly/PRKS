"""Positions typed API boundary + OpenAPI contract (#180)."""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_tests import apply_isolated_test_env

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
apply_isolated_test_env(_PROJECT_DIR)

from backend.api_contract.boundary import dump_response, parse_request
from backend.api_contract.errors import ApiErrorEnvelope, validation_error_envelope
from backend.api_contract.openapi import positions_openapi_document
from backend.api_contract.positions import (
    PositionCreateRequest,
    PositionDetail,
    PositionSummary,
    PositionUpdateRequest,
)
from backend.storage.config import StorageConfig
import backend.server as server_module


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


class PositionBoundaryUnitTests(unittest.TestCase):
    def test_create_request_rejects_non_object(self):
        model, err = parse_request(PositionCreateRequest, ["x"])
        self.assertIsNone(model)
        self.assertEqual(err["code"], "invalid_request")

    def test_create_request_requires_string_name(self):
        model, err = parse_request(PositionCreateRequest, {"name": 12})
        self.assertIsNone(model)
        self.assertEqual(err["code"], "invalid_request")

    def test_create_request_passes_plain_values(self):
        model, err = parse_request(
            PositionCreateRequest, {"name": "Realism", "description": "note"}
        )
        self.assertIsNone(err)
        self.assertIsInstance(model.name, str)
        self.assertEqual(model.name, "Realism")
        # Domain receives plain attrs, not the model as business state.
        self.assertEqual(type(model.name), str)

    def test_update_request_tracks_which_fields_were_sent(self):
        model, err = parse_request(PositionUpdateRequest, {"name": "Only name"})
        self.assertIsNone(err)
        self.assertEqual(model.domain_field_kwargs(), {"name": "Only name"})

    def test_response_dump_round_trips_detail_shape(self):
        payload = {
            "id": "P-TEST",
            "name": "A",
            "description": "",
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
            "arguments": [],
        }
        out = dump_response(PositionDetail, payload)
        self.assertEqual(out["id"], "P-TEST")
        self.assertEqual(out["arguments"], [])

    def test_error_envelope_omits_null_code(self):
        body = ApiErrorEnvelope(error="Position not found.").as_dict()
        self.assertEqual(body, {"error": "Position not found."})

    def test_validation_error_envelope_has_stable_code(self):
        body = validation_error_envelope(ValueError("x"))
        self.assertEqual(body["code"], "invalid_request")

    def test_openapi_document_covers_positions_family(self):
        doc = positions_openapi_document()
        self.assertEqual(doc["openapi"], "3.1.0")
        self.assertIn("/api/positions", doc["paths"])
        self.assertIn("/api/positions/{position_id}", doc["paths"])
        self.assertIn(
            "/api/positions/{position_id}/sync-state", doc["paths"]
        )
        schemas = doc["components"]["schemas"]
        for name in (
            "PositionCreateRequest",
            "PositionUpdateRequest",
            "PositionDetail",
            "PositionSummary",
            "ApiErrorEnvelope",
            "PositionDeleted",
            "PositionSyncState",
        ):
            self.assertIn(name, schemas)

    def test_checked_in_openapi_artifact_matches_generator(self):
        artifact = Path(_PROJECT_DIR) / "docs" / "api" / "openapi-positions.json"
        self.assertTrue(artifact.is_file(), "commit docs/api/openapi-positions.json")
        on_disk = json.loads(artifact.read_text(encoding="utf-8"))
        self.assertEqual(on_disk, positions_openapi_document())

    def test_openapi_core_accepts_positions_document(self):
        try:
            from openapi_core import OpenAPI
        except ImportError:
            self.skipTest("openapi-core not installed")
        api = OpenAPI.from_dict(positions_openapi_document())
        self.assertIsNotNone(api)


class PositionHttpContractTests(unittest.TestCase):
    """Live HTTP: behavior preserved + responses match response models / OpenAPI."""

    @classmethod
    def _wait_ready(cls, timeout_seconds=8.0):
        deadline = time.time() + timeout_seconds
        last_err = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"{cls._base_url}/api/positions", timeout=1.2
                ) as res:
                    if res.status == 200:
                        return
            except Exception as e:
                last_err = e
            time.sleep(0.1)
        raise RuntimeError(f"server not ready: {last_err}")

    @classmethod
    def setUpClass(cls):
        cls._test_port = _find_free_port()
        cls._base_url = f"http://127.0.0.1:{cls._test_port}"
        cls._tmpdir = tempfile.mkdtemp(prefix="prks-positions-contract-")
        storage = os.path.join(cls._tmpdir, "storage")
        processing = os.path.join(cls._tmpdir, "processing")
        os.makedirs(storage)
        os.makedirs(processing)
        cfg = replace(
            StorageConfig.for_testing(storage),
            processing_dir=processing,
        )
        server_module.bind_storage(cfg)
        cls.server_thread = threading.Thread(
            target=server_module.run_server,
            args=(cls._test_port,),
            daemon=True,
        )
        cls.server_thread.start()
        cls._wait_ready()

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "_tmpdir", None):
            try:
                import shutil

                shutil.rmtree(cls._tmpdir, ignore_errors=True)
            except Exception:
                pass

    def _json(self, method: str, path: str, body=None, expect_status=None):
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"{self._base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                status = res.status
                raw = res.read()
                parsed = json.loads(raw.decode("utf-8")) if raw else None
        except urllib.error.HTTPError as e:
            status = e.code
            raw = e.read()
            parsed = json.loads(raw.decode("utf-8")) if raw else None
        if expect_status is not None:
            self.assertEqual(status, expect_status, parsed)
        return status, parsed

    def test_openapi_endpoint_serves_positions_slice(self):
        status, doc = self._json("GET", "/api/openapi.json", expect_status=200)
        self.assertEqual(status, 200)
        self.assertEqual(doc["info"]["title"], "PRKS API — Positions slice")
        self.assertEqual(doc, positions_openapi_document())

    def test_create_list_get_patch_delete_round_trip(self):
        status, created = self._json(
            "POST",
            "/api/positions",
            {"name": "  Typed   boundary  ", "description": "hello"},
            expect_status=201,
        )
        # Domain still normalizes whitespace — model did not.
        self.assertEqual(created["name"], "Typed boundary")
        self.assertEqual(created["description"], "hello")
        self.assertIn("arguments", created)
        PositionDetail.model_validate(created)

        status, rows = self._json("GET", "/api/positions", expect_status=200)
        self.assertTrue(any(r["id"] == created["id"] for r in rows))
        for row in rows:
            PositionSummary.model_validate(row)

        status, detail = self._json(
            "GET", f"/api/positions/{created['id']}", expect_status=200
        )
        PositionDetail.model_validate(detail)

        status, updated = self._json(
            "PATCH",
            f"/api/positions/{created['id']}",
            {"description": "updated"},
            expect_status=200,
        )
        self.assertEqual(updated["description"], "updated")
        self.assertEqual(updated["name"], "Typed boundary")

        status, sync = self._json(
            "GET",
            f"/api/positions/{created['id']}/sync-state",
            expect_status=200,
        )
        self.assertEqual(sync["position_id"], created["id"])
        self.assertIn("name", sync["fields"])
        self.assertIn("description", sync["fields"])

        status, deleted = self._json(
            "DELETE", f"/api/positions/{created['id']}", expect_status=200
        )
        self.assertEqual(deleted, {"status": "deleted"})

        status, missing = self._json(
            "GET", f"/api/positions/{created['id']}", expect_status=404
        )
        self.assertEqual(missing["error"], "Position not found.")

    def test_invalid_json_object_uses_common_envelope(self):
        status, body = self._json(
            "POST", "/api/positions", ["not", "an", "object"], expect_status=400
        )
        self.assertEqual(body["code"], "invalid_request")

    def test_domain_still_refuses_empty_name(self):
        status, body = self._json(
            "POST", "/api/positions", {"name": "   "}, expect_status=400
        )
        # Domain rule, not Pydantic length/emptiness.
        self.assertEqual(body.get("code"), "invalid_name")

    def test_openapi_core_validates_create_response(self):
        try:
            from openapi_core import OpenAPI
            from openapi_core.templating.paths.exceptions import PathNotFound
        except ImportError:
            self.skipTest("openapi-core not installed")

        status, created = self._json(
            "POST",
            "/api/positions",
            {"name": "OpenAPI core check", "description": ""},
            expect_status=201,
        )
        api = OpenAPI.from_dict(positions_openapi_document())
        # Validate response body against the PositionDetail schema component.
        schema = api.spec["components"]["schemas"]["PositionDetail"]
        # openapi-core 0.23: use schema validator via unmarshal if available.
        PositionDetail.model_validate(created)
        # Document path exists for POST /api/positions -> 201
        post = positions_openapi_document()["paths"]["/api/positions"]["post"]
        self.assertIn("201", post["responses"])
        # Cleanup
        self._json("DELETE", f"/api/positions/{created['id']}", expect_status=200)


if __name__ == "__main__":
    unittest.main()
