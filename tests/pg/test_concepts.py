"""Concepts, their aliases and hierarchy on PostgreSQL: what parity cannot show (L-R-1 #634).

``tests/test_concepts_parity.py`` proves the facade returns SQLite's JSON.
These tests cover what only PostgreSQL has: identity keys (Unicode names
that are one name, or two) in the shared ``concept_keys`` namespace,
resolution through that index, refusals of what PostgreSQL text cannot
hold, the scopes a delete tombstones, the Research Note check, Library
isolation, and concurrent writers (each race holds one transaction open
until the other is waiting on it).
"""

from __future__ import annotations

import unicodedata
from typing import Any

from backend.domain.identity.authorization import AccessRefused
from backend.domain.identity.library_scope import library_query
from backend.domain.research import concepts
from backend.domain.works import notes
from backend.entity_ids import generate
from backend.pg_facade import context as facade_context
from backend.research_network import ResearchError
from tests.pg.test_tags import OrganizationTestCase


class ConceptTestCase(OrganizationTestCase):
    def concept(self, name: str, description: str = "") -> str:
        return self.library.create_concept(name, description)["id"]

    def refused(self, code: str, call: Any, *args: Any, **kwargs: Any) -> ResearchError:
        with self.assertRaises(ResearchError) as caught:
            call(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def state(self, concept_id: str) -> dict[str, Any]:
        state = self.library.get_concept_sync_state(concept_id)
        assert state is not None
        return state

    def resolve(self, name: str) -> Any:
        return library_query(self.db, self.actor.context, lambda scope: concepts.resolve(scope, name))

    def research_note(self, work_id: str, text: str) -> None:
        self.run_command(lambda scope: notes.set_note(scope, work_id, notes.RESEARCH, text))

    def concept_keys(self) -> list[tuple[str, str, bool]]:
        rows = self.sql("SELECT key, concept_id, alias_key IS NULL AS is_name FROM prks.concept_keys ORDER BY key")
        return [(row["key"], row["concept_id"], row["is_name"]) for row in rows]

    def assert_integrity(self) -> None:
        """One key row per name and alias, each naming its holder."""
        names = self.count("concepts")
        aliases = self.count("concept_aliases")
        self.assertEqual(len(self.concept_keys()), names + aliases)
        self.assertEqual(self.sql("""
            SELECT count(*) AS n FROM prks.concept_keys k
            LEFT JOIN prks.concepts c ON c.id = k.concept_id AND k.alias_key IS NULL AND c.name_key = k.key
            LEFT JOIN prks.concept_aliases a ON a.concept_id = k.concept_id AND a.alias_key = k.key
            WHERE c.id IS NULL AND a.concept_id IS NULL""")[0]["n"], 0)


class IdentityTests(ConceptTestCase):
    def test_names_compare_by_identity_key(self) -> None:
        logos = self.concept("  Λόγος  ")
        self.assertEqual(self.library.get_concept(logos)["name"], "Λόγος")
        for name in ("ΛΌΓΟΣ", "λόγος"):
            with self.subTest(name=name):
                self.refused("concept_exists", self.library.create_concept, name)
        strasse = self.concept("Straße")
        self.refused("concept_exists", self.library.create_concept, "STRASSE")
        composed = self.concept(unicodedata.normalize("NFC", "Café"))
        self.refused("concept_exists", self.library.create_concept, unicodedata.normalize("NFD", "CAFÉ"))
        # Diacritics are not folded (§8.1).
        self.assertNotEqual(self.concept("Resume"), self.concept("Résumé"))
        self.assertEqual((self.resolve("λόγος"), self.resolve("strasse"), self.resolve("CAFÉ")),
                         (logos, strasse, composed))
        self.assertIsNone(self.resolve("Logos"))
        self.assertIsNone(self.resolve("x" * 161))
        self.assertEqual(self.count("concepts"), 5)
        self.assert_integrity()

    def test_names_and_aliases_share_one_namespace(self) -> None:
        ethics, logic = self.concept("Ethics"), self.concept("Logic")
        self.library.replace_concept_aliases(ethics, ["Morals", "MORALS", "ethics", " Moral  philosophy "])
        self.assertEqual(self.library.get_concept(ethics)["aliases"], ["Moral philosophy", "Morals"])
        self.refused("alias_conflict", self.library.replace_concept_aliases, logic, ["morals"])
        self.refused("alias_conflict", self.library.replace_concept_aliases, ethics, ["LOGIC"])
        self.refused("concept_exists", self.library.create_concept, "MORAL PHILOSOPHY")
        self.refused("concept_exists", self.library.update_concept, logic, name="morals")
        self.assertEqual(self.resolve("moral philosophy"), ethics)
        self.assertEqual(self.library.get_concept(logic)["aliases"], [])
        self.assert_integrity()

    def test_a_rename_keeps_the_old_name_as_an_alias(self) -> None:
        ethics = self.concept("Ethics")
        self.library.replace_concept_aliases(ethics, ["Morals"])
        # Renamed onto its own alias: the old name takes the freed place.
        renamed = self.library.update_concept(ethics, name="MORALS")
        self.assertEqual((renamed["name"], renamed["aliases"]), ("MORALS", ["Ethics"]))
        self.assertEqual(self.resolve("ethics"), ethics)
        # A change of case or spacing only keeps the key: no alias is added.
        self.assertEqual(self.library.update_concept(ethics, name="Morals")["aliases"], ["Ethics"])
        # Dropped deliberately: the request places the old key itself.
        state = self.state(ethics)
        self.library.replace_concept_aliases(ethics, [])
        self.assertEqual(self.library.get_concept(ethics)["aliases"], [])
        self.assertEqual(self.state(ethics)["identity_revision"], state["identity_revision"] + 1)
        self.assert_integrity()

    def test_only_a_change_advances_a_scope(self) -> None:
        ethics = self.concept("Ethics", "About")
        self.library.update_concept(ethics, name=" Ethics ", description="About")
        self.library.replace_concept_aliases(ethics, [])
        self.library.replace_concept_parents(ethics, [])
        self.assertEqual(self.revisions("concept-field"), {})
        self.assertEqual(self.revisions("concept-identity"), {})
        self.assertEqual(self.revisions("concept-parents"), {})
        self.library.replace_concept_aliases(ethics, ["morals"])
        self.library.replace_concept_aliases(ethics, ["Morals"])  # a new spelling is a change
        self.library.update_concept(ethics, description="Changed")
        state = self.state(ethics)
        self.assertEqual((state["identity_revision"], state["fields"]["description"]),
                         (2, {"value": "Changed", "revision": 1}))
        self.assertEqual(state["identity"], {"name": "Ethics", "aliases": ["Morals"]})

    def test_what_postgresql_cannot_hold_or_the_rules_refuse_is_refused_before_any_write(self) -> None:
        ethics = self.concept("Ethics")
        for code, call, args in (("invalid_name", self.library.create_concept, ("a\x00b",)),
                                 ("invalid_name", self.library.create_concept, ("x" * 161,)),
                                 ("invalid_text", self.library.create_concept, ("Ok", "d\x00")),
                                 ("invalid_name", self.library.replace_concept_aliases, (ethics, ["\x00"])),
                                 ("not_found", lambda cid: self.library.update_concept(cid, name="X"), ("C-\x00",)),
                                 ("not_found", self.library.delete_concept, ("C-\x00",))):
            with self.subTest(code=code, args=[str(arg)[:12] for arg in args]):
                self.refused(code, call, *args)
        self.assertIsNone(self.library.get_concept("C-\x00"))
        self.assertIsNone(self.library.get_concept_sync_state("C-\x00"))
        self.refused("parent_not_found", self.library.replace_concept_parents, ethics, ["C-\x00"])
        self.assertEqual(self.library.get_concept(self.concept("é" * 160))["name"], "é" * 160)
        self.assertEqual(self.count("concepts"), 2)
        self.assert_integrity()


class HierarchyTests(ConceptTestCase):
    def test_a_cycle_is_refused_at_any_depth(self) -> None:
        a, b, c = self.concept("A"), self.concept("B"), self.concept("C")
        self.library.replace_concept_parents(b, [a])
        self.library.replace_concept_parents(c, [b])
        self.refused("concept_cycle", self.library.replace_concept_parents, a, [c])
        self.refused("concept_cycle", self.library.replace_concept_parents, a, [a])
        self.refused("parent_not_found", self.library.replace_concept_parents, a, [generate("C")])
        # Several parents, a diamond: no cycle.
        self.library.replace_concept_parents(c, [b, a, f" {a} "])
        self.assertEqual([p["id"] for p in self.library.get_concept(c)["parents"]], [a, b])
        self.assertEqual([child["id"] for child in self.library.get_concept(a)["children"]], [b, c])
        self.assertEqual(self.library.list_concepts()[0]["subconcept_count"], 2)

    def test_both_ends_of_an_edge_advance(self) -> None:
        a, b, c = self.concept("A"), self.concept("B"), self.concept("C")
        self.library.replace_concept_parents(c, [a])
        self.assertEqual(self.revisions("concept-parents"), {c: 1, a: 1})
        self.library.replace_concept_parents(c, [b])
        self.assertEqual(self.revisions("concept-parents"), {c: 2, a: 2, b: 1})
        self.library.replace_concept_parents(c, [b])
        self.assertEqual(self.revisions("concept-parents"), {c: 2, a: 2, b: 1})


class DeleteTests(ConceptTestCase):
    def test_a_concept_a_research_note_names_is_refused(self) -> None:
        ethics = self.concept("Ethics")
        self.library.replace_concept_aliases(ethics, ["Morals"])
        work = self.library.add_work("W")
        self.research_note(work, "See `[[concept:Ethics]]` and [[concept: MORALS ]].")
        error = self.refused("concept_in_use", self.library.delete_concept, ethics)
        self.assertEqual(error.http_status, 409)
        # Markup in code does not count; nor does a Private Note.
        self.research_note(work, "See `[[concept:Ethics]]`.")
        self.run_command(lambda scope: notes.set_note(scope, work, notes.PRIVATE, "[[concept:Ethics]]"))
        self.library.delete_concept(ethics)
        self.assertIsNone(self.library.get_concept(ethics))
        self.refused("not_found", self.library.delete_concept, ethics)
        self.assertEqual(self.concept_keys(), [])

    def test_edges_go_and_every_scope_it_owned_moves_on(self) -> None:
        parent, middle, child = self.concept("Parent"), self.concept("Middle"), self.concept("Child")
        self.library.replace_concept_parents(middle, [parent])
        self.library.replace_concept_parents(child, [middle])
        self.library.replace_concept_aliases(middle, ["Mid"])
        before = self.revisions("concept-parents")
        self.library.delete_concept(middle)
        after = self.revisions("concept-parents")
        self.assertEqual({key: after[key] - before.get(key, 0) for key in after},
                         {parent: 1, middle: 1, child: 1})
        self.assertEqual(self.revisions("concept-identity"), {middle: 2})
        self.assertEqual(self.revisions("concept-field"), {f'["{middle}","description"]': 1})
        # Its child is not reparented.
        self.assertEqual(self.library.get_concept(child)["parents"], [])
        self.assertEqual(self.library.get_concept(parent)["children"], [])
        self.assertEqual(self.count("concept_parents"), 0)
        self.assert_integrity()


class IsolationTests(ConceptTestCase):
    def test_another_librarys_concepts_are_invisible_and_untouchable(self) -> None:
        ethics = self.concept("Ethics")
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            self.assertEqual(self.library.list_concepts(), [])
            self.assertIsNone(self.library.get_concept(ethics))
            self.assertIsNone(self.library.get_concept_sync_state(ethics))
            theirs = self.concept("Ethics")  # the namespace is per Library
            for call, args, kwargs in (("update_concept", (ethics,), {"name": "X"}),
                                       ("replace_concept_aliases", (ethics, ["X"]), {}),
                                       ("replace_concept_parents", (ethics, []), {}),
                                       ("delete_concept", (ethics,), {})):
                with self.subTest(call=call):
                    self.refused("not_found", getattr(self.library, call), *args, **kwargs)
            self.refused("parent_not_found", self.library.replace_concept_parents, theirs, [ethics])
        self.assertEqual([row["id"] for row in self.library.list_concepts()], [ethics])
        self.assertEqual(self.resolve("ethics"), ethics)

    def test_a_reader_reads_concepts_but_cannot_change_them(self) -> None:
        ethics = self.concept("Ethics")
        with facade_context.acting_as(self.reader_context()):
            self.assertEqual(len(self.library.list_concepts()), 1)
            for call, args in (("create_concept", ("New",)), ("replace_concept_aliases", (ethics, ["X"])),
                               ("delete_concept", (ethics,))):
                with self.subTest(call=call), self.assertRaises(AccessRefused):
                    getattr(self.library, call)(*args)


class ConcurrencyTests(ConceptTestCase):
    def test_two_creates_of_one_name_make_one_concept(self) -> None:
        concept_id = generate("C")
        holder, contender = self.race(lambda scope: concepts.create(scope, concept_id, "Λόγος"),
                                      lambda: self.library.create_concept("ΛΌΓΟΣ"))
        self.assertEqual(holder["id"], concept_id)
        self.assertIsInstance(contender, ResearchError)
        self.assertEqual(contender.code, "concept_exists")
        self.assertEqual(self.count("concepts"), 1)
        self.assert_integrity()

    def test_two_renames_to_one_name(self) -> None:
        first, second = self.concept("First"), self.concept("Second")
        holder, contender = self.race(lambda scope: concepts.update(scope, first, "Shared"),
                                      lambda: self.library.update_concept(second, name="SHARED"))
        self.assertEqual(holder["name"], "Shared")
        self.assertIsInstance(contender, ResearchError)
        self.assertEqual(contender.code, "concept_exists")
        self.assertEqual(self.library.get_concept(second)["name"], "Second")
        self.assert_integrity()

    def test_an_alias_racing_a_create_of_the_same_name(self) -> None:
        ethics = self.concept("Ethics")
        holder, contender = self.race(lambda scope: concepts.replace_aliases(scope, ethics, ["Morals"]),
                                      lambda: self.library.create_concept("morals"))
        self.assertEqual(holder["aliases"], ["Morals"])
        self.assertEqual(contender.code, "concept_exists")
        self.assert_integrity()

    def test_a_create_racing_an_alias_of_the_same_name(self) -> None:
        ethics = self.concept("Ethics")
        holder, contender = self.race(lambda scope: concepts.create(scope, generate("C"), "Morals"),
                                      lambda: self.library.replace_concept_aliases(ethics, ["MORALS"]))
        self.assertEqual(holder["name"], "Morals")
        self.assertIsInstance(contender, ResearchError)
        self.assertEqual(contender.code, "alias_conflict")
        self.assertEqual(self.count("concept_aliases"), 0)
        self.assert_integrity()

    def test_two_parent_changes_that_together_close_a_cycle(self) -> None:
        a, b = self.concept("A"), self.concept("B")
        holder, contender = self.race(lambda scope: concepts.replace_parents(scope, a, [b]),
                                      lambda: self.library.replace_concept_parents(b, [a]))
        self.assertEqual([p["id"] for p in holder["parents"]], [b])
        self.assertIsInstance(contender, ResearchError)
        self.assertEqual(contender.code, "concept_cycle")
        self.assertEqual(self.count("concept_parents"), 1)

    def test_a_delete_racing_a_parent_change_naming_it(self) -> None:
        parent, child = self.concept("Parent"), self.concept("Child")
        holder, contender = self.race(lambda scope: concepts.remove(scope, parent),
                                      lambda: self.library.replace_concept_parents(child, [parent]))
        self.assertIsNone(holder)
        self.assertEqual(contender.code, "parent_not_found")
        self.assertEqual(self.count("concept_parents"), 0)

    def test_a_parent_change_racing_a_delete_of_the_parent(self) -> None:
        parent, child = self.concept("Parent"), self.concept("Child")
        holder, contender = self.race(lambda scope: concepts.replace_parents(scope, child, [parent]),
                                      lambda: self.library.delete_concept(parent))
        self.assertEqual(len(holder["parents"]), 1)
        self.assertIsNone(contender)
        # The delete waited, then removed the edge and advanced the child.
        self.assertEqual(self.library.get_concept(child)["parents"], [])
        self.assertEqual(self.revisions("concept-parents")[child], 2)

    def test_a_delete_racing_a_rename(self) -> None:
        ethics = self.concept("Ethics")
        holder, contender = self.race(lambda scope: concepts.update(scope, ethics, "Morals"),
                                      lambda: self.library.delete_concept(ethics))
        self.assertEqual(holder["aliases"], ["Ethics"])
        self.assertIsNone(contender)
        self.assertEqual(self.revisions("concept-identity"), {ethics: 2})
        self.assert_integrity()

    def test_a_delete_racing_a_research_note_that_names_it(self) -> None:
        ethics = self.concept("Ethics")
        work = self.library.add_work("W")
        holder, contender = self.race(
            lambda scope: notes.set_note(scope, work, notes.RESEARCH, "On [[concept:ETHICS]]."),
            lambda: self.library.delete_concept(ethics))
        self.assertEqual(holder, (True, 1))
        self.assertIsInstance(contender, ResearchError)
        self.assertEqual(contender.code, "concept_in_use")
        self.assertEqual(self.library.get_concept(ethics)["name"], "Ethics")

    def test_a_research_note_racing_a_delete_of_a_concept_it_names(self) -> None:
        ethics = self.concept("Ethics")
        work = self.library.add_work("W")
        holder, contender = self.race(
            lambda scope: concepts.remove(scope, ethics),
            lambda: self.run_command(lambda scope: notes.set_note(scope, work, notes.RESEARCH,
                                                                  "On [[concept:Ethics]].")))
        self.assertIsNone(holder)
        # The note waited for the delete, then was stored; the notes slice
        # resolves (and creates) the names it holds.
        self.assertEqual(contender, (True, 1))
        self.assertIsNone(self.library.get_concept(ethics))
