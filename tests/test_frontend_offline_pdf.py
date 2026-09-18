"""Structural regressions for offline-mode behavior in the Work PDF viewer
integration (works-pdf.js): the viewer's live mutation capability must follow
connectivity (PrksPdfViewerHandle.setMutationEnabled) without ever
destroying/recreating the viewer or document, annotation-sync persistence
must pause/resume rather than get torn down, and every annotation mutation
entry point must be guarded the same way as other canonical Work
mutations."""
import os
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_WORKS_PDF = os.path.join(_FRONTEND, "js", "components", "works-pdf.js")
_PDF_RUNTIME = os.path.join(_FRONTEND, "js", "pdf-work-runtime.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendOfflinePdfViewerTests(unittest.TestCase):
    def test_file_exists(self):
        self.assertTrue(os.path.isfile(_WORKS_PDF))

    def test_viewer_mode_follows_capability_not_connectivity_alone(self):
        src = _read(_WORKS_PDF)
        # Modes are still only 'work' / 'preview', but Slice E decides via
        # resolved annotationMutationAllowed on the runtime — not solely
        # prksOfflineRuntimeState().
        self.assertNotIn("mode: 'work',", src)
        self.assertNotIn('mode: "work",', src)
        self.assertIn("function prksPdfDesiredMode(runtime)", src)
        self.assertIn("annotationMutationAllowed === true", src)
        self.assertIn("prksResolvePdfAnnotationMutationCapability", src)
        self.assertIn("prksApplyPdfAnnotationCapability", src)
        body = src.split("function prksPdfDesiredMode")[1].split("async function prksApplyPdfAnnotationCapability")[0]
        allowed_at = body.index("annotationMutationAllowed === true")
        fallback_at = body.index("prksOfflineRuntimeState()")
        self.assertLess(allowed_at, fallback_at)

    def test_initial_mount_uses_desired_mode(self):
        src = _read(_WORKS_PDF)
        init_start = src.index("export function initPdfViewerForWork")
        init_body = src[init_start : init_start + 3000]
        # Always start EmbedPDF in preview; capability + durable bridge enable
        # mutations only after hydrate (never mutation-capable during startup).
        self.assertIn(
            "prksMountPdfViewer(ctx, work, runtime, targetNode, lastPage.initialPage, 'preview')",
            init_body,
        )
        self.assertIn("annotationDurableBridgeReady = false", src)

    def test_annotation_persistence_only_installed_in_work_mode(self):
        src = _read(_WORKS_PDF)
        mount_start = src.index("async function prksMountPdfViewer")
        mount_end = src.index("export function initPdfViewerForWork")
        mount_body = src[mount_start:mount_end]
        # Durable startup may install hydrate/bridge while still preview
        # (online_awaiting_base / *_awaiting_bridge); legacy stays work-only.
        self.assertIn("needsPersistenceSetup", mount_body)
        self.assertIn("online_awaiting_base", mount_body)
        self.assertIn(
            "prksEnsureAnnotationPersistence(ctx, runtime, work.id, viewer, setupToken)", mount_body
        )

    def test_mount_reconciles_stale_desired_mode_before_publishing(self):
        """A viewer that began mounting for a stale `mode` (connectivity
        changed while createPrksPdfViewer() awaited) must be reconciled to
        the *current* desired mode via the live mutation lock -- never left
        stuck on the mode it started with, and never discarded/recreated."""
        src = _read(_WORKS_PDF)
        mount_start = src.index("async function prksMountPdfViewer")
        mount_end = src.index("export function initPdfViewerForWork")
        mount_body = src[mount_start:mount_end]
        self.assertIn("const desired = prksPdfDesiredMode(runtime);", mount_body)
        self.assertIn("if (desired !== mode", mount_body)
        self.assertIn("viewer.setMutationEnabled(desired === 'work');", mount_body)
        # The reconciliation happens before runtime.mode is set to the
        # publish-time value, i.e. before this viewer is treated as settled.
        reconcile_at = mount_body.index("viewer.setMutationEnabled(desired === 'work');")
        publish_at = mount_body.index("runtime.mode = desired;")
        self.assertLess(reconcile_at, publish_at)
        # Never destroy/recreate the just-created viewer merely because its
        # starting mode was stale.
        after_await = mount_body[mount_body.index("const viewer = await createPrksPdfViewer") :]
        stale_reconcile_region = after_await[after_await.index("const desired = prksPdfDesiredMode(runtime);") :]
        self.assertNotIn("viewer.destroy()", stale_reconcile_region[:400])

    def test_connectivity_reconcile_never_destroys_the_viewer(self):
        src = _read(_WORKS_PDF)
        reconcile_start = src.index("function prksReconcilePdfMutationMode")
        reconcile_end = src.index(
            "if (typeof prksOfflineRuntimeSubscribe === 'function') {",
            reconcile_start,
        )
        reconcile_body = src[reconcile_start:reconcile_end]
        self.assertIn("runtime.viewer.setMutationEnabled(desired === 'work');", reconcile_body)
        self.assertNotIn(".destroy()", reconcile_body)
        self.assertNotIn("createPrksPdfViewer", reconcile_body)
        self.assertNotIn("prksMountPdfViewer", reconcile_body)

    def test_connectivity_reconcile_pauses_and_resumes_persistence(self):
        src = _read(_WORKS_PDF)
        reconcile_start = src.index("function prksReconcilePdfMutationMode")
        reconcile_end = src.index(
            "if (typeof prksOfflineRuntimeSubscribe === 'function') {",
            reconcile_start,
        )
        reconcile_body = src[reconcile_start:reconcile_end]
        self.assertIn("runtime.annotationPersistence.resume()", reconcile_body)
        self.assertIn("runtime.annotationPersistence.pause()", reconcile_body)
        self.assertIn("prksEnsureAnnotationPersistence(", reconcile_body)

    def test_subscriber_reconciles_every_live_pdf_runtime(self):
        src = _read(_WORKS_PDF)
        # Shell-level subscriber is the last prksOfflineRuntimeSubscribe block.
        sub_start = src.rindex("if (typeof prksOfflineRuntimeSubscribe === 'function') {")
        sub_body = src[sub_start:]
        self.assertIn("prksForEachLiveTabContext(function (ctx) {", sub_body)
        self.assertIn("prksApplyPdfAnnotationCapability(ctx, runtime", sub_body)
        self.assertIn("annotationDurableBridgeReady", sub_body)

    def test_ensure_annotation_persistence_installs_at_most_once(self):
        src = _read(_WORKS_PDF)
        start = src.index("function prksEnsureAnnotationPersistence")
        end = src.index("async function prksMountPdfViewer")
        body = src[start:end]
        self.assertIn("runtime.annotationPersistence || runtime._persistenceSetupStarted", body)
        self.assertIn("runtime._persistenceSetupStarted = true;", body)

    def test_annotation_persistence_stilllive_pinned_to_viewer_identity(self):
        src = _read(_WORKS_PDF)
        setup_start = src.index("async function setupAnnotationPersistence")
        still_live_at = src.index("function stillLive()", setup_start)
        snippet = src[still_live_at : still_live_at + 300]
        self.assertIn(
            "prksPdfPersistenceStillLive(ctx, generation, runtime, viewer, setupToken)", snippet
        )

    def test_viewer_setup_token_bumped_on_every_publish(self):
        src = _read(_WORKS_PDF)
        mount_start = src.index("async function prksMountPdfViewer")
        mount_end = src.index("export function initPdfViewerForWork")
        mount_body = src[mount_start:mount_end]
        self.assertIn("runtime.viewerSetupToken = (runtime.viewerSetupToken || 0) + 1;", mount_body)

    def test_persistence_worker_stilllive_helper_supports_viewer_identity(self):
        src = _read(_PDF_RUNTIME)
        self.assertIn(
            "function prksPdfPersistenceStillLive(ctx, generation, runtime, viewer, setupToken)", src
        )
        self.assertIn("if (viewer !== undefined && runtime.viewer !== viewer) return false;", src)
        self.assertIn(
            "if (setupToken !== undefined && runtime.viewerSetupToken !== setupToken) return false;", src
        )

    def test_persistence_worker_has_pause_and_resume(self):
        src = _read(_PDF_RUNTIME)
        self.assertIn("worker.pause = function ()", src)
        self.assertIn("worker.resume = function ()", src)
        pause_start = src.index("worker.pause = function ()")
        pause_body = src[pause_start : pause_start + 500]
        self.assertIn("unschedule(worker.retryTimer)", pause_body)
        resume_start = src.index("worker.resume = function ()")
        resume_body = src[resume_start : resume_start + 400]
        self.assertIn("opts.hasPendingChanges", resume_body)

    def test_drain_queue_does_not_spin_while_paused(self):
        src = _read(_WORKS_PDF)
        drain_start = src.index("async function drainFlushQueue")
        drain_end = src.index("function requestFlush(")
        drain_body = src[drain_start:drain_end]
        self.assertIn("worker && worker.paused", drain_body)

    def test_annotation_mutation_entry_points_are_guarded(self):
        src = _read(_WORKS_PDF)
        for fn_name in ("window.deletePdfAnnotationFromEditor = async function () {", "window.savePdfAnnotationComment = async function () {"):
            at = src.index(fn_name)
            snippet = src[at : at + 550]
            self.assertIn("annotationMutationAllowed", snippet, "%s must check capability" % fn_name)
            self.assertIn("prksOfflineGuardMutation", snippet, "%s must retain legacy online guard" % fn_name)

    def test_sidebar_row_delete_is_guarded(self):
        src = _read(_WORKS_PDF)
        at = src.index(".annotation-row__delete")
        handler_at = src.index("annotation-row__delete", at + 1)
        snippet = src[handler_at : handler_at + 700]
        self.assertIn("annotationMutationAllowed", snippet)
        self.assertIn("prksOfflineGuardMutation", snippet)

    def test_vendor_handle_exposes_set_mutation_enabled(self):
        types_path = os.path.join(_PROJECT_DIR, "tools", "pdf-viewer", "src", "types.ts")
        src = _read(types_path)
        self.assertIn("setMutationEnabled(enabled: boolean): void;", src)

    def test_vendor_handle_exposes_programmatic_annotation_mutation(self):
        """User-input lock must not block reconcile create/update/delete."""
        types_path = os.path.join(_PROJECT_DIR, "tools", "pdf-viewer", "src", "types.ts")
        src = _read(types_path)
        self.assertIn("beginProgrammaticAnnotationMutation(): void;", src)
        self.assertIn("endProgrammaticAnnotationMutation(): void;", src)
        viewer_path = os.path.join(_PROJECT_DIR, "tools", "pdf-viewer", "src", "viewer.tsx")
        viewer = _read(viewer_path)
        self.assertIn("allowsAnnotationMutation()", viewer)
        self.assertIn("controller.setUserMutationEnabled(enabled)", viewer)
        # Markup tools are user-only — programmatic depth must not authorize them.
        act_at = viewer.index("activateMarkupTool: (tool) => {")
        act_body = viewer[act_at:act_at + 280]
        self.assertIn("allowsUserAnnotationMutation()", act_body)
        self.assertNotIn("allowsAnnotationMutation()", act_body)
        controller = _read(os.path.join(_PROJECT_DIR, "tools", "pdf-viewer", "src", "controller.ts"))
        self.assertIn("setUserMutationEnabled", controller)
        self.assertIn("allowsAnnotationMutation()", controller)
        # Synchronous gate: setUserMutationEnabled before React mode flip.
        start = viewer.index("handle.setMutationEnabled = (enabled: boolean) => {")
        end = viewer.index("\n    };", start)
        body = viewer[start:end]
        self.assertLess(
            body.index("controller.setUserMutationEnabled(enabled)"),
            body.index("currentMode = nextMode;"),
        )
        reconcile = _read(os.path.join(_PROJECT_DIR, "frontend", "js", "pdf-annotation-reconcile.js"))
        self.assertIn("beginProgrammaticAnnotationMutation", reconcile)
        self.assertIn("endProgrammaticAnnotationMutation", reconcile)

    def test_sidebar_delete_waits_out_materialization_not_programmatic(self):
        """User Delete/comment must wait for materialization; programmatic is reconcile-only."""
        works = _read(os.path.join(_PROJECT_DIR, "frontend", "js", "components", "works-pdf.js"))
        self.assertIn("prksWaitOutAnnotationMaterialization", works)
        del_at = works.index("window.deletePdfAnnotationFromEditor")
        del_body = works[del_at:del_at + 1200]
        self.assertIn("prksWaitOutAnnotationMaterialization", del_body)
        self.assertNotIn("prksViewerProgrammaticDelete", del_body)
        save_at = works.index("window.savePdfAnnotationComment")
        save_body = works[save_at:save_at + 1600]
        self.assertIn("prksWaitOutAnnotationMaterialization", save_body)
        self.assertNotIn("prksViewerProgrammaticUpdate", save_body)

    def test_set_mutation_enabled_clears_active_tool_before_preview(self):
        """setMutationEnabled(false) must synchronously return the annotation
        plugin to a non-mutating pointer state (clearActiveTool()) before the
        render that flips ApiBinder into 'preview' -- an already-active
        markup tool must never survive merely because the toolbar disappears
        a frame later."""
        viewer_path = os.path.join(_PROJECT_DIR, "tools", "pdf-viewer", "src", "viewer.tsx")
        src = _read(viewer_path)
        start = src.index("handle.setMutationEnabled = (enabled: boolean) => {")
        end = src.index("\n    };", start)
        body = src[start:end]
        self.assertIn("handle.clearActiveTool()", body)
        clear_idx = body.index("handle.clearActiveTool()")
        mode_flip_idx = body.index("currentMode = nextMode;")
        self.assertLess(clear_idx, mode_flip_idx)

    def test_undo_redo_gated_by_work_mode(self):
        viewer_path = os.path.join(_PROJECT_DIR, "tools", "pdf-viewer", "src", "viewer.tsx")
        src = _read(viewer_path)
        for fn in ("undo: () => {", "redo: () => {"):
            at = src.index(fn)
            snippet = src[at : at + 180]
            self.assertIn("allowsUserAnnotationMutation()", snippet)

    def test_setup_annotation_persistence_has_setup_time_eligibility_gate(self):
        """AGENTS.md 'persistence setup cannot install an active worker after
        an offline transition': setupAnnotationPersistence() must re-check
        eligibility (viewer identity + 'work' mode + online) after every
        async boundary and immediately before installing, and abandon
        (resetting _persistenceSetupStarted, never destroying the viewer)
        rather than install when ineligible."""
        src = _read(_WORKS_PDF)
        setup_start = src.index("async function setupAnnotationPersistence")
        setup_end = src.index("function prksPdfLastPageLocalKey")
        body = src[setup_start:setup_end]
        self.assertIn("function setupEligible()", body)
        self.assertIn("prksPdfPersistenceSetupEligible(ctx, generation, runtime, viewer, setupToken)", body)
        self.assertIn("function abandonSetup()", body)
        self.assertIn("runtime._persistenceSetupStarted = false;", body)
        self.assertNotIn(".destroy()", body)
        # Gate immediately before the actual install call.
        install_idx = body.index("prksInstallPdfAnnotationPersistenceIfCurrent(ctx, generation, runtime, viewer, setupToken")
        preceding = body[:install_idx]
        self.assertIn("if (!setupEligible()) {", preceding[-260:])

    def test_setup_eligibility_helper_checks_mode_and_connectivity(self):
        src = _read(_PDF_RUNTIME)
        self.assertIn("function prksPdfPersistenceSetupEligible(ctx, generation, runtime, viewer, setupToken)", src)
        start = src.index("function prksPdfPersistenceSetupEligible")
        end = src.index("function createPdfAnnotationPersistenceWorker", start)
        body = src[start:end]
        self.assertIn("prksPdfPersistenceStillLive(ctx, generation, runtime, viewer, setupToken)", body)
        self.assertIn("runtime.mode !== 'work'", body)
        self.assertIn("prksOfflineRuntimeState", body)
        # Durable hydrate may run while mode is still preview.
        self.assertIn("online_awaiting_base", body)
        self.assertIn("online_awaiting_bridge", body)
        self.assertIn("annotationMutationDurable === true", body)

    def test_confirm_persisted_token_stops_when_worker_paused(self):
        src = _read(_WORKS_PDF)
        start = src.index("async function confirmPersistedToken")
        end = src.index("\n    }\n", start)
        body = src[start:end]
        self.assertIn("pausedOffline()", body)
        # Checked before the first attempt, after the network probe, and
        # before the backoff delay -- i.e. at least 3 occurrences.
        self.assertGreaterEqual(body.count("pausedOffline()"), 3)


if __name__ == "__main__":
    unittest.main()
