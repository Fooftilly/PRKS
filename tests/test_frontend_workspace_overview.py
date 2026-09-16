"""Workspace Overview: visible panes follow visual-tiled, not secondaryTree alone."""
from __future__ import annotations

import json
import os
import subprocess
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OVERVIEW = os.path.join(_ROOT, "frontend", "js", "workspace-overview.js")
_TREE = os.path.join(_ROOT, "frontend", "js", "workspace-tree.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _run_overview(body: str) -> None:
    inner = "var overviewApi = api;\n" + body
    script = (
        "const vm = require('vm');\n"
        "const treeSrc = "
        + json.dumps(_read(_TREE))
        + ";\n"
        "const overviewSrc = "
        + json.dumps(_read(_OVERVIEW))
        + ";\n"
        "const module = { exports: {} };\n"
        "const context = { console: console, module: module, exports: module.exports };\n"
        "vm.createContext(context);\n"
        "vm.runInContext(treeSrc, context);\n"
        "vm.runInContext(overviewSrc, context);\n"
        "context.api = module.exports;\n"
        "vm.runInContext("
        + json.dumps(inner)
        + ", context);\n"
    )
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout or "node failed")


class WorkspaceOverviewVisiblePanesTests(unittest.TestCase):
    def test_source_uses_visual_tiled_and_dialog_a11y(self):
        src = _read(_OVERVIEW)
        self.assertIn("prksWorkspaceVisualTiled", src)
        self.assertIn("aria-modal", src)
        self.assertIn("trapKeydown", src)
        self.assertIn("previouslyFocused", src)
        # Restore preference: explicit opener → activeElement → toolbar.
        self.assertIn("explicit || activeOk || toolbar", src)
        palette = _read(os.path.join(_ROOT, "frontend", "js", "command-palette.js"))
        self.assertIn("prksWorkspaceOverviewOpen(opener", palette)
        self.assertIn("restoreFocus: true", palette)

    def test_hide_split_and_narrow_fallback_main_only(self):
        _run_overview(
            r"""
            var snap = {
                mode: 'stacked',
                mainTabId: 'm1',
                focusedTabId: 'm1',
                secondaryTree: { type: 'leaf', tabId: 's1' },
                tabs: [
                    { id: 'm1', title: 'Main', route: '#/folders' },
                    { id: 's1', title: 'Split', route: '#/works/1' },
                    { id: 'p1', title: 'Parked', route: '#/people' }
                ]
            };
            prksWorkspaceVisualTiled = function () { return false; };
            prksPageSummaryHtml = function (opts) {
                var parts = (opts && opts.parts) || [];
                return '<p class="prks-page-summary">' + parts.filter(Boolean).join(' · ') + '</p>';
            };
            var ids = overviewApi.collectVisibleTabIds(snap);
            if (JSON.stringify(ids) !== JSON.stringify(['m1'])) {
                throw new Error('hide-split must show main only, got ' + JSON.stringify(ids));
            }
            var html = overviewApi.buildBodyHtml(snap);
            if (html.indexOf('1 visible pane') < 0) throw new Error('expected 1 visible pane: ' + html);
            if (html.indexOf('Parked · 2') < 0) throw new Error('secondary+parked must be parked: ' + html);

            prksWorkspaceVisualTiled = function () { return true; };
            ids = overviewApi.collectVisibleTabIds(snap);
            if (ids.length !== 2 || ids[0] !== 'm1' || ids[1] !== 's1') {
                throw new Error('tiled must include secondary, got ' + JSON.stringify(ids));
            }
            html = overviewApi.buildBodyHtml(snap);
            if (html.indexOf('2 visible panes') < 0) throw new Error('expected 2 visible panes: ' + html);
            if (html.indexOf('2 of 4 remaining') < 0) throw new Error('pane capacity wrong: ' + html);

            snap.mode = 'tiled';
            prksWorkspaceVisualTiled = function () { return false; };
            prksWorkspaceIsNarrowFallback = function () { return true; };
            ids = overviewApi.collectVisibleTabIds(snap);
            if (JSON.stringify(ids) !== JSON.stringify(['m1'])) {
                throw new Error('narrow fallback must show main only, got ' + JSON.stringify(ids));
            }
            """
        )


if __name__ == "__main__":
    unittest.main()
