"""Work-Person roles: durable-first linking, and the credit rule it composes with.

The precedence the whole milestone protects is `linked Author(s) -> effective
author_text -> linked Editor`. These drive it through the real UI rather than
asserting the overlay's output, because the overlay feeding the wrong input to
the right helper is exactly the failure a unit test cannot see.
"""
import os
import unittest

from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig
from tests.e2e import test_offline as o
from tests.e2e.fixtures import (
    ED_DISPLAY,
    JANE_DISPLAY,
    PEOPLE_CREDITED_TITLE,
    PEOPLE_EDITOR_TITLE,
    PEOPLE_WORK_TITLE,
    seed_work_people_library,
)
from tests.e2e.harness import AppServer, open_app_page, require_chromium, wait_for_async


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get('PRKS_E2E') == '1' else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close(); _PW.stop()


class OfflineWorkPeopleTests(unittest.TestCase):
    # Progress lists every Work of a status; Recent lists only opened ones and
    # can legitimately render empty, which is not what these tests are about.
    BROWSE = '#/progress?status=Not%20Started'
    ROLE_OPS = ("['ADD_WORK_PERSON_ROLE','REMOVE_WORK_PERSON_ROLE',"
                "'SET_WORK_PERSON_ROLE_CREDIT'].indexOf(r.operation) !== -1")
    FIELD_OPS = "r.operation === 'SET_WORK_METADATA_FIELD'"

    # ---- harness ------------------------------------------------------------

    def start(self, title=PEOPLE_WORK_TITLE):
        server = AppServer(seed_fn=seed_work_people_library)
        self.addCleanup(server.stop); server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin,
                                                 service_workers='allow')
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        o._wait_sw_active(page)
        # Everything these tests read offline has to have been read once while
        # connected -- that is the whole contract of a read cache. Linking an
        # EXISTING Person needs the People index; reading a card credit needs
        # the browse catalog. Warming them here is what a user does by
        # visiting those pages.
        page.evaluate("() => { void prksNavigate('#/people'); }")
        page.wait_for_selector('[data-person-id], .person-card, #content a[href^="#/people/"]')
        o._wait_list_cached(page, 'people:index')
        # Jane's own page is read offline by the membership assertions, so her
        # detail has to have been read once too.
        o._open_person(page, server.ids['jane'])
        o._wait_entity_cached(page, 'person', server.ids['jane'])
        self.open_work(page, title)
        page.evaluate("r => { void prksNavigate(r); }", self.BROWSE)
        page.wait_for_selector('[data-work-id]')
        o._wait_list_cached(page, 'works-browse:index')
        self.open_work(page, title)
        return server, page, context

    def open_work(self, page, title):
        o._open_work_from_home(page, title)
        o._open_details_drawer_if_tiled(page)
        page.wait_for_selector('.work-detail')

    def metadata_editor(self, page, work_id):
        """Enter the bibliographic editor and wait for its base to be usable.

        The FIELD's own enabled state, not just the Save button's: the button
        reflects whether a base was read, while each field is additionally
        disabled while an operation for it is in flight. Filling a disabled
        input silently changes nothing.
        """
        page.evaluate("() => { void prksSetWorkDetailsMode('metadata'); }")
        page.wait_for_function(
            "() => { const b = document.getElementById('save-work-bib-btn');"
            "        const f = document.querySelector('[data-prks-work-field=\"author_text\"]');"
            "        return !!b && !b.disabled && !!f && !f.disabled; }")

    def manage_people(self, page, work_id):
        """Open the Manage relationships view and wait for its base to be read."""
        # The panel has one mode at a time; switch directly rather than
        # depending on which button the current mode happens to offer.
        #
        # `void`, because `page.evaluate` AWAITS a returned promise and has no
        # timeout of its own -- a mode switch that never settles offline blocks
        # the whole worker silently rather than failing. What follows waits on
        # observable DOM state instead.
        page.evaluate("() => { void prksSetWorkDetailsMode('people'); }")
        page.wait_for_selector('.work-link-person-btn')
        o._wait_entity_cached(page, 'work-people-state', work_id)
        page.wait_for_function(
            "() => { const b = document.querySelector('.work-link-person-btn');"
            "        return !!b && !b.disabled; }")

    def link(self, page, person_display, role='Author', credit=None):
        page.locator('.work-link-person-btn').click()
        page.wait_for_selector('#role-modal:not(.hidden)')
        page.wait_for_function(
            "() => typeof document.getElementById('role-person-search').oninput === 'function'")
        page.locator('#role-person-search').fill(person_display)
        result = page.locator('#role-person-results .result-item--person-pick').first
        result.wait_for(state='visible')
        result.click()
        if role != 'Author':
            page.locator('#role-type-trigger').click()
            page.locator('#role-type-listbox [role="option"][data-value="%s"]' % role).click()
        if credit is not None:
            # The override is opt-in: the checkbox is what says "this file
            # credits them differently", and the input only then applies.
            page.locator('#role-link-credit-enable').check()
            page.locator('#role-link-credit-input').fill(credit)
        page.locator('#save-role-btn').click()

    def unlink(self, page, person_id, role='Author'):
        page.locator('.work-linked-persons__unlink[data-person-id="%s"][data-role-type="%s"]'
                     % (person_id, role)).click()
        page.locator('#prks-modal-confirm-ok').click()

    def pending(self, page, count, pred=None):
        page.evaluate("""async ([n, pred]) => {
            const matches = new Function('r', 'return ' + pred);
            const deadline = Date.now() + 25000;
            for (;;) {
                const rows = (await prksSync.store.listOperations()).filter(matches);
                if (rows.length === n && !rows.some(r => r.status === 'syncing')) return;
                if (Date.now() > deadline) throw new Error('Sync did not settle: ' + JSON.stringify(rows));
                await new Promise(resolve => setTimeout(resolve, 50));
            }
        }""", [count, pred or self.ROLE_OPS])

    def credit_line(self, page, work_id):
        """The Progress card credit, scoped to the visible Main tile.

        The Work detail's `.work-workspace` also carries `data-work-id`, and a
        PDF Work is warm-parked there when we navigate away. A page-wide
        selector matches that survivor before the browse card paints.
        """
        return page.evaluate("""id => {
            const el = document.querySelector(
                '.prks-tile--main .project-card--work-card[data-work-id="' + id + '"] .work-card__meta');
            return el ? el.textContent : '';
        }""", work_id)

    def wait_browse_credit(self, page, work_id, text):
        """Navigate is fire-and-forget; wait until the Progress card shows `text`."""
        page.wait_for_function(
            """([id, text]) => {
                const el = document.querySelector(
                    '.prks-tile--main .project-card--work-card[data-work-id="' + id + '"] .work-card__meta');
                return !!el && el.textContent.indexOf(text) !== -1;
            }""",
            arg=[work_id, text])

    def detail_people(self, page):
        return page.evaluate(
            "() => Array.from(document.querySelectorAll("
            "  '#panel-content .work-linked-persons__chip-link')).map(el => el.textContent.trim())")

    def offline(self, page, context):
        context.set_offline(True)
        # A SYNCHRONOUS signal. Awaiting a probe request inside `page.evaluate`
        # blocks with no timeout when the service worker never settles it --
        # the worker then stalls silently instead of failing.
        page.evaluate('() => prksOfflineNoteRequestFailure()')
        page.wait_for_function("() => prksOfflineRuntimeState() !== 'online'")

    def reconnect(self, page, context):
        context.set_offline(False)
        page.evaluate("() => window.dispatchEvent(new Event('online'))")
        page.wait_for_function("() => prksOfflineRuntimeState() === 'online'")

    def db_for(self, server):
        return PRKSDatabase(storage=StorageConfig.for_testing(server.storage_root))

    def roles(self, server, work_id):
        return {(r['id'], r['role_type']) for r in self.db_for(server).get_work_roles(work_id)}

    # ---- 1: offline add Author ---------------------------------------------

    def test_an_offline_author_link_outranks_author_text_everywhere(self):
        """The widest case. A linked Author outranks `author_text`, so the
        credit has to change on the detail, on every card, and on the Person's
        own page -- before anything is acknowledged."""
        server, page, context = self.start()
        work = server.ids['people_work']
        self.manage_people(page, work)
        self.offline(page, context)

        self.link(page, JANE_DISPLAY)
        self.pending(page, 1)
        self.assertIn(JANE_DISPLAY, self.detail_people(page))

        page.evaluate("r => { void prksNavigate(r); }", self.BROWSE)
        self.wait_browse_credit(page, work, JANE_DISPLAY)
        self.assertNotIn('Text Author', self.credit_line(page, work),
                         'a linked Author outranks author_text at once')

        o._open_person(page, server.ids['jane'])
        o._wait_content_contains(page, PEOPLE_WORK_TITLE)

        # It is durable: a reload while still offline reproduces all of it.
        page.reload()
        page.wait_for_selector('#sidebar')
        o._open_person(page, server.ids['jane'])
        o._wait_content_contains(page, PEOPLE_WORK_TITLE)

        self.assertEqual(self.roles(server, work), set(),
                         'and the server has not been told anything yet')
        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(self.roles(server, work), {(server.ids['jane'], 'Author')})
        page.evaluate("r => { void prksNavigate(r); }", self.BROWSE)
        self.wait_browse_credit(page, work, JANE_DISPLAY)

    # ---- 2: offline remove last Author -------------------------------------

    def test_removing_the_last_author_reveals_author_text(self):
        server, page, context = self.start(PEOPLE_CREDITED_TITLE)
        work = server.ids['credited_work']
        self.manage_people(page, work)
        self.offline(page, context)

        self.unlink(page, server.ids['jane'])
        self.pending(page, 1)
        self.assertEqual(self.detail_people(page), [])

        page.evaluate("r => { void prksNavigate(r); }", self.BROWSE)
        self.wait_browse_credit(page, work, 'Text Author')
        self.assertNotIn(JANE_DISPLAY, self.credit_line(page, work))

        o._open_person(page, server.ids['jane'])
        page.wait_for_function(
            "t => document.body.innerText.indexOf(t) === -1", arg=PEOPLE_CREDITED_TITLE)

        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(self.roles(server, work), set())

    # ---- 3: Editor fallback -------------------------------------------------

    def test_with_no_author_text_the_editor_becomes_the_credit(self):
        """The third branch of the rule. An empty `author_text` must fall
        through to the linked Editor rather than to an empty credit."""
        server, page, context = self.start(PEOPLE_EDITOR_TITLE)
        work = server.ids['editor_work']
        self.manage_people(page, work)
        self.offline(page, context)

        self.unlink(page, server.ids['jane'])
        self.pending(page, 1)
        page.evaluate("r => { void prksNavigate(r); }", self.BROWSE)
        self.wait_browse_credit(page, work, ED_DISPLAY)
        credit = self.credit_line(page, work)
        self.assertIn('Editor', credit, 'and it is labelled as an Editor credit')

        self.reconnect(page, context)
        self.pending(page, 0)
        page.evaluate("r => { void prksNavigate(r); }", self.BROWSE)
        self.wait_browse_credit(page, work, ED_DISPLAY)

    # ---- 4: composition with pending author_text ----------------------------

    def test_a_pending_role_and_a_pending_author_text_compose(self):
        """The highest-value cross-family case: two durable families, one
        displayed credit. Composition has to use the effective state of BOTH,
        and neither overlay may decide the precedence."""
        server, page, context = self.start(PEOPLE_CREDITED_TITLE)
        work = server.ids['credited_work']
        # Both editors' bases are read while connected -- each family refuses to
        # save against a base it could not establish, and this test edits one
        # of each.
        self.metadata_editor(page, work)
        self.manage_people(page, work)
        self.offline(page, context)

        self.unlink(page, server.ids['jane'])
        self.pending(page, 1)

        # One mode switch while offline, not two: each is a full panel
        # re-render, and this test is about composition rather than about the
        # panel's mode machinery.
        self.metadata_editor(page, work)
        page.locator('[data-prks-work-field="author_text"]').fill('New Text Author')
        page.locator('#save-work-bib-btn').click()
        self.pending(page, 1, self.FIELD_OPS)
        operations = page.evaluate(
            "pred => prksSync.store.listOperations().then(r => r"
            "  .filter(new Function('r', 'return ' + pred))"
            "  .map(o => o.payload))", self.FIELD_OPS)
        self.assertEqual(operations, [{'field': 'author_text', 'value': 'New Text Author'}])

        page.evaluate("r => { void prksNavigate(r); }", self.BROWSE)
        self.wait_browse_credit(page, work, 'New Text Author')
        credit = self.credit_line(page, work)
        self.assertNotIn(JANE_DISPLAY, credit, 'the removed Author is gone')

        self.reconnect(page, context)
        self.pending(page, 0)
        self.pending(page, 0, self.FIELD_OPS)
        self.assertEqual(self.roles(server, work), set())
        self.assertEqual(self.db_for(server).get_work(work)['author_text'], 'New Text Author')

    # ---- 5: credit override -------------------------------------------------

    def test_a_credit_override_is_part_of_the_link(self):
        """"The name on this file" travels with the link rather than needing a
        second operation -- otherwise linking with a custom credit would show
        the profile name first and correct itself."""
        server, page, context = self.start()
        work = server.ids['people_work']
        self.manage_people(page, work)
        self.offline(page, context)

        self.link(page, JANE_DISPLAY, credit='Mark Twain')
        self.pending(page, 1)
        self.assertIn('Mark Twain', self.detail_people(page))
        self.assertNotIn(JANE_DISPLAY, self.detail_people(page))
        operations = page.evaluate(
            "pred => prksSync.store.listOperations().then(r => r"
            "  .filter(new Function('r', 'return ' + pred))"
            "  .map(o => [o.operation, o.payload.credit_name]))", self.ROLE_OPS)
        self.assertEqual(operations, [['ADD_WORK_PERSON_ROLE', 'Mark Twain']],
                         'one operation, carrying the credit')

        self.reconnect(page, context)
        self.pending(page, 0)
        roles = self.db_for(server).get_work_roles(work)
        self.assertEqual(roles[0]['credit_name'], 'Mark Twain')
        self.assertIn('Mark Twain', self.db_for(server).get_person(server.ids['jane'])['aliases'],
                      'and the override became a Person alias, as it always has')

    # ---- 6: Research Graph --------------------------------------------------

    def test_a_pending_author_link_draws_its_graph_edge(self):
        """Only the Author role produces an edge, and only where the node data
        to draw it exactly is available. Returns scalars, never live Cytoscape
        objects."""
        # A Work the Graph actually contains: nodes arrive there through note
        # markup and Argument sources, so a file with neither is not in the
        # snapshot and has no edge to draw either way.
        server, page, context = self.start(o.WORK_A_TITLE)
        work = server.ids['work_a']
        # The snapshot is warmed by READING it, not by mounting the graph.
        # Mounting Cytoscape twice in one test -- once to warm and once to
        # assert -- crashed the worker process outright, which the parallel
        # runner sees as a shard that never reports rather than as a failure.
        # The cache is what the assertion needs; the render is not.
        page.evaluate("""async () => {
            await prksOfflineResearchGraphFetch(true);
        }""")
        o._wait_entity_cached(page, 'research-graph-people', 'snapshot')

        self.manage_people(page, work)
        self.offline(page, context)
        self.link(page, JANE_DISPLAY)
        self.pending(page, 1)

        page.evaluate("() => { void prksNavigate('#/graph?focus=person:' + %r); }" % server.ids['jane'])
        page.wait_for_function(
            "() => { const d = prksGetResearchGraphDebug(); return !!(d && d.cy); }")
        edge = 'work_author:person:%s>work:%s' % (server.ids['jane'], work)
        page.wait_for_function(
            "id => { const d = prksGetResearchGraphDebug();"
            "        return !!d && !!d.cy && d.cy.getElementById(id).length > 0; }",
            arg=edge, timeout=20000)

        self.reconnect(page, context)
        self.pending(page, 0)
        # This Work already had an Author; Jane joins it rather than replacing.
        self.assertIn((server.ids['jane'], 'Author'), self.roles(server, work))

    # ---- 7: two consecutive edits ------------------------------------------

    def test_two_consecutive_edits_do_not_conflict_with_each_other(self):
        """The lifecycle defect the source aggregate had. An acknowledgement
        advances the element's revision, and the base the next edit is measured
        against has to move with it."""
        server, page, _context = self.start()
        work = server.ids['people_work']
        self.manage_people(page, work)

        self.link(page, JANE_DISPLAY)
        self.pending(page, 0)
        self.assertEqual(self.roles(server, work), {(server.ids['jane'], 'Author')})

        # ... and immediately again, without reloading or reopening.
        self.unlink(page, server.ids['jane'])
        self.pending(page, 0)
        self.assertEqual(self.roles(server, work), set(),
                         'the second edit lands rather than conflicting')

    # ---- 8: convergence -----------------------------------------------------

    def test_the_same_link_from_a_stale_base_converges(self):
        """Two devices that both linked Jane as Author have agreed, however
        many revisions apart they started. Producing a conflict merely because
        the revisions differ would ask the user to resolve an agreement."""
        server, page, context = self.start()
        work = server.ids['people_work']
        self.manage_people(page, work)
        self.offline(page, context)
        self.link(page, JANE_DISPLAY)
        self.pending(page, 1)

        # Another device makes exactly the same decision while this one is away.
        self.db_for(server).add_role(server.ids['jane'], work, 'Author')

        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(self.roles(server, work), {(server.ids['jane'], 'Author')})
        self.assertEqual(self.detail_people(page), [JANE_DISPLAY])

    # ---- 9: never-sent coalescing ------------------------------------------

    def test_linking_and_unlinking_before_it_sends_leaves_no_intent(self):
        server, page, context = self.start()
        work = server.ids['people_work']
        self.manage_people(page, work)
        self.offline(page, context)

        self.link(page, JANE_DISPLAY)
        self.pending(page, 1)
        self.unlink(page, server.ids['jane'])
        self.pending(page, 0)
        self.assertEqual(self.detail_people(page), [])

        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(self.roles(server, work), set(),
                         'nothing was ever sent, because nothing changed')
