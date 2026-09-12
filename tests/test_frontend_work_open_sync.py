import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend' / 'js'


class WorkOpenSyncFrontendTests(unittest.TestCase):
    def test_runtime_selftests(self):
        result = subprocess.run(['node', str(ROOT / 'tests/browser/run_work_open_sync_selftest.js')],
                                cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_recent_row_validators_exist_where_the_overlay_expects_them(self):
        """work-open-state.js guards on the browse-row validators rather than
        trusting a cached list, so those names are part of its contract."""
        app = (FRONTEND / 'app.js').read_text()
        for name in ('function prksIsRecentRowShape(', 'function prksIsRecentIndexShape('):
            self.assertIn(name, app)
        module = (FRONTEND / 'work-open-state.js').read_text()
        self.assertIn('root.prksIsRecentRowShape', module)
        runtime = (FRONTEND / 'offline-runtime.js').read_text()
        self.assertIn('root.prksIsRecentIndexShape', runtime)

    def test_the_coordinator_owns_no_family_specific_meaning(self):
        """Transport, claiming, backoff and retirement are shared; what a
        result MEANS belongs to the family handler. A code name leaking back
        into the coordinator is the start of the switch statement this
        milestone exists to avoid."""
        source = (FRONTEND / 'sync-runtime.js').read_text()
        # The reusable coordinator, excluding the bootstrap that wires PRKS's
        # own registry -- naming the families there is the whole point.
        coordinator = source[: source.index('root.createPrksSyncRuntime = createRuntime;')]
        for leaked in ('ADD_WORK_TAG', 'REMOVE_WORK_TAG', 'MARK_WORK_OPENED', 'TAG_MERGED',
                       'TAG_DELETED', 'REVISION_CONFLICT', 'FUTURE_REVISION', 'tag_id',
                       'recent_item', 'effective_opened_at', 'prksOfflineReconcile'):
            self.assertNotIn(leaked, coordinator, leaked)
        registry = source[source.index('handlers: {'): source.index('lock: root.navigator.locks')]
        self.assertEqual(sorted(name for name in ('ADD_WORK_TAG', 'REMOVE_WORK_TAG', 'MARK_WORK_OPENED')
                                if name in registry),
                         ['ADD_WORK_TAG', 'MARK_WORK_OPENED', 'REMOVE_WORK_TAG'])

    def test_semantic_transport_stays_with_the_coordinator(self):
        for path in FRONTEND.rglob('*.js'):
            if path.name != 'sync-runtime.js':
                self.assertNotIn('/api/sync/operations', path.read_text(), str(path))

    def test_pending_open_events_never_enter_the_disposable_cache(self):
        """The overlay is computed at render time. Writing pending intent into
        recent:index would make an unsynchronized event indistinguishable from
        acknowledged server state, and Clear offline cache would erase it."""
        module = (FRONTEND / 'work-open-state.js').read_text()
        at = module.index('function effectiveRecent(')
        body = module[at: module.index('function mergeRecentOpen(', at)]
        for forbidden in ('putList', 'cacheList', 'prksOfflineCache'):
            self.assertNotIn(forbidden, body, forbidden)
