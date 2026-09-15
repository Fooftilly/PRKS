"""Concepts: five shapes, and parity with the ordinary endpoints.

The decision worth testing hardest is why `name` and `aliases` share one
revision: renaming a Concept keeps the old name reachable as an alias, so the
two cannot be judged apart.
"""
import tempfile
import unittest
import uuid

from backend import concept_sync as concepts, entity_ids, sync_protocol
from backend.db_manager import PRKSDatabase
from backend.research_network import (
    ResearchError,
    create_concept,
    delete_concept,
    get_concept,
    replace_concept_aliases,
    replace_concept_parents,
    update_concept,
)
from backend.storage.config import StorageConfig


class ConceptSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-concept-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.device = str(uuid.uuid4())

    # ---- helpers ----------------------------------------------------------

    def send(self, operation, entity_id, payload, base=None, op_id=None):
        return sync_protocol.process_operation(self.db, dict(
            op_id=op_id or str(uuid.uuid4()), device_id=self.device, operation=operation,
            entity_type="concept", entity_id=entity_id, payload=payload,
            base_revision=base, occurred_at="2026-09-15T10:00:00Z",
            created_at="2026-09-15T10:00:00Z", depends_on=[]))

    def create(self, name, description="", concept_id=None):
        cid = concept_id or entity_ids.generate("C")
        status, result = self.send("CREATE_CONCEPT", cid,
                                   dict(name=name, description=description), None)
        return cid, status, result

    def identity(self, concept_id, name, aliases, base):
        return self.send("SET_CONCEPT_IDENTITY", concept_id,
                         dict(name=name, aliases=list(aliases)), base)

    def parents(self, concept_id, parent_ids, base):
        return self.send("SET_CONCEPT_PARENTS", concept_id,
                         dict(parent_ids=list(parent_ids)), base)

    def field(self, concept_id, value, base):
        return self.send("SET_CONCEPT_FIELD", concept_id,
                         dict(field="description", value=value), base)

    def stored(self, concept_id):
        rows = self.db.execute_query("SELECT * FROM concepts WHERE id = ?", (concept_id,))
        return rows[0] if rows else None

    def state(self, concept_id):
        with self.db.connection() as conn:
            return concepts.current_identity(conn, concept_id)

    def revision(self, concept_id, field):
        with self.db.connection() as conn:
            return concepts.get_revision(conn, concept_id, field)

    def identity_revision(self, concept_id):
        with self.db.connection() as conn:
            return concepts.get_identity_revision(conn, concept_id)

    def parents_revision(self, concept_id):
        with self.db.connection() as conn:
            return concepts.get_parents_revision(conn, concept_id)

    def parent_ids(self, concept_id):
        with self.db.connection() as conn:
            return concepts.current_parents(conn, concept_id)

    # ---- construction -----------------------------------------------------

    def test_a_concept_is_created_under_the_id_the_client_minted(self):
        cid, status, result = self.create("Emergence", "A definition.")
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["changed"])
        self.assertEqual(result["concept"]["name"], "Emergence")
        self.assertEqual(self.stored(cid)["description"], "A definition.")
        # Construction is not mutation: nothing has "changed" yet.
        self.assertEqual(self.revision(cid, "description"), 0)
        self.assertEqual(self.identity_revision(cid), 0)
        self.assertEqual(self.parents_revision(cid), 0)

    def test_creation_requires_a_collision_resistant_id(self):
        for bad in ("C-ABCD1234", "emergence", "P-" + "A" * 32):
            with self.subTest(bad=bad):
                _, status, result = self.create("X", concept_id=bad)
                self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    def test_a_taken_name_is_a_named_refusal(self):
        create_concept(self.db, "Emergence")
        _, status, result = self.create("emergence")
        self.assertEqual((status, result["code"]), (409, "CONCEPT_EXISTS"),
                         "identity is normalized, so case is not a new Concept")

    def test_a_taken_alias_is_the_same_refusal(self):
        """Identity is name-or-alias over one normalized space."""
        first = create_concept(self.db, "Emergence")
        replace_concept_aliases(self.db, first["id"], ["Self-organization"])
        # Normalization is case-folding, not punctuation-folding: a hyphen and
        # a space are different keys, which is the rule already in use.
        _, status, result = self.create("SELF-ORGANIZATION")
        self.assertEqual((status, result["code"]), (409, "CONCEPT_EXISTS"))

    def test_replaying_a_creation_changes_nothing_twice(self):
        cid = entity_ids.generate("C")
        op_id = str(uuid.uuid4())
        first = self.send("CREATE_CONCEPT", cid, dict(name="Once", description=""),
                          None, op_id=op_id)
        second = self.send("CREATE_CONCEPT", cid, dict(name="Once", description=""),
                           None, op_id=op_id)
        self.assertEqual(first, second)

    def test_a_second_delivery_of_the_same_creation_is_idempotent(self):
        cid, _, _ = self.create("Emergence")
        _, status, result = self.create("Emergence Again", concept_id=cid)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])
        self.assertEqual(self.stored(cid)["name"], "Emergence",
                         "an existing Concept is never rewritten by a replayed creation")

    def test_an_empty_name_is_refused_rather_than_placeheld(self):
        """Unlike a Folder or a Playlist, a Concept has no placeholder name:
        its name IS its identity, and inventing one would invent a key."""
        _, status, result = self.create("   ")
        self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    # ---- the definition ---------------------------------------------------

    def test_the_definition_is_a_scalar_with_its_own_revision(self):
        cid, _, _ = self.create("Emergence")
        status, result = self.field(cid, "Higher-order behaviour.", 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(result["server_revision"], 1)
        self.assertTrue(result["value_omitted"])
        self.assertEqual(self.identity_revision(cid), 0,
                         "a definition is not part of what the Concept IS")

    def test_a_stale_definition_base_against_a_different_value_conflicts(self):
        cid, _, _ = self.create("Emergence")
        self.field(cid, "First", 0)
        status, result = self.field(cid, "Second", 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(result["current_value"], "First")

    def test_a_stale_definition_base_that_agrees_converges(self):
        cid, _, _ = self.create("Emergence")
        self.field(cid, "First", 0)
        status, result = self.field(cid, "First", 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])

    def test_only_the_definition_is_a_field(self):
        cid, _, _ = self.create("Emergence")
        for bad in ("name", "aliases", "parents", "id"):
            with self.subTest(field=bad):
                status, result = self.send(
                    "SET_CONCEPT_FIELD", cid, dict(field=bad, value="x"), 0)
                self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    def test_the_ordinary_endpoint_advances_the_same_definition_revision(self):
        cid, _, _ = self.create("Emergence")
        update_concept(self.db, cid, description="Changed elsewhere")
        self.assertEqual(self.revision(cid, "description"), 1)
        status, _ = self.field(cid, "Mine", 0)
        self.assertEqual(status, 409)

    # ---- identity ---------------------------------------------------------

    def test_a_rename_keeps_the_old_name_reachable(self):
        """Every note that already says `[[concept:Old Name]]` must go on
        resolving -- which is exactly why name and aliases are one unit."""
        cid, _, _ = self.create("Emergence")
        status, result = self.identity(cid, "Emergent behaviour", [], 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(result["name"], "Emergent behaviour")
        self.assertEqual(result["aliases"], ["Emergence"])
        self.assertEqual(self.identity_revision(cid), 1)

    def test_an_alias_edit_shares_the_rename_revision(self):
        cid, _, _ = self.create("Emergence")
        self.identity(cid, "Emergence", ["Self-organization"], 0)
        self.assertEqual(self.identity_revision(cid), 1)
        # A rename measured against the pre-alias base is a conflict, because
        # the rename would have written into the set the alias edit changed.
        status, result = self.identity(cid, "Emergent behaviour", [], 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(result["current_value"], "Emergence")

    def test_an_identity_conflict_is_not_converged_by_an_equal_name(self):
        """Two devices that gave one Concept two different alias sets have made
        two claims about what it IS, and the names agreeing says nothing."""
        cid, _, _ = self.create("Emergence")
        self.identity(cid, "Emergence", ["Self-organization"], 0)
        status, result = self.identity(cid, "Emergence", ["Holism"], 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(self.state(cid)["aliases"], ["Self-organization"])

    def test_an_identity_that_changes_nothing_is_not_a_second_change(self):
        cid, _, _ = self.create("Emergence")
        status, result = self.identity(cid, "Emergence", [], 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])
        self.assertEqual(self.identity_revision(cid), 0)

    def test_a_name_taken_by_another_concept_is_refused(self):
        create_concept(self.db, "Holism")
        cid, _, _ = self.create("Emergence")
        status, result = self.identity(cid, "Holism", [], 0)
        self.assertEqual((status, result["code"]), (409, "CONCEPT_EXISTS"))
        self.assertEqual(self.stored(cid)["name"], "Emergence")

    def test_an_alias_taken_by_another_concept_is_refused(self):
        other = create_concept(self.db, "Holism")
        cid, _, _ = self.create("Emergence")
        status, result = self.identity(cid, "Emergence", ["holism"], 0)
        self.assertEqual((status, result["code"]), (409, "ALIAS_CONFLICT"))
        self.assertEqual(self.state(cid)["aliases"], [])

    def test_an_alias_equal_to_the_name_is_dropped_not_stored(self):
        cid, _, _ = self.create("Emergence")
        status, _ = self.identity(cid, "Emergence", ["emergence"], 0)
        self.assertEqual(status, 200)
        self.assertEqual(self.state(cid)["aliases"], [])

    def test_a_rename_may_deliberately_drop_the_old_name(self):
        """The old name is kept unless the request already says where it
        belongs -- a set that names it differently is a decision."""
        cid, _, _ = self.create("Emergence")
        self.identity(cid, "Emergent behaviour", ["Emergence"], 0)
        self.assertEqual(self.state(cid)["aliases"], ["Emergence"])

    def test_identity_requires_a_base(self):
        cid, _, _ = self.create("Emergence")
        status, result = self.identity(cid, "Other", [], None)
        self.assertEqual((status, result["code"]), (400, "INVALID_BASE_REVISION"))

    def test_the_ordinary_rename_advances_the_same_revision(self):
        cid, _, _ = self.create("Emergence")
        update_concept(self.db, cid, name="Renamed elsewhere")
        self.assertEqual(self.identity_revision(cid), 1)
        self.assertEqual(self.state(cid)["aliases"], ["Emergence"],
                         "the ordinary path keeps the old name too")
        status, _ = self.identity(cid, "Mine", [], 0)
        self.assertEqual(status, 409)

    def test_the_ordinary_alias_replacement_advances_the_same_revision(self):
        cid, _, _ = self.create("Emergence")
        replace_concept_aliases(self.db, cid, ["Self-organization"])
        self.assertEqual(self.identity_revision(cid), 1)
        self.assertEqual(self.state(cid)["aliases"], ["Self-organization"])

    # ---- the hierarchy ----------------------------------------------------

    def test_parents_are_one_aggregate(self):
        child, _, _ = self.create("Emergence")
        first, _, _ = self.create("Systems")
        second, _, _ = self.create("Complexity")
        status, result = self.parents(child, [first, second], 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(sorted(result["parent_ids"]), sorted([first, second]))
        self.assertEqual(self.parents_revision(child), 1)
        self.assertEqual(self.identity_revision(child), 0)

    def test_a_parent_change_advances_the_parents_own_revision(self):
        """A parent's `children` is rendered from the same table, so a device
        holding its page has to be able to discover it was overtaken."""
        child, _, _ = self.create("Emergence")
        parent, _, _ = self.create("Systems")
        before = self.parents_revision(parent)
        self.parents(child, [parent], 0)
        self.assertEqual(self.parents_revision(parent), before + 1)

    def test_the_same_parent_set_converges_even_after_the_revision_moved(self):
        """The hierarchy is a SET: two devices that chose the same parents made
        the same decision, whatever order they wrote them in."""
        child, _, _ = self.create("Emergence")
        first, _, _ = self.create("Systems")
        second, _, _ = self.create("Complexity")
        self.parents(child, [first, second], 0)
        status, result = self.parents(child, [second, first], 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])

    def test_a_different_parent_set_against_a_stale_base_conflicts(self):
        child, _, _ = self.create("Emergence")
        first, _, _ = self.create("Systems")
        second, _, _ = self.create("Complexity")
        self.parents(child, [first], 0)
        status, result = self.parents(child, [second], 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(result["current_count"], 1)
        self.assertEqual(self.parent_ids(child), [first])

    def test_an_unknown_parent_is_a_named_refusal(self):
        child, _, _ = self.create("Emergence")
        status, result = self.parents(child, [entity_ids.generate("C")], 0)
        self.assertEqual((status, result["code"]), (404, "PARENT_NOT_FOUND"))

    def test_a_cycle_is_refused_and_stays_canonical(self):
        top, _, _ = self.create("Systems")
        middle, _, _ = self.create("Complexity")
        self.parents(middle, [top], 0)
        # `top`'s own hierarchy revision moved when it gained a child, so the
        # cycle attempt is measured against that -- not against zero.
        status, result = self.parents(top, [middle], self.parents_revision(top))
        self.assertEqual((status, result["code"]), (409, "CONCEPT_CYCLE"))
        self.assertEqual(self.parent_ids(top), [])

    def test_a_concept_cannot_be_its_own_parent(self):
        cid, _, _ = self.create("Emergence")
        status, result = self.parents(cid, [cid], 0)
        self.assertEqual((status, result["code"]), (409, "CONCEPT_CYCLE"))

    def test_the_ordinary_parent_replacement_advances_the_same_revision(self):
        child, _, _ = self.create("Emergence")
        parent, _, _ = self.create("Systems")
        replace_concept_parents(self.db, child, [parent])
        self.assertEqual(self.parents_revision(child), 1)
        status, _ = self.parents(child, [], 0)
        self.assertEqual(status, 409)

    def test_an_oversized_parent_list_is_refused(self):
        cid, _, _ = self.create("Emergence")
        too_many = [entity_ids.generate("C") for _ in range(concepts.MAX_PARENTS + 1)]
        status, result = self.parents(cid, too_many, 0)
        self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    # ---- deletion ---------------------------------------------------------

    def test_deleting_a_concept_advances_what_it_invalidates(self):
        child, _, _ = self.create("Emergence")
        parent, _, _ = self.create("Systems")
        self.parents(child, [parent], 0)
        before = self.parents_revision(child)
        status, result = self.send("DELETE_CONCEPT", parent, {}, None)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["changed"])
        self.assertIsNone(self.stored(parent))
        self.assertEqual(self.parents_revision(child), before + 1,
                         "the child's hierarchy changed when its parent vanished")
        self.assertEqual(self.parent_ids(child), [], "the edge went with it")

    def test_a_concept_named_by_a_note_is_refused(self):
        from backend.research_network import save_work_notes
        cid, _, _ = self.create("Emergence")
        work = self.db.add_work(title="A paper")
        save_work_notes(self.db, work, "See [[concept:Emergence]].")
        status, result = self.send("DELETE_CONCEPT", cid, {}, None)
        self.assertEqual((status, result["code"]), (409, "CONCEPT_IN_USE"))
        self.assertIsNotNone(self.stored(cid))

    def test_deleting_twice_is_idempotent(self):
        cid, _, _ = self.create("Emergence")
        self.send("DELETE_CONCEPT", cid, {}, None)
        status, result = self.send("DELETE_CONCEPT", cid, {}, None)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])

    def test_deletion_carries_no_base_revision_and_no_payload(self):
        cid, _, _ = self.create("Emergence")
        status, result = self.send("DELETE_CONCEPT", cid, {}, 0)
        self.assertEqual((status, result["code"]), (400, "INVALID_BASE_REVISION"))
        status, result = self.send("DELETE_CONCEPT", cid, {"cascade": True}, None)
        self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))
        self.assertIsNotNone(self.stored(cid))

    def test_the_ordinary_delete_keeps_its_protection(self):
        from backend.research_network import save_work_notes
        cid, _, _ = self.create("Emergence")
        work = self.db.add_work(title="A paper")
        save_work_notes(self.db, work, "See [[concept:Emergence]].")
        with self.assertRaises(ResearchError) as caught:
            delete_concept(self.db, cid)
        self.assertEqual(caught.exception.code, "concept_in_use")

    # ---- state ------------------------------------------------------------

    def test_the_sync_state_carries_the_aggregates_and_the_revisions(self):
        child, _, _ = self.create("Emergence", "A definition.")
        parent, _, _ = self.create("Systems")
        self.identity(child, "Emergence", ["Self-organization"], 0)
        self.parents(child, [parent], 0)
        self.field(child, "Changed.", 0)
        state = self.db.get_concept_sync_state(child)
        self.assertEqual(state["fields"]["description"]["revision"], 1)
        self.assertEqual(state["identity"],
                         {"name": "Emergence", "aliases": ["Self-organization"]})
        self.assertEqual(state["identity_revision"], 1)
        self.assertEqual(state["parent_ids"], [parent])
        self.assertEqual(state["parents_revision"], 1)

    def test_the_sync_state_of_an_unknown_concept_is_none(self):
        self.assertIsNone(self.db.get_concept_sync_state(entity_ids.generate("C")))

    def test_the_detail_and_the_state_agree_on_the_aliases(self):
        """The base a client measures an identity edit against has to be built
        the same way as what it is showing, or it would diff two orderings."""
        cid, _, _ = self.create("Emergence")
        self.identity(cid, "Emergence", ["Zeta", "Alpha"], 0)
        detail = get_concept(self.db, cid)
        state = self.db.get_concept_sync_state(cid)
        self.assertEqual(detail["aliases"], state["identity"]["aliases"])


if __name__ == "__main__":
    unittest.main()
