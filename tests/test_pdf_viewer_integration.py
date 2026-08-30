"""Structural guards for the pinned local PRKS PDF viewer."""
import hashlib
import json
import os
import re
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_VENDOR = os.path.join(_FRONTEND, "vendor", "prks-pdf-viewer")
_TOOLS = os.path.join(_PROJECT_DIR, "tools", "pdf-viewer")
_INDEX = os.path.join(_FRONTEND, "index.html")
_RUNTIME = os.path.join(_FRONTEND, "js", "pdf-viewer-runtime.js")
_VIEWER_SRC = os.path.join(_TOOLS, "src", "viewer.tsx")
_PAGE_VIEW_SRC = os.path.join(_TOOLS, "src", "page-view.tsx")
_PLUGINS_SRC = os.path.join(_TOOLS, "src", "plugins.ts")
_PACKAGE = os.path.join(_TOOLS, "package.json")
_LOCK = os.path.join(_TOOLS, "package-lock.json")
_FIXTURE = os.path.join(_PROJECT_DIR, "tests", "browser", "pdf_viewer.html")
_POINTER = os.path.join(_PROJECT_DIR, "tests", "browser", "pointer_capture.py")
_SERVE = os.path.join(_PROJECT_DIR, "tests", "browser", "serve.py")
_MINIMAL_PDF = os.path.join(_PROJECT_DIR, "tests", "browser", "assets", "minimal.pdf")
_LARGE_PDF = os.path.join(_PROJECT_DIR, "tests", "browser", "assets", "large.pdf")
_PATCHES = os.path.join(_TOOLS, "patches")
_APPLY = os.path.join(_TOOLS, "scripts", "apply-embedpdf-patches.mjs")
_GESTURES = os.path.join(_TOOLS, "src", "gestures.tsx")
_PARITY = os.path.join(_PROJECT_DIR, "tests", "PDF_VIEWER_PARITY.md")
_MANUAL = os.path.join(_PROJECT_DIR, "tests", "PDF_VIEWER_MANUAL.md")
_COVERAGE_TOKEN = re.compile(r"^(browser|playwright|python|manual):([a-z0-9-]+)$")
_COVERAGE_EMPTY = frozenset({"", "—", "-", "–"})

_REQUIRED_PATCHES = (
    "embedpdf-plugin-selection-2.15.0.patch",
    "embedpdf-plugin-pan-2.15.0.patch",
    "embedpdf-plugin-viewport-2.15.0.patch",
)

_BANNED = ("@embedpdf/snippet", "@embedpdf/react-pdf-viewer")
_CDN_MARKERS = (
    "cdn.jsdelivr.net/npm/@embedpdf/snippet",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _parity_marker(kind: str, slug: str) -> str:
    if kind == "browser":
        return f'data-parity="{slug}"'
    if kind in ("playwright", "python"):
        return f"# parity: {slug}"
    if kind == "manual":
        return f"[manual:{slug}]"
    raise AssertionError(kind)


def _parity_artifact_text(kind: str) -> str:
    if kind == "browser":
        return _read(_FIXTURE)
    if kind == "playwright":
        return _read(_POINTER)
    if kind == "manual":
        return _read(_MANUAL)
    if kind == "python":
        tests_dir = os.path.join(_PROJECT_DIR, "tests")
        chunks = []
        for name in sorted(os.listdir(tests_dir)):
            if name.startswith("test_") and name.endswith(".py"):
                chunks.append(_read(os.path.join(tests_dir, name)))
        return "\n".join(chunks)
    raise AssertionError(kind)


def _parse_parity_rows(md: str):
    rows = []
    in_table = False
    header = None
    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and "Classification" in stripped and "Coverage" in stripped:
            header = [c.strip() for c in stripped.strip("|").split("|")]
            in_table = True
            continue
        if in_table:
            if not stripped.startswith("|"):
                in_table = False
                header = None
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if cells and set(cells[0]) <= set("-: "):
                continue
            if header and len(cells) >= 3:
                rec = dict(zip(header, cells))
                rows.append(rec)
    return rows


def _iter_production_js():
    js = os.path.join(_FRONTEND, "js")
    for root, _dirs, files in os.walk(js):
        for name in files:
            if name.endswith(".js"):
                yield os.path.join(root, name)


class PdfViewerIntegrationTests(unittest.TestCase):
    def test_vendor_bundle_and_manifest_pins(self):
        manifest = json.loads(_read(os.path.join(_VENDOR, "BUILD-MANIFEST.json")))
        version = _read(os.path.join(_VENDOR, "VERSION"))
        pkg = json.loads(_read(_PACKAGE))
        self.assertEqual(manifest["embedpdf"], "2.15.0")
        self.assertEqual(manifest["react"], "18.3.1")
        self.assertEqual(manifest["reactDom"], "18.3.1")
        self.assertIsNone(manifest["fontFallback"])
        self.assertFalse(manifest["tiling"])
        self.assertEqual(pkg["dependencies"]["react"], "18.3.1")
        self.assertEqual(pkg["dependencies"]["react-dom"], "18.3.1")
        self.assertEqual(pkg["dependencies"]["@embedpdf/core"], "2.15.0")
        self.assertIn("embedpdf 2.15.0", version)
        self.assertIn("react 18.3.1", version)
        self.assertIn("fontFallback null", version)
        js = os.path.join(_VENDOR, "prks-pdf-viewer.js")
        css = os.path.join(_VENDOR, "prks-pdf-viewer.css")
        wasm = os.path.join(_VENDOR, "pdfium.wasm")
        self.assertTrue(os.path.isfile(js))
        self.assertTrue(os.path.isfile(css))
        self.assertTrue(os.path.isfile(wasm))
        self.assertGreater(os.path.getsize(js), 100000)
        self.assertGreater(os.path.getsize(wasm), 100000)
        self.assertTrue(os.path.isfile(os.path.join(_VENDOR, "LICENSES", "react-LICENSE")))
        self.assertTrue(os.path.isfile(os.path.join(_VENDOR, "THIRD_PARTY.md")))
        patches = manifest.get("embedpdfPatches") or []
        self.assertEqual(len(patches), 3, patches)
        names = {p["package"] for p in patches}
        self.assertEqual(
            names,
            {
                "@embedpdf/plugin-selection",
                "@embedpdf/plugin-pan",
                "@embedpdf/plugin-viewport",
            },
        )
        listed = {p["patch"] for p in patches}
        for name in _REQUIRED_PATCHES:
            path = os.path.join(_PATCHES, name)
            self.assertTrue(os.path.isfile(path), name)
            with open(path, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
            match = next(p for p in patches if p["patch"] == name)
            self.assertEqual(match["version"], "2.15.0")
            self.assertEqual(match["sha256"], digest)
            self.assertEqual(len(match["sha256"]), 64)
        self.assertEqual(listed, set(_REQUIRED_PATCHES))
        self.assertTrue(os.path.isfile(_APPLY))
        self.assertIn("apply-embedpdf-patches.mjs", pkg.get("scripts", {}).get("postinstall", ""))

    def test_react_bundled_once_in_metafile_inputs(self):
        manifest = json.loads(_read(os.path.join(_VENDOR, "BUILD-MANIFEST.json")))
        inputs = manifest["reactMetafileInputs"]
        norm = [p.replace("\\", "/") for p in inputs]
        react = [p for p in norm if "/node_modules/react/" in f"/{p}"]
        react_dom = [p for p in norm if "/node_modules/react-dom/" in f"/{p}"]
        self.assertTrue(react, inputs)
        self.assertTrue(react_dom, inputs)
        self.assertTrue(any("react.production.min.js" in p for p in react), react)
        self.assertTrue(any("react-dom.production.min.js" in p for p in react_dom), react_dom)

    def test_lockfile_unique_react_and_no_banned_packages(self):
        lock = json.loads(_read(_LOCK))
        pkg = json.loads(_read(_PACKAGE))
        for banned in _BANNED:
            self.assertNotIn(banned, pkg.get("dependencies", {}))
            self.assertNotIn(banned, pkg.get("devDependencies", {}))
        packages = lock.get("packages") or {}
        react_versions = set()
        react_dom_versions = set()
        names = []
        for key, meta in packages.items():
            name = meta.get("name") or key.rsplit("node_modules/", 1)[-1]
            names.append(name)
            if name == "react" and meta.get("version"):
                react_versions.add(meta["version"])
            if name == "react-dom" and meta.get("version"):
                react_dom_versions.add(meta["version"])
        for banned in _BANNED:
            self.assertNotIn(banned, names)
        self.assertEqual(react_versions, {"18.3.1"})
        self.assertEqual(react_dom_versions, {"18.3.1"})

    def test_source_font_fallback_null_and_pan_baseline(self):
        viewer = _read(_VIEWER_SRC)
        plugins = _read(_PLUGINS_SRC)
        self.assertIn("fontFallback: null", viewer)
        self.assertIn("wasmUrl", viewer)
        self.assertIn("worker: true", viewer)
        self.assertIn("PanPluginPackage", plugins)
        self.assertIn("defaultZoomLevel: ZoomMode.FitWidth", plugins)
        # parity: fit-width-default
        self.assertIn("defaultMode: 'never'", plugins)
        self.assertNotIn("TilingPluginPackage", plugins)
        self.assertNotIn("plugin-tiling", plugins)
        self.assertNotIn("SelectionReleaseGuard", viewer)
        self.assertNotIn("PanLayer", viewer)
        self.assertIn("WheelZoom", viewer)
        # parity: wheel-zoom
        self.assertIn("enableWheel={false}", viewer)
        gestures = _read(_GESTURES)
        self.assertIn("export function WheelZoom", gestures)
        self.assertNotIn("SelectionReleaseGuard", gestures)
        self.assertNotIn("PanLayer", gestures)
        self.assertIn("PRKS owns Ctrl/Cmd+wheel", gestures)
        page_view = _read(_PAGE_VIEW_SRC)
        self.assertIn('draggable={false}', page_view)
        self.assertIn("prks-pdf-render-image", page_view)
        self.assertIn("onDragStartCapture", page_view)

    def test_no_cjk_font_packs_in_vendor_output(self):
        for root, dirs, files in os.walk(_VENDOR):
            names = dirs + files
            for name in names:
                low = name.lower()
                self.assertNotIn("fonts-sc", low)
                self.assertNotIn("fonts-tc", low)
                self.assertNotIn("fonts-jp", low)
                self.assertNotIn("fonts-kr", low)
                self.assertNotIn("fonts-latin", low)

    def test_runtime_caches_one_import(self):
        src = _read(_RUNTIME)
        self.assertIn("prks-pdf-viewer.js", src)
        self.assertIn("loaders", src)
        self.assertNotIn("@embedpdf/snippet", src)

    def test_production_js_has_no_react_or_embedpdf_imports(self):
        for path in _iter_production_js():
            src = _read(path)
            self.assertNotIn("from 'react'", src, path)
            self.assertNotIn('from "react"', src, path)
            self.assertNotIn("from '@embedpdf", src, path)
            self.assertNotIn('from "@embedpdf', src, path)
            self.assertNotIn("@embedpdf/snippet", src, path)

    def test_no_snippet_cdn_in_loaders(self):
        html = _read(_INDEX)
        self.assertNotIn("cdn.jsdelivr.net/npm/@embedpdf/snippet", html)
        for path in _iter_production_js():
            src = _read(path)
            for marker in _CDN_MARKERS:
                self.assertNotIn(marker, src, f"{path} contains {marker}")

    def test_fixture_and_assets(self):
        html = _read(_FIXTURE)
        self.assertTrue(os.path.isfile(_MINIMAL_PDF))
        self.assertTrue(os.path.isfile(_LARGE_PDF))
        self.assertGreater(os.path.getsize(_MINIMAL_PDF), 400)
        self.assertGreater(os.path.getsize(_LARGE_PDF), 10000)
        self.assertIn("pdf_viewer.html", _read(_SERVE))
        self.assertIn(".wasm", _read(_SERVE))
        self.assertIn("application/wasm", _read(_SERVE))
        self.assertIn("createPrksPdfViewer", html)
        self.assertIn("font/wasm CDN", html)
        self.assertIn("goToPage(2)", html)
        self.assertIn("createAnnotation", html)
        self.assertIn("createAnnotation API", html)
        self.assertNotIn("popup fallback", html)
        self.assertIn("highlight committed", html)
        self.assertIn("toolbar highlight equals popup highlight", html)
        self.assertIn("toolbar underline equals popup underline", html)
        self.assertIn("reload highlight markup persists", html)
        self.assertIn("initialPage restores page", html)
        self.assertIn("scrollWidth", html)
        self.assertIn("isSelecting", html)
        self.assertIn("pointer clears markup tool", html)
        self.assertIn("pointer exclusive vs highlight", html)
        self.assertIn("pointercancel", html)
        self.assertIn("does not prove", html)
        self.assertIn("prks-pdf-render-image", html)
        self.assertIn("draggable === false", html)
        self.assertIn("dragstart", html)
        self.assertTrue(os.path.isfile(_POINTER))
        self.assertIn("Not collected by python run_tests.py", _read(_POINTER))
        self.assertIn("__prksNativeDragStarts", _read(_POINTER))

    def test_parity_keep_coverage_markers(self):
        self.assertTrue(os.path.isfile(_PARITY))
        self.assertTrue(os.path.isfile(_MANUAL))
        rows = _parse_parity_rows(_read(_PARITY))
        keep = [r for r in rows if r.get("Classification") == "KEEP"]
        self.assertGreaterEqual(len(keep), 8, keep)
        for row in keep:
            cap = row.get("Capability", "")
            coverage = row.get("Coverage", "")
            self.assertNotIn(coverage, _COVERAGE_EMPTY, cap)
            tokens = [t.strip() for t in coverage.split(",") if t.strip()]
            self.assertTrue(tokens, cap)
            for token in tokens:
                match = _COVERAGE_TOKEN.fullmatch(token)
                self.assertIsNotNone(match, f"{cap}: {token}")
                kind, slug = match.group(1), match.group(2)
                marker = _parity_marker(kind, slug)
                self.assertIn(marker, _parity_artifact_text(kind), f"{cap}: missing {marker}")

        types_src = _read(os.path.join(_TOOLS, "src", "types.ts"))
        self.assertIn("deleteAnnotation", types_src)
        self.assertNotIn("requestZoom", types_src)
        # parity: delete-annotation-api
        plugins = _read(_PLUGINS_SRC)
        self.assertIn("PRKS_MARKUP", plugins)
        self.assertIn("id: 'highlight'", plugins)
        self.assertIn("id: 'underline'", plugins)
        self.assertNotIn("setToolDefaults", plugins)
        self.assertNotIn("setToolDefaults", _read(_VIEWER_SRC))
        self.assertIn("annotation?.setActiveTool(null)", _read(_VIEWER_SRC))
        markup = _read(os.path.join(_TOOLS, "src", "markup.ts"))
        menu = _read(os.path.join(_TOOLS, "src", "selection-menu.tsx"))
        # parity: markup-source-parity
        self.assertIn("{ ...PRKS_MARKUP.highlight }", plugins)
        self.assertIn("{ ...PRKS_MARKUP.underline }", plugins)
        self.assertIn("const markup = PRKS_MARKUP[tool]", menu)
        self.assertIn("...markup", menu)
        self.assertIn("'#FFCD45'", markup)
        self.assertIn("'#2563eb'", markup)
        self.assertIn("PdfBlendMode.Multiply", markup)
        self.assertNotIn("#E44234", plugins)
        self.assertNotIn("#E44234", menu)
        works = _read(os.path.join(_FRONTEND, "js", "components", "works-pdf.js"))
        self.assertIn("onAnnotationCommentRequest", works)
        self.assertNotIn("onAnnotationSelect:", works)
        self.assertIn("initialPage: lastPage.initialPage", works)
        self.assertIn("if (!alive) return", works)
        self.assertIn("document.visibilityState === 'hidden'", works)
        self.assertIn("onLayoutReady", _read(_VIEWER_SRC))
        self.assertIn("pendingPageRef", _read(_VIEWER_SRC))
        self.assertIn("setInterval", _read(_VIEWER_SRC))
        types_src = _read(os.path.join(_TOOLS, "src", "types.ts"))
        self.assertIn("initialPage?: number", types_src)

        css = _read(os.path.join(_TOOLS, "src", "styles.css"))
        # parity: responsive-toolbar
        self.assertIn("container-type: inline-size", css)
        self.assertIn("@container (max-width: 640px)", css)
        self.assertIn("prks-pdf-toolbar__more", css)
        self.assertNotIn("overflow-x: auto", css)
        toolbar = _read(os.path.join(_TOOLS, "src", "toolbar.tsx"))
        self.assertIn('aria-label="Zoom level"', toolbar)
        self.assertIn("Fit width", toolbar)
        self.assertIn("Fit page", toolbar)
        self.assertIn("annotation?.setActiveTool(null)", toolbar)
        self.assertIn("const pointerActive = !isPanning && !toolId", toolbar)
        self.assertIn("aria-pressed={pointerActive}", toolbar)
        menu = _read(os.path.join(_TOOLS, "src", "floating-menu.tsx"))
        self.assertIn("onClick", menu)
        self.assertIn("onActivate", menu)
        src_dir = os.path.join(_TOOLS, "src")
        # parity: touch-selection-api-probe
        for name in os.listdir(src_dir):
            if not name.endswith((".ts", ".tsx")):
                continue
            text = _read(os.path.join(src_dir, name))
            self.assertNotIn(".setSelection(", text, name)

    def test_annotation_action_locks(self):
        src = _read(os.path.join(_TOOLS, "src", "annotation-actions.ts"))
        # parity: annotation-action-locks
        self.assertIn("deletable: isPrksMarkup && !input.structurallyLocked", src)
        self.assertIn("commentable: isPrksMarkup && !input.contentLocked", src)
        self.assertIn(
            "editable: isPrksMarkup && !input.structurallyLocked && !input.contentLocked",
            src,
        )
        self.assertIn("PdfAnnotationSubtype.HIGHLIGHT", src)
        self.assertIn("PdfAnnotationSubtype.UNDERLINE", src)
        highlight, underline, link, widget, freetext = 9, 10, 2, 20, 3

        def actions(type_, structurally_locked, content_locked):
            is_prks = type_ in (highlight, underline)
            return {
                "deletable": is_prks and not structurally_locked,
                "commentable": is_prks and not content_locked,
                "editable": is_prks and not structurally_locked and not content_locked,
            }

        cases = (
            ("hl unlocked", highlight, False, False, True, True, True),
            ("hl structurallyLocked", highlight, True, False, False, True, False),
            ("hl contentLocked", highlight, False, True, True, False, False),
            ("hl both locks", highlight, True, True, False, False, False),
            ("ul unlocked", underline, False, False, True, True, True),
            ("ul structurallyLocked", underline, True, False, False, True, False),
            ("ul contentLocked", underline, False, True, True, False, False),
            ("ul both locks", underline, True, True, False, False, False),
            ("link", link, False, False, False, False, False),
            ("widget", widget, False, False, False, False, False),
            ("freetext", freetext, False, False, False, False, False),
        )
        for name, type_, struct_lock, content_lock, deletable, commentable, editable in cases:
            got = actions(type_, struct_lock, content_lock)
            want = {
                "deletable": deletable,
                "commentable": commentable,
                "editable": editable,
            }
            self.assertEqual(got, want, name)


if __name__ == "__main__":
    unittest.main()
