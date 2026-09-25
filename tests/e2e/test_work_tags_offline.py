"""Existing Work Tags: durable UI intent, reconnect, replay and conflicts."""
import json
import os
import unittest

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

    # The durable queue is shared with Work open events now, so every
    # assertion here scopes itself to the Work-Tag family.
    TAG_OPS = "['ADD_WORK_TAG', 'REMOVE_WORK_TAG'].includes(r.operation)"
    TAG_LEDGER = "operation_type IN ('ADD_WORK_TAG', 'REMOVE_WORK_TAG')"

    def tag_operations(self, page):
        return page.evaluate(
            "() => prksSync.store.listOperations().then(rows => rows.filter(r => %s))" % self.TAG_OPS)

    def tag_ledger(self, server, columns='*'):
        return self.db_for(server).execute_query(
            'SELECT %s FROM sync_operations WHERE %s' % (columns, self.TAG_LEDGER))

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
                const rows = (await prksSync.store.listOperations())
                    .filter(r => ['ADD_WORK_TAG', 'REMOVE_WORK_TAG'].includes(r.operation));
                if (rows.filter(r => r.status !== 'acknowledged').length === n) return;
                await new Promise(resolve => setTimeout(resolve, 50));
            }
            throw new Error('Sync state did not settle');
        }""", count)

    def clear_offline_cache(self, page):
        """Clear the disposable cache through the real Settings surface,
        leaving Diagnostics open where the durable changes are listed."""
        page.locator('button.settings-btn').click()
        page.locator('#prks-settings-tab-diagnostics').click()
        page.locator('#prks-offline-cache-clear-btn').click()
        page.locator('#prks-modal-confirm-ok').click()
        page.wait_for_function("() => document.getElementById('prks-offline-cache-status').textContent === 'Offline cache cleared.'")

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

    def test_revision_conflict_retains_intent_and_apply_creates_new_operation(self):
        server, page, context = self.start(); self.offline(page, context)
        self.add(page); self.pending(page, 1)
        db = PRKSDatabase(storage=StorageConfig.for_testing(server.storage_root))
        w, t = server.ids['work_a'], server.ids['tag']
        db.add_tag_to_work(w, t); db.remove_tag_from_work(w, t)
        original = self.tag_operations(page)[0]['op_id']
        self.reconnect(page, context)
        page.get_by_role('button', name='Apply my change', exact=True).wait_for()
        page.locator('#work-tags-list .work-tag-chip', has_text='Offline Existing').wait_for()
        self.assertNotIn(t, [r['id'] for r in db.get_work_tags(w)])
        page.get_by_role('button', name='Apply my change', exact=True).click()
        self.pending(page, 0)
        self.assertEqual(self.tag_operations(page), [],
                         'the completed operation is retired locally')
        self.assertIn(t, [r['id'] for r in db.get_work_tags(w)])
        # The ledger proves a NEW operation carried the reapplied intent: the
        # original id kept its recorded conflict and was never reused.
        ledger = {r['op_id']: r['status'] for r in self.tag_ledger(server, 'op_id, status')}
        self.assertEqual(ledger.pop(original), 'REVISION_CONFLICT')
        self.assertEqual(list(ledger.values()), ['ACKNOWLEDGED'])

    def test_cache_clear_keeps_intent_and_syncs_without_base(self):
        server, page, context = self.start(); self.offline(page, context)
        self.add(page); self.pending(page, 1)
        self.clear_offline_cache(page)
        self.pending(page, 1)
        page.reload()
        page.locator('[data-prks-role=offline-unavailable]').wait_for()
        self.pending(page, 1)
        self.reconnect(page, context); self.pending(page, 0)
        db = PRKSDatabase(storage=StorageConfig.for_testing(server.storage_root))
        self.assertIn(server.ids['tag'], [t['id'] for t in db.get_work_tags(server.ids['work_a'])])

    # ---- helpers for the degraded and conflicted paths ----

    def manage_degraded(self, page):
        """Enter Manage tags without requiring the picker to become usable."""
        page.locator('#panel-content button', has_text='Manage tags').click()
        page.locator('#work-tag-search').wait_for()
        page.wait_for_function(
            "() => document.getElementById('work-tag-search').placeholder === 'Tags unavailable'")

    def record_requests(self, page):
        seen = []
        page.on('request', lambda request: seen.append((request.method, request.url)))
        return seen

    def db_for(self, server):
        return PRKSDatabase(storage=StorageConfig.for_testing(server.storage_root))

    def sole_conflict(self, page):
        """The single durable operation, once it has reached a terminal
        semantic result. Waiting on `status` alone would also match the instant
        before the structured result is readable."""
        return page.evaluate("""async () => {
            const deadline = Date.now() + 20000;
            for (;;) {
                const rows = (await prksSync.store.listOperations())
                    .filter(r => ['ADD_WORK_TAG', 'REMOVE_WORK_TAG'].includes(r.operation));
                if (rows.length === 1 && rows[0].status === 'conflict' && rows[0].server_result) return rows[0];
                if (Date.now() > deadline) throw new Error('No conflict settled: ' + JSON.stringify(rows));
                await new Promise(resolve => setTimeout(resolve, 50));
            }
        }""")

    def conflict_text(self, page):
        return page.locator('[data-work-tag-sync]').inner_text()

    # ---- Phase G: what the editor may and may not offer offline ----

    def test_offline_add_requires_the_tag_catalog_and_invents_nothing(self):
        """Without a cached Tag catalog there is no honest way to offer an
        existing Tag. An enabled picker over an empty list would read as "this
        library has no Tags", which is a different and false statement."""
        server, page, context = self.start()
        o._clear_cached_list(page, 'tags:index')
        self.offline(page, context)
        page.reload()
        seen = self.record_requests(page)
        self.manage_degraded(page)
        picker = page.locator('#work-tag-search')
        self.assertTrue(picker.is_disabled())
        self.assertEqual(picker.get_attribute('placeholder'), 'Tags unavailable')
        # Focusing it offers nothing at all -- not an empty library.
        page.evaluate("() => document.getElementById('work-tag-search').dispatchEvent(new Event('focus'))")
        page.wait_for_timeout(200)
        self.assertEqual(page.locator('#work-tag-search-results .result-item').count(), 0)
        self.assertEqual([url for method, url in seen if method != 'GET'], [])
        self.assertEqual(self.tag_operations(page), [])

    def test_offline_remove_does_not_need_the_tag_catalog(self):
        """Removing an assigned Tag needs only what the Work and its
        tag-options already carry: the id, a display snapshot and a base
        revision. Requiring the whole catalog for that would be an artificial
        dependency."""
        server, page, context = self.start()
        o._clear_cached_list(page, 'tags:index')
        self.offline(page, context)
        page.reload()
        self.manage_degraded(page)
        self.remove(page)
        self.pending(page, 1)
        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(self.db_for(server).get_work_tags(server.ids['work_a']), [])

    def test_a_tag_created_offline_is_attachable_immediately(self):
        """The id is minted on this device, so it is a real Tag the moment it
        is written -- and the attachment that follows is ordered behind its
        creation by the generic dependency mechanism."""
        server, page, context = self.start()
        self.offline(page, context)
        seen = self.record_requests(page)
        page.locator('#work-tag-search').fill('Brand New Offline Tag')
        row = page.locator('#work-tag-search-results .result-item--create',
                           has_text='Create tag "Brand New Offline Tag"')
        row.wait_for()
        row.click()
        page.locator('#work-tags-list .work-tag-chip',
                     has_text='Brand New Offline Tag').wait_for(timeout=30000)
        self.assertEqual([url for method, url in seen if method != 'GET'], [],
                         'nothing is sent while there is no server')
        created = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => rows.filter("
            "  r => r.operation === 'CREATE_TAG'))")
        self.assertEqual(len(created), 1, created)
        self.assertRegex(created[0]['entity_id'], r'^T-[0-9A-F]{32}$')
        attached = self.tag_operations(page)
        self.assertEqual(len(attached), 1, attached)
        self.assertEqual(attached[0]['depends_on'], [created[0]['op_id']])

        # Reconnecting creates it once, then attaches it.
        self.reconnect(page, context)
        page.evaluate("""async () => {
            const deadline = Date.now() + 30000;
            while (Date.now() < deadline) {
                const rows = await prksSync.store.listOperations();
                if (!rows.some(o => o.status !== 'conflict')) return;
                await new Promise(r => setTimeout(r, 100));
            }
        }""")
        db = self.db_for(server)
        rows = [t for t in db.get_all_tags() if t['name'] == 'Brand New Offline Tag']
        self.assertEqual(len(rows), 1, 'created once, not twice')
        self.assertEqual(rows[0]['id'], created[0]['entity_id'],
                         'the id the client minted is the id SQLite stores')
        self.assertIn(rows[0]['id'],
                      [t['id'] for t in db.get_work_tags(server.ids['work_a'])])

    # ---- Phase H: one durable-first path, online and offline ----

    def test_online_manage_tags_uses_the_durable_queue(self):
        """Online and offline must travel the same road. If the editor called
        the canonical Work-Tag endpoints when connected, the offline path would
        be a separate, less-exercised implementation and the two would drift."""
        server, page, context = self.start()
        seen = self.record_requests(page)
        self.add(page)
        self.pending(page, 0)
        self.remove(page)
        self.pending(page, 0)
        writes = [(method, url) for method, url in seen if method in ('POST', 'PUT', 'PATCH', 'DELETE')]
        self.assertTrue([1 for _, url in writes if url.endswith('/api/sync/operations')], writes)
        self.assertEqual([(m, u) for m, u in writes if '/tags' in u], [], writes)
        db = self.db_for(server)
        self.assertEqual([t['id'] for t in db.get_work_tags(server.ids['work_a'])], [server.ids['tag']])
        self.assertEqual(
            sorted(r['status'] for r in self.tag_ledger(server, 'status')),
            ['ACKNOWLEDGED', 'ACKNOWLEDGED'])

    # ---- Phase E: the conflict branches the browser run had not covered ----

    def test_revision_conflict_use_server_state(self):
        server, page, context = self.start()
        self.offline(page, context)
        self.add(page)
        self.pending(page, 1)
        db = self.db_for(server)
        w, t = server.ids['work_a'], server.ids['tag']
        db.add_tag_to_work(w, t)
        db.remove_tag_from_work(w, t)
        self.reconnect(page, context)
        page.get_by_role('button', name='Use server state', exact=True).click()
        self.pending(page, 0)
        page.locator('#work-tags-list .work-tag-chip', has_text='Offline Existing').wait_for(state='detached')
        self.assertEqual(self.tag_operations(page), [],
                         'resolving with server state creates no replacement operation')
        self.assertNotIn(t, [r['id'] for r in db.get_work_tags(w)])
        # The conflict itself was ledgered; resolving it mutated nothing more.
        rows = self.tag_ledger(server, 'status')
        self.assertEqual([r['status'] for r in rows], ['REVISION_CONFLICT'])
        options = page.evaluate(
            """id => window.createPrksOfflineStore().getEntity('work-tag-options', id)
                .then(row => row.value)""", w)
        self.assertEqual(options['known_absent'][t], 2,
                         'the cached base advances to the server current_revision')

    def test_tag_merged_conflict_retains_intent_and_never_retargets(self):
        """A merge is a decision about Tag identity that the offline device did
        not participate in. Silently redirecting the pending intent to the
        merge target would apply a Tag the user never chose."""
        server, page, context = self.start()
        self.offline(page, context)
        self.add(page)
        self.pending(page, 1)
        db = self.db_for(server)
        w, source, target = server.ids['work_a'], server.ids['tag'], server.ids['assigned']
        db.merge_tags_into(source, target)
        self.reconnect(page, context)
        operation = self.sole_conflict(page)
        self.assertEqual(operation['server_result']['code'], 'TAG_MERGED')
        self.assertEqual(operation['server_result']['target_tag_id'], target)
        self.assertIn('was merged into', self.conflict_text(page))
        # The local intent is still visible and the server was not touched.
        page.locator('#work-tags-list .work-tag-chip', has_text='Offline Existing').wait_for()
        self.assertEqual([t['id'] for t in db.get_work_tags(w)], [target])

        page.get_by_role('button', name='Discard local change', exact=True).click()
        self.pending(page, 0)
        self.assertEqual(self.tag_operations(page), [])
        self.assertEqual([t['id'] for t in db.get_work_tags(w)], [target],
                         'discarding a merge conflict retargets nothing')
        self.assertNotIn(source, [r['id'] for r in db.execute_query('SELECT id FROM tags')])

    def test_tag_deleted_conflict_never_recreates_the_tag(self):
        server, page, context = self.start()
        self.offline(page, context)
        self.add(page)
        self.pending(page, 1)
        db = self.db_for(server)
        w, t = server.ids['work_a'], server.ids['tag']
        db.delete_tag(t)
        self.reconnect(page, context)
        operation = self.sole_conflict(page)
        self.assertEqual(operation['server_result']['code'], 'TAG_DELETED')
        self.assertIn('was deleted on the server', self.conflict_text(page))
        page.get_by_role('button', name='Discard local change', exact=True).click()
        self.pending(page, 0)
        self.assertEqual(self.tag_operations(page), [])
        self.assertNotIn(t, [r['id'] for r in db.execute_query('SELECT id FROM tags')])
        self.assertEqual([r['id'] for r in db.get_work_tags(w)], [server.ids['assigned']])

    def test_missing_work_is_terminal_and_stays_discoverable(self):
        server, page, context = self.start()
        self.offline(page, context)
        self.add(page)
        self.pending(page, 1)
        db = self.db_for(server)
        db.delete_work_record(server.ids['work_a'])
        self.reconnect(page, context)
        operation = self.sole_conflict(page)
        self.assertEqual(operation['server_result']['code'], 'ENTITY_NOT_FOUND')
        self.assertEqual(operation['attempt_count'], 1)
        # No retry loop: the attempt count is still 1 a while later, and the
        # change is still there for the user to decide about.
        page.wait_for_timeout(2500)
        after = self.tag_operations(page)[0]
        self.assertEqual((after['attempt_count'], after['status']), (1, 'conflict'))
        self.assertEqual(len(self.tag_ledger(server)), 1)

    # ---- Phase F: conflicts survive the loss of the Work they belong to ----

    def test_conflict_is_discardable_from_diagnostics_without_the_work(self):
        """The disposable cache can be cleared while a conflict is pending, so
        the Work editor that normally resolves it may simply not exist. The
        durable change must still be reachable and removable."""
        server, page, context = self.start()
        self.offline(page, context)
        self.add(page)
        self.pending(page, 1)
        db = self.db_for(server)
        w, t = server.ids['work_a'], server.ids['tag']
        db.add_tag_to_work(w, t)
        db.remove_tag_from_work(w, t)
        self.clear_offline_cache(page)
        self.reconnect(page, context)
        self.assertEqual(self.sole_conflict(page)['server_result']['code'], 'REVISION_CONFLICT')

        # Settings is still open on Diagnostics; Refresh is the surface a user
        # reaches for. No navigation back to the Work is possible or needed.
        panel = page.locator('#prks-settings-panel-diagnostics')
        panel.locator('#prks-offline-cache-refresh-btn').click()
        discard = panel.get_by_role('button', name='Discard local change', exact=True)
        discard.wait_for()
        discard.click()
        wait_for_async(page,
            "() => prksSync.store.listOperations().then(rows => rows.every(r => !%s))" % self.TAG_OPS)
        self.assertNotIn(t, [r['id'] for r in db.get_work_tags(w)])
        self.assertEqual([r['status'] for r in self.tag_ledger(server, 'status')], ['REVISION_CONFLICT'])

    # ---- the Tag vocabulary itself -------------------------------------------

    def test_deleting_a_tag_offline_hides_it_and_reaches_the_server(self):
        """A tombstone, not a destruction: nothing acknowledged is discarded,
        so a server that refuses restores the Tag by doing nothing."""
        server, page, context = self.start()
        self.offline(page, context)
        page.evaluate("id => prksDeleteTagDurably(id)", server.ids['tag'])
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === 'DELETE_TAG'))",
            timeout=30000, message='the deletion was never recorded durably')

        # Gone from the picker: offering a Tag that is about to stop existing
        # would only produce an operation the server refuses. The name is free
        # again, so what the picker offers is to CREATE one -- which is the
        # honest reading of a vocabulary this device has just removed it from.
        page.locator('#work-tag-search').fill('Offline Existing')
        page.locator('#work-tag-search-results .result-item--create').wait_for(timeout=15000)
        offered = page.evaluate(
            "() => Array.from(document.querySelectorAll("
            "  '#work-tag-search-results .result-item:not(.result-item--create)'))"
            "  .map(el => el.textContent)")
        self.assertEqual([t for t in offered if 'Offline Existing' in t], [], offered)

        self.reconnect(page, context)
        page.evaluate("""async () => {
            const deadline = Date.now() + 30000;
            while (Date.now() < deadline) {
                const rows = await prksSync.store.listOperations();
                if (!rows.some(o => o.status !== 'conflict')) return;
                await new Promise(r => setTimeout(r, 100));
            }
        }""")
        db = self.db_for(server)
        self.assertEqual(
            [t for t in db.get_all_tags() if t['id'] == server.ids['tag']], [])

    def test_deleting_a_tag_cancels_the_attachment_it_would_undo(self):
        server, page, context = self.start()
        self.offline(page, context)
        self.add(page)
        self.pending(page, 1)
        page.evaluate("id => prksDeleteTagDurably(id)", server.ids['tag'])
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 1)",
            timeout=30000,
            message='attaching a Tag immediately before deleting it is work the delete undoes')
        rows = page.evaluate("() => prksSync.store.listOperations()")
        self.assertEqual(rows[0]['operation'], 'DELETE_TAG')
