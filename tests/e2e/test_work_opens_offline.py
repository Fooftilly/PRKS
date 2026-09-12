"""Work open events: durable activity, optimistic Recent, max-register convergence."""
import os
import unittest
from datetime import datetime

from backend import work_open_sync
from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig
from tests.e2e import test_offline as o
from tests.e2e.fixtures import WORK_A_TITLE, WORK_B_TITLE, seed_library
from tests.e2e.harness import AppServer, open_app_page, require_chromium


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get('PRKS_E2E') == '1' else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close(); _PW.stop()


class OfflineWorkOpenTests(unittest.TestCase):
    def start(self):
        server = AppServer(seed_fn=seed_library)
        self.addCleanup(server.stop); server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin, service_workers='allow')
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        o._wait_sw_active(page)
        # Open both Works online so each has a cached detail and Recent exists.
        for title, key in ((WORK_A_TITLE, 'work_a'), (WORK_B_TITLE, 'work_b')):
            o._open_work_from_home(page, title)
            o._wait_entity_cached(page, 'work', server.ids[key])
        self.pending(page, 0)
        self.recent(page)
        o._wait_list_cached(page, 'recent:index')
        return server, page, context

    # ---- helpers ------------------------------------------------------------

    def open_work(self, page, work_id, title):
        """Genuine foreground navigation, which works from cache when offline."""
        page.evaluate("() => prksNavigate('#/folders')")
        page.wait_for_function("() => location.hash.indexOf('/works/') === -1")
        page.evaluate("id => prksNavigate('#/works/' + encodeURIComponent(id))", work_id)
        page.wait_for_function("t => document.body.innerText.indexOf(t) !== -1", arg=title)

    def recent(self, page):
        page.evaluate("() => prksNavigate('#/recent')")
        page.wait_for_function("() => location.hash.indexOf('/recent') !== -1")

    def recent_order(self, page):
        self.recent(page)
        page.wait_for_function("""() => {
            const ctx = prksGetFocusedTabContext();
            return !!(ctx && ctx.root && ctx.root.querySelector('[data-work-id]'));
        }""")
        return page.evaluate("""() => {
            const ctx = prksGetFocusedTabContext();
            return Array.from(ctx.root.querySelectorAll('[data-work-id]')).map(el => el.dataset.workId);
        }""")

    def pending(self, page, count):
        page.evaluate("""async n => {
            const deadline = Date.now() + 25000;
            for (;;) {
                const rows = await prksSync.store.listOperations();
                if (rows.length === n) return;
                if (Date.now() > deadline) throw new Error('Sync did not settle: ' + JSON.stringify(rows));
                await new Promise(resolve => setTimeout(resolve, 50));
            }
        }""", count)

    def operations(self, page):
        return page.evaluate("() => prksSync.store.listOperations()")

    def offline(self, page, context):
        context.set_offline(True)
        page.evaluate('() => prksOfflineNoteRequestFailure()')
        page.wait_for_function("() => prksOfflineRuntimeState() !== 'online'")

    def reconnect(self, page, context):
        context.set_offline(False)
        page.evaluate("() => window.dispatchEvent(new Event('online'))")
        page.wait_for_function("() => prksOfflineRuntimeState() === 'online'")

    def record_paths(self, page):
        seen = []
        page.on('request', lambda request: seen.append(request.url))
        return seen

    def db_for(self, server):
        return PRKSDatabase(storage=StorageConfig.for_testing(server.storage_root))

    def server_opened_at(self, server, work_id):
        rows = self.db_for(server).execute_query(
            "SELECT last_opened_at FROM works WHERE id = ?", (work_id,))
        return rows[0]['last_opened_at']

    def canonical(self, iso):
        return work_open_sync.format_moment(datetime.fromisoformat(iso.replace('Z', '+00:00')))

    def enqueue(self, page, work_id, occurred_at, independent=False):
        """A durable open event with an exact timestamp.

        `independent` forces a second event for the same Work by first marking
        the existing one as already sent -- which is precisely when the client
        must stop coalescing and enqueue a new one instead.
        """
        return page.evaluate("""async ([id, at, independent]) => {
            if (independent) {
                const rows = await prksSync.store.listOperations();
                const prior = rows.find(r => r.entity_id === id && r.operation === 'MARK_WORK_OPENED');
                if (prior) {
                    await prksSync.store.claimOperation(prior.op_id);
                    await prksSync.store.updateOperationSyncState(prior.op_id, { status: 'pending' });
                }
            }
            const op = await prksSync.store.recordWorkOpened(id, at, null);
            return op.op_id;
        }""", [work_id, occurred_at, independent])

    # ---- offline activity ---------------------------------------------------

    def test_offline_open_reorders_recent_and_sends_nothing(self):
        server, page, context = self.start()
        work_a, work_b = server.ids['work_a'], server.ids['work_b']
        self.assertEqual(self.recent_order(page), [work_b, work_a])

        self.offline(page, context)
        seen = self.record_paths(page)
        self.open_work(page, work_a, WORK_A_TITLE)
        self.pending(page, 1)
        self.assertEqual(self.recent_order(page), [work_a, work_b],
                         'the optimistic overlay moves the Work the user just opened')
        self.assertEqual([url for url in seen if '/api/sync/operations' in url], [],
                         'nothing is sent while PRKS is unreachable')
        # The pending event is never written into the disposable snapshot.
        cached = o._cached_list(page, 'recent:index')
        self.assertEqual([row['id'] for row in cached['value']], [work_b, work_a])

    def test_offline_open_survives_reload(self):
        """The overlay is rebuilt from prks-local-v1, not from tab memory."""
        server, page, context = self.start()
        work_a, work_b = server.ids['work_a'], server.ids['work_b']
        self.offline(page, context)
        self.open_work(page, work_a, WORK_A_TITLE)
        self.pending(page, 1)
        page.reload()
        page.wait_for_selector('#sidebar')
        self.assertEqual(self.recent_order(page), [work_a, work_b])
        self.pending(page, 1)

    def test_reconnect_records_when_the_user_opened_it_not_when_it_synced(self):
        server, page, context = self.start()
        work_a, work_b = server.ids['work_a'], server.ids['work_b']
        self.offline(page, context)
        self.open_work(page, work_a, WORK_A_TITLE)
        self.pending(page, 1)
        occurred = self.operations(page)[0]['occurred_at']

        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(self.server_opened_at(server, work_a), self.canonical(occurred),
                         'the canonical open time is the event time, not the arrival time; '
                         'and reconnecting is not itself an open')
        # The cached list was reconciled in place rather than dropped.
        cached = o._cached_list(page, 'recent:index')
        self.assertEqual([row['id'] for row in cached['value']], [work_a, work_b])
        self.assertEqual(self.recent_order(page), [work_a, work_b], 'no jump backwards after sync')

    def test_pending_open_survives_a_server_restart(self):
        server, page, context = self.start()
        work_a = server.ids['work_a']
        self.offline(page, context)
        self.open_work(page, work_a, WORK_A_TITLE)
        self.pending(page, 1)
        occurred = self.operations(page)[0]['occurred_at']
        # Restart the same isolated library without reseeding or deleting it.
        server._release_process()
        server._seed_fn = None; server.start()
        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(self.server_opened_at(server, work_a), self.canonical(occurred))

    def test_lost_response_applies_the_event_once(self):
        server, page, context = self.start()
        work_a = server.ids['work_a']
        seen = []

        def lose(route):
            seen.append(route.request.post_data_json['op_id'])
            response = route.fetch()
            if len(seen) == 1:
                route.abort('failed')
            else:
                route.fulfill(response=response)

        before = len(self.db_for(server).execute_query(
            "SELECT op_id FROM sync_operations WHERE operation_type = 'MARK_WORK_OPENED'"))
        page.route('**/api/sync/operations', lose)
        self.open_work(page, work_a, WORK_A_TITLE)
        self.pending(page, 0)
        self.assertGreaterEqual(len(seen), 2)
        self.assertEqual(len(set(seen)), 1, 'the retry replays the same operation id')
        ledger = self.db_for(server).execute_query(
            "SELECT op_id FROM sync_operations WHERE operation_type = 'MARK_WORK_OPENED'")
        self.assertEqual(len(ledger) - before, 1, 'the server ledgered it exactly once')
        self.assertIn(seen[0], [row['op_id'] for row in ledger])

    # ---- convergence --------------------------------------------------------

    def test_an_older_event_can_never_overwrite_a_newer_one(self):
        """The max-register, in both arrival orders. An event that spent a week
        in a pocket must not drag the Work backwards when it finally lands."""
        server, page, context = self.start()
        work_a, work_b = server.ids['work_a'], server.ids['work_b']
        older, newer = '2025-03-04T12:00:00.000Z', '2025-03-04T13:00:00.000Z'
        db = self.db_for(server)
        # Clear the opens `start()` recorded so these two fixed instants are
        # the only candidates; the max-register would otherwise (correctly)
        # keep today's real open over either of them.
        db.execute_query("UPDATE works SET last_opened_at = NULL")
        self.offline(page, context)
        # Work A receives the newer event first, then the older one.
        reversed_order = [self.enqueue(page, work_a, newer),
                          self.enqueue(page, work_a, older, independent=True)]
        # Work B receives them the natural way round.
        natural_order = [self.enqueue(page, work_b, older),
                         self.enqueue(page, work_b, newer, independent=True)]
        self.pending(page, 4)

        self.reconnect(page, context)
        self.pending(page, 0)
        for work in (work_a, work_b):
            self.assertEqual(self.server_opened_at(server, work), self.canonical(newer),
                             'arrival order must not decide the canonical open time')
        # Every event is ledgered either way; how many of them actually moved
        # the canonical value differs, and the converged result does not.
        def changes(op_ids):
            rows = db.execute_query(
                "SELECT result_json FROM sync_operations WHERE op_id IN (?, ?)", tuple(op_ids))
            self.assertEqual(len(rows), 2, 'both events reached the ledger')
            return sum(1 for row in rows if '"changed":true' in row['result_json'])

        self.assertEqual(changes(reversed_order), 1, 'the late older event was a no-op')
        self.assertEqual(changes(natural_order), 2)

    def test_repeated_opens_of_one_work_are_one_event(self):
        server, page, context = self.start()
        work_a = server.ids['work_a']
        self.offline(page, context)
        for _ in range(3):
            self.open_work(page, work_a, WORK_A_TITLE)
        self.pending(page, 1)
        rows = self.operations(page)
        self.assertEqual(rows[0]['entity_id'], work_a)
        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(self.server_opened_at(server, work_a), self.canonical(rows[0]['occurred_at']))

    def test_different_works_keep_their_own_events(self):
        server, page, context = self.start()
        work_a, work_b = server.ids['work_a'], server.ids['work_b']
        self.offline(page, context)
        self.open_work(page, work_a, WORK_A_TITLE)
        self.open_work(page, work_b, WORK_B_TITLE)
        self.open_work(page, work_a, WORK_A_TITLE)
        self.pending(page, 2)
        rows = {row['entity_id']: row for row in self.operations(page)}
        self.assertEqual(sorted(rows), sorted([work_a, work_b]))
        self.assertGreater(rows[work_a]['occurred_at'], rows[work_b]['occurred_at'])
        self.assertEqual(self.recent_order(page), [work_a, work_b])
        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertGreater(self.server_opened_at(server, work_a), self.server_opened_at(server, work_b))

    # ---- degraded caches ----------------------------------------------------

    def test_without_a_recent_snapshot_nothing_is_fabricated(self):
        """One open event is not a Recent page. The activity is still durable
        and still synchronizes; the list simply stays honestly unavailable."""
        server, page, context = self.start()
        work_a = server.ids['work_a']
        o._clear_cached_list(page, 'recent:index')
        self.offline(page, context)
        self.open_work(page, work_a, WORK_A_TITLE)
        self.pending(page, 1)
        self.recent(page)
        page.locator('[data-prks-role=offline-unavailable]').wait_for()
        self.assertIsNone(o._cached_list(page, 'recent:index'))
        occurred = self.operations(page)[0]['occurred_at']
        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(self.server_opened_at(server, work_a), self.canonical(occurred))

    def test_an_open_event_for_a_deleted_work_is_consumed_not_parked(self):
        """There is no such thing as "apply my open event to a Work that no
        longer exists", so no conflict is offered and none is left pending."""
        server, page, context = self.start()
        work_a = server.ids['work_a']
        self.offline(page, context)
        self.open_work(page, work_a, WORK_A_TITLE)
        self.pending(page, 1)
        self.db_for(server).delete_work_record(work_a)
        self.reconnect(page, context)
        self.pending(page, 0)
        self.assertEqual(page.evaluate("() => prksSync.discarded()"),
                         [{'operation': 'MARK_WORK_OPENED', 'entity_id': work_a,
                           'code': 'ENTITY_NOT_FOUND'}])
