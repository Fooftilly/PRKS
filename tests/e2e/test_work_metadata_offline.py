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
