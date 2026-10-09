"""Local-first Work notes: two independent whole-document aggregates.

Research Notes and Reminders each have one revision. The browser never parses
research markup; the server still does on ACK.

Deterministic cancel / private-fence / compact-conflict shape contracts live in
Node (`run_work_note_sync_selftest.js`) and Python (`test_work_note_sync.py`).
This module keeps the Chromium boundaries: real editor reload/remount and a
thin reconnect conflict park, and browser-local recovery of Research Notes
(#466 slices 2-3) and Work Reminders (#474) text across a reload or a closed tab.
"""
import os
import unittest

from backend import work_lifecycle_sync, work_note_sync
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


class _WorkNotesPage:
    def start(self, without_locks=False):
        server = AppServer(seed_fn=seed_library)
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


class OfflineWorkNotesTests(_WorkNotesPage, unittest.TestCase):

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

    def test_text_typed_behind_a_failed_save_is_saved_after_recovery(self):
        """#465: A is attempted and fails, B is refused with scope_busy.

        Once A recovers, B is sent without another keystroke, the status never
        reads "All changes saved" before B is acknowledged, and a reload keeps
        B. The browser stays "online": only the sync POST is refused.
        """
        server, page, context = self.start()
        work = server.ids['work_a']
        page.wait_for_function(
            "() => !!prksGetFocusedTabContext().getResource('workNotesObserved')")
        page.route('**/api/sync/operations', lambda route: route.abort('connectionrefused'))
        self.set_notes(page, 'Body A')
        wait_for_async(
            page,
            """() => prksSync.store.listOperations().then(rows => rows.some(r =>
                r.operation === 'SET_WORK_RESEARCH_NOTE' && r.attempt_count > 0
                && r.status === 'pending'))""",
            timeout=15000,
            message='A was never attempted')

        self.set_notes(page, 'Body A then B')
        page.locator('[data-prks-role="editor-status"]', has_text='Still syncing').wait_for()
        rows = page.evaluate("""() => prksSync.store.listOperations().then(rows => rows
            .filter(r => r.operation === 'SET_WORK_RESEARCH_NOTE').map(r => r.payload.text))""")
        self.assertEqual(rows, ['Body A'], 'B is refused while attempted A holds the note')
        page.evaluate("""() => {
            const el = document.querySelector('[data-prks-role="editor-status"]');
            window.__notesStatusLog = [el.innerText];
            new MutationObserver(() => window.__notesStatusLog.push(el.innerText))
                .observe(el, { childList: true, characterData: true, subtree: true });
        }""")

        page.unroute('**/api/sync/operations')
        page.evaluate('() => prksSync.wake()')
        wait_for_async(
            page,
            """(id) => fetch('/api/works/' + encodeURIComponent(id), { cache: 'no-store' })
                .then(r => r.json()).then(w => w.text_content === 'Body A then B')""",
            arg=work,
            timeout=70000,
            message='B never reached the server')
        page.locator('[data-prks-role="editor-status"]', has_text='All changes saved').wait_for()
        log = page.evaluate('() => window.__notesStatusLog')
        self.assertIn('Saving...', log, 'B was re-sent by the retry: %r' % log)
        last_saving = len(log) - 1 - log[::-1].index('Saving...')
        self.assertNotIn('All changes saved', log[:last_saving],
                         'saved was claimed while B was unsent: %r' % log)
        self.assertEqual(self.db_for(server).get_work(work)['text_content'], 'Body A then B')

        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('.CodeMirror')
        self.assertEqual(self.editor_text(page), 'Body A then B')

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


_WITHOUT_WEB_LOCKS = """
    Object.defineProperty(Navigator.prototype, 'locks', { configurable: true, get() { return undefined; } });
"""

_RECOVERY_RECORDS = """
    async (workId) => {
        const store = window.prksEditorRecovery.runtime().store;
        const out = [];
        for (const row of await store.listByEntity('work-research-note', workId)) {
            const body = await store.getBody(row.draftId);
            out.push({ draftId: row.draftId, generation: row.generation, owner: row.owner,
                       pipeline: row.pipeline, base: row.base, body: body && body.body });
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
            unloadListeners: writers.emergencyListenersActive(),
        };
    }
"""


class _RecoveryPage(_WorkNotesPage):
    def start(self, without_locks=False):
        server, page, context = super().start(without_locks=without_locks)
        self.dialogs = []
        self._dialog_handler = lambda d: (self.dialogs.append(d.type), d.accept())
        page.on('dialog', self._dialog_handler)
        self.addCleanup(lambda: self.assertEqual(self.dialogs, [], 'no leave prompt for an ordinary note'))
        page.wait_for_function(
            "() => !!prksGetFocusedTabContext().getResource('workNotesObserved')")
        return server, page, context

    def type_marker(self, page, marker):
        page.locator('.CodeMirror').click()
        page.keyboard.press('Control+End')
        page.keyboard.type(marker)
        page.locator('[data-prks-role="editor-status"]', has_text='Drafting').wait_for()

    def records(self, page, work):
        return page.evaluate(_RECOVERY_RECORDS, work)

    def wait_recorded(self, page, work, marker):
        wait_for_async(
            page,
            """async ([workId, marker]) => {
                const store = window.prksEditorRecovery.runtime().store;
                for (const row of await store.listByEntity('work-research-note', workId)) {
                    const body = await store.getBody(row.draftId);
                    if (body && body.body.endsWith(marker)) return true;
                }
                return false;
            }""",
            arg=[work, marker],
            timeout=5000,
            message='the edit never reached recovery storage')

    def leave_after(self, page, ms):
        # prks-allow-wait-for-timeout: the contract is "reload within N ms of the last keystroke"
        page.wait_for_timeout(ms)
        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('.CodeMirror')

    def server_text(self, server, work):
        return self.db_for(server).get_work(work)['text_content']

    def wait_server_text(self, page, work, text):
        wait_for_async(
            page,
            """([id, text]) => fetch('/api/works/' + encodeURIComponent(id), { cache: 'no-store' })
                .then(r => r.json()).then(w => w.text_content === text)""",
            arg=[work, text],
            timeout=30000,
            message='the server never received the text')

    def wait_no_records(self, page, work):
        wait_for_async(
            page,
            """(workId) => window.prksEditorRecovery.runtime().store
                .listByEntity('work-research-note', workId).then(rows => rows.length === 0)""",
            arg=work,
            timeout=15000,
            message='the recovery record outlived the acknowledgement')

    def wait_record_count(self, page, work, count):
        wait_for_async(
            page,
            '([workId, n]) => (' + _RECOVERY_RECORDS + ')(workId).then(rows => rows.length === n)',
            arg=[work, count],
            timeout=15000,
            message='the recovery records did not settle')

    def note_rows(self, page):
        return page.evaluate("""() => prksSync.store.listOperations().then(rows => rows
            .filter(r => r.operation === 'SET_WORK_RESEARCH_NOTE').map(r => r.payload.text))""")

    def recovery_notice(self, page):
        return page.evaluate("() => prksGetFocusedTabContext().ui.researchNotesRecovery || null")

    def notice(self, page):
        return page.locator('[data-prks-role="editor-recovery-drafts"]')

    def open_review(self, page):
        self.notice(page).locator('[data-prks-role="editor-recovery-open-review"]').click()
        dialog = page.locator('[data-prks-role="editor-recovery-review"]')
        dialog.wait_for()
        dialog.locator('[data-prks-role="editor-recovery-candidate"], [data-prks-role="editor-recovery-empty"]').first.wait_for()
        return dialog

    def close_review(self, page):
        page.locator('[data-prks-role="editor-recovery-cancel"]').click()
        page.locator('[data-prks-role="editor-recovery-review"]').wait_for(state='detached')

    def assert_restored_and_saved(self, server, page, work, expected):
        page.wait_for_function(
            "text => prksGetFocusedTabContext().getResource('workNotes')?.editor.value() === text",
            arg=expected)
        self.assertIsNone(self.recovery_notice(page))
        self.wait_server_text(page, work, expected)
        self.wait_no_records(page, work)
        page.locator('[data-prks-role="editor-status"]', has_text='All changes saved').wait_for()
        self.assertEqual(page.evaluate(_RECOVERY_IDLE), {'pending': 0, 'guard': False, 'unloadListeners': False})


class ResearchNotesRecoveryTests(_RecoveryPage, unittest.TestCase):
    """#466 slice 2: Research Notes text typed inside the 2 s save debounce
    survives a reload in the same pane, without a prompt, and then saves
    through the ordinary queue. Anything that could overwrite newer text is
    kept for review instead."""

    def test_reload_within_500_ms_restores_the_exact_newest_text_and_saves_it(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        original = self.server_text(server, work)
        self.type_marker(page, ' Recovered 500')
        self.wait_recorded(page, work, ' Recovered 500')
        # Recoverable is not saved: the status still says Drafting, and nothing is queued.
        self.assertEqual(page.locator('[data-prks-role="editor-status"]').inner_text(), 'Drafting...')
        self.assertEqual(self.note_rows(page), [])
        self.leave_after(page, 150)
        page.wait_for_function(
            "text => prksGetFocusedTabContext().getResource('workNotes')?.editor.value() === text",
            arg=original + ' Recovered 500')
        page.locator('[data-prks-role="editor-status"]', has_text='Restored unsaved changes').wait_for()
        self.assertEqual(self.server_text(server, work), original)
        self.assert_restored_and_saved(server, page, work, original + ' Recovered 500')

    def test_reload_within_1200_ms_restores_the_exact_newest_text(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        original = self.server_text(server, work)
        self.type_marker(page, ' Recovered 1200')
        self.leave_after(page, 1200)
        self.assert_restored_and_saved(server, page, work, original + ' Recovered 1200')

    def test_reload_without_web_locks_restores_the_text(self):
        """LAN over HTTP: Web Locks are missing, the runtime claim is the channel."""
        server, page, context = self.start(without_locks=True)
        work = server.ids['work_a']
        original = self.server_text(server, work)
        self.type_marker(page, ' On the LAN')
        self.leave_after(page, 500)
        self.assertEqual(page.evaluate(
            "() => window.prksEditorRecovery.runtime().identity.current().verified"), 'channel')
        self.assert_restored_and_saved(server, page, work, original + ' On the LAN')

    def test_in_app_navigation_still_saves_and_leaves_no_recovery_record(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        original = self.server_text(server, work)
        self.type_marker(page, ' Left in app')
        page.locator('#sidebar a.nav-link[href="#/folders"]').click()
        page.wait_for_function("() => location.hash === '#/folders'")
        self.wait_server_text(page, work, original + ' Left in app')
        self.wait_no_records(page, work)

    def test_text_changed_elsewhere_is_kept_for_review_and_never_applied(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        self.type_marker(page, ' Mine')
        self.wait_recorded(page, work, ' Mine')
        work_note_sync.set_research_note(self.db_for(server), work, 'Another device wrote this.')
        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('.CodeMirror')
        page.wait_for_function(
            "() => !!prksGetFocusedTabContext().ui.researchNotesRecovery")
        notice = self.recovery_notice(page)
        self.assertEqual([c['reason'] for c in notice['candidates']], ['base-advanced'])
        self.assertEqual(self.editor_text(page), 'Another device wrote this.')
        records = self.records(page, work)
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]['body'].endswith(' Mine'))
        self.assertEqual(self.note_rows(page), [])
        self.assertEqual(self.server_text(server, work), 'Another device wrote this.')

    def test_offline_reload_keeps_the_text_until_the_server_can_be_checked(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        original = self.server_text(server, work)
        self.offline(page, context)
        self.type_marker(page, ' Typed offline')
        self.wait_recorded(page, work, ' Typed offline')
        self.leave_after(page, 100)
        page.wait_for_function(
            "() => !!prksGetFocusedTabContext().ui.researchNotesRecovery")
        self.assertEqual([c['reason'] for c in self.recovery_notice(page)['candidates']], ['base-unverified'])
        self.assertEqual(self.editor_text(page), original)
        self.assertEqual(self.note_rows(page), [])
        # Review never claims the cached note was checked, and offers only a comparison.
        dialog = self.open_review(page)
        dialog.locator('[data-prks-role="editor-recovery-current"]', has_text='not checked with the server').wait_for()
        self.assertEqual(dialog.locator('[data-prks-role="editor-recovery-restore"]').count(), 0)
        self.assertEqual(dialog.locator('[data-prks-role="editor-recovery-compare-btn"]').count(), 1)
        self.close_review(page)
        self.assertEqual(len(self.records(page, work)), 1)
        self.reconnect(page, context)
        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('.CodeMirror')
        self.assert_restored_and_saved(server, page, work, original + ' Typed offline')

    def test_blocked_body_survives_reload_and_resumes_on_its_own_predecessor(self):
        """#465/#475: A is attempted and fails, B waits behind it (scope_busy).

        After a reload B is restored as blocked, A is still the only queued
        row, and once A is acknowledged B is sent on top of it."""
        server, page, context = self.start()
        work = server.ids['work_a']
        page.route('**/api/sync/operations', lambda route: route.abort('connectionrefused'))
        self.set_notes(page, 'Body A')
        wait_for_async(
            page,
            """() => prksSync.store.listOperations().then(rows => rows.some(r =>
                r.operation === 'SET_WORK_RESEARCH_NOTE' && r.attempt_count > 0
                && r.status === 'pending'))""",
            timeout=15000,
            message='A was never attempted')
        self.set_notes(page, 'Body A then B')
        page.locator('[data-prks-role="editor-status"]', has_text='Still syncing').wait_for()
        wait_for_async(
            page,
            """(workId) => window.prksEditorRecovery.runtime().store.listByEntity('work-research-note', workId)
                .then(rows => rows.some(r => r.pipeline && r.pipeline.state === 'blocked'))""",
            arg=work,
            timeout=5000,
            message='the blocked state never reached recovery storage')

        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('.CodeMirror')
        page.wait_for_function(
            "() => prksGetFocusedTabContext().getResource('workNotes')?.editor.value() === 'Body A then B'")
        page.locator('[data-prks-role="editor-status"]', has_text='Still syncing').wait_for()
        self.assertIsNone(self.recovery_notice(page))
        rows = page.evaluate("""() => prksSync.store.listOperations().then(rows => rows
            .filter(r => r.operation === 'SET_WORK_RESEARCH_NOTE').map(r => r.payload.text))""")
        self.assertEqual(rows, ['Body A'])
        self.assertNotEqual(self.server_text(server, work), 'Body A then B')

        page.unroute('**/api/sync/operations')
        page.evaluate('() => prksSync.wake()')
        self.wait_server_text(page, work, 'Body A then B')
        self.wait_no_records(page, work)

    def test_two_tabs_on_one_work_keep_separate_recovery_records(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        other = context.new_page()
        other.on('dialog', lambda d: (self.dialogs.append(d.type), d.accept()))
        other.goto(page.url, wait_until='domcontentloaded')
        other.wait_for_selector('.CodeMirror')
        other.wait_for_function(
            "() => !!prksGetFocusedTabContext().getResource('workNotesObserved')")
        self.type_marker(page, ' From tab one')
        self.type_marker(other, ' From tab two')
        self.wait_recorded(page, work, ' From tab one')
        self.wait_recorded(page, work, ' From tab two')
        records = self.records(page, work)
        self.assertEqual(len(records), 2)
        self.assertEqual(len({r['draftId'] for r in records}), 2)
        self.assertEqual(len({r['owner']['pageInstanceId'] for r in records}), 2)
        self.assertEqual(sorted(r['body'][-13:] for r in records), sorted([' From tab one', ' From tab two']))


_WITHOUT_RECOVERY_STORAGE = """
    (() => {
        const open = IDBFactory.prototype.open;
        IDBFactory.prototype.open = function (name) {
            if (name === 'prks-editor-recovery-v1') throw new DOMException('Recovery storage refused', 'QuotaExceededError');
            return open.apply(this, arguments);
        };
    })();
"""


class ResearchNotesTabCloseAndReviewTests(_RecoveryPage, unittest.TestCase):
    """#466 slice 3: text from a browser tab that was closed is recovered in a
    new tab when nothing can be overwritten; everything else is a notice and
    a Review dialog, where nothing changes until the user chooses."""

    def new_tab(self, context, server):
        tab = context.new_page()
        tab.on('dialog', lambda d: (self.dialogs.append(d.type), d.accept()))
        tab.goto(server.origin + '/#/works/' + server.ids['work_a'], wait_until='domcontentloaded')
        tab.wait_for_selector('.CodeMirror')
        tab.wait_for_function(
            "() => !!prksGetFocusedTabContext().getResource('workNotesObserved')")
        return tab

    def hold_saves(self, page):
        # The tab closes before its 2 s semantic save, however slow the
        # runner is: these tests are about the draft, not the save timing.
        page.evaluate('() => { window.prksScheduleWorkResearchNotesSave = () => {}; }')

    def close_tab(self, page):
        # A user closing the tab: beforeunload runs, then the final pagehide.
        page.close(run_before_unload=True)

    def other_tab(self, context, page):
        tab = context.new_page()
        tab.on('dialog', lambda d: (self.dialogs.append(d.type), d.accept()))
        tab.goto(page.url, wait_until='domcontentloaded')
        tab.wait_for_selector('.CodeMirror')
        tab.wait_for_function(
            "() => !!prksGetFocusedTabContext().getResource('workNotesObserved')")
        return tab

    def owners(self, page, work):
        return sorted(r['owner']['pageInstanceId'] for r in self.records(page, work))

    def test_tab_closed_within_500_ms_is_recovered_in_a_new_tab(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        original = self.server_text(server, work)
        self.type_marker(page, ' Closed tab')
        # prks-allow-wait-for-timeout: the contract is "close the tab within 500 ms of the last keystroke"
        page.wait_for_timeout(150)
        self.close_tab(page)
        tab = self.new_tab(context, server)
        tab.locator('[data-prks-role="editor-status"]', has_text='Restored unsaved changes').wait_for()
        self.assertEqual(self.notice(tab).count(), 0)
        self.assert_restored_and_saved(server, tab, work, original + ' Closed tab')

    def test_tab_closed_on_the_lan_without_web_locks_is_recovered(self):
        server, page, context = self.start(without_locks=True)
        work = server.ids['work_a']
        original = self.server_text(server, work)
        self.type_marker(page, ' Closed on the LAN')
        # prks-allow-wait-for-timeout: the contract is "close the tab within 500 ms of the last keystroke"
        page.wait_for_timeout(400)
        self.close_tab(page)
        tab = self.new_tab(context, server)
        self.assertFalse(tab.evaluate('() => !!navigator.locks'))
        self.assert_restored_and_saved(server, tab, work, original + ' Closed on the LAN')

    def test_server_change_after_tab_close_needs_review_and_reconciles_explicitly(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        self.hold_saves(page)
        self.type_marker(page, ' Mine')
        self.wait_recorded(page, work, ' Mine')
        mine = self.records(page, work)[0]['body']
        self.close_tab(page)
        work_note_sync.set_research_note(self.db_for(server), work, 'Another device wrote this.')
        tab = self.new_tab(context, server)
        self.notice(tab).wait_for()
        self.assertIn('Unsaved Research Notes from an earlier session are available.', self.notice(tab).inner_text())
        self.assertEqual(self.editor_text(tab), 'Another device wrote this.')
        self.assertEqual(self.note_rows(tab), [])
        dialog = self.open_review(tab)
        dialog.locator('[data-prks-role="editor-recovery-reason"]', has_text='The note changed after this text was typed').wait_for()
        self.assertEqual(dialog.locator('[data-prks-role="editor-recovery-origin"]').inner_text(), 'A browser tab that was closed')
        self.assertEqual(dialog.locator('[data-prks-role="editor-recovery-restore"]').count(), 0)
        dialog.locator('[data-prks-role="editor-recovery-compare-btn"]').click()
        self.assertEqual(dialog.locator('[data-prks-role="editor-recovery-current-text"]').input_value(), 'Another device wrote this.')
        self.assertEqual(dialog.locator('[data-prks-role="editor-recovery-chosen-text"]').input_value(), mine)
        chosen = 'Another device wrote this. ' + mine
        dialog.locator('[data-prks-role="editor-recovery-chosen-text"]').fill(chosen)
        # Nothing is written before the explicit, confirmed choice.
        self.assertEqual(self.note_rows(tab), [])
        # Escape asks before the combined text is dropped; Keep editing keeps it.
        tab.keyboard.press('Escape')
        tab.locator('#prks-modal-confirm-title', has_text='Discard the combined text?').wait_for()
        tab.locator('#prks-modal-confirm-cancel').click()
        tab.locator('#prks-modal-confirm').wait_for(state='hidden')
        self.assertEqual(dialog.locator('[data-prks-role="editor-recovery-chosen-text"]').input_value(), chosen)
        dialog.locator('[data-prks-role="editor-recovery-replace"]').click()
        tab.locator('#prks-modal-confirm-ok').click()
        tab.locator('[data-prks-role="editor-recovery-review"]').wait_for(state='detached')
        self.wait_server_text(tab, work, chosen)
        self.wait_no_records(tab, work)
        self.assertEqual(self.notice(tab).count(), 0)

    def test_two_closed_tabs_show_both_drafts_and_apply_neither(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        original = self.server_text(server, work)
        second = self.other_tab(context, page)
        self.hold_saves(page)
        self.hold_saves(second)
        self.type_marker(page, ' From tab one')
        self.type_marker(second, ' From tab two')
        self.wait_recorded(page, work, ' From tab one')
        self.wait_recorded(page, work, ' From tab two')
        self.close_tab(page)
        self.close_tab(second)
        tab = self.new_tab(context, server)
        self.notice(tab).wait_for()
        self.assertIn('2 unsaved Research Notes drafts', self.notice(tab).inner_text())
        self.assertEqual(self.editor_text(tab), original)
        self.assertEqual(self.note_rows(tab), [])
        dialog = self.open_review(tab)
        self.assertEqual(dialog.locator('[data-prks-role="editor-recovery-candidate"]').count(), 2)
        # Choosing one restores exactly that one; the other stays for review.
        dialog.locator('[data-prks-role="editor-recovery-candidate"]').filter(has=tab.locator('input')).nth(1).click()
        chosen = dialog.locator('[data-prks-role="editor-recovery-text"]').input_value()
        dialog.locator('[data-prks-role="editor-recovery-restore"]').click()
        dialog.wait_for(state='detached')
        tab.wait_for_function(
            "text => prksGetFocusedTabContext().getResource('workNotes')?.editor.value() === text", arg=chosen)
        self.notice(tab).filter(has_text='Unsaved Research Notes from an earlier session are available.').wait_for()
        self.wait_server_text(tab, work, chosen)
        # The restored draft goes once its save is acknowledged; the other stays.
        self.wait_record_count(tab, work, 1)

    def test_a_tab_already_open_learns_of_a_draft_another_tab_left_on_close(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        original = self.server_text(server, work)
        survivor = self.other_tab(context, page)
        self.hold_saves(page)
        self.type_marker(page, ' Left behind')
        self.wait_recorded(page, work, ' Left behind')
        self.assertEqual(self.notice(survivor).count(), 0)
        self.close_tab(page)
        # No remount: the open pane re-plans when the other tab's close is recorded.
        self.notice(survivor).wait_for()
        self.assertEqual(self.editor_text(survivor), original)
        self.assertEqual(self.note_rows(survivor), [])

    def test_a_live_editor_in_another_tab_is_never_adopted(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        original = self.server_text(server, work)
        self.hold_saves(page)
        self.type_marker(page, ' Still typing')
        self.wait_recorded(page, work, ' Still typing')
        owners = self.owners(page, work)
        tab = self.new_tab(context, server)
        self.assertEqual(self.editor_text(tab), original)
        # Another live editor's text is that editor's: no notice, no adoption.
        self.assertEqual(self.notice(tab).count(), 0)
        self.assertEqual(self.owners(tab, work), owners)
        self.assertTrue(self.editor_text(page).endswith(' Still typing'))

    def test_a_crashed_lan_tab_is_offered_for_review_only(self):
        server, page, context = self.start(without_locks=True)
        work = server.ids['work_a']
        original = self.server_text(server, work)
        self.hold_saves(page)
        self.type_marker(page, ' Before the crash')
        self.wait_recorded(page, work, ' Before the crash')
        owners = self.owners(page, work)
        # No final pagehide and no Web Locks: nothing proves the page is gone.
        from playwright.sync_api import Error as PlaywrightError
        # Wait for the renderer to be gone before opening the next tab, so the
        # new tab never lands in the dying process.
        with page.expect_event('crash', timeout=15000):
            try:
                page.goto('chrome://crash', timeout=5000)
            except PlaywrightError:
                pass
        tab = self.new_tab(context, server)
        self.notice(tab).wait_for()
        self.assertEqual(self.editor_text(tab), original)
        dialog = self.open_review(tab)
        self.assertEqual(dialog.locator('[data-prks-role="editor-recovery-origin"]').inner_text(), 'Another tab that may still be open')
        self.assertEqual(dialog.locator('[data-prks-role="editor-recovery-restore"]').count(), 0)
        self.assertEqual(dialog.locator('[data-prks-role="editor-recovery-compare-btn"]').count(), 0)
        self.assertTrue(dialog.locator('[data-prks-role="editor-recovery-text"]').input_value().endswith(' Before the crash'))
        self.close_review(tab)
        self.assertEqual(self.owners(tab, work), owners)
        self.assertEqual(self.note_rows(tab), [])

    def test_an_ownership_change_while_review_is_open_is_refused(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        original = self.server_text(server, work)
        second = self.other_tab(context, page)
        self.hold_saves(page)
        self.hold_saves(second)
        self.type_marker(page, ' One')
        self.type_marker(second, ' Two')
        self.wait_recorded(page, work, ' One')
        self.wait_recorded(page, work, ' Two')
        self.close_tab(page)
        self.close_tab(second)
        tab = self.new_tab(context, server)
        dialog = self.open_review(tab)
        draft = dialog.locator('[data-prks-role="editor-recovery-candidate"]').first.get_attribute('data-draft-id')
        # Another tab adopts the shown draft while Review is open.
        tab.evaluate(
            """async (draftId) => {
                const store = window.prksEditorRecovery.runtime().store;
                const record = await store.get(draftId);
                await store.adopt(draftId, record.owner.pageInstanceId,
                    { runtimeId: null, pageInstanceId: 'p-another-tab', paneId: 'tab-x', claimedAt: Date.now() });
            }""",
            draft)
        dialog.locator('[data-prks-role="editor-recovery-restore"]').click()
        dialog.locator('[data-prks-role="editor-recovery-message"]', has_text='This draft changed').wait_for()
        self.assertEqual(self.editor_text(tab), original)
        self.assertIn('p-another-tab', self.owners(tab, work))
        self.assertEqual(self.note_rows(tab), [])

    def test_storage_failure_warns_and_keeps_the_leave_guard_until_saved(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        original = self.server_text(server, work)
        context.add_init_script(_WITHOUT_RECOVERY_STORAGE)
        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('.CodeMirror')
        page.wait_for_function(
            "() => !!prksGetFocusedTabContext().getResource('workNotesObserved')")
        page.route('**/api/sync/operations', lambda route: route.abort('connectionrefused'))
        self.type_marker(page, ' Unprotected')
        warning = page.locator('[data-prks-role="editor-recovery-unprotected"]')
        warning.wait_for()
        self.assertIn('Not protected if the browser closes', warning.inner_text())
        self.assertTrue(page.evaluate('() => window.prksEditorRecovery.runtime().writers.leaveGuardActive()'))
        # Leaving now asks first.
        page.remove_listener('dialog', self._dialog_handler)
        with page.expect_event('dialog', timeout=10000) as prompt:
            page.close(run_before_unload=True)
        self.assertEqual(prompt.value.type, 'beforeunload')
        prompt.value.dismiss()
        self.assertFalse(page.is_closed())
        page.unroute('**/api/sync/operations')
        page.evaluate('() => prksSync.wake()')
        self.wait_server_text(page, work, original + ' Unprotected')
        warning.wait_for(state='detached')
        self.assertFalse(page.evaluate('() => window.prksEditorRecovery.runtime().writers.leaveGuardActive()'))

    def test_hiding_the_notice_or_closing_review_keeps_the_draft_and_discard_is_final(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        self.hold_saves(page)
        self.type_marker(page, ' Keep me')
        self.wait_recorded(page, work, ' Keep me')
        self.close_tab(page)
        work_note_sync.set_research_note(self.db_for(server), work, 'Another device wrote this.')
        tab = self.new_tab(context, server)
        self.notice(tab).wait_for()
        self.notice(tab).locator('[data-prks-role="editor-recovery-hide"]').click()
        self.notice(tab).wait_for(state='detached')
        self.assertEqual(len(self.records(tab, work)), 1)
        # Back on the next mount; Escape and Close change nothing.
        tab.reload(wait_until='domcontentloaded')
        tab.wait_for_selector('.CodeMirror')
        self.notice(tab).wait_for()
        self.open_review(tab)
        tab.keyboard.press('Escape')
        tab.locator('[data-prks-role="editor-recovery-review"]').wait_for(state='detached')
        self.assertEqual(len(self.records(tab, work)), 1)
        dialog = self.open_review(tab)
        dialog.locator('[data-prks-role="editor-recovery-discard"]').click()
        tab.locator('#prks-modal-confirm-ok').click()
        dialog.locator('[data-prks-role="editor-recovery-empty"]').wait_for()
        self.close_review(tab)
        self.notice(tab).wait_for(state='detached')
        self.wait_no_records(tab, work)
        tab.reload(wait_until='domcontentloaded')
        tab.wait_for_selector('.CodeMirror')
        tab.wait_for_function(
            "() => !!prksGetFocusedTabContext().getResource('workNotesObserved')")
        self.assertEqual(self.notice(tab).count(), 0)
        self.assertEqual(self.records(tab, work), [])
        self.assertEqual(self.editor_text(tab), 'Another device wrote this.')
        self.assertEqual(self.server_text(server, work), 'Another device wrote this.')

    def test_leaving_the_work_closes_review_without_acting(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        self.hold_saves(page)
        self.type_marker(page, ' Pending review')
        self.wait_recorded(page, work, ' Pending review')
        self.close_tab(page)
        work_note_sync.set_research_note(self.db_for(server), work, 'Another device wrote this.')
        tab = self.new_tab(context, server)
        self.open_review(tab)
        tab.evaluate("() => { location.hash = '#/folders'; }")
        tab.wait_for_function("() => location.hash === '#/folders'")
        tab.locator('[data-prks-role="editor-recovery-review"]').wait_for(state='detached')
        self.assertEqual(len(self.records(tab, work)), 1)
        self.assertEqual(self.note_rows(tab), [])


_REMINDER_RECORDS = _RECOVERY_RECORDS.replace("'work-research-note'", "'work-private-note'")


class WorkRemindersRecoveryTests(_RecoveryPage, unittest.TestCase):
    """#474 slice 4: Work Reminders text typed inside the 850 ms save debounce
    survives a reload or a closed tab on the same recovery runtime as Research
    Notes, and an unsafe draft is a notice in the Reminders card."""

    new_tab = ResearchNotesTabCloseAndReviewTests.new_tab
    close_tab = ResearchNotesTabCloseAndReviewTests.close_tab

    def field(self, page, server):
        return page.locator('#prks-private-notes-work-' + server.ids['work_a'])

    def status(self, page, server):
        return page.locator('#prks-private-notes-status-work-' + server.ids['work_a'])

    def type_reminder(self, page, server, marker):
        field = self.field(page, server)
        field.click()
        page.keyboard.press('Control+End')
        page.keyboard.type(marker)
        self.status(page, server).filter(has_text='Drafting').wait_for()

    def hold_reminder_saves(self, page):
        # The tab closes before its 850 ms save, however slow the runner is.
        page.evaluate('() => { window.prksPrivateNotesArmSave = () => {}; }')

    def reminder_records(self, page, work):
        return page.evaluate(_REMINDER_RECORDS, work)

    def wait_reminder_recorded(self, page, work, marker):
        wait_for_async(
            page,
            '([workId, marker]) => (' + _REMINDER_RECORDS + ')(workId).then(rows => rows.some(r => (r.body || "").endsWith(marker)))',
            arg=[work, marker],
            timeout=5000,
            message='the Reminders edit never reached recovery storage')

    def wait_no_reminder_records(self, page, work):
        wait_for_async(
            page,
            '(workId) => (' + _REMINDER_RECORDS + ')(workId).then(rows => rows.length === 0)',
            arg=work,
            timeout=15000,
            message='the Reminders recovery record outlived the acknowledgement')

    def reminder_rows(self, page):
        return page.evaluate("""() => prksSync.store.listOperations().then(rows => rows
            .filter(r => r.operation === 'SET_WORK_PRIVATE_NOTE').map(r => r.payload.text))""")

    def server_reminder(self, server, work):
        return self.db_for(server).get_work(work)['private_notes'] or ''

    def set_server_reminder(self, server, work, text):
        with self.db_for(server).connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            work_note_sync.set_private_note_on_conn(conn, work, text)

    def wait_server_reminder(self, page, work, text):
        wait_for_async(
            page,
            """([id, text]) => fetch('/api/works/' + encodeURIComponent(id), { cache: 'no-store' })
                .then(r => r.json()).then(w => (w.private_notes || '') === text)""",
            arg=[work, text],
            timeout=30000,
            message='the server never received the Reminders text')

    def reminders_notice(self, page):
        return page.locator('.prks-private-notes-card [data-prks-role="editor-recovery-drafts"]')

    def open_reminders_review(self, page):
        self.reminders_notice(page).locator('[data-prks-role="editor-recovery-open-review"]').click()
        dialog = page.locator('[data-prks-role="editor-recovery-review"]')
        dialog.wait_for()
        dialog.locator('[data-prks-role="editor-recovery-candidate"], [data-prks-role="editor-recovery-empty"]').first.wait_for()
        return dialog

    def assert_reminder_restored_and_saved(self, server, page, work, expected):
        page.wait_for_function(
            "([sel, text]) => (document.querySelector(sel) || {}).value === text",
            arg=['#prks-private-notes-work-' + work, expected])
        self.assertEqual(self.reminders_notice(page).count(), 0)
        self.wait_server_reminder(page, work, expected)
        self.wait_no_reminder_records(page, work)
        self.assertEqual(page.evaluate(_RECOVERY_IDLE), {'pending': 0, 'guard': False, 'unloadListeners': False})

    def test_reminders_reload_within_500_ms_restores_the_exact_text_and_saves_it(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        original = self.server_reminder(server, work)
        self.type_reminder(page, server, ' Reminder 500')
        self.wait_reminder_recorded(page, work, ' Reminder 500')
        # Recoverable is not saved: nothing is queued inside the debounce.
        self.assertEqual(self.reminder_rows(page), [])
        self.leave_after(page, 150)
        self.status(page, server).filter(has_text='Restored unsaved changes').wait_for()
        self.assert_reminder_restored_and_saved(server, page, work, original + ' Reminder 500')

    def test_reminders_tab_closed_within_500_ms_is_recovered_in_a_new_tab(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        original = self.server_reminder(server, work)
        self.type_reminder(page, server, ' Closed reminder')
        # prks-allow-wait-for-timeout: the contract is "close the tab within 500 ms of the last keystroke"
        page.wait_for_timeout(150)
        self.close_tab(page)
        tab = self.new_tab(context, server)
        self.assert_reminder_restored_and_saved(server, tab, work, original + ' Closed reminder')

    def test_reminders_tab_closed_on_the_lan_without_web_locks_is_recovered(self):
        server, page, context = self.start(without_locks=True)
        work = server.ids['work_a']
        original = self.server_reminder(server, work)
        self.type_reminder(page, server, ' LAN reminder')
        # prks-allow-wait-for-timeout: the contract is "close the tab within 500 ms of the last keystroke"
        page.wait_for_timeout(400)
        self.close_tab(page)
        tab = self.new_tab(context, server)
        self.assertFalse(tab.evaluate('() => !!navigator.locks'))
        self.assert_reminder_restored_and_saved(server, tab, work, original + ' LAN reminder')

    def test_reminders_changed_elsewhere_after_close_needs_review_and_is_never_applied(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        self.hold_reminder_saves(page)
        self.type_reminder(page, server, ' Mine')
        self.wait_reminder_recorded(page, work, ' Mine')
        self.close_tab(page)
        self.set_server_reminder(server, work, 'Another device wrote this reminder.')
        tab = self.new_tab(context, server)
        self.reminders_notice(tab).wait_for()
        self.assertEqual(self.field(tab, server).input_value(), 'Another device wrote this reminder.')
        dialog = self.open_reminders_review(tab)
        self.assertIn('Reminders', dialog.inner_text())
        self.assertTrue(dialog.locator('[data-prks-role="editor-recovery-text"]').input_value().endswith(' Mine'))
        self.assertEqual(self.reminder_rows(tab), [])
        dialog.locator('[data-prks-role="editor-recovery-discard"]').click()
        tab.locator('#prks-modal-confirm-ok').click()
        dialog.locator('[data-prks-role="editor-recovery-empty"]').wait_for()
        self.close_review(tab)
        self.wait_no_reminder_records(tab, work)
        self.assertEqual(self.server_reminder(server, work), 'Another device wrote this reminder.')

    def test_a_crashed_lan_tab_reminder_is_offered_for_review_only(self):
        server, page, context = self.start(without_locks=True)
        work = server.ids['work_a']
        original = self.server_reminder(server, work)
        self.hold_reminder_saves(page)
        self.type_reminder(page, server, ' Before the crash')
        self.wait_reminder_recorded(page, work, ' Before the crash')
        from playwright.sync_api import Error as PlaywrightError
        with page.expect_event('crash', timeout=15000):
            try:
                page.goto('chrome://crash', timeout=5000)
            except PlaywrightError:
                pass
        tab = self.new_tab(context, server)
        self.reminders_notice(tab).wait_for()
        self.assertEqual(self.field(tab, server).input_value(), original)
        dialog = self.open_reminders_review(tab)
        self.assertEqual(dialog.locator('[data-prks-role="editor-recovery-origin"]').inner_text(), 'Another tab that may still be open')
        self.assertEqual(dialog.locator('[data-prks-role="editor-recovery-restore"]').count(), 0)
        self.close_review(tab)
        self.assertEqual(len(self.reminder_records(tab, work)), 1)
        self.assertEqual(self.reminder_rows(tab), [])

    def test_reminders_storage_failure_warns_and_keeps_the_leave_guard_until_saved(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        original = self.server_reminder(server, work)
        context.add_init_script(_WITHOUT_RECOVERY_STORAGE)
        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('.CodeMirror')
        self.field(page, server).wait_for()
        page.route('**/api/sync/operations', lambda route: route.abort('connectionrefused'))
        self.type_reminder(page, server, ' Unprotected')
        warning = page.locator('.prks-private-notes-card [data-prks-role="editor-recovery-unprotected"]')
        warning.wait_for()
        self.assertTrue(page.evaluate('() => window.prksEditorRecovery.runtime().writers.leaveGuardActive()'))
        # Queued is not saved: the guard holds while the row cannot be sent.
        wait_for_async(page, "() => prksSync.store.listOperations().then(rows => rows.some(r => r.operation === 'SET_WORK_PRIVATE_NOTE'))",
                       timeout=10000, message='the Reminders save never queued')
        self.assertTrue(page.evaluate('() => window.prksEditorRecovery.runtime().writers.leaveGuardActive()'))
        page.unroute('**/api/sync/operations')
        page.evaluate('() => prksSync.wake()')
        self.wait_server_reminder(page, work, original + ' Unprotected')
        warning.wait_for(state='detached')
        self.assertFalse(page.evaluate('() => window.prksEditorRecovery.runtime().writers.leaveGuardActive()'))


_WORK_RECOVERY_RECORDS = """
    (workId) => window.prksEditorRecovery.runtime().store.listAll()
        .then(rows => rows.filter(r => r.entityId === workId).map(r => r.kind + ':' + r.status))
"""


class WorkDeleteRecoveryCleanupTests(_RecoveryPage, unittest.TestCase):
    """#533: a Work delete keeps its recovery drafts while it is only requested,
    and removes them once the server confirms the Work is gone."""

    field = WorkRemindersRecoveryTests.field
    status = WorkRemindersRecoveryTests.status
    type_reminder = WorkRemindersRecoveryTests.type_reminder
    hold_reminder_saves = WorkRemindersRecoveryTests.hold_reminder_saves
    wait_reminder_recorded = WorkRemindersRecoveryTests.wait_reminder_recorded
    close_tab = ResearchNotesTabCloseAndReviewTests.close_tab

    def work_records(self, page, work):
        return page.evaluate(_WORK_RECOVERY_RECORDS, work)

    def open_delete_confirm(self, page):
        o._open_details_drawer_if_tiled(page)
        advanced = page.locator('.work-details-advanced')
        if advanced.get_attribute('open') is None:
            advanced.locator('summary').click()
        page.locator('.delete-work-btn').click()
        dialog = page.locator('#prks-modal-confirm:not(.hidden)', has_text='Delete file?')
        dialog.wait_for()
        return dialog

    def delete_rows(self, page):
        return page.evaluate("""() => prksSync.store.listOperations().then(rows => rows
            .filter(r => r.operation === 'DELETE_WORK').map(r => ({ status: r.status, attempts: r.attempt_count })))""")

    def test_reminders_typed_before_a_delete_stay_until_the_ack_then_are_removed(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        # Offline: the Reminders row the delete cancels was never sent, so the
        # recovery draft is the only copy of that text until the server confirms.
        self.offline(page, context)
        self.type_reminder(page, server, ' Deleted reminder')
        self.wait_reminder_recorded(page, work, ' Deleted reminder')

        # Cancel deletes nothing.
        dialog = self.open_delete_confirm(page)
        self.assertIn('cannot be undone', dialog.inner_text())
        self.assertIn('Once the deletion is confirmed, unsaved Research Notes and Reminders drafts', dialog.inner_text())
        page.locator('#prks-modal-confirm-cancel').click()
        dialog.wait_for(state='hidden')
        self.assertEqual(self.delete_rows(page), [])
        self.assertEqual(self.work_records(page, work), ['work-private-note:active'])

        self.open_delete_confirm(page)
        page.locator('#prks-modal-confirm-ok').click()
        page.wait_for_function("() => location.hash === '#/folders'", timeout=15000)
        wait_for_async(
            page,
            """() => prksSync.store.listOperations().then(rows =>
                rows.length === 1 && rows[0].operation === 'DELETE_WORK' && rows[0].status === 'pending')""",
            timeout=15000,
            message='the delete request did not replace the unsent Reminders row')
        # Requested, not acknowledged: the draft stays.
        self.assertEqual(self.work_records(page, work), ['work-private-note:active'])
        self.assertIsNotNone(self.db_for(server).get_work(work))

        self.reconnect(page, context)
        wait_for_async(page, "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
                       timeout=30000, message='the delete was never acknowledged')
        self.assertIsNone(self.db_for(server).get_work(work))
        wait_for_async(
            page,
            '(workId) => (' + _WORK_RECOVERY_RECORDS + ')(workId).then(rows => rows.length === 0)',
            arg=work,
            timeout=15000,
            message='recovery drafts outlived the acknowledged delete')
        self.assertEqual(page.evaluate(_RECOVERY_IDLE), {'pending': 0, 'guard': False, 'unloadListeners': False})
        self.assertIsNone(page.evaluate("(workId) => localStorage.getItem('prks.workRecoveryCleanup.v1.' + workId)", work))

    def test_drafts_of_a_never_synced_work_deleted_from_its_own_pane_are_removed(self):
        server, page, context = self.start()
        # The video viewer is loaded on first use; a browser that has used it can open one offline.
        page.evaluate("() => import('/js/components/works-video.js').then(() => true)")
        # Offline, so the creation is never sent and the delete folds it away.
        self.offline(page, context)
        page.locator('#prks-ribbon-new-file').click()
        page.wait_for_selector('#work-modal:not(.hidden):not([inert])')
        page.locator('.prks-kind-toggle__btn[data-kind="video"]').click()
        page.wait_for_selector('#work-video-url-row:not(.hidden)')
        page.locator('#work-video-url').fill('https://www.youtube.com/watch?v=e2e0000533')
        page.locator('#work-title').fill('Never synced')
        page.locator('#save-work-btn').click()
        # The page starts on the seeded Work; wait for the new one.
        page.wait_for_function(
            "(seeded) => location.hash.indexOf('#/works/') === 0 && location.hash.indexOf(seeded) === -1",
            arg=server.ids['work_a'], timeout=20000)
        work = page.evaluate("() => decodeURIComponent(location.hash.slice('#/works/'.length).split(/[/?]/)[0])")
        field = page.locator('#prks-private-notes-work-' + work)
        field.click()
        page.keyboard.press('Control+End')
        page.keyboard.type(' Typed before it ever synced')
        page.locator('#prks-private-notes-status-work-' + work).filter(has_text='Drafting').wait_for()
        self.wait_reminder_recorded(page, work, ' Typed before it ever synced')
        self.assertEqual(self.work_records(page, work), ['work-private-note:active'])
        self.assertEqual(page.evaluate("""() => prksSync.store.listOperations().then(rows => rows
            .filter(r => r.operation === 'CREATE_WORK').map(r => r.attempt_count))"""), [0])

        # Delete File from this Work's own Details. Its navigation away is held, so the
        # cleanup runs while this pane still shows the Work and holds its Reminders session.
        page.evaluate("""() => {
            const navigate = window.prksNavigate;
            window.prksNavigate = function () {
                window.prksNavigate = navigate;
                const self = this, args = arguments;
                window.__prksReleaseNavigation = () => navigate.apply(self, args);
            };
        }""")
        self.open_delete_confirm(page)
        page.locator('#prks-modal-confirm-ok').click()
        page.wait_for_function('() => typeof window.__prksReleaseNavigation === "function"', timeout=15000)
        wait_for_async(
            page,
            '(workId) => (' + _WORK_RECOVERY_RECORDS + ')(workId).then(rows => rows.length === 0)',
            arg=work, timeout=15000, message='drafts of a folded creation outlived its deletion')
        self.assertEqual(field.count(), 1)
        page.evaluate('() => window.__prksReleaseNavigation()')
        page.wait_for_function("() => location.hash === '#/folders'", timeout=15000)
        self.assertEqual(self.work_records(page, work), [])
        self.assertEqual(page.evaluate('() => prksSync.store.listOperations().then(rows => rows.length)'), 0)
        self.assertEqual(page.evaluate(_RECOVERY_IDLE), {'pending': 0, 'guard': False, 'unloadListeners': False})

    def test_an_acknowledged_delete_interrupted_before_cleanup_is_retried_on_a_folders_load(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        # The delete was acknowledged here, but the browser stopped before the cleanup: the Work
        # is still marked, and a tab that closed before its first IndexedDB commit left the draft
        # only in its emergency key.
        page.evaluate("""(workId) => {
            localStorage.setItem('prks.workRecoveryCleanup.v1.' + workId, JSON.stringify({ marked: 1, tried: 0 }));
            const pageInstanceId = 'p-closed-before-commit';
            localStorage.setItem('prks.editorRecovery.emergency.v1.' + pageInstanceId, JSON.stringify({
                v: 1, pageInstanceId, runtimeId: null, at: Date.now(),
                entries: [{
                    draftId: 'd-closed-before-commit', kind: 'work-private-note', entityType: 'work', entityId: workId,
                    generation: 1, committedGeneration: 0, body: 'Only in the emergency key',
                    lineage: { createdAt: Date.now(), owner: { runtimeId: null, pageInstanceId, paneId: 'tab-1' },
                               base: { revision: 0, length: 0, fingerprint: null, source: 'server' } },
                }],
            }));
        }""", work)
        with self.db_for(server).connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            work_lifecycle_sync.delete_work_record_on_conn(conn, work)
        self.close_tab(page)
        tab = context.new_page()
        tab.on('dialog', lambda d: (self.dialogs.append(d.type), d.accept()))
        tab.goto(server.origin + '/#/folders', wait_until='domcontentloaded')
        tab.wait_for_selector('#sidebar')
        wait_for_async(
            tab,
            """(workId) => window.prksEditorRecovery.runtime().store.listAll().then(rows =>
                Object.keys(localStorage).every(k => k.indexOf('prks.editorRecovery.emergency.v1.p-closed-before-commit') !== 0) &&
                rows.every(r => r.entityId !== workId || r.status === 'discarded'))""",
            arg=work,
            timeout=20000,
            message='a draft only an emergency key held outlived its acknowledged delete')
        self.assertIsNone(tab.evaluate("(workId) => localStorage.getItem('prks.workRecoveryCleanup.v1.' + workId)", work))

    def test_drafts_of_a_work_missing_on_the_server_are_kept_on_the_next_load(self):
        server, page, context = self.start()
        work = server.ids['work_a']
        self.hold_reminder_saves(page)
        self.type_reminder(page, server, ' Elsewhere')
        self.wait_reminder_recorded(page, work, ' Elsewhere')
        # The server no longer has the Work (deleted elsewhere, or another library on this
        # origin); this tab closes before its save. This device never saw it deleted.
        with self.db_for(server).connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            work_lifecycle_sync.delete_work_record_on_conn(conn, work)
        self.close_tab(page)
        tab = context.new_page()
        tab.on('dialog', lambda d: (self.dialogs.append(d.type), d.accept()))
        probes = []
        tab.on('request', lambda r: probes.append(r.url) if '/notes-state' in r.url else None)
        tab.goto(server.origin + '/#/folders', wait_until='domcontentloaded')
        tab.wait_for_selector('#sidebar')
        tab.wait_for_function("() => window.prksOfflineRuntimeState && window.prksOfflineRuntimeState() === 'online'", timeout=20000)
        self.assertEqual(tab.evaluate(_WORK_RECOVERY_RECORDS, work), ['work-private-note:active'])
        self.assertEqual(probes, [])
