"""Local-first Work notes: two independent whole-document aggregates.

Research Notes and Reminders each have one revision. The browser never parses
research markup; the server still does on ACK.

Deterministic cancel / private-fence / compact-conflict shape contracts live in
Node (`run_work_note_sync_selftest.js`) and Python (`test_work_note_sync.py`).
This module keeps the Chromium boundaries: real editor reload/remount and a
thin reconnect conflict park.
"""
import os
import unittest

from backend import work_note_sync
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


class OfflineWorkNotesTests(unittest.TestCase):
    def start(self):
        server = AppServer(seed_fn=seed_library)
        self.addCleanup(server.stop); server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin, service_workers='allow')
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        o._wait_sw_active(page)
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work', server.ids['work_a'])
        page.wait_for_selector('.CodeMirror')
        return server, page, context

    def pending(self, page, operation, count):
        wait_for_async(
            page,
            """([op, n]) => prksSync.store.listOperations().then(rows => {
                const matched = rows.filter(r => r.operation === op);
                return matched.length === n && !matched.some(r => r.status === 'syncing');
            })""",
            arg=[operation, count],
            timeout=25000,
            message='Sync did not settle')

    def conflicts(self, page, operation, count):
        wait_for_async(
            page,
            """([op, n]) => prksSync.store.listOperations().then(rows => {
                const matched = rows.filter(r => r.operation === op);
                return matched.length === n
                    && matched.every(r => r.status === 'conflict' && !!r.server_result);
            })""",
            arg=[operation, count],
            timeout=25000,
            message='No conflict settled')

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

    def set_notes(self, page, text):
        page.evaluate(
            """(text) => {
                const ctx = window.prksGetFocusedTabContext();
                const notes = ctx.getResource('workNotes');
                notes.editor.value(text);
            }""",
            text,
        )
        page.locator('[data-prks-role="editor-status"]', has_text='Drafting').wait_for()
        page.evaluate("""() => {
            const ctx = window.prksGetFocusedTabContext();
            window.prksFlushPendingWorkResearchNotes(ctx);
        }""")

    def editor_text(self, page):
        return page.evaluate("""() => {
            const ctx = window.prksGetFocusedTabContext();
            return ctx.getResource('workNotes').editor.value();
        }""")

    def cached_work(self, page, work_id):
        return page.evaluate(
            "id => window.createPrksOfflineStore().getEntity('work', id).then(row => row && row.value)",
            work_id)

    def test_offline_research_note_survives_reload_and_creates_a_concept_on_ack(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        original = self.db_for(server).get_work(work)['text_content']
        self.offline(page, context)
        self.set_notes(page, 'See [[concept:Culture Industry]].')
        self.pending(page, 'SET_WORK_RESEARCH_NOTE', 1)
        self.assertEqual(self.db_for(server).get_work(work)['text_content'],
                         original)

        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('.CodeMirror')
        self.assertEqual(self.editor_text(page), 'See [[concept:Culture Industry]].')

        self.reconnect(page, context)
        self.pending(page, 'SET_WORK_RESEARCH_NOTE', 0)
        self.assertEqual(self.db_for(server).get_work(work)['text_content'],
                         'See [[concept:Culture Industry]].')
        names = {row['name'] for row in self.db_for(server).execute_query(
            'SELECT name FROM concepts')}
        self.assertIn('Culture Industry', names)
        cached = self.cached_work(page, work)
        self.assertEqual(cached['text_content'], 'See [[concept:Culture Industry]].')

    def test_private_notes_are_independent_of_research_and_do_not_create_concepts(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        selector = '#prks-private-notes-work-' + work
        page.locator(selector).wait_for()
        self.offline(page, context)
        self.set_notes(page, 'Research B')
        page.locator(selector).fill('Remind me: [[concept:Should Not Exist]].')
        page.locator(selector).blur()
        self.pending(page, 'SET_WORK_RESEARCH_NOTE', 1)
        self.pending(page, 'SET_WORK_PRIVATE_NOTE', 1)

        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('.CodeMirror')
        page.locator(selector).wait_for()
        page.wait_for_function(
            """([sel, text]) => (document.querySelector(sel) || {}).value === text""",
            arg=[selector, 'Remind me: [[concept:Should Not Exist]].'],
        )
        self.assertEqual(self.editor_text(page), 'Research B')
        self.assertEqual(page.locator(selector).input_value(),
                         'Remind me: [[concept:Should Not Exist]].')

        self.reconnect(page, context)
        self.pending(page, 'SET_WORK_RESEARCH_NOTE', 0)
        self.pending(page, 'SET_WORK_PRIVATE_NOTE', 0)
        db = self.db_for(server)
        self.assertEqual(db.get_work(work)['text_content'], 'Research B')
        self.assertEqual(db.get_work(work)['private_notes'],
                         'Remind me: [[concept:Should Not Exist]].')
        names = {row['name'] for row in db.execute_query('SELECT name FROM concepts')}
        self.assertNotIn('Should Not Exist', names)

    def test_a_stale_research_revision_is_a_conflict(self):
        """Thin reconnect boundary: a stale base parks as conflict.

        Compact conflict shape (no note body fields) is owned by Node
        `handlerContract` and Python `test_compact_conflict_results_omit_note_bodies`.
        """
        server, page, context = self.start()
        work = server.ids['work_a']
        self.offline(page, context)
        self.set_notes(page, 'This device wrote B')
        self.pending(page, 'SET_WORK_RESEARCH_NOTE', 1)
        work_note_sync.set_research_note(self.db_for(server), work, 'Other device wrote C')
        self.reconnect(page, context)
        self.conflicts(page, 'SET_WORK_RESEARCH_NOTE', 1)
        result = page.evaluate("""() => prksSync.store.listOperations().then(rows => {
            const row = rows.find(r => r.operation === 'SET_WORK_RESEARCH_NOTE');
            return row && row.server_result;
        })""")
        self.assertEqual(result['code'], 'REVISION_CONFLICT')
        self.assertEqual(self.db_for(server).get_work(work)['text_content'],
                         'Other device wrote C')
