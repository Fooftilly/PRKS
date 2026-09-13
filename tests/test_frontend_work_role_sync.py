"""Work-Person roles on the client: parity with the server's contract."""
import pathlib
import re
import subprocess
import unittest

from backend import work_role_sync

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend' / 'js'


class WorkRoleSyncFrontendTests(unittest.TestCase):
    def test_runtime_selftests(self):
        proc = subprocess.run(
            ['node', str(ROOT / 'tests' / 'browser' / 'run_work_role_sync_selftest.js')],
            cwd=ROOT, capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('checks passed', proc.stdout)

    def test_the_role_vocabulary_matches_on_both_sides(self):
        """A role the client offers and the server refuses is a link the user
        can compose and never save; the reverse is a row nothing can render."""
        source = (FRONTEND / 'work-role-state.js').read_text()
        listed = source[source.index('const ROLE_TYPES = Object.freeze(['):]
        client = re.findall(r"'([A-Za-z]+)'", listed[: listed.index(']')])
        self.assertEqual(tuple(client), work_role_sync.ROLE_TYPES)

    def test_the_credit_bound_matches_on_both_sides(self):
        source = (FRONTEND / 'work-role-state.js').read_text()
        match = re.search(r'MAX_CREDIT_NAME_BYTES = (\d+)', source)
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)), work_role_sync.MAX_CREDIT_NAME_BYTES)

    def test_the_family_is_registered_everywhere_it_must_be(self):
        from backend import sync_protocol
        families = ('ADD_WORK_PERSON_ROLE', 'REMOVE_WORK_PERSON_ROLE',
                    'SET_WORK_PERSON_ROLE_CREDIT')
        store = (FRONTEND / 'local-store.js').read_text()
        runtime = (FRONTEND / 'sync-runtime.js').read_text()
        diagnostics = (FRONTEND / 'sync-diagnostics.js').read_text()
        for family in families:
            with self.subTest(family=family):
                self.assertIn(family, sync_protocol.supported_operations())
                self.assertIn("'%s'," % family, store, 'the durable store must accept it')
                self.assertIn('%s: root.prksWorkRoleSyncHandler' % family, runtime)
                self.assertIn(family, diagnostics,
                              'diagnostics must describe and invalidate it explicitly')
        # The coordinator stays family-agnostic.
        coordinator = runtime[: runtime.index('root.createPrksSyncRuntime = createRuntime;')]
        for leaked in families + ('person_id', 'role_type'):
            self.assertNotIn(leaked, coordinator, leaked)

    def test_the_ui_does_not_enqueue_role_operations_itself(self):
        """The family writer owns coalescing, cancellation and the scope-busy
        rule. A bare enqueue from a component would leave two intents for one
        element, and the coordinator would send the superseded one."""
        for name in ('ui.js', 'components/works.js', 'components/work-cards.js',
                     'command-palette.js'):
            source = (FRONTEND / name).read_text()
            with self.subTest(module=name):
                self.assertNotIn('enqueueOperation', source)
                for family in ('ADD_WORK_PERSON_ROLE', 'REMOVE_WORK_PERSON_ROLE',
                               'SET_WORK_PERSON_ROLE_CREDIT'):
                    self.assertNotIn(family, source,
                                     'components must not name operation families')

    def test_every_route_hydrates_both_families_together(self):
        """A Work's displayed credit is composed from linked people AND
        `author_text`. A route that hydrated one map and not the other would
        render a credit built half from pending state and half from
        acknowledged state -- and would correct itself only once some other
        surface happened to read the queue."""
        app = (FRONTEND / 'app.js').read_text()
        hook = app[app.index('async function prksHydratePendingWorkMetadata('):]
        hook = hook[: hook.index('\n}')]
        self.assertIn('prksRefreshPendingWorkMetadata()', hook)
        self.assertIn('prksRefreshPendingWorkRoles()', hook)
        # It is the ONE hook every route uses; a route hydrating by hand would
        # be a second place for the two to drift apart.
        self.assertGreaterEqual(app.count('await prksHydratePendingWorkMetadata();'), 8)
        self.assertEqual(app.count('await prksRefreshPendingWorkRoles();'), 1,
                         'role hydration belongs to the shared hook only')

    def test_both_overlays_are_applied_in_one_fixed_order(self):
        """Relationships decide who is linked and recompute the flattened
        credit columns; metadata decides what `author_text` is. Applying them
        in different orders on different surfaces would show different credits
        for one Work."""
        app = (FRONTEND / 'app.js').read_text()
        for name in ('prksEffectiveBrowseRows', 'prksEffectiveWorkSummaryRows'):
            body = app[app.index('function %s(' % name):]
            body = body[: body.index('\n}')]
            with self.subTest(helper=name):
                self.assertIn('prksEffectiveWorkRows', body, 'relationships first')
        palette = (FRONTEND / 'command-palette.js').read_text()
        roles_at = palette.index('prksEffectiveWorkRoles(')
        metadata_at = palette.index('prksEffectiveWorkSync(')
        self.assertLess(roles_at, metadata_at, 'the palette composes in the same order')

    def test_the_overlay_never_decides_the_displayed_credit(self):
        """The precedence rule -- Authors, then author_text, then Editor --
        lives in the card helper. A second copy inside the overlay would drift,
        and the overlay is exactly where a pending change could silently
        reorder it."""
        source = (FRONTEND / 'work-role-state.js').read_text()
        for forbidden in ('author_text', 'Author: ', 'Editor: '):
            self.assertNotIn(forbidden, source, forbidden)
