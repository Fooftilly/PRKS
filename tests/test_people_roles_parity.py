"""Work-Person roles: the PostgreSQL facade returns SQLite's JSON (L-P-1 #632, PR 3).

Each test runs the same calls on the library object of its leg and on a
reference ``PRKSDatabase`` built in the same test, and compares the results
after replacing generated ids, timestamps and ETag digests
(``tests/people_parity_helpers.py``). On the ``postgres`` leg
(``scripts/test-postgres --parity``) the left side is
:class:`backend.pg_facade.PostgresLibrary`. Inputs avoid the documented
differences (``backend/pg_facade/people.py``), and a Person's roles on
several Works are created in separate commands.
"""

from __future__ import annotations

import os
import sys
import sqlite3
from typing import Any

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from tests.backend_parity import parametrize_backends
from tests.people_parity_helpers import PeopleParityTestCase

_MISSING_PERSON = "P-" + "0" * 32
_MISSING_WORK = "W-" + "0" * 32


@parametrize_backends
class RoleParityTests(PeopleParityTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ann = self.person_both("Lee", first_name="Ann")
        self.bo = self.person_both("Ng", first_name="Bo")
        self.essay = self.work_both("essay", title="Essay")

    def work_both(self, label: str, **fields: Any) -> tuple[str, str]:
        return self.leg.work(label, **fields), self.ref.work(label, **fields)

    def same_each(self, call: str, *pairs: tuple[Any, Any], **kwargs: Any) -> Any:
        """``call(pairs[0][side], pairs[1][side], …)`` on each side; asserts equal results."""
        mine = getattr(self.leg.db, call)(*(pair[0] for pair in pairs), **kwargs)
        theirs = getattr(self.ref.db, call)(*(pair[1] for pair in pairs), **kwargs)
        self.assertEqual(self.leg.normal(mine), self.ref.normal(theirs), call)
        return mine

    def refused_each(self, call: str, *pairs: tuple[Any, Any], **kwargs: Any) -> str:
        messages = []
        for index, side in enumerate(self.sides()):
            with self.assertRaises(ValueError) as caught:
                getattr(side.db, call)(*(pair[index] for pair in pairs), **kwargs)
            messages.append(str(caught.exception))
        self.assertEqual(messages[0], messages[1], call)
        return messages[0]

    def link(self, person: tuple[str, str], work: tuple[str, str], role: str, credit: str = "") -> None:
        self.same_each("add_role", person, work, (role, role), credit_name=credit)

    def test_links_read_back_on_the_work_and_its_summaries(self) -> None:
        self.link(self.ann, self.essay, "Author", "  A. Lee ")
        self.link(self.bo, self.essay, "Editor")
        self.link(self.bo, self.essay, "Author")
        roles = self.same_each("get_work_roles", self.essay)
        self.assertEqual([(r["role_type"], r["order_index"], r["credit_name"]) for r in roles],
                         [("Author", 0, "A. Lee"), ("Editor", 1, None), ("Author", 2, None)])
        self.assertEqual(self.same_each("get_work", self.essay)["roles"], roles)
        summary = self.same("get_all_works")[0]
        self.assertEqual((summary["primary_author"], summary["primary_editor"], summary["linked_authors"]),
                         ("A. Lee", "Bo Ng", "A. Lee, Bo Ng"))
        self.same("get_works_browse_catalog")
        self.same("get_recently_added_works")
        self.same_each("get_work_summaries_by_ids_ordered", ([self.essay[0]], [self.essay[1]]))
        self.assertTrue(self.same_each("has_work_role", self.bo, self.essay, ("Editor", "Editor")))
        self.assertFalse(self.same_each("has_work_role", self.ann, self.essay, ("Editor", "Editor")))
        self.assertEqual(self.same_each("next_role_order_index", self.essay), 3)
        self.same_each("get_person", self.bo)

    def test_a_work_without_roles(self) -> None:
        self.assertEqual(self.same_each("get_work_roles", self.essay), [])
        self.assertEqual(self.same_each("next_role_order_index", self.essay), 0)
        self.assertEqual(self.both("get_work_roles", _MISSING_WORK), ([], []))
        summary = self.same("get_all_works")[0]
        self.assertEqual((summary["primary_author"], summary["linked_people"]), (None, []))

    def test_refusals(self) -> None:
        self.link(self.ann, self.essay, "Author")
        self.assertIn("already linked", self.refused_each(
            "add_role", self.ann, self.essay, ("Author", "Author")))
        self.assertIn("not a Work role", self.refused_each(
            "add_role", self.ann, self.essay, ("Producer", "Producer")))
        self.assertIn("exceeds", self.refused_each(
            "add_role", self.bo, self.essay, ("Editor", "Editor"), credit_name="x" * 501))
        self.assertIn("not a Work role", self.refused_each(
            "delete_work_role", self.essay, self.ann, ("Producer", "Producer")))
        self.assertEqual(len(self.same_each("get_work_roles", self.essay)), 1)

    def test_credit_edits_and_removal(self) -> None:
        self.link(self.ann, self.essay, "Author")
        self.assertTrue(self.same_each("update_role_credit_name", self.essay, self.ann, ("Author", "Author"),
                                       (7, 7), ("Ann L.", "Ann L.")))
        self.assertFalse(self.same_each("update_role_credit_name", self.essay, self.bo, ("Author", "Author"),
                                        (0, 0), ("x", "x")))
        self.assertFalse(self.same_each("update_role_credit_name", self.essay, self.ann,
                                        ("Producer", "Producer"), (0, 0), ("x", "x")))
        person = self.same_each("get_person", self.ann)
        self.assertEqual(person["aliases"], "Ann L.")
        self.assertEqual(self.same_each("get_person_metadata_state", self.ann)["fields"]["aliases"]["revision"], 1)
        # Clearing the override keeps the alias; the link shows the Person's name again.
        self.assertTrue(self.same_each("update_role_credit_name", self.essay, self.ann, ("Author", "Author"),
                                       (0, 0), ("", "")))
        self.assertEqual(self.same("get_all_works")[0]["linked_authors"], "Ann Lee")
        self.assertTrue(self.same_each("delete_work_role", self.essay, self.ann, ("Author", "Author")))
        self.assertFalse(self.same_each("delete_work_role", self.essay, self.ann, ("Author", "Author")))
        self.assertFalse(self.same_each("delete_work_role", (_MISSING_WORK, _MISSING_WORK), self.ann,
                                        ("Author", "Author")))
        self.assertEqual(self.same_each("get_work_roles", self.essay), [])

    def test_credits_that_are_the_name_or_hold_a_comma_are_not_promoted(self) -> None:
        self.link(self.ann, self.essay, "Author", "Ann Lee")
        self.link(self.ann, self.essay, "Editor", "Lee, Ann")
        self.link(self.ann, self.essay, "Reviewer", "A. Lee")
        self.link(self.ann, self.essay, "Translator", "A. Lee")
        self.assertEqual(self.same_each("get_person", self.ann)["aliases"], "A. Lee")
        self.same_each("get_person_metadata_state", self.ann)

    def test_construction_keeps_the_given_order_and_skips_what_it_cannot_insert(self) -> None:
        wanted = [
            {"person_id": self.bo, "role_type": "Editor"},
            {"person_id": self.ann, "role_type": "Author", "credit_name": "A. Lee"},
            {"person_id": None, "role_type": "Author"},
            {"person_id": self.bo, "role_type": "Producer"},
            {"person_id": self.bo, "role_type": "Author", "credit_name": "x" * 501},
            {"person_id": self.ann, "role_type": "Author"},
            "not a role",
            {"person_id": self.bo, "role_type": "Mentioned", "credit_name": 7},
        ]

        def sided(index: int) -> list[Any]:
            out: list[Any] = []
            for item in wanted:
                if isinstance(item, dict) and isinstance(item["person_id"], tuple):
                    item = {**item, "person_id": item["person_id"][index]}
                out.append(item)
            return out

        self.assertEqual(self.same_each("missing_initial_role_person_ids", (sided(0), sided(1))), [])
        self.assertEqual(self.same_each("insert_initial_roles", self.essay, (sided(0), sided(1))), 3)
        roles = self.same_each("get_work_roles", self.essay)
        self.assertEqual([(r["role_type"], r["order_index"], r["credit_name"]) for r in roles],
                         [("Editor", 0, None), ("Author", 1, "A. Lee"), ("Mentioned", 7, None)])
        self.assertEqual(self.same_each("get_person", self.ann)["aliases"], "A. Lee")
        self.same_each("get_person_metadata_state", self.ann)
        self.assertEqual(self.same_each("insert_initial_roles", self.essay, ([], [])), 0)

    def test_construction_with_a_missing_person_writes_nothing(self) -> None:
        wanted = ([{"person_id": self.ann[0], "role_type": "Author", "credit_name": "A. Lee"},
                   {"person_id": _MISSING_PERSON, "role_type": "Editor"}],
                  [{"person_id": self.ann[1], "role_type": "Author", "credit_name": "A. Lee"},
                   {"person_id": _MISSING_PERSON, "role_type": "Editor"}])
        self.assertEqual(self.same_each("missing_initial_role_person_ids", wanted), [_MISSING_PERSON])
        self.assertEqual(self.same_each("missing_person_ids", ([self.bo[0], _MISSING_PERSON, " "],
                                                               [self.bo[1], _MISSING_PERSON, " "])),
                         [_MISSING_PERSON])
        self.assertIn("no longer exists", self.refused_each("insert_initial_roles", self.essay, wanted))
        self.assertEqual(self.same_each("get_work_roles", self.essay), [])
        self.assertEqual(self.same_each("get_person", self.ann)["aliases"], "")

    def test_one_constructed_role(self) -> None:
        self.same_each("insert_initial_role", self.essay, self.ann, ("Author", "Author"), (2, 2),
                       ("A. Lee", "A. Lee"))
        self.same_each("insert_initial_role", self.essay, self.bo, ("Editor", "Editor"))
        self.assertIn("not a Work role", self.refused_each(
            "insert_initial_role", self.essay, self.bo, ("Producer", "Producer")))
        self.assertIn("no longer exists", self.refused_each(
            "insert_initial_role", self.essay, (_MISSING_PERSON, _MISSING_PERSON), ("Author", "Author")))
        roles = self.same_each("get_work_roles", self.essay)
        self.assertEqual([(r["role_type"], r["order_index"]) for r in roles], [("Editor", 0), ("Author", 2)])
        self.assertEqual(self.same_each("next_role_order_index", self.essay), 3)

    def test_constructed_ties_keep_insertion_order(self) -> None:
        cy = self.person_both("Oh", first_name="Cy")
        notes = self.work_both("notes", title="Notes")
        for work, indexes in ((self.essay, (0, 0, 0)), (notes, (0, 1, 0))):
            for person, index in zip((self.ann, self.bo, cy), indexes):
                self.same_each("insert_initial_role", work, person, ("Author", "Author"), (index, index))
        self.assertEqual([(r["first_name"], r["order_index"]) for r in self.same_each("get_work_roles", self.essay)],
                         [("Ann", 0), ("Bo", 0), ("Cy", 0)])
        self.assertEqual([(r["first_name"], r["order_index"]) for r in self.same_each("get_work_roles", notes)],
                         [("Ann", 0), ("Cy", 0), ("Bo", 1)])
        mine, theirs = (sorted(rows, key=lambda row: row["title"]) for rows in self.both("get_all_works"))
        self.assertEqual(self.leg.normal(mine), self.ref.normal(theirs))
        self.assertEqual([row["linked_authors"] for row in mine], ["Ann Lee, Bo Ng, Cy Oh", "Ann Lee, Cy Oh, Bo Ng"])
        self.same_each("get_work", self.essay)
        self.same_each("get_work", notes)
        for person in (self.ann, self.bo, cy):
            self.same_each("get_person", person)

    def test_a_constructed_duplicate_is_refused(self) -> None:
        self.same_each("insert_initial_role", self.essay, self.ann, ("Author", "Author"))
        for side, work, person in zip(self.sides(), self.essay, self.ann):
            with self.assertRaises((ValueError, sqlite3.IntegrityError)):
                side.db.insert_initial_role(work, person, "Author", 3)
        self.assertEqual([(r["role_type"], r["order_index"]) for r in self.same_each("get_work_roles", self.essay)],
                         [("Author", 0)])

    def test_a_persons_works(self) -> None:
        notes = self.work_both("notes", title="Notes", publisher="Verso", year="1951")
        self.link(self.ann, notes, "Editor")
        self.link(self.bo, self.essay, "Author")
        self.link(self.ann, self.essay, "Author", "A. Lee")
        person = self.same_each("get_person", self.ann)
        self.assertEqual([(w["title"], w["role_type"], w["order_index"]) for w in person["works"]],
                         [("Notes", "Editor", 0), ("Essay", "Author", 1)])
        self.assertEqual(person["works"][1]["linked_authors"], "Bo Ng, A. Lee")

    def test_a_linked_person_is_kept_until_unlinked(self) -> None:
        self.link(self.ann, self.essay, "Author")
        self.assertIn("linked works", self.refused_each("delete_person_if_unlinked", self.ann))
        self.same_each("delete_work_role", self.essay, self.ann, ("Author", "Author"))
        self.same_each("delete_person_if_unlinked", self.ann)
        self.assertIsNone(self.same_each("get_person", self.ann))

    def test_a_deleted_work_takes_its_roles(self) -> None:
        self.link(self.ann, self.essay, "Author")
        self.same_each("insert_initial_role", self.essay, self.bo, ("Editor", "Editor"), (5, 5))
        for side, work in zip(self.sides(), self.essay):
            self.assertIsNotNone(side.db.delete_work_record(work))
        self.assertEqual(self.same_each("get_person", self.ann)["works"], [])
        self.assertEqual(self.same_each("get_work_roles", self.essay), [])
        self.same_each("delete_person_if_unlinked", self.bo)

    def test_a_rename_reaches_every_linked_work(self) -> None:
        notes = self.work_both("notes", title="Notes")
        self.link(self.ann, self.essay, "Author")
        self.link(self.ann, notes, "Author")
        self.same_each("update_person_profile", self.ann, ({"last_name": "Leigh"}, {"last_name": "Leigh"}))
        # Works created within one second list in id order, so compare by title.
        mine, theirs = (sorted(rows, key=lambda row: row["title"]) for rows in self.both("get_all_works"))
        self.assertEqual(self.leg.normal(mine), self.ref.normal(theirs))
        self.assertEqual([row["linked_authors"] for row in mine], ["Ann Leigh", "Ann Leigh"])
