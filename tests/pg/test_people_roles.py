"""Work-Person roles on PostgreSQL: scope, revisions, tombstones and locks (L-P-1 #632, PR 3).

What SQLite has no counterpart for, or answers differently by design
(``backend/domain/people/__init__.py``): Manifestation-scoped roles, Library
isolation, the role decision table run against the domain commands, the
tombstones a Work or Person delete leaves for a recreated id, and the §8.3
Person row locks. :class:`AnswerTests` spells out what the role commands and
reads answer.
"""

from __future__ import annotations

from typing import Any, Optional

from backend.domain.people.errors import MissingPersonError
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.people import persons, roles
from backend.domain.works import commands
from tests import acting_context as facade_context
from tests.pg.test_people_facade import PeopleFacadeTestCase
from tests.narrowing import require
from tests.pg.answers import AnswerAssertions
from backend.domain.identity.library_scope import library_query
from backend.domain.people import person_commands
from backend.domain.people import role_commands
from tests import people_fixtures
from backend.domain.works import work_commands
from tests import works_fixtures
from backend.web import etags

_MISSING_PERSON = "P-" + "0" * 32


class RoleTestCase(PeopleFacadeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ann = people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee")
        self.bo = people_fixtures.add_person(self.db, self.ctx, "Bo", "Ng")
        self.work = works_fixtures.add_work(self.db, self.ctx, self.storage, "Essay")
        self.primary = require(work_commands.detail(self.db, self.ctx, self.pdfs_dir, self.work))["primary_manifestation_id"]

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
        role_commands.add(self.db, self.ctx, self.ann, self.work, "Author")
        self.assertTrue(self.set_role(roles.Role(self.work, self.bo, "Translator", self.primary), True, "B. N."))
        self.assertTrue(self.set_role(roles.Role(self.work, self.ann, "Translator", other), True))
        self.assertEqual([(r["id"], r["role_type"], r["manifestation_id"])
                          for r in library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, self.work))],
                         [(self.ann, "Author", None), (self.bo, "Translator", self.primary)])
        self.assertEqual([p["display_name"] for p in work_commands.catalog(self.db, self.ctx, self.pdfs_dir)[0]["linked_people"]],
                         ["Ann Lee", "B. N."])
        # The Translator role on the other Version is not one of the primary's credits.
        self.assertEqual([w["role_type"] for w in require(person_commands.detail(self.db, self.ctx, self.ann, self.pdfs_dir))["works"]], ["Author"])
        self.assertTrue(library_query(self.db, self.ctx, lambda scope: roles.has_role(scope, self.work, self.ann, "Translator")))
        self.assertEqual(self.revisions("manifestation-person-role"), {
            roles.Role(self.work, self.bo, "Translator", self.primary).revision_scope()[1]: 1,
            roles.Role(self.work, self.ann, "Translator", other).revision_scope()[1]: 1,
        })
        self.assertEqual(role_commands.next_order_index(self.db, self.ctx, self.work), 3)

    def test_one_person_may_translate_two_versions(self) -> None:
        other = self.add_manifestation(self.work, "MF-" + "2" * 32)
        for manifestation in (self.primary, other):
            self.assertTrue(self.set_role(roles.Role(self.work, self.ann, "Translator", manifestation), True))
        self.assertEqual(self.count("roles"), 2)

    def test_a_manifestation_of_another_work_is_refused(self) -> None:
        elsewhere = require(work_commands.detail(self.db, self.ctx, self.pdfs_dir, works_fixtures.add_work(self.db, self.ctx, self.storage, "Other")))["primary_manifestation_id"]
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
            self.assertEqual(library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, self.work)), [])
            for call in (lambda: role_commands.add(self.db, self.ctx, self.ann, self.work, "Author"),
                         lambda: role_commands.remove(self.db, self.ctx, self.work, self.ann, "Author"),
                         lambda: role_commands.insert_initial(self.db, self.ctx, self.work, [{"person_id": self.ann, "role_type": "Author"}])):
                with self.assertRaises(AccessRefused) as caught:
                    call()
                self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual(self.count("roles"), 0)

    def test_another_librarys_works_and_people_are_out_of_reach(self) -> None:
        role_commands.add(self.db, self.ctx, self.ann, self.work, "Author", "A. Lee")
        with facade_context.acting_as(self.other_actor().context):
            their_work = works_fixtures.add_work(self.db, self.ctx, self.storage, "Theirs")
            their_person = people_fixtures.add_person(self.db, self.ctx, "Cy", "Oh")
            self.assertEqual(library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, self.work)), [])
            self.assertFalse(library_query(self.db, self.ctx, lambda scope: roles.has_role(scope, self.work, self.ann, "Author")))
            self.assertIsNone(person_commands.detail(self.db, self.ctx, self.ann, self.pdfs_dir))
            self.assertEqual(role_commands.missing_person_ids(self.db, self.ctx, [self.ann]), [self.ann])
            self.assertEqual(role_commands.next_order_index(self.db, self.ctx, self.work), 0)
            with self.assertRaisesRegex(ValueError, "Work not found"):
                role_commands.add(self.db, self.ctx, their_person, self.work, "Editor")
            with self.assertRaisesRegex(ValueError, "Person not found"):
                role_commands.add(self.db, self.ctx, self.ann, their_work, "Editor")
            with self.assertRaisesRegex(ValueError, "Work not found"):
                role_commands.insert_initial(self.db, self.ctx, self.work, [{"person_id": their_person, "role_type": "Editor"}])
            with self.assertRaises(MissingPersonError):
                role_commands.insert_initial(self.db, self.ctx, their_work, [{"person_id": self.ann, "role_type": "Editor"}])
            self.assertFalse(role_commands.update_credit(self.db, self.ctx, self.work, self.ann, "Author", "Taken"))
            self.assertFalse(role_commands.remove(self.db, self.ctx, self.work, self.ann, "Author"))
            self.assertEqual(library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, their_work)), [])
        self.assertEqual([(r["id"], r["credit_name"]) for r in library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, self.work))],
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
        self.assertFalse(role_commands.remove(self.db, self.ctx, self.work, self.bo, "Editor"))
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
        role_commands.insert_initial(self.db, self.ctx, self.work, [{"person_id": self.bo, "role_type": "Editor"}])
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

    def test_constructed_roles_at_a_taken_index_keep_insertion_order(self) -> None:
        for person, role_type, index in ((self.ann, "Author", 0), (self.bo, "Editor", 1),
                                         (self.bo, "Author", 0)):
            people_fixtures.insert_initial_role(self.db, self.ctx, self.work, person, role_type, order_index=index)
        self.assertEqual([(r["id"], r["role_type"], r["order_index"])
                          for r in library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, self.work))],
                         [(self.ann, "Author", 0), (self.bo, "Author", 0), (self.bo, "Editor", 1)])


class CatalogEtagTests(RoleTestCase):
    def etag(self) -> str:
        return etags.etag_for_representation("works", work_commands.catalog(self.db, self.ctx, self.pdfs_dir))

    def test_adding_and_removing_a_credit_change_the_catalog_etag(self) -> None:
        before = self.etag()
        role_commands.add(self.db, self.ctx, self.ann, self.work, "Author")
        linked = self.etag()
        self.assertNotEqual(before, linked)
        role_commands.add(self.db, self.ctx, self.bo, self.work, "Editor")
        both = self.etag()
        self.assertNotEqual(linked, both)
        self.assertTrue(role_commands.remove(self.db, self.ctx, self.work, self.bo, "Editor"))
        self.assertNotEqual(both, self.etag())


class TombstoneTests(RoleTestCase):
    def recreate_work(self, work_id: str, manifestation_id: str) -> None:
        new = commands.NewWork(work_id=work_id, manifestation_id=manifestation_id, title="Again")
        self.run_command(lambda scope: commands.create_work(scope, new, if_absent=True), serializable=True)

    def test_a_recreated_work_refuses_role_bases_from_before_its_delete(self) -> None:
        other = self.add_manifestation(self.work, "MF-" + "3" * 32)
        role_commands.insert_initial(self.db, self.ctx, self.work, [{"person_id": self.bo, "role_type": "Editor"}])
        role_commands.add(self.db, self.ctx, self.ann, self.work, "Author")
        role_commands.add(self.db, self.ctx, self.ann, self.work, "Reviewer")
        role_commands.remove(self.db, self.ctx, self.work, self.ann, "Reviewer")
        translator = roles.Role(self.work, self.bo, "Translator", other)
        gone = roles.Role(self.work, self.ann, "Translator", self.primary)
        self.set_role(translator, True)
        self.set_role(gone, True)
        self.set_role(gone, False)
        before = {(s["person_id"], s["role_type"]): s["revision"] for s in
                  self.run_command(lambda scope: roles.roles_state(scope, self.work))["scopes"]}
        self.assertEqual(before, {(self.bo, "Editor"): 0, (self.ann, "Author"): 1, (self.ann, "Reviewer"): 2})
        work_commands.delete(self.db, self.ctx, self.work)
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
        role_commands.add(self.db, self.ctx, self.ann, self.work, "Author")
        role_commands.remove(self.db, self.ctx, self.work, self.ann, "Author")
        self.set_role(roles.Role(self.work, self.ann, "Translator", self.primary), True)
        self.set_role(roles.Role(self.work, self.ann, "Translator", self.primary), False)
        role_commands.add(self.db, self.ctx, self.bo, self.work, "Author")
        person_commands.delete_if_unlinked(self.db, self.ctx, self.ann)
        self.assertEqual(self.revisions("work-person-role"),
                         {self.work_scope(self.ann, "Author"): 3, self.work_scope(self.bo, "Author"): 1})
        self.assertEqual(list(self.revisions("manifestation-person-role").values()), [3])
        people_fixtures.add_person(self.db, self.ctx, "Ann", "Lee", person_id=self.ann)
        status, result = self.operation(role, "ADD_WORK_PERSON_ROLE", 2, "")
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        status, result = self.operation(role, "ADD_WORK_PERSON_ROLE", 3, "")
        self.assertEqual((status, result["code"], result["server_revision"]), (200, "ACKNOWLEDGED", 4))


class ConcurrencyTests(RoleTestCase):
    def test_a_link_committed_while_the_person_is_deleted(self) -> None:
        holder, contender = self.race(
            lambda scope: roles.set_role(scope, roles.Role(self.work, self.ann, "Author"), True),
            lambda: person_commands.delete_if_unlinked(self.db, self.ctx, self.ann))
        self.assertTrue(holder)
        self.assertIsInstance(contender, ValueError)
        self.assertIn("linked works", str(contender))
        self.assertEqual([r["id"] for r in library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, self.work))], [self.ann])

    def test_a_link_waiting_for_a_person_delete(self) -> None:
        holder, contender = self.race(lambda scope: persons.delete_person(scope, self.ann),
                                      lambda: role_commands.add(self.db, self.ctx, self.ann, self.work, "Author"))
        self.assertEqual(holder, (True, 0))
        self.assertIsInstance(contender, ValueError)
        self.assertIn("Person not found", str(contender))
        self.assertEqual(self.count("roles"), 0)

    def test_a_rename_and_a_link_queue_on_the_person_row(self) -> None:
        def rename(scope: LibraryScope) -> Any:
            persons.lock(scope, self.ann)
            return persons.set_field(scope, self.ann, "last_name", "Leigh")

        holder, contender = self.race(rename, lambda: role_commands.add(self.db, self.ctx, self.ann, self.work, "Author"))
        self.assertEqual((holder, contender), ((True, 1), None))
        other = works_fixtures.add_work(self.db, self.ctx, self.storage, "Other")
        holder, contender = self.race(
            lambda scope: roles.set_role(scope, roles.Role(other, self.ann, "Author"), True),
            lambda: person_commands.update_profile(self.db, self.ctx, self.ann, {"last_name": "Lea"}))
        self.assertEqual((holder, contender), (True, None))
        self.assertEqual(sorted(row["linked_authors"] for row in work_commands.catalog(self.db, self.ctx, self.pdfs_dir)),
                         ["Ann Lea", "Ann Lea"])

    def test_two_links_on_one_work_append_in_turn(self) -> None:
        holder, contender = self.race(
            lambda scope: roles.set_role(scope, roles.Role(self.work, self.ann, "Author"), True),
            lambda: role_commands.add(self.db, self.ctx, self.bo, self.work, "Editor"))
        self.assertEqual((holder, contender), (True, None))
        self.assertEqual([(r["id"], r["order_index"]) for r in library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, self.work))],
                         [(self.ann, 0), (self.bo, 1)])

    def test_a_link_waiting_for_the_work_delete(self) -> None:
        holder, contender = self.race(lambda scope: commands.delete_work(scope, self.work),
                                      lambda: role_commands.add(self.db, self.ctx, self.ann, self.work, "Author"))
        self.assertIsNotNone(holder)
        self.assertIsInstance(contender, ValueError)
        self.assertIn("Work not found", str(contender))
        self.assertEqual((self.count("roles"), self.revisions("work-person-role")), (0, {}))


_MISSING_WORK = "W-" + "0" * 32
_NOT_A_ROLE = ("'Producer' is not a Work role; use one of Author, Editor, Reviewer, Mentioned, Translator,"
               " Introduction, Foreword, Afterword")
_PERSON = {"about": "", "aliases": "", "birth_date": "", "created_at": "<s>", "death_date": "", "image_url": "",
           "link_iep": "", "link_stanford_encyclopedia": "", "link_wikipedia": "", "links_other": "",
           "updated_at": "<s>"}


class AnswerTests(AnswerAssertions, RoleTestCase):
    """The JSON the Work-Person role commands and reads answer, spelled out."""

    def setUp(self) -> None:
        super().setUp()
        self.labels.name(self.ann, "<P Lee>")
        self.labels.name(self.bo, "<P Ng>")
        self.labels.name(self.work, "<W Essay>")

    def roles(self, work_id: Optional[str] = None) -> list[tuple[str, int, Optional[str]]]:
        return [(row["role_type"], row["order_index"], row["credit_name"])
                for row in library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, work_id or self.work))]

    def test_links_read_back_on_the_work_and_its_summaries(self) -> None:
        role_commands.add(self.db, self.ctx, self.ann, self.work, "Author", "  A. Lee ")
        role_commands.add(self.db, self.ctx, self.bo, self.work, "Editor")
        role_commands.add(self.db, self.ctx, self.bo, self.work, "Author")
        stored = library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, self.work))
        self.answer(stored, [
            {**_PERSON, "aliases": "A. Lee", "credit_name": "A. Lee", "first_name": "Ann", "id": "<P Lee>",
             "last_name": "Lee", "manifestation_id": None, "order_index": 0, "role_type": "Author"},
            {**_PERSON, "credit_name": None, "first_name": "Bo", "id": "<P Ng>", "last_name": "Ng",
             "manifestation_id": None, "order_index": 1, "role_type": "Editor"},
            {"credit_name": None, "id": "<P Ng>", "order_index": 2, "role_type": "Author"}])
        self.assertEqual(require(work_commands.detail(self.db, self.ctx, self.pdfs_dir, self.work))["roles"], stored)
        linked = [
            {"canonical_name": "Ann Lee", "credit_name": "A. Lee", "display_name": "A. Lee", "order_index": 0,
             "person_id": "<P Lee>", "role_type": "Author"},
            {"canonical_name": "Bo Ng", "credit_name": "", "display_name": "Bo Ng", "order_index": 1,
             "person_id": "<P Ng>", "role_type": "Editor"},
            {"canonical_name": "Bo Ng", "credit_name": "", "display_name": "Bo Ng", "order_index": 2,
             "person_id": "<P Ng>", "role_type": "Author"}]
        summary = {"linked_authors": "A. Lee, Bo Ng", "linked_people": linked, "primary_author": "A. Lee",
                   "primary_editor": "Bo Ng"}
        for rows in (work_commands.catalog(self.db, self.ctx, self.pdfs_dir), work_commands.browse_catalog(self.db, self.ctx, self.pdfs_dir),
                     works_fixtures.recently_added_works(self.db, self.ctx, self.storage),
                     works_fixtures.summaries_by_ids(self.db, self.ctx, self.storage, [self.work])):
            self.answer(rows, [summary])
        self.assertTrue(library_query(self.db, self.ctx, lambda scope: roles.has_role(scope, self.work, self.bo, "Editor")))
        self.assertFalse(library_query(self.db, self.ctx, lambda scope: roles.has_role(scope, self.work, self.ann, "Editor")))
        self.assertEqual(role_commands.next_order_index(self.db, self.ctx, self.work), 3)
        works = require(person_commands.detail(self.db, self.ctx, self.bo, self.pdfs_dir))["works"]
        self.answer(works, [{"credit_name": None, "id": "<W Essay>", "order_index": 1, "role_type": "Editor", **summary},
                            {"credit_name": None, "id": "<W Essay>", "order_index": 2, "role_type": "Author"}])

    def test_a_work_without_roles(self) -> None:
        self.assertEqual(library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, self.work)), [])
        self.assertEqual(role_commands.next_order_index(self.db, self.ctx, self.work), 0)
        self.assertEqual(library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, _MISSING_WORK)), [])
        self.answer(work_commands.catalog(self.db, self.ctx, self.pdfs_dir), [{"linked_authors": None, "linked_people": [],
                                                    "primary_author": None, "primary_editor": None}])

    def test_refusals(self) -> None:
        role_commands.add(self.db, self.ctx, self.ann, self.work, "Author")
        for call, message in (
                (lambda: role_commands.add(self.db, self.ctx, self.ann, self.work, "Author"),
                 "This person is already linked to this file as Author."),
                (lambda: role_commands.add(self.db, self.ctx, self.ann, self.work, "Producer"), _NOT_A_ROLE),
                (lambda: role_commands.add(self.db, self.ctx, self.bo, self.work, "Editor", "x" * 501),
                 "credit_name exceeds 500 bytes"),
                (lambda: role_commands.remove(self.db, self.ctx, self.work, self.ann, "Producer"), _NOT_A_ROLE)):
            self.assert_refused(call, message)
        self.assertEqual(self.roles(), [("Author", 0, None)])

    def test_credit_edits_and_removal(self) -> None:
        role_commands.add(self.db, self.ctx, self.ann, self.work, "Author")
        self.assertTrue(role_commands.update_credit(self.db, self.ctx, self.work, self.ann, "Author", "Ann L."))
        self.assertFalse(role_commands.update_credit(self.db, self.ctx, self.work, self.bo, "Author", "x"))
        self.assertFalse(role_commands.update_credit(self.db, self.ctx, self.work, self.ann, "Producer", "x"))
        # A credit that is not the name becomes an alias of the Person.
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, self.ann, self.pdfs_dir))["aliases"], "Ann L.")
        state = require(person_commands.metadata_state(self.db, self.ctx, self.ann))
        self.assertEqual(state["fields"]["aliases"]["revision"], 1)
        # Clearing the override keeps the alias; the link shows the Person's name again.
        self.assertTrue(role_commands.update_credit(self.db, self.ctx, self.work, self.ann, "Author", ""))
        self.assertEqual(work_commands.catalog(self.db, self.ctx, self.pdfs_dir)[0]["linked_authors"], "Ann Lee")
        self.assertTrue(role_commands.remove(self.db, self.ctx, self.work, self.ann, "Author"))
        self.assertFalse(role_commands.remove(self.db, self.ctx, self.work, self.ann, "Author"))
        self.assertFalse(role_commands.remove(self.db, self.ctx, _MISSING_WORK, self.ann, "Author"))
        self.assertEqual(library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, self.work)), [])

    def test_credits_that_are_the_name_or_hold_a_comma_are_not_promoted(self) -> None:
        for role, credit in (("Author", "Ann Lee"), ("Editor", "Lee, Ann"), ("Reviewer", "A. Lee"),
                             ("Translator", "A. Lee")):
            role_commands.add(self.db, self.ctx, self.ann, self.work, role, credit)
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, self.ann, self.pdfs_dir))["aliases"], "A. Lee")
        self.assertEqual(require(person_commands.metadata_state(self.db, self.ctx, self.ann))["fields"]["aliases"],
                         {"revision": 1})

    def test_construction_keeps_the_given_order_and_skips_what_it_cannot_insert(self) -> None:
        wanted: list[Any] = [
            {"person_id": self.bo, "role_type": "Editor"},
            {"person_id": self.ann, "role_type": "Author", "credit_name": "A. Lee"},
            {"person_id": None, "role_type": "Author"},
            {"person_id": self.bo, "role_type": "Producer"},
            {"person_id": self.bo, "role_type": "Author", "credit_name": "x" * 501},
            {"person_id": self.ann, "role_type": "Author"},
            "not a role",
            {"person_id": self.bo, "role_type": "Mentioned", "credit_name": 7},
        ]
        self.assertEqual(role_commands.missing_initial_person_ids(self.db, self.ctx, wanted), [])
        self.assertEqual(role_commands.insert_initial(self.db, self.ctx, self.work, wanted), 3)
        self.assertEqual(self.roles(), [("Editor", 0, None), ("Author", 1, "A. Lee"), ("Mentioned", 7, None)])
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, self.ann, self.pdfs_dir))["aliases"], "A. Lee")
        self.assertEqual(role_commands.insert_initial(self.db, self.ctx, self.work, []), 0)

    def test_construction_with_a_missing_person_writes_nothing(self) -> None:
        wanted = [{"person_id": self.ann, "role_type": "Author", "credit_name": "A. Lee"},
                  {"person_id": _MISSING_PERSON, "role_type": "Editor"}]
        self.assertEqual(role_commands.missing_initial_person_ids(self.db, self.ctx, wanted), [_MISSING_PERSON])
        self.assertEqual(role_commands.missing_person_ids(self.db, self.ctx, [self.bo, _MISSING_PERSON, " "]), [_MISSING_PERSON])
        with self.assertRaisesRegex(ValueError, "no longer exists"):
            role_commands.insert_initial(self.db, self.ctx, self.work, wanted)
        self.assertEqual(library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, self.work)), [])
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, self.ann, self.pdfs_dir))["aliases"], "")

    def test_one_constructed_role(self) -> None:
        people_fixtures.insert_initial_role(self.db, self.ctx, self.work, self.ann, "Author", order_index=2, credit_name="A. Lee")
        people_fixtures.insert_initial_role(self.db, self.ctx, self.work, self.bo, "Editor")
        self.assert_refused(lambda: people_fixtures.insert_initial_role(self.db, self.ctx, self.work, self.bo, "Producer"), _NOT_A_ROLE)
        with self.assertRaisesRegex(ValueError, "no longer exists"):
            people_fixtures.insert_initial_role(self.db, self.ctx, self.work, _MISSING_PERSON, "Author")
        self.assertEqual([(role, index) for role, index, _ in self.roles()], [("Editor", 0), ("Author", 2)])
        self.assertEqual(role_commands.next_order_index(self.db, self.ctx, self.work), 3)

    def test_constructed_ties_keep_insertion_order(self) -> None:
        cy = people_fixtures.add_person(self.db, self.ctx, "Cy", "Oh")
        notes = works_fixtures.add_work(self.db, self.ctx, self.storage, "Notes")
        for work, indexes in ((self.work, (0, 0, 0)), (notes, (0, 1, 0))):
            for person, index in zip((self.ann, self.bo, cy), indexes):
                people_fixtures.insert_initial_role(self.db, self.ctx, work, person, "Author", order_index=index)
        firsts = lambda work_id: [(row["first_name"], row["order_index"]) for row in library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, work_id))]
        self.assertEqual(firsts(self.work), [("Ann", 0), ("Bo", 0), ("Cy", 0)])
        self.assertEqual(firsts(notes), [("Ann", 0), ("Cy", 0), ("Bo", 1)])
        rows = sorted(work_commands.catalog(self.db, self.ctx, self.pdfs_dir), key=lambda row: row["title"])
        self.assertEqual([row["linked_authors"] for row in rows], ["Ann Lee, Bo Ng, Cy Oh", "Ann Lee, Cy Oh, Bo Ng"])

    def test_a_constructed_duplicate_is_refused(self) -> None:
        people_fixtures.insert_initial_role(self.db, self.ctx, self.work, self.ann, "Author")
        with self.assertRaises(ValueError):
            people_fixtures.insert_initial_role(self.db, self.ctx, self.work, self.ann, "Author", order_index=3)
        self.assertEqual(self.roles(), [("Author", 0, None)])

    def test_a_persons_works(self) -> None:
        notes = self.labels.name(works_fixtures.add_work(self.db, self.ctx, self.storage, "Notes", year="1951", publisher="Verso"), "<W Notes>")
        role_commands.add(self.db, self.ctx, self.ann, notes, "Editor")
        role_commands.add(self.db, self.ctx, self.bo, self.work, "Author")
        role_commands.add(self.db, self.ctx, self.ann, self.work, "Author", "A. Lee")
        self.answer(require(person_commands.detail(self.db, self.ctx, self.ann, self.pdfs_dir))["works"], [
            {"credit_name": None, "id": "<W Notes>", "linked_authors": None, "order_index": 0,
             "primary_editor": "Ann Lee", "publisher": "Verso", "role_type": "Editor", "title": "Notes",
             "year": "1951"},
            {"credit_name": "A. Lee", "id": "<W Essay>", "linked_authors": "Bo Ng, A. Lee", "order_index": 1,
             "primary_author": "Bo Ng", "role_type": "Author", "title": "Essay"}])

    def test_a_linked_person_is_kept_until_unlinked(self) -> None:
        role_commands.add(self.db, self.ctx, self.ann, self.work, "Author")
        with self.assertRaisesRegex(ValueError, "linked works"):
            person_commands.delete_if_unlinked(self.db, self.ctx, self.ann)
        role_commands.remove(self.db, self.ctx, self.work, self.ann, "Author")
        person_commands.delete_if_unlinked(self.db, self.ctx, self.ann)
        self.assertIsNone(person_commands.detail(self.db, self.ctx, self.ann, self.pdfs_dir))

    def test_a_deleted_work_takes_its_roles(self) -> None:
        role_commands.add(self.db, self.ctx, self.ann, self.work, "Author")
        people_fixtures.insert_initial_role(self.db, self.ctx, self.work, self.bo, "Editor", order_index=5)
        self.assertIsNotNone(work_commands.delete(self.db, self.ctx, self.work))
        self.assertEqual(require(person_commands.detail(self.db, self.ctx, self.ann, self.pdfs_dir))["works"], [])
        self.assertEqual(library_query(self.db, self.ctx, lambda scope: roles.work_roles(scope, self.work)), [])
        self.assertIsNone(person_commands.delete_if_unlinked(self.db, self.ctx, self.bo))

    def test_a_rename_reaches_every_linked_work(self) -> None:
        notes = works_fixtures.add_work(self.db, self.ctx, self.storage, "Notes")
        for work_id in (self.work, notes):
            role_commands.add(self.db, self.ctx, self.ann, work_id, "Author")
        person_commands.update_profile(self.db, self.ctx, self.ann, {"last_name": "Leigh"})
        self.assertEqual([row["linked_authors"] for row in work_commands.catalog(self.db, self.ctx, self.pdfs_dir)], ["Ann Leigh", "Ann Leigh"])
