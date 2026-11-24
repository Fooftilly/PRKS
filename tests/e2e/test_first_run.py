"""First-run setup in the browser (#612), end to end.

The server starts on a migrated database with no instance, so it serves the
setup page instead of the Library. The browser walks the three steps with a
token issued as ``prks_app.py admin bootstrap issue-token`` would, lands in
the empty Library signed in as the new owner, and then:

- a reload, a restart and a second tab never show setup again;
- a new browser (no session) gets the sign-in dialog, not setup, and the
  owner signs in there with the password chosen during setup.

The 409 for a second setup request is covered without a browser in
``tests/pg/test_readiness.py``.

A server whose folder is still choosable (#720: selected by a config file,
not by PRKS_STORAGE) offers another folder on the token step. Choosing one
asks for a restart; after it, PRKS runs on the chosen folder, setup finishes
there with the same token, and the folder can no longer be changed.
"""
from __future__ import annotations

import json
import os
import unittest

from tests.e2e.harness import AppServer, PageCollector, require_chromium
from tests.e2e.postgres_identity import issue_setup_token

OWNER_LOGIN = "first-owner"
OWNER_PASSWORD = "first run owner passphrase"
LIBRARY_NAME = "First Library"


def load_tests(loader, standard_tests, pattern):
    if os.environ.get("PRKS_E2E") != "1":
        return unittest.TestSuite()
    return standard_tests


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close()
    _PW.stop()


class FirstRunSetupTests(unittest.TestCase):
    def setUp(self):
        self.server = AppServer(first_run=True)
        self.addCleanup(self.server.stop_later)
        self.server.start()

    def new_page(self, context):
        page = context.new_page()
        collector = PageCollector(page, self.server.origin)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        return page

    def new_context(self):
        context = _BROWSER.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
        self.addCleanup(context.close)
        return context

    def setup_state(self, context):
        response = context.request.get(self.server.origin + "/api/setup")
        self.assertEqual(response.status, 200, response.text())
        return response.json()["state"]

    def open_library(self, page):
        page.goto(self.server.origin + "/#/folders", wait_until="domcontentloaded")
        page.wait_for_selector(".prks-folder-library")
        self.assertEqual(page.locator("#prks-setup-heading").count(), 0)
        self.assertEqual(page.locator("#prks-sign-in").count(), 0)

    def test_setup_in_the_browser_then_restart_and_a_new_browser_sign_in(self):
        context = self.new_context()
        self.assertEqual(self.setup_state(context), "unconfigured")
        # Every other route waits for setup.
        self.assertEqual(context.request.get(self.server.origin + "/api/works").status, 503)

        page = self.new_page(context)
        page.goto(self.server.origin + "/", wait_until="domcontentloaded")
        heading = page.locator("#prks-setup-heading")
        heading.wait_for()
        self.assertEqual(heading.inner_text(), "Set up PRKS")
        # A testing run's folder is set on the host (PRKS_STORAGE), so the page
        # offers no folder choice (#720).
        self.assertEqual(context.request.get(self.server.origin + "/api/setup").json()["storage"], "fixed")
        self.assertEqual(page.locator("#prks-setup-folder").count(), 0)

        assert self.server.library is not None
        page.fill("#prks-setup-token", issue_setup_token(self.server.library))
        page.get_by_role("button", name="Continue").click()
        page.get_by_role("heading", name="Owner and Library").wait_for()
        page.fill("#prks-setup-login", OWNER_LOGIN)
        page.fill("#prks-setup-password", OWNER_PASSWORD)
        page.fill("#prks-setup-password-again", OWNER_PASSWORD)
        page.fill("#prks-setup-library", LIBRARY_NAME)
        page.get_by_role("button", name="Continue").click()
        page.get_by_role("heading", name="Review").wait_for()
        page.get_by_role("button", name="Finish setup").click()

        # Setup signs the owner in and hands over to the Library.
        page.wait_for_selector(".prks-folder-library", timeout=30000)
        self.assertEqual(self.setup_state(context), "ready")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".prks-folder-library")

        # A second tab of the same browser goes straight to the Library.
        self.open_library(self.new_page(context))

        # A restart serves the Library, not setup; the session survives it.
        self.server._release_process()
        self.server.start()
        self.assertEqual(self.setup_state(context), "ready")
        self.open_library(self.new_page(context))

        # A new browser is a new device: it signs in, it never sees setup.
        other = self.new_context()
        page = self.new_page(other)
        page.goto(self.server.origin + "/", wait_until="domcontentloaded")
        page.wait_for_selector("#prks-sign-in")
        self.assertEqual(page.locator("#prks-setup-heading").count(), 0)
        page.fill("#prks-sign-in-login", OWNER_LOGIN)
        page.fill("#prks-sign-in-password", OWNER_PASSWORD)
        with page.expect_navigation(wait_until="domcontentloaded"):
            page.click("#prks-sign-in button[type=submit]")
        page.wait_for_selector(".prks-folder-library")
        self.assertEqual(page.locator("#prks-sign-in").count(), 0)
        self.open_library(self.new_page(other))


class ChooseFolderSetupTests(unittest.TestCase):
    def setUp(self):
        self.server = AppServer(first_run=True, choosable=True)
        self.addCleanup(self.server.stop_later)
        self.server.start()

    def setup_status(self, context):
        response = context.request.get(self.server.origin + "/api/setup")
        self.assertEqual(response.status, 200, response.text())
        return response.json()

    def test_choose_a_folder_restart_and_finish_setup_on_it(self):
        assert self.server.library is not None
        chosen = os.path.join(os.path.dirname(self.server.storage_root), "chosen-library")
        context = _BROWSER.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
        self.addCleanup(context.close)
        page = context.new_page()
        collector = PageCollector(page, self.server.origin)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        self.assertEqual(self.setup_status(context)["storage"], "choosable")

        page.goto(self.server.origin + "/", wait_until="domcontentloaded")
        page.locator("#prks-setup-heading").wait_for()
        token = issue_setup_token(self.server.library)
        page.fill("#prks-setup-token", token)
        page.click("#prks-setup-folder summary")
        page.fill("#prks-setup-folder-path", chosen)
        page.get_by_role("button", name="Use this folder").click()

        page.get_by_role("heading", name="Restart PRKS").wait_for()
        self.assertEqual(page.locator("#prks-setup-chosen-folder").inner_text(), chosen)
        # The choice is recorded for the next start; this process keeps its folder.
        self.assertEqual(self.setup_status(context)["storage"], "restart_required")
        with open(self.server.config_file, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["storage"]["local_root"], chosen)
        page.reload(wait_until="domcontentloaded")
        page.get_by_role("heading", name="Restart PRKS").wait_for()

        self.server._release_process()
        self.server.start()
        status = self.setup_status(context)
        self.assertEqual((status["state"], status["storage"]), ("unconfigured", "choosable"))

        # The same token finishes setup on the chosen folder.
        page.reload(wait_until="domcontentloaded")
        page.fill("#prks-setup-token", token)
        page.get_by_role("button", name="Continue").click()
        page.get_by_role("heading", name="Owner and Library").wait_for()
        page.fill("#prks-setup-login", OWNER_LOGIN)
        page.fill("#prks-setup-password", OWNER_PASSWORD)
        page.fill("#prks-setup-password-again", OWNER_PASSWORD)
        page.fill("#prks-setup-library", LIBRARY_NAME)
        page.get_by_role("button", name="Continue").click()
        page.get_by_role("heading", name="Review").wait_for()
        page.get_by_role("button", name="Finish setup").click()
        page.wait_for_selector(".prks-folder-library", timeout=30000)

        # Setup claimed the chosen folder, so it can no longer change.
        self.assertEqual(self.setup_status(context), {"state": "ready", "storage": "fixed"})
        self.assertTrue(os.path.isfile(os.path.join(chosen, "prks-root.json")))


if __name__ == "__main__":
    unittest.main()
