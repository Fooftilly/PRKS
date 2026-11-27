"""The #1472 required-theorem manifest rejects weakened or removed lemmas."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "formal" / "dafny_theorems.py"
MODEL = ROOT / "formal" / "dafny"


class DafnyManifestTests(unittest.TestCase):
    def check(self, edit=None) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp)
            for path in MODEL.iterdir():
                shutil.copy(path, model / path.name)
            if edit:
                name, old, new = edit
                text = (model / name).read_text(encoding="utf-8")
                self.assertEqual(text.count(old), 1, old)
                (model / name).write_text(text.replace(old, new), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "--model", str(model)],
                capture_output=True,
                text=True,
                check=False,
            )

    def test_current_model_matches_the_manifest(self):
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_added_precondition_fails(self):
        result = self.check(
            (
                "Sequence.dfy",
                "    requires Inv(m, s) && s.op.Some? && s.op.value.base == Cur(s)\n",
                "    requires Inv(m, s) && s.op.Some? && s.op.value.base == Cur(s) && m == M1\n",
            )
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Intent: statement changed", result.stderr)

    def test_a_removed_lemma_fails(self):
        result = self.check(
            ("Reconcile.dfy", "lemma NoProofNoApply(", "lemma NoProofNoApplyRenamed(")
        )
        self.assertIn("NoProofNoApply: required lemma is missing", result.stderr)

    def test_an_escape_hatch_fails(self):
        result = self.check(
            (
                "Reconcile.dfy",
                "  lemma PlanAckTable(e: Evidence)\n",
                "  lemma {:axiom} Extra() ensures false\n  lemma PlanAckTable(e: Evidence)\n",
            )
        )
        self.assertIn("forbidden construct", result.stderr)


if __name__ == "__main__":
    unittest.main()
