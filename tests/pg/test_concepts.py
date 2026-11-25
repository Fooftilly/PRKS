"""Concepts, their aliases and hierarchy on PostgreSQL (L-R-1 #634).

:class:`AnswerTests` spells out what the facade answers. The other tests
cover identity keys (Unicode names
that are one name, or two) in the shared ``concept_keys`` namespace,
resolution through that index, refusals of what PostgreSQL text cannot
hold, the scopes a delete tombstones, the Research Note check, Library
isolation, and concurrent writers (each race holds one transaction open
until the other is waiting on it).
"""

from __future__ import annotations

import unicodedata
from functools import partial
from typing import Any, Optional
from unittest import mock

from backend.domain.identity.authorization import AccessRefused
from backend.domain.identity.library_scope import LibraryScope, library_query
from backend.domain.research import concepts
from backend.domain.works import notes
from backend.domain.entity_ids import generate
from tests import acting_context as facade_context
from backend.domain.research.rules import ResearchError
from tests.pg.answers import AnswerAssertions
from tests.pg.test_tags import OrganizationTestCase
from tests.narrowing import require
from backend.domain.research import concept_commands
from backend.domain.research import note_commands


class ConceptTestCase(OrganizationTestCase):
    def concept(self, name: str, description: str = "") -> str:
        return concept_commands.create(self.db, self.ctx, name, description)["id"]

    def refused(self, code: str, call: Any, *args: Any, **kwargs: Any) -> ResearchError:
        with self.assertRaises(ResearchError) as caught:
            call(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def state(self, concept_id: str) -> dict[str, Any]:
        state = concept_commands.sync_state(self.db, self.ctx, concept_id)
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
        self.assertEqual(require(concept_commands.detail(self.db, self.ctx, logos))["name"], "Λόγος")
        for name in ("ΛΌΓΟΣ", "λόγος"):
            with self.subTest(name=name):
                self.refused("concept_exists", partial(concept_commands.create, self.db, self.ctx), name)
        strasse = self.concept("Straße")
        self.refused("concept_exists", partial(concept_commands.create, self.db, self.ctx), "STRASSE")
        composed = self.concept(unicodedata.normalize("NFC", "Café"))
        self.refused("concept_exists", partial(concept_commands.create, self.db, self.ctx), unicodedata.normalize("NFD", "CAFÉ"))
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
        concept_commands.replace_aliases(self.db, self.ctx, ethics, ["Morals", "MORALS", "ethics", " Moral  philosophy "])
        self.assertEqual(require(concept_commands.detail(self.db, self.ctx, ethics))["aliases"], ["Moral philosophy", "Morals"])
        self.refused("alias_conflict", partial(concept_commands.replace_aliases, self.db, self.ctx), logic, ["morals"])
        self.refused("alias_conflict", partial(concept_commands.replace_aliases, self.db, self.ctx), ethics, ["LOGIC"])
        self.refused("concept_exists", partial(concept_commands.create, self.db, self.ctx), "MORAL PHILOSOPHY")
        self.refused("concept_exists", partial(concept_commands.update, self.db, self.ctx), logic, name="morals")
        self.assertEqual(self.resolve("moral philosophy"), ethics)
        self.assertEqual(require(concept_commands.detail(self.db, self.ctx, logic))["aliases"], [])
        self.assert_integrity()

    def test_a_rename_keeps_the_old_name_as_an_alias(self) -> None:
        ethics = self.concept("Ethics")
        concept_commands.replace_aliases(self.db, self.ctx, ethics, ["Morals"])
        # Renamed onto its own alias: the old name takes the freed place.
        renamed = concept_commands.update(self.db, self.ctx, ethics, name="MORALS")
        self.assertEqual((renamed["name"], renamed["aliases"]), ("MORALS", ["Ethics"]))
        self.assertEqual(self.resolve("ethics"), ethics)
        # A change of case or spacing only keeps the key: no alias is added.
        self.assertEqual(concept_commands.update(self.db, self.ctx, ethics, name="Morals")["aliases"], ["Ethics"])
        # Dropped deliberately: the request places the old key itself.
        state = self.state(ethics)
        concept_commands.replace_aliases(self.db, self.ctx, ethics, [])
        self.assertEqual(require(concept_commands.detail(self.db, self.ctx, ethics))["aliases"], [])
        self.assertEqual(self.state(ethics)["identity_revision"], state["identity_revision"] + 1)
        self.assert_integrity()

    def test_only_a_change_advances_a_scope(self) -> None:
        ethics = self.concept("Ethics", "About")
        concept_commands.update(self.db, self.ctx, ethics, name=" Ethics ", description="About")
        concept_commands.replace_aliases(self.db, self.ctx, ethics, [])
        concept_commands.replace_parents(self.db, self.ctx, ethics, [])
        self.assertEqual(self.revisions("concept-field"), {})
        self.assertEqual(self.revisions("concept-identity"), {})
        self.assertEqual(self.revisions("concept-parents"), {})
        concept_commands.replace_aliases(self.db, self.ctx, ethics, ["morals"])
        concept_commands.replace_aliases(self.db, self.ctx, ethics, ["Morals"])  # a new spelling is a change
        concept_commands.update(self.db, self.ctx, ethics, description="Changed")
        state = self.state(ethics)
        self.assertEqual((state["identity_revision"], state["fields"]["description"]),
                         (2, {"value": "Changed", "revision": 1}))
        self.assertEqual(state["identity"], {"name": "Ethics", "aliases": ["Morals"]})

    def test_what_postgresql_cannot_hold_or_the_rules_refuse_is_refused_before_any_write(self) -> None:
        ethics = self.concept("Ethics")
        for code, call, args in (("invalid_name", partial(concept_commands.create, self.db, self.ctx), ("a\x00b",)),
                                 ("invalid_name", partial(concept_commands.create, self.db, self.ctx), ("x" * 161,)),
                                 ("invalid_text", partial(concept_commands.create, self.db, self.ctx), ("Ok", "d\x00")),
                                 ("invalid_name", partial(concept_commands.replace_aliases, self.db, self.ctx), (ethics, ["\x00"])),
                                 ("not_found", lambda cid: concept_commands.update(self.db, self.ctx, cid, name="X"), ("C-\x00",)),
                                 ("not_found", partial(concept_commands.delete, self.db, self.ctx), ("C-\x00",))):
            with self.subTest(code=code, args=[str(arg)[:12] for arg in args]):
                self.refused(code, call, *args)
        self.assertIsNone(concept_commands.detail(self.db, self.ctx, "C-\x00"))
        self.assertIsNone(concept_commands.sync_state(self.db, self.ctx, "C-\x00"))
        self.refused("parent_not_found", partial(concept_commands.replace_parents, self.db, self.ctx), ethics, ["C-\x00"])
        self.assertEqual(require(concept_commands.detail(self.db, self.ctx, self.concept("é" * 160)))["name"], "é" * 160)
        self.assertEqual(self.count("concepts"), 2)
        self.assert_integrity()


class HierarchyTests(ConceptTestCase):
    def test_a_cycle_is_refused_at_any_depth(self) -> None:
        a, b, c = self.concept("A"), self.concept("B"), self.concept("C")
        concept_commands.replace_parents(self.db, self.ctx, b, [a])
        concept_commands.replace_parents(self.db, self.ctx, c, [b])
        self.refused("concept_cycle", partial(concept_commands.replace_parents, self.db, self.ctx), a, [c])
        self.refused("concept_cycle", partial(concept_commands.replace_parents, self.db, self.ctx), a, [a])
        self.refused("parent_not_found", partial(concept_commands.replace_parents, self.db, self.ctx), a, [generate("C")])
        # Several parents, a diamond: no cycle.
        concept_commands.replace_parents(self.db, self.ctx, c, [b, a, f" {a} "])
        self.assertEqual([p["id"] for p in require(concept_commands.detail(self.db, self.ctx, c))["parents"]], [a, b])
        self.assertEqual([child["id"] for child in require(concept_commands.detail(self.db, self.ctx, a))["children"]], [b, c])
        self.assertEqual(concept_commands.catalog(self.db, self.ctx)[0]["subconcept_count"], 2)

    def test_both_ends_of_an_edge_advance(self) -> None:
        a, b, c = self.concept("A"), self.concept("B"), self.concept("C")
        concept_commands.replace_parents(self.db, self.ctx, c, [a])
        self.assertEqual(self.revisions("concept-parents"), {c: 1, a: 1})
        concept_commands.replace_parents(self.db, self.ctx, c, [b])
        self.assertEqual(self.revisions("concept-parents"), {c: 2, a: 2, b: 1})
        concept_commands.replace_parents(self.db, self.ctx, c, [b])
        self.assertEqual(self.revisions("concept-parents"), {c: 2, a: 2, b: 1})


class DeleteTests(ConceptTestCase):
    def test_a_concept_a_research_note_names_is_refused(self) -> None:
        ethics = self.concept("Ethics")
        concept_commands.replace_aliases(self.db, self.ctx, ethics, ["Morals"])
        work = self.library.add_work("W")
        self.research_note(work, "See `[[concept:Ethics]]` and [[concept: MORALS ]].")
        error = self.refused("concept_in_use", partial(concept_commands.delete, self.db, self.ctx), ethics)
        self.assertEqual(error.http_status, 409)
        # Markup in code does not count; nor does a Private Note.
        self.research_note(work, "See `[[concept:Ethics]]`.")
        self.run_command(lambda scope: notes.set_note(scope, work, notes.PRIVATE, "[[concept:Ethics]]"))
        concept_commands.delete(self.db, self.ctx, ethics)
        self.assertIsNone(concept_commands.detail(self.db, self.ctx, ethics))
        self.refused("not_found", partial(concept_commands.delete, self.db, self.ctx), ethics)
        self.assertEqual(self.concept_keys(), [])

    def test_encoded_reference_prevents_deleting_canonical_concept(self) -> None:
        target = self.concept("Category]] Theory")
        work = self.library.add_work("W")
        self.research_note(work, "See [[concept-encoded:Category%5D%5D%20Theory]].")
        self.refused("concept_in_use", partial(concept_commands.delete, self.db, self.ctx), target)
        self.assertIsNotNone(concept_commands.detail(self.db, self.ctx, target))

        # The validation boundary also accepts C1 code points such as U+0091.
        c1_target = self.concept("Foo\\Bar\u0091")
        self.research_note(work, "See [[concept-encoded:Foo%5CBar%C2%91]].")
        self.refused("concept_in_use", partial(concept_commands.delete, self.db, self.ctx), c1_target)

    def test_edges_go_and_every_scope_it_owned_moves_on(self) -> None:
        parent, middle, child = self.concept("Parent"), self.concept("Middle"), self.concept("Child")
        concept_commands.replace_parents(self.db, self.ctx, middle, [parent])
        concept_commands.replace_parents(self.db, self.ctx, child, [middle])
        concept_commands.replace_aliases(self.db, self.ctx, middle, ["Mid"])
        before = self.revisions("concept-parents")
        concept_commands.delete(self.db, self.ctx, middle)
        after = self.revisions("concept-parents")
        self.assertEqual({key: after[key] - before.get(key, 0) for key in after},
                         {parent: 1, middle: 1, child: 1})
        self.assertEqual(self.revisions("concept-identity"), {middle: 2})
        self.assertEqual(self.revisions("concept-field"), {f'["{middle}","description"]': 1})
        # Its child is not reparented.
        self.assertEqual(require(concept_commands.detail(self.db, self.ctx, child))["parents"], [])
        self.assertEqual(require(concept_commands.detail(self.db, self.ctx, parent))["children"], [])
        self.assertEqual(self.count("concept_parents"), 0)
        self.assert_integrity()


class IsolationTests(ConceptTestCase):
    def test_another_librarys_concepts_are_invisible_and_untouchable(self) -> None:
        ethics = self.concept("Ethics")
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            self.assertEqual(concept_commands.catalog(self.db, self.ctx), [])
            self.assertIsNone(concept_commands.detail(self.db, self.ctx, ethics))
            self.assertIsNone(concept_commands.sync_state(self.db, self.ctx, ethics))
            theirs = self.concept("Ethics")  # the namespace is per Library
            for call, args, kwargs in ((concept_commands.update, (ethics,), {"name": "X"}),
                                       (concept_commands.replace_aliases, (ethics, ["X"]), {}),
                                       (concept_commands.replace_parents, (ethics, []), {}),
                                       (concept_commands.delete, (ethics,), {})):
                with self.subTest(call=f"{call.__module__}.{call.__name__}"):
                    self.refused("not_found", partial(call, self.db, self.ctx), *args, **kwargs)
            self.refused("parent_not_found", partial(concept_commands.replace_parents, self.db, self.ctx), theirs, [ethics])
        self.assertEqual([row["id"] for row in concept_commands.catalog(self.db, self.ctx)], [ethics])
        self.assertEqual(self.resolve("ethics"), ethics)

    def test_a_reader_reads_concepts_but_cannot_change_them(self) -> None:
        ethics = self.concept("Ethics")
        with facade_context.acting_as(self.reader_context()):
            self.assertEqual(len(concept_commands.catalog(self.db, self.ctx)), 1)
            for name, write in (("create", lambda: concept_commands.create(self.db, self.ctx, "New")),
                                ("replace_aliases", lambda: concept_commands.replace_aliases(self.db, self.ctx, ethics, ["X"])),
                                ("delete", lambda: concept_commands.delete(self.db, self.ctx, ethics))):
                with self.subTest(call=name), self.assertRaises(AccessRefused):
                    write()


class ConcurrencyTests(ConceptTestCase):
    def test_two_creates_of_one_name_make_one_concept(self) -> None:
        concept_id = generate("C")
        holder, contender = self.race(lambda scope: concepts.create(scope, concept_id, "Λόγος"),
                                      lambda: concept_commands.create(self.db, self.ctx, "ΛΌΓΟΣ"))
        self.assertEqual(holder["id"], concept_id)
        self.assertIsInstance(contender, ResearchError)
        self.assertEqual(contender.code, "concept_exists")
        self.assertEqual(self.count("concepts"), 1)
        self.assert_integrity()

    def test_two_renames_to_one_name(self) -> None:
        first, second = self.concept("First"), self.concept("Second")
        holder, contender = self.race(lambda scope: concepts.update(scope, first, "Shared"),
                                      lambda: concept_commands.update(self.db, self.ctx, second, name="SHARED"))
        self.assertEqual(holder["name"], "Shared")
        self.assertIsInstance(contender, ResearchError)
        self.assertEqual(contender.code, "concept_exists")
        self.assertEqual(require(concept_commands.detail(self.db, self.ctx, second))["name"], "Second")
        self.assert_integrity()

    def test_identity_writes_of_one_library_queue_on_its_key_lock(self) -> None:
        ethics, morals = self.concept("Ethics"), self.concept("Morals")
        holder, contender = self.race(lambda scope: concepts.replace_aliases(scope, ethics, ["Virtue"]),
                                      lambda: concept_commands.replace_aliases(self.db, self.ctx, morals, ["Duty"]))
        self.assertEqual(holder["aliases"], ["Virtue"])
        self.assertEqual(contender["aliases"], ["Duty"])
        self.assert_integrity()

    def test_two_identity_writes_taking_each_others_keys(self) -> None:
        # Without the key lock each writer would hold one key the other wants.
        a, b = self.concept("Alpha"), self.concept("Beta")
        holder, contender = self.race(
            lambda scope: concepts.set_identity(scope, a, "Duty", ["Virtue"]),
            lambda: self.run_command(lambda scope: concepts.set_identity(scope, b, "Virtue", ["Duty"])))
        self.assertEqual(holder, (True, 1))
        self.assertIsInstance(contender, ResearchError)
        self.assertEqual(contender.code, "concept_exists")
        self.assertEqual(require(concept_commands.detail(self.db, self.ctx, b))["name"], "Beta")
        self.assert_integrity()

    def test_an_alias_racing_a_create_of_the_same_name(self) -> None:
        ethics = self.concept("Ethics")
        holder, contender = self.race(lambda scope: concepts.replace_aliases(scope, ethics, ["Morals"]),
                                      lambda: concept_commands.create(self.db, self.ctx, "morals"))
        self.assertEqual(holder["aliases"], ["Morals"])
        self.assertEqual(contender.code, "concept_exists")
        self.assert_integrity()

    def test_a_create_racing_an_alias_of_the_same_name(self) -> None:
        ethics = self.concept("Ethics")
        holder, contender = self.race(lambda scope: concepts.create(scope, generate("C"), "Morals"),
                                      lambda: concept_commands.replace_aliases(self.db, self.ctx, ethics, ["MORALS"]))
        self.assertEqual(holder["name"], "Morals")
        self.assertIsInstance(contender, ResearchError)
        self.assertEqual(contender.code, "alias_conflict")
        self.assertEqual(self.count("concept_aliases"), 0)
        self.assert_integrity()

    def test_two_parent_changes_that_together_close_a_cycle(self) -> None:
        a, b = self.concept("A"), self.concept("B")
        holder, contender = self.race(lambda scope: concepts.replace_parents(scope, a, [b]),
                                      lambda: concept_commands.replace_parents(self.db, self.ctx, b, [a]))
        self.assertEqual([p["id"] for p in holder["parents"]], [b])
        self.assertIsInstance(contender, ResearchError)
        self.assertEqual(contender.code, "concept_cycle")
        self.assertEqual(self.count("concept_parents"), 1)

    def test_a_delete_racing_a_parent_change_naming_it(self) -> None:
        parent, child = self.concept("Parent"), self.concept("Child")
        holder, contender = self.race(lambda scope: concepts.remove(scope, parent),
                                      lambda: concept_commands.replace_parents(self.db, self.ctx, child, [parent]))
        self.assertIsNone(holder)
        self.assertEqual(contender.code, "parent_not_found")
        self.assertEqual(self.count("concept_parents"), 0)

    def test_a_parent_change_racing_a_delete_of_the_parent(self) -> None:
        parent, child = self.concept("Parent"), self.concept("Child")
        holder, contender = self.race(lambda scope: concepts.replace_parents(scope, child, [parent]),
                                      lambda: concept_commands.delete(self.db, self.ctx, parent))
        self.assertEqual(len(holder["parents"]), 1)
        self.assertIsNone(contender)
        # The delete waited, then removed the edge and advanced the child.
        self.assertEqual(require(concept_commands.detail(self.db, self.ctx, child))["parents"], [])
        self.assertEqual(self.revisions("concept-parents")[child], 2)

    def test_a_delete_racing_a_rename(self) -> None:
        ethics = self.concept("Ethics")
        holder, contender = self.race(lambda scope: concepts.update(scope, ethics, "Morals"),
                                      lambda: concept_commands.delete(self.db, self.ctx, ethics))
        self.assertEqual(holder["aliases"], ["Ethics"])
        self.assertIsNone(contender)
        self.assertEqual(self.revisions("concept-identity"), {ethics: 2})
        self.assert_integrity()

    def test_a_delete_racing_a_research_note_that_names_it(self) -> None:
        ethics = self.concept("Ethics")
        work = self.library.add_work("W")
        holder, contender = self.race(
            lambda scope: notes.set_note(scope, work, notes.RESEARCH, "On [[concept:ETHICS]]."),
            lambda: concept_commands.delete(self.db, self.ctx, ethics))
        self.assertEqual(holder, (True, 1))
        self.assertIsInstance(contender, ResearchError)
        self.assertEqual(contender.code, "concept_in_use")
        self.assertEqual(require(concept_commands.detail(self.db, self.ctx, ethics))["name"], "Ethics")

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
        self.assertIsNone(concept_commands.detail(self.db, self.ctx, ethics))


class CatalogTests(ConceptTestCase):
    """The Concept catalog's child counts, parents and aliases (the retired ``tests/test_concept_catalog_counts.py``)."""

    EDGES = {"beta": ["Alpha"], "Gamma": ["Alpha", "zeta"], "delta": ["beta", "Gamma", "Alpha"],
             "Epsilon": ["delta"], "eta": ["delta", "zeta"], "Theta": ["Epsilon"], "iota": ["Theta", "eta", "beta"]}

    def counts(self) -> dict[str, int]:
        return {item["name"]: item["subconcept_count"] for item in concept_commands.catalog(self.db, self.ctx)}

    def graph(self) -> dict[str, str]:
        ids = {name: self.concept(name)
               for name in ("zeta", "Alpha", "beta", "Gamma", "delta", "Epsilon", "eta", "Theta", "iota")}
        for child, parents in self.EDGES.items():
            concept_commands.replace_parents(self.db, self.ctx, ids[child], [ids[p] for p in parents])
        concept_commands.replace_aliases(self.db, self.ctx, ids["Gamma"], ["gamma-2", "Gamma One"])
        concept_commands.replace_aliases(self.db, self.ctx, ids["iota"], ["jot"])
        return ids

    def catalog_statements(self) -> list[str]:
        seen: list[str] = []
        real = LibraryScope.execute

        def record(scope: LibraryScope, query: Any, params: Any = None) -> Any:
            seen.append(str(query))
            return real(scope, query, params)

        with mock.patch.object(LibraryScope, "execute", autospec=True, side_effect=record):
            concept_commands.catalog(self.db, self.ctx)
        return [query for query in seen if "concept" in query]

    def test_child_counts_are_direct_children_once_per_parent(self) -> None:
        self.assertEqual(concept_commands.catalog(self.db, self.ctx), [])
        lonely = self.concept("Lonely")
        (item,) = concept_commands.catalog(self.db, self.ctx)
        self.assertEqual((item["subconcept_count"], item["parents"], item["aliases"]), (0, [], []))
        self.assertIs(type(item["subconcept_count"]), int)
        for name in ("A", "B"):
            concept_commands.replace_parents(self.db, self.ctx, self.concept(name), [lonely])
        left, right = self.concept("Left"), self.concept("Right")
        mid = self.concept("Mid")
        concept_commands.replace_parents(self.db, self.ctx, mid, [left, right])
        concept_commands.replace_parents(self.db, self.ctx, self.concept("Leaf"), [mid])
        self.assertEqual(self.counts(), {"A": 0, "B": 0, "Leaf": 0, "Left": 1, "Lonely": 2, "Mid": 1, "Right": 1})

    def test_order_parents_aliases_and_counts_over_a_graph(self) -> None:
        ids = self.graph()
        items = concept_commands.catalog(self.db, self.ctx)
        self.assertEqual([item["name"] for item in items],
                         ["Alpha", "beta", "delta", "Epsilon", "eta", "Gamma", "iota", "Theta", "zeta"])
        by_name = {item["name"]: item for item in items}
        for child, parents in self.EDGES.items():
            self.assertEqual(by_name[child]["parents"],
                             [{"id": ids[p], "name": p} for p in sorted(parents, key=str.lower)], child)
        self.assertEqual(self.counts(), {"Alpha": 3, "beta": 2, "delta": 2, "Epsilon": 1, "eta": 1, "Gamma": 1,
                                         "iota": 0, "Theta": 1, "zeta": 2})
        self.assertEqual((by_name["Gamma"]["aliases"], by_name["iota"]["aliases"]), (["Gamma One", "gamma-2"], ["jot"]))
        for item in items:
            self.assertIs(type(item["subconcept_count"]), int)
            self.assertEqual(require(concept_commands.detail(self.db, self.ctx, item["id"]))["name"], item["name"])

    def test_the_catalog_is_three_uncorrelated_statements_whatever_its_size(self) -> None:
        self.concept("Solo")
        small = self.catalog_statements()
        self.graph()
        large = self.catalog_statements()
        self.assertEqual(small, large)
        self.assertEqual(len(large), 3)
        for query in large:
            self.assertNotIn("(SELECT", query.upper())


_MISSING_CONCEPT = "C-" + "0" * 32
_EXISTS = ("concept_exists", "A Concept with that name or alias already exists.", 409)
_NOT_FOUND = ("not_found", "Concept not found.", 404)


def concept_state(label: str, name: str, *, description: str = "", description_revision: int = 0,
           aliases: Optional[list[str]] = None, identity_revision: int = 0, parent_ids: Optional[list[str]] = None,
           parents_revision: int = 0) -> dict[str, Any]:
    return {"concept_id": label, "fields": {"description": {"revision": description_revision, "value": description}},
            "identity": {"aliases": aliases or [], "name": name}, "identity_revision": identity_revision,
            "parent_ids": parent_ids or [], "parents_revision": parents_revision}


class AnswerTests(AnswerAssertions, ConceptTestCase):
    """What the Concept calls answer and refuse, spelled out."""

    def refusal(self, call: Any, *args: Any, **kwargs: Any) -> tuple[str, str, int]:
        with self.assertRaises(ResearchError) as caught:
            call(*args, **kwargs)
        return caught.exception.code, str(caught.exception), caught.exception.http_status

    def named(self, name: str, description: str = "") -> str:
        return self.labels.name(self.concept(name, description), f"<C {name}>")

    def test_create_read_and_list(self) -> None:
        ethics = self.named("Ethics", "The study of *conduct*.")
        self.named("logic")
        for args, expected in ((("ETHICS",), _EXISTS), (("  ",), ("invalid_name", "Name is required.", 400)),
                               (("a\tb\x01",), ("invalid_name", "Name contains invalid characters.", 400)),
                               (("x" * 161,), ("invalid_name", "Name is too long.", 400)),
                               (("Ok", "bad\x00"), ("invalid_text", "Text contains invalid characters.", 400))):
            with self.subTest(args=[arg[:8] for arg in args]):
                self.assertEqual(self.refusal(partial(concept_commands.create, self.db, self.ctx), *args), expected)
        self.answer(concept_commands.detail(self.db, self.ctx, ethics), {
            "aliases": [], "children": [], "created_at": "<s>", "description": "The study of *conduct*.",
            "id": "<C Ethics>", "name": "Ethics", "parents": [], "updated_at": "<s>"})
        self.assertIsNone(concept_commands.detail(self.db, self.ctx, _MISSING_CONCEPT))
        self.assertIsNone(concept_commands.detail(self.db, self.ctx, "  "))
        self.answer(concept_commands.sync_state(self.db, self.ctx, ethics),
                    concept_state("<C Ethics>", "Ethics", description="The study of *conduct*."))
        self.assertIsNone(concept_commands.sync_state(self.db, self.ctx, _MISSING_CONCEPT))
        self.answer(concept_commands.catalog(self.db, self.ctx), [
            {"aliases": [], "created_at": "<s>", "description": "The study of *conduct*.", "id": "<C Ethics>",
             "name": "Ethics", "parents": [], "subconcept_count": 0, "updated_at": "<s>"},
            {"description": "", "id": "<C logic>", "name": "logic"}])

    def test_research_note_save(self) -> None:
        """``save_work_notes`` (the Work PATCH's note): the Concepts it names, revisions, refusals."""
        self.named("Ethics")
        work = self.labels.name(self.library.add_work("W"), "<W W>")
        note = "On [[concept:ethics]] and [[concept:Logic]]."
        notes_state = {"private_note_revision": 0, "research_note_revision": 1, "work_id": "<W W>"}
        for _ in range(2):  # the same text again advances nothing
            self.assertIsNone(note_commands.save(self.db, self.ctx, work, note))
            self.answer(self.library.get_work_notes_state(work), notes_state)
        # A Concept the note names that does not exist yet is created.
        self.answer(concept_commands.catalog(self.db, self.ctx), [{"id": "<C Ethics>", "name": "Ethics"},
                                                   {"id": "<unknown id>", "name": "Logic", "description": ""}])
        for text, expected in ((None, ("invalid_text", "Research notes must be a string.", 400)),
                               (7, ("invalid_text", "Research notes must be a string.", 400)),
                               ("a\x01b", ("invalid_text", "Research notes contain invalid characters.", 400))):
            with self.subTest(text=text):
                self.assertEqual(self.refusal(partial(note_commands.save, self.db, self.ctx), work, text), expected)
        work_missing = ("not_found", "Work not found.", 404)
        self.assertEqual(self.refusal(partial(note_commands.save, self.db, self.ctx), "W-" + "0" * 32, "[[concept:Gone]]"), work_missing)
        self.assertEqual(self.refusal(partial(note_commands.save, self.db, self.ctx), "  ", "text"), work_missing)
        self.assertEqual([row["name"] for row in concept_commands.catalog(self.db, self.ctx)], ["Ethics", "Logic"])
        self.answer(self.library.get_work_notes_state(work), notes_state)

    def test_identity_and_definition(self) -> None:
        ethics, logic = self.named("Ethics"), self.named("Logic")
        self.answer(concept_commands.replace_aliases(self.db, self.ctx, ethics, ["Morals", "morals", "ethics", "Moral  code"]), {
            "aliases": ["Moral code", "Morals"], "children": [], "created_at": "<s>", "description": "",
            "id": "<C Ethics>", "name": "Ethics", "parents": [], "updated_at": "<s>"})
        # A renamed Concept keeps its old name as an alias.
        self.answer(concept_commands.update(self.db, self.ctx, ethics, name="Moral philosophy", description="About"), {
            "aliases": ["Ethics", "Moral code", "Morals"], "description": "About", "name": "Moral philosophy"})
        self.answer(concept_commands.update(self.db, self.ctx, ethics, name="MORALS"), {
            "aliases": ["Ethics", "Moral code", "Moral philosophy"], "name": "MORALS"})
        self.answer(concept_commands.update(self.db, self.ctx, ethics, name="Morals"), {
            "aliases": ["Ethics", "Moral code", "Moral philosophy"], "name": "Morals"})
        for concept_id, kwargs, expected in (
                (logic, {"name": "ethics"}, _EXISTS), (logic, {"name": "moral philosophy"}, _EXISTS),
                (ethics, {}, ("nothing_to_update", "Nothing to update.", 400)),
                (_MISSING_CONCEPT, {"name": "X"}, _NOT_FOUND), (" ", {"name": "X"}, _NOT_FOUND),
                (ethics, {"description": "\x00"}, ("invalid_text", "Text contains invalid characters.", 400)),
                (ethics, {"name": ""}, ("invalid_name", "Name is required.", 400))):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.refusal(partial(concept_commands.update, self.db, self.ctx), concept_id, **kwargs), expected)
        conflict = ("alias_conflict", "That search key already belongs to another Concept.", 409)
        for concept_id, aliases, expected in (
                (ethics, ["LOGIC"], conflict), (ethics, "Logic", ("invalid_aliases", "Aliases must be a JSON array.", 400)),
                (ethics, [1], ("invalid_aliases", "Each alias must be a string.", 400)),
                (ethics, ["\x07"], ("invalid_name", "Name contains invalid characters.", 400)),
                (logic, ["moral code"], conflict), (_MISSING_CONCEPT, [], _NOT_FOUND)):
            with self.subTest(aliases=aliases):
                self.assertEqual(self.refusal(partial(concept_commands.replace_aliases, self.db, self.ctx), concept_id, aliases), expected)
        self.answer(concept_commands.sync_state(self.db, self.ctx, ethics), concept_state(
            "<C Ethics>", "Morals", description="About", description_revision=1,
            aliases=["Ethics", "Moral code", "Moral philosophy"], identity_revision=4))
        self.answer(concept_commands.sync_state(self.db, self.ctx, logic), concept_state("<C Logic>", "Logic"))
        self.assertEqual([row["name"] for row in concept_commands.catalog(self.db, self.ctx)], ["Logic", "Morals"])

    def test_hierarchy(self) -> None:
        a, b, c = self.named("A"), self.named("B"), self.named("C")
        self.answer(concept_commands.replace_parents(self.db, self.ctx, b, [a]), {"id": "<C B>", "parents": [{"id": "<C A>", "name": "A"}]})
        # Parents are kept in name order, each once.
        self.answer(concept_commands.replace_parents(self.db, self.ctx, c, [b, a, " " + a]), {
            "id": "<C C>", "parents": [{"id": "<C A>", "name": "A"}, {"id": "<C B>", "name": "B"}]})
        cycle = ("concept_cycle", "That parent would create a Concept hierarchy cycle.", 409)
        for parents, expected in (([c], cycle), ([a], cycle),
                                  ([_MISSING_CONCEPT], ("parent_not_found", "Parent Concept not found.", 404)),
                                  ("A", ("invalid_parents", "Parents must be a JSON array.", 400)),
                                  ([""], ("invalid_parents", "Each parent id must be a string.", 400)),
                                  ([3], ("invalid_parents", "Each parent id must be a string.", 400))):
            with self.subTest(parents=parents):
                self.assertEqual(self.refusal(partial(concept_commands.replace_parents, self.db, self.ctx), a, parents), expected)
        self.assertEqual(self.refusal(partial(concept_commands.replace_parents, self.db, self.ctx), _MISSING_CONCEPT, []), _NOT_FOUND)
        self.answer(concept_commands.detail(self.db, self.ctx, a), {"children": [{"id": "<C B>", "name": "B"}, {"id": "<C C>", "name": "C"}],
                                                  "parents": []})
        self.answer(concept_commands.detail(self.db, self.ctx, b), {"children": [{"id": "<C C>", "name": "C"}],
                                                  "parents": [{"id": "<C A>", "name": "A"}]})
        # The edges of a Concept's children are its scope too: A moved on with B's and C's changes.
        self.answer([concept_commands.sync_state(self.db, self.ctx, concept) for concept in (a, b, c)], [
            concept_state("<C A>", "A", parents_revision=2), concept_state("<C B>", "B", parent_ids=["<C A>"], parents_revision=2),
            concept_state("<C C>", "C", parent_ids=["<C A>", "<C B>"], parents_revision=1)])
        self.answer(concept_commands.catalog(self.db, self.ctx), [{"id": "<C A>", "subconcept_count": 2}, {"id": "<C B>"},
                                                   {"id": "<C C>"}])

    def test_delete(self) -> None:
        a, b, c = self.named("A"), self.named("B"), self.named("C")
        concept_commands.replace_parents(self.db, self.ctx, b, [a])
        concept_commands.replace_parents(self.db, self.ctx, c, [b])
        work = self.library.add_work("W")
        self.research_note(work, "On [[concept:b]].")
        self.assertEqual(self.refusal(partial(concept_commands.delete, self.db, self.ctx), b), (
            "concept_in_use", "This Concept is still referenced in research notes. Remove or replace those"
            " references before deleting it.", 409))
        self.research_note(work, "On `[[concept:b]]`.")  # code, not a reference
        self.assertIsNone(concept_commands.delete(self.db, self.ctx, b))
        for concept_id in (b, " "):
            self.assertEqual(self.refusal(partial(concept_commands.delete, self.db, self.ctx), concept_id), _NOT_FOUND)
        self.answer([concept_commands.detail(self.db, self.ctx, a), concept_commands.detail(self.db, self.ctx, c)], [
            {"id": "<C A>", "children": [], "parents": []}, {"id": "<C C>", "children": [], "parents": []}])
        self.answer([concept_commands.sync_state(self.db, self.ctx, a), concept_commands.sync_state(self.db, self.ctx, c)], [
            concept_state("<C A>", "A", parents_revision=2), concept_state("<C C>", "C", parents_revision=2)])
        self.answer(concept_commands.catalog(self.db, self.ctx), [{"id": "<C A>", "subconcept_count": 0},
                                                   {"id": "<C C>", "subconcept_count": 0}])

