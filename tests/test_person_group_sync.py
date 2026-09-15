"""Person Groups: four shapes over one entity, and parity with the endpoints."""
import tempfile
import unittest
import uuid

from backend import entity_ids, person_group_sync as groups, person_sync, sync_protocol
from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig


class PersonGroupSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-person-group-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.device = str(uuid.uuid4())

    # ---- helpers ----------------------------------------------------------

    def envelope(self, operation, entity_id, payload, base, op_id=None):
        return dict(
            op_id=op_id or str(uuid.uuid4()), device_id=self.device,
            operation=operation, entity_type="person-group", entity_id=entity_id,
            payload=payload, base_revision=base,
            occurred_at="2026-09-14T10:00:00Z", created_at="2026-09-14T10:00:00Z",
            depends_on=[])

    def send(self, *args, **kwargs):
        return sync_protocol.process_operation(self.db, self.envelope(*args, **kwargs))

    def create(self, name, parent_id="", description="", group_id=None):
        gid = group_id or entity_ids.generate("PG")
        status, result = self.send("CREATE_PERSON_GROUP", gid, dict(
            name=name, parent_id=parent_id, description=description), None)
        return gid, status, result

    def make_person(self, last_name="Lovelace"):
        person = entity_ids.generate("P")
        body = {name: "" for name in person_sync.FIELDS}
        body["first_name"], body["last_name"] = "Ada", last_name
        self.assertEqual(sync_protocol.process_operation(self.db, dict(
            op_id=str(uuid.uuid4()), device_id=self.device, operation="CREATE_PERSON",
            entity_type="person", entity_id=person, payload=body, base_revision=None,
            occurred_at="2026-09-14T10:00:00Z", created_at="2026-09-14T10:00:00Z",
            depends_on=[]))[0], 200)
        return person

    def revision(self, group_id, field):
        with self.db.connection() as conn:
            return groups.get_revision(conn, group_id, field)

    def member_revision(self, group_id, person_id):
        with self.db.connection() as conn:
            return groups.get_member_revision(conn, group_id, person_id)

    def stored(self, group_id):
        rows = self.db.execute_query(
            "SELECT * FROM person_groups WHERE id = ?", (group_id,))
        return rows[0] if rows else None

    # ---- construction -----------------------------------------------------

    def test_a_group_is_created_under_the_id_the_client_minted(self):
        gid, status, result = self.create("Analysts", description="Engine people")
        self.assertEqual(status, 200, result)
        self.assertEqual(result["code"], "ACKNOWLEDGED")
        self.assertTrue(result["changed"])
        self.assertEqual(result["group"]["id"], gid)
        self.assertEqual(self.stored(gid)["name"], "Analysts")
        # Construction is not mutation: no field has "changed" yet.
        for field in groups.FIELDS:
            self.assertEqual(self.revision(gid, field), 0, field)

    def test_creation_requires_a_collision_resistant_id(self):
        for bad in ("PG-ABCD1234", "analysts", "", "P-" + "A" * 32):
            with self.subTest(bad=bad):
                status, result = self.send("CREATE_PERSON_GROUP", bad or "x",
                                           dict(name="X", parent_id="", description=""), None)
                self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    def test_recreating_the_same_id_acknowledges_without_overwriting(self):
        """A retry after a lost response must not rewrite a group that has
        since been renamed. Creation is not an update."""
        gid, _, _ = self.create("Analysts")
        self.db.update_person_group(gid, {"name": "Renamed"})
        _, status, result = self.create("Analysts", group_id=gid)
        self.assertEqual(status, 200)
        self.assertFalse(result["changed"])
        self.assertEqual(self.stored(gid)["name"], "Renamed")

    def test_a_name_another_group_already_has_is_terminal(self):
        """Uniqueness is CANONICAL: only the server sees every group, so two
        devices that both created "Analysts" offline cannot have resolved it
        between themselves."""
        self.create("Analysts")
        gid, status, result = self.create("analysts")
        self.assertEqual(status, 409)
        self.assertEqual(result["code"], "NAME_TAKEN")
        self.assertIsNone(self.stored(gid))

    def test_a_parent_that_does_not_exist_is_terminal(self):
        gid, status, result = self.create("Analysts", parent_id=entity_ids.generate("PG"))
        self.assertEqual(status, 404)
        self.assertEqual(result["code"], "PARENT_NOT_FOUND")
        self.assertIsNone(self.stored(gid))

    # ---- fields -----------------------------------------------------------

    def test_a_field_edit_advances_only_its_own_revision(self):
        gid, _, _ = self.create("Analysts")
        status, result = self.send("SET_PERSON_GROUP_FIELD", gid,
                                   dict(field="description", value="Engine people"), 0)
        self.assertEqual(status, 200, result)
        self.assertEqual(result["server_revision"], 1)
        self.assertIs(result["value_omitted"], True)
        self.assertNotIn("value", result)
        self.assertEqual(self.revision(gid, "name"), 0,
                         "a rename and a description are separate decisions")
        self.assertEqual(self.stored(gid)["description"], "Engine people")

    def test_a_stale_base_with_a_different_value_is_a_conflict(self):
        gid, _, _ = self.create("Analysts")
        self.send("SET_PERSON_GROUP_FIELD", gid, dict(field="name", value="Engineers"), 0)
        status, result = self.send("SET_PERSON_GROUP_FIELD", gid,
                                   dict(field="name", value="Mathematicians"), 0)
        self.assertEqual(status, 409)
        self.assertEqual(result["code"], "REVISION_CONFLICT")
        self.assertEqual(result["current_value"], "Engineers")
        self.assertEqual(self.stored(gid)["name"], "Engineers")

    def test_two_devices_that_typed_the_same_name_have_not_collided(self):
        gid, _, _ = self.create("Analysts")
        self.send("SET_PERSON_GROUP_FIELD", gid, dict(field="name", value="Engineers"), 0)
        status, result = self.send("SET_PERSON_GROUP_FIELD", gid,
                                   dict(field="name", value="Engineers"), 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))

    def test_a_rename_onto_another_groups_name_is_terminal(self):
        self.create("Analysts")
        gid, _, _ = self.create("Engineers")
        status, result = self.send("SET_PERSON_GROUP_FIELD", gid,
                                   dict(field="name", value="Analysts"), 0)
        self.assertEqual((status, result["code"]), (409, "NAME_TAKEN"))
        self.assertEqual(self.stored(gid)["name"], "Engineers")

    def test_a_cycle_is_refused_by_the_server_that_can_see_the_whole_tree(self):
        top, _, _ = self.create("Top")
        middle, _, _ = self.create("Middle", parent_id=top)
        status, result = self.send("SET_PERSON_GROUP_FIELD", top,
                                   dict(field="parent_id", value=middle), 0)
        self.assertEqual((status, result["code"]), (409, "PARENT_CYCLE"))
        status, result = self.send("SET_PERSON_GROUP_FIELD", top,
                                   dict(field="parent_id", value=top), 0)
        self.assertEqual((status, result["code"]), (409, "PARENT_CYCLE"))

    def test_clearing_a_parent_stores_null_not_an_empty_string(self):
        top, _, _ = self.create("Top")
        child, _, _ = self.create("Child", parent_id=top)
        self.send("SET_PERSON_GROUP_FIELD", child, dict(field="parent_id", value=""), 0)
        self.assertIsNone(self.stored(child)["parent_id"])

    def test_editing_requires_a_base_revision(self):
        gid, _, _ = self.create("Analysts")
        status, result = self.send("SET_PERSON_GROUP_FIELD", gid,
                                   dict(field="name", value="X"), None)
        self.assertEqual((status, result["code"]), (400, "INVALID_BASE_REVISION"))

    def test_only_editable_columns_are_reachable(self):
        gid, _, _ = self.create("Analysts")
        for field in ("id", "created_at", "updated_at", "name; DROP TABLE person_groups", ""):
            with self.subTest(field=field):
                status, result = self.send("SET_PERSON_GROUP_FIELD", gid,
                                           dict(field=field, value="x"), 0)
                self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    def test_an_empty_name_is_refused_on_every_path(self):
        gid, _, _ = self.create("Analysts")
        status, result = self.send("SET_PERSON_GROUP_FIELD", gid,
                                   dict(field="name", value="   "), 0)
        self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))
        with self.assertRaises(ValueError):
            self.db.update_person_group(gid, {"name": "  "})

    def test_a_long_conflict_still_fits_what_the_client_can_store(self):
        from backend import work_metadata_sync as results
        gid, _, _ = self.create("Analysts")
        self.send("SET_PERSON_GROUP_FIELD", gid,
                  dict(field="description", value="A" * 2000), 0)
        status, result = self.send("SET_PERSON_GROUP_FIELD", gid,
                                   dict(field="description", value="B" * 2000), 0)
        self.assertEqual(status, 409)
        self.assertLessEqual(results.serialized_result_bytes(result),
                             results.MAX_DURABLE_RESULT_BYTES)

    # ---- membership -------------------------------------------------------

    def test_membership_is_an_element_with_its_own_revision(self):
        gid, _, _ = self.create("Analysts")
        person = self.make_person()
        status, result = self.send("ADD_PERSON_GROUP_MEMBER", gid,
                                   dict(person_id=person), 0)
        self.assertEqual(status, 200, result)
        self.assertTrue(result["changed"])
        self.assertIs(result["present"], True)
        self.assertEqual(result["server_revision"], 1)
        self.assertEqual(self.revision(gid, "name"), 0,
                         "a membership is not a change to the group's own fields")

    def test_adding_someone_already_in_the_group_is_convergence(self):
        gid, _, _ = self.create("Analysts")
        person = self.make_person()
        self.send("ADD_PERSON_GROUP_MEMBER", gid, dict(person_id=person), 0)
        status, result = self.send("ADD_PERSON_GROUP_MEMBER", gid, dict(person_id=person), 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])

    def test_a_removal_that_lost_a_race_with_an_add_is_a_conflict(self):
        gid, _, _ = self.create("Analysts")
        person = self.make_person()
        self.send("ADD_PERSON_GROUP_MEMBER", gid, dict(person_id=person), 0)
        status, result = self.send("REMOVE_PERSON_GROUP_MEMBER", gid,
                                   dict(person_id=person), 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertIs(result["current_present"], True)
        self.assertIs(result["requested_present"], False)

    def test_the_revision_survives_the_relationship(self):
        """A removal has to leave a record, or an offline 'add' replayed after
        a remote 'remove' would look like it was based on current state."""
        gid, _, _ = self.create("Analysts")
        person = self.make_person()
        self.send("ADD_PERSON_GROUP_MEMBER", gid, dict(person_id=person), 0)
        self.send("REMOVE_PERSON_GROUP_MEMBER", gid, dict(person_id=person), 1)
        self.assertEqual(self.member_revision(gid, person), 2)

    def test_a_membership_for_someone_who_does_not_exist_is_terminal(self):
        gid, _, _ = self.create("Analysts")
        status, result = self.send("ADD_PERSON_GROUP_MEMBER", gid,
                                   dict(person_id=entity_ids.generate("P")), 0)
        self.assertEqual((status, result["code"]), (404, "PERSON_NOT_FOUND"))

    def test_a_membership_in_a_group_that_is_gone_is_terminal(self):
        person = self.make_person()
        status, result = self.send("ADD_PERSON_GROUP_MEMBER", entity_ids.generate("PG"),
                                   dict(person_id=person), 0)
        self.assertEqual((status, result["code"]), (404, "ENTITY_NOT_FOUND"))

    # ---- deletion ---------------------------------------------------------

    def test_deleting_a_group_reparents_its_children_and_says_so(self):
        top, _, _ = self.create("Top")
        middle, _, _ = self.create("Middle", parent_id=top)
        leaf, _, _ = self.create("Leaf", parent_id=middle)
        status, result = self.send("DELETE_PERSON_GROUP", middle, {}, None)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertIsNone(self.stored(middle))
        self.assertEqual(self.stored(leaf)["parent_id"], top)
        # The reparenting is a canonical change to ANOTHER group, so that
        # group's own parent revision advances with it.
        self.assertEqual(self.revision(leaf, "parent_id"), 1)

    def test_deleting_a_group_that_is_already_gone_is_convergence(self):
        gid, _, _ = self.create("Analysts")
        self.send("DELETE_PERSON_GROUP", gid, {}, None)
        status, result = self.send("DELETE_PERSON_GROUP", gid, {}, None)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])

    def test_deletion_addresses_an_identity_and_carries_no_base_revision(self):
        gid, _, _ = self.create("Analysts")
        status, result = self.send("DELETE_PERSON_GROUP", gid, {}, 0)
        self.assertEqual((status, result["code"]), (400, "INVALID_BASE_REVISION"))

    def test_an_edit_that_arrives_after_the_deletion_is_told_so(self):
        gid, _, _ = self.create("Analysts")
        self.send("DELETE_PERSON_GROUP", gid, {}, None)
        status, result = self.send("SET_PERSON_GROUP_FIELD", gid,
                                   dict(field="name", value="Too late"), 0)
        self.assertEqual((status, result["code"]), (404, "ENTITY_NOT_FOUND"))

    # ---- idempotency ------------------------------------------------------

    def test_a_replay_of_the_same_op_id_is_exact(self):
        gid, _, _ = self.create("Analysts")
        op_id = str(uuid.uuid4())
        first = self.send("SET_PERSON_GROUP_FIELD", gid,
                          dict(field="name", value="Once"), 0, op_id=op_id)
        second = self.send("SET_PERSON_GROUP_FIELD", gid,
                           dict(field="name", value="Once"), 0, op_id=op_id)
        self.assertEqual(first, second)
        self.assertEqual(self.revision(gid, "name"), 1)

    # ---- parity with the ordinary endpoints --------------------------------

    def test_the_ordinary_patch_advances_the_same_revisions(self):
        gid, _, _ = self.create("Analysts")
        self.db.update_person_group(gid, {"name": "From the PATCH"})
        self.assertEqual(self.revision(gid, "name"), 1)
        self.assertEqual(self.revision(gid, "description"), 0,
                         "and only the field it changed")
        status, result = self.send("SET_PERSON_GROUP_FIELD", gid,
                                   dict(field="name", value="From the queue"), 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))

    def test_a_patch_that_changes_nothing_advances_nothing(self):
        gid, _, _ = self.create("Analysts")
        self.db.update_person_group(gid, {"name": "Analysts"})
        self.assertEqual(self.revision(gid, "name"), 0)

    def test_the_ordinary_membership_endpoints_advance_the_same_revisions(self):
        gid, _, _ = self.create("Analysts")
        person = self.make_person()
        self.db.add_person_to_group(person, gid)
        self.assertEqual(self.member_revision(gid, person), 1)
        self.db.remove_person_from_group(person, gid)
        self.assertEqual(self.member_revision(gid, person), 2)

    def test_replacing_a_membership_set_advances_only_what_changed(self):
        """A profile save that did not touch the groups must not manufacture
        staleness for every device that already holds them."""
        a, _, _ = self.create("A")
        b, _, _ = self.create("B")
        person = self.make_person()
        self.db.set_person_group_memberships(person, [a])
        self.assertEqual(self.member_revision(a, person), 1)
        self.db.set_person_group_memberships(person, [a, b])
        self.assertEqual(self.member_revision(a, person), 1, "untouched")
        self.assertEqual(self.member_revision(b, person), 1)
        self.db.update_person_profile(person, {"about": "x"}, [a, b])
        self.assertEqual(self.member_revision(a, person), 1)
        self.assertEqual(self.member_revision(b, person), 1)

    def test_the_ordinary_delete_reparents_through_the_same_boundary(self):
        top, _, _ = self.create("Top")
        child, _, _ = self.create("Child", parent_id=top)
        leaf, _, _ = self.create("Leaf", parent_id=child)
        self.db.delete_person_group(child)
        self.assertEqual(self.stored(leaf)["parent_id"], top)
        self.assertEqual(self.revision(leaf, "parent_id"), 1)

    # ---- projections -------------------------------------------------------

    def test_the_group_state_projection_reports_revisions_only(self):
        gid, _, _ = self.create("Analysts")
        person = self.make_person()
        self.send("ADD_PERSON_GROUP_MEMBER", gid, dict(person_id=person), 0)
        self.send("SET_PERSON_GROUP_FIELD", gid, dict(field="name", value="Engineers"), 0)
        state = self.db.get_person_group_sync_state(gid)
        self.assertEqual(state["group_id"], gid)
        self.assertEqual(sorted(state["fields"]), sorted(groups.FIELDS))
        self.assertEqual(state["fields"]["name"], {"revision": 1})
        for entry in state["fields"].values():
            self.assertEqual(sorted(entry), ["revision"])
        self.assertEqual(state["members"],
                         [{"person_id": person, "revision": 1, "present": True}])
        self.assertIsNone(self.db.get_person_group_sync_state(entity_ids.generate("PG")))

    def test_the_group_state_keeps_membership_tombstones(self):
        gid, _, _ = self.create("Analysts")
        person = self.make_person()
        self.send("ADD_PERSON_GROUP_MEMBER", gid, dict(person_id=person), 0)
        self.send("REMOVE_PERSON_GROUP_MEMBER", gid, dict(person_id=person), 1)
        state = self.db.get_person_group_sync_state(gid)
        self.assertEqual(state["members"],
                         [{"person_id": person, "revision": 2, "present": False}])

    def test_a_membership_that_predates_revisions_reads_as_zero(self):
        """Construction is not mutation: a person added when the group was
        created has not 'changed', and revision 0 is the honest answer."""
        gid, _, _ = self.create("Analysts")
        person = self.make_person()
        with self.db.connection() as conn:
            conn.execute(
                "INSERT INTO person_group_members (person_id, group_id) VALUES (?, ?)",
                (person, gid))
            conn.commit()
        state = self.db.get_person_group_sync_state(gid)
        self.assertEqual(state["members"],
                         [{"person_id": person, "revision": 0, "present": True}])

    def test_the_person_side_projection_names_the_same_scopes(self):
        a, _, _ = self.create("A")
        b, _, _ = self.create("B")
        person = self.make_person()
        self.send("ADD_PERSON_GROUP_MEMBER", a, dict(person_id=person), 0)
        self.send("ADD_PERSON_GROUP_MEMBER", b, dict(person_id=person), 0)
        self.send("REMOVE_PERSON_GROUP_MEMBER", b, dict(person_id=person), 1)
        state = self.db.get_person_groups_state(person)
        self.assertEqual(state["person_id"], person)
        by_group = {g["group_id"]: g for g in state["groups"]}
        self.assertEqual(by_group[a], {"group_id": a, "revision": 1, "present": True})
        self.assertEqual(by_group[b], {"group_id": b, "revision": 2, "present": False})
        self.assertIsNone(self.db.get_person_groups_state(entity_ids.generate("P")))


if __name__ == "__main__":
    unittest.main()


class PersonDeletionSyncTests(unittest.TestCase):
    """DELETE_PERSON: destruction, and the protection that stays canonical."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-person-delete-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.device = str(uuid.uuid4())

    def make_person(self):
        person = entity_ids.generate("P")
        body = {name: "" for name in person_sync.FIELDS}
        body["first_name"], body["last_name"] = "Ada", "Lovelace"
        self.assertEqual(sync_protocol.process_operation(self.db, dict(
            op_id=str(uuid.uuid4()), device_id=self.device, operation="CREATE_PERSON",
            entity_type="person", entity_id=person, payload=body, base_revision=None,
            occurred_at="2026-09-15T10:00:00Z", created_at="2026-09-15T10:00:00Z",
            depends_on=[]))[0], 200)
        return person

    def delete(self, person_id, base=None):
        return sync_protocol.process_operation(self.db, dict(
            op_id=str(uuid.uuid4()), device_id=self.device, operation="DELETE_PERSON",
            entity_type="person", entity_id=person_id, payload={}, base_revision=base,
            occurred_at="2026-09-15T10:00:00Z", created_at="2026-09-15T10:00:00Z",
            depends_on=[]))

    def exists(self, person_id):
        return bool(self.db.execute_query(
            "SELECT 1 FROM persons WHERE id = ?", (person_id,)))

    def test_a_person_with_no_links_is_removed(self):
        person = self.make_person()
        status, result = self.delete(person)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["changed"])
        self.assertFalse(self.exists(person))

    def test_deleting_someone_already_gone_is_convergence(self):
        person = self.make_person()
        self.delete(person)
        status, result = self.delete(person)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])

    def test_destruction_addresses_an_identity_and_carries_no_base_revision(self):
        person = self.make_person()
        status, result = self.delete(person, base=0)
        self.assertEqual((status, result["code"]), (400, "INVALID_BASE_REVISION"))
        self.assertTrue(self.exists(person))

    def test_a_person_credited_on_a_file_is_protected(self):
        """The relationship is a real record of who wrote what, and dropping it
        silently is the one outcome neither path should produce."""
        person = self.make_person()
        work = self.db.add_work(title="A Work")
        self.db.add_role(person, work, "Author")
        status, result = self.delete(person)
        self.assertEqual((status, result["code"]), (409, "PERSON_HAS_LINKS"))
        self.assertTrue(self.exists(person))
        with self.assertRaises(ValueError):
            self.db.delete_person_if_unlinked(person)

    def test_deletion_advances_the_revisions_of_the_memberships_it_removes(self):
        """A device holding "this person is in that group" has to be able to
        discover it was overtaken, rather than replaying an add against
        somebody who no longer exists."""
        person = self.make_person()
        group = self.db.add_person_group("Analysts")
        self.db.add_person_to_group(person, group)
        with self.db.connection() as conn:
            before = groups.get_member_revision(conn, group, person)
        self.delete(person)
        with self.db.connection() as conn:
            self.assertEqual(groups.get_member_revision(conn, group, person), before + 1)
        self.assertEqual(self.db.execute_query(
            "SELECT 1 FROM person_group_members WHERE person_id = ?", (person,)), [])

    def test_the_ordinary_endpoint_shares_the_same_boundary(self):
        person = self.make_person()
        group = self.db.add_person_group("Analysts")
        self.db.add_person_to_group(person, group)
        self.db.delete_person_if_unlinked(person)
        self.assertFalse(self.exists(person))
        with self.db.connection() as conn:
            self.assertEqual(groups.get_member_revision(conn, group, person), 2)
