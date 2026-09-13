import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend' / 'js'
SYNCED = ('title', 'status', 'doc_type', 'thumb_page', 'author_text', 'year',
          'published_date', 'abstract', 'publisher', 'location', 'edition', 'journal',
          'volume', 'issue', 'pages', 'isbn', 'doi', 'source_url')
# Nothing user-editable remains outside the registry. `provider`, `provider_id`
# and `source_kind` are canonical VIDEO IDENTITY, which is an aggregate rather
# than a set of field-scoped scalars -- see work-source-identity.md.
DEFERRED = ('provider', 'provider_id', 'source_kind')

_REGISTRY_JS = """
require(process.argv[1] + '/frontend/js/date-format.js');
require(process.argv[1] + '/frontend/js/work-metadata-state.js');
process.stdout.write(JSON.stringify({
    fields: globalThis.PRKS_SYNCED_WORK_FIELDS,
    projections: globalThis.PRKS_SYNCED_WORK_FIELD_PROJECTIONS,
    summary: globalThis.PRKS_WORK_SUMMARY_FIELDS,
    byteLimited: Array.from(globalThis.PRKS_BYTE_LIMITED_WORK_FIELDS),
    statuses: globalThis.PRKS_WORK_STATUSES,
    byteLimits: globalThis.PRKS_WORK_FIELD_BYTE_LIMITS,
    storeLimits: require(process.argv[1] + '/frontend/js/local-store.js')
        .PRKS_LOCAL_WORK_FIELD_VALUE_BYTES,
}));
"""


def client_registries():
    """The registries the client actually EXPORTS, not how its source is
    formatted. Slicing the module text for `const X = Object.freeze([` broke
    twice on ordinary edits -- a wrapped line, and a semicolon inside a
    comment -- which is a test failing for a reason the code is not
    responsible for."""
    import json
    proc = subprocess.run(['node', '-e', _REGISTRY_JS, str(ROOT)],
                          cwd=ROOT, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError(proc.stdout + proc.stderr)
    return json.loads(proc.stdout)


class WorkMetadataSyncFrontendTests(unittest.TestCase):
    def test_runtime_selftests(self):
        result = subprocess.run(['node', str(ROOT / 'tests/browser/run_work_metadata_sync_selftest.js')],
                                cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_client_and_server_agree_on_the_synchronized_field_set(self):
        """Two registries that drift would mean the editor offering a field the
        server refuses, or silently online-only saving one it would accept."""
        from backend import work_metadata_sync
        self.assertEqual(sorted(client_registries()['fields']),
                         sorted(work_metadata_sync.SYNCED_FIELDS))
        self.assertEqual(sorted(work_metadata_sync.SYNCED_FIELDS), sorted(SYNCED))

    def test_no_user_editable_work_metadata_saves_over_http_any_more(self):
        """The end state of the local-first metadata program.

        This replaces five separate "field X left the legacy payload" tests.
        Each of those asserted an absence from `submitWorkMetaEdit`, and that
        function is now gone entirely -- its last version sent an EMPTY
        payload. What remains to pin is stronger and says it once: there is no
        online-only Work metadata save, and every synchronized field has a
        control that belongs to a durable group."""
        ui = (FRONTEND / 'ui.js').read_text()
        self.assertNotIn('async function submitWorkMetaEdit(', ui)
        self.assertNotIn('inline-save-metadata-btn', ui)
        self.assertNotIn('data-prks-role="work-meta-online-only"', ui)

        # Four durable groups, each with its own bounded save.
        for role, button in (
            ('work-identity-editor', 'save-work-identity-btn'),
            ('work-status-editor', 'save-work-status-btn'),
            ('work-bib-editor', 'save-work-bib-btn'),
            ('work-source-editor', 'save-work-source-btn'),
        ):
            with self.subTest(group=role):
                self.assertIn('data-prks-role="%s"' % role, ui)
                self.assertIn('id="%s"' % button, ui)

        # Every synchronized field is reachable through one of them. The
        # segmented and doc-type controls stamp their marker from a helper, so
        # the call is what proves it rather than a literal attribute.
        markers = ui.count('data-prks-work-field=')
        self.assertGreaterEqual(markers, 10)
        for field in SYNCED:
            if field in ('status', 'doc_type'):
                continue   # emitted by prksSegmentedControlHtml / the doc-type menu
            with self.subTest(field=field):
                self.assertIn('data-prks-work-field="%s"' % field, ui, field)
        self.assertIn("{ workField: 'status' }", ui)
        self.assertIn("{ workField: 'doc_type' }", ui)

    def test_no_frontend_code_patches_a_local_first_work_field(self):
        """The regression this exists to catch, in the general case.

        Every field below has ONE durable mutation path. A second one -- a
        PATCH body carrying the field name -- would not be revision-aware, so
        it would silently overwrite whatever the durable path had conflicted
        over. That mistake has been made twice already in this program: the
        Playlist inline rename PATCHed `title`, and the metadata editor
        PATCHed the whole bibliographic block.

        Scans the PATCH REQUEST BODIES rather than whole files, so a component
        may still mention a field it renders.
        """
        import re
        from backend import work_metadata_sync
        synced = set(work_metadata_sync.SYNCED_FIELDS)
        # Creating a NEW Work legitimately sends these; only mutation of an
        # existing one is forbidden, and creation is a POST.
        offenders = []
        for path in sorted((FRONTEND).rglob('*.js')):
            source = path.read_text(encoding='utf-8')
            for match in re.finditer(r"method:\s*'PATCH'", source):
                # The body literal that accompanies this PATCH, if any.
                window = source[match.start(): match.start() + 900]
                body = re.search(r'JSON\.stringify\(\s*\{(.*?)\}\s*\)', window, re.S)
                if not body:
                    continue
                for field in synced:
                    if re.search(r'\b%s\s*:' % re.escape(field), body.group(1)):
                        offenders.append('%s: PATCH body carries %s' % (path.name, field))
        self.assertEqual(sorted(set(offenders)), [],
                         'a synchronized Work field is being PATCHed directly')

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
        client = client_registries()
        server = work_metadata_sync.FIELD_PROJECTIONS
        self.assertEqual(sorted(client['projections']), sorted(server))
        for field, domains in server.items():
            self.assertEqual(list(client['projections'][field]), list(domains), field)
        self.assertEqual(server['publisher'], ('recently-added',))
        self.assertEqual(server['abstract'], ('works-browse',))
        # Every Work card shows a year, a Status badge and a credit line, and
        # Status additionally decides Progress group membership -- so all four
        # reach every browse catalog.
        for field in ('year', 'published_date', 'status', 'author_text', 'thumb_page',
                      'doc_type', 'title', 'source_url'):
            self.assertEqual(server[field], ('works-browse', 'recent', 'recently-added'), field)
        # And nothing else claims a projection on either side.
        for field in SYNCED:
            if field not in ('publisher', 'abstract', 'year', 'published_date',
                             'status', 'author_text', 'thumb_page', 'doc_type', 'title',
                             'source_url'):
                self.assertNotIn(field, client['projections'], field)
                self.assertNotIn(field, server, field)

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

    def test_progress_filters_effective_rows_and_stays_ignorant_of_operations(self):
        """Status is the first synchronized field that changes which GROUP a
        Work belongs to, not merely what its card says. The route must hand
        `renderProgressByStatus` the OVERLAID rows and the component must keep
        filtering them by value -- teaching the component about durable
        operations would give it a second opinion about pending state, and it
        would drift from every other surface the first time either changed."""
        app = (FRONTEND / 'app.js').read_text()
        at = app.index("case 'progress': {")
        body = app[at: app.index("case 'processing-files': {", at)]
        self.assertIn("prksEffectiveBrowseRows(base, 'works-browse')", body)
        self.assertIn('prksRefreshPendingWorkMetadata', body)
        # The overlaid rows, not the acknowledged snapshot, are what it renders.
        self.assertIn('renderProgressByStatus(works,', body)

        progress = (FRONTEND / 'components' / 'progress.js').read_text()
        for forbidden in ('SET_WORK_METADATA_FIELD', 'listOperations', 'prksSync',
                          'payload.field', 'prksEffective'):
            self.assertNotIn(forbidden, progress, forbidden)
        # It selects on the value it was given -- that is the whole mechanism.
        self.assertIn('w.status === status', progress)

    def test_search_result_cards_render_the_effective_work(self):
        """Search and Saved View results are fetched fresh from the server, so
        they still carry the acknowledged value while a local edit is pending.
        One overlay for every synchronized field, in the shared renderer, so
        this is not a Status-specific patch and the next field needs none."""
        saved = (FRONTEND / 'saved-views.js').read_text()
        at = saved.index('function prksSearchResultCardsHtml(')
        body = saved[at: saved.index('const modalState', at)]
        self.assertIn('prksEffectiveWorksSync', body)
        for forbidden in ('SET_WORK_METADATA_FIELD', 'listOperations', 'payload.field'):
            self.assertNotIn(forbidden, saved, forbidden)
        # The routes that call it must hydrate the map before rendering.
        app = (FRONTEND / 'app.js').read_text()
        for marker in ("case 'search': {", "case 'saved-view-detail': {"):
            at = app.index(marker)   # missing marker is a failure, not a skip
            self.assertIn('prksHydratePendingWorkMetadata', app[at: at + 1400], marker)

    def test_credit_is_composed_after_the_overlay_not_before(self):
        """`author_text` is only one of three possible sources of a credit: a
        linked Author outranks it, a linked Editor stands in when it is empty.
        Reading it off the acknowledged row before the overlay both misses the
        edit and cannot reveal the Editor when the field is cleared -- and
        overlaying onto already-rendered HTML could not work at all."""
        palette = (FRONTEND / 'command-palette.js').read_text()
        at = palette.index('function workSubtitle(')
        body = palette[at: palette.index('function entityRows(', at)]
        overlay = body.index('prksEffectiveWorkSync(')
        credit = body.index('linked_authors')
        self.assertLess(overlay, credit,
                        'the credit is composed from the acknowledged row')
        self.assertIn('work.linked_authors', body)
        self.assertIn('work.author_text', body)
        self.assertNotIn('w.author_text', body)

        # The card renderer receives an already-overlaid row; it must not try
        # to interpret pending operations itself.
        cards = (FRONTEND / 'components' / 'work-cards.js').read_text()
        for forbidden in ('SET_WORK_METADATA_FIELD', 'listOperations', 'prksSync',
                          'prksEffective', 'payload.field'):
            self.assertNotIn(forbidden, cards, forbidden)
        # And the precedence itself still lives there, in one place.
        credit_fn = cards[cards.index('function prksWorkCardCreditLine('):]
        credit_fn = credit_fn[: credit_fn.index('\n}')]
        self.assertLess(credit_fn.index('linked_authors'), credit_fn.index('author_text'))
        self.assertLess(credit_fn.index('author_text'), credit_fn.index('primary_editor'))

    def test_the_thumbnail_url_always_states_its_page(self):
        """A URL with no page means "whatever the server currently stores",
        which is not a resource identity the client can reason about -- and is
        wrong while a clear is pending. Behaviour is pinned by the selftest;
        this pins that the page-less form cannot come back."""
        cards = (FRONTEND / 'components' / 'work-cards.js').read_text()
        at = cards.index('function prksWorkThumbUrl(')
        body = cards[at: cards.index('\n}', at)]
        self.assertIn('?page=', body)
        self.assertNotIn('/thumbnail`', body,
                         'a page-less thumbnail URL is reachable again')
        # Suppression decides BEFORE any URL exists, never after.
        self.assertIn('const thumbSrc = suppressThumbnail', cards)
        suppression = cards.index('const suppressThumbnail =')
        self.assertLess(suppression, cards.index('const thumbSrc ='))
        # And the card never learns what a durable operation is.
        for forbidden in ('SET_WORK_METADATA_FIELD', 'listOperations', 'prksSync',
                          'prksEffective', 'payload.field'):
            self.assertNotIn(forbidden, cards, forbidden)

    def test_the_request_coordinator_classifies_by_pathname(self):
        """The added `?page=` must not change how a thumbnail request is
        treated; it does not, because classification uses `URL.pathname`."""
        coordinator = (FRONTEND / 'request-coordinator.js').read_text()
        self.assertIn('function isWorkThumbnailPath(pathname)', coordinator)
        self.assertIn('const pathname = parsed.pathname;', coordinator)
        self.assertIn('new URL(raw, origin)', coordinator)

    def test_wire_and_entity_conversions_run_where_values_cross_boundaries(self):
        """`thumb_page` is the first field whose wire and entity spellings
        differ, so every place a value is written into a Work-like object has
        to convert. A missed one puts a string where a shape validator
        requires an integer, and the cached row is discarded as corrupt."""
        runtime = (FRONTEND / 'offline-runtime.js').read_text()
        # The two sites that write an acknowledged value into entity-shaped rows.
        self.assertIn("work[result.field] = root.prksWorkFieldToEntity(result.field, result.value)",
                      runtime)
        self.assertIn('root.prksWorkFieldToEntity(result.field, result.value) }', runtime)
        self.assertNotIn('work[result.field] = result.value;', runtime)
        # Projection rows convert through the transform, not by assignment.
        self.assertIn('root.prksProjectionFieldPatch(domain, result.field, result.value)', runtime)
        state = (FRONTEND / 'work-metadata-state.js').read_text()
        self.assertIn('derive: value => toEntityValue(field, value)', state)
        # metadata-state deliberately keeps the WIRE value; it is
        # synchronization bookkeeping, not an entity.
        self.assertIn('function metadataStateAckPatch', state)

    def test_thumb_page_codecs_agree_across_the_boundary(self):
        """The client and the server decide separately what a page number is.
        If they disagree, one of them accepts a value the other refuses --
        which is the split contract the whole architecture removes."""
        import json
        from backend import work_metadata_sync
        server = work_metadata_sync.codec_for('thumb_page')
        cases = ['', '1', '3', '003', ' 3 ', '  ', '0', '-1', '1.5', 'abc',
                 '3abc', '+3', '\uff13', '1_0', '1e3', '٣']
        js = """
        require(process.argv[1] + '/frontend/js/date-format.js');
        require(process.argv[1] + '/frontend/js/work-metadata-state.js');
        const out = {};
        for (const v of JSON.parse(process.argv[2])) {
            out[v] = [globalThis.prksWorkFieldToCanonical('thumb_page', v),
                      globalThis.prksWorkFieldToEntity('thumb_page', v)];
        }
        process.stdout.write(JSON.stringify(out));
        """
        proc = subprocess.run(['node', '-e', js, str(ROOT), json.dumps(cases)],
                              cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        client = json.loads(proc.stdout)
        for value in cases:
            with self.subTest(value=value):
                accepted = server.is_valid_wire(value)
                canonical, entity = client[value]
                self.assertEqual(canonical is not None, accepted,
                                 '%r: client accepts=%s server accepts=%s'
                                 % (value, canonical is not None, accepted))
                if accepted:
                    self.assertEqual(canonical, server.to_wire(server.to_database(value)))
                    self.assertEqual(entity, server.to_database(value))

    def test_video_identity_columns_are_deliberately_not_field_scoped(self):
        """`prksYoutubeEmbedUrl` short-circuits on `provider_id`, so changing a
        video's URL alone would move the stored value while the video that
        plays stays the same. Identity is an aggregate; only PROVENANCE is
        field-scoped, and the server refuses a field-scoped write to a video
        Work's URL."""
        from backend import work_metadata_sync
        for field in ('provider', 'provider_id', 'source_kind'):
            self.assertNotIn(field, work_metadata_sync.SYNCED_FIELDS, field)
            self.assertNotIn(field, client_registries()['fields'], field)
        self.assertEqual(work_metadata_sync.FIELD_KIND_GUARDS, {'source_url': 'video'})

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

    def test_every_byte_limit_is_one_number_on_every_path(self):
        """A value savable online and refused offline -- or accepted by the
        editor and rejected by the durable store -- is the split contract that
        moving a field to local-first removes. Three registries hold these
        numbers (server, client, durable store) and all three must agree."""
        from backend import work_metadata_sync
        client = client_registries()
        server = dict(work_metadata_sync.BYTE_LIMITS)
        self.assertEqual(server, {'abstract': 1024 * 1024, 'author_text': 64 * 1024,
                                  'title': 64 * 1024, 'source_url': 64 * 1024})
        self.assertEqual(client['byteLimits'], server, 'client registry drifted')
        self.assertEqual(client['storeLimits'], server, 'durable store registry drifted')
        # The byte-limited SET is derived from the registry, never listed twice.
        self.assertEqual(sorted(client['byteLimited']), sorted(server))
        self.assertEqual(sorted(work_metadata_sync.BYTE_LIMITED_FIELDS), sorted(server))
        # And each one is the field's entry in the size registry too.
        for field, limit in server.items():
            self.assertEqual(work_metadata_sync.SYNCED_FIELDS[field], limit, field)

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
        # One composed entry point, so a component never learns which families
        # exist and the overlays are always applied in the same order.
        for name in ('folders.js', 'people.js', 'playlists.js'):
            self.assertIn('prksEffectiveWorkSummaryRows',
                          (FRONTEND / 'components' / name).read_text(), name)
        self.assertIn('prksEffectiveWorkSync',
                      (FRONTEND / 'command-palette.js').read_text())
        app = (FRONTEND / 'app.js').read_text()
        composed = app[app.index('function prksEffectiveWorkSummaryRows('):]
        composed = composed[: composed.index('\n}')]
        self.assertIn('prksEffectiveWorkRows', composed, 'relationships first')
        self.assertIn('prksEffectiveWorkSummaries', composed, 'then metadata')

    def test_summary_field_registries_agree(self):
        from backend import work_metadata_sync
        self.assertEqual(sorted(client_registries()['summary']),
                         sorted(work_metadata_sync.SUMMARY_FIELDS))
        self.assertEqual(sorted(work_metadata_sync.SUMMARY_FIELDS),
                         ['author_text', 'doc_type', 'published_date', 'publisher',
                          'source_url', 'status', 'thumb_page', 'title', 'year'])
        for field in work_metadata_sync.SUMMARY_FIELDS:
            self.assertIn(field, work_metadata_sync.SYNCED_FIELDS, field)

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
