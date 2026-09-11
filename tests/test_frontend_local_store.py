"""Structural + Node regressions for the DURABLE local store (local-store.js).

These guard the architectural invariant that makes local-first safe: durable
user-owned state lives in a different IndexedDB database from the disposable
download cache, so no bug in cache clearing can reach unsynchronized work.
"""
import os
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_LOCAL = os.path.join(_FRONTEND, "js", "local-store.js")
_OFFLINE = os.path.join(_FRONTEND, "js", "offline-store.js")
_RUNTIME = os.path.join(_FRONTEND, "js", "offline-runtime.js")
_APP = os.path.join(_FRONTEND, "js", "app.js")
_INDEX = os.path.join(_FRONTEND, "index.html")
_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_local_store_selftest.js")
_DOC = os.path.join(_PROJECT_DIR, "docs", "local-first-sync.md")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _strip_comments(src: str) -> str:
    """Code only: prose legitimately names the other database to explain it."""
    import re

    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    return re.sub(r"//[^\n]*", "", src)


class FrontendLocalStoreTests(unittest.TestCase):
    def test_files_exist(self):
        self.assertTrue(os.path.isfile(_LOCAL))
        self.assertTrue(os.path.isfile(_RUNNER))
        self.assertTrue(os.path.isfile(_DOC), "the sync design document is authoritative")

    def test_durable_state_uses_a_separate_database(self):
        """The whole point: physical separation, not a naming convention."""
        local = _read(_LOCAL)
        offline = _read(_OFFLINE)
        self.assertIn("const DB_NAME = 'prks-local-v1';", local)
        self.assertIn("const DB_NAME = 'prks-offline-v1';", offline)
        self.assertNotIn("prks-offline-v1", _strip_comments(local))
        self.assertNotIn("prks-local-v1", _strip_comments(offline))

    def test_cache_clearing_cannot_reach_durable_state(self):
        """`Clear offline cache` must be incapable of deleting the outbox."""
        offline = _strip_comments(_read(_OFFLINE))
        runtime = _strip_comments(_read(_RUNTIME))
        for name, src in (("offline-store.js", offline), ("offline-runtime.js", runtime)):
            with self.subTest(module=name):
                self.assertNotIn("createPrksLocalStore", src)
                self.assertNotIn("prks-local-v1", src)
                self.assertNotIn("resetDurableLocalState", src)
        # The cache-clear entry point itself must not mention local state.
        at = runtime.index("function clearCache(")
        self.assertNotIn("Local", runtime[at: at + 800])

    def test_local_store_is_persistence_only(self):
        src = _read(_LOCAL)
        self.assertNotIn("document.", src)
        self.assertNotIn("prksNavigate", src)
        self.assertNotIn("prksRequest", src)
        self.assertNotRegex(src, r"\bfetch\s*\(")
        self.assertNotIn("prksOfflineRuntimeState", src)

    def test_operations_are_semantic_not_serialized_requests(self):
        """Synchronization must never become an HTTP replay queue."""
        src = _read(_LOCAL)
        self.assertIn("MARK_WORK_OPENED", src)
        self.assertIn("ADD_WORK_TAG", src)
        code = _strip_comments(src)
        # An envelope carrying a method/url/body would be a replayed request.
        for forbidden in ("'method'", '"method"', "'url'", '"url"'):
            self.assertNotIn(forbidden, code, forbidden)

    def test_writes_report_failure_rather_than_degrading_silently(self):
        """The opposite contract from the disposable cache, deliberately."""
        src = _read(_LOCAL)
        self.assertIn("localStoreError(", src)
        self.assertIn("prksLocalStoreCode", src)
        # A commit is oncomplete, never a request's onsuccess.
        self.assertIn("tx.oncomplete", src)
        self.assertIn("tx.onabort", src)

    def test_operation_types_are_an_explicit_allowlist(self):
        src = _read(_LOCAL)
        self.assertIn("OPERATION_TYPES.indexOf(operation) === -1", src)
        self.assertIn("Object.freeze([", src)

    def test_no_offline_mutation_was_enabled_by_this_milestone(self):
        """Milestone 2A is foundation only: PRKS stays read-only offline."""
        for name in ("app.js", "ui.js", "api.js", "components/works.js",
                     "components/tags.js", "components/folders.js",
                     "components/playlists.js", "components/concepts.js"):
            src = _read(os.path.join(_FRONTEND, "js", *name.split("/")))
            with self.subTest(module=name):
                self.assertNotIn("createPrksLocalStore", src)
                self.assertNotIn("enqueueOperation", src)

    def test_local_store_is_not_loaded_into_the_app_shell_yet(self):
        """Nothing consumes it, so it must not be in the shell manifest --
        adding it there would put an unused module on the offline boot path."""
        html = _read(_INDEX)
        self.assertNotIn("local-store.js", html)

    def test_device_id_is_random_not_derived(self):
        """A synchronization identity, never a fingerprint."""
        src = _read(_LOCAL)
        for forbidden in ("userAgent", "navigator.platform", "hostname", "screen."):
            self.assertNotIn(forbidden, src, forbidden)
        self.assertIn("randomUUID", src)

    def test_payload_and_error_sizes_are_bounded(self):
        src = _read(_LOCAL)
        self.assertIn("MAX_PAYLOAD_BYTES", src)
        self.assertIn("payload_too_large", src)
        self.assertIn("MAX_ERROR_CHARS", src)

    def test_node_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for local store tests")
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
