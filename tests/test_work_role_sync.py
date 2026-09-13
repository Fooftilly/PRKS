"""Work-Person roles: an element conflict unit, with revisions that outlive the
relationship they describe."""
import json
import tempfile
import unittest
import uuid

from backend import sync_protocol, work_role_sync as roles
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

    def op(self, present, person=None, role="Author", base=0, **changes):
        envelope = dict(
            op_id=str(uuid.uuid4()), device_id=str(uuid.uuid4()),
            operation="ADD_WORK_PERSON_ROLE" if present else "REMOVE_WORK_PERSON_ROLE",
            entity_type="work", entity_id=self.work,
            payload={"person_id": person or self.jane, "role_type": role},
            base_revision=base, occurred_at="2026-09-13T10:00:00Z",
            created_at="2026-09-13T10:00:00Z", depends_on=[])
        envelope.update(changes)
        return envelope

    def send(self, present, **kw):
        return sync_protocol.process_operation(self.db, self.op(present, **kw))

    def revision(self, person=None, role="Author"):
        with self.db.connection() as conn:
            return roles.get_revision(conn, self.work, person or self.jane, role)

    def linked(self):
        return {(r["id"], r["role_type"]) for r in self.db.get_work_roles(self.work)}

    # ---- the element write ----

    def test_add_and_remove_each_advance_one_revision(self):
        code, result = self.send(True)
        self.assertEqual((code, result["code"], result["changed"]), (200, "ACKNOWLEDGED", True))
        self.assertEqual(result["server_revision"], 1)
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
        self.assertEqual((result["current_state"], result["requested_state"]), (True, False))
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

    def test_the_envelope_carries_exactly_the_relationship(self):
        for payload in ({"person_id": "P-1"}, {"role_type": "Author"},
                        {"person_id": "P-1", "role_type": "Author", "order_index": 3},
                        {"person_id": "", "role_type": "Author"}):
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
        """`POST /api/roles` and the durable operation are the same canonical
        write. A relationship changed without its revision would be invisible
        to every offline device, which would then overwrite it."""
        self.db.add_role(self.jane, self.work, "Author")
        self.assertEqual(self.revision(), 1, "the ordinary path advances it too")
        self.db.delete_work_role(self.work, self.jane, "Author")
        self.assertEqual(self.revision(), 2)

        # ... and a durable operation measured against that revision lands.
        self.assertEqual(self.send(True, base=2)[1]["code"], "ACKNOWLEDGED")
        self.assertEqual(self.revision(), 3)

    def test_a_stale_order_index_does_not_silently_remove_nothing(self):
        """The old delete matched on `order_index`, so a caller holding a stale
        index removed nothing and was told it succeeded. At most one row exists
        per (person, work, role), so the triple is the identity."""
        self.db.add_role(self.jane, self.work, "Author", order_index=7)
        self.assertTrue(self.db.delete_work_role(self.work, self.jane, "Author", 0))
        self.assertEqual(self.linked(), set())

    # ---- construction vs mutation ----

    def test_relationships_a_work_is_born_with_are_revision_zero(self):
        """Construction is not mutation. Manufacturing revision 1 for a Work
        created with an Author would make every device's first read look like a
        missed change."""
        with self.db.connection() as conn:
            conn.execute(
                "INSERT INTO roles (person_id, work_id, role_type, order_index) VALUES (?, ?, ?, 0)",
                (self.jane, self.work, "Author"))
        state = roles.get_roles_state(self.db, self.work)
        self.assertEqual(state["scopes"],
                         [{"person_id": self.jane, "role_type": "Author",
                           "revision": 0, "present": True}])

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
            roles.set_state(conn, other, self.ed, "Author", True)
        state = roles.get_roles_state(self.db, self.work)
        self.assertEqual([s["person_id"] for s in state["scopes"]], [self.jane])

    def test_scope_keys_are_structural(self):
        """Ids need not exclude any delimiter for the scope to stay
        unambiguous."""
        self.assertEqual(json.loads(roles.scope_key("W-1", "P-2", "Author")),
                         ["W-1", "P-2", "Author"])
        self.assertNotEqual(roles.scope_key("W-1", "P-2", "Author"),
                            roles.scope_key("W-1", "P-2", "Editor"))
