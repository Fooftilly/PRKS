"""Independent unit tests for the Saved Views HTTP controller.

These call ``backend.api.saved_views`` with a fake handler — no threaded
HTTP server — so the controller boundary stays testable without
``server.py`` lifecycle.
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

from backend.api import saved_views as saved_views_api
from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig

_SCHEMA_PATH = os.path.join(_PROJECT_DIR, "backend", "db_schema.sql")


class _FakeHandler:
    def __init__(self):
        self.calls = []

    def send_json(self, status, body, etag=None, precondition_checked=False):
        self.calls.append(
            {
                "status": status,
                "body": body,
                "etag": etag,
                "precondition_checked": precondition_checked,
            }
        )


class TestSavedViewsApiController(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="prks-api-saved-views-")
        self.storage = StorageConfig.for_testing(self._tmpdir)
        os.makedirs(self.storage.pdfs_dir, exist_ok=True)
        self.db = PRKSDatabase(storage=self.storage, schema_path=_SCHEMA_PATH)
        self.handler = _FakeHandler()

    def tearDown(self):
        if getattr(self, "_tmpdir", None):
            shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_path_helpers(self):
        self.assertTrue(saved_views_api.handles("/api/saved-views"))
        self.assertTrue(saved_views_api.handles("/api/saved-views/SV-1"))
        self.assertFalse(saved_views_api.handles("/api/tags"))
        self.assertFalse(saved_views_api.handles("/api/saved-views/SV-1/extra"))
        self.assertEqual(saved_views_api.item_id("/api/saved-views/SV%2D1"), "SV-1")
        self.assertIsNone(saved_views_api.item_id("/api/saved-views"))

    def test_crud_via_controller(self):
        search = {
            "mode": "advanced",
            "q": "culture",
            "tag": "",
            "author": "Adorno",
            "publisher": "",
        }
        self.assertTrue(
            saved_views_api.handle_post(
                self.handler,
                self.db,
                "/api/saved-views",
                {"name": "Industry", "search": search},
            )
        )
        self.assertEqual(self.handler.calls[-1]["status"], 201)
        vid = self.handler.calls[-1]["body"]["id"]
        self.assertTrue(vid.startswith("SV-"))

        self.handler.calls.clear()
        self.assertTrue(
            saved_views_api.handle_get(self.handler, self.db, "/api/saved-views")
        )
        self.assertEqual(self.handler.calls[-1]["status"], 200)
        self.assertTrue(any(v["id"] == vid for v in self.handler.calls[-1]["body"]))

        self.handler.calls.clear()
        self.assertTrue(
            saved_views_api.handle_get(
                self.handler, self.db, f"/api/saved-views/{vid}"
            )
        )
        self.assertEqual(self.handler.calls[-1]["status"], 200)
        self.assertEqual(self.handler.calls[-1]["body"]["id"], vid)

        self.handler.calls.clear()
        self.assertTrue(
            saved_views_api.handle_patch(
                self.handler,
                self.db,
                f"/api/saved-views/{vid}",
                {"name": "Renamed"},
            )
        )
        self.assertEqual(self.handler.calls[-1]["status"], 200)
        self.assertEqual(self.handler.calls[-1]["body"]["name"], "Renamed")

        self.handler.calls.clear()
        self.assertTrue(
            saved_views_api.handle_delete(
                self.handler, self.db, f"/api/saved-views/{vid}"
            )
        )
        self.assertEqual(self.handler.calls[-1]["status"], 200)
        self.assertEqual(self.handler.calls[-1]["body"], {"status": "deleted"})

        self.handler.calls.clear()
        self.assertTrue(
            saved_views_api.handle_get(
                self.handler, self.db, f"/api/saved-views/{vid}"
            )
        )
        self.assertEqual(self.handler.calls[-1]["status"], 404)

    def test_validation_errors_mapped(self):
        self.assertTrue(
            saved_views_api.handle_post(
                self.handler, self.db, "/api/saved-views", ["not", "object"]
            )
        )
        self.assertEqual(self.handler.calls[-1]["status"], 400)
        self.assertEqual(
            self.handler.calls[-1]["body"]["error"], "JSON object body required"
        )

        self.handler.calls.clear()
        self.assertTrue(
            saved_views_api.handle_post(
                self.handler,
                self.db,
                "/api/saved-views",
                {"name": "", "search": {"mode": "all", "q": "x"}},
            )
        )
        self.assertEqual(self.handler.calls[-1]["status"], 400)

        self.handler.calls.clear()
        self.assertTrue(
            saved_views_api.handle_patch(
                self.handler,
                self.db,
                "/api/saved-views/SV-missing",
                {"name": "Nope"},
            )
        )
        self.assertEqual(self.handler.calls[-1]["status"], 404)

        self.handler.calls.clear()
        self.assertTrue(
            saved_views_api.handle_patch(
                self.handler,
                self.db,
                "/api/saved-views/SV-missing",
                {},
            )
        )
        self.assertEqual(self.handler.calls[-1]["status"], 400)
        self.assertEqual(self.handler.calls[-1]["body"]["error"], "Nothing to update.")

    def test_foreign_paths_not_claimed(self):
        self.assertFalse(
            saved_views_api.handle_get(self.handler, self.db, "/api/tags")
        )
        self.assertFalse(
            saved_views_api.handle_post(self.handler, self.db, "/api/tags", {})
        )
        self.assertFalse(
            saved_views_api.handle_patch(
                self.handler, self.db, "/api/tags/T-1", {"name": "x"}
            )
        )
        self.assertFalse(
            saved_views_api.handle_delete(self.handler, self.db, "/api/tags/T-1")
        )
        self.assertEqual(self.handler.calls, [])


if __name__ == "__main__":
    unittest.main()
