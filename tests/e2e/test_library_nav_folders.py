"""Library Navigation V1 — hierarchical Folder switcher on Folder detail."""
from __future__ import annotations

import os
import unittest

from tests.e2e.fixtures import (
    LIBRARY_NAV_AI,
    LIBRARY_NAV_EPISTEMOLOGY,
    LIBRARY_NAV_ETHICS,
    LIBRARY_NAV_PHILOSOPHY,
    LIBRARY_NAV_RESEARCH,
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

    def trigger_in(self, page, scope: str):
        # Scope must be a single ancestor selector (no commas): Playwright/CSS
        # would otherwise treat ".tile, #page .trigger" as matching the tile itself.
        return page.locator(f"{scope} .prks-folder-nav__trigger").first

    def open_folder(self, page, folder_id, *, scope=".prks-tile--main"):
        page.evaluate(
            "id => prksNavigate('#/folders/' + encodeURIComponent(id))",
            folder_id,
        )
        page.wait_for_selector(f"{scope} .prks-folder-nav__trigger", timeout=15000)

    def open_switcher(self, page, *, scope=".prks-tile--main"):
        trigger = self.trigger_in(page, scope)
        trigger.click()
        page.wait_for_selector("#prks-folder-nav-panel:not([hidden])", timeout=10000)
        page.wait_for_function(
            """() => {
                const panel = document.getElementById('prks-folder-nav-panel');
                if (!panel || panel.hidden) return false;
                const list = panel.querySelector('#prks-folder-nav-listbox[role="listbox"]');
                if (!list) return false;
                return !!list.querySelector('[role="option"], .prks-folder-nav__empty');
            }""",
            timeout=10000,
        )
        return trigger

    def select_option_by_title(self, page, title):
        opt = page.locator(
            '#prks-folder-nav-listbox [role="option"] .prks-folder-nav__option-title',
            has_text=title,
        ).first
        opt.click()

    def wait_folder_title(self, page, title, folder_id=None, *, scope=".prks-tile--main"):
        if folder_id is not None and scope == ".prks-tile--main":
            page.wait_for_function(
                "id => location.hash === '#/folders/' + encodeURIComponent(id)",
                arg=folder_id,
                timeout=10000,
            )
        page.wait_for_function(
            """({ title, scope }) => {
                const el = document.querySelector(scope + ' .prks-page-title');
                return el && (el.textContent || '').includes(title);
            }""",
            arg={"title": title, "scope": scope},
            timeout=10000,
        )
        page.wait_for_selector(f"{scope} .prks-folder-nav__trigger", timeout=10000)

    def test_nearby_parent_sibling_and_cross_branch_filter(self):
        _server, page, ids = self.start()
        self.open_folder(page, ids["ethics"])
        trigger = self.trigger_in(page, ".prks-tile--main")
        band = page.locator(".prks-tile--main .prks-folder-nav").first
        page.wait_for_function(
            """() => {
                const crumbs = document.querySelector(
                    '.prks-tile--main [data-prks-role="folder-nav-crumbs"]'
                );
                return crumbs && (crumbs.textContent || '').includes('Research');
            }""",
            timeout=10000,
        )
        band_text = band.inner_text()
        self.assertIn("Location", band_text)
        self.assertIn(LIBRARY_NAV_RESEARCH, band_text)
        self.assertIn(LIBRARY_NAV_PHILOSOPHY, band_text)
        self.assertIn(LIBRARY_NAV_ETHICS, band_text)
        self.assertIn(LIBRARY_NAV_EPISTEMOLOGY, band_text)
        self.assertIn("Browse hierarchy", trigger.inner_text())

        self.open_switcher(page)
        panel = page.locator("#prks-folder-nav-panel")
        self.assertEqual(panel.get_attribute("role"), "dialog")
        listbox = page.locator("#prks-folder-nav-listbox")
        self.assertEqual(listbox.get_attribute("role"), "listbox")
        # Filter must live outside the listbox (valid dialog + listbox ARIA).
        self.assertEqual(
            page.evaluate(
                """() => {
                    const list = document.getElementById('prks-folder-nav-listbox');
                    const filter = document.getElementById('prks-folder-nav-filter');
                    return !!(list && filter && !list.contains(filter)
                        && filter.closest('#prks-folder-nav-panel'));
                }"""
            ),
            True,
        )
        text = panel.inner_text()
        self.assertIn(LIBRARY_NAV_PHILOSOPHY, text)
        self.assertIn(LIBRARY_NAV_EPISTEMOLOGY, text)
        self.assertIn("Current", text)

        self.select_option_by_title(page, LIBRARY_NAV_PHILOSOPHY)
        self.wait_folder_title(page, LIBRARY_NAV_PHILOSOPHY, ids["philosophy"])

        self.open_switcher(page)
        self.select_option_by_title(page, LIBRARY_NAV_EPISTEMOLOGY)
        self.wait_folder_title(page, LIBRARY_NAV_EPISTEMOLOGY, ids["epistemology"])

        self.open_switcher(page)
        page.fill("#prks-folder-nav-filter", "AI")
        page.wait_for_function(
            """() => {
                const opts = [...document.querySelectorAll('#prks-folder-nav-listbox [role="option"]')];
                return opts.some(o => (o.textContent || '').includes('AI'));
            }""",
            timeout=5000,
        )
        self.select_option_by_title(page, LIBRARY_NAV_AI)
        self.wait_folder_title(page, LIBRARY_NAV_AI, ids["ai"])

    def test_band_crumb_and_nearby_chip_navigate_without_popover(self):
        """Location crumbs and Nearby chips switch Folders without opening Browse hierarchy."""
        _server, page, ids = self.start()
        self.open_folder(page, ids["ethics"])
        page.wait_for_function(
            """() => {
                const nearby = document.querySelector(
                    '.prks-tile--main [data-prks-role="folder-nav-nearby"]'
                );
                return nearby && !nearby.hidden
                    && (nearby.textContent || '').includes('Epistemology');
            }""",
            timeout=10000,
        )
        # Sibling chip → Epistemology
        page.locator(
            '.prks-tile--main .prks-folder-nav__chip[data-prks-folder-nav-goto]',
            has_text=LIBRARY_NAV_EPISTEMOLOGY,
        ).first.click()
        self.wait_folder_title(page, LIBRARY_NAV_EPISTEMOLOGY, ids["epistemology"])
        self.assertTrue(
            page.evaluate(
                """() => {
                    const p = document.getElementById('prks-folder-nav-panel');
                    return !p || p.hidden === true;
                }"""
            )
        )
        # Ancestor crumb → Philosophy
        page.locator(
            '.prks-tile--main .prks-folder-nav__crumb[data-prks-folder-nav-goto]',
            has_text=LIBRARY_NAV_PHILOSOPHY,
        ).first.click()
        self.wait_folder_title(page, LIBRARY_NAV_PHILOSOPHY, ids["philosophy"])
        # Inside chip → Ethics
        page.wait_for_function(
            """() => {
                const nearby = document.querySelector(
                    '.prks-tile--main [data-prks-role="folder-nav-nearby"]'
                );
                return nearby && (nearby.textContent || '').includes('Ethics');
            }""",
            timeout=10000,
        )
        page.locator(
            '.prks-tile--main .prks-folder-nav__chip[data-prks-folder-nav-goto]',
            has_text=LIBRARY_NAV_ETHICS,
        ).first.click()
        self.wait_folder_title(page, LIBRARY_NAV_ETHICS, ids["ethics"])

    def test_keyboard_open_nav_select_escape_restores_focus(self):
        _server, page, ids = self.start()
        self.open_folder(page, ids["ethics"])
        trigger = self.trigger_in(page, ".prks-tile--main")
        trigger.focus()
        page.keyboard.press("Enter")
        page.wait_for_selector("#prks-folder-nav-panel:not([hidden])", timeout=10000)
        page.wait_for_selector("#prks-folder-nav-filter", timeout=10000)
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Escape")
        page.wait_for_function(
            """() => {
                const p = document.getElementById('prks-folder-nav-panel');
                return !p || p.hidden === true;
            }""",
            timeout=5000,
        )
        focused = page.evaluate(
            """() => {
                const el = document.activeElement;
                return el && el.classList && el.classList.contains('prks-folder-nav__trigger');
            }"""
        )
        self.assertTrue(focused)

        page.keyboard.press("Enter")
        page.wait_for_selector("#prks-folder-nav-panel:not([hidden])", timeout=10000)
        page.wait_for_function(
            """() => {
                const list = document.getElementById('prks-folder-nav-listbox');
                return list && list.querySelectorAll('[role="option"]').length > 0;
            }""",
            timeout=10000,
        )
        page.focus("#prks-folder-nav-filter")
        found = False
        for _ in range(12):
            page.keyboard.press("ArrowDown")
            label = page.evaluate(
                """() => {
                    const el = document.querySelector('#prks-folder-nav-listbox [role="option"].is-active');
                    return el ? (el.textContent || '') : '';
                }"""
            )
            if LIBRARY_NAV_EPISTEMOLOGY in label:
                found = True
                break
        self.assertTrue(found, "Epistemology option should become active")
        page.keyboard.press("Enter")
        self.wait_folder_title(page, LIBRARY_NAV_EPISTEMOLOGY, ids["epistemology"])

    def test_switcher_targets_owning_workspace_tab(self):
        _server, page, ids = self.start()
        self.open_folder(page, ids["ethics"])
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
        page.wait_for_selector(".prks-folder-nav__trigger", timeout=10000)

        owner_before = page.evaluate(
            """() => {
                const ctx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
                return ctx && ctx.tabId;
            }"""
        )
        self.open_switcher(page)
        self.select_option_by_title(page, LIBRARY_NAV_EPISTEMOLOGY)
        self.wait_folder_title(page, LIBRARY_NAV_EPISTEMOLOGY, ids["epistemology"])
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

    def test_two_visible_folder_panes_keep_instance_local_ownership(self):
        """Two tiled Folder details: each switcher navigates only its own TabContext."""
        _server, page, ids = self.start()
        self.open_folder(page, ids["ethics"])
        page.evaluate(
            "id => prksNavigate('#/folders/' + encodeURIComponent(id), { target: 'tile' })",
            ids["philosophy"],
        )
        page.wait_for_function(
            """() => {
                const s = prksWorkspaceSnapshot();
                return !!(s && s.mode === 'tiled' && s.secondaryTree && s.secondaryTree.type === 'leaf');
            }""",
            timeout=10000,
        )
        page.wait_for_selector(".prks-tile--main .prks-folder-nav__trigger", timeout=10000)
        page.wait_for_selector(".prks-tile--secondary .prks-folder-nav__trigger", timeout=10000)

        # Distinct per-instance trigger IDs (not one shared #prks-folder-nav-trigger).
        trigger_ids = page.evaluate(
            """() => [...document.querySelectorAll('.prks-folder-nav__trigger')].map(el => el.id)"""
        )
        self.assertEqual(len(trigger_ids), 2)
        self.assertEqual(len(set(trigger_ids)), 2)
        self.assertTrue(all(tid.startswith("prks-folder-nav-trigger-") for tid in trigger_ids))

        tab_ids = page.evaluate(
            """() => {
                const s = prksWorkspaceSnapshot();
                return {
                    main: s.mainTabId,
                    secondary: s.secondaryTree && s.secondaryTree.tabId,
                };
            }"""
        )
        self.assertIsNotNone(tab_ids["main"])
        self.assertIsNotNone(tab_ids["secondary"])
        self.assertNotEqual(tab_ids["main"], tab_ids["secondary"])

        # Secondary (Philosophy) → Epistemology; Main stays Ethics.
        self.open_switcher(page, scope=".prks-tile--secondary")
        owner_from_secondary = page.evaluate(
            """() => {
                const t = document.querySelector('.prks-tile--secondary .prks-folder-nav__trigger');
                return t && t.getAttribute('data-prks-folder-nav-tab-id');
            }"""
        )
        self.assertEqual(owner_from_secondary, tab_ids["secondary"])
        self.select_option_by_title(page, LIBRARY_NAV_EPISTEMOLOGY)
        self.wait_folder_title(
            page,
            LIBRARY_NAV_EPISTEMOLOGY,
            scope=".prks-tile--secondary",
        )
        main_still = page.evaluate(
            """(ethicsId) => {
                const s = prksWorkspaceSnapshot();
                const main = (s.tabs || []).find(t => t.id === s.mainTabId);
                const title = document.querySelector('.prks-tile--main .prks-page-title');
                return {
                    route: main && main.route,
                    title: title && title.textContent,
                    hash: location.hash,
                    ethicsInHash: location.hash.indexOf(ethicsId) >= 0,
                };
            }""",
            ids["ethics"],
        )
        self.assertIn(ids["ethics"], main_still["route"] or "")
        self.assertIn(LIBRARY_NAV_ETHICS, main_still["title"] or "")
        self.assertTrue(main_still["ethicsInHash"])

        # Main (Ethics) → Philosophy parent; Secondary stays Epistemology.
        self.open_switcher(page, scope=".prks-tile--main")
        owner_from_main = page.evaluate(
            """() => {
                const t = document.querySelector('.prks-tile--main .prks-folder-nav__trigger');
                return t && t.getAttribute('data-prks-folder-nav-tab-id');
            }"""
        )
        self.assertEqual(owner_from_main, tab_ids["main"])
        self.select_option_by_title(page, LIBRARY_NAV_PHILOSOPHY)
        self.wait_folder_title(page, LIBRARY_NAV_PHILOSOPHY, ids["philosophy"], scope=".prks-tile--main")
        secondary_still = page.evaluate(
            """(epistemologyId) => {
                const s = prksWorkspaceSnapshot();
                const secId = s.secondaryTree && s.secondaryTree.tabId;
                const sec = (s.tabs || []).find(t => t.id === secId);
                const title = document.querySelector('.prks-tile--secondary .prks-page-title');
                return {
                    route: sec && sec.route,
                    title: title && title.textContent,
                    hasEpistemology: !!(sec && sec.route && sec.route.indexOf(epistemologyId) >= 0),
                };
            }""",
            ids["epistemology"],
        )
        self.assertTrue(secondary_still["hasEpistemology"])
        self.assertIn(LIBRARY_NAV_EPISTEMOLOGY, secondary_still["title"] or "")

    def test_one_hierarchy_fetch_not_per_row(self):
        _server, page, ids = self.start()
        page.evaluate("() => prksNavigate('#/folders')")
        wait_for_async(
            page,
            """async () => {
                const rows = await (window.prksOfflinePeekList
                    ? window.prksOfflinePeekList('folders:index')
                    : null);
                if (rows) return true;
                return !!document.querySelector('.prks-folder-library, .prks-folder-tree');
            }""",
            timeout=15000,
        )
        self.open_folder(page, ids["ethics"])
        counts = {"folders_index": 0, "folder_detail": 0}

        def on_request(req):
            url = req.url or ""
            if "/api/folders/" in url and not url.rstrip("/").endswith("/api/folders"):
                if "/tag-options" in url or "/sync-state" in url:
                    return
                counts["folder_detail"] += 1
            elif url.rstrip("/").endswith("/api/folders") or "/api/folders?" in url:
                counts["folders_index"] += 1

        page.on("request", on_request)
        before_detail = counts["folder_detail"]
        self.open_switcher(page)
        page.wait_for_timeout(400)
        self.assertLessEqual(counts["folder_detail"] - before_detail, 1)
        self.assertLessEqual(counts["folders_index"], 2)
