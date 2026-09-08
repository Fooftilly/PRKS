"""Structural + Node regressions for the offline connectivity/read-through/
mutation-guard runtime (offline-runtime.js)."""
import os
import re
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
        self.assertNotIn("createPrksOfflineRuntime", coord)
        self.assertNotIn("indexedDB", coord)
        self.assertNotIn("createPrksOfflineStore", coord)
        # Transport-health signalling is allowed via dynamic lookups; it must
        # not import or construct the offline runtime/store.
        self.assertIn("root.prksOfflineNoteRequestSuccess", coord)
        self.assertIn("root.prksOfflineNoteRequestFailure", coord)

    def test_no_canonical_persistence_in_runtime(self):
        src = _read(_RUNTIME)
        # All persistence is delegated to offline-store.js; the runtime holds
        # no canonical domain data of its own.
        self.assertNotIn("indexedDB.open", src)
        self.assertIn("createPrksOfflineStore", src)

        def test_navigator_online_is_a_hint_not_authoritative(self):
            src = _read(_RUNTIME)
            # Both the browser's 'online' AND 'offline' events are hints only; each
            # one only ever triggers a real probe request (runProbe), never a
            # direct flip to STATE_ONLINE/STATE_OFFLINE by the listener itself.
            self.assertIn("addEventListener('online', runProbe)", src)
            self.assertIn("addEventListener('offline', runProbe)", src)

        def test_init_begins_a_real_probe_immediately(self):
            src = _read(_RUNTIME)
            init_start = src.index("function init()")
            init_body = src[init_start : init_start + 400]
            self.assertIn("runProbe()", init_body)

        def test_probe_reachability_is_any_http_response_not_just_ok(self):
            src = _read(_RUNTIME)
            probe_start = src.index("function runProbe()")
            probe_body = src[probe_start : probe_start + 1600]
            # Must react to a resolved response existing at all, not res.ok.
            self.assertIn("if (res) {", probe_body)
            self.assertNotIn("if (res && res.ok)", probe_body)

    def test_mutation_guard_never_queues(self):
        src = _read(_RUNTIME)
        self.assertIn("This change requires a connection to PRKS.", src)
        # The module documents that it deliberately has no outbox (Phase 1), it
        # must never actually implement queuing/persisting a blocked mutation.
        self.assertIn("no offline mutation outbox", src)
        self.assertNotIn("queueMutation", src)
        self.assertNotIn("pendingMutation", src)

    def test_domain_invalidation_blocks_before_the_physical_sweep(self):
        src = _read(_RUNTIME)
        start = src.index("function markDomainChanged(")
        body = src[start : start + 2200]
        block_at = body.index("blockedDomains.add(key)")
        # The generation bump and the fallback block must both be synchronous,
        # and must come before any IndexedDB work is scheduled: a stale domain
        # is ineligible the instant a canonical mutation is acknowledged.
        self.assertLess(body.index("domainGeneration.set(key, next)"), block_at)
        self.assertLess(block_at, body.index("deleteEntitiesByKind"))
        # Only the current generation may settle the domain.
        self.assertIn("if (currentDomainGeneration(key) !== next) return ok;", body)

    def test_concepts_domain_shape_is_defined_once(self):
        src = _read(_RUNTIME)
        self.assertIn("function prksOfflineMarkConceptsChanged()", src)
        self.assertIn("entityKinds: ['concept']", src)
        self.assertIn("const CONCEPTS_LIST_KEY = 'concepts:index';", src)
        # Every canonical caller shares that one definition rather than
        # re-listing the domain's kinds/list keys at each call site.
        for path in (
            os.path.join(_FRONTEND, "js", "api.js"),
            os.path.join(_FRONTEND, "js", "components", "works.js"),
        ):
            caller = _read(path)
            self.assertIn("prksOfflineMarkConceptsChanged", caller)
        for path in (
            os.path.join(_FRONTEND, "js", "api.js"),
            os.path.join(_FRONTEND, "js", "ui.js"),
            os.path.join(_FRONTEND, "js", "components", "works.js"),
            os.path.join(_FRONTEND, "js", "components", "playlists.js"),
        ):
            self.assertNotIn("entityKinds:", _read(path))

    def test_every_work_title_surface_uses_the_shared_coherence_helper(self):
        """A cached Concept detail shows the titles of the Works that mention it,
        so every canonical Work-title change must invalidate the Concepts domain
        -- not just the one in the metadata editor."""
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        start = api.index("function prksMarkWorkTitleChanged(")
        body = api[start : start + 700]
        self.assertIn("prksOfflineMarkEntityChanged('work', workId)", body)
        self.assertIn("prksMarkConceptsDomainChanged();", body)

        # Find every surface that PATCHes a Work title, and require each one to
        # route through that helper rather than evicting only the Work entity.
        title_patch = re.compile(r"JSON\.stringify\(\{\s*title[:,]")
        for name in ("ui.js", os.path.join("components", "playlists.js"), os.path.join("components", "works.js")):
            src = _read(os.path.join(_FRONTEND, "js", name))
            for match in title_patch.finditer(src):
                window = src[match.start() : match.start() + 1800]
                if "/api/works/" not in src[max(0, match.start() - 600) : match.start()]:
                    continue
                self.assertIn(
                    "prksMarkWorkTitleChanged",
                    window,
                    "%s PATCHes a Work title without the shared Concepts coherence hook" % name,
                )

    def test_concept_domain_invalidated_by_canonical_research_changes(self):
        works = _read(os.path.join(_FRONTEND, "js", "components", "works.js"))
        # Successful Research Notes save and successful Work deletion both change
        # canonical Work -> Concept research data.
        notes_at = works.index("function prksEnqueueWorkResearchNotesSave(")
        notes_body = works[notes_at : notes_at + 6000]
        self.assertIn("prksOfflineMarkConceptsChanged()", notes_body)
        delete_at = works.index("async function deleteWork(")
        delete_body = works[delete_at : delete_at + 2500]
        self.assertIn("prksOfflineMarkConceptsChanged()", delete_body)
        # ... and only on acknowledged success, never on a failed/aborted save.
        self.assertIn("if (ok && typeof prksOfflineMarkConceptsChanged === 'function')", notes_body)

    def test_concept_mutations_invalidate_at_the_canonical_helper_boundary(self):
        api = _read(os.path.join(_FRONTEND, "js", "api.js"))
        for fn in (
            "async function createConcept(",
            "async function updateConcept(",
            "async function deleteConcept(",
            "async function putConceptParents(",
            "async function putConceptAliases(",
        ):
            start = api.index(fn)
            body = api[start : start + 900]
            # prksResearchJson throws on a non-ok response, so reaching the
            # invalidation means the canonical mutation actually succeeded.
            self.assertIn("prksResearchJson(", body, fn)
            self.assertIn("prksMarkConceptsDomainChanged();", body, fn)
            self.assertLess(body.index("prksResearchJson("), body.index("prksMarkConceptsDomainChanged();"), fn)

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
