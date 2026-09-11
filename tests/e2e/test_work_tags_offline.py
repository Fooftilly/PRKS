"""Existing Work Tags: durable UI intent, reconnect, replay and conflicts."""
import json
import os
import unittest

from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig
from tests.e2e import test_offline as o
from tests.e2e.fixtures import WORK_A_TITLE, seed_library
from tests.e2e.harness import AppServer, open_app_page, require_chromium


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
    ids['tag'] = db.add_tag('Offline Existing')['id']
    ids['assigned'] = db.add_tag('Initially Assigned')['id']
    db.add_tag_to_work(ids['work_a'], ids['assigned'])
    return ids


class OfflineWorkTagTests(unittest.TestCase):
    def start(self):
        server = AppServer(seed_fn=seed)
        self.addCleanup(server.stop); server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin, service_workers='allow')
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        o._wait_sw_active(page)
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work', server.ids['work_a'])
        self.manage(page)
        o._wait_entity_cached(page, 'work-tag-options', server.ids['work_a'])
        o._wait_list_cached(page, 'tags:index')
        return server, page, context

    def manage(self, page):
        page.locator('#panel-content button', has_text='Manage tags').click()
        page.wait_for_function("() => { const i = document.getElementById('work-tag-search'); return i && !i.disabled; }")

    def offline(self, page, context):
        context.set_offline(True)
        page.evaluate('() => prksOfflineNoteRequestFailure()')
        page.wait_for_function("() => prksOfflineRuntimeState() !== 'online'")

    def add(self, page, name='Offline Existing'):
        page.locator('#work-tag-search').fill(name)
        page.locator('#work-tag-search-results .result-item').filter(has_text=name).last.click()
        try:
            page.locator('#work-tags-list .work-tag-chip', has_text=name).wait_for()
        except Exception:
            self.fail(str(page.evaluate("""async () => ({
                rows: await prksSync.store.listOperations(), work: prksGetFocusedTabContext().getEntity('work'),
                state: prksGetFocusedTabContext().getResource('workTagEditor'),
                text: document.getElementById('panel-content').innerText
            })""")))

    def remove(self, page, name='Initially Assigned'):
        chip = page.locator('#work-tags-list .work-tag-chip', has_text=name)
        chip.locator('.work-tag-remove').click()
        chip.wait_for(state='detached')

    def pending(self, page, count):
        page.evaluate("""async n => {
            const deadline = Date.now() + 20000;
            while (Date.now() < deadline) {
                const rows = await prksSync.store.listOperations();
                if (rows.filter(r => r.status !== 'acknowledged').length === n) return;
                await new Promise(resolve => setTimeout(resolve, 50));
            }
            throw new Error('Sync state did not settle');
        }""", count)

    def reconnect(self, page, context):
        context.set_offline(False)
        page.evaluate("() => window.dispatchEvent(new Event('online'))")
        page.wait_for_function("() => prksOfflineRuntimeState() === 'online'")

    def test_offline_add_remove_reload_restart_reconnect(self):
        server, page, context = self.start()
        self.offline(page, context)
        self.add(page); self.remove(page); self.pending(page, 2)
        page.reload()
        page.locator('#work-tags-list .work-tag-chip', has_text='Offline Existing').wait_for()
        page.locator('#work-tags-list .work-tag-chip', has_text='Initially Assigned').wait_for(state='detached')
        self.pending(page, 2)
        # Restart the same isolated library without reseeding or deleting storage.
        server._release_process()
        server._seed_fn = None; server.start()
        self.reconnect(page, context); self.pending(page, 0)
        db = PRKSDatabase(storage=StorageConfig.for_testing(server.storage_root))
        self.assertEqual([t['id'] for t in db.get_work_tags(server.ids['work_a'])], [server.ids['tag']], db.execute_query("SELECT operation_type, result_json FROM sync_operations"))
        self.offline(page, context); page.reload()
        page.locator('#work-tags-list .work-tag-chip', has_text='Offline Existing').wait_for()
        page.locator('#work-tags-list .work-tag-chip', has_text='Initially Assigned').wait_for(state='detached')

    def test_coalescing_across_reload_and_repeated_intent(self):
        server, page, context = self.start(); self.offline(page, context)
        self.add(page); self.pending(page, 1)
        page.reload(); self.manage(page)
        self.remove(page, 'Offline Existing'); self.pending(page, 0)
        self.remove(page); self.pending(page, 1)
        page.reload(); self.manage(page)
        self.add(page, 'Initially Assigned'); self.pending(page, 0)
        self.assertEqual(page.evaluate('() => prksSync.store.listOperations()'), [])

    def test_revision_conflict_retains_intent_and_apply_creates_new_operation(self):
        server, page, context = self.start(); self.offline(page, context)
        self.add(page); self.pending(page, 1)
        db = PRKSDatabase(storage=StorageConfig.for_testing(server.storage_root))
        w, t = server.ids['work_a'], server.ids['tag']
        db.add_tag_to_work(w, t); db.remove_tag_from_work(w, t)
        original = page.evaluate('() => prksSync.store.listOperations().then(r => r[0].op_id)')
        self.reconnect(page, context)
        page.get_by_role('button', name='Apply my change', exact=True).wait_for()
        page.locator('#work-tags-list .work-tag-chip', has_text='Offline Existing').wait_for()
        self.assertNotIn(t, [r['id'] for r in db.get_work_tags(w)])
        page.get_by_role('button', name='Apply my change', exact=True).click()
        self.pending(page, 0)
        rows = page.evaluate('() => prksSync.store.listOperations()')
        self.assertNotEqual(rows[0]['op_id'], original)
        self.assertIn(t, [r['id'] for r in db.get_work_tags(w)])

    def test_lost_response_replays_once(self):
        server, page, context = self.start()
        seen = []
        def lose(route):
            seen.append(route.request.post_data_json['op_id'])
            response = route.fetch()
            if len(seen) == 1:
                route.abort('failed')
            else:
                route.fulfill(response=response)
        page.route('**/api/sync/operations', lose)
        self.add(page); self.pending(page, 0)
        self.assertGreaterEqual(len(seen), 2)
        self.assertEqual(len(set(seen)), 1)
        db = PRKSDatabase(storage=StorageConfig.for_testing(server.storage_root))
        options = db.get_work_tag_options(server.ids['work_a'])
        self.assertEqual(next(t['relation_revision'] for t in options['assigned'] if t['tag_id'] == server.ids['tag']), 1)
        self.assertEqual(len(db.execute_query('SELECT * FROM sync_operations')), 1)

    def test_cache_clear_keeps_intent_and_syncs_without_base(self):
        server, page, context = self.start(); self.offline(page, context)
        self.add(page); self.pending(page, 1)
        page.locator('button.settings-btn').click()
        page.locator('#prks-settings-tab-diagnostics').click()
        page.locator('#prks-offline-cache-clear-btn').click()
        page.locator('#prks-modal-confirm-ok').click()
        page.wait_for_function("() => document.getElementById('prks-offline-cache-status').textContent === 'Offline cache cleared.'")
        self.pending(page, 1)
        page.reload()
        page.locator('[data-prks-role=offline-unavailable]').wait_for()
        self.pending(page, 1)
        self.reconnect(page, context); self.pending(page, 0)
        db = PRKSDatabase(storage=StorageConfig.for_testing(server.storage_root))
        self.assertIn(server.ids['tag'], [t['id'] for t in db.get_work_tags(server.ids['work_a'])])
