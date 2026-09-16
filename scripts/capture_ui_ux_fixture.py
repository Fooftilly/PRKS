#!/usr/bin/env python3
"""Capture the primary 1024x582 three-pane Work/PDF density fixture.

Uses AppServer + UX-tour seed (deterministic temp storage — never repo data/).
Writes PNGs and a JSON chrome-measurement report under
docs/screenshots/ui-ux-consistency/.

Usage:
  python3 scripts/capture_ui_ux_fixture.py --label before
  python3 scripts/capture_ui_ux_fixture.py --label after
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tests.e2e.fixtures import WORK_A_TITLE, WORK_B_TITLE
from tests.e2e.harness import AppServer, require_chromium
from tests.ux_tour.fixtures import WORK_C_TITLE, seed_ux_tour_library

OUT = REPO / "docs" / "screenshots" / "ui-ux-consistency"
VIEWPORT = {"width": 1024, "height": 582}


def _wait_pdf(page, tab_id: str, timeout_ms: int = 45000) -> None:
    page.wait_for_function(
        """(tabId) => {
            const ctx = window.prksGetTabContext && window.prksGetTabContext(tabId);
            if (!ctx) return false;
            const pdf = ctx.getResource && ctx.getResource('pdf');
            if (!pdf) return false;
            const root = ctx.root;
            if (!root) return false;
            return !!root.querySelector('[data-prks-role="pdf-viewer"] .prks-pdf-viewer');
        }""",
        arg=tab_id,
        timeout=timeout_ms,
    )


def _collapse_notes(page, tab_id: str) -> None:
    page.evaluate(
        """(tabId) => {
            const ctx = window.prksGetTabContext(tabId);
            if (!ctx || !ctx.root) return;
            const ws = ctx.root.querySelector('.work-workspace');
            if (!ws) return;
            if (!ws.classList.contains('work-workspace--notes-collapsed')) {
                const btn = ctx.root.querySelector('[data-prks-role="work-notes-collapse-btn"]');
                if (btn) btn.click();
            }
            // Ensure preference is collapsed for this work.
            const workId = ws.getAttribute('data-work-id');
            if (workId) localStorage.setItem('prks.workNotesCollapsed.' + workId, '1');
            ws.classList.add('work-workspace--notes-collapsed');
        }""",
        tab_id,
    )


def _measure(page) -> dict:
    return page.evaluate(
        """() => {
            const vp = { w: window.innerWidth, h: window.innerHeight };
            const sidebar = document.getElementById('sidebar');
            const ribbon = document.querySelector('.top-ribbon');
            const tabs = document.querySelector('.prks-workspace-tabs-shell');
            const canvas = document.querySelector('.prks-workspace-canvas');
            const right = document.getElementById('right-panel');
            const tiles = [...document.querySelectorAll('.prks-workspace-canvas--tiled .prks-tile')].map((tile) => {
                const header = tile.querySelector(':scope > .prks-tile-header');
                const body = tile.querySelector('.prks-tile__body') || tile.querySelector('[data-prks-role="tab-root"]');
                const pdfViewer = tile.querySelector('.prks-pdf-viewer');
                const pdfToolbar = tile.querySelector('.prks-pdf-toolbar');
                const notesHeader = tile.querySelector('.work-notes-pane-header');
                const notesPane = tile.querySelector('.work-notes-pane');
                const pdfPane = tile.querySelector('.work-pdf-pane, [data-prks-role="pdf-viewer"]');
                const backRow = tile.querySelector('.prks-nav-back-row--work');
                const br = (el) => el ? el.getBoundingClientRect() : null;
                const r = br(tile);
                const hr = br(header);
                const pvr = br(pdfViewer);
                const ptr = br(pdfToolbar);
                const nr = br(notesPane);
                const nhr = br(notesHeader);
                const ppr = br(pdfPane);
                const brr = br(backRow);
                return {
                    tabId: tile.getAttribute('data-prks-tab-id'),
                    role: tile.classList.contains('prks-tile--main') ? 'main' : 'secondary',
                    focused: tile.classList.contains('prks-tile--focused'),
                    tile: r && { w: Math.round(r.width), h: Math.round(r.height), y: Math.round(r.top) },
                    headerH: hr ? Math.round(hr.height) : 0,
                    backRowH: brr ? Math.round(brr.height) : 0,
                    pdfToolbarH: ptr ? Math.round(ptr.height) : 0,
                    pdfViewerH: pvr ? Math.round(pvr.height) : 0,
                    pdfPaneH: ppr ? Math.round(ppr.height) : 0,
                    notesPaneH: nr ? Math.round(nr.height) : 0,
                    notesHeaderH: nhr ? Math.round(nhr.height) : 0,
                    notesCollapsed: !!(tile.querySelector('.work-workspace--notes-collapsed')),
                    pdfTitle: (tile.querySelector('.prks-pdf-toolbar__title') || {}).textContent || null,
                    tileTitle: (header && header.querySelector('.prks-tile-header__title') || {}).textContent || null,
                };
            });
            const sb = sidebar && sidebar.getBoundingClientRect();
            const rb = ribbon && ribbon.getBoundingClientRect();
            const tb = tabs && tabs.getBoundingClientRect();
            const cb = canvas && canvas.getBoundingClientRect();
            const rpb = right && right.getBoundingClientRect();
            const shellChromeH = (rb ? rb.height : 0) + (tb ? tb.height : 0);
            const usableCanvasH = cb ? cb.height : 0;
            const secondary = tiles.filter(t => t.role === 'secondary');
            const secondaryPdfHeights = secondary.map(t => t.pdfViewerH || t.pdfPaneH || 0);
            return {
                viewport: vp,
                theme: document.documentElement.getAttribute('data-theme'),
                bodyClasses: [...document.body.classList],
                appClasses: [...(document.getElementById('app-container') || {}).classList || []],
                sidebarW: sb ? Math.round(sb.width) : 0,
                ribbonH: rb ? Math.round(rb.height) : 0,
                tabStripH: tb ? Math.round(tb.height) : 0,
                canvas: cb && { w: Math.round(cb.width), h: Math.round(cb.height), y: Math.round(cb.top) },
                rightPanelVisible: document.body.classList.contains('prks-right-panel-open'),
                rightPanelW: rpb ? Math.round(rpb.width) : 0,
                shellChromeH: Math.round(shellChromeH),
                canvasH: Math.round(usableCanvasH),
                chromeCostPct: usableCanvasH && vp.h ? Math.round(1000 * (1 - usableCanvasH / vp.h)) / 10 : null,
                tiles,
                secondaryMinPdfH: secondaryPdfHeights.length ? Math.min(...secondaryPdfHeights) : null,
                secondaryAvgPdfH: secondaryPdfHeights.length
                    ? Math.round(secondaryPdfHeights.reduce((a, b) => a + b, 0) / secondaryPdfHeights.length)
                    : null,
                snapshot: window.prksWorkspaceSnapshot ? window.prksWorkspaceSnapshot() : null,
            };
        }"""
    )


def build_fixture(page, ids: dict) -> dict:
    # Dark theme. At 1024px the stacked right panel (~338px) leaves the canvas
    # under the 720px tiling threshold, so hide Details *before* opening tiles.
    page.evaluate(
        """() => {
            localStorage.setItem('prks-theme', 'dark');
            document.documentElement.setAttribute('data-theme', 'dark');
            document.body.classList.remove('prks-right-panel-open', 'prks-sidebar-open', 'prks-overlay-open');
            const app = document.getElementById('app-container');
            if (app) app.classList.add('app-container--hide-right-panel');
        }"""
    )

    work_a = ids["work_a"]
    work_b = ids["work_b"]
    work_c = ids["work_c"]

    page.goto(page.url.split("#")[0] + "#/works/%s" % work_a, wait_until="domcontentloaded")
    page.wait_for_function("() => !!window.prksWorkspaceSnapshot")
    page.wait_for_selector('.prks-pdf-viewer', timeout=45000)
    # Re-assert panel hide after route paint (Work route may reopen Details).
    page.evaluate(
        """() => {
            document.body.classList.remove('prks-right-panel-open', 'prks-sidebar-open', 'prks-overlay-open');
            const app = document.getElementById('app-container');
            if (app) app.classList.add('app-container--hide-right-panel');
        }"""
    )
    page.wait_for_timeout(200)

    main_id = page.evaluate("() => window.prksWorkspaceSnapshot().mainTabId")
    _wait_pdf(page, main_id)

    # Open work B as Secondary (tile) — must await the Promise.
    page.evaluate(
        """async (hash) => { await window.prksNavigate(hash, { target: 'tile' }); }""",
        "#/works/%s" % work_b,
    )
    page.wait_for_function(
        """() => {
            const s = window.prksWorkspaceSnapshot();
            const canvas = document.querySelector('.prks-workspace-canvas');
            return s.mode === 'tiled'
                && s.secondaryTree
                && s.secondaryTree.type === 'leaf'
                && canvas
                && canvas.classList.contains('prks-workspace-canvas--tiled');
        }"""
    )
    sec_b = page.evaluate(
        """() => {
            const s = window.prksWorkspaceSnapshot();
            return s.secondaryTree.tabId;
        }"""
    )
    _wait_pdf(page, sec_b)

    # Split Secondary down with work C.
    page.evaluate(
        """async ({ target, hash }) => {
            await window.prksWorkspaceSplitLeaf(target, 'top-bottom', { hash });
        }""",
        {"target": sec_b, "hash": "#/works/%s" % work_c},
    )
    page.wait_for_function(
        """() => {
            const s = window.prksWorkspaceSnapshot();
            const t = s.secondaryTree;
            return t && t.type === 'split' && t.axis === 'top-bottom'
                && document.querySelectorAll('.prks-workspace-canvas--tiled .prks-tile').length >= 3;
        }"""
    )
    page.wait_for_timeout(500)

    tab_ids = page.evaluate(
        """() => {
            const s = window.prksWorkspaceSnapshot();
            const ids = [s.mainTabId];
            const walk = (n) => {
                if (!n) return;
                if (n.type === 'leaf') ids.push(n.tabId);
                else { walk(n.first); walk(n.second); }
            };
            walk(s.secondaryTree);
            return ids;
        }"""
    )
    for tid in tab_ids:
        _wait_pdf(page, tid)
        _collapse_notes(page, tid)

    # Ensure right panel closed; sidebar stays collapsed rail in tiled mode.
    page.evaluate(
        """() => {
            document.body.classList.remove('prks-right-panel-open', 'prks-sidebar-open', 'prks-overlay-open');
            // Focus main for a stable shot.
            const main = window.prksWorkspaceSnapshot().mainTabId;
            if (window.prksWorkspaceFocusTab) window.prksWorkspaceFocusTab(main);
        }"""
    )
    page.wait_for_timeout(300)

    # Titles sanity (for audit).
    titles = page.evaluate(
        """() => [...document.querySelectorAll('.prks-tile-header__title')].map(el => el.textContent.trim())"""
    )
    return {"tab_ids": tab_ids, "titles": titles, "ids": ids}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", choices=("before", "after"), required=True)
    parser.add_argument("--extra", action="store_true", help="Also capture gallery shots")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    pw, browser = require_chromium()
    server = AppServer(seed_fn=seed_ux_tour_library)
    try:
        server.start()
        context = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=1,
            color_scheme="dark",
        )
        page = context.new_page()
        page.goto(server.origin + "/", wait_until="domcontentloaded")
        page.wait_for_function("() => !!window.prksWorkspaceSnapshot", timeout=30000)

        meta = build_fixture(page, server.ids)
        measures = _measure(page)

        shot_name = "workspace-%s.png" % args.label
        shot_path = OUT / shot_name
        page.screenshot(path=str(shot_path), full_page=False)

        report = {
            "label": args.label,
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "viewport": VIEWPORT,
            "fixture": {
                "works": [WORK_A_TITLE, WORK_B_TITLE, WORK_C_TITLE],
                "work_ids": {
                    "a": server.ids["work_a"],
                    "b": server.ids["work_b"],
                    "c": server.ids["work_c"],
                },
                "titles_in_headers": meta["titles"],
                "notes": "collapsed",
                "right_panel": "hidden",
                "nav": "tiled collapsed rail",
                "theme": "dark",
            },
            "measures": measures,
            "screenshot": str(shot_path.relative_to(REPO)),
        }
        report_path = OUT / ("workspace-%s-measures.json" % args.label)
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "screenshot": str(shot_path), "report": str(report_path), "summary": {
            "sidebarW": measures.get("sidebarW"),
            "ribbonH": measures.get("ribbonH"),
            "tabStripH": measures.get("tabStripH"),
            "canvasH": measures.get("canvasH"),
            "chromeCostPct": measures.get("chromeCostPct"),
            "secondaryMinPdfH": measures.get("secondaryMinPdfH"),
            "secondaryAvgPdfH": measures.get("secondaryAvgPdfH"),
            "tiles": [
                {
                    "role": t.get("role"),
                    "tileH": (t.get("tile") or {}).get("h"),
                    "headerH": t.get("headerH"),
                    "pdfToolbarH": t.get("pdfToolbarH"),
                    "pdfViewerH": t.get("pdfViewerH"),
                    "notesPaneH": t.get("notesPaneH"),
                    "notesCollapsed": t.get("notesCollapsed"),
                }
                for t in measures.get("tiles") or []
            ],
        }}, indent=2))

        if args.extra:
            # Additional gallery shots (best-effort; failures don't abort baseline).
            extras = [
                ("work-detail-%s.png" % args.label, "#/works/%s" % server.ids["work_a"], ".work-detail"),
                ("list-folders-%s.png" % args.label, "#/folders", ".prks-folder-library"),
                ("concept-%s.png" % args.label, "#/concepts", None),
                ("graph-%s.png" % args.label, "#/graph", ".research-graph"),
            ]
            for name, hash_path, wait_sel in extras:
                try:
                    page.evaluate("(h) => window.prksNavigate(h)", hash_path)
                    page.wait_for_timeout(800)
                    if wait_sel:
                        page.wait_for_selector(wait_sel, timeout=15000)
                    page.screenshot(path=str(OUT / name), full_page=False)
                except Exception as exc:
                    print("extra shot failed %s: %s" % (name, exc), file=sys.stderr)

        context.close()
        return 0
    finally:
        try:
            server.stop()
        except Exception:
            pass
        try:
            browser.close()
        finally:
            pw.stop()


if __name__ == "__main__":
    raise SystemExit(main())
