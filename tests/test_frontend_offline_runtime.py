"""Structural + Node regressions for the offline connectivity/read-through/
mutation-guard runtime (offline-runtime.js)."""
import os
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_RUNTIME = os.path.join(_FRONTEND, "js", "offline-runtime.js")
_STORE = os.path.join(_FRONTEND, "js", "offline-store.js")
_COORD = os.path.join(_FRONTEND, "js", "request-coordinator.js")
_INDEX = os.path.join(_FRONTEND, "index.html")
_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_offline_runtime_selftest.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendOfflineRuntimeTests(unittest.TestCase):
    def test_files_exist(self):
        self.assertTrue(os.path.isfile(_RUNTIME))
        self.assertTrue(os.path.isfile(_RUNNER))

    def test_loaded_before_api_after_offline_store(self):
        html = _read(_INDEX)
        store_at = html.find('src="/js/offline-store.js"')
        runtime_at = html.find('src="/js/offline-runtime.js"')
        api_at = html.find('src="/js/api.js"')
        self.assertNotEqual(store_at, -1)
        self.assertNotEqual(runtime_at, -1)
        self.assertNotEqual(api_at, -1)
        self.assertLess(store_at, runtime_at)
        self.assertLess(runtime_at, api_at)

    def test_request_coordinator_stays_memory_only(self):
        coord = _read(_COORD)
        self.assertNotIn("offline-runtime", coord)
        self.assertNotIn("createPrksOfflineRuntime", coord)
        self.assertNotIn("indexedDB", coord)

    def test_no_canonical_persistence_in_runtime(self):
        src = _read(_RUNTIME)
        # All persistence is delegated to offline-store.js; the runtime holds
        # no canonical domain data of its own.
        self.assertNotIn("indexedDB.open", src)
        self.assertIn("createPrksOfflineStore", src)

    def test_navigator_online_is_a_hint_not_authoritative(self):
        src = _read(_RUNTIME)
        # navigator 'online' event only ever triggers a real probe request; it
        # must never directly flip state to STATE_ONLINE by itself.
        online_listener = src.find("addEventListener('online'")
        self.assertNotEqual(online_listener, -1)
        snippet = src[online_listener : online_listener + 300]
        self.assertIn("runProbe()", snippet)
        self.assertNotIn("setState(STATE_ONLINE)", snippet)

    def test_mutation_guard_never_queues(self):
        src = _read(_RUNTIME)
        self.assertIn("This change requires a connection to PRKS.", src)
        # The module documents that it deliberately has no outbox (Phase 1), it
        # must never actually implement queuing/persisting a blocked mutation.
        self.assertIn("no offline mutation outbox", src)
        self.assertNotIn("queueMutation", src)
        self.assertNotIn("pendingMutation", src)

    def test_node_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for offline runtime tests")
        proc = subprocess.run(
            [node, _RUNNER],
            cwd=_PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertIn("passed", proc.stdout)
        self.assertIn(", 0 failed", proc.stdout)
        self.assertNotIn("FAIL  ", proc.stdout)


if __name__ == "__main__":
    unittest.main()
