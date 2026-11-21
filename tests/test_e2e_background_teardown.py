"""AppServer.stop_later(): the teardown overlaps the next test, and a failure is never lost."""

from __future__ import annotations

import threading
import unittest
from unittest import mock

from tests.e2e import harness


class _FakeServer:
    """Stands in for AppServer: only the blocking half of the stop is called."""

    def __init__(self, release: threading.Event | None = None, error: Exception | None = None):
        self.proc = None
        self.finished = threading.Event()
        self.thread_name = ""
        self._release = release
        self._error = error

    def _finish_stop(self, *, drop=True):
        self.thread_name = threading.current_thread().name
        if self._release is not None:
            self._release.wait(5)
        self.finished.set()
        if self._error is not None:
            raise self._error


class BackgroundTeardownTests(unittest.TestCase):
    def setUp(self):
        harness.finish_pending_teardown()
        self.addCleanup(harness.finish_pending_teardown)

    def _stop_later(self, server, test_id="tests.e2e.test_x.T.test_one"):
        with mock.patch.object(harness, "get_e2e_heartbeat", return_value={"test_id": test_id}):
            harness.AppServer.stop_later(server)  # pyright: ignore[reportArgumentType]

    def test_the_teardown_runs_off_the_test_and_is_awaited_once(self):
        release = threading.Event()
        server = _FakeServer(release)
        self._stop_later(server)
        # stop_later returned while the teardown is still blocked.
        self.assertFalse(server.finished.is_set())
        release.set()
        harness.finish_pending_teardown()
        self.assertTrue(server.finished.is_set())
        self.assertEqual(server.thread_name, "prks-e2e-teardown")
        harness.finish_pending_teardown()  # nothing pending: a no-op

    def test_the_next_stop_waits_for_the_previous_teardown(self):
        release = threading.Event()
        first = _FakeServer(release)
        self._stop_later(first)
        threading.Timer(0.05, release.set).start()
        second = _FakeServer()
        self._stop_later(second)
        self.assertTrue(first.finished.is_set())

    def test_a_failed_teardown_is_raised_with_its_test_id(self):
        self._stop_later(_FakeServer(error=OSError("disk")), "tests.e2e.test_x.T.test_failed")
        with self.assertRaisesRegex(RuntimeError, r"teardown of tests\.e2e\.test_x\.T\.test_failed failed") as caught:
            harness.finish_pending_teardown()
        self.assertIsInstance(caught.exception.__cause__, OSError)
        harness.finish_pending_teardown()  # reported once, not again

    def test_the_failure_is_raised_by_the_next_stop(self):
        self._stop_later(_FakeServer(error=OSError("disk")), "tests.e2e.test_x.T.test_failed")
        with self.assertRaisesRegex(RuntimeError, "test_failed failed"):
            self._stop_later(_FakeServer(), "tests.e2e.test_x.T.test_next")

    def test_the_module_cleanup_drains_the_last_teardown(self):
        with mock.patch.object(harness.unittest, "addModuleCleanup") as add, \
                mock.patch.object(harness, "_TEARDOWN_CLEANUP_REGISTERED", False):
            self._stop_later(_FakeServer())
            self._stop_later(_FakeServer())
            add.assert_called_once_with(harness._drain_teardown_for_module)
            harness._drain_teardown_for_module()
            self.assertIsNone(harness._PENDING_TEARDOWN)
            self.assertFalse(harness._TEARDOWN_CLEANUP_REGISTERED)

    def test_stop_all_servers_finishes_the_pending_teardown(self):
        release = threading.Event()
        server = _FakeServer(release)
        self._stop_later(server)
        threading.Timer(0.05, release.set).start()
        with mock.patch.object(harness, "_LIVE_SERVERS", []):
            harness.stop_all_servers()
        self.assertTrue(server.finished.is_set())
        self.assertIsNone(harness._PENDING_TEARDOWN)


if __name__ == "__main__":
    unittest.main()
