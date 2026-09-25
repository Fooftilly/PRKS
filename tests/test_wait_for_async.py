"""Parity tests for browser-side wait_for_async (resolved-value semantics).

Uses a blank Chromium page — no PRKS server — so the harness contract can be
checked without paying full E2E startup. Skips when Chromium is unavailable.
"""
from __future__ import annotations

import os
import time
import unittest

from tests.e2e import harness


def _chromium_or_skip(test_case: unittest.TestCase):
    try:
        return harness.require_chromium()
    except Exception as exc:
        raise unittest.SkipTest("Chromium unavailable: %s" % exc) from exc


class WaitForAsyncParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._pw, cls._browser = _chromium_or_skip(cls)

    @classmethod
    def tearDownClass(cls):
        try:
            cls._browser.close()
        finally:
            cls._pw.stop()

    def setUp(self):
        self.context = self._browser.new_context()
        self.page = self.context.new_page()
        self.page.goto("about:blank")

    def tearDown(self):
        self.context.close()

    def test_awaits_resolved_promise_value_not_promise_object(self):
        """The wait_for_function trap: Promise.resolve(false) must not pass."""
        self.page.evaluate(
            "() => { window.__flip = false; setTimeout(() => { window.__flip = true; }, 120); }"
        )
        started = time.perf_counter()
        result = harness.wait_for_async(
            self.page,
            "() => Promise.resolve(window.__flip)",
            timeout=2000,
        )
        elapsed = time.perf_counter() - started
        self.assertIs(result, True)
        self.assertGreaterEqual(elapsed, 0.10)

    def test_returns_truthy_resolved_value_and_arg(self):
        result = harness.wait_for_async(
            self.page,
            "(id) => Promise.resolve(id === 'x' ? 'match' : '')",
            arg="x",
            timeout=1000,
        )
        self.assertEqual(result, "match")

    def test_empty_sentinel_and_zero_remain_failure(self):
        with self.assertRaises(AssertionError) as empty_cm:
            harness.wait_for_async(
                self.page,
                "() => Promise.resolve('')",
                timeout=150,
                message="empty-sentinel",
            )
        self.assertIn("empty-sentinel", str(empty_cm.exception))
        self.assertIn("last value was ''", str(empty_cm.exception))

        with self.assertRaises(AssertionError) as zero_cm:
            harness.wait_for_async(self.page, "() => 0", timeout=150)
        self.assertIn("last value was 0", str(zero_cm.exception))

        with self.assertRaises(AssertionError) as list_cm:
            harness.wait_for_async(self.page, "() => []", timeout=150)
        self.assertIn("last value was []", str(list_cm.exception))

    def test_timeout_message_names_expression_and_last_value(self):
        with self.assertRaises(AssertionError) as cm:
            harness.wait_for_async(
                self.page,
                "() => Promise.resolve(false)",
                timeout=200,
                message="queue-still-busy",
            )
        text = str(cm.exception)
        self.assertIn("queue-still-busy after 0.2s", text)
        self.assertIn("last value was False", text)
        self.assertIn("() => Promise.resolve(false)", text)

    def test_profiles_async_wait_phase(self):
        previous = os.environ.get("PRKS_E2E_PROFILE")
        os.environ["PRKS_E2E_PROFILE"] = "1"
        try:
            harness.start_test_profile("wait-parity")
            try:
                harness.wait_for_async(self.page, "() => true", timeout=1000)
            finally:
                profile = harness.finish_test_profile("wait-parity")
        finally:
            if previous is None:
                os.environ.pop("PRKS_E2E_PROFILE", None)
            else:
                os.environ["PRKS_E2E_PROFILE"] = previous
        self.assertIn("async_wait", profile)
        self.assertGreater(profile["async_wait"], 0.0)

    def test_single_evaluate_round_trip_for_delayed_ready(self):
        """Browser-side polling must not re-enter page.evaluate every 50ms."""
        calls = {"n": 0}
        real_evaluate = self.page.evaluate

        def counting_evaluate(expression, arg=None):
            calls["n"] += 1
            return real_evaluate(expression, arg)

        self.page.evaluate = counting_evaluate  # type: ignore[method-assign]
        self.page.evaluate(
            "() => { window.__ready = false; setTimeout(() => { window.__ready = true; }, 350); }"
        )
        setup_calls = calls["n"]
        calls["n"] = 0
        result = harness.wait_for_async(
            self.page,
            "() => window.__ready",
            timeout=2000,
        )
        self.assertIs(result, True)
        self.assertEqual(calls["n"], 1, "expected one evaluate; setup used %d" % setup_calls)


if __name__ == "__main__":
    unittest.main()
