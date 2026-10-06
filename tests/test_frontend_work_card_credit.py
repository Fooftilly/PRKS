"""Work card credit plain-text helper — no double-escaping into summaries."""
from __future__ import annotations

import json
import os
import subprocess
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CARDS = os.path.join(_ROOT, "frontend", "js", "components", "work-cards.js")
_WORK_LIFECYCLE = os.path.join(
    _ROOT, "frontend-app", "src", "features", "work", "detail-lifecycle.ts"
)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class WorkCardCreditTextTests(unittest.TestCase):
    def test_works_rel_summary_uses_plain_credit_text(self):
        lifecycle = _read(_WORK_LIFECYCLE)
        self.assertIn("prksWorkCardCreditText", lifecycle)
        # Must not feed the HTML-escaped credit line into escaping summary parts.
        idx = lifecycle.index("prksRelSummaryHtml")
        window = lifecycle[max(0, idx - 400) : idx + 800]
        self.assertIn("prksWorkCardCreditText", window)
        self.assertNotIn("prksWorkCardCreditLine", window)

    def test_plain_text_preserves_ampersand_and_angles(self):
        cards = _read(_CARDS)
        script = r"""
const vm = require('vm');
const src = %s;
const context = { console, window: {}, document: undefined };
context.window = context;
vm.createContext(context);
vm.runInContext(src, context);
const plain = context.prksWorkCardCreditText({
    author_text: 'A & B <C>',
});
if (plain !== 'Author: A & B <C>') throw new Error('plain must not escape: ' + plain);
const html = context.prksWorkCardCreditLine({
    author_text: 'A & B <C>',
});
if (!html.includes('&amp;') || !html.includes('&lt;')) {
    throw new Error('HTML helper must escape: ' + html);
}
if (html.includes('A & B <C>')) throw new Error('HTML helper leaked raw: ' + html);
""" % (
            json.dumps(cards),
        )
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError(proc.stderr or proc.stdout or "node failed")


if __name__ == "__main__":
    unittest.main()
