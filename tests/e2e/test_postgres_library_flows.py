"""PG-C1 (#640) browser flows on the PostgreSQL Library.

These run only on the PostgreSQL leg (``PRKS_E2E_LIBRARY_BACKEND=postgres``),
where every server serves its Library through the facade and runs its
embedded worker. They are deterministic: each waits on an observable state
(an answer, a stored row, a rendered element), never on a sleep.

- Restore recovery: Settings backs the Library up (the worker writes the
  archive) and downloads it; a later rename and a change still queued in the
  browser are then undone by restoring that file through Settings. After the
  reload the page binds the new epoch, the rename is gone, and the queued
  change was never applied: it waits for review.
- Processing queue: upload by the picker and by drop, a duplicate warning,
  the preview frame, a draft edit at its revision, a single import that
  opens its Work, a batch import, and remove.
- Research-ACK fence: a Research Note's ACK carries ``research_refs`` and
  fences the cached Research Graph; the Concept the note now names lists the
  Work once the worker's ``research.reindex_work`` has run.
"""
from __future__ import annotations

import json
import os
import unittest

import psycopg

from tests.e2e import test_offline as o
from tests.e2e.fixtures import (
    CONCEPT_UNVISITED_NAME,
    MINIMAL_PDF,
    WORK_A_TITLE,
    open_library,
    seed_concepts_library,
    seed_library,
)
from tests.e2e.harness import AppServer, open_app_page, require_chromium, wait_for_async
from tests.e2e.postgres_identity import postgres_library
from backend.storage.config import StorageConfig

GRAPH_CORE = "research-graph-core"


def load_tests(loader, standard_tests, pattern):
    # Opt-in (tests/e2e/run.py POSTGRES_LIBRARY_MODULE): the runner lists this
    # module only on the PostgreSQL leg, so it never reports a skip.
    if os.environ.get("PRKS_E2E") != "1" or not postgres_library():
        return unittest.TestSuite()
    return standard_tests


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close()
    _PW.stop()


QUEUE_FOLDER_TITLE = "E2E Queue Folder"


def seed_queue_library(storage_root: str) -> dict:
    """seed_library plus one folder the queue can file an import into."""
    ids = seed_library(storage_root)
    db = open_library(StorageConfig.for_testing(storage_root))
    ids["folder"] = db.add_folder(QUEUE_FOLDER_TITLE, "Imported Works land here.", None)
    return ids


def _start(test, seed_fn, service_workers="block"):
    server = AppServer(seed_fn=seed_fn)
    test.addCleanup(server.stop)
    server.start()
    page, context, collector = open_app_page(_BROWSER, server.origin, service_workers=service_workers)
    test.addCleanup(context.close)
    test.addCleanup(lambda: test.assertEqual(collector.pageerrors, []))
    return server, page, context


_API = """
    async ({ method, path, body }) => {
        const headers = { 'Content-Type': 'application/json' };
        if (method !== 'GET') Object.assign(headers, await prksLibraryEpochHeaders());
        const res = await fetch(path, { method, headers, body: body == null ? undefined : JSON.stringify(body) });
        let data = null;
        try { data = await res.json(); } catch (_) {}
        return { status: res.status, body: data };
    }
"""


def _api(page, method, path, body=None):
    return page.evaluate(_API, {"method": method, "path": path, "body": body})


class RestoreRecoveryTests(unittest.TestCase):
    QUEUED = "Typed after the backup"
    RENAMED = "Renamed after the backup"

    def open_settings_backup(self, page):
        page.locator("button.settings-btn").click()
        page.locator("#prks-settings-tab-backup").click()
        page.locator("#prks-backup-download-btn").wait_for()

    def test_a_restore_through_settings_undoes_later_changes_and_holds_queued_ones(self):
        server, page, context = _start(self, seed_queue_library)
        work_id = server.ids["work_a"]
        page.wait_for_function("() => !!window.prksSync && !!window.prksBrowserScope")
        page.evaluate("() => prksBrowserScope.resolve()")
        self.assertEqual(page.evaluate("() => prksBrowserScope.pageEpoch()"), "1")

        # Back up through Settings: the worker writes and verifies the archive.
        self.open_settings_backup(page)
        with page.expect_download(timeout=120000) as download_info:
            page.locator("#prks-backup-download-btn").click()
        archive = os.path.join(server.storage_root, os.pardir, "downloaded.prks-backup")
        download_info.value.save_as(archive)
        self.assertGreater(os.path.getsize(archive), 0)
        page.keyboard.press("Escape")

        # After the backup: a rename the server applies, and a change still queued.
        renamed = _api(page, "PATCH", "/api/works/%s" % work_id, {"title": self.RENAMED})
        self.assertEqual(renamed["status"], 200, renamed)
        page.route("**/api/sync/operations",
                   lambda route: route.fulfill(status=503, content_type="application/json", body="{}"))
        queued = page.evaluate(
            """async ({ workId, text }) => {
                const op = await prksSync.store.saveWorkNote(workId, 'SET_WORK_PRIVATE_NOTE', text, { value: '', revision: 0 });
                prksSync.changed();
                return op.op_id;
            }""",
            {"workId": work_id, "text": self.QUEUED},
        )
        wait_for_async(
            page,
            "async (id) => (await prksSync.store.listOperations()).some((r) => r.op_id === id && r.attempt_count >= 1)",
            arg=queued, message="the queued change was never attempted")

        # Restore the downloaded file through Settings. The page reloads itself.
        self.open_settings_backup(page)
        page.locator("#prks-backup-file-input").set_input_files(archive)
        page.locator("#prks-backup-verify-btn").click()
        page.locator("#prks-backup-verified-panel:not(.hidden)").wait_for(timeout=60000)
        self.assertIn("Backup verified", page.locator("#prks-backup-restore-status").inner_text())
        page.locator("#prks-backup-restore-btn").click()
        page.locator("#prks-restore-confirm-modal:not(.hidden)").wait_for()
        page.locator("#prks-restore-confirm-input").fill("RESTORE")
        with page.expect_navigation(timeout=120000):
            page.locator("#prks-restore-confirm-submit:not([disabled])").click()

        # The reloaded page binds the restored Library's epoch.
        page.wait_for_function("() => !!window.prksSync && !!window.prksBrowserScope")
        page.evaluate("() => prksBrowserScope.resolve()")
        self.assertEqual(page.evaluate("() => prksBrowserScope.pageEpoch()"), "2")
        # Sync stays held until the page has bound the new epoch: a retry
        # before the restore could otherwise deliver the queued change.
        page.unroute("**/api/sync/operations")
        work = _api(page, "GET", "/api/works/%s" % work_id)
        self.assertEqual(work["status"], 200, work)
        self.assertEqual(work["body"]["title"], WORK_A_TITLE, "the rename after the backup is gone")

        # The change queued before the restore is never sent: it waits for review.
        wait_for_async(
            page,
            """async (id) => {
                await prksSync.wake();
                const queued = await prksSync.store.listOperations();
                const restored = await prksSync.store.listRestored();
                return queued.length === 0 && restored.some((r) => r.op_id === id);
            }""",
            arg=queued, timeout=30000, message="the change from before the restore never reached review")
        notes = _api(page, "GET", "/api/works/%s" % work_id)["body"].get("private_notes")
        self.assertNotEqual(notes, self.QUEUED, "the queued change was applied to the restored Library")

        # Its text is offered in the review-only list in Settings -> Diagnostics.
        page.locator("button.settings-btn").click()
        page.locator("#prks-settings-tab-diagnostics").click()
        panel = page.locator("#prks-settings-panel-diagnostics")
        panel.locator("#prks-offline-cache-refresh-btn").click()
        row = panel.locator('[data-prks-role="restored-change"]')
        row.wait_for(timeout=20000)
        self.assertIn(self.QUEUED, row.inner_text())
        self.assertIn("before this library was restored",
                      panel.locator('[data-prks-role="restored-changes-heading"]').inner_text())
        row.get_by_role("button", name="Copy text", exact=True).wait_for()


class ProcessingQueueTests(unittest.TestCase):
    def open_queue(self, page):
        page.evaluate("h => prksNavigate(h)", "#/processing-files")
        page.locator("[data-prks-processing-queue]").wait_for()
        page.locator("[data-processing-list]").wait_for()

    def card(self, page, filename):
        return page.locator("[data-processing-item-id]", has_text=filename)

    def drop(self, page, filename, data):
        page.evaluate(
            """({ name, bytes }) => {
                const transfer = new DataTransfer();
                transfer.items.add(new File([new Uint8Array(bytes)], name, { type: 'application/pdf' }));
                const zone = document.querySelector('[data-processing-drop]');
                zone.dispatchEvent(new DragEvent('dragenter', { bubbles: true, cancelable: true, dataTransfer: transfer }));
                zone.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: transfer }));
            }""",
            {"name": filename, "bytes": list(data)},
        )

    def test_upload_preview_edit_import_and_remove(self):
        server, page, context = _start(self, seed_queue_library)
        pdf = MINIMAL_PDF.read_bytes()
        # Distinct bytes per file, so only the one the test means is a duplicate.
        first = pdf
        second = pdf + b"\n% second\n"
        third = pdf + b"\n% third\n"
        self.open_queue(page)
        page.locator("[data-processing-empty]").wait_for()

        # Upload by the picker: two files, one request each.
        page.locator("[data-processing-picker]").set_input_files([
            {"name": "queue-one.pdf", "mimeType": "application/pdf", "buffer": first},
            {"name": "queue-two.pdf", "mimeType": "application/pdf", "buffer": second},
        ])
        self.card(page, "queue-one.pdf").wait_for()
        self.card(page, "queue-two.pdf").wait_for()
        uploads = page.locator("[data-processing-uploads]")
        self.assertEqual(uploads.locator("li", has_text="Added to the queue.").count(), 2)

        # Upload by drop: the same bytes as queue-one.pdf are warned about, not refused.
        self.drop(page, "queue-three.pdf", third)
        self.card(page, "queue-three.pdf").wait_for()
        self.drop(page, "queue-one-again.pdf", first)
        uploads.locator('li[data-processing-upload-state="done"]', has_text="queue-one-again.pdf").wait_for()
        self.assertIn("queue-one.pdf", uploads.inner_text())
        self.card(page, "queue-one-again.pdf").wait_for()

        # A failed upload shows its message and never an item.
        page.route("**/api/processing-items", lambda route: route.fulfill(
            status=415, content_type="application/json",
            body=json.dumps({"error": "PROCESSING_NOT_A_PDF", "code": "PROCESSING_NOT_A_PDF",
                             "message": "The upload is not a PDF."}))
            if route.request.method == "POST" else route.fallback())
        page.locator("[data-processing-picker]").set_input_files(
            {"name": "queue-refused.pdf", "mimeType": "application/pdf", "buffer": pdf + b"\n% refused\n"})
        uploads.locator('li[data-processing-upload-state="failed"]', has_text="The upload is not a PDF.").wait_for()
        page.unroute("**/api/processing-items")
        self.assertEqual(self.card(page, "queue-refused.pdf").count(), 0)

        # Progress: a held upload shows its bar before the item appears.
        release = []
        page.route("**/api/processing-items", lambda route: release.append(route)
                   if route.request.method == "POST" else route.fallback())
        page.locator("[data-processing-picker]").set_input_files(
            {"name": "queue-progress.pdf", "mimeType": "application/pdf", "buffer": pdf + b"\n% progress\n"})
        uploads.locator('li[data-processing-upload-state="sending"] [data-processing-upload-progress]').wait_for()
        self.assertEqual(self.card(page, "queue-progress.pdf").count(), 0, "an item showed before the upload ended")
        for _ in range(200):  # each round trip lets Playwright run the pending route handler
            if release:
                break
            page.evaluate("() => 0")
        self.assertEqual(len(release), 1, "the upload never reached the held route")
        page.unroute("**/api/processing-items")
        release[0].continue_()
        self.card(page, "queue-progress.pdf").wait_for()
        self.card(page, "queue-progress.pdf").locator("[data-processing-remove]").click()
        self.card(page, "queue-progress.pdf").wait_for(state="detached")

        # Preview: read-only, in the PRKS PDF viewer, from the uploaded bytes.
        one = self.card(page, "queue-one.pdf").first
        with page.expect_response(lambda r: "/api/processing-items/" in r.url and "/pdf" in r.url) as preview:
            one.locator("[data-processing-preview]").click()
        self.assertIn(preview.value.status, (200, 206))
        one.locator('[data-processing-preview-host] .prks-pdf-page').first.wait_for(timeout=30000)
        self.assertEqual(one.locator("[data-processing-preview-error]").count(), 0)
        one.locator("[data-processing-preview]").click()
        one.locator("[data-processing-preview-host]").wait_for(state="detached")

        # Draft edit at its revision, kept across a reload.
        drafted = next(item for item in _api(page, "GET", "/api/processing-items")["body"]["items"]
                       if item["original_filename"] == "queue-one.pdf")
        one.locator('[data-field="title"]').fill("Queue One Title")
        one.locator('[data-field="year"]').fill("2001")
        one.locator('[data-field="target_folder_id"]').select_option(server.ids["folder"])
        status = one.locator("[data-processing-save-status]")
        self.assertIn("Unsaved changes", status.inner_text())
        with page.expect_response(lambda r: r.request.method == "PATCH" and "/api/processing-items/" in r.url) as saved:
            one.locator("[data-processing-save]").click()
        status.filter(has_text="Draft saved.").wait_for()
        self.assertEqual(saved.value.status, 200)
        self.assertEqual(saved.value.json()["revision"], drafted["revision"] + 1)
        self.assertEqual(saved.value.json()["draft"]["target_folder_id"], server.ids["folder"])
        page.reload()
        self.open_queue(page)
        one = self.card(page, "queue-one.pdf").first
        one.wait_for()
        self.assertEqual(one.locator('[data-field="title"]').input_value(), "Queue One Title")

        # Single import: the item leaves the queue and its Work opens.
        one.locator("[data-processing-import]").click()
        link = page.locator("[data-processing-imported] a", has_text="queue-one.pdf")
        link.wait_for()
        self.assertEqual(self.card(page, "queue-one.pdf").count(), 0)
        work_id = link.get_attribute("href").rsplit("/", 1)[-1]
        work = _api(page, "GET", "/api/works/%s" % work_id)
        self.assertEqual((work["status"], work["body"]["title"], work["body"]["year"]), (200, "Queue One Title", "2001"))

        # Batch import: two selected items, a result each.
        for name in ("queue-two.pdf", "queue-three.pdf"):
            self.card(page, name).locator("[data-processing-select]").check()
        page.locator("[data-processing-import-selected]").click()
        page.locator("[data-processing-imported] a", has_text="queue-three.pdf").wait_for()
        page.locator("[data-processing-imported] a", has_text="queue-two.pdf").wait_for()
        self.assertEqual(self.card(page, "queue-two.pdf").count(), 0)

        # Remove the duplicate: it is gone from the queue, also after a reload.
        self.card(page, "queue-one-again.pdf").locator("[data-processing-remove]").click()
        self.card(page, "queue-one-again.pdf").wait_for(state="detached")
        page.reload()
        self.open_queue(page)
        page.locator("[data-processing-empty]").wait_for()
        listed = _api(page, "GET", "/api/processing-items")
        self.assertEqual((listed["status"], listed["body"]["items"]), (200, []))


class ResearchAckFenceTests(unittest.TestCase):
    """The ACK says the references are pending while the reindex is held (§8.4).

    The test holds the Work's reindex key (the advisory lock
    ``research.reindex_work`` takes first) in its own transaction, so the job
    cannot finish before the ACK is read; it then releases the key. No sleep
    decides anything.
    """

    def hold_reindex(self, server, work_id):
        conn = psycopg.connect(server.library.conninfo)
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended("
            "'prks.research_reindex:' || %s || ':' || %s, 0))",
            (str(server.library.library_id), str(work_id)),
        )
        self.addCleanup(conn.close)
        return conn

    def test_a_research_note_ack_is_pending_until_the_reindex_completes(self):
        server, page, context = _start(self, seed_concepts_library, service_workers="allow")
        ids = server.ids
        work_id = ids["work_a"]
        o._wait_sw_active(page)

        # Warm the cached Research Graph core snapshot.
        page.evaluate("h => prksNavigate(h)", "#/graph")
        page.wait_for_function("() => { const d = prksGetResearchGraphDebug(); return !!(d && d.cy); }")
        o._wait_entity_cached(page, GRAPH_CORE, "snapshot")
        before = o._domain_generation(page, GRAPH_CORE)

        acks = []

        def on_response(response):
            request = response.request
            if request.method == "POST" and request.url.endswith("/api/sync/operations") and response.status == 200:
                try:
                    body = response.json()
                except Exception:  # an unreadable answer is not an ACK
                    return
                if isinstance(body, dict) and isinstance(body.get("research_refs"), dict):
                    acks.append(body)

        page.on("response", on_response)

        # Name the Concept that no note mentioned yet, while its reindex is held.
        o._open_work_from_home(page, WORK_A_TITLE)
        held = self.hold_reindex(server, work_id)
        page.locator(".work-notes-editor-wrap .CodeMirror").first.click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text("Fenced research note.\n\n[[concept:%s]]\n" % CONCEPT_UNVISITED_NAME)
        page.evaluate("() => window.prksFlushPendingWorkResearchNotes(window.prksGetFocusedTabContext())")
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=40000, message="the Research Note was never acknowledged")
        page.locator('[data-prks-role="editor-status"]', has_text="All changes saved").wait_for()

        # The ACK: references pending, and the cached graph is fenced.
        self.assertEqual(len(acks), 1, acks)
        self.assertIs(acks[0]["research_refs"]["pending"], True, acks[0])
        page.wait_for_function("x => prksOfflineDomainGeneration(x[0]) > x[1]", arg=[GRAPH_CORE, before])
        o._wait_entity_uncached(page, GRAPH_CORE, "snapshot")
        held_refs = _api(page, "GET", "/api/works/%s" % work_id)["body"]["research_refs"]
        self.assertIs(held_refs["pending"], True, "the reindex finished while its key was held")

        # Release the reindex: the references settle and name the new Concept.
        held.commit()
        wait_for_async(
            page,
            """async (workId) => {
                const res = await fetch('/api/works/' + encodeURIComponent(workId));
                if (!res.ok) return false;
                const refs = (await res.json()).research_refs;
                return !!refs && refs.pending === false;
            }""",
            arg=work_id, timeout=60000, message="the references stayed pending after the reindex was released")
        refs = _api(page, "GET", "/api/works/%s" % work_id)["body"]["research_refs"]
        self.assertIn(ids["concept_unvisited"], json.dumps(refs["concepts"]), refs)
        self.assertNotIn(ids["concept_child"], json.dumps(refs["concepts"]), "the old mention stayed")

        # The Concept the note now names lists the Work.
        page.evaluate("h => prksNavigate(h)", "#/concepts/%s" % ids["concept_unvisited"])
        page.locator('a[href="#/works/%s"]' % work_id).first.wait_for()


if __name__ == "__main__":
    unittest.main()
