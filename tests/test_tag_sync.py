"""The Tag VOCABULARY: CREATE_TAG and DELETE_TAG, and parity with the endpoints."""
import tempfile
import unittest
import uuid

from backend import entity_ids, sync_protocol, tag_sync, work_tag_sync
from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig


class TagVocabularySyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-tag-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.device = str(uuid.uuid4())

    def send(self, operation, entity_id, payload, base=None, op_id=None):
        return sync_protocol.process_operation(self.db, dict(
            op_id=op_id or str(uuid.uuid4()), device_id=self.device, operation=operation,
            entity_type="tag", entity_id=entity_id, payload=payload, base_revision=base,
            occurred_at="2026-09-15T10:00:00Z", created_at="2026-09-15T10:00:00Z",
            depends_on=[]))

    def create(self, name, tag_id=None, color="#6d6cf7"):
        tid = tag_id or entity_ids.generate("T")
        status, result = self.send("CREATE_TAG", tid, {"name": name, "color": color})
        return tid, status, result

    def stored(self, tag_id):
        rows = self.db.execute_query("SELECT * FROM tags WHERE id = ?", (tag_id,))
        return rows[0] if rows else None

    def lifecycle(self, tag_id):
        with self.db.connection() as conn:
            return work_tag_sync.resolve_lifecycle(conn, tag_id)

    # ---- construction -----------------------------------------------------

    def test_a_tag_is_created_under_the_id_the_client_minted(self):
        tid, status, result = self.create("Epistemology")
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["changed"])
        self.assertEqual(result["tag"]["name"], "Epistemology")
        self.assertEqual(self.stored(tid)["name"], "Epistemology")

    def test_a_created_tag_is_immediately_usable_by_the_relationship_family(self):
        """The lifecycle row is what every later answer about a Tag is read
        from. A Tag inserted without one comes back UNKNOWN to the very family
        that depends on it."""
        tid, _, _ = self.create("Epistemology")
        self.assertEqual(self.lifecycle(tid), {"state": "ACTIVE"})

    def test_creation_requires_a_collision_resistant_id(self):
        for bad in ("T-ABCD1234", "epistemology", "P-" + "A" * 32):
            with self.subTest(bad=bad):
                status, result = self.send(
                    "CREATE_TAG", bad, {"name": "X", "color": "#6d6cf7"})
                self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    def test_an_empty_name_never_reaches_the_catalogue(self):
        for bad in ("", "   "):
            with self.subTest(bad=bad):
                _, status, result = self.create(bad)
                self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    def test_recreating_the_same_id_acknowledges_without_overwriting(self):
        tid, _, _ = self.create("Epistemology")
        _, status, result = self.create("Something else", tag_id=tid)
        self.assertEqual(status, 200)
        self.assertFalse(result["changed"])
        self.assertEqual(self.stored(tid)["name"], "Epistemology",
                         "creation is not an update")

    def test_a_name_another_tag_already_has_is_terminal(self):
        first, _, _ = self.create("Epistemology")
        second, status, result = self.create("epistemology")
        self.assertEqual((status, result["code"]), (409, "NAME_TAKEN"))
        self.assertEqual(result["target_tag_id"], first,
                         "the client is told which Tag it collided with")
        self.assertIsNone(self.stored(second))

    def test_a_name_that_is_another_tags_alias_is_taken_too(self):
        """A name is unique across canonical names AND aliases: that is the
        rule `resolve_tag_id_by_label` has always applied, and a second path
        that ignored it would create a Tag the picker could never tell apart."""
        first, _, _ = self.create("Epistemology")
        self.db.add_tag_alias(first, "Theory of knowledge")
        _, status, result = self.create("theory of knowledge")
        self.assertEqual((status, result["code"]), (409, "NAME_TAKEN"))
        self.assertEqual(result["target_tag_id"], first)

    def test_a_replay_of_the_same_op_id_is_exact(self):
        op_id = str(uuid.uuid4())
        tid = entity_ids.generate("T")
        first = self.send("CREATE_TAG", tid, {"name": "Once", "color": "#6d6cf7"},
                          op_id=op_id)
        second = self.send("CREATE_TAG", tid, {"name": "Once", "color": "#6d6cf7"},
                           op_id=op_id)
        self.assertEqual(first, second)
        self.assertEqual(len(self.db.execute_query(
            "SELECT 1 FROM tags WHERE name = 'Once'")), 1)

    # ---- destruction ------------------------------------------------------

    def test_deleting_a_tag_removes_it_and_advances_its_relationships(self):
        tid, _, _ = self.create("Epistemology")
        work = self.db.add_work(title="A Work")
        self.db.add_tag_to_work(work, tid)
        with self.db.connection() as conn:
            before = work_tag_sync.get_revision(conn, work, tid)
        status, result = self.send("DELETE_TAG", tid, {})
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["changed"])
        self.assertEqual(result["affected_work_ids"], [work])
        self.assertIsNone(self.stored(tid))
        with self.db.connection() as conn:
            self.assertEqual(work_tag_sync.get_revision(conn, work, tid), before + 1)

    def test_a_deleted_tag_stays_deleted_for_the_relationship_family(self):
        """The lifecycle survives the row: an offline device replaying an
        attach has to be told the Tag was deleted, not that it never existed."""
        tid, _, _ = self.create("Epistemology")
        self.send("DELETE_TAG", tid, {})
        self.assertEqual(self.lifecycle(tid), {"state": "DELETED"})

    def test_deleting_a_tag_that_is_already_gone_is_convergence(self):
        tid, _, _ = self.create("Epistemology")
        self.send("DELETE_TAG", tid, {})
        status, result = self.send("DELETE_TAG", tid, {})
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])

    def test_deleting_a_merged_tag_is_refused_rather_than_redirected(self):
        """It is not there, and it is not gone either -- it became another Tag.
        Deleting the target would destroy one the user never named."""
        source, _, _ = self.create("Epistemology")
        target, _, _ = self.create("Knowledge")
        self.db.merge_tags_into(source, target)
        status, result = self.send("DELETE_TAG", source, {})
        self.assertEqual((status, result["code"]), (409, "TAG_MERGED"))
        self.assertEqual(result["target_tag_id"], target)
        self.assertIsNotNone(self.stored(target))

    def test_destruction_addresses_an_identity_and_carries_no_base_revision(self):
        tid, _, _ = self.create("Epistemology")
        status, result = self.send("DELETE_TAG", tid, {}, base=0)
        self.assertEqual((status, result["code"]), (400, "INVALID_BASE_REVISION"))
        self.assertIsNotNone(self.stored(tid))

    # ---- parity with the ordinary endpoint ---------------------------------

    def test_the_ordinary_creation_shares_the_construction_boundary(self):
        out = self.db.add_tag("From the endpoint")
        self.assertFalse(out["existed"])
        self.assertEqual(self.lifecycle(out["id"]), {"state": "ACTIVE"})

    def test_the_ordinary_creation_still_converges_on_an_existing_name(self):
        """Unchanged behaviour, deliberately: `POST /api/tags` has always
        handed back the existing Tag. The DURABLE path cannot, because
        operations already queued behind the creation name the id this device
        minted -- so it refuses instead of silently redirecting them."""
        first = self.db.add_tag("Epistemology")
        again = self.db.add_tag("epistemology")
        self.assertTrue(again["existed"])
        self.assertEqual(again["id"], first["id"])


if __name__ == "__main__":
    unittest.main()
