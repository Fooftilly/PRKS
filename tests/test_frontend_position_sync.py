"""Positions: the client half, and its parity with the server."""
import pathlib
import re
import subprocess
import unittest

from backend import position_sync, sync_protocol

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend' / 'js'


def js_string_list(source, name):
    body = source[source.index(name):]
    body = body[: body.index(']')]
    return re.findall(r"'([a-z_]+)'", body)


class PositionSyncFrontendTests(unittest.TestCase):
    def setUp(self):
        self.store = (FRONTEND / 'local-store.js').read_text()
        self.state = (FRONTEND / 'position-state.js').read_text()
        self.backend = (ROOT / 'backend' / 'position_sync.py').read_text()

    def test_runtime_selftests(self):
        proc = subprocess.run(
            ['node', str(ROOT / 'tests' / 'browser' / 'run_position_sync_selftest.js')],
            cwd=ROOT, capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('checks passed', proc.stdout)

    def test_the_two_sides_synchronize_the_same_fields(self):
        client = js_string_list(self.store, 'const POSITION_FIELDS =')
        self.assertEqual(sorted(client), sorted(position_sync.FIELDS))
        labels = self.state[self.state.index('const LABELS = Object.freeze({'):]
        labels = labels[: labels.index('});')]
        for field in position_sync.FIELDS:
            with self.subTest(field=field):
                self.assertRegex(labels, r'\b%s:' % field)

    def test_every_family_is_registered_on_both_sides(self):
        families = {'CREATE_POSITION', 'SET_POSITION_FIELD', 'DELETE_POSITION'}
        self.assertTrue(families <= set(sync_protocol.supported_operations()))
        runtime = (FRONTEND / 'sync-runtime.js').read_text()
        diagnostics = (FRONTEND / 'sync-diagnostics.js').read_text()
        for family in families:
            with self.subTest(family=family):
                self.assertIn("'%s'" % family, self.store)
                self.assertIn('%s:' % family, runtime)
                self.assertIn(family, diagnostics)

    def test_construction_mints_a_permanent_distributed_id(self):
        at = self.store.index('function createPosition(')
        body = self.store[at: self.store.index('\n        /**', at)]
        self.assertIn("generateEntityId('P', uuid)", body)
        self.assertIn('base_revision: null', body)
        self.assertIn('is_distributed(op["entity_id"], "P")', self.backend)

    def test_the_two_fields_are_independent_not_an_aggregate(self):
        """The reading of the schema this whole domain rests on: nothing links
        the two, so an unrelated description edit must not conflict with a
        rename."""
        self.assertEqual(position_sync.FIELD_SCOPE_TYPE, 'position-field')
        self.assertEqual(sorted(position_sync.FIELDS), ['description', 'name'])
        # The scope key names the FIELD, so each carries its own revision.
        at = self.backend.index('def scope_key(')
        self.assertIn('[position_id, field]', self.backend[at: at + 300])
        # And the ordinary endpoint advances them one at a time.
        network = (ROOT / 'backend' / 'research_network.py').read_text()
        at = network.index('def update_position(')
        body = network[at: network.index('\ndef ', at + 5)]
        self.assertEqual(body.count('position_sync.set_field_on_conn(conn, pid,'), 2)

    def test_a_position_has_no_placeholder_name(self):
        at = self.store.index('function createPosition(')
        body = self.store[at: self.store.index('\n        /**', at)]
        self.assertIn('A position needs a name.', body)
        self.assertNotIn("'Untitled", body)

    def test_deletion_cancels_only_what_was_never_sent(self):
        at = self.store.index('function deletePosition(')
        body = self.store[at: self.store.index('\n        /* ----', at)]
        self.assertIn('base_revision: null', body)
        self.assertIn('const neverSent =', body)
        self.assertIn('depends_on: waitFor', body)

    def test_a_refused_deletion_restores_the_position(self):
        at = self.state.index('function pendingDeletions(')
        body = self.state[at: self.state.index('\n    }', at)]
        self.assertIn('.filter(deletionAwaitsServer)', body)
        # And the server's refusal is the canonical one, unchanged.
        at = self.backend.index('def delete_position_on_conn(')
        server = self.backend[at: self.backend.index('\ndef ', at + 5)]
        self.assertIn('argument_target_positions', server)
        self.assertIn('"POSITION_IN_USE"', server)

    def test_a_pending_rename_reaches_argument_targets_but_not_argument_ids(self):
        """An Argument may target another ARGUMENT, whose ids live in a
        different space -- renaming one of those would be renaming a stranger."""
        at = self.state.index('function applyPendingPositionNamesToTargets(')
        body = self.state[at: self.state.index('\n    }', at)]
        self.assertIn("target.type !== 'position'", body)
        app = (FRONTEND / 'app.js').read_text()
        at = app.index("case 'argument-detail': {")
        route = app[at: app.index("case 'research-graph': {", at)]
        self.assertIn('prksApplyPendingPositionNamesToTargets(', route)

    def test_a_pending_rename_reaches_the_graph_label_only(self):
        """Graph is a projection: a label is fully determined, an edge is not,
        and a node the snapshot lacks is never synthesized."""
        app = (FRONTEND / 'app.js').read_text()
        at = app.index('function prksEffectiveResearchGraphLabels(')
        body = app[at: app.index('\n}', at)]
        self.assertIn('node.record_id', body)
        self.assertIn('label: renamed.name', body)
        self.assertNotIn('push(', body)
        self.assertNotIn('edges', body)

    def test_a_pending_position_name_is_hydrated_with_the_other_overlays(self):
        app = (FRONTEND / 'app.js').read_text()
        at = app.index('async function prksHydratePendingWorkMetadata(')
        body = app[at: app.index('\n}', at)]
        self.assertIn('prksRefreshPendingPositionNames', body)

    def test_the_reconciler_fences_arguments_only_on_a_rename(self):
        """A cached Argument embeds the name of every Position it targets. A
        description edit reaches none of that."""
        runtime = (FRONTEND / 'offline-runtime.js').read_text()
        at = runtime.index('async function reconcilePositionField(')
        body = runtime[at: runtime.index('\n        /**', at)]
        self.assertIn("if (field !== 'name' || !result.changed) return true;", body)
        self.assertIn('prksOfflineMarkArgumentsChanged();', body)
        self.assertIn('patchGraphPositionLabel(id, value)', body)

    def test_no_position_surface_guards_connectivity_any_more(self):
        component = (FRONTEND / 'components' / 'positions.js').read_text()
        self.assertNotIn('positionMutationBlocked', component)
        self.assertNotIn('requires a connection to PRKS', component)
        api = (FRONTEND / 'api.js').read_text()
        for fn in ('async function createPosition(', 'async function updatePosition(',
                   'async function deletePosition('):
            at = api.index(fn)
            body = api[at: api.index('\n}', at)]
            with self.subTest(fn=fn):
                self.assertNotIn("prksRequest('/api/positions", body)
                self.assertNotIn('prksOfflineGuardMutation', body)
                self.assertNotIn('prksMarkPositionsDomainChanged', body)

    def test_the_named_refusal_reaches_diagnostics(self):
        diagnostics = (FRONTEND / 'sync-diagnostics.js').read_text()
        self.assertIn('POSITION_IN_USE', diagnostics)


if __name__ == '__main__':
    unittest.main()
