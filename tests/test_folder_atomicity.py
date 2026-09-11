"""Canonical Folder multi-write operations must commit all-or-nothing.

Offline coherence reads an HTTP failure as "nothing changed" and keeps the
cached copy eligible. A canonical operation that half-commits therefore leaves
a permanently stale cache: the client was told the write failed, so it never
invalidates, but the server data really did move. These tests force a failure
partway through each multi-write Folder path and assert the first write was
rolled back.

Every test here fails against the pre-transaction implementations, which ran
their DELETE and their tag prune as separate autocommitted statements.
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig

_SCHEMA_PATH = os.path.join(_PROJECT_DIR, "backend", "db_schema.sql")
_ABORT_TAGS = "prks_test_abort_folder_tag_prune"


def _install_tag_delete_abort(db: PRKSDatabase) -> None:
    """Make the unused-tag prune -- the LAST write of each path -- fail."""
    db.execute_query(
        f"""
        CREATE TRIGGER {_ABORT_TAGS} BEFORE DELETE ON tags
        BEGIN
            SELECT RAISE(ABORT, 'forced tag prune failure');
        END
        """
    )


class TestFolderAtomicity(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="prks-folder-atomic-")
        self.storage = StorageConfig.for_testing(self._tmpdir)
        os.makedirs(self.storage.pdfs_dir, exist_ok=True)
        os.makedirs(self.storage.processing_dir, exist_ok=True)
        self.db = PRKSDatabase(storage=self.storage, schema_path=_SCHEMA_PATH)

    def tearDown(self):
        try:
            self.db.execute_query(f"DROP TRIGGER IF EXISTS {_ABORT_TAGS}")
        except Exception:
            pass
        if getattr(self, "_tmpdir", None):
            shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _folder_exists(self, folder_id: str) -> bool:
        return bool(self.db.execute_query("SELECT 1 FROM folders WHERE id = ?", (folder_id,)))

    def test_delete_empty_folder_rolls_back_when_tag_prune_fails(self):
        folder_id = self.db.add_folder("Doomed", "", None)
        tag_id = self.db.add_tag("OnlyOnThisFolder", "#333")["id"]
        self.db.add_tag_to_folder(folder_id, tag_id)
        _install_tag_delete_abort(self.db)

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.delete_empty_folder(folder_id)

        # HTTP failure => the folder must still exist, with its tag link intact.
        self.assertTrue(self._folder_exists(folder_id))
        links = self.db.execute_query(
            "SELECT 1 FROM folder_tags WHERE folder_id = ? AND tag_id = ?",
            (folder_id, tag_id),
        )
        self.assertTrue(links, "folder_tags row must survive the rollback")
        self.assertEqual(
            len(self.db.execute_query("SELECT id FROM tags WHERE id = ?", (tag_id,))), 1
        )

    def test_delete_empty_folder_commits_folder_and_prune_together(self):
        folder_id = self.db.add_folder("Fine", "", None)
        tag_id = self.db.add_tag("Orphaned", "#333")["id"]
        self.db.add_tag_to_folder(folder_id, tag_id)

        self.db.delete_empty_folder(folder_id)

        self.assertFalse(self._folder_exists(folder_id))
        self.assertEqual(self.db.execute_query("SELECT id FROM tags WHERE id = ?", (tag_id,)), [])

    def test_delete_empty_folder_keeps_tag_still_used_elsewhere(self):
        keeper = self.db.add_folder("Keeper", "", None)
        doomed = self.db.add_folder("Doomed", "", None)
        tag_id = self.db.add_tag("Shared", "#333")["id"]
        self.db.add_tag_to_folder(keeper, tag_id)
        self.db.add_tag_to_folder(doomed, tag_id)

        self.db.delete_empty_folder(doomed)

        self.assertTrue(self._folder_exists(keeper))
        self.assertEqual(
            len(self.db.execute_query("SELECT id FROM tags WHERE id = ?", (tag_id,))), 1
        )

    def test_remove_tag_from_folder_rolls_back_when_prune_fails(self):
        folder_id = self.db.add_folder("Tagged", "", None)
        tag_id = self.db.add_tag("SoleUse", "#333")["id"]
        self.db.add_tag_to_folder(folder_id, tag_id)
        _install_tag_delete_abort(self.db)

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.remove_tag_from_folder(folder_id, tag_id)

        links = self.db.execute_query(
            "SELECT 1 FROM folder_tags WHERE folder_id = ? AND tag_id = ?",
            (folder_id, tag_id),
        )
        self.assertTrue(links, "membership must not be gone after a reported failure")

    def test_remove_tag_from_work_rolls_back_when_prune_fails(self):
        work_id = self.db.add_work(title="Tagged work")
        tag_id = self.db.add_tag("SoleUse", "#333")["id"]
        self.db.add_tag_to_work(work_id, tag_id)
        _install_tag_delete_abort(self.db)

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.remove_tag_from_work(work_id, tag_id)

        links = self.db.execute_query(
            "SELECT 1 FROM work_tags WHERE work_id = ? AND tag_id = ?",
            (work_id, tag_id),
        )
        self.assertTrue(links, "membership must not be gone after a reported failure")

    def test_remove_tag_from_folder_still_prunes_on_success(self):
        folder_id = self.db.add_folder("Tagged", "", None)
        tag_id = self.db.add_tag("SoleUse", "#333")["id"]
        self.db.add_tag_to_folder(folder_id, tag_id)

        self.db.remove_tag_from_folder(folder_id, tag_id)

        self.assertEqual(self.db.execute_query("SELECT id FROM tags WHERE id = ?", (tag_id,)), [])

    def test_folder_patch_reports_member_work_ids_for_rename(self):
        """Offline coherence evicts exactly these Work snapshots on a rename."""
        folder_id = self.db.add_folder("Before", "", None)
        w1 = self.db.add_work(title="One")
        w2 = self.db.add_work(title="Two")
        self.db.add_work_to_folder(folder_id, w1)
        self.db.add_work_to_folder(folder_id, w2)

        self.assertEqual(sorted(self.db.get_folder_work_ids(folder_id)), sorted([w1, w2]))

        other = self.db.add_folder("Other", "", None)
        self.assertEqual(self.db.get_folder_work_ids(other), [])


if __name__ == "__main__":
    unittest.main()
