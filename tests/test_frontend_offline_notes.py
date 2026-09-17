"""Research Notes editors are durable. Liveness still guards every
programmatic EasyMDE mutation; connectivity does not.

CodeMirror's own `readOnly` option is not a sufficient editor-instance
guard, because PRKS's toolbar-driven programmatic edit paths (Concept
picker, Argument picker, Argument/Stance creation) never consult it.
Every PRKS-owned programmatic edit boundary must go through one shared
`prksWorkNotesMutationAllowed(ctx)` predicate. That predicate is about
the live TabContext and the live CodeMirror instance -- never about
whether PRKS is reachable.
"""
import os
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_WORKS = os.path.join(_FRONTEND, "js", "components", "works.js")
_UI = os.path.join(_FRONTEND, "js", "ui.js")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendOfflineNotesGuardTests(unittest.TestCase):
    def test_file_exists(self):
        self.assertTrue(os.path.isfile(_WORKS))

    def test_mutation_allowed_predicate_checks_liveness_not_connectivity(self):
        src = _read(_WORKS)
        self.assertIn("function prksWorkNotesMutationAllowed(ctx, cm)", src)
        start = src.index("function prksWorkNotesMutationAllowed(ctx, cm)")
        end = src.index("\n}\n", start)
        body = src[start:end]
        self.assertIn("isCurrent", body)
        self.assertIn("getResource('workNotes')", body)
        self.assertNotIn("prksOfflineRuntimeState", body)
        self.assertIn("window.prksWorkNotesMutationAllowed = prksWorkNotesMutationAllowed;", src)

    def test_mutation_allowed_requires_exact_live_codemirror_instance(self):
        src = _read(_WORKS)
        start = src.index("function prksWorkNotesMutationAllowed(ctx, cm)")
        end = src.index("\n}\n", start)
        body = src[start:end]
        self.assertIn("if (cm && notes.editor.codemirror !== cm) return false;", body)

    def test_completion_pickers_are_guarded_and_editor_instance_aware(self):
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
            self.assertNotIn("prksOfflineGuardMutation", body, fn_name)
            guard_idx = body.index("prksWorkNotesMutationAllowed(")
            mutate_idx = body.index("replaceRange(")
            self.assertLess(guard_idx, mutate_idx, fn_name)

    def test_no_offline_beforechange_barrier(self):
        src = _read(_WORKS)
        init_start = src.index("function initEasyMDE(ctx, work)")
        init_end = src.index("function prksWorkNotesMarkEdit")
        init_body = src[init_start:init_end]
        self.assertNotIn("notesBeforeChangeHandler", init_body)
        self.assertNotIn("prksApplyOfflineNotesReadOnly", src)
        self.assertNotIn("Offline — notes are read-only", src)

    def test_insert_notes_markup_is_guarded(self):
        src = _read(_WORKS)
        start = src.index("function prksInsertNotesMarkup(cm, markup)")
        end = src.index("\n}\n", start)
        body = src[start:end]
        self.assertIn("prksWorkNotesMutationAllowed(", body)
        self.assertNotIn("prksOfflineGuardMutation", body)
        guard_idx = body.index("prksWorkNotesMutationAllowed(")
        mutate_idx = body.index("replaceRange(")
        self.assertLess(guard_idx, mutate_idx)

    def test_argument_create_is_guarded_before_construction(self):
        src = _read(_WORKS)
        start = src.index("function insertCreatedArgument(kind, name)")
        end = src.index("\n    }\n", start)
        body = src[start:end]
        guard_idx = body.index("prksWorkNotesMutationAllowed(")
        network_idx = body.index("prksCreateArgumentFromWork")
        self.assertLess(guard_idx, network_idx)

    def test_concept_and_argument_picker_onpick_route_through_insert_notes_markup(self):
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

    def test_research_notes_save_is_durable(self):
        src = _read(_WORKS)
        start = src.index("function prksEnqueueWorkResearchNotesSave(")
        body = src[start : src.index("function prksFlushPendingWorkResearchNotes", start)]
        self.assertIn("prksSaveWorkNoteDurably", body)
        self.assertNotIn("prksRequest(", body)
        self.assertNotIn("text_content", body)
        self.assertNotIn("prksOfflineRuntimeState", body)
        self.assertNotIn("prksOfflineMarkConceptsChanged", body)
        self.assertNotIn("prksOfflineMarkArgumentsChanged", body)

    def test_work_private_notes_save_is_durable(self):
        src = _read(_UI)
        start = src.index("function prksEnqueueWorkPrivateNoteSave(")
        body = src[start : src.index("function prksEnqueuePrivateNotesSave(", start)]
        self.assertIn("prksSaveWorkNoteDurably", body)
        self.assertNotIn("prksRequest(", body)
        self.assertNotIn("prksOfflineMarkEntityChanged", body)
        # Park-flush may still be syncing when a remounted draft is edited;
        # scope_busy must keep the draft dirty and schedule a retry.
        self.assertIn("scope_busy", body)
        self.assertIn("prksSchedulePrivateNoteBusyRetry", body)
        self.assertIn("editor.dirty = true", body)

    def test_private_note_busy_retry_waits_for_sync(self):
        src = _read(_UI)
        self.assertIn("function prksSchedulePrivateNoteBusyRetry(editor, token)", src)
        start = src.index("function prksSchedulePrivateNoteBusyRetry(editor, token)")
        body = src[start : src.index("\nfunction prksEnqueueWorkPrivateNoteSave(", start)]
        self.assertIn("prksSync.subscribe", body)
        self.assertIn("prksEnqueuePrivateNotesSave(editor)", body)
        self.assertIn("privateNotesBusyRetry:", body)

    def test_folder_private_notes_use_durable_set_folder_field(self):
        src = _read(_UI)
        start = src.index("function prksEnqueuePrivateNotesSave(")
        body = src[start : src.index("function prksFlushPendingPrivateNotes(", start)]
        self.assertIn("patchFolder(", body)
        self.assertIn("private_notes", body)
        self.assertNotIn("/api/folders/", body)
        self.assertNotIn("prksRequest(", body)
        self.assertNotIn("Offline — notes are read-only", body)

    def test_toolbar_class_list_still_names_mutating_actions(self):
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
        for cls in ("preview", "side-by-side", "fullscreen", "prks-notes-help"):
            self.assertNotIn("'%s'" % cls, classes_block)

    def test_toolbar_disable_helper_sets_native_disabled_and_aria(self):
        src = _read(_WORKS)
        start = src.index("function prksSetEasyMDEToolbarMutationEnabled(ctx, enabled)")
        end = src.index("\n}\n", start)
        body = src[start:end]
        self.assertIn("btn.disabled = !enabled", body)
        self.assertIn("aria-disabled", body)


if __name__ == "__main__":
    unittest.main()
