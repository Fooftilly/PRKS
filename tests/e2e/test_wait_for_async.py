"""Parity tests for browser-side wait_for_async (resolved-value semantics).

Uses a blank Chromium page — no PRKS server — so the harness contract can be
checked without paying full AppServer startup. Gated on PRKS_E2E so the default
unit path never downloads or launches Chromium.
"""
from __future__ import annotations

import os
import time
import unittest

from tests.e2e import harness


def load_tests(loader, standard_tests, pattern):
    if os.environ.get("PRKS_E2E") != "1":
        return unittest.TestSuite()
    return standard_tests


class WaitForAsyncParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._pw, cls._browser = harness.require_chromium()

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

    def test_never_settling_promise_times_out_instead_of_hanging(self):
        """Predicate Promise that never settles must hit the caller timeout."""
        started = time.perf_counter()
        with self.assertRaises(AssertionError) as cm:
            harness.wait_for_async(
                self.page,
                "() => new Promise(() => {})",
                timeout=400,
                message="hung-promise",
            )
        elapsed = time.perf_counter() - started
        self.assertGreaterEqual(elapsed, 0.35)
        self.assertLess(elapsed, 2.0, "evaluate must not hang past the deadline")
        text = str(cm.exception)
        self.assertIn("hung-promise after 0.4s", text)
        self.assertIn("last value was None", text)

    def test_offline_metadata_pending_fails_promptly_on_never_settling_store(self):
        """OfflineWorkMetadata pending() must not hang when listOperations never settles."""
        from tests.e2e.test_work_metadata_offline import OfflineWorkMetadataTests

        self.page.evaluate(
            """() => {
                window.prksSync = {
                    store: { listOperations: () => new Promise(() => {}) },
                };
            }"""
        )
        helper = OfflineWorkMetadataTests(
            "test_offline_metadata_pending_fails_promptly_on_never_settling_store"
        )
        started = time.perf_counter()
        with self.assertRaises(AssertionError) as cm:
            helper.pending(self.page, 1, timeout=400)
        elapsed = time.perf_counter() - started
        self.assertGreaterEqual(elapsed, 0.35)
        self.assertLess(elapsed, 2.0, "pending must not hang past its timeout")
        self.assertIn("Sync did not settle after 0.4s", str(cm.exception))

    def test_offline_metadata_settled_conflicts_fails_promptly_on_never_settling_store(self):
        from tests.e2e.test_work_metadata_offline import OfflineWorkMetadataTests

        self.page.evaluate(
            """() => {
                window.prksSync = {
                    store: { listOperations: () => new Promise(() => {}) },
                };
            }"""
        )
        helper = OfflineWorkMetadataTests(
            "test_offline_metadata_settled_conflicts_fails_promptly_on_never_settling_store"
        )
        started = time.perf_counter()
        with self.assertRaises(AssertionError) as cm:
            helper.settled_conflicts(self.page, 1, timeout=400)
        elapsed = time.perf_counter() - started
        self.assertGreaterEqual(elapsed, 0.35)
        self.assertLess(elapsed, 2.0, "settled_conflicts must not hang past its timeout")
        self.assertIn("No conflict settled after 0.4s", str(cm.exception))

    def test_offline_metadata_operations_fails_promptly_on_never_settling_store(self):
        from tests.e2e.test_work_metadata_offline import OfflineWorkMetadataTests

        self.page.evaluate(
            """() => {
                window.prksSync = {
                    store: { listOperations: () => new Promise(() => {}) },
                };
            }"""
        )
        helper = OfflineWorkMetadataTests(
            "test_offline_metadata_operations_fails_promptly_on_never_settling_store"
        )
        started = time.perf_counter()
        with self.assertRaises(AssertionError) as cm:
            helper.operations(self.page, timeout=400)
        elapsed = time.perf_counter() - started
        self.assertGreaterEqual(elapsed, 0.35)
        self.assertLess(elapsed, 2.0, "operations must not hang past its timeout")
        self.assertIn("listOperations did not settle after 0.4s", str(cm.exception))

    def test_offline_metadata_operations_returns_empty_rows(self):
        """Empty durable queue must still return [] (truthiness sentinel)."""
        from tests.e2e.test_work_metadata_offline import OfflineWorkMetadataTests

        self.page.evaluate(
            """() => {
                window.prksSync = {
                    store: { listOperations: () => Promise.resolve([]) },
                };
            }"""
        )
        helper = OfflineWorkMetadataTests(
            "test_offline_metadata_operations_returns_empty_rows"
        )
        self.assertEqual(helper.operations(self.page, timeout=1000), [])

    def test_direct_expression_re_evaluates_each_poll(self):
        """Non-function expressions must not freeze the first eval's value."""
        self.page.evaluate(
            "() => { window.__ready = false; setTimeout(() => { window.__ready = 'yes'; }, 120); }"
        )
        started = time.perf_counter()
        result = harness.wait_for_async(
            self.page,
            "window.__ready",
            timeout=2000,
        )
        elapsed = time.perf_counter() - started
        self.assertEqual(result, "yes")
        self.assertGreaterEqual(elapsed, 0.10)

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

    def test_fast_false_polls_do_not_accumulate_race_timers(self):
        """Per-poll deadline timers must be cleared when the predicate settles."""
        self.page.evaluate(
            """() => {
                const realSet = window.setTimeout.bind(window);
                const realClear = window.clearTimeout.bind(window);
                const pending = new Set();
                window.__prksTimerPeak = 0;
                window.__prksTimerPending = () => pending.size;
                window.setTimeout = (fn, delay, ...rest) => {
                    const id = realSet((...args) => {
                        pending.delete(id);
                        fn(...args);
                    }, delay, ...rest);
                    pending.add(id);
                    if (pending.size > window.__prksTimerPeak) {
                        window.__prksTimerPeak = pending.size;
                    }
                    return id;
                };
                window.clearTimeout = (id) => {
                    pending.delete(id);
                    return realClear(id);
                };
                window.__polls = 0;
            }"""
        )
        # ~30 quick false polls (50ms sleep between) then success — without
        # clearTimeout the race timers would peak near the poll count.
        result = harness.wait_for_async(
            self.page,
            "() => { window.__polls += 1; return window.__polls > 30; }",
            timeout=10000,
        )
        self.assertIs(result, True)
        peak, pending, polls = self.page.evaluate(
            "() => [window.__prksTimerPeak, window.__prksTimerPending(), window.__polls]"
        )
        self.assertGreaterEqual(polls, 31)
        # At most one race timer + one inter-poll sleep timer live at once.
        self.assertLessEqual(peak, 3, "race timers leaked; peak=%r polls=%r" % (peak, polls))
        self.assertEqual(pending, 0)


if __name__ == "__main__":
    unittest.main()
