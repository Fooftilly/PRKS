"""Arguments/Stances: client durable parity and architecture contracts."""
import pathlib
import subprocess
import unittest

from backend import argument_sync, sync_protocol

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
JS = FRONTEND / "js"


class ArgumentSyncFrontendTests(unittest.TestCase):
    def setUp(self):
        self.store = (JS / "local-store.js").read_text()
        self.state = (JS / "argument-state.js").read_text()
        self.api = (JS / "api.js").read_text()
        self.app = (JS / "app.js").read_text()
        self.component = (JS / "components" / "arguments.js").read_text()

    def test_runtime_selftest(self):
        proc = subprocess.run(
            ["node", str(ROOT / "tests/browser/run_argument_sync_selftest.js")],
            cwd=ROOT, capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("argument checks passed", proc.stdout)

    def test_all_five_families_registered_everywhere(self):
        families = {
            "CREATE_ARGUMENT", "SET_ARGUMENT_FIELD", "SET_ARGUMENT_SOURCES",
            "SET_ARGUMENT_TARGETS", "DELETE_ARGUMENT",
        }
        self.assertTrue(families <= set(sync_protocol.supported_operations()))
        runtime = (JS / "sync-runtime.js").read_text()
        diagnostics = (JS / "sync-diagnostics.js").read_text()
        for family in families:
            with self.subTest(family=family):
                self.assertIn("'%s'" % family, self.store)
                self.assertIn("%s:" % family, runtime)
                self.assertIn(family, diagnostics)

    def test_script_and_service_worker_order(self):
        html = (FRONTEND / "index.html").read_text()
        self.assertLess(html.index('/js/argument-state.js'),
                        html.index('/js/sync-runtime.js'))
        sw = (FRONTEND / "sw.js").read_text()
        self.assertIn("/js/argument-state.js", sw)

    def test_user_facing_api_uses_only_durable_writers(self):
        pairs = (
            ("async function createArgument(", "prksCreateArgumentDurably("),
            ("async function updateArgument(", "prksSaveArgumentFieldsDurably("),
            ("async function deleteArgument(", "prksDeleteArgumentDurably("),
            ("async function putArgumentSources(", "prksSetArgumentSourcesDurably("),
            ("async function putArgumentTargets(", "prksSetArgumentTargetsDurably("),
        )
        for marker, writer in pairs:
            start = self.api.index(marker)
            body = self.api[start:self.api.index("\n}", start)]
            with self.subTest(marker=marker):
                self.assertIn(writer, body)
                self.assertNotIn("prksRequest('/api/arguments", body)
                self.assertNotIn("prksResearchJson(", body)

    def test_durable_surfaces_have_no_connectivity_policy(self):
        self.assertNotIn("argumentMutationBlocked", self.component)
        self.assertNotIn("prksOfflineGuardMutation", self.component)
        self.assertNotIn("Arguments are read-only offline", self.component)
        for text in ("Creating a response requires", "Saving requires a connection",
                     "Deleting requires a connection"):
            self.assertNotIn(text, self.component)

    def test_construction_is_atomic_and_targets_are_one_aggregate(self):
        at = self.store.index("function createArgument(")
        create = self.store[at:self.store.index("\n        /**", at)]
        self.assertIn("payload: { name: name, kind: kind, main_text: mainText", create)
        self.assertIn("sources: sources, targets: targets", create)
        self.assertNotIn("SET_ARGUMENT_SOURCES", create)
        self.assertNotIn("SET_ARGUMENT_TARGETS", create)
        self.assertEqual(argument_sync.SOURCES_SCOPE_TYPE, "argument-sources")
        self.assertEqual(argument_sync.TARGETS_SCOPE_TYPE, "argument-targets")
        backend = (ROOT / "backend/argument_sync.py").read_text()
        self.assertIn("argument_target_positions", backend)
        self.assertIn("argument_target_arguments", backend)
        self.assertIn("network._replace_targets_on_conn", backend)

    def test_effective_reads_precede_kind_filter_and_server_fetch(self):
        start = self.app.index("case 'arguments': {")
        body = self.app[start:self.app.index("case 'argument-detail': {", start)]
        self.assertLess(body.index("prksDurableOperationsOrNone()"),
                        body.index("prksOfflineListFetch("))
        self.assertLess(body.index("prksEffectiveArgumentRows("),
                        body.index("prksFilterArgumentsByKind("))
        detail = self.app[self.app.index("case 'argument-detail': {"):
                          self.app.index("case 'research-graph': {")]
        self.assertLess(detail.index("prksDurableOperationsOrNone()"),
                        detail.index("prksOfflineDetailFetch("))
        self.assertIn("prksPendingCreatedArgument", detail)
        self.assertIn("prksEffectiveArgumentDetail(item, argumentOps)", detail)

    def test_graph_and_embedded_argument_names_use_record_id_overlay(self):
        at = self.app.index("function prksEffectiveResearchGraphLabels(")
        graph = self.app[at:self.app.index("\n}", at)]
        self.assertIn("node.record_id", graph)
        self.assertIn("node.type === 'argument'", graph)
        self.assertIn("prksApplyPendingArgumentNames", graph)
        self.assertNotIn("push(", graph)
        hydrate = self.app[self.app.index("async function prksHydratePendingWorkMetadata("):
                           self.app.index("\n}", self.app.index("async function prksHydratePendingWorkMetadata("))]
        self.assertIn("prksRefreshPendingArgumentNames", hydrate)
        self.assertIn("prksApplyPendingArgumentNames(item2.arguments)", self.app)
        self.assertIn("prksApplyPendingArgumentNamesToTargets", self.app)
        self.assertIn("prksApplyPendingArgumentNames(effectiveArgument.responses)", self.app)

    def test_destructive_conflict_restores_and_diagnostics_names_refusals(self):
        at = self.state.index("function pendingDeletions(")
        body = self.state[at:self.state.index("\n    }", at)]
        self.assertIn(".filter(deletionAwaitsServer)", body)
        diagnostics = (JS / "sync-diagnostics.js").read_text()
        for code in ("WORK_NOT_FOUND", "POSITION_NOT_FOUND", "TARGET_NOT_FOUND",
                     "INVALID_VERDICT", "ARGUMENT_CYCLE", "ARGUMENT_IN_USE",
                     "ARGUMENT_TARGETED", "ENTITY_NOT_FOUND", "REVISION_CONFLICT",
                     "FUTURE_REVISION", "DEPENDENCY_FAILED"):
            self.assertIn(code, diagnostics)

    def test_production_javascript_parses(self):
        for path in (JS / "argument-state.js", JS / "local-store.js",
                     JS / "offline-runtime.js", JS / "sync-runtime.js",
                     JS / "api.js", JS / "app.js",
                     JS / "components/arguments.js", JS / "sync-diagnostics.js"):
            proc = subprocess.run(["node", "--check", str(path)], cwd=ROOT,
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, str(path) + "\n" + proc.stderr)


if __name__ == "__main__":
    unittest.main()
