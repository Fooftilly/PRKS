"""Static contract for the shared modal lifecycle in frontend/js/ui.js."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
UI = ROOT / "frontend" / "js" / "ui.js"


def _between(text, start, end):
    return text.split(start, 1)[1].split(end, 1)[0]


class ModalLifecycleContractTests(unittest.TestCase):
    def setUp(self):
        self.ui = UI.read_text(encoding="utf-8")

    def test_escape_is_one_capture_listener(self):
        init = _between(self.ui, "function initModalCloseUi()", "function prksSetOverlayBackdropVisible")
        self.assertIn("prksOnModalLifecycleKeydown", init)
        self.assertIn("addEventListener('keydown', prksOnModalLifecycleKeydown, true)", init)
        keydown = _between(
            self.ui,
            "function prksOnModalLifecycleKeydown(e)",
            "function prksDismissModalInnerEscapeLayer()",
        )
        drag_at = keydown.index("prks-workspace-dragging")
        stop_at = keydown.index("prksStopModalEscape")
        self.assertLess(drag_at, stop_at)
        self.assertIn("prksIsModalConfirmOpen()", keydown)
        self.assertIn("prksIsModalUnsavedConfirmOpen()", keydown)
        self.assertIn("requestModalClose('escape')", keydown)
        self.assertIn("prksFinishModalConfirm(prksModalConfirmAlertOnly)", keydown)
        self.assertIn("prksHideModalUnsavedConfirm()", keydown)
        self.assertIn("prksCloseStandalonePageModal(activeModal)", keydown)
        self.assertIn("prksCloseTagsAliasModal", self.ui)
        self.assertIn("prksCloseTagsMergeModal", self.ui)
        self.assertIn("prksClosePublishersAliasModal", self.ui)
        schedule = _between(
            self.ui,
            "function prksScheduleModalBaselineCapture(modalId)",
            "function prksGetActiveModalId()",
        )
        self.assertNotIn("setTimeout", schedule)
        self.assertIn("prksSerializeModalFormState(modalId) === baseline", schedule)
        open_modal = _between(self.ui, "function openModal(id)", "const PRKS_LS_HINTS")
        self.assertIn("prksOpenGeneration", open_modal)
        self.assertEqual(open_modal.count("if (!isCurrentOpening()) return;"), 2)

    def test_confirm_overlays_do_not_bind_their_own_escape(self):
        confirm = _between(
            self.ui,
            "function prksBindModalConfirmOnce()",
            "function prksBindModalUnsavedConfirmOnce()",
        )
        unsaved = _between(
            self.ui,
            "function prksBindModalUnsavedConfirmOnce()",
            "function prksStopModalEscape(e)",
        )
        for body in (confirm, unsaved):
            self.assertNotIn("keydown", body)
            self.assertIn("addEventListener('click'", body)

    def test_close_still_confirms_dirty_forms(self):
        close = _between(self.ui, "function requestModalClose(reason)", "function prksAutosizeTextarea")
        self.assertIn("prksOpenModalUnsavedConfirm", close)
        self.assertIn("prksModalHasUnsavedChanges(activeModalId)", close)
        self.assertIn("prksIsModalUnsavedConfirmOpen()", close)
        self.assertIn("return false", close)

    def test_person_create_reset_does_not_touch_profile_edit(self):
        open_modal = _between(self.ui, "function openModal(id)", "const PRKS_LS_HINTS")
        person_branch = open_modal.split("id === 'person-modal'", 1)[1].split("} else if", 1)[0]
        self.assertIn("resetPersonCreateForm()", person_branch)
        reset = _between(self.ui, "function resetPersonCreateForm()", "function syncPersonAliasesFromNames()")
        self.assertIn("person-fname", self.ui.split("const PRKS_PERSON_CREATE_FIELD_IDS", 1)[1].split("function resetPersonAliasAutoSyncState", 1)[0])
        self.assertNotIn("pd-first-name", reset)
        self.assertNotIn("pd-", reset)
        self.assertIn("el.value = ''", reset)
        self.assertIn("removeAttribute('aria-invalid')", reset)

    def test_focus_restore_does_not_steal_the_modal_opener(self):
        close = _between(self.ui, "function closeModals()", "window.requestModalClose")
        self.assertIn("prksHideModalUnsavedConfirm({ restoreFocus: false })", close)
        self.assertIn("__prksModalFocusRestore", close)
        open_modal = _between(self.ui, "function openModal(id)", "const PRKS_LS_HINTS")
        self.assertIn("prksHideModalUnsavedConfirm({ restoreFocus: false })", open_modal)
        self.assertIn("__prksModalFocusRestore", open_modal)
