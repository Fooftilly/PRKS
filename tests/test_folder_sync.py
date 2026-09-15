"""Folders: four shapes, and parity with the ordinary endpoints."""
import tempfile
import unittest
import uuid

from backend import entity_ids, folder_sync as folders, sync_protocol
from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig


class FolderSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-folder-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.device = str(uuid.uuid4())

    def send(self, operation, entity_type, entity_id, payload, base=None, op_id=None):
        return sync_protocol.process_operation(self.db, dict(
            op_id=op_id or str(uuid.uuid4()), device_id=self.device, operation=operation,
            entity_type=entity_type, entity_id=entity_id, payload=payload,
            base_revision=base, occurred_at="2026-09-15T10:00:00Z",
            created_at="2026-09-15T10:00:00Z", depends_on=[]))

    def create(self, title, parent_id="", description="", notes="", folder_id=None):
        fid = folder_id or entity_ids.generate("F")
        status, result = self.send("CREATE_FOLDER", "folder", fid, dict(
            title=title, description=description, parent_id=parent_id,
            private_notes=notes), None)
        return fid, status, result

    def field(self, folder_id, name, value, base):
        return self.send("SET_FOLDER_FIELD", "folder", folder_id,
                         dict(field=name, value=value), base)

    def stored(self, folder_id):
        rows = self.db.execute_query("SELECT * FROM folders WHERE id = ?", (folder_id,))
        return rows[0] if rows else None

    def revision(self, folder_id, field):
        with self.db.connection() as conn:
            return folders.get_revision(conn, folder_id, field)

    # ---- construction -----------------------------------------------------

    def test_a_folder_is_created_under_the_id_the_client_minted(self):
        fid, status, result = self.create("Drafts", description="Work in progress")
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["changed"])
        self.assertEqual(result["folder"]["title"], "Drafts")
        self.assertEqual(self.stored(fid)["description"], "Work in progress")
        # Construction is not mutation: no field has "changed" yet.
        for name in folders.FIELDS:
            self.assertEqual(self.revision(fid, name), 0, name)

    def test_creation_requires_a_collision_resistant_id(self):
        for bad in ("F-ABCD1234", "drafts", "T-" + "A" * 32):
            with self.subTest(bad=bad):
                _, status, result = self.create("X", folder_id=bad)
                self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    def test_an_empty_title_becomes_the_placeholder_on_both_paths(self):
        """`add_folder` has always substituted "Untitled Folder"; a second path
        that stored an empty title would make two devices disagree about a
        value neither of them typed."""
        fid, status, _ = self.create("   ")
        self.assertEqual(status, 200)
        self.assertEqual(self.stored(fid)["title"], "Untitled Folder")
        # And the ordinary path substitutes the same placeholder -- under a
        # different parent, since the two would otherwise collide by name.
        top, _, _ = self.create("Top")
        self.assertEqual(
            self.stored(self.db.add_folder("  ", parent_id=top))["title"],
            "Untitled Folder")

    def test_a_title_another_folder_has_in_the_same_place_is_terminal(self):
        self.create("Drafts")
        fid, status, result = self.create("drafts")
        self.assertEqual((status, result["code"]), (409, "TITLE_TAKEN"))
        self.assertIsNone(self.stored(fid))

    def test_the_same_title_under_a_different_parent_is_not_a_collision(self):
        top, _, _ = self.create("Top")
        self.create("Drafts")
        fid, status, _ = self.create("Drafts", parent_id=top)
        self.assertEqual(status, 200)
        self.assertEqual(self.stored(fid)["parent_id"], top)

    def test_a_parent_that_does_not_exist_is_terminal(self):
        fid, status, result = self.create("Drafts", parent_id=entity_ids.generate("F"))
        self.assertEqual((status, result["code"]), (404, "PARENT_NOT_FOUND"))
        self.assertIsNone(self.stored(fid))

    def test_recreating_the_same_id_acknowledges_without_overwriting(self):
        fid, _, _ = self.create("Drafts")
        self.db.update_folder_metadata(fid, {"title": "Renamed"})
        _, status, result = self.create("Drafts", folder_id=fid)
        self.assertEqual(status, 200)
        self.assertFalse(result["changed"])
        self.assertEqual(self.stored(fid)["title"], "Renamed")

    # ---- fields -----------------------------------------------------------

    def test_a_field_edit_advances_only_its_own_revision(self):
        fid, _, _ = self.create("Drafts")
        status, result = self.field(fid, "description", "Now described", 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertIs(result["value_omitted"], True)
        self.assertEqual(self.revision(fid, "title"), 0,
                         "a rename and a description are separate decisions")

    def test_moving_a_folder_is_a_field_edit(self):
        """The hierarchy is a parent pointer on one row, so a move changes
        exactly one value -- not a structure several rows have to agree on."""
        top, _, _ = self.create("Top")
        child, _, _ = self.create("Child")
        status, result = self.field(child, "parent_id", top, 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(self.stored(child)["parent_id"], top)
        self.assertEqual(self.revision(child, "parent_id"), 1)

    def test_clearing_a_parent_stores_null_not_an_empty_string(self):
        top, _, _ = self.create("Top")
        child, _, _ = self.create("Child", parent_id=top)
        self.field(child, "parent_id", "", 0)
        self.assertIsNone(self.stored(child)["parent_id"])

    def test_a_cycle_is_refused_by_the_server_that_sees_the_whole_tree(self):
        top, _, _ = self.create("Top")
        middle, _, _ = self.create("Middle", parent_id=top)
        status, result = self.field(top, "parent_id", middle, 0)
        self.assertEqual((status, result["code"]), (409, "PARENT_CYCLE"))
        status, result = self.field(top, "parent_id", top, 0)
        self.assertEqual((status, result["code"]), (409, "PARENT_CYCLE"))

    def test_a_move_that_would_collide_where_it_lands_is_terminal(self):
        """A title is unique WITHIN its parent, so a move can collide exactly
        as a rename can -- and both fields have to be judged together."""
        top, _, _ = self.create("Top")
        self.create("Drafts", parent_id=top)
        loose, _, _ = self.create("Drafts")
        status, result = self.field(loose, "parent_id", top, 0)
        self.assertEqual((status, result["code"]), (409, "TITLE_TAKEN"))
        self.assertIsNone(self.stored(loose)["parent_id"])

    def test_a_stale_base_with_a_different_value_is_a_conflict(self):
        fid, _, _ = self.create("Drafts")
        self.field(fid, "title", "First", 0)
        status, result = self.field(fid, "title", "Second", 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(result["current_value"], "First")
        self.assertEqual(self.stored(fid)["title"], "First")

    def test_two_devices_that_typed_the_same_title_have_not_collided(self):
        fid, _, _ = self.create("Drafts")
        self.field(fid, "title", "First", 0)
        status, result = self.field(fid, "title", "First", 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))

    def test_editing_requires_a_base_revision(self):
        fid, _, _ = self.create("Drafts")
        status, result = self.field(fid, "title", "X", None)
        self.assertEqual((status, result["code"]), (400, "INVALID_BASE_REVISION"))

    def test_only_editable_columns_are_reachable(self):
        fid, _, _ = self.create("Drafts")
        for name in ("id", "created_at", "title; DROP TABLE folders", ""):
            with self.subTest(field=name):
                status, result = self.field(fid, name, "x", 0)
                self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    # ---- which folder a Work is in ----------------------------------------

    def work(self, title="A Work"):
        return self.db.add_work(title=title)

    def work_state(self, work_id):
        return self.db.get_work_folder_state(work_id)

    def test_a_works_folder_is_a_scalar_with_its_own_revision(self):
        fid, _, _ = self.create("Drafts")
        work = self.work()
        status, result = self.send("SET_WORK_FOLDER", "work", work,
                                   {"folder_id": fid}, 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["changed"])
        self.assertEqual(result["folder_id"], fid)
        self.assertEqual(result["folder_title"], "Drafts",
                         "a cached Work detail renders the folder's title")
        self.assertEqual(self.work_state(work), {"work_id": work, "folder_id": fid,
                                                 "revision": 1})

    def test_moving_a_work_replaces_rather_than_accumulates(self):
        a, _, _ = self.create("A")
        b, _, _ = self.create("B")
        work = self.work()
        self.send("SET_WORK_FOLDER", "work", work, {"folder_id": a}, 0)
        self.send("SET_WORK_FOLDER", "work", work, {"folder_id": b}, 1)
        self.assertEqual(
            [r["folder_id"] for r in self.db.execute_query(
                "SELECT folder_id FROM folder_files WHERE work_id = ?", (work,))],
            [b], "a Work is in at most one folder")

    def test_clearing_a_works_folder_is_the_same_operation(self):
        fid, _, _ = self.create("Drafts")
        work = self.work()
        self.send("SET_WORK_FOLDER", "work", work, {"folder_id": fid}, 0)
        status, result = self.send("SET_WORK_FOLDER", "work", work, {"folder_id": ""}, 1)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(result["folder_id"], "")
        self.assertEqual(self.work_state(work)["folder_id"], "")

    def test_a_stale_base_that_disagrees_about_the_folder_is_a_conflict(self):
        a, _, _ = self.create("A")
        b, _, _ = self.create("B")
        work = self.work()
        self.send("SET_WORK_FOLDER", "work", work, {"folder_id": a}, 0)
        status, result = self.send("SET_WORK_FOLDER", "work", work, {"folder_id": b}, 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(result["current_value"], a)

    def test_filing_into_a_folder_that_is_gone_is_terminal(self):
        work = self.work()
        status, result = self.send("SET_WORK_FOLDER", "work", work,
                                   {"folder_id": entity_ids.generate("F")}, 0)
        self.assertEqual((status, result["code"]), (404, "FOLDER_NOT_FOUND"))

    # ---- deletion ---------------------------------------------------------

    def test_an_empty_folder_is_deleted(self):
        fid, _, _ = self.create("Drafts")
        status, result = self.send("DELETE_FOLDER", "folder", fid, {}, None)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["changed"])
        self.assertIsNone(self.stored(fid))

    def test_a_folder_holding_files_is_refused_rather_than_cascaded(self):
        fid, _, _ = self.create("Drafts")
        work = self.work()
        self.send("SET_WORK_FOLDER", "work", work, {"folder_id": fid}, 0)
        status, result = self.send("DELETE_FOLDER", "folder", fid, {}, None)
        self.assertEqual((status, result["code"]), (409, "FOLDER_NOT_EMPTY"))
        self.assertIsNotNone(self.stored(fid))

    def test_a_folder_with_subfolders_is_refused(self):
        top, _, _ = self.create("Top")
        self.create("Child", parent_id=top)
        status, result = self.send("DELETE_FOLDER", "folder", top, {}, None)
        self.assertEqual((status, result["code"]), (409, "FOLDER_HAS_SUBFOLDERS"))
        self.assertIsNotNone(self.stored(top))

    def test_deleting_a_folder_that_is_already_gone_is_convergence(self):
        fid, _, _ = self.create("Drafts")
        self.send("DELETE_FOLDER", "folder", fid, {}, None)
        status, result = self.send("DELETE_FOLDER", "folder", fid, {}, None)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])

    def test_destruction_addresses_an_identity_and_carries_no_base_revision(self):
        fid, _, _ = self.create("Drafts")
        status, result = self.send("DELETE_FOLDER", "folder", fid, {}, 0)
        self.assertEqual((status, result["code"]), (400, "INVALID_BASE_REVISION"))

    # ---- parity with the ordinary endpoints --------------------------------

    def test_the_ordinary_patch_advances_the_same_revisions(self):
        fid, _, _ = self.create("Drafts")
        self.db.update_folder_metadata(fid, {"title": "From the PATCH"})
        self.assertEqual(self.revision(fid, "title"), 1)
        self.assertEqual(self.revision(fid, "description"), 0,
                         "and only the field it changed")
        status, result = self.field(fid, "title", "From the queue", 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))

    def test_a_patch_that_changes_nothing_advances_nothing(self):
        fid, _, _ = self.create("Drafts")
        self.db.update_folder_metadata(fid, {"title": "Drafts"})
        self.assertEqual(self.revision(fid, "title"), 0)

    def test_the_ordinary_move_advances_the_works_own_revision(self):
        fid, _, _ = self.create("Drafts")
        work = self.work()
        self.db.move_work_to_folder(work, fid)
        self.assertEqual(self.work_state(work), {"work_id": work, "folder_id": fid,
                                                 "revision": 1})
        self.db.move_work_to_folder(work, None)
        self.assertEqual(self.work_state(work)["revision"], 2)

    def test_the_ordinary_creation_shares_the_construction_boundary(self):
        self.db.add_folder("Drafts")
        with self.assertRaises(ValueError):
            self.db.add_folder("drafts")

    def test_the_ordinary_delete_keeps_its_empty_only_rule(self):
        fid, _, _ = self.create("Drafts")
        work = self.work()
        self.db.move_work_to_folder(work, fid)
        with self.assertRaises(ValueError):
            self.db.delete_empty_folder(fid)

    # ---- projections -------------------------------------------------------

    def test_the_folder_state_projection_reports_revisions_only(self):
        fid, _, _ = self.create("Drafts")
        self.field(fid, "title", "Renamed", 0)
        state = self.db.get_folder_sync_state(fid)
        self.assertEqual(state["folder_id"], fid)
        self.assertEqual(sorted(state["fields"]), sorted(folders.FIELDS))
        self.assertEqual(state["fields"]["title"], {"revision": 1})
        for entry in state["fields"].values():
            self.assertEqual(sorted(entry), ["revision"])
        self.assertIsNone(self.db.get_folder_sync_state(entity_ids.generate("F")))

    def test_a_work_in_no_folder_reads_as_empty_at_revision_zero(self):
        work = self.work()
        self.assertEqual(self.work_state(work),
                         {"work_id": work, "folder_id": "", "revision": 0})
        self.assertIsNone(self.db.get_work_folder_state(entity_ids.generate("W")))


if __name__ == "__main__":
    unittest.main()
