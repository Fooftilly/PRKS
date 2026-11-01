"""Browser-free contracts for E2E-only cold server startup/shutdown attribution."""
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
    def test_only_disposable_e2e_testing_enables_recorder(self):
        for env in (
            {"PRKS_E2E_STARTUP_PROFILE": "1"},
            {"PRKS_E2E_STARTUP_PROFILE": "1", "PRKS_E2E": "1"},
            {"PRKS_E2E_STARTUP_PROFILE": "1", "PRKS_TESTING": "1"},
        ):
            with mock.patch.dict(os.environ, env, clear=True):
                output = io.StringIO()
                with (
                    contextlib.redirect_stderr(output),
                    mock.patch.object(startup_profile.time, "perf_counter") as clock,
                ):
                    with startup_profile.phase("startup_identity"):
                        pass
                    startup_profile.record_imports(1.0)
                clock.assert_not_called()
                self.assertEqual(output.getvalue(), "")

    def test_success_and_failure_keep_only_safe_fixed_names(self):
        env = {
            "PRKS_E2E_STARTUP_PROFILE": "1",
            "PRKS_E2E": "1",
            "PRKS_TESTING": "1",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            output = io.StringIO()
            with (
                contextlib.redirect_stderr(output),
                mock.patch.object(
                    startup_profile.time, "perf_counter",
                    side_effect=[10.0, 12.0, 20.0, 20.5],
                ),
            ):
                with startup_profile.phase("startup_identity"):
                    pass
                with self.assertRaisesRegex(RuntimeError, "synthetic"):
                    with startup_profile.phase("startup_bind"):
                        raise RuntimeError("synthetic private data /tmp/secret")
        self.assertEqual(
            startup_profile.parse_records(output.getvalue()),
            {"startup_identity": 2.0, "startup_bind_failed": 0.5},
        )
        self.assertNotIn("synthetic", output.getvalue())
        self.assertNotIn("secret", output.getvalue())

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

    def test_parser_refuses_unknown_injected_duplicate_nonfinite_and_bad_lines(self):
        log = "\n".join([
            "PRKS_E2E_STARTUP_PHASE startup_imports 1.250000",
            "PRKS_E2E_STARTUP_PHASE startup_imports 999.000000",
            "PRKS_E2E_STARTUP_PHASE startup_unknown 999.000000",
            "PRKS_E2E_STARTUP_PHASE startup_identity nan",
            "PRKS_E2E_STARTUP_PHASE startup_identity -1.00",
            "PRKS_E2E_STARTUP_PHASE startup_identity 1e6",
            "PRKS_E2E_STARTUP_PHASE startup_identity 1.23 /tmp/secret",
            "PRKS_E2E_STARTUP_PHASE startup_identity 0.350000",
            "PRKS_E2E_STARTUP_PHASE startup_x | secret | 1",
        ])
        self.assertEqual(
            startup_profile.parse_records(log),
            {"startup_imports": 1.25, "startup_identity": 0.35},
        )


class HarnessColdStartIntegrationTests(unittest.TestCase):
    def test_child_records_reach_profile_and_shutdown_is_nested(self):
        test_id = "profile.synthetic_child_startup"
        library = mock.Mock()
        library.server_env.return_value = {}
        with (
            mock.patch.dict(os.environ, {"PRKS_E2E_PROFILE": "1"}),
            mock.patch.object(harness, "find_free_port", return_value=18888),
            mock.patch.object(harness, "provision_library", return_value=library),
            mock.patch.object(harness, "apply_playwright_browser_env"),
            mock.patch.object(harness, "wait_http"),
            mock.patch.object(harness, "_terminate") as terminate,
        ):
            server = harness.AppServer()
            server._spawn = mock.Mock(side_effect=lambda env: (
                self.assertEqual(env["PRKS_E2E_STARTUP_PROFILE"], "1"),
                Path(server._stderr_path).write_text(
                    "PRKS_E2E_STARTUP_PHASE startup_imports 0.400000\n"
                    "PRKS_E2E_STARTUP_PHASE startup_dependency_gate 0.300000\n"
                    "PRKS_E2E_STARTUP_PHASE startup_x 900.000000\n",
                    encoding="utf-8",
                ),
                setattr(server, "proc", mock.Mock(
                    poll=mock.Mock(return_value=0)
                )),
            ))
            harness.start_test_profile(test_id)
            try:
                server.start()
                server.stop()
            finally:
                phases = harness.finish_test_profile(test_id)
        terminate.assert_called_once()
        library.drop.assert_called_once()
        self.assertEqual(phases["startup_imports"], 0.4)
        self.assertEqual(phases["startup_dependency_gate"], 0.3)
        self.assertNotIn("startup_x", phases)
        for name in ("server_start", "server_stop", "server_stop_process",
                     "server_stop_temp_cleanup", "postgres_drop"):
            self.assertIn(name, phases)
        self.assertGreaterEqual(phases["server_stop"], phases["server_stop_process"])
        self.assertGreaterEqual(phases["server_stop"], phases["server_stop_temp_cleanup"])

    def test_new_timers_are_not_counted_twice_in_ranking(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            from tests.e2e import run

        phases = {
            "postgres_provision": 0.2,
            "server_start": 1.1,
            "server_http_ready": 1.09,
            "startup_imports": 0.3,
            "startup_dependency_gate": 0.5,
            "startup_dependency_gate_failed": 0.1,
            # These are inside startup_text_reconcile and server_start.
            "startup_text_reconcile": 0.6,
            "startup_text_source_scan": 0.4,
            "startup_text_sync": 0.1,
            "server_stop": 0.7,
            "server_stop_process": 0.5,
            "server_stop_temp_cleanup": 0.1,
            "postgres_drop": 0.04,
        }
        self.assertAlmostEqual(run._profile_infrastructure_total(phases), 2.0)
        # A successful or failed text-reconcile parent must win over its
        # child stages even if the outer server_start sample is unavailable.
        # No phase is removed from the detailed metrics; only ranking excludes
        # values that would otherwise be counted twice.
        for parent in ("startup_text_reconcile", "startup_text_reconcile_failed"):
            with self.subTest(parent=parent):
                self.assertAlmostEqual(
                    run._profile_infrastructure_total({
                        parent: 0.6,
                        "startup_text_load_state": 0.1,
                        "startup_text_source_scan": 0.4,
                        "startup_text_sync_failed": 0.1,
                        "startup_text_orphan_cleanup": 0.02,
                        "startup_imports": 0.3,
                    }), 0.9,
                )

        # Partial samples can lose reconciliation/server_start but keep the
        # inclusive sync stage. Its extractor and work children are still
        # nested, including when the parent or one of the stages failed.
        for parent in ("startup_text_sync", "startup_text_sync_failed"):
            with self.subTest(sync_parent=parent):
                self.assertAlmostEqual(
                    run._profile_infrastructure_total({
                        parent: 0.5,
                        "startup_text_extractor_probe": 0.3,
                        "startup_text_work_sync_failed": 0.2,
                        "startup_imports": 0.1,
                    }), 0.6,
                )

        # Parentless fragments still count; nested phases must not vanish
        # when their inclusive measurements are absent from a partial report.
        self.assertAlmostEqual(
            run._profile_infrastructure_total({
                "startup_imports": 0.3,
                "server_stop_process": 0.5,
            }), 0.8,
        )
        self.assertAlmostEqual(
            run._profile_infrastructure_total({
                "startup_imports": 0.3,
                "startup_text_source_scan": 0.4,
                "startup_text_sync_failed": 0.1,
            }), 0.8,
        )
        self.assertAlmostEqual(
            run._profile_infrastructure_total({
                "startup_imports": 0.3,
                "startup_text_extractor_probe": 0.4,
                "startup_text_work_sync_failed": 0.1,
            }), 0.8,
        )

    def test_production_has_no_profile_side_channel_without_opt_in(self):
        library = mock.Mock()
        library.server_env.return_value = {}
        with (
            mock.patch.dict(os.environ, {"PRKS_E2E_PROFILE": "0",
                                         "PRKS_E2E_STARTUP_PROFILE": "1"}),
            mock.patch.object(harness, "find_free_port", return_value=18888),
            mock.patch.object(harness, "provision_library", return_value=library),
            mock.patch.object(harness, "apply_playwright_browser_env"),
            mock.patch.object(harness, "wait_http"),
        ):
            server = harness.AppServer()
            received = {}
            server._spawn = mock.Mock(side_effect=lambda env: received.update(env))
            try:
                server.start()
            finally:
                server.stop()
        self.assertNotIn("PRKS_E2E_STARTUP_PROFILE", received)

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
            "startup_storage_open", "startup_restore", "startup_bind",
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
