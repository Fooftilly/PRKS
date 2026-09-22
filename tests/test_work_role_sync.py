"""Work-Person roles: an element conflict unit, with revisions that outlive the
relationship they describe."""
import json
import tempfile
import unittest
import uuid

from backend import sync_protocol, work_role_sync as roles
from backend import person_metadata_sync as person_meta
from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig


class WorkRoleSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-roles-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.work = self.db.add_work("Paper")
        self.jane = self.db.add_person("Jane", "Doe")
        self.ed = self.db.add_person("Ed", "Smith")

    def op(self, present, person=None, role="Author", base=0, credit="",
           operation=None, **changes):
        payload = {"person_id": person or self.jane, "role_type": role}
        operation = operation or ("ADD_WORK_PERSON_ROLE" if present
                                  else "REMOVE_WORK_PERSON_ROLE")
        if operation in roles.CARRIES_CREDIT:
            payload["credit_name"] = credit
        envelope = dict(
            op_id=str(uuid.uuid4()), device_id=str(uuid.uuid4()), operation=operation,
            entity_type="work", entity_id=self.work, payload=payload,
            base_revision=base, occurred_at="2026-09-13T10:00:00Z",
            created_at="2026-09-13T10:00:00Z", depends_on=[])
        envelope.update(changes)
        return envelope

    def send(self, present, **kw):
        return sync_protocol.process_operation(self.db, self.op(present, **kw))

    def state(self, person=None, role="Author"):
        with self.db.connection() as conn:
            return roles.current_state(conn, self.work, person or self.jane, role)

    def revision(self, person=None, role="Author"):
        with self.db.connection() as conn:
            return roles.get_revision(conn, self.work, person or self.jane, role)

    def linked(self):
        return {(r["id"], r["role_type"]) for r in self.db.get_work_roles(self.work)}

    # ---- the element write ----

    def test_add_and_remove_each_advance_one_revision(self):
        code, result = self.send(True)
        self.assertEqual((code, result["code"], result["changed"]), (200, "ACKNOWLEDGED", True))
        self.assertEqual((result["server_revision"], result["present"]), (1, True))
        self.assertIn((self.jane, "Author"), self.linked())

        code, result = self.send(False, base=1)
        self.assertEqual((code, result["code"], result["changed"]), (200, "ACKNOWLEDGED", True))
        self.assertEqual(result["server_revision"], 2)
        self.assertNotIn((self.jane, "Author"), self.linked())

    def test_the_same_desired_state_changes_and_advances_nothing(self):
        self.send(True)
        code, result = self.send(True, base=1)
        self.assertEqual((code, result["code"], result["changed"]), (200, "ACKNOWLEDGED", False))
        self.assertEqual(self.revision(), 1, "a no-op is not a revision")

    def test_a_stale_but_convergent_choice_is_not_a_conflict(self):
        """Two devices that both linked Jane as Author have converged, however
        many revisions apart they started. Producing a conflict merely because
        the revisions differ would ask the user to resolve an agreement."""
        self.send(True)                       # another device, revision 0 -> 1
        code, result = self.send(True, base=0)
        self.assertEqual((code, result["code"], result["changed"]),
                         (200, "ACKNOWLEDGED", False))
        # ... and the same for a removal both devices wanted.
        self.send(False, base=1)
        code, result = self.send(False, base=0)
        self.assertEqual((code, result["code"]), (200, "ACKNOWLEDGED"))

    def test_a_genuine_disagreement_is_a_conflict(self):
        self.send(True)                       # another device links Jane
        code, result = self.send(False, base=0)   # this one, from before that
        self.assertEqual((code, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual((result["current"]["present"], result["requested"]["present"]),
                         (True, False))
        self.assertEqual(result["current_revision"], 1)
        self.assertIn((self.jane, "Author"), self.linked(), "the server's state stands")

    def test_a_future_base_revision_is_refused(self):
        code, result = self.send(True, base=9)
        self.assertEqual((code, result["code"]), (400, "FUTURE_REVISION"))
        self.assertEqual(self.linked(), set())

    # ---- tombstones ----

    def test_the_revision_outlives_the_relationship(self):
        """An offline REMOVE replayed after a remote ADD must be seen as stale.
        Deriving the revision from "does the row exist" would make the removal
        look current, and the user's own later decision would be overwritten by
        an older one."""
        self.send(True)
        self.send(False, base=1)
        self.assertEqual(self.linked(), set())
        self.assertEqual(self.revision(), 2, "the scope remembers, though the row is gone")

        # Another device adds it again; a removal based on revision 1 is stale.
        self.send(True, base=2)
        code, result = self.send(False, base=1)
        self.assertEqual((code, result["code"]), (409, "REVISION_CONFLICT"))

    # ---- independence ----

    def test_each_person_and_role_is_its_own_scope(self):
        """The reason this is an element family. An aggregate would have made
        every independent link on one Work a single conflict."""
        self.send(True, person=self.jane, role="Author")
        self.send(True, person=self.ed, role="Editor")
        self.assertEqual(self.linked(), {(self.jane, "Author"), (self.ed, "Editor")})
        self.assertEqual(self.revision(self.jane, "Author"), 1)
        self.assertEqual(self.revision(self.ed, "Editor"), 1)
        self.assertEqual(self.revision(self.jane, "Editor"), 0, "untouched scopes never move")

    def test_one_person_may_hold_several_roles_on_one_work(self):
        self.send(True, person=self.jane, role="Author")
        self.send(True, person=self.jane, role="Translator")
        self.assertEqual(self.linked(), {(self.jane, "Author"), (self.jane, "Translator")})
        self.send(False, person=self.jane, role="Author", base=1)
        self.assertEqual(self.linked(), {(self.jane, "Translator")},
                         "removing one role leaves the other")

    def test_links_are_appended_in_the_order_they_arrive(self):
        """`order_index` is assigned by the server, never chosen by a caller.
        Nothing requires the values to be contiguous -- ORDER BY is a total
        order whatever they are -- which is why independent element operations
        cannot produce an invalid author order."""
        self.send(True, person=self.jane, role="Author")
        self.send(True, person=self.ed, role="Author")
        row = next(w for w in self.db.get_all_works() if w["id"] == self.work)
        self.assertEqual(row["linked_authors"], "Jane Doe, Ed Smith")
        self.assertEqual(row["primary_author"], "Jane Doe")
        indexes = [r["order_index"] for r in self.db.get_work_roles(self.work)]
        self.assertEqual(indexes, sorted(indexes))

    # ---- the vocabulary ----

    def test_the_role_vocabulary_is_closed(self):
        """A durable envelope is replayed exactly, so a typo'd role would be
        stored forever and match no filter, icon or BibTeX mapping. Refused
        rather than normalized: turning an unknown role into "Author" would
        assert a relationship the user never described."""
        for bad in ("author", "Auther", "", "Producer", None, 7):
            with self.subTest(role=repr(bad)):
                code, result = self.send(True, role=bad)
                self.assertEqual((code, result), (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual(self.linked(), set())
        for good in roles.ROLE_TYPES:
            with self.subTest(role=good):
                self.assertEqual(self.send(True, role=good)[1]["code"], "ACKNOWLEDGED")

    def test_every_write_path_agrees_about_the_role_domain(self):
        """The durable validator refused unknown roles while `add_role()` took
        anything, so `Producer` was impossible offline and fine online -- and
        the accepted row then matched no filter, icon or BibTeX mapping."""
        with self.assertRaises(ValueError):
            self.db.add_role(self.jane, self.work, "Producer")
        with self.assertRaises(ValueError):
            self.db.insert_initial_role(self.work, self.jane, "Producer")
        self.assertEqual(self.send(True, role="Producer")[1], {"code": "INVALID_ENVELOPE"})
        self.assertEqual(self.linked(), set())

    def test_the_envelope_carries_exactly_the_relationship(self):
        for payload in ({"person_id": "P-1"}, {"role_type": "Author"},
                        {"person_id": "P-1", "role_type": "Author", "order_index": 3},
                        {"person_id": "P-1", "role_type": "Author"},
                        {"person_id": "", "role_type": "Author", "credit_name": ""}):
            with self.subTest(payload=payload):
                envelope = self.op(True)
                envelope["payload"] = payload
                self.assertEqual(sync_protocol.process_operation(self.db, envelope),
                                 (400, {"code": "INVALID_ENVELOPE"}))

    def test_a_null_base_revision_is_refused(self):
        self.assertEqual(sync_protocol.process_operation(self.db, self.op(True, base_revision=None)),
                         (400, {"code": "INVALID_BASE_REVISION"}))

    # ---- missing ends ----

    def test_a_missing_work_or_person_is_not_a_conflict(self):
        self.assertEqual(self.send(True, entity_id="W-NOPE")[1]["code"], "ENTITY_NOT_FOUND")
        self.assertEqual(self.send(True, person="P-NOPE")[1]["code"], "PERSON_NOT_FOUND")
        self.assertEqual(self.linked(), set(),
                         "Person creation is not part of this family")

    # ---- one write boundary ----

    def test_the_ordinary_endpoints_share_the_revision_boundary(self):
        """`POST /api/roles`, credit PATCH, and the durable operation are the
        same canonical write. A relationship changed without its revision would
        be invisible to every offline device, which would then overwrite it."""
        self.db.add_role(self.jane, self.work, "Author")
        self.assertEqual(self.revision(), 1, "the ordinary path advances it too")
        self.assertTrue(self.db.update_role_credit_name(
            self.work, self.jane, "Author", 0, "Jane D."))
        self.assertEqual((self.state(), self.revision()), ("Jane D.", 2))
        self.assertIn("Jane D.", self.db.get_person(self.jane)["aliases"],
                      "the write boundary owns the alias side effect")
        self.assertTrue(self.db.update_role_credit_name(
            self.work, self.jane, "Author", 0, ""))
        self.assertEqual((self.state(), self.revision()), ("", 3))
        self.db.delete_work_role(self.work, self.jane, "Author")
        self.assertEqual(self.revision(), 4)

        # ... and a durable operation measured against that revision lands.
        self.assertEqual(self.send(True, base=4)[1]["code"], "ACKNOWLEDGED")
        self.assertEqual(self.revision(), 5)

    def test_a_stale_order_index_does_not_silently_remove_nothing(self):
        """The old delete matched on `order_index`, so a caller holding a stale
        index removed nothing and was told it succeeded. At most one row exists
        per (person, work, role), so the triple is the identity."""
        self.db.add_role(self.jane, self.work, "Author", order_index=7)
        self.assertTrue(self.db.delete_work_role(self.work, self.jane, "Author", 0))
        self.assertEqual(self.linked(), set())

    def test_a_stale_order_index_still_updates_the_credit(self):
        """The ordinary credit PATCH had the same identity bug as delete: it
        matched `order_index`, so a caller holding 0 against a row at 1 was
        told the relationship did not exist."""
        self.db.add_role(self.jane, self.work, "Author")
        self.db.add_role(self.ed, self.work, "Author")
        ed_index = next(r["order_index"] for r in self.db.get_work_roles(self.work)
                        if r["id"] == self.ed)
        self.assertNotEqual(ed_index, 0)
        self.assertTrue(self.db.update_role_credit_name(
            self.work, self.ed, "Author", 0, "E. Smith"))
        self.assertEqual(self.state(self.ed), "E. Smith")
        self.assertEqual(self.revision(self.ed), 2)
        self.assertEqual(self.revision(self.jane), 1, "the other triple is untouched")

    def test_credit_patch_on_a_missing_triple_does_not_create_it(self):
        """PATCH is an edit, not an add. Routing it through set_role_state
        without an existence check would insert the relationship and report
        success for a 404."""
        self.assertFalse(self.db.update_role_credit_name(
            self.work, self.jane, "Author", 0, "Jane D."))
        self.assertEqual(self.linked(), set())
        self.assertEqual(self.revision(), 0)

    # ---- construction vs mutation ----

    def test_relationships_a_work_is_born_with_are_revision_zero(self):
        """Construction is not mutation. Manufacturing revision 1 for a Work
        created with two Authors would make every device's first read look like
        two missed changes it has to reconcile.

        Through the real construction boundary, not a direct INSERT: routing
        construction at `add_role()` made every created Work start at revision
        1 and threw away the importer's author order, and a SQL fixture would
        have proved only that the projection understands revision-zero rows.
        """
        self.db.insert_initial_role(self.work, self.jane, "Author", order_index=0)
        self.db.insert_initial_role(self.work, self.ed, "Author", order_index=1)
        state = {(s["person_id"], s["role_type"]): s
                 for s in roles.get_roles_state(self.db, self.work)["scopes"]}
        self.assertEqual(state[(self.jane, "Author")]["revision"], 0)
        self.assertEqual(state[(self.ed, "Author")]["revision"], 0)
        row = next(w for w in self.db.get_all_works() if w["id"] == self.work)
        self.assertEqual(row["linked_authors"], "Jane Doe, Ed Smith",
                         "the caller's author order is preserved at construction")

        # Mutating one moves only that element.
        self.send(False, person=self.jane, base=0)
        self.assertEqual(self.revision(self.jane, "Author"), 1)
        self.assertEqual(self.revision(self.ed, "Author"), 0, "the other is untouched")

    def test_construction_preserves_the_order_the_caller_states(self):
        """An importer replaying a BibTeX author list means the order it
        states. `add_role()` ignored `order_index` and appended, so an import
        that placed an author second could silently become first."""
        self.db.insert_initial_role(self.work, self.ed, "Author", order_index=1)
        self.db.insert_initial_role(self.work, self.jane, "Author", order_index=0)
        row = next(w for w in self.db.get_all_works() if w["id"] == self.work)
        self.assertEqual(row["linked_authors"], "Jane Doe, Ed Smith")
        self.assertEqual(row["primary_author"], "Jane Doe")

    def test_mutation_appends_and_never_takes_a_callers_placement(self):
        """After the Work exists, order is server-owned: an index chosen by one
        device is a claim about placement it cannot coordinate with others."""
        self.db.insert_initial_role(self.work, self.jane, "Author", order_index=0)
        self.db.add_role(self.ed, self.work, "Author", order_index=0)
        row = next(w for w in self.db.get_all_works() if w["id"] == self.work)
        self.assertEqual(row["linked_authors"], "Jane Doe, Ed Smith",
                         "appended after what was already there")

    # ---- credit_name is part of the element's state ----

    def test_a_link_carries_its_credit_override(self):
        """"The name on THIS file". It reaches linked_authors, the card credit,
        BibTeX and Person aliases -- so a durable ADD that dropped it would
        silently lose a value the user typed."""
        code, result = self.send(True, credit="Mark Twain")
        self.assertEqual((code, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(result["credit_name"], "Mark Twain")
        self.assertEqual(self.state(), "Mark Twain")
        row = next(w for w in self.db.get_all_works() if w["id"] == self.work)
        self.assertEqual(row["linked_authors"], "Mark Twain",
                         "the override is what the card credits")

    def test_the_credit_override_becomes_a_person_alias(self):
        """A long-standing side effect of linking with an override, and People
        search depends on it. At the canonical boundary now, so every write
        path does it rather than the one handler that remembered."""
        self.send(True, credit="Mark Twain")
        self.assertIn("Mark Twain", self.db.get_person(self.jane)["aliases"])

    def aliases_revision(self, person=None):
        with self.db.connection() as conn:
            return person_meta.get_revision(conn, person or self.jane, "aliases")

    def test_promotable_credit_advances_person_aliases_revision_once(self):
        """#119: promoting a credit into aliases is a Person-field mutation."""
        self.assertEqual(self.aliases_revision(), 0)
        code, result = self.send(True, credit="Mark Twain")
        self.assertEqual((code, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(self.db.get_person(self.jane)["aliases"], "Mark Twain")
        self.assertEqual(self.aliases_revision(), 1)
        self.assertEqual(result["aliases_revision"], 1,
                         "ACK must carry the Person aliases revision so the "
                         "client can patch person-metadata-state")
        self.assertEqual(self.revision(), 1, "role revision is a separate scope")

    def test_ack_omits_aliases_revision_when_promotion_is_a_no_op(self):
        code, result = self.send(True, credit="")
        self.assertEqual((code, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertNotIn("aliases_revision", result)
        code, result = self.send(True, base=1, credit="Smith, John",
                                 operation="SET_WORK_PERSON_ROLE_CREDIT")
        self.assertEqual((code, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(self.state(), "Smith, John")
        self.assertNotIn("aliases_revision", result,
                         "comma-bearing credits stay on the role")

    def test_already_known_alias_does_not_bump_aliases_revision(self):
        self.db.update_person_profile(self.jane, {"aliases": "Mark Twain"})
        self.assertEqual(self.aliases_revision(), 1)
        self.send(True, credit="Mark Twain")
        self.assertEqual(self.db.get_person(self.jane)["aliases"], "Mark Twain")
        self.assertEqual(self.aliases_revision(), 1, "already represented is a no-op")
        self.assertEqual(self.revision(), 1)

    def test_comma_bearing_credit_is_preserved_on_role_not_split_into_aliases(self):
        """#118: `Smith, John` is one printed name. Legacy aliases cannot hold
        it losslessly, so auto-promotion is refused rather than inventing
        `Smith` and `John` as separate Person aliases."""
        self.assertTrue(roles.credit_promotable_as_alias("Mark Twain"))
        self.assertFalse(roles.credit_promotable_as_alias("Smith, John"))

        code, result = self.send(True, credit="Smith, John")
        self.assertEqual((code, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(self.state(), "Smith, John")
        row = next(w for w in self.db.get_all_works() if w["id"] == self.work)
        self.assertEqual(row["linked_authors"], "Smith, John")

        aliases = self.db.get_person(self.jane)["aliases"] or ""
        self.assertEqual(aliases, "")
        self.assertNotIn("Smith", roles.parse_person_aliases(aliases))
        self.assertNotIn("John", roles.parse_person_aliases(aliases))
        self.assertEqual(self.aliases_revision(), 0)

        # Ordinary mutation path: same containment.
        other = self.db.add_work("Credit patch")
        self.db.add_role(self.ed, other, "Author")
        self.assertTrue(self.db.update_role_credit_name(
            other, self.ed, "Author", 0, "Smith, John"))
        with self.db.connection() as conn:
            self.assertEqual(
                roles.current_state(conn, other, self.ed, "Author"), "Smith, John")
        ed_aliases = self.db.get_person(self.ed)["aliases"] or ""
        self.assertEqual(ed_aliases, "")
        self.assertEqual(self.aliases_revision(self.ed), 0)

    def test_existing_comma_delimited_aliases_remain_readable(self):
        """Containment must not reinterpret stored alias text."""
        self.db.update_person_profile(
            self.jane, {"aliases": "Samuel Clemens, Mark Twain"})
        self.assertEqual(
            roles.parse_person_aliases(self.db.get_person(self.jane)["aliases"]),
            ["Samuel Clemens", "Mark Twain"])
        self.send(True, credit="S. L. Clemens")
        self.assertEqual(
            roles.parse_person_aliases(self.db.get_person(self.jane)["aliases"]),
            ["Samuel Clemens", "Mark Twain", "S. L. Clemens"])

    def test_construction_with_credit_advances_existing_person_aliases_revision(self):
        """Construction is not a Work-role revision, but it still mutates an
        existing Person field when it promotes a credit."""
        self.assertEqual(self.aliases_revision(), 0)
        born = self.db.add_work("Born with credit")
        self.db.insert_initial_role(
            born, self.jane, "Author", order_index=0, credit_name="Mark Twain")
        with self.db.connection() as conn:
            self.assertEqual(roles.get_revision(conn, born, self.jane, "Author"), 0)
        self.assertEqual(self.db.get_person(self.jane)["aliases"], "Mark Twain")
        self.assertEqual(self.aliases_revision(), 1)

        # Comma-bearing construction keeps the role credit, skips aliases.
        born2 = self.db.add_work("Born inverted")
        self.db.insert_initial_role(
            born2, self.ed, "Author", order_index=0, credit_name="Smith, John")
        with self.db.connection() as conn:
            self.assertEqual(
                roles.current_state(conn, born2, self.ed, "Author"), "Smith, John")
        self.assertEqual(self.db.get_person(self.ed)["aliases"] or "", "")
        self.assertEqual(self.aliases_revision(self.ed), 0)

    def test_ordinary_and_durable_role_writes_share_alias_side_effects(self):
        online = self.db.add_work("Online credit")
        self.db.add_role(self.jane, online, "Author", credit_name="Mark Twain")
        self.assertEqual(self.db.get_person(self.jane)["aliases"], "Mark Twain")
        self.assertEqual(self.aliases_revision(), 1)

        durable = self.db.add_work("Durable credit")
        env = self.op(True, person=self.ed, credit="E. Smith")
        env["entity_id"] = durable
        code, result = sync_protocol.process_operation(self.db, env)
        self.assertEqual((code, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(self.db.get_person(self.ed)["aliases"], "E. Smith")
        self.assertEqual(self.aliases_revision(self.ed), 1)

    def test_credit_derived_alias_makes_stale_offline_aliases_conflict(self):
        """#119 conflict-loss sequence: after promotion advances aliases
        revision, a stale base-0 aliases write must conflict rather than
        silently overwrite the credit-derived alias."""
        self.send(True, credit="Mark Twain")
        self.assertEqual(self.aliases_revision(), 1)
        status, result = sync_protocol.process_operation(self.db, dict(
            op_id=str(uuid.uuid4()),
            device_id=str(uuid.uuid4()),
            operation="SET_PERSON_METADATA_FIELD", entity_type="person",
            entity_id=self.jane,
            payload={"field": "aliases", "value": "Samuel Clemens"},
            base_revision=0, occurred_at="2026-09-13T10:00:00Z",
            created_at="2026-09-13T10:00:00Z", depends_on=[]))
        self.assertEqual(status, 409)
        self.assertEqual(result["code"], "REVISION_CONFLICT")
        self.assertEqual(result["current_revision"], 1)
        self.assertEqual(result["current_value"], "Mark Twain")
        self.assertEqual(self.db.get_person(self.jane)["aliases"], "Mark Twain")

    def test_replaying_the_same_credit_does_not_keep_bumping_aliases(self):
        self.send(True, credit="Mark Twain")
        self.assertEqual(self.aliases_revision(), 1)
        code, result = self.send(True, base=1, credit="Mark Twain")
        self.assertEqual((code, result["code"], result["changed"]),
                         (200, "ACKNOWLEDGED", False))
        self.assertEqual(self.aliases_revision(), 1)
        # A second append of the same credit is also a no-op.
        with self.db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.assertFalse(roles._append_person_alias(conn, self.jane, "Mark Twain"))
        self.assertEqual(self.aliases_revision(), 1)

    def test_two_devices_choosing_different_credits_have_not_converged(self):
        """Presence alone was not the state. A boolean model would have called
        this agreement and silently kept one device's name."""
        self.send(True, credit="Mark Twain")
        code, result = self.send(True, base=0, credit="S. Clemens")
        self.assertEqual((code, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(result["current"], {"present": True, "credit_name": "Mark Twain"})
        self.assertEqual(result["requested"], {"present": True, "credit_name": "S. Clemens"})

    def test_two_devices_choosing_the_same_credit_have_converged(self):
        self.send(True, credit="Mark Twain")
        code, result = self.send(True, base=0, credit="Mark Twain")
        self.assertEqual((code, result["code"], result["changed"]),
                         (200, "ACKNOWLEDGED", False))

    def test_the_credit_can_be_edited_and_cleared_revision_aware(self):
        self.send(True, credit="Mark Twain")
        code, result = sync_protocol.process_operation(self.db, self.op(
            True, base=1, credit="M. Twain", operation="SET_WORK_PERSON_ROLE_CREDIT"))
        self.assertEqual((code, result["code"], result["changed"]),
                         (200, "ACKNOWLEDGED", True))
        self.assertEqual((self.state(), self.revision()), ("M. Twain", 2))

        # Clearing it reveals the canonical Person name again.
        sync_protocol.process_operation(self.db, self.op(
            True, base=2, credit="", operation="SET_WORK_PERSON_ROLE_CREDIT"))
        self.assertEqual(self.state(), "")
        row = next(w for w in self.db.get_all_works() if w["id"] == self.work)
        self.assertEqual(row["linked_authors"], "Jane Doe")

    def test_editing_the_credit_of_a_link_that_is_not_there_is_said_plainly(self):
        """Not a revision disagreement: there is nothing to edit, so the client
        re-reads rather than being offered a choice between two states one of
        which does not exist."""
        code, result = sync_protocol.process_operation(self.db, self.op(
            True, base=0, credit="X", operation="SET_WORK_PERSON_ROLE_CREDIT"))
        self.assertEqual((code, result["code"]), (409, "ROLE_NOT_PRESENT"))

    def test_a_credit_name_is_bounded(self):
        code, result = self.send(True, credit="x" * (roles.MAX_CREDIT_NAME_BYTES + 1))
        self.assertEqual((code, result), (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual(self.linked(), set())

    def test_ordinary_writes_also_bound_the_credit_name(self):
        """The durable envelope was the only layer that enforced 500 bytes, so
        ordinary add/edit/construction accepted a value no offline device
        could ever store."""
        too_long = "x" * (roles.MAX_CREDIT_NAME_BYTES + 1)
        with self.assertRaises(ValueError):
            self.db.add_role(self.jane, self.work, "Author", credit_name=too_long)
        self.assertEqual(self.linked(), set())
        self.assertEqual(self.revision(), 0)

        self.db.add_role(self.jane, self.work, "Author")
        with self.assertRaises(ValueError):
            self.db.update_role_credit_name(
                self.work, self.jane, "Author", 0, too_long)
        self.assertEqual((self.state(), self.revision()), ("", 1))

        other = self.db.add_work("Born with a credit")
        with self.assertRaises(ValueError):
            self.db.insert_initial_role(
                other, self.jane, "Author", credit_name=too_long)
        self.assertEqual(self.db.get_work_roles(other), [])

        self.db.add_role(self.ed, self.work, "Editor",
                         credit_name="x" * roles.MAX_CREDIT_NAME_BYTES)
        self.assertEqual(len(self.state(self.ed, "Editor")),
                         roles.MAX_CREDIT_NAME_BYTES)

    # ---- the structured projection the overlay reads ----

    def test_a_browse_row_carries_enough_to_undo_a_credit_override(self):
        """`display_name` alone is lossy in the direction the overlay needs.

        "Mark Twain" cannot be turned back into "Samuel Clemens", so a pending
        edit that CLEARS an override could not render its own result -- the row
        would have to go and find a Person cache to do it. A browse row is
        self-sufficient for the fields it promises to render.
        """
        self.send(True, credit="Mark Twain")
        row = next(w for w in self.db.get_all_works() if w["id"] == self.work)
        link = row["linked_people"][0]
        self.assertEqual(link["person_id"], self.jane)
        self.assertEqual(link["role_type"], "Author")
        self.assertEqual(link["credit_name"], "Mark Twain")
        self.assertEqual(link["display_name"], "Mark Twain")
        self.assertEqual(link["canonical_name"], "Jane Doe",
                         "the Person's own name, so clearing the override is reconstructible")
        self.assertEqual(row["linked_authors"], "Mark Twain")

        # Clearing it locally must reach exactly the canonical name.
        self.assertEqual(
            link["canonical_name"],
            next(w for w in self.db.get_all_works() if w["id"] == self.work)["linked_people"][0]["canonical_name"])

    def test_the_structured_links_carry_the_order_the_flattened_columns_use(self):
        """The overlay recomputes `linked_authors` from these, so they have to
        agree with it about order -- otherwise a pending change would silently
        reorder the credit line."""
        self.db.insert_initial_role(self.work, self.jane, "Author", order_index=0)
        self.db.insert_initial_role(self.work, self.ed, "Author", order_index=1)
        row = next(w for w in self.db.get_all_works() if w["id"] == self.work)
        names = [l["display_name"] for l in row["linked_people"]
                 if l["role_type"] == "Author"]
        self.assertEqual(", ".join(names), row["linked_authors"])
        self.assertEqual([l["order_index"] for l in row["linked_people"]], [0, 1])

    # ---- the state projection ----

    def test_the_state_projection_reports_revisions_and_tombstones(self):
        self.send(True, person=self.jane, role="Author")
        self.send(True, person=self.ed, role="Editor")
        self.send(False, person=self.ed, role="Editor", base=1)
        state = roles.get_roles_state(self.db, self.work)
        self.assertEqual(state["work_id"], self.work)
        by_person = {(s["person_id"], s["role_type"]): s for s in state["scopes"]}
        self.assertEqual(by_person[(self.jane, "Author")],
                         {"person_id": self.jane, "role_type": "Author",
                          "revision": 1, "present": True})
        self.assertEqual(by_person[(self.ed, "Editor")],
                         {"person_id": self.ed, "role_type": "Editor",
                          "revision": 2, "present": False},
                         "a tombstone is what makes a pending removal judgeable")
        self.assertIsNone(roles.get_roles_state(self.db, "W-NOPE"))

    def test_the_state_projection_is_scoped_to_its_own_work(self):
        other = self.db.add_work("Other")
        self.send(True)
        with self.db.connection() as conn:
            roles.set_role_state(conn, other, self.ed, "Author", True)
        state = roles.get_roles_state(self.db, self.work)
        self.assertEqual([s["person_id"] for s in state["scopes"]], [self.jane])

    def test_scope_keys_are_structural(self):
        """Ids need not exclude any delimiter for the scope to stay
        unambiguous."""
        self.assertEqual(json.loads(roles.scope_key("W-1", "P-2", "Author")),
                         ["W-1", "P-2", "Author"])
        self.assertNotEqual(roles.scope_key("W-1", "P-2", "Author"),
                            roles.scope_key("W-1", "P-2", "Editor"))
