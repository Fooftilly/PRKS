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

    def test_viewer_mode_is_never_hardcoded_to_work(self):
        src = _read(_WORKS_PDF)
        # The only two modes this integration ever passes to the vendor
        # viewer are 'work' and 'preview', decided by prksPdfDesiredMode() --
        # never a hardcoded mode: 'work' literal that would ignore current
        # connectivity.
        self.assertNotIn("mode: 'work',", src)
        self.assertNotIn('mode: "work",', src)
        self.assertIn("function prksPdfDesiredMode()", src)
        self.assertIn("prksOfflineRuntimeState() !== 'online' ? 'preview' : 'work'", src)

    def test_initial_mount_uses_desired_mode(self):
        src = _read(_WORKS_PDF)
        init_start = src.index("export function initPdfViewerForWork")
        init_body = src[init_start : init_start + 3000]
        self.assertIn("prksPdfDesiredMode()", init_body)
        self.assertIn("prksMountPdfViewer(ctx, work, runtime, targetNode, lastPage.initialPage, prksPdfDesiredMode())", init_body)

    def test_annotation_persistence_only_installed_in_work_mode(self):
        src = _read(_WORKS_PDF)
        mount_start = src.index("async function prksMountPdfViewer")
        mount_end = src.index("export function initPdfViewerForWork")
        mount_body = src[mount_start:mount_end]
        self.assertIn("if (desired === 'work') {", mount_body)
        self.assertIn(
            "prksEnsureAnnotationPersistence(ctx, runtime, work.id, viewer, setupToken)", mount_body
        )
        idx = mount_body.index("prksEnsureAnnotationPersistence(ctx, runtime, work.id, viewer, setupToken)")
        preceding = mount_body[:idx]
        self.assertIn("if (desired === 'work') {", preceding[-120:])

    def test_mount_reconciles_stale_desired_mode_before_publishing(self):
        """A viewer that began mounting for a stale `mode` (connectivity
        changed while createPrksPdfViewer() awaited) must be reconciled to
        the *current* desired mode via the live mutation lock -- never left
        stuck on the mode it started with, and never discarded/recreated."""
        src = _read(_WORKS_PDF)
        mount_start = src.index("async function prksMountPdfViewer")
        mount_end = src.index("export function initPdfViewerForWork")
        mount_body = src[mount_start:mount_end]
        self.assertIn("const desired = prksPdfDesiredMode();", mount_body)
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
        stale_reconcile_region = after_await[after_await.index("const desired = prksPdfDesiredMode();") :]
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
        sub_start = src.index("if (typeof prksOfflineRuntimeSubscribe === 'function') {")
        sub_body = src[sub_start : sub_start + 500]
        self.assertIn("prksForEachLiveTabContext(function (ctx) {", sub_body)
        self.assertIn("prksReconcilePdfMutationMode(ctx, runtime)", sub_body)

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
            snippet = src[at : at + 300]
            self.assertIn("prksOfflineGuardMutation", snippet, "%s must guard before any mutation" % fn_name)

    def test_sidebar_row_delete_is_guarded(self):
        src = _read(_WORKS_PDF)
        at = src.index(".annotation-row__delete")
        snippet = src[at : at + 400]
        self.assertIn("prksOfflineGuardMutation", snippet)

    def test_vendor_handle_exposes_set_mutation_enabled(self):
        types_path = os.path.join(_PROJECT_DIR, "tools", "pdf-viewer", "src", "types.ts")
        src = _read(types_path)
        self.assertIn("setMutationEnabled(enabled: boolean): void;", src)


if __name__ == "__main__":
    unittest.main()
