import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.db_manager import (
    PRKSDatabase,
    managed_pdf_filename,
    prks_delete_pdf_thumbnails_for_work_id,
    prks_thumb_cache_stem,
    referenced_managed_pdf_filename,
    safe_pdf_path_under_dir,
)
from backend.storage.config import StorageConfig
from backend.text_index import PRKSTextIndex
from backend.work_deletion import delete_work

_SCHEMA_PATH = os.path.join(_PROJECT_DIR, "backend", "db_schema.sql")
_ABORT_WORKS = "prks_test_abort_delete_works"
_ABORT_TAGS = "prks_test_abort_delete_tags"


def _index_has(index: PRKSTextIndex, work_id: str) -> bool:
    with index._conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM work_text_index WHERE work_id = ?",
            (work_id,),
        ).fetchone()
    return row is not None


def _install_abort_trigger(db: PRKSDatabase, name: str, table: str) -> None:
    db.execute_query(
        f"""
        CREATE TRIGGER {name} BEFORE DELETE ON {table}
        BEGIN
            SELECT RAISE(ABORT, 'forced {table} delete failure');
        END
        """
    )


def _drop_trigger(db: PRKSDatabase, name: str) -> None:
    db.execute_query(f"DROP TRIGGER IF EXISTS {name}")


class _BoomIndex:
    def remove_work(self, work_id: str) -> int:
        raise RuntimeError("forced text-index cleanup failure")


class TestWorkDeletion(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="prks-work-del-")
        self.storage = StorageConfig.for_testing(self._tmpdir)
        os.makedirs(self.storage.pdfs_dir, exist_ok=True)
        os.makedirs(self.storage.thumbs_dir, exist_ok=True)
        os.makedirs(self.storage.processing_dir, exist_ok=True)
        self.db = PRKSDatabase(storage=self.storage, schema_path=_SCHEMA_PATH)
        self.index = PRKSTextIndex(storage=self.storage)

    def tearDown(self):
        _drop_trigger(self.db, _ABORT_WORKS)
        _drop_trigger(self.db, _ABORT_TAGS)
        if getattr(self, "_tmpdir", None):
            shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _add_managed_work(self, title: str) -> tuple[str, str, str]:
        fname = f"{uuid.uuid4().hex}.pdf"
        pdf_abs = os.path.join(self.storage.pdfs_dir, fname)
        with open(pdf_abs, "wb") as f:
            f.write(b"%PDF-1.4\n%DEL\n%%EOF\n")
        w_id = self.db.add_work(title=title, file_path=f"/api/pdfs/{fname}")
        thumb = os.path.join(self.storage.thumbs_dir, f"{prks_thumb_cache_stem(w_id, 1)}.webp")
        with open(thumb, "wb") as f:
            f.write(b"t")
        self.index.upsert_text(w_id, f"indexed body for {title}")
        return w_id, pdf_abs, thumb

    def _write_pdf(self, filename: str) -> str:
        pdf_abs = os.path.join(self.storage.pdfs_dir, filename)
        with open(pdf_abs, "wb") as f:
            f.write(b"%PDF-1.4\n%DEL\n%%EOF\n")
        return pdf_abs

    def _delete_capturing(self, work_id: str):
        captured = []
        real = self.db.delete_work_record

        def wrap(wid):
            rec = real(wid)
            captured.append(rec)
            return rec

        self.db.delete_work_record = wrap
        try:
            result = delete_work(self.db, self.index, work_id)
        finally:
            self.db.delete_work_record = real
        return result, captured[0] if captured else None

    def test_db_abort_preserves_work_pdf_thumb_and_index(self):
        w_id, pdf_abs, thumb = self._add_managed_work("AbortKeep")
        _install_abort_trigger(self.db, _ABORT_WORKS, "works")
        with self.assertRaises(sqlite3.IntegrityError):
            delete_work(self.db, self.index, w_id)
        self.assertIsNotNone(self.db.get_work(w_id))
        self.assertTrue(os.path.isfile(pdf_abs))
        self.assertTrue(os.path.isfile(thumb))
        self.assertTrue(_index_has(self.index, w_id))

    def test_pdf_cleanup_failure_after_commit(self):
        w_id, pdf_abs, thumb = self._add_managed_work("PdfFail")
        real_remove = os.remove

        def pdf_remove(path, *args, **kwargs):
            if os.path.realpath(path) == os.path.realpath(pdf_abs):
                raise OSError("forced pdf cleanup failure")
            return real_remove(path, *args, **kwargs)

        with patch("backend.work_deletion.os.remove", side_effect=pdf_remove):
            result = delete_work(self.db, self.index, w_id)
        self.assertTrue(result.existed)
        self.assertEqual(result.cleanup_failures, ("pdf",))
        self.assertIsNone(self.db.get_work(w_id))
        self.assertFalse(_index_has(self.index, w_id))
        self.assertFalse(os.path.isfile(thumb))
        self.assertTrue(os.path.isfile(pdf_abs))

    def test_thumbnail_cleanup_failure_continues(self):
        w_id, pdf_abs, thumb_fail = self._add_managed_work("ThumbFail")
        thumb_ok = os.path.join(
            self.storage.thumbs_dir,
            f"{prks_thumb_cache_stem(w_id, 2)}.png",
        )
        with open(thumb_ok, "wb") as f:
            f.write(b"ok")
        real_remove = os.remove

        def thumb_remove(path, *args, **kwargs):
            if os.path.realpath(path) == os.path.realpath(thumb_fail):
                raise OSError("forced thumbnail cleanup failure")
            return real_remove(path, *args, **kwargs)

        with self.assertLogs("prks.work_deletion", level="WARNING") as logs:
            with patch("backend.db_manager.os.remove", side_effect=thumb_remove):
                result = delete_work(self.db, self.index, w_id)
        self.assertTrue(result.existed)
        self.assertEqual(result.cleanup_failures, ("thumbnails",))
        self.assertIsNone(self.db.get_work(w_id))
        self.assertFalse(_index_has(self.index, w_id))
        self.assertTrue(os.path.isfile(thumb_fail))
        self.assertFalse(os.path.isfile(thumb_ok))
        self.assertFalse(os.path.isfile(pdf_abs))
        self.assertTrue(
            any("work_delete_thumbnails_cleanup_failed" in line for line in logs.output)
        )

    def test_text_index_cleanup_failure_continues(self):
        w_id, pdf_abs, thumb = self._add_managed_work("IndexFail")
        boom = _BoomIndex()
        result = delete_work(self.db, boom, w_id)
        self.assertTrue(result.existed)
        self.assertEqual(result.cleanup_failures, ("text_index",))
        self.assertIsNone(self.db.get_work(w_id))
        self.assertFalse(os.path.isfile(thumb))
        self.assertFalse(os.path.isfile(pdf_abs))
        self.assertTrue(_index_has(self.index, w_id))

    def test_missing_artifacts_still_succeed(self):
        fname = f"{uuid.uuid4().hex}.pdf"
        w_id = self.db.add_work(title="GhostPdf", file_path=f"/api/pdfs/{fname}")
        result = delete_work(self.db, self.index, w_id)
        self.assertTrue(result.existed)
        self.assertEqual(result.cleanup_failures, ())
        self.assertIsNone(self.db.get_work(w_id))

    def test_missing_work_clears_stale_derived_state(self):
        w_id = f"W-{uuid.uuid4().hex[:8].upper()}"
        thumb = os.path.join(self.storage.thumbs_dir, f"{prks_thumb_cache_stem(w_id, 1)}.webp")
        with open(thumb, "wb") as f:
            f.write(b"stale")
        self.index.upsert_text(w_id, "stale derived index")
        stray_pdf = os.path.join(self.storage.pdfs_dir, "should-not-delete.pdf")
        with open(stray_pdf, "wb") as f:
            f.write(b"x")
        result = delete_work(self.db, self.index, w_id)
        self.assertFalse(result.existed)
        self.assertEqual(result.cleanup_failures, ())
        self.assertFalse(os.path.isfile(thumb))
        self.assertFalse(_index_has(self.index, w_id))
        self.assertTrue(os.path.isfile(stray_pdf))

    def test_missing_work_index_cleanup_failure_is_recorded(self):
        w_id = f"W-{uuid.uuid4().hex[:8].upper()}"
        result = delete_work(self.db, _BoomIndex(), w_id)
        self.assertFalse(result.existed)
        self.assertEqual(result.cleanup_failures, ("text_index",))

    def test_thumbnail_helper_listdir_failure_is_reported(self):
        w_id, pdf_abs, thumb = self._add_managed_work("ListFail")
        with patch("backend.db_manager.os.listdir", side_effect=OSError("cannot list")):
            failed = prks_delete_pdf_thumbnails_for_work_id(w_id, self.storage.thumbs_dir)
        self.assertEqual(failed, (self.storage.thumbs_dir,))
        self.assertTrue(os.path.isfile(thumb))
        self.assertTrue(os.path.isfile(pdf_abs))

    def test_tag_used_only_by_deleted_work_is_pruned(self):
        w_id = self.db.add_work(title="TaggedGone")
        tid = self.db.add_tag("OnlyHere", "#333")["id"]
        self.db.add_tag_to_work(w_id, tid)
        self.db.delete_work_record(w_id)
        rows = self.db.execute_query("SELECT id FROM tags WHERE id = ?", (tid,))
        self.assertEqual(len(rows), 0)

    def test_tag_shared_with_another_work_is_kept(self):
        w1 = self.db.add_work(title="A")
        w2 = self.db.add_work(title="B")
        tid = self.db.add_tag("Shared", "#222")["id"]
        self.db.add_tag_to_work(w1, tid)
        self.db.add_tag_to_work(w2, tid)
        self.db.delete_work_record(w1)
        rows = self.db.execute_query("SELECT id FROM tags WHERE id = ?", (tid,))
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(self.db.get_work(w2))

    def test_tag_used_by_folder_is_kept(self):
        w_id = self.db.add_work(title="FolderTagged")
        folder_id = self.db.add_folder(title="KeepTagFolder", description="")
        tid = self.db.add_tag("OnFolder", "#111")["id"]
        self.db.add_tag_to_work(w_id, tid)
        self.db.add_tag_to_folder(folder_id, tid)
        self.db.delete_work_record(w_id)
        rows = self.db.execute_query("SELECT id FROM tags WHERE id = ?", (tid,))
        self.assertEqual(len(rows), 1)

    def test_tag_prune_abort_rolls_back_work_and_links(self):
        w_id = self.db.add_work(title="PruneAbort")
        tid = self.db.add_tag("DoNotDrop", "#444")["id"]
        self.db.add_tag_to_work(w_id, tid)
        _install_abort_trigger(self.db, _ABORT_TAGS, "tags")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.delete_work_record(w_id)
        self.assertIsNotNone(self.db.get_work(w_id))
        links = self.db.execute_query(
            "SELECT 1 FROM work_tags WHERE work_id = ? AND tag_id = ?",
            (w_id, tid),
        )
        self.assertTrue(links)
        tags = self.db.execute_query("SELECT id FROM tags WHERE id = ?", (tid,))
        self.assertEqual(len(tags), 1)

    def test_import_rollback_removes_work_and_destination_when_db_delete_succeeds(self):
        processing_root = self.storage.processing_dir
        pdf_path = os.path.join(processing_root, "ok-roll.pdf")
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-1.4\n%OKROLL\n%%EOF\n")
        staged = self.db.scan_processing_files()
        row = staged[0]
        with patch.object(self.db, "add_work_to_folder", side_effect=RuntimeError("link fail")):
            with self.assertRaises(ValueError) as ctx:
                self.db.import_processing_file(row["id"])
        self.assertIn("link fail", str(ctx.exception))
        self.assertEqual(self.db.execute_query("SELECT id FROM works"), [])
        leftovers = [n for n in os.listdir(self.storage.pdfs_dir) if n.lower().endswith(".pdf")]
        self.assertEqual(leftovers, [])
        self.assertTrue(os.path.isfile(pdf_path))
        pf = self.db.execute_query(
            "SELECT status, last_error FROM processing_files WHERE id = ?",
            (row["id"],),
        )
        self.assertEqual(pf[0]["status"], "error")
        self.assertIn("link fail", pf[0]["last_error"] or "")

    def test_import_rollback_keeps_destination_when_db_delete_fails(self):
        processing_root = self.storage.processing_dir
        pdf_path = os.path.join(processing_root, "keep-dest.pdf")
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-1.4\n%KEEPDEST\n%%EOF\n")
        staged = self.db.scan_processing_files()
        row = staged[0]
        _install_abort_trigger(self.db, _ABORT_WORKS, "works")
        with patch.object(self.db, "add_work_to_folder", side_effect=RuntimeError("link fail")):
            with self.assertRaises(ValueError) as ctx:
                self.db.import_processing_file(row["id"])
        self.assertIn("link fail", str(ctx.exception))
        works = self.db.execute_query("SELECT id, file_path FROM works")
        self.assertEqual(len(works), 1)
        filename = (works[0]["file_path"] or "").split("/")[-1]
        dest = os.path.join(self.storage.pdfs_dir, filename)
        self.assertTrue(os.path.isfile(dest))
        self.assertTrue(os.path.isfile(pdf_path))
        pf = self.db.execute_query(
            "SELECT status FROM processing_files WHERE id = ?",
            (row["id"],),
        )
        self.assertEqual(pf[0]["status"], "error")

    def test_duplicate_canonical_pdf_kept_until_last_work(self):
        pdf_abs = self._write_pdf("shared.pdf")
        a_id = self.db.add_work(title="ShareA", file_path="/api/pdfs/shared.pdf")
        b_id = self.db.add_work(title="ShareB", file_path="/api/pdfs/shared.pdf")
        result, rec = self._delete_capturing(b_id)
        self.assertTrue(result.existed)
        self.assertEqual(result.cleanup_failures, ())
        self.assertTrue(rec.managed_pdf_still_referenced)
        self.assertIsNone(self.db.get_work(b_id))
        self.assertIsNotNone(self.db.get_work(a_id))
        self.assertTrue(os.path.isfile(pdf_abs))
        result_a = delete_work(self.db, self.index, a_id)
        self.assertTrue(result_a.existed)
        self.assertEqual(result_a.cleanup_failures, ())
        self.assertFalse(os.path.isfile(pdf_abs))

    def test_surviving_nested_legacy_alias_keeps_pdf(self):
        pdf_abs = self._write_pdf("victim.pdf")
        a_id = self.db.add_work(title="Canon", file_path="/api/pdfs/victim.pdf")
        b_id = self.db.add_work(title="Nested", file_path="/api/pdfs/subdir/victim.pdf")
        result, rec = self._delete_capturing(a_id)
        self.assertTrue(result.existed)
        self.assertEqual(result.cleanup_failures, ())
        self.assertTrue(rec.managed_pdf_still_referenced)
        self.assertIsNone(self.db.get_work(a_id))
        self.assertIsNotNone(self.db.get_work(b_id))
        self.assertTrue(os.path.isfile(pdf_abs))

    def test_surviving_traversal_legacy_alias_keeps_pdf(self):
        pdf_abs = self._write_pdf("victim.pdf")
        a_id = self.db.add_work(title="Canon", file_path="/api/pdfs/victim.pdf")
        b_id = self.db.add_work(title="Traversal", file_path="/api/pdfs/../victim.pdf")
        result, rec = self._delete_capturing(a_id)
        self.assertTrue(result.existed)
        self.assertEqual(result.cleanup_failures, ())
        self.assertTrue(rec.managed_pdf_still_referenced)
        self.assertIsNone(self.db.get_work(a_id))
        self.assertIsNotNone(self.db.get_work(b_id))
        self.assertTrue(os.path.isfile(pdf_abs))

    def test_surviving_encoded_alias_keeps_pdf(self):
        pdf_abs = self._write_pdf("victim.pdf")
        a_id = self.db.add_work(title="Canon", file_path="/api/pdfs/victim.pdf")
        b_id = self.db.add_work(title="Encoded", file_path="/api/pdfs/foo%2Fvictim.pdf")
        result, rec = self._delete_capturing(a_id)
        self.assertTrue(result.existed)
        self.assertEqual(result.cleanup_failures, ())
        self.assertTrue(rec.managed_pdf_still_referenced)
        self.assertIsNone(self.db.get_work(a_id))
        self.assertIsNotNone(self.db.get_work(b_id))
        self.assertTrue(os.path.isfile(pdf_abs))

    def test_malformed_deleted_row_cannot_claim_owned_pdf(self):
        pdf_abs = self._write_pdf("victim.pdf")
        owner_id = self.db.add_work(title="Owner", file_path="/api/pdfs/victim.pdf")
        malformed = (
            "/api/pdfs/../victim.pdf",
            "/api/pdfs/subdir/victim.pdf",
            "/api/pdfs/foo%2Fvictim.pdf",
            "/api/pdfs/foo%5Cvictim.pdf",
        )
        for i, path in enumerate(malformed):
            w_id = self.db.add_work(title=f"Bad{i}", file_path=path)
            result, rec = self._delete_capturing(w_id)
            self.assertTrue(result.existed)
            self.assertEqual(result.cleanup_failures, ())
            self.assertFalse(rec.managed_pdf_still_referenced)
            self.assertIsNone(self.db.get_work(w_id))
            self.assertIsNotNone(self.db.get_work(owner_id))
            self.assertTrue(os.path.isfile(pdf_abs))

    def test_nul_file_path_row_deletes_without_pdf_failure(self):
        pdf_abs = self._write_pdf("victim.pdf")
        owner_id = self.db.add_work(title="Owner", file_path="/api/pdfs/victim.pdf")
        try:
            w_id = self.db.add_work(title="NulPath", file_path="/api/pdfs/bad\x00name.pdf")
        except (ValueError, sqlite3.Error):
            self.skipTest("SQLite rejected embedded NUL in file_path")
        stored = self.db.execute_query(
            "SELECT file_path FROM works WHERE id = ?",
            (w_id,),
        )
        if not stored or "\x00" not in (stored[0]["file_path"] or ""):
            self.db.delete_work_record(w_id)
            self.skipTest("SQLite did not store embedded NUL in file_path")
        result, rec = self._delete_capturing(w_id)
        self.assertTrue(result.existed)
        self.assertEqual(result.cleanup_failures, ())
        self.assertFalse(rec.managed_pdf_still_referenced)
        self.assertIsNone(self.db.get_work(w_id))
        self.assertIsNotNone(self.db.get_work(owner_id))
        self.assertTrue(os.path.isfile(pdf_abs))

    def test_unique_canonical_path_still_cleans_pdf_index_and_thumbs(self):
        w_id, pdf_abs, thumb = self._add_managed_work("UniquePdf")
        result, rec = self._delete_capturing(w_id)
        self.assertTrue(result.existed)
        self.assertEqual(result.cleanup_failures, ())
        self.assertFalse(rec.managed_pdf_still_referenced)
        self.assertIsNone(self.db.get_work(w_id))
        self.assertFalse(_index_has(self.index, w_id))
        self.assertFalse(os.path.isfile(thumb))
        self.assertFalse(os.path.isfile(pdf_abs))

    def test_surviving_whitespace_alias_keeps_pdf(self):
        pdf_abs = self._write_pdf("victim.pdf")
        a_id = self.db.add_work(title="Canon", file_path="/api/pdfs/victim.pdf")
        b_id = self.db.add_work(title="Padded", file_path=" /api/pdfs/victim.pdf ")
        result, rec = self._delete_capturing(a_id)
        self.assertTrue(result.existed)
        self.assertEqual(result.cleanup_failures, ())
        self.assertTrue(rec.managed_pdf_still_referenced)
        self.assertIsNone(self.db.get_work(a_id))
        self.assertIsNotNone(self.db.get_work(b_id))
        self.assertTrue(os.path.isfile(pdf_abs))


class TestManagedPdfPathHelpers(unittest.TestCase):
    def test_managed_pdf_filename_canonical_success(self):
        self.assertEqual(managed_pdf_filename("/api/pdfs/example.pdf"), "example.pdf")

    def test_managed_pdf_filename_rejects_noncanonical(self):
        rejected = (
            "/api/pdfs/../example.pdf",
            "/api/pdfs/sub/example.pdf",
            "/api/pdfs/foo%2Fexample.pdf",
            "/api/pdfs/foo%5Cexample.pdf",
            "/api/pdfs/..",
            "/api/pdfs/%2e%2e",
            " /api/pdfs/victim.pdf ",
            "/api/pdfs/bad\x00name.pdf",
        )
        for path in rejected:
            with self.subTest(path=path):
                self.assertIsNone(managed_pdf_filename(path))

    def test_referenced_managed_pdf_filename_pins(self):
        pinned = (
            "/api/pdfs/victim.pdf",
            "/api/pdfs/subdir/victim.pdf",
            "/api/pdfs/../victim.pdf",
            "/api/pdfs/foo%2Fvictim.pdf",
            " /api/pdfs/victim.pdf ",
            "\t/api/pdfs/victim.pdf\n",
        )
        for path in pinned:
            with self.subTest(path=path):
                self.assertEqual(referenced_managed_pdf_filename(path), "victim.pdf")

    def test_safe_pdf_path_under_dir_rejects_nul(self):
        pdfs = tempfile.mkdtemp()
        try:
            self.assertIsNone(safe_pdf_path_under_dir(pdfs, "bad\x00name.pdf"))
        finally:
            shutil.rmtree(pdfs)


if __name__ == "__main__":
    unittest.main()
