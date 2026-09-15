"""Concepts, created and changed with no server.

The two decisions worth proving here are the ones that are easy to get wrong:
a Concept's NAME and its ALIAS SET are one decision, because renaming keeps the
old name reachable; and the parent set is one structural judgement rather than
a collection of independently racing edges.

Every durable-queue gate here allows a generous timeout. A save offline first
reads the base it measures the edit against, and offline that read has to let a
doomed request fail before the cache answers.
"""
import os
import unittest

from backend.db_manager import PRKSDatabase
from backend.research_network import get_concept
from backend.storage.config import StorageConfig
from tests.e2e import test_offline as o
from tests.e2e.fixtures import (
    CONCEPT_CHILD_NAME,
    CONCEPT_PARENT_NAME,
    seed_concepts_library,
)
from tests.e2e.harness import AppServer, open_app_page, require_chromium, wait_for_async


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get('PRKS_E2E') == '1' else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close(); _PW.stop()


class DurableConceptTests(unittest.TestCase):

    # ---- harness ------------------------------------------------------------

    def start(self):
        server = AppServer(seed_fn=seed_concepts_library)
        self.addCleanup(server.stop); server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin,
                                                 service_workers='allow')
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        o._wait_sw_active(page)
        self.index(page)
        o._wait_list_cached(page, 'concepts:index')
        return server, page, context

    def index(self, page):
        page.evaluate("() => prksNavigate('#/concepts')")

    def detail(self, page, concept_id):
        page.evaluate("id => prksNavigate('#/concepts/' + id)", concept_id)

    def prepare(self, page, concept_id):
        """Open the Concept once while connected, so its revisions are cached."""
        self.detail(page, concept_id)
        o._wait_entity_cached(page, 'concept', concept_id)
        page.evaluate("id => { void Promise.resolve(prksReadConceptState(id)).catch(() => {}); }",
                      concept_id)
        o._wait_entity_cached(page, 'concept-state', concept_id)

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

    def concepts_in_db(self, server):
        return {row['name']: row for row in
                self.db_for(server).execute_query('SELECT * FROM concepts')}

    # ---- creation -----------------------------------------------------------

    def test_a_concept_created_offline_is_real_and_survives_reload(self):
        server, page, context = self.start()
        self.offline(page, context)

        concept = page.evaluate(
            "() => createConcept({ name: 'Offline Concept' }).then(c => c.id)")
        self.assertRegex(concept, r'^C-[0-9A-F]{32}$',
                         'a permanent id minted here, never remapped later')
        self.index(page)
        o._wait_content_contains(page, 'Offline Concept')

        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('#sidebar')
        self.offline(page, context)
        self.index(page)
        o._wait_content_contains(page, 'Offline Concept')
        self.detail(page, concept)
        o._wait_content_contains(page, 'Offline Concept')

        self.reconnect(page, context)
        self.drained(page)
        rows = self.concepts_in_db(server)
        self.assertIn('Offline Concept', rows)
        self.assertEqual(rows['Offline Concept']['id'], concept,
                         'the id the client minted is the id SQLite stores')

    def test_a_taken_name_comes_back_as_a_named_refusal(self):
        server, page, context = self.start()
        self.offline(page, context)
        page.evaluate("name => createConcept({ name: name })", CONCEPT_PARENT_NAME)
        self.wait_for_family(page, 'CREATE_CONCEPT')

        self.reconnect(page, context)
        self.settled(page)
        state = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => (rows.find("
            "  o => o.operation === 'CREATE_CONCEPT') || {}).server_result)")
        self.assertEqual(state['code'], 'CONCEPT_EXISTS')

    def test_a_concept_created_offline_can_be_edited_before_it_is_sent(self):
        """Its construction payload is the base, at a revision that is known
        rather than assumed -- nothing else can have written to an id no other
        device has seen."""
        server, page, context = self.start()
        self.offline(page, context)
        concept = page.evaluate(
            "() => createConcept({ name: 'Draft Concept' }).then(c => c.id)")
        page.evaluate("id => updateConcept(id, { description: 'Written offline.' })", concept)
        self.wait_for_family(page, 'SET_CONCEPT_FIELD')
        depends = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => (rows.find("
            "  o => o.operation === 'SET_CONCEPT_FIELD') || {}).depends_on)")
        self.assertEqual(len(depends), 1, 'the edit waits for the creation')

        self.reconnect(page, context)
        self.drained(page)
        stored = self.concepts_in_db(server)['Draft Concept']
        self.assertEqual(stored['description'], 'Written offline.')

    def test_a_concept_created_offline_can_become_another_concepts_parent(self):
        """The cross-family chain the generic dependency mechanism exists for --
        no Concept-specific ordering anywhere."""
        server, page, context = self.start()
        child = server.ids['concept_child']
        self.prepare(page, child)
        self.offline(page, context)

        parent = page.evaluate(
            "() => createConcept({ name: 'Offline Parent' }).then(c => c.id)")
        page.evaluate("([cid, pid]) => putConceptParents(cid, [pid])", [child, parent])
        self.wait_for_family(page, 'SET_CONCEPT_PARENTS')
        depends = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => (rows.find("
            "  o => o.operation === 'SET_CONCEPT_PARENTS') || {}).depends_on)")
        self.assertEqual(len(depends), 1,
                         'the reparent waits for the parent that does not exist yet')

        self.reconnect(page, context)
        self.drained(page)
        stored = get_concept(self.db_for(server), child)
        self.assertEqual([p['name'] for p in stored['parents']], ['Offline Parent'])

    # ---- the definition -----------------------------------------------------

    def test_a_definition_edited_offline_shows_and_lands(self):
        server, page, context = self.start()
        child = server.ids['concept_child']
        self.prepare(page, child)
        self.offline(page, context)

        page.evaluate("id => updateConcept(id, { description: 'Edited offline.' })", child)
        self.wait_for_family(page, 'SET_CONCEPT_FIELD')
        self.detail(page, child)
        o._wait_content_contains(page, 'Edited offline.')

        self.reconnect(page, context)
        self.drained(page)
        self.assertEqual(get_concept(self.db_for(server), child)['description'],
                         'Edited offline.')

    def test_a_definition_taken_back_before_it_is_sent_leaves_no_intent(self):
        server, page, context = self.start()
        child = server.ids['concept_child']
        original = get_concept(self.db_for(server), child)['description']
        self.prepare(page, child)
        self.offline(page, context)

        page.evaluate("id => updateConcept(id, { description: 'Temporary.' })", child)
        self.wait_for_family(page, 'SET_CONCEPT_FIELD')
        page.evaluate("([id, text]) => updateConcept(id, { description: text })",
                      [child, original])
        self.drained(page, 'reverting an unsent edit must leave no operation behind')

    # ---- identity -----------------------------------------------------------

    def test_a_rename_offline_keeps_the_old_name_as_an_alias(self):
        """The rule that makes name and aliases one decision: every note that
        already says the old name must go on resolving."""
        server, page, context = self.start()
        child = server.ids['concept_child']
        self.prepare(page, child)
        self.offline(page, context)

        page.evaluate("id => updateConcept(id, { name: 'Renamed Offline' })", child)
        self.wait_for_family(page, 'SET_CONCEPT_IDENTITY')
        self.index(page)
        o._wait_content_contains(page, 'Renamed Offline')

        self.reconnect(page, context)
        self.drained(page)
        stored = get_concept(self.db_for(server), child)
        self.assertEqual(stored['name'], 'Renamed Offline')
        self.assertIn(CONCEPT_CHILD_NAME, stored['aliases'],
                      'the old name stays reachable, so existing notes still resolve')

    def test_an_alias_set_edited_offline_survives_a_reload(self):
        server, page, context = self.start()
        child = server.ids['concept_child']
        self.prepare(page, child)
        self.offline(page, context)

        page.evaluate("id => putConceptAliases(id, ['Offline Alias'])", child)
        self.wait_for_family(page, 'SET_CONCEPT_IDENTITY')

        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('#sidebar')
        self.offline(page, context)
        self.detail(page, child)
        o._wait_content_contains(page, 'Offline Alias')

        self.reconnect(page, context)
        self.drained(page)
        self.assertEqual(get_concept(self.db_for(server), child)['aliases'],
                         ['Offline Alias'])

    def test_a_rename_and_an_alias_edit_are_one_conflict_unit(self):
        """They cannot be separate: the rename writes into the set the alias
        edit is choosing."""
        server, page, context = self.start()
        child = server.ids['concept_child']
        self.prepare(page, child)
        self.offline(page, context)

        page.evaluate("id => updateConcept(id, { name: 'First Rename' })", child)
        self.wait_for_family(page, 'SET_CONCEPT_IDENTITY')
        page.evaluate("id => putConceptAliases(id, ['Chosen Alias'])", child)
        rows = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => rows"
            "  .filter(o => o.operation === 'SET_CONCEPT_IDENTITY').length)")
        self.assertEqual(rows, 1, 'one scope, so the later decision replaces the earlier')

    # ---- hierarchy ----------------------------------------------------------

    def test_a_reparent_offline_shows_at_both_ends(self):
        server, page, context = self.start()
        child = server.ids['concept_child']
        parent = server.ids['concept_parent']
        unvisited = server.ids['concept_unvisited']
        self.prepare(page, child)
        self.prepare(page, parent)
        self.prepare(page, unvisited)
        self.offline(page, context)

        page.evaluate("([cid, pid]) => putConceptParents(cid, [pid])", [child, unvisited])
        self.wait_for_family(page, 'SET_CONCEPT_PARENTS')

        # The child names its new parent ...
        self.detail(page, child)
        o._wait_content_contains(page, server.ids['concept_unvisited_name'], timeout=30000)
        # ... and the new parent lists the child.
        self.detail(page, unvisited)
        o._wait_content_contains(page, CONCEPT_CHILD_NAME, timeout=30000)

        self.reconnect(page, context)
        self.drained(page)
        stored = get_concept(self.db_for(server), child)
        self.assertEqual([p['id'] for p in stored['parents']], [unvisited])

    def test_the_same_parents_in_a_different_order_leave_no_intent(self):
        server, page, context = self.start()
        child = server.ids['concept_child']
        parent = server.ids['concept_parent']
        self.prepare(page, child)
        self.offline(page, context)
        page.evaluate("([cid, pid]) => putConceptParents(cid, [pid])", [child, parent])
        self.drained(page, 'the same parent set is the same decision, not a change')

    def test_a_cycle_comes_back_as_a_named_refusal(self):
        server, page, context = self.start()
        child = server.ids['concept_child']
        parent = server.ids['concept_parent']
        self.prepare(page, parent)
        self.offline(page, context)

        page.evaluate("([pid, cid]) => putConceptParents(pid, [cid])", [parent, child])
        self.wait_for_family(page, 'SET_CONCEPT_PARENTS')
        self.reconnect(page, context)
        self.settled(page)
        state = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => (rows.find("
            "  o => o.operation === 'SET_CONCEPT_PARENTS') || {}).server_result)")
        self.assertEqual(state['code'], 'CONCEPT_CYCLE')
        self.assertEqual(get_concept(self.db_for(server), parent)['parents'], [])

    # ---- deletion -----------------------------------------------------------

    def test_deleting_a_concept_offline_is_a_tombstone(self):
        server, page, context = self.start()
        unvisited = server.ids['concept_unvisited']
        self.prepare(page, unvisited)
        self.offline(page, context)

        page.evaluate("id => deleteConcept(id)", unvisited)
        self.wait_for_family(page, 'DELETE_CONCEPT')
        self.index(page)
        page.wait_for_function(
            """needle => {
                const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                const root = ctx && ctx.root;
                return !!root && root.innerText.indexOf(needle) === -1;
            }""",
            arg=server.ids['concept_unvisited_name'], timeout=30000)

        self.reconnect(page, context)
        self.drained(page)
        self.assertNotIn(server.ids['concept_unvisited_name'], self.concepts_in_db(server))

    def test_a_concept_a_note_still_names_comes_back_refused_and_visible(self):
        """The canonical protection is unchanged, and the tombstone is undone by
        the server doing nothing: the Concept comes back."""
        server, page, context = self.start()
        child = server.ids['concept_child']
        self.prepare(page, child)
        self.offline(page, context)

        page.evaluate("id => deleteConcept(id)", child)
        self.wait_for_family(page, 'DELETE_CONCEPT')
        self.reconnect(page, context)
        self.settled(page)
        state = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => (rows.find("
            "  o => o.operation === 'DELETE_CONCEPT') || {}).server_result)")
        self.assertEqual(state['code'], 'CONCEPT_IN_USE')
        self.assertIn(CONCEPT_CHILD_NAME, self.concepts_in_db(server))

    def test_deleting_a_concept_created_offline_folds_the_whole_case_away(self):
        server, page, context = self.start()
        self.offline(page, context)
        concept = page.evaluate(
            "() => createConcept({ name: 'Offline Mistake' }).then(c => c.id)")
        self.wait_for_family(page, 'CREATE_CONCEPT')
        page.evaluate("id => deleteConcept(id)", concept)
        self.drained(page, 'nothing about this Concept should ever reach the server')

        self.reconnect(page, context)
        self.settled(page)
        self.assertNotIn('Offline Mistake', self.concepts_in_db(server))


if __name__ == '__main__':
    unittest.main()
