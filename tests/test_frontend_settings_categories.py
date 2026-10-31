"""Structural regressions for Settings category organization and the Vue surface."""
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
_AGENTS = os.path.join(_PROJECT_DIR, "frontend", "AGENTS.md")
_SETTINGS = os.path.join(_PROJECT_DIR, "frontend-app", "src", "features", "settings")
_NAV = os.path.join(_SETTINGS, "navigation.ts")
_CATEGORIES = os.path.join(_SETTINGS, "categories.ts")
_SURFACE = os.path.join(_SETTINGS, "SettingsSurface.vue")
_PANEL = os.path.join(_SETTINGS, "SettingsPanel.vue")

_CATEGORY_IDS = ["general", "reading", "export", "backup", "maintenance", "diagnostics"]


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _vue_settings() -> str:
    parts = []
    for name in sorted(os.listdir(_SETTINGS)):
        if name.endswith((".vue", ".ts")):
            parts.append(_read(os.path.join(_SETTINGS, name)))
    return "\n".join(parts)


def _panel_source(name: str) -> str:
    return _read(os.path.join(_SETTINGS, name))


class SettingsCategoryStructureTests(unittest.TestCase):
    def test_shell_is_a_host_and_vue_owns_the_categories(self):
        html = _read(_INDEX)
        shell = html.split('id="settings-modal"', 1)[1].split('id="prks-restore-confirm-modal"', 1)[0]
        self.assertIn('id="prks-settings-root"', shell)
        self.assertIn("requestModalClose", shell)
        self.assertNotIn('id="prks-settings-tab-general"', shell)
        self.assertNotIn('id="annotation-author-input"', shell)
        vue = _vue_settings()
        categories = _read(_CATEGORIES)
        for cat in _CATEGORY_IDS:
            self.assertIn(f"id: '{cat}'", categories)
            self.assertIn(f"prks-settings-tab-${{category.id}}", _read(_SURFACE).replace("`", ""))
            self.assertIn(f"prks-settings-panel-${{category}}", _read(_PANEL).replace("`", ""))
        self.assertIn('role="tablist"', vue)
        self.assertIn('role="tab"', vue)
        self.assertIn('role="tabpanel"', vue)
        self.assertIn("data-prks-settings-category", vue)
        self.assertIn("aria-controls", vue)
        self.assertIn("aria-labelledby", vue)

    def test_category_labels(self):
        categories = _read(_CATEGORIES)
        self.assertIn("label: 'General'", categories)
        self.assertIn("label: 'Reading & layout'", categories)
        self.assertIn("label: 'Export'", categories)
        self.assertIn("label: 'Backup'", categories)
        self.assertIn("label: 'Maintenance'", categories)
        self.assertIn("label: 'Diagnostics'", categories)

    def test_inactive_panels_stay_hidden_and_inert(self):
        panel = _read(_PANEL)
        self.assertIn(":hidden=", panel)
        self.assertIn(":inert=", panel)
        nav = _read(_NAV)
        self.assertIn("settingsActiveCategory = ref<SettingsCategoryId>('general')", nav)
        self.assertNotIn(".remove(", panel)
        self.assertNotIn("v-if", panel)

    def test_no_url_hash_routes_for_settings_categories(self):
        html = _read(_INDEX)
        self.assertNotIn("#/settings", html)
        for path in (_UI, _APP, _SETTINGS):
            if os.path.isdir(path):
                text = _vue_settings()
            else:
                text = _read(path)
            self.assertNotIn("#/settings", text)

    def test_existing_control_ids_preserved(self):
        vue = _vue_settings()
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
            "prks-settings-perf-root",
            "prks-setting-ui-hints",
            "prks-setting-force-mobile",
            "prks-setting-mobile-work-notes-right",
            "prks-setting-pdf-remember-page",
        ]
        for the_id in preserved_ids:
            self.assertIn(f'id="{the_id}"', vue, msg=f"missing id={the_id}")

    def test_general_category_is_calm_first_impression(self):
        general = _panel_source("SettingsGeneral.vue")
        for noisy in ("Backup", "Restore", "Maintenance", "Diagnostics", "Linearize", "Rebuild"):
            self.assertNotIn(noisy, general)
        self.assertIn("Appearance", general)
        self.assertIn("Annotation author", general)
        self.assertIn("Show help hints", general)

    def test_export_has_compact_field_summary_placeholder(self):
        self.assertIn('id="prks-bibtex-export-summary"', _panel_source("SettingsExport.vue"))

    def test_backup_separates_create_and_restore_headings(self):
        backup = _panel_source("SettingsBackup.vue")
        self.assertIn("Create backup", backup)
        self.assertIn("Restore backup", backup)
        self.assertLess(backup.index("Create backup"), backup.index("Restore backup"))

    def test_maintenance_has_non_alarming_intro(self):
        maintenance = _panel_source("SettingsMaintenance.vue")
        self.assertIn("normally unnecessary", maintenance)
        self.assertNotIn("danger", maintenance.lower())
        self.assertNotIn("delete", maintenance.lower())

    def test_no_new_maintenance_tools_added(self):
        maintenance = _panel_source("SettingsMaintenance.vue")
        for forbidden in ("vacuum", "purge", "cache clear", "reindex-all", "migration"):
            self.assertNotIn(forbidden, maintenance.lower())

    def test_scope_badges_present(self):
        vue = _vue_settings()
        self.assertIn("Library-wide", vue)
        self.assertIn("This device", vue)
        backup = _panel_source("SettingsBackup.vue")
        self.assertNotIn("prks-settings-scope", backup)

    def test_reading_layout_clarifies_conditional_setting(self):
        reading = _panel_source("SettingsReading.vue")
        self.assertIn("Research notes beside PDF when narrow", reading)
        self.assertIn("notes expand as a drawer by default", reading)
        self.assertIn("side-by-side sidecar", reading)
        self.assertIn("narrow stacked", reading)
        self.assertIn("Split-view tiles always use a drawer", reading)
        self.assertNotIn("Research notes beside PDF on mobile", reading)
        self.assertNotIn("narrow stacked containers and tiles", reading)

    def test_modal_width_scoped_to_settings_only(self):
        css = _read(_CSS)
        self.assertIn(".modal--settings", css)
        settings_block = css.split(".modal--settings {", 1)[1].split("}", 1)[0]
        self.assertIn("94vw", settings_block)
        m = re.search(r"(?m)^\.modal \{([^}]*)\}", css)
        self.assertIsNotNone(m, "base .modal rule not found")
        self.assertIn("500px", m.group(1))

    def test_settings_close_is_not_an_unsaved_discard(self):
        ui = _read(_UI)
        block = ui.split("const PRKS_MODAL_UNSAVED_CONFIRM_IDS = new Set([", 1)[1].split("]);", 1)[0]
        self.assertNotIn("settings-modal", block)


class SettingsCategoryJsTests(unittest.TestCase):
    def test_shared_activation_helper_exists(self):
        nav = _read(_NAV)
        self.assertIn("export function activateSettingsCategory(", nav)
        self.assertIn("target.prksActivateSettingsCategory = activateSettingsCategory", nav)
        self.assertIn("export function openSettingsToLastCategory(", nav)
        self.assertIn("export function syncSettingsNavOrientation(", nav)

    def test_activation_keeps_panels_mounted(self):
        panel = _read(_PANEL)
        self.assertIn(":hidden=", panel)
        self.assertIn(":inert=", panel)
        self.assertNotIn("v-if", panel)

    def test_orientation_sync_helper_exists_and_shares_css_breakpoint(self):
        nav = _read(_NAV)
        categories = _read(_CATEGORIES)
        css = _read(_CSS)
        self.assertIn("export function syncSettingsNavOrientation(", nav)
        self.assertIn("target.prksSyncSettingsNavOrientation = syncSettingsNavOrientation", nav)
        self.assertIn("export const SETTINGS_NARROW_MEDIA_QUERY = '(max-width: 640px)'", categories)
        self.assertIn("@media (max-width: 640px)", css)

    def test_open_settings_synchronizes_orientation_before_category_activation(self):
        nav = _read(_NAV)
        fn = nav.split("export function openSettingsToLastCategory()", 1)[1].split(
            "export function registerSettingsNavigationBridge", 1
        )[0]
        self.assertIn("syncSettingsNavOrientation()", fn)
        self.assertIn("activateSettingsCategory(settingsActiveCategory.value)", fn)
        self.assertLess(
            fn.index("syncSettingsNavOrientation()"),
            fn.index("activateSettingsCategory(settingsActiveCategory.value)"),
        )

    def test_orientation_sync_uses_matchmedia_change_not_raw_resize(self):
        nav = _read(_NAV)
        fn = nav.split("export function bindSettingsNavOrientation()", 1)[1].split(
            "export function activateSettingsCategory(", 1
        )[0]
        self.assertIn("syncSettingsNavOrientation()", fn)
        self.assertIn("window.matchMedia(SETTINGS_NARROW_MEDIA_QUERY)", fn)
        self.assertIn("addEventListener('change', onChange)", fn)
        self.assertNotIn("window.addEventListener('resize'", fn)

    def test_keyboard_navigation_supports_arrows_home_end(self):
        surface = _read(_SURFACE)
        self.assertIn("ArrowDown", surface)
        self.assertIn("ArrowUp", surface)
        self.assertIn("Home", surface)
        self.assertIn("End", surface)
        self.assertIn("focusTab: true", surface)

    def test_diagnostics_not_loaded_on_settings_open(self):
        ui = _read(_UI)
        open_modal_fn = ui.split("function openModal(id)", 1)[1].split("\nfunction ", 1)[0]
        self.assertNotIn("prksLoadPerformanceDiagnostics()", open_modal_fn)
        self.assertIn("prksOpenSettingsToLastCategory", open_modal_fn)
        self.assertNotIn("prksLoadOfflineCacheStatus", open_modal_fn)

    def test_diagnostics_lazy_load_only_on_first_activation(self):
        nav = _read(_NAV)
        fn = nav.split("export function activateSettingsCategory(", 1)[1].split(
            "export function openSettingsToLastCategory(", 1
        )[0]
        self.assertIn("diagnosticsActivated", fn)
        self.assertIn("activatePerformanceDiagnostics()", fn)
        self.assertIn("__prksPerformanceDiagnosticsRequested", fn)
        self.assertIn("prksLoadOfflineCacheStatus", fn)
        self.assertNotIn("prksLoadPerformanceDiagnostics", _read(_APP))
        vue = _read(
            os.path.join(
                _PROJECT_DIR,
                "frontend-app",
                "src",
                "features",
                "performance-diagnostics",
                "PerformanceDiagnostics.vue",
            )
        )
        for control_id in (
            "prks-perf-refresh-btn",
            "prks-perf-reset-btn",
            "prks-perf-copy-btn",
        ):
            self.assertIn(f'id="{control_id}"', vue)

    def test_settings_category_state_not_persisted_to_localstorage_or_api(self):
        nav = _read(_NAV)
        self.assertNotIn("localStorage", nav)
        self.assertNotIn("prksPatchAppSettings", nav)
        self.assertNotIn("prksGetFocusedTabContext", nav)

    def test_bibtex_export_summary_derives_from_toggle_flags(self):
        summary = _read(os.path.join(_SETTINGS, "bibtex-summary.ts"))
        export = _panel_source("SettingsExport.vue")
        self.assertIn("export function bibtexExportSummary(", summary)
        self.assertIn("fields included", summary)
        self.assertIn("bibtexExportSummary(fields.value.map((field) => field.on))", export)
        self.assertIn(':on="field.on"', export)
        switch = _panel_source("SettingsSwitch.vue")
        self.assertIn(":aria-checked=\"on ? 'true' : 'false'\"", switch)


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
