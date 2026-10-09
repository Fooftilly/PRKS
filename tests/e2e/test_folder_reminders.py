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


_FOLDER_REMINDER_RECORDS = """
    async (folderId) => {
        const store = window.prksEditorRecovery.runtime().store;
        const out = [];
        for (const row of await store.listByEntity('folder-private-note', folderId)) {
            const body = await store.getBody(row.draftId);
            out.push({ draftId: row.draftId, owner: row.owner, base: row.base, body: body && body.body });
        }
        return out;
    }
"""

_RECOVERY_IDLE = """
    () => {
        const writers = window.prksEditorRecovery.runtime().writers;
        return {
            pending: writers.writers().filter(w => w.state() === 'pending' || w.state() === 'unprotected').length,
            guard: writers.leaveGuardActive(),
        };
    }
"""

_WITHOUT_RECOVERY_STORAGE = """
    (() => {
        const open = IDBFactory.prototype.open;
        IDBFactory.prototype.open = function (name) {
            if (name === 'prks-editor-recovery-v1') throw new DOMException('Recovery storage refused', 'QuotaExceededError');
            return open.apply(this, arguments);
        };
    })();
"""


class FolderRemindersRecoveryTests(_FolderRemindersPage, unittest.TestCase):
    """PR C: Folder Reminders typed inside the 850 ms save debounce survive a
    reload or a closed tab on the shared recovery runtime, measured against
    the `private_notes` Folder field revision; an unsafe draft is a notice in
    the Folder Reminders card, never applied over newer server text."""

    def start(self, without_locks=False):
        server, page, context = super().start(without_locks=without_locks)
        self.dialogs = []
        page.on('dialog', lambda d: (self.dialogs.append(d.type), d.accept()))
        self.addCleanup(lambda: self.assertEqual(self.dialogs, [], 'no leave prompt for an ordinary note'))
        self.folder_id = server.ids['folder_a']
        self.open_folder(page, self.folder_id)
        return server, page, context

    def new_tab(self, context, server):
        tab = context.new_page()
        tab.on('dialog', lambda d: (self.dialogs.append(d.type), d.accept()))
        tab.goto(server.origin + '/#/folders/' + self.folder_id, wait_until='domcontentloaded')
        tab.wait_for_selector('#sidebar')
        self.field(tab, self.folder_id)
        return tab

    def close_tab(self, page):
        # A user closing the tab: beforeunload runs, then the final pagehide.
        page.close(run_before_unload=True)

    def type_reminder(self, page, marker):
        field = page.locator('#prks-private-notes-folder-' + self.folder_id)
        field.click()
        page.keyboard.press('Control+End')
        page.keyboard.type(marker)
        self.status(page, self.folder_id).filter(has_text='Drafting').wait_for()

    def hold_reminder_saves(self, page):
        # The tab closes before its 850 ms save, however slow the runner is.
        page.evaluate('() => { window.prksPrivateNotesArmSave = () => {}; }')

    def records(self, page):
        return page.evaluate(_FOLDER_REMINDER_RECORDS, self.folder_id)

    def wait_recorded(self, page, marker):
        wait_for_async(
            page,
            '([id, marker]) => (' + _FOLDER_REMINDER_RECORDS + ')(id).then(rows => rows.some(r => (r.body || "").endsWith(marker)))',
            arg=[self.folder_id, marker], timeout=5000,
            message='the Folder Reminders edit never reached recovery storage')

    def wait_no_records(self, page):
        wait_for_async(
            page,
            '(id) => (' + _FOLDER_REMINDER_RECORDS + ')(id).then(rows => rows.length === 0)',
            arg=self.folder_id, timeout=15000,
            message='the Folder Reminders recovery record outlived the acknowledgement')

    def note_rows(self, page):
        return page.evaluate("""() => prksSync.store.listOperations().then(rows => rows
            .filter(r => r.operation === 'SET_FOLDER_FIELD' && r.payload && r.payload.field === 'private_notes')
            .map(r => r.payload.value))""")

    def set_server_notes(self, server, text):
        self.db(server).update_folder_metadata(self.folder_id, {'private_notes': text})

    def wait_server_notes(self, page, text):
        wait_for_async(
            page,
            """([id, text]) => fetch('/api/folders/' + encodeURIComponent(id), { cache: 'no-store' })
                .then(r => r.json()).then(f => (f.private_notes || '') === text)""",
            arg=[self.folder_id, text], timeout=30000,
            message='the server never received the Folder Reminders text')

    def notice(self, page):
        return page.locator('.prks-private-notes-card [data-prks-role="editor-recovery-drafts"]')

    def open_review(self, page):
        self.notice(page).locator('[data-prks-role="editor-recovery-open-review"]').click()
        dialog = page.locator('[data-prks-role="editor-recovery-review"]')
        dialog.wait_for()
        dialog.locator('[data-prks-role="editor-recovery-candidate"], [data-prks-role="editor-recovery-empty"]').first.wait_for()
        return dialog

    def close_review(self, page):
        page.locator('[data-prks-role="editor-recovery-cancel"]').click()
        page.locator('[data-prks-role="editor-recovery-review"]').wait_for(state='detached')

    def assert_restored_and_saved(self, server, page, expected):
        page.wait_for_function(
            "([sel, text]) => (document.querySelector(sel) || {}).value === text",
            arg=['#prks-private-notes-folder-' + self.folder_id, expected])
        self.assertEqual(self.notice(page).count(), 0)
        self.wait_server_notes(page, expected)
        self.assertEqual(self.server_notes(server, self.folder_id), expected)
        self.wait_no_records(page)
        self.assertEqual(page.evaluate(_RECOVERY_IDLE), {'pending': 0, 'guard': False})

    def test_reload_within_500_ms_restores_the_exact_text_and_saves_it(self):
        server, page, _ = self.start()
        self.type_reminder(page, ' Reminder 500')
        self.wait_recorded(page, ' Reminder 500')
        # Recoverable is not saved: nothing is queued inside the debounce.
        self.assertEqual(self.note_rows(page), [])
        record = self.records(page)[0]
        self.assertEqual(record['base']['source'], 'server')
        self.assertEqual(record['owner']['paneId'], page.evaluate('() => prksGetFocusedTabContext().tabId'))
        # prks-allow-wait-for-timeout: the contract is "reload within 500 ms of the last keystroke"
        page.wait_for_timeout(150)
        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('#sidebar')
        self.status(page, self.folder_id).filter(has_text='Restored unsaved changes').wait_for()
        self.assertEqual(self.server_notes(server, self.folder_id), FOLDER_A_NOTES)
        self.assert_restored_and_saved(server, page, FOLDER_A_NOTES + ' Reminder 500')

    def test_tab_closed_within_500_ms_is_recovered_in_a_new_tab(self):
        server, page, context = self.start()
        self.type_reminder(page, ' Closed tab')
        # prks-allow-wait-for-timeout: the contract is "close the tab within 500 ms of the last keystroke"
        page.wait_for_timeout(150)
        self.close_tab(page)
        tab = self.new_tab(context, server)
        self.assert_restored_and_saved(server, tab, FOLDER_A_NOTES + ' Closed tab')

    def test_tab_closed_on_the_lan_without_web_locks_is_recovered(self):
        server, page, context = self.start(without_locks=True)
        self.type_reminder(page, ' LAN reminder')
        # prks-allow-wait-for-timeout: the contract is "close the tab within 500 ms of the last keystroke"
        page.wait_for_timeout(400)
        self.close_tab(page)
        tab = self.new_tab(context, server)
        self.assertFalse(tab.evaluate('() => !!navigator.locks'))
        self.assert_restored_and_saved(server, tab, FOLDER_A_NOTES + ' LAN reminder')

    def test_reminders_changed_on_the_server_after_close_need_review_and_are_never_applied(self):
        server, page, context = self.start()
        self.hold_reminder_saves(page)
        self.type_reminder(page, ' Mine')
        self.wait_recorded(page, ' Mine')
        self.close_tab(page)
        self.set_server_notes(server, 'Another device wrote this reminder.')
        tab = self.new_tab(context, server)
        self.notice(tab).wait_for()
        self.assertEqual(self.field(tab, self.folder_id).input_value(), 'Another device wrote this reminder.')
        self.assertEqual(self.note_rows(tab), [])
        dialog = self.open_review(tab)
        self.assertIn('Reminders', dialog.inner_text())
        self.assertEqual(dialog.locator('[data-prks-role="editor-recovery-entity"]').inner_text(), FOLDER_A_TITLE)
        self.assertTrue(dialog.locator('[data-prks-role="editor-recovery-text"]').input_value().endswith(' Mine'))
        self.assertEqual(dialog.locator('[data-prks-role="editor-recovery-restore"]').count(), 0)
        dialog.locator('[data-prks-role="editor-recovery-discard"]').click()
        tab.locator('#prks-modal-confirm-ok').click()
        dialog.locator('[data-prks-role="editor-recovery-empty"]').wait_for()
        self.close_review(tab)
        self.wait_no_records(tab)
        self.assertEqual(self.notice(tab).count(), 0)
        self.assertEqual(self.server_notes(server, self.folder_id), 'Another device wrote this reminder.')
        # The discarded draft does not come back on the next visit.
        tab.reload(wait_until='domcontentloaded')
        tab.wait_for_selector('#sidebar')
        self.field(tab, self.folder_id)
        self.assertEqual(self.records(tab), [])
        self.assertEqual(self.notice(tab).count(), 0)

    def test_a_crashed_lan_tab_reminder_is_offered_for_review_only(self):
        server, page, context = self.start(without_locks=True)
        self.hold_reminder_saves(page)
        self.type_reminder(page, ' Before the crash')
        self.wait_recorded(page, ' Before the crash')
        from playwright.sync_api import Error as PlaywrightError
        with page.expect_event('crash', timeout=15000):
            try:
                page.goto('chrome://crash', timeout=5000)
            except PlaywrightError:
                pass
        tab = self.new_tab(context, server)
        self.notice(tab).wait_for()
        self.assertEqual(self.field(tab, self.folder_id).input_value(), FOLDER_A_NOTES)
        dialog = self.open_review(tab)
        self.assertEqual(dialog.locator('[data-prks-role="editor-recovery-origin"]').inner_text(), 'Another tab that may still be open')
        self.assertEqual(dialog.locator('[data-prks-role="editor-recovery-restore"]').count(), 0)
        self.close_review(tab)
        self.assertEqual(len(self.records(tab)), 1)
        self.assertEqual(self.note_rows(tab), [])
        self.assertEqual(self.server_notes(server, self.folder_id), FOLDER_A_NOTES)

    def test_storage_failure_warns_and_keeps_the_leave_guard_until_saved(self):
        server, page, context = self.start()
        context.add_init_script(_WITHOUT_RECOVERY_STORAGE)
        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('#sidebar')
        self.field(page, self.folder_id)
        page.route('**/api/sync/operations', lambda route: route.abort('connectionrefused'))
        self.type_reminder(page, ' Unprotected')
        warning = page.locator('.prks-private-notes-card [data-prks-role="editor-recovery-unprotected"]')
        warning.wait_for()
        self.assertTrue(page.evaluate('() => window.prksEditorRecovery.runtime().writers.leaveGuardActive()'))
        # Queued is not saved: the guard holds while the row cannot be sent.
        wait_for_async(page, """() => prksSync.store.listOperations().then(rows => rows.some(r =>
                r.operation === 'SET_FOLDER_FIELD' && r.payload && r.payload.field === 'private_notes'))""",
                       timeout=10000, message='the Folder Reminders save never queued')
        self.assertTrue(page.evaluate('() => window.prksEditorRecovery.runtime().writers.leaveGuardActive()'))
        page.unroute('**/api/sync/operations')
        page.evaluate('() => prksSync.wake()')
        self.wait_server_notes(page, FOLDER_A_NOTES + ' Unprotected')
        warning.wait_for(state='detached')
        self.assertFalse(page.evaluate('() => window.prksEditorRecovery.runtime().writers.leaveGuardActive()'))
