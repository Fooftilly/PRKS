"""Tags, aliases, delete, merge and Work/Folder tags on PostgreSQL (L-O-1 #633).

:class:`AnswerTests` spells out the JSON each command answers. The rest
cover identity keys (Unicode names
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
from backend.domain.organization import lifecycle, links, tag_commands, tags
from backend.domain.works import commands
from backend.domain.entity_ids import generate
from tests import acting_context as facade_context
from tests.pg.test_people_facade import PeopleFacadeTestCase
from backend.web.etags import etag_for_representation
from tests.narrowing import require
from tests.pg.answers import AnswerAssertions
from backend.domain.organization import folder_commands
from backend.domain.works import work_commands
from tests import works_fixtures


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
        return folder_commands.create(self.db, self.ctx, title)

    def tag(self, name: str) -> str:
        return tag_commands.create(self.db, self.ctx, name)["id"]

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
        logos = tag_commands.create(self.db, self.ctx, "Λόγος")
        self.assertEqual(tag_commands.create(self.db, self.ctx, "ΛΌΓΟΣ")["id"], logos["id"])
        strasse = self.tag("Straße")
        self.assertEqual(tag_commands.create(self.db, self.ctx, "  STRASSE ")["id"], strasse)
        composed = self.tag(unicodedata.normalize("NFC", "Café"))
        self.assertEqual(self.tag(unicodedata.normalize("NFD", "CAFÉ")), composed)
        # Diacritics are not folded (§8.1).
        self.assertNotEqual(self.tag("Resume"), self.tag("Résumé"))
        self.assertEqual(self.count("tags"), 5)
        self.assertEqual(library_query(self.db, self.ctx, lambda scope: tags.resolve_label(scope, "λόγος")), logos["id"])
        self.assertIsNone(library_query(self.db, self.ctx, lambda scope: tags.resolve_label(scope, "Logos")))
        self.assert_integrity()

    def test_an_existing_name_takes_the_colour_given(self) -> None:
        first = tag_commands.create(self.db, self.ctx, "Ethics", "#111111")
        again = tag_commands.create(self.db, self.ctx, "ETHICS", "#222222")
        self.assertEqual(again, {"id": first["id"], "name": "Ethics", "color": "#222222", "existed": True})
        self.assertEqual(tag_commands.create(self.db, self.ctx, "Blank", "  ")["color"], tags.DEFAULT_COLOR)

    def test_untrusted_tag_color_is_rejected_on_postgresql(self) -> None:
        for value in ("red;position:fixed", "x;background:url(https://example.test)",
                      "#12345", "javascript:alert(1)"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                tag_commands.create(self.db, self.ctx, "Untrusted", value)
        self.assertEqual(self.count("tags"), 0)
        self.assertEqual(tag_commands.create(self.db, self.ctx, "Safe", "#AABBCC")["color"], "#AABBCC")

    def test_names_and_aliases_share_one_namespace(self) -> None:
        ethics, logic = self.tag("Ethics"), self.tag("Logic")
        tag_commands.add_alias(self.db, self.ctx, ethics, "Morals")
        tag_commands.add_alias(self.db, self.ctx, ethics, "MORALS")  # its own alias again: a no-op
        refusals = {
            (ethics, "ETHICS"): "alias matches canonical tag name",
            (ethics, "logic"): "alias conflicts with another tag name",
            (logic, "morals"): "alias already used",
            (generate("T"), "Anything"): "tag not found",
            (ethics, "   "): "alias is empty",
        }
        for (tag_id, alias), message in refusals.items():
            with self.subTest(alias=alias), self.assertRaises(ValueError) as caught:
                tag_commands.add_alias(self.db, self.ctx, tag_id, alias)
            self.assertEqual(str(caught.exception), message)
        # A name that is an alias is that alias's Tag.
        self.assertEqual(tag_commands.create(self.db, self.ctx, "morals")["id"], ethics)
        self.assertEqual(library_query(self.db, self.ctx, lambda scope: tags.resolve_label(scope, "Morals")), ethics)
        self.assertEqual(tag_commands.all_tags(self.db, self.ctx)[0]["aliases"], ["Morals"])
        self.assertTrue(tag_commands.delete_alias(self.db, self.ctx, ethics, "MORALS"))
        self.assertFalse(tag_commands.delete_alias(self.db, self.ctx, ethics, "Morals"))
        self.assertIsNone(library_query(self.db, self.ctx, lambda scope: tags.resolve_label(scope, "Morals")))
        self.assert_integrity()

    def test_what_postgresql_cannot_hold_or_the_schema_bounds_is_refused_before_any_write(self) -> None:
        ethics = self.tag("Ethics")
        for call, args in (("create", ("a\x00b",)), ("create", ("x" * 201,)),
                           ("add_alias", (ethics, "a\x00b")), ("add_alias", (ethics, "é" * 101))):
            with self.subTest(call=call, length=len(args[-1])), self.assertRaises(ValueError):
                getattr(tag_commands, call)(self.db, self.ctx, *args)
        self.assertEqual(tag_commands.create(self.db, self.ctx, "é" * 100)["name"], "é" * 100)  # 200 bytes
        self.assertFalse(tag_commands.delete_alias(self.db, self.ctx, ethics, "a\x00b"))
        self.assertIsNone(library_query(self.db, self.ctx, lambda scope: tags.resolve_label(scope, "a\x00b")))
        self.assertEqual(self.count("tag_aliases"), 0)


class LinkTests(OrganizationTestCase):
    def test_links_advance_their_scopes_and_no_op_edits_leave_no_row(self) -> None:
        work, folder, tag = works_fixtures.add_work(self.db, self.ctx, self.storage, "W"), self.folder(), self.tag("Ethics")
        tag_commands.untag_work(self.db, self.ctx, work, tag)
        tag_commands.tag_work(self.db, self.ctx, work, tag)
        tag_commands.tag_work(self.db, self.ctx, work, tag)
        tag_commands.tag_folder(self.db, self.ctx, folder, tag)
        tag_commands.untag_folder(self.db, self.ctx, folder, tag)
        self.assertEqual(self.revisions("work-tag"), {links.WORK.scope_id(work, tag): 1})
        self.assertEqual(self.revisions("folder-tag"), {links.FOLDER.scope_id(folder, tag): 2})
        self.assertEqual(tag_commands.work_options(self.db, self.ctx, work), {
            "work_id": work, "assigned": [{"tag_id": tag, "relation_revision": 1}], "known_absent": {}})
        self.assertEqual(tag_commands.folder_options(self.db, self.ctx, folder),
                         {"folder_id": folder, "assigned": [], "known_absent": {tag: 2}})
        self.assertEqual([row["id"] for row in library_query(self.db, self.ctx, lambda scope: links.tags_of(scope, links.WORK, work))], [tag])
        self.assertEqual(require(work_commands.detail(self.db, self.ctx, self.pdfs_dir, work))["tags"], library_query(self.db, self.ctx, lambda scope: links.tags_of(scope, links.WORK, work)))
        self.assertIsNone(tag_commands.work_options(self.db, self.ctx, generate("W")))
        self.assertIsNone(tag_commands.folder_options(self.db, self.ctx, generate("F")))

    def test_a_link_needs_its_work_folder_and_tag(self) -> None:
        work, folder, tag = works_fixtures.add_work(self.db, self.ctx, self.storage, "W"), self.folder(), self.tag("Ethics")
        for call, args, message in (("tag_work", (generate("W"), tag), "work not found"),
                                    ("tag_work", (work, generate("T")), "tag not found"),
                                    ("tag_folder", (generate("F"), tag), "folder not found"),
                                    ("tag_folder", (folder, generate("T")), "tag not found")):
            with self.subTest(call=call, message=message), self.assertRaises(ValueError) as caught:
                getattr(tag_commands, call)(self.db, self.ctx, *args)
            self.assertEqual(str(caught.exception), message)
        tag_commands.untag_work(self.db, self.ctx, generate("W"), tag)  # nothing to remove
        self.assertEqual(self.count("work_tags") + self.count("folder_tags"), 0)
        self.assertEqual(self.revisions("work-tag"), {})

    def test_a_work_delete_takes_its_links_and_tombstones_their_scopes(self) -> None:
        work, tag = works_fixtures.add_work(self.db, self.ctx, self.storage, "W"), self.tag("Ethics")
        tag_commands.tag_work(self.db, self.ctx, work, tag)
        tag_commands.untag_work(self.db, self.ctx, work, tag)
        other = self.tag("Logic")
        tag_commands.tag_work(self.db, self.ctx, work, other)
        self.assertIsNotNone(work_commands.delete(self.db, self.ctx, work))
        self.assertEqual(self.revisions("work-tag"), {links.WORK.scope_id(work, tag): 3,
                                                      links.WORK.scope_id(work, other): 2})
        self.assertEqual(self.count("work_tags"), 0)
        self.assertEqual(tag_commands.in_use(self.db, self.ctx), [])


    def test_scope_ids_are_structural(self) -> None:
        for link in (links.WORK, links.FOLDER):
            with self.subTest(owner=link.owner_key):
                self.assertNotEqual(link.scope_id("a:b", "c"), link.scope_id("a", "b:c"))

class DeleteTests(OrganizationTestCase):
    def test_a_delete_removes_links_aliases_and_keys_and_keeps_the_lifecycle(self) -> None:
        work, folder, tag = works_fixtures.add_work(self.db, self.ctx, self.storage, "W"), self.folder(), self.tag("Ethics")
        unlinked = works_fixtures.add_work(self.db, self.ctx, self.storage, "Unlinked")
        tag_commands.add_alias(self.db, self.ctx, tag, "Morals")
        tag_commands.tag_work(self.db, self.ctx, work, tag)
        tag_commands.tag_folder(self.db, self.ctx, folder, tag)
        tag_commands.tag_work(self.db, self.ctx, unlinked, tag)
        tag_commands.untag_work(self.db, self.ctx, unlinked, tag)
        result = tag_commands.delete(self.db, self.ctx, tag)
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
            tag_commands.delete(self.db, self.ctx, tag)
        self.assertEqual(str(caught.exception), "tag not found")
        # The name is free again, under a new identity.
        self.assertNotEqual(self.tag("ethics"), tag)
        self.assert_integrity()


    def test_a_delete_in_a_failed_command_leaves_the_tag_and_its_links(self) -> None:
        work, tag = works_fixtures.add_work(self.db, self.ctx, self.storage, "W"), self.tag("Ethics")
        tag_commands.tag_work(self.db, self.ctx, work, tag)

        class Abort(Exception):
            pass

        def delete_then_fail(scope: LibraryScope) -> None:
            self.assertEqual(tags.delete_tag(scope, tag)[0], True)
            raise Abort()

        with self.assertRaises(Abort):
            self.run_command(delete_then_fail)
        self.assertEqual(self.lifecycle_of(tag), {"state": lifecycle.ACTIVE})
        self.assertEqual([row["id"] for row in library_query(self.db, self.ctx, lambda scope: links.tags_of(scope, links.WORK, work))], [tag])
        self.assertEqual(self.revisions("work-tag"), {links.WORK.scope_id(work, tag): 1})
        self.assert_integrity()

class TagIdentityTests(OrganizationTestCase):
    """Only an explicit Delete Tag or Merge Tag destroys a Tag (the retired SQLite folder atomicity tests).

    With ``forbid_tag_deletion`` installed, a path that still tried to collect an
    "unused" Tag would raise instead of quietly succeeding.
    """

    def forbid_tag_deletion(self) -> None:
        self.sql("""
            CREATE FUNCTION prks.test_forbid_tag_delete() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'this path must not delete a tag'; END $$""")
        self.sql("CREATE TRIGGER test_forbid_tag_delete BEFORE DELETE ON prks.tags "
                 "FOR EACH ROW EXECUTE FUNCTION prks.test_forbid_tag_delete()")
        self.addCleanup(self.sql, "DROP FUNCTION IF EXISTS prks.test_forbid_tag_delete() CASCADE")

    def test_the_guard_refuses_a_tag_delete(self) -> None:
        tag = self.tag("Guarded")
        self.forbid_tag_deletion()
        with self.assertRaises(Exception):
            tag_commands.delete(self.db, self.ctx, tag)
        self.assertEqual(self.count("tags"), 1)

    def test_deleting_a_folder_keeps_its_tags(self) -> None:
        folder, tag = self.folder("Doomed"), self.tag("OnlyOnThisFolder")
        tag_commands.tag_folder(self.db, self.ctx, folder, tag)
        self.forbid_tag_deletion()
        folder_commands.delete(self.db, self.ctx, folder)
        self.assertEqual((self.count("folders"), self.count("folder_tags"), self.count("tags")), (0, 0, 1))
        self.assertEqual(self.lifecycle_of(tag), {"state": lifecycle.ACTIVE})

    def test_removing_a_tag_from_a_folder_or_a_work_keeps_the_tag(self) -> None:
        folder, work, tag = self.folder("Tagged"), works_fixtures.add_work(self.db, self.ctx, self.storage, "Tagged work"), self.tag("SoleUse")
        tag_commands.tag_folder(self.db, self.ctx, folder, tag)
        tag_commands.tag_work(self.db, self.ctx, work, tag)
        self.forbid_tag_deletion()
        tag_commands.untag_folder(self.db, self.ctx, folder, tag)
        tag_commands.untag_work(self.db, self.ctx, work, tag)
        self.assertEqual((library_query(self.db, self.ctx, lambda scope: links.tags_of(scope, links.FOLDER, folder)), library_query(self.db, self.ctx, lambda scope: links.tags_of(scope, links.WORK, work))), ([], []))
        self.assertEqual(self.count("tags"), 1)

    def test_bulk_tag_removal_keeps_the_tags(self) -> None:
        tag = self.tag("BulkTag")
        works = [works_fixtures.add_work(self.db, self.ctx, self.storage, "Bulk %d" % i) for i in range(3)]
        for work in works:
            tag_commands.tag_work(self.db, self.ctx, work, tag)
        self.forbid_tag_deletion()
        work_commands.bulk_update(self.db, self.ctx, {"action": "remove_tags", "work_ids": works, "tag_ids": [tag]})
        self.assertEqual([library_query(self.db, self.ctx, lambda scope, work=work: links.tags_of(scope, links.WORK, work)) for work in works], [[], [], []])
        self.assertEqual(self.count("tags"), 1)

    def test_deleting_a_work_keeps_its_tags(self) -> None:
        work, tag = works_fixtures.add_work(self.db, self.ctx, self.storage, "Doomed work"), self.tag("OnlyOnThisWork")
        tag_commands.tag_work(self.db, self.ctx, work, tag)
        self.forbid_tag_deletion()
        self.run_command(lambda scope: commands.delete_work(scope, work), serializable=True)
        self.assertEqual((self.count("works"), self.count("work_tags"), self.count("tags")), (0, 0, 1))

    def test_an_explicit_delete_destroys_the_tag_and_all_its_links(self) -> None:
        work, folder, tag = works_fixtures.add_work(self.db, self.ctx, self.storage, "Explicit"), self.folder("Explicit"), self.tag("Doomed")
        tag_commands.tag_work(self.db, self.ctx, work, tag)
        tag_commands.tag_folder(self.db, self.ctx, folder, tag)
        tag_commands.delete(self.db, self.ctx, tag)
        self.assertEqual((self.count("tags"), library_query(self.db, self.ctx, lambda scope: links.tags_of(scope, links.WORK, work)), library_query(self.db, self.ctx, lambda scope: links.tags_of(scope, links.FOLDER, folder))),
                         (0, [], []))

    def test_a_merge_destroys_only_the_source_tag(self) -> None:
        work, source, target = works_fixtures.add_work(self.db, self.ctx, self.storage, "Merged"), self.tag("Source"), self.tag("Target")
        tag_commands.tag_work(self.db, self.ctx, work, source)
        tag_commands.merge(self.db, self.ctx, source, target)
        self.assertEqual([row["id"] for row in tag_commands.all_tags(self.db, self.ctx)], [target])
        self.assertEqual([row["id"] for row in library_query(self.db, self.ctx, lambda scope: links.tags_of(scope, links.WORK, work))], [target])


class MergeTests(OrganizationTestCase):
    def test_a_merge_moves_links_aliases_and_the_name(self) -> None:
        source, target = self.tag("Moral philosophy"), self.tag("Ethics")
        tag_commands.add_alias(self.db, self.ctx, source, "Morals")
        tag_commands.add_alias(self.db, self.ctx, target, "Ethik")
        only_source, both = works_fixtures.add_work(self.db, self.ctx, self.storage, "Only"), works_fixtures.add_work(self.db, self.ctx, self.storage, "Both")
        folder = self.folder()
        for work in (only_source, both):
            tag_commands.tag_work(self.db, self.ctx, work, source)
        tag_commands.tag_work(self.db, self.ctx, both, target)
        tag_commands.tag_folder(self.db, self.ctx, folder, source)
        result = tag_commands.merge(self.db, self.ctx, source, target)
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
        [ethics] = tag_commands.all_tags(self.db, self.ctx)
        self.assertEqual(ethics["aliases"], ["Ethik", "Moral philosophy", "Morals"])
        self.assertEqual(library_query(self.db, self.ctx, lambda scope: tags.resolve_label(scope, "moral PHILOSOPHY")), target)
        self.assertEqual(self.lifecycle_of(source), {"state": lifecycle.MERGED, "target_tag_id": target})
        self.assertEqual(tag_commands.in_use(self.db, self.ctx)[0]["work_count"], 2)
        self.assert_integrity()

    def test_merges_flatten_their_chains(self) -> None:
        first, second, third = self.tag("A"), self.tag("B"), self.tag("C")
        tag_commands.merge(self.db, self.ctx, first, second)
        tag_commands.merge(self.db, self.ctx, second, third)
        rows = self.sql("SELECT tag_id, target_tag_id FROM prks.sync_tag_lifecycle WHERE state = 'merged'")
        self.assertEqual({row["tag_id"]: row["target_tag_id"] for row in rows}, {first: third, second: third})
        self.assertEqual(self.lifecycle_of(first), {"state": lifecycle.MERGED, "target_tag_id": third})
        tag_commands.delete(self.db, self.ctx, third)
        self.assertEqual(self.lifecycle_of(first), {"state": lifecycle.DELETED})
        self.assert_integrity()

    def test_refusals_name_sqlites_messages(self) -> None:
        tag = self.tag("A")
        for source, target, message in (("", tag, "source_tag_id and target_tag_id are required"),
                                        (tag, tag, "cannot merge a tag into itself"),
                                        (tag, generate("T"), "tag not found"),
                                        (generate("T"), tag, "tag not found")):
            with self.subTest(message=message), self.assertRaises(ValueError) as caught:
                tag_commands.merge(self.db, self.ctx, source, target)
            self.assertEqual(str(caught.exception), message)

    def test_a_lifecycle_cycle_is_a_server_failure(self) -> None:
        first, second = self.tag("A"), self.tag("B")
        tag_commands.merge(self.db, self.ctx, first, second)
        # No constraint forbids a cycle; only the merge command's flattening does.
        self.sql("UPDATE prks.sync_tag_lifecycle SET state = 'merged', target_tag_id = %s WHERE tag_id = %s",
                 (first, second))
        with self.assertRaises(RuntimeError):
            self.lifecycle_of(first)


class IsolationTests(OrganizationTestCase):
    def test_another_librarys_tags_are_invisible_and_untouchable(self) -> None:
        tag, work = self.tag("Ethics"), works_fixtures.add_work(self.db, self.ctx, self.storage, "W")
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            self.assertEqual(tag_commands.all_tags(self.db, self.ctx), [])
            self.assertIsNone(library_query(self.db, self.ctx, lambda scope: tags.resolve_label(scope, "Ethics")))
            theirs = tag_commands.create(self.db, self.ctx, "Ethics")  # the namespace is per Library
            self.assertFalse(theirs["existed"])
            their_work = works_fixtures.add_work(self.db, self.ctx, self.storage, "Theirs")
            for call, args in (("tag_work", (their_work, tag)), ("tag_work", (work, theirs["id"])),
                               ("add_alias", (tag, "x")), ("delete", (tag,)),
                               ("merge", (theirs["id"], tag))):
                with self.subTest(call=call), self.assertRaises(ValueError):
                    getattr(tag_commands, call)(self.db, self.ctx, *args)
            self.assertFalse(tag_commands.delete_alias(self.db, self.ctx, tag, "x"))
            self.assertIsNone(tag_commands.work_options(self.db, self.ctx, work))
        self.assertEqual([row["id"] for row in tag_commands.all_tags(self.db, self.ctx)], [tag])
        self.assertEqual(self.lifecycle_of(theirs["id"]), {"state": lifecycle.UNKNOWN})

    def test_a_reader_reads_tags_but_cannot_change_them(self) -> None:
        tag, work = self.tag("Ethics"), works_fixtures.add_work(self.db, self.ctx, self.storage, "W")
        with facade_context.acting_as(self.reader_context()):
            self.assertEqual(len(tag_commands.all_tags(self.db, self.ctx)), 1)
            for call, args in (("create", ("New",)), ("tag_work", (work, tag)), ("delete", (tag,))):
                with self.subTest(call=call), self.assertRaises(AccessRefused):
                    getattr(tag_commands, call)(self.db, self.ctx, *args)


class ConcurrencyTests(OrganizationTestCase):
    def test_two_creates_of_one_name_make_one_tag(self) -> None:
        tag_id = generate("T")
        holder, contender = self.race(lambda scope: tags.add_tag(scope, tag_id, "Λόγος", "#111111"),
                                      lambda: tag_commands.create(self.db, self.ctx, "ΛΌΓΟΣ"), serializable=True)
        self.assertFalse(holder["existed"])
        self.assertEqual((contender["id"], contender["existed"]), (tag_id, True))
        self.assertEqual(self.count("tags"), 1)
        self.assert_integrity()

    def test_an_alias_racing_a_create_of_the_same_name(self) -> None:
        ethics = self.tag("Ethics")
        holder, contender = self.race(lambda scope: tags.add_alias(scope, generate("L"), ethics, "Morals"),
                                      lambda: tag_commands.create(self.db, self.ctx, "morals"), serializable=True)
        self.assertIsNone(holder)
        self.assertEqual((contender["id"], contender["existed"]), (ethics, True))
        self.assertEqual(self.count("tags"), 1)
        self.assert_integrity()

    def test_a_create_racing_an_alias_of_the_same_name(self) -> None:
        ethics = self.tag("Ethics")
        holder, contender = self.race(lambda scope: tags.add_tag(scope, generate("T"), "Morals", "#111111"),
                                      lambda: tag_commands.add_alias(self.db, self.ctx, ethics, "MORALS"), serializable=True)
        self.assertFalse(holder["existed"])
        self.assertIsInstance(contender, ValueError)
        self.assertEqual(str(contender), "alias conflicts with another tag name")
        self.assertEqual(self.count("tag_aliases"), 0)
        self.assert_integrity()

    def test_a_link_to_the_source_waits_for_the_merge_and_is_refused(self) -> None:
        source, target, work = self.tag("A"), self.tag("B"), works_fixtures.add_work(self.db, self.ctx, self.storage, "W")
        holder, contender = self.race(lambda scope: tags.merge(scope, source, target, generate("L")),
                                      lambda: tag_commands.tag_work(self.db, self.ctx, work, source), serializable=True)
        self.assertEqual(holder["canonical_tag_id"], target)
        self.assertIsInstance(contender, ValueError)
        self.assertEqual(self.count("work_tags"), 0)
        self.assert_integrity()

    def merge_after_link(self, *, serializable: bool) -> None:
        source, target, work = self.tag("A"), self.tag("B"), works_fixtures.add_work(self.db, self.ctx, self.storage, "W")

        def link(scope: LibraryScope) -> None:
            links.edit(scope, links.WORK, work, source, True)

        holder, contender = self.race(link, lambda: tag_commands.merge(self.db, self.ctx, source, target),
                                      serializable=serializable)
        self.assertIsNone(holder)
        self.assertEqual(contender["affected_work_ids"], [work])
        self.assertEqual([row["id"] for row in library_query(self.db, self.ctx, lambda scope: links.tags_of(scope, links.WORK, work))], [target])
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
                                      lambda: tag_commands.create(self.db, self.ctx, "a"), serializable=True)
        self.assertEqual(holder["canonical_tag_id"], target)
        self.assertEqual((contender["id"], contender["existed"]), (target, True))
        self.assert_integrity()

    def test_crossed_merges_leave_one_tag(self) -> None:
        first, second = self.tag("A"), self.tag("B")
        holder, contender = self.race(lambda scope: tags.merge(scope, first, second, generate("L")),
                                      lambda: tag_commands.merge(self.db, self.ctx, second, first), serializable=True)
        self.assertEqual(holder["canonical_tag_id"], second)
        self.assertIsInstance(contender, ValueError)
        self.assertEqual([row["id"] for row in tag_commands.all_tags(self.db, self.ctx)], [second])
        self.assert_integrity()

    def test_a_link_waits_for_the_delete_and_is_refused(self) -> None:
        tag, work = self.tag("A"), works_fixtures.add_work(self.db, self.ctx, self.storage, "W")
        holder, contender = self.race(lambda scope: tags.delete_tag(scope, tag),
                                      lambda: tag_commands.tag_work(self.db, self.ctx, work, tag))
        self.assertTrue(holder[0])
        self.assertIsInstance(contender, ValueError)
        self.assertEqual(self.count("work_tags"), 0)

    def linked(self) -> tuple[str, str, str]:
        tag, work = self.tag("Ethics"), works_fixtures.add_work(self.db, self.ctx, self.storage, "W")
        tag_commands.tag_work(self.db, self.ctx, work, tag)
        return tag, work, links.WORK.scope_id(work, tag)

    def test_a_work_delete_waits_for_a_tag_delete(self) -> None:
        tag, work, scope_id = self.linked()
        holder, contender = self.race(lambda scope: tags.delete_tag(scope, tag),
                                      lambda: work_commands.delete(self.db, self.ctx, work))
        self.assertTrue(holder[0])
        self.assertIsNotNone(contender)
        # The removal advanced the scope, then the tombstone.
        self.assertEqual(self.revisions("work-tag"), {scope_id: 3})

    def test_a_tag_delete_waits_for_a_work_delete(self) -> None:
        tag, work, scope_id = self.linked()
        holder, contender = self.race(lambda scope: commands.delete_work(scope, work),
                                      lambda: tag_commands.delete(self.db, self.ctx, tag))
        self.assertIsNotNone(holder)
        # The link went with its Work; the Tag delete found nothing to remove.
        self.assertEqual(contender["affected_work_ids"], [])
        self.assertEqual(contender["affected_tag_options_work_ids"], [work])
        self.assertEqual(self.revisions("work-tag"), {scope_id: 2})
        self.assertEqual(self.count("work_tags"), 0)


class AnswerTests(AnswerAssertions, OrganizationTestCase):
    """The JSON each Tag command and read answers, spelled out."""

    def create(self, name: str, color: str = "#6d6cf7") -> dict[str, Any]:
        return tag_commands.create(self.db, self.actor.context, name, color)

    def named_tag(self, name: str) -> str:
        return self.labels.name(self.create(name)["id"], f"<T {name}>")

    def named_work(self, title: str) -> str:
        return self.labels.name(works_fixtures.add_work(self.db, self.ctx, self.storage, title), f"<W {title}>")

    def named_folder(self, title: str) -> str:
        return self.labels.name(self.folder(title), f"<F {title}>")

    def test_names_aliases_and_the_catalog(self) -> None:
        db, ctx = self.db, self.actor.context
        ethics, logic = self.named_tag("Ethics"), self.named_tag("Logic")
        self.answer(self.create("ETHICS", "#123456"),
                    {"color": "#123456", "existed": True, "id": "<T Ethics>", "name": "Ethics"})
        self.assertIsNone(tag_commands.add_alias(db, ctx, ethics, "Morals"))
        self.assertIsNone(tag_commands.add_alias(db, ctx, ethics, "morals"))
        for tag, alias, message in ((ethics, "ethics", "alias matches canonical tag name"),
                                    (ethics, "LOGIC", "alias conflicts with another tag name"),
                                    (logic, "MORALS", "alias already used"),
                                    (ethics, "  ", "alias is empty"),
                                    ("T-" + "0" * 32, "x", "tag not found")):
            with self.subTest(alias=alias):
                self.assert_refused(lambda: tag_commands.add_alias(db, ctx, tag, alias), message)
        # An alias resolves to its Tag, in a create too.
        self.answer(self.create("Morals"), {"color": "#6d6cf7", "existed": True, "id": "<T Ethics>",
                                            "name": "Ethics"})
        self.answer(library_query(self.db, self.ctx, lambda scope: tags.resolve_label(scope, "MORALS")), "<T Ethics>")
        self.assertIsNone(library_query(self.db, self.ctx, lambda scope: tags.resolve_label(scope, "nothing")))
        catalog = tag_commands.all_tags(db, ctx)
        self.answer(catalog, [
            {"aliases": ["Morals"], "color": "#6d6cf7", "created_at": "<s>", "id": "<T Ethics>", "name": "Ethics"},
            {"aliases": [], "color": "#6d6cf7", "created_at": "<s>", "id": "<T Logic>", "name": "Logic"}])
        before = etag_for_representation("tags", catalog)
        self.assertRegex(before, r'^W/"prks-tags-2-[0-9a-f]+"$')
        self.assertIs(tag_commands.delete_alias(db, ctx, ethics, "MORALS"), True)
        self.assertIs(tag_commands.delete_alias(db, ctx, ethics, "Morals"), False)
        catalog = tag_commands.all_tags(db, ctx)
        self.answer([row["aliases"] for row in catalog], [[], []])
        self.assertNotEqual(etag_for_representation("tags", catalog), before)
        self.assert_refused(lambda: self.create("  "), "tag name is empty")

    def test_work_and_folder_tags_and_their_options(self) -> None:
        db, ctx = self.db, self.actor.context
        work, folder = self.named_work("W"), self.named_folder("F")
        ethics, logic = self.named_tag("Ethics"), self.named_tag("Logic")
        for link, unlink, owner in ((tag_commands.tag_work, tag_commands.untag_work, work),
                                    (tag_commands.tag_folder, tag_commands.untag_folder, folder)):
            self.assertIsNone(link(db, ctx, owner, ethics))
            self.assertIsNone(link(db, ctx, owner, logic))
            self.assertIsNone(unlink(db, ctx, owner, logic))
        # Removing an absent link is a no-op.
        self.assertIsNone(tag_commands.untag_work(db, ctx, work, logic))
        assigned = [{"relation_revision": 1, "tag_id": "<T Ethics>"}]
        self.answer(tag_commands.work_options(db, ctx, work),
                    {"assigned": assigned, "known_absent": {"<T Logic>": 2}, "work_id": "<W W>"})
        self.answer(tag_commands.folder_options(db, ctx, folder),
                    {"assigned": assigned, "folder_id": "<F F>", "known_absent": {"<T Logic>": 2}})
        row = {"color": "#6d6cf7", "created_at": "<s>", "id": "<T Ethics>", "name": "Ethics"}
        self.answer(library_query(self.db, self.ctx, lambda scope: links.tags_of(scope, links.WORK, work)), [row])
        self.answer(library_query(self.db, self.ctx, lambda scope: links.tags_of(scope, links.FOLDER, folder)), [row])
        self.answer(require(work_commands.detail(self.db, self.ctx, self.pdfs_dir, work))["tags"], [row])
        self.answer(tag_commands.in_use(db, ctx), [{**row, "aliases": [], "folder_count": 1, "work_count": 1}])
        self.assertIsNone(tag_commands.work_options(db, ctx, "W-" + "0" * 32))

    def test_delete(self) -> None:
        db, ctx = self.db, self.actor.context
        work, folder, unlinked = self.named_work("W"), self.named_folder("F"), self.named_work("U")
        ethics = self.named_tag("Ethics")
        tag_commands.add_alias(db, ctx, ethics, "Morals")
        tag_commands.tag_work(db, ctx, work, ethics)
        tag_commands.tag_folder(db, ctx, folder, ethics)
        tag_commands.tag_work(db, ctx, unlinked, ethics)
        tag_commands.untag_work(db, ctx, unlinked, ethics)
        # A Work whose link was removed still has its options refreshed.
        self.answer(tag_commands.delete(db, ctx, ethics), {
            "affected_folder_ids": ["<F F>"], "affected_tag_options_folder_ids": ["<F F>"],
            "affected_tag_options_work_ids": ["<W U>", "<W W>"], "affected_work_ids": ["<W W>"],
            "status": "deleted"})
        self.answer(tag_commands.work_options(db, ctx, work), {"assigned": [], "known_absent": {}, "work_id": "<W W>"})
        self.answer(tag_commands.work_options(db, ctx, unlinked),
                    {"assigned": [], "known_absent": {}, "work_id": "<W U>"})
        self.answer(tag_commands.folder_options(db, ctx, folder),
                    {"assigned": [], "folder_id": "<F F>", "known_absent": {}})
        self.assertEqual(tag_commands.all_tags(db, ctx), [])
        self.assertIsNone(library_query(self.db, self.ctx, lambda scope: tags.resolve_label(scope, "Morals")))
        self.assert_refused(lambda: tag_commands.delete(db, ctx, ethics), "tag not found")
        self.assert_refused(lambda: tag_commands.delete(db, ctx, ""), "tag_id is required")

    def test_merge(self) -> None:
        db, ctx = self.db, self.actor.context
        source, target = self.named_tag("Moral philosophy"), self.named_tag("Ethics")
        tag_commands.add_alias(db, ctx, source, "Morals")
        only, shared, folder = self.named_work("Only"), self.named_work("Shared"), self.named_folder("F")
        for work in (only, shared):
            tag_commands.tag_work(db, ctx, work, source)
        tag_commands.tag_work(db, ctx, shared, target)
        tag_commands.tag_folder(db, ctx, folder, source)
        self.answer(tag_commands.merge(db, ctx, source, target), {
            "affected_folder_ids": ["<F F>"], "affected_tag_options_folder_ids": ["<F F>"],
            "affected_tag_options_work_ids": ["<W Only>", "<W Shared>"],
            "affected_work_ids": ["<W Only>", "<W Shared>"],
            "canonical_name": "Ethics", "canonical_tag_id": "<T Ethics>"})
        assigned = [{"relation_revision": 1, "tag_id": "<T Ethics>"}]
        for work, label in ((only, "<W Only>"), (shared, "<W Shared>")):
            self.answer(tag_commands.work_options(db, ctx, work),
                        {"assigned": assigned, "known_absent": {}, "work_id": label})
        self.answer(tag_commands.folder_options(db, ctx, folder),
                    {"assigned": assigned, "folder_id": "<F F>", "known_absent": {}})
        row = {"aliases": ["Moral philosophy", "Morals"], "color": "#6d6cf7", "created_at": "<s>",
               "id": "<T Ethics>", "name": "Ethics"}
        self.answer(tag_commands.all_tags(db, ctx), [row])
        self.answer(tag_commands.in_use(db, ctx), [{**row, "folder_count": 1, "work_count": 2}])
