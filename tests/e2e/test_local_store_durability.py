"""Browser-level durability of `prks-local-v1`, the DURABLE local database.

The Node selftest covers the local store's logic against a fake IndexedDB.
What only a real browser can prove is the property the whole local-first plan
rests on: a pending operation written to durable storage survives a page
reload, and survives "Clear offline cache" wiping the disposable database
beside it.

Milestone 2A ships no offline mutation UI, so these tests drive the store
directly. That is deliberate -- they verify the storage boundary, not a
feature.
"""
import os
import unittest

from tests.e2e import test_offline as o
from tests.e2e.fixtures import WORK_A_TITLE, seed_folders_library
from tests.e2e.harness import AppServer, open_app_page, require_chromium


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get('PRKS_E2E') == '1' else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close()
    _PW.stop()


# local-store.js is deliberately not in the app shell yet (nothing consumes
# it), so these tests load it into the page themselves.
_LOAD_LOCAL_STORE = """
    async () => {
        if (typeof window.createPrksLocalStore === 'function') return true;
        const res = await fetch('/js/local-store.js');
        const src = await res.text();
        // eslint-disable-next-line no-eval
        (0, eval)(src);
        return typeof window.createPrksLocalStore === 'function';
    }
"""

_ENQUEUE = """
    async (tagId) => {
        const store = window.createPrksLocalStore();
        const deviceId = await store.getOrCreateDeviceId();
        const op = await store.enqueueOperation({
            operation: 'ADD_WORK_TAG',
            entity_type: 'work',
            entity_id: 'W-DURABILITY',
            payload: { tag_id: tagId },
        }, deviceId);
        return { op_id: op.op_id, device_id: deviceId, status: op.status };
    }
"""

_LIST = """
    async () => {
        const store = window.createPrksLocalStore();
        const rows = await store.listOperations();
        return rows.map((r) => ({
            op_id: r.op_id, tag: r.payload.tag_id, status: r.status, sequence: r.sequence,
        }));
    }
"""


class LocalStoreDurabilityTests(unittest.TestCase):
    def start(self):
        server = AppServer(seed_fn=seed_folders_library)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin, service_workers='allow')
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        o._wait_sw_active(page)
        self.assertTrue(page.evaluate(_LOAD_LOCAL_STORE), 'local-store.js failed to load')
        return server, page, context

    def reload(self, page, server):
        page.goto(server.origin + '/')
        page.wait_for_function("() => typeof window.prksOfflineRuntimeState === 'function'")
        self.assertTrue(page.evaluate(_LOAD_LOCAL_STORE))

    def test_a_pending_operation_survives_a_page_reload(self):
        server, page, context = self.start()
        created = page.evaluate(_ENQUEUE, 'T-RELOAD')
        self.assertEqual(created['status'], 'pending')

        self.reload(page, server)

        rows = page.evaluate(_LIST)
        self.assertEqual([r['op_id'] for r in rows], [created['op_id']])
        self.assertEqual(rows[0]['tag'], 'T-RELOAD')
        # The device identity is durable too.
        self.assertEqual(
            page.evaluate("async () => await window.createPrksLocalStore().getOrCreateDeviceId()"),
            created['device_id'],
        )

    def test_clear_offline_cache_destroys_snapshots_but_never_operations(self):
        """The invariant the two-database split exists to guarantee."""
        server, page, context = self.start()
        # Warm a real cached snapshot so there is something to lose.
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work', server.ids['work_a'])
        page.evaluate("() => prksNavigate('#/folders')")
        o._wait_list_cached(page, 'folders:index')

        created = page.evaluate(_ENQUEUE, 'T-SURVIVES-CLEAR')

        # Drive the real Settings control, not the store API.
        page.locator('button.settings-btn').click()
        page.wait_for_selector('#settings-modal:not(.hidden)')
        # The Offline cache section lives in the Diagnostics category; the
        # modal restores whichever category was last used.
        page.locator('#prks-settings-tab-diagnostics').click()
        page.locator('#prks-offline-cache-clear-btn').wait_for(state='visible')
        page.locator('#prks-offline-cache-clear-btn').click()
        page.locator('#prks-modal-confirm:not(.hidden)').wait_for()
        page.locator('#prks-modal-confirm-ok').click()
        page.wait_for_function(
            """() => {
                const el = document.getElementById('prks-offline-cache-status');
                return !!el && el.textContent.indexOf('cleared') !== -1;
            }""",
            timeout=20000,
        )

        # The disposable cache really is gone ...
        self.assertIsNone(o._cached_entity(page, 'work', server.ids['work_a']))
        self.assertIsNone(o._cached_list(page, 'folders:index'))
        # ... and the durable operation is untouched.
        rows = page.evaluate(_LIST)
        self.assertEqual([r['op_id'] for r in rows], [created['op_id']])
        self.assertEqual(rows[0]['tag'], 'T-SURVIVES-CLEAR')
        self.assertEqual(
            page.evaluate("async () => await window.createPrksLocalStore().getOrCreateDeviceId()"),
            created['device_id'],
        )

    def test_operations_survive_a_clear_followed_by_a_reload(self):
        """Clearing the cache must not have left durable state half-alive in
        memory only -- reopen the database from scratch and look again."""
        server, page, context = self.start()
        first = page.evaluate(_ENQUEUE, 'T-ONE')
        second = page.evaluate(_ENQUEUE, 'T-TWO')
        page.evaluate("async () => { await window.prksOfflineClearCache(); }")
        self.reload(page, server)

        rows = page.evaluate(_LIST)
        self.assertEqual([r['op_id'] for r in rows], [first['op_id'], second['op_id']])
        # Durable user-action order is preserved across the reload.
        self.assertEqual([r['sequence'] for r in rows], [1, 2])

    def test_the_two_databases_are_physically_separate(self):
        server, page, context = self.start()
        page.evaluate(_ENQUEUE, 'T-SEPARATE')
        names = page.evaluate(
            """async () => {
                if (!indexedDB.databases) return null;
                return (await indexedDB.databases()).map((d) => d.name).filter(Boolean).sort();
            }"""
        )
        if names is None:
            self.skipTest('indexedDB.databases() unsupported in this browser')
        self.assertIn('prks-offline-v1', names)
        self.assertIn('prks-local-v1', names)

    def test_a_failed_enqueue_is_reported_rather_than_silently_dropped(self):
        """Durable writes must never degrade the way the cache does."""
        server, page, context = self.start()
        outcome = page.evaluate(
            """async () => {
                const store = window.createPrksLocalStore();
                try {
                    await store.enqueueOperation({
                        operation: 'NOT_A_REAL_OPERATION',
                        entity_type: 'work',
                        entity_id: 'W-1',
                    });
                    return { rejected: false };
                } catch (e) {
                    return { rejected: true, code: e && e.prksLocalStoreCode };
                }
            }"""
        )
        self.assertTrue(outcome['rejected'])
        self.assertEqual(outcome['code'], 'unknown_operation')
        self.assertEqual(page.evaluate(_LIST), [])


if __name__ == '__main__':
    unittest.main()
