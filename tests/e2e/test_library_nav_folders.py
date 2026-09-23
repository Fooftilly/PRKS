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
        page.wait_for_selector(f"{scope} [data-prks-role='folder-detail']", timeout=15000)

    def force_narrow_folder_layout(self, page, *, scope=".prks-tile--main"):
        """Compact Location+Nearby is the narrow/tile fallback only.

        Locks layout so ResizeObserver cannot flip a wide pane back to tree|contents.
        """
        page.evaluate(
            """(scope) => {
                const root = document.querySelector(
                    scope + ' [data-prks-role="folder-detail"]'
                );
                if (!root) return;
                root.setAttribute('data-prks-folder-layout-lock', 'narrow');
                root.setAttribute('data-prks-folder-layout', 'narrow');
            }""",
            scope,
        )
        page.wait_for_selector(f"{scope} .prks-folder-nav__trigger", timeout=10000)

    def open_switcher(self, page, *, scope=".prks-tile--main"):
        self.force_narrow_folder_layout(page, scope=scope)
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
                const el = document.querySelector(scope + ' .prks-folder-detail__main .prks-page-title')
                    || document.querySelector(scope + ' .prks-page-title');
                return el && (el.textContent || '').includes(title);
            }""",
            arg={"title": title, "scope": scope},
            timeout=10000,
        )
        page.wait_for_selector(f"{scope} [data-prks-role='folder-detail']", timeout=10000)

    def test_desktop_tree_selects_and_indents(self):
        """Normal-width Folder workspace: persistent tree | contents (not Location+Nearby)."""
        _server, page, ids = self.start()
        self.open_folder(page, ids["research"])
        page.wait_for_function(
            """() => {
                const root = document.querySelector(
                    '.prks-tile--main [data-prks-role="folder-detail"]'
                );
                return root && root.getAttribute('data-prks-folder-layout') === 'wide';
            }""",
            timeout=10000,
        )
        page.wait_for_selector(
            '.prks-tile--main [data-prks-folder-detail-tree-host] .prks-folder-tree__link[aria-current="page"]',
            timeout=10000,
        )
        # Compact band must stay hidden on wide layout; tree pane stays visible.
        self.assertTrue(
            page.evaluate(
                """() => {
                    const nav = document.querySelector('.prks-tile--main .prks-folder-nav');
                    const tree = document.querySelector(
                        '.prks-tile--main .prks-folder-detail__tree-pane'
                    );
                    const navHidden = !nav || getComputedStyle(nav).display === 'none';
                    const treeVisible = !!(tree && getComputedStyle(tree).display !== 'none');
                    return navHidden && treeVisible;
                }"""
            )
        )
        # Expand Philosophy so Ethics is reachable, then select Ethics in-tree.
        page.locator(
            '.prks-tile--main [data-prks-folder-detail-tree-host] .prks-folder-tree__row',
            has_text=LIBRARY_NAV_PHILOSOPHY,
        ).locator(".prks-folder-tree__toggle").first.click()
        page.locator(
            '.prks-tile--main [data-prks-folder-detail-tree-host] .prks-folder-tree__link',
            has_text=LIBRARY_NAV_ETHICS,
        ).first.click()
        self.wait_folder_title(page, LIBRARY_NAV_ETHICS, ids["ethics"])
        page.wait_for_function(
            """() => {
                const cur = document.querySelector(
                    '.prks-tile--main [data-prks-folder-detail-tree-host] .prks-folder-tree__link[aria-current="page"]'
                );
                return cur && (cur.textContent || '').includes('Ethics');
            }""",
            timeout=10000,
        )
        depth = page.evaluate(
            """() => {
                const row = document.querySelector(
                    '.prks-tile--main [data-prks-folder-detail-tree-host] .prks-folder-tree__row.is-selected'
                );
                return row ? row.style.getPropertyValue('--depth') : '';
            }"""
        )
        self.assertNotEqual(depth, "")
        self.assertGreaterEqual(int(depth or "0"), 2)
        # Quiet selected-row clear (surface-selected, not a heavy chrome treatment).
        self.assertTrue(
            page.evaluate(
                """() => {
                    const row = document.querySelector(
                        '.prks-tile--main [data-prks-folder-detail-tree-host] .prks-folder-tree__row.is-selected'
                    );
                    if (!row) return false;
                    return getComputedStyle(row).backgroundColor !== 'rgba(0, 0, 0, 0)';
                }"""
            )
        )

    def test_nearby_parent_sibling_and_cross_branch_filter(self):
        _server, page, ids = self.start()
        self.open_folder(page, ids["ethics"])
        self.force_narrow_folder_layout(page)
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
        self.assertIn("location", band_text.lower())
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
        self.force_narrow_folder_layout(page)
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
        self.force_narrow_folder_layout(page)
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
        self.force_narrow_folder_layout(page)
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
        self.force_narrow_folder_layout(page)
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
        page.wait_for_selector(".prks-tile--main [data-prks-role='folder-detail']", timeout=10000)
        self.force_narrow_folder_layout(page)

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
        page.wait_for_selector(".prks-tile--main [data-prks-role='folder-detail']", timeout=10000)
        page.wait_for_selector(".prks-tile--secondary [data-prks-role='folder-detail']", timeout=10000)
        # Compact switcher is the narrow/tile fallback — lock both panes so
        # a wide Main does not keep Location+Nearby display:none.
        self.force_narrow_folder_layout(page, scope=".prks-tile--main")
        self.force_narrow_folder_layout(page, scope=".prks-tile--secondary")

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
        self.force_narrow_folder_layout(page, scope=".prks-tile--main")
        self.force_narrow_folder_layout(page, scope=".prks-tile--secondary")
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
        self.force_narrow_folder_layout(page, scope=".prks-tile--secondary")
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

    def test_folder_to_folder_keeps_workspace_while_pending(self):
        """Folder→Folder must not blank the center with generic Loading view..."""
        from urllib.parse import urlparse

        _server, page, ids = self.start()
        research = ids["research"]
        philosophy = ids["philosophy"]
        ethics = ids["ethics"]
        self.open_folder(page, research)
        page.wait_for_selector(
            ".prks-tile--main [data-prks-folder-detail-tree-host] .prks-folder-tree__link",
            timeout=10000,
        )

        held_b = []
        held_c = []

        def hold_destinations(route):
            req = route.request
            path = urlparse(req.url).path
            if req.method != "GET":
                route.fallback()
                return
            if path == "/api/folders/" + philosophy:
                held_b.append(route)
                return
            if path == "/api/folders/" + ethics:
                held_c.append(route)
                return
            route.fallback()

        page.route("**/api/folders/**", hold_destinations)
        try:
            page.locator(
                f'.prks-tile--main [data-prks-folder-detail-tree-host] '
                f'.prks-folder-tree__link[href="#/folders/{philosophy}"]'
            ).click()
            # Wait until Philosophy detail GET is held.
            deadline = page.evaluate("() => Date.now()") + 8000
            while page.evaluate("() => Date.now()") < deadline and not held_b:
                page.wait_for_timeout(50)
            self.assertTrue(held_b, "destination Folder GET was not held")

            mid = page.evaluate(
                """({ researchTitle }) => {
                    const scope = '.prks-tile--main';
                    const detail = document.querySelector(scope + ' [data-prks-role="folder-detail"]');
                    const tree = document.querySelector(
                        scope + ' [data-prks-folder-detail-tree-host] .prks-folder-tree--detail-nav'
                    );
                    const loading = document.querySelector(scope + ' .prks-route-loading');
                    const title = document.querySelector(
                        scope + ' .prks-folder-detail__main .prks-page-title'
                    );
                    return {
                        hasDetail: !!detail,
                        hasTree: !!tree,
                        hasRouteLoading: !!loading,
                        stillResearch: !!(title && (title.textContent || '').includes(researchTitle)),
                    };
                }""",
                {"researchTitle": LIBRARY_NAV_RESEARCH},
            )
            self.assertTrue(mid["hasDetail"], "Folder workspace must stay mounted")
            self.assertTrue(mid["hasTree"], "hierarchy tree must stay mounted")
            self.assertFalse(mid["hasRouteLoading"], "must not insert .prks-route-loading")
            self.assertTrue(mid["stillResearch"], "prior Folder content must remain until commit")

            held_b.pop(0).fallback()
            self.wait_folder_title(page, LIBRARY_NAV_PHILOSOPHY, philosophy)

            # Return to Research (also Folder→Folder; usually cache-served).
            held_b.clear()
            held_c.clear()
            page.locator(
                f'.prks-tile--main [data-prks-folder-detail-tree-host] '
                f'.prks-folder-tree__link[href="#/folders/{research}"]'
            ).click()
            self.wait_folder_title(page, LIBRARY_NAV_RESEARCH, research)

            # Evict B/C detail cache so A→B→C still hits the network (and our hold).
            page.evaluate(
                """async ({ b, c }) => {
                    const store = window.createPrksOfflineStore && window.createPrksOfflineStore();
                    if (!store) return;
                    await store.deleteEntity('folder', b);
                    await store.deleteEntity('folder', c);
                }""",
                {"b": philosophy, "c": ethics},
            )

            # A → B → C with out-of-order responses: release B after C is selected;
            # only C may win.
            held_b.clear()
            held_c.clear()
            page.locator(
                f'.prks-tile--main [data-prks-folder-detail-tree-host] '
                f'.prks-folder-tree__link[href="#/folders/{philosophy}"]'
            ).click()
            deadline = page.evaluate("() => Date.now()") + 8000
            while page.evaluate("() => Date.now()") < deadline and not held_b:
                page.wait_for_timeout(50)
            self.assertTrue(held_b, "B (Philosophy) GET not held")
            page.locator(
                f'.prks-tile--main [data-prks-folder-detail-tree-host] '
                f'.prks-folder-tree__link[href="#/folders/{ethics}"]'
            ).click()
            deadline = page.evaluate("() => Date.now()") + 8000
            while page.evaluate("() => Date.now()") < deadline and not held_c:
                page.wait_for_timeout(50)
            self.assertTrue(held_c, "C (Ethics) GET not held")
            self.assertEqual(page.locator(".prks-tile--main .prks-route-loading").count(), 0)
            self.assertEqual(
                page.locator(".prks-tile--main [data-prks-role='folder-detail']").count(),
                1,
            )
            # Narrowed guarantee: when C is released first, C commits and the
            # workspace never paints B. Late-B generation-guard delivery is
            # covered by test_folder_to_folder_stale_detail_cannot_commit.
            while held_c:
                held_c.pop(0).fallback()
            self.wait_folder_title(page, LIBRARY_NAV_ETHICS, ethics)
            title = page.locator(
                ".prks-tile--main .prks-folder-detail__main .prks-page-title"
            ).inner_text()
            self.assertIn(LIBRARY_NAV_ETHICS, title)
            self.assertNotIn(LIBRARY_NAV_PHILOSOPHY, title)
            while held_b:
                held_b.pop(0).abort()
        finally:
            for bucket in (held_b, held_c):
                while bucket:
                    route = bucket.pop(0)
                    try:
                        route.abort()
                    except Exception:
                        try:
                            route.fallback()
                        except Exception:
                            pass
            try:
                page.unroute("**/api/folders/**")
            except Exception:
                pass

    def test_folder_to_folder_pending_freezes_owned_panel_not_tree(self):
        """While A→B is pending, owned right panel is inert; tree can still go to C."""
        from urllib.parse import urlparse

        _server, page, ids = self.start()
        research = ids["research"]
        philosophy = ids["philosophy"]
        ethics = ids["ethics"]
        self.open_folder(page, research)
        page.wait_for_selector("#panel-content .prks-private-notes-input", timeout=10000)
        page.wait_for_selector(
            ".prks-tile--main [data-prks-folder-detail-tree-host] .prks-folder-tree__link",
            timeout=10000,
        )

        held_b = []

        def hold_b(route):
            req = route.request
            path = urlparse(req.url).path
            if req.method == "GET" and path == "/api/folders/" + philosophy:
                held_b.append(route)
                return
            route.fallback()

        page.route("**/api/folders/**", hold_b)
        try:
            page.locator(
                f'.prks-tile--main [data-prks-folder-detail-tree-host] '
                f'.prks-folder-tree__link[href="#/folders/{philosophy}"]'
            ).click()
            deadline = page.evaluate("() => Date.now()") + 8000
            while page.evaluate("() => Date.now()") < deadline and not held_b:
                page.wait_for_timeout(50)
            self.assertTrue(held_b, "B (Philosophy) GET not held")

            pending = page.evaluate(
                """() => {
                    const panel = document.getElementById('panel-content');
                    const notes = panel && panel.querySelector('.prks-private-notes-input');
                    const tagSearch = panel && panel.querySelector('#folder-tag-search');
                    const main = document.querySelector(
                        '.prks-tile--main .prks-folder-detail__main'
                    );
                    const newBtn = document.querySelector(
                        '.prks-tile--main [data-prks-role="folder-detail-new-folder"]'
                    );
                    const treeLink = document.querySelector(
                        '.prks-tile--main [data-prks-folder-detail-tree-host] '
                        + '.prks-folder-tree__link'
                    );
                    return {
                        panelInert: !!(panel && panel.inert),
                        notesBlocked: !!(notes && (panel && panel.inert)),
                        tagSearchBlocked: !!(tagSearch && (panel && panel.inert)),
                        mainInert: !!(main && main.inert),
                        newDisabled: !!(newBtn && newBtn.disabled),
                        treeNotInert: !!(
                            treeLink &&
                            !treeLink.closest('[inert]') &&
                            !treeLink.disabled
                        ),
                    };
                }"""
            )
            self.assertTrue(pending["panelInert"], "owned #panel-content must be inert while B pending")
            self.assertTrue(pending["notesBlocked"], "Reminders must not accept input while pending")
            self.assertTrue(pending["tagSearchBlocked"], "folder tag search must be blocked while pending")
            self.assertTrue(pending["mainInert"], "Folder main must stay inert while pending")
            self.assertTrue(pending["newDisabled"], "New Folder must stay disabled while pending")
            self.assertTrue(pending["treeNotInert"], "hierarchy tree must remain clickable")

            # Expand Philosophy (if needed) and activate Ethics via the live tree
            # DOM — proves hierarchy is not under inert. Prefer a real click() so
            # an accidentally-inert tree cannot silently succeed.
            activated = page.evaluate(
                """({ philosophyId, ethicsId }) => {
                    const host = document.querySelector(
                        '.prks-tile--main [data-prks-folder-detail-tree-host]'
                    );
                    if (!host) return { ok: false, reason: 'no-host' };
                    if (host.closest('[inert]')) return { ok: false, reason: 'host-inert' };
                    const phil = host.querySelector(
                        '.prks-folder-tree__row[data-folder-id="' + philosophyId + '"]'
                    );
                    if (phil && phil.getAttribute('aria-expanded') !== 'true') {
                        const toggle = phil.querySelector('.prks-folder-tree__toggle');
                        if (toggle) toggle.click();
                    }
                    const link = host.querySelector(
                        '.prks-folder-tree__link[href="#/folders/' + ethicsId + '"]'
                    );
                    if (!link) return { ok: false, reason: 'no-ethics-link' };
                    if (link.closest('[inert]')) return { ok: false, reason: 'link-inert' };
                    link.click();
                    return { ok: true };
                }""",
                {"philosophyId": philosophy, "ethicsId": ethics},
            )
            self.assertTrue(activated.get("ok"), f"tree→C failed: {activated}")
            self.wait_folder_title(page, LIBRARY_NAV_ETHICS, ethics)
            settled = page.evaluate(
                """() => {
                    const panel = document.getElementById('panel-content');
                    const main = document.querySelector(
                        '.prks-tile--main .prks-folder-detail__main'
                    );
                    return {
                        panelInert: !!(panel && panel.inert),
                        mainInert: !!(main && main.inert),
                    };
                }"""
            )
            self.assertFalse(settled["panelInert"], "panel inert must clear after C commits")
            self.assertFalse(settled["mainInert"], "main inert must clear after C commits")
        finally:
            while held_b:
                try:
                    held_b.pop(0).abort()
                except Exception:
                    pass
            try:
                page.unroute("**/api/folders/**")
            except Exception:
                pass

    def test_folder_to_folder_stale_detail_cannot_commit(self):
        """Late B detail result after C supersedes must not commit (generation guard)."""
        _server, page, ids = self.start()
        research = ids["research"]
        philosophy = ids["philosophy"]
        ethics = ids["ethics"]
        self.open_folder(page, research)
        self.wait_folder_title(page, LIBRARY_NAV_RESEARCH, research)

        philosophy_body = page.evaluate(
            """async (id) => {
                const res = await fetch('/api/folders/' + encodeURIComponent(id));
                if (!res.ok) throw new Error('philosophy fetch failed');
                return await res.json();
            }""",
            philosophy,
        )
        self.assertEqual(philosophy_body.get("id"), philosophy)

        page.evaluate(
            """({ philosophyId }) => {
                const original = window.prksOfflineDetailFetch;
                if (typeof original !== 'function') throw new Error('missing prksOfflineDetailFetch');
                window.__prksOrigOfflineDetailFetch = original;
                window.__prksHeldFolderDetail = null;
                window.prksOfflineDetailFetch = async function (kind, id, path, signal, options) {
                    if (kind === 'folder' && String(id) === String(philosophyId)) {
                        return await new Promise((resolve) => {
                            window.__prksHeldFolderDetail = { resolve };
                        });
                    }
                    return original.call(this, kind, id, path, signal, options);
                };
            }""",
            {"philosophyId": philosophy},
        )
        try:
            page.locator(
                f'.prks-tile--main [data-prks-folder-detail-tree-host] '
                f'.prks-folder-tree__link[href="#/folders/{philosophy}"]'
            ).click()
            page.wait_for_function(
                "() => !!(window.__prksHeldFolderDetail && window.__prksHeldFolderDetail.resolve)",
                timeout=10000,
            )
            activated = page.evaluate(
                """({ philosophyId, ethicsId }) => {
                    const host = document.querySelector(
                        '.prks-tile--main [data-prks-folder-detail-tree-host]'
                    );
                    if (!host) return { ok: false, reason: 'no-host' };
                    const phil = host.querySelector(
                        '.prks-folder-tree__row[data-folder-id="' + philosophyId + '"]'
                    );
                    if (phil && phil.getAttribute('aria-expanded') !== 'true') {
                        const toggle = phil.querySelector('.prks-folder-tree__toggle');
                        if (toggle) toggle.click();
                    }
                    const link = host.querySelector(
                        '.prks-folder-tree__link[href="#/folders/' + ethicsId + '"]'
                    );
                    if (!link) return { ok: false, reason: 'no-ethics-link' };
                    link.click();
                    return { ok: true };
                }""",
                {"philosophyId": philosophy, "ethicsId": ethics},
            )
            self.assertTrue(activated.get("ok"), f"tree→C failed: {activated}")
            self.wait_folder_title(page, LIBRARY_NAV_ETHICS, ethics)

            delivered = page.evaluate(
                """(body) => {
                    const held = window.__prksHeldFolderDetail;
                    if (!held || typeof held.resolve !== 'function') {
                        return false;
                    }
                    held.resolve({
                        value: body,
                        source: 'network',
                        cachedAt: null,
                    });
                    window.__prksHeldFolderDetail = null;
                    return true;
                }""",
                philosophy_body,
            )
            self.assertTrue(delivered, "late B detail must be delivered to the held promise")
            page.evaluate(
                "() => new Promise((r) => requestAnimationFrame(() => setTimeout(r, 0)))"
            )
            page.wait_for_timeout(100)
            title = page.locator(
                ".prks-tile--main .prks-folder-detail__main .prks-page-title"
            ).inner_text()
            self.assertIn(LIBRARY_NAV_ETHICS, title)
            self.assertNotIn(LIBRARY_NAV_PHILOSOPHY, title)
            self.assertEqual(
                page.evaluate("() => location.hash"),
                "#/folders/" + ethics,
            )
        finally:
            page.evaluate(
                """() => {
                    if (window.__prksHeldFolderDetail && window.__prksHeldFolderDetail.resolve) {
                        try {
                            window.__prksHeldFolderDetail.resolve({
                                value: null,
                                source: 'unavailable',
                                cachedAt: null,
                            });
                        } catch (_) {}
                    }
                    window.__prksHeldFolderDetail = null;
                    if (window.__prksOrigOfflineDetailFetch) {
                        window.prksOfflineDetailFetch = window.__prksOrigOfflineDetailFetch;
                        delete window.__prksOrigOfflineDetailFetch;
                    }
                }"""
            )

    def test_folder_to_folder_replaces_offline_provenance_banner(self):
        """In-place Folder→Folder must not stack or leave stale Offline banners."""
        server, page, ids = self.start()
        context = page.context
        research = ids["research"]
        philosophy = ids["philosophy"]

        # Warm both Folder details online so offline can serve them from cache.
        self.open_folder(page, research)
        wait_for_async(
            page,
            """async (id) => {
                const row = await window.createPrksOfflineStore().getEntity('folder', id);
                return !!(row && row.value && row.value.id === id);
            }""",
            arg=research,
            timeout=15000,
        )
        self.open_folder(page, philosophy)
        wait_for_async(
            page,
            """async (id) => {
                const row = await window.createPrksOfflineStore().getEntity('folder', id);
                return !!(row && row.value && row.value.id === id);
            }""",
            arg=philosophy,
            timeout=15000,
        )

        context.set_offline(True)
        page.evaluate("async () => { try { await prksRequest('/api/settings'); } catch (_) {} }")
        page.wait_for_function("() => prksOfflineRuntimeState() === 'offline'", timeout=20000)

        # cached A
        page.locator(
            f'.prks-tile--main [data-prks-folder-detail-tree-host] '
            f'.prks-folder-tree__link[href="#/folders/{research}"]'
        ).click()
        self.wait_folder_title(page, LIBRARY_NAV_RESEARCH, research)
        page.locator(
            '.prks-tile--main [data-prks-role="offline-provenance-banner"]',
            has_text="Offline",
        ).wait_for(timeout=10000)
        self.assertEqual(
            page.locator('.prks-tile--main [data-prks-role="offline-provenance-banner"]').count(),
            1,
        )

        # cached A → cached B: exactly one banner (no stack)
        page.locator(
            f'.prks-tile--main [data-prks-folder-detail-tree-host] '
            f'.prks-folder-tree__link[href="#/folders/{philosophy}"]'
        ).click()
        self.wait_folder_title(page, LIBRARY_NAV_PHILOSOPHY, philosophy)
        page.locator(
            '.prks-tile--main [data-prks-role="offline-provenance-banner"]',
            has_text="Offline",
        ).wait_for(timeout=10000)
        self.assertEqual(
            page.locator('.prks-tile--main [data-prks-role="offline-provenance-banner"]').count(),
            1,
        )

        # cached B → online B/A: banner must clear
        context.set_offline(False)
        page.evaluate("async () => { await prksRequest('/api/settings'); }")
        page.wait_for_function("() => prksOfflineRuntimeState() === 'online'", timeout=20000)
        page.locator(
            f'.prks-tile--main [data-prks-folder-detail-tree-host] '
            f'.prks-folder-tree__link[href="#/folders/{research}"]'
        ).click()
        self.wait_folder_title(page, LIBRARY_NAV_RESEARCH, research)
        page.wait_for_function(
            """() => document.querySelectorAll(
                '.prks-tile--main [data-prks-role="offline-provenance-banner"]'
            ).length === 0""",
            timeout=20000,
        )
        self.assertEqual(
            page.locator('.prks-tile--main [data-prks-role="offline-provenance-banner"]').count(),
            0,
        )

    def test_tiled_folder_switch_stays_in_own_pane(self):
        """Tiled Folder TabContexts: in-place switch must not cross-pane wipe."""
        from urllib.parse import urlparse

        _server, page, ids = self.start()
        self.open_folder(page, ids["ethics"])
        page.evaluate(
            """(id) => prksNavigate('#/folders/' + encodeURIComponent(id), { target: 'tile' })""",
            ids["epistemology"],
        )
        page.wait_for_selector(".prks-tile--secondary [data-prks-role='folder-detail']", timeout=15000)
        held = []

        def hold_phil_main(route):
            req = route.request
            path = urlparse(req.url).path
            if req.method == "GET" and path == "/api/folders/" + ids["philosophy"]:
                held.append(route)
                return
            route.fallback()

        page.route("**/api/folders/**", hold_phil_main)
        try:
            page.locator(
                f'.prks-tile--main [data-prks-folder-detail-tree-host] '
                f'.prks-folder-tree__link[href="#/folders/{ids["philosophy"]}"]'
            ).click()
            deadline = page.evaluate("() => Date.now()") + 8000
            while page.evaluate("() => Date.now()") < deadline and not held:
                page.wait_for_timeout(50)
            self.assertTrue(held)
            mid = page.evaluate(
                """() => ({
                    mainLoading: !!document.querySelector('.prks-tile--main .prks-route-loading'),
                    secLoading: !!document.querySelector('.prks-tile--secondary .prks-route-loading'),
                    secDetail: !!document.querySelector(
                        '.prks-tile--secondary [data-prks-role="folder-detail"]'
                    ),
                    mainDetail: !!document.querySelector(
                        '.prks-tile--main [data-prks-role="folder-detail"]'
                    ),
                })"""
            )
            self.assertFalse(mid["mainLoading"])
            self.assertFalse(mid["secLoading"])
            self.assertTrue(mid["secDetail"])
            self.assertTrue(mid["mainDetail"])
            held[0].fallback()
            self.wait_folder_title(
                page, LIBRARY_NAV_PHILOSOPHY, ids["philosophy"], scope=".prks-tile--main"
            )
            sec_title = page.locator(
                ".prks-tile--secondary .prks-folder-detail__main .prks-page-title"
            ).inner_text()
            self.assertIn(LIBRARY_NAV_EPISTEMOLOGY, sec_title)
        finally:
            while held:
                try:
                    held.pop(0).fallback()
                except Exception:
                    pass
            try:
                page.unroute("**/api/folders/**")
            except Exception:
                pass
