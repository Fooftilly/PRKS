"""Deliberate pass/fail/crash cases used to exercise tests/e2e/run.py itself.

Not named `test_*.py`, so `run_tests.py` discovery never collects it. Nothing
here starts Chromium or a PRKS server; `tests/test_e2e_sharding.py` hands these
IDs to the parallel runner to prove that a passing worker, a failing worker and
a worker that dies without reporting each reach the parent correctly.
"""
from __future__ import annotations

import os
import time
import unittest


class PassingCases(unittest.TestCase):
    def test_first(self):
        self.assertTrue(True)

    def test_second(self):
        self.assertEqual(1, 1)


class FailingCases(unittest.TestCase):
    def test_fails(self):
        self.assertEqual("expected", "actual")

    def test_also_fails(self):
        # Second failure in the same class: used to prove worker failfast stops
        # the shard after the first assertion rather than running every ID.
        self.assertEqual("also-expected", "also-actual")


class CrashingCases(unittest.TestCase):
    def test_kills_the_worker(self):
        # Leaves no unittest result behind: the worker process simply vanishes.
        os._exit(3)


class HangingCases(unittest.TestCase):
    def test_never_finishes(self):
        # Exercises the per-test watchdog under a short PRKS_E2E_TEST_WATCHDOG.
        # Must not be collected by ordinary discovery (this module is not test_*).
        time.sleep(3600)


class FailFastSiblingCases(unittest.TestCase):
    """One worker fails only after its sibling has an active test id.

    Used to prove --fail-fast cancellation does not record the stopped sibling
    as a failed test. Not collected by ordinary discovery.
    """

    def test_runs_until_cancelled(self):
        sentinel = os.environ.get("PRKS_E2E_CANCEL_SENTINEL")
        if sentinel:
            with open(sentinel, "w", encoding="utf-8") as handle:
                handle.write("started")
        time.sleep(30)

    def test_fails_once_sibling_is_active(self):
        sentinel = os.environ.get("PRKS_E2E_CANCEL_SENTINEL")
        if sentinel:
            deadline = time.time() + 15
            while time.time() < deadline and not os.path.exists(sentinel):
                time.sleep(0.05)
        self.assertEqual("expected", "actual")
