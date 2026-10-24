"""Concepts, created and changed with no server.

The two decisions worth proving here are the ones that are easy to get wrong:
a Concept's NAME and its ALIAS SET are one decision, because renaming keeps the
old name reachable; and the parent set is one structural judgement rather than
a collection of independently racing edges.

Every durable-queue gate here allows a generous timeout. A save offline first
reads the base it measures the edit against, and offline that read has to let a
doomed request fail before the cache answers.
"""
import json
import os
import unittest
import urllib.request

from backend.db_manager import PRKSDatabase
from backend.research_network import get_concept
from backend.storage.config import StorageConfig
from tests.e2e import test_offline as o
from tests.e2e.fixtures import (
    CONCEPT_CHILD_NAME,
    CONCEPT_PARENT_NAME,
    seed_concepts_library,
)
from tests.e2e.harness import (
    AppServer,
    PageCollector,
    open_app_page,
    require_chromium,
    wait_for_async,
)


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

    # Definition A→B→A cancel is owned by Node
    # `theDefinitionCoalescesAndCancels` (store) and
    # `theDefinitionCancelGoesThroughApiWrapper` (production updateConcept)
    # in run_concept_sync_selftest.js.

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

    # Parent-set equality (order-insensitive) and A→B→A cancel are owned by
    # Node `theParentSetIsASet` (store) and
    # `theParentSetCancelGoesThroughApiWrapper` (production putConceptParents)
    # in run_concept_sync_selftest.js.

    def test_opening_concept_online_rewarms_invalidated_hierarchy_catalogue(self):
        """A detail visited online must prepare names for later offline reparenting.

        Saving Research Notes or mutating Concepts invalidates concepts:index.
        Opening the Concept detail without visiting the index must refresh
        that list before disconnecting; otherwise an offline SET_CONCEPT_PARENTS
        cannot name the new parent and hides the user's pending change.
        """
        server, page, context = self.start()
        child = server.ids['concept_child']
        unvisited = server.ids['concept_unvisited']
        self.prepare(page, child)
        self.prepare(page, unvisited)

        # Mimic a real domain invalidation (only the catalogue), after both
        # detail caches are ready. No #/concepts navigation may refill it.
        page.evaluate("() => prksOfflineInvalidateList('concepts:index')")
        o._wait_list_uncached(page, 'concepts:index')
        self.detail(page, child)
        o._wait_list_cached(page, 'concepts:index')

        self.offline(page, context)
        page.evaluate("([cid, pid]) => putConceptParents(cid, [pid])", [child, unvisited])
        self.wait_for_family(page, 'SET_CONCEPT_PARENTS')
        self.detail(page, child)
        o._wait_content_contains(page, server.ids['concept_unvisited_name'])
        self.detail(page, unvisited)
        o._wait_content_contains(page, CONCEPT_CHILD_NAME)

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

    def test_a_concept_a_note_still_names_is_refused_and_comes_back(self):
        """The whole deletion contract, in the order a user experiences it.

        A tombstone hides the Concept while the server has not answered. The
        server then refuses, because canonical notes still name it -- and at
        that moment the Concept has to become VISIBLE again. Leaving it hidden
        would show a library that disagrees with both the server and the
        Diagnostics entry explaining why, and the user would have no way to see
        the thing they were told could not be deleted.
        """
        server, page, context = self.start()
        child = server.ids['concept_child']
        self.prepare(page, child)
        self.offline(page, context)

        page.evaluate("id => deleteConcept(id)", child)
        self.wait_for_family(page, 'DELETE_CONCEPT')
        # While undecided it IS hidden -- that is the tombstone half.
        self.index(page)
        page.wait_for_function(
            """needle => {
                const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                const root = ctx && ctx.root;
                return !!root && root.innerText.indexOf(needle) === -1;
            }""",
            arg=CONCEPT_CHILD_NAME, timeout=30000)

        self.reconnect(page, context)
        self.settled(page)
        state = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => (rows.find("
            "  o => o.operation === 'DELETE_CONCEPT') || {}).server_result)")
        self.assertEqual(state['code'], 'CONCEPT_IN_USE')
        self.assertIn(CONCEPT_CHILD_NAME, self.concepts_in_db(server))

        # ... and now the user can see it again, on the index ...
        self.index(page)
        o._wait_content_contains(page, CONCEPT_CHILD_NAME, timeout=30000)
        # ... and on its own page.
        self.detail(page, child)
        o._wait_content_contains(page, CONCEPT_CHILD_NAME, timeout=30000)
        # The refusal is not lost by becoming visible: it is still a decision
        # waiting in Diagnostics, with the reason named.
        conflicts = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => rows.filter("
            "  o => o.status === 'conflict').length)")
        self.assertEqual(conflicts, 1)

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


class ConceptDefinitionTwoTabTests(unittest.TestCase):
    """#568: two tabs of one browser editing one Concept's Definition.

    Both pages share one context, so they share the durable queue, the offline
    cache and the device id, exactly like two tabs of one browser profile.
    """

    def start(self):
        server = AppServer(seed_fn=seed_concepts_library)
        self.addCleanup(server.stop)
        server.start()
        page_a, context, collector = open_app_page(_BROWSER, server.origin,
                                                   service_workers='allow')
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        o._wait_sw_active(page_a)
        page_b = context.new_page()
        collector_b = PageCollector(page_b, server.origin)
        self.addCleanup(lambda: self.assertEqual(collector_b.pageerrors, []))
        page_b.goto(server.origin + '/#/folders', wait_until='domcontentloaded')
        page_b.wait_for_selector('#sidebar')
        self._pages = [page_a, page_b]
        return server, context, page_a, page_b

    def open_concept(self, page, concept_id, needle):
        page.evaluate("id => prksNavigate('#/concepts/' + id)", concept_id)
        o._wait_content_contains(page, needle)

    def open_definition(self, page):
        page.locator('#prks-concept-edit-def:visible').click()
        return self.dialog_field(page)

    def dialog_field(self, page):
        field = page.locator('#prks-modal-confirm .prks-modal-prompt__input')
        field.wait_for(state='visible')
        return field

    def save_dialog(self, page):
        page.locator('#prks-modal-confirm-ok').click()

    def server_json(self, server, path):
        """What the server holds, over HTTP: polling the database file from the
        test process would contend with the server for its locks."""
        with urllib.request.urlopen(server.origin + path, timeout=10) as response:
            return json.loads(response.read().decode('utf-8'))

    def definition_in_db(self, server, concept_id):
        return self.server_json(server, '/api/concepts/' + concept_id)['description']

    def wait_for_definition(self, server, concept_id, expected, timeout=40000):
        """Polls the server from a page: the harness routes every browser
        request through this test process, so a test thread blocked in a
        Python sleep would hold the very save it is waiting for."""
        wait_for_async(
            self._pages[0],
            """([path, want]) => fetch(path, { cache: 'no-store' })
                .then(r => r.json()).then(c => c.description === want)""",
            arg=['/api/concepts/' + concept_id, expected], timeout=timeout,
            message='the server never had %r' % expected)

    def definition_writes(self, page):
        """Every SET_CONCEPT_FIELD this page sends, as sent."""
        sent = []

        def record(request):
            if request.method != 'POST' or not request.url.endswith('/api/sync/operations'):
                return
            body = request.post_data_json or {}
            if body.get('operation') == 'SET_CONCEPT_FIELD':
                sent.append(body)
        page.on('request', record)
        return sent

    def queued_definitions(self, page):
        return page.evaluate("""() => prksSync.store.listOperations().then(rows => rows
            .filter(r => r.operation === 'SET_CONCEPT_FIELD')
            .map(r => ({ value: r.payload.value, base: r.base_revision, status: r.status })))""")

    def revision_in_db(self, server, concept_id):
        state = self.server_json(server, '/api/concepts/%s/sync-state' % concept_id)
        return state['fields']['description']['revision']

    def test_an_older_draft_does_not_overwrite_another_tabs_save(self):
        server, _context, page_a, page_b = self.start()
        concept_id = server.ids['concept_parent']
        original = 'Parent concept definition.'
        opened_on = self.revision_in_db(server, concept_id)
        self.open_concept(page_a, concept_id, original)
        self.open_concept(page_b, concept_id, original)
        sent_b = self.definition_writes(page_b)

        # Both tabs start editing the same revision.
        field_a = self.open_definition(page_a)
        field_b = self.open_definition(page_b)
        self.assertEqual(field_a.input_value(), original)
        self.assertEqual(field_b.input_value(), original)
        field_a.fill('Def A')
        field_b.fill('Def B')

        # Tab A saves first, and lands.
        self.save_dialog(page_a)
        self.wait_for_definition(server, concept_id, 'Def A')
        after_a = self.revision_in_db(server, concept_id)
        self.assertGreater(after_a, opened_on)

        # Tab B saves its older draft: refused here, not sent, draft kept.
        self.save_dialog(page_b)
        page_b.wait_for_function("""() => {
            const hint = document.querySelector('#prks-modal-confirm .prks-modal-prompt__hint');
            return !!hint && hint.textContent.indexOf('changed in another tab') !== -1;
        }""")
        hint = page_b.locator('#prks-modal-confirm .prks-modal-prompt__hint').inner_text()
        self.assertIn('Def A', hint, 'the stale notice shows what PRKS has now')
        self.assertEqual(self.dialog_field(page_b).input_value(), 'Def B',
                         "B's draft is still in the editor")
        self.assertEqual(self.queued_definitions(page_b), [])
        self.assertEqual(sent_b, [], "B's older draft never reaches the server")
        self.assertEqual(self.definition_in_db(server, concept_id), 'Def A')

        # Recovery: B saves again with A's text in view, a deliberate replace,
        # measured against the revision A's save produced.
        self.save_dialog(page_b)
        self.wait_for_definition(server, concept_id, 'Def B')
        self.assertEqual(len(sent_b), 1)
        self.assertEqual(sent_b[0]['base_revision'], after_a)
        o._wait_content_contains(page_b, 'Def B')

        # Tab A was still painting its own text; returning to it catches up.
        page_a.bring_to_front()
        page_a.evaluate("() => window.dispatchEvent(new Event('focus'))")
        o._wait_content_contains(page_a, 'Def B')

    def test_cancelling_after_a_stale_save_keeps_the_other_tabs_text(self):
        server, _context, page_a, page_b = self.start()
        concept_id = server.ids['concept_parent']
        original = 'Parent concept definition.'
        self.open_concept(page_a, concept_id, original)
        self.open_concept(page_b, concept_id, original)
        field_b = self.open_definition(page_b)
        field_b.fill('Def B')

        field_a = self.open_definition(page_a)
        field_a.fill('Def A')
        self.save_dialog(page_a)
        self.wait_for_definition(server, concept_id, 'Def A')

        self.save_dialog(page_b)
        self.dialog_field(page_b)
        page_b.wait_for_function("""() => {
            const hint = document.querySelector('#prks-modal-confirm .prks-modal-prompt__hint');
            return !!hint && hint.textContent.indexOf('changed in another tab') !== -1;
        }""")
        page_b.locator('#prks-modal-confirm-cancel').click()
        # B now shows A's text rather than the original it was painting.
        o._wait_content_contains(page_b, 'Def A')
        self.assertEqual(self.queued_definitions(page_b), [])
        self.assertEqual(self.definition_in_db(server, concept_id), 'Def A')

    def test_a_tab_whose_page_is_stale_edits_from_the_current_text(self):
        server, _context, page_a, page_b = self.start()
        concept_id = server.ids['concept_parent']
        original = 'Parent concept definition.'
        self.open_concept(page_a, concept_id, original)
        self.open_concept(page_b, concept_id, original)

        self.open_definition(page_a).fill('Def A')
        self.save_dialog(page_a)
        self.wait_for_definition(server, concept_id, 'Def A')

        # B still paints the original text, but its editor opens on Def A and
        # its save is measured against the revision Def A has.
        after_a = self.revision_in_db(server, concept_id)
        sent_b = self.definition_writes(page_b)
        field_b = self.open_definition(page_b)
        self.assertEqual(field_b.input_value(), 'Def A')
        field_b.fill('Def A, extended by B')
        self.save_dialog(page_b)
        self.wait_for_definition(server, concept_id, 'Def A, extended by B')
        self.assertEqual(sent_b[0]['base_revision'], after_a)

    def test_a_single_tab_edit_queues_offline_and_lands(self):
        server, context, page_a, _page_b = self.start()
        concept_id = server.ids['concept_child']
        self.open_concept(page_a, concept_id, CONCEPT_CHILD_NAME)
        o._wait_entity_cached(page_a, 'concept', concept_id)
        page_a.evaluate("id => { void Promise.resolve(prksReadConceptState(id)).catch(() => {}); }",
                        concept_id)
        o._wait_entity_cached(page_a, 'concept-state', concept_id)
        opened_on = self.revision_in_db(server, concept_id)

        context.set_offline(True)
        page_a.evaluate('() => prksOfflineNoteRequestFailure()')
        page_a.wait_for_function("() => prksOfflineRuntimeState() !== 'online'")
        self.open_definition(page_a).fill('Edited offline in one tab.')
        self.save_dialog(page_a)
        wait_for_async(page_a, """() => prksSync.store.listOperations().then(rows =>
            rows.some(r => r.operation === 'SET_CONCEPT_FIELD'))""", timeout=60000,
            message='the offline Definition edit was never queued')
        queued = self.queued_definitions(page_a)
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]['base'], opened_on)
        o._wait_content_contains(page_a, 'Edited offline in one tab.')

        # Editing again before it is sent coalesces into the same row.
        self.open_definition(page_a).fill('Edited offline twice.')
        self.save_dialog(page_a)
        wait_for_async(page_a, """() => prksSync.store.listOperations().then(rows =>
            rows.some(r => r.operation === 'SET_CONCEPT_FIELD' &&
                           r.payload.value === 'Edited offline twice.'))""", timeout=60000)
        self.assertEqual(len(self.queued_definitions(page_a)), 1)

        context.set_offline(False)
        page_a.evaluate("() => window.dispatchEvent(new Event('online'))")
        page_a.wait_for_function("() => prksOfflineRuntimeState() === 'online'")
        self.wait_for_definition(server, concept_id, 'Edited offline twice.')


if __name__ == '__main__':
    unittest.main()
