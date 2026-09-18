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
        # Catch-up always fetches/applies coherent /annotations-snapshot
        # (bodies + gens together; never gens-only /pdf-materialization).
        catch_up_at = works_pdf.index("async function maybeCatchUpMaterialization")
        catch_up_end = works_pdf.index("function enqueueDurableAnnotationWrite", catch_up_at)
        catch_up_body = works_pdf[catch_up_at:catch_up_end]
        self.assertIn("/annotations-snapshot", catch_up_body)
        self.assertIn("applyAnnotationSnapshotToRuntime", catch_up_body)
        self.assertNotIn("/pdf-materialization", catch_up_body)
        self.assertLess(
            catch_up_body.index("/annotations-snapshot"),
            catch_up_body.index("if (canonical <= materialized)"),
        )
        # Fresh snapshot must project into live viewer+sidebar before unlock /
        # materialize decision — even when canonical <= materialized.
        self.assertIn("restoreEffectiveViewerAnnotations({ paintList: true })", catch_up_body)
        self.assertLess(
            catch_up_body.index("restoreEffectiveViewerAnnotations({ paintList: true })"),
            catch_up_body.index("if (canonical <= materialized)"),
        )
        self.assertIn("prksRefreshPendingPdfAnnotations", catch_up_body)
        self.assertIn("handoffToMaterialize", catch_up_body)
        # ACK-drained path must also use coherent catch-up — never assign
        # pendingMaterializationRevision from an incremental ACK alone.
        ack_sub_at = works_pdf.index("const isPdfAck = ack && op && (")
        ack_sub = works_pdf[ack_sub_at:ack_sub_at + 2200]
        self.assertIn("maybeCatchUpMaterialization", ack_sub)
        self.assertNotIn("pendingMaterializationRevision = setRev", ack_sub)
        self.assertNotIn("requestFlush('materialize')", ack_sub)
        # Incremental ACK must not relabel snapshot gen without continuity + changed.
        offline = (ROOT / "frontend" / "js" / "offline-runtime.js").read_text(encoding="utf-8")
        rec_at = offline.index("async function reconcilePdfAnnotation")
        rec_body = offline[rec_at:rec_at + 3500]
        self.assertIn("result.changed === true && nextGen === prevGen + 1", rec_body)
        state_js = (ROOT / "frontend" / "js" / "pdf-annotation-state.js").read_text(encoding="utf-8")
        self.assertIn("data.changed === true && nextGen === prevGen + 1", state_js)
        # User sidebar/editor waits out materialization; programmatic = reconcile only.
        self.assertIn("prksWaitOutAnnotationMaterialization", works_pdf)
        self.assertIn("prksBeginAnnotationMaterializationGate", works_pdf)
        self.assertIn("prksEndAnnotationMaterializationGate", works_pdf)
        self.assertIn("_annotationMaterializationGate", works_pdf)
        self.assertIn("User path: plain delete after materialization lock", works_pdf)
        # Materialization fail-closed: ACK-only reconcile must not be swallowed.
        mat_pass_at = works_pdf.index("async function runWorkAnnotationAndPdfPersistencePass")
        mat_pass = works_pdf[mat_pass_at:mat_pass_at + 7500]
        self.assertIn("prksBeginAnnotationMaterializationGate(runtime)", mat_pass)
        self.assertIn("_annotationDurableWriteChain", mat_pass)
        self.assertIn("setMutationEnabled(false)", mat_pass)
        self.assertIn("restoreEffectiveViewerAnnotations", mat_pass)
        self.assertIn("await window.prksReconcileViewerAnnotations(viewer, ackOnly", mat_pass)
        self.assertNotIn("catch (_eRec)", mat_pass)
        # Gate released only after restore + unlock (not before).
        self.assertIn("prksEndAnnotationMaterializationGate", mat_pass)
        end_gate_at = mat_pass.rindex("prksEndAnnotationMaterializationGate")
        restore_at = mat_pass.index("restoreEffectiveViewerAnnotations")
        unlock_at = mat_pass.index("viewer.setMutationEnabled(runtime.mode === 'work')")
        self.assertLess(restore_at, end_gate_at)
        self.assertLess(unlock_at, end_gate_at)
        self.assertGreater(end_gate_at, mat_pass.index("finally {"))
        # Real write-chain serializer: enqueue write fn, do not start early.
        self.assertIn("function enqueueDurableAnnotationWrite", works_pdf)
        self.assertIn("enqueueDurableAnnotationWrite(async function", works_pdf)
        # STALE recovery: clear obsolete claim, coherent catch-up, no identical retry.
        self.assertIn("ANNOTATION_MATERIALIZATION_STALE", works_pdf)
        stale_at = works_pdf.index("msg === 'ANNOTATION_MATERIALIZATION_STALE'")
        stale_handler = works_pdf[stale_at:stale_at + 700]
        self.assertIn("pendingMaterializationRevision = null", stale_handler)
        self.assertIn("maybeCatchUpMaterialization", stale_handler)
        self.assertNotIn("scheduleRetry", stale_handler)
        # Snapshot hydration retries after transient non-OK.
        self.assertIn("scheduleAnnotationBaseHydrationRetry", works_pdf)
        self.assertIn("_annotationBaseHydrationNeedsRetry", works_pdf)
        self.assertIn("hydrateAnnotationBaseFromServer", works_pdf)
        # Adoption skips known-stale bytes / unresolved ops.
        self.assertIn("adoptBlocked", works_pdf)
        adopt_py = (ROOT / "backend" / "pdf_annotation_adopt.py").read_text(encoding="utf-8")
        self.assertIn("known-stale PDF bytes", adopt_py)
        self.assertIn("STALE_CODE", adopt_py)
        # Local durable save path must not immediately flush PDF bytes.
        save_idx = works_pdf.index("await window.prksSavePdfAnnotationDurably")
        save_tail = works_pdf[save_idx:save_idx + 1600]
        self.assertIn("Do NOT materialize PDF bytes here", save_tail)
        self.assertNotIn("requestFlush('materialize')", save_tail)
        self.assertNotIn("pendingMaterializationRevision =", save_tail)
        # Materialization flush lives only inside coherent catch-up.
        only_flush = works_pdf.count("requestFlush('materialize')")
        self.assertEqual(only_flush, 1)
        self.assertIn("void requestFlush('materialize');", catch_up_body)
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
