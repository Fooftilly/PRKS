"""Storage status (storage-architecture §11.1, #720): facts, wire contract and route rules."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openapi_core import OpenAPI
from openapi_core.testing import MockRequest, MockResponse

from backend.api import storage as storage_api
from backend.api.session import route_capability
from backend.api_contract.openapi import storage_status_openapi_document
from backend.domain.identity.authorization import Capability
from backend.runtime.log_safety import safe_route
from backend.storage import root_binding, status
from backend.storage.config import StorageConfig
from backend.storage.errors import StorageRootError
from backend.storage.preflight import (
    NORMALIZATION_INSENSITIVE,
    NORMALIZATION_NORMALIZED,
    NORMALIZATION_PRESERVED,
    PREFLIGHT_DIRNAME,
    probe_name_semantics,
)
from backend.storage.root_marker import MAINTENANCE_DIRNAME
from backend.web.app import App
from backend.web.http import Headers, Request, Response
from backend.web.routing import Match

_ROOT = Path(__file__).resolve().parents[1]
_ARTIFACT = _ROOT / "docs" / "api" / "openapi-storage-status.json"


def _get(app: App) -> Response:
    """``GET /api/storage-status`` through the route table."""
    found = app.routes.resolve("GET", storage_api.PATH)
    assert isinstance(found, Match)
    request = Request("GET", storage_api.PATH, "", Headers(), app)
    return found.route.handler(request)


class StatusTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="prks-status-")
        self.tmp = os.path.realpath(self._tmp.name)
        self.root = os.path.join(self.tmp, "library")
        self.bound = root_binding.open_storage_root(StorageConfig.for_testing(self.root), register=False)
        status.reset_cache()

    def tearDown(self) -> None:
        self.bound.release()
        status.reset_cache()
        self._tmp.cleanup()

    def body(self, **kwargs) -> dict:
        kwargs.setdefault("configured_root", self.root)
        kwargs.setdefault("resolved_root", self.bound.root_real)
        kwargs.setdefault("source", "env")
        kwargs.setdefault("leased", True)
        return storage_api.body(**kwargs)


class StorageStatusFactsTests(StatusTestCase):
    def test_checked_in_openapi_artifact_matches_generator(self) -> None:
        self.assertTrue(_ARTIFACT.is_file(), "commit docs/api/openapi-storage-status.json")
        self.assertEqual(json.loads(_ARTIFACT.read_text(encoding="utf-8")), storage_status_openapi_document())

    def test_the_body_validates_against_the_document(self) -> None:
        api = OpenAPI.from_dict(storage_status_openapi_document())
        request = MockRequest(host_url="http://127.0.0.1", method="get", path="/api/storage-status")
        api.validate_request(request)
        data = json.dumps(self.body(conninfo="postgresql://prks:hunter2@db:5432/prks")).encode()
        api.validate_response(request, MockResponse(data=data, status_code=200, content_type="application/json"))

    def test_identity_and_root_come_from_the_marker_and_the_binding(self) -> None:
        body = self.body()
        self.assertEqual(body["root"], {
            "configured": self.root, "resolved": self.bound.root_real, "source": "env", "editable_in_app": False,
        })
        self.assertEqual(body["identity"]["storage_root_id"], self.bound.storage_root_id)
        self.assertEqual(body["identity"]["state"], "active")
        self.assertEqual(body["identity"]["relocation_state"], "none")
        self.assertEqual(body["identity"]["backend_type"], "local")
        self.assertIsNone(body["objects"])

    def test_filesystem_facts_are_measured(self) -> None:
        fs = self.body()["filesystem"]
        self.assertGreater(fs["total_bytes"], 0)
        self.assertGreaterEqual(fs["total_bytes"], fs["free_bytes"])
        self.assertIsInstance(fs["case_sensitive"], bool)
        self.assertIn(fs["normalization"], {NORMALIZATION_PRESERVED, NORMALIZATION_INSENSITIVE, NORMALIZATION_NORMALIZED})
        self.assertIsInstance(fs["directory_fsync"], bool)
        # The probe leaves nothing behind.
        self.assertFalse(os.path.lexists(os.path.join(self.bound.root_real, MAINTENANCE_DIRNAME, PREFLIGHT_DIRNAME)))

    def test_a_root_this_process_does_not_lease_gets_no_probe_files(self) -> None:
        with patch.object(status, "probe_name_semantics") as probe:
            fs = self.body(leased=False)["filesystem"]
        probe.assert_not_called()
        self.assertIsNone(fs["case_sensitive"])
        self.assertIsNone(fs["normalization"])

    def test_the_probe_waits_for_no_opening_process(self) -> None:
        opening = root_binding.acquire_open_lock(self.bound.root_real, timeout=0.0)
        try:
            with patch.object(status, "_PROBE_LOCK_WAIT_SECONDS", 0.0):
                fs = self.body()["filesystem"]
        finally:
            opening.release()
        self.assertIsNone(fs["case_sensitive"])

    def test_network_classification(self) -> None:
        self.assertEqual(status.network_class("nfs4"), "network")
        self.assertEqual(status.network_class("fuse.sshfs"), "network")
        self.assertEqual(status.network_class("fuse.gocryptfs"), "uncertain")
        self.assertEqual(status.network_class("ext4"), "local")
        self.assertEqual(status.network_class(None), "unknown")
        with patch.object(status, "detect_filesystem_type", return_value="cifs"):
            fs = self.body()["filesystem"]
        self.assertEqual((fs["filesystem_type"], fs["network"]), ("cifs", "network"))

    def test_a_relocation_in_the_marker_is_shown(self) -> None:
        with patch.object(status, "read_marker") as read:
            read.return_value = self.bound.marker.__class__(
                storage_root_id=self.bound.storage_root_id, layout_version=1, state="fenced",
                created_at="2026-11-23T00:00:00Z", relocation={"id": "r1", "role": "source", "phase": "fenced"},
                document={},
            )
            identity = self.body()["identity"]
        self.assertEqual((identity["state"], identity["relocation_state"]), ("fenced", "fenced"))


    def test_an_unreadable_marker_never_claims_no_move(self) -> None:
        with patch.object(status, "read_marker", side_effect=StorageRootError("marker_unreadable", "unreadable")):
            identity = self.body()["identity"]
        self.assertEqual((identity["storage_root_id"], identity["relocation_state"]), (None, "unknown"))


class NameSemanticsProbeTests(unittest.TestCase):
    def test_probe_runs_in_the_scaffold_and_removes_it(self) -> None:
        with tempfile.TemporaryDirectory(prefix="prks-probe-") as tmp:
            maintenance = os.path.join(tmp, MAINTENANCE_DIRNAME)
            os.mkdir(maintenance)
            semantics = probe_name_semantics(maintenance)
            self.assertEqual(os.listdir(maintenance), [])
            # The filesystem decides, not the OS: compare with an independent probe.
            other = os.path.join(tmp, "independent")
            os.mkdir(other)
            with open(os.path.join(other, "a"), "w", encoding="utf-8"):
                pass
            self.assertEqual(semantics.case_sensitive, not os.path.exists(os.path.join(other, "A")))
            nfc, nfd = "\u00e9", "e\u0301"
            with open(os.path.join(other, nfc), "w", encoding="utf-8"):
                pass
            listed = set(os.listdir(other))
            if nfc not in listed:
                expected = NORMALIZATION_NORMALIZED
            elif os.path.exists(os.path.join(other, nfd)):
                expected = NORMALIZATION_INSENSITIVE
            else:
                expected = NORMALIZATION_PRESERVED
            self.assertEqual(semantics.normalization, expected)


class StorageRouteTests(StatusTestCase):
    def test_only_the_owner_reads_it(self) -> None:
        self.assertIs(route_capability("GET", storage_api.PATH), Capability.ADMIN_LIBRARY)

    def test_routes_are_logged_as_themselves(self) -> None:
        for route in (storage_api.PATH, "/api/openapi/storage-status.json"):
            with self.subTest(route=route):
                self.assertEqual(safe_route(route), route)

    def test_the_answer_is_never_cached(self) -> None:
        response = _get(App(storage=StorageConfig.for_testing(self.root)))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.header("Cache-Control"), "no-store")
        body = json.loads(response.body)
        self.assertEqual(body["identity"]["storage_root_id"], self.bound.storage_root_id)

    def test_other_paths_and_an_unbound_process(self) -> None:
        self.assertNotIsInstance(App().routes.resolve("GET", "/api/storage-statuses"), Match)
        self.assertEqual(_get(App()).status, 503)

    def test_the_database_summary_never_carries_credentials(self) -> None:
        for conninfo, expected in (
            ("postgresql://prks:hunter2@db.example:5433/library", ("db.example", "5433", "library")),
            ("host=/run/postgresql dbname=prks user=prks password=hunter2", ("local socket", None, "prks")),
            (None, (None, None, None)),
        ):
            with self.subTest(conninfo=conninfo):
                summary = storage_api.database_summary(conninfo)
                self.assertEqual((summary["host"], summary["port"], summary["name"]), expected)
                self.assertNotIn("hunter2", json.dumps(summary))
                self.assertNotIn("user", summary)


if __name__ == "__main__":
    unittest.main()
