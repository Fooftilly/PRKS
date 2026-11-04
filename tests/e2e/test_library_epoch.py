"""Library epoch fencing of the durable queue (#629 L-F-1, PR 3a).

Authoritative acceptance for the queue half of §10.4 and §11.4 of
`docs/postgresql-architecture.md`, on a PostgreSQL server. The Library's
epoch is advanced in the database directly, as a restore's commit does,
without a real restore (that belongs to L-B-1 and the combined scenario to
PG-C1).

- A change queued before the epoch changed is never applied: its send under
  the old epoch is refused 409 `LIBRARY_EPOCH_CHANGED`, the page reads the
  new epoch from `/api/session`, and the change leaves the queue for the
  review-only list in Settings -> Diagnostics, where its text can be copied
  and it can be discarded.
- A page loaded after the epoch changed reads the new epoch from
  `/api/session` and moves an older queued change to review before sending
  anything.
- A page loaded before the restore keeps naming the epoch it loaded, and
  records no new change.
- Signing the account out in another tab clears the review list from an open
  Diagnostics view, also when its read was still in flight, and leaves the
  stored changes for the account.
"""

import json
import os
import unittest

import psycopg

from tests.e2e.fixtures import seed_folders_library
from tests.e2e.harness import AppServer, PageCollector, require_chromium, wait_for_async
from tests.e2e.postgres_identity import OWNER_LOGIN, PASSWORD


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get("PRKS_E2E") == "1" else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close()
    _PW.stop()


_QUEUE_NOTE = """
    async ({ workId, text }) => {
        await prksBrowserScope.resolve();
        const op = await prksSync.store.saveWorkNote(workId, 'SET_WORK_PRIVATE_NOTE', text, { value: '', revision: 0 });
        prksSync.changed();
        return { opId: op.op_id, epoch: op.library_epoch };
    }
"""

_QUEUE_STATE = """
    async () => ({
        queued: (await prksSync.store.listOperations()).map((r) => r.op_id),
        restored: (await prksSync.store.listRestored()).map((r) => ({
            opId: r.op_id, epoch: r.library_epoch, supersededBy: r.superseded_by_epoch,
            text: r.payload && r.payload.text,
        })),
    })
"""

_TRY_NOTE = """
    async ({ workId, text }) => {
        try {
            await prksSync.store.saveWorkNote(workId, 'SET_WORK_PRIVATE_NOTE', text, { value: '', revision: 0 });
            return 'queued';
        } catch (err) {
            return err.prksLocalStoreCode || 'error';
        }
    }
"""

TYPED = "Typed before the restore"


class LibraryEpochQueueTests(unittest.TestCase):
    def setUp(self):
        self.server = AppServer(seed_fn=seed_folders_library)
        self.addCleanup(self.server.stop)
        self.server.start()
        self.work_id = self.server.ids["work_a"]
        self.context = _BROWSER.new_context(viewport={"width": 1400, "height": 900}, service_workers="block")
        self.addCleanup(self.context.close)

    # --- helpers -------------------------------------------------------------

    def restore_epoch(self):
        """Advances the Library's epoch, as a restore's commit does (§11.4)."""
        with psycopg.connect(self.server.library.conninfo, autocommit=True) as conn:
            row = conn.execute(
                "UPDATE prks.libraries SET epoch = epoch + 1 WHERE id = %s RETURNING epoch",
                (self.server.library.library_id,),
            ).fetchone()
        return str(row[0])

    def open_page(self):
        page = self.context.new_page()
        PageCollector(page, self.server.origin)
        page.goto(self.server.origin + "/#/folders", wait_until="domcontentloaded")
        page.wait_for_selector(".prks-folder-library")
        page.wait_for_function("() => !!window.prksSync && !!window.prksBrowserScope")
        return page

    def watch_sends(self, page):
        """Every answer to a queued send, with the epoch it named."""
        answers = []

        def on_response(response):
            request = response.request
            if request.method == "POST" and request.url.endswith("/api/sync/operations"):
                answers.append((request.headers.get("prks-library-epoch"), response.status))

        page.on("response", on_response)
        return answers

    def hold_sync(self, page):
        page.route(
            "**/api/sync/operations",
            lambda route: route.fulfill(status=503, content_type="application/json", body="{}"),
        )

    def queue_note(self, page):
        self.hold_sync(page)
        queued = page.evaluate(_QUEUE_NOTE, {"workId": self.work_id, "text": TYPED})
        self.assertEqual(queued["epoch"], "1", "a change records the epoch its page loaded")
        wait_for_async(
            page,
            "async (id) => (await prksSync.store.listOperations()).some((r) => r.op_id === id && r.attempt_count >= 1)",
            arg=queued["opId"],
            message="the queued change was never attempted",
        )
        return queued["opId"]

    def wait_reviewed(self, page, op_id):
        wait_for_async(
            page,
            """async (id) => {
                await prksSync.wake();
                const queued = await prksSync.store.listOperations();
                const restored = await prksSync.store.listRestored();
                return queued.length === 0 && restored.some((r) => r.op_id === id);
            }""",
            arg=op_id,
            timeout=30000,
            message="the change from before the restore never reached review",
        )
        return page.evaluate(_QUEUE_STATE)

    # --- tests ---------------------------------------------------------------

    def test_a_change_queued_before_the_restore_is_refused_and_kept_for_review(self):
        page = self.open_page()
        answers = self.watch_sends(page)
        op_id = self.queue_note(page)
        epoch = self.restore_epoch()
        page.unroute("**/api/sync/operations")

        state = self.wait_reviewed(page, op_id)
        self.assertEqual(state["queued"], [])
        self.assertEqual(
            state["restored"],
            [{"opId": op_id, "epoch": "1", "supersededBy": epoch, "text": TYPED}],
        )
        delivered = [a for a in answers if a[1] not in (409, 503)]
        self.assertEqual(delivered, [], "the server never applied the change: %r" % answers)
        self.assertIn(("1", 409), answers, "its send named its own epoch and was refused")

        # The page loaded before the restore: it keeps naming epoch 1 and records nothing new.
        self.assertEqual(page.evaluate("() => prksLibraryEpochHeaders()"), {"PRKS-Library-Epoch": "1"})
        self.assertEqual(page.evaluate("() => prksBrowserScope.latestEpoch()"), epoch)
        self.assertEqual(
            page.evaluate(_TRY_NOTE, {"workId": self.work_id, "text": "Typed after it"}),
            "library_restored",
        )

        # Review in Settings -> Diagnostics: the text can be copied, then discarded.
        page.locator("button.settings-btn").click()
        page.locator("#prks-settings-tab-diagnostics").click()
        panel = page.locator("#prks-settings-panel-diagnostics")
        panel.locator("#prks-offline-cache-refresh-btn").click()
        row = panel.locator('[data-prks-role="restored-change"]')
        row.wait_for(timeout=20000)
        self.assertIn(TYPED, row.inner_text())
        self.assertIn(
            "before this library was restored",
            panel.locator('[data-prks-role="restored-changes-heading"]').inner_text(),
        )
        self.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=self.server.origin)
        row.get_by_role("button", name="Copy text", exact=True).click()
        row.get_by_role("button", name="Copied", exact=True).wait_for()
        self.assertEqual(page.evaluate("() => navigator.clipboard.readText()"), TYPED)
        row.get_by_role("button", name="Discard", exact=True).click()
        # Never on one click: the row may be the only copy of what was typed.
        confirm = page.locator("#prks-modal-confirm:not(.hidden)", has_text="Discard this change?")
        confirm.wait_for()
        page.locator("#prks-modal-confirm-cancel").click()
        self.assertEqual(panel.locator('[data-prks-role="restored-change"]').count(), 1)
        row.get_by_role("button", name="Discard", exact=True).click()
        confirm.get_by_role("button", name="Discard change", exact=True).click()
        wait_for_async(page, "async () => (await prksSync.store.listRestored()).length === 0")
        self.assertEqual(panel.locator('[data-prks-role="restored-change"]').count(), 0)

    def test_a_page_loaded_after_the_restore_sends_nothing_from_before_it(self):
        page = self.open_page()
        op_id = self.queue_note(page)
        page.close()
        epoch = self.restore_epoch()

        # A reload reads the new epoch from /api/session before anything is sent.
        reloaded = self.context.new_page()
        PageCollector(reloaded, self.server.origin)
        answers = self.watch_sends(reloaded)
        reloaded.goto(self.server.origin + "/#/folders", wait_until="domcontentloaded")
        reloaded.wait_for_selector(".prks-folder-library")
        reloaded.wait_for_function("() => !!window.prksSync && !!window.prksBrowserScope")
        state = self.wait_reviewed(reloaded, op_id)
        self.assertEqual(answers, [], "nothing from before the restore was sent")
        self.assertEqual(state["restored"][0]["supersededBy"], epoch)
        self.assertEqual(reloaded.evaluate("() => prksBrowserScope.pageEpoch()"), epoch)

        # The reloaded page acts under the new epoch.
        self.assertEqual(reloaded.evaluate("() => prksLibraryEpochHeaders()"), {"PRKS-Library-Epoch": epoch})
        self.assertEqual(reloaded.evaluate(_TRY_NOTE, {"workId": self.work_id, "text": "After the restore"}), "queued")
        rows = reloaded.evaluate("async () => (await prksSync.store.listOperations()).map((r) => r.library_epoch)")
        self.assertTrue(rows and all(e == epoch for e in rows), rows)



class LibraryEpochReviewSignOutTests(unittest.TestCase):
    """The review list of an account that signs out in another tab (#1115 review)."""

    restore_epoch = LibraryEpochQueueTests.restore_epoch
    queue_note = LibraryEpochQueueTests.queue_note
    hold_sync = LibraryEpochQueueTests.hold_sync
    wait_reviewed = LibraryEpochQueueTests.wait_reviewed

    def setUp(self):
        self.server = AppServer(seed_fn=seed_folders_library, auth="session")
        self.addCleanup(self.server.stop)
        self.server.start()
        self.work_id = self.server.ids["work_a"]
        self.context = _BROWSER.new_context(viewport={"width": 1400, "height": 900}, service_workers="block")
        self.addCleanup(self.context.close)
        response = self.context.request.post(
            self.server.origin + "/api/login",
            data=json.dumps({"login": OWNER_LOGIN, "password": PASSWORD}),
            headers={"Content-Type": "application/json", "Origin": self.server.origin},
        )
        self.assertEqual(response.status, 200, response.text())

    open_page = LibraryEpochQueueTests.open_page

    def reviewed_page(self):
        page = self.open_page()
        op_id = self.queue_note(page)
        self.restore_epoch()
        page.unroute("**/api/sync/operations")
        self.wait_reviewed(page, op_id)
        page.locator("button.settings-btn").click()
        page.locator("#prks-settings-tab-diagnostics").click()
        panel = page.locator("#prks-settings-panel-diagnostics")
        return page, panel

    def sign_out_elsewhere(self, page):
        other = self.open_page()
        other.evaluate("() => prksBrowserScope.signOut()")
        page.wait_for_function("() => prksBrowserScope.isRetired()")
        other.close()

    def assert_kept_for_the_account(self):
        response = self.context.request.post(
            self.server.origin + "/api/login",
            data=json.dumps({"login": OWNER_LOGIN, "password": PASSWORD}),
            headers={"Content-Type": "application/json", "Origin": self.server.origin},
        )
        self.assertEqual(response.status, 200, response.text())
        again = self.open_page()
        restored = again.evaluate("async () => (await prksSync.store.listRestored()).map((r) => r.payload.text)")
        self.assertEqual(restored, [TYPED], "nothing stored was removed")

    def test_signing_out_clears_a_rendered_review_list(self):
        page, panel = self.reviewed_page()
        panel.locator("#prks-offline-cache-refresh-btn").click()
        rows = panel.locator('[data-prks-role="restored-change"]')
        rows.first.wait_for(timeout=20000)
        copy = rows.first.get_by_role("button", name="Copy text", exact=True)
        handle = copy.element_handle()
        self.sign_out_elsewhere(page)
        page.wait_for_function("() => !document.querySelector('[data-prks-role=restored-change]')")
        self.assertEqual(panel.locator('[data-prks-role="restored-changes-heading"]').count(), 0)
        # A copy button kept from before hands out nothing.
        self.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=self.server.origin)
        page.evaluate("() => navigator.clipboard.writeText('unchanged')")
        copied = page.evaluate(
            """async (button) => {
                button.click();
                await new Promise((resolve) => setTimeout(resolve, 50));
                return { label: button.textContent, clipboard: await navigator.clipboard.readText() };
            }""",
            handle,
        )
        self.assertEqual(copied, {"label": "Copy text", "clipboard": "unchanged"})
        page.close()
        self.assert_kept_for_the_account()

    def test_a_review_read_in_flight_at_sign_out_shows_nothing(self):
        page, panel = self.reviewed_page()
        page.evaluate(
            """() => {
                const store = prksSync.store;
                const read = store.listRestored;
                window.__release = null;
                const gate = new Promise((resolve) => { window.__release = resolve; });
                window.__served = false;
                store.listRestored = async () => {
                    const rows = await read();
                    await gate;
                    window.__served = true;
                    return rows;
                };
            }"""
        )
        panel.locator("#prks-offline-cache-refresh-btn").click()
        self.sign_out_elsewhere(page)
        page.evaluate("() => window.__release()")
        wait_for_async(
            page,
            "async () => { await new Promise((r) => setTimeout(r, 50)); return window.__served; }",
            message="the delayed review read never completed",
        )
        self.assertEqual(panel.locator('[data-prks-role="restored-change"]').count(), 0)
        self.assertEqual(panel.locator('[data-prks-role="restored-changes-heading"]').count(), 0)
        page.close()
        self.assert_kept_for_the_account()


if __name__ == "__main__":
    unittest.main()
