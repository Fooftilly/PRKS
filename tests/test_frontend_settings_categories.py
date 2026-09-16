"""Structural regressions for Usability Polish 3 — Settings category reorganization."""
from __future__ import annotations

import os
import re
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_INDEX = os.path.join(_FRONTEND, "index.html")
_UI = os.path.join(_FRONTEND, "js", "ui.js")
_APP = os.path.join(_FRONTEND, "js", "app.js")
_CSS = os.path.join(_FRONTEND, "css", "style.css")
_DESIGN = os.path.join(_PROJECT_DIR, "DESIGN.md")
_AGENTS = os.path.join(_PROJECT_DIR, "AGENTS.md")

_CATEGORIES = ["general", "reading", "export", "backup", "maintenance", "diagnostics"]


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _settings_modal_html() -> str:
    html = _read(_INDEX)
    chunk = html.split('id="settings-modal"', 1)[1]
    # Settings modal is closed by the next top-level modal div.
    return chunk.split('id="prks-restore-confirm-modal"', 1)[0]


class SettingsCategoryStructureTests(unittest.TestCase):
    def test_six_categories_present_as_tabs_and_panels(self):
        modal = _settings_modal_html()
        for cat in _CATEGORIES:
            self.assertIn(f'id="prks-settings-tab-{cat}"', modal)
            self.assertIn(f'id="prks-settings-panel-{cat}"', modal)
            self.assertIn(f'data-prks-settings-category="{cat}"', modal)

    def test_category_labels(self):
        modal = _settings_modal_html()
        self.assertIn(">General<", modal)
        self.assertIn("Reading &amp; layout", modal)
        self.assertIn(">Export<", modal)
        self.assertIn(">Backup<", modal)
        self.assertIn(">Maintenance<", modal)
        self.assertIn(">Diagnostics<", modal)

    def test_tablist_accessibility_markup(self):
        modal = _settings_modal_html()
        self.assertIn('role="tablist"', modal)
        # Static markup ships the desktop/default orientation; JS
        # (prksSyncSettingsNavOrientation) synchronizes it with the actual
        # responsive layout before interactive use — it is not always vertical.
        self.assertIn('aria-orientation="vertical"', modal)
        for cat in _CATEGORIES:
            self.assertIn(f'aria-controls="prks-settings-panel-{cat}"', modal)
            self.assertIn(f'role="tab"', modal)
        self.assertIn('role="tabpanel"', modal)
        for cat in _CATEGORIES:
            self.assertIn(f'aria-labelledby="prks-settings-tab-{cat}"', modal)

    def test_all_but_first_panel_hidden_and_inert_by_default(self):
        modal = _settings_modal_html()
        for cat in _CATEGORIES:
            panel_chunk = modal.split(f'id="prks-settings-panel-{cat}"', 1)[1][:400]
            self.assertIn("hidden", panel_chunk)
            self.assertIn("inert", panel_chunk)

    def test_no_url_hash_routes_for_settings_categories(self):
        html = _read(_INDEX)
        self.assertNotIn("#/settings", html)
        for f in (_UI, _APP):
            self.assertNotIn("#/settings", _read(f))

    def test_existing_control_ids_preserved(self):
        modal = _settings_modal_html()
        preserved_ids = [
            "annotation-author-input",
            "prks-bibtex-export-fields",
            "prks-bibtex-export-reset",
            "prks-backup-download-btn",
            "prks-backup-cancel-btn",
            "prks-backup-file-input",
            "prks-backup-choose-btn",
            "prks-backup-verify-btn",
            "prks-backup-restore-btn",
            "prks-reindex-pdf-text-btn",
            "prks-linearize-existing-pdfs-btn",
            "prks-perf-refresh-btn",
            "prks-perf-reset-btn",
            "prks-perf-copy-btn",
            "prks-setting-ui-hints",
            "prks-setting-force-mobile",
            "prks-setting-mobile-work-notes-right",
            "prks-setting-pdf-remember-page",
        ]
        for the_id in preserved_ids:
            self.assertIn(f'id="{the_id}"', modal, msg=f"missing id={the_id}")

    def test_general_category_is_calm_first_impression(self):
        modal = _settings_modal_html()
        general = modal.split('id="prks-settings-panel-general"', 1)[1].split(
            'id="prks-settings-panel-reading"', 1
        )[0]
        for noisy in ("Backup", "Restore", "Maintenance", "Diagnostics", "Linearize", "Rebuild"):
            self.assertNotIn(noisy, general)
        self.assertIn("Appearance", general)
        self.assertIn("Annotation author", general)
        self.assertIn("Show help hints", general)

    def test_export_has_compact_field_summary_placeholder(self):
        modal = _settings_modal_html()
        self.assertIn('id="prks-bibtex-export-summary"', modal)

    def test_backup_separates_create_and_restore_headings(self):
        modal = _settings_modal_html()
        backup = modal.split('id="prks-settings-panel-backup"', 1)[1].split(
            'id="prks-settings-panel-maintenance"', 1
        )[0]
        self.assertIn(">Create backup<", backup)
        self.assertIn(">Restore backup<", backup)
        self.assertLess(backup.index(">Create backup<"), backup.index(">Restore backup<"))

    def test_maintenance_has_non_alarming_intro(self):
        modal = _settings_modal_html()
        maintenance = modal.split('id="prks-settings-panel-maintenance"', 1)[1].split(
            'id="prks-settings-panel-diagnostics"', 1
        )[0]
        self.assertIn("normally unnecessary", maintenance)
        self.assertNotIn("danger", maintenance.lower())
        self.assertNotIn("delete", maintenance.lower())

    def test_no_new_maintenance_tools_added(self):
        modal = _settings_modal_html()
        maintenance = modal.split('id="prks-settings-panel-maintenance"', 1)[1].split(
            'id="prks-settings-panel-diagnostics"', 1
        )[0]
        for forbidden in ("vacuum", "purge", "cache clear", "reindex-all", "migration"):
            self.assertNotIn(forbidden, maintenance.lower())

    def test_scope_badges_present(self):
        modal = _settings_modal_html()
        self.assertIn("Library-wide", modal)
        self.assertIn("This device", modal)
        # Backup / Maintenance / Diagnostics are actions, not preferences: no scope badge there.
        backup = modal.split('id="prks-settings-panel-backup"', 1)[1].split(
            'id="prks-settings-panel-maintenance"', 1
        )[0]
        self.assertNotIn("prks-settings-scope", backup)

    def test_reading_layout_clarifies_conditional_setting(self):
        modal = _settings_modal_html()
        reading = modal.split('id="prks-settings-panel-reading"', 1)[1].split(
            'id="prks-settings-panel-export"', 1
        )[0]
        self.assertIn("notes expand as a drawer by default", reading)
        self.assertIn("side-by-side sidecar", reading)

    def test_modal_width_scoped_to_settings_only(self):
        css = _read(_CSS)
        self.assertIn(".modal--settings", css)
        settings_block = css.split(".modal--settings {", 1)[1].split("}", 1)[0]
        self.assertIn("94vw", settings_block)
        # Base .modal rule (bare selector, not a feature-specific `.foo.modal`)
        # must be untouched (still ~500px default dialog width).
        m = re.search(r"(?m)^\.modal \{([^}]*)\}", css)
        self.assertIsNotNone(m, "base .modal rule not found")
        self.assertIn("500px", m.group(1))


class SettingsCategoryJsTests(unittest.TestCase):
    def test_shared_activation_helper_exists(self):
        app = _read(_APP)
        self.assertIn("function prksActivateSettingsCategory(", app)
        self.assertIn("window.prksActivateSettingsCategory", app)
        self.assertIn("function initPrksSettingsCategoryNav(", app)

    def test_activation_helper_uses_hidden_and_inert_not_removal(self):
        app = _read(_APP)
        fn = app.split("function prksActivateSettingsCategory(", 1)[1].split(
            "\nwindow.prksActivateSettingsCategory", 1
        )[0]
        self.assertIn("panel.hidden = !active", fn)
        self.assertIn("setAttribute('inert'", fn)
        self.assertIn("removeAttribute('inert')", fn)
        self.assertIn("aria-selected", fn)

    def test_orientation_sync_helper_exists_and_shares_css_breakpoint(self):
        app = _read(_APP)
        self.assertIn("function prksSyncSettingsNavOrientation(", app)
        self.assertIn("window.prksSyncSettingsNavOrientation", app)
        css = _read(_CSS)
        self.assertIn("PRKS_SETTINGS_NARROW_MEDIA_QUERY = '(max-width: 640px)'", app)
        self.assertIn("@media (max-width: 640px)", css)

    def test_orientation_sync_uses_matchmedia_change_not_raw_resize(self):
        app = _read(_APP)
        fn_start = app.index("function initPrksSettingsCategoryNav(")
        fn_end = app.index("\nwindow.initPrksSettingsCategoryNav", fn_start)
        fn = app[fn_start:fn_end]
        self.assertIn("prksSyncSettingsNavOrientation()", fn)
        self.assertIn("matchMedia(PRKS_SETTINGS_NARROW_MEDIA_QUERY)", fn)
        self.assertIn("addEventListener('change'", fn)
        self.assertNotIn("window.addEventListener('resize'", fn)

    def test_keyboard_navigation_supports_arrows_home_end(self):
        app = _read(_APP)
        fn = app.split("function initPrksSettingsCategoryNav(", 1)[1].split(
            "\nwindow.initPrksSettingsCategoryNav", 1
        )[0]
        self.assertIn("ArrowDown", fn)
        self.assertIn("ArrowUp", fn)
        self.assertIn("Home", fn)
        self.assertIn("End", fn)

    def test_diagnostics_not_loaded_on_settings_open(self):
        ui = _read(_UI)
        # openModal() must no longer eagerly load diagnostics for settings-modal.
        open_modal_fn = ui.split("function openModal(id)", 1)[1].split(
            "\nfunction ", 1
        )[0]
        self.assertNotIn("prksLoadPerformanceDiagnostics()", open_modal_fn)
        self.assertIn("prksOpenSettingsToLastCategory", open_modal_fn)

    def test_diagnostics_lazy_load_only_on_first_activation(self):
        app = _read(_APP)
        fn = app.split("function prksActivateSettingsCategory(", 1)[1].split(
            "\nwindow.prksActivateSettingsCategory", 1
        )[0]
        self.assertIn("__prksSettingsDiagnosticsLoaded", fn)
        self.assertIn("prksLoadPerformanceDiagnostics()", fn)

    def test_settings_category_state_not_persisted_to_localstorage_or_api(self):
        app = _read(_APP)
        block = app.split("PRKS_SETTINGS_CATEGORIES", 1)[1].split(
            "function applyTheme(theme)", 1
        )[0]
        self.assertNotIn("localStorage", block)
        self.assertNotIn("prksPatchAppSettings", block)

    def test_bibtex_export_summary_derives_from_existing_toggle_state(self):
        app = _read(_APP)
        self.assertIn("function prksUpdateBibtexExportSummary(", app)
        fn = app.split("function prksUpdateBibtexExportSummary(", 1)[1].split(
            "\nasync function initBibtexExportFieldsSetting", 1
        )[0]
        self.assertIn("prks-bibtex-export-fields", fn)
        self.assertIn('aria-checked="true"', fn)


class SettingsDesignDocTests(unittest.TestCase):
    def test_design_doc_mentions_settings_contract(self):
        design = _read(_DESIGN)
        self.assertIn("Settings", design)
        low = design.lower()
        self.assertIn("category", low)
        self.assertIn("diagnostics", low)

    def test_agents_md_protects_category_state_invariant(self):
        agents = _read(_AGENTS)
        self.assertIn("Settings category navigation is presentation state", agents)
