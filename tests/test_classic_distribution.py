"""Deterministic production classic-script package and offline-cache contracts."""
from __future__ import annotations

import re
from pathlib import Path
import tempfile
import unittest

from scripts.build_classic_distribution import (
    BODY_BUNDLE,
    HEAD_BUNDLE,
    build_distribution,
    render_assets,
)

ROOT = Path(__file__).resolve().parents[1]


def fixture(root: Path) -> Path:
    frontend = root / "frontend"
    js = frontend / "js"
    js.mkdir(parents=True)
    lines = ["<!doctype html>\n", "<html><head>\n"]
    originals = []
    for i in range(5):
        path = f"/js/head{i}.js"
        (js / f"head{i}.js").write_text(f"window.prksHead{i} = {i};\n", encoding="utf-8")
        originals.append(path)
        lines.append(f'    <script src="{path}"></script>\n')
    lines.append("</head><body>\n")
    for i in range(79):
        path = f"/js/body{i}.js"
        (js / f"body{i}.js").write_text(f"window.prksBody{i} = {i};\n", encoding="utf-8")
        originals.append(path)
        if i == 40:
            lines.append('    <script type="module" src="/js/module.js"></script>\n')
        lines.append(f'    <script src="{path}"></script>\n')
    lines.append("</body></html>\n")
    (js / "module.js").write_text("export const MODULE = true;\n", encoding="utf-8")
    (frontend / "index.html").write_text("".join(lines), encoding="utf-8")
    entries = "".join(f"        '{path}',\n" for path in originals)
    (frontend / "sw.js").write_text(
        "const DEPENDENCY_REVISION = '123456789abc';\n"
        "const SHELL_CACHE = 'prks-shell-' + DEPENDENCY_REVISION;\n"
        "const STATIC_CACHE = 'prks-static-' + DEPENDENCY_REVISION;\n"
        "const STATIC_PATH_PREFIXES = ['/js/'];\n"
        "    const STATIC_PRECACHE_PATHS = [\n" + entries
        + "        '/js/module.js',\n"
        "    ];\n",
        encoding="utf-8",
    )
    return root


class ClassicDistributionTests(unittest.TestCase):
    def test_real_source_inventory_is_explicit_and_valid(self):
        html, bundles, worker = render_assets(ROOT)
        self.assertEqual(set(bundles), {HEAD_BUNDLE, BODY_BUNDLE})
        self.assertEqual(html.count(f'<script src="{HEAD_BUNDLE}"></script>'), 1)
        self.assertEqual(html.count(f'<script src="{BODY_BUNDLE}"></script>'), 1)
        self.assertEqual(html.count('<script type="module" src='), 3)
        self.assertEqual(bundles[HEAD_BUNDLE].count("/* bundled source:"), 5)
        self.assertEqual(bundles[BODY_BUNDLE].count("/* bundled source:"), 79)
        self.assertIn(f"'{HEAD_BUNDLE}',", worker)
        self.assertIn(f"'{BODY_BUNDLE}',", worker)
        self.assertIn("const CLASSIC_BUNDLE_REVISION = '", worker)

    def test_distribution_is_repeatable_and_preserves_the_source(self):
        with tempfile.TemporaryDirectory() as td:
            root = fixture(Path(td) / "repo")
            source_html = (root / "frontend/index.html").read_bytes()
            source_sw = (root / "frontend/sw.js").read_bytes()
            output_a = Path(td) / "dist-a"
            output_b = Path(td) / "dist-b"
            build_distribution(root, output_a)
            build_distribution(root, output_b)
            self.assertEqual(source_html, (root / "frontend/index.html").read_bytes())
            self.assertEqual(source_sw, (root / "frontend/sw.js").read_bytes())

            for name in ("index.html", "sw.js", HEAD_BUNDLE.lstrip("/"),
                         BODY_BUNDLE.lstrip("/"), "js/body0.js", "js/module.js"):
                self.assertEqual(
                    (output_a / name).read_bytes(),
                    (output_b / name).read_bytes(),
                    name,
                )
            generated_html = (output_a / "index.html").read_text(encoding="utf-8")
            generated_sw = (output_a / "sw.js").read_text(encoding="utf-8")
            self.assertEqual(generated_html.count("<script src="), 2)
            self.assertIn('<script type="module" src="/js/module.js"></script>', generated_html)
            self.assertNotIn('/js/body40.js"></script>', generated_html)
            self.assertIn("'/js/module.js',", generated_sw)
            self.assertIn(f"'{HEAD_BUNDLE}',", generated_sw)
            self.assertIn(f"'{BODY_BUNDLE}',", generated_sw)
            self.assertNotIn("'/js/body40.js',", generated_sw)
            self.assertNotIn("'/js/head0.js',", generated_sw)
            self.assertTrue(
                re.search(r"CLASSIC_BUNDLE_REVISION = '[0-9a-f]{16}';", generated_sw)
            )
            self.assertIn(
                "const SHELL_CACHE = 'prks-shell-' + DEPENDENCY_REVISION + '-' "
                "+ CLASSIC_BUNDLE_REVISION;", generated_sw
            )
            self.assertIn(
                "const STATIC_CACHE = 'prks-static-' + DEPENDENCY_REVISION + '-' "
                "+ CLASSIC_BUNDLE_REVISION;", generated_sw
            )
            self.assertEqual(
                (output_a / "js/head0.js").read_bytes(),
                (root / "frontend/js/head0.js").read_bytes(),
            )

    def test_preexisting_output_and_source_path_are_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = fixture(Path(td) / "repo")
            output = Path(td) / "exists"
            output.mkdir()
            with self.assertRaises(FileExistsError):
                build_distribution(root, output)
            with self.assertRaisesRegex(ValueError, "must not overwrite"):
                build_distribution(root, root / "frontend")
            with self.assertRaisesRegex(ValueError, "must not overwrite"):
                build_distribution(root, root / "frontend" / "dist")

    def test_missing_precache_entry_fails_without_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = fixture(Path(td) / "repo")
            sw = root / "frontend/sw.js"
            sw.write_text(
                sw.read_text(encoding="utf-8").replace("        '/js/body5.js',\n", ""),
                encoding="utf-8",
            )
            output = Path(td) / "dist"
            with self.assertRaisesRegex(ValueError, "missing from worker precache"):
                build_distribution(root, output)
            self.assertFalse(output.exists())

    def test_changed_inventory_or_missing_source_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = fixture(Path(td) / "repo")
            output = Path(td) / "dist"
            index = root / "frontend/index.html"
            index.write_text(
                index.read_text(encoding="utf-8").replace(
                    '    <script src="/js/body78.js"></script>\n', ""
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "inventory has changed"):
                build_distribution(root, output)
            self.assertFalse(output.exists())

    def test_changed_worker_version_contract_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = fixture(Path(td) / "repo")
            sw = root / "frontend/sw.js"
            sw.write_text(
                sw.read_text(encoding="utf-8").replace(
                    "const SHELL_CACHE = 'prks-shell-' + DEPENDENCY_REVISION;",
                    "const SHELL_CACHE = 'broken';",
                ), encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "cache revision contract"):
                render_assets(root)


if __name__ == "__main__":
    unittest.main()
