"""Offline Person profile editing, and how it composes with offline creation.

The milestone's vertical slice: editing an existing Person is the same feature
with or without a server, a pending edit is visible everywhere the Person's
name or profile is shown, and an edit to a Person this device created is
ordered behind that creation by the generic dependency machinery rather than by
anything private to Person editing.
"""
import json
import os
import time
import unittest

from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig
from tests.e2e import test_offline as o
from tests.e2e.fixtures import PERSON_DISPLAY, WORK_A_TITLE, seed_library
from tests.e2e.harness import AppServer, open_app_page, require_chromium, wait_for_async


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get('PRKS_E2E') == '1' else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close(); _PW.stop()


class OfflinePersonEditTests(unittest.TestCase):

    # ---- harness ------------------------------------------------------------

    def start(self):
        server = AppServer(seed_fn=seed_library)
        self.addCleanup(server.stop); server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin,
                                                 service_workers='allow')
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        o._wait_sw_active(page)
        o._open_people_index(page)
        o._wait_list_cached(page, 'people:index')
        return server, page, context

    def offline(self, page, context):
        context.set_offline(True)
        page.evaluate('() => prksOfflineNoteRequestFailure()')
        page.wait_for_function("() => prksOfflineRuntimeState() !== 'online'")

    def reconnect(self, page, context):
        context.set_offline(False)
        page.evaluate("() => window.dispatchEvent(new Event('online'))")
        page.wait_for_function("() => prksOfflineRuntimeState() === 'online'")

    def prepare_person(self, page, person_id):
        """Open the Person once while connected, so its revisions are cached.

        Unknown durable state is not empty: without this projection the client
        would have to guess revision 0 and could overwrite another device.
        """
        o._open_person(page, person_id)
        o._wait_content_contains(page, PERSON_DISPLAY)
        o._wait_entity_cached(page, 'person', person_id)
        o._open_details_drawer_if_tiled(page)
        page.evaluate("id => { void prksReadPersonMetadataState(id); }", person_id)
        o._wait_entity_cached(page, 'person-metadata-state', person_id)

    def open_editor(self, page):
        o._open_details_drawer_if_tiled(page)
        page.evaluate("() => { try { openPersonProfileEdit(); } catch (_e) {} }")
        page.locator('.person-panel-edit').wait_for(timeout=15000)

    def edit_field(self, page, selector, value):
        page.locator(selector).fill(value)

    def save_editor(self, page, closes=True):
        page.locator('#pd-save-btn').click()
        # A successful save closes the editor. Reopening before it has closed
        # would match the OLD panel, which then re-renders underneath the next
        # fill -- and the field detaches mid-typing. A REFUSED save leaves it
        # open on purpose, so the caller says which it expects.
        if closes:
            page.locator('.person-panel-edit').wait_for(state='detached', timeout=30000)

    def operations(self, page):
        return [tuple(row) for row in page.evaluate(
            "() => prksSync.store.listOperations().then(rows => rows.map("
            "  o => [o.operation, o.entity_id, o.status,"
            "        JSON.stringify(o.payload), JSON.stringify(o.depends_on || [])]))")]

    def profile_operations(self, page):
        return [row for row in self.operations(page)
                if row[0] == 'SET_PERSON_METADATA_FIELD']

    def settled(self, page):
        page.evaluate("""async () => {
            const deadline = Date.now() + 30000;
            for (;;) {
                const rows = await prksSync.store.listOperations();
                if (!rows.length) return;
                if (Date.now() > deadline) {
                    throw new Error('Sync did not settle: ' + JSON.stringify(rows));
                }
                await new Promise(resolve => setTimeout(resolve, 50));
            }
        }""")

    def wait_for_family(self, page, operation):
        """The save reached the durable queue."""
        wait_for_async(
            page,
            "op => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === op))",
            arg=operation, message='%s was never enqueued' % operation)

    def wait_until_synced(self, page, operation):
        """Enqueued, then drained.

        The canonical record is read ONCE, afterwards. Polling it during a sync
        competes with the very write being waited for: a reader holds the
        database while the server is trying to take it, and the queue never
        gets to finish.
        """
        self.wait_for_family(page, operation)
        self.settled(page)

    def db_for(self, server):
        """ONE handle per server, reused.

        Opening a fresh PRKSDatabase per poll re-runs the schema check and takes
        a write lock against the server's own connection -- the poll then
        competes with the very writes it is waiting for.
        """
        existing = getattr(self, '_db', None)
        if existing is None:
            existing = PRKSDatabase(storage=StorageConfig.for_testing(server.storage_root))
            self._db = existing
        return existing

    def stored(self, server, person_id, field):
        rows = self.db_for(server).execute_query(
            'SELECT %s FROM persons WHERE id = ?' % field, (person_id,))
        return rows[0][field] if rows else None

    # ---- editing an existing Person -----------------------------------------

    def test_an_existing_person_is_editable_offline_and_the_edit_is_immediate(self):
        """Saving creates the intent and updates what the user sees.

        Putting it on the wire is subsequent work, so Save cannot fail for a
        reason the user has no way to act on -- and the value they typed is on
        screen before anything has been sent.
        """
        server, page, context = self.start()
        person = server.ids['person']
        self.prepare_person(page, person)
        self.offline(page, context)

        self.open_editor(page)
        self.edit_field(page, '#pd-about', 'Written with no server')
        self.save_editor(page)

        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === 'SET_PERSON_METADATA_FIELD'))",
            message='the profile edit was never recorded durably')
        o._wait_content_contains(page, 'Written with no server')

        ops = self.profile_operations(page)
        self.assertEqual(len(ops), 1, ops)
        self.assertIn('"field":"about"', ops[0][3])
        self.assertEqual(ops[0][1], person)

    def test_the_edit_survives_a_reload_that_is_still_offline(self):
        server, page, context = self.start()
        person = server.ids['person']
        self.prepare_person(page, person)
        self.offline(page, context)
        self.open_editor(page)
        self.edit_field(page, '#pd-about', 'Durable across a reload')
        self.save_editor(page)
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 1)")

        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('#sidebar')
        self.offline(page, context)
        o._open_person(page, person)
        # The disposable cache still holds the acknowledged value; the effective
        # profile is that plus the intent, recomputed from the durable queue.
        o._wait_content_contains(page, 'Durable across a reload')
        self.assertEqual(len(self.profile_operations(page)), 1)

    def test_two_edits_to_one_field_are_one_intent(self):
        server, page, context = self.start()
        person = server.ids['person']
        self.prepare_person(page, person)
        self.offline(page, context)

        for value in ('First attempt', 'Second attempt'):
            self.open_editor(page)
            self.edit_field(page, '#pd-about', value)
            self.save_editor(page)
            wait_for_async(
                page,
                "v => prksSync.store.listOperations().then(rows => rows.some("
                "  o => o.operation === 'SET_PERSON_METADATA_FIELD' &&"
                "       o.payload.value === v))",
                arg=value, message='the edit was not recorded')

        ops = self.profile_operations(page)
        self.assertEqual(len(ops), 1, 'a never-sent row is rewritten, not stacked')
        self.assertIn('Second attempt', ops[0][3])
        o._wait_content_contains(page, 'Second attempt')

    def test_editing_a_field_back_to_the_server_value_leaves_no_intent(self):
        """A -> B -> A is not two changes, it is none.

        The second save is the one that can only be got right by measuring
        against the ACKNOWLEDGED value rather than the record on screen: that
        record already says B, so a base taken from it would read the revert as
        a change and leave behind an operation asking the server to write a
        value it already holds.
        """
        server, page, context = self.start()
        person = server.ids['person']
        self.prepare_person(page, person)
        original = self.stored(server, person, 'about') or ''
        self.offline(page, context)

        self.open_editor(page)
        self.edit_field(page, '#pd-about', 'A biography typed by mistake')
        self.save_editor(page)
        self.wait_for_family(page, 'SET_PERSON_METADATA_FIELD')
        o._wait_content_contains(page, 'A biography typed by mistake')

        # Reopened from the EFFECTIVE profile -- the form shows the pending
        # value -- and typed back to what the server holds.
        self.open_editor(page)
        self.assertEqual(
            page.evaluate("() => document.querySelector('#pd-about').value"),
            'A biography typed by mistake',
            'the editor opens from the effective profile, not the cached one')
        self.edit_field(page, '#pd-about', original)
        self.save_editor(page)

        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            message='reverting an unsent edit must leave no operation behind')
        self.assertEqual(self.profile_operations(page), [])

    def test_one_conflicted_field_does_not_make_the_rest_unsavable(self):
        """The conflict unit is one field, and a save carries only what changed.

        Saving the whole form every time would let a single undecided biography
        make the birth date uneditable -- which is exactly the independence the
        per-field conflict unit exists to provide.
        """
        server, page, context = self.start()
        person = server.ids['person']
        self.prepare_person(page, person)
        self.offline(page, context)

        self.open_editor(page)
        self.edit_field(page, '#pd-about', 'Will end up in conflict')
        self.save_editor(page)
        self.wait_for_family(page, 'SET_PERSON_METADATA_FIELD')

        # Exactly what a stale base comes back as, without needing a race.
        page.evaluate("""() => prksSync.store.listOperations().then(rows => {
            const row = rows.find(o => o.operation === 'SET_PERSON_METADATA_FIELD');
            return prksSync.store.updateOperationSyncState(row.op_id, {
                status: 'conflict',
                server_result: { code: 'REVISION_CONFLICT', current_revision: 4,
                                 current_value: 'Someone else wrote this' },
            });
        })""")

        self.open_editor(page)
        self.edit_field(page, '#pd-link-wikipedia', 'https://example.org/still-editable')
        self.save_editor(page)
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.payload.field === 'link_wikipedia'))",
            message='an undecided biography must not refuse the whole form')
        states = {op[3]: op[2] for op in self.profile_operations(page)}
        self.assertEqual(len(states), 2, states)
        self.assertTrue(any(s == 'conflict' for s in states.values()))
        self.assertTrue(any(s == 'pending' for s in states.values()))

    def test_two_different_fields_compose_as_separate_decisions(self):
        server, page, context = self.start()
        person = server.ids['person']
        self.prepare_person(page, person)
        self.offline(page, context)

        self.open_editor(page)
        self.edit_field(page, '#pd-about', 'A biography')
        self.edit_field(page, '#pd-link-wikipedia', 'https://example.org/wiki')
        self.save_editor(page)
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 2)",
            message='each field is its own decision and its own operation')

        fields = sorted(op[3] for op in self.profile_operations(page))
        self.assertEqual(len(fields), 2)
        self.assertTrue(any('"field":"about"' in f for f in fields), fields)
        self.assertTrue(any('"field":"link_wikipedia"' in f for f in fields), fields)

    def test_a_pending_rename_reaches_the_role_picker_and_the_work_credit(self):
        """A renamed Person is still the person a Work is credited to.

        Their name is displayed in rows keyed by Work, which the acknowledgement
        path invalidates rather than patches -- so a PENDING rename is in no
        cache at all and only the overlay can make it visible.
        """
        server, page, context = self.start()
        person = server.ids['person']
        work = server.ids['work_a']
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work-people-state', work)
        self.prepare_person(page, person)
        self.offline(page, context)

        self.open_editor(page)
        self.edit_field(page, '#pd-last-name', 'Renamed')
        self.save_editor(page)
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === 'SET_PERSON_METADATA_FIELD' &&"
            "       o.payload.field === 'last_name'))")

        # The People index reads the same overlay.
        o._open_people_index(page)
        o._wait_content_contains(page, 'Renamed')

        # And so does the Work whose credit line names them.
        o._open_work_from_home(page, WORK_A_TITLE)
        o._open_details_drawer_if_tiled(page)
        page.wait_for_selector('.work-detail')
        page.evaluate("() => { void prksSetWorkDetailsMode('people'); }")
        # The linked-people chips live in the details panel, not the main
        # content area -- the Work route's content is the document itself.
        page.wait_for_function(
            """text => {
                const chips = Array.from(document.querySelectorAll(
                    '#panel-content .work-linked-persons__chip-link'));
                return chips.some(el => el.textContent.indexOf(text) !== -1);
            }""",
            arg='Renamed', timeout=30000)

    def test_reconnecting_applies_the_edit_once_with_no_rollback(self):
        server, page, context = self.start()
        person = server.ids['person']
        self.prepare_person(page, person)
        self.offline(page, context)
        self.open_editor(page)
        self.edit_field(page, '#pd-about', 'Synchronized afterwards')
        self.save_editor(page)
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 1)")

        self.reconnect(page, context)
        self.settled(page)
        self.assertEqual(self.stored(server, person, 'about'), 'Synchronized afterwards')

        # No visual rollback: the value the user typed is what the page shows,
        # before and after the acknowledgement patched the cache.
        o._wait_content_contains(page, 'Synchronized afterwards')
        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('#sidebar')
        o._open_person(page, person)
        o._wait_content_contains(page, 'Synchronized afterwards')
        self.assertEqual(self.profile_operations(page), [],
                         'and no pending overlay is left behind')

    def test_the_online_save_takes_the_same_durable_path(self):
        """One path or two features. An online save that bypassed the queue
        would be a second mutation boundary for the same decision."""
        server, page, context = self.start()
        person = server.ids['person']
        self.prepare_person(page, person)

        self.open_editor(page)
        self.edit_field(page, '#pd-about', 'Saved while connected')
        # The queue is not polled for the row: online it can be enqueued, sent
        # and retired between two polls. What proves the durable path was used
        # is the sync request itself.
        with page.expect_response(
                lambda r: '/api/sync/operations' in r.url and r.status == 200):
            self.save_editor(page)
        self.settled(page)
        self.assertEqual(self.stored(server, person, 'about'), 'Saved while connected')
        # The revision moved, which is what an offline device needs in order to
        # discover it was overtaken.
        state = self.db_for(server).get_person_metadata_state(person)
        self.assertEqual(state['fields']['about']['revision'], 1)

    # ---- composition with a Person created offline ---------------------------

    def create_person(self, page, first, last):
        page.evaluate("() => { void openModal('person-modal'); }")
        page.locator('#person-modal:not(.hidden)').wait_for()
        page.locator('#person-fname').fill(first)
        page.locator('#person-lname').fill(last)
        page.locator('#save-person-btn').click()
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === 'CREATE_PERSON'))",
            message='the Person was never recorded durably')
        return page.evaluate(
            "() => prksSync.store.listOperations().then(rows => (rows.find("
            "  o => o.operation === 'CREATE_PERSON') || {}).entity_id)")

    def test_editing_a_person_created_offline_is_ordered_behind_the_creation(self):
        """The reason this milestone follows 3B.

        Both decisions are the user's and both are real, so neither is folded
        into the other: the edit is a separate operation carrying a dependency
        on the creation, through the same generic mechanism a role link uses.
        """
        server, page, context = self.start()
        self.offline(page, context)
        person = self.create_person(page, 'Ada', 'Offline')
        create_op = [op for op in self.operations(page) if op[0] == 'CREATE_PERSON'][0]

        o._open_person(page, person)
        o._wait_content_contains(page, 'Ada Offline')
        self.open_editor(page)
        self.edit_field(page, '#pd-about', 'Edited before it ever synchronized')
        self.save_editor(page)
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === 'SET_PERSON_METADATA_FIELD'))")

        edit = self.profile_operations(page)[0]
        create_id = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => (rows.find("
            "  o => o.operation === 'CREATE_PERSON') || {}).op_id)")
        self.assertEqual(edit[4], '["%s"]' % create_id,
                         'the edit names the creation as its prerequisite')
        self.assertEqual(create_op[3].count('Edited before'), 0,
                         'and the creation payload is not rewritten')

        # The effective profile composes both, before either has been sent.
        o._wait_content_contains(page, 'Edited before it ever synchronized')

        self.reconnect(page, context)
        self.settled(page)
        db = self.db_for(server)
        rows = [p for p in db.get_all_persons() if p['id'] == person]
        self.assertEqual(len(rows), 1, 'created once, not twice')
        self.assertEqual(rows[0]['about'], 'Edited before it ever synchronized')
        self.assertEqual(rows[0]['first_name'], 'Ada')

    def test_one_offline_creation_carries_two_unrelated_families_behind_it(self):
        """CREATE_PERSON, with a role link and a profile edit BOTH behind it.

        The graph is a fan-out, not a chain -- the role and the edit are
        independent of each other, and inventing a dependency between them to
        reach three levels would be testing a rule the product does not have.
        What this proves is the part that matters: the generic machinery orders
        families it knows nothing about, and one prerequisite can carry several
        dependants that were never told about one another.
        """
        server, page, context = self.start()
        work = server.ids['work_a']
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work-people-state', work)
        self.offline(page, context)

        person = self.create_person(page, 'Grace', 'Chained')
        o._open_work_from_home(page, WORK_A_TITLE)
        o._open_details_drawer_if_tiled(page)
        page.wait_for_selector('.work-detail')
        page.evaluate("() => { void prksSetWorkDetailsMode('people'); }")
        page.wait_for_function(
            "() => { const b = document.querySelector('.work-link-person-btn');"
            "        return !!b && !b.disabled; }")
        page.locator('.work-link-person-btn').click()
        page.wait_for_selector('#role-modal:not(.hidden)')
        page.wait_for_function(
            "() => typeof document.getElementById('role-person-search').oninput === 'function'")
        page.locator('#role-person-search').fill('Grace Chained')
        result = page.locator('#role-person-results .result-item--person-pick').first
        result.wait_for(state='visible')
        result.click()
        page.locator('#save-role-btn').click()
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === 'ADD_WORK_PERSON_ROLE'))")

        o._open_person(page, person)
        o._wait_content_contains(page, 'Grace Chained')
        self.open_editor(page)
        self.edit_field(page, '#pd-about', 'Third in the chain')
        self.save_editor(page)
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === 'SET_PERSON_METADATA_FIELD'))")

        rows = self.operations(page)
        families = {op[0] for op in rows}
        self.assertTrue({'CREATE_PERSON', 'ADD_WORK_PERSON_ROLE',
                         'SET_PERSON_METADATA_FIELD'} <= families, families)
        # Both dependants name the CREATION, and neither names the other.
        created = [op for op in rows if op[0] == 'CREATE_PERSON']
        self.assertEqual(len(created), 1)
        create_id = [op for op in page.evaluate(
            "() => prksSync.store.listOperations().then(rows => rows.filter("
            "  o => o.operation === 'CREATE_PERSON').map(o => o.op_id))")][0]
        for family in ('ADD_WORK_PERSON_ROLE', 'SET_PERSON_METADATA_FIELD'):
            depends = [json.loads(op[4]) for op in rows if op[0] == family]
            self.assertEqual(depends, [[create_id]], family)

        self.reconnect(page, context)
        self.settled(page)
        db = self.db_for(server)
        created = [p for p in db.get_all_persons() if p['id'] == person]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]['about'], 'Third in the chain')
        roles = db.execute_query(
            'SELECT role_type FROM roles WHERE person_id = ? AND work_id = ?', (person, work))
        self.assertEqual([r['role_type'] for r in roles], ['Author'])

    def test_a_failed_prerequisite_blocks_the_edit_instead_of_stranding_it(self):
        """A creation the server refuses takes its dependants down VISIBLY.

        Left pending, the edit would wait forever on something that can never
        arrive, with nothing on screen to say why.
        """
        server, page, context = self.start()
        self.offline(page, context)
        person = self.create_person(page, 'Doomed', 'Person')
        o._open_person(page, person)
        o._wait_content_contains(page, 'Doomed Person')
        self.open_editor(page)
        self.edit_field(page, '#pd-about', 'Never reaches the server')
        self.save_editor(page)
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 2)")

        # The server refuses the creation, terminally.
        page.evaluate("""() => prksSync.store.listOperations().then(rows => {
            const create = rows.find(o => o.operation === 'CREATE_PERSON');
            return prksSync.store.updateOperationSyncState(create.op_id, {
                status: 'acknowledged', server_result: { code: 'INVALID_ENVELOPE' } })
                .then(() => prksSync.store.markDependentsFailed(create.op_id));
        })""")
        edits = self.profile_operations(page)
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0][2], 'conflict',
                         'surfaced as a decision rather than left waiting')

        # And a NEW edit cannot be enqueued against that creation either.
        self.open_editor(page)
        self.edit_field(page, '#pd-about', 'Another attempt')
        self.save_editor(page, closes=False)
        page.wait_for_function(
            "() => { const el = document.getElementById('prks-modal-confirm');"
            "        return !!el && !el.classList.contains('hidden'); }",
            timeout=15000)
        page.locator('#prks-modal-confirm-ok').click()
        self.assertEqual(len(self.profile_operations(page)), 1,
                         'no second doomed operation is written')


if __name__ == '__main__':
    unittest.main()
