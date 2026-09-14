"""Offline Person creation, and the dependency it creates.

The vertical slice this milestone exists for: a Person created without a server
is immediately usable, including as the subject of a relationship, and the two
operations reach the server in an order that can succeed.
"""
import os
import unittest

from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig
from tests.e2e import test_offline as o
from tests.e2e.fixtures import WORK_A_TITLE, seed_library
from tests.e2e.harness import AppServer, open_app_page, require_chromium, wait_for_async


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get('PRKS_E2E') == '1' else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close(); _PW.stop()


class OfflinePersonCreateTests(unittest.TestCase):
    BROWSE = '#/progress?status=Not%20Started'

    # ---- harness ------------------------------------------------------------

    def start(self):
        server = AppServer(seed_fn=seed_library)
        self.addCleanup(server.stop); server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin,
                                                 service_workers='allow')
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        o._wait_sw_active(page)
        # Everything read offline has to have been read once while connected.
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

    def operations(self, page):
        return [tuple(row) for row in page.evaluate(
            "() => prksSync.store.listOperations().then(rows => rows.map("
            "  o => [o.operation, o.entity_id, o.status,"
            "        JSON.stringify(o.depends_on || [])]))")]

    def settled(self, page, count):
        page.evaluate("""async n => {
            const deadline = Date.now() + 30000;
            for (;;) {
                const rows = await prksSync.store.listOperations();
                if (rows.length === n && !rows.some(r => r.status === 'syncing')) return;
                if (Date.now() > deadline) throw new Error('Sync did not settle: ' + JSON.stringify(rows));
                await new Promise(resolve => setTimeout(resolve, 50));
            }
        }""", count)

    def db_for(self, server):
        return PRKSDatabase(storage=StorageConfig.for_testing(server.storage_root))

    def people(self, server):
        return {(p['first_name'], p['last_name']) for p in self.db_for(server).get_all_persons()}

    def link_as_author(self, page, work_title, person_display):
        o._open_work_from_home(page, work_title)
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
        page.locator('#role-person-search').fill(person_display)
        result = page.locator('#role-person-results .result-item--person-pick').first
        result.wait_for(state='visible')
        result.click()
        page.locator('#save-role-btn').click()

    # ---- the slice ----------------------------------------------------------

    def test_a_person_created_offline_can_be_linked_and_both_reach_the_server(self):
        """The whole point of the milestone, end to end.

        A Person created with no server is a real Person immediately -- its
        identity was chosen here and will never be renamed -- so a relationship
        may be built on it at once. The two operations must then arrive in an
        order the server can accept: the creation first.
        """
        server, page, context = self.start()
        work = server.ids['work_a']
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work-people-state', work)
        self.offline(page, context)

        person = self.create_person(page, 'Ada', 'Offline')
        self.assertTrue(person.startswith('P-'), person)
        self.assertEqual(len(person), 34,
                         'a client-chosen identity is full-width, never an 8-hex stub')

        # It is a Person the rest of the app can see at once.
        o._open_people_index(page)
        o._wait_content_contains(page, 'Ada Offline')

        self.link_as_author(page, WORK_A_TITLE, 'Ada Offline')
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === 'ADD_WORK_PERSON_ROLE'))",
            message='the link was never recorded durably')

        rows = {op: (entity, status, deps) for op, entity, status, deps in self.operations(page)}
        self.assertIn('CREATE_PERSON', rows)
        create_id = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => (rows.find("
            "  o => o.operation === 'CREATE_PERSON') || {}).op_id)")
        self.assertEqual(rows['ADD_WORK_PERSON_ROLE'][2], '["%s"]' % create_id,
                         'the link waits on exactly the creation it needs')

        # Durable: a reload while still offline reproduces both.
        page.reload()
        page.wait_for_selector('#sidebar')
        o._open_people_index(page)
        o._wait_content_contains(page, 'Ada Offline')
        # The two this test is about. Opening a Work also records an open
        # event, which is a real durable operation and not this test's concern.
        surviving = {op for op, _entity, _status, _deps in self.operations(page)}
        self.assertIn('CREATE_PERSON', surviving)
        self.assertIn('ADD_WORK_PERSON_ROLE', surviving)

        self.reconnect(page, context)
        self.settled(page, 0)

        self.assertIn(('Ada', 'Offline'), self.people(server))
        roles = self.db_for(server).get_work_roles(work)
        self.assertIn((person, 'Author'), {(r['id'], r['role_type']) for r in roles})

        # And no duplicate or rollback once everything is acknowledged.
        page.reload()
        page.wait_for_selector('#sidebar')
        o._open_people_index(page)
        o._wait_content_contains(page, 'Ada Offline')
        self.assertEqual(
            len([p for p in self.db_for(server).get_all_persons()
                 if p['last_name'] == 'Offline']), 1)

    def test_reconnecting_immediately_after_creation_synchronizes_it(self):
        """No relationship involved: the creation stands on its own."""
        server, page, context = self.start()
        self.offline(page, context)
        self.create_person(page, 'Solo', 'Person')
        self.reconnect(page, context)
        self.settled(page, 0)
        self.assertIn(('Solo', 'Person'), self.people(server))

    def test_quick_create_from_the_role_picker_builds_the_same_chain(self):
        """The picker's own create path. It was the last surface still posting
        directly, which meant the dependency it exists to produce was never
        formed."""
        server, page, context = self.start()
        work = server.ids['work_a']
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work-people-state', work)
        self.offline(page, context)

        page.evaluate("() => { void prksSetWorkDetailsMode('people'); }")
        page.wait_for_function(
            "() => { const b = document.querySelector('.work-link-person-btn');"
            "        return !!b && !b.disabled; }")
        page.locator('.work-link-person-btn').click()
        page.wait_for_selector('#role-modal:not(.hidden)')
        page.wait_for_function(
            "() => typeof document.getElementById('role-person-search').oninput === 'function'")
        page.locator('#role-person-search').fill('Quick Created')
        create = page.locator('#role-person-results .result-item--create').first
        create.wait_for(state='visible')
        create.click()

        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === 'CREATE_PERSON'))",
            message='quick-create must record a durable creation')
        # The new Person is selected in the picker without any server round trip.
        page.wait_for_function(
            "() => (document.getElementById('role-person-id').value || '').startsWith('P-')")
        page.locator('#save-role-btn').click()
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === 'ADD_WORK_PERSON_ROLE'))")

        self.reconnect(page, context)
        self.settled(page, 0)
        self.assertIn(('Quick', 'Created'), self.people(server))
        self.assertTrue(any(r['last_name'] == 'Created'
                            for r in self.db_for(server).get_work_roles(work)))
