"""Non-browser tests for the E2E harness diagnostics, Chromium holder and navigation probe.

None of them opens a database: the heartbeat and diagnostic output, the
Chromium recycle policy and the manual-only Person recent probe are pure
harness behaviour.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.e2e import harness


class E2EDiagnosticAndChromiumHolderTests(unittest.TestCase):
    def setUp(self):
        self._diag = os.environ.get("PRKS_E2E_DIAGNOSTIC")
        self._recycle = os.environ.get("PRKS_E2E_CHROMIUM_RECYCLE_EVERY")
        os.environ.pop("PRKS_E2E_DIAGNOSTIC", None)
        os.environ.pop("PRKS_E2E_CHROMIUM_RECYCLE_EVERY", None)
        harness.clear_e2e_heartbeat()
        harness.set_heartbeat_file(None)

    def tearDown(self):
        harness.clear_e2e_heartbeat()
        harness.set_heartbeat_file(None)
        if self._diag is None:
            os.environ.pop("PRKS_E2E_DIAGNOSTIC", None)
        else:
            os.environ["PRKS_E2E_DIAGNOSTIC"] = self._diag
        if self._recycle is None:
            os.environ.pop("PRKS_E2E_CHROMIUM_RECYCLE_EVERY", None)
        else:
            os.environ["PRKS_E2E_CHROMIUM_RECYCLE_EVERY"] = self._recycle

    def test_e2e_diag_is_silent_unless_enabled(self):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            harness.e2e_diag("START", "tests.e2e.fake.T.test_x")
        self.assertEqual(buf.getvalue(), "")

        os.environ["PRKS_E2E_DIAGNOSTIC"] = "1"
        buf = io.StringIO()
        with redirect_stdout(buf):
            harness.e2e_diag("SERVER_READY", "tests.e2e.fake.T.test_x")
        self.assertIn("[e2e-diag] SERVER_READY tests.e2e.fake.T.test_x", buf.getvalue())

    def test_heartbeat_tracks_stage_without_printing(self):
        import io
        from contextlib import redirect_stdout

        harness.clear_e2e_heartbeat()
        buf = io.StringIO()
        with redirect_stdout(buf):
            harness.e2e_heartbeat("START", "tests.e2e.fake.T.test_hang", begin_test=True)
            harness.e2e_heartbeat("SERVER_READY")
        self.assertEqual(buf.getvalue(), "")
        snap = harness.get_e2e_heartbeat()
        self.assertEqual(snap["test_id"], "tests.e2e.fake.T.test_hang")
        self.assertEqual(snap["stage"], "SERVER_READY")
        self.assertGreater(snap["test_started_mono"], 0)
        started = snap["test_started_mono"]
        harness.e2e_heartbeat("STOP", "tests.e2e.fake.T.test_hang", end_test=True)
        after_stop = harness.get_e2e_heartbeat()
        self.assertEqual(after_stop["test_id"], "")
        self.assertEqual(after_stop["stage"], "BETWEEN_TESTS")
        self.assertGreater(after_stop["test_started_mono"], 0)
        self.assertGreaterEqual(after_stop["test_started_mono"], started)

    def test_recycle_diag_does_not_replace_test_id_or_reset_timer(self):
        """Codex/Qodo: only begin_test replaces the tracked unittest id."""
        harness.clear_e2e_heartbeat()
        harness.e2e_heartbeat(
            "START", "tests.e2e.fake.T.test_meta", begin_test=True
        )
        before = harness.get_e2e_heartbeat()
        # Simulate ChromiumHolder.recycle stage-only diagnostic.
        harness.e2e_diag("CHROMIUM_RECYCLE after_1_contexts")
        # Also the old mistaken shape: synthetic label as second arg.
        harness.e2e_diag("CHROMIUM_RECYCLE", "after_1_contexts")
        after = harness.get_e2e_heartbeat()
        self.assertEqual(after["test_id"], "tests.e2e.fake.T.test_meta")
        self.assertEqual(after["test_started_mono"], before["test_started_mono"])
        self.assertEqual(after["test_started_wall"], before["test_started_wall"])
        self.assertIn("CHROMIUM_RECYCLE", after["stage"])

    def test_module_fixture_arms_watchdog_clock_without_test_id(self):
        harness.clear_e2e_heartbeat()
        harness.e2e_heartbeat("CHROMIUM_LAUNCH")
        snap = harness.get_e2e_heartbeat()
        self.assertEqual(snap["test_id"], "")
        self.assertEqual(snap["stage"], "CHROMIUM_LAUNCH")
        self.assertGreater(snap["test_started_mono"], 0)

    def test_heartbeat_file_is_written_for_parent_poll(self):
        with tempfile.TemporaryDirectory(prefix="prks-hb-") as raw:
            path = Path(raw) / "heartbeat.json"
            harness.set_heartbeat_file(path)
            try:
                harness.e2e_heartbeat(
                    "START", "tests.e2e.fake.T.test_file", begin_test=True
                )
                harness.e2e_heartbeat("APP_READY")
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data["test_id"], "tests.e2e.fake.T.test_file")
                self.assertEqual(data["stage"], "APP_READY")
                self.assertGreater(data["test_started_wall"], 0)
            finally:
                harness.clear_e2e_heartbeat()
                harness.set_heartbeat_file(None)

    def test_chromium_recycle_every_env_and_default(self):
        self.assertEqual(harness.chromium_recycle_every(default=20), 20)
        os.environ["PRKS_E2E_CHROMIUM_RECYCLE_EVERY"] = "15"
        self.assertEqual(harness.chromium_recycle_every(default=20), 15)
        os.environ["PRKS_E2E_CHROMIUM_RECYCLE_EVERY"] = "0"
        self.assertEqual(harness.chromium_recycle_every(default=20), 0)

    def test_chromium_holder_recycles_lazily_on_next_get_browser(self):
        launches = []

        def fake_require():
            launches.append(1)
            return ("pw-%d" % len(launches), "browser-%d" % len(launches))

        with mock.patch.object(harness, "require_chromium", side_effect=fake_require):
            holder = harness.ChromiumHolder(recycle_every=2)
            self.assertEqual(len(launches), 1)
            holder.browser = mock.Mock()
            holder.pw = mock.Mock()
            holder.after_context_closed()
            self.assertEqual(len(launches), 1)
            self.assertFalse(holder._needs_recycle)
            holder.after_context_closed()
            self.assertEqual(len(launches), 1)
            self.assertTrue(holder._needs_recycle)
            browser = holder.get_browser()
            self.assertEqual(len(launches), 2)
            self.assertIs(browser, holder.browser)
            self.assertFalse(holder._needs_recycle)
            self.assertEqual(holder._contexts_since_launch, 0)

    def test_chromium_holder_close_skips_pending_relaunch(self):
        launches = []

        def fake_require():
            launches.append(1)
            return ("pw-%d" % len(launches), "browser-%d" % len(launches))

        with mock.patch.object(harness, "require_chromium", side_effect=fake_require):
            holder = harness.ChromiumHolder(recycle_every=1)
            holder.browser = mock.Mock()
            holder.pw = mock.Mock()
            holder.after_context_closed()
            self.assertTrue(holder._needs_recycle)
            self.assertEqual(len(launches), 1)
            holder.close()
            self.assertEqual(len(launches), 1)
            self.assertFalse(holder._needs_recycle)
            self.assertIsNone(holder.browser)
            self.assertIsNone(holder.pw)

    def test_chromium_holder_failed_recycle_keeps_retry_flag(self):
        """A failed relaunch must not clear _needs_recycle or leave stale refs."""
        launches = []

        def fake_require():
            launches.append(1)
            if len(launches) == 1:
                return ("pw-1", "browser-1")
            raise RuntimeError("chromium relaunch failed")

        with mock.patch.object(harness, "require_chromium", side_effect=fake_require):
            holder = harness.ChromiumHolder(recycle_every=1)
            old_browser = mock.Mock()
            old_pw = mock.Mock()
            holder.browser = old_browser
            holder.pw = old_pw
            holder.after_context_closed()
            self.assertTrue(holder._needs_recycle)
            with self.assertRaises(RuntimeError):
                holder.get_browser()
            self.assertEqual(len(launches), 2)
            self.assertTrue(holder._needs_recycle)
            self.assertIsNone(holder.browser)
            self.assertIsNone(holder.pw)
            old_browser.close.assert_called()
            old_pw.stop.assert_called()

            # Next call retries relaunch instead of returning the closed browser.
            def recover_require():
                launches.append(1)
                return ("pw-ok", "browser-ok")

            with mock.patch.object(harness, "require_chromium", side_effect=recover_require):
                browser = holder.get_browser()
            self.assertEqual(browser, "browser-ok")
            self.assertFalse(holder._needs_recycle)
            self.assertEqual(holder.browser, "browser-ok")


class PersonRecentActionabilityProbeTests(unittest.TestCase):
    """The manual-only probe never changes ordinary E2E navigation."""

    @staticmethod
    def _tab_calls(profile_prefix, enabled):
        from tests.e2e.test_app import _open_work_from_home

        page = mock.Mock()
        recent = mock.Mock()

        def locator(selector, **_kwargs):
            if selector == '.prks-folder-library__tab-btn[data-tab="recently-added"]':
                return recent
            return mock.Mock()

        page.locator.side_effect = locator
        with mock.patch.dict(
            os.environ,
            {"PRKS_E2E_PERSON_RECENT_TRIAL": "1" if enabled else "0"},
        ):
            _open_work_from_home(
                page, "Synthetic Work", profile_prefix=profile_prefix
            )
        # Original Work selection and readiness checks are still executed.
        page.wait_for_function.assert_called()
        return recent.click.call_args_list

    def test_default_and_unrelated_navigation_have_one_real_click(self):
        for prefix in (None, "person_create", "person_edit", "other"):
            with self.subTest(prefix=prefix):
                self.assertEqual(
                    self._tab_calls(prefix, enabled=False), [mock.call()]
                )
                if prefix not in ("person_create", "person_edit"):
                    self.assertEqual(
                        self._tab_calls(prefix, enabled=True), [mock.call()]
                    )

    def test_opt_in_person_probe_precedes_the_real_click(self):
        for prefix in ("person_create", "person_edit"):
            with self.subTest(prefix=prefix):
                self.assertEqual(
                    self._tab_calls(prefix, enabled=True),
                    [mock.call(trial=True), mock.call()],
                )


if __name__ == "__main__":
    unittest.main()
