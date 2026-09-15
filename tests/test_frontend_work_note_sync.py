"""Work notes on the client: whole-document aggregate family's selftests."""
import pathlib
import subprocess
import tempfile
import unittest

from backend import work_note_sync

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend' / 'js'


class WorkNoteSyncFrontendTests(unittest.TestCase):
    def test_runtime_selftests(self):
        result = subprocess.run(
            ['node', str(ROOT / 'tests/browser/run_work_note_sync_selftest.js')],
            cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Work note checks passed', result.stdout)

    def test_byte_limits_match_on_both_sides(self):
        js = """
        require(process.argv[1] + '/frontend/js/local-store.js');
        require(process.argv[1] + '/frontend/js/work-notes-state.js');
        process.stdout.write(JSON.stringify({
            storeResearch: globalThis.PRKS_LOCAL_WORK_RESEARCH_NOTE_BYTES,
            storePrivate: globalThis.PRKS_LOCAL_WORK_PRIVATE_NOTE_BYTES,
            stateResearch: globalThis.PRKS_MAX_RESEARCH_NOTE_BYTES,
            statePrivate: globalThis.PRKS_MAX_PRIVATE_NOTE_BYTES,
        }));
        """
        proc = subprocess.run(['node', '-e', js, str(ROOT)],
                              cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        import json
        client = json.loads(proc.stdout)
        self.assertEqual(client['storeResearch'], work_note_sync.MAX_RESEARCH_NOTE_UTF8_BYTES)
        self.assertEqual(client['storePrivate'], work_note_sync.MAX_PRIVATE_NOTE_UTF8_BYTES)
        self.assertEqual(client['stateResearch'], work_note_sync.MAX_RESEARCH_NOTE_UTF8_BYTES)
        self.assertEqual(client['statePrivate'], work_note_sync.MAX_PRIVATE_NOTE_UTF8_BYTES)

    def test_the_family_is_registered_everywhere_it_must_be(self):
        from backend import sync_protocol
        families = ('SET_WORK_RESEARCH_NOTE', 'SET_WORK_PRIVATE_NOTE')
        store = (FRONTEND / 'local-store.js').read_text()
        runtime = (FRONTEND / 'sync-runtime.js').read_text()
        diagnostics = (FRONTEND / 'sync-diagnostics.js').read_text()
        for family in families:
            with self.subTest(family=family):
                self.assertIn(family, sync_protocol.supported_operations())
                self.assertIn("'%s'," % family, store)
                self.assertIn('%s: root.prksNoteSyncHandler' % family, runtime)
                self.assertIn(family, diagnostics)
        coordinator = runtime[: runtime.index('root.createPrksSyncRuntime = createRuntime;')]
        for leaked in families + ('text_content', 'private_notes', 'work-notes-state'):
            self.assertNotIn(leaked, coordinator, leaked)

    def test_script_and_service_worker_order(self):
        html = (ROOT / 'frontend' / 'index.html').read_text()
        self.assertLess(html.index('/js/work-notes-state.js'),
                        html.index('/js/sync-runtime.js'))
        sw = (ROOT / 'frontend' / 'sw.js').read_text()
        self.assertIn('/js/work-notes-state.js', sw)

    def test_production_javascript_parses(self):
        for path in (FRONTEND / 'work-notes-state.js', FRONTEND / 'local-store.js',
                     FRONTEND / 'offline-runtime.js', FRONTEND / 'sync-runtime.js',
                     FRONTEND / 'sync-diagnostics.js'):
            proc = subprocess.run(['node', '--check', str(path)], cwd=ROOT,
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, str(path) + '\n' + proc.stderr)

    def test_acknowledged_base_is_explicit_and_not_derived_from_effective_work(self):
        source = (FRONTEND / 'work-notes-state.js').read_text()
        at = source.index('function acknowledgedNoteBase(')
        body = source[at: source.index('\n    }', at)]
        self.assertIn('research: {', body)
        self.assertIn('private: {', body)
        self.assertIn('value:', body)
        self.assertIn('revision:', body)
        self.assertNotIn('effectiveNoteWork', body)
        self.assertNotIn('noteOperations', body)
        self.assertNotIn("type === 'work-notes-state'", source)

    def test_reconcilers_live_in_offline_runtime_and_are_invoked_once(self):
        runtime = (FRONTEND / 'offline-runtime.js').read_text()
        self.assertIn('async function reconcileWorkNote(', runtime)
        self.assertIn('async function reconcilePrivateNote(', runtime)
        self.assertIn('markDomainChanged(DOMAIN_CONCEPTS', runtime)
        state = (FRONTEND / 'work-notes-state.js').read_text()
        at = state.index('reconcile:')
        body = state[at: state.index('\n    };', at)]
        self.assertEqual(body.count('prksOfflineReconcileWorkNote'), 1)
        self.assertEqual(body.count('prksOfflineReconcilePrivateNote'), 1)
        self.assertNotIn('prksOfflineMarkConceptsChanged', body)
        self.assertNotIn('parse_research_markup', runtime)
        self.assertNotIn('parseResearchMarkup', runtime)

    def test_a_to_b_to_a_is_mutation_tested(self):
        """If saveWorkNote deleted B and then enqueued A, A->B->A would leave
        an operation. The store selftest already proves the live code cancels;
        this proves that test would fail if the cancel were removed."""
        store = (FRONTEND / 'local-store.js').read_text()
        at = store.index('function saveWorkNote(')
        body = store[at: store.index('\n        function saveWorkSource(', at)]
        needle = 'if (text === observed.value) { setResult(null); return; }'
        self.assertIn(needle, body)
        mutated = store.replace(needle, '/* mutated: no cancel */', 1)
        self.assertNotEqual(mutated, store)
        runner = r'''
"use strict";
const { createFakeIndexedDBFactory } = require(process.argv[1] + '/tests/browser/lib/fake_indexeddb.js');
const { createPrksLocalStore } = require(process.argv[2]);
let n = 0;
const uuid = () => '00000000-0000-4000-8000-' + (++n).toString(16).padStart(12, '0');
(async () => {
    const store = createPrksLocalStore({ indexedDB: createFakeIndexedDBFactory(), uuid });
    const observed = { value: 'A', revision: 0 };
    await store.saveWorkNote('W-1', 'SET_WORK_RESEARCH_NOTE', 'B', observed);
    await store.saveWorkNote('W-1', 'SET_WORK_RESEARCH_NOTE', 'A', observed);
    const rows = (await store.listOperations())
        .filter(r => r.operation === 'SET_WORK_RESEARCH_NOTE');
    process.stdout.write(String(rows.length) + ':' + ((rows[0] && rows[0].payload.text) || ''));
})().catch(err => { console.error(err); process.exit(1); });
'''
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as tmp:
            tmp.write(mutated)
            mutated_path = tmp.name
        try:
            proc = subprocess.run(
                ['node', '-e', runner, str(ROOT), mutated_path],
                cwd=ROOT, capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(proc.stdout.strip(), '1:A',
                             'without the cancel, A->B->A must leave an A operation')
        finally:
            pathlib.Path(mutated_path).unlink(missing_ok=True)

    def test_diagnostics_describes_both_note_families(self):
        source = (FRONTEND / 'sync-diagnostics.js').read_text()
        self.assertIn("'Research notes = \"'", source)
        self.assertIn("'Reminders = \"'", source)
        self.assertIn("SET_WORK_RESEARCH_NOTE: 'work-notes-state'", source)
        self.assertIn("SET_WORK_PRIVATE_NOTE: 'work-notes-state'", source)


if __name__ == '__main__':
    unittest.main()
