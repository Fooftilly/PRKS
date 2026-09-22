"""Recoverable post-delete cleanup for managed PDFs (EF-011 / #91).

The canonical Work row commits before any filesystem work, so a failed
`os.remove()` used to be unrecoverable: the `file_path` that identified the
bytes was already gone. These tests pin the durable claim, its retry, and the
safety rules that keep a stale claim from ever deleting live bytes.
"""
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.db_manager import PRKSDatabase, prks_thumb_cache_stem
from backend.storage.config import StorageConfig
from backend.text_index import PRKSTextIndex
from backend.work_deletion import (
    PENDING_PDF_CLEANUP_RETRY_LIMIT,
    cleanup_after_work_delete,
    delete_work,
    pending_pdf_cleanup_count,
    retry_pending_pdf_cleanup,
    retry_pending_pdf_cleanup_at_startup,
)

_SCHEMA_PATH = os.path.join(_PROJECT_DIR, "backend", "db_schema.sql")


class PendingPdfCleanupTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="prks-pdf-cleanup-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        self.storage = StorageConfig.for_testing(self._tmpdir)
        os.makedirs(self.storage.pdfs_dir, exist_ok=True)
        os.makedirs(self.storage.thumbs_dir, exist_ok=True)
        self.db = self._open_db()
        self.index = PRKSTextIndex(storage=self.storage)

    def _open_db(self) -> PRKSDatabase:
        """A database handle. Reopening stands in for a process restart."""
        return PRKSDatabase(storage=self.storage, schema_path=_SCHEMA_PATH)

    # --- fixtures -------------------------------------------------------

    def _write_pdf(self, filename: str) -> str:
        abs_path = os.path.join(self.storage.pdfs_dir, filename)
        with open(abs_path, "wb") as handle:
            handle.write(b"%PDF-1.4\n%CLEANUP\n%%EOF\n")
        return abs_path

    def _managed_work(self, title: str, filename: str | None = None):
        name = filename or f"{uuid.uuid4().hex}.pdf"
        abs_path = self._write_pdf(name)
        work_id = self.db.add_work(title=title, file_path=f"/api/pdfs/{name}")
        return work_id, name, abs_path

    def _claims(self, db: PRKSDatabase | None = None) -> list:
        rows = (db or self.db).execute_query(
            "SELECT filename FROM pending_pdf_cleanup ORDER BY filename"
        )
        return [row["filename"] for row in rows]

    def _failing_remove(self, target_abs: str, error=None):
        """Patch `os.remove` so exactly one path fails, as a locked file would."""
        real_remove = os.remove

        def fake_remove(path, *args, **kwargs):
            if os.path.realpath(path) == os.path.realpath(target_abs):
                raise error or OSError("forced pdf cleanup failure")
            return real_remove(path, *args, **kwargs)

        return patch("backend.work_deletion.os.remove", side_effect=fake_remove)

    # --- the healthy path leaves nothing behind -------------------------

    def test_successful_delete_leaves_no_claim(self):
        work_id, name, abs_path = self._managed_work("Clean")
        result = delete_work(self.db, self.index, work_id)
        self.assertTrue(result.existed)
        self.assertEqual(result.cleanup_failures, ())
        self.assertFalse(result.pending_pdf_cleanup)
        self.assertFalse(os.path.isfile(abs_path))
        self.assertEqual(self._claims(), [])
        self.assertEqual(pending_pdf_cleanup_count(self.db), 0)

    def test_a_work_without_a_managed_pdf_never_records_a_claim(self):
        work_id = self.db.add_work(title="Video only")
        result = delete_work(self.db, self.index, work_id)
        self.assertTrue(result.existed)
        self.assertEqual(self._claims(), [])

    # --- failure leaves durable identity --------------------------------

    def test_failed_removal_keeps_the_work_deleted_and_the_claim_durable(self):
        work_id, name, abs_path = self._managed_work("Locked")
        with self._failing_remove(abs_path):
            result = delete_work(self.db, self.index, work_id)
        self.assertTrue(result.existed)
        self.assertEqual(result.cleanup_failures, ("pdf",))
        self.assertTrue(result.pending_pdf_cleanup)
        # 1. The canonical deletion stays committed.
        self.assertIsNone(self.db.get_work(work_id))
        # 2. The orphan and the claim that owns it both survive.
        self.assertTrue(os.path.isfile(abs_path))
        self.assertEqual(self._claims(), [name])

    def test_the_claim_is_written_before_any_filesystem_work(self):
        """A crash between the commit and `os.remove()` must still be recoverable.

        `delete_work_record()` is the canonical transaction on its own; nothing
        has touched the filesystem yet when it returns.
        """
        work_id, name, abs_path = self._managed_work("Crash")
        record = self.db.delete_work_record(work_id)
        self.assertIsNotNone(record)
        self.assertTrue(os.path.isfile(abs_path))
        self.assertEqual(self._claims(), [name])

    def test_claim_survives_a_fresh_database_instance_and_retry_removes_it(self):
        work_id, name, abs_path = self._managed_work("Restart")
        with self._failing_remove(abs_path):
            delete_work(self.db, self.index, work_id)
        self.assertTrue(os.path.isfile(abs_path))

        # Process restart: nothing in memory carries over.
        restarted = self._open_db()
        self.assertEqual(self._claims(restarted), [name])
        summary = retry_pending_pdf_cleanup_at_startup(restarted)
        self.assertEqual(summary["removed"], 1)
        self.assertFalse(os.path.isfile(abs_path))
        self.assertEqual(self._claims(restarted), [])

    def test_a_later_deletion_drains_the_backlog_without_a_restart(self):
        stuck_id, stuck_name, stuck_abs = self._managed_work("Stuck")
        with self._failing_remove(stuck_abs):
            delete_work(self.db, self.index, stuck_id)
        self.assertEqual(self._claims(), [stuck_name])

        other_id, _, other_abs = self._managed_work("Later")
        delete_work(self.db, self.index, other_id)
        self.assertFalse(os.path.isfile(other_abs))
        self.assertFalse(os.path.isfile(stuck_abs))
        self.assertEqual(self._claims(), [])

    # --- a claim never outranks the live catalogue ----------------------

    def test_retry_refuses_a_filename_another_work_now_references(self):
        work_id, name, abs_path = self._managed_work("Adopted")
        with self._failing_remove(abs_path):
            delete_work(self.db, self.index, work_id)
        self.assertEqual(self._claims(), [name])

        # The same managed name becomes live again before the retry runs.
        new_id = self.db.add_work(title="New owner", file_path=f"/api/pdfs/{name}")

        summary = retry_pending_pdf_cleanup(self.db)
        self.assertEqual(summary["removed"], 0)
        self.assertEqual(summary["superseded"], 1)
        self.assertTrue(os.path.isfile(abs_path))
        self.assertIsNotNone(self.db.get_work(new_id))
        # The retired claim is not left dormant over live bytes.
        self.assertEqual(self._claims(), [])

        # Coherent afterwards: deleting the live owner cleans up normally.
        result = delete_work(self.db, self.index, new_id)
        self.assertEqual(result.cleanup_failures, ())
        self.assertFalse(os.path.isfile(abs_path))
        self.assertEqual(self._claims(), [])

    def test_an_unreadable_catalogue_is_reported_as_still_pending(self):
        """No exception is not the same as cleanup done.

        With the catalogue unreadable the removal is never authorized, so the
        claim stands and the result must say so rather than look successful.
        """
        work_id, name, abs_path = self._managed_work("Undecidable")
        real_query = self.db.execute_query

        def no_catalogue(query, params=()):
            if "FROM works" in query:
                raise RuntimeError("catalogue unavailable")
            return real_query(query, params)

        record = self.db.delete_work_record(work_id)
        with patch.object(self.db, "execute_query", side_effect=no_catalogue):
            result = cleanup_after_work_delete(
                self.db, self.index, work_id,
                file_path=record.file_path, existed=True)
        self.assertTrue(result.pending_pdf_cleanup)
        self.assertTrue(os.path.isfile(abs_path))
        self.assertEqual(self._claims(), [name])

    def test_retry_defers_when_the_live_catalogue_cannot_be_read(self):
        work_id, name, abs_path = self._managed_work("Unreadable")
        with self._failing_remove(abs_path):
            delete_work(self.db, self.index, work_id)

        real_query = self.db.execute_query

        def flaky(query, params=()):
            if "FROM works" in query:
                raise RuntimeError("catalogue unavailable")
            return real_query(query, params)

        with patch.object(self.db, "execute_query", side_effect=flaky):
            summary = retry_pending_pdf_cleanup(self.db)
        # Unknown is not "unreferenced": nothing is deleted and nothing is
        # settled, so the claim is still there for a readable database later.
        self.assertEqual(summary["deferred"], 1)
        self.assertEqual(summary["removed"], 0)
        self.assertEqual(summary["superseded"], 0)
        self.assertTrue(os.path.isfile(abs_path))
        self.assertEqual(self._claims(), [name])

    def test_a_shared_pdf_is_claimed_only_when_its_last_reference_goes(self):
        abs_path = self._write_pdf("shared.pdf")
        first = self.db.add_work(title="A", file_path="/api/pdfs/shared.pdf")
        second = self.db.add_work(title="B", file_path="/api/pdfs/shared.pdf")

        self.db.delete_work_record(first)
        self.assertEqual(self._claims(), [])
        self.assertTrue(os.path.isfile(abs_path))

        self.db.delete_work_record(second)
        self.assertEqual(self._claims(), ["shared.pdf"])
        self.assertEqual(retry_pending_pdf_cleanup(self.db)["removed"], 1)
        self.assertFalse(os.path.isfile(abs_path))

    def test_deleting_both_sharers_records_one_claim_not_two(self):
        self._write_pdf("twice.pdf")
        first = self.db.add_work(title="A", file_path="/api/pdfs/twice.pdf")
        second = self.db.add_work(title="B", file_path="/api/pdfs/twice.pdf")
        self.db.delete_work_record(first)
        self.db.delete_work_record(second)
        self.db.delete_work_record(second)  # replayed row delete: already gone
        self.assertEqual(self._claims(), ["twice.pdf"])

    # --- idempotence ----------------------------------------------------

    def test_a_missing_file_resolves_the_claim(self):
        work_id, name, abs_path = self._managed_work("Vanished")
        with self._failing_remove(abs_path):
            delete_work(self.db, self.index, work_id)
        os.remove(abs_path)  # removed out from under PRKS

        summary = retry_pending_pdf_cleanup(self.db)
        self.assertEqual(summary["missing"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(self._claims(), [])

    def test_repeated_passes_have_no_duplicate_destructive_effect(self):
        work_id, name, abs_path = self._managed_work("Twice")
        with self._failing_remove(abs_path):
            delete_work(self.db, self.index, work_id)

        first = retry_pending_pdf_cleanup(self.db)
        self.assertEqual((first["removed"], first["claimed"]), (1, 1))
        self.assertFalse(os.path.isfile(abs_path))

        # A bystander's bytes must not be collateral for a settled claim.
        bystander = self._write_pdf(f"{uuid.uuid4().hex}.pdf")
        self.db.add_work(title="Bystander", file_path=f"/api/pdfs/{os.path.basename(bystander)}")

        for _ in range(3):
            again = retry_pending_pdf_cleanup(self.db)
            self.assertEqual(again["claimed"], 0)
            self.assertEqual(again["removed"], 0)
        self.assertTrue(os.path.isfile(bystander))
        self.assertEqual(self._claims(), [])

    def test_a_retry_that_fails_again_keeps_the_claim(self):
        work_id, name, abs_path = self._managed_work("StillLocked")
        with self._failing_remove(abs_path):
            delete_work(self.db, self.index, work_id)
            first = retry_pending_pdf_cleanup(self.db)
            second = retry_pending_pdf_cleanup(self.db)
        self.assertEqual(first["failed"], 1)
        self.assertEqual(second["failed"], 1)
        self.assertEqual(self._claims(), [name])
        self.assertTrue(os.path.isfile(abs_path))

        # And the claim is still actionable once the failure clears.
        self.assertEqual(retry_pending_pdf_cleanup(self.db)["removed"], 1)
        self.assertEqual(self._claims(), [])

    def test_a_failed_delete_does_not_retry_itself_in_the_same_breath(self):
        work_id, name, abs_path = self._managed_work("NoDoubleTry")
        with self._failing_remove(abs_path) as removal:
            delete_work(self.db, self.index, work_id)
            attempts = [
                call for call in removal.call_args_list
                if os.path.realpath(call.args[0]) == os.path.realpath(abs_path)
            ]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(self._claims(), [name])

    # --- partial cleanup ------------------------------------------------

    def test_a_thumbnail_failure_does_not_make_the_pdf_repeat(self):
        work_id, name, pdf_abs = self._managed_work("PartialThumb")
        thumb = os.path.join(
            self.storage.thumbs_dir, f"{prks_thumb_cache_stem(work_id, 1)}.webp"
        )
        with open(thumb, "wb") as handle:
            handle.write(b"t")
        real_remove = os.remove

        def thumb_fails(path, *args, **kwargs):
            if os.path.realpath(path) == os.path.realpath(thumb):
                raise OSError("forced thumbnail cleanup failure")
            return real_remove(path, *args, **kwargs)

        with patch("backend.db_manager.os.remove", side_effect=thumb_fails):
            result = delete_work(self.db, self.index, work_id)
        self.assertEqual(result.cleanup_failures, ("thumbnails",))
        self.assertFalse(result.pending_pdf_cleanup)
        # The PDF portion completed, so nothing owes a second removal; the
        # thumbnail is reconciled by prune_orphan_pdf_thumbnails(), not by a
        # retry record of its own.
        self.assertFalse(os.path.isfile(pdf_abs))
        self.assertTrue(os.path.isfile(thumb))
        self.assertEqual(self._claims(), [])

    # --- containment ----------------------------------------------------

    def test_a_claim_that_cannot_be_contained_is_never_resolved_to_a_path(self):
        """Only exact managed basenames are stored, so this cannot arise from
        PRKS itself -- but a hand-edited row must not become a traversal."""
        outside = os.path.join(self._tmpdir, "outside.pdf")
        with open(outside, "wb") as handle:
            handle.write(b"keep me")
        for hostile in ("../outside.pdf", "sub/outside.pdf", "..", ""):
            with self.subTest(name=hostile):
                self.db.execute_query(
                    "INSERT OR REPLACE INTO pending_pdf_cleanup (filename) VALUES (?)",
                    (hostile,),
                )
                summary = retry_pending_pdf_cleanup(self.db)
                self.assertEqual(summary["removed"], 0)
                self.assertTrue(os.path.isfile(outside))
                self.db.execute_query(
                    "DELETE FROM pending_pdf_cleanup WHERE filename = ?", (hostile,)
                )

    # --- bounded work ---------------------------------------------------

    def test_a_pass_is_bounded_and_the_rest_is_kept_for_the_next_one(self):
        names = []
        for index in range(5):
            name = f"bounded-{index}.pdf"
            self._write_pdf(name)
            names.append(name)
            self.db.execute_query(
                "INSERT INTO pending_pdf_cleanup (filename) VALUES (?)", (name,)
            )
        summary = retry_pending_pdf_cleanup(self.db, limit=2)
        self.assertEqual(summary["claimed"], 2)
        self.assertEqual(summary["removed"], 2)
        self.assertEqual(len(self._claims()), 3)
        self.assertEqual(pending_pdf_cleanup_count(self.db), 3)

        retry_pending_pdf_cleanup(self.db)
        self.assertEqual(self._claims(), [])
        for name in names:
            self.assertFalse(os.path.isfile(os.path.join(self.storage.pdfs_dir, name)))

    def test_the_default_limit_is_bounded(self):
        self.assertGreater(PENDING_PDF_CLEANUP_RETRY_LIMIT, 0)
        self.assertLessEqual(PENDING_PDF_CLEANUP_RETRY_LIMIT, 1000)

    def test_startup_reports_what_it_could_not_clean(self):
        work_id, name, abs_path = self._managed_work("StillOwed")
        with self._failing_remove(abs_path):
            delete_work(self.db, self.index, work_id)
            with self.assertLogs("prks.work_deletion", level="WARNING") as logs:
                retry_pending_pdf_cleanup_at_startup(self.db)
        self.assertTrue(any("pdf_cleanup_pending count=1" in line for line in logs.output))
        # Counts only: a managed filename is library content.
        self.assertFalse(any(name in line for line in logs.output))
        self.assertEqual(self._claims(), [name])

    def test_startup_never_fails_on_a_broken_claim_store(self):
        with patch.object(
            self.db, "execute_query", side_effect=RuntimeError("no database")
        ):
            self.assertEqual(retry_pending_pdf_cleanup_at_startup(self.db), {
                "claimed": 0, "removed": 0, "missing": 0, "superseded": 0,
                "failed": 0, "unsafe": 0, "deferred": 0,
            })


if __name__ == "__main__":
    unittest.main()
