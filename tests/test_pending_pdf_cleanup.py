"""Recoverable post-delete cleanup for managed PDFs (EF-011 / #91).

The canonical Work row commits before any filesystem work, so a failed
`os.remove()` used to be unrecoverable: the `file_path` that identified the
bytes was already gone. These tests pin the durable claim, its retry, and the
safety rules that keep a stale claim from ever deleting live bytes.
"""
import contextlib
import os
import shutil
import sqlite3
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
from backend.services.work_pdf_replace import managed_pdf_path_lock
from backend.work_deletion import (
    PENDING_PDF_CLEANUP_RETRY_LIMIT,
    cleanup_after_work_delete,
    cleanup_released_managed_pdfs,
    delete_work,
    pending_pdf_cleanup_count,
    retry_pending_pdf_cleanup,
    retry_pending_pdf_cleanup_at_startup,
    settle_claim_if_referenced,
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

    def _unreadable_catalogue(self):
        """Make the live-catalogue read fail where the code actually reads it.

        The reference check runs inside its own write transaction, so the
        failure is injected on the connection that transaction uses rather
        than on a helper the code no longer calls. Everything else --
        selecting claims, stamping an attempt -- still works, which is what a
        partially unreadable database looks like.
        """
        real_connection = self.db.connection

        class _Proxy:
            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, params=()):
                if "FROM works" in sql:
                    raise sqlite3.OperationalError("catalogue unavailable")
                return self._conn.execute(sql, params)

            def __getattr__(self, name):
                return getattr(self._conn, name)

        @contextlib.contextmanager
        def broken():
            with real_connection() as conn:
                yield _Proxy(conn)

        return patch.object(self.db, "connection", side_effect=broken)

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
        record = self.db.delete_work_record(work_id)
        with self._unreadable_catalogue():
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

        with self._unreadable_catalogue():
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

    def test_delete_legacy_delimiter_named_work_unlinks_bytes(self):
        """Owner P2: physical cleanup still owns pre-upgrade ``?;#`` names.

        ``managed_pdf_filename`` refuses these for adoption/serving, but a
        restored library may already hold ``file_path=/api/pdfs/legacy.pdf?x``
        with matching on-disk bytes. Delete must claim and unlink them.
        """
        name = f"legacy-{uuid.uuid4().hex}.pdf?x"
        try:
            abs_path = self._write_pdf(name)
        except OSError:
            self.skipTest("filesystem refuses '?' in filenames")
        work_id = self.db.add_work(
            title="Legacy delimiter",
            file_path=f"/api/pdfs/{name}",
        )
        from backend.db_manager import managed_pdf_filename, owned_managed_pdf_basename

        self.assertIsNone(managed_pdf_filename(f"/api/pdfs/{name}"))
        self.assertEqual(owned_managed_pdf_basename(f"/api/pdfs/{name}"), name)

        result = delete_work(self.db, self.index, work_id)
        self.assertTrue(result.existed)
        self.assertEqual(result.cleanup_failures, ())
        self.assertFalse(result.pending_pdf_cleanup)
        self.assertFalse(os.path.isfile(abs_path))
        self.assertEqual(self._claims(), [])

    def test_retry_preexisting_delimiter_cleanup_claim(self):
        """A pending claim for ``legacy.pdf?x`` must settle across the new rules."""
        name = f"claim-{uuid.uuid4().hex}.pdf?x"
        try:
            abs_path = self._write_pdf(name)
        except OSError:
            self.skipTest("filesystem refuses '?' in filenames")
        with self.db.connection() as conn:
            conn.execute(
                "INSERT INTO pending_pdf_cleanup (filename) VALUES (?)",
                (name,),
            )
            conn.commit()
        self.assertEqual(self._claims(), [name])
        summary = retry_pending_pdf_cleanup(self.db)
        self.assertEqual(summary["removed"], 1)
        self.assertEqual(summary["unsafe"], 0)
        self.assertFalse(os.path.isfile(abs_path))
        self.assertEqual(self._claims(), [])

    def test_surviving_query_spelling_protects_stem_basename(self):
        """CodeRabbit: ``/api/pdfs/x.pdf?q`` protects ``x.pdf`` from cleanup."""
        stem = f"protect-{uuid.uuid4().hex}.pdf"
        abs_path = self._write_pdf(stem)
        # Survivor uses a delimiter spelling that GET would strip to the stem.
        survivor_id = self.db.add_work(
            title="Query survivor",
            file_path=f"/api/pdfs/{stem}?q",
        )
        with self.db.connection() as conn:
            conn.execute(
                "INSERT INTO pending_pdf_cleanup (filename) VALUES (?)",
                (stem,),
            )
            conn.commit()
        self.assertEqual(self._claims(), [stem])
        summary = retry_pending_pdf_cleanup(self.db)
        self.assertEqual(summary["superseded"], 1)
        self.assertEqual(summary["removed"], 0)
        self.assertTrue(os.path.isfile(abs_path))
        self.assertEqual(self._claims(), [])
        self.assertIsNotNone(self.db.get_work(survivor_id))

    def test_delete_delimiter_survivor_reclaims_superseded_stem(self):
        """Owner P2: stem claim superseded by ``?x`` survivor is reclaimed on delete.

        Sequence: claim(stem) → delimiter survivor supersedes → delete survivor
        → both physical ``stem?x`` and serving stem cleanup eventually settle.
        """
        stem = f"stem-{uuid.uuid4().hex}.pdf"
        physical = f"{stem}?x"
        stem_path = self._write_pdf(stem)
        try:
            physical_path = self._write_pdf(physical)
        except OSError:
            physical_path = None  # optional; missing physical is still success

        # Stem claim pending (as after deleting the canonical stem owner).
        with self.db.connection() as conn:
            conn.execute(
                "INSERT INTO pending_pdf_cleanup (filename) VALUES (?)",
                (stem,),
            )
            conn.commit()

        survivor_id = self.db.add_work(
            title="Delimiter survivor",
            file_path=f"/api/pdfs/{physical}",
        )
        # While the survivor lives, the stem claim must retire as superseded.
        summary = retry_pending_pdf_cleanup(self.db)
        self.assertEqual(summary["superseded"], 1)
        self.assertEqual(self._claims(), [])
        self.assertTrue(os.path.isfile(stem_path))

        # Deleting the survivor must claim BOTH physical and serving identities.
        result = delete_work(self.db, self.index, survivor_id)
        self.assertTrue(result.existed)
        self.assertEqual(result.cleanup_failures, ())
        self.assertFalse(result.pending_pdf_cleanup)
        self.assertFalse(os.path.isfile(stem_path))
        if physical_path is not None:
            self.assertFalse(os.path.isfile(physical_path))
        self.assertEqual(self._claims(), [])

    def test_delete_alias_survivor_defers_stem_claim_until_alias_gone(self):
        """Owner P2: weak aliases defer stem cleanup; they never mint claims.

        Fail-closed aliases (traversal / nested / ``%2F``) block unlink but
        must not retire a pending stem claim and must not create a new one on
        their own delete. Sequence: claim stays pending while the alias lives;
        after the alias is gone the existing claim settles.
        """
        aliases = (
            ("traversal", "/api/pdfs/../{stem}"),
            ("nested", "/api/pdfs/subdir/{stem}"),
            ("encoded", "/api/pdfs/foo%2F{stem}"),
        )
        for label, template in aliases:
            with self.subTest(alias=label):
                stem = f"{label}-{uuid.uuid4().hex}.pdf"
                stem_path = self._write_pdf(stem)
                # --- path A: pending claim deferred (not superseded) by weak alias ---
                with self.db.connection() as conn:
                    conn.execute(
                        "INSERT INTO pending_pdf_cleanup (filename) VALUES (?)",
                        (stem,),
                    )
                    conn.commit()
                survivor_id = self.db.add_work(
                    title=f"Alias {label}",
                    file_path=template.format(stem=stem),
                )
                summary = retry_pending_pdf_cleanup(self.db)
                self.assertEqual(summary["superseded"], 0)
                self.assertEqual(summary["removed"], 0)
                self.assertGreaterEqual(summary["deferred"], 1)
                self.assertEqual(self._claims(), [stem])
                self.assertTrue(os.path.isfile(stem_path))
                # Weak alias delete mints nothing; existing claim then settles
                # inside delete_work's post-commit retry pass.
                result = delete_work(self.db, self.index, survivor_id)
                self.assertTrue(result.existed)
                self.assertEqual(result.cleanup_failures, ())
                self.assertFalse(os.path.isfile(stem_path))
                self.assertEqual(self._claims(), [])

                # --- path B: canon delete mints claim; observe before cleanup ---
                stem_b = f"{label}-b-{uuid.uuid4().hex}.pdf"
                stem_b_path = self._write_pdf(stem_b)
                canon_id = self.db.add_work(
                    title=f"Canon {label}",
                    file_path=f"/api/pdfs/{stem_b}",
                )
                alias_id = self.db.add_work(
                    title=f"AliasB {label}",
                    file_path=template.format(stem=stem_b),
                )
                # Split the durable-claim boundary from post-commit cleanup so
                # an empty claims list cannot mean "never claimed".
                rec = self.db.delete_work_record(canon_id)
                self.assertIsNotNone(rec)
                self.assertFalse(rec.managed_pdf_still_referenced)
                self.assertEqual(self._claims(), [stem_b])
                cleanup = cleanup_after_work_delete(
                    self.db,
                    self.index,
                    canon_id,
                    file_path=rec.file_path,
                    existed=True,
                )
                self.assertEqual(cleanup.cleanup_failures, ())
                # Weak alias still blocks unlink; claim must remain.
                self.assertTrue(cleanup.pending_pdf_cleanup)
                self.assertEqual(self._claims(), [stem_b])
                self.assertTrue(os.path.isfile(stem_b_path))
                # Alias gone → pending claim settles in delete_work's retry.
                result = delete_work(self.db, self.index, alias_id)
                self.assertTrue(result.existed)
                self.assertFalse(os.path.isfile(stem_b_path))
                self.assertEqual(self._claims(), [])

    def test_retarget_alone_claims_and_unlinks_old_pdf(self):
        """Owner P2: A→B with no other A referrer must claim and remove A."""
        a_id, a_name, a_path = self._managed_work("OnA")
        b_name = f"b-{uuid.uuid4().hex}.pdf"
        b_path = self._write_pdf(b_name)
        # Observe the durable-claim boundary before post-commit unlink.
        claimed = self.db.update_work_metadata(
            a_id, {"file_path": f"/api/pdfs/{b_name}"}
        )
        self.assertEqual(claimed, (a_name,))
        self.assertEqual(self._claims(), [a_name])
        self.assertTrue(os.path.isfile(a_path))
        pending = cleanup_released_managed_pdfs(self.db, claimed)
        self.assertFalse(pending)
        self.assertFalse(os.path.isfile(a_path))
        self.assertTrue(os.path.isfile(b_path))
        self.assertEqual(self._claims(), [])
        rows = self.db.execute_query(
            "SELECT file_path FROM works WHERE id = ?", (a_id,)
        )
        self.assertEqual(rows[0]["file_path"], f"/api/pdfs/{b_name}")

    def test_retarget_with_shared_referrer_keeps_old_pdf(self):
        """Owner P2: A→B while another Work still strongly owns A claims nothing."""
        a_name = f"shared-{uuid.uuid4().hex}.pdf"
        a_path = self._write_pdf(a_name)
        first = self.db.add_work(title="First", file_path=f"/api/pdfs/{a_name}")
        second = self.db.add_work(title="Second", file_path=f"/api/pdfs/{a_name}")
        b_name = f"b-{uuid.uuid4().hex}.pdf"
        self._write_pdf(b_name)
        claimed = self.db.update_work_metadata(
            first, {"file_path": f"/api/pdfs/{b_name}"}
        )
        self.assertEqual(claimed, ())
        self.assertEqual(self._claims(), [])
        cleanup_released_managed_pdfs(self.db, claimed)
        self.assertTrue(os.path.isfile(a_path))
        self.assertIsNotNone(self.db.get_work(second))

    def test_retarget_recreates_previously_superseded_claim(self):
        """Owner P2: Work that retired an A claim must recreate it on A→B."""
        a_name = f"supersede-{uuid.uuid4().hex}.pdf"
        a_path = self._write_pdf(a_name)
        # Orphan claim as after deleting a prior owner.
        with self.db.connection() as conn:
            conn.execute(
                "INSERT INTO pending_pdf_cleanup (filename) VALUES (?)",
                (a_name,),
            )
            conn.commit()
        owner = self.db.add_work(title="Owner", file_path=f"/api/pdfs/{a_name}")
        summary = retry_pending_pdf_cleanup(self.db)
        self.assertEqual(summary["superseded"], 1)
        self.assertEqual(self._claims(), [])
        self.assertTrue(os.path.isfile(a_path))

        b_name = f"b-{uuid.uuid4().hex}.pdf"
        self._write_pdf(b_name)
        claimed = self.db.update_work_metadata(
            owner, {"file_path": f"/api/pdfs/{b_name}"}
        )
        self.assertEqual(claimed, (a_name,))
        self.assertEqual(self._claims(), [a_name])
        pending = cleanup_released_managed_pdfs(self.db, claimed)
        self.assertFalse(pending)
        self.assertFalse(os.path.isfile(a_path))
        self.assertEqual(self._claims(), [])

    def test_retarget_claim_survives_missing_post_commit_cleanup(self):
        """Crash after path commit still leaves a retryable claim for A."""
        a_id, a_name, a_path = self._managed_work("CrashBoundary")
        b_name = f"b-{uuid.uuid4().hex}.pdf"
        self._write_pdf(b_name)
        claimed = self.db.update_work_metadata(
            a_id, {"file_path": f"/api/pdfs/{b_name}"}
        )
        self.assertEqual(claimed, (a_name,))
        self.assertEqual(self._claims(), [a_name])
        self.assertTrue(os.path.isfile(a_path))
        # No cleanup_released_managed_pdfs — simulate process death.
        restarted = self._open_db()
        self.assertEqual(self._claims(restarted), [a_name])
        summary = retry_pending_pdf_cleanup(restarted)
        self.assertEqual(summary["removed"], 1)
        self.assertFalse(os.path.isfile(a_path))
        self.assertEqual(self._claims(restarted), [])

    def test_retarget_defers_when_only_weak_alias_survives(self):
        """Weak alias after A→B keeps the claim pending (does not supersede)."""
        a_name = f"weak-retarget-{uuid.uuid4().hex}.pdf"
        a_path = self._write_pdf(a_name)
        owner = self.db.add_work(title="Owner", file_path=f"/api/pdfs/{a_name}")
        self.db.add_work(
            title="Weak alias",
            file_path=f"/api/pdfs/../{a_name}",
        )
        b_name = f"b-{uuid.uuid4().hex}.pdf"
        self._write_pdf(b_name)
        claimed = self.db.update_work_metadata(
            owner, {"file_path": f"/api/pdfs/{b_name}"}
        )
        self.assertEqual(claimed, (a_name,))
        self.assertEqual(self._claims(), [a_name])
        pending = cleanup_released_managed_pdfs(self.db, claimed)
        self.assertTrue(pending)
        self.assertTrue(os.path.isfile(a_path))
        self.assertEqual(self._claims(), [a_name])

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

    # --- review findings: concurrency, rotation, restore -----------------

    def test_a_stale_pass_cannot_erase_a_concurrent_deletions_claim(self):
        """End-to-end semantics behind the qodo (High) finding on PR #145.

        The interleaving itself is pinned by
        `test_settling_a_referenced_claim_is_one_transaction`, which is where
        the atomicity lives; this walks the sequence that finding describes --
        one of two referrers goes, then the last one's unlink fails -- and
        asserts the claim that survives is honoured rather than retired.
        """
        first, name, abs_path = self._managed_work("Shared", filename="raced.pdf")
        second = self.db.add_work(title="Other", file_path=f"/api/pdfs/{name}")

        # Deleting one of two referrers owes nothing: no claim, file kept.
        delete_work(self.db, self.index, first)
        self.assertEqual(self._claims(), [])
        self.assertTrue(os.path.isfile(abs_path))

        # The last referrer goes and its unlink fails: the claim must stand.
        with self._failing_remove(abs_path):
            result = delete_work(self.db, self.index, second)
        self.assertTrue(result.pending_pdf_cleanup)
        self.assertEqual(self._claims(), [name])

        # A pass that still believed the Work was alive would have retired it;
        # the transactional check sees the committed deletion instead.
        summary = retry_pending_pdf_cleanup(self.db)
        self.assertEqual(summary["superseded"], 0)
        self.assertEqual(summary["removed"], 1)
        self.assertEqual(self._claims(), [])

    def test_settling_a_referenced_claim_is_one_transaction(self):
        work_id, name, abs_path = self._managed_work("Live")
        self.db.execute_query(
            "INSERT INTO pending_pdf_cleanup (filename) VALUES (?)", (name,)
        )
        # Referenced: retire the claim, keep the bytes, report it settled.
        self.assertIs(settle_claim_if_referenced(self.db, name), True)
        self.assertEqual(self._claims(), [])
        self.assertTrue(os.path.isfile(abs_path))

        # Unreferenced: leave the claim standing for the caller to act on.
        self.db.delete_work_record(work_id)
        self.assertEqual(self._claims(), [name])
        self.assertIs(settle_claim_if_referenced(self.db, name), False)
        self.assertEqual(self._claims(), [name])

        # Unreadable: neither answer, and the claim is untouched.
        with self._unreadable_catalogue():
            self.assertIsNone(settle_claim_if_referenced(self.db, name))
        self.assertEqual(self._claims(), [name])

    def test_unsettleable_claims_rotate_so_later_ones_are_reached(self):
        """PR #145 review (qodo + greptile): a bounded pass that always takes
        the oldest rows never reaches anything behind a stuck claim."""
        # Names chosen so the stuck claims sort first: rows recorded in the
        # same second tie on recorded_at, and the filename breaks the tie.
        stuck = []
        for index in range(3):
            name = f"aaa-stuck-{index}.pdf"
            self._write_pdf(name)
            stuck.append(name)
            self.db.execute_query(
                "INSERT INTO pending_pdf_cleanup (filename) VALUES (?)", (name,)
            )
        later = "zzz-later.pdf"
        later_abs = self._write_pdf(later)
        self.db.execute_query(
            "INSERT INTO pending_pdf_cleanup (filename) VALUES (?)", (later,)
        )

        real_remove = os.remove

        def only_stuck_fails(path, *args, **kwargs):
            if os.path.basename(path) in stuck:
                raise OSError("forced pdf cleanup failure")
            return real_remove(path, *args, **kwargs)

        # A pass small enough to be filled entirely by the stuck claims.
        with patch("backend.work_deletion.os.remove", side_effect=only_stuck_fails):
            first = retry_pending_pdf_cleanup(self.db, limit=2)
        self.assertEqual(first["failed"], 2)
        self.assertTrue(os.path.isfile(later_abs))

        # The next pass must move past them rather than take the same two.
        with patch("backend.work_deletion.os.remove", side_effect=only_stuck_fails):
            second = retry_pending_pdf_cleanup(self.db, limit=2)
        self.assertEqual(second["removed"], 1)
        self.assertFalse(os.path.isfile(later_abs))
        self.assertEqual(sorted(self._claims()), sorted(stuck))

    def test_claims_prks_cannot_act_on_rotate_like_any_other(self):
        """PR #145 review (codex, P2): the unresolvable branch skipped the
        attempt stamp, so enough invalid rows sorting first would re-fill every
        bounded pass and strand the valid claims behind them.

        Only a manually repaired or corrupted database has such rows -- PRKS
        writes canonical basenames -- but that is exactly the case the rotation
        exists for.
        """
        invalid = ("aaa-../escape.pdf", "aaa-sub/nested.pdf")
        for bad in invalid:
            self.db.execute_query(
                "INSERT INTO pending_pdf_cleanup (filename) VALUES (?)", (bad,)
            )
        valid = "zzz-real.pdf"
        valid_abs = self._write_pdf(valid)
        self.db.execute_query(
            "INSERT INTO pending_pdf_cleanup (filename) VALUES (?)", (valid,)
        )

        first = retry_pending_pdf_cleanup(self.db, limit=2)
        self.assertEqual(first["unsafe"], 2)
        self.assertEqual(first["removed"], 0)
        self.assertTrue(os.path.isfile(valid_abs))
        stamped = self.db.execute_query(
            "SELECT filename FROM pending_pdf_cleanup WHERE last_attempt_at IS NOT NULL"
        )
        self.assertEqual(sorted(r["filename"] for r in stamped), sorted(invalid))

        # The next bounded pass gets past them to the claim it can settle.
        second = retry_pending_pdf_cleanup(self.db, limit=2)
        self.assertEqual(second["removed"], 1)
        self.assertFalse(os.path.isfile(valid_abs))
        self.assertEqual(sorted(self._claims()), sorted(invalid))

    def test_an_attempted_claim_is_stamped_and_a_settled_one_is_gone(self):
        work_id, name, abs_path = self._managed_work("Stamped")
        with self._failing_remove(abs_path):
            delete_work(self.db, self.index, work_id)
            retry_pending_pdf_cleanup(self.db)
        rows = self.db.execute_query(
            "SELECT filename, last_attempt_at FROM pending_pdf_cleanup"
        )
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0]["last_attempt_at"])
        self.assertEqual(retry_pending_pdf_cleanup(self.db)["removed"], 1)
        self.assertEqual(self._claims(), [])

    def test_cleanup_holds_the_shared_managed_pdf_lock(self):
        """PR #145 review (qodo, High): the reference check and the unlink must
        not be separable, or a Work can adopt the name in between."""
        work_id, name, abs_path = self._managed_work("Guarded")
        self.db.delete_work_record(work_id)
        lock = managed_pdf_path_lock(self.storage.pdfs_dir, name)
        self.assertIsNotNone(lock)
        observed = {}

        real_remove = os.remove

        def watch(path, *args, **kwargs):
            observed["held"] = lock.locked()
            return real_remove(path, *args, **kwargs)

        with patch("backend.work_deletion.os.remove", side_effect=watch):
            retry_pending_pdf_cleanup(self.db)
        self.assertTrue(observed.get("held"))
        self.assertFalse(os.path.isfile(abs_path))
        self.assertFalse(lock.locked())

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
