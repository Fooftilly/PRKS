"""Work-Person roles on PostgreSQL: scope, revisions, tombstones and locks (L-P-1 #632, PR 3).

What SQLite has no counterpart for, or answers differently by design
(``backend/pg_facade/people.py``): Manifestation-scoped roles, Library
isolation, the role decision table run against the domain commands, the
tombstones a Work or Person delete leaves for a recreated id, and the §8.3
Person row locks. Behaviour SQLite shares is compared directly in
``tests/test_people_roles_parity.py``.
"""

from __future__ import annotations

from typing import Any, Optional

from backend.db_manager import MissingPersonError
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.people import persons, roles
from backend.domain.works import commands
from backend.pg_facade import context as facade_context
from tests.pg.test_people_facade import PeopleFacadeTestCase

_MISSING_PERSON = "P-" + "0" * 32


class RoleTestCase(PeopleFacadeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ann = self.library.add_person("Ann", "Lee")
        self.bo = self.library.add_person("Bo", "Ng")
        self.work = self.library.add_work("Essay")
        self.primary = self.library.get_work(self.work)["primary_manifestation_id"]

    def add_manifestation(self, work_id: str, manifestation_id: str) -> str:
        self.sql("INSERT INTO prks.manifestations (library_id, id, work_id) VALUES (%s, %s, %s)",
                 (self.actor.library_id, manifestation_id, work_id))
        return manifestation_id

    def operation(self, role: roles.Role, operation: str, base: int, credit: Optional[str] = None,
                  ) -> tuple[int, dict[str, Any]]:
        return self.run_command(lambda scope: roles.apply_role_operation(scope, role, operation, credit, base),
                                serializable=True)

    def set_role(self, role: roles.Role, present: bool, credit: str = "") -> bool:
        return self.run_command(lambda scope: roles.set_role(scope, role, present, credit),
                                serializable=True)

    def work_scope(self, person_id: str, role_type: str, work_id: Optional[str] = None) -> str:
        return roles.Role(work_id or self.work, person_id, role_type).revision_scope()[1]


class ManifestationScopeTests(RoleTestCase):
    def test_the_work_shows_credits_of_its_primary_manifestation_only(self) -> None:
        other = self.add_manifestation(self.work, "MF-" + "1" * 32)
        self.library.add_role(self.ann, self.work, "Author")
        self.assertTrue(self.set_role(roles.Role(self.work, self.bo, "Translator", self.primary), True, "B. N."))
        self.assertTrue(self.set_role(roles.Role(self.work, self.ann, "Translator", other), True))
        self.assertEqual([(r["id"], r["role_type"], r["manifestation_id"])
                          for r in self.library.get_work_roles(self.work)],
                         [(self.ann, "Author", None), (self.bo, "Translator", self.primary)])
        self.assertEqual([p["display_name"] for p in self.library.get_all_works()[0]["linked_people"]],
                         ["Ann Lee", "B. N."])
        # The Translator role on the other Version is not one of the primary's credits.
        self.assertEqual([w["role_type"] for w in self.library.get_person(self.ann)["works"]], ["Author"])
        self.assertTrue(self.library.has_work_role(self.ann, self.work, "Translator"))
        self.assertEqual(self.revisions("manifestation-person-role"), {
            roles.Role(self.work, self.bo, "Translator", self.primary).revision_scope()[1]: 1,
            roles.Role(self.work, self.ann, "Translator", other).revision_scope()[1]: 1,
        })
        self.assertEqual(self.library.next_role_order_index(self.work), 3)

    def test_one_person_may_translate_two_versions(self) -> None:
        other = self.add_manifestation(self.work, "MF-" + "2" * 32)
        for manifestation in (self.primary, other):
            self.assertTrue(self.set_role(roles.Role(self.work, self.ann, "Translator", manifestation), True))
        self.assertEqual(self.count("roles"), 2)

    def test_a_manifestation_of_another_work_is_refused(self) -> None:
        elsewhere = self.library.get_work(self.library.add_work("Other"))["primary_manifestation_id"]
        with self.assertRaises(roles.RoleTargetMissing) as caught:
            self.set_role(roles.Role(self.work, self.ann, "Translator", elsewhere), True)
        self.assertEqual(caught.exception.code, "MANIFESTATION_NOT_FOUND")
        status, result = self.operation(roles.Role(self.work, self.ann, "Translator", elsewhere),
                                        "ADD_WORK_PERSON_ROLE", 0, "")
        self.assertEqual((status, result["code"]), (404, "ENTITY_NOT_FOUND"))
        self.assertEqual((self.count("roles"), self.revisions("manifestation-person-role")), (0, {}))


class IsolationTests(RoleTestCase):
    def test_a_reader_cannot_link(self) -> None:
        with facade_context.acting_as(self.reader_context()):
            self.assertEqual(self.library.get_work_roles(self.work), [])
            for call in (lambda: self.library.add_role(self.ann, self.work, "Author"),
                         lambda: self.library.delete_work_role(self.work, self.ann, "Author"),
                         lambda: self.library.insert_initial_roles(
                             self.work, [{"person_id": self.ann, "role_type": "Author"}])):
                with self.assertRaises(AccessRefused) as caught:
                    call()
                self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual(self.count("roles"), 0)

    def test_another_librarys_works_and_people_are_out_of_reach(self) -> None:
        self.library.add_role(self.ann, self.work, "Author", credit_name="A. Lee")
        with facade_context.acting_as(self.other_actor().context):
            their_work = self.library.add_work("Theirs")
            their_person = self.library.add_person("Cy", "Oh")
            self.assertEqual(self.library.get_work_roles(self.work), [])
            self.assertFalse(self.library.has_work_role(self.ann, self.work, "Author"))
            self.assertIsNone(self.library.get_person(self.ann))
            self.assertEqual(self.library.missing_person_ids([self.ann]), [self.ann])
            self.assertEqual(self.library.next_role_order_index(self.work), 0)
            with self.assertRaisesRegex(ValueError, "Work not found"):
                self.library.add_role(their_person, self.work, "Editor")
            with self.assertRaisesRegex(ValueError, "Person not found"):
                self.library.add_role(self.ann, their_work, "Editor")
            with self.assertRaisesRegex(ValueError, "Work not found"):
                self.library.insert_initial_roles(self.work, [{"person_id": their_person, "role_type": "Editor"}])
            with self.assertRaises(MissingPersonError):
                self.library.insert_initial_roles(their_work, [{"person_id": self.ann, "role_type": "Editor"}])
            self.assertFalse(self.library.update_role_credit_name(self.work, self.ann, "Author", 0, "Taken"))
            self.assertFalse(self.library.delete_work_role(self.work, self.ann, "Author"))
            self.assertEqual(self.library.get_work_roles(their_work), [])
        self.assertEqual([(r["id"], r["credit_name"]) for r in self.library.get_work_roles(self.work)],
                         [(self.ann, "A. Lee")])
        self.assertEqual(self.sql("SELECT library_id, work_id FROM prks.roles"),
                         [{"library_id": self.actor.library_id, "work_id": self.work}])
        self.assertEqual(self.sql("SELECT library_id, scope_type FROM prks.entity_revisions ORDER BY scope_type"),
                         [{"library_id": self.actor.library_id, "scope_type": "person-field"},
                          {"library_id": self.actor.library_id, "scope_type": "work-person-role"}])


class RevisionTests(RoleTestCase):
    def test_changes_advance_and_no_ops_write_nothing(self) -> None:
        role = roles.Role(self.work, self.ann, "Author")
        self.assertFalse(self.set_role(role, False))
        self.assertFalse(self.library.delete_work_role(self.work, self.bo, "Editor"))
        self.assertEqual(self.revisions("work-person-role"), {})
        self.assertTrue(self.set_role(role, True, "A. Lee"))
        self.assertFalse(self.set_role(role, True, " A. Lee "))
        self.assertTrue(self.set_role(role, True, ""))
        self.assertTrue(self.set_role(role, False))
        self.assertEqual(self.revisions("work-person-role"), {self.work_scope(self.ann, "Author"): 3})
        state = self.run_command(lambda scope: roles.roles_state(scope, self.work))
        self.assertEqual(state["scopes"], [{"person_id": self.ann, "role_type": "Author", "revision": 3,
                                            "present": False}])

    def test_constructed_roles_read_at_revision_0(self) -> None:
        self.library.insert_initial_roles(self.work, [{"person_id": self.bo, "role_type": "Editor"}])
        state = self.run_command(lambda scope: roles.roles_state(scope, self.work))
        self.assertEqual(state["scopes"], [{"person_id": self.bo, "role_type": "Editor", "revision": 0,
                                            "present": True}])
        self.assertEqual(self.revisions("work-person-role"), {})
        self.assertIsNone(self.run_command(lambda scope: roles.roles_state(scope, "W-" + "0" * 32)))

    def test_the_decision_table(self) -> None:
        role = roles.Role(self.work, self.ann, "Author")
        status, result = self.operation(role, "ADD_WORK_PERSON_ROLE", 0, "A. Lee")
        self.assertEqual((status, result["code"], result["server_revision"], result["aliases_revision"],
                          result["first_name"], result["last_name"]),
                         (200, "ACKNOWLEDGED", 1, 1, "Ann", "Lee"))
        status, result = self.operation(role, "ADD_WORK_PERSON_ROLE", 3, "A. Lee")
        self.assertEqual((status, result["code"], result["current_revision"]), (400, "FUTURE_REVISION", 1))
        status, result = self.operation(role, "SET_WORK_PERSON_ROLE_CREDIT", 0, "Ann")
        self.assertEqual((status, result["code"], result["current"], result["requested"]),
                         (409, "REVISION_CONFLICT", {"present": True, "credit_name": "A. Lee"},
                          {"present": True, "credit_name": "Ann"}))
        status, result = self.operation(role, "ADD_WORK_PERSON_ROLE", 0, "A. Lee")  # stale but converged
        self.assertEqual((status, result["code"], result["changed"], result["server_revision"]),
                         (200, "ACKNOWLEDGED", False, 1))
        self.assertNotIn("aliases_revision", result)
        status, result = self.operation(role, "REMOVE_WORK_PERSON_ROLE", 1)
        self.assertEqual((status, result["changed"], result["present"]), (200, True, False))
        status, result = self.operation(role, "SET_WORK_PERSON_ROLE_CREDIT", 2, "x")
        self.assertEqual((status, result["code"]), (409, "ROLE_NOT_PRESENT"))
        status, result = self.operation(roles.Role(self.work, _MISSING_PERSON, "Author"),
                                        "ADD_WORK_PERSON_ROLE", 0, "")
        self.assertEqual((status, result["code"]), (404, "PERSON_NOT_FOUND"))
        status, result = self.operation(roles.Role("W-" + "0" * 32, self.ann, "Author"),
                                        "ADD_WORK_PERSON_ROLE", 0, "")
        self.assertEqual((status, result["code"]), (404, "ENTITY_NOT_FOUND"))
        self.assertEqual(self.revisions("work-person-role"), {self.work_scope(self.ann, "Author"): 2})

    def test_a_constructed_role_at_a_taken_index_goes_after_it(self) -> None:
        for person, role_type, index in ((self.ann, "Author", 0), (self.bo, "Editor", 1),
                                         (self.bo, "Author", 0)):
            self.library.insert_initial_role(self.work, person, role_type, index)
        self.assertEqual([(r["id"], r["role_type"], r["order_index"])
                          for r in self.library.get_work_roles(self.work)],
                         [(self.ann, "Author", 0), (self.bo, "Author", 1), (self.bo, "Editor", 2)])


class TombstoneTests(RoleTestCase):
    def recreate_work(self, work_id: str, manifestation_id: str) -> None:
        new = commands.NewWork(work_id=work_id, manifestation_id=manifestation_id, title="Again")
        self.run_command(lambda scope: commands.create_work(scope, new, if_absent=True), serializable=True)

    def test_a_recreated_work_refuses_role_bases_from_before_its_delete(self) -> None:
        other = self.add_manifestation(self.work, "MF-" + "3" * 32)
        self.library.insert_initial_roles(self.work, [{"person_id": self.bo, "role_type": "Editor"}])
        self.library.add_role(self.ann, self.work, "Author")
        self.library.add_role(self.ann, self.work, "Reviewer")
        self.library.delete_work_role(self.work, self.ann, "Reviewer")
        translator = roles.Role(self.work, self.bo, "Translator", other)
        gone = roles.Role(self.work, self.ann, "Translator", self.primary)
        self.set_role(translator, True)
        self.set_role(gone, True)
        self.set_role(gone, False)
        before = {(s["person_id"], s["role_type"]): s["revision"] for s in
                  self.run_command(lambda scope: roles.roles_state(scope, self.work))["scopes"]}
        self.assertEqual(before, {(self.bo, "Editor"): 0, (self.ann, "Author"): 1, (self.ann, "Reviewer"): 2})
        self.library.delete_work_record(self.work)
        # Every role scope advanced once: present, constructed (revision 0) and removed alike.
        self.assertEqual(self.revisions("work-person-role"), {
            self.work_scope(self.bo, "Editor"): 1, self.work_scope(self.ann, "Author"): 2,
            self.work_scope(self.ann, "Reviewer"): 3})
        self.assertEqual(self.revisions("manifestation-person-role"),
                         {translator.revision_scope()[1]: 2, gone.revision_scope()[1]: 3})
        self.assertEqual(self.count("roles"), 0)
        self.recreate_work(self.work, self.primary)
        for (person, role_type), base in before.items():
            status, result = self.operation(roles.Role(self.work, person, role_type),
                                            "ADD_WORK_PERSON_ROLE", base, "")
            self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"), role_type)
        status, result = self.operation(gone, "ADD_WORK_PERSON_ROLE", 2, "")
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(self.count("roles"), 0)

    def test_a_recreated_person_refuses_role_bases_from_before_its_delete(self) -> None:
        role = roles.Role(self.work, self.ann, "Author")
        self.library.add_role(self.ann, self.work, "Author")
        self.library.delete_work_role(self.work, self.ann, "Author")
        self.set_role(roles.Role(self.work, self.ann, "Translator", self.primary), True)
        self.set_role(roles.Role(self.work, self.ann, "Translator", self.primary), False)
        self.library.add_role(self.bo, self.work, "Author")
        self.library.delete_person_if_unlinked(self.ann)
        self.assertEqual(self.revisions("work-person-role"),
                         {self.work_scope(self.ann, "Author"): 3, self.work_scope(self.bo, "Author"): 1})
        self.assertEqual(list(self.revisions("manifestation-person-role").values()), [3])
        self.library.add_person("Ann", "Lee", person_id=self.ann)
        status, result = self.operation(role, "ADD_WORK_PERSON_ROLE", 2, "")
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        status, result = self.operation(role, "ADD_WORK_PERSON_ROLE", 3, "")
        self.assertEqual((status, result["code"], result["server_revision"]), (200, "ACKNOWLEDGED", 4))


class ConcurrencyTests(RoleTestCase):
    def test_a_link_committed_while_the_person_is_deleted(self) -> None:
        holder, contender = self.race(
            lambda scope: roles.set_role(scope, roles.Role(self.work, self.ann, "Author"), True),
            lambda: self.library.delete_person_if_unlinked(self.ann))
        self.assertTrue(holder)
        self.assertIsInstance(contender, ValueError)
        self.assertIn("linked works", str(contender))
        self.assertEqual([r["id"] for r in self.library.get_work_roles(self.work)], [self.ann])

    def test_a_link_waiting_for_a_person_delete(self) -> None:
        holder, contender = self.race(lambda scope: persons.delete_person(scope, self.ann),
                                      lambda: self.library.add_role(self.ann, self.work, "Author"))
        self.assertEqual(holder, (True, 0))
        self.assertIsInstance(contender, ValueError)
        self.assertIn("Person not found", str(contender))
        self.assertEqual(self.count("roles"), 0)

    def test_a_rename_and_a_link_queue_on_the_person_row(self) -> None:
        def rename(scope: LibraryScope) -> Any:
            persons.lock(scope, self.ann)
            return persons.set_field(scope, self.ann, "last_name", "Leigh")

        holder, contender = self.race(rename, lambda: self.library.add_role(self.ann, self.work, "Author"))
        self.assertEqual((holder, contender), ((True, 1), None))
        other = self.library.add_work("Other")
        holder, contender = self.race(
            lambda scope: roles.set_role(scope, roles.Role(other, self.ann, "Author"), True),
            lambda: self.library.update_person_profile(self.ann, {"last_name": "Lea"}))
        self.assertEqual((holder, contender), (True, None))
        self.assertEqual(sorted(row["linked_authors"] for row in self.library.get_all_works()),
                         ["Ann Lea", "Ann Lea"])

    def test_two_links_on_one_work_append_in_turn(self) -> None:
        holder, contender = self.race(
            lambda scope: roles.set_role(scope, roles.Role(self.work, self.ann, "Author"), True),
            lambda: self.library.add_role(self.bo, self.work, "Editor"))
        self.assertEqual((holder, contender), (True, None))
        self.assertEqual([(r["id"], r["order_index"]) for r in self.library.get_work_roles(self.work)],
                         [(self.ann, 0), (self.bo, 1)])

    def test_a_link_waiting_for_the_work_delete(self) -> None:
        holder, contender = self.race(lambda scope: commands.delete_work(scope, self.work),
                                      lambda: self.library.add_role(self.ann, self.work, "Author"))
        self.assertIsNotNone(holder)
        self.assertIsInstance(contender, ValueError)
        self.assertIn("Work not found", str(contender))
        self.assertEqual((self.count("roles"), self.revisions("work-person-role")), (0, {}))
