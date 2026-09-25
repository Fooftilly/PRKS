"""Positions typed API boundary + OpenAPI contract (#180)."""
from __future__ import annotations

import http.client
import json
import logging
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_tests import apply_isolated_test_env

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
apply_isolated_test_env(_PROJECT_DIR)

from openapi_core import OpenAPI
from openapi_core.testing import MockRequest, MockResponse

from backend.api_contract.boundary import dump_response
from backend.api_contract.errors import ApiErrorEnvelope, validation_error_envelope
from backend.api_contract.openapi import positions_openapi_document
from backend.api_contract.positions import (
    PositionCreateRequest,
    PositionDetail,
    PositionSummary,
    PositionUpdateRequest,
    parse_position_request,
)
from backend.log_safety import safe_error_type
from backend.research_index import (
    get_research_index,
    replace_research_index,
    reset_research_index,
)
from backend.storage.config import StorageConfig
from backend.text_index import get_text_index, replace_text_index, reset_text_index
import backend.server as server_module


class _CaptureServer(server_module.PRKSThreadingTCPServer):
    """Capture the live httpd so tearDownClass can shut it down."""

    last = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        type(self).last = self


def _capture_server_bind():
    """Snapshot module-global storage/index bindings for teardown restore."""
    try:
        previous_text = get_text_index()
    except RuntimeError:
        previous_text = None
    try:
        previous_research = get_research_index()
    except RuntimeError:
        previous_research = None
    return (
        server_module._bound_storage,
        server_module.pdfs_dir,
        server_module.thumbs_dir,
        server_module.processing_dir,
        server_module.db,
        server_module.text_index,
        server_module.research_index,
        previous_text,
        previous_research,
    )


def _restore_server_bind(snapshot) -> None:
    """Restore bindings published by ``bind_storage`` (incl. global indexes)."""
    (
        server_module._bound_storage,
        server_module.pdfs_dir,
        server_module.thumbs_dir,
        server_module.processing_dir,
        server_module.db,
        server_module.text_index,
        server_module.research_index,
        previous_text,
        previous_research,
    ) = snapshot
    if previous_text is None:
        reset_text_index()
    else:
        replace_text_index(previous_text)
    if previous_research is None:
        reset_research_index()
    else:
        replace_research_index(previous_research)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


def _openapi_api() -> OpenAPI:
    """openapi-core is a required test dep for this module (CI installs it)."""
    return OpenAPI.from_dict(positions_openapi_document())


def _validate_http_against_openapi(
    *,
    method: str,
    path: str,
    status: int,
    response_body: bytes,
    request_body: bytes | None = None,
    path_pattern: str | None = None,
    view_args: dict | None = None,
    check_request: bool = True,
) -> None:
    """Fail the test if openapi-core rejects the live request/response pair.

    Set ``check_request=False`` for deliberately schema-invalid bodies (wrong
    JSON types) where only the error *response* is under contract — openapi-core
    correctly refuses those requests against the request schema.
    """
    api = _openapi_api()
    req = MockRequest(
        host_url="http://127.0.0.1",
        method=method.lower(),
        path=path,
        path_pattern=path_pattern,
        view_args=view_args,
        data=request_body,
    )
    # Bodyless 304 must not claim application/json content.
    content_type = "" if status == 304 else "application/json"
    resp = MockResponse(
        data=response_body, status_code=status, content_type=content_type
    )
    if check_request:
        api.validate_request(req)
    api.validate_response(req, resp)


class PositionBoundaryUnitTests(unittest.TestCase):
    def test_create_request_rejects_non_object(self):
        model, err = parse_position_request(PositionCreateRequest, ["x"])
        self.assertIsNone(model)
        self.assertEqual(err["code"], "invalid_request")

    def test_create_request_wrong_type_name_preserves_domain_code(self):
        model, err = parse_position_request(PositionCreateRequest, {"name": 12})
        self.assertIsNone(model)
        self.assertEqual(err["code"], "invalid_name")
        self.assertEqual(err["error"], "Name must be a string.")

    def test_create_request_wrong_type_description_preserves_domain_code(self):
        model, err = parse_position_request(
            PositionCreateRequest, {"name": "ok", "description": 5}
        )
        self.assertIsNone(model)
        self.assertEqual(err["code"], "invalid_text")
        self.assertEqual(err["error"], "Text must be a string.")

    def test_create_request_passes_plain_values(self):
        model, err = parse_position_request(
            PositionCreateRequest, {"name": "Realism", "description": "note"}
        )
        self.assertIsNone(err)
        self.assertIsInstance(model.name, str)
        self.assertEqual(model.name, "Realism")
        # Domain receives plain attrs, not the model as business state.
        self.assertEqual(type(model.name), str)

    def test_update_request_tracks_which_fields_were_sent(self):
        model, err = parse_position_request(PositionUpdateRequest, {"name": "Only name"})
        self.assertIsNone(err)
        self.assertEqual(model.domain_field_kwargs(), {"name": "Only name"})

    def test_update_explicit_null_description_is_omit_sentinel(self):
        """Pre-#185: JSON null description is omit, not clear."""
        model, err = parse_position_request(
            PositionUpdateRequest, {"description": None}
        )
        self.assertIsNone(err)
        self.assertEqual(model.domain_field_kwargs(), {"description": None})

    def test_update_omitted_description_not_in_kwargs(self):
        model, err = parse_position_request(
            PositionUpdateRequest, {"name": "Keep desc"}
        )
        self.assertIsNone(err)
        self.assertNotIn("description", model.domain_field_kwargs())

    def test_update_name_with_null_description_keeps_description_key_as_none(self):
        model, err = parse_position_request(
            PositionUpdateRequest, {"name": "Renamed", "description": None}
        )
        self.assertIsNone(err)
        self.assertEqual(
            model.domain_field_kwargs(),
            {"name": "Renamed", "description": None},
        )

    def test_update_request_wrong_type_name_preserves_domain_code(self):
        model, err = parse_position_request(PositionUpdateRequest, {"name": 1})
        self.assertIsNone(model)
        self.assertEqual(err["code"], "invalid_name")
        self.assertEqual(err["error"], "Name must be a string.")

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
        delete_responses = doc["paths"]["/api/positions/{position_id}"]["delete"][
            "responses"
        ]
        self.assertIn("409", delete_responses)
        self.assertNotIn("400", delete_responses)
        self.assertIn("position_in_use", delete_responses["409"]["description"])
        sync_responses = doc["paths"]["/api/positions/{position_id}/sync-state"]["get"][
            "responses"
        ]
        self.assertIn("304", sync_responses)
        post_responses = doc["paths"]["/api/positions"]["post"]["responses"]
        patch_responses = doc["paths"]["/api/positions/{position_id}"]["patch"][
            "responses"
        ]
        for responses in (post_responses, patch_responses):
            self.assertIn("413", responses)
            self.assertIn("415", responses)

    def test_checked_in_openapi_artifact_matches_generator(self):
        artifact = Path(_PROJECT_DIR) / "docs" / "api" / "openapi-positions.json"
        self.assertTrue(artifact.is_file(), "commit docs/api/openapi-positions.json")
        on_disk = json.loads(artifact.read_text(encoding="utf-8"))
        self.assertEqual(on_disk, positions_openapi_document())

    def test_openapi_core_accepts_positions_document(self):
        api = _openapi_api()
        self.assertIsNotNone(api)


class PositionHttpContractTests(unittest.TestCase):
    """Live HTTP: behavior preserved + responses match response models / OpenAPI.

    Uses ``http.client`` (not ``urllib.request.urlopen``) so Bandit B310 does
    not flag localhost contract probes as open-scheme URL opens.
    """

    @classmethod
    def _request_full(
        cls,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict | None = None,
        timeout: float = 5.0,
    ):
        conn = http.client.HTTPConnection("127.0.0.1", cls._test_port, timeout=timeout)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            res = conn.getresponse()
            raw = res.read()
            hdrs = {k.lower(): v for k, v in res.getheaders()}
            return res.status, raw, hdrs
        finally:
            conn.close()

    @classmethod
    def _request(cls, method: str, path: str, body: bytes | None = None,
                 headers: dict | None = None, timeout: float = 5.0):
        status, raw, _hdrs = cls._request_full(
            method, path, body=body, headers=headers, timeout=timeout
        )
        return status, raw

    @classmethod
    def _wait_ready(cls, timeout_seconds=8.0):
        deadline = time.time() + timeout_seconds
        last_err = None
        while time.time() < deadline:
            try:
                status, _raw = cls._request("GET", "/api/positions", timeout=1.2)
                if status == 200:
                    return
            except (OSError, http.client.HTTPException) as e:
                last_err = e
            time.sleep(0.1)
        raise RuntimeError(f"server not ready: {last_err}")

    @classmethod
    def setUpClass(cls):
        cls._prev_bind = _capture_server_bind()
        cls._test_port = _find_free_port()
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
        _CaptureServer.last = None
        cls._server_patch = patch.object(
            server_module, "PRKSThreadingTCPServer", _CaptureServer
        )
        cls._server_patch.start()
        cls.server_thread = threading.Thread(
            target=server_module.run_server,
            args=(cls._test_port,),
            daemon=True,
        )
        cls.server_thread.start()
        cls._wait_ready()
        cls._httpd = _CaptureServer.last
        if cls._httpd is None:
            raise RuntimeError("contract test server did not register httpd")

    @classmethod
    def tearDownClass(cls):
        httpd = getattr(cls, "_httpd", None)
        if httpd is not None:
            try:
                httpd.shutdown()
            except OSError as e:
                # Best-effort teardown: keep going so storage cleanup still runs.
                logging.getLogger(__name__).warning(
                    "contract_httpd_shutdown_failed error_type=%s",
                    safe_error_type(e),
                )
            try:
                httpd.server_close()
            except OSError as e:
                logging.getLogger(__name__).warning(
                    "contract_httpd_close_failed error_type=%s",
                    safe_error_type(e),
                )
        thread = getattr(cls, "server_thread", None)
        if thread is not None:
            thread.join(5)
        patcher = getattr(cls, "_server_patch", None)
        if patcher is not None:
            patcher.stop()
        prev = getattr(cls, "_prev_bind", None)
        if prev is not None:
            _restore_server_bind(prev)
        tmpdir = getattr(cls, "_tmpdir", None)
        if tmpdir:
            # ignore_errors already swallows filesystem races; no bare except.
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _json(self, method: str, path: str, body=None, expect_status=None,
              extra_headers=None):
        headers = {"Accept": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        status, raw, hdrs = self._request_full(
            method, path, body=payload, headers=headers
        )
        parsed = json.loads(raw.decode("utf-8")) if raw else None
        if expect_status is not None:
            self.assertEqual(status, expect_status, parsed)
        return status, parsed, raw, payload, hdrs

    def test_openapi_endpoint_serves_positions_slice(self):
        status, doc, raw, _req, _hdrs = self._json(
            "GET", "/api/openapi.json", expect_status=200
        )
        self.assertEqual(status, 200)
        self.assertEqual(doc["info"]["title"], "PRKS API — Positions slice")
        self.assertEqual(doc, positions_openapi_document())

    def test_create_list_get_patch_delete_round_trip(self):
        status, created, raw_created, req_body, _ = self._json(
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
        _validate_http_against_openapi(
            method="POST",
            path="/api/positions",
            status=status,
            response_body=raw_created,
            request_body=req_body,
        )

        status, rows, raw_rows, _, _ = self._json(
            "GET", "/api/positions", expect_status=200
        )
        self.assertTrue(any(r["id"] == created["id"] for r in rows))
        for row in rows:
            PositionSummary.model_validate(row)
        _validate_http_against_openapi(
            method="GET",
            path="/api/positions",
            status=status,
            response_body=raw_rows,
        )

        detail_path = f"/api/positions/{created['id']}"
        status, detail, raw_detail, _, _ = self._json(
            "GET", detail_path, expect_status=200
        )
        PositionDetail.model_validate(detail)
        _validate_http_against_openapi(
            method="GET",
            path=detail_path,
            path_pattern="/api/positions/{position_id}",
            view_args={"position_id": created["id"]},
            status=status,
            response_body=raw_detail,
        )

        status, updated, raw_updated, patch_body, _ = self._json(
            "PATCH",
            detail_path,
            {"description": "updated"},
            expect_status=200,
        )
        self.assertEqual(updated["description"], "updated")
        self.assertEqual(updated["name"], "Typed boundary")
        _validate_http_against_openapi(
            method="PATCH",
            path=detail_path,
            path_pattern="/api/positions/{position_id}",
            view_args={"position_id": created["id"]},
            status=status,
            response_body=raw_updated,
            request_body=patch_body,
        )

        sync_path = f"/api/positions/{created['id']}/sync-state"
        status, sync, raw_sync, _, _ = self._json(
            "GET",
            sync_path,
            expect_status=200,
        )
        self.assertEqual(sync["position_id"], created["id"])
        self.assertIn("name", sync["fields"])
        self.assertIn("description", sync["fields"])
        _validate_http_against_openapi(
            method="GET",
            path=sync_path,
            path_pattern="/api/positions/{position_id}/sync-state",
            view_args={"position_id": created["id"]},
            status=status,
            response_body=raw_sync,
        )

        status, deleted, raw_deleted, _, _ = self._json(
            "DELETE", detail_path, expect_status=200
        )
        self.assertEqual(deleted, {"status": "deleted"})
        _validate_http_against_openapi(
            method="DELETE",
            path=detail_path,
            path_pattern="/api/positions/{position_id}",
            view_args={"position_id": created["id"]},
            status=status,
            response_body=raw_deleted,
        )

        status, missing, _raw, _, _ = self._json(
            "GET", detail_path, expect_status=404
        )
        self.assertEqual(missing["error"], "Position not found.")

    def test_invalid_json_object_uses_common_envelope(self):
        status, body, _raw, _, _ = self._json(
            "POST", "/api/positions", ["not", "an", "object"], expect_status=400
        )
        self.assertEqual(body["code"], "invalid_request")

    def test_domain_still_refuses_empty_name(self):
        status, body, _raw, _, _ = self._json(
            "POST", "/api/positions", {"name": "   "}, expect_status=400
        )
        # Domain rule, not Pydantic length/emptiness.
        self.assertEqual(body.get("code"), "invalid_name")

    def test_wrong_type_name_on_post_preserves_invalid_name(self):
        status, body, raw, req, _ = self._json(
            "POST", "/api/positions", {"name": 12}, expect_status=400
        )
        self.assertEqual(body["code"], "invalid_name")
        self.assertEqual(body["error"], "Name must be a string.")
        _validate_http_against_openapi(
            method="POST",
            path="/api/positions",
            status=status,
            response_body=raw,
            request_body=req,
            check_request=False,
        )

    def test_wrong_type_description_on_post_preserves_invalid_text(self):
        status, body, raw, req, _ = self._json(
            "POST",
            "/api/positions",
            {"name": "ok", "description": ["nope"]},
            expect_status=400,
        )
        self.assertEqual(body["code"], "invalid_text")
        self.assertEqual(body["error"], "Text must be a string.")
        _validate_http_against_openapi(
            method="POST",
            path="/api/positions",
            status=status,
            response_body=raw,
            request_body=req,
            check_request=False,
        )

    def test_wrong_type_name_and_description_on_patch(self):
        status, created, _raw, _, _ = self._json(
            "POST",
            "/api/positions",
            {"name": "Patch type target", "description": ""},
            expect_status=201,
        )
        path = f"/api/positions/{created['id']}"
        status, body, raw, req, _ = self._json(
            "PATCH", path, {"name": 99}, expect_status=400
        )
        self.assertEqual(body["code"], "invalid_name")
        self.assertEqual(body["error"], "Name must be a string.")
        _validate_http_against_openapi(
            method="PATCH",
            path=path,
            path_pattern="/api/positions/{position_id}",
            view_args={"position_id": created["id"]},
            status=status,
            response_body=raw,
            request_body=req,
            check_request=False,
        )
        status, body, raw, req, _ = self._json(
            "PATCH", path, {"description": True}, expect_status=400
        )
        self.assertEqual(body["code"], "invalid_text")
        self.assertEqual(body["error"], "Text must be a string.")
        _validate_http_against_openapi(
            method="PATCH",
            path=path,
            path_pattern="/api/positions/{position_id}",
            view_args={"position_id": created["id"]},
            status=status,
            response_body=raw,
            request_body=req,
            check_request=False,
        )
        self._json("DELETE", path, expect_status=200)

    def test_patch_null_description_preserves_pre_slice_omit_semantics(self):
        """Pre-#185: null description alone → nothing_to_update; with name, omit."""
        status, created, _, _, _ = self._json(
            "POST",
            "/api/positions",
            {"name": "Null desc target", "description": "keep me"},
            expect_status=201,
        )
        path = f"/api/positions/{created['id']}"
        status, body, _, _, _ = self._json(
            "PATCH", path, {"description": None}, expect_status=400
        )
        self.assertEqual(body.get("code"), "nothing_to_update")
        status, detail, _, _, _ = self._json("GET", path, expect_status=200)
        self.assertEqual(detail["description"], "keep me")

        status, updated, _, _, _ = self._json(
            "PATCH",
            path,
            {"name": "Null desc renamed", "description": None},
            expect_status=200,
        )
        self.assertEqual(updated["name"], "Null desc renamed")
        self.assertEqual(updated["description"], "keep me")
        self._json("DELETE", path, expect_status=200)

    def test_sync_state_304_when_etag_matches(self):
        status, created, _, _, _ = self._json(
            "POST",
            "/api/positions",
            {"name": "ETag position", "description": ""},
            expect_status=201,
        )
        sync_path = f"/api/positions/{created['id']}/sync-state"
        status, sync, raw, _, hdrs = self._json(
            "GET", sync_path, expect_status=200
        )
        etag = hdrs.get("etag")
        self.assertTrue(etag, hdrs)
        status, body, raw304, _, _ = self._json(
            "GET",
            sync_path,
            expect_status=304,
            extra_headers={"If-None-Match": etag},
        )
        self.assertIsNone(body)
        self.assertEqual(raw304, b"")
        _validate_http_against_openapi(
            method="GET",
            path=sync_path,
            path_pattern="/api/positions/{position_id}/sync-state",
            view_args={"position_id": created["id"]},
            status=304,
            response_body=raw304,
        )
        self._json("DELETE", f"/api/positions/{created['id']}", expect_status=200)

    def test_post_and_patch_415_and_413_documented(self):
        # 415: wrong Content-Type
        status, raw, hdrs = self._request_full(
            "POST",
            "/api/positions",
            body=b'{"name":"x"}',
            headers={
                "Accept": "application/json",
                "Content-Type": "text/plain",
            },
        )
        self.assertEqual(status, 415, raw)
        payload = json.loads(raw.decode("utf-8"))
        self.assertEqual(payload["error"], "unsupported_media_type")
        _validate_http_against_openapi(
            method="POST",
            path="/api/positions",
            status=415,
            response_body=raw,
            request_body=b'{"name":"x"}',
            check_request=False,
        )

        status, created, _, _, _ = self._json(
            "POST",
            "/api/positions",
            {"name": "Body read limits", "description": ""},
            expect_status=201,
        )
        path = f"/api/positions/{created['id']}"
        status, raw, _ = self._request_full(
            "PATCH",
            path,
            body=b'{"description":"x"}',
            headers={
                "Accept": "application/json",
                "Content-Type": "text/plain",
            },
        )
        self.assertEqual(status, 415, raw)
        _validate_http_against_openapi(
            method="PATCH",
            path=path,
            path_pattern="/api/positions/{position_id}",
            view_args={"position_id": created["id"]},
            status=415,
            response_body=raw,
            request_body=b'{"description":"x"}',
            check_request=False,
        )

        # 413: oversized Content-Length (limit patched down so we need not send 50MiB).
        oversized = json.dumps({"name": "overflow-body-name"}).encode("utf-8")
        with patch.object(server_module, "_PRKS_MAX_JSON_BODY_BYTES", 8):
            status, raw, _ = self._request_full(
                "POST",
                "/api/positions",
                body=oversized,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
        self.assertEqual(status, 413, raw)
        payload = json.loads(raw.decode("utf-8"))
        self.assertEqual(payload["error"], "request_too_large")
        _validate_http_against_openapi(
            method="POST",
            path="/api/positions",
            status=413,
            response_body=raw,
            request_body=oversized,
            check_request=False,
        )
        with patch.object(server_module, "_PRKS_MAX_JSON_BODY_BYTES", 8):
            status, raw, _ = self._request_full(
                "PATCH",
                path,
                body=oversized,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
        self.assertEqual(status, 413, raw)
        _validate_http_against_openapi(
            method="PATCH",
            path=path,
            path_pattern="/api/positions/{position_id}",
            view_args={"position_id": created["id"]},
            status=413,
            response_body=raw,
            request_body=oversized,
            check_request=False,
        )
        self._json("DELETE", path, expect_status=200)

    def test_delete_in_use_returns_409_matching_openapi(self):
        status, position, _raw, _, _ = self._json(
            "POST",
            "/api/positions",
            {"name": "Targeted claim", "description": ""},
            expect_status=201,
        )
        # Create an Argument that targets this Position so delete is refused.
        status, argument, _raw, _, _ = self._json(
            "POST",
            "/api/arguments",
            {
                "name": "Uses position",
                "kind": "argument",
                "main_text": "body",
                "targets": [
                    {
                        "type": "position",
                        "id": position["id"],
                        "verdict_id": "supports",
                    }
                ],
            },
            expect_status=201,
        )
        path = f"/api/positions/{position['id']}"
        status, body, raw, _, _ = self._json("DELETE", path, expect_status=409)
        self.assertEqual(body["code"], "position_in_use")
        _validate_http_against_openapi(
            method="DELETE",
            path=path,
            path_pattern="/api/positions/{position_id}",
            view_args={"position_id": position["id"]},
            status=status,
            response_body=raw,
        )
        # Cleanup: delete argument then position.
        self._json("DELETE", f"/api/arguments/{argument['id']}", expect_status=200)
        self._json("DELETE", path, expect_status=200)

    def test_openapi_core_validates_create_request_and_response(self):
        req_payload = {"name": "OpenAPI core check", "description": ""}
        status, created, raw, req_body, _ = self._json(
            "POST",
            "/api/positions",
            req_payload,
            expect_status=201,
        )
        _validate_http_against_openapi(
            method="POST",
            path="/api/positions",
            status=status,
            response_body=raw,
            request_body=req_body,
        )
        PositionDetail.model_validate(created)
        self._json("DELETE", f"/api/positions/{created['id']}", expect_status=200)


if __name__ == "__main__":
    unittest.main()
