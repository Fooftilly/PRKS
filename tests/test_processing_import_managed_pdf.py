"""Processing Inbox import must use the durable managed-PDF store (#169)
and survivor-aware rollback (#152).

Ordinary upload/create already publishes through ``store_new_managed_pdf_bytes``
(exclusive create + content/parent durability, then optional linearize) and
rolls back with ``discard_unowned_managed_pdf`` / ``_remove_managed_pdf``.
Import used to ``shutil.copy2`` and raw ``os.remove``, which could overwrite a
pre-seeded basename and delete bytes a surviving Work still referenced.

These tests pin the unified contract without sleeps: hooks and threading
barriers drive the survivor race.
"""
from __future__ import annotations

import contextlib
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend import fs_durability
from backend.db_manager import PRKSDatabase
from backend.services import work_pdf_replace
from backend.storage.config import StorageConfig
from backend.work_deletion import (
    pending_pdf_cleanup_count,
    retry_pending_pdf_cleanup,
)

_SCHEMA_PATH = os.path.join(_PROJECT_DIR, "backend", "db_schema.sql")

PDF_BODY = b"%PDF-1.4\n%PROCESSING-IMPORT\n%%EOF\n"
SEEDED_BODY = b"%PDF-1.4\n%PRE-SEEDED-BYTES\n%%EOF\n"
LINEARIZED = b"%PDF-1.4\n%\x80\x80\x80\x80\n/Linearized 1\n% qpdf output\n%%EOF\n"


class _StoreHarness:
    """Records durability-relevant calls the store makes during import."""

    def __init__(self):
        self.events = []
        self.synced_sizes = []
        self.sync_file_error = None
        self.dir_sync_ok = True

    def fsync_open_file(self, fd):
        if self.sync_file_error is not None:
            raise self.sync_file_error
        self.synced_sizes.append(os.fstat(fd).st_size)
        self.events.append(("fsync-file", fd))
        fs_durability.fsync_open_file(fd)

    def fsync_directory(self, path):
        self.events.append(("fsync-dir", path))
        if not self.dir_sync_ok:
            return False
        return fs_durability.fsync_directory(path)

    def linearize(self, path, *, context):
        self.events.append(("linearize", path, context))
        return False, "disabled"

    @property
    def steps(self):
        return [event[0] for event in self.events]


class ProcessingImportManagedPdfTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="prks-processing-import-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self.storage = StorageConfig.for_testing(self._tmpdir)
        os.makedirs(self.storage.pdfs_dir, exist_ok=True)
        os.makedirs(self.storage.processing_dir, exist_ok=True)
        self.db = PRKSDatabase(storage=self.storage, schema_path=_SCHEMA_PATH)

    def _stage(self, name: str = "inbox.pdf", body: bytes = PDF_BODY) -> dict:
        path = os.path.join(self.storage.processing_dir, name)
        with open(path, "wb") as handle:
            handle.write(body)
        staged = self.db.scan_processing_files()
        self.assertEqual(len(staged), 1)
        return staged[0]

    def _claims(self) -> list[str]:
        rows = self.db.execute_query(
            "SELECT filename FROM pending_pdf_cleanup ORDER BY filename"
        )
        return [row["filename"] for row in rows]

    def _managed_pdfs(self) -> list[str]:
        return sorted(
            n for n in os.listdir(self.storage.pdfs_dir) if n.lower().endswith(".pdf")
        )

    def _patch_store_harness(self, harness: _StoreHarness):
        return (
            patch.object(work_pdf_replace, "fsync_open_file", harness.fsync_open_file),
            patch.object(work_pdf_replace, "fsync_directory", harness.fsync_directory),
            patch.object(
                work_pdf_replace, "maybe_linearize_pdf_in_place", harness.linearize
            ),
        )

    # --- #169: durable exclusive store -----------------------------------

    def test_import_goes_through_store_new_managed_pdf_bytes(self):
        row = self._stage()
        seen = {}

        real_store = work_pdf_replace.store_new_managed_pdf_bytes

        def wrap(pdfs_dir, original_name, body, *, linearize_context="work-create-upload"):
            seen["called"] = True
            seen["body"] = body
            seen["context"] = linearize_context
            seen["pdfs_dir"] = pdfs_dir
            return real_store(
                pdfs_dir, original_name, body, linearize_context=linearize_context
            )

        with patch.object(
            work_pdf_replace, "store_new_managed_pdf_bytes", side_effect=wrap
        ):
            # Patch the late-bound name import_processing_file resolves.
            with patch(
                "backend.services.work_pdf_replace.store_new_managed_pdf_bytes",
                side_effect=wrap,
            ):
                out = self.db.import_processing_file(row["id"])

        self.assertTrue(seen.get("called"))
        self.assertEqual(seen.get("body"), PDF_BODY)
        self.assertEqual(seen.get("context"), "processing-import")
        self.assertEqual(seen.get("pdfs_dir"), self.storage.pdfs_dir)
        work = self.db.get_work(out["work_id"])
        self.assertTrue(str(work.get("file_path") or "").startswith("/api/pdfs/"))
        self.assertFalse(
            os.path.exists(os.path.join(self.storage.processing_dir, "inbox.pdf"))
        )

    def test_import_with_linearize_disabled_applies_durability_helpers(self):
        row = self._stage()
        harness = _StoreHarness()
        patches = self._patch_store_harness(harness)
        for p in patches:
            p.start()
        try:
            out = self.db.import_processing_file(row["id"])
        finally:
            for p in reversed(patches):
                p.stop()

        self.assertEqual(
            harness.steps,
            ["fsync-file", "fsync-dir", "linearize"],
            "contents durable first, then the directory entry, then optional lin",
        )
        self.assertEqual(harness.synced_sizes, [len(PDF_BODY)])
        self.assertEqual(harness.events[2][2], "processing-import")
        work = self.db.get_work(out["work_id"])
        name = work["file_path"].rsplit("/", 1)[-1]
        with open(os.path.join(self.storage.pdfs_dir, name), "rb") as handle:
            self.assertEqual(handle.read(), PDF_BODY)

    def test_import_survives_qpdf_failure_and_keeps_durable_bytes(self):
        row = self._stage()

        def boom(path, *, context):
            raise RuntimeError("qpdf exploded before try")

        with patch.object(work_pdf_replace, "maybe_linearize_pdf_in_place", boom):
            with self.assertLogs("prks", level=logging.WARNING) as logs:
                out = self.db.import_processing_file(row["id"])

        self.assertTrue(any("pdf_linearize_error" in line for line in logs.output))
        work = self.db.get_work(out["work_id"])
        name = work["file_path"].rsplit("/", 1)[-1]
        dest = os.path.join(self.storage.pdfs_dir, name)
        self.assertTrue(os.path.isfile(dest))
        with open(dest, "rb") as handle:
            self.assertEqual(handle.read(), PDF_BODY)

    def test_preseeded_basename_collision_refuses_without_overwrite(self):
        fixed = f"collision-{uuid.uuid4().hex}.pdf"
        seeded = os.path.join(self.storage.pdfs_dir, fixed)
        with open(seeded, "wb") as handle:
            handle.write(SEEDED_BODY)
        row = self._stage("collide.pdf")

        with patch(
            "backend.services.work_pdf_replace.mint_managed_pdf_filename",
            return_value=fixed,
        ):
            with self.assertRaises(ValueError) as ctx:
                self.db.import_processing_file(row["id"])

        self.assertIn("name already taken", str(ctx.exception).lower())
        with open(seeded, "rb") as handle:
            self.assertEqual(handle.read(), SEEDED_BODY)
        self.assertTrue(
            os.path.isfile(os.path.join(self.storage.processing_dir, "collide.pdf")),
            "inbox source must survive a refused collision",
        )
        self.assertEqual(self.db.execute_query("SELECT id FROM works"), [])
        pf = self.db.execute_query(
            "SELECT status, last_error FROM processing_files WHERE id = ?",
            (row["id"],),
        )
        self.assertEqual(pf[0]["status"], "error")

    def test_successful_linearize_rewrites_after_durable_publish(self):
        row = self._stage()
        order = []

        real_fsync_open = work_pdf_replace.fsync_open_file

        def spy_fsync(fd):
            order.append("fsync-file")
            return real_fsync_open(fd)

        def fake_qpdf(argv, **kwargs):
            with open(argv[3], "wb") as handle:
                handle.write(LINEARIZED)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        from backend import pdf_linearize

        real_lin = pdf_linearize.maybe_linearize_pdf_in_place

        def spy_lin(path, *, context):
            order.append(("linearize", context))
            return real_lin(path, context=context)

        with patch.object(work_pdf_replace, "fsync_open_file", spy_fsync), \
                patch.object(work_pdf_replace, "maybe_linearize_pdf_in_place", spy_lin), \
                patch.object(pdf_linearize, "_linearize_enabled", return_value=True), \
                patch.object(pdf_linearize.shutil, "which", return_value="/usr/bin/qpdf"), \
                patch.object(pdf_linearize.subprocess, "run", side_effect=fake_qpdf), \
                patch.object(fs_durability, "_FULLFSYNC", None):
            out = self.db.import_processing_file(row["id"])

        self.assertEqual(order[0], "fsync-file")
        self.assertEqual(order[1], ("linearize", "processing-import"))
        work = self.db.get_work(out["work_id"])
        name = work["file_path"].rsplit("/", 1)[-1]
        with open(os.path.join(self.storage.pdfs_dir, name), "rb") as handle:
            self.assertEqual(handle.read(), LINEARIZED)

    def test_source_lifecycle_preserved_on_add_work_failure(self):
        row = self._stage("fragile.pdf")
        with patch.object(
            self.db, "add_work", side_effect=RuntimeError("simulated DB failure")
        ):
            with self.assertRaises(ValueError):
                self.db.import_processing_file(row["id"])
        self.assertTrue(
            os.path.isfile(os.path.join(self.storage.processing_dir, "fragile.pdf"))
        )
        self.assertEqual(self._managed_pdfs(), [])
        self.assertEqual(self._claims(), [])

    # --- #152: survivor-aware rollback -----------------------------------

    def test_case_a_survivor_keeps_bytes_and_settles_claim(self):
        """Case A: sibling adopts the basename before rollback unlinks."""
        row = self._stage("shared-roll.pdf")
        survivor_id = {"id": None}

        def adopt_then_fail(*args, **kwargs):
            works = self.db.execute_query("SELECT id, file_path FROM works")
            self.assertEqual(len(works), 1)
            fp = works[0]["file_path"]
            survivor_id["id"] = self.db.add_work(
                title="Survivor", file_path=fp
            )
            raise RuntimeError("link fail")

        with patch.object(self.db, "add_work_to_folder", side_effect=adopt_then_fail):
            with self.assertRaises(ValueError) as ctx:
                self.db.import_processing_file(row["id"])
        self.assertIn("link fail", str(ctx.exception))

        self.assertIsNotNone(survivor_id["id"])
        survivor = self.db.get_work(survivor_id["id"])
        name = survivor["file_path"].rsplit("/", 1)[-1]
        dest = os.path.join(self.storage.pdfs_dir, name)
        self.assertTrue(os.path.isfile(dest), "survivor must keep the managed bytes")
        self.assertEqual(self._claims(), [], "no pending claim over a live referrer")
        self.assertEqual(
            [w["id"] for w in self.db.execute_query("SELECT id FROM works")],
            [survivor_id["id"]],
        )
        self.assertTrue(
            os.path.isfile(os.path.join(self.storage.processing_dir, "shared-roll.pdf"))
        )

    def test_case_a_survivor_race_with_barriers_no_sleeps(self):
        """Rollback holds the shared lock across settle+unlink; adopter serializes.

        Barriers only (no sleeps). The adopter observes the lock held before
        unlink proceeds, proving it cannot commit between settle and remove.
        """
        row = self._stage("race.pdf")
        remove_entered = threading.Event()
        adopter_observed = threading.Event()
        adopter_done = threading.Event()
        observed = {"held": False, "adopter_saw_lock": False}

        minted = {"fp": None, "name": None}
        real_add = self.db.add_work

        def capturing_add(*args, **kwargs):
            wid = real_add(*args, **kwargs)
            minted["fp"] = kwargs.get("file_path")
            minted["name"] = (minted["fp"] or "").rsplit("/", 1)[-1]
            return wid

        real_remove = os.remove

        def blocked_remove(path, *args, **kwargs):
            name = os.path.basename(path)
            lock = work_pdf_replace.managed_pdf_path_lock(self.storage.pdfs_dir, name)
            observed["held"] = bool(lock and lock.locked())
            remove_entered.set()
            # Do not unlink until the adopter has observed the held lock.
            self.assertTrue(adopter_observed.wait(timeout=5))
            return real_remove(path, *args, **kwargs)

        def adopter():
            self.assertTrue(remove_entered.wait(timeout=5))
            lock = work_pdf_replace.managed_pdf_path_lock(
                self.storage.pdfs_dir, minted["name"]
            )
            self.assertIsNotNone(lock)
            observed["adopter_saw_lock"] = lock.locked()
            adopter_observed.set()
            with lock:
                self.db.add_work(title="Late adopter", file_path=minted["fp"])
            adopter_done.set()

        thread = threading.Thread(target=adopter, daemon=True)
        thread.start()
        with patch.object(self.db, "add_work", side_effect=capturing_add):
            with patch.object(
                self.db,
                "add_work_to_folder",
                side_effect=RuntimeError("post-step fail"),
            ):
                with patch(
                    "backend.work_deletion.os.remove", side_effect=blocked_remove
                ):
                    with self.assertRaises(ValueError):
                        self.db.import_processing_file(row["id"])
        self.assertTrue(adopter_done.wait(timeout=5))
        thread.join(timeout=5)

        self.assertTrue(observed["held"])
        self.assertTrue(observed["adopter_saw_lock"])
        self.assertFalse(
            os.path.isfile(os.path.join(self.storage.pdfs_dir, minted["name"]))
        )

    def test_case_a_survivor_committed_before_cleanup_keeps_bytes(self):
        """Sibling commits (Event barrier) before rollback cleanup runs."""
        row = self._stage("pre-adopt.pdf")
        minted_ready = threading.Event()
        sibling_ready = threading.Event()
        state = {"sibling": None, "fp": None}

        real_add = self.db.add_work

        def capturing_add(*args, **kwargs):
            wid = real_add(*args, **kwargs)
            state["fp"] = kwargs.get("file_path")
            minted_ready.set()
            return wid

        def fail_after_sibling(*args, **kwargs):
            sibling_ready.wait(timeout=5)
            raise RuntimeError("post-step fail")

        def sibling_worker():
            minted_ready.wait(timeout=5)
            state["sibling"] = self.db.add_work(
                title="Early sibling", file_path=state["fp"]
            )
            sibling_ready.set()

        thread = threading.Thread(target=sibling_worker, daemon=True)
        thread.start()
        with patch.object(self.db, "add_work", side_effect=capturing_add):
            with patch.object(
                self.db, "add_work_to_folder", side_effect=fail_after_sibling
            ):
                with self.assertRaises(ValueError):
                    self.db.import_processing_file(row["id"])
        thread.join(timeout=5)

        self.assertIsNotNone(state["sibling"])
        name = state["fp"].rsplit("/", 1)[-1]
        self.assertTrue(os.path.isfile(os.path.join(self.storage.pdfs_dir, name)))
        self.assertEqual(self._claims(), [])
        self.assertEqual(
            [w["id"] for w in self.db.execute_query("SELECT id FROM works")],
            [state["sibling"]],
        )

    def test_case_b_no_survivor_removes_bytes_and_settles_claim(self):
        row = self._stage("alone.pdf")
        with patch.object(
            self.db, "add_work_to_folder", side_effect=RuntimeError("link fail")
        ):
            with self.assertRaises(ValueError):
                self.db.import_processing_file(row["id"])
        self.assertEqual(self.db.execute_query("SELECT id FROM works"), [])
        self.assertEqual(self._managed_pdfs(), [])
        self.assertEqual(self._claims(), [])
        self.assertEqual(pending_pdf_cleanup_count(self.db), 0)

    def test_case_c_oserror_cleanup_leaves_retryable_claim(self):
        row = self._stage("locked.pdf")

        def fail_folder(*args, **kwargs):
            raise RuntimeError("link fail")

        real_remove = os.remove

        def boom(path, *args, **kwargs):
            if os.path.realpath(path).startswith(
                os.path.realpath(self.storage.pdfs_dir) + os.sep
            ):
                raise OSError("forced pdf cleanup failure")
            return real_remove(path, *args, **kwargs)

        with patch.object(self.db, "add_work_to_folder", side_effect=fail_folder):
            with patch("backend.work_deletion.os.remove", side_effect=boom):
                with self.assertRaises(ValueError):
                    self.db.import_processing_file(row["id"])

        self.assertEqual(self.db.execute_query("SELECT id FROM works"), [])
        leftovers = self._managed_pdfs()
        self.assertEqual(len(leftovers), 1)
        self.assertEqual(self._claims(), leftovers)
        summary = retry_pending_pdf_cleanup(self.db)
        self.assertEqual(summary["removed"], 1)
        self.assertEqual(self._claims(), [])
        self.assertEqual(self._managed_pdfs(), [])

    def test_case_d_ref_query_failure_fails_closed(self):
        row = self._stage("opaque.pdf")

        def fail_folder(*args, **kwargs):
            raise RuntimeError("link fail")

        real_connection = self.db.connection

        class _Proxy:
            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, params=()):
                if "FROM works" in sql and "file_path" in sql:
                    raise sqlite3.OperationalError("catalogue unavailable")
                return self._conn.execute(sql, params)

            def __getattr__(self, name):
                return getattr(self._conn, name)

        @contextlib.contextmanager
        def broken():
            with real_connection() as conn:
                yield _Proxy(conn)

        with patch.object(self.db, "add_work_to_folder", side_effect=fail_folder):
            # delete_work_record itself needs a readable catalogue for the
            # still_referenced check; break only the post-delete settle path.
            real_delete = self.db.delete_work_record

            def delete_then_break(work_id):
                record = real_delete(work_id)
                # After the delete commits its claim, make live-ref reads fail.
                self.db.connection = broken
                return record

            with patch.object(self.db, "delete_work_record", side_effect=delete_then_break):
                with self.assertRaises(ValueError):
                    self.db.import_processing_file(row["id"])

        leftovers = self._managed_pdfs()
        self.assertEqual(len(leftovers), 1, "fail closed: keep bytes when catalogue is unreadable")
        self.assertEqual(self._claims(), leftovers)

    def test_case_e_already_missing_is_idempotent(self):
        row = self._stage("missing.pdf")

        def fail_folder(*args, **kwargs):
            raise RuntimeError("link fail")

        real_remove = os.remove

        def already_gone(path, *args, **kwargs):
            if os.path.realpath(path).startswith(
                os.path.realpath(self.storage.pdfs_dir) + os.sep
            ):
                raise FileNotFoundError(path)
            return real_remove(path, *args, **kwargs)

        with patch.object(self.db, "add_work_to_folder", side_effect=fail_folder):
            with patch("backend.work_deletion.os.remove", side_effect=already_gone):
                with self.assertRaises(ValueError):
                    self.db.import_processing_file(row["id"])

        # The claim was recorded, then settled as "already gone".
        self.assertEqual(self._claims(), [])
        self.assertEqual(self.db.execute_query("SELECT id FROM works"), [])

    def test_replay_import_is_idempotent_after_success(self):
        row = self._stage("once.pdf")
        self.db.update_processing_file(row["id"], {"title": "Once"})
        first = self.db.import_processing_file(row["id"])
        second = self.db.import_processing_file(row["id"])
        self.assertEqual(first["work_id"], second["work_id"])
        self.assertEqual(len(self.db.execute_query("SELECT id FROM works")), 1)
        self.assertEqual(len(self._managed_pdfs()), 1)

    def test_discard_unowned_holds_shared_lock(self):
        name = f"orphan-{uuid.uuid4().hex}.pdf"
        path = os.path.join(self.storage.pdfs_dir, name)
        with open(path, "wb") as handle:
            handle.write(PDF_BODY)
        lock = work_pdf_replace.managed_pdf_path_lock(self.storage.pdfs_dir, name)
        observed = {}

        real_remove = os.remove

        def watch(path_arg, *args, **kwargs):
            observed["held"] = lock.locked()
            return real_remove(path_arg, *args, **kwargs)

        with patch.object(work_pdf_replace.os, "remove", side_effect=watch):
            self.assertTrue(
                work_pdf_replace.discard_unowned_managed_pdf(
                    self.storage.pdfs_dir, name, db=self.db
                )
            )
        self.assertTrue(observed.get("held"))
        self.assertFalse(os.path.isfile(path))


if __name__ == "__main__":
    unittest.main()
