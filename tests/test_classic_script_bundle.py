"""Regression guards for the strict classic-script bundle candidate."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts.classic_script_bundle import (
    BODY_URL,
    EXPECTED_CLASSIC_COUNTS,
    HEAD_URL,
    build_candidate,
)

ROOT = Path(__file__).resolve().parents[1]


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


class ClassicBundleCandidateTests(unittest.TestCase):
    def test_real_repo_candidate_preserves_head_body_and_module(self):
        original = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
        candidate, bundles, counts = build_candidate(ROOT)
        self.assertEqual(counts, EXPECTED_CLASSIC_COUNTS)
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

if __name__ == "__main__":
    unittest.main()
