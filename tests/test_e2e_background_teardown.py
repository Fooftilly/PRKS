"""AppServer.stop_later(): the teardown overlaps the next test, and a failure is never lost."""

from __future__ import annotations

import subprocess
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

    def test_the_failure_is_raised_by_the_next_stop_which_still_stops_its_server(self):
        self._stop_later(_FakeServer(error=OSError("disk")), "tests.e2e.test_x.T.test_failed")
        second = _FakeServer()
        with self.assertRaisesRegex(RuntimeError, "test_failed failed"):
            self._stop_later(second, "tests.e2e.test_x.T.test_next")
        harness.finish_pending_teardown()
        self.assertTrue(second.finished.is_set())

    def test_a_concurrent_waiter_still_sees_the_teardown_being_joined(self):
        release = threading.Event()
        server = _FakeServer(release)
        self._stop_later(server)
        waiter = threading.Thread(target=harness.finish_pending_teardown)
        waiter.start()
        # The first waiter is joining; a second (a signal handler, atexit)
        # must also wait instead of finding nothing pending.
        threading.Timer(0.05, release.set).start()
        harness.finish_pending_teardown()
        self.assertTrue(server.finished.is_set())
        waiter.join(5)
        self.assertIsNone(harness._PENDING_TEARDOWN)

    def test_a_teardown_that_outlives_its_join_stays_awaited(self):
        release = threading.Event()
        server = _FakeServer(release)
        self._stop_later(server)
        with mock.patch.object(harness, "STOP_TIMEOUT_S", 0.01):
            with self.assertRaisesRegex(RuntimeError, "did not finish"):
                harness.finish_pending_teardown()
        self.assertEqual(len(harness._STUCK_TEARDOWNS), 1)
        threading.Timer(0.05, release.set).start()
        with mock.patch.object(harness, "_LIVE_SERVERS", []):
            harness.stop_all_servers()
        self.assertTrue(server.finished.is_set())
        self.assertEqual(harness._STUCK_TEARDOWNS, [])

    def test_the_lock_is_reentrant_for_a_signal_handler(self):
        with harness._TEARDOWN_LOCK:
            harness.stop_all_servers()  # would deadlock on a plain Lock

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


class _Clock:
    """A fake monotonic clock that only moves when a fake join waits."""

    def __init__(self):
        self.now = 100.0
        self.events: list[str] = []

    def monotonic(self):
        return self.now


class _BlockedThread:
    """A teardown thread that never finishes: each join just spends its timeout."""

    def __init__(self, clock: _Clock, name: str):
        self.clock = clock
        self.name = name
        self.timeouts: list[float] = []

    def join(self, timeout=None):
        assert timeout is not None, "a shutdown join must be bounded"
        self.timeouts.append(timeout)
        self.clock.events.append("join " + self.name)
        self.clock.now += timeout

    def is_alive(self):
        return True


class _ShutdownServer:
    """A live server for stop_all_servers(): records what was done to it, and by whom."""

    def __init__(self, clock: _Clock, name: str, live: list):
        self.name = name
        self.live = live
        self.proc = mock.Mock()
        self.proc.poll.return_value = None
        self.proc.terminate.side_effect = lambda: clock.events.append("terminate " + name)
        self.proc.kill.side_effect = lambda: clock.events.append("kill " + name)
        self.finish_timeouts: list[float | None] = []

    def _finish_stop(self, *, drop=True, timeout=None):
        self.finish_timeouts.append(timeout)
        self.live.remove(self)


class ShutdownTests(unittest.TestCase):
    """stop_all_servers() on a signal: one shared budget, and no second owner."""

    def setUp(self):
        harness.finish_pending_teardown()
        self.clock = _Clock()
        self.live: list = []
        patches = [
            mock.patch.object(harness, "_LIVE_SERVERS", self.live),
            mock.patch.object(harness, "_STUCK_TEARDOWNS", []),
            mock.patch.object(harness, "_PENDING_TEARDOWN", None),
            mock.patch.object(harness.time, "monotonic", self.clock.monotonic),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _owned(self, name):
        server = _ShutdownServer(self.clock, name, self.live)
        thread = _BlockedThread(self.clock, name)
        self.live.append(server)
        return server, (thread, "tests.e2e.test_x.T." + name, [], server)

    def test_a_server_owned_by_a_running_teardown_is_killed_not_stopped_twice(self):
        server, record = self._owned("owned")
        harness._STUCK_TEARDOWNS.append(record)
        harness.stop_all_servers(budget_s=1.0)
        # Its teardown thread still owns it: no second _finish_stop from here.
        self.assertEqual(server.finish_timeouts, [])
        self.assertEqual(self.clock.events, ["terminate owned", "join owned", "kill owned"])
        self.assertEqual(harness._STUCK_TEARDOWNS, [record])  # still running, still awaited

    def test_the_pending_teardown_owns_its_server_too(self):
        server, record = self._owned("pending")
        harness._PENDING_TEARDOWN = record
        harness.stop_all_servers(budget_s=1.0)
        self.assertEqual(server.finish_timeouts, [])
        self.assertIn("kill pending", self.clock.events)

    def test_a_signal_while_a_teardown_moves_to_stuck_waits_for_it_once(self):
        # finish_pending_teardown() appends to the stuck list, then clears the
        # pending slot: a signal between the two sees the same record twice.
        server, record = self._owned("moving")
        harness._STUCK_TEARDOWNS.append(record)
        harness._PENDING_TEARDOWN = record
        harness.stop_all_servers(budget_s=4.0)
        self.assertEqual(record[0].timeouts, [4.0])  # one wait, not a repeated one
        self.assertEqual(server.finish_timeouts, [])  # still the teardown's server
        self.assertEqual(self.clock.events, ["terminate moving", "join moving", "kill moving"])

    def test_every_wait_shares_one_budget(self):
        owners = [self._owned(name) for name in ("a", "b", "c")]
        harness._STUCK_TEARDOWNS.extend(record for _server, record in owners)
        free = _ShutdownServer(self.clock, "free", self.live)
        self.live.append(free)
        started = self.clock.now
        harness.stop_all_servers(budget_s=6.0)
        # SIGTERM reaches every live process before anything waits.
        self.assertEqual(self.clock.events[:4], ["terminate a", "terminate b", "terminate c", "terminate free"])
        joins = [t for _server, record in owners for t in record[0].timeouts]
        # The first stuck teardown spends the whole budget; the rest get none.
        self.assertEqual(joins, [6.0, 0.0, 0.0])
        self.assertLessEqual(self.clock.now - started, 6.0)
        # The unowned server is still stopped, with what is left (nothing here).
        self.assertEqual(free.finish_timeouts, [0.0])
        self.assertEqual([e for e in self.clock.events if e.startswith("kill")], ["kill a", "kill b", "kill c"])

    def test_an_unowned_server_gets_half_the_time_left_for_each_signal(self):
        free = _ShutdownServer(self.clock, "free", self.live)
        self.live.append(free)
        self.assertEqual(harness.stop_all_servers(budget_s=8.0), 1)
        self.assertEqual(free.finish_timeouts, [4.0])

    def test_the_default_budget_fits_the_parents_stop_timeout(self):
        from tests.e2e import run

        self.assertLess(harness.SHUTDOWN_BUDGET_S, run.WORKER_STOP_TIMEOUT_S)


class FinishStopTests(unittest.TestCase):
    def test_a_process_that_does_not_exit_keeps_its_database_and_entry(self):
        server = object.__new__(harness.AppServer)
        server.proc = stuck = mock.create_autospec(subprocess.Popen, instance=True)
        stuck.poll.return_value = None
        server._stdout = server._stderr = None
        server.library = mock.Mock()
        server._library_store = None
        server._tmpdir = mock.Mock()
        with mock.patch.object(harness, "_LIVE_SERVERS", [server]) as live:
            with self.assertRaisesRegex(RuntimeError, "did not exit"):
                server._finish_stop()
            self.assertEqual(live, [server])
        self.assertIsNotNone(server.proc)
        server.library.drop.assert_not_called()
        server._tmpdir.cleanup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
