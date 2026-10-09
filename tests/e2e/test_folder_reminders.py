"""Folder Reminders (#534): the Folder `private_notes` field in a real browser.

Folder Reminders are one field of the Folder, saved as SET_FOLDER_FIELD against
that field's revision. Each pane keeps its own Reminders session with the
acknowledged base it observed. Deterministic session and contract cases live
in Vitest (`features/folder-detail/private-note-session.test.ts`) and Node
(`run_folder_private_note_save_selftest.js`); this module keeps the Chromium
boundaries. All Folder data is synthetic.
"""
import os
import unittest

from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig
from tests.e2e import test_offline as o
from tests.e2e.fixtures import seed_library
from tests.e2e.harness import AppServer, open_app_page, require_chromium, wait_for_async

FOLDER_A_TITLE = 'Reminders Folder Alpha'
FOLDER_B_TITLE = 'Reminders Folder Beta'
FOLDER_A_NOTES = 'Synthetic reminder for Alpha.'

_WITHOUT_WEB_LOCKS = """
    Object.defineProperty(Navigator.prototype, 'locks', { configurable: true, get() { return undefined; } });
"""


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get('PRKS_E2E') == '1' else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close(); _PW.stop()


def seed(root):
    ids = seed_library(root)
    db = PRKSDatabase(storage=StorageConfig.for_testing(root))
    ids['folder_a'] = db.add_folder(FOLDER_A_TITLE, 'Synthetic folder A.')
    ids['folder_b'] = db.add_folder(FOLDER_B_TITLE, 'Synthetic folder B.')
    db.update_folder_metadata(ids['folder_a'], {'private_notes': FOLDER_A_NOTES})
    return ids


class _FolderRemindersPage:
    def start(self, without_locks=False):
        server = AppServer(seed_fn=seed)
        self.addCleanup(server.stop); server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin, service_workers='allow')
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        if without_locks:
            # The LAN/HTTP deployment is an insecure context: no Web Locks.
            context.add_init_script(_WITHOUT_WEB_LOCKS)
            page.reload(wait_until='domcontentloaded')
            page.wait_for_selector('#sidebar')
            self.assertFalse(page.evaluate('() => !!navigator.locks'))
        o._wait_sw_active(page)
        return server, page, context

    def db(self, server):
        return PRKSDatabase(storage=StorageConfig.for_testing(server.storage_root))

    def open_folder(self, page, folder_id):
        page.evaluate("id => prksNavigate('#/folders/' + encodeURIComponent(id))", folder_id)
        return self.field(page, folder_id)

    def field(self, page, folder_id):
        selector = '#prks-private-notes-folder-' + folder_id
        page.wait_for_selector(selector + '[data-prks-notes-bound="1"]')
        # The pane's observed base is read after the detail paints.
        wait_for_async(
            page,
            """id => { const ctx = prksGetFocusedTabContext();
                return !!(ctx && typeof prksFolderNoteObserved === 'function' && prksFolderNoteObserved(ctx, id)); }""",
            arg=folder_id, timeout=15000, message='Folder Reminders base was not observed')
        return page.locator(selector)

    def status(self, page, folder_id):
        return page.locator('#prks-private-notes-status-folder-' + folder_id)

    def settled(self, page):
        wait_for_async(
            page,
            """() => prksSync.store.listOperations().then(rows => rows.length === 0)""",
            timeout=25000, message='Sync did not settle')

    def server_notes(self, server, folder_id):
        return self.db(server).get_folder(folder_id)['private_notes']


class FolderRemindersSessionTests(_FolderRemindersPage, unittest.TestCase):
    """PR B: per-pane sessions over the Folder field save contract."""

    def test_typed_reminders_save_through_the_field_contract_and_survive_reload(self):
        server, page, _ = self.start()
        folder_id = server.ids['folder_a']
        field = self.open_folder(page, folder_id)
        self.assertEqual(field.input_value(), FOLDER_A_NOTES)
        field.fill('Order synthetic toner \n')
        self.status(page, folder_id).filter(has_text='Saved').wait_for(timeout=10000)
        self.settled(page)
        # The server stores the field trimmed, at the next field revision.
        self.assertEqual(self.server_notes(server, folder_id), 'Order synthetic toner')
        observed = page.evaluate(
            "id => prksFolderNoteObserved(prksGetFocusedTabContext(), id)", folder_id)
        self.assertEqual(observed['value'], 'Order synthetic toner')
        self.assertEqual(observed['source'], 'server')
        self.assertGreaterEqual(observed['revision'], 2)
        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('#sidebar')
        self.assertEqual(self.field(page, folder_id).input_value(), 'Order synthetic toner')

    def test_folder_a_to_b_to_a_inside_the_debounce_keeps_and_saves_the_text(self):
        server, page, _ = self.start()
        a, b = server.ids['folder_a'], server.ids['folder_b']
        field = self.open_folder(page, a)
        field.fill('Typed just before leaving')
        # Leave inside the 850 ms debounce: the leave path saves it.
        field_b = self.open_folder(page, b)
        self.assertEqual(field_b.input_value(), '')
        back = self.open_folder(page, a)
        self.assertEqual(back.input_value(), 'Typed just before leaving')
        self.settled(page)
        self.assertEqual(self.server_notes(server, a), 'Typed just before leaving')
        self.assertEqual(self.server_notes(server, b), '')
