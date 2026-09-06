"""Structural guards for UX Tour isolation from ordinary unit/E2E runs.

Fast, no-Chromium tests. They exist to make it impossible for the ~10-minute,
artifact-producing UX Interaction Tour to accidentally enter `python
run_tests.py`, `--e2e`, or `--all` -- and to lock in the handful of contracts
the tour's own safety and retention behavior depends on.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)


class TestRunTestsModeExcludesUxTour(unittest.TestCase):
    def test_default_and_e2e_and_all_modes_never_ux_tour(self):
        from run_tests import parse_mode

        self.assertEqual(parse_mode([]), "unit")
        self.assertEqual(parse_mode(["--e2e"]), "e2e")
        self.assertEqual(parse_mode(["--all"]), "all")

    def test_ux_tour_flag_selects_ux_tour_mode(self):
        from run_tests import parse_mode

        self.assertEqual(parse_mode(["--ux-tour"]), "ux-tour")
        self.assertEqual(parse_mode(["-ux-tour"]), "ux-tour")

    def test_main_invokes_only_the_tour_runner_for_ux_tour_mode(self):
        """--ux-tour must call run_ux_tour() and nothing else (no unit suite,
        no E2E runner) -- and the other three modes must never call it."""
        import run_tests

        with mock.patch.object(run_tests, "run_ux_tour", return_value=0) as ux, mock.patch.object(
            run_tests, "run_unit_tests", return_value=0
        ) as unit, mock.patch.object(run_tests, "run_e2e_tests", return_value=0) as e2e:
            rc = run_tests.main(["--ux-tour"])
            self.assertEqual(rc, 0)
            ux.assert_called_once()
            unit.assert_not_called()
            e2e.assert_not_called()

        for argv in ([], ["--e2e"], ["--all"]):
            with mock.patch.object(run_tests, "run_ux_tour", return_value=0) as ux, mock.patch.object(
                run_tests, "run_unit_tests", return_value=0
            ), mock.patch.object(run_tests, "run_e2e_tests", return_value=0):
                run_tests.main(argv)
                ux.assert_not_called()

    def test_unittest_discovery_of_tests_dir_collects_no_ux_tour_cases(self):
        """The exact discovery run_tests.py's default/--all mode performs
        (unittest.TestLoader().discover(tests/, 'test_*.py')) must yield zero
        UX Tour test ids when PRKS_UX_TOUR is unset -- mirroring how
        tests/e2e/test_app.py stays out via the same load_tests() gate."""
        self.assertNotIn("PRKS_UX_TOUR", os.environ)
        loader = unittest.TestLoader()
        suite = loader.discover(
            start_dir=os.path.join(_PROJECT_DIR, "tests"), pattern="test_*.py"
        )
        ids = []

        def collect(node):
            for item in node:
                if isinstance(item, unittest.TestSuite):
                    collect(item)
                else:
                    ids.append(item.id())

        collect(suite)
        # Match the actual tour suite module, not this runner-guard file itself
        # (test_ux_tour_runner) which legitimately gets collected.
        tour_ids = [i for i in ids if i.startswith("test_tours.")]
        self.assertEqual(tour_ids, [])

    def test_ux_tour_test_module_has_the_load_tests_gate(self):
        path = os.path.join(_PROJECT_DIR, "tests", "ux_tour", "test_tours.py")
        with open(path, encoding="utf-8") as handle:
            src = handle.read()
        self.assertIn("def load_tests(", src)
        self.assertIn('os.environ.get("PRKS_UX_TOUR") != "1"', src)
        self.assertIn("unittest.TestSuite()", src)

    def test_ux_tour_run_script_sets_its_own_env_flag(self):
        path = os.path.join(_PROJECT_DIR, "tests", "ux_tour", "run.py")
        with open(path, encoding="utf-8") as handle:
            src = handle.read()
        self.assertIn('os.environ["PRKS_UX_TOUR"] = "1"', src)


class TestUxTourArtifactIsolation(unittest.TestCase):
    def test_artifacts_root_is_outside_runtime_storage(self):
        from tests.ux_tour import harness

        artifacts_root = str(harness.ARTIFACTS_ROOT)
        self.assertTrue(artifacts_root.startswith(str(harness.REPO)))
        self.assertIn(os.path.join("artifacts", "ux-tour"), artifacts_root)
        # Never inside data/, data_testing/, or any PRKS_STORAGE-shaped path.
        self.assertNotIn("data_testing", artifacts_root)
        self.assertNotIn(os.sep + "data" + os.sep, artifacts_root)

    def test_gitignore_excludes_ux_tour_artifacts(self):
        path = os.path.join(_PROJECT_DIR, ".gitignore")
        with open(path, encoding="utf-8") as handle:
            src = handle.read()
        self.assertIn("artifacts/ux-tour", src)

    def test_app_server_never_targets_repo_data(self):
        """Every UX Tour scenario opens tests.e2e.harness.AppServer, whose
        storage_root is a fresh TemporaryDirectory -- confirm that contract is
        still what open_tour_page relies on (see tests/ux_tour/harness.py)."""
        from tests.e2e.harness import AppServer

        server = AppServer()
        try:
            self.assertNotIn(os.path.join(_PROJECT_DIR, "data"), server.storage_root)
            self.assertNotEqual(server.storage_root, os.environ.get("PRKS_STORAGE"))
            self.assertTrue(os.path.basename(os.path.dirname(server.storage_root)) != "PRKS")
        finally:
            server._tmpdir.cleanup()


class TestUxRecordEnvParsing(unittest.TestCase):
    def test_record_mode_requires_literal_1(self):
        from tests.ux_tour import harness

        old = os.environ.get("PRKS_UX_RECORD")
        try:
            for falsy in (None, "", "0", "false", "False", "yes", "true", "TRUE"):
                if falsy is None:
                    os.environ.pop("PRKS_UX_RECORD", None)
                else:
                    os.environ["PRKS_UX_RECORD"] = falsy
                self.assertFalse(harness.record_mode_enabled(), msg=repr(falsy))
            os.environ["PRKS_UX_RECORD"] = "1"
            self.assertTrue(harness.record_mode_enabled())
        finally:
            if old is None:
                os.environ.pop("PRKS_UX_RECORD", None)
            else:
                os.environ["PRKS_UX_RECORD"] = old

    def test_run_tests_help_documents_ux_tour_and_record_env(self):
        import shutil
        import subprocess

        py = shutil.which("python3") or shutil.which("python") or sys.executable
        proc = subprocess.run(
            [py, os.path.join(_PROJECT_DIR, "run_tests.py"), "--help"],
            cwd=_PROJECT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertIn("--ux-tour", proc.stdout)
        self.assertIn("PRKS_UX_RECORD", proc.stdout)


class _FakePage:
    """Minimal stand-in for a Playwright Page: only the surface PageCollector.__init__
    touches (page.on(...) / page.route(...)), so TourCollector can be constructed and
    exercised without a real browser."""

    def on(self, *_args, **_kwargs):
        pass

    def route(self, *_args, **_kwargs):
        pass


class _FakeRequest:
    def __init__(self, method):
        self.method = method


class _FakeResponse:
    def __init__(self, url, status, method="GET"):
        self.url = url
        self.status = status
        self.request = _FakeRequest(method)


class TestTourCollectorHttpStatusClassification(unittest.TestCase):
    """TourCollector._on_response() must classify 4xx and 5xx disjointly: a 5xx must never
    also land in http_4xx (it would otherwise satisfy an unrelated expect_status() 4xx
    exemption and hide a real server error)."""

    def _collector(self):
        from tests.ux_tour.harness import TourCollector

        return TourCollector(_FakePage(), "http://127.0.0.1:1")

    def test_404_recorded_as_4xx_only(self):
        c = self._collector()
        c._on_response(_FakeResponse("http://127.0.0.1:1/api/x", 404))
        self.assertEqual(c.http_4xx, ["GET /api/x -> 404"])
        self.assertEqual(c.http_5xx, [])

    def test_500_recorded_as_5xx_only(self):
        c = self._collector()
        c._on_response(_FakeResponse("http://127.0.0.1:1/api/x", 500))
        self.assertEqual(c.http_5xx, ["GET /api/x -> 500"])
        self.assertEqual(c.http_4xx, [])

    def test_expected_404_recorded_as_neither(self):
        c = self._collector()
        c.expect_status("/api/x")
        c._on_response(_FakeResponse("http://127.0.0.1:1/api/x", 404))
        self.assertEqual(c.http_4xx, [])
        self.assertEqual(c.http_5xx, [])
        c.assert_clean()  # must not raise: no unexpected 4xx, no 5xx at all

    def test_expected_path_returning_500_still_fails_as_5xx(self):
        """An expect_status() 4xx exemption must never hide a 5xx on that same path."""
        c = self._collector()
        c.expect_status("/api/x")
        c._on_response(_FakeResponse("http://127.0.0.1:1/api/x", 500))
        self.assertEqual(c.http_5xx, ["GET /api/x -> 500"])
        self.assertEqual(c.http_4xx, [])
        with self.assertRaises(AssertionError):
            c.assert_clean()


class TestManifestRetentionTruthfulness(unittest.TestCase):
    """A passing scenario's directory is deleted in default (non-recording) mode -- the
    manifest entry must say so rather than pointing at files that no longer exist."""

    def _entry(self):
        return {
            "name": "example",
            "status": "PASS",
            "checkpoints": ["01-a.png", "02-b.png"],
            "video": "video.webm",
            "trace": "trace.zip",
        }

    def test_non_retained_entry_nulls_paths_and_says_so(self):
        from tests.ux_tour.harness import apply_retention

        entry = self._entry()
        apply_retention(entry, retained=False)
        self.assertEqual(entry["artifacts_retained"], False)
        self.assertIsNone(entry["video"])
        self.assertIsNone(entry["trace"])
        self.assertEqual(entry["checkpoints"], [])

    def test_retained_entry_keeps_real_paths(self):
        from tests.ux_tour.harness import apply_retention

        entry = self._entry()
        apply_retention(entry, retained=True)
        self.assertEqual(entry["artifacts_retained"], True)
        self.assertEqual(entry["video"], "video.webm")
        self.assertEqual(entry["trace"], "trace.zip")
        self.assertEqual(entry["checkpoints"], ["01-a.png", "02-b.png"])

    def test_report_md_pass_line_unaffected_by_retention_fields(self):
        """build_report_md's compact PASS output must stay unchanged regardless of
        artifacts_retained/video/trace/checkpoints -- it never reads those fields for a
        passing scenario."""
        from tests.ux_tour.harness import apply_retention, build_report_md

        retained = {"name": "a", "status": "PASS", "duration": 1.0, **self._entry()}
        not_retained = dict(retained)
        apply_retention(not_retained, retained=False)
        manifest_retained = {"scenarios": [retained], "commit": "abc", "viewport": {"width": 1, "height": 1}}
        manifest_not_retained = {
            "scenarios": [not_retained],
            "commit": "abc",
            "viewport": {"width": 1, "height": 1},
        }
        self.assertEqual(build_report_md(manifest_retained), build_report_md(manifest_not_retained))


if __name__ == "__main__":
    unittest.main()
