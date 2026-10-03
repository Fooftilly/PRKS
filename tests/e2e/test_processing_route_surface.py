"""Processing Files route surface opens #/processing-files in the main pane.

Preview lifetime and owner isolation stay in the Processing Vitest files.
This module mounts the page from the route and runs one save and import
through the typed client against the real server.
"""
from __future__ import annotations

import os
import shutil
import unittest

from backend.storage.config import StorageConfig
from tests.e2e.fixtures import MINIMAL_PDF, seed_library
from tests.e2e.harness import AppServer, open_app_page, require_chromium


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get("PRKS_E2E") == "1" else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close()
    _PW.stop()


def _seed_with_inbox_file(storage_root: str) -> dict:
    ids = seed_library(storage_root)
    processing_dir = StorageConfig.for_testing(storage_root).processing_dir
    os.makedirs(processing_dir, exist_ok=True)
    shutil.copy2(str(MINIMAL_PDF), os.path.join(processing_dir, "e2e-inbox.pdf"))
    return ids


class ProcessingRouteSurfaceTests(unittest.TestCase):
    def start(self, seed_fn=seed_library):
        server = AppServer(seed_fn=seed_fn)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(
            _BROWSER, server.origin, service_workers="allow"
        )
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        return page

    def test_processing_files_page_mounts_from_the_route(self):
        page = self.start()
        page.evaluate("() => prksNavigate('#/processing-files')")
        page.wait_for_selector(
            ".prks-tile--main [data-prks-processing-page]",
            timeout=15000,
        )
        title = page.locator(".prks-tile--main .prks-page-title").inner_text()
        self.assertEqual(title, "Files for Processing")
        self.assertEqual(
            page.locator(".prks-tile--main [data-prks-processing-page]").count(),
            1,
        )

    def test_save_and_import_go_through_the_typed_client(self):
        page = self.start(_seed_with_inbox_file)
        page.evaluate("() => prksNavigate('#/processing-files')")
        card = page.locator(".prks-tile--main .prks-processing-card", has_text="e2e-inbox.pdf")
        card.wait_for(timeout=15000)
        card.locator("input[data-field='title']").fill("E2E Inbox Import")
        with page.expect_response(
            lambda res: res.request.method == "PATCH" and "/api/processing-files/" in res.url
        ) as saved:
            card.get_by_role("button", name="Save metadata").click()
        self.assertEqual(saved.value.status, 200)
        card.locator(".prks-processing-card__message", has_text="Saved.").wait_for(timeout=15000)
        with page.expect_response(
            lambda res: res.request.method == "POST" and res.url.endswith("/import")
        ) as imported:
            card.get_by_role("button", name="Import to library").click()
        self.assertEqual(imported.value.status, 200)
        work_id = imported.value.json()["work_id"]
        page.wait_for_selector(
            ".prks-tile--main .prks-processing-card", state="detached", timeout=15000
        )
        inbox = page.evaluate(
            "async () => (await fetch('/api/processing-files')).json()"
        )
        self.assertEqual(inbox, [])
        work = page.evaluate(
            "async (id) => (await fetch('/api/works/' + encodeURIComponent(id))).json()",
            work_id,
        )
        self.assertEqual(work["title"], "E2E Inbox Import")
