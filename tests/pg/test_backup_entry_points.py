"""Backup and restore entry points: ``backup.create``, recovery task, ``prks admin backup|restore`` (L-B-1 #639).

The job is queued by an ``ADMIN_LIBRARY`` command (owners only, once per
Library at a time) and writes a verified archive into the root's private
archive directory; the idle task recovers expired runs and removes archives
no published run owns. The admin commands back up and restore after the
start check, and a restore through the CLI round-trips a Library.
"""

from __future__ import annotations

import errno
import io
import os
import shutil
import tempfile
import threading
import time
import stat
import uuid
from datetime import timedelta
from typing import Any
from unittest import mock

import psycopg

from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.db.errors import DatabaseError
from backend.domain.identity import admin
from backend.domain.identity.authorization import AccessRefused
from backend.domain.maintenance import jobs as backup_jobs
from backend.jobs import queue
from backend.jobs.handlers import HandlerRegistry
from backend.jobs.runner import JobRun
from tests.pg.test_backup_export import BackupTestCase
from tests.pg.test_root_pairing import OWNER, RootPairingTestCase


class BackupJobTests(BackupTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.lane = backup_jobs.BackupMaintenance(self.objects, self.storage.root,
                                                  recover_every=timedelta(0))

    def claim(self) -> JobRun:
        self.sql("DELETE FROM prks.jobs WHERE kind <> %s", (backup_jobs.BACKUP_KIND,))
        job = queue.claim(self.db, "test", lease=timedelta(minutes=5))
        assert job is not None
        return JobRun(self.db, job, lease=timedelta(minutes=5))

    def test_an_owner_queues_one_backup_and_the_handler_publishes_it(self) -> None:
        obj = self.put(b"%PDF backed up")
        self.create(obj)
        first = backup_jobs.request_backup(self.db, self.actor.context)
        self.assertIsNotNone(first)
        self.assertIsNone(backup_jobs.request_backup(self.db, self.actor.context))
        run = self.claim()
        self.lane.backup(run)
        (published,) = self.runs()
        self.assertEqual(published["state"], "published")
        directory = backup_jobs.archive_dir(self.storage.root)
        self.assertEqual(stat.S_IMODE(os.stat(directory).st_mode), 0o700)
        name = f"{published['id']}.prks-backup"
        self.assertEqual(os.listdir(directory), [name])
        progress = self.sql("SELECT progress FROM prks.jobs WHERE id = %s", (first,))[0]["progress"]
        self.assertEqual(progress["backup_run"], str(published["id"]))

    def test_a_backup_requested_while_one_runs_is_not_queued_again(self) -> None:
        first = backup_jobs.request_backup(self.db, self.actor.context)
        run = self.claim()
        self.assertEqual(run.job.id, first)
        self.assertIsNone(backup_jobs.request_backup(self.db, self.actor.context))
        self.lane.backup(run)
        self.assertTrue(queue.complete(self.db, run.job))
        self.assertIsNotNone(backup_jobs.request_backup(self.db, self.actor.context))

    def test_a_request_racing_another_through_its_claim_queues_nothing(self) -> None:
        # A checks and finds nothing, B queues a backup and a worker claims it,
        # then A inserts: without the request lock A's job would be a second backup.
        checked, resume = threading.Event(), threading.Event()
        enqueue = queue.enqueue_library_job
        results: dict[str, Any] = {}

        def paused_enqueue(*args: Any, **kwargs: Any) -> Any:
            if threading.current_thread().name == "A":
                checked.set()
                resume.wait(30)
            return enqueue(*args, **kwargs)

        def request(name: str) -> None:
            results[name] = backup_jobs.request_backup(self.db, self.actor.context)

        with mock.patch.object(backup_jobs.queue, "enqueue_library_job", side_effect=paused_enqueue):
            a = threading.Thread(target=request, args=("A",), name="A")
            a.start()
            self.assertTrue(checked.wait(30))
            b = threading.Thread(target=request, args=("B",), name="B")
            b.start()
            deadline = time.monotonic() + 5
            while b.is_alive() and not self.request_lock_waiters() and time.monotonic() < deadline:
                time.sleep(0.01)
            if not b.is_alive():  # B got through while A was between its check and insert
                self.claim()
            resume.set()
            a.join(30)
            b.join(30)
        jobs = self.sql("SELECT state FROM prks.jobs WHERE kind = %s", (backup_jobs.BACKUP_KIND,))
        self.assertEqual(len(jobs), 1, f"one backup, not one per request: {results}")
        self.assertEqual(sum(value is not None for value in results.values()), 1)

    def request_lock_waiters(self) -> int:
        return int(self.sql("SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory' AND NOT granted")[0]["n"])

    def test_readers_cannot_queue_a_backup(self) -> None:
        with self.assertRaises(AccessRefused):
            backup_jobs.request_backup(self.db, self.reader_context())

    def test_attach_registers_the_job_and_the_idle_task(self) -> None:
        registry = HandlerRegistry()
        backup_jobs.attach(registry, self.objects, self.storage.root)
        self.assertIn(backup_jobs.BACKUP_KIND, registry.kinds())
        self.assertEqual(len(registry.idle_tasks()), 1)

    def test_a_database_outage_in_recovery_is_a_database_error_the_worker_survives(self) -> None:
        down = psycopg.OperationalError("connection refused")
        with mock.patch.object(backup_jobs, "recover_now", side_effect=down):
            with self.assertRaises(DatabaseError) as caught:
                self.lane.tick(self.db)
        self.assertIs(caught.exception.__cause__, down)
        with mock.patch.object(backup_jobs, "recover_now", side_effect=PermissionError("denied")):
            self.lane.tick(self.db)  # logged; the next interval retries
        self.lane.tick(self.db)

    def test_the_idle_task_recovers_runs_and_removes_unpublished_archives(self) -> None:
        directory = backup_jobs.archive_dir(self.storage.root)
        published = self.backup(library_id=None)
        os.replace(published.path, os.path.join(directory, os.path.basename(published.path)))
        crashed = self.sql("INSERT INTO prks.backup_runs (library_id, locked_until) "
                           "VALUES (%s, now() - interval '1 s') RETURNING id", (self.actor.library_id,))[0]["id"]
        unknown = uuid.uuid4()
        for run_id in (crashed, unknown):
            with open(os.path.join(directory, f"{run_id}.prks-backup"), "wb") as handle:
                handle.write(b"partial")
        with open(os.path.join(directory, "notes.txt"), "wb") as handle:
            handle.write(b"not ours")
        self.lane.tick(self.db)
        self.assertEqual(sorted(os.listdir(directory)),
                         sorted([os.path.basename(published.path), "notes.txt"]))
        states = {row["id"]: row["state"] for row in self.runs()}
        self.assertEqual(states[crashed], "failed")


class RestoreCopyTests(BackupTestCase):
    def test_a_live_copy_is_kept_and_a_dead_ones_copy_is_removed(self) -> None:
        source = os.path.join(self.staging_root(), "archive.prks-backup")
        with open(source, "wb") as handle:
            handle.write(b"archive bytes")
        directory = backup_jobs.restore_dir(self.storage.root)
        with backup_jobs.private_copy(source, self.storage.root) as private:
            self.assertEqual(os.path.dirname(private), directory)
            self.assertEqual(stat.S_IMODE(os.stat(private).st_mode), 0o600)
            with open(private, "rb") as handle:
                self.assertEqual(handle.read(), b"archive bytes")
            self.assertEqual(backup_jobs.prune_restore_copies(self.storage.root), 0)
            self.assertTrue(os.path.exists(private))
        self.assertEqual(os.listdir(directory), [])
        # A copy whose process died before its cleanup holds no lock.
        stranded = os.path.join(directory, f"{uuid.uuid4()}.upload")
        with open(stranded, "wb") as handle:
            handle.write(b"left behind")
        with open(os.path.join(directory, "notes.txt"), "wb") as handle:
            handle.write(b"not ours")
        backup_jobs.recover_now(self.conninfo, self.storage.root)
        self.assertEqual(os.listdir(directory), ["notes.txt"])

    def staging_root(self) -> str:
        path = tempfile.mkdtemp(prefix="prks-pg-copy-")
        self.addCleanup(shutil.rmtree, path, True)
        return path


class AdminBackupRestoreTests(RootPairingTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.name, self.root = self.database(), self.new_root("lib")
        self.boot = self.bootstrap(self.name, self.root)
        self.out = os.path.join(self.tmp, "out")
        os.mkdir(self.out)

    def open_db(self) -> Database:
        return Database(DatabaseConfig(conninfo=self.harness.conninfo(self.name), role="test", pool_min=0))

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = admin.main([*argv, "--root", self.root, "--testing"], environ={}, stdout=out, stderr=err,
                          open_database=lambda _env: self.open_db())
        return code, out.getvalue(), err.getvalue()

    def sql(self, statement: str, params: Any = None) -> list[Any]:
        with psycopg.connect(self.harness.conninfo(self.name), autocommit=True) as conn:
            cursor = conn.execute(statement.encode(), params)
            return cursor.fetchall() if cursor.description else []

    def test_backup_then_restore_round_trips_the_library(self) -> None:
        self.sql("INSERT INTO prks.library_settings (library_id, key, value) VALUES (%s, 'theme', 'Before')",
                 (self.boot.library_id,))
        code, out, err = self.run_cli("backup", "--library", self.boot.library_id, "--out", self.out)
        self.assertEqual((code, err), (admin.EXIT_OK, ""), err)
        (archive,) = os.listdir(self.out)
        self.assertIn(archive, out)
        self.sql("UPDATE prks.library_settings SET value = 'After'")
        code, out, err = self.run_cli("restore", "--library", self.boot.library_id,
                                      "--archive", os.path.join(self.out, archive), "--owner", OWNER.login_name)
        self.assertEqual((code, err), (admin.EXIT_OK, ""), err)
        self.assertIn("epoch 2", out)
        self.assertEqual(self.sql("SELECT value FROM prks.library_settings"), [("Before",)])
        # The private copy is gone; nothing is left in the restore staging directory.
        self.assertEqual(os.listdir(os.path.join(self.root, ".prks-maintenance", "restore")), [])

    def test_restore_refusals_name_the_reason(self) -> None:
        code, _out, err = self.run_cli("backup", "--library", self.boot.library_id, "--out", self.out)
        self.assertEqual(code, admin.EXIT_OK, err)
        (archive,) = os.listdir(self.out)
        path = os.path.join(self.out, archive)
        code, _out, err = self.run_cli("restore", "--library", self.boot.library_id, "--archive", path,
                                       "--owner", "nobody")
        self.assertEqual(code, admin.EXIT_REFUSED)
        self.assertIn("No account has that login.", err)
        # A database failure in the owner lookup is not a missing login.
        with mock.patch.object(admin, "_OWNER_ID_SQL", "SELECT missing_column FROM prks.users WHERE %s IS NULL"):
            code, _out, err = self.run_cli("restore", "--library", self.boot.library_id, "--archive", path,
                                           "--owner", OWNER.login_name)
        self.assertEqual(code, admin.EXIT_REFUSED)
        self.assertIn("The database could not be reached", err)
        self.sql("UPDATE prks.libraries SET maintenance = 'rebuild', maintenance_run = gen_random_uuid()")
        code, _out, err = self.run_cli("restore", "--library", self.boot.library_id, "--archive", path,
                                       "--owner", OWNER.login_name)
        self.assertEqual(code, admin.EXIT_REFUSED)
        self.assertIn("(MAINTENANCE)", err)
        code, _out, err = self.run_cli("restore", "--library", self.boot.library_id,
                                       "--archive", os.path.join(self.out, "missing"), "--owner", OWNER.login_name)
        self.assertEqual(code, admin.EXIT_USAGE)

    def test_a_full_disk_while_copying_the_archive_is_a_clean_refusal(self) -> None:
        code, _out, err = self.run_cli("backup", "--library", self.boot.library_id, "--out", self.out)
        self.assertEqual(code, admin.EXIT_OK, err)
        (archive,) = os.listdir(self.out)

        def disk_full(*_args: Any, **_kwargs: Any) -> None:
            raise OSError(errno.ENOSPC, "No space left on device", "/secret/path")

        with mock.patch.object(backup_jobs.shutil, "copyfileobj", side_effect=disk_full):
            code, _out, err = self.run_cli("restore", "--library", self.boot.library_id,
                                           "--archive", os.path.join(self.out, archive), "--owner", OWNER.login_name)
        self.assertEqual(code, admin.EXIT_REFUSED)
        self.assertIn("could not be copied into the data root (ENOSPC)", err)
        self.assertNotIn("/secret/path", err)
        self.assertEqual(os.listdir(backup_jobs.restore_dir(self.root)), [])
        self.assertEqual(self.sql("SELECT count(*) FROM prks.restore_runs")[0][0], 0)

    def test_backup_refuses_a_missing_out_directory_and_an_unknown_library(self) -> None:
        code, _out, _err = self.run_cli("backup", "--library", self.boot.library_id,
                                        "--out", os.path.join(self.out, "nope"))
        self.assertEqual(code, admin.EXIT_USAGE)
        code, _out, err = self.run_cli("backup", "--library", "LIB-" + "0" * 32, "--out", self.out)
        self.assertEqual(code, admin.EXIT_REFUSED)
        self.assertIn("does not exist", err)
