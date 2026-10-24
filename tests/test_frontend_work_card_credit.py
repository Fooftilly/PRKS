"""Work credit text: one plain-text rule, owned by the Vue card module."""
from __future__ import annotations

import json
import os
import subprocess
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CARDS = os.path.join(_ROOT, "frontend", "js", "components", "work-cards.js")
_CARD_TS = os.path.join(_ROOT, "frontend-app", "src", "components", "work-card.ts")
_WORK_LIFECYCLE = os.path.join(
    _ROOT, "frontend-app", "src", "features", "work", "detail-lifecycle.ts"
)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class WorkCardCreditTextTests(unittest.TestCase):
    def test_works_rel_summary_uses_plain_credit_text(self):
        lifecycle = _read(_WORK_LIFECYCLE)
        idx = lifecycle.index("function relSummaryParts(")
        window = lifecycle[idx : idx + 800]
        self.assertIn("workCardCreditText(work)", window)
        self.assertNotIn("prksWorkCardCredit", lifecycle)

    def test_classic_credit_helpers_are_retired(self):
        cards = _read(_CARDS)
        self.assertNotIn("prksWorkCardCreditText", cards)
        self.assertNotIn("prksWorkCardCreditLine", cards)

    def test_plain_text_preserves_ampersand_and_angles(self):
        script = r"""
const { workCardCreditText } = require(%s);
const plain = workCardCreditText({ author_text: 'A & B <C>' });
if (plain !== 'Author: A & B <C>') throw new Error('plain must not escape: ' + plain);
""" % (
            json.dumps(_CARD_TS),
        )
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError(proc.stderr or proc.stdout or "node failed")


if __name__ == "__main__":
    unittest.main()
