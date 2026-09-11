"""Canonical Folder/Tag lifecycle invariants.

Two properties, both learned the hard way:

1. **Tag identity is persistent.** Only an explicit Delete Tag or Merge Tag may
   destroy a Tag. Ordinary relationship edits -- removing a tag from a Work or
   Folder, deleting a Work or Folder, a bulk tag removal -- touch relationships
   only. PRKS used to garbage-collect "unused" Tags during those operations,
   which made Tags temporary values rather than a reusable vocabulary, silently
   destroyed `processing_file_tags` rows the "unused" test never consulted, and
   would have turned ordinary edits into ENTITY_NOT_FOUND sync conflicts.

2. **Multi-write canonical operations commit all-or-nothing.** Offline
   coherence reads an HTTP failure as "nothing changed" and keeps the cached
   copy eligible, which is only sound if a failure really means nothing moved.
"""

import os
import shutil
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
_FORBID_TAG_DELETE = "prks_test_forbid_tag_delete"


def _forbid_tag_deletion(db: PRKSDatabase) -> None:
    """Make ANY delete from `tags` fail.

    Stronger than asserting the Tag still exists afterwards: with this trigger
    installed, a path that still tried to garbage-collect a Tag would raise
    rather than quietly succeed.
    """
    db.execute_query(
        f"""
        CREATE TRIGGER {_FORBID_TAG_DELETE} BEFORE DELETE ON tags
        BEGIN
            SELECT RAISE(ABORT, 'this path must not delete a tag');
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
            self.db.execute_query(f"DROP TRIGGER IF EXISTS {_FORBID_TAG_DELETE}")
        except Exception:
            pass
        if getattr(self, "_tmpdir", None):
            shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _folder_exists(self, folder_id: str) -> bool:
        return bool(self.db.execute_query("SELECT 1 FROM folders WHERE id = ?", (folder_id,)))

    # -- Tag identity is persistent -------------------------------------
    #
    # These replace the prune-atomicity tests this module used to carry.
    # Those existed because relationship removal and an unused-tag prune were
    # two writes that had to commit together. PRKS no longer garbage-collects
    # Tags during ordinary edits, so there is no second write to roll back --
    # keeping the old tests would have asserted behavior that was removed on
    # purpose. What matters now is the stronger property: these operations
    # cannot destroy Tag identity at all.

    def test_deleting_a_folder_keeps_its_tags_in_the_catalog(self):
        folder_id = self.db.add_folder("Doomed", "", None)
        tag_id = self.db.add_tag("OnlyOnThisFolder", "#333")["id"]
        self.db.add_tag_to_folder(folder_id, tag_id)

        _forbid_tag_deletion(self.db)
        self.db.delete_empty_folder(folder_id)

        self.assertFalse(self._folder_exists(folder_id))
        self.assertEqual(
            len(self.db.execute_query("SELECT id FROM tags WHERE id = ?", (tag_id,))), 1
        )
        # The relationship cascaded away with the folder, as intended.
        self.assertEqual(
            self.db.execute_query("SELECT 1 FROM folder_tags WHERE tag_id = ?", (tag_id,)), []
        )

    def test_removing_a_tag_from_a_folder_keeps_the_tag(self):
        folder_id = self.db.add_folder("Tagged", "", None)
        tag_id = self.db.add_tag("SoleUse", "#333")["id"]
        self.db.add_tag_to_folder(folder_id, tag_id)

        _forbid_tag_deletion(self.db)
        self.db.remove_tag_from_folder(folder_id, tag_id)

        self.assertEqual(self.db.get_folder_tags(folder_id), [])
        self.assertEqual(
            len(self.db.execute_query("SELECT id FROM tags WHERE id = ?", (tag_id,))), 1
        )

    def test_removing_a_tag_from_a_work_keeps_the_tag(self):
        work_id = self.db.add_work(title="Tagged work")
        tag_id = self.db.add_tag("SoleUse", "#333")["id"]
        self.db.add_tag_to_work(work_id, tag_id)

        _forbid_tag_deletion(self.db)
        self.db.remove_tag_from_work(work_id, tag_id)

        self.assertEqual(self.db.get_work_tags(work_id), [])
        self.assertEqual(
            len(self.db.execute_query("SELECT id FROM tags WHERE id = ?", (tag_id,))), 1
        )

    def test_bulk_tag_removal_keeps_the_tags(self):
        tag_id = self.db.add_tag("BulkTag", "#333")["id"]
        work_ids = [self.db.add_work(title="Bulk %d" % i) for i in range(3)]
        for wid in work_ids:
            self.db.add_tag_to_work(wid, tag_id)

        _forbid_tag_deletion(self.db)
        self.db.bulk_update_works(
            {"action": "remove_tags", "work_ids": work_ids, "tag_ids": [tag_id]}
        )

        for wid in work_ids:
            self.assertEqual(self.db.get_work_tags(wid), [])
        self.assertEqual(
            len(self.db.execute_query("SELECT id FROM tags WHERE id = ?", (tag_id,))), 1
        )

    def test_a_tag_on_a_processing_file_is_never_collected_by_a_work_edit(self):
        """The prune's `unused` test consulted work_tags and folder_tags but
        never processing_file_tags, so removing an unrelated Work relationship
        could delete a Tag a staged file still referenced -- destroying that
        relationship through the FK cascade."""
        pdf_path = os.path.join(self.storage.processing_dir, "tag-identity.pdf")
        with open(pdf_path, "wb") as fh:
            fh.write(b"%PDF-1.4\n%TAGID\n%%EOF\n")
        staged = self.db.scan_processing_files()
        self.assertTrue(staged)
        processing_id = staged[0]["id"]

        tag_id = self.db.add_tag("StagedOnly", "#333")["id"]
        work_id = self.db.add_work(title="Unrelated")
        self.db.add_tag_to_work(work_id, tag_id)
        self.db._set_processing_tags(processing_id, [{"id": tag_id}])

        self.db.remove_tag_from_work(work_id, tag_id)

        self.assertEqual(
            len(self.db.execute_query("SELECT id FROM tags WHERE id = ?", (tag_id,))), 1
        )
        self.assertEqual(
            len(self.db.execute_query(
                "SELECT 1 FROM processing_file_tags WHERE tag_id = ?", (tag_id,)
            )),
            1,
        )

    def test_explicit_delete_tag_still_destroys_it_and_cascades(self):
        """The one remaining destructive path, alongside merge."""
        work_id = self.db.add_work(title="Explicit")
        folder_id = self.db.add_folder("Explicit", "", None)
        tag_id = self.db.add_tag("Doomed", "#333")["id"]
        self.db.add_tag_to_work(work_id, tag_id)
        self.db.add_tag_to_folder(folder_id, tag_id)

        self.db.delete_tag(tag_id)

        self.assertEqual(self.db.execute_query("SELECT id FROM tags WHERE id = ?", (tag_id,)), [])
        self.assertEqual(self.db.get_work_tags(work_id), [])
        self.assertEqual(self.db.get_folder_tags(folder_id), [])

    def test_merge_destroys_only_the_source_tag(self):
        work_id = self.db.add_work(title="Merged")
        source = self.db.add_tag("Source", "#333")["id"]
        target = self.db.add_tag("Target", "#444")["id"]
        self.db.add_tag_to_work(work_id, source)

        self.db.merge_tags_into(source, target)

        self.assertEqual(self.db.execute_query("SELECT id FROM tags WHERE id = ?", (source,)), [])
        self.assertEqual(
            len(self.db.execute_query("SELECT id FROM tags WHERE id = ?", (target,))), 1
        )
        self.assertEqual([t["id"] for t in self.db.get_work_tags(work_id)], [target])

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
