"""Browser storage per account and the sync auth hold (#629 L-F-1, PR 2).

Authoritative acceptance for the client half of §10.3–10.4 of
`docs/postgresql-architecture.md`: a session-mode PostgreSQL server with two
accounts of one Library, signed into one browser by `POST /api/login`.

- Two accounts in one browser never see each other's queue; each account's
  pending rows wait in its own database and are delivered when it signs in
  again.
- An expired session mid-queue holds the queue (rows stay pending, nothing
  is settled) and delivers once the account signs in again.
- Another account signing in under an open page retires that page's stores:
  nothing more is sent from it.
- Without a session the page opens no store at all.

Editor recovery drafts are scoped in a later slice; these tests use the
durable operation queue (`prksSync.store`).
"""

import json
import os
import unittest
from datetime import datetime, timezone

from tests.e2e.fixtures import seed_folders_library
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

    def sign_out(self):
        response = self.context.request.post(
            self.server.origin + "/api/logout", headers={"Origin": self.server.origin}
        )
        self.assertEqual(response.status, 200, response.text())

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

        self.sign_out()
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


if __name__ == "__main__":
    unittest.main()
