"""Disposable derived research-reference index."""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.db_manager import PRKSDatabase
from backend.research_index import PRKSResearchIndex, content_hash
from backend.research_network import create_argument, list_concepts, save_work_notes
from backend.storage.config import StorageConfig

_SCHEMA_PATH = os.path.join(_PROJECT_DIR, "backend", "db_schema.sql")


class ResearchIndexTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="prks-ridx-")
        self.storage = StorageConfig.for_testing(self._tmpdir)
        os.makedirs(self.storage.pdfs_dir, exist_ok=True)
        self.db = PRKSDatabase(storage=self.storage, schema_path=_SCHEMA_PATH)
        self.index = PRKSResearchIndex(storage=self.storage)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_changed_note_reindexed_unchanged_skipped(self):
        wid = self.db.add_work(title="W")
        text = "See [[concept:Culture Industry]]."
        save_work_notes(self.db, wid, text)
        self.index.sync_work(wid, text, self.db)
        cid = list_concepts(self.db)[0]["id"]
        self.assertEqual(self.index.mention_count_for_concept(cid), 1)
        with patch.object(self.index, "sync_work", wraps=self.index.sync_work) as wrapped:
            summary = self.index.reconcile_all(self.db)
        self.assertEqual(summary["unchanged"], 1)
        self.assertEqual(summary["updated"], 0)
        wrapped.assert_not_called()
        save_work_notes(self.db, wid, text + " More [[concept:Mass Culture]].")
        summary = self.index.reconcile_all(self.db)
        self.assertEqual(summary["updated"], 1)
        self.assertEqual(len(list_concepts(self.db)), 2)

    def test_deleted_work_removes_orphan(self):
        wid = self.db.add_work(title="W")
        text = "[[concept:Culture Industry]]"
        save_work_notes(self.db, wid, text)
        self.index.sync_work(wid, text, self.db)
        cid = list_concepts(self.db)[0]["id"]
        self.db.execute_query("DELETE FROM works WHERE id = ?", (wid,))
        summary = self.index.reconcile_all(self.db)
        self.assertEqual(summary["removed_orphans"], 1)
        self.assertEqual(self.index.mention_count_for_concept(cid), 0)

    def test_corrupt_derived_recreated_canonical_untouched(self):
        wid = self.db.add_work(title="W")
        text = "[[concept:Culture Industry]]"
        save_work_notes(self.db, wid, text)
        canonical_mtime = os.path.getmtime(self.storage.db_path)
        canonical_size = os.path.getsize(self.storage.db_path)
        with open(self.storage.research_index_db_path, "wb") as fh:
            fh.write(b"not a sqlite database")
        index = PRKSResearchIndex(storage=self.storage)
        index.reconcile_all(self.db)
        self.assertEqual(os.path.getsize(self.storage.db_path), canonical_size)
        self.assertGreaterEqual(os.path.getmtime(self.storage.db_path), canonical_mtime)
        cid = list_concepts(self.db)[0]["id"]
        self.assertEqual(index.mention_count_for_concept(cid), 1)
        conn = sqlite3.connect(self.storage.research_index_db_path)
        try:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
                if not str(r[0]).startswith("sqlite_")
            }
        finally:
            conn.close()
        self.assertIn("concept_mentions", tables)

    def test_index_sync_failure_does_not_roll_back_notes(self):
        wid = self.db.add_work(title="W")
        text = "[[concept:Culture Industry]]"
        save_work_notes(self.db, wid, text)
        with patch.object(self.index, "sync_work", side_effect=RuntimeError("boom")):
            try:
                self.index.sync_work(wid, text, self.db)
            except RuntimeError:
                pass
        work = self.db.get_work(wid)
        self.assertEqual(work["text_content"], text)
        self.assertEqual(list_concepts(self.db)[0]["name"], "Culture Industry")

    def test_missing_argument_omitted_from_work_research_refs(self):
        wid = self.db.add_work(title="W")
        arg = create_argument(self.db, name="Real argument", kind="argument")
        text = "See [[argument:A-MISSING]] and [[argument:%s|ok]]." % arg["id"]
        save_work_notes(self.db, wid, text)
        self.index.sync_work(wid, text, self.db)
        self.index.reconcile_all(self.db)
        refs = self.index.work_research_refs(wid, self.db)
        ids = [a["id"] for a in refs.get("arguments") or []]
        self.assertNotIn("A-MISSING", ids)
        self.assertIn(arg["id"], ids)

    def test_content_hash_stable(self):
        self.assertEqual(content_hash("abc"), content_hash("abc"))
        self.assertNotEqual(content_hash("abc"), content_hash("abd"))


if __name__ == "__main__":
    unittest.main()
