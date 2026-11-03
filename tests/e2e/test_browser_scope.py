"""Browser storage per account and the sync auth hold (#629 L-F-1, PR 2).

Authoritative acceptance for the client half of §10.3–10.4 of
`docs/postgresql-architecture.md`: a session-mode PostgreSQL server with two
accounts of one Library, signed into one browser by `POST /api/login`.

- Two accounts in one browser never see each other's queue; each account's
  pending rows wait in its own database and are delivered when it signs in
  again.
- An expired session mid-queue holds the queue (rows stay pending, nothing
  is settled) and delivers once the account signs in again.
- An explicit sign-out in one tab retires every open page of that account:
  their stores refuse reads and writes and nothing more is sent, and the rows
  wait until the account signs in again.
- Another account signing in under an open page retires that page's stores:
  nothing more is sent from it.
- Without a session the page opens no store at all.
- The service worker serves each tab its own account's PDF cache: offline,
  two tabs of two accounts asking for the same path get different bytes, and
  a retired tab gets none.

Editor recovery drafts are scoped in a later slice; these tests use the
durable operation queue (`prksSync.store`).
"""

import json
import os
import unittest
from datetime import datetime, timezone

from backend.storage.config import StorageConfig
from tests.e2e.fixtures import seed_folders_library, seed_library
from tests.e2e.harness import AppServer, PageCollector, require_chromium, wait_for_async
from tests.e2e.postgres_identity import OWNER_LOGIN, PASSWORD

READER = "reader"


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get("PRKS_E2E") == "1" else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close()
    _PW.stop()


_ENQUEUE_OPEN = """
    async (workId) => {
        const op = await prksSync.store.recordWorkOpened(workId, new Date().toISOString());
        prksSync.changed();
        return op.op_id;
    }
"""

_OPS = """
    async () => (await prksSync.store.listOperations()).map((r) => ({
        op_id: r.op_id, operation: r.operation, status: r.status,
        attempts: r.attempt_count, result: r.server_result || null,
    }))
"""

_DATABASES = """
    async () => (await indexedDB.databases()).map((d) => d.name).filter(Boolean).sort()
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


class BrowserScopeTests(unittest.TestCase):
    def setUp(self):
        self.server = AppServer(seed_fn=seed_folders_library, auth="session", members=(READER,))
        self.addCleanup(self.server.stop)
        self.server.start()
        self.work_id = self.server.ids["work_a"]
        self.context = _BROWSER.new_context(viewport={"width": 1400, "height": 900}, service_workers="block")
        self.addCleanup(self.context.close)

    # --- helpers -------------------------------------------------------------

    def sign_in(self, login):
        response = self.context.request.post(
            self.server.origin + "/api/login",
            data=json.dumps({"login": login, "password": PASSWORD}),
            headers={"Content-Type": "application/json", "Origin": self.server.origin},
        )
        self.assertEqual(response.status, 200, response.text())

    def expire_session(self):
        """The session ends without this browser signing out (as on expiry)."""
        self.context.clear_cookies()

    def open_page(self):
        page = self.context.new_page()
        PageCollector(page, self.server.origin)
        page.goto(self.server.origin + "/#/folders", wait_until="domcontentloaded")
        page.wait_for_selector(".prks-folder-library")
        return page

    def scope_user(self, page):
        return page.evaluate("async () => { const s = await prksBrowserScope.resolve(); return s && s.userId; }")

    def hold_sync(self, page):
        """Every send answers 503, so queued rows stay pending (retried, never settled)."""
        page.route(
            "**/api/sync/operations",
            lambda route: route.fulfill(status=503, content_type="application/json", body="{}"),
        )

    def release_sync(self, page):
        page.unroute("**/api/sync/operations")

    def sends(self, page):
        sent = []
        page.on(
            "request",
            lambda request: sent.append(request.url)
            if request.method == "POST" and request.url.endswith("/api/sync/operations")
            else None,
        )
        return sent

    def queue_one_attempted_open(self, page):
        self.hold_sync(page)
        op_id = page.evaluate(_ENQUEUE_OPEN, self.work_id)
        wait_for_async(
            page,
            "async (id) => (await prksSync.store.listOperations()).some((r) => r.op_id === id && r.attempt_count >= 1)",
            arg=op_id,
            message="the queued open was never attempted",
        )
        return op_id

    def wait_delivered(self, page, timeout=45000):
        wait_for_async(
            page,
            "async () => { await prksSync.wake(); return (await prksSync.store.listOperations()).length === 0; }",
            timeout=timeout,
            message="the held operation was never delivered",
        )

    # --- tests ---------------------------------------------------------------

    def test_two_accounts_in_one_browser_never_see_each_others_queue(self):
        owner_id = self.server.library.accounts[OWNER_LOGIN]
        reader_id = self.server.library.accounts[READER]
        self.sign_in(OWNER_LOGIN)
        page = self.open_page()
        self.assertEqual(self.scope_user(page), owner_id)
        owner_op = self.queue_one_attempted_open(page)
        owner_db = page.evaluate("() => prksBrowserScope.dbName('local')")
        self.assertTrue(owner_db.startswith("prks-local-v2:") and owner_db.endswith(owner_id), owner_db)
        page.close()

        self.sign_in(READER)
        page = self.open_page()
        self.hold_sync(page)
        self.assertEqual(self.scope_user(page), reader_id)
        self.assertEqual(page.evaluate(_OPS), [], "the reader must not see the owner's queue")
        reader_db = page.evaluate("() => prksBrowserScope.dbName('local')")
        self.assertNotEqual(reader_db, owner_db)
        reader_op = page.evaluate(_ENQUEUE_OPEN, self.work_id)
        self.assertEqual([r["op_id"] for r in page.evaluate(_OPS)], [reader_op])
        names = page.evaluate(_DATABASES)
        self.assertIn(owner_db, names, "the owner's queue must still exist, untouched")
        self.assertNotIn("prks-local-v1", names)
        self.assertNotIn("prks-offline-v1", names)
        page.close()

        # The owner signs in again: only the owner's row is there, and it is delivered.
        self.sign_in(OWNER_LOGIN)
        page = self.open_page()
        ops = page.evaluate(_OPS)
        self.assertEqual([r["op_id"] for r in ops], [owner_op])
        self.assertEqual(ops[0]["status"], "pending")
        self.wait_delivered(page)
        page.close()

        # And the reader's row is still waiting in the reader's own database.
        self.sign_in(READER)
        page = self.open_page()
        self.hold_sync(page)
        self.assertEqual([r["op_id"] for r in page.evaluate(_OPS)], [reader_op])

    def test_expired_session_mid_queue_holds_then_delivers(self):
        self.sign_in(OWNER_LOGIN)
        page = self.open_page()
        op_id = self.queue_one_attempted_open(page)

        self.expire_session()
        self.release_sync(page)
        wait_for_async(
            page,
            "async () => { await prksSync.wake(); return prksSync.held() === 'signed_out'; }",
            timeout=45000,
            message="an expired session did not hold the queue",
        )
        ops = page.evaluate(_OPS)
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["op_id"], op_id)
        self.assertEqual(ops[0]["status"], "pending", ops)
        self.assertIsNone(ops[0]["result"], "a held row is never settled")

        self.sign_in(OWNER_LOGIN)
        self.wait_delivered(page)
        self.assertIsNone(page.evaluate("() => prksSync.held()"))

    def test_explicit_sign_out_retires_every_open_page_of_the_account(self):
        self.sign_in(OWNER_LOGIN)
        first = self.open_page()
        op_id = self.queue_one_attempted_open(first)
        second = self.open_page()
        sent = self.sends(second)
        self.release_sync(first)

        first.evaluate("() => prksBrowserScope.signOut()")
        for page in (first, second):
            wait_for_async(
                page,
                "async () => prksBrowserScope.isRetired() && prksBrowserScope.retiredReason() === 'logged_out'",
                message="an explicit sign-out did not retire the page",
            )
            wait_for_async(
                page,
                "async () => { await prksSync.wake(); return prksSync.held() === 'logged_out'; }",
                message="an explicit sign-out did not stop delivery",
            )
            refused = page.evaluate(
                """async () => {
                    try { await prksSync.store.listOperations(); return null; }
                    catch (err) { return err.prksLocalStoreCode || 'error'; }
                }"""
            )
            self.assertEqual(refused, "unavailable", "a signed-out page must not read the account's queue")
        self.assertEqual(sent, [], "nothing may be sent after an explicit sign-out")
        first.close()
        second.close()

        # Nothing was deleted: the same account signing in again delivers it.
        self.sign_in(OWNER_LOGIN)
        page = self.open_page()
        self.assertEqual([r["op_id"] for r in page.evaluate(_OPS)], [op_id])
        self.wait_delivered(page)

    def test_another_account_signing_in_retires_the_open_page(self):
        reader_id = self.server.library.accounts[READER]
        self.sign_in(OWNER_LOGIN)
        page = self.open_page()
        op_id = self.queue_one_attempted_open(page)
        owner_db = page.evaluate("() => prksBrowserScope.dbName('local')")

        self.sign_in(READER)
        sent = self.sends(page)
        self.release_sync(page)
        wait_for_async(
            page,
            "async () => { await prksSync.wake(); return prksSync.held() === 'changed'; }",
            timeout=45000,
            message="the page kept its scope after another account signed in",
        )
        self.assertEqual(sent, [], "nothing may be sent under the other account")
        self.assertTrue(page.evaluate("() => prksBrowserScope.isRetired()"))
        refused = page.evaluate(
            """async (workId) => {
                try { await prksSync.store.recordWorkOpened(workId, new Date().toISOString()); return null; }
                catch (err) { return err.prksLocalStoreCode || 'error'; }
            }""",
            self.work_id,
        )
        self.assertEqual(refused, "unavailable", "a retired page must not write the old account's queue")
        page.close()

        # A reload binds the new account; the owner's row is still in the owner's database.
        page = self.open_page()
        self.hold_sync(page)
        self.assertEqual(self.scope_user(page), reader_id)
        self.assertEqual(page.evaluate(_OPS), [])
        self.assertIn(owner_db, page.evaluate(_DATABASES))
        page.close()
        self.sign_in(OWNER_LOGIN)
        page = self.open_page()
        self.assertEqual([r["op_id"] for r in page.evaluate(_OPS)], [op_id])
        self.wait_delivered(page)

    def test_without_a_session_no_store_is_opened(self):
        page = self.context.new_page()
        page.goto(self.server.origin + "/#/folders", wait_until="domcontentloaded")
        page.wait_for_function("() => !!window.prksBrowserScope && !!window.prksSync")
        self.assertIsNone(page.evaluate("() => prksBrowserScope.resolve()"))
        self.assertFalse(page.evaluate("() => prksSync.store.isAvailable()"))
        wait_for_async(
            page,
            "async () => { await prksSync.wake(); return prksSync.held() === 'unbound'; }",
            message="a page without a session did not hold its queue",
        )
        names = page.evaluate(_DATABASES)
        for name in names:
            self.assertFalse(
                name.startswith(("prks-local", "prks-offline")),
                "no queue or cache may be opened without a session: %s" % names,
            )



class BrowserScopePdfCacheTests(unittest.TestCase):
    """Offline, each tab reads its own account's PDF cache through the worker."""

    def setUp(self):
        self.server = AppServer(seed_fn=seed_library, auth="session", members=(READER,))
        self.addCleanup(self.server.stop)
        self.server.start()
        self.pdf_path = "/api/pdfs/%s" % self.server.ids["pdf_name"]
        self.context = _BROWSER.new_context(viewport={"width": 1400, "height": 900})
        self.addCleanup(self.context.close)

    def sign_in(self, login):
        response = self.context.request.post(
            self.server.origin + "/api/login",
            data=json.dumps({"login": login, "password": PASSWORD}),
            headers={"Content-Type": "application/json", "Origin": self.server.origin},
        )
        self.assertEqual(response.status, 200, response.text())

    def open_controlled_page(self, bind=True):
        page = self.context.new_page()
        PageCollector(page, self.server.origin)
        if not bind:
            # This tab's binding never reaches the worker: a live, unbound page.
            page.add_init_script(
                """(() => {
                    const post = ServiceWorker.prototype.postMessage;
                    ServiceWorker.prototype.postMessage = function (message, ...rest) {
                        if (message && message.type === 'prks-scope-bind') return;
                        return post.call(this, message, ...rest);
                    };
                })()"""
            )
        page.goto(self.server.origin + "/#/folders", wait_until="domcontentloaded")
        page.wait_for_function("() => !!window.prksBrowserScope")
        page.evaluate("() => navigator.serviceWorker.ready.then(() => true)")
        page.wait_for_function("() => !!(navigator.serviceWorker && navigator.serviceWorker.controller)")
        page.evaluate("() => prksBrowserScope.resolve()")
        return page

    def fetch_pdf(self, page):
        return page.evaluate(
            """async (path) => {
                try {
                    const res = await fetch(path, { cache: 'no-store' });
                    if (!res.ok) return { status: res.status };
                    const text = await res.text();
                    return { status: res.status, head: text.slice(0, 6) };
                } catch (err) { return { status: 0 }; }
            }""",
            self.pdf_path,
        )

    def test_each_tab_reads_its_own_account_pdf_cache_offline(self):
        self.sign_in(OWNER_LOGIN)
        tab_a = self.open_controlled_page()
        name_a = tab_a.evaluate("() => prksBrowserScope.dbName('pdf')")
        # Online, the worker caches the whole file into tab A's own cache.
        self.assertEqual(self.fetch_pdf(tab_a)["head"][:4], "%PDF")
        wait_for_async(
            tab_a,
            "async ([name, path]) => !!(await (await caches.open(name)).match(path))",
            arg=[name_a, self.pdf_path],
            message="tab A's PDF never reached its own cache",
        )

        # Account B signs in while tab A stays open, and has the same path
        # cached with different bytes.
        self.sign_in(READER)
        tab_b = self.open_controlled_page()
        name_b = tab_b.evaluate("() => prksBrowserScope.dbName('pdf')")
        self.assertNotEqual(name_a, name_b)
        tab_b.evaluate(
            """async ([name, path]) => {
                const cache = await caches.open(name);
                await cache.put(path, new Response('B-ONLY', { headers: { 'Content-Type': 'application/pdf' } }));
            }""",
            [name_b, self.pdf_path],
        )

        self.context.set_offline(True)
        self.assertEqual(self.fetch_pdf(tab_b), {"status": 200, "head": "B-ONLY"})
        a_offline = self.fetch_pdf(tab_a)
        self.assertEqual(a_offline["status"], 200, a_offline)
        self.assertEqual(a_offline["head"][:4], "%PDF", "tab A must never be served account B's bytes")

        # Back online, tab A notices the other account and retires: the
        # worker then serves it no cache at all.
        self.context.set_offline(False)
        self.assertEqual(tab_a.evaluate("() => prksBrowserScope.verify()"), "changed")
        self.context.set_offline(True)
        wait_for_async(
            tab_a,
            """async (path) => {
                try { return (await fetch(path, { cache: 'no-store' })).status === 503; }
                catch (err) { return false; }
            }""",
            arg=self.pdf_path,
            message="a retired tab was still served a PDF cache",
        )
        self.assertEqual(self.fetch_pdf(tab_b), {"status": 200, "head": "B-ONLY"})


    def cached_head(self, page, name):
        return page.evaluate(
            """async ([name, path]) => {
                const hit = await (await caches.open(name)).match(path);
                return hit ? (await hit.text()).slice(0, 8) : null;
            }""",
            [name, self.pdf_path],
        )

    def put_bytes(self, page, name, body):
        page.evaluate(
            """async ([name, path, body]) => {
                const cache = await caches.open(name);
                await cache.put(path, new Response(body, { headers: { 'Content-Type': 'application/pdf' } }));
            }""",
            [name, self.pdf_path, body],
        )

    def test_an_unbound_tab_is_never_served_the_last_confirmed_accounts_pdf(self):
        self.sign_in(READER)
        tab_b = self.open_controlled_page()
        name_b = tab_b.evaluate("() => prksBrowserScope.dbName('pdf')")
        self.put_bytes(tab_b, name_b, "B-ONLY")
        # Tab A is live under the same browser, but the worker never learns
        # its binding (still loading, or its message lost).
        tab_a = self.open_controlled_page(bind=False)

        self.context.set_offline(True)
        self.assertEqual(self.fetch_pdf(tab_b), {"status": 200, "head": "B-ONLY"})
        self.assertEqual(
            self.fetch_pdf(tab_a), {"status": 503},
            "an unbound tab must get the network only, never the last confirmed account's cache",
        )

    def test_an_old_tab_never_caches_the_new_accounts_pdf_bytes(self):
        self.sign_in(OWNER_LOGIN)
        tab_a = self.open_controlled_page()
        name_a = tab_a.evaluate("() => prksBrowserScope.dbName('pdf')")
        self.assertEqual(self.fetch_pdf(tab_a)["head"][:4], "%PDF")
        wait_for_async(
            tab_a,
            "async ([name, path]) => !!(await (await caches.open(name)).match(path))",
            arg=[name_a, self.pdf_path],
            message="tab A's PDF never reached its own cache",
        )
        original = self.cached_head(tab_a, name_a)
        self.assertTrue(original.startswith("%PDF-1"), original)

        # B signs in while tab A is still bound, and the same route now
        # answers different bytes.
        self.sign_in(READER)
        pdf_file = os.path.join(StorageConfig.for_testing(self.server.storage_root).pdfs_dir, self.server.ids["pdf_name"])
        with open(pdf_file, "wb") as fh:
            fh.write(b"%PDF-B-ONLY\n%%EOF\n")
        self.assertEqual(self.fetch_pdf(tab_a)["head"], "%PDF-B", "the online answer is served as is")
        # The worker's refused cache write leaves no observable event to wait on.
        # prks-allow-wait-for-timeout: asserting a cache write that must NOT happen needs a bounded window
        tab_a.wait_for_timeout(1500)
        self.assertEqual(
            self.cached_head(tab_a, name_a), original,
            "tab A's cache must never take bytes fetched under another account",
        )

    def install_saved_bytes(self, page, name, body):
        """Posts the worker the same install a successful PDF save sends."""
        return page.evaluate(
            """async ([name, path, body]) => {
                const bytes = new TextEncoder().encode(body).buffer;
                const channel = new MessageChannel();
                const answer = new Promise((resolve) => {
                    channel.port1.onmessage = (event) => resolve(!!(event.data && event.data.ok));
                });
                navigator.serviceWorker.controller.postMessage(
                    { type: 'prks-pdf-cache-install', pathname: path, cacheName: name, buffer: bytes },
                    [channel.port2, bytes],
                );
                return answer;
            }""",
            [name, self.pdf_path, body],
        )

    def test_an_old_tab_never_installs_saved_bytes_after_another_account_signs_in(self):
        self.sign_in(OWNER_LOGIN)
        tab_a = self.open_controlled_page()
        name_a = tab_a.evaluate("() => prksBrowserScope.dbName('pdf')")
        # Under its own session, a save's install still replaces the cached copy.
        self.assertTrue(self.install_saved_bytes(tab_a, name_a, "%PDF-A-SAVED"))
        self.assertEqual(self.cached_head(tab_a, name_a), "%PDF-A-S")

        # B signs in while tab A is still bound; A's next save runs under B's
        # session and its install must not touch A's cache.
        self.sign_in(READER)
        self.assertFalse(
            self.install_saved_bytes(tab_a, name_a, "%PDF-B-SAVED"),
            "the worker must refuse bytes saved under another account",
        )
        self.assertEqual(
            self.cached_head(tab_a, name_a), "%PDF-A-S",
            "tab A's cache must never take bytes saved under another account",
        )

        # Offline, A still reads the bytes it cached under its own session.
        self.context.set_offline(True)
        self.assertEqual(self.fetch_pdf(tab_a), {"status": 200, "head": "%PDF-A"})


if __name__ == "__main__":
    unittest.main()
