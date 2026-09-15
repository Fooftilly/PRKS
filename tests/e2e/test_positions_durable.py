"""Positions, created and changed with no server.

The property worth proving hardest is the one that makes this domain SMALL:
`name` and `description` are independent, so each coalesces and cancels on its
own and one busy field never blocks the other. If they were ever joined into an
aggregate, three of these tests would fail.

The cross-family case -- an offline-created Position becoming a pending
Argument's target -- is deliberately deferred to the Argument milestone rather
than faked here.
"""
import os
import unittest

from backend.db_manager import PRKSDatabase
from backend.research_network import get_position, update_position
from backend.storage.config import StorageConfig
from tests.e2e import test_offline as o
from tests.e2e.fixtures import (
    POSITION_A_NAME,
    POSITION_ARGUMENT_NAME,
    seed_positions_library,
)
from tests.e2e.harness import AppServer, open_app_page, require_chromium, wait_for_async


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get('PRKS_E2E') == '1' else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close(); _PW.stop()


class DurablePositionTests(unittest.TestCase):

    # ---- harness ------------------------------------------------------------

    def start(self):
        server = AppServer(seed_fn=seed_positions_library)
        self.addCleanup(server.stop); server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin,
                                                 service_workers='allow')
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        o._wait_sw_active(page)
        self.index(page)
        o._wait_list_cached(page, 'positions:index')
        return server, page, context

    def index(self, page):
        page.evaluate("() => prksNavigate('#/positions')")

    def detail(self, page, position_id):
        page.evaluate("id => prksNavigate('#/positions/' + id)", position_id)

    def prepare(self, page, position_id):
        """Open the Position once while connected, so its revisions are cached."""
        self.detail(page, position_id)
        o._wait_entity_cached(page, 'position', position_id)
        page.evaluate("id => { void Promise.resolve(prksReadPositionState(id)).catch(() => {}); }",
                      position_id)
        o._wait_entity_cached(page, 'position-state', position_id)

    def offline(self, page, context):
        context.set_offline(True)
        page.evaluate('() => prksOfflineNoteRequestFailure()')
        page.wait_for_function("() => prksOfflineRuntimeState() !== 'online'")

    def reconnect(self, page, context):
        context.set_offline(False)
        page.evaluate("() => window.dispatchEvent(new Event('online'))")
        page.wait_for_function("() => prksOfflineRuntimeState() === 'online'")

    def drained(self, page, message='a durable operation never retired'):
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=40000, message=message)

    def settled(self, page):
        page.evaluate("""async () => {
            const deadline = Date.now() + 30000;
            while (Date.now() < deadline) {
                const rows = await prksSync.store.listOperations();
                if (!rows.some(op => op.status !== 'conflict')) return;
                await new Promise(r => setTimeout(r, 120));
            }
        }""")

    def wait_for_family(self, page, operation):
        wait_for_async(
            page,
            "op => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === op))",
            arg=operation, timeout=60000,
            message='%s was never enqueued' % operation)

    def db_for(self, server):
        existing = getattr(self, '_db', None)
        if existing is None:
            existing = PRKSDatabase(storage=StorageConfig.for_testing(server.storage_root))
            self._db = existing
        return existing

    def positions_in_db(self, server):
        return {row['name']: row for row in
                self.db_for(server).execute_query('SELECT * FROM positions')}

    # ---- creation -----------------------------------------------------------

    def test_a_position_created_offline_is_real_and_survives_reload(self):
        server, page, context = self.start()
        self.offline(page, context)

        position = page.evaluate(
            "() => createPosition({ name: 'Offline Position' }).then(p => p.id)")
        self.assertRegex(position, r'^P-[0-9A-F]{32}$',
                         'a permanent id minted here, never remapped later')
        self.index(page)
        o._wait_content_contains(page, 'Offline Position')

        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('#sidebar')
        self.offline(page, context)
        self.index(page)
        o._wait_content_contains(page, 'Offline Position')
        self.detail(page, position)
        o._wait_content_contains(page, 'Offline Position')

        self.reconnect(page, context)
        self.drained(page)
        rows = self.positions_in_db(server)
        self.assertIn('Offline Position', rows)
        self.assertEqual(rows['Offline Position']['id'], position,
                         'the id the client minted is the id SQLite stores')

    def test_a_position_created_offline_can_be_edited_before_it_is_sent(self):
        server, page, context = self.start()
        self.offline(page, context)
        position = page.evaluate(
            "() => createPosition({ name: 'Draft Position' }).then(p => p.id)")
        page.evaluate("id => updatePosition(id, { description: 'Written offline.' })", position)
        self.wait_for_family(page, 'SET_POSITION_FIELD')
        depends = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => (rows.find("
            "  o => o.operation === 'SET_POSITION_FIELD') || {}).depends_on)")
        self.assertEqual(len(depends), 1, 'the edit waits for the creation')

        self.reconnect(page, context)
        self.drained(page)
        self.assertEqual(self.positions_in_db(server)['Draft Position']['description'],
                         'Written offline.')

    def test_a_repeated_name_is_accepted(self):
        """Positions have never been unique by name, so inventing that rule
        offline would refuse something the ordinary endpoint accepts."""
        server, page, context = self.start()
        self.offline(page, context)
        page.evaluate("name => createPosition({ name: name })", POSITION_A_NAME)
        self.wait_for_family(page, 'CREATE_POSITION')

        self.reconnect(page, context)
        self.drained(page, 'a repeated name is not a refusal')
        rows = self.db_for(server).execute_query(
            'SELECT id FROM positions WHERE name = ?', (POSITION_A_NAME,))
        self.assertEqual(len(rows), 2)

    # ---- fields -------------------------------------------------------------

    def test_a_name_edited_offline_shows_and_lands(self):
        server, page, context = self.start()
        position = server.ids['position_a']
        self.prepare(page, position)
        self.offline(page, context)

        page.evaluate("id => updatePosition(id, { name: 'Renamed Offline' })", position)
        self.wait_for_family(page, 'SET_POSITION_FIELD')
        self.index(page)
        o._wait_content_contains(page, 'Renamed Offline')

        self.reconnect(page, context)
        self.drained(page)
        self.assertEqual(get_position(self.db_for(server), position)['name'],
                         'Renamed Offline')

    def test_a_description_is_edited_independently_of_the_name(self):
        """The property this whole domain rests on: two decisions, two conflict
        units, so neither can refuse the other."""
        server, page, context = self.start()
        position = server.ids['position_a']
        self.prepare(page, position)
        self.offline(page, context)

        page.evaluate("id => updatePosition(id, { description: 'Edited offline.' })", position)
        self.wait_for_family(page, 'SET_POSITION_FIELD')
        page.evaluate("id => updatePosition(id, { name: 'Renamed too' })", position)
        fields = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => rows"
            "  .filter(o => o.operation === 'SET_POSITION_FIELD')"
            "  .map(o => o.payload.field).sort())")
        self.assertEqual(fields, ['description', 'name'],
                         'two independent operations, not one replacing the other')

        self.reconnect(page, context)
        self.drained(page)
        stored = get_position(self.db_for(server), position)
        self.assertEqual(stored['name'], 'Renamed too')
        self.assertEqual(stored['description'], 'Edited offline.')

    def test_an_edit_taken_back_before_it_is_sent_leaves_no_intent(self):
        server, page, context = self.start()
        position = server.ids['position_a']
        self.prepare(page, position)
        self.offline(page, context)

        page.evaluate("id => updatePosition(id, { name: 'Temporarily Renamed' })", position)
        self.wait_for_family(page, 'SET_POSITION_FIELD')
        page.evaluate("([id, name]) => updatePosition(id, { name: name })",
                      [position, POSITION_A_NAME])
        self.drained(page, 'reverting an unsent rename must leave no operation behind')

    def test_one_conflicted_field_does_not_block_the_other(self):
        """A rename refused by the server must not make the description
        unsavable -- they are different decisions."""
        server, page, context = self.start()
        position = server.ids['position_a']
        self.prepare(page, position)

        # Make the name conflict: change it on the server behind the client's
        # back, through the ORDINARY endpoint, so the name's revision actually
        # advances. A raw UPDATE would bypass the shared boundary and prove
        # nothing -- which is precisely why that boundary exists.
        update_position(self.db_for(server), position, name='Renamed by another device')
        self.offline(page, context)
        page.evaluate("id => updatePosition(id, { name: 'Renamed offline' })", position)
        self.wait_for_family(page, 'SET_POSITION_FIELD')
        self.reconnect(page, context)
        self.settled(page)

        conflicted = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => rows"
            "  .filter(o => o.status === 'conflict').map(o => o.payload.field))")
        self.assertEqual(conflicted, ['name'])

        # The description is still editable, and still lands.
        page.evaluate("id => updatePosition(id, { description: 'Still savable.' })", position)
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === 'SET_POSITION_FIELD' &&"
            "       o.payload.field === 'description'))",
            timeout=30000,
            message='a conflicted name must not block the description')
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => !rows.some("
            "  o => o.payload && o.payload.field === 'description'))",
            timeout=40000, message='the description operation never retired')
        self.assertEqual(get_position(self.db_for(server), position)['description'],
                         'Still savable.')

    def test_a_pending_rename_reaches_an_argument_that_targets_it(self):
        """An Argument renders the name of every Position it targets."""
        server, page, context = self.start()
        position = server.ids['position_a']
        argument = server.ids['position_argument']
        self.prepare(page, position)
        page.evaluate("id => prksNavigate('#/arguments/' + id)", argument)
        o._wait_entity_cached(page, 'argument', argument)
        self.offline(page, context)

        page.evaluate("id => updatePosition(id, { name: 'Renamed For Argument' })", position)
        self.wait_for_family(page, 'SET_POSITION_FIELD')
        page.evaluate("id => prksNavigate('#/arguments/' + id)", argument)
        o._wait_content_contains(page, 'Renamed For Argument', timeout=30000)

    # ---- deletion -----------------------------------------------------------

    def test_deleting_a_position_offline_is_a_tombstone(self):
        server, page, context = self.start()
        target = server.ids['position_b']
        self.prepare(page, target)
        self.offline(page, context)

        page.evaluate("id => deletePosition(id)", target)
        self.wait_for_family(page, 'DELETE_POSITION')
        self.index(page)
        page.wait_for_function(
            """needle => {
                const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                const root = ctx && ctx.root;
                return !!root && root.innerText.indexOf(needle) === -1;
            }""",
            arg=server.ids['position_b_name'], timeout=30000)

        self.reconnect(page, context)
        self.drained(page)
        self.assertNotIn(server.ids['position_b_name'], self.positions_in_db(server))

    def test_a_targeted_position_is_refused_and_comes_back(self):
        """The whole deletion contract, in the order a user experiences it:
        hidden while undecided, visible again once the server refuses, with the
        conflict still waiting in Diagnostics."""
        server, page, context = self.start()
        position = server.ids['position_a']
        self.prepare(page, position)
        self.offline(page, context)

        page.evaluate("id => deletePosition(id)", position)
        self.wait_for_family(page, 'DELETE_POSITION')
        self.index(page)
        page.wait_for_function(
            """needle => {
                const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                const root = ctx && ctx.root;
                return !!root && root.innerText.indexOf(needle) === -1;
            }""",
            arg=POSITION_A_NAME, timeout=30000)

        self.reconnect(page, context)
        self.settled(page)
        state = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => (rows.find("
            "  o => o.operation === 'DELETE_POSITION') || {}).server_result)")
        self.assertEqual(state['code'], 'POSITION_IN_USE')
        self.assertIn(POSITION_A_NAME, self.positions_in_db(server))

        # ... and the user can see it again, on the index and on its own page.
        self.index(page)
        o._wait_content_contains(page, POSITION_A_NAME, timeout=30000)
        self.detail(page, position)
        o._wait_content_contains(page, POSITION_A_NAME, timeout=30000)
        conflicts = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => rows.filter("
            "  o => o.status === 'conflict').length)")
        self.assertEqual(conflicts, 1)

    def test_deleting_a_position_created_offline_folds_the_whole_case_away(self):
        server, page, context = self.start()
        self.offline(page, context)
        position = page.evaluate(
            "() => createPosition({ name: 'Offline Mistake' }).then(p => p.id)")
        self.wait_for_family(page, 'CREATE_POSITION')
        page.evaluate("id => deletePosition(id)", position)
        self.drained(page, 'nothing about this Position should ever reach the server')

        self.reconnect(page, context)
        self.settled(page)
        self.assertNotIn('Offline Mistake', self.positions_in_db(server))


if __name__ == '__main__':
    unittest.main()
