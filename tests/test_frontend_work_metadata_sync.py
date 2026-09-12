import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend' / 'js'
SYNCED = ('abstract', 'publisher', 'location', 'edition', 'journal', 'volume',
          'issue', 'pages', 'isbn', 'doi')
DEFERRED = ('title', 'status', 'doc_type', 'year', 'published_date',
            'source_url', 'author_text', 'thumb_page')


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
        """One Save must not secretly mean "nine fields into the durable queue
        and the rest over HTTP, either half able to fail alone"."""
        ui = (FRONTEND / 'ui.js').read_text()
        at = ui.index('async function submitWorkMetaEdit(')
        body = ui[at: ui.index('const saveBtn = panel ? panel.querySelector', at)]
        for field in SYNCED:
            self.assertNotIn('%s: draft.%s' % (field, field), body, field)
            self.assertNotIn('%s: draft' % field, body, field)
        # The fields this milestone deliberately leaves online-only are still there.
        for field in ('title', 'status'):
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

    def test_client_and_server_agree_on_which_fields_reach_another_projection(self):
        """A field the server thinks is Work-detail-only, but the client
        overlays into a list -- or the reverse -- is a silent coherence bug:
        one side reconciles a cached projection the other never updates."""
        from backend import work_metadata_sync
        module = (FRONTEND / 'work-metadata-state.js').read_text()
        listed = module[module.index('const FIELD_PROJECTIONS = Object.freeze('):]
        listed = listed[: listed.index(';')]
        self.assertIn("publisher: ['recently-added']", listed)
        self.assertIn("abstract: ['works-browse']", listed)
        self.assertEqual(sorted(work_metadata_sync.FIELD_PROJECTIONS), ['abstract', 'publisher'])
        self.assertEqual(work_metadata_sync.FIELD_PROJECTIONS['publisher'], ('recently-added',))
        self.assertEqual(work_metadata_sync.FIELD_PROJECTIONS['abstract'], ('works-browse',))
        for field in SYNCED:
            if field not in ('publisher', 'abstract'):
                self.assertNotIn("%s:" % field, listed, field)

    def test_recently_added_does_not_reimplement_the_overlay(self):
        """The Folder component says "repaint"; it must not grow a second
        opinion about which fields are pending or what they mean. Two
        interpretations of operation semantics would drift the moment either
        changed."""
        folders = (FRONTEND / 'components' / 'folders.js').read_text()
        self.assertIn("prksEffectiveProjectionRows(acknowledged, 'recently-added')", folders)
        self.assertIn('prksRefreshPendingWorkMetadata', folders)
        for forbidden in ('SET_WORK_METADATA_FIELD', 'listOperations', 'server_result',
                          'payload.field'):
            self.assertNotIn(forbidden, folders, forbidden)

    def test_pending_values_never_enter_the_acknowledged_recently_added_rows(self):
        """This tab already shipped a bug where its RAM copy outlived an
        IndexedDB invalidation. Baking unsynchronized values into that array
        would be the same mistake with a longer fuse."""
        folders = (FRONTEND / 'components' / 'folders.js').read_text()
        at = folders.index('function prksRenderFolderLibraryRecentlyAdded(')
        body = folders[at: folders.index('async function prksLoadFolderLibraryRecentlyAdded(', at)]
        self.assertIn('const acknowledged =', body)
        self.assertNotIn('st.recentlyAddedWorks =', body)
        # The memoized render is refused when the overlay moved, not only when
        # the coherence domain did.
        self.assertIn('recentlyAddedPendingGeneration', folders)

    def test_progress_does_not_interpret_durable_operations_itself(self):
        """The overlay rules live in the metadata-state layer. A component that
        grew its own `if (field === 'abstract')` would be a second opinion about
        operation semantics, drifting the moment either side changed."""
        for name in ('components/progress.js', 'components/folders.js'):
            source = (FRONTEND / name).read_text()
            with self.subTest(module=name):
                for forbidden in ('SET_WORK_METADATA_FIELD', 'listOperations',
                                  'payload.field', "=== 'abstract'"):
                    self.assertNotIn(forbidden, source, forbidden)

    def test_the_abstract_limit_is_one_number_on_both_sides(self):
        """An Abstract savable online and refused offline would be exactly the
        split contract that moving a field to local-first removes."""
        from backend import work_metadata_sync
        module = (FRONTEND / 'work-metadata-state.js').read_text()
        self.assertEqual(work_metadata_sync.MAX_ABSTRACT_UTF8_BYTES, 1024 * 1024)
        self.assertIn('const MAX_ABSTRACT_UTF8_BYTES = 1024 * 1024;', module)
        self.assertEqual(sorted(work_metadata_sync.BYTE_LIMITED_FIELDS), ['abstract'])
        self.assertIn("const BYTE_LIMITED_FIELDS = new Set(['abstract']);", module)

    def test_the_editor_refuses_an_oversize_value_before_enqueueing(self):
        editor = (FRONTEND / 'work-metadata-editor.js').read_text()
        at = editor.index('const changes = root.prksDirtyWorkMetadataFields(')
        body = editor[at: editor.index('await root.prksSync.store.saveWorkMetadataFields(', at)]
        self.assertIn('prksWorkFieldLimitError', body)
        self.assertIn('return;', body, 'the save must abort, not continue')

    def test_a_conflict_result_never_carries_a_byte_limited_value(self):
        """The durable row bounds a structured result to 2 KB; a conflict the
        browser cannot store is a conflict the user never sees."""
        from backend import work_metadata_sync
        self.assertEqual(
            sorted(work_metadata_sync.disagreement('abstract', 'a' * 5000, 'b' * 5000)),
            ['current_bytes', 'current_preview', 'requested_bytes'])
        self.assertEqual(
            sorted(work_metadata_sync.disagreement('doi', '10.1/a', '10.1/b')),
            ['current_value', 'requested_value'])
