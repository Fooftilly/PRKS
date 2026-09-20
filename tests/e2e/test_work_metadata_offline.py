"""Field-scoped Work metadata: independence, conflicts and atomic local save."""
import json
import os
import unittest

from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig
from tests.e2e import test_offline as o
from tests.e2e.fixtures import PERSON_DISPLAY, WORK_A_TITLE, WORK_B_TITLE, seed_library
from tests.e2e.harness import (
    AppServer,
    ChromiumHolder,
    chromium_recycle_every,
    e2e_diag,
    open_app_page,
    wait_for_async,
)


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get('PRKS_E2E') == '1' else unittest.TestSuite()


# Heavy service-worker module: recycle Chromium after N closed contexts.
# Override with PRKS_E2E_CHROMIUM_RECYCLE_EVERY (0 disables).
# Measured (class alone, PRKS_E2E_DIAGNOSTIC=1): PASS often, but intermittent
# hangs after APP_READY at varying depths — ~case 47 (recycle=20), ~13
# (recycle=15), ~5 (recycle=12). Quiet run with recycle=4 still hung on
# acknowledgement (~case 24): Playwright wedged so pending()'s 25s JS
# deadline never fired. Recycle every 1 = fresh Chromium per test here.
_DEFAULT_RECYCLE_EVERY = 1
_HOLDER = None


def setUpModule():
    global _HOLDER
    every = chromium_recycle_every(default=_DEFAULT_RECYCLE_EVERY)
    _HOLDER = ChromiumHolder(recycle_every=every)


def tearDownModule():
    global _HOLDER
    if _HOLDER is not None:
        _HOLDER.close()
        _HOLDER = None


class OfflineWorkMetadataTests(unittest.TestCase):
    FIELD_OPS = "r.operation === 'SET_WORK_METADATA_FIELD'"

    def start(self):
        tid = self.id()
        e2e_diag("START", tid)
        server = AppServer(seed_fn=seed_library)
        self.addCleanup(lambda: e2e_diag("SERVER_STOPPED", tid))
        self.addCleanup(server.stop)
        server.start()
        e2e_diag("SERVER_READY", tid)
        page, context, collector = open_app_page(
            _HOLDER.get_browser(), server.origin, service_workers='allow'
        )
        e2e_diag("CONTEXT_READY", tid)

        def _after_context():
            e2e_diag("CONTEXT_CLOSED", tid)
            _HOLDER.after_context_closed()

        self.addCleanup(_after_context)
        self.addCleanup(context.close)
        self.addCleanup(lambda: e2e_diag("BODY_DONE", tid))
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        o._wait_sw_active(page)
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work', server.ids['work_a'])
        self.edit(page)
        o._wait_entity_cached(page, 'work-metadata-state', server.ids['work_a'])
        e2e_diag("APP_READY", tid)
        return server, page, context

    # ---- helpers ------------------------------------------------------------

    def edit(self, page):
        """Open Edit metadata and wait for the synchronized group to be usable."""
        page.locator('#panel-content button', has_text='Edit metadata').click()
        page.wait_for_function(
            "() => { const b = document.getElementById('save-work-bib-btn'); return b && !b.disabled; }")

    def field(self, page, name, value):
        page.locator('[data-prks-work-field="%s"]' % name).fill(value)

    def save(self, page):
        page.locator('#save-work-bib-btn').click()

    def settled_conflicts(self, page, count):
        """Exactly `count` field operations, all of which have reached a
        terminal result. Waiting on the row count alone also matches the
        instant before a retry is claimed, or before the result is written."""
        page.evaluate("""async n => {
            const deadline = Date.now() + 25000;
            for (;;) {
                const rows = (await prksSync.store.listOperations())
                    .filter(r => r.operation === 'SET_WORK_METADATA_FIELD');
                if (rows.length === n && rows.every(r => r.status === 'conflict' && !!r.server_result)) return;
                if (Date.now() > deadline) throw new Error('No conflict settled: ' + JSON.stringify(rows));
                await new Promise(resolve => setTimeout(resolve, 50));
            }
        }""", count)

    def operations(self, page):
        return page.evaluate(
            "() => prksSync.store.listOperations().then(rows => rows.filter(r => %s))" % self.FIELD_OPS)

    def pending(self, page, count):
        page.evaluate("""async n => {
            const deadline = Date.now() + 25000;
            for (;;) {
                const rows = (await prksSync.store.listOperations())
                    .filter(r => r.operation === 'SET_WORK_METADATA_FIELD');
                if (rows.length === n && !rows.some(r => r.status === 'syncing')) return;
                if (Date.now() > deadline) throw new Error('Sync did not settle: ' + JSON.stringify(rows));
                await new Promise(resolve => setTimeout(resolve, 50));
            }
        }""", count)

    def offline(self, page, context):
        context.set_offline(True)
        page.evaluate('() => prksOfflineNoteRequestFailure()')
        page.wait_for_function("() => prksOfflineRuntimeState() !== 'online'")

    def reconnect(self, page, context):
        context.set_offline(False)
        page.evaluate("() => window.dispatchEvent(new Event('online'))")
        page.wait_for_function("() => prksOfflineRuntimeState() === 'online'")

    def db_for(self, server):
        return PRKSDatabase(storage=StorageConfig.for_testing(server.storage_root))

    def server_fields(self, server, work_id):
        return self.db_for(server).get_work_metadata_state(work_id)['fields']

    def server_value(self, server, work_id, field):
        """The canonical column. A BYTE-LIMITED field's metadata-state entry
        carries its revision alone -- the Work record already has the value,
        and duplicating it into a second projection would double what every
        read costs -- so the projection is the wrong place to ask."""
        return self.db_for(server).get_work(work_id)[field]

    def record_paths(self, page):
        seen = []
        page.on('request', lambda request: seen.append((request.method, request.url)))
        return seen

    def bib_rows(self, page):
        """The read-only card's synchronized rows, which only exist outside
        edit mode -- so leave the editor first, the way a user would."""
        page.evaluate("() => prksSetWorkDetailsMode('view')")
        # Attached, not visible: the host lives inside a collapsed <details>.
        page.wait_for_selector('[data-prks-role="work-bib-rows"]', state='attached')
        # textContent, not innerText: the metadata card is a collapsed
        # <details>, so its rendered text is empty until the user expands it.
        return page.evaluate(
            "() => document.querySelector('[data-prks-role=\"work-bib-rows\"]').textContent")

    def wait_for_bib_rows(self, page, text):
        page.evaluate("() => prksSetWorkDetailsMode('view')")
        page.wait_for_function("""(needle) => {
            const host = document.querySelector('[data-prks-role="work-bib-rows"]');
            return !!host && host.textContent.indexOf(needle) !== -1;
        }""", arg=text)

    # ---- offline editing ----------------------------------------------------

    def test_offline_field_edit_survives_reload_and_synchronizes(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        self.offline(page, context)
        seen = self.record_paths(page)
        self.field(page, 'doi', '10.1234/offline')
        self.save(page)
        self.pending(page, 1)
        self.assertIn('10.1234/offline', self.bib_rows(page),
                      'the effective Work shows the pending value')
        self.assertEqual([url for method, url in seen if method != 'GET'], [],
                         'nothing is sent while PRKS is unreachable')

        page.reload()
        page.wait_for_selector('#sidebar')
        self.assertIn('10.1234/offline', self.bib_rows(page),
                      'the overlay is rebuilt from durable storage, not tab memory')
        self.pending(page, 1)

        self.reconnect(page, context)
        self.pending(page, 0)
        state = self.server_fields(server, work)
        self.assertEqual(state['doi'], {'value': '10.1234/offline', 'revision': 1})
        cached = page.evaluate(
            "id => window.createPrksOfflineStore().getEntity('work', id).then(r => r.value.doi)", work)
        self.assertEqual(cached, '10.1234/offline', 'the acknowledgement reconciled the cached Work')

    def test_one_save_across_several_fields_is_one_durable_unit(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        self.offline(page, context)
        for name, value in (('doi', '10.1/multi'), ('isbn', '978-multi'), ('journal', 'Multi Journal')):
            self.field(page, name, value)
        self.save(page)
        self.pending(page, 3)
        self.assertEqual(sorted(op['payload']['field'] for op in self.operations(page)),
                         ['doi', 'isbn', 'journal'])
        page.reload()
        page.wait_for_selector('#sidebar')
        rows = self.bib_rows(page)
        for value in ('10.1/multi', '978-multi', 'Multi Journal'):
            self.assertIn(value, rows)
        self.reconnect(page, context)
        self.pending(page, 0)
        state = self.server_fields(server, work)
        self.assertEqual(state['doi']['value'], '10.1/multi')
        self.assertEqual(state['isbn']['value'], '978-multi')
        self.assertEqual(state['journal']['value'], 'Multi Journal')

    def test_repeated_edits_coalesce_and_returning_to_base_cancels(self):
        server, page, context = self.start()
        self.offline(page, context)
        for value in ('first', 'second', 'third'):
            self.field(page, 'doi', value)
            self.save(page)
            self.pending(page, 1)
        rows = self.operations(page)
        self.assertEqual(rows[0]['payload']['value'], 'third')
        self.assertEqual(rows[0]['base_revision'], 0, 'still measured against the observed base')
        # Editing back to the acknowledged value leaves no intent at all.
        self.field(page, 'doi', '')
        self.save(page)
        self.pending(page, 0)
        self.assertNotIn('DOI', self.bib_rows(page))

    # ---- the point of the milestone ----------------------------------------

    def test_different_fields_synchronize_independently(self):
        """Two devices editing DOI and ISBN have not disagreed about anything.
        A single Work-level revision would tell them they had."""
        server, page, context = self.start()
        work = server.ids['work_a']
        self.offline(page, context)
        self.field(page, 'doi', 'device-doi')
        self.save(page)
        self.pending(page, 1)
        # Another device changes a DIFFERENT field while this one is away.
        self.db_for(server).update_work_metadata(work, {'isbn': 'other-device-isbn'})
        self.reconnect(page, context)
        self.pending(page, 0)
        state = self.server_fields(server, work)
        self.assertEqual(state['doi']['value'], 'device-doi')
        self.assertEqual(state['isbn']['value'], 'other-device-isbn',
                         'the other device\'s field survived')
        self.assertEqual([r['status'] for r in self.db_for(server).execute_query(
            "SELECT status FROM sync_operations WHERE operation_type = 'SET_WORK_METADATA_FIELD'")],
            ['ACKNOWLEDGED'], 'no conflict was produced')

    def test_a_convergent_edit_is_not_a_conflict(self):
        """Two people typing the same DOI have converged, not collided."""
        server, page, context = self.start()
        work = server.ids['work_a']
        self.offline(page, context)
        self.field(page, 'doi', '10.1/agreed')
        self.save(page)
        self.pending(page, 1)
        self.db_for(server).update_work_metadata(work, {'doi': '10.1/agreed'})
        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(self.server_fields(server, work)['doi'],
                         {'value': '10.1/agreed', 'revision': 1},
                         'convergence advances nothing further')

    def test_same_field_conflict_leaves_the_other_fields_working(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        self.offline(page, context)
        self.field(page, 'doi', 'device-doi')
        self.field(page, 'journal', 'device-journal')
        self.save(page)
        self.pending(page, 2)
        self.db_for(server).update_work_metadata(work, {'doi': 'server-doi'})
        self.reconnect(page, context)
        self.settled_conflicts(page, 1)

        rows = self.operations(page)
        self.assertEqual(rows[0]['payload']['field'], 'doi')
        self.assertEqual(rows[0]['status'], 'conflict')
        self.assertEqual(rows[0]['server_result']['current_value'], 'server-doi')
        self.assertEqual(self.server_fields(server, work)['journal']['value'], 'device-journal',
                         'the field that did not collide synchronized normally')
        # Only the conflicting input is blocked.
        self.assertTrue(page.locator('[data-prks-work-field="doi"]').is_disabled())
        self.assertFalse(page.locator('[data-prks-work-field="journal"]').is_disabled())
        self.assertIn('server-doi', page.locator('[data-prks-role="work-bib-sync"]').inner_text())

    def test_use_server_resolves_one_field_without_touching_the_server(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        self.offline(page, context)
        self.field(page, 'doi', 'device-doi')
        self.save(page)
        self.pending(page, 1)
        self.db_for(server).update_work_metadata(work, {'doi': 'server-doi'})
        self.reconnect(page, context)
        self.settled_conflicts(page, 1)

        page.get_by_role('button', name='Use server', exact=True).click()
        self.pending(page, 0)
        self.assertEqual(self.operations(page), [], 'resolving creates no replacement operation')
        self.assertEqual(self.server_fields(server, work)['doi'],
                         {'value': 'server-doi', 'revision': 1}, 'the server was not mutated again')
        self.wait_for_bib_rows(page, 'server-doi')
        cached = page.evaluate(
            "id => window.createPrksOfflineStore().getEntity('work-metadata-state', id)"
            "   .then(r => r.value.fields.doi)", work)
        self.assertEqual(cached, {'value': 'server-doi', 'revision': 1})

    def test_apply_my_value_creates_a_new_operation_against_the_server_revision(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        self.offline(page, context)
        self.field(page, 'doi', 'device-doi')
        self.save(page)
        self.pending(page, 1)
        original = self.operations(page)[0]['op_id']
        self.db_for(server).update_work_metadata(work, {'doi': 'server-doi'})
        self.reconnect(page, context)
        self.settled_conflicts(page, 1)

        page.get_by_role('button', name='Apply my value', exact=True).click()
        self.pending(page, 0)
        self.assertEqual(self.server_fields(server, work)['doi'],
                         {'value': 'device-doi', 'revision': 2})
        ledger = {r['op_id']: r['status'] for r in self.db_for(server).execute_query(
            "SELECT op_id, status FROM sync_operations WHERE operation_type = 'SET_WORK_METADATA_FIELD'")}
        self.assertEqual(ledger.pop(original), 'REVISION_CONFLICT')
        self.assertEqual(list(ledger.values()), ['ACKNOWLEDGED'],
                         'a NEW operation carried the reapplied value')

    # ---- protocol and durability -------------------------------------------

    def test_lost_response_applies_the_edit_once(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        seen = []

        def lose(route):
            seen.append(route.request.post_data_json['op_id'])
            response = route.fetch()
            if len(seen) == 1:
                route.abort('failed')
            else:
                route.fulfill(response=response)

        page.route('**/api/sync/operations', lose)
        self.field(page, 'doi', '10.1/once')
        self.save(page)
        self.pending(page, 0)
        self.assertGreaterEqual(len(seen), 2)
        self.assertEqual(len(set(seen)), 1, 'the retry replays the same operation id')
        self.assertEqual(self.server_fields(server, work)['doi'],
                         {'value': '10.1/once', 'revision': 1}, 'applied exactly once')

    def test_clearing_the_cache_keeps_intent_and_diagnostics_can_resolve_it(self):
        """The Work page may be gone entirely; a change the user cannot reach
        is a change they cannot decide about."""
        server, page, context = self.start()
        work = server.ids['work_a']
        self.offline(page, context)
        self.field(page, 'doi', 'device-doi')
        self.save(page)
        self.pending(page, 1)
        self.db_for(server).update_work_metadata(work, {'doi': 'server-doi'})

        page.locator('button.settings-btn').click()
        page.locator('#prks-settings-tab-diagnostics').click()
        page.locator('#prks-offline-cache-clear-btn').click()
        page.locator('#prks-modal-confirm-ok').click()
        page.wait_for_function(
            "() => document.getElementById('prks-offline-cache-status').textContent === 'Offline cache cleared.'")
        self.pending(page, 1)

        self.reconnect(page, context)
        wait_for_async(page, """() => prksSync.store.listOperations().then(rows =>
            rows.length === 1 && rows[0].status === 'conflict' && !!rows[0].server_result)""", timeout=20000)
        panel = page.locator('#prks-settings-panel-diagnostics')
        panel.locator('#prks-offline-cache-refresh-btn').click()
        # The section re-renders asynchronously; wait for the refreshed content
        # rather than reading whatever node happened to be there first.
        page.wait_for_function("""() => {
            const section = document.querySelector('[data-sync-diagnostics]');
            return !!section && section.innerText.indexOf('server-doi') !== -1;
        }""", timeout=20000)
        text = panel.locator('[data-sync-diagnostics]').inner_text()
        self.assertIn(work, text, 'the Work id is identifiable without its page')
        self.assertIn('DOI', text)
        self.assertIn('device-doi', text, 'the local value is visible')
        self.assertIn('server-doi', text, 'so is the server value')
        panel.get_by_role('button', name='Discard local change', exact=True).click()
        wait_for_async(page, "() => prksSync.store.listOperations().then(rows => rows.length === 0)")
        self.assertEqual(self.server_fields(server, work)['doi'],
                         {'value': 'server-doi', 'revision': 1})

    # ---- the cross-projection case this milestone exists for ----------------

    def recently_added(self, page):
        """Home -> Recently Added, warmed and rendered."""
        page.evaluate("() => prksNavigate('#/folders')")
        page.wait_for_selector('.prks-folder-library__tab-btn[data-tab="recently-added"]')
        page.evaluate("() => prksSwitchFolderLibraryTab('recently-added')")
        page.wait_for_selector('#prks-folder-library-recently-added')
        # The pane div exists before its first render finishes; filtering an
        # empty pane would pass or fail on timing rather than on the overlay.
        page.wait_for_function("""() => {
            const pane = document.querySelector('#prks-folder-library-recently-added');
            return !!pane && pane.children.length > 0;
        }""")

    def filter_recently_added(self, page, query):
        """Type into the tab's local filter and return the matching Work ids."""
        page.locator('#prks-folder-library-files-search').fill(query)
        page.wait_for_timeout(150)
        return page.evaluate("""() => Array.from(
            document.querySelectorAll('#prks-folder-library-recently-added [data-work-id]')
        ).map(el => el.dataset.workId)""")

    def cached_recently_added_publisher(self, page, work_id):
        return page.evaluate("""id => window.createPrksOfflineStore().getList('recently-added:index')
            .then(row => {
                if (!row) return null;
                const found = row.value.find(w => w.id === id);
                return found ? found.publisher : null;
            })""", work_id)

    def test_pending_publisher_is_searchable_in_recently_added_before_it_syncs(self):
        """The core of this milestone. Recently Added filters locally over
        Publisher, so a pending value has to reach that projection's filtering
        even though no card renders it -- while the acknowledged snapshot on
        disk still says what the server said."""
        server, page, context = self.start()
        work = server.ids['work_a']
        self.db_for(server).update_work_metadata(work, {'publisher': 'Elsevier'})
        page.reload()
        page.wait_for_selector('#sidebar')
        self.recently_added(page)
        o._wait_list_cached(page, 'recently-added:index')
        self.assertIn(work, self.filter_recently_added(page, 'Elsevier'))

        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        self.field(page, 'publisher', 'Springer')
        self.save(page)
        self.pending(page, 1)

        self.recently_added(page)
        self.assertIn(work, self.filter_recently_added(page, 'Springer'),
                      'the pending publisher is searchable immediately')
        self.assertNotIn(work, self.filter_recently_added(page, 'Elsevier'),
                         'the value it replaced stops matching')
        self.assertEqual(self.cached_recently_added_publisher(page, work), 'Elsevier',
                         'the acknowledged snapshot is untouched until the server answers')

    def test_the_pending_publisher_overlay_survives_a_reload(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        self.db_for(server).update_work_metadata(work, {'publisher': 'Elsevier'})
        page.reload()
        page.wait_for_selector('#sidebar')
        self.recently_added(page)
        o._wait_list_cached(page, 'recently-added:index')

        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        self.field(page, 'publisher', 'Springer')
        self.save(page)
        self.pending(page, 1)

        page.reload()
        page.wait_for_selector('#sidebar')
        self.recently_added(page)
        self.assertIn(work, self.filter_recently_added(page, 'Springer'),
                      'the overlay is rebuilt from durable storage, not tab memory')
        self.pending(page, 1)

    def test_acknowledgement_reconciles_the_projection_and_retires_the_overlay(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        self.db_for(server).update_work_metadata(work, {'publisher': 'Elsevier'})
        page.reload()
        page.wait_for_selector('#sidebar')
        self.recently_added(page)
        o._wait_list_cached(page, 'recently-added:index')

        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        self.field(page, 'publisher', 'Springer')
        self.save(page)
        self.pending(page, 1)
        self.reconnect(page, context)
        self.pending(page, 0)

        self.assertEqual(self.server_fields(server, work)['publisher'],
                         {'value': 'Springer', 'revision': 2})
        self.assertEqual(self.cached_recently_added_publisher(page, work), 'Springer',
                         'the acknowledged projection row was patched, not dropped')
        self.recently_added(page)
        self.assertIn(work, self.filter_recently_added(page, 'Springer'),
                      'the value survives the overlay being retired')

    # ---- the high fan-out case (2G) ----------------------------------------

    # Progress renders one status at a time; Work A is seeded "In Progress".
    PROGRESS = '#/progress?status=In%20Progress'
    PROGRESS_NOT_STARTED = '#/progress?status=Not%20Started'

    def seed_dates(self, server, page, year='1970', published='1954-06-07'):
        """Give Work A an acknowledged Year and Published Date, then reload so
        every cached projection holds them."""
        self.db_for(server).update_work_metadata(
            server.ids['work_a'], {'year': year, 'published_date': published})
        page.reload()
        page.wait_for_selector('#sidebar')

    def warm_browse(self, page):
        """Visit the three browse catalogs so all three snapshots are cached."""
        for route, key in ((self.PROGRESS, 'works-browse:index'),
                           ('#/recent', 'recent:index')):
            page.evaluate("r => prksNavigate(r)", route)
            page.wait_for_selector('[data-work-id]')
            o._wait_list_cached(page, key)
        self.recently_added(page)
        o._wait_list_cached(page, 'recently-added:index')

    def card_meta(self, page, route, work_id):
        """The meta row of one Work card on a browse route."""
        page.evaluate("r => prksNavigate(r)", route)
        page.wait_for_selector('[data-work-id="%s"]' % work_id)
        return page.evaluate("""id => {
            const el = document.querySelector('[data-work-id="' + id + '"] .work-card__meta');
            return el ? el.textContent : '';
        }""", work_id)

    def cached_list_field(self, page, list_key, work_id, field):
        return page.evaluate("""([key, id, field]) =>
            window.createPrksOfflineStore().getList(key).then(row => {
                if (!row) return null;
                const found = row.value.find(w => w.id === id);
                return found ? found[field] : null;
            })""", [list_key, work_id, field])

    def cached_person_work_field(self, page, person_id, work_id, field):
        return page.evaluate("""([pid, wid, field]) =>
            window.createPrksOfflineStore().getEntity('person', pid).then(row => {
                if (!row) return null;
                const works = (row.value && row.value.works) || [];
                const found = works.find(w => w.id === wid);
                return found ? found[field] : null;
            })""", [person_id, work_id, field])

    def test_a_pending_year_reaches_every_surface_that_shows_one(self):
        """The reason 2G is its own milestone. Publisher reached ONE cached
        list; a Year is on every Work card, so one offline edit has to be seen
        by all three browse catalogs and by the Work summaries embedded in
        cached Person, Folder and Playlist details -- while every acknowledged
        snapshot on disk still says exactly what the server said."""
        server, page, context = self.start()
        work, person = server.ids['work_a'], server.ids['person']
        self.seed_dates(server, page)
        self.warm_browse(page)
        o._open_person(page, person)
        page.wait_for_selector('[data-work-id="%s"]' % work)
        o._wait_entity_cached(page, 'person', person)

        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        self.field(page, 'year', '2026')
        self.save(page)
        self.pending(page, 1)

        for route in (self.PROGRESS, '#/recent'):
            self.assertIn('2026', self.card_meta(page, route, work), route)
            self.assertNotIn('1970', self.card_meta(page, route, work), route)
        self.recently_added(page)
        self.assertIn('2026', page.evaluate("""id => {
            const el = document.querySelector('#prks-folder-library-recently-added [data-work-id="'
                + id + '"] .work-card__meta');
            return el ? el.textContent : '';
        }""", work), 'Recently Added shows the pending year')

        o._open_person(page, person)
        page.wait_for_selector('[data-work-id="%s"]' % work)
        self.assertIn('2026', page.evaluate("""id => {
            const el = document.querySelector('[data-work-id="' + id + '"] .work-card__meta');
            return el ? el.textContent : '';
        }""", work), 'the embedded Person summary shows it too')

        for key in ('works-browse:index', 'recent:index', 'recently-added:index'):
            self.assertEqual(self.cached_list_field(page, key, work, 'year'), '1970',
                             '%s must still hold the acknowledged value' % key)
        self.assertEqual(self.cached_person_work_field(page, person, work, 'year'), '1970',
                         'the cached Person detail is never rewritten with a pending value')

    def test_a_pending_year_is_searchable_in_recently_added(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        self.seed_dates(server, page)
        self.recently_added(page)
        o._wait_list_cached(page, 'recently-added:index')

        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        self.field(page, 'year', '2026')
        self.save(page)
        self.pending(page, 1)

        self.recently_added(page)
        self.assertIn(work, self.filter_recently_added(page, '2026'),
                      'the pending year is searchable immediately')
        self.assertNotIn(work, self.filter_recently_added(page, '1970'),
                         'the value it replaced stops matching')

    def test_the_year_overlay_survives_a_reload_on_an_embedded_summary(self):
        """A reload empties the in-page overlay map. Opening a cached Person
        profile directly then has to hydrate it from durable storage BEFORE
        rendering -- otherwise the card shows the value the user replaced and
        silently corrects itself only when some other surface reads the
        queue."""
        server, page, context = self.start()
        work, person = server.ids['work_a'], server.ids['person']
        self.seed_dates(server, page)
        o._open_person(page, person)
        page.wait_for_selector('[data-work-id="%s"]' % work)
        o._wait_entity_cached(page, 'person', person)

        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        self.field(page, 'year', '2026')
        self.save(page)
        self.pending(page, 1)

        page.reload()
        page.wait_for_selector('#sidebar')
        o._open_person(page, person)
        page.wait_for_selector('[data-work-id="%s"]' % work)
        page.wait_for_function("""id => {
            const el = document.querySelector('[data-work-id="' + id + '"] .work-card__meta');
            return !!el && el.textContent.indexOf('2026') !== -1;
        }""", arg=work)
        self.pending(page, 1)

    def test_acknowledgement_patches_every_cached_representation(self):
        """Patched, not invalidated: dropping three whole domains for a
        one-field edit would cost the user every cached Folder, profile and
        playlist for a change whose exact shape is already known."""
        server, page, context = self.start()
        work, person = server.ids['work_a'], server.ids['person']
        self.seed_dates(server, page)
        self.warm_browse(page)
        o._open_person(page, person)
        page.wait_for_selector('[data-work-id="%s"]' % work)
        o._wait_entity_cached(page, 'person', person)

        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        self.field(page, 'year', '2026')
        self.save(page)
        self.pending(page, 1)
        self.reconnect(page, context)
        self.pending(page, 0)

        self.assertEqual(self.server_fields(server, work)['year'],
                         {'value': '2026', 'revision': 2})
        for key in ('works-browse:index', 'recent:index', 'recently-added:index'):
            self.assertEqual(self.cached_list_field(page, key, work, 'year'), '2026',
                             '%s was patched, not dropped' % key)
        self.assertEqual(self.cached_person_work_field(page, person, work, 'year'), '2026',
                         'the embedded Person summary was patched in place')
        self.assertIn('2026', self.card_meta(page, self.PROGRESS, work),
                      'the value survives the overlay being retired')

    def test_a_cleared_year_falls_back_to_the_pending_published_date(self):
        """The displayed year is DERIVED: an explicit Year wins, and the
        Published Date supplies it otherwise. Both are synchronized precisely
        because an edit to either moves what every card shows."""
        server, page, context = self.start()
        work = server.ids['work_a']
        self.seed_dates(server, page)
        self.warm_browse(page)

        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        self.field(page, 'year', '')
        self.save(page)
        self.pending(page, 1)
        self.assertIn('1954', self.card_meta(page, self.PROGRESS, work),
                      'with no Year the Published Date supplies it')

        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.field(page, 'published_date', '09/09/1999')
        self.save(page)
        self.pending(page, 2)
        self.assertIn('1999', self.card_meta(page, self.PROGRESS, work),
                      'and a pending Published Date moves it again')

    def test_an_uninterpretable_date_is_refused_before_anything_is_stored(self):
        server, page, context = self.start()
        self.seed_dates(server, page)
        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        self.field(page, 'published_date', '31/02/2026')
        self.save(page)
        # textContent, not visibility: the editor group lives inside a
        # collapsed <details>, so the message is attached but not rendered.
        page.wait_for_function("""() => {
            const el = document.getElementById('meta-date-error');
            return !!el && el.textContent.trim().length > 0;
        }""")
        self.assertEqual(self.operations(page), [],
                         'nothing is enqueued for a date PRKS would have to guess at')

    def test_a_pending_published_date_reaches_a_cached_playlist_subtitle(self):
        """A Playlist item's subtitle is built from the Published Date, so the
        edit has to reach the Work summaries embedded in the cached Playlist --
        a third cached representation, in a third shape (`items`, not
        `works`)."""
        server, page, context = self.start()
        work = server.ids['work_a']
        self.seed_dates(server, page)
        playlist = page.evaluate("""async workId => {
            const id = await createPlaylist('E2E Year Playlist', '');
            await addWorkToPlaylist(id, workId);
            return id;
        }""", work)
        # Both writes are durable, so the playlist is only a CACHED entity once
        # the server has actually been told about it -- and this test is about
        # what an overlay does to a cached one.
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=30000,
            message='the playlist never reached the server')
        page.evaluate("id => prksNavigate('#/playlists/' + encodeURIComponent(id))", playlist)
        page.wait_for_selector('.prks-playlist-detail')
        page.wait_for_selector('[data-pl-nav="%s"]' % work)
        o._wait_entity_cached(page, 'playlist', playlist)
        self.assertIn('07/06/1954', page.evaluate("""id => {
            const el = document.querySelector('[data-pl-nav="' + id + '"] .meta-row');
            return el ? el.textContent : '';
        }""", work), 'the acknowledged date is shown first')

        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        self.field(page, 'published_date', '09/09/1999')
        self.save(page)
        self.pending(page, 1)

        page.evaluate("id => prksNavigate('#/playlists/' + encodeURIComponent(id))", playlist)
        page.wait_for_selector('[data-pl-nav="%s"]' % work)
        page.wait_for_function("""id => {
            const el = document.querySelector('[data-pl-nav="' + id + '"] .meta-row');
            return !!el && el.textContent.indexOf('09/09/1999') !== -1;
        }""", arg=work)
        self.assertEqual(page.evaluate("""([pid, wid]) =>
            window.createPrksOfflineStore().getEntity('playlist', pid).then(row => {
                const items = (row && row.value && row.value.items) || [];
                const found = items.find(w => w.id === wid);
                return found ? found.published_date : null;
            })""", [playlist, work]), '1954-06-07',
            'the cached Playlist still holds exactly what the server said')

    def test_year_and_published_date_synchronize_independently(self):
        """They are rendered by the same derived value, which is exactly why a
        conflict on one must not block the other: they are still two fields and
        two people can change them without disagreeing."""
        server, page, context = self.start()
        work = server.ids['work_a']
        self.seed_dates(server, page)
        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        self.field(page, 'year', '2026')
        self.field(page, 'published_date', '09/09/1999')
        self.save(page)
        self.pending(page, 2)

        # Someone else changes only the Year while this device is away.
        self.db_for(server).update_work_metadata(work, {'year': '1988'})
        self.reconnect(page, context)
        self.settled_conflicts(page, 1)
        fields = self.server_fields(server, work)
        self.assertEqual(fields['published_date']['value'], '1999-09-09',
                         'the Published Date synchronized while the Year conflicted')
        self.assertEqual(fields['year']['value'], '1988', 'and the Year was not overwritten')

    # ---- Status: a pending field that changes GROUP MEMBERSHIP (2H) --------

    STATUS_SECTION = '[data-prks-role="work-status-editor"]'

    def set_status(self, page, value):
        """The control is a segmented button group whose value lives on a
        hidden input, so a user sets it by clicking, not by typing."""
        page.locator('%s .prks-segmented__btn[data-value="%s"]' % (self.STATUS_SECTION, value)).click()

    def save_status(self, page):
        page.locator('#save-work-status-btn').click()

    def progress_ids(self, page, status):
        """The Work ids Progress shows for one status group."""
        page.evaluate("s => prksNavigate('#/progress?status=' + encodeURIComponent(s))", status)
        page.wait_for_function("""() => {
            const el = document.querySelector('.prks-tile--main');
            return !!el && !el.querySelector('.prks-route-loading');
        }""")
        page.wait_for_timeout(120)
        return page.evaluate(
            "() => Array.from(document.querySelectorAll('[data-work-id]')).map(el => el.dataset.workId)")

    def card_badge(self, page, work_id):
        return page.evaluate("""id => {
            const el = document.querySelector('[data-work-id="' + id + '"] .status-badge');
            return el ? el.textContent.trim() : '';
        }""", work_id)

    def cached_status(self, page, list_key, work_id):
        return self.cached_list_field(page, list_key, work_id, 'status')

    def warm_all_catalogs(self, page, work_id, status='In Progress', open_title=None):
        """All three browse snapshots on disk, each containing this Work. A
        test that asserts a list was patched must first have that list: a
        MISSING snapshot is left missing by design, and asserting over one
        would pass or fail on whether the route happened to be visited.

        `status` selects the Progress group the Work is actually in, and
        `open_title` opens it first -- Recent is ordered by last-opened, so a
        Work nobody has opened is legitimately absent from it."""
        if open_title:
            o._open_work_from_home(page, open_title)
        self.progress_ids(page, status)
        o._wait_list_cached(page, 'works-browse:index')
        page.evaluate("() => prksNavigate('#/recent')")
        page.wait_for_selector('[data-work-id="%s"]' % work_id)
        o._wait_list_cached(page, 'recent:index')
        self.recently_added(page)
        o._wait_list_cached(page, 'recently-added:index')

    def test_a_pending_status_moves_the_work_between_progress_groups(self):
        """The central case of this milestone. Every field before Status
        changed what a card SAID; Status changes where the card IS. Progress
        selects one group at a time, so a pending value has to make the Work
        leave the group the server put it in and join the pending one --
        before anything has been sent."""
        server, page, context = self.start()
        work = server.ids['work_a']
        # Work A is seeded "In Progress"; warm the catalog Progress reads.
        self.assertIn(work, self.progress_ids(page, 'In Progress'))
        o._wait_list_cached(page, 'works-browse:index')

        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        self.set_status(page, 'Completed')
        self.save_status(page)
        self.pending(page, 1)
        self.assertEqual(self.operations(page)[0]['payload'],
                         {'field': 'status', 'value': 'Completed'})

        self.assertNotIn(work, self.progress_ids(page, 'In Progress'),
                         'the Work left the group the server put it in')
        self.assertIn(work, self.progress_ids(page, 'Completed'),
                      'and joined the pending one, before synchronizing')
        self.assertEqual(self.cached_status(page, 'works-browse:index', work), 'In Progress',
                         'the acknowledged catalog is untouched until the server answers')

    def test_a_pending_status_badge_reaches_every_cached_surface(self):
        """One offline edit, every representation that renders a badge: the
        three browse catalogs and the Work summaries embedded in cached
        Folder, Person and Playlist details."""
        server, page, context = self.start()
        work, person = server.ids['work_a'], server.ids['person']
        playlist = page.evaluate("""async workId => {
            const id = await createPlaylist('E2E Status Playlist', '');
            await addWorkToPlaylist(id, workId);
            return id;
        }""", work)
        self.warm_all_catalogs(page, work)
        o._open_person(page, person)
        page.wait_for_selector('[data-work-id="%s"]' % work)
        o._wait_entity_cached(page, 'person', person)
        page.evaluate("id => prksNavigate('#/playlists/' + encodeURIComponent(id))", playlist)
        page.wait_for_selector('.prks-playlist-detail')
        o._wait_entity_cached(page, 'playlist', playlist)

        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        self.set_status(page, 'Paused')
        self.save_status(page)
        self.pending(page, 1)

        page.evaluate("() => prksNavigate('#/recent')")
        page.wait_for_selector('[data-work-id="%s"]' % work)
        self.assertEqual(self.card_badge(page, work), 'Paused', 'Recently opened')
        self.recently_added(page)
        self.assertEqual(self.card_badge(page, work), 'Paused', 'Recently added')
        o._open_person(page, person)
        page.wait_for_selector('[data-work-id="%s"]' % work)
        self.assertEqual(self.card_badge(page, work), 'Paused', 'the embedded Person summary')

        for key in ('works-browse:index', 'recent:index', 'recently-added:index'):
            self.assertEqual(self.cached_status(page, key, work), 'In Progress',
                             '%s still holds what the server said' % key)
        self.assertEqual(self.cached_person_work_field(page, person, work, 'status'), 'In Progress',
                         'and so does the cached Person detail')

    def test_the_status_overlay_survives_a_reload(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        self.progress_ids(page, 'In Progress')
        o._wait_list_cached(page, 'works-browse:index')

        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        self.set_status(page, 'Completed')
        self.save_status(page)
        self.pending(page, 1)

        page.reload()
        page.wait_for_selector('#sidebar')
        self.assertIn(work, self.progress_ids(page, 'Completed'),
                      'membership is rebuilt from durable storage, not tab memory')
        self.assertEqual(self.card_badge(page, work), 'Completed')
        self.pending(page, 1)

    def test_acknowledgement_keeps_the_work_in_its_new_group(self):
        """Retiring the overlay must not flicker the Work back: the
        acknowledged rows are patched to the value the overlay was already
        showing, so nothing visibly changes when the server answers."""
        server, page, context = self.start()
        work = server.ids['work_a']
        self.warm_all_catalogs(page, work)

        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        self.set_status(page, 'Completed')
        self.save_status(page)
        self.pending(page, 1)
        self.reconnect(page, context)
        self.pending(page, 0)

        self.assertEqual(self.server_fields(server, work)['status'],
                         {'value': 'Completed', 'revision': 1})
        for key in ('works-browse:index', 'recent:index', 'recently-added:index'):
            self.assertEqual(self.cached_status(page, key, work), 'Completed',
                             '%s was patched, not dropped' % key)
        self.assertIn(work, self.progress_ids(page, 'Completed'),
                      'the Work stayed where the overlay had already put it')
        self.assertNotIn(work, self.progress_ids(page, 'In Progress'))

    def test_a_status_conflict_uses_the_existing_field_level_ux(self):
        """Two devices genuinely disagreed about this Work's status, so the
        user decides -- and only about Status. No new conflict architecture."""
        server, page, context = self.start()
        work = server.ids['work_a']
        self.offline(page, context)
        self.set_status(page, 'Completed')
        self.field(page, 'doi', 'device-doi')
        self.save_status(page)
        self.save(page)
        self.pending(page, 2)

        self.db_for(server).update_work_metadata(work, {'status': 'Paused'})
        self.reconnect(page, context)
        self.settled_conflicts(page, 1)

        conflicted = [op for op in self.operations(page) if op['status'] == 'conflict']
        self.assertEqual(len(conflicted), 1)
        self.assertEqual(conflicted[0]['payload']['field'], 'status')
        self.assertEqual(conflicted[0]['server_result']['current_value'], 'Paused')
        self.assertEqual(self.server_fields(server, work)['doi']['value'], 'device-doi',
                         'the field that did not collide synchronized normally')

        # The conflict is reported by the Status group, not the bibliographic one.
        status_text = page.locator('[data-prks-role="work-status-sync"]').inner_text()
        self.assertIn('Paused', status_text)
        self.assertIn('Completed', status_text)
        self.assertNotIn('Paused', page.locator('[data-prks-role="work-bib-sync"]').inner_text())
        for label in ('Use server', 'Apply my value'):
            self.assertEqual(page.locator('%s button' % self.STATUS_SECTION,
                                          has_text=label).count(), 1, label)

        page.locator('%s button' % self.STATUS_SECTION, has_text='Apply my value').click()
        # Wait in the BROWSER, not by polling the database: constructing a
        # PRKSDatabase per poll contends with the server's own writer, which is
        # exactly the commit being waited for.
        self.pending(page, 0)
        self.assertEqual(self.server_fields(server, work)['status'],
                         {'value': 'Completed', 'revision': 2})

    def test_a_bulk_status_change_conflicts_with_an_offline_device(self):
        """The bulk action is an ordinary canonical mutation: it advances the
        same revision this device observed, so an edit made while away is a
        real disagreement rather than a silent overwrite."""
        server, page, context = self.start()
        work = server.ids['work_a']
        self.offline(page, context)
        self.set_status(page, 'Completed')
        self.save_status(page)
        self.pending(page, 1)

        self.db_for(server).bulk_update_works(
            {'work_ids': [work], 'action': 'set_status', 'status': 'Paused'})
        self.reconnect(page, context)
        self.settled_conflicts(page, 1)
        result = self.operations(page)[0]['server_result']
        self.assertEqual(result['code'], 'REVISION_CONFLICT')
        self.assertEqual(result['current_value'], 'Paused')
        self.assertEqual(self.server_fields(server, work)['status']['value'], 'Paused',
                         'the away device did not overwrite the bulk change')

    def test_status_left_the_online_save(self):
        """One mutation path, online and offline. Two would mean the one that
        is not revision-aware silently overwrites the other's conflicts."""
        server, page, context = self.start()
        work = server.ids['work_a']
        seen = self.record_paths(page)
        self.set_status(page, 'Planned')
        self.save_status(page)
        self.pending(page, 0)
        self.assertEqual([url for method, url in seen if method == 'PATCH'], [],
                         'a status change is never a PATCH, even online')
        self.assertEqual(self.server_fields(server, work)['status'],
                         {'value': 'Planned', 'revision': 1})

    # ---- author_text: the field whose value is not always what is shown (2I) --

    def credit(self, page, work_id):
        """The credit half of a Work card's meta line."""
        return page.evaluate("""id => {
            const el = document.querySelector('[data-work-id="' + id + '"] .work-card__meta');
            return el ? el.textContent.trim() : '';
        }""", work_id)

    def open_work_b(self, page, editing=True):
        o._open_work_from_home(page, WORK_B_TITLE)
        if editing:
            self.edit(page)

    def test_a_pending_author_text_becomes_the_credit_with_no_linked_author(self):
        """Work B has no linked Author, so `author_text` IS its credit. The
        pending value has to reach every cached card, survive a reload, and
        still be there after the server answers -- without the card visibly
        changing at acknowledgement."""
        server, page, context = self.start()
        work = server.ids['work_b']
        self.db_for(server).update_work_metadata(work, {'author_text': 'Old Author'})
        page.reload()
        page.wait_for_selector('#sidebar')
        self.warm_all_catalogs(page, work, status='Not Started', open_title=WORK_B_TITLE)
        self.assertIn('Old Author', self.credit(page, work))

        self.open_work_b(page)
        self.offline(page, context)
        self.field(page, 'author_text', 'New Author')
        self.save(page)
        self.pending(page, 1)
        self.assertEqual(self.operations(page)[0]['payload'],
                         {'field': 'author_text', 'value': 'New Author'})

        for route in (self.PROGRESS_NOT_STARTED, '#/recent'):
            page.evaluate("r => prksNavigate(r)", route)
            page.wait_for_selector('[data-work-id="%s"]' % work)
            self.assertIn('New Author', self.credit(page, work), route)
            self.assertNotIn('Old Author', self.credit(page, work), route)
        self.recently_added(page)
        self.assertIn('New Author', self.credit(page, work), 'Recently added')
        self.assertEqual(self.cached_list_field(page, 'recent:index', work, 'author_text'),
                         'Old Author', 'the acknowledged snapshot is untouched')

        page.reload()
        page.wait_for_selector('#sidebar')
        page.evaluate("r => prksNavigate(r)", self.PROGRESS_NOT_STARTED)
        page.wait_for_selector('[data-work-id="%s"]' % work)
        self.assertIn('New Author', self.credit(page, work), 'after a reload')

        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(self.server_value(server, work, 'author_text'), 'New Author')
        page.evaluate("r => prksNavigate(r)", self.PROGRESS_NOT_STARTED)
        page.wait_for_selector('[data-work-id="%s"]' % work)
        self.assertIn('New Author', self.credit(page, work), 'no reversion at acknowledgement')
        self.assertEqual(self.cached_list_field(page, 'recent:index', work, 'author_text'),
                         'New Author', 'the acknowledged row was patched')

    def test_a_linked_author_outranks_a_pending_author_text(self):
        """The central invariant. Work A has a linked Author, so changing the
        textual author changes the FIELD -- the editor shows it -- while the
        card keeps crediting the linked person. Synchronizing a field is not
        permission to take over how a credit is composed."""
        server, page, context = self.start()
        work = server.ids['work_a']
        self.db_for(server).update_work_metadata(work, {'author_text': 'Old Text'})
        page.reload()
        page.wait_for_selector('#sidebar')
        self.warm_all_catalogs(page, work)
        self.assertIn(PERSON_DISPLAY, self.credit(page, work),
                      'the linked Author is the credit to begin with')

        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        self.field(page, 'author_text', 'New Text')
        self.save(page)
        self.pending(page, 1)

        self.assertEqual(page.locator('[data-prks-work-field="author_text"]').input_value(),
                         'New Text', 'the editor shows the pending FIELD')
        page.evaluate("r => prksNavigate(r)", self.PROGRESS)
        page.wait_for_selector('[data-work-id="%s"]' % work)
        self.assertIn(PERSON_DISPLAY, self.credit(page, work),
                      'the card still credits the linked Author')
        self.assertNotIn('New Text', self.credit(page, work))

        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(self.server_value(server, work, 'author_text'), 'New Text')
        page.evaluate("r => prksNavigate(r)", self.PROGRESS)
        page.wait_for_selector('[data-work-id="%s"]' % work)
        self.assertIn(PERSON_DISPLAY, self.credit(page, work),
                      'and still does once the server knows')

    def test_clearing_author_text_reveals_the_linked_editor(self):
        """The fallback runs in the other direction too -- which is why the
        overlay has to happen BEFORE the credit is composed. Patching a
        rendered credit could never reveal a different person."""
        server, page, context = self.start()
        work, person = server.ids['work_b'], server.ids['person']
        db = self.db_for(server)
        db.update_work_metadata(work, {'author_text': 'Text Author'})
        db.add_role(person, work, 'Editor')
        page.reload()
        page.wait_for_selector('#sidebar')
        self.warm_all_catalogs(page, work, status='Not Started', open_title=WORK_B_TITLE)
        self.assertIn('Text Author', self.credit(page, work))

        self.open_work_b(page)
        self.offline(page, context)
        self.field(page, 'author_text', '')
        self.save(page)
        self.pending(page, 1)

        page.evaluate("r => prksNavigate(r)", self.PROGRESS_NOT_STARTED)
        page.wait_for_selector('[data-work-id="%s"]' % work)
        credit = self.credit(page, work)
        self.assertIn(PERSON_DISPLAY, credit, 'the linked Editor stands in')
        self.assertIn('Editor', credit)
        self.assertNotIn('Text Author', credit)

        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(self.server_value(server, work, 'author_text'), '')
        page.evaluate("r => prksNavigate(r)", self.PROGRESS_NOT_STARTED)
        page.wait_for_selector('[data-work-id="%s"]' % work)
        self.assertIn(PERSON_DISPLAY, self.credit(page, work))

    def test_a_large_author_text_survives_the_whole_durable_round_trip(self):
        """8 KiB is well past the point where the compact-result path matters:
        the old contract would have put the whole value in the server ledger
        and, on a conflict, tried to store two of them in a 2 KB durable
        result. Small enough to keep the browser test cheap."""
        server, page, context = self.start()
        work = server.ids['work_b']
        big = 'Q' * (8 * 1024)
        self.open_work_b(page)
        self.offline(page, context)
        self.field(page, 'author_text', big)
        self.save(page)
        self.pending(page, 1)
        self.assertEqual(len(self.operations(page)[0]['payload']['value']), len(big))

        page.reload()
        page.wait_for_selector('#sidebar')
        self.open_work_b(page)
        self.assertEqual(len(page.locator('[data-prks-work-field="author_text"]').input_value()),
                         len(big), 'the pending value survives a reload in full')
        self.pending(page, 1)

        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(self.server_fields(server, work)['author_text'], {'revision': 1},
                         'the projection carries the revision alone')
        self.assertEqual(
            self.db_for(server).get_work(work)['author_text'], big,
            'while the Work record has the whole value')
        ledger = self.db_for(server).execute_query(
            "SELECT result_json FROM sync_operations "
            "WHERE operation_type = 'SET_WORK_METADATA_FIELD'")[0]['result_json']
        self.assertNotIn(big, ledger, 'the ledger never became a second copy of it')
        self.assertLess(len(ledger), 1024)

        # The credit is still composed correctly from the acknowledged value.
        page.evaluate("r => prksNavigate(r)", self.PROGRESS_NOT_STARTED)
        page.wait_for_selector('[data-work-id="%s"]' % work)
        self.assertIn('QQQ', self.credit(page, work))

    def test_a_large_author_text_conflict_still_fits_the_durable_result(self):
        """Two 8 KiB values in one conflict would be four times the browser's
        2 KB structured-result bound. Without the bounded representation the
        operation could not settle at all -- it would fail to store its own
        outcome rather than reach the user, which is worse than either value
        winning."""
        server, page, context = self.start()
        work = server.ids['work_b']
        self.open_work_b(page)
        self.offline(page, context)
        self.field(page, 'author_text', 'D' * (8 * 1024))
        self.save(page)
        self.pending(page, 1)

        self.db_for(server).update_work_metadata(work, {'author_text': 'S' * (8 * 1024)})
        self.reconnect(page, context)
        self.settled_conflicts(page, 1)

        op = self.operations(page)[0]
        result = op['server_result']
        self.assertEqual(result['code'], 'REVISION_CONFLICT')
        self.assertNotIn('current_value', result)
        self.assertEqual(result['current_bytes'], 8 * 1024)
        self.assertEqual(result['requested_bytes'], 8 * 1024)
        self.assertLessEqual(len(json.dumps(result)), 2048,
                             'the stored terminal result fits the durable bound')
        # The user's own value is still there in full, in the immutable payload.
        self.assertEqual(len(op['payload']['value']), 8 * 1024)
        # And the conflict reached the UI with sizes rather than two values.
        sync_text = page.locator('[data-prks-role="work-bib-sync"]').inner_text()
        self.assertIn('KB', sync_text)

        page.get_by_role('button', name='Apply my value', exact=True).click()
        self.pending(page, 0)
        self.assertEqual(self.db_for(server).get_work(work)['author_text'], 'D' * (8 * 1024))

    def test_a_conflict_the_browser_could_not_store_still_reaches_the_user(self):
        """The 2I.2 regression, end to end. The server's answer used to be
        bounded by CHARACTERS while the browser bounds by serialized BYTES, and
        one control character is one code point and six bytes as `\\u0001`. The
        oversized result was refused by durable storage, the settle failed, the
        coordinator read a failed sync, and the operation went back to pending
        -- retrying forever on the same answer. The user never saw the conflict
        and had nothing to resolve.

        Nothing here asserts a size: it asserts the operation reaches a
        TERMINAL state and the user can act on it, which is the thing that was
        actually broken."""
        server, page, context = self.start()
        work = server.ids['work_b']
        self.open_work_b(page)
        self.offline(page, context)
        self.field(page, 'author_text', 'My Author')
        self.save(page)
        self.pending(page, 1)

        # 400+ control characters: what the old character cap let through.
        self.db_for(server).update_work_metadata(
            server.ids['work_b'], {'author_text': '\x01' * 5000})
        self.reconnect(page, context)
        self.settled_conflicts(page, 1)

        op = self.operations(page)[0]
        self.assertEqual(op['status'], 'conflict',
                         'the operation settled instead of retrying forever')
        self.assertEqual(op['server_result']['code'], 'REVISION_CONFLICT')
        self.assertEqual(op['server_result']['current_bytes'], 5000)
        self.assertEqual(op['attempt_count'], 1, 'and it was not retried')
        # It survives a reload, because it actually reached durable storage.
        page.reload()
        page.wait_for_selector('#sidebar')
        self.assertEqual(self.operations(page)[0]['status'], 'conflict')

        # And the user can resolve it.
        self.open_work_b(page)
        page.get_by_role('button', name='Apply my value', exact=True).click()
        self.pending(page, 0)
        self.assertEqual(self.server_value(server, work, 'author_text'), 'My Author')

    def test_a_rename_saves_durably_and_synchronizes(self):
        """Title is the widest synchronized field; this is the smoke test that
        its durable group works end to end."""
        server, page, context = self.start()
        work = server.ids['work_a']
        self.field(page, 'title', 'A Durable Rename')
        page.locator('#save-work-identity-btn').click()
        self.pending(page, 0)
        self.assertEqual(self.db_for(server).get_work(work)['title'], 'A Durable Rename')
        self.assertEqual(self.server_fields(server, work)['title'], {'revision': 1})

    def pick_doc_type(self, page, value):
        """Choose a document type through the real menu.

        The field's value lives on a hidden input, so filling it directly would
        bypass the presentation the user actually operates -- and the menu is
        exactly what 2L had to keep in step with the synchronized value.
        """
        page.locator('#meta-doc-type-trigger').click()
        page.locator('#meta-doc-type-listbox [role="option"][data-value="%s"]' % value).click()
        page.wait_for_function(
            "v => document.getElementById('meta-doc-type').value === v", arg=value)

    def type_group_ids(self, page, doc_type):
        """The Work ids File Types files under one type.

        Read from the type's own page rather than the index, because the index
        shows counts: the assertion is about which group a Work is IN, and a
        count that happens to match would not say that.
        """
        page.evaluate("r => prksNavigate(r)", '#/types/' + doc_type)
        page.wait_for_selector('.types-page')
        return page.evaluate("""() => Array.from(
            document.querySelectorAll('.types-page [data-work-id]'))
            .map(el => el.getAttribute('data-work-id'))""")

    def test_a_pending_doc_type_moves_the_work_between_type_groups(self):
        """doc_type is a grouping key, not a label: a pending change has to
        move the Work in the surface that groups on it, before it syncs."""
        server, page, context = self.start()
        work = server.ids['work_a']
        before = self.db_for(server).get_work(work)['doc_type']
        self.assertNotEqual(before, 'phdthesis')
        # Types reads the browse catalog, so that catalog has to be cached
        # before the network goes away or there is nothing to group at all.
        self.warm_browse(page)
        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        self.pick_doc_type(page, 'phdthesis')
        page.locator('#save-work-identity-btn').click()
        self.pending(page, 1)

        self.assertIn(work, self.type_group_ids(page, 'phdthesis'),
                      'the pending type decides where the Work is filed')
        self.assertNotIn(work, self.type_group_ids(page, before),
                         'and it has left the group it was filed under')
        self.assertEqual(self.db_for(server).get_work(work)['doc_type'], before,
                         'while the server still holds the old one')

        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(self.db_for(server).get_work(work)['doc_type'], 'phdthesis')
        self.assertIn(work, self.type_group_ids(page, 'phdthesis'),
                      'and the acknowledged type keeps it there')

    def test_a_pending_title_reaches_every_surface_that_names_the_work(self):
        """A Title is the most widely copied Work value there is: browse rows,
        embedded summaries and the labels other domains hold by reference. One
        offline rename has to be true on all of them at once, or the library
        shows a user two different names for one file."""
        server, page, context = self.start()
        work = server.ids['work_a']
        renamed = 'A Rename Seen Everywhere'
        self.warm_browse(page)
        o._open_person(page, server.ids['person'])
        o._wait_entity_cached(page, 'person', server.ids['person'])
        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)

        self.offline(page, context)
        self.field(page, 'title', renamed)
        page.locator('#save-work-identity-btn').click()
        self.pending(page, 1)

        for route in (self.PROGRESS, '#/recent'):
            page.evaluate("r => prksNavigate(r)", route)
            page.wait_for_selector('[data-work-id="%s"]' % work)
            self.assertIn(renamed, page.evaluate(
                "id => document.querySelector('[data-work-id=\"' + id + '\"]').textContent", work),
                'the pending title is the name on ' + route)
        self.recently_added(page)
        self.assertIn(work, self.filter_recently_added(page, renamed))
        o._open_person(page, server.ids['person'])
        o._wait_content_contains(page, renamed)

        # The cached rows themselves still hold what the server said: pending
        # intent is an overlay, never a write into the disposable cache.
        self.assertEqual(
            self.cached_list_field(page, 'works-browse:index', work, 'title'), WORK_A_TITLE)
        self.assertEqual(
            self.cached_person_work_field(page, server.ids['person'], work, 'title'), WORK_A_TITLE)

        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(self.db_for(server).get_work(work)['title'], renamed)
        self.assertEqual(
            self.cached_list_field(page, 'works-browse:index', work, 'title'), renamed,
            'the acknowledgement patches the cached row rather than dropping the catalog')
        self.assertEqual(
            self.cached_person_work_field(page, server.ids['person'], work, 'title'), renamed)

    def test_a_pending_provenance_url_is_the_link_the_detail_offers(self):
        """`source_url` on a PDF is its provenance -- the Original URL row on
        the Work detail. A pending value has to be the link that row actually
        offers, because a user who edits it and then clicks through would
        otherwise be sent to the address they just replaced."""
        server, page, context = self.start()
        work = server.ids['work_a']
        provenance = 'https://example.org/papers/the-corrected-source.pdf'
        self.offline(page, context)
        self.field(page, 'source_url', provenance)
        self.save(page)
        self.pending(page, 1)

        self.wait_for_bib_rows(page, provenance)
        self.assertEqual(
            page.evaluate("""() => {
                const a = Array.from(document.querySelectorAll('#panel-content a'))
                    .find(el => el.textContent.indexOf('example.org') !== -1);
                return a ? a.getAttribute('href') : null;
            }"""),
            provenance,
            'the link goes where the pending value says, not where the cache does')
        self.assertFalse(self.db_for(server).get_work(work)['source_url'],
                         'and the server still holds the old provenance')

        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(self.db_for(server).get_work(work)['source_url'], provenance)
        self.assertEqual(self.server_fields(server, work)['source_url'], {'revision': 1},
                         'a byte-limited field carries its revision alone')

    # ---- thumb_page: a typed value and a derived resource (2J) -------------

    def thumb_requests(self, page_obj):
        """Every thumbnail request the page makes, with its page query."""
        seen = []
        page_obj.on('request', lambda r: seen.append(r.url)
                    if '/thumbnail' in r.url else None)
        return seen

    def thumb_src(self, page_obj, work_id):
        """The thumbnail resource this card points at.

        The lazy loader promotes `data-prks-thumb-src` into `src` once the
        image is in view, so both have to be read -- checking only the data
        attribute would report "no thumbnail" for every card that had
        successfully loaded one."""
        return page_obj.evaluate("""id => {
            const img = document.querySelector('[data-work-id="' + id + '"] .work-card__thumb img');
            if (!img) return '';
            const pending = img.getAttribute('data-prks-thumb-src') || '';
            if (pending) return pending;
            const src = img.getAttribute('src') || '';
            return src.indexOf('data:') === 0 ? '' : src;
        }""", work_id)

    def test_a_pending_thumb_page_is_typed_everywhere_and_survives_a_reload(self):
        """The wire carries "5"; every cached row must carry the integer 5.
        A string there does not merely look wrong -- the row fails its own
        shape validator and the whole catalog is discarded as corrupt."""
        server, page, context = self.start()
        work = server.ids['work_a']
        self.db_for(server).update_work_metadata(work, {'thumb_page': 2})
        page.reload()
        page.wait_for_selector('#sidebar')
        self.warm_all_catalogs(page, work)

        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        self.field(page, 'thumb_page', '5')
        self.save(page)
        self.pending(page, 1)
        self.assertEqual(self.operations(page)[0]['payload'],
                         {'field': 'thumb_page', 'value': '5'},
                         'the WIRE value is a string')

        types = page.evaluate("""id => {
            const out = {};
            return Promise.all(['works-browse:index', 'recent:index', 'recently-added:index']
                .map(key => window.createPrksOfflineStore().getList(key).then(row => {
                    const found = row && row.value.find(w => w.id === id);
                    const eff = prksEffectiveProjectionRows(row.value, key.split(':')[0])
                        .find(w => w.id === id);
                    out[key] = [found ? typeof found.thumb_page : null,
                                eff ? eff.thumb_page : null,
                                eff ? typeof eff.thumb_page : null];
                }))).then(() => out);
        }""", work)
        for key, (cached_type, effective, effective_type) in types.items():
            self.assertEqual(cached_type, 'number', '%s snapshot stays typed' % key)
            self.assertEqual(effective, 5, '%s carries the pending page' % key)
            self.assertEqual(effective_type, 'number', '%s carries it as a NUMBER' % key)

        page.reload()
        page.wait_for_selector('#sidebar')
        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.assertEqual(page.locator('[data-prks-work-field="thumb_page"]').input_value(), '5',
                         'the pending page survives a reload')
        self.pending(page, 1)

        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(self.db_for(server).get_work(work)['thumb_page'], 5)
        self.assertEqual(self.server_fields(server, work)['thumb_page'],
                         {'value': '5', 'revision': 2},
                         'metadata-state keeps the wire form; the Work record is typed')

    def test_a_pending_clear_requests_page_one_not_the_page_still_stored(self):
        """The highest-value 2J regression. The server holds page 5 and a clear
        is pending, so the effective value is null -- which MEANS page 1. A URL
        with no page would still render page 5 until the server heard about it.

        Deliberately ONLINE: an offline card suppresses thumbnails entirely, so
        the offline case cannot show this at all. Synchronization is held with
        a route gate rather than a delay, so the pending window is
        deterministic rather than a race with the coordinator."""
        server, page, context = self.start()
        work = server.ids['work_a']
        self.db_for(server).update_work_metadata(work, {'thumb_page': 5})
        page.reload()
        page.wait_for_selector('#sidebar')
        page.evaluate("r => prksNavigate(r)", self.PROGRESS)
        page.wait_for_selector('[data-work-id="%s"]' % work)
        self.assertIn('page=5', self.thumb_src(page, work),
                      'the acknowledged page is stated explicitly')

        # The gate: the operation is saved durably and can never be sent.
        blocked = []

        def hold(route):
            blocked.append(route.request.url)
            route.abort('failed')

        page.route('**/api/sync/operations', hold)
        try:
            o._open_work_from_home(page, WORK_A_TITLE)
            self.edit(page)
            self.field(page, 'thumb_page', '')
            self.save(page)
            # The FIELD operation, not the whole queue: opening the Work
            # enqueued a MARK_WORK_OPENED that this same blocked send is also
            # holding, and counting both would be counting the wrong thing.
            self.pending(page, 1)

            page.evaluate("r => prksNavigate(r)", self.PROGRESS)
            page.wait_for_selector('[data-work-id="%s"]' % work)
            src = self.thumb_src(page, work)
            self.assertIn('page=1', src, 'a pending clear means page 1, immediately')
            self.assertNotIn('page=5', src, 'not the page the server still stores')
            self.assertEqual(self.db_for(server).get_work(work)['thumb_page'], 5,
                             'and the server has not been told anything yet')
            self.assertTrue(blocked, 'the gate actually held a send')
        finally:
            page.unroute('**/api/sync/operations', hold)

        self.pending(page, 0)
        self.assertIsNone(self.db_for(server).get_work(work)['thumb_page'])
        page.evaluate("r => prksNavigate(r)", self.PROGRESS)
        page.wait_for_selector('[data-work-id="%s"]' % work)
        self.assertIn('page=1', self.thumb_src(page, work),
                      'and nothing changes visibly at acknowledgement')

    def test_an_online_pending_page_renders_before_the_server_is_told(self):
        """The endpoint already accepts an explicit page, so a pending value
        needs nothing from the column to be rendered."""
        server, page, context = self.start()
        work = server.ids['work_a']
        self.db_for(server).update_work_metadata(work, {'thumb_page': 2})
        page.reload()
        page.wait_for_selector('#sidebar')

        def hold(route):
            route.abort('failed')

        page.route('**/api/sync/operations', hold)
        try:
            o._open_work_from_home(page, WORK_A_TITLE)
            self.edit(page)
            self.field(page, 'thumb_page', '5')
            self.save(page)
            # The FIELD operation, not the whole queue: opening the Work
            # enqueued a MARK_WORK_OPENED that this same blocked send is also
            # holding, and counting both would be counting the wrong thing.
            self.pending(page, 1)
            page.evaluate("r => prksNavigate(r)", self.PROGRESS)
            page.wait_for_selector('[data-work-id="%s"]' % work)
            self.assertIn('page=5', self.thumb_src(page, work))
            self.assertEqual(self.db_for(server).get_work(work)['thumb_page'], 2,
                             'the column still says 2')
        finally:
            page.unroute('**/api/sync/operations', hold)
        self.pending(page, 0)
        self.assertEqual(self.db_for(server).get_work(work)['thumb_page'], 5)

    def test_a_cached_card_makes_no_thumbnail_request_for_a_pending_page(self):
        """Offline suppression is absolute and decided before any URL exists.
        This counts actual requests to the endpoint rather than checking that
        an image is hidden -- a hidden image that was still fetched is exactly
        the failure being excluded."""
        server, page, context = self.start()
        work = server.ids['work_a']
        self.db_for(server).update_work_metadata(work, {'thumb_page': 2})
        page.reload()
        page.wait_for_selector('#sidebar')
        self.warm_all_catalogs(page, work)

        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        self.field(page, 'thumb_page', '4')
        self.save(page)
        self.pending(page, 1)

        seen = self.thumb_requests(page)
        page.evaluate("r => prksNavigate(r)", self.PROGRESS)
        page.wait_for_selector('[data-work-id="%s"]' % work)
        page.wait_for_timeout(400)   # a settle window: absence needs one
        self.assertEqual(seen, [], 'a cached card requested no thumbnail at all')
        self.assertEqual(self.thumb_src(page, work), '',
                         'and emitted no source to request later')

    def test_an_unreadable_thumb_page_is_refused_rather_than_cleared(self):
        """PATCH used to turn 0, -1 and junk into NULL silently -- a client
        asking for an impossible page was told the field had been emptied on
        purpose. Refusing is not clearing."""
        server, page, context = self.start()
        work = server.ids['work_a']
        self.db_for(server).update_work_metadata(work, {'thumb_page': 3})
        page.reload()
        page.wait_for_selector('#sidebar')
        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        # Only values the control can actually hold: it is `type="number"`, so
        # the browser itself refuses "abc" before the codec is ever consulted.
        # That is a first line of defence, not the contract -- the codec's
        # refusal of non-numeric text is covered by the unit and Node tests.
        for bad in ('0', '-1'):
            with self.subTest(value=bad):
                self.field(page, 'thumb_page', bad)
                self.save(page)
                page.wait_for_function("""() => {
                    const el = document.getElementById('meta-thumb-page-error');
                    return !!el && el.textContent.trim().length > 0;
                }""")
                self.assertEqual(self.operations(page), [],
                                 'nothing was enqueued for %r' % bad)
                self.assertEqual(
                    page.locator('[data-prks-work-field="thumb_page"]').input_value(), bad,
                    'the draft is preserved')
        self.assertEqual(self.db_for(server).get_work(work)['thumb_page'], 3,
                         'and the stored page is untouched')

    def test_a_thumb_page_conflict_uses_the_existing_field_level_ux(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        self.db_for(server).update_work_metadata(work, {'thumb_page': 2})
        page.reload()
        page.wait_for_selector('#sidebar')
        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        self.field(page, 'thumb_page', '7')
        self.save(page)
        self.pending(page, 1)

        self.db_for(server).update_work_metadata(work, {'thumb_page': 4})
        self.reconnect(page, context)
        self.settled_conflicts(page, 1)
        result = self.operations(page)[0]['server_result']
        self.assertEqual(result['code'], 'REVISION_CONFLICT')
        self.assertEqual(result['current_value'], '4', 'reported in the wire form')
        self.assertEqual(result['requested_value'], '7')

        page.get_by_role('button', name='Apply my value', exact=True).click()
        self.pending(page, 0)
        self.assertEqual(self.db_for(server).get_work(work)['thumb_page'], 7)

    def test_recently_added_filters_on_the_pending_author_text(self):
        """That filter indexes the RAW field, which is deliberately not the
        same as the displayed credit -- 2I preserves that rather than
        redesigning it."""
        server, page, context = self.start()
        work = server.ids['work_b']
        self.db_for(server).update_work_metadata(work, {'author_text': 'Old Author'})
        page.reload()
        page.wait_for_selector('#sidebar')
        self.recently_added(page)
        o._wait_list_cached(page, 'recently-added:index')
        self.assertIn(work, self.filter_recently_added(page, 'Old Author'))

        self.open_work_b(page)
        self.offline(page, context)
        self.field(page, 'author_text', 'New Author')
        self.save(page)
        self.pending(page, 1)

        self.recently_added(page)
        self.assertIn(work, self.filter_recently_added(page, 'New Author'),
                      'the pending value is searchable locally at once')
        self.assertNotIn(work, self.filter_recently_added(page, 'Old Author'),
                         'and the value it replaced stops matching')

    def test_an_author_text_conflict_is_visible_even_when_masked_on_the_card(self):
        """A conflict is about the FIELD, not about what happens to be
        displayed. Work A credits a linked Author throughout, so nothing on its
        card ever changes -- and the disagreement still has to be surfaced and
        resolvable."""
        server, page, context = self.start()
        work = server.ids['work_a']
        self.offline(page, context)
        self.field(page, 'author_text', 'Alice')
        self.save(page)
        self.pending(page, 1)
        self.db_for(server).update_work_metadata(work, {'author_text': 'Bob'})
        self.reconnect(page, context)
        self.settled_conflicts(page, 1)

        op = self.operations(page)[0]
        self.assertEqual(op['payload']['field'], 'author_text')
        self.assertEqual(op['server_result']['code'], 'REVISION_CONFLICT')
        # Byte-limited, so the disagreement arrives as a bounded preview plus
        # sizes rather than two values the durable result could not hold.
        self.assertEqual(op['server_result']['current_preview'], 'Bob')
        self.assertEqual(op['server_result']['current_bytes'], 3)
        sync_text = page.locator('[data-prks-role="work-bib-sync"]').inner_text()
        self.assertIn('Alice', sync_text)
        self.assertIn('Bob', sync_text)

        page.get_by_role('button', name='Apply my value', exact=True).click()
        self.pending(page, 0)
        self.assertEqual(self.server_value(server, work, 'author_text'), 'Alice')

    def test_location_is_detail_only_and_leaves_recently_added_alone(self):
        """The control case: expanding the field family must not make every
        field invalidate every projection."""
        server, page, context = self.start()
        work = server.ids['work_a']
        self.recently_added(page)
        o._wait_list_cached(page, 'recently-added:index')
        before = page.evaluate("() => prksOfflineDomainGeneration('recently-added')")

        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        self.field(page, 'location', 'Amsterdam; Boston')
        self.save(page)
        self.pending(page, 1)
        page.reload()
        page.wait_for_selector('#sidebar')
        self.assertIn('Amsterdam; Boston', self.bib_rows(page))
        self.pending(page, 1)

        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(self.server_fields(server, work)['location'],
                         {'value': 'Amsterdam; Boston', 'revision': 1})
        self.assertEqual(page.evaluate("() => prksOfflineDomainGeneration('recently-added')"), before,
                         'a detail-only field never touches an unrelated projection')
        self.assertIsNotNone(o._cached_list(page, 'recently-added:index'))

    def test_a_publisher_conflict_uses_the_existing_field_path(self):
        """No new conflict machinery: the expanded registry reuses 2D's."""
        server, page, context = self.start()
        work = server.ids['work_a']
        self.offline(page, context)
        self.field(page, 'publisher', 'device-publisher')
        self.field(page, 'location', 'device-location')
        self.field(page, 'doi', 'device-doi')
        self.save(page)
        self.pending(page, 3)
        self.db_for(server).update_work_metadata(work, {'publisher': 'server-publisher'})
        self.reconnect(page, context)
        self.settled_conflicts(page, 1)

        rows = self.operations(page)
        self.assertEqual(rows[0]['payload']['field'], 'publisher')
        self.assertEqual(rows[0]['status'], 'conflict')
        state = self.server_fields(server, work)
        self.assertEqual(state['location']['value'], 'device-location',
                         'Location was never in the collision')
        self.assertEqual(state['doi']['value'], 'device-doi')
        self.assertTrue(page.locator('[data-prks-work-field="publisher"]').is_disabled())
        self.assertFalse(page.locator('[data-prks-work-field="location"]').is_disabled())
        self.assertFalse(page.locator('[data-prks-work-field="doi"]').is_disabled())

        page.get_by_role('button', name='Use server', exact=True).click()
        self.pending(page, 0)
        self.assertEqual(self.server_fields(server, work)['publisher'],
                         {'value': 'server-publisher', 'revision': 1})

    def test_entering_the_editor_before_hydration_still_shows_pending_values(self):
        """The one race a unit test cannot reach: a reload, then Edit metadata
        pressed before the durable queue has been read out of IndexedDB.

        An un-hydrated empty map is indistinguishable from nothing pending, so
        building the draft from it would show the acknowledged text and then
        ask whether to discard changes the user had already saved. The read is
        held open deliberately here rather than raced against a timer.
        """
        server, page, context = self.start()
        self.offline(page, context)
        self.field(page, 'publisher', 'Held Press')
        self.save(page)
        self.pending(page, 1)

        # Install the gate BEFORE the reload, via an init script: the durable
        # store's first read blocks until this test releases it.
        context.add_init_script("""
            (() => {
                let real = null;
                window.__prksReleaseRead = null;
                const gate = new Promise(resolve => { window.__prksReleaseRead = resolve; });
                Object.defineProperty(window, 'createPrksLocalStore', {
                    configurable: true,
                    get() {
                        if (!real) return undefined;
                        return (...args) => {
                            const store = real(...args);
                            // EVERY read is held, not just the first: the sync
                            // coordinator's startup recovery reads before the
                            // metadata editor does, and gating only that one
                            // would let hydration finish before the race began.
                            const listOperations = store.listOperations.bind(store);
                            store.listOperations = async (...inner) => {
                                await gate;
                                return listOperations(...inner);
                            };
                            return store;
                        };
                    },
                    set(value) { real = value; },
                });
            })();
        """)
        page.reload()
        page.wait_for_selector('#sidebar')
        # The Work panel renders from the cached entity, not from hydration, so
        # waiting for it does not release the gate the mount is already held on.
        page.locator('#panel-content button', has_text='Edit metadata').wait_for()
        # Fire and forget: this call cannot resolve until the read is released.
        page.evaluate("() => { void prksSetWorkDetailsMode('metadata'); }")
        page.evaluate("() => window.__prksReleaseRead()")

        publisher = page.locator('[data-prks-work-field="publisher"]')
        publisher.wait_for()
        page.wait_for_function("""() => {
            const input = document.querySelector('[data-prks-work-field="publisher"]');
            return !!input && input.value === 'Held Press';
        }""")
        # ...and the draft it was built from agrees, so leaving raises nothing.
        self.assertFalse(page.evaluate("""() => {
            const ctx = prksGetFocusedTabContext();
            return prksWorkMetaDraftIsDirty(ctx, ctx.getEntity('work'));
        }"""), 'a saved field must not read as an unsaved draft')
        page.evaluate("() => prksNavigate('#/folders')")
        page.wait_for_function("() => location.hash.indexOf('/works/') === -1")
        self.assertEqual(page.locator('#prks-modal-confirm:not(.hidden)').count(), 0,
                         'no prompt to discard changes that were already saved')

    # ---- Abstract: large scalar with a DERIVED projection -------------------

    def progress(self, page, status='In Progress'):
        page.evaluate("s => prksNavigate('#/progress?status=' + encodeURIComponent(s))", status)
        page.wait_for_function("() => location.hash.indexOf('/progress') !== -1")
        page.wait_for_selector('.card-grid')

    def progress_excerpt(self, page, work_id):
        return page.evaluate("""id => {
            const ctx = prksGetFocusedTabContext();
            const card = ctx.root.querySelector('[data-work-id="' + id + '"]');
            const context = card && card.querySelector('.work-card__context');
            return context ? context.textContent : null;
        }""", work_id)

    def test_pending_abstract_reaches_progress_with_the_servers_own_excerpt(self):
        """The derived projection. Progress shows the first 100 code points of
        the pending Abstract, cut exactly where the server would cut it -- and
        the acknowledged catalog on disk still says what the server said."""
        server, page, context = self.start()
        work = server.ids['work_a']
        db = self.db_for(server)
        db.update_work_metadata(work, {'abstract': 'Server abstract text.', 'status': 'In Progress'})
        page.reload()
        page.wait_for_selector('#sidebar')
        self.progress(page)
        o._wait_list_cached(page, 'works-browse:index')
        self.assertIn('Server abstract', self.progress_excerpt(page, work))

        # An astral Abstract: JS slicing would cut this at 50 characters.
        pending = '\U0001F9EA' * 150
        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        page.locator('[data-prks-work-field="abstract"]').fill(pending)
        self.save(page)
        self.pending(page, 1)

        self.progress(page)
        shown = self.progress_excerpt(page, work)
        expected = db.execute_query(
            "SELECT SUBSTR(?, 1, 100) AS e", (pending,))[0]['e']
        self.assertIn(expected, shown,
                      'the pending excerpt equals what SQLite would produce')
        self.assertEqual(len(expected), 100)
        cached = page.evaluate("""id => window.createPrksOfflineStore().getList('works-browse:index')
            .then(row => row.value.find(w => w.id === id).abstract_excerpt)""", work)
        self.assertEqual(cached, 'Server abstract text.',
                         'the acknowledged catalog is untouched until the server answers')

        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(db.execute_query(
            "SELECT abstract FROM works WHERE id = ?", (work,))[0]['abstract'], pending,
            'the server received the whole Abstract, not the excerpt')
        after = page.evaluate("""id => window.createPrksOfflineStore().getList('works-browse:index')
            .then(row => row.value.find(w => w.id === id).abstract_excerpt)""", work)
        self.assertEqual(after, expected, 'and the acknowledged row now matches')
        self.progress(page)
        self.assertIn(expected, self.progress_excerpt(page, work),
                      'nothing visibly changed when the server answered')

    def test_an_abstract_larger_than_the_old_payload_ceiling_synchronizes(self):
        """The exact gap the previous suite missed. The editor and the server
        accepted 64 KiB-1 MiB, but durable storage refused it -- so the save
        failed at the one step the user had been told already succeeded. 150 KiB
        crosses that boundary without making the browser test slow."""
        server, page, context = self.start()
        work = server.ids['work_a']
        db = self.db_for(server)
        db.update_work_metadata(work, {'status': 'In Progress'})
        page.reload()
        page.wait_for_selector('#sidebar')
        self.progress(page)
        o._wait_list_cached(page, 'works-browse:index')

        o._open_work_from_home(page, WORK_A_TITLE)
        self.edit(page)
        self.offline(page, context)
        page.evaluate("""() => {
            const input = document.querySelector('[data-prks-work-field="abstract"]');
            input.value = 'Beyond the old ceiling. ' + 'L'.repeat(150 * 1024);
            input.dispatchEvent(new Event('input', { bubbles: true }));
        }""")
        self.save(page)
        self.pending(page, 1)

        stored = self.operations(page)[0]
        self.assertEqual(stored['payload']['field'], 'abstract')
        self.assertGreater(len(stored['payload']['value']), 150 * 1024,
                           'the whole Abstract reached the durable queue')

        page.reload()
        page.wait_for_selector('#sidebar')
        self.pending(page, 1)
        self.assertGreater(len(self.operations(page)[0]['payload']['value']), 150 * 1024,
                           'and survived the reload intact')
        self.progress(page)
        self.assertIn('Beyond the old ceiling', self.progress_excerpt(page, work))

        self.reconnect(page, context)
        self.pending(page, 0)
        saved = db.execute_query("SELECT abstract FROM works WHERE id = ?", (work,))[0]['abstract']
        self.assertGreater(len(saved), 150 * 1024, 'the server received the whole value')
        self.assertTrue(saved.startswith('Beyond the old ceiling.'))
        # The ledger keeps the outcome, never a second copy of the text.
        ledger = db.execute_query(
            "SELECT result_json FROM sync_operations WHERE operation_type = 'SET_WORK_METADATA_FIELD'")
        self.assertTrue(ledger)
        for row in ledger:
            self.assertLess(len(row['result_json']), 2048)
        self.progress(page)
        self.assertIn('Beyond the old ceiling', self.progress_excerpt(page, work),
                      'the excerpt is unchanged by acknowledgement')

    def test_an_oversize_abstract_is_refused_without_touching_anything(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        self.offline(page, context)
        seen = self.record_paths(page)
        page.evaluate("""() => {
            const input = document.querySelector('[data-prks-work-field="abstract"]');
            input.value = 'x'.repeat(1024 * 1024 + 1);
            input.dispatchEvent(new Event('input', { bubbles: true }));
        }""")
        self.save(page)
        page.wait_for_function("""() => {
            const status = document.querySelector('[data-prks-role="work-bib-sync"]');
            return !!status && status.textContent.indexOf('too long to save') !== -1;
        }""")
        self.assertEqual(self.operations(page), [], 'nothing durable was stored')
        self.assertEqual([url for method, url in seen if method != 'GET'], [])
        self.assertEqual(page.evaluate(
            "() => document.querySelector('[data-prks-work-field=\"abstract\"]').value.length"),
            1024 * 1024 + 1, 'the draft stays on screen')
        self.assertEqual(self.server_fields(server, work)['abstract'], {'revision': 0})

    def test_an_abstract_conflict_shows_bounded_previews(self):
        """A megabyte of Abstract must not be dumped into a conflict sentence,
        and the durable row could not store it even if it were."""
        server, page, context = self.start()
        work = server.ids['work_a']
        self.offline(page, context)
        page.locator('[data-prks-work-field="abstract"]').fill('D' * 5000)
        self.save(page)
        self.pending(page, 1)
        self.db_for(server).update_work_metadata(work, {'abstract': 'S' * 5000})
        self.reconnect(page, context)
        self.settled_conflicts(page, 1)

        row = self.operations(page)[0]
        self.assertEqual(row['status'], 'conflict')
        self.assertEqual(row['server_result']['code'], 'REVISION_CONFLICT')
        self.assertNotIn('current_value', row['server_result'])
        self.assertEqual(row['server_result']['current_bytes'], 5000)
        status = page.locator('[data-prks-role="work-bib-sync"]').inner_text()
        self.assertIn('Abstract differs', status)
        self.assertLess(len(status), 1200, 'the conflict line stays readable')

        page.get_by_role('button', name='Use server', exact=True).click()
        self.pending(page, 0)
        self.assertEqual(self.server_fields(server, work)['abstract'], {'revision': 1},
                         'the server was not mutated again')

    # ---- boundaries ---------------------------------------------------------

    def test_online_save_uses_the_same_durable_queue(self):
        """Online and offline must travel the same road, or the offline path is
        a separate, less-exercised implementation and the two will drift."""
        server, page, context = self.start()
        work = server.ids['work_a']
        seen = self.record_paths(page)
        self.field(page, 'doi', '10.1/online')
        self.save(page)
        self.pending(page, 0)
        writes = [(m, u) for m, u in seen if m in ('POST', 'PUT', 'PATCH', 'DELETE')]
        self.assertTrue([1 for _, url in writes if url.endswith('/api/sync/operations')], writes)
        self.assertEqual([(m, u) for m, u in writes if m == 'PATCH'], [],
                         'the synchronized fields never travel in the online PATCH')
        self.assertEqual(self.server_fields(server, work)['doi'],
                         {'value': '10.1/online', 'revision': 1})

    def test_publisher_and_location_left_the_online_save(self):
        """Two mutation paths for one field would mean the path that is not
        revision-aware silently overwriting the other's conflicts."""
        server, page, context = self.start()
        work = server.ids['work_a']
        seen = self.record_paths(page)
        self.field(page, 'publisher', 'Durable Press')
        self.field(page, 'location', 'Durable City')
        self.save(page)
        self.pending(page, 0)
        self.assertEqual([(m, u) for m, u in seen if m == 'PATCH'], [],
                         'the synchronized fields never travel in the online PATCH')
        state = self.server_fields(server, work)
        self.assertEqual(state['publisher'], {'value': 'Durable Press', 'revision': 1})
        self.assertEqual(state['location'], {'value': 'Durable City', 'revision': 1})

    def test_the_editor_has_no_online_only_save_left(self):
        """The end state of the program. Every user-editable Work metadata
        value belongs to a durable group, so there is no button to disable
        when the connection drops and no note to show about one."""
        server, page, context = self.start()
        self.assertEqual(page.locator('#inline-save-metadata-btn').count(), 0)
        self.assertEqual(page.locator('[data-prks-role="work-meta-online-only"]').count(), 0)
        self.offline(page, context)
        # Every durable group stays fully usable offline.
        for button in ('#save-work-identity-btn', '#save-work-status-btn', '#save-work-bib-btn'):
            self.assertFalse(page.locator(button).is_disabled(), button)
        for field in ('title', 'doi', 'thumb_page', 'source_url'):
            self.assertFalse(page.locator('[data-prks-work-field="%s"]' % field).is_disabled(),
                             field)

    def test_without_a_cached_projection_the_group_refuses_to_guess(self):
        """No observed base means no way to say what a save is relative to, so
        the group says so rather than sending an edit against revision zero."""
        server, page, context = self.start()
        work = server.ids['work_a']
        page.evaluate("id => window.createPrksOfflineStore().deleteEntity('work-metadata-state', id)", work)
        self.offline(page, context)
        page.reload()
        page.wait_for_selector('#sidebar')
        page.locator('#panel-content button', has_text='Edit metadata').click()
        page.wait_for_function(
            "() => { const b = document.getElementById('save-work-bib-btn'); return b && b.disabled; }")
        self.assertTrue(page.locator('[data-prks-work-field="doi"]').is_disabled())
        self.assertIn('not available offline',
                      page.locator('[data-prks-role="work-bib-sync"]').inner_text())
        self.assertEqual(self.operations(page), [])
