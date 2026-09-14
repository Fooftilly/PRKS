"""SET_PERSON_METADATA_FIELD: per-field revisions, and parity with the PATCH."""
import tempfile
import unittest
import uuid

from backend import entity_ids, person_metadata_sync as meta, person_sync, sync_protocol
from backend import work_metadata_sync as results
from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig


class PersonMetadataSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-person-meta-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.device = str(uuid.uuid4())
        self.person = entity_ids.generate("P")
        body = {name: "" for name in person_sync.FIELDS}
        body["first_name"] = "Ada"
        body["last_name"] = "Lovelace"
        self.assertEqual(sync_protocol.process_operation(self.db, dict(
            op_id=str(uuid.uuid4()), device_id=self.device, operation="CREATE_PERSON",
            entity_type="person", entity_id=self.person, payload=body,
            base_revision=None, occurred_at="2026-09-14T10:00:00Z",
            created_at="2026-09-14T10:00:00Z", depends_on=[]))[0], 200)

    def envelope(self, field, value, base, op_id=None, person_id=None):
        return dict(
            op_id=op_id or str(uuid.uuid4()), device_id=self.device,
            operation="SET_PERSON_METADATA_FIELD", entity_type="person",
            entity_id=person_id or self.person,
            payload={"field": field, "value": value}, base_revision=base,
            occurred_at="2026-09-14T10:00:00Z", created_at="2026-09-14T10:00:00Z",
            depends_on=[])

    def send(self, *args, **kwargs):
        return sync_protocol.process_operation(self.db, self.envelope(*args, **kwargs))

    def stored(self, field):
        return self.db.execute_query(
            "SELECT %s FROM persons WHERE id = ?" % field, (self.person,))[0][field]

    def revision(self, field):
        with self.db.connection() as conn:
            return meta.get_revision(conn, self.person, field)

    # --- the family itself -------------------------------------------------

    def test_a_field_edit_applies_and_advances_only_its_own_revision(self):
        status, result = self.send("about", "Analyst of the Engine", 0)
        self.assertEqual(status, 200)
        self.assertEqual(result["code"], "ACKNOWLEDGED")
        self.assertEqual(result["server_revision"], 1)
        self.assertTrue(result["changed"])
        self.assertEqual(self.stored("about"), "Analyst of the Engine")
        # The conflict unit is one field. A biography and a birth date are
        # separate decisions, so editing one must not make the other look
        # stale to a device that already holds it.
        self.assertEqual(self.revision("birth_date"), 0)

    def test_the_acknowledgement_never_echoes_the_value_back(self):
        """No profile field has a length bound.

        The ledger has no retention policy, so echoing a biography would make
        every edit a permanent second copy of it -- and a result the client
        cannot durably store is read as a failed sync and retried forever.
        """
        long_text = "x" * 20000
        status, result = self.send("about", long_text, 0)
        self.assertEqual(status, 200)
        self.assertNotIn("value", result)
        self.assertIs(result["value_omitted"], True)
        self.assertLessEqual(
            results.serialized_result_bytes(result), results.MAX_DURABLE_RESULT_BYTES)
        self.assertEqual(self.stored("about"), long_text)

    def test_a_no_op_write_advances_nothing(self):
        self.send("about", "Same", 0)
        before = self.revision("about")
        status, result = self.send("about", "Same", before)
        self.assertEqual(status, 200)
        self.assertFalse(result["changed"])
        self.assertEqual(self.revision("about"), before,
                         "a revision records the value changing, not a request arriving")

    def test_two_devices_that_typed_the_same_value_have_not_collided(self):
        self.send("last_name", "Byron", 0)
        status, result = self.send("last_name", "Byron", 0)
        self.assertEqual(status, 200, result)
        self.assertEqual(result["code"], "ACKNOWLEDGED")

    def test_a_stale_base_with_a_different_value_is_a_conflict(self):
        self.send("last_name", "Byron", 0)
        status, result = self.send("last_name", "King", 0)
        self.assertEqual(status, 409)
        self.assertEqual(result["code"], "REVISION_CONFLICT")
        self.assertEqual(result["current_revision"], 1)
        self.assertEqual(result["current_value"], "Byron")
        self.assertEqual(result["requested_value"], "King")
        self.assertEqual(self.stored("last_name"), "Byron", "and nothing was overwritten")

    def test_a_long_conflict_still_fits_what_the_client_can_store(self):
        """The only thing standing between a long biography and an
        unresolvable conflict: no Person field has a length limit of its own.
        """
        self.send("about", "A" * 40000, 0)
        status, result = self.send("about", "B" * 40000, 0)
        self.assertEqual(status, 409)
        self.assertLessEqual(
            results.serialized_result_bytes(result), results.MAX_DURABLE_RESULT_BYTES)
        self.assertIn("current_preview", result)
        self.assertNotIn("current_value", result)

    def test_a_base_ahead_of_the_server_is_refused(self):
        status, result = self.send("about", "Ahead", 3)
        self.assertEqual(status, 400)
        self.assertEqual(result["code"], "FUTURE_REVISION")

    def test_a_missing_person_is_terminal(self):
        status, result = self.send("about", "Nobody", 0, person_id=entity_ids.generate("P"))
        self.assertEqual(status, 404)
        self.assertEqual(result["code"], "ENTITY_NOT_FOUND")

    # --- envelope ----------------------------------------------------------

    def test_editing_requires_a_base_revision(self):
        """Construction is the family that carries none; editing is mutation.

        A null base is a client that cannot detect a conflict, and would
        silently overwrite whatever another device wrote.
        """
        status, result = self.send("about", "No base", None)
        self.assertEqual(status, 400)
        self.assertEqual(result["code"], "INVALID_BASE_REVISION")

    def test_only_editable_profile_columns_are_reachable(self):
        for field in ("id", "created_at", "updated_at", "aliases; DROP TABLE persons", ""):
            with self.subTest(field=field):
                status, result = self.send(field, "x", 0)
                self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    def test_the_field_vocabulary_is_the_creation_familys(self):
        """A field that can be constructed and not edited -- or the reverse --
        is a split contract by another name."""
        self.assertEqual(meta.SYNCED_FIELDS, frozenset(person_sync.FIELDS))

    def test_a_refused_portrait_url_is_refused_on_every_path(self):
        status, result = self.send("image_url", "javascript:alert(1)", 0)
        self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))
        with self.assertRaises(ValueError):
            self.db.update_person_metadata(self.person, {"image_url": "javascript:alert(1)"})

    def test_a_replay_of_the_same_op_id_is_exact(self):
        op_id = str(uuid.uuid4())
        first = self.send("about", "Once", 0, op_id=op_id)
        # A retry after the server succeeded but before the client heard must
        # not apply the edit twice nor change the answer.
        second = self.send("about", "Once", 0, op_id=op_id)
        self.assertEqual(first, second)
        self.assertEqual(self.revision("about"), 1)

    def test_a_changed_envelope_under_a_used_op_id_never_executes(self):
        op_id = str(uuid.uuid4())
        self.send("about", "Once", 0, op_id=op_id)
        status, result = self.send("about", "Different", 0, op_id=op_id)
        self.assertEqual((status, result["code"]), (409, "OP_ID_REUSE"))
        self.assertEqual(self.stored("about"), "Once")

    # --- parity with the ordinary online path -------------------------------

    def test_the_online_patch_advances_the_same_revisions(self):
        """Revisions record CANONICAL history, not sync-endpoint history.

        An online PATCH that bypassed them would leave an offline device
        holding the old value with no way to discover it was overtaken -- it
        would overwrite the newer value believing itself current.
        """
        self.db.update_person_metadata(self.person, {"about": "From the PATCH"})
        self.assertEqual(self.revision("about"), 1)
        self.assertEqual(self.revision("last_name"), 0, "and only the field it changed")
        status, result = self.send("about", "From the queue", 0)
        self.assertEqual(status, 409, result)
        self.assertEqual(result["code"], "REVISION_CONFLICT")

    def test_the_profile_patch_advances_revisions_alongside_memberships(self):
        group_id = self.db.add_person_group("Analysts")
        self.db.update_person_profile(
            self.person, {"about": "With groups"}, [group_id])
        self.assertEqual(self.revision("about"), 1)
        self.assertEqual(self.stored("about"), "With groups")

    def test_a_patch_that_changes_nothing_advances_nothing(self):
        self.db.update_person_metadata(self.person, {"first_name": "Ada"})
        self.assertEqual(self.revision("first_name"), 0)

    def test_the_metadata_state_projection_reports_revisions_only(self):
        """The Person detail already carries all eleven values, none of them
        bounded; echoing them here would make a second copy of the profile."""
        self.send("about", "Stated", 0)
        state = self.db.get_person_metadata_state(self.person)
        self.assertEqual(state["person_id"], self.person)
        self.assertEqual(sorted(state["fields"]), sorted(person_sync.FIELDS))
        self.assertEqual(state["fields"]["about"], {"revision": 1})
        for entry in state["fields"].values():
            self.assertEqual(sorted(entry), ["revision"])
        self.assertIsNone(self.db.get_person_metadata_state(entity_ids.generate("P")))


if __name__ == "__main__":
    unittest.main()
