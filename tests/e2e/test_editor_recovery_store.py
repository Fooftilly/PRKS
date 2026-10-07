"""Browser-level behavior of the editor draft recovery store (#466 slice 1).

Vitest covers the store, identity, coalescing and emergency rules against
fakes. What only a real browser proves is kept here: IndexedDB writes and
Web Locks across page teardown, the separate `prks-editor-recovery-v1`
database surviving Clear offline cache, simultaneous duplicated tabs, and the
synchronous emergency entry plus leave guard across a reload.

Slice 1 has no consumer, so these tests drive `prksEditorRecovery` directly.
"""
import os
import unittest

from tests.e2e.fixtures import seed_folders_library
from tests.e2e.harness import AppServer, open_app_page, require_chromium


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get('PRKS_E2E') == '1' else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close()
    _PW.stop()


COPIED_RUNTIME = 'r-' + 'c' * 32
LARGE_CHARS = 256 * 1024 + 4096

_START = """
    async () => {
        const rt = window.prksEditorRecovery.runtime();
        const { claim, merged } = await rt.start();
        return { claim, merged, page: rt.identity.pageInstanceId };
    }
"""

# Opens a writer, reports one generation and returns without waiting for the write.
_EDIT = """
    ([entityId, generation, body]) => {
        const rt = window.prksEditorRecovery.runtime();
        window.__w = window.__w || rt.writers.openWriter({
            kind: 'work-research-note', entityType: 'work', entityId,
            paneId: 'tab-1', base: window.prksEditorRecovery.UNKNOWN_BASE,
        });
        window.__w.edit(generation, typeof body === 'number' ? 'L'.repeat(body) + ':' + generation : body);
        return {
            draftId: window.__w.draftId(),
            state: window.__w.state(),
            guard: rt.writers.leaveGuardActive(),
            held: window.__w.heldByEmergency(),
        };
    }
"""

# Holds every later IndexedDB draft write of this page, as if the page died first.
_HOLD_WRITES = """
    () => {
        const store = window.prksEditorRecovery.runtime().store;
        const write = store.writeGeneration;
        store.writeGeneration = (input) => new Promise(() => {}).then(() => write(input));
    }
"""

_LIST = """
    async (entityId) => {
        const store = window.prksEditorRecovery.runtime().store;
        const rows = await store.listByEntity('work-research-note', entityId);
        const out = [];
        for (const row of rows) {
            const body = await store.getBody(row.draftId);
            out.push({
                draftId: row.draftId, generation: row.generation, status: row.status,
                owner: row.owner, bodyGeneration: body && body.generation,
                bodyLength: body && body.body.length, body: body && body.body.length < 200 ? body.body : null,
                tail: body && body.body.slice(-12),
            });
        }
        return out;
    }
"""

_EMERGENCY_KEYS = """
    () => Object.keys(localStorage).filter((k) => k.startsWith(window.prksEditorRecovery.EMERGENCY_KEY_PREFIX)).sort()
"""


class EditorRecoveryStoreTests(unittest.TestCase):
    def start(self):
        server = AppServer(seed_fn=seed_folders_library)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin)
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        # A leave guard may be armed; reloads in these tests always proceed.
        context.on('page', lambda p: p.on('dialog', lambda d: d.accept()))
        page.on('dialog', lambda d: d.accept())
        page.wait_for_function("() => !!window.prksEditorRecovery")
        return server, page, context

    def open_page(self, context, server):
        page = context.new_page()
        page.goto(server.origin + '/#/folders', wait_until='domcontentloaded')
        page.wait_for_function("() => !!window.prksEditorRecovery")
        return page

    def reload(self, page):
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function("() => !!window.prksEditorRecovery")

    def test_loading_the_script_starts_nothing(self):
        server, page, context = self.start()
        state = page.evaluate(
            """async () => ({
                locks: navigator.locks ? (await navigator.locks.query()).held
                    .filter((l) => String(l.name).startsWith('prks-editor-recovery')).length : 0,
                dbs: indexedDB.databases ? (await indexedDB.databases()).map((d) => d.name) : [],
            })"""
        )
        self.assertEqual(state['locks'], 0)
        self.assertNotIn('prks-editor-recovery-v1', state['dbs'])

    def test_committed_generation_survives_reload_and_clear_offline_cache(self):
        server, page, context = self.start()
        first = page.evaluate(_START)
        self.assertEqual(first['claim']['verified'], 'lock')
        page.evaluate(_EDIT, ['W-REAL', 1, 'first'])
        page.evaluate(_EDIT, ['W-REAL', 2, 'first second'])
        page.evaluate("async () => { await window.__w.flush(); }")
        self.assertEqual(page.evaluate("() => window.prksEditorRecovery.runtime().store.lastDurability()"), 'relaxed')
        page.evaluate("async () => { await window.prksOfflineClearCache(); }")

        self.reload(page)
        again = page.evaluate(_START)
        # Same browser tab: the runtime id survives; the page id never does.
        self.assertEqual(again['claim']['runtimeId'], first['claim']['runtimeId'])
        self.assertNotEqual(again['page'], first['page'])
        rows = page.evaluate(_LIST, 'W-REAL')
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['generation'], rows[0]['bodyGeneration'], rows[0]['body']), (2, 2, 'first second'))
        self.assertEqual(rows[0]['owner']['pageInstanceId'], first['page'])
        record = page.evaluate(
            "async (id) => window.prksEditorRecovery.runtime().store.get(id)", rows[0]['draftId']
        )
        # The previous load's lock is gone, so this tab's own draft is an adoptable orphan.
        self.assertEqual(
            page.evaluate("async (r) => window.prksEditorRecovery.runtime().classify(r)", record),
            'same-runtime-orphan',
        )
        names = page.evaluate("async () => (await indexedDB.databases()).map((d) => d.name)")
        self.assertIn('prks-editor-recovery-v1', names)

    def test_simultaneous_duplicate_runtime_start_keeps_both_drafts(self):
        server, page, context = self.start()
        context.add_init_script(
            "try { sessionStorage.setItem('prks.editorRecovery.runtime.v1', '%s'); } catch (e) {}" % COPIED_RUNTIME
        )
        one = self.open_page(context, server)
        two = self.open_page(context, server)
        # Both start and type before either runtime claim settles.
        for i, p in enumerate((one, two)):
            p.evaluate("() => { window.__start = window.prksEditorRecovery.runtime().start(); }")
            p.evaluate(_HOLD_WRITES)
            p.evaluate(_EDIT, ['W-DUP', 1, 'typed in page %d' % i])
            self.assertEqual(p.evaluate("() => window.prksEditorRecovery.runtime().writers.writeEmergencyNow()"), 'written')
        keys = one.evaluate(_EMERGENCY_KEYS)
        pages = [p.evaluate("() => window.prksEditorRecovery.runtime().identity.pageInstanceId") for p in (one, two)]
        self.assertEqual(keys, sorted('prks.editorRecovery.emergency.v1.' + pid for pid in pages))
        claims = [p.evaluate("async () => (await window.__start).claim.runtimeId") for p in (one, two)]
        self.assertNotEqual(claims[0], claims[1])
        self.assertIn(COPIED_RUNTIME, claims)
        drafts = [p.evaluate("() => window.__w.draftId()") for p in (one, two)]
        self.assertNotEqual(drafts[0], drafts[1])

        one.close()
        two.close()
        # A closed page's locks are released asynchronously; a key whose page
        # still reads as alive is kept for a later start, never merged early.
        page.wait_for_function(
            """(pids) => {
                navigator.locks.query().then((snap) => { window.__held = snap.held.map((l) => l.name); });
                return !!window.__held && pids.every((pid) => !window.__held.includes('prks-editor-recovery-page:' + pid));
            }""",
            arg=pages,
            polling=100,
        )
        page.evaluate(_START)
        rows = page.evaluate(_LIST, 'W-DUP')
        self.assertEqual(sorted(r['draftId'] for r in rows), sorted(drafts))
        self.assertEqual(sorted(r['body'] for r in rows), ['typed in page 0', 'typed in page 1'])
        self.assertEqual(page.evaluate(_EMERGENCY_KEYS), [])

    def test_pending_ordinary_edit_survives_an_immediate_reload(self):
        server, page, context = self.start()
        page.evaluate(_START)
        page.evaluate(_EDIT, ['W-TAIL', 1, 'saved'])
        page.evaluate("async () => { await window.__w.flush(); }")
        page.evaluate(_HOLD_WRITES)
        edit = page.evaluate(_EDIT, ['W-TAIL', 2, 'saved and the newest words'])
        # Ordinary body inside the 300 ms idle window: held by the emergency plan, no guard.
        self.assertEqual((edit['state'], edit['held'], edit['guard']), ('pending', True, False))
        self.reload(page)
        merged = page.evaluate(_START)['merged']
        self.assertEqual([m['outcomes'] for m in merged], [['written']])
        rows = page.evaluate(_LIST, 'W-TAIL')
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['generation'], rows[0]['body'], rows[0]['status']), (2, 'saved and the newest words', 'active'))
        self.assertEqual(page.evaluate(_EMERGENCY_KEYS), [])

    def test_large_body_is_recovered_exactly_or_guarded_before_navigation(self):
        """Correction A at the store level: a body above the emergency budget,
        reloaded and closed within 500 ms of the newest edit. Pass only if the
        exact newest generation was recovered, or the leave guard was active
        before navigation. A missing tail is never accepted silently."""
        for how in ('reload', 'close'):
            with self.subTest(how=how):
                server, page, context = self.start()
                target = page if how == 'reload' else self.open_page(context, server)
                target.evaluate(_START)
                target.evaluate(_EDIT, ['W-LARGE', 1, LARGE_CHARS])
                target.evaluate("async () => { await window.__w.flush(); }")
                edit = target.evaluate(_EDIT, ['W-LARGE', 2, LARGE_CHARS + 10])
                guard_before = edit['guard']
                self.assertFalse(edit['held'])
                # prks-allow-wait-for-timeout: the contract is "leave within 500 ms of the newest edit"
                target.wait_for_timeout(250)
                guard_at_leave = target.evaluate("() => window.prksEditorRecovery.runtime().writers.leaveGuardActive()")
                state_at_leave = target.evaluate("() => window.__w.state()")
                if how == 'reload':
                    self.reload(page)
                else:
                    target.close(run_before_unload=False)
                page.evaluate(_START)
                rows = page.evaluate(_LIST, 'W-LARGE')
                self.assertEqual(len(rows), 1)
                row = rows[0]
                recovered = row['generation'] == 2 and row['bodyLength'] == LARGE_CHARS + 10 + 2 and row['tail'].endswith(':2')
                # Armed synchronously at the edit, before any write or unload.
                self.assertTrue(guard_before)
                if not recovered:
                    self.assertEqual(state_at_leave, 'pending')
                    self.assertTrue(guard_at_leave, 'newest large generation neither recovered nor guarded')
                else:
                    self.assertEqual(row['bodyGeneration'], 2)
