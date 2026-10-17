"""Guard the manual E2E classic-script deferral A/B experiment.

The production index remains unchanged; benchmark-only copies preserve script
source ordering and disable the unrelated routing fast path in both arms.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import runpy
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

from scripts.benchmark_deferred_scripts import deferred_index, main

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "frontend" / "index.html"
WORKFLOW = ROOT / ".github" / "workflows" / "e2e-script-defer-benchmark.yml"


class ScriptDeferBenchmarkTests(unittest.TestCase):
    def test_defer_variant_preserves_every_src_and_module_tag(self):
        original = INDEX.read_text(encoding="utf-8")
        result, count = deferred_index(original)
        classics = re.findall(r'<script\s+src="(/[^"]+)"', original)
        self.assertGreater(len(classics), 0)
        self.assertEqual(count, len(classics))
        self.assertEqual(
            re.findall(r'<script[^>]*\bsrc="([^"]+)"', original),
            re.findall(r'<script[^>]*\bsrc="([^"]+)"', result),
        )
        self.assertEqual(result.count("<script defer src="), count)
        self.assertEqual(original.count("<script defer src="), 0)
        for module in (
            '<script type="module" src="/js/components/works-pdf.js"></script>',
            '<script type="module" src="/vue/prks-vue.js"></script>',
        ):
            self.assertIn(module, original)
            self.assertIn(module, result)
        self.assertNotEqual(original, result)
        self.assertEqual(deferred_index(original), (result, count))

    def test_transformation_refuses_partly_deferred_or_changed_html(self):
        original = INDEX.read_text(encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "already has async/defer"):
            deferred_index(original.replace(
                '<script src="/js/app.js">',
                '<script defer src="/js/app.js">',
                1,
            ))
        with self.assertRaisesRegex(ValueError, "no classic external scripts"):
            deferred_index(re.sub(
                r'<script\s+src="/[^"]+"></script>', "", original
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

    def test_manual_ab_summary_executes_on_both_modes(self):
        flow = WORKFLOW.read_text(encoding="utf-8")
        embedded = flow.split("          python - <<'PY'\n", 1)[1].split(
            "\n          PY", 1
        )[0]
        code = "\n".join(line[10:] for line in embedded.splitlines()) + "\n"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            summary_path = root / "summary.md"
            phases = {
                "routing_decision": 0.13,
                "server_start": 11.98,
                "server_spawn": 0.02,
                "server_http_ready": 11.96,
                "app_ready": 13.92,
                "app_navigation": 11.49,
                "app_sidebar_wait": 1.79,
                "app_folder_wait": 0.57,
                "app_route_wait": 0.07,
                "nav_response_wait": 0.25,
                "nav_response_body": 0.03,
                "nav_dom_content_loaded": 11.09,
                "nav_until_interactive": 9.05,
                "nav_interactive_to_dcl": 0.73,
                "nav_dcl_handlers": 1.31,
            }
            for trial, mode in enumerate(
                ("baseline", "deferred", "deferred", "baseline"), 1
            ):
                trial_phases = phases.copy()
                if mode == "deferred":
                    trial_phases["app_navigation"] -= 2.0
                    trial_phases["app_ready"] -= 2.0
                    trial_phases["nav_dom_content_loaded"] -= 2.0
                    trial_phases["nav_until_interactive"] -= 2.0
                payload = {
                    "schema_version": 2,
                    "benchmark_modes": ["profile"],
                    "workers": 1,
                    "tests": 40,
                    "failures": 0,
                    "errors": 0,
                    "skipped": 0,
                    "wall_seconds": 49.0 if mode == "baseline" else 45.0,
                    "phase_seconds": trial_phases,
                    "navigation_samples": {"attempted": 38, "captured": 38},
                }
                (root / f"e2e-script-defer-ab-{trial}.json").write_text(
                    json.dumps(payload), encoding="utf-8"
                )
            env = {
                **os.environ,
                "RUNNER_TEMP": raw,
                "GITHUB_STEP_SUMMARY": str(summary_path),
                "GITHUB_SHA": "a" * 40,
            }
            report_script = root / "summary-code.py"
            report_script.write_text(code, encoding="utf-8")
            with (
                mock.patch.dict(os.environ, env),
                contextlib.redirect_stdout(StringIO()),
            ):
                runpy.run_path(str(report_script), run_name="__main__")
            report = summary_path.read_text(encoding="utf-8")
            self.assertIn("E2E classic-script deferral A/B", report)
            self.assertIn("Deferred-script change: **-8.16%**", report)
            self.assertIn("| ↳ Response-end → DOM interactive | 9.05s | 7.05s |", report)
            self.assertIn("trial 4: 38/38", report)

    def test_cli_does_not_touch_source_file(self):
        original = INDEX.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as raw:
            src, dest = Path(raw) / "input.html", Path(raw) / "candidate.html"
            src.write_text(original, encoding="utf-8")
            printed = StringIO()
            with (
                mock.patch.object(sys, "argv", [
                    "benchmark_deferred_scripts.py", "--input", str(src),
                    "--output", str(dest),
                ]),
                contextlib.redirect_stdout(printed),
            ):
                main()
            self.assertIn(
                f"{deferred_index(original)[1]} classic scripts", printed.getvalue()
            )
            self.assertEqual(src.read_text(encoding="utf-8"), original)
            self.assertEqual(dest.read_text(encoding="utf-8"), deferred_index(original)[0])


if __name__ == "__main__":
    unittest.main()
