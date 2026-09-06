"""PRKS UX Interaction Tour.

Deliberately human-oriented, artifact-producing scenario suite. It complements
-- and never replaces -- the fast unit, browser-selftest, and E2E suites: it
exercises PRKS through the same controls used in practice, in realistic
multi-step sequences, and produces reviewable evidence (video, Playwright
trace, checkpoint screenshots, a machine-readable action log, and a browser
error report) for each scenario. See tests/ux_tour/COVERAGE.md for the map of
which capability is exercised where.

Collected ONLY when PRKS_UX_TOUR=1 (set by tests/ux_tour/run.py), exactly like
tests/e2e/test_app.py's PRKS_E2E gate -- this keeps it out of ordinary
`python run_tests.py`, `--e2e`, and `--all` runs. Never run with failfast: one
tour failing must not prevent the others from running and producing artifacts.

ACTION vs ASSERTION: interact with real UI controls (buttons, menus, links,
inputs, keyboard shortcuts, drag handles, the command palette, modals). Use
page.evaluate() only to inspect internal state for assertions/instrumentation
(workspace snapshot, TabContext ids, PDF runtime identity, request counters),
never as a substitute for the interaction under review.
"""
from __future__ import annotations

import os
import re
import unittest
from urllib.parse import urlparse

from tests.e2e.fixtures import MINIMAL_PDF, PERSON_DISPLAY, WORK_A_TITLE, WORK_B_TITLE
from tests.e2e.harness import require_chromium
from tests.e2e.test_app import (
    _click_graph_node,
    _click_workspace_menu,
    _diagnostics_requests,
    _drag_divider,
    _expand_research,
    _open_annotations_tab,
    _open_pane_actions,
    _open_settings,
    _open_work_from_home,
    _tile_box,
    _wait_pdf_tab,
    _wait_pdf_viewer,
    _workspace_ids,
)
from tests.ux_tour import harness
from tests.ux_tour.fixtures import (
    CONCEPT_CHILD_NAME,
    CONCEPT_PARENT_NAME,
    GROUP_CHILD_NAME,
    GROUP_PARENT_NAME,
    PERSON2_DISPLAY,
    PLAYLIST_TITLE,
    PUBLISHER_NAME,
    SAVED_VIEW_NAME,
    TAG_NAMES,
    TOUR_POSITION_NAME,
    TOUR_STANCE_NAME,
    WORK_C_TITLE,
    WORK_D_TITLE,
    seed_ux_tour_library,
)


def load_tests(loader, standard_tests, pattern):
    if os.environ.get("PRKS_UX_TOUR") != "1":
        return unittest.TestSuite()
    return standard_tests


def _palette_pick(page, entity_display_name: str):
    """Type into the already-open command palette and click the exact-match result."""
    page.locator("#prks-command-palette-input").fill(entity_display_name)
    label = page.locator(
        ".prks-command-palette__option-label",
        has_text=re.compile("^" + re.escape(entity_display_name) + "$"),
    )
    label.wait_for()
    page.locator(".prks-command-palette__option").filter(has=label).click()


def _split_via_pane_menu(test, page, target_id: str, label: str, entity_display_name: str) -> str:
    """Real-UI workflow: pane menu -> 'Split right'/'Split down' -> command palette ->
    pick an entity. Returns the new leaf's tab id. Never calls prksWorkspaceSplitLeaf
    directly -- this is the exact sequence a user takes, including the pane-menu ->
    command-palette -> selection lifecycle."""
    before_ids = set(page.evaluate("() => window.prksWorkspaceSnapshot().tabs.map(t => t.id)"))
    _open_pane_actions(page, target_id)
    _click_workspace_menu(page, label)
    page.wait_for_selector("#prks-command-palette:not([hidden])")
    _palette_pick(page, entity_display_name)
    page.wait_for_function(
        """() => {
            const snap = window.prksWorkspaceSnapshot();
            return snap.secondaryTree && snap.secondaryTree.type === 'split';
        }"""
    )
    after_ids = set(page.evaluate("() => window.prksWorkspaceSnapshot().tabs.map(t => t.id)"))
    new_ids = after_ids - before_ids
    test.assertEqual(len(new_ids), 1)
    return new_ids.pop()


_PW = None
_BROWSER = None
_RUN_DIR = None
_RUN_ID = None
_RECORD_ALL = False
_RESULTS: list = []


def setUpModule():
    global _PW, _BROWSER, _RUN_DIR, _RUN_ID, _RECORD_ALL
    _PW, _BROWSER = require_chromium()
    _RECORD_ALL = harness.record_mode_enabled()
    _RUN_ID = harness.new_run_id()
    _RUN_DIR = harness.ARTIFACTS_ROOT / _RUN_ID
    _RUN_DIR.mkdir(parents=True, exist_ok=True)


def tearDownModule():
    global _PW, _BROWSER
    try:
        harness.write_manifest_and_report(_RUN_DIR, _RUN_ID, _RESULTS, record_all=_RECORD_ALL)
        passed = harness.all_passed(_RESULTS)
        print("\n" + ("UX TOUR PASS" if passed else "UX TOUR FAIL"))
        print("Artifacts:")
        print(str(_RUN_DIR) + os.sep)
        if _RECORD_ALL:
            archive = harness.zip_run(_RUN_DIR, _RUN_ID)
            print("Archive:")
            print(str(archive))
    finally:
        try:
            if _BROWSER is not None:
                _BROWSER.close()
        finally:
            if _PW is not None:
                _PW.stop()
            _PW = None
            _BROWSER = None


class _UXTour(unittest.TestCase):
    def open_tour(self, name: str, seed_fn=seed_ux_tour_library, extra_env=None):
        return harness.open_tour_page(
            _BROWSER,
            _RUN_DIR,
            name,
            seed_fn=seed_fn,
            record_all=_RECORD_ALL,
            results=_RESULTS,
            extra_env=extra_env,
        )


class ShellNavigationTourTest(_UXTour):
    def test_shell_navigation_tour(self):
        with self.open_tour("shell-navigation") as (page, _collector, tour):
            tour.checkpoint(page, "initial-folders")

            for step_name, href, wait_hash, marker in (
                ("Open Recent", '#/recent', "#/recent", None),
                ("Open Saved Views", '#/views', "#/views", None),
                ("Open File Types", '#/types', "#/types", None),
                ("Open Playlists", '#/playlists', "#/playlists", None),
                ("Open Tags", '#/tags', "#/tags", None),
                ("Open Publishers", '#/publishers', "#/publishers", None),
                ("Open People", '#/people', "#/people", None),
                ("Open Processing", '#/processing-files', "#/processing-files", None),
            ):
                tour.step(step_name)
                page.locator('#sidebar a.nav-link[href="%s"]' % href).click()
                page.wait_for_function("(h) => location.hash === h", arg=wait_hash)

            # Research disclosure: Concepts, Positions, Arguments, Graph.
            tour.step("Expand Research disclosure (stacked)")
            _expand_research(page)
            for href, wait_hash in (
                ('#/concepts', "#/concepts"),
                ('#/positions', "#/positions"),
                ('#/arguments', "#/arguments"),
            ):
                page.locator('#prks-nav-research-children a.nav-link[href="%s"]' % href).click()
                page.wait_for_function("(h) => location.hash === h", arg=wait_hash)
            tour.step("Open Research Graph")
            page.locator('#prks-nav-research-children a.nav-link[href="#/graph"]').click()
            page.wait_for_function("() => location.hash.indexOf('#/graph') === 0")
            page.locator("h2", has_text="Research Graph").wait_for()

            # Progress disclosure: in the normal (stacked) shell this just expands an inline
            # list in the already-full-width sidebar -- no overlay involved yet.
            tour.step("Open Progress disclosure (stacked)")
            progress_toggle = page.locator('[data-nav-disclosure-toggle="progress"]')
            progress_children = page.locator("#prks-nav-progress-children")
            progress_toggle.click()
            page.wait_for_function(
                "() => document.querySelector('[data-nav-disclosure-toggle=\"progress\"]')"
                ".getAttribute('aria-expanded') === 'true'"
            )
            self.assertTrue(progress_children.is_visible())
            tour.step("Visit In Progress status filter")
            progress_children.locator('a[data-status="In Progress"]').click()
            page.wait_for_function("() => location.hash.indexOf('#/progress') === 0")
            tour.checkpoint(page, "full-navigation")

            # Compact rail + dismissible disclosure overlays: only meaningful once the dense
            # tiled shell is active, so open a second tileable route first (real UI: the
            # workspace Split button + command palette, not window.prksNavigate target:'tile').
            tour.step("Open Work A")
            _open_work_from_home(page, WORK_A_TITLE)
            _wait_pdf_viewer(page)
            tour.step("Open Work B in split view")
            page.locator("#prks-workspace-tile-layout").click()
            page.wait_for_selector("#prks-command-palette:not([hidden])")
            page.locator("#prks-command-palette-input").fill(WORK_B_TITLE)
            work_b_label = page.locator(
                ".prks-command-palette__option-label",
                has_text=re.compile("^" + re.escape(WORK_B_TITLE) + "$"),
            )
            work_b_label.wait_for()
            page.locator(".prks-command-palette__option").filter(has=work_b_label).click()
            page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
            page.wait_for_function(
                "() => document.getElementById('app-container').classList.contains('app-container--tiled')"
            )

            # Entering the tiled shell already collapses navigation to the compact rail
            # (no click needed) -- the rail and the drawer overlay are the same #sidebar
            # element, widened via a negative margin so it never resizes the canvas.
            page.wait_for_function("() => document.getElementById('sidebar').getBoundingClientRect().width < 100")
            canvas = page.locator(".prks-workspace-canvas")
            before_width = canvas.evaluate("el => el.getBoundingClientRect().width")
            tour.checkpoint(page, "compact-navigation-rail")

            tour.step("Open Research disclosure overlay from the compact rail")
            research_toggle = page.locator('[data-nav-disclosure-toggle="research"]')
            research_toggle.click()
            page.wait_for_function("() => document.body.classList.contains('prks-sidebar-open')")
            self.assertEqual(research_toggle.get_attribute("aria-expanded"), "true")
            self.assertTrue(page.locator("#prks-nav-research-children").is_visible())
            tour.checkpoint(page, "research-overlay-open")
            research_overlay_width = canvas.evaluate("el => el.getBoundingClientRect().width")
            self.assertAlmostEqual(before_width, research_overlay_width, delta=1)
            no_dead_icon = page.evaluate(
                "() => !!document.querySelector('[data-nav-disclosure-toggle=\"research\"] .nav-disclosure__chevron')"
            )
            self.assertTrue(no_dead_icon)

            tour.step("Close navigation overlay with Escape")
            page.keyboard.press("Escape")
            page.wait_for_function("() => !document.body.classList.contains('prks-sidebar-open')")

            tour.step("Open Progress disclosure overlay from the compact rail")
            page.locator('[data-nav-disclosure-toggle="progress"]').click()
            page.wait_for_function("() => document.body.classList.contains('prks-sidebar-open')")
            self.assertTrue(page.locator("#prks-nav-progress-children").is_visible())
            tour.checkpoint(page, "progress-overlay-open")
            progress_overlay_width = canvas.evaluate("el => el.getBoundingClientRect().width")
            self.assertAlmostEqual(before_width, progress_overlay_width, delta=1)

            tour.step("Close navigation overlay via the collapse control")
            page.locator("#prks-sidebar-collapse-btn").click()
            page.wait_for_function("() => !document.body.classList.contains('prks-sidebar-open')")
            self.assertAlmostEqual(
                canvas.evaluate("el => el.getBoundingClientRect().width"), before_width, delta=1
            )


class WorkspaceTourTest(_UXTour):
    """Multi-document workspace flow -- the first recording to review when UI
    flicker, lost focus, or a broken pane control is reported."""

    def test_workspace_tour(self):
        with self.open_tour("workspace") as (page, _collector, tour):
            tour.step("Open Work A")
            _open_work_from_home(page, WORK_A_TITLE)
            _wait_pdf_viewer(page)
            work_a_id = page.evaluate("() => window.prksWorkspaceSnapshot().mainTabId")
            page.evaluate(
                """(id) => {
                    window.__prksTourRt = { a: window.prksGetTabContext(id).getResource('pdf') };
                }""",
                arg=work_a_id,
            )

            tour.step("Open Work B as a new Main tab")
            page.locator("#prks-workspace-new-tab").click()
            page.wait_for_selector("#prks-command-palette:not([hidden])")
            _palette_pick(page, WORK_B_TITLE)
            page.wait_for_function("() => document.querySelectorAll('.prks-workspace-tab').length === 2")
            _wait_pdf_viewer(page)
            work_b_id = page.evaluate(
                """(aId) => window.prksWorkspaceSnapshot().tabs.find(t => t.id !== aId).id""",
                arg=work_a_id,
            )
            page.wait_for_function("(id) => window.prksWorkspaceSnapshot().mainTabId === id", arg=work_b_id)

            tour.step("Confirm Work A warm-suspends rather than cold-unmounts")
            a_after_b_main = page.evaluate(
                """(id) => {
                    const ctx = window.prksGetTabContext(id);
                    return { mounted: !!ctx.mounted, suspended: !!ctx.suspended };
                }""",
                arg=work_a_id,
            )
            self.assertEqual(a_after_b_main, {"mounted": False, "suspended": True})
            page.evaluate(
                """(id) => {
                    window.__prksTourRt.b = window.prksGetTabContext(id).getResource('pdf');
                }""",
                arg=work_b_id,
            )

            def _work_fetch_count():
                return len(
                    [
                        r
                        for r in page.evaluate("() => performance.getEntriesByType('resource').map(e => e.name)")
                        if "/api/works/" in r
                    ]
                )

            fetch_count_baseline = _work_fetch_count()

            def _assert_warm_resume(suspended_id):
                """Internal-JS assertion only (per the Tour's real-action-vs-instrumentation
                contract): the tab-strip click that triggered this transition was a real UI
                action; this just confirms the resulting state was a warm suspend/resume
                (same TabContext, same PDF resource object, no repeat Work/PDF fetch) rather
                than a cold unmount/remount."""
                state = page.evaluate(
                    """(id) => {
                        const ctx = window.prksGetTabContext(id);
                        return { mounted: !!ctx.mounted, suspended: !!ctx.suspended };
                    }""",
                    arg=suspended_id,
                )
                self.assertEqual(state, {"mounted": False, "suspended": True})
                same_runtimes = page.evaluate(
                    """(ids) => {
                        const rt = window.__prksTourRt;
                        const a = window.prksGetTabContext(ids.a).getResource('pdf');
                        const b = window.prksGetTabContext(ids.b).getResource('pdf');
                        return !!rt.a && !!rt.b && a === rt.a && b === rt.b;
                    }""",
                    arg={"a": work_a_id, "b": work_b_id},
                )
                self.assertTrue(same_runtimes)
                self.assertEqual(_work_fetch_count(), fetch_count_baseline)

            tour.step("Activate Work A from the tab strip")
            page.locator('.prks-workspace-tab[data-tab-id="%s"] .prks-workspace-tab__activate' % work_a_id).click()
            page.wait_for_function("(id) => window.prksWorkspaceSnapshot().mainTabId === id", arg=work_a_id)
            _assert_warm_resume(work_b_id)
            tour.step("Activate Work B from the tab strip")
            page.locator('.prks-workspace-tab[data-tab-id="%s"] .prks-workspace-tab__activate' % work_b_id).click()
            page.wait_for_function("(id) => window.prksWorkspaceSnapshot().mainTabId === id", arg=work_b_id)
            _assert_warm_resume(work_a_id)
            tour.step("Activate Work A again")
            page.locator('.prks-workspace-tab[data-tab-id="%s"] .prks-workspace-tab__activate' % work_a_id).click()
            page.wait_for_function("(id) => window.prksWorkspaceSnapshot().mainTabId === id", arg=work_a_id)
            _assert_warm_resume(work_b_id)
            tour.checkpoint(page, "warm-pdf-return")

            tour.step("Tile Work B beside Work A")
            page.locator('.prks-workspace-tab[data-tab-id="%s"] .prks-workspace-tab__split' % work_b_id).click()
            page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
            self.assertEqual(page.locator(".prks-tile").count(), 2)
            tour.checkpoint(page, "two-pane")

            tour.step("Resize the root Main/Secondary split")
            ratio_before = page.evaluate("() => window.prksWorkspaceSnapshot().mainSplitRatio")
            _drag_divider(page, 120)
            page.wait_for_function(
                "r => window.prksWorkspaceSnapshot().mainSplitRatio > r + 0.01", arg=ratio_before
            )

            tour.step("Split Work B right with a Person")
            person_id = _split_via_pane_menu(self, page, work_b_id, "Split right", PERSON_DISPLAY)
            self.assertEqual(page.locator(".prks-tile").count(), 3)
            tour.checkpoint(page, "three-pane-split-right")
            b_box = _tile_box(page, work_b_id)
            person_box = _tile_box(page, person_id)
            self.assertGreater(person_box["x"], b_box["x"])
            self.assertLess(abs(person_box["y"] - b_box["y"]), 4)

            tour.step("Split the Person pane down with a third PDF Work")
            work_c_id = _split_via_pane_menu(self, page, person_id, "Split down", WORK_C_TITLE)
            self.assertEqual(page.locator(".prks-tile").count(), 4)
            tour.checkpoint(page, "three-pane-split-down")
            person_box2 = _tile_box(page, person_id)
            work_c_box = _tile_box(page, work_c_id)
            self.assertGreater(work_c_box["y"], person_box2["y"])
            self.assertLess(abs(work_c_box["x"] - person_box2["x"]), 4)
            self.assertFalse(page.evaluate("() => window.prksWorkspaceCanAddSecondaryLeaf()"))

            tour.step("Resize a nested split")
            nested_ratio_before = page.evaluate(
                """() => {
                    const tree = window.prksWorkspaceSnapshot().secondaryTree;
                    return tree && tree.second && typeof tree.second.ratio === 'number' ? tree.second.ratio : null;
                }"""
            )
            # 3 splitters exist by now: root Main|Secondary, B|(Person/WorkC group), and the
            # deepest Person|WorkC divider -- the one this step targets.
            nested_splitter = page.locator(".prks-splitter").nth(2)
            box = nested_splitter.bounding_box()
            self.assertIsNotNone(box)
            start_x = box["x"] + box["width"] / 2
            start_y = box["y"] + box["height"] / 2
            page.mouse.move(start_x, start_y)
            page.mouse.down()
            page.mouse.move(start_x, start_y + 60, steps=10)
            page.mouse.up()
            if nested_ratio_before is not None:
                page.wait_for_function(
                    """(before) => {
                        const tree = window.prksWorkspaceSnapshot().secondaryTree;
                        return tree && tree.second && Math.abs(tree.second.ratio - before) > 0.01;
                    }""",
                    arg=nested_ratio_before,
                )

            tour.step("Focus each pane in turn")
            for tab_id in (work_a_id, work_b_id, person_id, work_c_id):
                page.locator('.prks-tile[data-prks-tab-id="%s"]' % tab_id).click(position={"x": 20, "y": 60})
                page.wait_for_function(
                    "(id) => window.prksWorkspaceSnapshot().focusedTabId === id", arg=tab_id
                )

            tour.step("Open Details")
            boxes_before_details = {
                tab_id: _tile_box(page, tab_id) for tab_id in (work_a_id, work_b_id, person_id, work_c_id)
            }
            page.locator("#prks-mobile-details-btn").click()
            page.wait_for_function("() => document.body.classList.contains('prks-right-panel-open')")
            self.assertTrue(page.locator("#prks-right-panel-close").is_visible())
            tour.checkpoint(page, "details-overlay")
            for tab_id, before_box in boxes_before_details.items():
                after_box = _tile_box(page, tab_id)
                for prop in ("x", "y", "width", "height"):
                    self.assertAlmostEqual(after_box[prop], before_box[prop], delta=0.5)

            tour.step("Close Details")
            page.locator("#prks-right-panel-close").click()
            page.wait_for_function("() => !document.body.classList.contains('prks-right-panel-open')")

            tour.step("Make Work B the Main pane")
            _open_pane_actions(page, work_b_id)
            _click_workspace_menu(page, "Make main")
            page.wait_for_function("(id) => window.prksWorkspaceSnapshot().mainTabId === id", arg=work_b_id)
            tour.checkpoint(page, "make-main")
            self.assertEqual(page.evaluate("() => window.prksWorkspaceSnapshot().mainTabId"), work_b_id)

            tour.step("Hide split")
            page.locator("#prks-workspace-tile-layout").click()
            page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'stacked'")
            tree_while_hidden = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree")
            self.assertIsNotNone(tree_while_hidden)
            self.assertEqual(page.locator(".prks-tile").count(), 1)
            tour.checkpoint(page, "hidden-split")

            tour.step("Show split")
            page.locator("#prks-workspace-tile-layout").click()
            page.wait_for_function("() => window.prksWorkspaceSnapshot().mode === 'tiled'")
            self.assertEqual(page.locator(".prks-tile").count(), 4)
            restored_tree = page.evaluate("() => window.prksWorkspaceSnapshot().secondaryTree")
            self.assertEqual(restored_tree, tree_while_hidden)
            tour.checkpoint(page, "restored-split")

            tour.step("Close the Person pane")
            page.locator('.prks-tile[data-prks-tab-id="%s"] .prks-tile-header__close' % person_id).click()
            page.wait_for_function("() => document.querySelectorAll('.prks-tile').length === 3")
            self.assertFalse(page.locator('.prks-tile[data-prks-tab-id="%s"]' % person_id).count())

            tour.step("Focus between visible PDF panes")
            main_id_now = page.evaluate("() => window.prksWorkspaceSnapshot().mainTabId")
            other_pdf_id = work_a_id if main_id_now != work_a_id else work_c_id
            # Distinct invariant from the earlier mounted<->warm-suspended transitions above:
            # both these panes are already visible tiles (Main + a Secondary), so per the
            # workspace's own contract, clicking a *visible* pane only focuses it in place (it
            # does not promote it to Main, and does not remount it -- that only applies to a
            # parked tab, or via the pane menu's explicit "Make main"). Stash the live
            # PDF-runtime references in-page (they aren't JSON-serializable); identity is then
            # checked in-browser, returning a plain boolean/dict to Python.
            page.evaluate(
                """(ids) => {
                    window.__prksTourVisibleRt = {
                        main: window.prksGetTabContext(ids.main).getResource('pdf'),
                        other: window.prksGetTabContext(ids.other).getResource('pdf'),
                    };
                }""",
                arg={"main": main_id_now, "other": other_pdf_id},
            )
            work_fetch_before = len(
                [r for r in page.evaluate("() => performance.getEntriesByType('resource').map(e => e.name)")
                 if "/api/works/" in r]
            )
            page.locator('.prks-tile[data-prks-tab-id="%s"]' % other_pdf_id).click(position={"x": 20, "y": 60})
            page.wait_for_function("(id) => window.prksWorkspaceSnapshot().focusedTabId === id", arg=other_pdf_id)
            page.locator('.prks-tile[data-prks-tab-id="%s"]' % main_id_now).click(position={"x": 20, "y": 60})
            page.wait_for_function("(id) => window.prksWorkspaceSnapshot().focusedTabId === id", arg=main_id_now)
            page.locator('.prks-tile[data-prks-tab-id="%s"]' % other_pdf_id).click(position={"x": 20, "y": 60})
            page.wait_for_function("(id) => window.prksWorkspaceSnapshot().focusedTabId === id", arg=other_pdf_id)
            tour.checkpoint(page, "visible-pdf-focus")
            work_fetch_after = len(
                [r for r in page.evaluate("() => performance.getEntriesByType('resource').map(e => e.name)")
                 if "/api/works/" in r]
            )
            self.assertEqual(work_fetch_before, work_fetch_after)
            both_states = page.evaluate(
                """(ids) => {
                    const rt = window.__prksTourVisibleRt;
                    const main = window.prksGetTabContext(ids.main);
                    const other = window.prksGetTabContext(ids.other);
                    return {
                        mainMounted: !!main.mounted,
                        otherMounted: !!other.mounted,
                        sameMainRt: !!rt.main && main.getResource('pdf') === rt.main,
                        sameOtherRt: !!rt.other && other.getResource('pdf') === rt.other,
                    };
                }""",
                arg={"main": main_id_now, "other": other_pdf_id},
            )
            self.assertTrue(both_states["mainMounted"])
            self.assertTrue(both_states["otherMounted"])
            self.assertTrue(both_states["sameMainRt"])
            self.assertTrue(both_states["sameOtherRt"])


class WorkPdfTourTest(_UXTour):
    def test_work_pdf_tour(self):
        with self.open_tour("work-pdf") as (page, _collector, tour):
            tour.step("Open Work A")
            _open_work_from_home(page, WORK_A_TITLE)
            _wait_pdf_viewer(page)
            tour.checkpoint(page, "pdf-open")

            tour.step("Edit Research Notes")
            page.wait_for_selector(".CodeMirror")
            page.locator(".CodeMirror").click()
            page.keyboard.press("Control+A")
            note_text = (
                "# UX Tour Notes\n\nThis note links [[concept:%s]] to the work.\n\n"
                "**Bold text** for the tour.\n" % CONCEPT_PARENT_NAME
            )
            page.keyboard.insert_text(note_text)
            page.locator('[data-prks-role="editor-status"]', has_text="All changes saved").wait_for()
            tour.checkpoint(page, "research-notes-edit")

            tour.step("Edit notes then switch tabs immediately (warm-suspension settlement)")
            page.locator(".CodeMirror").click()
            page.keyboard.press("Control+End")
            page.keyboard.insert_text("\n\nA follow-up sentence typed just before switching away.")
            page.locator("#prks-workspace-new-tab").click()
            page.wait_for_selector("#prks-command-palette:not([hidden])")
            _palette_pick(page, PERSON_DISPLAY)
            page.wait_for_function("() => location.hash.indexOf('#/people/') === 0")
            page.locator(".person-profile__summary").wait_for()

            tour.step("Return to Work A and confirm the note settled")
            page.locator(".prks-workspace-tab", has_text=WORK_A_TITLE).click()
            page.wait_for_function("() => location.hash.indexOf('#/works/') === 0")
            page.wait_for_selector(".CodeMirror")
            page.locator('[data-prks-role="editor-status"]', has_text="All changes saved").wait_for()
            saved_text = page.evaluate(
                """() => {
                    const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                    const wn = ctx && ctx.getResource ? ctx.getResource('workNotes') : null;
                    const ed = wn && wn.editor ? wn.editor : wn;
                    return ed && ed.value ? ed.value() : '';
                }"""
            )
            self.assertIn("A follow-up sentence typed just before switching away.", saved_text)
            tour.checkpoint(page, "warm-return")

            tour.step("Preview Markdown and follow the Concept link")
            page.locator(".EasyMDEContainer button.preview").click()
            preview = page.locator(".editor-preview, .editor-preview-active").first
            preview.wait_for(state="visible")
            self.assertGreaterEqual(preview.locator("strong", has_text="Bold text").count(), 1)
            tour.checkpoint(page, "research-notes-preview")
            concept_link = preview.locator("a.wiki-link-internal", has_text=CONCEPT_PARENT_NAME)
            self.assertEqual(concept_link.count(), 1)
            concept_link.click()
            page.wait_for_function("() => location.hash.indexOf('#/concepts/') === 0")
            page.locator("h2", has_text=CONCEPT_PARENT_NAME).wait_for()

            tour.step("Return to the Work")
            page.go_back()
            page.wait_for_function("() => location.hash.indexOf('#/works/') === 0")
            page.wait_for_selector(".CodeMirror")

            tour.step("Edit metadata, cancel a dirty edit")
            page.locator("#panel-content button", has_text="Edit metadata").click()
            page.locator("#meta-title").wait_for()
            original_title = page.locator("#meta-title").input_value()
            page.locator("#meta-title").fill("Unsaved UX Tour Title")
            page.locator('#panel-content .prks-segmented__btn[data-value="In Progress"]').click()
            tour.checkpoint(page, "metadata-edit")
            page.locator("#panel-content button", has_text="Cancel").click()
            page.locator("#prks-modal-confirm").wait_for()
            page.locator("#prks-modal-confirm-ok").click()
            page.locator("#panel-content .card-title", has_text=original_title).wait_for()

            tour.step("Edit metadata and save")
            page.locator("#panel-content button", has_text="Edit metadata").click()
            page.locator("#meta-title").wait_for()
            page.locator("#meta-title").fill("Saved UX Tour Title")
            page.locator("#inline-save-metadata-btn").click()
            page.locator("#panel-content .card-title", has_text="Saved UX Tour Title").wait_for()

            tour.step("Manage relationships")
            page.locator("#panel-content button", has_text="Manage relationships").click()
            page.locator("#panel-content .work-link-person-btn", has_text="Link person").wait_for()
            tour.checkpoint(page, "relationships")
            page.locator("#panel-content button", has_text="Done").first.click()

            tour.step("Manage tags")
            page.locator("#panel-content button", has_text="Manage tags").click()
            page.locator("#work-tag-search").wait_for()
            tour.checkpoint(page, "tags")
            page.locator("#panel-content button", has_text="Done").last.click()

            tour.step("Open Annotations tab")
            _open_annotations_tab(page)
            page.wait_for_selector("[id^='annotation-']")
            tour.checkpoint(page, "annotation-selected")
            page.locator('#right-panel .tab-btn[data-target="details"]').click()
            page.locator("#panel-content .card-title", has_text="Saved UX Tour Title").wait_for()


class LibraryCreationTourTest(_UXTour):
    def test_library_and_creation_tour(self):
        # YouTube Work creation triggers a best-effort server-side oEmbed metadata
        # fetch to the real youtube.com; point it at an unreachable local proxy so
        # it fails fast and deterministically instead of depending on the public
        # network (the created title is user-supplied either way, so the create
        # flow itself is unaffected). No application code is touched for this.
        with self.open_tour(
            "library", extra_env={"HTTPS_PROXY": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1"}
        ) as (page, _collector, tour):
            # The browser also fetches YouTube's oEmbed endpoint and an embed iframe
            # directly (independent of the server-side fetch the HTTPS_PROXY above
            # denies); stub both so the tour never depends on the public network.
            def _stub_youtube(route):
                url = route.request.url
                if "/oembed" in url:
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body='{"title": "UX Tour Stub Video", "author_name": "UX Tour", "thumbnail_url": ""}',
                    )
                else:
                    route.fulfill(status=200, content_type="text/html", body="<!doctype html><title>stub</title>")

            page.route("**://*.youtube.com/**", _stub_youtube)
            page.route("**://youtube.com/**", _stub_youtube)

            tour.step("Open the Folder library")
            page.locator('#sidebar a.nav-link[href="#/folders"]').click()
            page.wait_for_function("() => location.hash === '#/folders'")
            tour.checkpoint(page, "folder-view")

            tour.step("Open a subfolder from the tree")
            page.locator(".prks-folder-tree__row", has_text="UX Tour Library").first.click()
            page.wait_for_function("() => location.hash.indexOf('#/folders/') === 0")
            page.wait_for_selector(".card-title")

            tour.step("Filter the folder library by name")
            page.locator('#sidebar a.nav-link[href="#/folders"]').click()
            page.wait_for_function("() => location.hash === '#/folders'")
            page.locator("#prks-folder-library-search").fill("UX Tour")
            page.wait_for_function(
                "() => document.querySelectorAll('.prks-folder-tree__row').length > 0"
            )
            tour.checkpoint(page, "search-results")
            page.locator("#prks-folder-library-search-clear").click()

            tour.step("Open the command palette and jump to a Work")
            page.locator("#prks-command-palette-launch").click()
            page.wait_for_selector("#prks-command-palette:not([hidden])")
            tour.checkpoint(page, "command-palette")
            _palette_pick(page, WORK_A_TITLE)
            page.wait_for_function("() => location.hash.indexOf('#/works/') === 0")
            page.locator('#sidebar a.nav-link[href="#/folders"]').click()
            page.wait_for_function("() => location.hash === '#/folders'")

            tour.step("Create a new PDF Work")
            page.locator("#prks-ribbon-new-file").click()
            page.wait_for_selector("#work-modal:not(.hidden)")
            page.fill("#work-title", "UX Tour Created PDF Work")
            page.set_input_files("#work-file", str(MINIMAL_PDF))
            page.locator("#upload-selected-file-name").wait_for(state="visible")
            tour.checkpoint(page, "new-pdf-modal")
            page.locator("#save-work-btn").click()
            page.wait_for_function("() => location.hash.indexOf('#/works/') === 0")
            page.locator(
                ".prks-pdf-toolbar__title, h2.page-header--work-title", has_text="UX Tour Created PDF Work"
            ).first.wait_for()
            tour.checkpoint(page, "created-pdf")

            tour.step("Create a new YouTube Work")
            page.locator('#sidebar a.nav-link[href="#/folders"]').click()
            page.wait_for_function("() => location.hash === '#/folders'")
            page.locator("#prks-ribbon-new-file").click()
            page.wait_for_selector("#work-modal:not(.hidden)")
            page.locator(".prks-kind-toggle__btn[data-kind='video']").click()
            page.locator("#work-video-url").wait_for(state="visible")
            page.fill("#work-title", "UX Tour Created YouTube Work")
            page.fill("#work-video-url", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            tour.checkpoint(page, "new-youtube-modal")
            page.locator("#save-work-btn").click()
            page.wait_for_function("() => location.hash.indexOf('#/works/') === 0", timeout=15000)
            page.locator(
                ".prks-pdf-toolbar__title, h2.page-header--work-title",
                has_text="UX Tour Created YouTube Work",
            ).first.wait_for()
            tour.checkpoint(page, "created-youtube")


class ResearchGraphTourTest(_UXTour):
    def test_research_and_graph_tour(self):
        with self.open_tour("research") as (page, _collector, tour):
            tour.step("Open the Concept index and search")
            _expand_research(page)
            page.locator('#prks-nav-research-children a.nav-link[href="#/concepts"]').click()
            page.wait_for_function("() => location.hash === '#/concepts'")
            page.locator("#prks-concept-rows .prks-research-row").first.wait_for()
            page.locator("#prks-concept-search").fill(CONCEPT_PARENT_NAME)
            page.wait_for_function(
                "() => document.querySelectorAll('#prks-concept-rows .prks-research-row').length >= 1"
            )
            # The child concept can also match here (it names its parent) -- pick the exact row.
            page.locator("#prks-concept-rows .prks-research-row", has_text=re.compile("^" + re.escape(CONCEPT_PARENT_NAME))).first.click()
            page.wait_for_function("() => location.hash.indexOf('#/concepts/') === 0")
            page.locator("h2", has_text=CONCEPT_PARENT_NAME).wait_for()
            tour.checkpoint(page, "concept-detail")

            tour.step("Follow the Concept's child relationship")
            page.locator(".prks-research-row", has_text=CONCEPT_CHILD_NAME).first.click()
            page.wait_for_function("() => location.hash.indexOf('#/concepts/') === 0")
            page.locator("h2", has_text=CONCEPT_CHILD_NAME).wait_for()

            tour.step("Open Positions and Arguments/Stances")
            page.locator('#sidebar a.nav-link[href="#/positions"]').click()
            page.wait_for_function("() => location.hash === '#/positions'")
            page.locator("#prks-position-rows .prks-research-row", has_text=TOUR_POSITION_NAME).first.click()
            page.wait_for_function("() => location.hash.indexOf('#/positions/') === 0")
            page.locator("h2", has_text=TOUR_POSITION_NAME).wait_for()
            tour.checkpoint(page, "argument-detail")
            page.locator('#sidebar a.nav-link[href="#/arguments"]').click()
            page.wait_for_function("() => location.hash === '#/arguments'")
            page.locator("#prks-argument-rows .prks-research-row", has_text=TOUR_STANCE_NAME).first.click()
            page.wait_for_function("() => location.hash.indexOf('#/arguments/') === 0")
            page.locator("h2", has_text=TOUR_STANCE_NAME).wait_for()

            tour.step("View this Stance in the Graph")
            page.locator("#prks-arg-view-graph").click()
            page.wait_for_function("() => location.hash.indexOf('#/graph') === 0")
            page.wait_for_function(
                "() => { const d = window.prksGetResearchGraphDebug && window.prksGetResearchGraphDebug();"
                " return !!(d && d.cy && d.cy.nodes().length > 0); }"
            )
            tour.checkpoint(page, "graph-overview")

            tour.step("Use Graph Find")
            page.locator('[data-prks-role="graph-find"]').fill(CONCEPT_PARENT_NAME)
            page.locator(".research-graph__find-hit", has_text=CONCEPT_PARENT_NAME).click()
            page.locator("#prks-graph-inspector-title", has_text=CONCEPT_PARENT_NAME).wait_for()

            tour.step("Open Filters and hide People")
            page.locator('[data-prks-role="graph-filters-toggle"]').click()
            people_filter = page.locator('[data-graph-filter="people"]')
            people_filter.wait_for(state="visible")
            if people_filter.is_checked():
                people_filter.uncheck()
            tour.checkpoint(page, "graph-filtered")
            page.locator('[data-prks-role="graph-filters-toggle"]').click()

            tour.step("Open Legend")
            page.locator('[data-prks-role="graph-legend-toggle"]').click()
            page.wait_for_function(
                "() => document.querySelector('[data-prks-role=\"graph-legend-toggle\"]')"
                ".getAttribute('aria-expanded') === 'true'"
            )
            page.locator('[data-prks-role="graph-legend-toggle"]').click()

            tour.step("Select a Work node and inspect it")
            work_node_id = page.evaluate(
                """() => {
                    const d = window.prksGetResearchGraphDebug && window.prksGetResearchGraphDebug();
                    const cy = d && d.cy;
                    if (!cy) return null;
                    const n = cy.nodes().filter(n => n.id().indexOf('work:') === 0)[0];
                    return n ? n.id() : null;
                }"""
            )
            self.assertIsNotNone(work_node_id)
            _click_graph_node(page, work_node_id)
            page.locator("#prks-graph-inspector-title").wait_for()
            tour.checkpoint(page, "graph-work-inspector")
            self.assertEqual(page.locator("#prks-graph-inspector .doc-meta-card").count(), 0)
            neighbor_rows = page.locator("#prks-graph-inspector .research-graph__neighbor")
            if neighbor_rows.count():
                self.assertEqual(neighbor_rows.first.evaluate("el => el.tagName"), "BUTTON")

            # Graph edges are Cytoscape canvas pixels, not DOM nodes -- there is no
            # separate real-UI control for "select this edge" (Find only searches
            # nodes), so selection here goes through the same window.selectGraphEdge
            # hook the existing E2E suite itself uses for edge selection (e.g.
            # ResearchGraphContextTests). This is instrumentation of an interaction
            # that has no other real control, not a substitute for one that exists.
            tour.step("Select an edge")
            edge_id = page.evaluate(
                """(nodeId) => {
                    const d = window.prksGetResearchGraphDebug && window.prksGetResearchGraphDebug();
                    const cy = d && d.cy;
                    const node = cy.getElementById(nodeId);
                    const edge = node.connectedEdges()[0];
                    return edge && edge.length ? edge.id() : null;
                }""",
                arg=work_node_id,
            )
            if edge_id:
                page.evaluate("(eid) => { window.selectGraphEdge(eid); }", arg=edge_id)
                page.locator("#prks-graph-inspector-title").wait_for()
                tour.checkpoint(page, "graph-edge-inspector")

            tour.step("Open the selected Work")
            _click_graph_node(page, work_node_id)
            page.locator("#prks-graph-open").wait_for()
            page.locator("#prks-graph-open").click()
            page.wait_for_function("() => location.hash.indexOf('#/works/') === 0")
            page.wait_for_selector(".CodeMirror")

            tour.step("Return to Graph and close/reopen Details")
            page.locator('#sidebar a.nav-link[href="#/graph"]').click()
            page.wait_for_function("() => location.hash.indexOf('#/graph') === 0")
            page.wait_for_function(
                "() => { const d = window.prksGetResearchGraphDebug && window.prksGetResearchGraphDebug();"
                " return !!(d && d.cy && d.cy.nodes().length > 0); }"
            )
            _click_graph_node(page, work_node_id)
            page.locator("#prks-graph-inspector-title").wait_for()
            self.assertTrue(page.locator("[data-graph-clear-selection]").is_visible())
            tour.checkpoint(page, "graph-details-closed")

            tour.step("Fit and Reset layout")
            page.locator('[data-prks-role="graph-fit"]').click()
            page.locator('[data-prks-role="graph-reset"]').click()
            page.wait_for_function(
                "() => { const d = window.prksGetResearchGraphDebug && window.prksGetResearchGraphDebug();"
                " return !!(d && d.cy && d.cy.nodes().length > 0); }"
            )

            tour.step("Clear selection")
            page.locator("[data-graph-clear-selection]").click()
            self.assertEqual(page.evaluate("() => window.getSelectedGraphNodeId()"), "")


class PeopleGroupsTourTest(_UXTour):
    def test_people_and_groups_tour(self):
        with self.open_tour("people-groups") as (page, _collector, tour):
            tour.step("Open People index and search")
            page.locator('#sidebar a.nav-link[href="#/people"]').click()
            page.wait_for_function("() => location.hash === '#/people'")
            tour.checkpoint(page, "people-index")
            search = page.locator("#prks-people-search, [data-prks-role='people-search']").first
            if search.count():
                search.fill(PERSON_DISPLAY)
                page.wait_for_function(
                    "(n) => document.querySelectorAll('.prks-people-list__title').length >= 1",
                )

            tour.step("Open the Person profile")
            page.locator(".prks-people-list__title", has_text=PERSON_DISPLAY).click()
            page.wait_for_function("() => location.hash.indexOf('#/people/') === 0")
            page.locator(".person-profile__summary").wait_for()
            tour.checkpoint(page, "person-profile")

            tour.step("Edit the Person's profile and save")
            page.locator('.person-sidebar-summary .prks-btn--primary', has_text="Edit profile").click()
            page.wait_for_selector("#pd-first-name")
            page.locator("#pd-aliases").fill("UX Tour Alias")
            tour.checkpoint(page, "person-edit")
            page.locator("#pd-save-btn").click()
            page.locator('.person-sidebar-summary .prks-btn--primary', has_text="Edit profile").wait_for()

            tour.step("View this Person in the Graph and return")
            page.locator("#prks-person-view-graph").click()
            page.wait_for_function("() => location.hash.indexOf('#/graph') === 0")
            page.wait_for_function(
                "() => { const d = window.prksGetResearchGraphDebug && window.prksGetResearchGraphDebug();"
                " return !!(d && d.cy && d.cy.nodes().length > 0); }"
            )
            page.locator('#sidebar a.nav-link[href="#/people"]').click()
            page.wait_for_function("() => location.hash === '#/people'")

            tour.step("Open Person Groups and the hierarchy")
            people_toggle = page.locator('[data-nav-disclosure-toggle="people"]')
            if people_toggle.get_attribute("aria-expanded") != "true":
                people_toggle.click()
            page.locator('#prks-nav-people-children a.nav-link[href="#/people/groups"]').click()
            page.wait_for_function("() => location.hash === '#/people/groups'")
            tour.checkpoint(page, "groups-tree")

            tour.step("Filter groups by name")
            group_search = page.locator("#prks-group-library-search")
            if group_search.count():
                group_search.fill("UX Tour")
                page.wait_for_function(
                    "() => document.querySelectorAll('.prks-group-tree__row').length > 0"
                )
                page.locator("#prks-group-library-search-clear").click()

            tour.step("Open the parent Group")
            page.locator(".prks-group-tree__row", has_text=GROUP_PARENT_NAME).first.click()
            page.wait_for_function("() => location.hash.indexOf('#/people/groups/') === 0")
            tour.checkpoint(page, "group-detail")

            tour.step("Edit Group metadata")
            page.locator("#panel-content button", has_text="Edit group").click()
            page.locator("#gd-name").wait_for()
            page.locator("#gd-name").fill(GROUP_PARENT_NAME + " (edited)")
            page.locator(".group-sidebar__sticky-actions button", has_text="Save").click()
            page.locator("#panel-content .card-title, h2", has_text="(edited)").first.wait_for()

            tour.step("Manage members: add and remove")
            page.locator(".group-detail__section-head button", has_text="Manage members").click()
            page.locator("#group-add-member-search").wait_for()
            tour.checkpoint(page, "manage-members")
            page.locator("#group-add-member-search").fill(PERSON2_DISPLAY)
            page.locator("#group-add-member-results .result-item--person-pick").first.click()
            page.locator("#group-add-member-btn").click()
            page.locator(".prks-people-list__title", has_text=PERSON2_DISPLAY).wait_for()
            page.locator("[data-remove-member]").first.click()
            confirm = page.locator("#prks-modal-confirm")
            if confirm.is_visible():
                page.locator("#prks-modal-confirm-ok").click()
            page.locator(".group-detail__section-head button", has_text="Done").click()
            self.assertEqual(page.locator("#group-add-member-search").count(), 0)

            tour.step("Open the subgroup")
            page.locator(".route-sidebar__link, a", has_text=GROUP_CHILD_NAME).first.click()
            page.wait_for_function("() => location.hash.indexOf('#/people/groups/') === 0")
            page.locator("h2, .card-title", has_text=GROUP_CHILD_NAME).first.wait_for()


class OrganizationProgressTourTest(_UXTour):
    def test_organization_progress_tour(self):
        with self.open_tour("organization") as (page, _collector, tour):
            tour.step("Open Playlists and a Playlist's detail")
            page.locator('#sidebar a.nav-link[href="#/playlists"]').click()
            page.wait_for_function("() => location.hash === '#/playlists'")
            page.locator(".playlists-page__list-item", has_text=PLAYLIST_TITLE).first.click()
            page.wait_for_function("() => location.hash.indexOf('#/playlists/') === 0")
            page.locator(".prks-playlist-detail").wait_for()
            tour.checkpoint(page, "playlist-detail")

            tour.step("Open a Work from the Playlist")
            page.locator(".prks-playlist-item__body--link[data-pl-nav]").first.click()
            page.wait_for_function("() => location.hash.indexOf('#/works/') === 0")
            page.wait_for_selector(".CodeMirror")

            tour.step("Visit each Progress status")
            progress_toggle = page.locator('[data-nav-disclosure-toggle="progress"]')
            progress_children = page.locator("#prks-nav-progress-children")
            for status in ("Not Started", "Planned", "In Progress", "Paused", "Completed"):
                if progress_toggle.get_attribute("aria-expanded") != "true":
                    progress_toggle.click()
                progress_children.wait_for(state="visible")
                progress_children.locator('a[data-status="%s"]' % status).click()
                page.wait_for_function(
                    "(s) => decodeURIComponent(location.hash) === '#/progress?status=' + s", arg=status
                )
                page.wait_for_selector("h2, .page-header__title")
            tour.checkpoint(page, "progress-completed")

            tour.step("Visit the Processing inbox")
            page.locator('#sidebar a.nav-link[href="#/processing-files"]').click()
            page.wait_for_function("() => location.hash === '#/processing-files'")
            page.wait_for_selector("h2, .page-header__title")
            tour.checkpoint(page, "processing-inbox")
            # Actual file ingestion (detect metadata, import/save) needs a real
            # PRKS_FOR_PROCESSING_DIR watch folder wired into AppServer -- covered
            # by E2E/API instead (see COVERAGE.md); this tour only confirms the
            # page itself loads cleanly and is reachable from the sidebar.


class SettingsTourTest(_UXTour):
    def test_settings_tour(self):
        with self.open_tour("settings") as (page, _collector, tour):
            tour.step("Open Settings")
            page.locator("button.settings-btn").click()
            page.wait_for_selector("#settings-modal:not(.hidden)")
            tour.checkpoint(page, "settings-general")

            tour.step("General: edit the annotation author, then restore it")
            original_author = page.locator("#annotation-author-input").input_value()
            page.locator("#annotation-author-input").fill("UX Tour Author")
            page.locator("#annotation-author-input").fill(original_author)

            tour.step("Reading & layout: toggle a setting and restore it")
            page.locator("#prks-settings-tab-reading").click()
            page.wait_for_selector("#prks-settings-panel-reading:not([hidden])")
            tour.checkpoint(page, "settings-reading")
            remember_page = page.locator("#prks-setting-pdf-remember-page")
            before_checked = remember_page.get_attribute("aria-checked")
            remember_page.click()
            page.wait_for_function(
                "(before) => document.getElementById('prks-setting-pdf-remember-page')"
                ".getAttribute('aria-checked') !== before",
                arg=before_checked,
            )
            remember_page.click()
            page.wait_for_function(
                "(before) => document.getElementById('prks-setting-pdf-remember-page')"
                ".getAttribute('aria-checked') === before",
                arg=before_checked,
            )

            tour.step("Export: toggle a BibTeX field and restore it")
            page.locator("#prks-settings-tab-export").click()
            page.wait_for_selector("#prks-settings-panel-export:not([hidden])")
            tour.checkpoint(page, "settings-export")
            first_field = page.locator("#prks-bibtex-export-fields button.prks-toggle").first
            total = page.locator("#prks-bibtex-export-fields button.prks-toggle").count()
            summary_before = page.locator("#prks-bibtex-export-summary").inner_text()
            first_field.click()
            page.wait_for_function(
                "(t) => document.getElementById('prks-bibtex-export-summary').textContent"
                ".indexOf((t - 1) + ' of ' + t) !== -1",
                arg=total,
            )
            first_field.click()
            page.wait_for_function(
                "(text) => document.getElementById('prks-bibtex-export-summary').textContent === text",
                arg=summary_before,
            )

            tour.step("Backup: create a backup")
            page.locator("#prks-settings-tab-backup").click()
            page.wait_for_selector("#prks-settings-panel-backup:not([hidden])")
            tour.checkpoint(page, "settings-backup")
            download_btn = page.locator("#prks-backup-download-btn")
            if download_btn.count():
                with page.expect_download(timeout=15000) as download_info:
                    download_btn.click()
                download = download_info.value
                self.assertTrue(download.suggested_filename)

            tour.step("Maintenance: rebuild the PDF text index")
            page.locator("#prks-settings-tab-maintenance").click()
            page.wait_for_selector("#prks-settings-panel-maintenance:not([hidden])")
            tour.checkpoint(page, "settings-maintenance")
            page.locator("#prks-reindex-pdf-text-btn").click()
            page.wait_for_function(
                "() => (document.getElementById('prks-reindex-pdf-text-status').textContent || '').length > 0",
                timeout=15000,
            )

            tour.step("Diagnostics: confirm lazy load, then Refresh")
            self.assertEqual(_diagnostics_requests(page), [])
            page.locator("#prks-settings-tab-diagnostics").click()
            page.wait_for_function(
                "() => performance.getEntriesByType('resource').some("
                "e => e.name.indexOf('/api/diagnostics/performance') !== -1)"
            )
            self.assertEqual(len(_diagnostics_requests(page)), 1)
            tour.checkpoint(page, "settings-diagnostics")
            page.locator("#prks-perf-refresh-btn").click()
            page.wait_for_function(
                "() => performance.getEntriesByType('resource').filter("
                "e => e.name.indexOf('/api/diagnostics/performance') !== -1).length >= 2"
            )

            tour.step("Close Settings")
            page.locator("#settings-modal .close-btn").click()
            page.wait_for_selector("#settings-modal", state="hidden")
