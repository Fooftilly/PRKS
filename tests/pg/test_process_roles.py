"""``prks_app.py migrate`` and ``worker`` (§11.1, §12.2, §12.3; L-D-1 #630).

``migrate`` applies the schema, then runs the start check and bumps the start
generation once under the shared ``root.lock``; before setup is ``ready`` it
never touches the root. The command-line tests run ``prks_app.py`` as Compose
does, in a child process.
"""

from __future__ import annotations

import io
import os
import signal
import subprocess
import sys
import time
from typing import Any

from backend import process_roles
from backend.domain.identity import root_pairing
from backend.storage import root_binding
from backend.storage.file_lock import ExclusiveFileLock
from backend.storage.root_marker import StartState, lease_path
from tests.pg.test_root_pairing import Crash, RootPairingTestCase, crash_at

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class MigrateRoleTests(RootPairingTestCase):
    def run_migrate(self, name: str, root: str, **kwargs: Any) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        kwargs.setdefault("testing", True)
        code = process_roles.run_migrate(
            root=root,
            environ={"PRKS_DATABASE_URL": self.harness.conninfo(name)},
            stdout=out,
            stderr=err,
            **kwargs,
        )
        return code, out.getvalue(), err.getvalue()

    def ready(self, label: str = "pair") -> tuple[str, str]:
        name = self.database(label)
        root = self.new_root()
        lease = root_binding.acquire_shared_root_lease(root)
        try:
            self.bootstrap(name, root, lease=lease)
        finally:
            lease.release()  # nothing keeps the root after setup
        self.close(name)
        return name, root

    # -- before setup ---------------------------------------------------------

    def test_an_empty_database_is_migrated_and_the_root_is_left_alone(self) -> None:
        name = self.harness.create_database("fresh", empty=True)
        self.addCleanup(self.harness.drop_database, name)
        root = os.path.join(self.tmp, "not-created-yet")
        code, out, err = self.run_migrate(name, root)
        self.assertEqual(code, 0, err)
        self.assertIn("migrate: applied", out)
        self.assertIn("setup is not complete", out)
        self.assertFalse(os.path.exists(root))
        # A second run finds nothing to apply and still leaves the root alone.
        code, out, err = self.run_migrate(name, root)
        self.assertEqual(code, 0, err)
        self.assertIn("schema is up to date", out)
        self.assertFalse(os.path.exists(root))

    def test_a_bound_root_is_not_touched_while_setup_is_pending(self) -> None:
        name = self.database()
        root = self.new_root()
        before = self.marker_bytes(root)
        code, _out, err = self.run_migrate(name, root)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.marker_bytes(root), before)

    # -- the start check and bump ------------------------------------------

    def test_every_run_bumps_once_database_first(self) -> None:
        name, root = self.ready()
        self.assertEqual(self.db_state(name).generation, 1)
        for expected in (2, 3):
            code, out, err = self.run_migrate(name, root)
            self.assertEqual(code, 0, err)
            self.assertIn(f"start generation {expected}", out)
            self.assertEqual(self.db_state(name), self.marker(root).start_state)
            self.assertEqual(self.db_state(name).generation, expected)

    def test_runs_while_web_and_worker_hold_the_root_shared(self) -> None:
        name, root = self.ready()
        web, worker = self.lease(root), self.lease(root)
        self.assertTrue(web and worker)
        code, out, err = self.run_migrate(name, root)
        self.assertEqual(code, 0, err)
        self.assertIn("start generation 2", out)

    def test_refused_while_the_root_is_held_exclusively(self) -> None:
        name, root = self.ready()
        lock = ExclusiveFileLock.acquire(lease_path(root))
        self.addCleanup(lock.release)
        code, _out, err = self.run_migrate(name, root)
        self.assertEqual(code, 1)
        self.assertIn("refused to start", err)
        self.assertEqual(self.db_state(name).generation, 1)

    def test_an_older_copy_is_refused_after_the_original_ran(self) -> None:
        name, root = self.ready()
        copy = self.copy_of(name)
        self.assertEqual(self.run_migrate(name, root)[0], 0)
        marker = self.marker_bytes(root)
        code, _out, err = self.run_migrate(copy, root)
        self.assertEqual(code, 1)
        self.assertIn("stale copy", err)
        self.assertEqual(self.marker_bytes(root), marker)
        self.assertEqual(self.db_state(copy).generation, 1)

    def test_a_crash_between_the_two_writes_is_reconciled_by_the_next_start(self) -> None:
        name, root = self.ready()
        db = self.db(name)
        with self.assertRaises(Crash):
            process_roles.start_after_migrate(
                db, root=root, testing=True, faults=crash_at(root_pairing.FAULT_AFTER_DATABASE_BUMP)
            )
        self.close(name)
        # The database is one ahead: no process serves until the marker catches up.
        self.assertEqual(self.db_state(name).generation, 2)
        self.assertEqual(self.marker(root).start_state.generation, 1)
        before_restart = self.copy_of(name)
        code, out, err = self.run_migrate(name, root)
        self.assertEqual(code, 0, err)
        self.assertIn("start generation 3 (reconciled an interrupted start)", out)
        self.assertEqual(self.db_state(name), self.marker(root).start_state)
        # A copy taken before that start is refused against the root afterwards.
        code, _out, err = self.run_migrate(before_restart, root)
        self.assertEqual(code, 1, err)

    def test_a_ready_database_never_adopts_an_unbound_root(self) -> None:
        name, _root = self.ready()
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        code, _out, err = self.run_migrate(name, empty)
        self.assertEqual(code, 1)
        self.assertIn("no prks-root.json", err)
        self.assertEqual(os.listdir(empty), [])
        code, _out, err = self.run_migrate(name, os.path.join(self.tmp, "missing"))
        self.assertEqual(code, 1)
        self.assertIn("does not exist", err)

    def test_another_root_is_refused(self) -> None:
        name, _root = self.ready()
        other = self.new_root("other")
        before = self.marker_bytes(other)
        code, _out, err = self.run_migrate(name, other)
        self.assertEqual(code, 1)
        self.assertIn("belongs to storage root", err)
        self.assertEqual(self.marker_bytes(other), before)

    def test_a_testing_database_is_refused_by_a_non_testing_run(self) -> None:
        name, root = self.ready()
        code, _out, err = self.run_migrate(name, root, testing=False)
        self.assertEqual(code, 1)
        self.assertIn("testing", err)
        self.assertEqual(self.db_state(name), StartState(1, self.marker(root).start_state.token))

    def test_configuration_errors_exit_2_without_detail(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        code = process_roles.run_migrate(
            root=self.tmp, testing=True, environ={}, stdout=out, stderr=err
        )
        self.assertEqual(code, 2)
        self.assertIn("PRKS_DATABASE_URL", err.getvalue())


class CommandLineTests(RootPairingTestCase):
    def environment(self, conninfo: str) -> dict[str, str]:
        env = {
            k: v for k, v in os.environ.items()
            if not k.startswith("PRKS_") or k == "PRKS_TEST_DATABASE_URL"
        }
        url_file = os.path.join(self.tmp, "database_url")
        with open(url_file, "w", encoding="utf-8") as handle:
            handle.write(conninfo + "\n")
        os.chmod(url_file, 0o600)
        env["PRKS_DATABASE_URL_FILE"] = url_file
        return env

    def spawn(self, *args: str, conninfo: str) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, "prks_app.py", *args], cwd=_PROJECT_DIR, env=self.environment(conninfo),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

    def prks_app(self, *args: str, conninfo: str) -> subprocess.CompletedProcess[str]:
        env = self.environment(conninfo)
        return subprocess.run(
            [sys.executable, "prks_app.py", *args],
            cwd=_PROJECT_DIR, env=env, capture_output=True, text=True, timeout=120,
        )

    def test_migrate_reads_the_url_file_and_bumps(self) -> None:
        name = self.database()
        root = self.new_root()
        self.bootstrap(name, root)
        self.close(name)
        proc = self.prks_app("migrate", "--testing", "--storage-root", root, conninfo=self.harness.conninfo(name))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("start generation 2", proc.stdout)
        self.assertNotIn(self.harness.conninfo(name), proc.stdout + proc.stderr)
        self.assertEqual(self.db_state(name), self.marker(root).start_state)

    def test_migrate_refusal_exits_1(self) -> None:
        name = self.database()
        root = self.new_root()
        self.bootstrap(name, root)
        self.close(name)
        other = self.new_root("other")
        proc = self.prks_app("migrate", "--testing", "--storage-root", other, conninfo=self.harness.conninfo(name))
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("refused to start", proc.stderr)
        self.assertNotIn(other, proc.stderr)

    def test_worker_bumps_on_the_selected_root_and_stops_on_sigterm(self) -> None:
        name = self.database()
        root = self.new_root()
        self.bootstrap(name, root)
        self.close(name)
        proc = self.spawn("worker", "--testing", "--storage-root", root, conninfo=self.harness.conninfo(name))
        try:
            deadline = time.monotonic() + 60
            while self.db_state(name).generation < 2:
                self.assertIsNone(proc.poll(), "the worker exited before it started")
                self.assertLess(time.monotonic(), deadline, "the worker never bumped")
                time.sleep(0.2)
            self.assertEqual(self.db_state(name), self.marker(root).start_state)
        finally:
            proc.send_signal(signal.SIGTERM)
            _out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 0, err)
