"""Independent unit tests for the Saved Views HTTP controller.

These answer requests through the served route table
(``tests.route_calls.call_route``), with no threaded HTTP server; the
request's context is the actor the test is bound to.
"""

from __future__ import annotations

import unittest

from tests.route_calls import call_route
from tests.pg.test_saved_views import SavedViewTestCase

_SEARCH = {"mode": "advanced", "q": "culture", "tag": "", "author": "Adorno", "publisher": ""}


class TestSavedViewsApiController(SavedViewTestCase):
    def call(self, method, target, body=None):
        return call_route(self.library, method, target, body)

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

        status, body = self.call("PATCH", f"/api/saved-views/{vid}", {"base_revision": 0, "name": "Renamed"})
        self.assertEqual((status, body["name"]), (200, "Renamed"))

        self.assertEqual(self.call("DELETE", f"/api/saved-views/{vid}"), (200, {"status": "deleted"}))
        self.assertEqual(self.call("GET", f"/api/saved-views/{vid}")[0], 404)

    def test_validation_errors_mapped(self):
        self.assertEqual(self.call("POST", "/api/saved-views", ["not", "object"]),
                         (400, {"error": "JSON object body required", "code": "invalid_request"}))
        status, _ = self.call("POST", "/api/saved-views", {"name": "", "search": {"mode": "all", "q": "x"}})
        self.assertEqual(status, 400)
        self.assertEqual(self.call("PATCH", "/api/saved-views/SV-missing", {"base_revision": 0, "name": "Nope"})[0], 404)
        self.assertEqual(self.call("PATCH", "/api/saved-views/SV-missing", {"base_revision": 0}),
                         (400, {"error": "Nothing to update."}))


if __name__ == "__main__":
    unittest.main()
