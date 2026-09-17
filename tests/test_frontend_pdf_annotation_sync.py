"""Frontend PDF annotation durable family: registration + Node selftests."""
import pathlib
import subprocess
import unittest

from backend import sync_protocol

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "js"


class PdfAnnotationSyncFrontendTests(unittest.TestCase):
    def test_runtime_selftests(self):
        proc = subprocess.run(
            ["node", str(ROOT / "tests" / "browser" / "run_pdf_annotation_sync_selftest.js")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("checks passed", proc.stdout)

    def test_capability_selftests(self):
        proc = subprocess.run(
            ["node", str(ROOT / "tests" / "browser" / "run_pdf_annotation_capability_selftest.js")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("checks passed", proc.stdout)

    def test_the_family_is_registered_everywhere_it_must_be(self):
        families = (
            "CREATE_PDF_ANNOTATION",
            "SET_PDF_ANNOTATION",
            "DELETE_PDF_ANNOTATION",
        )
        store = (FRONTEND / "local-store.js").read_text(encoding="utf-8")
        runtime = (FRONTEND / "sync-runtime.js").read_text(encoding="utf-8")
        diagnostics = (FRONTEND / "sync-diagnostics.js").read_text(encoding="utf-8")
        index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        self.assertIn("pdf-annotation-state.js", index)
        for family in families:
            with self.subTest(family=family):
                self.assertIn(family, sync_protocol.supported_operations())
                self.assertIn("'%s'," % family, store)
                self.assertIn("%s: root.prksPdfAnnotationSyncHandler" % family, runtime)
                self.assertIn(family, diagnostics)
        coordinator = runtime[: runtime.index("root.createPrksSyncRuntime = createRuntime;")]
        for leaked in families + ("annotation_id", "pdf-annotation"):
            self.assertNotIn(leaked, coordinator, leaked)

    def test_works_pdf_holds_review_invariants(self):
        """Static contracts for local-first PDF annotation review blockers."""
        works_pdf = (FRONTEND / "components" / "works-pdf.js").read_text(encoding="utf-8")
        state = (FRONTEND / "pdf-annotation-state.js").read_text(encoding="utf-8")
        store = (FRONTEND / "local-store.js").read_text(encoding="utf-8")
        runtime = (FRONTEND / "sync-runtime.js").read_text(encoding="utf-8")
        # 1. Pending hydrate before reconcile
        self.assertIn("prksRefreshPendingPdfAnnotations", works_pdf)
        # 2. Live ACK applies revision to runtimes
        self.assertIn("prksApplyPdfAnnotationAckToLiveRuntimes", state)
        self.assertIn("prksApplyPdfAnnotationAckToLiveRuntimes", works_pdf)
        # 3. Coherent snapshot (items + revisions together)
        self.assertIn("work-annotations-snapshot", state)
        self.assertIn("annotations-snapshot", works_pdf)
        self.assertIn("prksIsPdfAnnotationsSnapshotShape", works_pdf)
        # 4. Mutation gated until durable bridge ready
        self.assertIn("annotationDurableBridgeReady", works_pdf)
        self.assertIn("online_awaiting_base", state)
        self.assertIn("online_awaiting_bridge", state)
        self.assertIn("mode: 'preview', durable: false, reason: 'online_awaiting_base'", state)
        # 5. Materialize only after ACK + clean queue (pendingMaterializationRevision)
        self.assertIn("pendingMaterializationRevision", works_pdf)
        self.assertIn("materialized_annotation_set_revision", works_pdf)
        self.assertIn("prksWorkHasUnresolvedPdfAnnotationOps", works_pdf)
        self.assertIn("Do NOT materialize PDF bytes here", works_pdf)
        self.assertIn("maybeCatchUpMaterialization", works_pdf)
        # Local durable save path must not immediately flush PDF bytes.
        save_idx = works_pdf.index("prksSavePdfAnnotationDurably")
        next_materialize = works_pdf.find("requestFlush('materialize')", save_idx)
        self.assertGreater(next_materialize, 0)
        self.assertIn("Do NOT materialize PDF bytes here", works_pdf[save_idx:next_materialize])
        # 6. SENT successor rebased against actual ACK server_revision
        self.assertIn("rebasePdfAnnotationDependents", store)
        self.assertIn("rebasePdfAnnotationDependents", state)
        self.assertIn("provisionalRevisionAfterAttempted", store)
        # Coordinator stays family-agnostic (no PDF op names in drain).
        self.assertNotIn("CREATE_PDF_ANNOTATION", runtime.split("root.createPrksSyncRuntime")[0])
        # 7. Terminal discard for gone Work / id reuse
        self.assertIn("discard: data.code", state)
        self.assertIn("ANNOTATION_ID_REUSED", state)


if __name__ == "__main__":
    unittest.main()
