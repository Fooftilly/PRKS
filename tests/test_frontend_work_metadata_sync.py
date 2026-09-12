import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend' / 'js'
SYNCED = ('edition', 'journal', 'volume', 'issue', 'pages', 'isbn', 'doi')
DEFERRED = ('title', 'status', 'doc_type', 'year', 'published_date', 'publisher',
            'location', 'abstract', 'source_url', 'author_text', 'thumb_page')


class WorkMetadataSyncFrontendTests(unittest.TestCase):
    def test_runtime_selftests(self):
        result = subprocess.run(['node', str(ROOT / 'tests/browser/run_work_metadata_sync_selftest.js')],
                                cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_client_and_server_agree_on_the_synchronized_field_set(self):
        """Two registries that drift would mean the editor offering a field the
        server refuses, or silently online-only saving one it would accept."""
        from backend import work_metadata_sync
        module = (FRONTEND / 'work-metadata-state.js').read_text()
        listed = module[module.index('const FIELDS = Object.freeze(['):]
        listed = listed[: listed.index(']')]
        for field in SYNCED:
            self.assertIn("'%s'" % field, listed, field)
        self.assertEqual(sorted(work_metadata_sync.SYNCED_FIELDS), sorted(SYNCED))

    def test_the_synchronized_fields_left_the_online_patch_payload(self):
        """One Save must not secretly mean "seven fields into the durable queue
        and eleven more over HTTP, either half able to fail alone"."""
        ui = (FRONTEND / 'ui.js').read_text()
        at = ui.index('async function submitWorkMetaEdit(')
        body = ui[at: ui.index('const saveBtn = panel ? panel.querySelector', at)]
        for field in SYNCED:
            self.assertNotIn('%s: draft.%s' % (field, field), body, field)
        # The fields this milestone deliberately leaves online-only are still there.
        for field in ('title', 'status', 'abstract', 'publisher', 'location'):
            self.assertIn(field, body, field)

    def test_high_fan_out_fields_are_not_synchronized(self):
        """These need their pending values to propagate through several cached
        read models, which is a separate milestone -- not a quiet addition."""
        from backend import work_metadata_sync
        for field in DEFERRED:
            self.assertNotIn(field, work_metadata_sync.SYNCED_FIELDS, field)

    def test_the_coordinator_owns_no_family_specific_meaning(self):
        source = (FRONTEND / 'sync-runtime.js').read_text()
        coordinator = source[: source.index('root.createPrksSyncRuntime = createRuntime;')]
        for leaked in ('SET_WORK_METADATA_FIELD', 'current_value', 'requested_value',
                       'metadata-state', 'prksOfflineReconcile'):
            self.assertNotIn(leaked, coordinator, leaked)

    def test_semantic_transport_stays_with_the_coordinator(self):
        for path in FRONTEND.rglob('*.js'):
            if path.name != 'sync-runtime.js':
                self.assertNotIn('/api/sync/operations', path.read_text(), str(path))

    def test_pending_values_never_enter_the_disposable_cache(self):
        module = (FRONTEND / 'work-metadata-state.js').read_text()
        at = module.index('function effectiveWork(')
        body = module[at: module.index('function dirtyFields(', at)]
        for forbidden in ('putEntity', 'cacheEntity', 'prksOfflineCache'):
            self.assertNotIn(forbidden, body, forbidden)

    def test_one_renderer_serves_the_card_and_the_overlay(self):
        """A second copy of the row markup would drift from the first the
        moment either changed."""
        ui = (FRONTEND / 'ui.js').read_text()
        self.assertEqual(ui.count('function prksWorkBibRowsHtml('), 1)
        self.assertIn('prksWorkBibRowsHtml(work)', ui)
        editor = (FRONTEND / 'work-metadata-editor.js').read_text()
        self.assertIn('root.prksWorkBibRowsHtml(effective)', editor)

    def test_every_editor_filters_acknowledgements_by_family(self):
        """The durable queue is shared between families. An editor that accepts
        any acknowledgement will eventually be handed one carrying none of its
        own fields -- and the damage is not a visible error but a silently
        cancelled read that leaves its controls disabled with nothing to retry
        them."""
        for name in ('work-tag-editor.js', 'work-metadata-editor.js'):
            source = (FRONTEND / name).read_text()
            at = source.index('prksSync.subscribe(')
            body = source[at: source.index('});', at)]
            with self.subTest(module=name):
                self.assertIn('event.acknowledged', body)
                self.assertIn('event.operation', body)
