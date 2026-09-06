"""Regression contracts for the shared busy-button helper (Usability Polish 10)."""
import json
import os
import subprocess
import unittest


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UI = os.path.join(_ROOT, "frontend", "js", "ui.js")
_APP = os.path.join(_ROOT, "frontend", "js", "app.js")
_PDF = os.path.join(_ROOT, "frontend", "js", "components", "works-pdf.js")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _extract(src, start_marker, end_marker):
    chunk = src.split(start_marker, 1)[1]
    return start_marker + chunk.split(end_marker, 1)[0]


def _run_busy_case(js_body):
    ui = _read(_UI)
    source = _extract(
        ui,
        "const _prksButtonBusySnapshots = new WeakMap();",
        "const PRKS_BUTTON_LABEL_FLASH_MS = 1500;",
    )
    script = r"""
const vm = require('vm');
const source = %s;
const context = {};
vm.createContext(context);
vm.runInContext(source + '; this.prksSetButtonBusy = prksSetButtonBusy;', context);
function makeButton(initialHtml) {
    const state = { html: initialHtml };
    return {
        disabled: false,
        _attrs: {},
        setAttribute(name, val) { this._attrs[name] = val; },
        getAttribute(name) { return this._attrs[name]; },
        removeAttribute(name) { delete this._attrs[name]; },
        get innerHTML() { return state.html; },
        set innerHTML(v) { state.html = v; },
        // Plain-text buttons: textContent and innerHTML reflect the same
        // underlying string, matching real DOM behavior for tag-free content.
        get textContent() { return state.html; },
        set textContent(v) { state.html = v; },
    };
}
context.makeButton = makeButton;
(() => {
%s
})();
""" % (json.dumps(source), js_body)
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)


class ButtonBusyHelperTests(unittest.TestCase):
    def test_idle_busy_idle_triad(self):
        _run_busy_case(
            r"""
            const btn = makeButton('Save');
            context.prksSetButtonBusy(btn, true, { busyLabel: 'Saving…' });
            if (btn.disabled !== true) throw new Error('expected disabled while busy');
            if (btn.getAttribute('aria-busy') !== 'true') throw new Error('expected aria-busy=true');
            if (btn.innerHTML !== 'Saving…') throw new Error('expected busy label, got ' + btn.innerHTML);
            context.prksSetButtonBusy(btn, false);
            if (btn.disabled !== false) throw new Error('expected re-enabled after busy(false)');
            // No aria-busy attribute existed before busy(true) was ever called,
            // so exact-state restoration means the attribute is absent again,
            // not set to the literal string 'false'.
            if (btn.getAttribute('aria-busy') !== undefined) throw new Error('expected aria-busy attribute removed, got ' + btn.getAttribute('aria-busy'));
            if (btn.innerHTML !== 'Save') throw new Error('expected idle label restored, got ' + btn.innerHTML);
            """
        )

    def test_icon_containing_button_is_preserved_exactly(self):
        _run_busy_case(
            r"""
            const original = '<i data-lucide="save" class="prks-icon"></i> Save';
            const btn = makeButton(original);
            context.prksSetButtonBusy(btn, true, { busyLabel: 'Saving…' });
            if (btn.innerHTML === original) throw new Error('busy label did not replace icon markup');
            context.prksSetButtonBusy(btn, false);
            if (btn.innerHTML !== original) throw new Error('icon+label markup not restored exactly, got ' + btn.innerHTML);
            """
        )

    def test_repeat_busy_calls_do_not_corrupt_restore_value(self):
        _run_busy_case(
            r"""
            const btn = makeButton('Add to group');
            context.prksSetButtonBusy(btn, true, { busyLabel: 'Adding…' });
            // A second busy(true) with a different label must not overwrite the
            // snapshot taken on the *first* busy(true) call.
            context.prksSetButtonBusy(btn, true, { busyLabel: 'Still adding…' });
            if (btn.innerHTML !== 'Still adding…') throw new Error('second busy label did not apply');
            context.prksSetButtonBusy(btn, false);
            if (btn.innerHTML !== 'Add to group') throw new Error('repeat busy(true) corrupted the idle restore value, got ' + btn.innerHTML);
            """
        )

    def test_busy_without_label_preserves_icon_only_button_untouched(self):
        _run_busy_case(
            r"""
            const btn = makeButton('×');
            context.prksSetButtonBusy(btn, true);
            if (btn.innerHTML !== '×') throw new Error('busy without busyLabel must not alter content');
            if (btn.disabled !== true) throw new Error('expected disabled while busy');
            context.prksSetButtonBusy(btn, false);
            if (btn.innerHTML !== '×') throw new Error('idle content should be unchanged');
            if (btn.disabled !== false) throw new Error('expected re-enabled after busy(false)');
            """
        )

    def test_busy_false_without_prior_busy_true_is_a_safe_noop(self):
        _run_busy_case(
            r"""
            const btn = makeButton('Save');
            context.prksSetButtonBusy(btn, false);
            if (btn.disabled !== false) throw new Error('expected disabled=false');
            if (btn.innerHTML !== 'Save') throw new Error('content should be untouched');
            """
        )

    def test_busy_false_cannot_enable_a_control_it_did_not_disable(self):
        # A different subsystem disabled this button (e.g. the action is
        # unavailable for another reason). The busy helper never created that
        # busy state, so busy(false) must be a true no-op: it must not enable
        # the control or touch its contents.
        _run_busy_case(
            r"""
            const btn = makeButton('Save');
            btn.disabled = true;
            context.prksSetButtonBusy(btn, false);
            if (btn.disabled !== true) throw new Error('busy(false) enabled a control it did not disable');
            if (btn.innerHTML !== 'Save') throw new Error('busy(false) altered content it did not own');
            if (btn.getAttribute('aria-busy') !== undefined) throw new Error('busy(false) set aria-busy it did not own');
            """
        )

    def test_aria_busy_absent_before_is_absent_after(self):
        _run_busy_case(
            r"""
            const btn = makeButton('Save');
            if (btn.getAttribute('aria-busy') !== undefined) throw new Error('test setup: expected no initial aria-busy');
            context.prksSetButtonBusy(btn, true, { busyLabel: 'Saving…' });
            if (btn.getAttribute('aria-busy') !== 'true') throw new Error('expected aria-busy=true while busy');
            context.prksSetButtonBusy(btn, false);
            if (btn.getAttribute('aria-busy') !== undefined) throw new Error('expected aria-busy attribute removed, got ' + btn.getAttribute('aria-busy'));
            """
        )

    def test_aria_busy_false_before_is_restored_exactly(self):
        _run_busy_case(
            r"""
            const btn = makeButton('Save');
            btn.setAttribute('aria-busy', 'false');
            context.prksSetButtonBusy(btn, true, { busyLabel: 'Saving…' });
            if (btn.getAttribute('aria-busy') !== 'true') throw new Error('expected aria-busy=true while busy');
            context.prksSetButtonBusy(btn, false);
            if (btn.getAttribute('aria-busy') !== 'false') throw new Error('expected aria-busy=\"false\" restored exactly, got ' + btn.getAttribute('aria-busy'));
            """
        )


class AnnotationConfirmStructureTests(unittest.TestCase):
    """Guards the Polish 10 native-dialog boundary: annotation deletion now
    goes through the in-app modal, but the synchronous pending-annotation-sync
    route-leave guard must keep using window.confirm (it cannot safely become
    async without redesigning navigation -- see AGENTS.md)."""

    def test_shared_helpers_exist_in_ui_js(self):
        ui = _read(_UI)
        self.assertIn("function prksSetButtonBusy(", ui)
        self.assertIn("function prksFlashButtonLabel(", ui)
        self.assertIn("function prksConfirmDeletePdfAnnotation()", ui)
        self.assertIn("window.prksSetButtonBusy = prksSetButtonBusy;", ui)
        self.assertIn("window.prksFlashButtonLabel = prksFlashButtonLabel;", ui)
        self.assertIn("window.prksConfirmDeletePdfAnnotation = prksConfirmDeletePdfAnnotation;", ui)

    def test_annotation_delete_entry_points_use_shared_confirm_not_native(self):
        # Both entry points must call the shared prksConfirmDeletePdfAnnotation
        # as their primary path. A native window.confirm may still appear as a
        # defensive fallback for when the helper itself is unavailable (see
        # prksConfirmDeletePdfAnnotation's own body) -- that's intentional, not
        # a second independent native-dialog call site.
        pdf = _read(_PDF)
        editor_delete = _extract(
            pdf,
            "window.deletePdfAnnotationFromEditor = async function () {",
            "\n};",
        )
        self.assertIn("await prksConfirmDeletePdfAnnotation()", editor_delete)

        list_delete = _extract(
            pdf,
            "if (e.target && e.target.closest && e.target.closest('.annotation-row__delete')) {",
            "if (e.target && e.target.closest && e.target.closest('.annotation-row__copy-link')) {",
        )
        self.assertIn("await prksConfirmDeletePdfAnnotation()", list_delete)

    def test_pending_sync_leave_guard_still_uses_native_confirm(self):
        app = _read(_APP)
        # Both call sites of the synchronous pending-annotation-sync guard must
        # remain window.confirm -- this is an intentional exception (see
        # AGENTS.md / DESIGN.md), not an oversight to "fix" in a later pass.
        self.assertEqual(
            app.count("PDF annotation sync still running. Leave page before all changes save to server?"),
            2,
        )
        guard = _extract(app, "function prksCanLeaveTabContext(ctx, nextHash) {", "\nfunction ")
        self.assertIn("window.confirm(", guard)

    def test_copy_link_uses_shared_flash_helper(self):
        pdf = _read(_PDF)
        copy_link = _extract(
            pdf,
            "if (e.target && e.target.closest && e.target.closest('.annotation-row__copy-link')) {",
            "if (e.target.closest('.annotation-row__jump')",
        )
        self.assertIn("prksFlashButtonLabel", copy_link)
        self.assertIn("errorLabel: 'Copy failed'", copy_link)


if __name__ == "__main__":
    unittest.main()
