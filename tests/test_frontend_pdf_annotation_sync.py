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

    def test_materialization_handoff_selftests(self):
        proc = subprocess.run(
            ["node", str(ROOT / "tests" / "browser" / "run_pdf_annotation_handoff_selftest.js")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("checks passed", proc.stdout)

    def test_cow_remount_retry_selftests(self):
        """Behavioral: failed staged remount retries despite live shared viewer."""
        proc = subprocess.run(
            ["node", str(ROOT / "tests" / "browser" / "run_pdf_cow_remount_retry_selftest.js")],
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
        self.assertIn(
            "restoreEffectiveViewerAnnotations({ paintList: true, required: true })",
            catch_up_body,
        )
        self.assertLess(
            catch_up_body.index(
                "restoreEffectiveViewerAnnotations({ paintList: true, required: true })"
            ),
            catch_up_body.index("if (canonical <= materialized)"),
        )
        self.assertIn("prksRefreshPendingPdfAnnotations", catch_up_body)
        # Pending hydrate + viewer projection are fail-closed after snapshot accept
        # (no best-effort swallow that unlocks against a stale viewer).
        self.assertIn("_annotationCatchUpBlocksMutation = true", catch_up_body)
        self.assertIn("ANNOTATION_PENDING_HYDRATION_UNAVAILABLE", catch_up_body)
        self.assertIn("required: true", catch_up_body)
        self.assertNotIn("catch (_ePend)", catch_up_body)
        self.assertNotIn("catch (_eProj)", catch_up_body)
        self.assertIn("scheduleCatchUpProjectionRetry", catch_up_body)
        self.assertIn("prksBeginAnnotationMaterializationGate", catch_up_body)
        self.assertIn("prksEndAnnotationMaterializationGate", catch_up_body)
        # Catch-up finally ends its own gate. When shouldMaterialize, it must
        # begin a handoff block and MUST NOT re-enable user mutation / await
        # capability before releasing the gate (no unlocked interval).
        finally_at = catch_up_body.rindex("} finally {")
        finally_end = catch_up_body.index(
            "// Independent materialization gate", finally_at
        )
        catch_up_finally = catch_up_body[finally_at:finally_end]
        self.assertIn("prksEndAnnotationMaterializationGate(runtime)", catch_up_finally)
        self.assertIn("projectionReady", catch_up_finally)
        self.assertIn("scheduleCatchUpProjectionRetry", catch_up_finally)
        self.assertIn("prksBeginMaterializationHandoff(runtime)", catch_up_finally)
        self.assertIn("shouldMaterialize", catch_up_finally)
        self.assertNotIn("void requestFlush('materialize')", catch_up_finally)
        handoff_branch = catch_up_finally[
            catch_up_finally.index("if (shouldMaterialize && stillLive())") : catch_up_finally.index(
                "} else if (projectionReady"
            )
        ]
        self.assertIn("prksBeginMaterializationHandoff", handoff_branch)
        self.assertIn("setMutationEnabled(false)", handoff_branch)
        self.assertNotIn("setMutationEnabled(runtime.mode === 'work')", handoff_branch)
        self.assertNotIn("prksApplyPdfAnnotationCapability", handoff_branch)
        try_end = catch_up_body.index("} catch (_eCatchUp)")
        try_body = catch_up_body[:try_end]
        self.assertNotIn("void requestFlush('materialize')", try_body)
        self.assertIn("shouldMaterialize = true", try_body)
        after_finally = catch_up_body[finally_end:]
        self.assertIn("void requestFlush('materialize')", after_finally)
        self.assertLess(
            catch_up_body.rindex("prksEndAnnotationMaterializationGate(runtime)"),
            catch_up_body.index("void requestFlush('materialize')"),
        )
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
        wait_fn_at = works_pdf.index("async function prksWaitOutAnnotationMaterialization")
        wait_fn_end = works_pdf.index("\nfunction prksPdfUserMutationStillAllowed", wait_fn_at)
        wait_fn = works_pdf[wait_fn_at:wait_fn_end]
        # Deadline/lifecycle must cover the gate itself — no unbounded await gate.
        self.assertNotIn("await gate", wait_fn)
        self.assertIn("Promise.race", wait_fn)
        self.assertLess(wait_fn.index("const deadline"), wait_fn.index("Promise.race"))
        self.assertIn("lifecycleEscape", wait_fn)
        self.assertIn("pdf._destroyed", wait_fn)
        self.assertIn("persistence.destroyed", wait_fn)
        self.assertIn("persistence.paused", wait_fn)
        self.assertIn("30000", wait_fn)
        # No-gate handoff must await the timeout slice — not race an empty resolve.
        self.assertIn("await timeout", wait_fn)
        self.assertIn("else {\n            await timeout;\n        }", wait_fn)
        self.assertNotIn("? Promise.resolve(gate)", wait_fn)
        self.assertNotIn(": Promise.resolve()", wait_fn)
        # Client fidelity keys must include ink geometry (backend parity).
        self.assertIn("'inkList'", state_js)
        self.assertIn("'vertices'", state_js)
        self.assertIn("User path: plain delete after materialization lock", works_pdf)
        self.assertIn("prksPdfUserMutationStillAllowed", works_pdf)
        # Capability re-check after gate wait (Delete/comment) — no false UI settle.
        del_at = works_pdf.index("window.deletePdfAnnotationFromEditor")
        del_body = works_pdf[del_at:del_at + 1800]
        wait_at = del_body.index("prksWaitOutAnnotationMaterialization")
        self.assertIn("prksPdfUserMutationStillAllowed", del_body[wait_at:])
        # Materialization fail-closed: ACK-only reconcile must not be swallowed.
        mat_pass_at = works_pdf.index("async function runWorkAnnotationAndPdfPersistencePass")
        mat_pass_end = works_pdf.index(
            "\n    // PRKS may go offline mid-confirmation-loop", mat_pass_at
        )
        mat_pass = works_pdf[mat_pass_at:mat_pass_end]
        self.assertIn("if (!prksBeginAnnotationMaterializationGate(runtime))", mat_pass)
        self.assertIn("_annotationDurableWriteChain", mat_pass)
        self.assertIn("setMutationEnabled(false)", mat_pass)
        self.assertIn("restoreEffectiveViewerAnnotations", mat_pass)
        self.assertIn("await window.prksReconcileViewerAnnotations(matLive, ackOnly", mat_pass)
        self.assertNotIn("catch (_eRec)", mat_pass)
        # Legacy path: annotations first, then PDF with claimed replace generation.
        legacy_ann = mat_pass.index("`/api/works/${workId}/annotations`")
        legacy_claim = mat_pass.index("await exportAndPersistPdfCopy(saveToken, replaceGen)")
        self.assertLess(legacy_ann, legacy_claim)
        self.assertIn("Annotation save missing generation", mat_pass)
        self.assertIn("canonical_annotation_set_revision", mat_pass[legacy_ann:legacy_claim + 80])
        # Stale full-list guard: send acknowledged tip; refuse ANNOTATION_SET_STALE.
        self.assertIn("acknowledgedAnnotationSetRevision", mat_pass)
        self.assertIn("ANNOTATION_SET_STALE", mat_pass)
        self.assertIn(
            "canonical_annotation_set_revision: baseSetRev",
            mat_pass,
        )
        # Materialization finally: clear handoff + enable + end gate synchronously
        # (no await after enabling controller mutation before gate ends).
        self.assertIn("prksClearMaterializationHandoff(runtime)", mat_pass)
        self.assertIn("prksEndAnnotationMaterializationGate", mat_pass)
        end_gate_at = mat_pass.rindex("prksEndAnnotationMaterializationGate")
        restore_at = mat_pass.index("restoreEffectiveViewerAnnotations")
        clear_handoff_at = mat_pass.index("prksClearMaterializationHandoff(runtime)")
        # Path-bound unlock: work mode alone is not enough — viewer must be on
        # the exclusive managed path (shared-URL survivors stay locked).
        unlock_at = mat_pass.index("unlockViewer.setMutationEnabled(allowUnlock)")
        self.assertLess(restore_at, end_gate_at)
        self.assertLess(clear_handoff_at, unlock_at)
        self.assertLess(unlock_at, end_gate_at)
        self.assertGreater(end_gate_at, mat_pass.index("finally {"))
        mat_finally = mat_pass[mat_pass.rindex("} finally {") : end_gate_at + 80]
        # No await between enabling user mutation and ending the gate.
        enable_at = mat_finally.index("unlockViewer.setMutationEnabled(allowUnlock)")
        gate_end_at = mat_finally.index("prksEndAnnotationMaterializationGate")
        between = mat_finally[enable_at:gate_end_at]
        self.assertNotIn("await ", between)
        self.assertIn("allowUnlock = !!(unlockToWork && viewerBoundExclusive)", mat_finally)
        # Capability helper respects catch-up projection + handoff blocks.
        self.assertIn("catch_up_projection_pending", works_pdf)
        self.assertIn("materialization_handoff", works_pdf)
        self.assertIn("_annotationCatchUpBlocksMutation", works_pdf)
        self.assertIn("_annotationMaterializationHandoff", works_pdf)
        self.assertIn("prksBeginMaterializationHandoff", works_pdf)
        self.assertIn("prksClearMaterializationHandoff", works_pdf)
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
        # Materialization flush from catch-up after gate release (plus optional
        # handoff-retry flush if gate acquire fails — same string).
        only_flush = works_pdf.count("void requestFlush('materialize')")
        self.assertGreaterEqual(only_flush, 1)
        self.assertLessEqual(only_flush, 2)
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

    def test_export_applies_cow_file_path_retarget(self):
        """After shared-PDF COW, client must adopt returned file_path.

        Regression: A+B share path → A materializes (COW) → client applies
        exclusive path + Cache Storage key + remounts viewer without bumping
        viewerSetupToken, so B overwriting the old shared file cannot affect
        A's live/offline capability.
        """
        works_pdf = (ROOT / "frontend" / "js" / "components" / "works-pdf.js").read_text(
            encoding="utf-8"
        )
        export_at = works_pdf.index("async function exportAndPersistPdfCopy(")
        export_end = works_pdf.index(
            "\n    async function restoreEffectiveViewerAnnotations(", export_at
        )
        # Helpers that implement COW apply live immediately above export.
        helpers_at = works_pdf.rindex("function liveAnnotationViewer()", 0, export_at)
        cow_region = works_pdf[helpers_at:export_end]
        self.assertIn("okBody.file_path", cow_region)
        self.assertIn("applyCowPdfRetarget", cow_region)
        self.assertIn("runtime.filePath = path", cow_region)
        self.assertIn("runtime.work.file_path = path", cow_region)
        self.assertIn("cacheManagedPdfBytes", cow_region)
        self.assertIn("caches.open", cow_region)
        self.assertIn("cache.put(path, response)", cow_region)
        self.assertIn("remountPdfViewerAfterCowRetarget", cow_region)
        self.assertIn("createPrksPdfViewer", cow_region)
        self.assertIn("detachAnnotationViewer", cow_region)
        # Persistence identity must survive remount: rebind viewer, do not bump token.
        remount_at = cow_region.index("async function remountPdfViewerAfterCowRetarget")
        remount_end = cow_region.index("\n    async function applyCowPdfRetarget", remount_at)
        remount = cow_region[remount_at:remount_end]
        self.assertIn("runtime.viewer = newViewer", remount)
        self.assertIn("viewer = newViewer", remount)
        self.assertNotIn("viewerSetupToken =", remount)
        self.assertNotIn("viewerSetupToken || 0) + 1", remount)
        self.assertIn("leave viewerSetupToken", remount)
        self.assertIn("onAnnotationEvent(onAnnotationEvent)", remount)
        # P2: staging-first remount — never destroy old viewer before new succeeds.
        self.assertIn("pdf-viewer-cow-staging", remount)
        self.assertIn("keepOldViewerMutationDisabled", remount)
        detach_at = remount.index("detachAnnotationViewer(oldViewer)")
        create_at = remount.index("await createPrksPdfViewer(")
        self.assertLess(create_at, detach_at)
        apply_at = cow_region.index("async function applyCowPdfRetarget")
        apply_fn = cow_region[apply_at:]
        self.assertIn("PDF_COW_REMOUNT_FAILED", apply_fn)
        self.assertIn("scheduleCowViewerRemount", apply_fn)
        # Remount retry must key off path binding, not mere viewer presence —
        # a surviving shared-URL viewer must not block recovery.
        schedule_at = cow_region.index("function scheduleCowViewerRemount")
        schedule_fn = cow_region[schedule_at:remount_at]
        self.assertIn("viewerBoundToManagedPath", schedule_fn)
        self.assertNotIn("if (liveAnnotationViewer()) return;", schedule_fn)
        self.assertIn("lockViewerPendingCowRemount", schedule_fn)
        self.assertIn("runtime.viewerFilePath", remount)
        self.assertIn("viewerBoundToManagedPath(path)", apply_fn)
        self.assertNotIn(
            "if (liveAnnotationViewer()) return false;",
            apply_fn.split("const remounted")[0],
        )
        # P1 failure path: apply file_path from non-OK bodies before throw.
        export_fn = works_pdf[export_at:export_end]
        err_path = export_fn.index("if (!pdfRes.ok)")
        err_block = export_fn[err_path : export_fn.index("Every successful replacement", err_path)]
        self.assertIn("errBody.file_path", err_block)
        self.assertIn("applyCowPdfRetarget(errRetarget, buffer)", err_block)
        self.assertLess(
            err_block.index("applyCowPdfRetarget(errRetarget, buffer)"),
            err_block.index("if (code === 'ANNOTATION_MATERIALIZATION_STALE')"),
        )
        # Capability re-resolve after materialization must see the exclusive path
        # and must not unlock a shared-URL viewer still pending remount.
        mat_pass_at = works_pdf.index("async function runWorkAnnotationAndPdfPersistencePass")
        mat_pass = works_pdf[
            mat_pass_at : works_pdf.index(
                "\n    // PRKS may go offline mid-confirmation-loop", mat_pass_at
            )
        ]
        self.assertIn("file_path: runtime.filePath", mat_pass)
        self.assertIn("liveAnnotationViewer", mat_pass)
        self.assertIn("viewerBoundExclusive", mat_pass)
        self.assertIn("allowUnlock = !!(unlockToWork && viewerBoundExclusive)", mat_pass)
        self.assertIn("unlockViewer.setMutationEnabled(allowUnlock)", mat_pass)
        # Module-level capability must refuse mutation until viewerFilePath matches.
        cap_at = works_pdf.index("async function prksApplyPdfAnnotationCapability")
        cap_fn = works_pdf[cap_at : works_pdf.index("\nfunction prksCurrentPdfPageNumber", cap_at)]
        self.assertIn("prksViewerBoundToManagedPath", cap_fn)
        self.assertIn("cow_remount_pending", cap_fn)
        reconcile_at = works_pdf.index("function prksReconcilePdfMutationMode")
        reconcile_fn = works_pdf[
            reconcile_at : works_pdf.index("\nif (typeof prksOfflineRuntimeSubscribe", reconcile_at)
        ]
        self.assertIn("prksViewerBoundToManagedPath", reconcile_fn)
        self.assertIn("cow_remount_pending", reconcile_fn)
        mount_at = works_pdf.index("async function prksMountPdfViewer")
        mount_fn = works_pdf[mount_at : works_pdf.index("\nexport function initPdfViewerForWork", mount_at)]
        self.assertIn("runtime.viewerFilePath", mount_fn)

    def test_inplace_materialization_refreshes_whole_file_pdf_cache(self):
        """Successful POST /pdf refreshes prks-pdf-v1 only on the canonical path.

        The success body always includes file_path. A path that differs from
        this tab retargets; an equal path is the only in-place cache write.
        A missing path must not fall through to runtime.filePath. A failed
        cache put throws before coherence hooks and before the caller clears
        pendingMaterializationRevision. A COW remount failure still skips
        those hooks. Whole-file puts that can race a service-worker GET go
        through prks-pdf-cache-install.
        """
        works_pdf = (ROOT / "frontend" / "js" / "components" / "works-pdf.js").read_text(
            encoding="utf-8"
        )
        sw = (ROOT / "frontend" / "sw.js").read_text(encoding="utf-8")
        export_at = works_pdf.index("async function exportAndPersistPdfCopy(")
        export_end = works_pdf.index(
            "\n    async function restoreEffectiveViewerAnnotations(", export_at
        )
        export_fn = works_pdf[export_at:export_end]
        self.assertIn("okBody.file_path", export_fn)
        self.assertIn("throw new Error('PDF save missing file path')", export_fn)
        self.assertNotIn("runtime.filePath, buffer", export_fn)
        self.assertNotIn("if (!sawRetarget)", export_fn)
        refresh = "const cached = await cacheManagedPdfBytes(canonical, buffer);"
        self.assertIn(refresh, export_fn)
        refresh_at = export_fn.index(refresh)
        fail_at = export_fn.index("if (!cached) throw new Error('PDF cache update failed')")
        self.assertLess(export_fn.index("throw new Error(`PDF save failed"), refresh_at)
        retarget_at = export_fn.index("if (needsRetarget)")
        self.assertLess(retarget_at, refresh_at)
        self.assertLess(refresh_at, fail_at)
        self.assertLess(fail_at, export_fn.index("prksOfflineMarkEntityChanged"))
        retarget_arm = export_fn[retarget_at:export_fn.index("} else {", retarget_at)]
        self.assertIn("applyCowPdfRetarget(canonical, buffer)", retarget_arm)
        self.assertNotIn("cacheManagedPdfBytes", retarget_arm)
        rethrow = export_fn.index("throw cowRemountFailed")
        self.assertLess(fail_at, rethrow)
        self.assertLess(rethrow, export_fn.index("prksOfflineMarkEntityChanged"))
        # Cache failure must escape export so the durable success path never
        # clears the pending revision. Only STALE does that, and it does not retry.
        pass_at = works_pdf.index("await exportAndPersistPdfCopy(saveToken, claimed)")
        clear_at = works_pdf.index(
            "runtime.pendingMaterializationRevision = null", pass_at
        )
        self.assertLess(pass_at, clear_at)
        install_src = (ROOT / "frontend" / "js" / "pdf-cache-install.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("type: 'prks-pdf-cache-install'", install_src)
        install_at = install_src.index("export function prksPostPdfCacheInstall(")
        install_fn = install_src[
            install_at:install_src.index("export function prksPdfCacheInstallOutcome(", install_at)
        ]
        self.assertIn("PDF_CACHE_INSTALL_ACK_TIMEOUT_MS", install_fn)
        self.assertIn("[channel.port2, body]", install_fn)
        self.assertIn("finish('unacknowledged')", install_fn)
        self.assertNotIn("finish(false)", install_fn)
        self.assertNotIn("}, 2000)", install_fn)
        self.assertIn("export const PDF_CACHE_INSTALL_ACK_TIMEOUT_MS = 120000;", install_src)
        self.assertIn("throw new Error('PDF cache install unacknowledged')", install_src)
        self.assertIn(
            "prksPostPdfCacheInstall(controller, pathname, body).then(prksPdfCacheInstallOutcome)",
            works_pdf,
        )
        self.assertIn("from '/js/pdf-cache-install.js'", works_pdf)
        self.assertIn("PDF_CACHE_INSTALL_MESSAGE = 'prks-pdf-cache-install'", sw)
        self.assertIn("advanceWholeFilePdfGeneration(path)", sw)
        self.assertIn("wholeFilePdfGeneration(path) !== snapshot", sw)
        self.assertIn("generationAtStart = wholeFilePdfGeneration(pathname)", sw)


if __name__ == "__main__":
    unittest.main()
