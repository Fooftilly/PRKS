"""Guard the manual E2E classic-script deferral A/B experiment.

The production index remains unchanged; benchmark-only copies preserve script
source ordering and disable the unrelated routing fast path in both arms.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.benchmark_deferred_scripts import deferred_index

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "frontend" / "index.html"
WORKFLOW = ROOT / ".github" / "workflows" / "e2e-script-defer-benchmark.yml"


class ScriptDeferBenchmarkTests(unittest.TestCase):
    def test_defer_variant_preserves_every_src_and_module_tag(self):
        original = INDEX.read_text(encoding="utf-8")
        result, count = deferred_index(original)
        self.assertEqual(count, 84)
        self.assertEqual(
            re.findall(r'<script[^>]*\bsrc="([^"]+)"', original),
            re.findall(r'<script[^>]*\bsrc="([^"]+)"', result),
        )
        self.assertEqual(result.count("<script defer src="), 84)
        self.assertEqual(original.count("<script defer src="), 0)
        for module in (
            '<script type="module" src="/js/components/works-pdf.js"></script>',
            '<script type="module" src="/vue/prks-vue.js"></script>',
        ):
            self.assertIn(module, original)
            self.assertIn(module, result)
        self.assertNotEqual(original, result)
        self.assertEqual(deferred_index(original), (result, 84))

    def test_transformation_refuses_partly_deferred_or_changed_html(self):
        original = INDEX.read_text(encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "already has async/defer"):
            deferred_index(original.replace(
                '<script src="/js/app.js">',
                '<script defer src="/js/app.js">',
                1,
            ))
        with self.assertRaisesRegex(ValueError, "expected 84"):
            deferred_index(original.replace(
                '<script src="/js/app.js"></script>', "", 1
            ))

    def test_workflow_is_manual_isolated_and_has_identical_selection(self):
        flow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", flow)
        self.assertNotIn("pull_request:", flow)
        self.assertIn("scripts/benchmark_deferred_scripts.py", flow)
        self.assertIn("trap 'cp", flow)
        self.assertIn('1|4) mode=baseline; html="$original_index"', flow)
        self.assertIn('2|3) mode=deferred; html="$deferred_index"', flow)
        self.assertEqual(flow.count("--feature shell --feature work-create"), 2)
        self.assertEqual(flow.count("--jobs 1 --no-pointer-capture --profile"), 2)
        self.assertIn('PRKS_E2E_ROUTE_FASTPATH=0', flow)
        self.assertNotIn('PRKS_E2E_ROUTE_FASTPATH=1', flow)
        self.assertIn('expected = ["profile"]', flow)
        self.assertIn('captured', flow)
        self.assertIn('browser DOM milestone totals disagree', flow)

    def test_cli_does_not_touch_source_file(self):
        original = INDEX.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as raw:
            src, dest = Path(raw) / "input.html", Path(raw) / "candidate.html"
            src.write_text(original, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "benchmark_deferred_scripts.py"),
                 "--input", str(src), "--output", str(dest)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("84 classic scripts", result.stdout)
            self.assertEqual(src.read_text(encoding="utf-8"), original)
            self.assertEqual(dest.read_text(encoding="utf-8"), deferred_index(original)[0])


if __name__ == "__main__":
    unittest.main()
