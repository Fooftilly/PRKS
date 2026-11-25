"""Independent unit tests for the Saved Views HTTP controller.

These answer requests through the served route table
(``tests.library_adapter.call_route``), with no threaded HTTP server. They
run on PostgreSQL (``tests/backend_parity.py``), the request's context being
the actor the test is bound to.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.storage.config import StorageConfig
from tests.backend_parity import BackendParityTestCase, parametrize_backends
from tests.library_adapter import call_route

_SEARCH = {"mode": "advanced", "q": "culture", "tag": "", "author": "Adorno", "publisher": ""}


@parametrize_backends
class TestSavedViewsApiController(BackendParityTestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="prks-api-saved-views-")
        self.storage = StorageConfig.for_testing(self._tmpdir)
        os.makedirs(self.storage.pdfs_dir, exist_ok=True)
        self.db = self.open_library(self.storage)

    def tearDown(self):
        if getattr(self, "_tmpdir", None):
            shutil.rmtree(self._tmpdir, ignore_errors=True)

    def call(self, method, target, body=None):
        return call_route(self.db, method, target, body)

    def test_crud_via_controller(self):
        status, body = self.call("POST", "/api/saved-views", {"name": "Industry", "search": _SEARCH})
        self.assertEqual(status, 201)
        vid = body["id"]
        self.assertTrue(vid.startswith("SV-"))

        status, body = self.call("GET", "/api/saved-views")
        self.assertEqual(status, 200)
        self.assertTrue(any(v["id"] == vid for v in body))

        status, body = self.call("GET", f"/api/saved-views/{vid}")
        self.assertEqual((status, body["id"]), (200, vid))
        # The id segment is percent-decoded once.
        status, body = self.call("GET", f"/api/saved-views/{vid.replace('-', '%2D', 1)}")
        self.assertEqual((status, body["id"]), (200, vid))

        status, body = self.call("PATCH", f"/api/saved-views/{vid}", {"name": "Renamed"})
        self.assertEqual((status, body["name"]), (200, "Renamed"))

        self.assertEqual(self.call("DELETE", f"/api/saved-views/{vid}"), (200, {"status": "deleted"}))
        self.assertEqual(self.call("GET", f"/api/saved-views/{vid}")[0], 404)

    def test_validation_errors_mapped(self):
        self.assertEqual(self.call("POST", "/api/saved-views", ["not", "object"]),
                         (400, {"error": "JSON object body required", "code": "invalid_request"}))
        status, _ = self.call("POST", "/api/saved-views", {"name": "", "search": {"mode": "all", "q": "x"}})
        self.assertEqual(status, 400)
        self.assertEqual(self.call("PATCH", "/api/saved-views/SV-missing", {"name": "Nope"})[0], 404)
        self.assertEqual(self.call("PATCH", "/api/saved-views/SV-missing", {}),
                         (400, {"error": "Nothing to update."}))


if __name__ == "__main__":
    unittest.main()
