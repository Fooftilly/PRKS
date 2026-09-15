"""Concepts: the client half, and its parity with the server."""
import pathlib
import re
import subprocess
import unittest

from backend import concept_sync, sync_protocol

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend' / 'js'


def js_string_list(source, name):
    body = source[source.index(name):]
    body = body[: body.index(']')]
    return re.findall(r"'([a-z_]+)'", body)


class ConceptSyncFrontendTests(unittest.TestCase):
    def setUp(self):
        self.store = (FRONTEND / 'local-store.js').read_text()
        self.state = (FRONTEND / 'concept-state.js').read_text()
        self.backend = (ROOT / 'backend' / 'concept_sync.py').read_text()

    def test_runtime_selftests(self):
        proc = subprocess.run(
            ['node', str(ROOT / 'tests' / 'browser' / 'run_concept_sync_selftest.js')],
            cwd=ROOT, capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('checks passed', proc.stdout)

    def test_the_two_sides_synchronize_the_same_fields(self):
        client = js_string_list(self.store, 'const CONCEPT_FIELDS =')
        self.assertEqual(sorted(client), sorted(concept_sync.FIELDS))
        labels = self.state[self.state.index('const LABELS = Object.freeze({'):]
        labels = labels[: labels.index('});')]
        for field in concept_sync.FIELDS:
            with self.subTest(field=field):
                self.assertRegex(labels, r'\b%s:' % field)

    def test_every_family_is_registered_on_both_sides(self):
        families = {'CREATE_CONCEPT', 'SET_CONCEPT_FIELD', 'SET_CONCEPT_IDENTITY',
                    'SET_CONCEPT_PARENTS', 'DELETE_CONCEPT'}
        self.assertTrue(families <= set(sync_protocol.supported_operations()))
        runtime = (FRONTEND / 'sync-runtime.js').read_text()
        diagnostics = (FRONTEND / 'sync-diagnostics.js').read_text()
        for family in families:
            with self.subTest(family=family):
                self.assertIn("'%s'" % family, self.store)
                self.assertIn('%s:' % family, runtime)
                self.assertIn(family, diagnostics)

    def test_construction_mints_a_permanent_distributed_id(self):
        at = self.store.index('function createConcept(')
        body = self.store[at: self.store.index('\n        /**', at)]
        self.assertIn("generateEntityId('C', uuid)", body)
        self.assertIn('base_revision: null', body)
        self.assertIn('is_distributed(op["entity_id"], "C")', self.backend)

    def test_a_concept_has_no_placeholder_name(self):
        """Unlike a Folder or a Playlist, a Concept's name IS its identity, so
        inventing one would invent a key note resolution then has to honour."""
        at = self.store.index('function createConcept(')
        body = self.store[at: self.store.index('\n        /**', at)]
        self.assertIn('A concept needs a name.', body)
        self.assertNotIn("'Untitled", body)

    def test_name_and_aliases_are_one_aggregate(self):
        """Renaming keeps the old name reachable as an alias, so a rename writes
        into the set an alias edit changes. Splitting them would let each
        silently overwrite the other's half."""
        self.assertEqual(concept_sync.IDENTITY_SCOPE_TYPE, 'concept-identity')
        self.assertNotIn('name', concept_sync.FIELDS)
        # One operation carries both.
        at = self.store.index('function setConceptIdentity(')
        body = self.store[at: self.store.index('\n        /**', at)]
        self.assertIn('payload: { name: desiredName, aliases: desiredAliases }', body)
        self.assertIn("r.operation === 'SET_CONCEPT_IDENTITY'", body)
        # And the server keeps the old name when the identity moves.
        at = self.backend.index('def set_identity_on_conn(')
        server = self.backend[at: self.backend.index('\ndef ', at + 5)]
        self.assertIn('wanted.append((old_name, old_key))', server)
        # Both API wrappers reach the one family.
        api = (FRONTEND / 'api.js').read_text()
        for fn in ('async function updateConcept(', 'async function putConceptAliases('):
            at = api.index(fn)
            self.assertIn('prksSetConceptIdentityDurably(', api[at: api.index('\n}', at)])

    def test_identity_is_not_converged_by_an_equal_name(self):
        """Two devices that gave one Concept different alias sets made two
        claims about what it IS; the names agreeing says nothing."""
        at = self.backend.index('def apply_identity(')
        body = self.backend[at: self.backend.index('\ndef ', at + 5)]
        self.assertIn('if base < revision:', body)
        self.assertNotIn('and current', body.split('if base < revision:')[1].split('\n')[0])

    def test_parents_are_one_aggregate_converged_on_the_set(self):
        """A SET: two devices that chose the same parents made the same
        decision, whatever order they wrote them in."""
        self.assertEqual(concept_sync.PARENTS_SCOPE_TYPE, 'concept-parents')
        at = self.store.index('function setConceptParents(')
        body = self.store[at: self.store.index('\n        /**', at)]
        self.assertIn('const sameSet = list =>', body)
        self.assertIn('if (sameSet(observed.parent_ids)) { setResult(null); return; }', body)
        at = self.backend.index('def apply_parents(')
        server = self.backend[at: self.backend.index('\ndef ', at + 5)]
        self.assertIn('set(desired) != set(present)', server)

    def test_a_hierarchy_edge_advances_both_ends(self):
        """A parent renders its children from the same table, so a device
        holding that page has to be able to discover it was overtaken."""
        at = self.backend.index('def set_parents_on_conn(')
        body = self.backend[at: self.backend.index('\ndef ', at + 5)]
        self.assertIn('for pid in set(before) ^ set(after):', body)
        self.assertIn('_advance(conn, PARENTS_SCOPE_TYPE, pid)', body)

    def test_a_parent_created_here_is_waited_for(self):
        at = self.store.index('function setConceptParents(')
        body = self.store[at: self.store.index('\n        /**', at)]
        self.assertIn('conceptCreationDependency(rows, pid,', body)
        self.assertIn('depends_on: waitFor', body)

    def test_deletion_cancels_only_what_was_never_sent(self):
        at = self.store.index('function deleteConcept(')
        body = self.store[at: self.store.index('\n        /* ----', at)]
        self.assertIn('base_revision: null', body)
        self.assertIn('const neverSent =', body)
        self.assertIn('depends_on: waitFor', body)
        # A Concept given to another as a PARENT counts as naming it.
        at = self.store.index('function operationsNamingConcept(')
        naming = self.store[at: self.store.index('\n    }', at)]
        self.assertIn("row.operation === 'SET_CONCEPT_PARENTS'", naming)

    def test_the_in_use_protection_stays_canonical(self):
        at = self.backend.index('def delete_concept_on_conn(')
        body = self.backend[at: self.backend.index('\ndef ', at + 5)]
        self.assertIn('_canonical_notes_reference_concept', body)
        self.assertIn('"CONCEPT_IN_USE"', body)

    def test_the_base_is_acknowledged_and_unknown_is_not_empty(self):
        at = self.state.index('async function acknowledgedConceptBase(')
        body = self.state[at: self.state.index('\n    }', at)]
        self.assertIn('return newConceptState(conceptId, catalogRowFromOp(creating))', body)
        self.assertIn('catch (_e) { return null; }', body)

    def test_every_named_refusal_reaches_diagnostics(self):
        """"Needs a decision" with no reason is a stranded user."""
        diagnostics = (FRONTEND / 'sync-diagnostics.js').read_text()
        for code in ('CONCEPT_EXISTS', 'AMBIGUOUS_CONCEPT', 'ALIAS_CONFLICT',
                     'CONCEPT_CYCLE', 'CONCEPT_IN_USE'):
            with self.subTest(code=code):
                self.assertIn(code, diagnostics)
        # PARENT_NOT_FOUND is shared with Person Groups, whose wording is wrong
        # for a Concept, so it is answered per family.
        at = diagnostics.index('function conflictDetail(')
        body = diagnostics[at: diagnostics.index('\n    }', at)]
        self.assertIn("result.code === 'PARENT_NOT_FOUND' && op.entity_type === 'concept'", body)
        self.assertIn("op.operation === 'SET_CONCEPT_PARENTS'", body)

    def test_no_concept_surface_guards_connectivity_any_more(self):
        component = (FRONTEND / 'components' / 'concepts.js').read_text()
        self.assertNotIn('conceptMutationBlocked', component)
        self.assertNotIn('requires a connection to PRKS', component)
        api = (FRONTEND / 'api.js').read_text()
        for fn in ('async function createConcept(', 'async function updateConcept(',
                   'async function deleteConcept(', 'async function putConceptParents(',
                   'async function putConceptAliases('):
            at = api.index(fn)
            body = api[at: api.index('\n}', at)]
            with self.subTest(fn=fn):
                self.assertNotIn("prksRequest('/api/concepts", body)
                self.assertNotIn('prksOfflineGuardMutation', body)

    def test_a_pending_concept_name_is_hydrated_with_the_other_overlays(self):
        app = (FRONTEND / 'app.js').read_text()
        at = app.index('async function prksHydratePendingWorkMetadata(')
        body = app[at: app.index('\n}', at)]
        self.assertIn('prksRefreshPendingConceptNames', body)

    def test_a_graph_node_is_patched_never_invented(self):
        """Graph is a projection. A Concept the snapshot does not contain is one
        the server did not put there."""
        runtime = (FRONTEND / 'offline-runtime.js').read_text()
        at = runtime.index('async function patchGraphConceptLabel(')
        body = runtime[at: runtime.index('\n        }', at)]
        self.assertIn('if (!snapshot || !Array.isArray(snapshot.nodes)) return null;', body)
        self.assertIn("node.type !== 'concept'", body)
        # Matched on `record_id`, never the namespaced `id` -- the projection
        # holds several record types in one node list.
        self.assertIn('node.record_id !== conceptId', body)
        self.assertNotIn('push(', body)
        # A creation cannot patch a snapshot computed before it existed.
        at = runtime.index('async function reconcileCreatedConcept(')
        create = runtime[at: runtime.index('\n        /**', at)]
        self.assertIn('prksOfflineMarkResearchGraphCoreChanged()', create)
        self.assertNotIn('patchGraphConceptLabel', create)


if __name__ == '__main__':
    unittest.main()
