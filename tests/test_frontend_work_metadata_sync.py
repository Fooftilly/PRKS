import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend' / 'js'
SYNCED = ('year', 'published_date', 'abstract', 'publisher', 'location',
          'edition', 'journal', 'volume', 'issue', 'pages', 'isbn', 'doi')
DEFERRED = ('title', 'status', 'doc_type', 'source_url', 'author_text', 'thumb_page')


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
        """One Save must not secretly mean "ten fields into the durable queue
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
        self.assertIn('year: BROWSE_LISTS', listed)
        self.assertIn('published_date: BROWSE_LISTS', listed)
        self.assertEqual(sorted(work_metadata_sync.FIELD_PROJECTIONS),
                         ['abstract', 'published_date', 'publisher', 'year'])
        self.assertEqual(work_metadata_sync.FIELD_PROJECTIONS['publisher'], ('recently-added',))
        self.assertEqual(work_metadata_sync.FIELD_PROJECTIONS['abstract'], ('works-browse',))
        # Every Work card shows a year, so both reach all three browse lists.
        for field in ('year', 'published_date'):
            self.assertEqual(work_metadata_sync.FIELD_PROJECTIONS[field],
                             ('works-browse', 'recent', 'recently-added'), field)
        for field in SYNCED:
            if field not in ('publisher', 'abstract', 'year', 'published_date'):
                self.assertNotIn("%s:" % field, listed, field)

    def test_every_declared_projection_can_actually_be_reconciled(self):
        """The defect this pins: `recent` was a declared projection for `year`
        and `published_date`, but the runtime's key map had no entry for it, so
        acknowledgements skipped `recent:index` in silence and that list kept
        serving a value the server no longer held. A declared projection with
        no addressable cache is a wiring error, not something to step over."""
        from backend import work_metadata_sync
        runtime = (FRONTEND / 'offline-runtime.js').read_text()
        block = runtime[runtime.index('const FIELD_PROJECTION_LISTS = {'):]
        block = block[: block.index('};')]
        declared = set()
        for domains in work_metadata_sync.FIELD_PROJECTIONS.values():
            declared.update(domains)
        for domain in sorted(declared):
            self.assertIn("'%s':" % domain, block,
                          "%s is a declared projection the runtime cannot address" % domain)
        # And an unknown domain must fail the reconciliation rather than be
        # skipped, so the next such gap cannot hide behind a `continue`.
        guard = runtime[runtime.index('const listKey = FIELD_PROJECTION_LISTS[domain];'):][:400]
        self.assertIn('if (!listKey) return false;', guard)

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

    def test_recently_added_search_filters_the_effective_rows(self):
        """Rendering the overlay but filtering the acknowledged array is a real
        and easy mistake: the card would show the pending Year while a search
        for it found nothing, and a search for the OLD year would still match a
        value the user had already replaced."""
        folders = (FRONTEND / 'components' / 'folders.js').read_text()
        at = folders.index('function prksRenderFolderLibraryRecentlyAdded(')
        body = folders[at: folders.index('async function prksLoadFolderLibraryRecentlyAdded(', at)]
        filter_line = [ln for ln in body.splitlines() if 'MatchesQuery(' in ln and '.filter(' in ln]
        self.assertEqual(len(filter_line), 1, 'expected exactly one local filter over the rows')
        self.assertIn('all.filter(', filter_line[0],
                      'the filter must run over the overlaid rows, not the acknowledged array')
        self.assertNotIn('acknowledged.filter(', body)
        # And the haystack has to include the fields that reach this projection.
        haystack = folders[folders.index('function prksRecentlyAddedWorkMatchesQuery('):]
        haystack = haystack[: haystack.index('\n}')]
        for field in ('year', 'published_date', 'publisher'):
            self.assertIn('work.%s' % field, haystack, field)

    def test_embedded_summary_routes_hydrate_the_overlay_before_rendering(self):
        """`prksEffectiveWorkSummaries()` reads a map hydrated from the durable
        queue. A route that renders embedded summaries without hydrating first
        reopens a cached Folder, profile or playlist showing the value the user
        already replaced -- and then silently corrects itself once some other
        surface happens to read the queue, which is worse than being wrong
        consistently."""
        app = (FRONTEND / 'app.js').read_text()
        for call in ('renderFolderDetails(ctx, folder, contentDiv',
                     'renderPlaylistDetail(ctx, pl, contentDiv',
                     'renderPersonDetails(ctx, person, contentDiv'):
            with self.subTest(call=call):
                at = app.index(call)
                self.assertIn('prksHydratePendingWorkMetadata()', app[max(0, at - 1000): at],
                              '%s renders embedded summaries without hydrating' % call)
        # And the hydration must be cancellable like every other route await.
        for at in [m for m in range(len(app)) if app.startswith('await prksHydratePendingWorkMetadata()', m)]:
            self.assertIn('stale()', app[at: at + 200], 'a hydrated route must re-check staleness')

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

    def test_embedded_summary_overlay_is_centralized(self):
        """Folder, Person and Playlist all embed Work summaries. Each learning
        to read the durable queue would be three interpretations of operation
        semantics, drifting the moment any of them changed."""
        for name in ('components/folders.js', 'components/people.js',
                     'components/playlists.js', 'command-palette.js'):
            source = (FRONTEND / name).read_text()
            with self.subTest(module=name):
                for forbidden in ('SET_WORK_METADATA_FIELD', 'listOperations',
                                  'prksSync.store', 'payload.field'):
                    self.assertNotIn(forbidden, source, forbidden)
        self.assertIn('prksEffectiveWorkSummaries',
                      (FRONTEND / 'components' / 'folders.js').read_text())
        self.assertIn('prksEffectiveWorkSummaries',
                      (FRONTEND / 'components' / 'people.js').read_text())
        self.assertIn('prksEffectiveWorkSummaries',
                      (FRONTEND / 'components' / 'playlists.js').read_text())
        self.assertIn('prksEffectiveWorkSync',
                      (FRONTEND / 'command-palette.js').read_text())

    def test_summary_field_registries_agree(self):
        from backend import work_metadata_sync
        module = (FRONTEND / 'work-metadata-state.js').read_text()
        listed = module[module.index('const SUMMARY_FIELDS = Object.freeze(['):]
        listed = listed[: listed.index(']')]
        for field in work_metadata_sync.SUMMARY_FIELDS:
            self.assertIn("'%s'" % field, listed, field)
        self.assertEqual(sorted(work_metadata_sync.SUMMARY_FIELDS),
                         ['published_date', 'publisher', 'year'])

    def test_year_and_published_date_left_the_legacy_save(self):
        """Two mutation paths for one field means the path that is not
        revision-aware silently overwrites the other's conflicts."""
        ui = (FRONTEND / 'ui.js').read_text()
        at = ui.index('async function submitWorkMetaEdit(')
        body = ui[at: ui.index('const saveBtn = panel ? panel.querySelector', at)]
        for fragment in ('year: draft.year', 'published_date:', 'metaDateIso'):
            self.assertNotIn(fragment, body, fragment)
        # ...and both now carry the synchronized-field marker in the editor.
        for field in ('year', 'published_date'):
            self.assertIn('data-prks-work-field="%s"' % field, ui, field)

    def test_published_date_conversion_lives_in_the_field_codec(self):
        """The durable store stores what it is handed and must never learn what
        a date is; the editor must not invent a second conversion."""
        state = (FRONTEND / 'work-metadata-state.js').read_text()
        self.assertIn('const CODECS = {', state)
        self.assertIn('published_date:', state[state.index('const CODECS = {'):])
        local_store = (FRONTEND / 'local-store.js').read_text()
        for forbidden in ('prksParsePublishedDateInput', 'prksIsoToDdMmYyyy', 'published_date'):
            self.assertNotIn(forbidden, local_store, forbidden)

    def test_every_browse_route_uses_effective_rows(self):
        app = (FRONTEND / 'app.js').read_text()
        for route in ("case 'progress': {", "case 'types': {", "case 'type-detail': {",
                      "case 'recent': {"):
            at = app.index(route)
            body = app[at: at + 1600]
            with self.subTest(route=route):
                self.assertIn('prksRefreshPendingWorkMetadata', body)
                self.assertIn('prksEffectiveBrowseRows', body)
