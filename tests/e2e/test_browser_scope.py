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

- Editor recovery drafts (#466) live in per-account storage: one account's
  drafts and emergency copies are never listed, merged or released for
  another account, and without a session recovery stores nothing.

The queue tests use the durable operation queue (`prksSync.store`).
"""

import json
import os
import time
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


_RECOVERY_READY = "() => !!window.prksEditorRecovery && !!window.prksEditorRecovery.runtime()"

_SEED_DRAFT = """
    async ({ workId, body }) => {
        const rt = window.prksEditorRecovery.runtime();
        const pageInstanceId = 'p-elsewhere-' + body.length;
        const outcome = await rt.store.writeGeneration({
            draftId: 'd-' + pageInstanceId, pageInstanceId, generation: 1, body,
            create: { kind: 'work-research-note', entityType: 'work', entityId: workId,
                      owner: { runtimeId: null, pageInstanceId, paneId: 'tab-1', claimedAt: Date.now() },
                      base: { revision: null, length: null, fingerprint: null, source: 'unknown', epoch: rt.epoch } },
        });
        // A copy only an emergency key holds, as a tab that closed before its commit leaves.
        const names = rt.names;
        localStorage.setItem(names.emergencyPrefix + 'p-emergency', JSON.stringify({
            v: 1, pageInstanceId: 'p-emergency', runtimeId: null, at: Date.now(),
            entries: [{ draftId: 'd-emergency', kind: 'work-research-note', entityType: 'work', entityId: workId,
                        generation: 1, committedGeneration: 0, body: body + ' (emergency)',
                        lineage: { createdAt: Date.now(), owner: { runtimeId: null, pageInstanceId: 'p-emergency', paneId: 'tab-1' },
                                   base: { revision: null, length: null, fingerprint: null, source: 'unknown', epoch: rt.epoch } } }],
        }));
        return { outcome, names: { ...names } };
    }
"""

_DRAFT_BODIES = """
    async () => {
        const store = window.prksEditorRecovery.runtime().store;
        const out = [];
        for (const row of await store.listAll()) {
            const body = await store.getBody(row.draftId);
            out.push(body ? body.body : null);
        }
        return out.sort();
    }
"""

_KEEP_RECOVERY_HANDLES = """
    () => {
        window.__heldRecovery = { rt: prksEditorRecovery.runtime(), diag: prksEditorRecovery.diagnostics() };
        return !!window.__heldRecovery.rt && !!window.__heldRecovery.diag;
    }
"""

_HELD_RECOVERY_ACCESS = """
    async () => {
        const held = window.__heldRecovery;
        const out = {
            runtime: prksEditorRecovery.runtime(),
            diagnostics: prksEditorRecovery.diagnostics(),
            names: prksEditorRecovery.scopeNames(),
            retired: held.rt.isRetired(),
        };
        try { await held.rt.store.listAll(); out.list = 'read'; } catch (err) { out.list = err.code || 'error'; }
        try { await held.rt.store.getBody('d-p-elsewhere-10'); out.body = 'read'; } catch (err) { out.body = err.code || 'error'; }
        try { held.rt.emergencyStorage.getItem('x'); out.local = 'read'; } catch (err) { out.local = 'refused'; }
        const inventory = await held.diag.inventory();
        out.inventory = JSON.stringify(inventory);
        return out;
    }
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

    def open_page(self, before_load=None):
        page = self.context.new_page()
        PageCollector(page, self.server.origin)
        if before_load:
            before_load(page)
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

        # The owner signs in again: only the owner's row is there, and it is
        # delivered. Sends are held from the first load; a held send marks the
        # row in flight until its 503 returns it to pending, so wait for that.
        self.sign_in(OWNER_LOGIN)
        page = self.open_page(before_load=self.hold_sync)
        self.assertEqual([r["op_id"] for r in page.evaluate(_OPS)], [owner_op])
        wait_for_async(
            page,
            "async (id) => { const rows = await prksSync.store.listOperations();"
            " return rows.length === 1 && rows[0].op_id === id && rows[0].status === 'pending'; }",
            arg=owner_op,
            message="the owner's row did not wait as pending",
        )
        self.release_sync(page)
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

    def test_a_session_answer_older_than_a_sign_out_never_restores_the_account(self):
        self.sign_in(OWNER_LOGIN)
        signer = self.open_page()
        # This tab misses the sign-out broadcast, so only the record can tell it.
        late = self.context.new_page()
        PageCollector(late, self.server.origin)
        late.add_init_script("window.BroadcastChannel = undefined;")
        late.goto(self.server.origin + "/#/folders", wait_until="domcontentloaded")
        late.wait_for_selector(".prks-folder-library")
        self.assertTrue(self.scope_user(late))

        # The late tab's session read is answered before the sign-out, but its
        # answer reaches the page only after it.
        held = []
        late.route("**/api/session", lambda route: held.append((route, route.fetch())))
        late.evaluate("() => { window.__verdict = prksBrowserScope.verify(); }")
        deadline = time.time() + 10
        while time.time() < deadline and not held:
            # prks-allow-wait-for-timeout: polling for a route handler to capture the held request
            late.wait_for_timeout(50)
        self.assertTrue(held, "the late tab never read its session")
        self.assertEqual(held[0][1].status, 200)

        signer.evaluate("() => prksBrowserScope.signOut()")
        route, response = held.pop()
        route.fulfill(response=response)
        late.unroute("**/api/session")

        self.assertEqual(late.evaluate("() => window.__verdict"), "logged_out")
        self.assertIsNone(
            late.evaluate(
                """() => new Promise((resolve, reject) => {
                    const open = indexedDB.open(PRKS_SCOPE_POINTER_DB_NAME);
                    open.onerror = () => reject(open.error);
                    open.onsuccess = () => {
                        const db = open.result;
                        const get = db.transaction(PRKS_SCOPE_POINTER_STORE).objectStore(PRKS_SCOPE_POINTER_STORE)
                            .get(PRKS_SCOPE_POINTER_KEY);
                        get.onsuccess = () => { db.close(); resolve((get.result && get.result.scope) || null); };
                        get.onerror = () => { db.close(); reject(get.error); };
                    };
                })"""
            ),
            "an answer older than the sign-out must not restore the last confirmed account",
        )
        # An offline reload finds no account to open.
        self.assertIsNone(
            late.evaluate(
                """async () => {
                    const offline = createPrksBrowserScope({ fetchImpl: () => Promise.reject(new TypeError('offline')) });
                    const scope = await offline.resolve();
                    return scope ? scope.key : (await offline.dbName('local'));
                }"""
            )
        )

    def test_an_older_refusal_never_forgets_a_newer_accounts_scope(self):
        reader_id = self.server.library.accounts[READER]
        self.sign_in(OWNER_LOGIN)
        stale = self.open_page()
        self.expire_session()
        # The stale tab's session read is refused, but the refusal reaches the
        # page only after another account has signed in and been confirmed.
        # Only that first read is held; the page's next read goes through.
        # A read the page began before the session ended still carries the
        # old cookie (a 200); only the refused read is held.
        held = []

        def hold_first(route):
            response = route.fetch()
            if held or response.status != 401:
                route.fulfill(response=response)
                return
            held.append((route, response))

        stale.route("**/api/session", hold_first)
        stale.evaluate("() => { window.__verdict = prksBrowserScope.verify(); }")
        deadline = time.time() + 10
        while time.time() < deadline and not held:
            # prks-allow-wait-for-timeout: polling for a route handler to capture the held request
            stale.wait_for_timeout(50)
        self.assertTrue(held, "the stale tab never read its session")
        self.assertEqual(held[0][1].status, 401)

        self.sign_in(READER)
        reader = self.open_page()
        self.assertEqual(self.scope_user(reader), reader_id)

        route, response = held[0]
        route.fulfill(response=response)
        # The older refusal is not acted on: the page reads again and finds
        # the reader's session.
        self.assertEqual(stale.evaluate("() => window.__verdict"), "changed")
        stale.unroute("**/api/session")

        # The reader's record survives, so an offline reload still binds it.
        self.assertEqual(
            reader.evaluate(
                """async () => {
                    const offline = createPrksBrowserScope({ fetchImpl: () => Promise.reject(new TypeError('offline')) });
                    const scope = await offline.resolve();
                    return scope && scope.userId;
                }"""
            ),
            reader_id,
            "an older refusal must not forget a newer account's last confirmed scope",
        )

    def test_a_superseded_first_answer_never_binds_the_older_account(self):
        owner_id = self.server.library.accounts[OWNER_LOGIN]
        reader_id = self.server.library.accounts[READER]
        self.sign_in(OWNER_LOGIN)
        late = self.open_page()
        # A page's first session read is answered for the owner, but the
        # answer reaches it only after another tab confirmed the reader.
        late.evaluate(
            """() => {
                let release;
                const gate = new Promise((r) => { release = r; });
                window.__releaseSession = release;
                window.__sessionAnswered = false;
                const fetchImpl = (url, init) => fetch(url, init).then((response) => {
                    if (url !== '/api/session' || window.__sessionAnswered) return response;
                    window.__sessionAnswered = true;
                    return gate.then(() => response);
                });
                window.__lateScope = createPrksBrowserScope({ fetchImpl });
                window.__lateBound = window.__lateScope.resolve();
            }"""
        )
        wait_for_async(late, "() => window.__sessionAnswered === true", timeout=10000)

        self.sign_in(READER)
        reader = self.open_page()
        self.assertEqual(self.scope_user(reader), reader_id)

        late.evaluate("() => window.__releaseSession()")
        bound = late.evaluate(
            """async () => {
                const scope = await window.__lateBound;
                return { user: scope && scope.userId, db: await window.__lateScope.dbName('local') };
            }"""
        )
        self.assertEqual(bound["user"], reader_id, "a superseded answer must not bind the older account")
        self.assertNotIn(owner_id, bound["db"] or "", "the page must not open the older account's store")
        self.assertEqual(self.scope_user(reader), reader_id)

    def test_an_older_confirmation_never_undoes_a_newer_refusal(self):
        self.sign_in(OWNER_LOGIN)
        page = self.open_page()
        # One reader's session read is answered 200 for the owner, but the
        # answer is delivered only after the session ended and a newer read
        # (another reader on the same browser storage) was refused.
        page.evaluate(
            """() => {
                let release;
                const gate = new Promise((r) => { release = r; });
                window.__releaseSession = release;
                window.__sessionAnswered = false;
                const fetchImpl = (url, init) => fetch(url, init).then((response) => {
                    if (url !== '/api/session' || window.__sessionAnswered) return response;
                    window.__sessionAnswered = true;
                    return gate.then(() => response);
                });
                window.__lateScope = createPrksBrowserScope({ fetchImpl });
                window.__lateBound = window.__lateScope.resolve();
            }"""
        )
        wait_for_async(page, "() => window.__sessionAnswered === true", timeout=10000)

        self.expire_session()
        self.assertIsNone(
            page.evaluate("async () => { const s = await createPrksBrowserScope().resolve(); return s && s.key; }"),
            "the newer read is refused",
        )

        page.evaluate("() => window.__releaseSession()")
        bound = page.evaluate(
            """async () => {
                const scope = await window.__lateBound;
                return { key: scope && scope.key, db: await window.__lateScope.dbName('local') };
            }"""
        )
        self.assertEqual(bound, {"key": None, "db": None}, "an older confirmation must not undo a newer refusal")
        self.assertIsNone(
            page.evaluate(
                """async () => {
                    const offline = createPrksBrowserScope({ fetchImpl: () => Promise.reject(new TypeError('offline')) });
                    const scope = await offline.resolve();
                    return scope ? scope.key : (await offline.dbName('local'));
                }"""
            ),
            "an offline reload must not reopen the refused account",
        )

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

    def test_two_accounts_in_one_browser_never_see_each_others_recovery_drafts(self):
        owner_id = self.server.library.accounts[OWNER_LOGIN]
        self.sign_in(OWNER_LOGIN)
        page = self.open_page()
        page.wait_for_function(_RECOVERY_READY)
        seeded = page.evaluate(_SEED_DRAFT, {"workId": self.work_id, "body": "Owner text"})
        self.assertEqual(seeded["outcome"], "ok")
        owner_names = seeded["names"]
        self.assertTrue(owner_names["dbName"].startswith("prks-editor-recovery-v2:"), owner_names)
        self.assertTrue(owner_names["dbName"].endswith("." + owner_id), owner_names)
        page.close()

        self.sign_in(READER)
        page = self.open_page()
        page.wait_for_function(_RECOVERY_READY)
        reader_names = page.evaluate("() => ({ ...prksEditorRecovery.scopeNames() })")
        for key in ("dbName", "emergencyPrefix", "reservationPrefix", "closedPagePrefix",
                    "supersededPrefix", "deletedWorkPrefix", "channel", "runtimeLockPrefix", "pageLockPrefix"):
            self.assertNotEqual(reader_names[key], owner_names[key], key)
        page.evaluate("() => prksEditorRecovery.runtime().scanEmergency().then(() => null)")
        self.assertEqual(page.evaluate(_DRAFT_BODIES), [], "the reader must not see the owner's drafts")
        # The reader's page never merges or removes the owner's emergency copy.
        self.assertIsNotNone(
            page.evaluate("(key) => localStorage.getItem(key)", owner_names["emergencyPrefix"] + "p-emergency"))
        page.evaluate(_SEED_DRAFT, {"workId": self.work_id, "body": "Reader text, longer"})
        self.assertIn(owner_names["dbName"], page.evaluate(_DATABASES), "the owner's drafts must still exist")
        page.close()

        # The owner signs in again: the owner's drafts are there, the emergency
        # copy merged, and nothing the reader wrote.
        self.sign_in(OWNER_LOGIN)
        page = self.open_page()
        page.wait_for_function(_RECOVERY_READY)
        wait_for_async(
            page,
            "async () => { await prksEditorRecovery.runtime().scanEmergency();"
            " const b = await (" + _DRAFT_BODIES + ")(); return b.includes('Owner text (emergency)') && b; }",
            message="the owner's emergency copy was not merged into the owner's drafts",
        )
        bodies = page.evaluate(_DRAFT_BODIES)
        self.assertIn("Owner text", bodies)
        self.assertFalse([b for b in bodies if b and b.startswith("Reader text")], bodies)

    def assert_recovery_retired(self, page):
        access = page.evaluate(_HELD_RECOVERY_ACCESS)
        self.assertIsNone(access["runtime"])
        self.assertIsNone(access["diagnostics"])
        self.assertIsNone(access["names"])
        self.assertTrue(access["retired"])
        self.assertEqual(access["list"], "retired")
        self.assertEqual(access["body"], "retired")
        self.assertEqual(access["local"], "refused")
        self.assertNotIn("Owner text", access["inventory"])

    def assert_owner_draft_kept(self):
        self.sign_in(OWNER_LOGIN)
        page = self.open_page()
        page.wait_for_function(_RECOVERY_READY)
        self.assertIn("Owner text", page.evaluate(_DRAFT_BODIES))

    def test_signing_out_retires_recovery_also_through_handles_taken_before(self):
        self.sign_in(OWNER_LOGIN)
        page = self.open_page()
        page.wait_for_function(_RECOVERY_READY)
        self.assertEqual(page.evaluate(_SEED_DRAFT, {"workId": self.work_id, "body": "Owner text"})["outcome"], "ok")
        self.assertTrue(page.evaluate(_KEEP_RECOVERY_HANDLES))
        page.evaluate("() => prksBrowserScope.signOut()")
        page.wait_for_function("() => prksBrowserScope.isRetired()")
        self.assert_recovery_retired(page)
        page.close()
        self.assert_owner_draft_kept()

    def test_another_account_signing_in_retires_recovery_of_the_open_page(self):
        self.sign_in(OWNER_LOGIN)
        page = self.open_page()
        page.wait_for_function(_RECOVERY_READY)
        self.assertEqual(page.evaluate(_SEED_DRAFT, {"workId": self.work_id, "body": "Owner text"})["outcome"], "ok")
        self.assertTrue(page.evaluate(_KEEP_RECOVERY_HANDLES))
        self.sign_in(READER)
        wait_for_async(
            page,
            "async () => (await prksBrowserScope.verify()) === 'changed' && prksBrowserScope.isRetired()",
            message="the page kept its scope after another account signed in",
        )
        self.assert_recovery_retired(page)
        page.close()
        self.assert_owner_draft_kept()

    def test_a_browser_without_a_session_signs_in_on_the_page(self):
        """PG-C2 #641: the page offers sign-in; a wrong password is refused, the right one reloads signed in."""
        page = self.context.new_page()
        PageCollector(page, self.server.origin)
        page.goto(self.server.origin + "/#/folders", wait_until="domcontentloaded")
        dialog = page.locator("#prks-sign-in")
        dialog.wait_for(state="visible")
        self.assertEqual(dialog.get_attribute("role"), "dialog")
        self.assertEqual(page.evaluate("() => document.activeElement && document.activeElement.id"), "prks-sign-in-login")
        self.assertTrue(page.evaluate(
            "() => Array.from(document.body.children)"
            ".filter((el) => !el.id.startsWith('prks-sign-in')).every((el) => el.inert)"))
        page.keyboard.press("Escape")
        self.assertTrue(dialog.is_visible(), "Escape must not close the sign-in dialog")

        page.fill("#prks-sign-in-login", OWNER_LOGIN)
        page.fill("#prks-sign-in-password", "not the password")
        held = []
        page.route("**/api/login", lambda route: held.append(route))
        page.keyboard.press("Enter")
        button = page.locator("#prks-sign-in button[type=submit]")
        page.wait_for_function(
            "() => document.querySelector('#prks-sign-in button[type=submit]').getAttribute('aria-busy') === 'true'")
        self.assertTrue(button.is_disabled())
        self.assertEqual(button.inner_text().strip(), "Signing in…")
        self.assertEqual(len(held), 1)
        held[0].continue_()
        page.unroute("**/api/login")
        error = page.locator("#prks-sign-in-error")
        error.wait_for(state="visible")
        self.assertEqual(error.inner_text(), "The login or password is not right.")
        self.assertEqual(page.input_value("#prks-sign-in-password"), "")
        self.assertEqual(page.input_value("#prks-sign-in-login"), OWNER_LOGIN)
        self.assertIsNone(button.get_attribute("aria-busy"))
        self.assertTrue(button.is_enabled())
        self.assertEqual(button.inner_text().strip(), "Sign in")

        page.fill("#prks-sign-in-password", PASSWORD)
        with page.expect_navigation(wait_until="domcontentloaded"):
            page.click("#prks-sign-in button[type=submit]")
        page.wait_for_selector(".prks-folder-library")
        self.assertEqual(page.locator("#prks-sign-in").count(), 0)
        owner = page.evaluate("async () => { const s = await prksBrowserScope.resolve(); return s && s.userId; }")
        self.assertTrue(owner)
        self.assertFalse(page.evaluate("() => prksBrowserScope.needsSignIn()"))

    def test_without_a_session_recovery_stores_nothing(self):
        page = self.context.new_page()
        page.goto(self.server.origin + "/#/folders", wait_until="domcontentloaded")
        page.wait_for_function("() => !!window.prksBrowserScope && !!window.prksEditorRecovery")
        self.assertIsNone(page.evaluate("() => prksBrowserScope.resolve()"))
        self.assertIsNone(page.evaluate("() => prksEditorRecovery.runtime()"))
        self.assertIsNone(page.evaluate("() => prksEditorRecovery.scopeNames()"))
        self.assertFalse([n for n in page.evaluate(_DATABASES) if n.startswith("prks-editor-recovery")])
        self.assertFalse(page.evaluate(
            "() => Object.keys(localStorage).some((k) => k.startsWith('prks.editorRecovery.'))"))

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
