"""Independent unit tests for the Saved Views HTTP controller.

These call ``backend.api.saved_views`` with a fake handler — no threaded
HTTP server — so the controller boundary stays testable without
``server.py`` lifecycle. The handler's request context is the actor the
test is bound to.
"""

from __future__ import annotations

import unittest

from backend.api import saved_views as saved_views_api
from tests.acting_context import current_context
from tests.pg.test_saved_views import SavedViewTestCase


class _FakeHandler:
    def __init__(self):
        self.calls = []

    @property
    def _prks_request_context(self):
        """The admitted request's context: the actor this test is bound to."""
        return current_context()

    def send_json(self, status, body, etag=None, precondition_checked=False):
        self.calls.append(
            {
                "status": status,
                "body": body,
                "etag": etag,
                "precondition_checked": precondition_checked,
            }
        )


class TestSavedViewsApiController(SavedViewTestCase):
    def setUp(self):
        super().setUp()
        self.handler = _FakeHandler()

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
                self.library,
                "/api/saved-views",
                {"name": "Industry", "search": search},
            )
        )
        self.assertEqual(self.handler.calls[-1]["status"], 201)
        vid = self.handler.calls[-1]["body"]["id"]
        self.assertTrue(vid.startswith("SV-"))

        self.handler.calls.clear()
        self.assertTrue(
            saved_views_api.handle_get(self.handler, self.library, "/api/saved-views")
        )
        self.assertEqual(self.handler.calls[-1]["status"], 200)
        self.assertTrue(any(v["id"] == vid for v in self.handler.calls[-1]["body"]))

        self.handler.calls.clear()
        self.assertTrue(
            saved_views_api.handle_get(
                self.handler, self.library, f"/api/saved-views/{vid}"
            )
        )
        self.assertEqual(self.handler.calls[-1]["status"], 200)
        self.assertEqual(self.handler.calls[-1]["body"]["id"], vid)

        self.handler.calls.clear()
        self.assertTrue(
            saved_views_api.handle_patch(
                self.handler,
                self.library,
                f"/api/saved-views/{vid}",
                {"name": "Renamed"},
            )
        )
        self.assertEqual(self.handler.calls[-1]["status"], 200)
        self.assertEqual(self.handler.calls[-1]["body"]["name"], "Renamed")

        self.handler.calls.clear()
        self.assertTrue(
            saved_views_api.handle_delete(
                self.handler, self.library, f"/api/saved-views/{vid}"
            )
        )
        self.assertEqual(self.handler.calls[-1]["status"], 200)
        self.assertEqual(self.handler.calls[-1]["body"], {"status": "deleted"})

        self.handler.calls.clear()
        self.assertTrue(
            saved_views_api.handle_get(
                self.handler, self.library, f"/api/saved-views/{vid}"
            )
        )
        self.assertEqual(self.handler.calls[-1]["status"], 404)

    def test_validation_errors_mapped(self):
        self.assertTrue(
            saved_views_api.handle_post(
                self.handler, self.library, "/api/saved-views", ["not", "object"]
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
                self.library,
                "/api/saved-views",
                {"name": "", "search": {"mode": "all", "q": "x"}},
            )
        )
        self.assertEqual(self.handler.calls[-1]["status"], 400)

        self.handler.calls.clear()
        self.assertTrue(
            saved_views_api.handle_patch(
                self.handler,
                self.library,
                "/api/saved-views/SV-missing",
                {"name": "Nope"},
            )
        )
        self.assertEqual(self.handler.calls[-1]["status"], 404)

        self.handler.calls.clear()
        self.assertTrue(
            saved_views_api.handle_patch(
                self.handler,
                self.library,
                "/api/saved-views/SV-missing",
                {},
            )
        )
        self.assertEqual(self.handler.calls[-1]["status"], 400)
        self.assertEqual(self.handler.calls[-1]["body"]["error"], "Nothing to update.")

    def test_foreign_paths_not_claimed(self):
        self.assertFalse(
            saved_views_api.handle_get(self.handler, self.library, "/api/tags")
        )
        self.assertFalse(
            saved_views_api.handle_post(self.handler, self.library, "/api/tags", {})
        )
        self.assertFalse(
            saved_views_api.handle_patch(
                self.handler, self.library, "/api/tags/T-1", {"name": "x"}
            )
        )
        self.assertFalse(
            saved_views_api.handle_delete(self.handler, self.library, "/api/tags/T-1")
        )
        self.assertEqual(self.handler.calls, [])


if __name__ == "__main__":
    unittest.main()
