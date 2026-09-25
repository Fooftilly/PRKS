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
