"""Structural guards for DESIGN.md, tokens, primitives, and inline-style policy."""
from __future__ import annotations

import os
import re
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DESIGN = os.path.join(_PROJECT_DIR, "DESIGN.md")
_CSS = os.path.join(_PROJECT_DIR, "frontend", "css", "style.css")
_INDEX = os.path.join(_PROJECT_DIR, "frontend", "index.html")
_MANIFEST = os.path.join(_PROJECT_DIR, "frontend", "manifest.webmanifest")
_GALLERY = os.path.join(_PROJECT_DIR, "tests", "browser", "design_system.html")
_FRONTEND_JS = os.path.join(_PROJECT_DIR, "frontend", "js")

_ACCENT = "#6d6cf7"

_REQUIRED_DESIGN_HEADINGS = (
    "Purpose and authority",
    "Design principles",
    "Brand mark versus application accent",
    "Semantic token system",
    "Canonical component vocabulary",
    "Local / content tabs versus workspace tabs",
    "Future workspace / tab visual contract",
    "Inline-style policy",
    "Documented exceptions",
)

_REQUIRED_TOKENS = (
    "--bg-color:",
    "--surface:",
    "--surface-muted:",
    "--surface-inset:",
    "--surface-selected:",
    "--sidebar-bg:",
    "--text-primary:",
    "--text-secondary:",
    "--text-tertiary:",
    "--text-inverse:",
    "--text-danger:",
    "--accent:",
    "--accent-hover:",
    "--accent-soft:",
    "--danger:",
    "--danger-soft:",
    "--success:",
    "--success-soft:",
    "--warning:",
    "--warning-soft:",
    "--info:",
    "--info-soft:",
    "--border:",
    "--border-strong:",
    "--focus-ring:",
    "--radius-sm:",
    "--radius-md:",
    "--radius-lg:",
    "--radius-round:",
    "--shadow-sm:",
    "--shadow-md:",
    "--space-xs:",
    "--space-3xl:",
    "--text-2xs:",
    "--text-2xl:",
    "--weight-normal:",
    "--weight-bold:",
    "--line-compact:",
    "--line-reading:",
    "--control-height-sm:",
    "--control-height-lg:",
    "--icon-sm:",
    "--icon-xl:",
    "--duration-fast:",
    "--duration-normal:",
    "--ease-standard:",
    "--ease-out-soft:",
)

_REQUIRED_PRIMITIVES = (
    ".prks-btn",
    ".prks-btn--primary",
    ".prks-btn--secondary",
    ".prks-btn--ghost",
    ".prks-btn--danger",
    ".prks-entity-choice",
    ".prks-icon-btn",
    ".prks-field",
    ".prks-input",
    ".prks-page-header",
    ".prks-toolbar",
    ".prks-panel",
    ".prks-card",
    ".prks-list-row",
    ".prks-tabs",
    ".prks-tab",
    ".prks-tag",
    ".prks-chip",
    ".prks-badge",
    ".prks-state",
    ".prks-workspace-tabs",
    ".prks-workspace-tab",
    ".prks-tile",
    ".prks-splitter",
)

_GALLERY_SECTIONS = (
    "typography",
    "surfaces",
    "buttons",
    "entity-choice",
    "icon-buttons",
    "fields",
    "segmented",
    "tabs",
    "cards",
    "rows",
    "tags",
    "chips",
    "badges",
    "status",
    "nav",
    "page-header",
    "toolbar",
    "panels",
    "states",
    "dialogs",
    "workspace",
)

_ALLOWED_INLINE_STYLE = re.compile(
    r"""style\s*=\s*(['"])\s*(?:--[a-zA-Z0-9-]+\s*:\s*[^;"']+\s*;?\s*)+\1""",
)

_STATIC_LAYOUT_HINT = re.compile(
    r"""style\s*=\s*['"][^'"]*(?:display\s*:|margin\s*:|margin-(?:top|bottom|left|right)\s*:|padding\s*:|font-size\s*:|gap\s*:|flex\s*:|width\s*:|height\s*:|color\s*:)""",
    re.I,
)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _root_token_block(css: str) -> str:
    start = css.find(":root {")
    self_end = css.find("@media (prefers-color-scheme: dark)", start)
    if start < 0 or self_end < 0:
        return css[:4000]
    return css[start:self_end]


def _iter_first_party_templates():
    yield _INDEX
    for root, _dirs, files in os.walk(_FRONTEND_JS):
        for name in files:
            if name.endswith(".js"):
                yield os.path.join(root, name)


class DesignSystemContractTests(unittest.TestCase):
    def test_design_md_exists_with_required_headings(self):
        self.assertTrue(os.path.isfile(_DESIGN))
        text = _read(_DESIGN)
        self.assertIn("PRKS is a dense, calm, utilitarian research workspace", text)
        for heading in _REQUIRED_DESIGN_HEADINGS:
            self.assertIn(heading, text, heading)
        self.assertIn(".prks-tabs", text)
        self.assertIn(".prks-workspace-tabs", text)
        self.assertIn("Local / content tabs", text)

    def test_canonical_tokens_and_square_radii(self):
        css = _read(_CSS)
        block = _root_token_block(css)
        for token in _REQUIRED_TOKENS:
            self.assertIn(token, block, token)
        self.assertIn("--accent: #6d6cf7;", block)
        self.assertRegex(block, r"--radius-sm:\s*0;")
        self.assertRegex(block, r"--radius-md:\s*0;")
        self.assertRegex(block, r"--radius-lg:\s*0;")
        self.assertIn("outline: 2px solid var(--focus-ring);", css)
        self.assertIn("prefers-reduced-motion: reduce", css)

    def test_canonical_primitives_exist(self):
        css = _read(_CSS)
        for name in _REQUIRED_PRIMITIVES:
            self.assertIn(name, css, name)

    def test_theme_chrome_matches_accent(self):
        html = _read(_INDEX)
        manifest = _read(_MANIFEST)
        self.assertIn('name="theme-color" content="%s"' % _ACCENT, html)
        self.assertIn('"theme_color": "%s"' % _ACCENT, manifest)
        self.assertNotIn("#1a94d2", manifest)

    def test_gallery_loads_production_css_only(self):
        self.assertTrue(os.path.isfile(_GALLERY))
        html = _read(_GALLERY)
        self.assertIn("/frontend/css/style.css", html)
        self.assertIn("/frontend/vendor/inter/inter.css", html)
        self.assertIn("/frontend/vendor/lucide/lucide.min.js", html)
        self.assertIn("?theme=light", html)
        self.assertIn("?theme=dark", html)
        self.assertNotIn("<link rel=\"stylesheet\" href=\"design", html)
        for section in _GALLERY_SECTIONS:
            self.assertIn('data-gallery-section="%s"' % section, html, section)
        self.assertIn("prks-workspace-tab", html)
        self.assertIn("prks-tile--main", html)
        self.assertIn("prks-splitter", html)
        self.assertNotIn("tailwind", html.lower())

    def test_inline_style_policy(self):
        violations = []
        for path in _iter_first_party_templates():
            src = _read(path)
            rel = os.path.relpath(path, _PROJECT_DIR)
            for match in re.finditer(r"""style=["']([^"']*)["']""", src):
                raw = match.group(0)
                value = match.group(1).strip()
                if not value:
                    violations.append("%s: empty style" % rel)
                    continue
                if _ALLOWED_INLINE_STYLE.search(raw) and not _STATIC_LAYOUT_HINT.search(raw):
                    continue
                decls = [part.strip() for part in value.split(";") if part.strip()]
                if decls and all(part.startswith("--") for part in decls):
                    continue
                violations.append("%s: %s" % (rel, raw[:160]))
        self.assertEqual(violations, [], "\n".join(violations[:40]))

    def test_legacy_button_classes_have_no_visual_ownership(self):
        css = _read(_CSS)
        for name in (".add-new-btn", ".btn-danger-outline", ".create-entity-btn", ".form-actions__btn"):
            self.assertNotIn(name, css, name)
        for path in _iter_first_party_templates():
            src = _read(path)
            rel = os.path.relpath(path, _PROJECT_DIR)
            for token in ("add-new-btn", "btn-danger-outline", "create-entity-btn", "form-actions__btn"):
                self.assertNotIn(token, src, "%s: %s" % (rel, token))
        self.assertIn(".prks-entity-choice", css)
        self.assertIn("prks-entity-choice", _read(_INDEX))
        chooser = _read(_INDEX).split("master-add-modal", 1)[1].split("work-modal", 1)[0]
        self.assertNotIn("prks-btn--primary", chooser)

    def test_ribbon_btn_is_top_ribbon_fitting_only(self):
        css = _read(_CSS)
        for match in re.finditer(r"^[ \t]*[^{}@/\n][^{]*\{", css, re.M):
            sel = match.group(0)
            if not re.search(r"(?<![\w-])\.ribbon-btn(?!__)", sel):
                continue
            self.assertIn("top-ribbon", sel, "unscoped ribbon-btn rule: %s" % sel.strip())
            start = match.end()
            end = css.find("}", start)
            body = css[start:end]
            for prop in ("background", "border:", "font-size", "box-shadow", "min-height"):
                self.assertNotIn(prop, body, "%s in %s" % (prop, sel.strip()))

    def test_generic_page_header_has_one_visual_source(self):
        css = _read(_CSS)
        self.assertEqual(len(re.findall(r"(?<![\w-])\.page-header\s*\{", css)), 1)
        self.assertEqual(len(re.findall(r"(?<![\w-])\.page-header h2\s*\{", css)), 0)
        self.assertIn(".prks-page-header,", css)
        self.assertIn(".prks-page-title,", css)

    def test_canonical_button_semantics_not_overridden_by_compat_classes(self):
        css = _read(_CSS)
        primary_pos = css.find(".prks-btn--primary {")
        self.assertGreater(primary_pos, 0)
        later = css[primary_pos + 1 :]
        self.assertNotIn(".add-new-btn {", later)
        self.assertNotIn(".btn-danger-outline {", later)
        self.assertNotIn(".create-entity-btn {", later)
        danger_pos = css.find(".prks-btn--danger {")
        self.assertGreater(danger_pos, 0)
        self.assertNotIn("background: var(--accent)", css[danger_pos : danger_pos + 220])


if __name__ == "__main__":
    unittest.main()
