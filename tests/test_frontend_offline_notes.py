"""Structural regressions for offline-mode behavior in Research Notes
(works.js): CodeMirror's own `readOnly` option is not a sufficient offline
guard, because PRKS's own toolbar-driven programmatic edit paths (Concept
picker, Argument picker, Argument/Stance creation) never consult it. Every
PRKS-owned programmatic edit boundary must go through one shared
`prksWorkNotesMutationAllowed(ctx)` predicate, and the mutating EasyMDE
toolbar buttons must be natively disabled while offline rather than relying
solely on that lower guard."""
import os
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_WORKS = os.path.join(_FRONTEND, "js", "components", "works.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendOfflineNotesGuardTests(unittest.TestCase):
    def test_file_exists(self):
        self.assertTrue(os.path.isfile(_WORKS))

    def test_mutation_allowed_predicate_exists_and_checks_connectivity_and_liveness(self):
        src = _read(_WORKS)
        self.assertIn("function prksWorkNotesMutationAllowed(ctx, cm)", src)
        start = src.index("function prksWorkNotesMutationAllowed(ctx, cm)")
        end = src.index("\n}\n", start)
        body = src[start:end]
        self.assertIn("isCurrent", body)
        self.assertIn("getResource('workNotes')", body)
        self.assertIn("prksOfflineRuntimeState() !== 'online'", body)
        self.assertIn("window.prksWorkNotesMutationAllowed = prksWorkNotesMutationAllowed;", src)

    def test_mutation_allowed_requires_exact_live_codemirror_instance(self):
        """A stale picker/autocomplete callback bound to a detached CodeMirror
        from a previous Work must never be allowed to mutate, even if the
        TabContext/resource otherwise look live -- e.g. the same TabContext
        has since navigated to a different Work whose
        workNotes.editor.codemirror is a different instance."""
        src = _read(_WORKS)
        start = src.index("function prksWorkNotesMutationAllowed(ctx, cm)")
        end = src.index("\n}\n", start)
        body = src[start:end]
        self.assertIn("if (cm && notes.editor.codemirror !== cm) return false;", body)

    def test_completion_pickers_are_guarded_and_editor_instance_aware(self):
        """prksWikiLinkCompletionPick / prksPdfAnnLinkCompletionPick /
        prksConceptLinkCompletionPick must all check
        prksWorkNotesMutationAllowed(prksHintOwnerCtx(cm), cm) before
        cm.replaceRange() -- CodeMirror's own readOnly option does not
        protect these programmatic completion callbacks."""
        src = _read(_WORKS)
        for fn_name in (
            "function prksWikiLinkCompletionPick(cm, data, completion)",
            "function prksPdfAnnLinkCompletionPick(cm, data, completion)",
            "function prksConceptLinkCompletionPick(cm, data, completion)",
        ):
            start = src.index(fn_name)
            end = src.index("\n}\n", start)
            body = src[start:end]
            self.assertIn("prksWorkNotesMutationAllowed(prksHintOwnerCtx(cm), cm)", body, fn_name)
            self.assertIn("prksOfflineGuardMutation", body, fn_name)
            guard_idx = body.index("prksWorkNotesMutationAllowed(")
            mutate_idx = body.index("replaceRange(")
            self.assertLess(guard_idx, mutate_idx, fn_name)

    def test_hard_beforechange_barrier_installed_and_removed(self):
        """A ctx-owned CodeMirror `beforeChange` handler must cancel every
        non-'setValue' change while PRKS is not online -- the one barrier no
        keyboard shortcut/toolbar command/EasyMDE internal command/
        autocomplete/stale picker can bypass -- and must be removed on
        editor destroy."""
        src = _read(_WORKS)
        init_start = src.index("function initEasyMDE(ctx, work)")
        init_end = src.index("function prksWorkNotesMarkEdit")
        init_body = src[init_start:init_end]
        self.assertIn("notesBeforeChangeHandler", init_body)
        self.assertIn("easyMDE.codemirror.on('beforeChange', notesBeforeChangeHandler);", init_body)
        self.assertIn("easyMDE.__notesBeforeChangeHandler = notesBeforeChangeHandler;", init_body)
        handler_start = init_body.index("const notesBeforeChangeHandler = function")
        handler_end = init_body.index("easyMDE.codemirror.on('beforeChange'", handler_start)
        handler_body = init_body[handler_start:handler_end]
        self.assertIn("changeObj.origin === 'setValue'", handler_body)
        self.assertIn("prksOfflineRuntimeState() !== 'online'", handler_body)
        self.assertIn("changeObj.cancel()", handler_body)

        destroy_start = src.index("destroy: function () {")
        destroy_end = src.index("if (transient)", destroy_start)
        destroy_body = src[destroy_start:destroy_end]
        self.assertIn("__notesBeforeChangeHandler", destroy_body)
        self.assertIn("cm.off('beforeChange', beforeChangeHandler);", destroy_body)

    def test_insert_notes_markup_is_guarded(self):
        src = _read(_WORKS)
        start = src.index("function prksInsertNotesMarkup(cm, markup)")
        end = src.index("\n}\n", start)
        body = src[start:end]
        self.assertIn("prksWorkNotesMutationAllowed(", body)
        self.assertIn("prksOfflineGuardMutation", body)
        # The guard must return *before* any CodeMirror mutation.
        guard_idx = body.index("prksWorkNotesMutationAllowed(")
        mutate_idx = body.index("replaceRange(")
        self.assertLess(guard_idx, mutate_idx)

    def test_argument_create_is_guarded_before_any_network_call(self):
        """insertCreatedArgument() (Argument picker onCreate) must refuse to
        call prksCreateArgumentFromWork -- the POST /api/arguments path --
        while offline, not merely skip inserting markup afterward."""
        src = _read(_WORKS)
        start = src.index("function insertCreatedArgument(kind, name)")
        end = src.index("\n    }\n", start)
        body = src[start:end]
        guard_idx = body.index("prksWorkNotesMutationAllowed(")
        network_idx = body.index("prksCreateArgumentFromWork")
        self.assertLess(guard_idx, network_idx)

    def test_concept_and_argument_picker_onpick_route_through_insert_notes_markup(self):
        """Both pickers' onPick handlers must call the guarded insert helper
        rather than mutating CodeMirror directly."""
        src = _read(_WORKS)
        concept_start = src.index("function prksOpenConceptPicker(cm)")
        concept_end = src.index("function prksOpenArgumentPicker")
        concept_body = src[concept_start:concept_end]
        self.assertIn("onPick: function (id) {", concept_body)
        self.assertIn("prksInsertNotesMarkup(cm,", concept_body)

        arg_start = src.index("function prksOpenArgumentPicker(cm, work)")
        arg_end = src.index("\nasync function deleteWork", arg_start)
        arg_body = src[arg_start:arg_end]
        self.assertIn("onPick: function (id) {", arg_body)
        self.assertIn("prksInsertNotesMarkup(cm,", arg_body)
        self.assertIn("onCreate: function (name, kind) {", arg_body)
        self.assertIn("insertCreatedArgument(", arg_body)

    def test_apply_offline_readonly_disables_mutating_toolbar_buttons(self):
        src = _read(_WORKS)
        self.assertIn("function prksSetEasyMDEToolbarMutationEnabled(ctx, enabled)", src)
        start = src.index("const PRKS_EASYMDE_MUTATING_TOOLBAR_CLASSES")
        end = src.index("];", start)
        classes_block = src[start:end]
        for cls in (
            "bold",
            "italic",
            "heading",
            "quote",
            "unordered-list",
            "ordered-list",
            "link",
            "image",
            "prks-insert-concept",
            "prks-insert-argument",
        ):
            self.assertIn("'%s'" % cls, classes_block)
        # Non-mutating actions must never be listed.
        for cls in ("preview", "side-by-side", "fullscreen", "prks-notes-help"):
            self.assertNotIn("'%s'" % cls, classes_block)

        apply_start = src.index("function prksApplyOfflineNotesReadOnly(ctx, offline)")
        apply_end = src.index("\n}\n", apply_start)
        apply_body = src[apply_start:apply_end]
        self.assertIn("setOption('readOnly', !!offline)", apply_body)
        self.assertIn("prksSetEasyMDEToolbarMutationEnabled(ctx, !offline)", apply_body)

    def test_toolbar_disable_helper_sets_native_disabled_and_aria(self):
        src = _read(_WORKS)
        start = src.index("function prksSetEasyMDEToolbarMutationEnabled(ctx, enabled)")
        end = src.index("\n}\n", start)
        body = src[start:end]
        self.assertIn("btn.disabled = !enabled", body)
        self.assertIn("aria-disabled", body)


if __name__ == "__main__":
    unittest.main()
