"""Static contract for the shared modal lifecycle.

Confirm, alert, prompt, unsaved dismissal, and Escape live in the Vue modal
host. Classic ``ui.js`` keeps dirty baselines, feature-modal shells, and the
call-site bridges.
"""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
UI = ROOT / "frontend" / "js" / "ui.js"
HOST = ROOT / "frontend-app" / "src" / "modal" / "ModalHost.vue"
FOCUS = ROOT / "frontend-app" / "src" / "modal" / "focus.ts"
INDEX = ROOT / "frontend" / "index.html"


def _between(text, start, end):
    return text.split(start, 1)[1].split(end, 1)[0]


class ModalLifecycleContractTests(unittest.TestCase):
    def setUp(self):
        self.ui = UI.read_text(encoding="utf-8")
        self.host = HOST.read_text(encoding="utf-8")
        self.focus = FOCUS.read_text(encoding="utf-8")
        self.index = INDEX.read_text(encoding="utf-8")

    def test_vue_host_owns_escape_and_the_dialog_dom(self):
        self.assertIn("document.addEventListener('keydown', onKeydown, true)", self.host)
        self.assertNotIn("function prksOnModalLifecycleKeydown", self.ui)
        self.assertNotIn("prksBindModalConfirmOnce", self.ui)
        self.assertNotIn('id="prks-modal-confirm"', self.index)
        self.assertNotIn('id="prks-modal-unsaved-confirm"', self.index)
        self.assertIn('id="prks-modal-confirm"', self.host)
        self.assertIn('id="prks-modal-unsaved-confirm"', self.host)
        self.assertIn("id=\"modal-backdrop\"", self.index)
        escape = _between(self.host, "function handleEscape", "function onKeydown")
        drag_at = escape.index("prks-workspace-dragging")
        confirm_at = escape.index("session.value")
        unsaved_at = escape.index("unsavedOpen.value")
        inner_at = escape.index("prksDismissModalInnerEscapeLayer")
        standalone_at = escape.index("prksCloseStandalonePageModal")
        request_at = escape.index("requestModalClose")
        self.assertLess(drag_at, confirm_at)
        self.assertLess(confirm_at, unsaved_at)
        self.assertLess(unsaved_at, inner_at)
        self.assertLess(inner_at, standalone_at)
        self.assertLess(standalone_at, request_at)
        self.assertIn("dismissConfirm()", escape)
        self.assertIn("hideUnsavedConfirm()", escape)

    def test_classic_call_sites_delegate_without_painting(self):
        confirm = _between(self.ui, "function prksConfirmDialog(options = {})", "function prksAlertDialog")
        prompt = _between(self.ui, "function prksPromptTextDialog(options = {})", "async function prksEditRoleCreditOnWork")
        self.assertIn("host.confirm(options || {})", confirm)
        self.assertIn("host.prompt(options || {})", prompt)
        self.assertNotIn("getElementById('prks-modal-confirm-title')", self.ui)
        self.assertNotIn("innerHTML", confirm)
        close = _between(self.ui, "function requestModalClose(reason)", "function prksAutosizeTextarea")
        self.assertIn("prksOpenModalUnsavedConfirm", close)
        self.assertIn("prksModalHasUnsavedChanges(activeModalId)", close)
        self.assertIn("prksIsModalUnsavedConfirmOpen()", close)
        self.assertIn("return false", close)
        self.assertIn("window.prksModalHost", self.ui)
        unsaved = _between(
            self.ui,
            "function prksOpenModalUnsavedConfirm(onDiscard)",
            "function prksIsModalConfirmOpen()",
        )
        self.assertNotIn("onDiscard()", unsaved)
        self.assertIn("openUnsavedConfirm", unsaved)

    def test_destructive_confirm_focuses_the_safe_control(self):
        self.assertIn("prks-modal-confirm-cancel", self.host)
        self.assertIn("prks-modal-unsaved-confirm-cancel", self.host)
        self.assertIn("alertOnly.value || !danger.value", self.host)

    def test_confirm_focus_return_stays_on_the_opening_drawer(self):
        self.assertIn("function replacementAnnotationDelete", self.focus)
        self.assertNotIn(
            'document.querySelector(\'[data-prks-role="pdf-annotation-drawer"] .annotation-row__delete\')',
            self.focus,
        )
        self.assertIn("replacementAnnotationDelete(opener, ownerTabId)", self.focus)
        self.assertIn("focusStaysOnFocusedTile", self.focus)

    def test_close_still_confirms_dirty_forms(self):
        schedule = _between(
            self.ui,
            "function prksScheduleModalBaselineCapture(modalId)",
            "function prksGetActiveModalId()",
        )
        self.assertNotIn("setTimeout", schedule)
        self.assertIn("prksSerializeModalFormState(modalId) === baseline", schedule)

    def test_person_create_reset_does_not_touch_profile_edit(self):
        open_modal = _between(self.ui, "function openModal(id)", "const PRKS_LS_HINTS")
        person_branch = open_modal.split("id === 'person-modal'", 1)[1].split("} else if", 1)[0]
        self.assertIn("resetPersonCreateForm()", person_branch)
        self.assertIn("prksOpenGeneration", open_modal)
        self.assertEqual(open_modal.count("if (!isCurrentOpening()) return;"), 2)
        reset = _between(self.ui, "function resetPersonCreateForm()", "function syncPersonAliasesFromNames()")
        self.assertNotIn("pd-first-name", reset)
        self.assertNotIn("pd-", reset)
        self.assertIn("el.value = ''", reset)
        self.assertIn("removeAttribute('aria-invalid')", reset)

    def test_focus_restore_does_not_steal_the_modal_opener(self):
        close = _between(self.ui, "function closeModals()", "window.prksDismissModalInnerEscapeLayer")
        self.assertIn("prksHideModalUnsavedConfirm({ restoreFocus: false })", close)
        self.assertIn("__prksModalFocusRestore", close)
        open_modal = _between(self.ui, "function openModal(id)", "const PRKS_LS_HINTS")
        self.assertIn("prksHideModalUnsavedConfirm({ restoreFocus: false })", open_modal)
        self.assertIn("__prksModalFocusRestore", open_modal)


if __name__ == "__main__":
    unittest.main()
