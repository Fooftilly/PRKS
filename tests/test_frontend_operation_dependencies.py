"""Operation dependencies: ordering, outcome and retention."""
import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend' / 'js'


class OperationDependencyTests(unittest.TestCase):
    def test_runtime_selftests(self):
        proc = subprocess.run(
            ['node', str(ROOT / 'tests' / 'browser' / 'run_operation_dependency_selftest.js')],
            cwd=ROOT, capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('checks passed', proc.stdout)

    def test_success_and_consumption_are_distinguished_in_both_places(self):
        """Two components decide whether a prerequisite is satisfied: the store,
        when it attaches a dependency, and the coordinator, when it decides what
        may be sent. Both must judge the OUTCOME rather than the status -- a
        terminally refused operation is marked `acknowledged` too, because
        nothing further is owed on it.
        """
        store = (FRONTEND / 'local-store.js').read_text()
        runtime = (FRONTEND / 'sync-runtime.js').read_text()
        for source, name in ((store, 'local-store.js'), (runtime, 'sync-runtime.js')):
            with self.subTest(module=name):
                at = source.index('function dependencySucceeded(')
                body = source[at: source.index('\n', source.index('return', at))]
                self.assertIn('server_result', body,
                              'the recorded result is what separates applied from refused')

    def test_the_server_refusal_is_terminal(self):
        runtime = (FRONTEND / 'sync-runtime.js').read_text()
        listed = runtime[runtime.index('const PROTOCOL_ERRORS'):]
        listed = listed[: listed.index(';')]
        self.assertIn('UNSATISFIED_DEPENDENCY', listed)

    def test_the_server_and_client_agree_on_the_refusal_code(self):
        from backend import sync_protocol  # noqa: F401
        backend = (ROOT / 'backend' / 'sync_protocol.py').read_text()
        codes = set(re.findall(r'"code": "([A-Z_]+)"', backend))
        runtime = (FRONTEND / 'sync-runtime.js').read_text()
        listed = runtime[runtime.index('const PROTOCOL_ERRORS'):]
        listed = set(re.findall(r"'([A-Z_]+)'", listed[: listed.index(';')]))
        # Every dependency-related refusal the protocol layer can emit must be
        # one the coordinator treats as terminal; otherwise it retries forever.
        for code in codes:
            if 'DEPENDENC' in code:
                with self.subTest(code=code):
                    self.assertIn(code, listed)

    def test_the_failure_walk_is_transitive_and_owned_by_the_store(self):
        """Propagation must cross more than one edge, and must be atomic.

        The coordinator is the wrong place for it: it would have to read the
        whole graph and write it back one row at a time, and whatever a partial
        pass missed is left waiting on a chain that can never complete. The
        store owns the graph, so the store owns the walk.
        """
        store = (FRONTEND / 'local-store.js').read_text()
        at = store.index('function unresolvedDependentClosure(')
        body = store[at: store.index('\n    }\n', at)]
        self.assertIn('queue.push(', body, 'the walk must descend, not stop at one edge')
        self.assertIn('seen.', body, 'and visit each operation once however many paths reach it')

        runtime = (FRONTEND / 'sync-runtime.js').read_text()
        at = runtime.index('async function blockDependents(')
        body = runtime[at: runtime.index('\n        }\n', at)]
        self.assertIn('store.markDependentsFailed(', body)
        self.assertNotIn('updateOperationSyncState', body,
                         'the coordinator must not walk the graph row by row')

    def test_resolving_a_conflict_cannot_orphan_its_dependents(self):
        """Resolution is the one place an operation leaves the graph while
        others may still name it. Because any operation may be a prerequisite
        (documented decision), both answers have to account for them: a discard
        settles the descendants, a reapply repoints them at the replacement.
        """
        store = (FRONTEND / 'local-store.js').read_text()
        at = store.index('function resolveConflict(')
        body = store[at: store.index('\n        }\n', at)]
        self.assertIn('unresolvedDependentClosure(', body, 'discard must settle what waited')
        self.assertIn('replacement.op_id', body, 'reapply must repoint what waited')

    def test_the_documented_decision_is_that_any_operation_may_be_a_prerequisite(self):
        """The choice is load-bearing: it is the reason conflict resolution has
        to be dependency-safe at all. Leaving it unwritten invites the opposite
        assumption and a lifecycle that quietly orphans operations.
        """
        doc = (ROOT / 'docs' / 'local-first-sync.md').read_text()
        self.assertIn('### Which operations may be prerequisites', doc)
        section = doc[doc.index('### Which operations may be prerequisites'):]
        self.assertIn('**Any operation may be a prerequisite.**', section)

    def test_a_failed_prerequisite_cannot_acquire_new_dependents(self):
        """Existence is not enough at the enqueue boundary.

        A terminally refused operation is retained exactly because something
        already depends on it, so it is the row a later operation is most
        likely to name -- and the result would be a `pending` row that can
        never become eligible and was never offered to the user as a decision.
        """
        store = (FRONTEND / 'local-store.js').read_text()
        at = store.index('async function assertDependenciesUsableIn(')
        body = store[at: store.index('\n        }\n', at)]
        self.assertIn('dependencyTerminallyFailed(', body)
        self.assertIn("'dependency_failed'", body)

        # One definition of failure, expressed as the negation of the one
        # definition of success, so the two cannot drift apart.
        at = store.index('function dependencyTerminallyFailed(')
        predicate = store[at: store.index('\n        }\n', at)]
        self.assertIn('!dependencySucceeded(row)', predicate)

    def test_the_role_writer_uses_the_stores_definition_of_a_healthy_dependency(self):
        store = (FRONTEND / 'local-store.js').read_text()
        # ONE helper, asked by every Person-scoped writer. A family that
        # re-derived "is this creation usable" would be free to disagree with
        # the enqueue boundary about it.
        at = store.index('function personCreationDependency(')
        helper = store[at: store.index('\n        }\n', at)]
        self.assertIn('dependencyTerminallyFailed(', helper,
                      'the helper must not select a refused creation')
        self.assertIn('dependencySucceeded', helper,
                      'nor name one that is already canonical')
        self.assertIn("'dependency_failed'", helper,
                      'and must refuse rather than silently drop the dependency')
        for writer in ('function saveWorkPersonRole(', 'function savePersonMetadataFields('):
            at = store.index(writer)
            body = store[at: store.index('\n        function ', at + 10)]
            with self.subTest(writer=writer):
                self.assertIn('personCreationDependency(', body)

    def test_every_role_surface_names_the_failed_dependency_explicitly(self):
        """G8: a refused prerequisite is not "could not save".

        The link itself is fine; the Person it names is what the server
        refused, and collapsing that into the generic bucket sends the user
        looking in the wrong place.
        """
        editor = (FRONTEND / 'work-role-editor.js').read_text()
        self.assertIn("'dependency_failed'", editor)
        self.assertIn("'dependency-failed'", editor)
        for name in ('ui.js', 'app.js'):
            source = (FRONTEND / name).read_text()
            with self.subTest(module=name):
                handled = source.count("'dependency-failed'")
                calls = source.count('prksSaveWorkPersonRoleDurably(')
                self.assertEqual(handled, calls,
                                 'every role-save call site must name this outcome')
