"""Regression coverage for Mergify's opt-in overnight unit repetitions."""
import contextlib
import io
import os
import unittest
from pathlib import Path
from unittest import mock

import run_tests


class NightlyUnitRepetitionsTests(unittest.TestCase):
    def test_runs_three_fresh_processes_and_preserves_any_failure(self):
        root = str(Path(__file__).resolve().parents[1])
        logs = io.StringIO()
        with (
            mock.patch.dict(
                os.environ,
                {
                    "PRKS_UNIT_REPETITIONS": "3",
                    "PRKS_JUNIT_REPORT": "test-reports/unit.xml",
                },
            ),
            mock.patch.object(run_tests, "apply_isolated_test_env"),
            mock.patch.object(
                run_tests.subprocess, "call", side_effect=[0, 1, 0]
            ) as spawn,
            contextlib.redirect_stdout(logs),
        ):
            code = run_tests.run_unit_tests(root)
        self.assertEqual(code, 1)
        self.assertEqual(spawn.call_count, 3)
        for index, call in enumerate(spawn.call_args_list, 1):
            args, kwargs = call
            self.assertEqual(args[0], [run_tests.sys.executable, os.path.join(root, "run_tests.py")])
            self.assertEqual(kwargs["cwd"], root)
            child_env = kwargs["env"]
            self.assertNotIn("PRKS_UNIT_REPETITIONS", child_env)
            self.assertEqual(
                child_env["PRKS_JUNIT_REPORT"], f"test-reports/unit-{index}.xml"
            )
        self.assertIn("Unit repetition 2/3: FAIL", logs.getvalue())
        self.assertIn("Unit repetition 3/3: PASS", logs.getvalue())

    def test_all_passes_remain_successful(self):
        with (
            mock.patch.dict(
                os.environ,
                {
                    "PRKS_UNIT_REPETITIONS": "3",
                    "PRKS_JUNIT_REPORT": "test-reports/unit.xml",
                },
            ),
            mock.patch.object(run_tests, "apply_isolated_test_env"),
            mock.patch.object(run_tests.subprocess, "call", return_value=0) as spawn,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(run_tests.run_unit_tests("/tmp/prks"), 0)
        self.assertEqual(spawn.call_count, 3)

    def test_spawn_failure_is_not_hidden_and_later_repetitions_continue(self):
        errors = io.StringIO()
        with (
            mock.patch.dict(
                os.environ,
                {
                    "PRKS_UNIT_REPETITIONS": "3",
                    "PRKS_JUNIT_REPORT": "unit.xml",
                },
            ),
            mock.patch.object(run_tests, "apply_isolated_test_env"),
            mock.patch.object(
                run_tests.subprocess,
                "call",
                side_effect=[OSError("PRIVATE_PATH"), 0, 0],
            ) as spawn,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(errors),
        ):
            self.assertEqual(run_tests.run_unit_tests("/tmp/prks"), 1)
        self.assertEqual(spawn.call_count, 3)
        self.assertIn("OSError", errors.getvalue())
        self.assertNotIn("PRIVATE_PATH", errors.getvalue())

    def test_invalid_repetition_configuration_fails_closed(self):
        for count, report in (("0", "unit.xml"), ("4", "unit.xml"), ("3", "")):
            with (
                self.subTest(count=count, report=report),
                mock.patch.dict(
                    os.environ,
                    {"PRKS_UNIT_REPETITIONS": count, "PRKS_JUNIT_REPORT": report},
                ),
                mock.patch.object(run_tests, "apply_isolated_test_env"),
                mock.patch.object(run_tests.subprocess, "call") as spawn,
            ):
                with self.assertRaises(ValueError):
                    run_tests.run_unit_tests("/tmp/prks")
                spawn.assert_not_called()
