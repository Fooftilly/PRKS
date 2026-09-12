"""Field-scoped Work metadata: independence, conflicts and atomic local save."""
import os
import unittest

from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig
from tests.e2e import test_offline as o
from tests.e2e.fixtures import WORK_A_TITLE, seed_library
from tests.e2e.harness import AppServer, open_app_page, require_chromium


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get('PRKS_E2E') == '1' else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close(); _PW.stop()


class OfflineWorkMetadataTests(unittest.TestCase):
    FIELD_OPS = "r.operation === 'SET_WORK_METADATA_FIELD'"

    def start(self):
        server = AppServer(seed_fn=seed_library)
        self.addCleanup(server.stop); server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin, service_workers='allow')
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        o._wait_sw_active(page)
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work', server.ids['work_a'])
        self.edit(page)
        o._wait_entity_cached(page, 'work-metadata-state', server.ids['work_a'])
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

    def operations(self, page):
        return page.evaluate(
            "() => prksSync.store.listOperations().then(rows => rows.filter(r => %s))" % self.FIELD_OPS)

    def pending(self, page, count):
        page.evaluate("""async n => {
            const deadline = Date.now() + 25000;
            for (;;) {
                const rows = (await prksSync.store.listOperations())
                    .filter(r => r.operation === 'SET_WORK_METADATA_FIELD');
                if (rows.length === n) return;
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
        self.pending(page, 1)

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
        self.pending(page, 1)

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
        self.pending(page, 1)

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
        page.wait_for_function("""() => prksSync.store.listOperations().then(rows =>
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
        page.wait_for_function("() => prksSync.store.listOperations().then(rows => rows.length === 0)")
        self.assertEqual(self.server_fields(server, work)['doi'],
                         {'value': 'server-doi', 'revision': 1})

    # ---- the cross-projection case this milestone exists for ----------------

    def recently_added(self, page):
        """Home -> Recently Added, warmed and rendered."""
        page.evaluate("() => prksNavigate('#/folders')")
        page.wait_for_selector('.prks-folder-library__tab-btn[data-tab="recently-added"]')
        page.evaluate("() => prksSwitchFolderLibraryTab('recently-added')")
        page.wait_for_selector('#prks-folder-library-recently-added')

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
        self.pending(page, 1)

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

    def test_unsupported_metadata_stays_explicitly_online_only(self):
        server, page, context = self.start()
        self.assertFalse(page.locator('#inline-save-metadata-btn').is_disabled())
        self.offline(page, context)
        page.wait_for_function(
            "() => document.getElementById('inline-save-metadata-btn').disabled")
        self.assertFalse(page.locator('[data-prks-role="work-meta-online-only"]').is_hidden())
        # ...while the synchronized group stays fully usable.
        self.assertFalse(page.locator('#save-work-bib-btn').is_disabled())
        self.assertFalse(page.locator('[data-prks-work-field="doi"]').is_disabled())

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
