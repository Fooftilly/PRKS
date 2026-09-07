"""Structural + Node regressions for the app-shell/PDF service worker (sw.js)."""
import os
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_SW = os.path.join(_FRONTEND, "sw.js")
_APP = os.path.join(_FRONTEND, "js", "app.js")
_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_sw_selftest.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendServiceWorkerTests(unittest.TestCase):
    def test_files_exist(self):
        self.assertTrue(os.path.isfile(_SW))
        self.assertTrue(os.path.isfile(_RUNNER))

    def test_sw_registered_from_app(self):
        app = _read(_APP)
        self.assertIn("'serviceWorker' in navigator", app)
        self.assertIn("navigator.serviceWorker.register('/sw.js')", app)

    def test_no_generic_api_json_caching(self):
        src = _read(_SW)
        # The only two request classes handled specially are managed PDFs and
        # navigations/static assets; everything else -- notably /api/... JSON --
        # must fall through untouched rather than being cached generically.
        self.assertIn("isManagedPdfPath(pathname)", src)
        self.assertIn("passes straight through", src)
        self.assertNotIn("caches.open('api", src)
        self.assertNotIn('caches.open("api', src)

    def test_never_queues_mutations(self):
        src = _read(_SW)
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            self.assertNotIn("'" + method + "'", src)
        self.assertIn("if (!isGetRequest(request)) return;", src)

    def test_no_indexeddb_or_persistent_domain_storage_in_sw(self):
        src = _read(_SW)
        self.assertNotIn("indexedDB", src)
        self.assertNotIn("localStorage", src)

    def test_eligibility_helpers_are_pure_and_exported(self):
        src = _read(_SW)
        for name in (
            "isGetRequest",
            "isSameOriginUrl",
            "isNavigationRequest",
            "isStaticEligiblePath",
            "isManagedPdfPath",
            "hasRangeHeader",
            "isCacheableStaticResponse",
            "isWholeFilePdfResponse",
            "shouldRetireCache",
        ):
            self.assertIn(name + ":", src, "%s must be exported for pure-function testing" % name)

    def test_node_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for service worker tests")
        proc = subprocess.run(
            [node, _RUNNER],
            cwd=_PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertIn("passed", proc.stdout)
        self.assertIn(", 0 failed", proc.stdout)
        self.assertNotIn("FAIL  ", proc.stdout)


if __name__ == "__main__":
    unittest.main()
