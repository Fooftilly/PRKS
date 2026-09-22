"""Library Navigation V1 — hierarchical Folder switcher on Folder detail."""
from __future__ import annotations

import os
import unittest

from tests.e2e.fixtures import (
    LIBRARY_NAV_AI,
    LIBRARY_NAV_EPISTEMOLOGY,
    LIBRARY_NAV_ETHICS,
    LIBRARY_NAV_PHILOSOPHY,
    seed_library_nav_folders,
)
from tests.e2e.harness import AppServer, open_app_page, require_chromium, wait_for_async


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get("PRKS_E2E") == "1" else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close()
    _PW.stop()


class LibraryNavFolderSwitcherTests(unittest.TestCase):
    def start(self):
        server = AppServer(seed_fn=seed_library_nav_folders)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin, service_workers="allow")
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        return server, page, server.ids

    def open_folder(self, page, folder_id):
        page.evaluate(
            "id => prksNavigate('#/folders/' + encodeURIComponent(id))",
            folder_id,
        )
        page.wait_for_selector("#prks-folder-nav-trigger", timeout=15000)

    def open_switcher(self, page):
        trigger = page.locator("#prks-folder-nav-trigger")
        trigger.click()
        page.wait_for_selector("#prks-folder-nav-panel:not([hidden])", timeout=10000)
        # Hierarchy load finishes when options (or empty copy) appear.
        page.wait_for_function(
            """() => {
                const panel = document.getElementById('prks-folder-nav-panel');
                if (!panel || panel.hidden) return false;
                return !!panel.querySelector('[role="option"], .prks-folder-nav__empty');
            }""",
            timeout=10000,
        )
        return trigger

    def select_option_by_title(self, page, title):
        opt = page.locator('#prks-folder-nav-panel [role="option"]').filter(has_text=title).first
        opt.click()

    def test_nearby_parent_sibling_and_cross_branch_filter(self):
        _server, page, ids = self.start()
        self.open_folder(page, ids["ethics"])
        trigger = page.locator("#prks-folder-nav-trigger")
        self.assertIn(LIBRARY_NAV_ETHICS, trigger.inner_text())

        self.open_switcher(page)
        panel = page.locator("#prks-folder-nav-panel")
        text = panel.inner_text()
        self.assertIn(LIBRARY_NAV_PHILOSOPHY, text)
        self.assertIn(LIBRARY_NAV_EPISTEMOLOGY, text)
        self.assertIn("Current", text)

        # Parent without visiting Folder index.
        self.select_option_by_title(page, LIBRARY_NAV_PHILOSOPHY)
        page.wait_for_function(
            "id => location.hash === '#/folders/' + encodeURIComponent(id)",
            arg=ids["philosophy"],
            timeout=10000,
        )
        page.wait_for_selector("#prks-folder-nav-trigger", timeout=10000)
        self.assertIn(LIBRARY_NAV_PHILOSOPHY, page.locator(".prks-page-title").inner_text())

        # Sibling of Ethics from Philosophy → Epistemology via children list.
        self.open_switcher(page)
        self.select_option_by_title(page, LIBRARY_NAV_EPISTEMOLOGY)
        page.wait_for_function(
            "id => location.hash === '#/folders/' + encodeURIComponent(id)",
            arg=ids["epistemology"],
            timeout=10000,
        )

        # Cross-branch: Epistemology → AI via filter (no Folder index).
        self.open_switcher(page)
        page.fill("#prks-folder-nav-filter", "AI")
        page.wait_for_function(
            """() => {
                const opts = [...document.querySelectorAll('#prks-folder-nav-panel [role="option"]')];
                return opts.some(o => (o.textContent || '').includes('AI'));
            }""",
            timeout=5000,
        )
        self.select_option_by_title(page, LIBRARY_NAV_AI)
        page.wait_for_function(
            "id => location.hash === '#/folders/' + encodeURIComponent(id)",
            arg=ids["ai"],
            timeout=10000,
        )
        self.assertIn(LIBRARY_NAV_AI, page.locator(".prks-page-title").inner_text())

    def test_keyboard_open_nav_select_escape_restores_focus(self):
        _server, page, ids = self.start()
        self.open_folder(page, ids["ethics"])
        trigger = page.locator("#prks-folder-nav-trigger")
        trigger.focus()
        page.keyboard.press("Enter")
        page.wait_for_selector("#prks-folder-nav-panel:not([hidden])", timeout=10000)
        page.wait_for_selector("#prks-folder-nav-filter", timeout=10000)
        # Move into options and Escape restores the trigger.
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Escape")
        page.wait_for_selector("#prks-folder-nav-panel[hidden]", timeout=5000)
        focused = page.evaluate("() => document.activeElement && document.activeElement.id")
        self.assertEqual(focused, "prks-folder-nav-trigger")

        # Re-open and select Epistemology with keyboard from Ethics' sibling list.
        page.keyboard.press("Enter")
        page.wait_for_selector("#prks-folder-nav-panel:not([hidden])", timeout=10000)
        page.wait_for_function(
            """() => {
                const panel = document.getElementById('prks-folder-nav-panel');
                return panel && !panel.hidden && panel.querySelectorAll('[role="option"]').length > 0;
            }""",
            timeout=10000,
        )
        # Focus filter → ArrowDown into list; walk until Epistemology is active.
        page.focus("#prks-folder-nav-filter")
        found = False
        for _ in range(12):
            page.keyboard.press("ArrowDown")
            label = page.evaluate(
                """() => {
                    const el = document.querySelector('#prks-folder-nav-panel [role="option"].is-active');
                    return el ? (el.textContent || '') : '';
                }"""
            )
            if LIBRARY_NAV_EPISTEMOLOGY in label:
                found = True
                break
        self.assertTrue(found, "Epistemology option should become active")
        page.keyboard.press("Enter")
        page.wait_for_function(
            "id => location.hash === '#/folders/' + encodeURIComponent(id)",
            arg=ids["epistemology"],
            timeout=10000,
        )

    def test_switcher_targets_owning_workspace_tab(self):
        _server, page, ids = self.start()
        self.open_folder(page, ids["ethics"])
        # Open Philosophy in a background tab, then ensure switcher on Ethics tab
        # keeps navigating the focused/owning tab.
        page.evaluate(
            """(id) => prksNavigate('#/folders/' + encodeURIComponent(id), { target: 'new-tab', activate: false })""",
            ids["philosophy"],
        )
        snap = page.evaluate(
            """() => {
                const s = prksWorkspaceSnapshot();
                return {
                    focused: s && s.focusedTabId,
                    main: s && s.mainTabId,
                    routes: (s && s.tabs || []).map(t => ({ id: t.id, route: t.route })),
                };
            }"""
        )
        ethics_tab = next(
            (t for t in snap["routes"] if t["route"] and ids["ethics"] in t["route"]),
            None,
        )
        self.assertIsNotNone(ethics_tab)
        page.evaluate("id => prksWorkspaceActivateTab(id)", ethics_tab["id"])
        page.wait_for_selector("#prks-folder-nav-trigger", timeout=10000)

        owner_before = page.evaluate(
            """() => {
                const ctx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
                return ctx && ctx.tabId;
            }"""
        )
        self.open_switcher(page)
        self.select_option_by_title(page, LIBRARY_NAV_EPISTEMOLOGY)
        page.wait_for_function(
            "id => location.hash === '#/folders/' + encodeURIComponent(id)",
            arg=ids["epistemology"],
            timeout=10000,
        )
        owner_after = page.evaluate(
            """() => {
                const ctx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
                const s = prksWorkspaceSnapshot();
                const tab = (s && s.tabs || []).find(t => t.id === (ctx && ctx.tabId));
                return { tabId: ctx && ctx.tabId, route: tab && tab.route };
            }"""
        )
        self.assertEqual(owner_after["tabId"], owner_before)
        self.assertIn(ids["epistemology"], owner_after["route"] or "")

    def test_one_hierarchy_fetch_not_per_row(self):
        _server, page, ids = self.start()
        # Warm the catalog once, then open switcher and assert no burst of /api/folders/:id.
        page.evaluate("() => prksNavigate('#/folders')")
        wait_for_async(
            page,
            """async () => {
                const rows = await (window.prksOfflinePeekList
                    ? window.prksOfflinePeekList('folders:index')
                    : null);
                if (rows) return true;
                // Fall back: list route painted.
                return !!document.querySelector('.prks-folder-library, .prks-folder-tree');
            }""",
            timeout=15000,
        )
        self.open_folder(page, ids["ethics"])
        counts = {"folders_index": 0, "folder_detail": 0}

        def on_request(req):
            url = req.url or ""
            if "/api/folders/" in url and not url.rstrip("/").endswith("/api/folders"):
                # detail or sub-resource
                if "/tag-options" in url or "/sync-state" in url:
                    return
                counts["folder_detail"] += 1
            elif url.rstrip("/").endswith("/api/folders") or "/api/folders?" in url:
                counts["folders_index"] += 1

        page.on("request", on_request)
        before_detail = counts["folder_detail"]
        self.open_switcher(page)
        page.wait_for_timeout(400)  # settle window: absence of per-row amplification
        # Opening the switcher must not issue one GET per sibling/child row.
        self.assertLessEqual(counts["folder_detail"] - before_detail, 1)
        # At most one catalog refresh is acceptable; never N for N rows.
        self.assertLessEqual(counts["folders_index"], 2)
