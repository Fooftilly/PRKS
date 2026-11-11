"""The SQLite startup reconcilers' cold-start phases (``backend/text_index.py``).

The recorder, the harness profile and the entrypoint phases are
tests/test_e2e_cold_start_recorder.py.
"""
from __future__ import annotations

import ast
import contextlib
import io
import os
import unittest
from pathlib import Path
from unittest import mock

from backend import startup_profile
from tests.e2e import harness


class ChildStartupPhaseTests(unittest.TestCase):
    def test_text_reconcile_subphases_record_bounded_durations(self):
        # Exercise the actual reconciler's stage boundaries without a browser,
        # database, PDF extraction or modifying canonical storage.
        from backend.text_index import PRKSTextIndex

        index = object.__new__(PRKSTextIndex)
        index._fts_suspect = True
        env = {
            "PRKS_E2E_STARTUP_PROFILE": "1",
            "PRKS_E2E": "1",
            "PRKS_TESTING": "1",
        }
        output = io.StringIO()
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(index, "_load_sync_state", return_value={}),
            mock.patch.object(index, "_canonical_managed_works", return_value={}),
            mock.patch.object(index, "_remove_orphan_ids", return_value=0) as orphans,
            mock.patch.object(index, "_verify_or_rebuild_fts", return_value=False) as fts,
            contextlib.redirect_stderr(output),
        ):
            result = index._reconcile_all_inner(object())
        self.assertEqual(result["processed"], 0)
        self.assertFalse(result["fts_rebuilt"])
        orphans.assert_called_once_with([])
        fts.assert_called_once_with()
        self.assertEqual(
            set(startup_profile.parse_records(output.getvalue())),
            {
                "startup_text_load_state",
                "startup_text_source_scan",
                "startup_text_sync",
                "startup_text_work_sync",
                "startup_text_orphan_cleanup",
                "startup_text_fts_verify",
            },
        )
        # No pending PDF means no extractor availability probe: absence is
        # intentional and must not be mistaken for an observed zero.
        self.assertNotIn("startup_text_extractor_probe", output.getvalue())
        self.assertNotIn("object at", output.getvalue())

    def test_pending_pdf_records_extractor_probe_and_work_sync_once(self):
        from backend.text_index import PRKSTextIndex, TextIndexSyncResult

        index = object.__new__(PRKSTextIndex)
        index._fts_suspect = False
        env = {
            "PRKS_E2E_STARTUP_PROFILE": "1",
            "PRKS_E2E": "1",
            "PRKS_TESTING": "1",
        }
        output = io.StringIO()
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(index, "_load_sync_state", return_value={}),
            mock.patch.object(index, "_canonical_managed_works",
                              return_value={
                                  "fixture-a": "/api/pdfs/fixture-a.pdf",
                                  "fixture-b": "/api/pdfs/fixture-b.pdf",
                              }),
            mock.patch.object(index, "_resolve_managed_source",
                              return_value=("missing", None, "/api/pdfs/fixture.pdf")),
            mock.patch("backend.text_index.extractor_available",
                       return_value=False) as availability,
            mock.patch.object(index, "sync_work",
                              return_value=TextIndexSyncResult(action="missing")) as sync,
            mock.patch.object(index, "_remove_orphan_ids", return_value=0),
            contextlib.redirect_stderr(output),
        ):
            summary = index._reconcile_all_inner(object())
        self.assertEqual(summary["processed"], 2)
        self.assertEqual(summary["missing"], 2)
        self.assertTrue(summary["extractor_unavailable"])
        availability.assert_called_once_with()
        self.assertEqual(sync.call_count, 2)
        for call, work_id in zip(sync.call_args_list, ("fixture-a", "fixture-b")):
            self.assertEqual(call.args, (work_id, f"/api/pdfs/{work_id}.pdf"))
            self.assertEqual(call.kwargs, {
                "force": False, "allow_extract": False,
                "known_row": None, "known_fingerprint": None,
            })
        phases = startup_profile.parse_records(output.getvalue())
        self.assertTrue({
            "startup_text_sync", "startup_text_extractor_probe",
            "startup_text_work_sync",
        }.issubset(phases))
        for name in ("startup_text_extractor_probe", "startup_text_work_sync"):
            self.assertEqual(output.getvalue().count("STARTUP_PHASE " + name + " "), 1)


class HarnessColdStartIntegrationTests(unittest.TestCase):
    def test_source_instruments_actual_reconcilers_and_entrypoint(self):
        root = Path(__file__).resolve().parents[1]
        app = ast.parse((root / "prks_app.py").read_text(encoding="utf-8"))
        server = ast.parse((root / "backend/server.py").read_text(encoding="utf-8"))
        text_index = ast.parse(
            (root / "backend/text_index.py").read_text(encoding="utf-8")
        )

        def phases(tree):
            return {
                call.args[0].value
                for node in ast.walk(tree)
                if isinstance(node, ast.With)
                for part in node.items
                if isinstance(part.context_expr, ast.Call)
                for call in [part.context_expr]
                if isinstance(call.func, ast.Name)
                and call.func.id == "startup_phase"
                and len(call.args) == 1
                and isinstance(call.args[0], ast.Constant)
            }

        self.assertTrue({
            "startup_dependency_gate", "startup_identity", "startup_jobs",
            "startup_storage_open", "startup_bind",
        }.issubset(phases(app)))
        self.assertTrue({
            "startup_text_reconcile", "startup_research_reconcile",
            "startup_pdf_cleanup", "startup_staging_cleanup",
        }.issubset(phases(server)))
        self.assertTrue({
            "startup_text_load_state", "startup_text_source_scan",
            "startup_text_sync", "startup_text_extractor_probe",
            "startup_text_work_sync", "startup_text_orphan_cleanup",
            "startup_text_fts_verify",
        }.issubset(phases(text_index)))
        self.assertTrue(
            phases(app) | phases(server) | phases(text_index) <= startup_profile.PHASES
        )


if __name__ == "__main__":
    unittest.main()
