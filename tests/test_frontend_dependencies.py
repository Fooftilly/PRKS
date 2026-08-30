"""Structural guards: ordinary UI deps are local and version-pinned."""
import os
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_INDEX = os.path.join(_FRONTEND, "index.html")
_VENDOR = os.path.join(_FRONTEND, "vendor")

_DEP_CDN_MARKERS = (
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdn.jsdelivr.net/npm/easymde",
    "cdn.jsdelivr.net/npm/codemirror",
    "cdn.jsdelivr.net/npm/lucide",
    "cdn.jsdelivr.net/npm/@embedpdf/snippet",
)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _iter_production_loader_files():
    yield _INDEX
    css = os.path.join(_FRONTEND, "css")
    if os.path.isdir(css):
        for root, _dirs, files in os.walk(css):
            for name in files:
                if name.endswith(".css"):
                    yield os.path.join(root, name)
    js = os.path.join(_FRONTEND, "js")
    if os.path.isdir(js):
        for root, _dirs, files in os.walk(js):
            for name in files:
                if name.endswith(".js"):
                    yield os.path.join(root, name)


class FrontendDependencyTests(unittest.TestCase):
    def test_inter_vendor_pin(self):
        woff = os.path.join(_VENDOR, "inter", "InterVariable.woff2")
        version = _read(os.path.join(_VENDOR, "inter", "VERSION"))
        license_txt = _read(os.path.join(_VENDOR, "inter", "LICENSE"))
        css = _read(os.path.join(_VENDOR, "inter", "inter.css"))
        self.assertTrue(os.path.isfile(woff))
        self.assertGreater(os.path.getsize(woff), 10000)
        self.assertIn("4.1", version)
        self.assertIn("github.com/rsms/inter", version)
        self.assertIn(
            "693b77d4f32ee9b8bfc995589b5fad5e99adf2832738661f5402f9978429a8e3",
            version,
        )
        self.assertIn("SIL Open Font License", license_txt)
        self.assertIn("/vendor/inter/InterVariable.woff2", css)

    def test_easymde_vendor_pin(self):
        js = os.path.join(_VENDOR, "easymde", "easymde.min.js")
        css = os.path.join(_VENDOR, "easymde", "easymde.min.css")
        version = _read(os.path.join(_VENDOR, "easymde", "VERSION"))
        self.assertTrue(os.path.isfile(js))
        self.assertTrue(os.path.isfile(css))
        self.assertIn("2.21.0", version)
        self.assertGreater(os.path.getsize(js), 10000)

    def test_codemirror_vendor_pin(self):
        lib = os.path.join(_VENDOR, "codemirror", "codemirror.js")
        hint = os.path.join(_VENDOR, "codemirror", "show-hint.js")
        hint_css = os.path.join(_VENDOR, "codemirror", "show-hint.css")
        version = _read(os.path.join(_VENDOR, "codemirror", "VERSION"))
        self.assertTrue(os.path.isfile(lib))
        self.assertTrue(os.path.isfile(hint))
        self.assertTrue(os.path.isfile(hint_css))
        self.assertIn("5.65.15", version)

    def test_lucide_vendor_pin(self):
        js = os.path.join(_VENDOR, "lucide", "lucide.min.js")
        version = _read(os.path.join(_VENDOR, "lucide", "VERSION"))
        self.assertTrue(os.path.isfile(js))
        self.assertIn("0.511.0", version)
        self.assertGreater(os.path.getsize(js), 10000)

    def test_dompurify_unchanged(self):
        version = _read(os.path.join(_VENDOR, "dompurify", "VERSION"))
        self.assertIn("3.4.14", version)

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
        for label, pos in (
            ("inter", inter),
            ("codemirror", cm),
            ("show-hint", hint),
            ("easymde", easy),
            ("purify", purify),
            ("sanitize", sanitize),
            ("lucide", lucide),
            ("icons", icons),
        ):
            self.assertNotEqual(pos, -1, label)
        self.assertLess(inter, cm)
        self.assertLess(cm, hint)
        self.assertLess(hint, easy)
        self.assertLess(easy, purify)
        self.assertLess(purify, sanitize)
        self.assertLess(lucide, icons)

    def test_no_ordinary_dependency_cdns_in_production_loaders(self):
        for path in _iter_production_loader_files():
            src = _read(path)
            for marker in _DEP_CDN_MARKERS:
                self.assertNotIn(marker, src, f"{path} contains {marker}")


if __name__ == "__main__":
    unittest.main()
