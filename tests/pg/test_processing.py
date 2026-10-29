"""Files for Processing on PostgreSQL: what parity cannot show (L-PR-1 #636).

``tests/test_processing_parity.py`` proves the facade answers as SQLite does.
These tests cover what only PostgreSQL has: no absolute path is stored; the
Library's inbox lock is held across an import's commit and its source
removal and released on every success and failure path; a crash between
the two is finished by the next scan (the file is never offered again, and
a file that replaced it is listed as new); rows a restore holds; the
``processing.scan`` job; Library isolation and authorization; the
isolation of each command; and a Work merge's view of an import.
"""

from __future__ import annotations

import contextvars
import hashlib
import os
import threading
import time
from typing import Any, Optional
from unittest import mock

import psycopg

from backend.db.config import DatabaseConfig
from backend.db.database import Database, Isolation
from backend.db.errors import ConstraintViolationError, PoolExhaustedError
from backend.domain.identity import library_scope
from backend.domain.identity.authorization import AccessRefused, Capability
from backend.domain.identity.library_scope import LibraryScope, library_command
from backend.domain.processing import inbox, jobs
from backend.domain.works import merge
from backend.jobs import queue
from backend.jobs.handlers import HandlerRegistry
from backend.jobs.runner import Outcome, run_job
from backend.pg_facade import PostgresLibrary
from backend.pg_facade import context as facade_context
from tests.pg.test_works_facade import WorksFacadeTestCase

PDF = b"%PDF-1.4\n%inbox\n%%EOF\n"
WAIT = 30.0
LOCK_SQL = "SELECT pg_try_advisory_lock(hashtextextended('prks.inbox:' || %s, 0)) AS got"
UNLOCK_SQL = "SELECT pg_advisory_unlock(hashtextextended('prks.inbox:' || %s, 0))"


class ProcessingTestCase(WorksFacadeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.inbox = self.storage.processing_dir
        os.makedirs(self.inbox, exist_ok=True)

    def put(self, rel_path: str, data: bytes = PDF) -> str:
        path = os.path.join(self.inbox, *rel_path.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def staged(self, rel_path: str) -> str:
        return {row["rel_path"]: row["id"] for row in self.library.scan_processing_files()}[rel_path]

    def row(self, file_id: str) -> Optional[dict[str, Any]]:
        rows = self.sql("SELECT * FROM prks.processing_files WHERE id = %s", (file_id,))
        return rows[0] if rows else None

    def raw(self) -> psycopg.Connection[Any]:
        name = self.sql("SELECT current_database() AS name")[0]["name"]
        conn = psycopg.connect(self.harness.conninfo(name), autocommit=True)
        self.addCleanup(conn.close)
        return conn

    def lock_is_free(self) -> bool:
        """Whether another session can take the Library's inbox lock now (released at once)."""
        with psycopg.connect(self.harness.conninfo(self.sql("SELECT current_database() AS name")[0]["name"]),
                             autocommit=True) as conn:
            got = conn.execute(LOCK_SQL, (self.actor.library_id,)).fetchone()
            assert got is not None
            if got[0]:
                conn.execute(UNLOCK_SQL, (self.actor.library_id,))
            return bool(got[0])

    def crash_after_commit(self, file_id: str) -> dict[str, Any]:
        """Import with the source removal failing, as a crash after COMMIT would leave it."""
        with mock.patch.object(inbox, "_remove", return_value=False):
            return self.library.import_processing_file(file_id)


class StorageTests(ProcessingTestCase):
    def test_no_absolute_path_is_stored(self) -> None:
        columns = {row["column_name"] for row in self.sql(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'prks'"
            " AND table_name = 'processing_files'")}
        self.assertNotIn("abs_path", columns)
        self.put("nested/dir/a.pdf")
        file_id = self.staged("nested/dir/a.pdf")
        row = self.row(file_id)
        assert row is not None
        self.assertEqual(row["rel_path"], "nested/dir/a.pdf")
        for value in row.values():
            if isinstance(value, str):
                self.assertNotIn(self.inbox, value)
                self.assertNotIn(os.path.realpath(self.inbox), value)
        with self.assertRaises(ConstraintViolationError) as ctx:
            self.sql("UPDATE prks.processing_files SET rel_path = %s WHERE id = %s",
                     (os.path.join(self.inbox, "a.pdf"), file_id))
        self.assertEqual(ctx.exception.constraint, "processing_files_rel_path_check")

    def test_an_update_refuses_nul(self) -> None:
        self.put("n.pdf")
        file_id = self.staged("n.pdf")
        with self.assertRaises(ValueError) as ctx:
            self.library.update_processing_file(file_id, {"title": "a\x00b"})
        self.assertEqual(str(ctx.exception), "Processing file fields cannot contain NUL.")

    def test_an_update_is_atomic(self) -> None:
        self.put("u.pdf")
        file_id = self.staged("u.pdf")
        with self.assertRaises(ValueError):
            self.library.update_processing_file(file_id, {"title": "Kept?", "tags": [{"id": "T-" + "0" * 32}]})
        self.assertEqual(self.library.get_processing_file(file_id)["title"], "")


class InboxLockTests(ProcessingTestCase):
    def test_a_pool_of_one_connection_scans_and_imports(self) -> None:
        small = Database(DatabaseConfig(conninfo=self.db.config.conninfo, role="test", pool_min=0, pool_max=1))
        self.addCleanup(small.close)
        library = PostgresLibrary(small, storage=self.storage)
        self.put("one.pdf")
        staged = library.scan_processing_files()
        self.assertEqual([row["rel_path"] for row in staged], ["one.pdf"])
        out = library.import_processing_file(staged[0]["id"])
        self.assertIsNotNone(library.get_work(out["work_id"]))
        self.assertTrue(self.lock_is_free())

    def test_lock_connections_are_bounded_by_the_pool_size(self) -> None:
        small = Database(DatabaseConfig(conninfo=self.db.config.conninfo, role="test", pool_min=0, pool_max=1,
                                        pool_timeout_seconds=0.2))
        self.addCleanup(small.close)
        with small.advisory_lock("hashtextextended('prks.test:' || %s, 0)", ("a",)):
            with self.assertRaises(PoolExhaustedError):
                with small.advisory_lock("hashtextextended('prks.test:' || %s, 0)", ("b",)):
                    pass  # pragma: no cover - no slot is free
            self.assertEqual(small.command(lambda tx: tx.execute("SELECT 1 AS one").fetchone())["one"], 1)
        with small.advisory_lock("hashtextextended('prks.test:' || %s, 0)", ("b",)):
            pass

    def test_the_lock_is_held_across_the_commit_and_the_removal(self) -> None:
        self.put("held.pdf")
        file_id = self.staged("held.pdf")
        seen: list[tuple[bool, Optional[str]]] = []
        real = inbox._remove

        def remove(*args: Any) -> bool:
            row = self.row(file_id)
            seen.append((self.lock_is_free(), None if row is None else row["status"]))
            return real(*args)

        with mock.patch.object(inbox, "_remove", remove):
            self.library.import_processing_file(file_id)
        self.assertEqual(seen, [(False, "imported")])
        self.assertTrue(self.lock_is_free())

    def test_the_lock_is_released_on_every_path(self) -> None:
        def boom(_row: Any, _managed: Any) -> Any:
            raise ValueError("boom")

        self.put("ok.pdf")
        self.put("bad.pdf", b"not a pdf")
        self.put("gone.pdf")
        self.put("fails.pdf")
        self.put("store.pdf")
        staged = {row["rel_path"]: row["id"] for row in self.library.scan_processing_files()}
        self.assertTrue(self.lock_is_free())
        os.remove(os.path.join(self.inbox, "gone.pdf"))
        cases: list[tuple[str, Any]] = [
            ("ok.pdf", None),
            ("bad.pdf", ValueError),
            ("gone.pdf", ValueError),
            ("missing", ValueError),
        ]
        for name, error in cases:
            with self.subTest(case=name):
                file_id = staged.get(name, "PF-" + "0" * 32)
                if error is None:
                    self.library.import_processing_file(file_id)
                else:
                    with self.assertRaises(error):
                        self.library.import_processing_file(file_id)
                self.assertTrue(self.lock_is_free())
        with self.subTest(case="the transaction fails"):
            with mock.patch("backend.pg_facade.processing._new_work", boom):
                with self.assertRaises(ValueError) as ctx:
                    self.library.import_processing_file(staged["fails.pdf"])
            message = "Failed to insert imported file into works table: boom"
            self.assertEqual(str(ctx.exception), message)
            row = self.row(staged["fails.pdf"])
            assert row is not None
            self.assertEqual((row["status"], row["last_error"]), ("error", message))
            self.assertTrue(os.path.isfile(os.path.join(self.inbox, "fails.pdf")))
            self.assertEqual(self.count("works"), 1)
            self.assertTrue(self.lock_is_free())
        with self.subTest(case="an unexpected error"):
            with mock.patch("backend.domain.objects.ingest.store_pdf", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    self.library.import_processing_file(staged["store.pdf"])
            self.assertTrue(self.lock_is_free())
        with self.subTest(case="a failed scan"):
            with mock.patch.object(inbox, "discover", side_effect=OSError("unreadable")):
                with self.assertRaises(OSError):
                    self.library.scan_processing_files()
            self.assertTrue(self.lock_is_free())

    def test_an_import_waits_for_another_holder(self) -> None:
        self.put("w.pdf")
        file_id = self.staged("w.pdf")
        holder = self.raw()
        holder.execute("SELECT pg_advisory_lock(hashtextextended('prks.inbox:' || %s, 0))",
                       (self.actor.library_id,))
        done: list[Any] = []
        errors: list[BaseException] = []
        context = contextvars.copy_context()

        def body() -> None:
            try:
                done.append(context.run(self.library.import_processing_file, file_id))
            except BaseException as exc:  # noqa: BLE001 - asserted below
                errors.append(exc)

        thread = threading.Thread(target=body)
        thread.start()
        try:
            deadline = time.monotonic() + WAIT
            while not self.sql("SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND NOT granted"):
                self.assertLess(time.monotonic(), deadline, "the import never waited for the lock")
                self.assertTrue(thread.is_alive())
                time.sleep(0.01)
            self.assertIsNotNone(self.row(file_id), "nothing is imported while another session holds the lock")
        finally:
            holder.execute(UNLOCK_SQL, (self.actor.library_id,))
            thread.join(WAIT)
        self.assertEqual(errors, [])
        self.assertEqual(len(done), 1)
        self.assertIsNone(self.row(file_id))


class CrashRecoveryTests(ProcessingTestCase):
    def test_a_crash_after_commit_is_finished_by_the_next_scan(self) -> None:
        path = self.put("crash/c.pdf")
        file_id = self.staged("crash/c.pdf")
        out = self.crash_after_commit(file_id)
        row = self.row(file_id)
        assert row is not None
        self.assertEqual(row["status"], "imported")
        self.assertEqual(row["imported_work_id"], out["work_id"])
        self.assertEqual(row["import_sha256"], hashlib.sha256(PDF).hexdigest())
        self.assertTrue(os.path.isfile(path))
        # Not offered again: the list hides it, and a second import answers the Work.
        self.assertEqual(self.library.get_processing_files(), [])
        self.assertEqual(self.library.import_processing_file(file_id), out)
        self.assertEqual(self.count("works"), 1)
        self.assertEqual(self.library.scan_processing_files(), [])
        self.assertFalse(os.path.exists(path))
        self.assertFalse(os.path.isdir(os.path.join(self.inbox, "crash")))
        self.assertIsNone(self.row(file_id))
        self.assertEqual(self.library.scan_processing_files(), [])
        self.assertIsNotNone(self.library.get_work(out["work_id"]))

    def test_a_file_that_replaced_the_import_is_listed_as_new(self) -> None:
        path = self.put("r.pdf")
        file_id = self.staged("r.pdf")
        self.crash_after_commit(file_id)
        replacement = b"%PDF-1.4\n%a different file\n%%EOF\n"
        self.put("r.pdf", replacement)
        staged = self.library.scan_processing_files()
        self.assertEqual([(row["rel_path"], row["status"]) for row in staged], [("r.pdf", "pending")])
        self.assertNotEqual(staged[0]["id"], file_id)
        self.assertIsNone(self.row(file_id))
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), replacement)

    def test_an_unreadable_file_keeps_the_imported_row(self) -> None:
        path = self.put("u.pdf")
        file_id = self.staged("u.pdf")
        self.crash_after_commit(file_id)
        with mock.patch.object(inbox, "file_sha256", return_value=None):
            self.assertEqual(self.library.scan_processing_files(), [])
        row = self.row(file_id)
        assert row is not None
        self.assertEqual(row["status"], "imported")
        self.assertTrue(os.path.isfile(path))
        self.library.scan_processing_files()
        self.assertIsNone(self.row(file_id))
        self.assertFalse(os.path.exists(path))

    def test_a_file_replaced_during_the_import_is_kept_and_listed_as_new(self) -> None:
        path = self.put("race.pdf")
        file_id = self.staged("race.pdf")
        replacement = b"%PDF-1.4\n%written by a scanner meanwhile\n%%EOF\n"
        real = inbox._commit

        def commit_then_replace(*args: Any, **kwargs: Any) -> Any:
            result = real(*args, **kwargs)
            self.put("race.pdf", replacement)
            return result

        with mock.patch.object(inbox, "_commit", commit_then_replace):
            out = self.library.import_processing_file(file_id)
        self.assertIsNotNone(self.library.get_work(out["work_id"]))
        self.assertIsNone(self.row(file_id))
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), replacement)
        staged = self.library.scan_processing_files()
        self.assertEqual([(row["rel_path"], row["status"]) for row in staged], [("race.pdf", "pending")])
        self.assertNotEqual(staged[0]["id"], file_id)

    def test_a_file_replaced_while_it_is_hashed_is_kept(self) -> None:
        path = self.put("swap.pdf")
        file_id = self.staged("swap.pdf")
        self.crash_after_commit(file_id)
        replacement = b"%PDF-1.4\n%arrived while the scan hashed\n%%EOF\n"
        real = inbox.file_sha256

        def hash_then_replace(claimed: str) -> Optional[str]:
            digest = real(claimed)
            self.put("swap.pdf", replacement)  # a new file at the public path
            return digest

        with mock.patch.object(inbox, "file_sha256", hash_then_replace):
            self.library.scan_processing_files()
        self.assertIsNone(self.row(file_id))
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), replacement)
        self.assertEqual([name for name in os.listdir(self.inbox) if name.startswith(inbox.CLAIM_PREFIX)], [])
        staged = self.library.scan_processing_files()
        self.assertEqual([(row["rel_path"], row["status"]) for row in staged], [("swap.pdf", "pending")])

    def test_a_claim_a_crash_left_is_put_back_by_the_next_scan(self) -> None:
        path = self.put("crash.pdf")
        file_id = self.staged("crash.pdf")
        self.crash_after_commit(file_id)
        claimed = inbox._claim(path)  # the process dies here, before the hash
        assert claimed is not None
        self.put("other.pdf")
        pending = self.put("pending.pdf")
        pending_claim = inbox._claim(pending)  # a staged file, claimed the same way
        assert pending_claim is not None
        staged = self.library.scan_processing_files()
        self.assertEqual(sorted(row["rel_path"] for row in staged), ["other.pdf", "pending.pdf"])
        self.assertIsNone(self.row(file_id))
        self.assertFalse(os.path.exists(path))
        self.assertTrue(os.path.isfile(pending))
        self.assertEqual([name for name in os.listdir(self.inbox) if name.startswith(inbox.CLAIM_PREFIX)], [])

    def test_a_claimed_import_is_removed_even_when_its_name_was_taken(self) -> None:
        path = self.put("paper.pdf")
        file_id = self.staged("paper.pdf")
        self.crash_after_commit(file_id)
        claimed = inbox._claim(path)  # the process dies here
        assert claimed is not None
        replacement = b"%PDF-1.4 written after the crash"
        self.put("paper.pdf", replacement)
        staged = self.library.scan_processing_files()
        self.assertEqual([(row["rel_path"], row["status"]) for row in staged], [("paper.pdf", "pending")])
        self.assertIsNone(self.row(file_id))
        self.assertEqual(sorted(os.listdir(self.inbox)), ["paper.pdf"])
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), replacement)

    def test_a_claimed_file_whose_name_was_taken_gets_a_new_name(self) -> None:
        path = self.put("taken.pdf", b"%PDF-1.4 B")
        claimed = inbox._claim(path)
        assert claimed is not None
        self.put("taken.pdf", b"%PDF-1.4 C")
        staged = sorted(row["rel_path"] for row in self.library.scan_processing_files())
        self.assertEqual(len(staged), 2)
        self.assertEqual(staged[1], "taken.pdf")
        self.assertRegex(staged[0], r"^taken \(recovered [0-9a-f]{8}\)\.pdf$")
        with open(os.path.join(self.inbox, staged[0]), "rb") as handle:
            self.assertEqual(handle.read(), b"%PDF-1.4 B")

    def test_a_long_name_is_claimed_and_put_back_under_a_name_that_fits(self) -> None:
        name = "é" * 120 + ".pdf"  # 244 bytes: the full claim name would not fit
        path = self.put(name, b"%PDF-1.4 long")
        claimed = inbox._claim(path)
        assert claimed is not None
        self.assertLessEqual(len(os.fsencode(os.path.basename(claimed))), 255)
        self.assertTrue(os.path.basename(claimed).endswith(".pdf"))
        staged = [row["rel_path"] for row in self.library.scan_processing_files()]
        self.assertEqual(len(staged), 1)
        self.assertTrue(staged[0].startswith("é") and staged[0].endswith(".pdf"))
        with open(os.path.join(self.inbox, staged[0]), "rb") as handle:
            self.assertEqual(handle.read(), b"%PDF-1.4 long")
        self.assertEqual([name for name in os.listdir(self.inbox) if name.startswith(inbox.CLAIM_PREFIX)], [])

    def test_a_long_named_import_is_finished_and_its_claim_removed(self) -> None:
        name = "x" * 240 + ".pdf"
        path = self.put(name)
        file_id = self.staged(name)
        self.crash_after_commit(file_id)
        self.assertIsNotNone(inbox._claim(path))  # the process dies here
        self.assertEqual(self.library.scan_processing_files(), [])
        self.assertIsNone(self.row(file_id))
        self.assertEqual(os.listdir(self.inbox), [])

    def test_a_claim_named_link_is_never_followed(self) -> None:
        outside = os.path.join(os.path.dirname(os.path.realpath(self.inbox)), "outside.pdf")
        with open(outside, "wb") as handle:
            handle.write(b"%PDF-1.4 outside")
        link = os.path.join(self.inbox, f"{inbox.CLAIM_PREFIX}{'0' * 32}-stolen.pdf")
        os.symlink(outside, link)
        self.assertEqual(self.library.scan_processing_files(), [])
        self.assertTrue(os.path.islink(link))
        self.assertFalse(os.path.exists(os.path.join(self.inbox, "stolen.pdf")))
        with open(outside, "rb") as handle:
            self.assertEqual(handle.read(), b"%PDF-1.4 outside")

    def test_an_absent_file_only_loses_the_row(self) -> None:
        path = self.put("a.pdf")
        file_id = self.staged("a.pdf")
        out = self.crash_after_commit(file_id)
        os.remove(path)
        self.assertEqual(self.library.scan_processing_files(), [])
        self.assertIsNone(self.row(file_id))
        self.assertIsNotNone(self.library.get_work(out["work_id"]))

    def test_a_work_delete_keeps_the_row_and_forgets_the_work(self) -> None:
        self.put("d.pdf")
        file_id = self.staged("d.pdf")
        out = self.crash_after_commit(file_id)
        self.library.delete_work_record(out["work_id"])
        row = self.row(file_id)
        assert row is not None
        self.assertEqual((row["status"], row["imported_work_id"]), ("imported", None))
        with self.assertRaises(ValueError) as ctx:
            self.library.import_processing_file(file_id)
        self.assertEqual(str(ctx.exception), "Processing file not found.")
        self.library.scan_processing_files()
        self.assertIsNone(self.row(file_id))


class RestoreHoldTests(ProcessingTestCase):
    def held(self, rel_path: str, sha256: Optional[str]) -> str:
        file_id = "PF-" + os.urandom(16).hex().upper()
        self.sql("INSERT INTO prks.processing_files (library_id, id, rel_path, filename, restore_hold,"
                 " restore_sha256) VALUES (%s, %s, %s, %s, true, %s)",
                 (self.actor.library_id, file_id, rel_path, os.path.basename(rel_path), sha256))
        return file_id

    def test_a_held_row_is_never_deleted_and_is_released_by_its_file(self) -> None:
        file_id = self.held("held.pdf", hashlib.sha256(PDF).hexdigest())
        self.assertEqual([row["id"] for row in self.library.scan_processing_files()], [file_id])
        self.put("held.pdf", b"%PDF-1.4 other bytes")
        self.library.scan_processing_files()
        row = self.row(file_id)
        assert row is not None
        self.assertTrue(row["restore_hold"])
        self.put("held.pdf")
        self.library.scan_processing_files()
        row = self.row(file_id)
        assert row is not None
        self.assertFalse(row["restore_hold"])
        self.assertIsNone(row["restore_sha256"])
        os.remove(os.path.join(self.inbox, "held.pdf"))
        self.assertEqual(self.library.scan_processing_files(), [])

    def test_a_held_row_is_imported_only_with_its_backed_up_file(self) -> None:
        file_id = self.held("paper.pdf", hashlib.sha256(PDF).hexdigest())
        for data in (None, b"%PDF-1.4 a different paper"):
            with self.subTest(file="absent" if data is None else "different"):
                if data is not None:
                    self.put("paper.pdf", data)
                with self.assertRaises(ValueError) as ctx:
                    self.library.import_processing_file(file_id)
                self.assertEqual(str(ctx.exception), inbox.HELD)
                self.assertIsNotNone(self.row(file_id))
                self.assertEqual(self.count("works"), 0)
                if data is not None:
                    self.assertTrue(os.path.isfile(os.path.join(self.inbox, "paper.pdf")))
        self.put("paper.pdf")
        out = self.library.import_processing_file(file_id)
        self.assertIsNotNone(self.library.get_work(out["work_id"]))
        self.assertIsNone(self.row(file_id))
        nohash = self.held("nohash.pdf", None)
        self.put("nohash.pdf")
        with self.assertRaises(ValueError):
            self.library.import_processing_file(nohash)

    def test_a_held_file_changed_after_its_check_is_not_imported(self) -> None:
        expected = hashlib.sha256(PDF).hexdigest()
        file_id = self.held("swap.pdf", expected)
        self.put("swap.pdf", b"%PDF-1.4 written after the check")
        with mock.patch.object(inbox, "file_sha256", return_value=expected):
            with self.assertRaises(ValueError) as ctx:
                self.library.import_processing_file(file_id)
        self.assertEqual(str(ctx.exception), inbox.HELD)
        row = self.row(file_id)
        assert row is not None
        self.assertTrue(row["restore_hold"])
        self.assertEqual(self.count("works"), 0)
        self.assertTrue(os.path.isfile(os.path.join(self.inbox, "swap.pdf")))

    def test_a_hold_without_a_hash_stays(self) -> None:
        file_id = self.held("nohash.pdf", None)
        self.put("nohash.pdf")
        self.library.scan_processing_files()
        os.remove(os.path.join(self.inbox, "nohash.pdf"))
        self.library.scan_processing_files()
        self.assertIsNotNone(self.row(file_id))


class ScanJobTests(ProcessingTestCase):
    def test_the_job_scans_the_librarys_inbox(self) -> None:
        self.put("job.pdf")
        registry = HandlerRegistry()
        self.assertIsNotNone(jobs.attach(registry, self.inbox, self.storage.root))
        enqueued = library_command(self.db, self.actor.context, Capability.EDIT_LIBRARY, jobs.enqueue_scan)
        self.assertIsNotNone(enqueued)
        self.assertIsNone(library_command(self.db, self.actor.context, Capability.EDIT_LIBRARY, jobs.enqueue_scan))
        job = queue.claim(self.db, "processing-test")
        assert job is not None and job.kind == jobs.KIND
        self.assertEqual(run_job(self.db, job, registry=registry), Outcome.SUCCEEDED)
        self.assertEqual([row["rel_path"] for row in self.library.get_processing_files()], ["job.pdf"])
        self.assertTrue(self.lock_is_free())

    def test_a_root_with_a_sqlite_catalogue_is_refused(self) -> None:
        with open(os.path.join(self.storage.root, "prks_data_testing.db"), "wb"):
            pass
        self.assertIsNone(jobs.attach(HandlerRegistry(), self.inbox, self.storage.root))


class AccessTests(ProcessingTestCase):
    def test_another_library_neither_sees_nor_changes_the_file(self) -> None:
        self.put("mine.pdf")
        file_id = self.staged("mine.pdf")
        with facade_context.acting_as(self.other_actor().context):
            self.assertIsNone(self.library.get_processing_file(file_id))
            self.assertIsNone(self.library.get_processing_file_pdf_path(file_id))
            with self.assertRaises(ValueError):
                self.library.update_processing_file(file_id, {"title": "Theirs"})
            with self.assertRaises(ValueError):
                self.library.import_processing_file(file_id)
        self.assertTrue(os.path.isfile(os.path.join(self.inbox, "mine.pdf")))
        self.assertEqual(self.library.get_processing_file(file_id)["title"], "")

    def test_a_reader_may_list_but_not_scan_or_import(self) -> None:
        self.put("r.pdf")
        file_id = self.staged("r.pdf")
        with facade_context.acting_as(self.reader_context()):
            self.assertEqual([row["id"] for row in self.library.get_processing_files()], [file_id])
            with self.assertRaises(AccessRefused):
                self.library.scan_processing_files()
            with self.assertRaises(AccessRefused):
                self.library.import_processing_file(file_id)
            with self.assertRaises(AccessRefused):
                self.library.update_processing_file(file_id, {"title": "x"})
        self.assertTrue(os.path.isfile(os.path.join(self.inbox, "r.pdf")))
        self.assertTrue(self.lock_is_free())


class IsolationTests(ProcessingTestCase):
    def isolations(self, fn: Any) -> list[Isolation]:
        seen: list[Isolation] = []
        real = library_scope.library_command

        def traced(*args: Any, **kwargs: Any) -> Any:
            seen.append(kwargs.get("isolation", Isolation.READ_COMMITTED))
            return real(*args, **kwargs)

        with mock.patch.object(library_scope, "library_command", traced):
            fn()
        return seen

    def test_adding_tags_and_importing_run_serializable(self) -> None:
        self.put("i.pdf")
        file_id = self.staged("i.pdf")
        tag = self.library.add_tag("T", "#111111")
        tag_id = tag["id"] if isinstance(tag, dict) else tag
        self.assertEqual(self.isolations(lambda: self.library.update_processing_file(file_id, {"title": "x"})),
                         [Isolation.READ_COMMITTED])
        self.assertEqual(self.isolations(lambda: self.library.update_processing_file(
            file_id, {"tags": [{"id": tag_id}]})), [Isolation.SERIALIZABLE])
        self.assertEqual(self.isolations(lambda: self.library.import_processing_file(file_id)),
                         [Isolation.SERIALIZABLE, Isolation.READ_COMMITTED])

    def test_a_work_merge_reads_the_imports_of_its_source(self) -> None:
        self.put("m.pdf")
        file_id = self.staged("m.pdf")
        out = self.crash_after_commit(file_id)
        target = self.library.add_work("Target")

        def prepare(scope: LibraryScope) -> merge.MergePlan:
            return merge.prepare(scope, out["work_id"], target)

        plan = library_command(self.db, self.actor.context, Capability.EDIT_LIBRARY, prepare,
                               isolation=Isolation.SERIALIZABLE)
        self.assertEqual(plan.imported_files, (file_id,))
