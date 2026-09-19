"""Structural guards: ordinary UI deps are local and version-pinned.

Expected versions come from authoritative sources (tools/*/package.json,
frontend/vendor/*/VERSION, DEPENDENCY-MANIFEST), not hard-coded literals.
"""
import json
import os
import unittest
from pathlib import Path

_PROJECT_DIR = Path(__file__).resolve().parents[1]
_FRONTEND = _PROJECT_DIR / "frontend"
_INDEX = _FRONTEND / "index.html"
_VENDOR = _FRONTEND / "vendor"

_DEP_CDN_MARKERS = (
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdn.jsdelivr.net/npm/easymde",
    "cdn.jsdelivr.net/npm/codemirror",
    "cdn.jsdelivr.net/npm/lucide",
    "cdn.jsdelivr.net/npm/@embedpdf/snippet",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _frontend_vendor_pins() -> dict:
    return json.loads((_PROJECT_DIR / "tools" / "frontend-vendor" / "package.json").read_text())[
        "dependencies"
    ]


def _cytoscape_pin() -> str:
    return json.loads((_PROJECT_DIR / "tools" / "research-graph" / "package.json").read_text())[
        "dependencies"
    ]["cytoscape"]


def _iter_production_loader_files():
    yield _INDEX
    css = _FRONTEND / "css"
    if css.is_dir():
        for path in css.rglob("*.css"):
            yield path
    js = _FRONTEND / "js"
    if js.is_dir():
        for path in js.rglob("*.js"):
            yield path


class FrontendDependencyTests(unittest.TestCase):
    def test_inter_vendor_pin(self):
        woff = _VENDOR / "inter" / "InterVariable.woff2"
        version = _read(_VENDOR / "inter" / "VERSION")
        license_txt = _read(_VENDOR / "inter" / "LICENSE")
        css = _read(_VENDOR / "inter" / "inter.css")
        self.assertTrue(woff.is_file())
        self.assertGreater(woff.stat().st_size, 10000)
        self.assertIn("github.com/rsms/inter", version)
        self.assertIn("sha256:", version)
        self.assertIn("SIL Open Font License", license_txt)
        self.assertIn("/vendor/inter/InterVariable.woff2", css)
        self.assertNotIn("fetched:", version.lower())

    def test_easymde_vendor_pin(self):
        want = _frontend_vendor_pins()["easymde"]
        js = _VENDOR / "easymde" / "easymde.min.js"
        css = _VENDOR / "easymde" / "easymde.min.css"
        version = _read(_VENDOR / "easymde" / "VERSION")
        self.assertTrue(js.is_file())
        self.assertTrue(css.is_file())
        self.assertIn(want, version)
        self.assertGreater(js.stat().st_size, 10000)
        self.assertNotIn("fetched:", version.lower())

    def test_codemirror_vendor_pin(self):
        want = _frontend_vendor_pins()["codemirror"]
        lib = _VENDOR / "codemirror" / "codemirror.js"
        hint = _VENDOR / "codemirror" / "show-hint.js"
        hint_css = _VENDOR / "codemirror" / "show-hint.css"
        version = _read(_VENDOR / "codemirror" / "VERSION")
        self.assertTrue(lib.is_file())
        self.assertTrue(hint.is_file())
        self.assertTrue(hint_css.is_file())
        self.assertIn(want, version)
        self.assertNotIn("fetched:", version.lower())

    def test_lucide_vendor_pin(self):
        want = _frontend_vendor_pins()["lucide"]
        js = _VENDOR / "lucide" / "lucide.min.js"
        version = _read(_VENDOR / "lucide" / "VERSION")
        self.assertTrue(js.is_file())
        self.assertIn(want, version)
        self.assertGreater(js.stat().st_size, 10000)
        self.assertNotIn("fetched:", version.lower())

    def test_dompurify_unchanged(self):
        want = _frontend_vendor_pins()["dompurify"]
        version = _read(_VENDOR / "dompurify" / "VERSION")
        self.assertIn(want, version)
        self.assertIn("sha256:", version)
        self.assertNotIn("fetched:", version.lower())

    def test_cytoscape_vendor_pin(self):
        want = _cytoscape_pin()
        js = _VENDOR / "cytoscape" / "cytoscape.min.js"
        version = _read(_VENDOR / "cytoscape" / "VERSION")
        self.assertTrue(js.is_file())
        self.assertIn(want, version)
        self.assertGreater(js.stat().st_size, 10000)
        self.assertNotIn("cdn.jsdelivr.net", version)
        self.assertNotIn("fetched:", version.lower())

    def test_dependency_manifest_lists_vendor_runtime(self):
        manifest = json.loads(_read(_VENDOR / "DEPENDENCY-MANIFEST.json"))
        names = {d["name"] for d in manifest["dependencies"]}
        for name in ("dompurify", "easymde", "codemirror", "lucide", "cytoscape", "inter", "prks-pdf-viewer"):
            self.assertIn(name, names)

    def test_index_loads_local_deps_in_order(self):
        html = _read(_INDEX)
        inter = html.find('href="/vendor/inter/inter.css"')
        cm = html.find('src="/vendor/codemirror/codemirror.js"')
        hint = html.find('src="/vendor/codemirror/show-hint.js"')
        easy = html.find('src="/vendor/easymde/easymde.min.js"')
        purify = html.find('src="/vendor/dompurify/purify.min.js"')
        sanitize = html.find('src="/js/markdown-sanitize.js"')
        lucide = html.find('src="/vendor/lucide/lucide.min.js"')
        icons = html.find('src="/js/icons.js"')
        cy = html.find('src="/vendor/cytoscape/cytoscape.min.js"')
        graph = html.find('src="/js/components/research-graph.js"')
        for label, pos in (
            ("inter", inter),
            ("codemirror", cm),
            ("show-hint", hint),
            ("easymde", easy),
            ("purify", purify),
            ("sanitize", sanitize),
            ("lucide", lucide),
            ("icons", icons),
            ("cytoscape", cy),
            ("research-graph", graph),
        ):
            self.assertNotEqual(pos, -1, label)
        self.assertLess(inter, cm)
        self.assertLess(cm, hint)
        self.assertLess(hint, easy)
        self.assertLess(easy, purify)
        self.assertLess(purify, sanitize)
        self.assertLess(lucide, icons)
        self.assertLess(cy, graph)

    def test_no_ordinary_dependency_cdns_in_production_loaders(self):
        for path in _iter_production_loader_files():
            src = _read(path)
            for marker in _DEP_CDN_MARKERS:
                self.assertNotIn(marker, src, f"{path} contains {marker}")


if __name__ == "__main__":
    unittest.main()
