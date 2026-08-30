import inspect
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import uuid
from contextlib import contextmanager
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.db_manager import PRKS_SCHEMA_VERSION, PRKSDatabase
from backend.log_safety import PrivacySafeFormatter
from backend.server import run_server
from backend.storage.config import StorageConfig
from backend.performance import snapshot, reset as reset_perf
from backend.text_index import (
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_INDEXED,
    STATUS_LEGACY,
    TEXT_EXTRACTOR_VERSION,
    TEXT_INDEX_SCHEMA_VERSION,
    PDFTextExtraction,
    PDFTextExtractionError,
    PRKSTextIndex,
    _MAX_EXTRACTED_CHARS,
    _SYNC_STATE_COLUMNS,
    extract_pdf,
    reconcile_at_startup,
)

_SCHEMA_PATH = os.path.join(_PROJECT_DIR, "backend", "db_schema.sql")

_LEGACY_INDEX_SQL = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS work_text_index (
    work_id TEXT PRIMARY KEY,
    extracted_text TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE VIRTUAL TABLE IF NOT EXISTS work_text_index_fts USING fts5(
    extracted_text,
    content='work_text_index',
    content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS work_text_index_ai AFTER INSERT ON work_text_index BEGIN
    INSERT INTO work_text_index_fts(rowid, extracted_text)
    VALUES (new.rowid, new.extracted_text);
END;
CREATE TRIGGER IF NOT EXISTS work_text_index_ad AFTER DELETE ON work_text_index BEGIN
    INSERT INTO work_text_index_fts(work_text_index_fts, rowid, extracted_text)
    VALUES ('delete', old.rowid, old.extracted_text);
END;
CREATE TRIGGER IF NOT EXISTS work_text_index_au AFTER UPDATE ON work_text_index BEGIN
    INSERT INTO work_text_index_fts(work_text_index_fts, rowid, extracted_text)
    VALUES ('delete', old.rowid, old.extracted_text);
    INSERT INTO work_text_index_fts(rowid, extracted_text)
    VALUES (new.rowid, new.extracted_text);
END;
"""


def _pdf_with_text_bytes(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text or "")
    out = doc.tobytes()
    doc.close()
    return out


def _empty_pdf_bytes() -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page()
    out = doc.tobytes()
    doc.close()
    return out


@contextmanager
def _capture_logs(logger_name: str, level: int = logging.WARNING):
    logger = logging.getLogger(logger_name)
    records: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    handler = _Handler()
    handler.setLevel(level)
    handler.setFormatter(PrivacySafeFormatter("%(levelname)s %(name)s %(message)s"))
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(level)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)


class TextIndexTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="prks-text-index-")
        self.storage = StorageConfig.for_testing(self._tmpdir)
        os.makedirs(self.storage.pdfs_dir, exist_ok=True)
        os.makedirs(self.storage.thumbs_dir, exist_ok=True)
        os.makedirs(self.storage.processing_dir, exist_ok=True)
        self.db = PRKSDatabase(storage=self.storage, schema_path=_SCHEMA_PATH)

    def tearDown(self):
        if getattr(self, "_tmpdir", None):
            shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _index(self) -> PRKSTextIndex:
        return PRKSTextIndex(storage=self.storage)

    def _add_pdf_work(self, text: str, filename: str | None = None) -> tuple[str, str]:
        fname = filename or f"{uuid.uuid4().hex}.pdf"
        abs_pdf = os.path.join(self.storage.pdfs_dir, fname)
        with open(abs_pdf, "wb") as handle:
            handle.write(_pdf_with_text_bytes(text))
        w_id = self.db.add_work(title="Indexed", file_path=f"/api/pdfs/{fname}")
        return w_id, f"/api/pdfs/{fname}"

    def _add_empty_pdf_work(self, filename: str | None = None) -> tuple[str, str]:
        fname = filename or f"{uuid.uuid4().hex}.pdf"
        abs_pdf = os.path.join(self.storage.pdfs_dir, fname)
        with open(abs_pdf, "wb") as handle:
            handle.write(_empty_pdf_bytes())
        w_id = self.db.add_work(title="Empty", file_path=f"/api/pdfs/{fname}")
        return w_id, f"/api/pdfs/{fname}"

    def _row(self, index: PRKSTextIndex, work_id: str) -> sqlite3.Row | None:
        with index._conn() as conn:
            return conn.execute(
                "SELECT * FROM work_text_index WHERE work_id = ?",
                (work_id,),
            ).fetchone()

    def _meta(self, index: PRKSTextIndex, key: str) -> str | None:
        with index._conn() as conn:
            row = conn.execute(
                "SELECT value FROM text_index_meta WHERE key = ?",
                (key,),
            ).fetchone()
        return None if row is None else str(row["value"])

    def _write_legacy_index(self, work_id: str, text: str) -> None:
        conn = sqlite3.connect(self.storage.index_db_path)
        try:
            conn.executescript(_LEGACY_INDEX_SQL)
            conn.execute(
                "INSERT INTO work_text_index (work_id, extracted_text) VALUES (?, ?)",
                (work_id, text),
            )
            conn.commit()
        finally:
            conn.close()

    def test_main_schema_version_unchanged(self):
        self.assertEqual(PRKS_SCHEMA_VERSION, 10)
        self.assertEqual(TEXT_INDEX_SCHEMA_VERSION, 2)
        self.assertEqual(TEXT_EXTRACTOR_VERSION, 1)

    def test_fresh_index_has_current_schema(self):
        index = self._index()
        self.assertEqual(self._meta(index, "schema_version"), str(TEXT_INDEX_SCHEMA_VERSION))
        self.assertEqual(self._meta(index, "extractor_version"), str(TEXT_EXTRACTOR_VERSION))
        with index._conn() as conn:
            cols = {
                r["name"]
                for r in conn.execute("PRAGMA table_info(work_text_index)").fetchall()
            }
        self.assertIn("source_ref_hash", cols)
        self.assertIn("extraction_status", cols)

    def test_legacy_index_upgrade_preserves_text(self):
        w_id, file_path = self._add_pdf_work("legacy searchable token omega")
        self._write_legacy_index(w_id, "legacy searchable token omega")
        index = self._index()
        row = self._row(index, w_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["extraction_status"], STATUS_LEGACY)
        self.assertIn("legacy searchable token omega", row["extracted_text"])
        self.assertIsNone(row["source_ref_hash"])
        self.assertIn(w_id, index.search_work_ids("omega"))
        summary = index.reconcile_all(self.db, force=False)
        self.assertGreaterEqual(summary["indexed"], 1)
        row = self._row(index, w_id)
        self.assertEqual(row["extraction_status"], STATUS_INDEXED)
        self.assertTrue(row["source_ref_hash"])
        self.assertIn(w_id, index.search_work_ids("omega"))

    def test_malformed_schema_recreates_without_touching_canonical(self):
        w_id, file_path = self._add_pdf_work("canonical keep term zeta")
        pdf_abs = os.path.join(self.storage.pdfs_dir, file_path.split("/")[-1])
        with open(pdf_abs, "rb") as handle:
            pdf_before = handle.read()
        conn = sqlite3.connect(self.storage.index_db_path)
        try:
            conn.execute("CREATE TABLE work_text_index (foo INTEGER)")
            conn.commit()
        finally:
            conn.close()
        index = self._index()
        self.assertEqual(index._last_recovery_reason, "schema_invalid")
        self.assertEqual(self._meta(index, "schema_version"), str(TEXT_INDEX_SCHEMA_VERSION))
        summary = index.reconcile_all(self.db, force=False)
        self.assertGreaterEqual(summary["indexed"], 1)
        self.assertIn(w_id, index.search_work_ids("zeta"))
        with open(pdf_abs, "rb") as handle:
            self.assertEqual(handle.read(), pdf_before)
        live = self.db.get_work(w_id)
        self.assertIsNotNone(live)

    def test_corrupt_sqlite_recreates_derived_only(self):
        w_id, file_path = self._add_pdf_work("recover after corrupt term")
        with open(self.storage.index_db_path, "wb") as handle:
            handle.write(b"not a sqlite database")
        index = self._index()
        self.assertEqual(index._last_recovery_reason, "corrupt")
        summary = index.reconcile_all(self.db, force=False)
        self.assertGreaterEqual(summary["indexed"], 1)
        self.assertIn(w_id, index.search_work_ids("corrupt"))
        self.assertIsNotNone(self.db.get_work(w_id))

    def test_valid_empty_pdf_is_not_failure(self):
        w_id, file_path = self._add_empty_pdf_work()
        index = self._index()
        result = index.sync_work(w_id, file_path)
        self.assertEqual(result.action, "empty")
        self.assertEqual(result.status, STATUS_EMPTY)
        row = self._row(index, w_id)
        self.assertEqual(row["extracted_text"], "")
        summary = index.reconcile_all(self.db, force=False)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["empty"], 1)
        with patch("backend.text_index.extract_pdf", side_effect=AssertionError("extract")):
            again = index.reconcile_all(self.db, force=False)
        self.assertEqual(again["unchanged"], 1)
        self.assertEqual(again["failed"], 0)

    def test_extraction_failure_clears_stale_text(self):
        w_id, file_path = self._add_pdf_work("old searchable unique term")
        index = self._index()
        index.sync_work(w_id, file_path)
        self.assertIn(w_id, index.search_work_ids("unique"))
        with patch(
            "backend.text_index.extract_pdf",
            side_effect=PDFTextExtractionError("FileDataError"),
        ):
            result = index.sync_work(w_id, file_path, force=True)
        self.assertEqual(result.action, "failed")
        row = self._row(index, w_id)
        self.assertEqual(row["extraction_status"], STATUS_FAILED)
        self.assertEqual(row["extracted_text"], "")
        self.assertNotIn(w_id, index.search_work_ids("unique"))

    def test_extractor_unavailable_clears_stale_changed_pdf(self):
        w_id, file_path = self._add_pdf_work("old searchable unique term")
        index = self._index()
        index.sync_work(w_id, file_path)
        self.assertIn(w_id, index.search_work_ids("unique"))
        pdf_abs = os.path.join(self.storage.pdfs_dir, file_path.split("/")[-1])
        with open(pdf_abs, "wb") as handle:
            handle.write(_pdf_with_text_bytes("brand new secret term"))
        with (
            patch("backend.text_index.extractor_available", return_value=False),
            patch(
                "backend.text_index.extract_pdf",
                side_effect=AssertionError("must not extract"),
            ),
        ):
            summary = index.reconcile_all(self.db, force=False)
        self.assertTrue(summary["extractor_unavailable"])
        self.assertGreaterEqual(summary["failed"], 1)
        row = self._row(index, w_id)
        self.assertEqual(row["extraction_status"], STATUS_FAILED)
        self.assertEqual(row["extracted_text"], "")
        self.assertNotIn(w_id, index.search_work_ids("unique"))
        self.assertNotIn(w_id, index.search_work_ids("secret"))

    def test_extractor_unavailable_preserves_existing_rows(self):
        w_id, file_path = self._add_pdf_work("keep existing searchable term")
        index = self._index()
        index.sync_work(w_id, file_path)
        self.assertIn(w_id, index.search_work_ids("existing"))
        with (
            patch("backend.text_index.extractor_available", return_value=False),
            patch(
                "backend.text_index.extract_pdf",
                side_effect=AssertionError("must not extract"),
            ),
        ):
            summary = index.reconcile_all(self.db, force=True)
        self.assertTrue(summary["extractor_unavailable"])
        row = self._row(index, w_id)
        self.assertEqual(row["extraction_status"], STATUS_INDEXED)
        self.assertIn("existing", row["extracted_text"])
        self.assertIn(w_id, index.search_work_ids("existing"))

    def test_changed_pdf_is_redetected(self):
        w_id, file_path = self._add_pdf_work("oldtermalpha")
        index = self._index()
        index.sync_work(w_id, file_path)
        pdf_abs = os.path.join(self.storage.pdfs_dir, file_path.split("/")[-1])
        with open(pdf_abs, "wb") as handle:
            handle.write(_pdf_with_text_bytes("newtermbravo"))
        with patch.object(index, "sync_work", wraps=index.sync_work) as synced:
            summary = index.reconcile_all(self.db, force=False)
        synced.assert_called()
        self.assertEqual(summary["updated"], 1)
        self.assertNotIn(w_id, index.search_work_ids("oldtermalpha"))
        self.assertIn(w_id, index.search_work_ids("newtermbravo"))

    def test_file_path_hash_detects_identity_change_with_same_stat(self):
        w_id, file_path_a = self._add_pdf_work("term-from-file-aaaa", "aaaa.pdf")
        path_b = os.path.join(self.storage.pdfs_dir, "bbbb.pdf")
        with open(path_b, "wb") as handle:
            handle.write(_pdf_with_text_bytes("term-from-file-bbbb"))
        index = self._index()
        index.sync_work(w_id, file_path_a)
        st_b = os.stat(path_b)
        with index._conn() as conn:
            conn.execute(
                """
                UPDATE work_text_index
                SET source_size = ?, source_mtime_ns = ?
                WHERE work_id = ?
                """,
                (int(st_b.st_size), int(st_b.st_mtime_ns), w_id),
            )
            conn.commit()
        self.db.update_work_metadata(w_id, {"file_path": "/api/pdfs/bbbb.pdf"})
        result = index.sync_work(w_id, "/api/pdfs/bbbb.pdf")
        self.assertIn(result.action, ("indexed", "empty"))
        self.assertNotIn(w_id, index.search_work_ids("aaaa"))
        self.assertIn(w_id, index.search_work_ids("bbbb"))

    def test_unchanged_pdf_skips_extraction(self):
        w_id, file_path = self._add_pdf_work("stable term")
        index = self._index()
        index.sync_work(w_id, file_path)
        with (
            patch(
                "backend.text_index.extract_pdf",
                side_effect=AssertionError("extract"),
            ),
            patch(
                "backend.text_index.extractor_available",
                side_effect=AssertionError("available"),
            ),
        ):
            summary = index.reconcile_all(self.db, force=False)
        self.assertEqual(summary["unchanged"], 1)
        self.assertEqual(summary["updated"], 0)

    def test_force_rebuild_reextracts_unchanged_pdf(self):
        w_id, file_path = self._add_pdf_work("force rebuild term")
        index = self._index()
        index.sync_work(w_id, file_path)
        calls = {"n": 0}

        def fake_extract(path, max_chars=_MAX_EXTRACTED_CHARS):
            calls["n"] += 1
            return PDFTextExtraction(text="force rebuild term", empty=False, truncated=False)

        with patch("backend.text_index.extract_pdf", side_effect=fake_extract):
            summary = index.reconcile_all(self.db, force=True)
        self.assertGreaterEqual(calls["n"], 1)
        self.assertEqual(summary["updated"], 1)
        self.assertEqual(summary["unchanged"], 0)

    def test_orphan_row_removed(self):
        index = self._index()
        index.upsert_text("W-DEAD", "orphan ghost term")
        self.assertIn("W-DEAD", index.search_work_ids("ghost"))
        summary = index.reconcile_all(self.db, force=False)
        self.assertEqual(summary["removed_orphans"], 1)
        self.assertIsNone(self._row(index, "W-DEAD"))
        self.assertEqual(index.search_work_ids("ghost"), [])

    def test_work_becoming_non_pdf_removes_index_row(self):
        w_id, file_path = self._add_pdf_work("leave when unlinked")
        index = self._index()
        index.sync_work(w_id, file_path)
        self.db.update_work_metadata(w_id, {"file_path": ""})
        summary = index.reconcile_all(self.db, force=False)
        self.assertEqual(summary["removed_orphans"], 1)
        self.assertIsNone(self._row(index, w_id))
        self.assertEqual(index.search_work_ids("unlinked"), [])

    def test_missing_pdf_clears_text_and_keeps_work(self):
        w_id, file_path = self._add_pdf_work("missing file term")
        index = self._index()
        index.sync_work(w_id, file_path)
        os.remove(os.path.join(self.storage.pdfs_dir, file_path.split("/")[-1]))
        summary = index.reconcile_all(self.db, force=False)
        self.assertEqual(summary["missing"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertIsNone(self._row(index, w_id))
        self.assertEqual(index.search_work_ids("missing"), [])
        self.assertIsNotNone(self.db.get_work(w_id))

    def test_failed_status_is_retried(self):
        w_id, file_path = self._add_pdf_work("retry after failure")
        index = self._index()
        with patch(
            "backend.text_index.extract_pdf",
            side_effect=PDFTextExtractionError("FileDataError"),
        ):
            index.sync_work(w_id, file_path)
        self.assertEqual(self._row(index, w_id)["extraction_status"], STATUS_FAILED)
        summary = index.reconcile_all(self.db, force=False)
        self.assertEqual(summary["updated"], 1)
        self.assertEqual(self._row(index, w_id)["extraction_status"], STATUS_INDEXED)
        self.assertIn(w_id, index.search_work_ids("retry"))

    def test_truncation_is_recorded(self):
        w_id, file_path = self._add_pdf_work("short")
        index = self._index()
        stored = "keepvisible " + ("x" * 50)

        def fake_extract(path, max_chars=_MAX_EXTRACTED_CHARS):
            text = stored
            if len(text) > 20:
                return PDFTextExtraction(text=text[:20], empty=False, truncated=True)
            return PDFTextExtraction(text=text, empty=False, truncated=False)

        with patch("backend.text_index.extract_pdf", side_effect=fake_extract):
            result = index.sync_work(w_id, file_path)
        self.assertEqual(result.action, "indexed")
        self.assertTrue(result.truncated)
        row = self._row(index, w_id)
        self.assertEqual(row["truncated"], 1)
        self.assertLessEqual(len(row["extracted_text"]), 20)
        self.assertIn(w_id, index.search_work_ids("keepvisible"))

    def test_extract_pdf_truncates_at_bound(self):
        w_id, file_path = self._add_pdf_work("abcdefghijklmnop")
        pdf_abs = os.path.join(self.storage.pdfs_dir, file_path.split("/")[-1])
        extraction = extract_pdf(pdf_abs, max_chars=8)
        self.assertTrue(extraction.truncated)
        self.assertLessEqual(len(extraction.text), 8)
        self.assertFalse(extraction.empty)

    def test_source_changed_during_extraction_retries_then_commits(self):
        w_id, file_path = self._add_pdf_work("stable after retry")
        index = self._index()
        stats = [(99, 9), (10, 1), (20, 2), (30, 3), (30, 3)]

        def fake_stat(_path):
            return stats.pop(0)

        with (
            patch.object(index, "_stat_source", side_effect=fake_stat),
            patch(
                "backend.text_index.extract_pdf",
                return_value=PDFTextExtraction(
                    text="stable after retry", empty=False, truncated=False
                ),
            ),
        ):
            result = index.sync_work(w_id, file_path, force=True)
        self.assertEqual(result.action, "indexed")
        self.assertEqual(self._row(index, w_id)["source_size"], 30)
        self.assertIn(w_id, index.search_work_ids("stable"))

    def test_source_changed_twice_does_not_commit_text(self):
        w_id, file_path = self._add_pdf_work("should not remain")
        index = self._index()
        index.sync_work(w_id, file_path)
        stats = [(99, 9), (10, 1), (20, 2), (30, 3), (40, 4)]

        def fake_stat(_path):
            return stats.pop(0)

        with (
            patch.object(index, "_stat_source", side_effect=fake_stat),
            patch(
                "backend.text_index.extract_pdf",
                return_value=PDFTextExtraction(
                    text="new unstable text", empty=False, truncated=False
                ),
            ),
        ):
            result = index.sync_work(w_id, file_path, force=True)
        self.assertEqual(result.action, "failed")
        row = self._row(index, w_id)
        self.assertEqual(row["extraction_status"], STATUS_FAILED)
        self.assertEqual(row["extracted_text"], "")
        self.assertNotIn(w_id, index.search_work_ids("unstable"))
        self.assertNotIn(w_id, index.search_work_ids("remain"))

    def test_fts_inconsistency_not_scanned_on_healthy_startup(self):
        w_id, file_path = self._add_pdf_work("original fts term")
        index = self._index()
        index.sync_work(w_id, file_path)
        conn = sqlite3.connect(index.db_path)
        try:
            conn.execute(
                "INSERT INTO work_text_index_fts(work_text_index_fts) VALUES('delete-all')"
            )
            conn.commit()
        finally:
            conn.close()
        self.assertNotIn(w_id, index.search_work_ids("original"))
        with (
            patch(
                "backend.text_index.extract_pdf",
                side_effect=AssertionError("extract"),
            ),
            patch(
                "backend.text_index.extractor_available",
                side_effect=AssertionError("available"),
            ),
            patch.object(
                index, "_fts_integrity_ok", side_effect=AssertionError("fts")
            ) as chk,
        ):
            summary = index.reconcile_all(self.db, force=False)
        chk.assert_not_called()
        self.assertFalse(summary["fts_rebuilt"])
        self.assertNotIn(w_id, index.search_work_ids("original"))

    def test_fts_suspect_reconcile_rebuilds_without_reextract(self):
        w_id, file_path = self._add_pdf_work("original fts term")
        index = self._index()
        index.sync_work(w_id, file_path)
        conn = sqlite3.connect(index.db_path)
        try:
            conn.execute(
                "INSERT INTO work_text_index_fts(work_text_index_fts) VALUES('delete-all')"
            )
            conn.commit()
        finally:
            conn.close()
        self.assertNotIn(w_id, index.search_work_ids("original"))
        index._fts_suspect = True
        with patch(
            "backend.text_index.extract_pdf",
            side_effect=AssertionError("extract"),
        ):
            summary = index.reconcile_all(self.db, force=False)
        self.assertTrue(summary["fts_rebuilt"])
        self.assertFalse(index._fts_suspect)
        self.assertIn(w_id, index.search_work_ids("original"))

    def test_rebuild_all_runs_strong_fts_verification(self):
        w_id, file_path = self._add_pdf_work("rebuild fts term")
        index = self._index()
        index.sync_work(w_id, file_path)
        with patch.object(
            index, "_fts_integrity_ok", wraps=index._fts_integrity_ok
        ) as chk:
            summary = index.rebuild_all(self.db)
        self.assertGreaterEqual(chk.call_count, 1)
        self.assertIn(w_id, index.search_work_ids("rebuild"))
        self.assertGreaterEqual(summary["updated"], 1)

    def test_extractor_version_mismatch_reextracts(self):
        w_id, file_path = self._add_pdf_work("version contract term")
        index = self._index()
        index.sync_work(w_id, file_path)
        with index._conn() as conn:
            conn.execute(
                "UPDATE work_text_index SET extractor_version = 0 WHERE work_id = ?",
                (w_id,),
            )
            conn.commit()
        with patch("backend.text_index.extract_pdf", wraps=extract_pdf) as wrapped:
            summary = index.reconcile_all(self.db, force=False)
        self.assertEqual(summary["updated"], 1)
        wrapped.assert_called()
        self.assertEqual(self._row(index, w_id)["extractor_version"], TEXT_EXTRACTOR_VERSION)

    def test_unchanged_reconcile_constant_index_work(self):
        n_files = 100
        index = self._index()
        for i in range(n_files):
            self._add_pdf_work(f"bulkterm{i:03d}", filename=f"bulk_{i:03d}.pdf")
        primed = index.reconcile_all(self.db, force=False)
        self.assertEqual(primed["updated"], n_files)
        orig = index._conn
        counts = {"n": 0}

        def wrapped_conn():
            counts["n"] += 1
            return orig()

        with (
            patch.object(index, "_conn", side_effect=wrapped_conn),
            patch(
                "backend.text_index.extract_pdf",
                side_effect=AssertionError("extract"),
            ),
            patch(
                "backend.text_index.extractor_available",
                side_effect=AssertionError("available"),
            ),
            patch.object(
                index, "_fts_integrity_ok", side_effect=AssertionError("fts")
            ),
        ):
            summary = index.reconcile_all(self.db, force=False)
        self.assertEqual(summary["unchanged"], n_files)
        self.assertEqual(summary["updated"], 0)
        self.assertEqual(counts["n"], 1)
        self.assertNotIn("extracted_text", _SYNC_STATE_COLUMNS)
        self.assertNotIn("extracted_text", inspect.getsource(PRKSTextIndex._load_sync_state))

        index.upsert_text("W-ORPHAN-BATCH", "orphan token")
        with (
            patch.object(index, "remove_work", side_effect=AssertionError("per-orphan")),
            patch(
                "backend.text_index.extract_pdf",
                side_effect=AssertionError("extract"),
            ),
            patch.object(
                index, "_fts_integrity_ok", side_effect=AssertionError("fts")
            ),
        ):
            orphaned = index.reconcile_all(self.db, force=False)
        self.assertEqual(orphaned["removed_orphans"], 1)
        self.assertEqual(orphaned["unchanged"], n_files)
        self.assertIsNone(self._row(index, "W-ORPHAN-BATCH"))

    def test_unchanged_reconcile_omits_extract_and_fts_spans(self):
        w_id, file_path = self._add_pdf_work("span skip term")
        index = self._index()
        index.sync_work(w_id, file_path)
        reset_perf()
        with (
            patch(
                "backend.text_index.extract_pdf",
                side_effect=AssertionError("extract"),
            ),
            patch(
                "backend.text_index.extractor_available",
                side_effect=AssertionError("available"),
            ),
        ):
            summary = index.reconcile_all(self.db, force=False)
        self.assertEqual(summary["unchanged"], 1)
        snap = snapshot()
        self.assertGreaterEqual(snap["spans"].get("text_index_reconcile", {}).get("count", 0), 1)
        self.assertGreaterEqual(snap["spans"].get("text_index_load_state", {}).get("count", 0), 1)
        self.assertGreaterEqual(snap["spans"].get("text_index_source_scan", {}).get("count", 0), 1)
        self.assertNotIn("text_index_extract", snap["spans"])
        self.assertNotIn("text_index_fts_verify", snap["spans"])


    def test_trigger_drift_recreates_derived_index(self):
        w_id, file_path = self._add_pdf_work("trigger drift term")
        index = self._index()
        index.sync_work(w_id, file_path)
        pdf_abs = os.path.join(self.storage.pdfs_dir, file_path.split("/")[-1])
        with open(pdf_abs, "rb") as handle:
            pdf_before = handle.read()
        conn = sqlite3.connect(index.db_path)
        try:
            conn.execute("DROP TRIGGER work_text_index_ai")
            conn.commit()
        finally:
            conn.close()
        recovered = PRKSTextIndex(storage=self.storage)
        self.assertEqual(recovered._last_recovery_reason, "schema_invalid")
        recovered.reconcile_all(self.db, force=False)
        self.assertIn(w_id, recovered.search_work_ids("drift"))
        with open(pdf_abs, "rb") as handle:
            self.assertEqual(handle.read(), pdf_before)

    def test_search_operational_error_is_fail_soft_and_logged_safely(self):
        w_id, file_path = self._add_pdf_work("still searchable elsewhere")
        index = self._index()
        index.sync_work(w_id, file_path)
        secret = "SECRET_SEARCH_QUERY_X9Q7"
        boom = sqlite3.OperationalError(f"disk image {secret} /tmp/secret-path.db")

        class BoomConn:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, *args, **kwargs):
                raise boom

        with (
            patch.object(index, "_conn", return_value=BoomConn()),
            _capture_logs("prks.text_index") as records,
        ):
            ids = index.search_work_ids(secret)
        self.assertEqual(ids, [])
        joined = "\n".join(records)
        self.assertIn("text_index_search_failed", joined)
        self.assertIn("OperationalError", joined)
        self.assertNotIn(secret, joined)
        self.assertNotIn("/tmp/secret-path.db", joined)
        self.assertNotIn("disk image", joined)
        self.assertTrue(index._fts_suspect)

    def test_startup_reconciliation_repairs_stale_and_orphans(self):
        w_id, file_path = self._add_pdf_work("startup old term")
        index = self._index()
        index.sync_work(w_id, file_path)
        index.upsert_text("W-ORPHAN", "startup orphan term")
        pdf_abs = os.path.join(self.storage.pdfs_dir, file_path.split("/")[-1])
        with open(pdf_abs, "wb") as handle:
            handle.write(_pdf_with_text_bytes("startup new term"))
        with patch(
            "backend.text_index.extract_pdf",
            wraps=extract_pdf,
        ) as wrapped:
            summary = reconcile_at_startup(self.db, index)
        self.assertEqual(summary["removed_orphans"], 1)
        self.assertEqual(summary["updated"], 1)
        self.assertIsNone(self._row(index, "W-ORPHAN"))
        self.assertIn(w_id, index.search_work_ids("startup new"))
        self.assertNotIn(w_id, index.search_work_ids("startup old"))
        wrapped.assert_called()

    def test_startup_skips_unchanged(self):
        w_id, file_path = self._add_pdf_work("startup skip term")
        index = self._index()
        index.sync_work(w_id, file_path)
        with (
            patch(
                "backend.text_index.extract_pdf",
                side_effect=AssertionError("extract"),
            ),
            patch(
                "backend.text_index.extractor_available",
                side_effect=AssertionError("available"),
            ),
        ):
            summary = reconcile_at_startup(self.db, index)
        self.assertEqual(summary["unchanged"], 1)
        self.assertEqual(summary["updated"], 0)

    def test_run_server_reconciles_before_serving(self):
        src = inspect.getsource(run_server)
        self.assertLess(src.index("reconcile_at_startup"), src.index("serve_forever"))

    def test_upsert_text_is_not_treated_as_synchronized(self):
        w_id, file_path = self._add_pdf_work("canonical extract unique")
        index = self._index()
        index.upsert_text(w_id, "fixtureonlyterm")
        self.assertEqual(self._row(index, w_id)["extraction_status"], STATUS_LEGACY)
        summary = index.reconcile_all(self.db, force=False)
        self.assertEqual(summary["updated"], 1)
        self.assertNotIn(w_id, index.search_work_ids("fixtureonlyterm"))
        self.assertIn(w_id, index.search_work_ids("canonical"))


if __name__ == "__main__":
    unittest.main()
