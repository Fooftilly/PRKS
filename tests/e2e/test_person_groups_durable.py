"""Person Groups, created and changed with no server.

The milestone's vertical slice: creating a group, renaming it, moving it,
deleting it and changing who is in it are the same feature with or without a
connection, every one of them composes into the surfaces that show a group, and
a group created on this device is usable by the operations that follow it.
"""
import json
import os
import unittest

from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig
from tests.e2e import test_offline as o
from tests.e2e.fixtures import (PERSON_B_LAST, PERSON_DISPLAY, PERSON_GROUP_NAME,
                                seed_people_library)
from tests.e2e.harness import AppServer, open_app_page, require_chromium, wait_for_async


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get('PRKS_E2E') == '1' else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close(); _PW.stop()


class DurablePersonGroupTests(unittest.TestCase):

    # ---- harness ------------------------------------------------------------

    def start(self):
        server = AppServer(seed_fn=seed_people_library)
        self.addCleanup(server.stop); server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin,
                                                 service_workers='allow')
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        o._wait_sw_active(page)
        o._open_people_index(page)
        o._wait_list_cached(page, 'people:index')
        self.index(page)
        o._wait_list_cached(page, 'person-groups:index')
        return server, page, context

    def index(self, page):
        page.evaluate("() => prksNavigate('#/people/groups')")

    def detail(self, page, gid):
        page.evaluate("id => prksNavigate('#/people/groups/' + id)", gid)

    def prepare_group(self, page, group_id):
        """Open the group once while connected, so its revisions are cached.

        Unknown durable state is not empty: without this projection the client
        would have to guess revision 0 and could overwrite another device.
        """
        self.detail(page, group_id)
        o._wait_entity_cached(page, 'person-group', group_id)
        page.evaluate("id => { void prksReadPersonGroupState(id); }", group_id)
        o._wait_entity_cached(page, 'person-group-state', group_id)

    def offline(self, page, context):
        context.set_offline(True)
        page.evaluate('() => prksOfflineNoteRequestFailure()')
        page.wait_for_function("() => prksOfflineRuntimeState() !== 'online'")

    def reconnect(self, page, context):
        context.set_offline(False)
        page.evaluate("() => window.dispatchEvent(new Event('online'))")
        page.wait_for_function("() => prksOfflineRuntimeState() === 'online'")

    def settled(self, page):
        page.evaluate("""async () => {
            const deadline = Date.now() + 30000;
            while (Date.now() < deadline) {
                const rows = await prksSync.store.listOperations();
                if (!rows.some(o => o.status !== 'conflict')) return;
                await new Promise(r => setTimeout(r, 120));
            }
        }""")

    def operations(self, page):
        return [tuple(row) for row in page.evaluate(
            "() => prksSync.store.listOperations().then(rows => rows.map("
            "  o => [o.operation, o.entity_id, o.status,"
            "        JSON.stringify(o.payload), JSON.stringify(o.depends_on || [])]))")]

    def wait_for_family(self, page, operation):
        wait_for_async(
            page,
            "op => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === op))",
            arg=operation, timeout=30000,
            message='%s was never enqueued' % operation)

    def db_for(self, server):
        existing = getattr(self, '_db', None)
        if existing is None:
            existing = PRKSDatabase(storage=StorageConfig.for_testing(server.storage_root))
            self._db = existing
        return existing

    def groups_in_db(self, server):
        return {row['name']: row for row in self.db_for(server).get_all_person_groups()}

    def create_group(self, page, name, description=''):
        """Through the real New Group modal."""
        page.evaluate("() => openModal('group-modal')")
        page.wait_for_selector('#group-modal:not(.hidden)')
        page.locator('#group-name').fill(name)
        if description:
            page.locator('#group-description').fill(description)
        page.locator('#save-group-btn').click()
        wait_for_async(
            page,
            "n => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === 'CREATE_PERSON_GROUP' && o.payload.name === n))",
            arg=name, timeout=30000,
            message='the group was never recorded durably')
        return page.evaluate(
            "n => prksSync.store.listOperations().then(rows => (rows.find("
            "  o => o.operation === 'CREATE_PERSON_GROUP' && o.payload.name === n)"
            "  || {}).entity_id)", name)

    def wait_content_lacks(self, page, text):
        page.wait_for_function(
            """needle => {
                const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                const root = ctx && ctx.root;
                return !!root && root.innerText.indexOf(needle) === -1;
            }""",
            arg=text, timeout=30000)

    def open_member_manager(self, page):
        page.wait_for_selector('.document-view--group-detail', timeout=15000)
        page.evaluate("() => prksTogglePersonGroupMembersEdit()")
        # The picker MOUNTS asynchronously: the button exists before its
        # handler is bound, and clicking in that window does nothing at all.
        page.wait_for_function(
            "() => !!document.querySelector('#group-add-member-btn')?.onclick",
            timeout=15000)

    def add_member(self, page, person_id):
        page.evaluate("id => { document.getElementById('group-add-member-id').value = id; }",
                      person_id)
        page.locator('#group-add-member-btn').click()

    def open_editor(self, page):
        # The route has to have finished rendering: `openPersonGroupEdit` reads
        # the group off the tab context, and a click during the navigation
        # would find nothing there.
        page.wait_for_selector('.document-view--group-detail', timeout=15000)
        o._open_details_drawer_if_tiled(page)
        page.evaluate("() => { try { openPersonGroupEdit(); } catch (_e) {} }")
        # The panel MOUNTS asynchronously: the button exists before its handler
        # is bound, and clicking in that window does nothing at all.
        page.wait_for_function("() => !!document.querySelector('#gd-save-btn')?.onclick",
                               timeout=15000)

    # ---- creation -----------------------------------------------------------

    def test_a_group_created_offline_is_real_immediately_and_survives_reload(self):
        server, page, context = self.start()
        self.offline(page, context)

        group = self.create_group(page, 'Offline Circle', 'Made with no server')
        self.assertRegex(group, r'^PG-[0-9A-F]{32}$',
                         'a permanent id minted here, never remapped later')
        o._wait_content_contains(page, 'Offline Circle', timeout=30000)

        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('#sidebar')
        self.offline(page, context)
        self.index(page)
        o._wait_content_contains(page, 'Offline Circle', timeout=30000)
        self.detail(page, group)
        o._wait_content_contains(page, 'Made with no server', timeout=30000)

    def test_reconnecting_creates_the_group_once_with_no_rollback(self):
        server, page, context = self.start()
        self.offline(page, context)
        group = self.create_group(page, 'Offline Circle')

        self.reconnect(page, context)
        self.settled(page)
        rows = self.groups_in_db(server)
        self.assertIn('Offline Circle', rows)
        self.assertEqual(rows['Offline Circle']['id'], group,
                         'the id the client minted is the id SQLite stores')
        self.index(page)
        o._wait_content_contains(page, 'Offline Circle', timeout=30000)
        self.assertEqual(
            len([g for g in self.db_for(server).get_all_person_groups()
                 if g['name'] == 'Offline Circle']), 1, 'created once, not twice')

    def test_a_name_the_server_already_has_comes_back_as_a_named_refusal(self):
        """Uniqueness is CANONICAL: only the server sees every group, so two
        devices that both created the same name cannot have resolved it between
        themselves."""
        server, page, context = self.start()
        self.offline(page, context)
        self.create_group(page, PERSON_GROUP_NAME)

        self.reconnect(page, context)
        self.settled(page)
        rows = [op for op in self.operations(page)
                if op[0] == 'CREATE_PERSON_GROUP']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], 'conflict',
                         'the user chose a name and a description; that decision is theirs')
        state = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => (rows.find("
            "  o => o.operation === 'CREATE_PERSON_GROUP') || {}).server_result)")
        self.assertEqual(state['code'], 'NAME_TAKEN')

    # ---- fields -------------------------------------------------------------

    def test_renaming_a_group_offline_reaches_every_surface_that_names_it(self):
        server, page, context = self.start()
        group = server.ids['person_group']
        self.prepare_group(page, group)
        o._open_person(page, server.ids['person'])
        o._wait_content_contains(page, PERSON_DISPLAY)
        self.offline(page, context)

        self.detail(page, group)
        self.open_editor(page)
        page.locator('#gd-name').fill('Renamed Offline')
        page.locator('#gd-save-btn').click()
        self.wait_for_family(page, 'SET_PERSON_GROUP_FIELD')
        o._wait_content_contains(page, 'Renamed Offline', timeout=30000)

        # The catalogue, and the chip on the Person who is in it.
        self.index(page)
        o._wait_content_contains(page, 'Renamed Offline', timeout=30000)
        o._open_person(page, server.ids['person'])
        o._wait_content_contains(page, 'Renamed Offline', timeout=30000)

        self.reconnect(page, context)
        self.settled(page)
        self.assertIn('Renamed Offline', self.groups_in_db(server))

    def test_a_rename_taken_back_before_it_is_sent_leaves_no_intent(self):
        """A -> B -> A is not two changes, it is none -- and that can only be
        got right by measuring against the ACKNOWLEDGED name rather than the
        one on screen."""
        server, page, context = self.start()
        group = server.ids['person_group']
        self.prepare_group(page, group)
        self.offline(page, context)

        self.open_editor(page)
        page.locator('#gd-name').fill('Temporarily Renamed')
        page.locator('#gd-save-btn').click()
        self.wait_for_family(page, 'SET_PERSON_GROUP_FIELD')
        o._wait_content_contains(page, 'Temporarily Renamed', timeout=30000)

        self.open_editor(page)
        self.assertEqual(page.evaluate("() => document.querySelector('#gd-name').value"),
                         'Temporarily Renamed',
                         'the editor opens from the effective group, not the cached one')
        page.locator('#gd-name').fill(PERSON_GROUP_NAME)
        page.locator('#gd-save-btn').click()
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=30000, message='reverting an unsent rename must leave no operation behind')

    def test_two_fields_compose_and_one_busy_field_blocks_only_itself(self):
        server, page, context = self.start()
        group = server.ids['person_group']
        self.prepare_group(page, group)
        self.offline(page, context)

        self.open_editor(page)
        page.locator('#gd-name').fill('Both Changed')
        page.locator('#gd-description').fill('A new description')
        page.locator('#gd-save-btn').click()
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 2)",
            timeout=30000, message='each field is its own decision and its own operation')

        # The name lands in conflict; the description must stay editable.
        page.evaluate("""() => prksSync.store.listOperations().then(rows => {
            const row = rows.find(o => o.payload.field === 'name');
            return prksSync.store.updateOperationSyncState(row.op_id, {
                status: 'conflict',
                server_result: { code: 'REVISION_CONFLICT', current_revision: 4,
                                 current_value: 'Someone else renamed it' },
            });
        })""")
        self.open_editor(page)
        page.locator('#gd-description').fill('Edited again')
        page.locator('#gd-save-btn').click()
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.payload.field === 'description' && o.payload.value === 'Edited again'))",
            timeout=30000, message='an undecided rename must not refuse the whole form')

    # ---- membership ---------------------------------------------------------

    def test_membership_changes_offline_and_composes_into_both_ends(self):
        server, page, context = self.start()
        group = server.ids['person_group']
        person = server.ids['person_b']
        self.prepare_group(page, group)
        o._open_person(page, person)
        o._wait_entity_cached(page, 'person', person)
        self.offline(page, context)

        self.detail(page, group)
        self.open_member_manager(page)
        self.add_member(page, person)
        self.wait_for_family(page, 'ADD_PERSON_GROUP_MEMBER')

        # The group's own member list, and the Person's group chips.
        o._wait_content_contains(page, PERSON_B_LAST, timeout=30000)
        o._open_person(page, person)
        o._wait_content_contains(page, PERSON_GROUP_NAME, timeout=30000)

        self.reconnect(page, context)
        self.settled(page)
        rows = self.db_for(server).execute_query(
            'SELECT 1 FROM person_group_members WHERE group_id = ? AND person_id = ?',
            (group, person))
        self.assertEqual(len(rows), 1)

    def test_a_membership_added_and_taken_back_leaves_nothing(self):
        server, page, context = self.start()
        group = server.ids['person_group']
        person = server.ids['person_b']
        self.prepare_group(page, group)
        self.offline(page, context)

        self.detail(page, group)
        self.open_member_manager(page)
        self.add_member(page, person)
        self.wait_for_family(page, 'ADD_PERSON_GROUP_MEMBER')
        page.locator('[data-remove-member="%s"]' % person).first.click()
        page.locator('#prks-modal-confirm:not(.hidden)').wait_for()
        page.locator('#prks-modal-confirm-ok').click()
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=30000, message='added and taken back is not two changes, it is none')

    def test_a_person_and_a_group_both_created_offline_can_be_joined(self):
        """The generic dependency machinery orders a relationship between two
        things this device invented, behind both of them."""
        server, page, context = self.start()
        self.offline(page, context)

        group = self.create_group(page, 'Offline Circle')
        page.evaluate("() => openModal('person-modal')")
        page.wait_for_selector('#person-modal:not(.hidden)')
        page.locator('#person-fname').fill('Grace')
        page.locator('#person-lname').fill('Invented')
        page.locator('#save-person-btn').click()
        self.wait_for_family(page, 'CREATE_PERSON')
        person = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => (rows.find("
            "  o => o.operation === 'CREATE_PERSON') || {}).entity_id)")

        self.detail(page, group)
        o._wait_content_contains(page, 'Offline Circle', timeout=30000)
        self.open_member_manager(page)
        self.add_member(page, person)
        self.wait_for_family(page, 'ADD_PERSON_GROUP_MEMBER')

        membership = [op for op in self.operations(page)
                      if op[0] == 'ADD_PERSON_GROUP_MEMBER'][0]
        self.assertEqual(len(json.loads(membership[4])), 2,
                         'it waits for the group AND the person')

        self.reconnect(page, context)
        self.settled(page)
        rows = self.db_for(server).execute_query(
            'SELECT 1 FROM person_group_members WHERE group_id = ? AND person_id = ?',
            (group, person))
        self.assertEqual(len(rows), 1, 'all three reached the server, in order')

    # ---- deletion -----------------------------------------------------------

    def test_deleting_a_group_offline_is_a_tombstone_that_survives_reload(self):
        server, page, context = self.start()
        group = server.ids['person_group']
        self.prepare_group(page, group)
        self.offline(page, context)

        self.detail(page, group)
        self.open_editor(page)
        # Invoked directly: Delete lives in a collapsed advanced section, so
        # which markup happens to expose it is not what this proves.
        page.evaluate("() => { void document.getElementById('gd-delete-btn').onclick(); }")
        page.locator('#prks-modal-confirm:not(.hidden)').wait_for()
        page.locator('#prks-modal-confirm-ok').click()
        self.wait_for_family(page, 'DELETE_PERSON_GROUP')

        self.index(page)
        self.wait_content_lacks(page, PERSON_GROUP_NAME)
        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('#sidebar')
        self.offline(page, context)
        self.index(page)
        self.wait_content_lacks(page, PERSON_GROUP_NAME)

        self.reconnect(page, context)
        self.settled(page)
        self.assertNotIn(PERSON_GROUP_NAME, self.groups_in_db(server))

    def test_deleting_a_group_created_offline_folds_the_whole_case_away(self):
        server, page, context = self.start()
        self.offline(page, context)
        group = self.create_group(page, 'Offline Mistake', 'Typed by accident')
        self.detail(page, group)
        self.open_editor(page)
        # Invoked directly: Delete lives in a collapsed advanced section, so
        # which markup happens to expose it is not what this proves.
        page.evaluate("() => { void document.getElementById('gd-delete-btn').onclick(); }")
        page.locator('#prks-modal-confirm:not(.hidden)').wait_for()
        page.locator('#prks-modal-confirm-ok').click()
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=30000, message='nothing about this group should ever reach the server')

        self.reconnect(page, context)
        self.settled(page)
        self.assertNotIn('Offline Mistake', self.groups_in_db(server))


if __name__ == '__main__':
    unittest.main()
