"""Structural + Node regressions for the disposable IndexedDB client cache (offline-store.js)."""
import os
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_STORE = os.path.join(_FRONTEND, "js", "offline-store.js")
_COORD = os.path.join(_FRONTEND, "js", "request-coordinator.js")
_INDEX = os.path.join(_FRONTEND, "index.html")
_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_offline_store_selftest.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendOfflineStoreTests(unittest.TestCase):
    def test_files_exist(self):
        self.assertTrue(os.path.isfile(_STORE))
        self.assertTrue(os.path.isfile(_RUNNER))

    def test_loaded_before_api_after_request_coordinator(self):
        html = _read(_INDEX)
        coord_at = html.find('src="/js/request-coordinator.js"')
        store_at = html.find('src="/js/offline-store.js"')
        api_at = html.find('src="/js/api.js"')
        self.assertNotEqual(coord_at, -1)
        self.assertNotEqual(store_at, -1)
        self.assertNotEqual(api_at, -1)
        self.assertLess(coord_at, store_at)
        self.assertLess(store_at, api_at)

    def test_persistence_boundary_kept_out_of_request_coordinator(self):
        coord = _read(_COORD)
        self.assertNotIn("indexedDB", coord)
        self.assertNotIn("createPrksOfflineStore", coord)

    def test_never_touches_dom_or_routing(self):
        src = _read(_STORE)
        self.assertNotIn("document.", src)
        self.assertNotIn("prksNavigate", src)
        self.assertNotRegex(src, r"\bfetch\s*\(")

    def test_envelope_shape_and_domain_oriented_stores(self):
        src = _read(_STORE)
        for store_name in ("entities", "lists", "metadata"):
            self.assertIn(store_name, src)
        self.assertIn("cachedAt", src)
        self.assertIn("sourceRevision", src)
        self.assertIn("keyPath: ['kind', 'id']", src)

    def test_kind_sweep_reuses_the_existing_compound_key_without_a_schema_bump(self):
        src = _read(_STORE)
        # Domain-level coherence sweeps a whole entity kind; the existing
        # ["kind", "id"] compound key is enough for a bounded range, so the
        # IndexedDB schema/version must not have been changed for it.
        self.assertIn("function deleteEntitiesByKind(", src)
        self.assertIn("const DB_VERSION = 1;", src)
        self.assertIn("keyPath: ['kind', 'id']", src)
        self.assertIn("openCursor", src)

    def test_node_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for offline store tests")
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
