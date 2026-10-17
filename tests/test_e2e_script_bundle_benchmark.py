"""Regression guards for the manual classic-script coalescing A/B benchmark."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.benchmark_classic_script_bundle import (
    BODY_URL,
    HEAD_URL,
    build_candidate,
    run_benchmark,
    summarize,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/e2e-classic-bundle-benchmark.yml"


def fixture_root(root: Path) -> Path:
    frontend = root / "frontend"
    (frontend / "js").mkdir(parents=True)
    (frontend / "js/head.js").write_text("window.headReady = true;\n", encoding="utf-8")
    (frontend / "js/first.js").write_text("window.firstReady = true;\n", encoding="utf-8")
    (frontend / "js/second.js").write_text("window.secondReady = true;\n", encoding="utf-8")
    (frontend / "js/module.js").write_text("export const x = 1;\n", encoding="utf-8")
    (frontend / "index.html").write_text(
        "<!doctype html>\n<html><head>\n"
        '<script src="/js/head.js"></script>\n'
        "</head><body>\n"
        '<script src="/js/first.js"></script>\n'
        '<script type="module" src="/js/module.js"></script>\n'
        '<script src="/js/second.js"></script>\n'
        "</body></html>\n",
        encoding="utf-8",
    )
    return root


def trial_payload(wall: float) -> dict:
    return {
        "schema_version": 2,
        "benchmark_modes": ["profile"],
        "workers": 1,
        "tests": 40,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "wall_seconds": wall,
        "navigation_samples": {"attempted": 38, "captured": 38},
        "phase_seconds": {
            "server_start": 15.0,
            "server_spawn": 0.1,
            "server_http_ready": 14.9,
            "app_ready": 18.0,
            "app_navigation": 15.0,
            "app_sidebar_wait": 2.0,
            "app_folder_wait": 0.9,
            "app_route_wait": 0.1,
            "nav_dom_content_loaded": 14.0,
            "nav_until_interactive": 12.0,
            "nav_interactive_to_dcl": 1.0,
            "nav_dcl_handlers": 1.0,
        },
    }


class ClassicBundleBenchmarkTests(unittest.TestCase):
    def test_real_repo_candidate_preserves_head_body_and_module(self):
        original = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
        candidate, bundles, counts = build_candidate(ROOT)
        self.assertEqual(counts["head"] + counts["body"], 84)
        self.assertEqual(set(bundles), {HEAD_URL, BODY_URL})
        self.assertEqual(candidate.count('<script src="/__e2e_classic_'), 2)
        self.assertEqual(candidate.count('<script type="module"'), 2)
        self.assertEqual(original.count('<script src="/__e2e_classic_'), 0)
        self.assertLess(candidate.index(HEAD_URL), candidate.index("</head>"))
        self.assertGreater(candidate.index(BODY_URL), candidate.index("<body>"))
        self.assertLess(candidate.index(BODY_URL), candidate.index("/js/components/works-pdf.js"))
        self.assertLess(
            bundles[BODY_URL].index("benchmark source: /js/components/research-graph.js"),
            bundles[BODY_URL].index("benchmark source: /js/app.js"),
        )
        self.assertEqual((ROOT / "frontend/index.html").read_text(encoding="utf-8"), original)

    @unittest.skipUnless(shutil.which("node"), "Node.js required for JS parser check")
    def test_generated_real_bundles_are_parseable_javascript(self):
        _, bundles, _ = build_candidate(ROOT)
        with tempfile.TemporaryDirectory() as td:
            for name, content in bundles.items():
                path = Path(td) / Path(name).name
                path.write_text(content, encoding="utf-8")
                result = subprocess.run(
                    ["node", "--check", str(path)],
                    text=True, capture_output=True, check=False,
                )
                self.assertEqual(result.returncode, 0, f"{name}: {result.stderr}")

    def test_explicit_script_boundaries_and_sources(self):
        with tempfile.TemporaryDirectory() as td:
            root = fixture_root(Path(td))
            candidate, bundles, counts = build_candidate(root)
            self.assertEqual(counts, {"head": 1, "body": 2})
            self.assertIn("window.headReady", bundles[HEAD_URL])
            self.assertLess(
                bundles[BODY_URL].index("window.firstReady"),
                bundles[BODY_URL].index("window.secondReady"),
            )
            self.assertIn('<script type="module" src="/js/module.js"></script>', candidate)
            self.assertNotIn('<script src="/js/second.js"></script>', candidate)

    def test_refuses_unsupported_and_missing_script_sources(self):
        with tempfile.TemporaryDirectory() as td:
            root = fixture_root(Path(td))
            index = root / "frontend/index.html"
            baseline = index.read_text(encoding="utf-8")
            for unsupported in ('<script src="/js/first.js" defer></script>',
                                '<script>window.foo = 1;</script>',
                                '<script src="/../escape.js"></script>'):
                index.write_text(baseline.replace(
                    '<script src="/js/first.js"></script>', unsupported
                ), encoding="utf-8")
                with self.subTest(unsupported=unsupported):
                    with self.assertRaises(ValueError):
                        build_candidate(root)
            index.write_text(baseline, encoding="utf-8")
            (root / "frontend/js/first.js").unlink()
            with self.assertRaisesRegex(ValueError, "does not resolve"):
                build_candidate(root)

    def test_restore_source_and_temp_assets_on_tool_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = fixture_root(Path(td))
            source = root / "frontend/index.html"
            baseline = source.read_bytes()
            with (
                patch.dict(os.environ, {"PRKS_E2E_PROFILE": "1"}),
                patch("scripts.benchmark_classic_script_bundle.subprocess.run",
                      side_effect=subprocess.CalledProcessError(1, "node")),
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    run_benchmark(root)
            self.assertEqual(source.read_bytes(), baseline)
            self.assertFalse((root / "frontend" / HEAD_URL.lstrip("/")).exists())
            self.assertFalse((root / "frontend" / BODY_URL.lstrip("/")).exists())

    def test_summary_checks_complete_matching_trials(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = [root / f"{i}.json" for i in range(4)]
            for path, wall in zip(paths, (50.0, 40.0, 40.0, 50.0)):
                path.write_text(json.dumps(trial_payload(wall)), encoding="utf-8")
            report = summarize(paths, "a" * 40)
            self.assertIn("Bundle change **-20.00%**", report)
            self.assertIn("38/38", report)
            self.assertIn("Response → DOM interactive", report)
            corrupted = trial_payload(40.0)
            corrupted["navigation_samples"]["captured"] = 30
            paths[1].write_text(json.dumps(corrupted), encoding="utf-8")
            partial_report = summarize(paths, "a" * 40)
            self.assertNotIn("Response → DOM interactive", partial_report)
            corrupted["failures"] = 1
            paths[1].write_text(json.dumps(corrupted), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "incomplete or failed"):
                summarize(paths, "a" * 40)

    def test_manual_workflow_has_no_normal_ci_side_effect(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("pull_request:", workflow)
        self.assertNotIn("push:", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn('PRKS_E2E_PROFILE: "1"', workflow)
        self.assertIn("benchmark_classic_script_bundle.py --run", workflow)


if __name__ == "__main__":
    unittest.main()
