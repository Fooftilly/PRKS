"""Structural regressions for offline-mode behavior in the Work PDF viewer
integration (works-pdf.js): the viewer must be created/rebuilt in the vendor
viewer's own read-only `mode: 'preview'` boundary whenever PRKS is not
confirmed online, annotation-sync persistence must never be installed for a
preview-mode viewer, and every annotation mutation entry point must be
guarded the same way as other canonical Work mutations."""
import os
import re
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_WORKS_PDF = os.path.join(_FRONTEND, "js", "components", "works-pdf.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendOfflinePdfViewerTests(unittest.TestCase):
    def test_file_exists(self):
        self.assertTrue(os.path.isfile(_WORKS_PDF))

    def test_viewer_mode_is_never_hardcoded_to_work(self):
        src = _read(_WORKS_PDF)
        # The only two modes this integration ever passes to the vendor
        # viewer are 'work' and 'preview', decided by prksPdfDesiredMode()/
        # explicit rebuild target -- never a hardcoded mode: 'work' literal
        # that would ignore current connectivity.
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
        self.assertIn("if (mode === 'work') {", mount_body)
        self.assertIn("setupAnnotationPersistence(ctx, runtime, work.id)", mount_body)
        # Must be conditioned on mode, not called unconditionally after every mount.
        unconditional = re.search(r"\n\s*void setupAnnotationPersistence\(ctx, runtime, work\.id\);\n", mount_body)
        # the only call is inside the `if (mode === 'work')` block; confirm indentation
        # depth right above the call includes that guard.
        idx = mount_body.index("void setupAnnotationPersistence(ctx, runtime, work.id);")
        preceding = mount_body[:idx]
        self.assertIn("if (mode === 'work') {", preceding[-120:] if len(preceding) >= 120 else preceding)

    def test_online_to_offline_transition_rebuilds_existing_viewer(self):
        src = _read(_WORKS_PDF)
        self.assertIn("function prksRebuildPdfViewerForModeChange(ctx, runtime, desiredMode)", src)
        self.assertIn("prksOfflineRuntimeSubscribe(function (state)", src)
        sub_start = src.index("if (typeof prksOfflineRuntimeSubscribe === 'function') {")
        sub_body = src[sub_start : sub_start + 700]
        self.assertIn("desiredMode = state === 'online' ? 'work' : 'preview'", sub_body)
        self.assertIn("prksRebuildPdfViewerForModeChange(ctx, runtime, desiredMode)", sub_body)

    def test_rebuild_stops_annotation_persistence_before_tearing_down_viewer(self):
        src = _read(_WORKS_PDF)
        rebuild_start = src.index("function prksRebuildPdfViewerForModeChange")
        rebuild_end = src.index("if (typeof prksOfflineRuntimeSubscribe === 'function') {")
        rebuild_body = src[rebuild_start:rebuild_end]
        persistence_destroy_at = rebuild_body.index("runtime.annotationPersistence.destroy()")
        old_viewer_destroy_at = rebuild_body.index("oldViewer.destroy()")
        self.assertLess(
            persistence_destroy_at,
            old_viewer_destroy_at,
            "the annotation-sync worker must be stopped before the viewer it syncs is destroyed",
        )

    def test_rebuild_preserves_current_page(self):
        src = _read(_WORKS_PDF)
        self.assertIn("function prksCurrentPdfPageNumber(runtime)", src)
        rebuild_start = src.index("function prksRebuildPdfViewerForModeChange")
        rebuild_body = src[rebuild_start : rebuild_start + 1800]
        self.assertIn("prksCurrentPdfPageNumber(runtime)", rebuild_body)
        self.assertIn("prksMountPdfViewer(ctx, work, runtime, targetNode, page, desiredMode)", rebuild_body)

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


if __name__ == "__main__":
    unittest.main()
