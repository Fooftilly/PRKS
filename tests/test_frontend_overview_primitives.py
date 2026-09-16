"""Contracts for shared overview / context summary primitives."""
from __future__ import annotations

import json
import os
import subprocess
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PRIM = os.path.join(_ROOT, "frontend", "js", "overview-primitives.js")
_DESIGN = os.path.join(_ROOT, "DESIGN.md")
_INDEX = os.path.join(_ROOT, "frontend", "index.html")
_CSS = os.path.join(_ROOT, "frontend", "css", "style.css")
_GALLERY = os.path.join(_ROOT, "tests", "browser", "design_system.html")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _run_js(body: str) -> None:
    source = _read(_PRIM)
    script = r"""
const vm = require('vm');
const source = %s;
const context = { console };
vm.createContext(context);
vm.runInContext(source, context);
(() => {
%s
})();
""" % (
        json.dumps(source),
        body,
    )
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)


class OverviewPrimitivesTests(unittest.TestCase):
    def test_design_documents_summary_family(self):
        design = _read(_DESIGN)
        self.assertIn("Overview and context summaries", design)
        self.assertIn(".prks-page-summary", design)
        self.assertIn(".prks-scope-line", design)
        self.assertIn(".prks-rel-summary", design)
        self.assertIn(".prks-state-summary", design)
        self.assertIn(".prks-nav-attention", design)
        self.assertIn("Unknown is not zero", design)

    def test_css_and_gallery_expose_primitives(self):
        css = _read(_CSS)
        gallery = _read(_GALLERY)
        for cls in (
            ".prks-page-summary",
            ".prks-scope-line",
            ".prks-rel-summary",
            ".prks-state-summary",
            ".prks-nav-attention",
        ):
            self.assertIn(cls, css)
        self.assertIn('id="summaries"', gallery)
        self.assertIn("prks-page-summary", gallery)
        self.assertIn("overview-primitives.js", _read(_INDEX))

    def test_scope_line_matching_and_totals(self):
        _run_js(
            r"""
            const html = context.prksScopeLineHtml({ shown: 12, total: 48, filter: 'x', label: 'Concepts' });
            if (!html.includes('12 of 48 matching')) throw new Error('expected matching scope, got ' + html);
            const totalOnly = context.prksScopeLineHtml({ total: 3, label: 'People' });
            if (!totalOnly.includes('3 People')) throw new Error('expected total label, got ' + totalOnly);
            """
        )

    def test_unknown_parts_omitted_never_zero(self):
        _run_js(
            r"""
            const html = context.prksPageSummaryHtml({
                parts: ['12 folders', { unknown: true }, null, Number.NaN, ''],
            });
            if (!html.includes('12 folders')) throw new Error('expected known part');
            if (html.includes('0')) throw new Error('must not invent zero, got ' + html);
            const badge = context.prksNavAttentionBadgeHtml({ count: 0 });
            if (badge !== '') throw new Error('zero badge must be empty');
            const unknown = context.prksNavAttentionBadgeHtml({ unknown: true, count: 3 });
            if (unknown !== '') throw new Error('unknown must hide badge');
            const empty = context.prksPageSummaryHtml({ parts: [null, { unknown: true }] });
            if (empty !== '') throw new Error('all-unknown summary must be empty');
            """
        )

    def test_rel_summary_supports_safe_internal_links(self):
        _run_js(
            r"""
            const html = context.prksRelSummaryHtml({
                parts: [{ text: 'Inbox', href: '#/folders/abc' }, '2 people'],
            });
            if (!html.includes('href="#/folders/abc"')) throw new Error('expected folder link');
            if (!html.includes('2 people')) throw new Error('expected people part');
            const bad = context.prksRelSummaryHtml({
                parts: [{ text: 'x', href: 'javascript:alert(1)' }],
            });
            if (bad.includes('javascript:')) throw new Error('must not emit unsafe href');
            if (!bad.includes('>x<')) throw new Error('unsafe href should fall back to text');
            """
        )


if __name__ == "__main__":
    unittest.main()
