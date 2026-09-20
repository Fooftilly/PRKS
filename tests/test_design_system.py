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
    "Workspace / tab visual contract",
    "Inline-style policy",
    "Research entities",
    "Research visualization",
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
    ".prks-page-summary",
    ".prks-scope-line",
    ".prks-rel-summary",
    ".prks-state-summary",
    ".prks-nav-attention",
    ".prks-toolbar",
    ".prks-panel",
    ".prks-card",
    ".prks-list-row",
    ".prks-dialog",
    ".prks-filter-toggle",
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
    "summaries",
    "status",
    "nav",
    "page-header",
    "toolbar",
    "panels",
    "states",
    "dialogs",
    "research",
    "workspace",
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
        # Collapsed Research Notes specimen must use production class structure
        # (not an inline-styled imitation of the disclosure chrome).
        self.assertIn('class="work-workspace work-workspace--notes-collapsed"', html)
        self.assertIn('class="work-notes-pane"', html)
        self.assertIn('class="work-notes-pane-header"', html)
        self.assertIn('class="work-notes-title"', html)
        self.assertIn('data-prks-role="editor-status"', html)
        self.assertIn('class="work-editor-status"', html)
        self.assertNotIn(
            'class="work-notes-pane work-workspace--notes-collapsed"',
            html,
            "notes-collapsed belongs on .work-workspace, matching production DOM",
        )
        self.assertNotIn('style="height:var(--control-height-sm)', html)
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

    def test_work_delete_uses_canonical_danger(self):
        ui = _read(os.path.join(_FRONTEND_JS, "ui.js"))
        self.assertIn('class="prks-btn prks-btn--danger delete-work-btn"', ui)
        self.assertNotIn("prks-btn--secondary delete-work-btn", ui)
        css = _read(_CSS)
        self.assertNotIn(".delete-work-btn", css)
        self.assertNotIn("#ef4444", css.split(".prks-btn--danger {", 1)[-1][:800])

    def test_quiet_selected_state_contract(self):
        """Selected chrome uses surface + weight — not persistent purple edges/rings."""
        design = _read(_DESIGN)
        css = _read(_CSS)
        self.assertIn("### Selected / current state", design)
        self.assertIn("never a loud full-purple outline, outer ring, glow, or persistent accent stripe/underline", design)
        self.assertNotIn("Strongest selected indication (accent border/background)", design)
        self.assertNotIn("Optional inset accent edge", design)
        self.assertIn("persistent purple inset edges", design)

        for selector in (
            ".project-card--work-card.is-selected {",
            ".prks-list-row.is-selected {",
            ".prks-card.is-selected {",
            ".nav-link.active,",
            ".prks-settings-nav__item.is-active {",
            ".prks-command-palette__option.is-active {",
            ".prks-kind-toggle__btn.is-active {",
            ".research-graph__find-hit.is-active {",
            ".prks-workspace-overview__row.is-focused {",
        ):
            parts = css.split(selector, 1)
            self.assertEqual(len(parts), 2, selector)
            body = parts[1].split("}", 1)[0]
            self.assertIn("var(--surface-selected)", body, selector)
            self.assertNotIn("var(--accent)", body, selector)
            self.assertNotIn("inset 3px 0 0", body, selector)
            self.assertNotIn("inset 0 -2px 0 0", body, selector)

        tab_active = css.split(".prks-tab.is-active,", 1)
        self.assertEqual(len(tab_active), 2)
        tab_body = tab_active[1].split("}", 1)[0]
        self.assertIn("var(--border-strong)", tab_body)
        self.assertNotIn("var(--accent)", tab_body)

        nav_body = css.split(".nav-link.active,", 1)[1].split("}", 1)[0]
        self.assertNotIn("color: var(--accent)", nav_body)

    def test_annotation_sync_dots_use_semantic_tokens(self):
        css = _read(_CSS)
        saved = css.split(
            ".work-annotation-sync-status--saved .work-annotation-sync-status__label::before {",
            1,
        )
        self.assertEqual(len(saved), 2)
        saved_body = saved[1].split("}", 1)[0]
        self.assertIn("var(--success)", saved_body)
        self.assertNotIn("#16a34a", saved_body)
        err = css.split(
            ".work-annotation-sync-status--error .work-annotation-sync-status__label::before {",
            1,
        )
        self.assertEqual(len(err), 2)
        err_body = err[1].split("}", 1)[0]
        self.assertIn("var(--danger)", err_body)
        self.assertNotIn("#dc2626", err_body)
        saving = css.split(
            ".work-annotation-sync-status--saving .work-annotation-sync-status__label::before {",
            1,
        )[1].split("}", 1)[0]
        self.assertIn("var(--accent)", saving)
        conflict = css.split(
            ".work-annotation-sync-status--conflict .work-annotation-sync-status__label::before {",
            1,
        )
        self.assertEqual(len(conflict), 2)
        self.assertIn("var(--warning", conflict[1].split("}", 1)[0])
        self.assertIn("Keep server", _read(os.path.join(_FRONTEND_JS, "components", "works-pdf.js")))
        self.assertIn("Apply mine", _read(os.path.join(_FRONTEND_JS, "components", "works-pdf.js")))
        self.assertIn("Saved locally", _read(os.path.join(_FRONTEND_JS, "components", "works-pdf.js")))
        self.assertIn("Materialization pending", _read(os.path.join(_FRONTEND_JS, "components", "works-pdf.js")))
        self.assertIn("Offline · editable", _read(os.path.join(_FRONTEND_JS, "components", "works-pdf.js")))
        self.assertIn("Offline · read-only", _read(os.path.join(_FRONTEND_JS, "components", "works-pdf.js")))

    def test_easymde_toolbar_icons_use_lucide_not_font_awesome(self):
        works = _read(os.path.join(_FRONTEND_JS, "components", "works.js"))
        self.assertIn("autoDownloadFontAwesome: false", works)
        self.assertIn("prksPaintEasyMDEToolbarIcons", works)
        self.assertIn("PRKS_EASYMDE_TOOLBAR_ICONS", works)
        self.assertIn("preview: 'eye'", works)
        self.assertIn("fullscreen: 'maximize-2'", works)
        self.assertIn("'prks-insert-concept': 'lightbulb'", works)
        self.assertIn("'prks-insert-argument': 'message-square'", works)
        self.assertIn("'prks-notes-help': 'circle-help'", works)
        index = _read(_INDEX).lower()
        self.assertNotIn("font-awesome", index)
        self.assertNotIn("fontawesome", index)

    def test_section_13_is_third_party_integrations(self):
        css = _read(_CSS)
        start = css.find("/* 13 Third-party integrations */")
        end = css.find("/* 14 Responsive / container rules */")
        self.assertGreater(start, 0)
        self.assertGreater(end, start)
        block = css[start:end]
        self.assertIn("EasyMDE", block)
        self.assertIn("CodeMirror", block)
        for banned in (
            ".right-panel-stack",
            ".right-panel-work-actions",
            ".route-sidebar__",
            ".delete-work-btn",
            ".work-tags-list",
            ".tag-add-shell {",
            "#right-panel",
        ):
            self.assertNotIn(banned, block, banned)

    def test_section_15_is_last_stylesheet_section(self):
        css = _read(_CSS)
        start = css.find("/* 15 Reduced motion */")
        self.assertGreater(start, 0)
        rest = css[start:]
        self.assertEqual(rest.count("/* "), 1)
        self.assertNotIn(".prks-research-picker", rest)
        self.assertNotIn(".research-graph", rest)
        trailing = rest[rest.rfind("}") + 1 :].strip()
        self.assertEqual(trailing, "")

    def test_border_longhand_is_never_written_before_a_border_shorthand(self):
        """A PRKS stylesheet convention, not a law of CSS.

        `border: …` always resets `border-color`/`border-width`/`border-style`,
        so writing a longhand first and the shorthand after makes the longhand
        dead code. `.doc-type-badge` set `border-color: var(--doc-type-border)`
        and then `border: 2px solid transparent`, and the per-type colour
        silently stopped reaching the badge.

        A deliberate reset — `border-color: red; border: 0` — has the same shape
        and is not a bug, which is why this is a house rule rather than a
        universal invariant: in one stylesheet there is no reason to set a
        longhand you intend to discard three lines later, so write the shorthand
        first (`border: 0`) and any longhand after it. Relax this rule here if
        that ever stops being true.

        Restricted to the border family on purpose. `background-color` before
        `background` looks like the same mistake but is a real idiom: a browser
        that cannot parse the shorthand's value drops that declaration under
        normal CSS error handling and the longhand stands. `border` has no such
        fallback — with `var()` an unresolvable value is invalid at
        computed-value time, which unsets the property rather than dropping the
        declaration."""
        self.assertEqual(self._border_ordering_offenders(_read(_CSS)), [])

    def test_border_ordering_check_sees_a_reset_after_an_earlier_shorthand(self):
        """The check compares every declaration, not just the first of each.

        `border: 0; border-color: red; border: 2px solid transparent` resets the
        longhand exactly like the badge did, but a first-match-only comparison
        sees `border:` before `border-color:` and reports nothing."""
        offenders = self._border_ordering_offenders(
            ".x {\n  border: 0;\n  border-color: red;\n  border: 2px solid transparent;\n}"
        )
        self.assertEqual(len(offenders), 1, offenders)
        self.assertIn("border-color", offenders[0])
        # And the genuinely correct order stays accepted.
        self.assertEqual(
            self._border_ordering_offenders(".y {\n  border: 0;\n  border-color: red;\n}"),
            [],
        )

    @staticmethod
    def _border_ordering_offenders(css: str) -> list[str]:
        offenders = []
        for match in re.finditer(r"\{([^{}]*)\}", css):
            block = match.group(1)
            shorthand_at = [
                m.start() for m in re.finditer(r"(?<![-\w])border\s*:", block)
            ]
            if not shorthand_at:
                continue
            last_shorthand = shorthand_at[-1]
            for longhand in ("border-color", "border-width", "border-style"):
                # Any longhand before the LAST shorthand is reset by it; taking
                # the first occurrence of each would miss a later reset.
                first_longhand = next(
                    (m.start() for m in re.finditer(r"(?<![-\w])%s\s*:" % longhand, block)),
                    None,
                )
                if first_longhand is not None and last_shorthand > first_longhand:
                    line = css[: match.start()].count("\n") + 1
                    offenders.append(
                        "line %d: %s written before %s, which resets it"
                        % (line, longhand, "border")
                    )
        return offenders

    def test_doc_type_badge_border_carries_the_per_type_token(self):
        css = _read(_CSS)
        match = re.search(r"\n\.doc-type-badge\s*\{([^{}]*)\}", css)
        self.assertIsNotNone(match, "the doc-type badge base rule")
        block = match.group(1)
        self.assertIn("var(--doc-type-color", block)
        # The token has to be on the `border` declaration itself. Finding it
        # anywhere in the rule is what the regression looked like: the value was
        # present, on a `border-color` a later shorthand had already discarded.
        border_decl = re.search(r"(?<![-\w])border\s*:([^;]*);", block)
        self.assertIsNotNone(border_decl, block)
        self.assertIn("var(--doc-type-border", border_decl.group(1))

    def test_every_visible_input_has_an_accessible_name(self):
        """Accessibility is part of the design system, and the gallery is the
        production-class reference: an unlabelled text field is a defect in
        both. `type=hidden` carries no accessible name by definition."""
        for path in (_INDEX, _GALLERY):
            html = _read(path)
            # A wrapping <label> names its control on its own; what is left has
            # to say so itself.
            unwrapped = re.sub(r"<label\b.*?</label>", "", html, flags=re.S)
            for tag in re.findall(r"<input\b[^>]*>", unwrapped):
                if re.search(r'type\s*=\s*"hidden"', tag):
                    continue
                if "aria-label=" in tag or "aria-labelledby=" in tag:
                    continue
                identifier = re.search(r'id\s*=\s*"([^"]+)"', tag)
                self.assertTrue(
                    identifier and ('for="%s"' % identifier.group(1)) in html,
                    "%s has no accessible name: %s" % (os.path.basename(path), tag),
                )


if __name__ == "__main__":
    unittest.main()
