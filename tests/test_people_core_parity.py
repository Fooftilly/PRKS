"""Persons, Person Groups and publishers: the PostgreSQL facade returns SQLite's JSON (L-P-1 #632).

Each test runs the same calls on the library object of its leg and on a
reference ``PRKSDatabase`` built in the same test, and compares the results
after replacing generated ids, timestamps and ETag digests
(``tests/people_parity_helpers.py``). On the SQLite leg both sides are
SQLite; on the ``postgres`` leg (``scripts/test-postgres --parity``) the left
side is :class:`backend.pg_facade.PostgresLibrary`. Inputs avoid the
documented differences (``backend/pg_facade/people.py``): aliases are
written in their canonical ``", "`` form and names differ in ASCII letters
only where case matters.
"""

from __future__ import annotations

import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from tests.backend_parity import parametrize_backends
from tests.people_parity_helpers import PeopleParityTestCase

_PROFILE = {
    "first_name": "Hannah", "aliases": "H. Arendt, Hannah Arendt", "about": "Political theorist",
    "image_url": "https://example.org/arendt.jpg", "link_wikipedia": "https://en.wikipedia.org/wiki/HA",
    "link_stanford_encyclopedia": "https://plato.stanford.edu/arendt",
    "link_iep": "https://iep.utm.edu/arendt", "links_other": "https://example.org/more",
    "birth_date": "1906-10-14", "death_date": "1975-12-04",
}
_MISSING_PERSON = "P-" + "0" * 32
_MISSING_GROUP = "PG-" + "0" * 32


@parametrize_backends
class PersonParityTests(PeopleParityTestCase):
    def test_a_person_reads_back_alike(self) -> None:
        ids = self.person_both("Arendt", **_PROFILE)
        person = self.same_for("get_person", ids)
        self.assertEqual(person["aliases"], "H. Arendt, Hannah Arendt")
        self.assertEqual((person["works"], person["groups"]), ([], []))
        self.same_for("get_person_metadata_state", ids)

    def test_an_unknown_person_reads_as_none(self) -> None:
        self.assertEqual(self.both("get_person", _MISSING_PERSON), (None, None))
        self.assertEqual(self.both("get_person_metadata_state", _MISSING_PERSON), (None, None))
        self.assertEqual(self.both("get_person_groups_state", _MISSING_PERSON), (None, None))

    def test_the_catalog_orders_by_last_name_then_id(self) -> None:
        for label in ("beta", "Alpha", "_under", "Zed", "Ärzt"):
            self.person_both(label)
        group = self.group_both("Thinkers")
        mine, theirs = self.both("get_all_persons")
        for side, rows in ((self.leg, mine), (self.ref, theirs)):
            self.assertEqual([row["last_name"] for row in rows], ["Alpha", "Zed", "_under", "beta", "Ärzt"])
        self.assertEqual(self.leg.normal(mine), self.ref.normal(theirs))
        first = (mine[0]["id"], theirs[0]["id"])
        self.leg.db.add_person_to_group(first[0], group[0])
        self.ref.db.add_person_to_group(first[1], group[1])
        rows = self.same("get_all_persons")
        self.assertEqual(rows[0]["groups"], [{"id": group[0], "name": "Thinkers"}])
        etags = (self.leg.db.etag_persons_catalog(rows), self.ref.db.etag_persons_catalog(rows))
        self.assertEqual(etags[0], etags[1])

    def test_profile_fields_advance_only_when_they_change(self) -> None:
        ids = self.person_both("Weil", first_name="Simone")
        for fields in ({"about": "Philosopher"}, {"about": "Philosopher"},
                       {"first_name": "S.", "aliases": "Simone Weil"}, {"not_a_field": "ignored"}):
            self.same_for("update_person_profile", ids, fields)
            state = self.same_for("get_person_metadata_state", ids)
        self.assertEqual(state["fields"]["about"]["revision"], 1)
        self.assertEqual(state["fields"]["aliases"]["revision"], 1)
        self.assertEqual(self.same_for("get_person", ids)["aliases"], "Simone Weil")

    def test_an_invalid_portrait_is_refused_alike(self) -> None:
        ids = self.person_both("Portrait")
        self.refused_alike("update_person_profile", ids, {"image_url": "javascript:alert(1)"})
        self.assertEqual(self.same_for("get_person", ids)["image_url"], "")

    def test_a_profile_and_its_groups_change_together(self) -> None:
        ids = self.person_both("Adorno")
        first, second = self.group_both("Frankfurt"), self.group_both("Critical theory")
        self.leg.db.update_person_profile(ids[0], {"about": "T"}, [first[0], second[0], first[0], ""])
        self.ref.db.update_person_profile(ids[1], {"about": "T"}, [first[1], second[1], first[1], ""])
        self.same_state_for("get_person_groups_state", ids)
        self.leg.db.update_person_profile(ids[0], {}, [second[0]])
        self.ref.db.update_person_profile(ids[1], {}, [second[1]])
        state = self.same_state_for("get_person_groups_state", ids)
        self.assertEqual([(g["revision"], g["present"]) for g in state["groups"]],
                         [(2, False), (1, True)] if state["groups"][0]["group_id"] == first[0]
                         else [(1, True), (2, False)])
        self.same_for("get_person", ids)

    def test_an_unknown_group_refuses_the_whole_profile_edit(self) -> None:
        ids = self.person_both("Benjamin")
        message = self.refused_alike("update_person_profile", ids, {"about": "Critic"}, [_MISSING_GROUP])
        self.assertEqual(message, f"Unknown group id: {_MISSING_GROUP}")
        self.assertEqual(self.same_for("get_person", ids)["about"], "")
        self.assertEqual(self.refused_alike("update_person_profile", ids, {}, "not-a-list"),
                         "group_ids must be a JSON array")

    def test_an_unknown_person_with_groups_is_refused_and_without_is_ignored(self) -> None:
        missing = (_MISSING_PERSON, _MISSING_PERSON)
        self.assertEqual(self.refused_alike("update_person_profile", missing, {"about": "x"}, []),
                         "Person not found.")
        self.same_for("update_person_profile", missing, {"about": "x"})

    def test_delete_removes_memberships_and_refuses_the_unknown(self) -> None:
        ids = self.person_both("Leaving")
        group = self.group_both("Club")
        self.leg.db.add_person_to_group(ids[0], group[0])
        self.ref.db.add_person_to_group(ids[1], group[1])
        self.same_for("delete_person_if_unlinked", ids)
        self.assertEqual(self.both("get_person", ids[0]), (None, None))
        detail = self.same_for("get_person_group", group)
        self.assertEqual((detail["member_count"], detail["members"]), (0, []))
        self.same_state_for("get_person_group_sync_state", group)
        for missing in ((ids[0], ids[1]), ("", "")):
            self.assertEqual(self.refused_alike("delete_person_if_unlinked", missing), "Person not found.")


@parametrize_backends
class PersonGroupParityTests(PeopleParityTestCase):
    def test_a_group_with_a_new_parent_by_name(self) -> None:
        child = self.group_both("Frankfurt School", parent_name="Philosophy", description="Critical theory")
        parent = (self.leg.group_named("Philosophy"), self.ref.group_named("Philosophy"))
        detail = self.same_for("get_person_group", child)
        self.assertEqual(detail["parent"], {"id": parent[0], "name": "Philosophy"})
        self.same_for("get_person_group", parent)
        rows = self.same("get_all_person_groups")
        self.assertEqual([(row["name"], row["child_count"]) for row in rows],
                         [("Frankfurt School", 0), ("Philosophy", 1)])
        self.assertEqual(self.leg.db.etag_person_groups_catalog(rows),
                         self.ref.db.etag_person_groups_catalog(rows))
        self.same_state_for("get_person_group_sync_state", child)

    def test_an_existing_parent_is_found_by_name(self) -> None:
        self.group_both("Philosophy")
        self.group_both("Ethics", parent_name="philosophy")
        rows = self.same("get_all_person_groups")
        self.assertEqual(len(rows), 2)

    def test_names_are_unique_and_a_parent_cannot_share_the_name(self) -> None:
        self.group_both("Vienna Circle")
        for fields in ({"name": "vienna circle"}, {"name": " Vienna Circle "}, {"name": ""},
                       {"name": "Same", "parent_name": "SAME"}, {"name": "x" * 201},
                       {"name": "Orphan", "parent_id": _MISSING_GROUP},
                       {"name": "Long", "description": "d" * 2001}):
            with self.subTest(fields=fields):
                self.refused_alike("add_person_group_with_parent_options", (), **fields)
        self.assertEqual(len(self.same("get_all_person_groups")), 1)

    def test_fields_update_and_advance(self) -> None:
        group = self.group_both("Old name")
        self.group_both("Other")
        self.same_for("update_person_group", group, {"name": "New name", "description": "About"})
        self.same_for("update_person_group", group, {"name": "New name"})
        self.same_for("update_person_group", group, {"parent_name": "Created parent"})
        self.same_for("get_person_group", group)
        state = self.same_state_for("get_person_group_sync_state", group)
        self.assertEqual({f: v["revision"] for f, v in state["fields"].items()},
                         {"name": 1, "description": 1, "parent_id": 1})
        self.assertEqual(self.refused_alike("update_person_group", group, {"name": "other"}),
                         "A group with this name already exists.")
        self.assertEqual(self.refused_alike("update_person_group", (_MISSING_GROUP, _MISSING_GROUP),
                                            {"name": "x"}), "Group not found.")
        self.same_for("update_person_group", group, {"parent_id": None})
        self.assertIsNone(self.same_for("get_person_group", group)["parent"])

    def test_the_hierarchy_stays_acyclic(self) -> None:
        top = self.group_both("Top")
        middle = self.group_both("Middle", parent_name="Top")
        bottom = self.group_both("Bottom", parent_name="Middle")
        self.assertEqual(self.refused_alike("update_person_group", top, {"parent_name": "Bottom"}),
                         "Cannot set parent to a subgroup (cycle).")
        self.assertEqual(self.refused_alike("update_person_group", top, {"parent_name": "Top"}),
                         "A group cannot be its own parent.")
        self.same_state_for("get_person_group_sync_state", top)
        self.same_for("get_person_group", bottom)
        self.same_for("get_person_group", middle)

    def test_delete_reparents_children_and_drops_members(self) -> None:
        top = self.group_both("Top")
        middle = self.group_both("Middle", parent_name="Top")
        bottom = self.group_both("Bottom", parent_name="Middle")
        person = self.person_both("Member")
        self.leg.db.add_person_to_group(person[0], middle[0])
        self.ref.db.add_person_to_group(person[1], middle[1])
        self.same_for("delete_person_group", middle)
        self.assertEqual(self.same_for("get_person_group", bottom)["parent"], {"id": top[0], "name": "Top"})
        state = self.same_state_for("get_person_group_sync_state", bottom)
        self.assertEqual(state["fields"]["parent_id"]["revision"], 1)
        self.same_for("get_person", person)
        self.same("get_all_person_groups")
        self.assertEqual(self.refused_alike("delete_person_group", middle), "Group not found.")

    def test_membership_from_both_ends(self) -> None:
        group = self.group_both("Readers")
        people = [self.person_both(label) for label in ("b", "A", "c")]
        for person in people:
            self.leg.db.add_person_to_group(person[0], group[0])
            self.ref.db.add_person_to_group(person[1], group[1])
        self.leg.db.add_person_to_group(people[0][0], group[0])
        self.ref.db.add_person_to_group(people[0][1], group[1])
        self.leg.db.remove_person_from_group(people[2][0], group[0])
        self.ref.db.remove_person_from_group(people[2][1], group[1])
        self.leg.db.remove_person_from_group(people[2][0], group[0])
        self.ref.db.remove_person_from_group(people[2][1], group[1])
        detail = self.same_for("get_person_group", group)
        self.assertEqual([m["last_name"] for m in detail["members"]], ["A", "b"])
        self.same_state_for("get_person_group_sync_state", group)
        for person in people:
            self.same_state_for("get_person_groups_state", person)
        self.same("get_all_persons")
        self.assertEqual(self.refused_alike("add_person_to_group", people[0], _MISSING_GROUP),
                         "Person or group not found.")
        self.refused_alike("add_person_to_group", (_MISSING_PERSON, _MISSING_PERSON), group[0])


@parametrize_backends
class PublisherParityTests(PeopleParityTestCase):
    def test_create_finds_an_existing_name(self) -> None:
        created = (self.leg.publisher("press", "Verso"), self.ref.publisher("press", "Verso"))
        self.assertEqual(self.leg.normal(created[0]), self.ref.normal(created[1]))
        again = self.same("add_publisher", "  verso ")
        self.assertEqual((again["name"], again["existed"]), ("Verso", True))
        self.assertEqual(self.refused_alike("add_publisher", (), "   "), "publisher name is empty")

    def test_alias_rules_in_order(self) -> None:
        verso = (self.leg.publisher("verso", "Verso")["id"], self.ref.publisher("verso", "Verso")["id"])
        self.leg.publisher("penguin", "Penguin")
        self.ref.publisher("penguin", "Penguin")
        self.same_for("add_publisher_alias", verso, "Verso Books")
        self.same_for("add_publisher_alias", verso, "verso books")
        for alias, message in (("", "alias is empty"), ("VERSO", "alias matches canonical publisher name"),
                               ("penguin", "alias conflicts with another publisher name")):
            with self.subTest(alias=alias):
                self.assertEqual(self.refused_alike("add_publisher_alias", verso, alias), message)
        self.assertEqual(self.refused_alike("add_publisher_alias", ("R-" + "0" * 32,) * 2, "x"),
                         "publisher not found")
        penguin = (self.leg.db.add_publisher("Penguin")["id"], self.ref.db.add_publisher("Penguin")["id"])
        self.assertEqual(self.refused_alike("add_publisher_alias", penguin, "Verso Books"), "alias already used")
        self.same("get_publishers_in_use")

    def test_delete_alias_and_publisher(self) -> None:
        verso = (self.leg.publisher("verso", "Verso")["id"], self.ref.publisher("verso", "Verso")["id"])
        self.same_for("add_publisher_alias", verso, "Verso Books")
        self.assertEqual(self.same_for("delete_publisher_alias", verso, "VERSO BOOKS"), True)
        self.assertEqual(self.same_for("delete_publisher_alias", verso, "Verso Books"), False)
        self.assertEqual(self.same_for("delete_publisher_alias", verso, "  "), False)
        self.same_for("add_publisher_alias", verso, "Verso Books")
        self.same_for("delete_publisher", verso)
        self.same_for("delete_publisher", verso)
        self.assertEqual(self.same("get_publishers_in_use"), [])
        # The alias went with its publisher, so another may take it.
        self.leg.publisher("other", "Other")
        self.ref.publisher("other", "Other")
        other = (self.leg.db.add_publisher("Other")["id"], self.ref.db.add_publisher("Other")["id"])
        self.same_for("add_publisher_alias", other, "Verso Books")

    def test_in_use_counts_works_by_name_and_alias(self) -> None:
        verso = (self.leg.publisher("verso", "Verso")["id"], self.ref.publisher("verso", "Verso")["id"])
        self.leg.publisher("beta", "beta press")
        self.ref.publisher("beta", "beta press")
        self.same_for("add_publisher_alias", verso, "Verso Books")
        for label, publisher in (("one", "Verso"), ("two", " verso books "), ("three", "Unknown"),
                                 ("four", "")):
            self.leg.work(label, title=label, publisher=publisher)
            self.ref.work(label, title=label, publisher=publisher)
        rows = self.same("get_publishers_in_use")
        self.assertEqual([(row["name"], row["work_count"]) for row in rows],
                         [("beta press", 0), ("Verso", 2)])
