"""Coverage for on-demand E2E / suite inventory metrics. No Chromium."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from tests.e2e import inventory as inv
from tests.e2e.sharding import DEFAULT_TEST_SECONDS


def _ids(*tails):
    return ["tests.e2e.test_app.C.%s" % name for name in tails]


class InventoryPureLogicTests(unittest.TestCase):
    def test_by_module_partitions(self):
        ids = [
            "tests.e2e.test_app.A.test_1",
            "tests.e2e.test_app.A.test_2",
            "tests.e2e.test_offline.B.test_x",
        ]
        rows = inv.e2e_by_module(ids)
        self.assertEqual(
            [(r["module"], r["count"]) for r in rows],
            [
                ("tests.e2e.test_app", 2),
                ("tests.e2e.test_offline", 1),
            ],
        )

    def test_feature_overlap_not_treated_as_partition(self):
        # PdfPersistenceTests is owned by both pdf-annotations and work-detail.
        tid = "tests.e2e.test_app.PdfPersistenceTests.test_example"
        features = inv.features_for_test_id(tid)
        self.assertIn("pdf-annotations", features)
        self.assertIn("work-detail", features)
        self.assertGreaterEqual(len(features), 2)

    def test_unmapped_detects_orphan(self):
        orphan = "tests.e2e.test_does_not_exist.OrphanTests.test_x"
        known = [
            "tests.e2e.test_app.AppShellAndNavigationTests.test_app_loads_and_real_navigation",
            orphan,
        ]
        self.assertEqual(inv.unmapped_e2e_ids(known), [orphan])

    def test_estimated_seconds_uses_weights(self):
        ids = _ids("a", "b")
        weights = {ids[0]: 10.0, ids[1]: 2.5}
        self.assertEqual(inv.estimated_seconds_for_ids(ids, weights), 12.5)
        self.assertEqual(
            inv.estimated_seconds_for_ids(["missing.T.test_x"], {}),
            DEFAULT_TEST_SECONDS,
        )

    def test_slowest_modules_sum_weights(self):
        ids = [
            "tests.e2e.test_app.A.test_1",
            "tests.e2e.test_app.A.test_2",
            "tests.e2e.test_offline.B.test_x",
        ]
        weights = {
            ids[0]: 5.0,
            ids[1]: 5.0,
            ids[2]: 9.0,
        }
        rows = inv.slowest_modules(ids, weights, limit=2)
        self.assertEqual(rows[0]["module"], "tests.e2e.test_app")
        self.assertEqual(rows[0]["seconds"], 10.0)
        self.assertEqual(rows[0]["count"], 2)
        self.assertEqual(rows[1]["module"], "tests.e2e.test_offline")

    def test_format_text_mentions_coverage_boundary(self):
        repo = Path(_PROJECT_DIR)
        ids = [
            "tests.e2e.test_app.AppShellAndNavigationTests.test_app_loads_and_real_navigation",
        ]
        with mock.patch.object(inv, "count_python_unit_tests", return_value={
            "total_discovered": 10,
            "unit_api_count": 10,
            "e2e_leaked_into_unit": 0,
            "ux_tour_leaked_into_unit": 0,
        }):
            report = inv.build_inventory(ids, repo=repo, include_node=True)
        text = inv.format_inventory_text(report)
        self.assertIn("Coverage boundaries matter more than raw counts", text)
        self.assertIn("NOT additive", text)
        self.assertIn("Total tests: 1", text)
        self.assertIn("Node browser selftests", text)

    def test_format_json_round_trip(self):
        repo = Path(_PROJECT_DIR)
        ids = [
            "tests.e2e.test_app.AppShellAndNavigationTests.test_app_loads_and_real_navigation",
        ]
        with mock.patch.object(
            inv,
            "count_python_unit_tests",
            return_value={
                "total_discovered": 1,
                "unit_api_count": 1,
                "e2e_leaked_into_unit": 0,
                "ux_tour_leaked_into_unit": 0,
            },
        ):
            report = inv.build_inventory(ids, repo=repo, include_node=False, include_unit=True)
        payload = json.loads(inv.format_inventory_json(report))
        self.assertEqual(payload["e2e"]["total"], 1)
        self.assertIn("coverage_note", payload)

    def test_node_selftest_count_finds_runners(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            browser = root / "tests" / "browser"
            browser.mkdir(parents=True)
            (browser / "run_foo_selftest.js").write_text("//", encoding="utf-8")
            (browser / "run_bar_selftest.js").write_text("//", encoding="utf-8")
            (browser / "helper_selftest.js").write_text("//", encoding="utf-8")
            (browser / "unrelated.js").write_text("//", encoding="utf-8")
            counts = inv.count_node_selftests(root)
            self.assertEqual(counts["runner_count"], 2)
            self.assertEqual(counts["support_module_count"], 1)
            self.assertEqual(
                counts["runners"],
                [
                    "tests/browser/run_bar_selftest.js",
                    "tests/browser/run_foo_selftest.js",
                ],
            )


class InventoryDiscoveryFailureTests(unittest.TestCase):
    def test_collect_test_ids_excludes_failed_test(self):
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromName("tests.this_module_does_not_exist_for_inventory")
        ids, failed = inv.collect_test_ids(suite)
        self.assertEqual(ids, [])
        self.assertEqual(len(failed), 1)
        self.assertTrue(inv._is_failed_test(failed[0]))
        self.assertTrue(loader.errors)

    def test_raise_if_discovery_errors_on_loader_errors(self):
        with self.assertRaises(inv.DiscoveryError) as ctx:
            inv.raise_if_discovery_errors(
                "Python unit/API",
                errors=["Failed to import test module: boom"],
                failed=[],
            )
        self.assertIn("refusing to report inventory counts", str(ctx.exception))
        self.assertEqual(ctx.exception.errors[0], "Failed to import test module: boom")

    def test_raise_if_discovery_errors_on_failed_test_only(self):
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromName("tests.missing_inventory_module_xyz")
        _ids, failed = inv.collect_test_ids(suite)
        with self.assertRaises(inv.DiscoveryError) as ctx:
            inv.raise_if_discovery_errors("E2E", errors=[], failed=failed)
        self.assertTrue(ctx.exception.failed_ids)

    def test_discover_e2e_test_ids_fails_closed(self):
        with self.assertRaises(inv.DiscoveryError) as ctx:
            inv.discover_e2e_test_ids(["tests.e2e.definitely_missing_module_for_inventory"])
        self.assertIn("E2E discovery failed", str(ctx.exception))

    def test_count_python_unit_tests_fails_closed_on_import_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tests_dir = root / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_ok.py").write_text(
                "import unittest\n"
                "class Ok(unittest.TestCase):\n"
                "    def test_pass(self):\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            (tests_dir / "test_broken_import.py").write_text(
                "raise ImportError('deliberate inventory discovery failure')\n",
                encoding="utf-8",
            )
            with self.assertRaises(inv.DiscoveryError) as ctx:
                inv.count_python_unit_tests(root)
            self.assertIn("Python unit/API discovery failed", str(ctx.exception))
            # Must not quietly count the _FailedTest placeholder as a unit test.
            self.assertNotIn("unit_api_count", str(ctx.exception))

    def test_count_python_unit_tests_overrides_live_prks_storage(self):
        sentinel = "/sentinel/production/prks-storage-must-not-be-used"
        previous_storage = os.environ.get("PRKS_STORAGE")
        previous_testing = os.environ.get("PRKS_TESTING")
        os.environ["PRKS_STORAGE"] = sentinel
        os.environ["PRKS_TESTING"] = "0"
        seen = []

        def fake_discover(_loader, start_dir=None, pattern="test*.py", top_level_dir=None):
            storage = os.environ.get("PRKS_STORAGE")
            seen.append(storage)
            if os.environ.get("PRKS_TESTING") != "1":
                raise AssertionError("PRKS_TESTING not forced to 1 during discovery")
            if storage == sentinel:
                raise AssertionError("live PRKS_STORAGE was not overridden")
            if "prks-inventory-unit-" not in str(storage or ""):
                raise AssertionError("expected temp inventory storage, got %r" % storage)
            return unittest.TestSuite()

        try:
            with mock.patch.object(unittest.TestLoader, "discover", fake_discover):
                result = inv.count_python_unit_tests(Path(_PROJECT_DIR))
            self.assertEqual(result["unit_api_count"], 0)
            self.assertEqual(len(seen), 1)
            self.assertEqual(os.environ.get("PRKS_STORAGE"), sentinel)
            self.assertEqual(os.environ.get("PRKS_TESTING"), "0")
        finally:
            if previous_storage is None:
                os.environ.pop("PRKS_STORAGE", None)
            else:
                os.environ["PRKS_STORAGE"] = previous_storage
            if previous_testing is None:
                os.environ.pop("PRKS_TESTING", None)
            else:
                os.environ["PRKS_TESTING"] = previous_testing


class InventoryRunnerWireTests(unittest.TestCase):
    def test_inventory_flag_exits_without_chromium(self):
        previous = os.environ.get("PRKS_E2E")
        os.environ["PRKS_E2E"] = "1"
        try:
            from tests.e2e import run as runner

            buf = io.StringIO()
            with mock.patch.object(runner, "ensure_chromium_installed") as ensure:
                with mock.patch.object(
                    inv,
                    "discover_e2e_test_ids",
                    return_value=[
                        "tests.e2e.test_app.AppShellAndNavigationTests."
                        "test_app_loads_and_real_navigation"
                    ],
                ):
                    with mock.patch.object(
                        inv,
                        "count_python_unit_tests",
                        return_value={
                            "total_discovered": 0,
                            "unit_api_count": 0,
                            "e2e_leaked_into_unit": 0,
                            "ux_tour_leaked_into_unit": 0,
                        },
                    ):
                        with redirect_stdout(buf):
                            code = runner.main(["--inventory"])
            ensure.assert_not_called()
            self.assertEqual(code, 0)
            out = buf.getvalue()
            self.assertIn("PRKS test inventory", out)
            self.assertIn("Total tests:", out)
            self.assertIn("Coverage boundaries matter", out)
        finally:
            if previous is None:
                os.environ.pop("PRKS_E2E", None)
            else:
                os.environ["PRKS_E2E"] = previous

    def test_inventory_json_flag(self):
        previous = os.environ.get("PRKS_E2E")
        os.environ["PRKS_E2E"] = "1"
        try:
            from tests.e2e import run as runner

            buf = io.StringIO()
            with mock.patch.object(runner, "ensure_chromium_installed"):
                with mock.patch.object(
                    inv,
                    "discover_e2e_test_ids",
                    return_value=[
                        "tests.e2e.test_app.AppShellAndNavigationTests."
                        "test_app_loads_and_real_navigation"
                    ],
                ):
                    with mock.patch.object(
                        inv,
                        "count_python_unit_tests",
                        return_value={
                            "total_discovered": 0,
                            "unit_api_count": 0,
                            "e2e_leaked_into_unit": 0,
                            "ux_tour_leaked_into_unit": 0,
                        },
                    ):
                        with redirect_stdout(buf):
                            code = runner.main(["--inventory-json"])
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertGreater(payload["e2e"]["total"], 0)
            self.assertIn("by_feature", payload["e2e"])
        finally:
            if previous is None:
                os.environ.pop("PRKS_E2E", None)
            else:
                os.environ["PRKS_E2E"] = previous

    def test_inventory_exits_nonzero_on_discovery_error(self):
        previous = os.environ.get("PRKS_E2E")
        os.environ["PRKS_E2E"] = "1"
        try:
            from tests.e2e import run as runner

            err = io.StringIO()
            with mock.patch.object(runner, "ensure_chromium_installed"):
                with mock.patch.object(
                    inv,
                    "discover_e2e_test_ids",
                    side_effect=inv.DiscoveryError(
                        "E2E discovery failed; refusing to report inventory counts",
                        errors=["boom"],
                        failed_ids=["unittest.loader._FailedTest.x"],
                    ),
                ):
                    with mock.patch("sys.stderr", err):
                        code = runner.main(["--inventory"])
            self.assertEqual(code, 2)
            self.assertIn("refusing to report inventory counts", err.getvalue())
        finally:
            if previous is None:
                os.environ.pop("PRKS_E2E", None)
            else:
                os.environ["PRKS_E2E"] = previous

    def test_inventory_exits_nonzero_on_unit_discovery_error(self):
        previous = os.environ.get("PRKS_E2E")
        os.environ["PRKS_E2E"] = "1"
        try:
            from tests.e2e import run as runner

            err = io.StringIO()
            with mock.patch.object(runner, "ensure_chromium_installed"):
                with mock.patch.object(
                    inv,
                    "discover_e2e_test_ids",
                    return_value=[
                        "tests.e2e.test_app.AppShellAndNavigationTests."
                        "test_app_loads_and_real_navigation"
                    ],
                ):
                    with mock.patch.object(
                        inv,
                        "count_python_unit_tests",
                        side_effect=inv.DiscoveryError(
                            "Python unit/API discovery failed; refusing",
                            errors=["Failed to import test module: missing_dep"],
                        ),
                    ):
                        with mock.patch("sys.stderr", err):
                            code = runner.main(["--inventory"])
            self.assertEqual(code, 2)
            self.assertIn("Python unit/API discovery failed", err.getvalue())
        finally:
            if previous is None:
                os.environ.pop("PRKS_E2E", None)
            else:
                os.environ["PRKS_E2E"] = previous


if __name__ == "__main__":
    unittest.main()
