"""Structural + Node regressions for stacked workspace tabs."""
import os
import re
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_INDEX = os.path.join(_FRONTEND, "index.html")
_WS = os.path.join(_FRONTEND, "js", "workspace-tabs.js")
_TILING = os.path.join(_FRONTEND, "js", "workspace-tiling.js")
_NAV = os.path.join(_FRONTEND, "js", "navigation.js")
_APP = os.path.join(_FRONTEND, "js", "app.js")
_COORD = os.path.join(_FRONTEND, "js", "request-coordinator.js")
_API = os.path.join(_FRONTEND, "js", "api.js")
_DESIGN = os.path.join(_PROJECT_DIR, "DESIGN.md")
_AGENTS = os.path.join(_PROJECT_DIR, "AGENTS.md")
_WIKI_WORKSPACE = os.path.join(
    _PROJECT_DIR, "docs", "wiki", "Workspace-Tabs-and-Split-View.md"
)
_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_workspace_tabs_selftest.js")
_TAB_STATUS_WARM_RUNNER = os.path.join(
    _PROJECT_DIR, "tests", "browser", "run_workspace_tab_status_warm_selftest.js"
)
_TREE = os.path.join(_FRONTEND, "js", "workspace-tree.js")
_TREE_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_workspace_tree_selftest.js")
_PERSIST = os.path.join(_FRONTEND, "js", "workspace-persistence.js")
_PERSIST_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_workspace_persistence_selftest.js")
_DRAG = os.path.join(_FRONTEND, "js", "workspace-drag.js")
_DRAG_RUNNER = os.path.join(_PROJECT_DIR, "tests", "browser", "run_workspace_drag_selftest.js")
_SPLIT = os.path.join(_FRONTEND, "js", "workspace-split.js")
_MENU = os.path.join(_FRONTEND, "js", "workspace-tab-menu.js")
_UI = os.path.join(_FRONTEND, "js", "ui.js")
_CSS = os.path.join(_FRONTEND, "css", "style.css")

_HASH_ASSIGN_RE = re.compile(r"(?:window\.)?location\.hash\s*=(?!=)")
_OPEN_BLANK_RE = re.compile(r"""window\.open\s*\([^)]*['_"]_blank['_"]""")

_LOW_LEVEL_HASH_FILES = {
    os.path.join(_FRONTEND, "js", "navigation.js"),
    os.path.join(_FRONTEND, "js", "workspace-tabs.js"),
    os.path.join(_FRONTEND, "js", "app.js"),
}


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _frontend_js_files():
    root = os.path.join(_FRONTEND, "js")
    out = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.endswith(".js"):
                out.append(os.path.join(dirpath, name))
    out.sort()
    return out


def _line_at(src: str, index: int) -> str:
    start = src.rfind("\n", 0, index) + 1
    end = src.find("\n", index)
    if end < 0:
        end = len(src)
    return src[start:end]


class FrontendWorkspaceTabsTests(unittest.TestCase):
    def test_workspace_module_load_order(self):
        html = _read(_INDEX)
        nav_at = html.find('src="/js/navigation.js"')
        ws_at = html.find('src="/js/workspace-tabs.js"')
        tc_at = html.find('src="/js/tab-context.js"')
        tree_at = html.find('src="/js/workspace-tree.js"')
        persist_at = html.find('src="/js/workspace-persistence.js"')
        tiling_at = html.find('src="/js/workspace-tiling.js"')
        split_at = html.find('src="/js/workspace-split.js"')
        menu_at = html.find('src="/js/workspace-tab-menu.js"')
        drag_at = html.find('src="/js/workspace-drag.js"')
        coord_at = html.find('src="/js/request-coordinator.js"')
        api_at = html.find('src="/js/api.js"')
        app_at = html.find('src="/js/app.js"')
        self.assertNotEqual(nav_at, -1)
        self.assertNotEqual(ws_at, -1)
        self.assertNotEqual(tree_at, -1)
        self.assertNotEqual(persist_at, -1)
        self.assertNotEqual(tiling_at, -1)
        self.assertNotEqual(split_at, -1)
        self.assertNotEqual(menu_at, -1)
        self.assertNotEqual(drag_at, -1)
        self.assertNotEqual(coord_at, -1)
        self.assertNotEqual(api_at, -1)
        self.assertNotEqual(app_at, -1)
        # Module load-order contract: workspace-tabs, workspace-tree/tiling, workspace-split,
        # workspace-tab-menu, workspace-drag, ..., app. Drag orchestrates the other workspace
        # modules' canonical/DOM APIs, so it must load after all of them and before app.js wires
        # up initialization.
        self.assertLess(nav_at, ws_at)
        self.assertLess(ws_at, tc_at)
        self.assertLess(tree_at, persist_at)
        self.assertLess(persist_at, ws_at)
        self.assertLess(tree_at, ws_at)
        self.assertLess(tc_at, tiling_at)
        self.assertLess(tiling_at, split_at)
        self.assertLess(split_at, menu_at)
        self.assertLess(menu_at, drag_at)
        self.assertLess(drag_at, app_at)
        self.assertLess(tiling_at, app_at)
        self.assertLess(ws_at, app_at)
        self.assertLess(coord_at, api_at)
        self.assertLess(api_at, app_at)
        self.assertTrue(os.path.isfile(_WS))
        self.assertTrue(os.path.isfile(_TILING))
        self.assertTrue(os.path.isfile(_SPLIT))
        self.assertTrue(os.path.isfile(_MENU))
        self.assertTrue(os.path.isfile(_DRAG))
        self.assertTrue(os.path.isfile(_PERSIST))
        self.assertTrue(os.path.isfile(_PERSIST_RUNNER))
        self.assertTrue(os.path.isfile(_RUNNER))
        self.assertTrue(os.path.isfile(_COORD))

    def test_tab_strip_markup(self):
        html = _read(_INDEX)
        self.assertIn('id="prks-workspace-tabs"', html)
        self.assertIn('class="prks-workspace-tabs"', html)
        self.assertIn('role="tablist"', html)
        self.assertIn('id="prks-workspace-tab-overflow"', html)
        self.assertIn('id="prks-workspace-new-tab"', html)
        self.assertIn('id="prks-workspace-tile-layout"', html)
        self.assertIn('id="prks-workspace-live"', html)
        self.assertIn('id="prks-workspace-status"', html)
        self.assertIn('id="prks-workspace-status" class="prks-workspace-status meta-row" hidden aria-hidden="true"', html)
        self.assertIn("prks-workspace-tabs-shell", html)
        src = _read(_WS)
        self.assertIn("prks-workspace-tab__activate", src)
        self.assertIn("prks-workspace-tab__close", src)
        self.assertIn('role="tab"', src)
        self.assertIn("NARROW_SPLIT_MESSAGE", src)
        self.assertIn("showWorkspaceStatus", src)
        self.assertIn("wider workspace", src)
        css = _read(_CSS)
        self.assertIn(".prks-workspace-status", css)
        self.assertNotIn("animation:", css[css.find(".prks-workspace-status") : css.find(".prks-workspace-status") + 400])
        self.assertNotIn("box-shadow", css[css.find(".prks-workspace-status") : css.find(".prks-workspace-status") + 400])

    def test_dense_tiled_shell_contract(self):
        html = _read(_INDEX)
        css = _read(_CSS)
        ui = _read(_UI)
        tiling = _read(_TILING)

        self.assertIn('id="prks-sidebar-collapse-btn"', html)
        self.assertIn('id="prks-mobile-details-btn"', html)
        self.assertIn("app-container--tiled", css)
        self.assertIn("flex: 0 0 54px", css)
        self.assertIn("margin-right: -196px", css)
        self.assertIn("position: fixed", css[css.find("#app-container.app-container--tiled #right-panel") :])
        self.assertIn("prksSyncDenseWorkspaceShell(visualTiled)", tiling)
        self.assertIn("prksIsTiledWorkspace", ui)
        self.assertIn("prksOpenSidebarDrawer", ui)
        self.assertIn("prksOpenRightPanelOverlay", ui)

        # Desktop tiled Details stays non-modal: only small-screen path gets backdrop state.
        right_open = ui[ui.find("function prksOpenRightPanelOverlay") : ui.find("function prksToggleSidebarDrawer")]
        self.assertIn("if (prksIsSmallScreen())", right_open)
        self.assertIn("prksSetOverlayBackdropVisible(false)", right_open)

        for selector in (
            '.prks-workspace-tab.is-main,\n.prks-workspace-tab[aria-current="page"]',
            ".prks-workspace-tab.is-tiled",
            ".prks-workspace-tab.is-tiled.is-focused",
        ):
            block = css.split(selector + " {", 1)[1].split("}", 1)[0]
            self.assertNotIn("box-shadow", block, selector)

    def test_work_pdf_density_contract(self):
        """Work/PDF density slice: tiled chrome suppression, compact collapsed Notes,
        drawer vs sidecar composition, tiled PDF min-height."""
        css = _read(_CSS)
        works = _read(os.path.join(_FRONTEND, "js", "components", "works.js"))
        design = _read(_DESIGN)
        index = _read(_INDEX)

        self.assertIn(
            ".prks-workspace-canvas--tiled .prks-nav-back-row--work",
            css,
        )
        back_block = css.split(".prks-workspace-canvas--tiled .prks-nav-back-row--work {", 1)[1].split("}", 1)[0]
        self.assertIn("display: none", back_block)

        self.assertIn(".prks-workspace-canvas--tiled .prks-pdf-toolbar__title", css)
        title_block = css.split(".prks-workspace-canvas--tiled .prks-pdf-toolbar__title {", 1)[1].split("}", 1)[0]
        self.assertIn("display: none", title_block)

        self.assertIn(".prks-workspace-canvas--tiled .prks-pdf-viewer", css)
        pdf_block = css.split(".prks-workspace-canvas--tiled .prks-pdf-viewer {", 1)[1].split("}", 1)[0]
        self.assertIn("min-height: 0", pdf_block)

        # Collapsed Notes keep a compact save/sync cue — never hide all status.
        collapsed = css[
            css.find(".document-view--work .work-workspace--notes-collapsed .work-editor-status") :
        ]
        self.assertIn("text-overflow: ellipsis", collapsed)
        self.assertNotIn(
            ".document-view--work .work-workspace--notes-collapsed .work-editor-status {\n    display: none",
            css,
        )

        self.assertIn("work-workspace--notes-drawer", works)
        self.assertIn("work-workspace--side", works)
        self.assertIn("prksGetMobileWorkNotesRightEnabled", works)
        # Tiled expanded Notes must be drawer always — Settings preference is stacked-only.
        self.assertIn("inTiled", works)
        self.assertIn("wantDrawer", works)
        layout = works[works.find("function prksReapplyWorkNotesSplitLayout") : works.find("window.prksReapplyWorkNotesSplitLayout")]
        self.assertIn("!inTiled &&", layout)
        self.assertIn("Settings cannot override", layout)
        self.assertNotIn(
            "mobileForceSide &&",
            layout.replace(
                "!inTiled && width > 0 && (!isNarrowWidth || mobileForceSide)",
                "",
            ),
            "force-side must not apply outside the !inTiled gate",
        )
        self.assertIn("PRKS_WORKSPACE_NARROW_PX", layout)
        self.assertIn("prksWorkspaceWidthIsNarrow", layout)
        self.assertIn("isNarrowWidth", layout)

        # Works consumer: exact 720px must be narrow (drawer, not side), matching CSS
        # max-width inclusive semantics. Node selftest overrides clientWidth and restores it.
        notes_layout = os.path.join(
            _PROJECT_DIR, "tests", "browser", "run_work_notes_layout_selftest.js"
        )
        proc = subprocess.run(
            ["node", notes_layout],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=_PROJECT_DIR,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertIn("exact 720px stacked uses drawer", proc.stdout)
        self.assertIn("exact 720px stacked is not side", proc.stdout)

        self.assertIn("Research notes beside PDF when narrow", index)
        self.assertNotIn("Research notes beside PDF on mobile", index)
        self.assertIn("Research notes beside PDF when narrow", design)
        self.assertIn("compact save/sync cue", design)
        self.assertIn("Work / PDF composition (density)", design)
        self.assertIn("does **not** apply in tiled mode", design)
        self.assertNotIn("Scope note (PR #4)", design)
        self.assertNotIn("shipped first", design)

    def test_tiled_v1_state_contract(self):
        src = _read(_WS)
        tiling = _read(_TILING)
        self.assertIn("function createPrksWorkspaceTabs", src)
        self.assertIn("'stacked'", src)
        self.assertIn("'tiled'", src)
        self.assertIn("mainTabId", src)
        self.assertIn("focusedTabId", src)
        self.assertIn("secondaryTree", src)
        self.assertIn("type: 'leaf'", src)
        # Recursive split nodes are now legitimate (Recursive Secondary Splits); the tree
        # helper module owns split-node construction/mutation, workspace-tabs.js only clones.
        self.assertIn("root.splitLeaf(", src)
        self.assertIn("root.removeLeaf(", src)
        self.assertIn("root.validateTree(", src)
        self.assertIn("PRKS_MAX_VISIBLE_TABS", src)
        self.assertNotIn("splitRatio", src)
        self.assertNotIn("localStorage", src)
        self.assertNotIn("sessionStorage", src)
        self.assertNotIn("indexedDB", src)
        self.assertIn("prksWorkspaceFocusTab", src)
        self.assertIn("prksWorkspaceMakeMain", src)
        self.assertIn("target: 'tile'", src)
        self.assertIn("prksWorkspaceHostForTab", src)
        self.assertIn("prksWorkspaceFindTabByRoute", src)
        self.assertIn("prksWorkspaceCloseOtherTabs", src)
        self.assertIn("prksWorkspaceCloseTabsToTheRight", src)
        self.assertIn("prks-workspace-tab__split", src)
        self.assertIn("syncTrailingTabStops", src)
        self.assertIn("Open split view", _read(_INDEX))
        refresh = src[src.find("function prksWorkspaceRefreshTabStatus") : src.find("function revealWorkspaceTab")]
        self.assertIn("updateTabOverflow()", refresh)
        kind = src[src.find("function tabStatusKind") : src.find("function statusLabel")]
        self.assertLess(kind.find("return 'error'"), kind.find("return 'saving'"))
        self.assertLess(kind.find("return 'saving'"), kind.find("return 'drafting'"))
        self.assertIn("prks-workspace-canvas", tiling)
        self.assertIn("observedCanvas", tiling)
        self.assertIn("prks-tile--main", tiling)
        self.assertIn("prks-tile--secondary", tiling)
        self.assertIn("prks-tile--focused", tiling)
        self.assertNotIn("type: 'split'", tiling)
        self.assertNotIn("aria-valuenow", tiling)
        self.assertNotIn("pointermove", tiling)
        self.assertNotIn("is-dragging", tiling)
        self.assertNotIn("localStorage", tiling)
        self.assertNotIn("buildSplitDropdown", tiling)
        self.assertNotIn("prks-tile-header__make-main", tiling)
        self.assertNotIn("prks-tile-header__split-menu", tiling)
        self.assertIn("prks-tile-header__menu", tiling)
        self.assertIn("prksWorkspaceOpenTabMenu", tiling)
        self.assertIn("Pane actions", tiling)
        menu = _read(os.path.join(_FRONTEND, "js", "workspace-tab-menu.js"))
        self.assertNotIn("type: 'split'", menu)
        self.assertIn("Open in split view", menu)
        self.assertIn("Make main", menu)
        self.assertIn("menuItems.push(split)", menu)
        self.assertIn("menuItems.push(close)", menu)
        self.assertIn("role', 'menuitem'", menu)
        nav = _read(_NAV)
        self.assertIn("function prksRouteSupportsTile", nav)
        self.assertIn("function prksPublishMainShell", nav)

    def test_navigate_target_contract(self):
        src = _read(_WS)
        self.assertIn("new-tab", src)
        self.assertIn("'tile'", src)
        self.assertIn("activate", src)
        nav = _read(_NAV)
        self.assertIn("prksWorkspaceNavigate", nav)
        self.assertIn("prksResolvedRouteTitle", nav)
        self.assertIn("prksRouteTabIcon", nav)
        self.assertIn("prksRouteSupportsTile", nav)

    def test_old_browser_new_tab_helpers_removed(self):
        leftovers = []
        for path in _frontend_js_files():
            src = _read(path)
            if "prksOpenHashInNewTab" in src or "prksMaybeOpenHashInNewTab" in src:
                leftovers.append(os.path.relpath(path, _PROJECT_DIR))
        self.assertEqual(leftovers, [], leftovers)
        app = _read(_APP)
        self.assertNotIn("window.open(", app)

    def test_no_window_open_blank_for_internal_hash(self):
        leftovers = []
        for path in _frontend_js_files():
            src = _read(path)
            if _OPEN_BLANK_RE.search(src):
                leftovers.append(os.path.relpath(path, _PROJECT_DIR))
        self.assertEqual(leftovers, [], leftovers)

    def test_feature_files_do_not_assign_location_hash(self):
        leftovers = []
        for path in _frontend_js_files():
            if path in _LOW_LEVEL_HASH_FILES:
                continue
            src = _read(path)
            for match in _HASH_ASSIGN_RE.finditer(src):
                line = _line_at(src, match.start()).strip()
                leftovers.append("%s: %s" % (os.path.relpath(path, _PROJECT_DIR), line))
        self.assertEqual(
            leftovers,
            [],
            "feature files must not assign location.hash:\n" + "\n".join(leftovers),
        )

    def test_low_level_hash_writes_remain_narrow(self):
        app = _read(_APP)
        self.assertIn("prksCanLeaveCurrentRoute", app)
        self.assertIn("prksHasPendingWorkAnnotationSync", app)
        self.assertIn("workspaceSwitch", app)
        ws = _read(_WS)
        self.assertIn("createPrksWorkspaceTabs", ws)
        works = _read(os.path.join(_FRONTEND, "js", "components", "works.js"))
        self.assertIn("prksFlushPendingWorkResearchNotes", works)
        self.assertIn("prksSaveWorkNoteDurably", works)
        self.assertIn("latestSaveToken", works)
        self.assertIn("saveSequence", works)

    def test_dirty_draft_leave_contract_is_central_and_async(self):
        app = _read(_APP)
        ui = _read(_UI)
        guard = app.split("function prksCanLeaveTabContext(ctx, nextHash)", 1)[1].split(
            "function prksCanLeaveCurrentRoute", 1
        )[0]
        self.assertIn("prksFlushPendingWorkResearchNotes(ctx)", guard)
        self.assertIn("prksFlushPendingPrivateNotes(ctx)", guard)
        self.assertIn("window.confirm(", guard)
        self.assertIn("prksSyncPersonProfileDraftFromEditor", guard)
        self.assertIn("prksPersonProfileDraftIsDirty(ctx, person)", guard)
        self.assertIn("prksCaptureWorkMetaDraft(ctx)", guard)
        self.assertIn("prksWorkMetaDraftIsDirty(ctx, work)", guard)
        self.assertEqual(guard.count("prksConfirmUnsavedRouteLeave({"), 2)
        self.assertIn("function prksConfirmUnsavedRouteLeave(options)", ui)
        confirm = ui.split("function prksConfirmUnsavedRouteLeave(options)", 1)[1].split(
            "function prksBindModalConfirmOnce", 1
        )[0]
        self.assertIn("cancelLabel: 'Keep editing'", confirm)
        self.assertIn("confirmLabel: 'Discard changes'", confirm)
        self.assertNotIn("window.confirm", confirm)

        ws = _read(_WS)
        await_leave = ws.split("function awaitLeave(tabId, nextHash)", 1)[1].split(
            "function enforceInvariants", 1
        )[0]
        self.assertIn("Promise.resolve(canLeave(tabId, nextHash))", await_leave)
        history = ws.split("function bindHistory()", 1)[1].split("function ensureProduction", 1)[0]
        self.assertIn("pendingHistoryNavigation", history)
        self.assertIn("void pending.then(finishHashChange)", history)

    def test_docs_and_design_contract(self):
        design = _read(_DESIGN)
        self.assertIn("Local / content tabs versus workspace tabs", design)
        self.assertIn("main/master tile owns the left column", design)
        self.assertIn("Main is not the same state as focus", design)
        self.assertIn("Never flash Home", design)
        self.assertIn(".prks-workspace-tabs", design)
        self.assertIn("stacked", design.lower())
        agents = _read(_AGENTS)
        self.assertIn("prksNavigate", agents)
        self.assertIn("Parked tabs", agents)
        self.assertIn("Secondary", agents)
        wiki = _read(_WIKI_WORKSPACE)
        self.assertIn("Workspace tabs", wiki)
        self.assertIn("stacked", wiki.lower())
        self.assertIn("Open in split view", wiki)
        self.assertIn("Split view", wiki)
        self.assertIn("Close other tabs", wiki)
        self.assertIn("Close tabs to the right", wiki)
        self.assertIn("overflow", wiki.lower())
        self.assertIn("Shift+F10", wiki)
        self.assertIn("drafting", wiki.lower())
        self.assertIn("remembers your open tabs", wiki.lower())
        self.assertIn("workspace-persistence.js", agents)
        self.assertIn("before first normal mount", agents.lower())
        self.assertIn("Workspace logical state is persistent", design)
        self.assertIn("workspace runtime state is ephemeral", design.lower())

    def test_link_layer_skips_explicitly_disabled_destinations(self):
        """The link layer hijacks anchor clicks in the capture phase, so an
        anchor the owning component marked aria-disabled must make it bow out
        entirely -- otherwise its preventDefault/stopPropagation would swallow
        the component's own explanation (e.g. an offline page keeping a real
        href while saying the destination is not cached)."""
        src = _read(_WS)
        start = src.index("function handleNavEvent(")
        body = src[start : src.index("function onMiddleMouseDown(")]
        skip = "if (navEl.getAttribute && navEl.getAttribute('aria-disabled') === 'true') return;"
        self.assertIn(skip, body)
        # The bail-out has to happen before the event is consumed.
        self.assertLess(body.index(skip), body.index("e.preventDefault();"))
        self.assertLess(body.index(skip), body.index("e.stopPropagation();"))

        # The split is deliberate. onMiddleMouseDown only suppresses the
        # middle-button *mousedown* default (autoscroll); it never navigates, so
        # it has no reason to special-case a disabled destination. The activation
        # event that follows (auxclick) is where handleNavEvent bows out and the
        # owning component refuses and explains.
        mid_start = src.index("function onMiddleMouseDown(")
        mid_body = src[mid_start : src.index("function bindNewTabButton(")]
        self.assertNotIn("aria-disabled", mid_body)

    def test_no_component_disables_a_navigable_destination(self):
        """The shared bail-out above is a standing contract, but nothing uses it
        today: every Phase-1 destination reachable from a cached page (Work,
        Folder, Concept, Position, Argument/Stance, Person, Person Group,
        Playlist, Research Graph) is offline-capable and owns its own
        availability, so no component marks a *link* aria-disabled any more.

        Position -> Argument and Person -> Group each carried such a guard while
        their destination was still online-only, and each was removed once that
        destination landed. If a future component reintroduces one it must also
        explain the refusal itself -- the shared layer only declines to
        navigate -- and this test should then assert both halves against it.
        """
        for name in ("positions.js", "people.js", "playlists.js", "arguments.js", "concepts.js"):
            src = _read(os.path.join(_FRONTEND, "js", "components", name))
            self.assertNotIn("auxclick", src, name)
            # Buttons are settled with native `disabled`; aria-disabled on a
            # component's own markup is the marker the nav layer bows out on.
            self.assertNotIn("guardActivation", src, name)

    def test_node_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for workspace tab tests")
        proc = subprocess.run(
            [node, _RUNNER],
            cwd=_PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertIn("passed", proc.stdout)
        self.assertIn(", 0 failed", proc.stdout)
        self.assertNotIn("FAIL  ", proc.stdout)

    def test_tab_status_recognizes_warm_suspended_contexts(self):
        """Warm-suspended tabs (mounted=false, suspended=true) must keep showing
        saving/error status; cold-parked and destroyed contexts must not."""
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for workspace tab tests")
        self.assertTrue(os.path.isfile(_TAB_STATUS_WARM_RUNNER))
        proc = subprocess.run(
            [node, _TAB_STATUS_WARM_RUNNER],
            cwd=_PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertIn("passed", proc.stdout)
        self.assertIn(", 0 failed", proc.stdout)
        self.assertNotIn("FAIL  ", proc.stdout)

    def test_tree_module_load_order_and_selftest(self):
        self.assertTrue(os.path.isfile(_TREE))
        self.assertTrue(os.path.isfile(_TREE_RUNNER))
        html = _read(_INDEX)
        tree_at = html.find('src="/js/workspace-tree.js"')
        ws_at = html.find('src="/js/workspace-tabs.js"')
        self.assertNotEqual(tree_at, -1)
        self.assertLess(tree_at, ws_at)
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for workspace tree tests")
        proc = subprocess.run(
            [node, _TREE_RUNNER],
            cwd=_PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertIn("passed", proc.stdout)
        self.assertIn(", 0 failed", proc.stdout)
        self.assertNotIn("FAIL  ", proc.stdout)

    def test_persistence_module_and_selftest(self):
        self.assertTrue(os.path.isfile(_PERSIST))
        self.assertTrue(os.path.isfile(_PERSIST_RUNNER))
        html = _read(_INDEX)
        persist_at = html.find('src="/js/workspace-persistence.js"')
        tree_at = html.find('src="/js/workspace-tree.js"')
        ws_at = html.find('src="/js/workspace-tabs.js"')
        self.assertNotEqual(persist_at, -1)
        self.assertLess(tree_at, persist_at)
        self.assertLess(persist_at, ws_at)
        persist_src = _read(_PERSIST)
        self.assertIn("prks.workspace.v1", persist_src)
        self.assertIn("pagehide", persist_src)
        self.assertIn("last-writer-wins", persist_src)
        self.assertNotIn("new BroadcastChannel", persist_src)
        for name in (
            "workspace-tabs.js",
            "workspace-tree.js",
            "workspace-tiling.js",
            "workspace-split.js",
            "workspace-drag.js",
            "workspace-tab-menu.js",
            "tab-context.js",
        ):
            src = _read(os.path.join(_FRONTEND, "js", name))
            self.assertNotIn("localStorage", src, name)
            self.assertNotIn("sessionStorage", src, name)
            self.assertNotIn("indexedDB", src, name)
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for workspace persistence tests")
        proc = subprocess.run(
            [node, _PERSIST_RUNNER],
            cwd=_PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertIn("passed", proc.stdout)
        self.assertIn(", 0 failed", proc.stdout)
        self.assertNotIn("FAIL  ", proc.stdout)

    def test_tiling_observer_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for workspace tiling tests")
        runner = os.path.join(_PROJECT_DIR, "tests", "browser", "run_workspace_tiling_selftest.js")
        proc = subprocess.run(
            [node, runner],
            cwd=_PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertNotIn("FAIL  ", proc.stdout)

    def test_drag_module_structural_contract(self):
        self.assertTrue(os.path.isfile(_DRAG))
        self.assertTrue(os.path.isfile(_DRAG_RUNNER))
        html = _read(_INDEX)
        self.assertIn('src="/js/workspace-drag.js"', html)
        src = _read(_DRAG)
        # Production wiring: workspace-tabs.js must actually call the init function, not just
        # define it in isolation.
        ws = _read(_WS)
        self.assertIn("prksWorkspaceInitDrag()", ws)
        # Exported canonical APIs this module drives on drop must exist on workspace-tabs.js.
        self.assertIn("prksWorkspaceMovePane", ws)
        self.assertIn("prksWorkspaceReorderTab", ws)
        self.assertIn("prksWorkspaceMoveTabStep", ws)
        self.assertIn("prksWorkspaceIsNarrowFallback", ws)
        # workspace-drag.js itself only ever calls those canonical APIs to mutate state; it does
        # not reimplement tree/tab mutation.
        self.assertIn("root.prksWorkspaceMovePane", src)
        self.assertIn("root.prksWorkspaceReorderTab", src)
        self.assertIn("root.prksWorkspaceHideLeaf", src)
        self.assertIn("root.prksWorkspaceSplitLeaf", src)
        self.assertIn("root.prksWorkspaceTileTab", src)
        self.assertIn("root.prksWorkspaceIsNarrowFallback", src)
        # Pointer Events, not native HTML5 drag/drop.
        self.assertIn("pointerdown", src)
        self.assertIn("pointermove", src)
        self.assertIn("pointerup", src)
        self.assertIn("pointercancel", src)
        self.assertIn("lostpointercapture", src)
        self.assertNotIn("dragstart", src)
        self.assertNotIn('"dragover"', src)
        self.assertNotIn("ondrop", src)
        # Drag state is transient only -- never persisted.
        self.assertNotIn("localStorage", src)
        self.assertNotIn("sessionStorage", src)
        self.assertNotIn("indexedDB", src)
        # Two defensive lifecycle integration points call back into this module, and this module
        # never mutates responsive/canonical state from either of them.
        tiling = _read(_TILING)
        self.assertIn("prksWorkspaceCancelActiveDrag", tiling)
        applied_narrow = tiling[tiling.find("function applyNarrow") : tiling.find("function applyNarrow") + 1600]
        self.assertIn("prksWorkspaceCancelActiveDrag", applied_narrow)
        prune_stale = tiling[tiling.find("function pruneStale") : tiling.find("function pruneStale") + 1500]
        self.assertIn("prksWorkspaceCancelActiveDrag", prune_stale)
        # pruneStale() must only cancel when it actually found stale DOM to remove -- not
        # unconditionally on every ordinary paint.
        self.assertIn("staleTiles.length || staleContainers.length", prune_stale)
        # Move tab left/right context-menu commands reuse the same canonical ordering API as
        # drag/drop, and existing non-drag workflows remain intact alongside it.
        menu = _read(_MENU)
        self.assertIn("prksWorkspaceMoveTabStep", menu)
        self.assertIn("Move tab left", menu)
        self.assertIn("Move tab right", menu)
        self.assertIn("Open in split view", menu)
        self.assertIn("Make main", menu)

    def test_drag_node_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for workspace drag tests")
        proc = subprocess.run(
            [node, _DRAG_RUNNER],
            cwd=_PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertIn("passed", proc.stdout)
        self.assertIn(", 0 failed", proc.stdout)
        self.assertNotIn("FAIL  ", proc.stdout)

    def test_tiling_recursive_selftest(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for workspace tiling tests")
        runner = os.path.join(_PROJECT_DIR, "tests", "browser", "run_workspace_tiling_recursive_selftest.js")
        self.assertTrue(os.path.isfile(runner))
        proc = subprocess.run(
            [node, runner],
            cwd=_PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertIn(", 0 failed", proc.stdout)
        self.assertNotIn("FAIL  ", proc.stdout)


if __name__ == "__main__":
    unittest.main()
