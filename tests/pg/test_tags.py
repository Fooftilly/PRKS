"""Tags, aliases, delete, merge and Work/Folder tags on PostgreSQL: what parity cannot show (L-O-1 #633).

``tests/test_tags_parity.py`` proves the facade returns SQLite's JSON.
These tests cover what only PostgreSQL has: identity keys (Unicode names
that are one name, or two), the shared ``tag_keys`` namespace, refusals of
what PostgreSQL text cannot hold, the lifecycle and its flattening,
tombstones, integrity after every destructive command, Library isolation,
and concurrent writers (each race holds one transaction open until the
other is waiting on it).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from typing import Any

from backend.domain.identity.authorization import AccessRefused
from backend.domain.identity.library_scope import LibraryScope, library_query
from backend.domain.organization import lifecycle, links, tags
from backend.domain.works import commands
from backend.entity_ids import generate
from backend.pg_facade import context as facade_context
from tests.pg.test_people_facade import PeopleFacadeTestCase


class OrganizationTestCase(PeopleFacadeTestCase):
    hold_serializable = False

    def run_command(self, fn: Callable[[LibraryScope], Any], *, serializable: bool = False) -> Any:
        return super().run_command(fn, serializable=serializable or self.hold_serializable)

    def race(self, holder: Callable[[LibraryScope], Any], contender: Callable[[], Any], *,
             serializable: bool = False) -> tuple[Any, Any]:
        """``PeopleFacadeTestCase.race``, the holder SERIALIZABLE when its command is (§7.1)."""
        self.hold_serializable = serializable
        try:
            return super().race(holder, contender)
        finally:
            self.hold_serializable = False

    def folder(self, title: str = "Folder") -> str:
        return self.library.add_folder(title)

    def tag(self, name: str) -> str:
        return self.library.add_tag(name)["id"]

    def lifecycle_of(self, tag_id: str) -> dict[str, Any]:
        return library_query(self.db, self.actor.context, lambda scope: lifecycle.resolve(scope, tag_id))

    def keys(self) -> list[tuple[str, str, bool]]:
        rows = self.sql("SELECT key, tag_id, alias_id IS NULL AS is_name FROM prks.tag_keys ORDER BY key")
        return [(row["key"], row["tag_id"], row["is_name"]) for row in rows]

    def assert_integrity(self) -> None:
        """One key row per name and alias, each naming its holder; no link to a gone Tag."""
        names = self.sql("SELECT count(*) AS n FROM prks.tags")[0]["n"]
        aliases = self.sql("SELECT count(*) AS n FROM prks.tag_aliases")[0]["n"]
        self.assertEqual(len(self.keys()), names + aliases)
        self.assertEqual(self.sql("""
            SELECT count(*) AS n FROM prks.tag_keys k
            LEFT JOIN prks.tags t ON t.id = k.tag_id AND k.alias_id IS NULL AND t.name_key = k.key
            LEFT JOIN prks.tag_aliases a ON a.id = k.alias_id AND a.tag_id = k.tag_id AND a.alias_key = k.key
            WHERE t.id IS NULL AND a.id IS NULL""")[0]["n"], 0)
        self.assertEqual(self.sql("""
            SELECT count(*) AS n FROM prks.tags t
            LEFT JOIN prks.sync_tag_lifecycle l ON l.tag_id = t.id AND l.state = 'active'
            WHERE l.tag_id IS NULL""")[0]["n"], 0)
        # A merged lifecycle points at an active Tag or a deleted one, never along a chain.
        self.assertEqual(self.sql("""
            SELECT count(*) AS n FROM prks.sync_tag_lifecycle l
            JOIN prks.sync_tag_lifecycle t ON t.tag_id = l.target_tag_id
            WHERE l.state = 'merged' AND t.state = 'merged'""")[0]["n"], 0)


class IdentityTests(OrganizationTestCase):
    def test_names_compare_by_identity_key(self) -> None:
        logos = self.library.add_tag("Λόγος")
        self.assertEqual(self.library.add_tag("ΛΌΓΟΣ")["id"], logos["id"])
        strasse = self.tag("Straße")
        self.assertEqual(self.library.add_tag("  STRASSE ")["id"], strasse)
        composed = self.tag(unicodedata.normalize("NFC", "Café"))
        self.assertEqual(self.tag(unicodedata.normalize("NFD", "CAFÉ")), composed)
        # Diacritics are not folded (§8.1).
        self.assertNotEqual(self.tag("Resume"), self.tag("Résumé"))
        self.assertEqual(self.count("tags"), 5)
        self.assertEqual(self.library.resolve_tag_id_by_label("λόγος"), logos["id"])
        self.assertIsNone(self.library.resolve_tag_id_by_label("Logos"))
        self.assert_integrity()

    def test_an_existing_name_takes_the_colour_given(self) -> None:
        first = self.library.add_tag("Ethics", "#111111")
        again = self.library.add_tag("ETHICS", "#222222")
        self.assertEqual(again, {"id": first["id"], "name": "Ethics", "color": "#222222", "existed": True})
        self.assertEqual(self.library.add_tag("Blank", "  ")["color"], tags.DEFAULT_COLOR)

    def test_names_and_aliases_share_one_namespace(self) -> None:
        ethics, logic = self.tag("Ethics"), self.tag("Logic")
        self.library.add_tag_alias(ethics, "Morals")
        self.library.add_tag_alias(ethics, "MORALS")  # its own alias again: a no-op
        refusals = {
            (ethics, "ETHICS"): "alias matches canonical tag name",
            (ethics, "logic"): "alias conflicts with another tag name",
            (logic, "morals"): "alias already used",
            (generate("T"), "Anything"): "tag not found",
            (ethics, "   "): "alias is empty",
        }
        for (tag_id, alias), message in refusals.items():
            with self.subTest(alias=alias), self.assertRaises(ValueError) as caught:
                self.library.add_tag_alias(tag_id, alias)
            self.assertEqual(str(caught.exception), message)
        # A name that is an alias is that alias's Tag.
        self.assertEqual(self.library.add_tag("morals")["id"], ethics)
        self.assertEqual(self.library.resolve_tag_id_by_label("Morals"), ethics)
        self.assertEqual(self.library.get_all_tags()[0]["aliases"], ["Morals"])
        self.assertTrue(self.library.delete_tag_alias(ethics, "MORALS"))
        self.assertFalse(self.library.delete_tag_alias(ethics, "Morals"))
        self.assertIsNone(self.library.resolve_tag_id_by_label("Morals"))
        self.assert_integrity()

    def test_what_postgresql_cannot_hold_or_the_schema_bounds_is_refused_before_any_write(self) -> None:
        ethics = self.tag("Ethics")
        for call, args in (("add_tag", ("a\x00b",)), ("add_tag", ("x" * 201,)),
                           ("add_tag_alias", (ethics, "a\x00b")), ("add_tag_alias", (ethics, "é" * 101))):
            with self.subTest(call=call, length=len(args[-1])), self.assertRaises(ValueError):
                getattr(self.library, call)(*args)
        self.assertEqual(self.library.add_tag("é" * 100)["name"], "é" * 100)  # 200 bytes
        self.assertFalse(self.library.delete_tag_alias(ethics, "a\x00b"))
        self.assertIsNone(self.library.resolve_tag_id_by_label("a\x00b"))
        self.assertEqual(self.count("tag_aliases"), 0)


class LinkTests(OrganizationTestCase):
    def test_links_advance_their_scopes_and_no_op_edits_leave_no_row(self) -> None:
        work, folder, tag = self.library.add_work("W"), self.folder(), self.tag("Ethics")
        self.library.remove_tag_from_work(work, tag)
        self.library.add_tag_to_work(work, tag)
        self.library.add_tag_to_work(work, tag)
        self.library.add_tag_to_folder(folder, tag)
        self.library.remove_tag_from_folder(folder, tag)
        self.assertEqual(self.revisions("work-tag"), {links.WORK.scope_id(work, tag): 1})
        self.assertEqual(self.revisions("folder-tag"), {links.FOLDER.scope_id(folder, tag): 2})
        self.assertEqual(self.library.get_work_tag_options(work), {
            "work_id": work, "assigned": [{"tag_id": tag, "relation_revision": 1}], "known_absent": {}})
        self.assertEqual(self.library.get_folder_tag_options(folder),
                         {"folder_id": folder, "assigned": [], "known_absent": {tag: 2}})
        self.assertEqual([row["id"] for row in self.library.get_work_tags(work)], [tag])
        self.assertEqual(self.library.get_work(work)["tags"], self.library.get_work_tags(work))
        self.assertIsNone(self.library.get_work_tag_options(generate("W")))
        self.assertIsNone(self.library.get_folder_tag_options(generate("F")))

    def test_a_link_needs_its_work_folder_and_tag(self) -> None:
        work, folder, tag = self.library.add_work("W"), self.folder(), self.tag("Ethics")
        for call, args, message in (("add_tag_to_work", (generate("W"), tag), "work not found"),
                                    ("add_tag_to_work", (work, generate("T")), "tag not found"),
                                    ("add_tag_to_folder", (generate("F"), tag), "folder not found"),
                                    ("add_tag_to_folder", (folder, generate("T")), "tag not found")):
            with self.subTest(call=call, message=message), self.assertRaises(ValueError) as caught:
                getattr(self.library, call)(*args)
            self.assertEqual(str(caught.exception), message)
        self.library.remove_tag_from_work(generate("W"), tag)  # nothing to remove
        self.assertEqual(self.count("work_tags") + self.count("folder_tags"), 0)
        self.assertEqual(self.revisions("work-tag"), {})

    def test_a_work_delete_takes_its_links_and_tombstones_their_scopes(self) -> None:
        work, tag = self.library.add_work("W"), self.tag("Ethics")
        self.library.add_tag_to_work(work, tag)
        self.library.remove_tag_from_work(work, tag)
        other = self.tag("Logic")
        self.library.add_tag_to_work(work, other)
        self.assertIsNotNone(self.library.delete_work_record(work))
        self.assertEqual(self.revisions("work-tag"), {links.WORK.scope_id(work, tag): 3,
                                                      links.WORK.scope_id(work, other): 2})
        self.assertEqual(self.count("work_tags"), 0)
        self.assertEqual(self.library.get_tags_in_use(), [])


class DeleteTests(OrganizationTestCase):
    def test_a_delete_removes_links_aliases_and_keys_and_keeps_the_lifecycle(self) -> None:
        work, folder, tag = self.library.add_work("W"), self.folder(), self.tag("Ethics")
        unlinked = self.library.add_work("Unlinked")
        self.library.add_tag_alias(tag, "Morals")
        self.library.add_tag_to_work(work, tag)
        self.library.add_tag_to_folder(folder, tag)
        self.library.add_tag_to_work(unlinked, tag)
        self.library.remove_tag_from_work(unlinked, tag)
        result = self.library.delete_tag(tag)
        self.assertEqual(result, {
            "status": "deleted", "affected_work_ids": [work], "affected_folder_ids": [folder],
            "affected_tag_options_work_ids": sorted([work, unlinked]),
            "affected_tag_options_folder_ids": [folder]})
        self.assertEqual(self.revisions("work-tag"), {links.WORK.scope_id(work, tag): 2,
                                                      links.WORK.scope_id(unlinked, tag): 2})
        self.assertEqual(self.revisions("folder-tag"), {links.FOLDER.scope_id(folder, tag): 2})
        self.assertEqual(self.lifecycle_of(tag), {"state": lifecycle.DELETED})
        self.assertEqual((self.count("tags"), self.count("tag_aliases"), self.keys()), (0, 0, []))
        with self.assertRaises(ValueError) as caught:
            self.library.delete_tag(tag)
        self.assertEqual(str(caught.exception), "tag not found")
        # The name is free again, under a new identity.
        self.assertNotEqual(self.tag("ethics"), tag)
        self.assert_integrity()


class MergeTests(OrganizationTestCase):
    def test_a_merge_moves_links_aliases_and_the_name(self) -> None:
        source, target = self.tag("Moral philosophy"), self.tag("Ethics")
        self.library.add_tag_alias(source, "Morals")
        self.library.add_tag_alias(target, "Ethik")
        only_source, both = self.library.add_work("Only"), self.library.add_work("Both")
        folder = self.folder()
        for work in (only_source, both):
            self.library.add_tag_to_work(work, source)
        self.library.add_tag_to_work(both, target)
        self.library.add_tag_to_folder(folder, source)
        result = self.library.merge_tags_into(source, target)
        self.assertEqual(result, {
            "canonical_tag_id": target, "canonical_name": "Ethics",
            "affected_work_ids": sorted([only_source, both]), "affected_folder_ids": [folder],
            "affected_tag_options_work_ids": sorted([only_source, both]),
            "affected_tag_options_folder_ids": [folder]})
        self.assertEqual(self.revisions("work-tag"), {
            links.WORK.scope_id(only_source, source): 2, links.WORK.scope_id(only_source, target): 1,
            links.WORK.scope_id(both, source): 2, links.WORK.scope_id(both, target): 1})
        self.assertEqual(self.revisions("folder-tag"), {links.FOLDER.scope_id(folder, source): 2,
                                                        links.FOLDER.scope_id(folder, target): 1})
        [ethics] = self.library.get_all_tags()
        self.assertEqual(ethics["aliases"], ["Ethik", "Moral philosophy", "Morals"])
        self.assertEqual(self.library.resolve_tag_id_by_label("moral PHILOSOPHY"), target)
        self.assertEqual(self.lifecycle_of(source), {"state": lifecycle.MERGED, "target_tag_id": target})
        self.assertEqual(self.library.get_tags_in_use()[0]["work_count"], 2)
        self.assert_integrity()

    def test_merges_flatten_their_chains(self) -> None:
        first, second, third = self.tag("A"), self.tag("B"), self.tag("C")
        self.library.merge_tags_into(first, second)
        self.library.merge_tags_into(second, third)
        rows = self.sql("SELECT tag_id, target_tag_id FROM prks.sync_tag_lifecycle WHERE state = 'merged'")
        self.assertEqual({row["tag_id"]: row["target_tag_id"] for row in rows}, {first: third, second: third})
        self.assertEqual(self.lifecycle_of(first), {"state": lifecycle.MERGED, "target_tag_id": third})
        self.library.delete_tag(third)
        self.assertEqual(self.lifecycle_of(first), {"state": lifecycle.DELETED})
        self.assert_integrity()

    def test_refusals_name_sqlites_messages(self) -> None:
        tag = self.tag("A")
        for source, target, message in (("", tag, "source_tag_id and target_tag_id are required"),
                                        (tag, tag, "cannot merge a tag into itself"),
                                        (tag, generate("T"), "tag not found"),
                                        (generate("T"), tag, "tag not found")):
            with self.subTest(message=message), self.assertRaises(ValueError) as caught:
                self.library.merge_tags_into(source, target)
            self.assertEqual(str(caught.exception), message)

    def test_a_lifecycle_cycle_is_a_server_failure(self) -> None:
        first, second = self.tag("A"), self.tag("B")
        self.library.merge_tags_into(first, second)
        # No constraint forbids a cycle; only the merge command's flattening does.
        self.sql("UPDATE prks.sync_tag_lifecycle SET state = 'merged', target_tag_id = %s WHERE tag_id = %s",
                 (first, second))
        with self.assertRaises(RuntimeError):
            self.lifecycle_of(first)


class IsolationTests(OrganizationTestCase):
    def test_another_librarys_tags_are_invisible_and_untouchable(self) -> None:
        tag, work = self.tag("Ethics"), self.library.add_work("W")
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            self.assertEqual(self.library.get_all_tags(), [])
            self.assertIsNone(self.library.resolve_tag_id_by_label("Ethics"))
            theirs = self.library.add_tag("Ethics")  # the namespace is per Library
            self.assertFalse(theirs["existed"])
            their_work = self.library.add_work("Theirs")
            for call, args in (("add_tag_to_work", (their_work, tag)), ("add_tag_to_work", (work, theirs["id"])),
                               ("add_tag_alias", (tag, "x")), ("delete_tag", (tag,)),
                               ("merge_tags_into", (theirs["id"], tag))):
                with self.subTest(call=call), self.assertRaises(ValueError):
                    getattr(self.library, call)(*args)
            self.assertFalse(self.library.delete_tag_alias(tag, "x"))
            self.assertIsNone(self.library.get_work_tag_options(work))
        self.assertEqual([row["id"] for row in self.library.get_all_tags()], [tag])
        self.assertEqual(self.lifecycle_of(theirs["id"]), {"state": lifecycle.UNKNOWN})

    def test_a_reader_reads_tags_but_cannot_change_them(self) -> None:
        tag, work = self.tag("Ethics"), self.library.add_work("W")
        with facade_context.acting_as(self.reader_context()):
            self.assertEqual(len(self.library.get_all_tags()), 1)
            for call, args in (("add_tag", ("New",)), ("add_tag_to_work", (work, tag)), ("delete_tag", (tag,))):
                with self.subTest(call=call), self.assertRaises(AccessRefused):
                    getattr(self.library, call)(*args)


class ConcurrencyTests(OrganizationTestCase):
    def test_two_creates_of_one_name_make_one_tag(self) -> None:
        tag_id = generate("T")
        holder, contender = self.race(lambda scope: tags.add_tag(scope, tag_id, "Λόγος", "#111111"),
                                      lambda: self.library.add_tag("ΛΌΓΟΣ"), serializable=True)
        self.assertFalse(holder["existed"])
        self.assertEqual((contender["id"], contender["existed"]), (tag_id, True))
        self.assertEqual(self.count("tags"), 1)
        self.assert_integrity()

    def test_an_alias_racing_a_create_of_the_same_name(self) -> None:
        ethics = self.tag("Ethics")
        holder, contender = self.race(lambda scope: tags.add_alias(scope, generate("L"), ethics, "Morals"),
                                      lambda: self.library.add_tag("morals"), serializable=True)
        self.assertIsNone(holder)
        self.assertEqual((contender["id"], contender["existed"]), (ethics, True))
        self.assertEqual(self.count("tags"), 1)
        self.assert_integrity()

    def test_a_create_racing_an_alias_of_the_same_name(self) -> None:
        ethics = self.tag("Ethics")
        holder, contender = self.race(lambda scope: tags.add_tag(scope, generate("T"), "Morals", "#111111"),
                                      lambda: self.library.add_tag_alias(ethics, "MORALS"), serializable=True)
        self.assertFalse(holder["existed"])
        self.assertIsInstance(contender, ValueError)
        self.assertEqual(str(contender), "alias conflicts with another tag name")
        self.assertEqual(self.count("tag_aliases"), 0)
        self.assert_integrity()

    def test_a_link_to_the_source_waits_for_the_merge_and_is_refused(self) -> None:
        source, target, work = self.tag("A"), self.tag("B"), self.library.add_work("W")
        holder, contender = self.race(lambda scope: tags.merge(scope, source, target, generate("L")),
                                      lambda: self.library.add_tag_to_work(work, source), serializable=True)
        self.assertEqual(holder["canonical_tag_id"], target)
        self.assertIsInstance(contender, ValueError)
        self.assertEqual(self.count("work_tags"), 0)
        self.assert_integrity()

    def merge_after_link(self, *, serializable: bool) -> None:
        source, target, work = self.tag("A"), self.tag("B"), self.library.add_work("W")

        def link(scope: LibraryScope) -> None:
            links.edit(scope, links.WORK, work, source, True)

        holder, contender = self.race(link, lambda: self.library.merge_tags_into(source, target),
                                      serializable=serializable)
        self.assertIsNone(holder)
        self.assertEqual(contender["affected_work_ids"], [work])
        self.assertEqual([row["id"] for row in self.library.get_work_tags(work)], [target])
        self.assertEqual(self.revisions("work-tag"), {links.WORK.scope_id(work, source): 2,
                                                      links.WORK.scope_id(work, target): 1})

    def test_a_merge_waits_for_a_link_to_the_source_and_moves_it(self) -> None:
        # The link SERIALIZABLE, as every link add is: the merge's snapshot
        # missed it, so it retries on 40001.
        self.merge_after_link(serializable=True)

    def test_a_merge_reruns_on_the_key_after_a_read_committed_link(self) -> None:
        # No PRKS command adds a link READ COMMITTED; if one did, the link's
        # key would refuse the source's delete and the merge would run again.
        self.merge_after_link(serializable=False)

    def test_a_create_of_the_source_name_waits_for_the_merge(self) -> None:
        source, target = self.tag("A"), self.tag("B")
        holder, contender = self.race(lambda scope: tags.merge(scope, source, target, generate("L")),
                                      lambda: self.library.add_tag("a"), serializable=True)
        self.assertEqual(holder["canonical_tag_id"], target)
        self.assertEqual((contender["id"], contender["existed"]), (target, True))
        self.assert_integrity()

    def test_crossed_merges_leave_one_tag(self) -> None:
        first, second = self.tag("A"), self.tag("B")
        holder, contender = self.race(lambda scope: tags.merge(scope, first, second, generate("L")),
                                      lambda: self.library.merge_tags_into(second, first), serializable=True)
        self.assertEqual(holder["canonical_tag_id"], second)
        self.assertIsInstance(contender, ValueError)
        self.assertEqual([row["id"] for row in self.library.get_all_tags()], [second])
        self.assert_integrity()

    def test_a_link_waits_for_the_delete_and_is_refused(self) -> None:
        tag, work = self.tag("A"), self.library.add_work("W")
        holder, contender = self.race(lambda scope: tags.delete_tag(scope, tag),
                                      lambda: self.library.add_tag_to_work(work, tag))
        self.assertTrue(holder[0])
        self.assertIsInstance(contender, ValueError)
        self.assertEqual(self.count("work_tags"), 0)

    def linked(self) -> tuple[str, str, str]:
        tag, work = self.tag("Ethics"), self.library.add_work("W")
        self.library.add_tag_to_work(work, tag)
        return tag, work, links.WORK.scope_id(work, tag)

    def test_a_work_delete_waits_for_a_tag_delete(self) -> None:
        tag, work, scope_id = self.linked()
        holder, contender = self.race(lambda scope: tags.delete_tag(scope, tag),
                                      lambda: self.library.delete_work_record(work))
        self.assertTrue(holder[0])
        self.assertIsNotNone(contender)
        # The removal advanced the scope, then the tombstone.
        self.assertEqual(self.revisions("work-tag"), {scope_id: 3})

    def test_a_tag_delete_waits_for_a_work_delete(self) -> None:
        tag, work, scope_id = self.linked()
        holder, contender = self.race(lambda scope: commands.delete_work(scope, work),
                                      lambda: self.library.delete_tag(tag))
        self.assertIsNotNone(holder)
        # The link went with its Work; the Tag delete found nothing to remove.
        self.assertEqual(contender["affected_work_ids"], [])
        self.assertEqual(contender["affected_tag_options_work_ids"], [work])
        self.assertEqual(self.revisions("work-tag"), {scope_id: 2})
        self.assertEqual(self.count("work_tags"), 0)
