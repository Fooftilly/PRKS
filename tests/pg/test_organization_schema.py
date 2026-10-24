"""Constraints of the Organization schema (L-O-1 #633; ``120_organization.sql``).

Each rule the schema owns is shown refused and its legal path shown to
commit: the one name-and-alias namespace of Tags (``tag_keys``), including
under concurrency and across a merge, the Tag lifecycle, per-parent Folder
identity with root folders in one scope (``NULLS NOT DISTINCT``), one folder
and one playlist per Work, Saved View definitions and names, the settings
split, and Library isolation of every reference. Keys of rows that own
revision scopes never cascade a delete, except the listed Work-Tag cascade,
which the Work delete tombstones.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, Optional

import psycopg

from tests.pg.test_people_schema import PeopleSchemaTestCase, new_id
from tests.pg.test_works_facade import WorksFacadeTestCase

Conn = Optional[psycopg.Connection[Any]]


class OrganizationSchemaTestCase(PeopleSchemaTestCase):
    # -- Tags --------------------------------------------------------------

    def create_tag(self, name: str, *, lib: Optional[str] = None, conn: Conn = None) -> str:
        """A Tag, its name's key row and its lifecycle row, in the caller's transaction."""
        tag_id = new_id("T")
        self.sql("INSERT INTO prks.tags (library_id, id, name) VALUES (%s, %s, %s)",
                 (lib or self.lib, tag_id, name), conn)
        self.sql("INSERT INTO prks.tag_keys (library_id, key, tag_id) "
                 "SELECT library_id, name_key, id FROM prks.tags WHERE id = %s", (tag_id,), conn)
        self.sql("INSERT INTO prks.sync_tag_lifecycle (library_id, tag_id, state) VALUES (%s, %s, 'active')",
                 (lib or self.lib, tag_id), conn)
        return tag_id

    def add_alias(self, tag_id: str, alias: str, conn: Conn = None) -> str:
        alias_id = new_id("L")
        self.sql("INSERT INTO prks.tag_aliases (library_id, id, tag_id, alias) "
                 "SELECT library_id, %s, id, %s FROM prks.tags WHERE id = %s", (alias_id, alias, tag_id), conn)
        self.sql("INSERT INTO prks.tag_keys (library_id, key, tag_id, alias_id) "
                 "SELECT library_id, alias_key, tag_id, id FROM prks.tag_aliases WHERE id = %s", (alias_id,), conn)
        return alias_id

    def tag(self, name: str, *aliases: str, lib: Optional[str] = None) -> str:
        with self.tx():
            tag_id = self.create_tag(name, lib=lib)
            for alias in aliases:
                self.add_alias(tag_id, alias)
        return tag_id

    def keys(self, tag_id: str) -> list[tuple[str, str]]:
        return [(row[0], row[1]) for row in self.sql(
            "SELECT kind, key FROM prks.tag_keys WHERE tag_id = %s ORDER BY kind, key", (tag_id,)).fetchall()]

    def assert_refused(self, error: type[psycopg.Error], body: Callable[[], object]) -> None:
        with self.assertRaises(error):
            with self.tx():
                body()

    # -- Folders, playlists, Saved Views -----------------------------------

    def folder(self, title: str, *, parent_id: Optional[str] = None, lib: Optional[str] = None,
               conn: Conn = None) -> str:
        folder_id = new_id("F")
        self.sql("INSERT INTO prks.folders (library_id, id, title, parent_id) VALUES (%s, %s, %s, %s)",
                 (lib or self.lib, folder_id, title, parent_id), conn)
        return folder_id

    def playlist(self, title: str = "Playlist", *, lib: Optional[str] = None) -> str:
        playlist_id = new_id("PL")
        self.sql("INSERT INTO prks.playlists (library_id, id, title) VALUES (%s, %s, %s)",
                 (lib or self.lib, playlist_id, title))
        return playlist_id

    def saved_view(self, name: str, mode: str = "all", q: str = "query", tag: str = "", author: str = "",
                   publisher: str = "", *, lib: Optional[str] = None, conn: Conn = None) -> str:
        view_id = new_id("SV")
        self.sql("INSERT INTO prks.saved_views (library_id, id, name, mode, search_q, search_tag, "
                 "search_author, search_publisher) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                 (lib or self.lib, view_id, name, mode, q, tag, author, publisher), conn)
        return view_id

    # -- concurrency -------------------------------------------------------

    def wait_for_lock_waiter(self) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.value("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                          "AND wait_event_type = 'Lock'"):
                return
            time.sleep(0.02)
        self.fail("the second writer never waited on the key")

    def race(self, first: Callable[[psycopg.Connection[Any]], object],
             second: Callable[[psycopg.Connection[Any]], object]) -> list[BaseException]:
        """``first`` holds its key uncommitted while ``second`` tries the same key; then ``first`` commits."""
        holder, contender = self.connect(), self.connect()
        errors: list[BaseException] = []

        def run_second() -> None:
            try:
                with contender.transaction():
                    second(contender)
            except BaseException as exc:  # noqa: BLE001 - reported to the test thread
                errors.append(exc)

        with holder.transaction():
            first(holder)
            thread = threading.Thread(target=run_second)
            thread.start()
            self.wait_for_lock_waiter()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        return errors


class TagTests(OrganizationSchemaTestCase):
    def test_a_tag_has_a_distributed_id_a_bounded_name_and_a_colour(self) -> None:
        tag_id = self.tag("Philosophy")
        self.assertEqual(self.value("SELECT color FROM prks.tags WHERE id = %s", (tag_id,)), "#6d6cf7")
        for name in ("", "   ", "x" * 201):
            with self.subTest(name=name):
                self.assert_refused(psycopg.errors.CheckViolation, lambda name=name: self.create_tag(name))
        # 200 bytes of two-byte characters is the bound; the 201st byte is not.
        self.tag("é" * 100)
        self.assert_refused(psycopg.errors.CheckViolation, lambda: self.create_tag("é" * 100 + "x"))
        self.refused(psycopg.errors.CheckViolation,
                     ("INSERT INTO prks.tags (library_id, id, name) VALUES (%s, 'T-1234ABCD', 'Legacy')",
                      (self.lib,)))
        self.refused(psycopg.errors.CheckViolation,
                     ("UPDATE prks.tags SET color = ' ' WHERE id = %s", (tag_id,)))

    def test_a_name_and_its_aliases_hold_one_key_each(self) -> None:
        tag_id = self.tag("Ἠθική", "Етика", "Etika")
        self.assertEqual(self.keys(tag_id), [("alias", "etika"), ("alias", "етика"), ("name", "ἠθική")])

    def test_a_name_without_its_key_row_or_lifecycle_fails_at_commit(self) -> None:
        tag_id = new_id("T")
        self.refused_at_commit(psycopg.errors.ForeignKeyViolation,
                               ("INSERT INTO prks.tags (library_id, id, name) VALUES (%s, %s, 'T')",
                                (self.lib, tag_id)),
                               ("INSERT INTO prks.sync_tag_lifecycle (library_id, tag_id, state) "
                                "VALUES (%s, %s, 'active')", (self.lib, tag_id)))
        self.refused_at_commit(psycopg.errors.ForeignKeyViolation,
                               ("INSERT INTO prks.tags (library_id, id, name) VALUES (%s, %s, 'T')",
                                (self.lib, tag_id)),
                               ("INSERT INTO prks.tag_keys (library_id, key, tag_id) VALUES (%s, 't', %s)",
                                (self.lib, tag_id)))
        owner = self.tag("Owner")
        self.refused_at_commit(psycopg.errors.ForeignKeyViolation,
                               ("INSERT INTO prks.tag_aliases (library_id, id, tag_id, alias) "
                                "VALUES (%s, %s, %s, 'Unkeyed')", (self.lib, new_id("L"), owner)))

    def test_names_and_aliases_never_share_a_key(self) -> None:
        ethics = self.tag("Ethics", "Moral Philosophy")
        other = self.tag("Етика")
        for name in ("ETHICS", "  moral   philosophy ", "ЕТИКА"):
            with self.subTest(name=name):
                self.assert_refused(psycopg.errors.UniqueViolation, lambda name=name: self.create_tag(name))
        for alias in ("ethics", "Moral\tPhilosophy", "етика"):
            with self.subTest(alias=alias):
                self.assert_refused(psycopg.errors.UniqueViolation,
                                    lambda alias=alias: self.add_alias(other, alias))
        # An alias equal to its own Tag's name is refused too: one namespace.
        self.assert_refused(psycopg.errors.UniqueViolation, lambda: self.add_alias(ethics, "ETHICS"))

    def test_unicode_identity_follows_the_shared_key(self) -> None:
        # Full case folding: ß and SS, final and capital sigma are one name.
        self.tag("Straße")
        self.assert_refused(psycopg.errors.UniqueViolation, lambda: self.create_tag("STRASSE"))
        self.tag("Λόγος")
        self.assert_refused(psycopg.errors.UniqueViolation, lambda: self.create_tag("ΛΌΓΟΣ"))
        # NFC: a precomposed and a combining é are one name.
        self.tag("Café")
        self.assert_refused(psycopg.errors.UniqueViolation, lambda: self.create_tag("Café"))
        # Diacritics are not folded, Serbian Cyrillic and Latin are different
        # names, and each Library is its own namespace.
        self.tag("Cafe")
        self.tag("Ђаво")
        self.tag("Đavo")
        self.tag("Café", lib=self.library())

    def test_a_key_row_is_held_by_exactly_one_name_or_alias(self) -> None:
        first = self.tag("First", "Alias")
        second = self.tag("Second")
        alias_id = self.value("SELECT id FROM prks.tag_aliases WHERE tag_id = %s", (first,))
        self.refused(psycopg.errors.UniqueViolation,
                     ("INSERT INTO prks.tag_keys (library_id, key, tag_id) VALUES (%s, 'spare', %s)",
                      (self.lib, first)))
        unkeyed = new_id("L")
        self.refused(psycopg.errors.ForeignKeyViolation,
                     ("INSERT INTO prks.tag_aliases (library_id, id, tag_id, alias) VALUES (%s, %s, %s, 'Spare')",
                      (self.lib, unkeyed, first)),
                     ("INSERT INTO prks.tag_keys (library_id, key, tag_id, alias_id) VALUES (%s, 'spare', %s, %s)",
                      (self.lib, second, unkeyed)))
        self.refused(psycopg.errors.UniqueViolation,
                     ("INSERT INTO prks.tag_keys (library_id, key, tag_id, alias_id) VALUES (%s, 'spare', %s, %s)",
                      (self.lib, first, alias_id)))
        self.refused_at_commit(psycopg.errors.ForeignKeyViolation,
                               ("UPDATE prks.tag_keys SET key = 'stale' WHERE tag_id = %s AND alias_id IS NULL",
                                (second,)))
        self.refused_at_commit(psycopg.errors.ForeignKeyViolation,
                               ("UPDATE prks.tag_keys SET key = 'stale' WHERE alias_id = %s", (alias_id,)))

    def test_a_merge_moves_aliases_and_keeps_the_source_name_as_an_alias(self) -> None:
        source = self.tag("Ontology", "Being", "Seiendes")
        target = self.tag("Metaphysics", "First Philosophy")
        work_id, _ = self.work()
        self.sql("INSERT INTO prks.work_tags (library_id, work_id, tag_id) VALUES (%s, %s, %s)",
                 (self.lib, work_id, source))
        with self.tx():
            # The merge command moves the links, the aliases and the identity.
            self.sql("INSERT INTO prks.work_tags (library_id, work_id, tag_id) VALUES (%s, %s, %s)",
                     (self.lib, work_id, target))
            self.sql("DELETE FROM prks.work_tags WHERE tag_id = %s", (source,))
            self.sql("UPDATE prks.tag_aliases SET tag_id = %s WHERE tag_id = %s", (target, source))
            self.sql("UPDATE prks.sync_tag_lifecycle SET state = 'merged', target_tag_id = %s WHERE tag_id = %s",
                     (target, source))
            self.sql("DELETE FROM prks.tags WHERE id = %s", (source,))
            self.add_alias(target, "Ontology")
        self.assertEqual(self.keys(target), [
            ("alias", "being"), ("alias", "first philosophy"), ("alias", "ontology"), ("alias", "seiendes"),
            ("name", "metaphysics")])
        self.assertEqual(self.keys(source), [])
        self.assertEqual(self.value("SELECT state || ':' || target_tag_id FROM prks.sync_tag_lifecycle "
                                    "WHERE tag_id = %s", (source,)), "merged:" + target)
        # The source's name now names the target's alias, so a new Tag cannot take it.
        self.assert_refused(psycopg.errors.UniqueViolation, lambda: self.create_tag("ONTOLOGY"))

    def test_a_merge_that_forgets_an_alias_key_fails_at_commit(self) -> None:
        source = self.tag("Source", "Kept")
        target = self.tag("Target")
        alias_id = self.value("SELECT id FROM prks.tag_aliases WHERE tag_id = %s", (source,))
        # Re-pointing only the key row leaves the alias's key dangling.
        self.refused(psycopg.errors.ForeignKeyViolation,
                     ("UPDATE prks.tag_keys SET tag_id = %s WHERE alias_id = %s", (target, alias_id)))

    def test_a_delete_frees_every_key_but_not_while_linked(self) -> None:
        tag_id = self.tag("Gone", "Gone Tag")
        work_id, _ = self.work()
        self.sql("INSERT INTO prks.work_tags (library_id, work_id, tag_id) VALUES (%s, %s, %s)",
                 (self.lib, work_id, tag_id))
        self.refused(psycopg.errors.ForeignKeyViolation, ("DELETE FROM prks.tags WHERE id = %s", (tag_id,)))
        folder_id = self.folder("Folder")
        self.sql("DELETE FROM prks.work_tags WHERE tag_id = %s", (tag_id,))
        self.sql("INSERT INTO prks.folder_tags (library_id, folder_id, tag_id) VALUES (%s, %s, %s)",
                 (self.lib, folder_id, tag_id))
        self.refused(psycopg.errors.ForeignKeyViolation, ("DELETE FROM prks.tags WHERE id = %s", (tag_id,)))
        self.sql("DELETE FROM prks.folder_tags WHERE tag_id = %s", (tag_id,))
        with self.tx():
            self.sql("DELETE FROM prks.tags WHERE id = %s", (tag_id,))
            self.sql("UPDATE prks.sync_tag_lifecycle SET state = 'deleted' WHERE tag_id = %s", (tag_id,))
        self.assertEqual((self.keys(tag_id), self.count("tag_aliases", "tag_id", tag_id)), ([], 0))
        self.assertEqual(self.value("SELECT state FROM prks.sync_tag_lifecycle WHERE tag_id = %s", (tag_id,)),
                         "deleted")
        self.tag("GONE", "gone tag")

    def test_the_lifecycle_names_a_target_only_when_merged_and_in_the_library(self) -> None:
        source, target = self.tag("Source"), self.tag("Target")
        theirs = self.tag("Theirs", lib=self.library())
        for state, target_id in (("merged", None), ("active", target), ("deleted", target), ("merged", source),
                                 ("gone", None)):
            with self.subTest(state=state, target=target_id):
                self.refused(psycopg.errors.CheckViolation,
                             ("UPDATE prks.sync_tag_lifecycle SET state = %s, target_tag_id = %s WHERE tag_id = %s",
                              (state, target_id, source)))
        for target_id in (theirs, new_id("T")):
            with self.subTest(target=target_id):
                self.refused(psycopg.errors.ForeignKeyViolation,
                             ("UPDATE prks.sync_tag_lifecycle SET state = 'merged', target_tag_id = %s "
                              "WHERE tag_id = %s", (target_id, source)))

    def test_tags_and_their_links_stay_in_their_library(self) -> None:
        other = self.library()
        theirs = self.tag("Theirs", lib=other)
        mine = self.tag("Mine")
        work_id, _ = self.work()
        folder_id = self.folder("Mine")
        self.refused(psycopg.errors.ForeignKeyViolation,
                     ("INSERT INTO prks.tag_aliases (library_id, id, tag_id, alias) VALUES (%s, %s, %s, 'A')",
                      (self.lib, new_id("L"), theirs)))
        self.refused(psycopg.errors.ForeignKeyViolation,
                     ("INSERT INTO prks.tag_keys (library_id, key, tag_id) VALUES (%s, 'k', %s)", (other, mine)))
        for table, column, owner in (("work_tags", "work_id", work_id), ("folder_tags", "folder_id", folder_id)):
            for library_id in (self.lib, other):
                with self.subTest(table=table, library=library_id):
                    self.refused(psycopg.errors.ForeignKeyViolation,
                                 (f"INSERT INTO prks.{table} (library_id, {column}, tag_id) VALUES (%s, %s, %s)",
                                  (library_id, owner, theirs)))
        self.refused(psycopg.errors.ForeignKeyViolation,
                     ("INSERT INTO prks.sync_tag_lifecycle (library_id, tag_id, state, target_tag_id) "
                      "VALUES (%s, %s, 'merged', %s)", (other, new_id("T"), mine)))

    def test_one_link_per_work_or_folder_and_tag(self) -> None:
        tag_id = self.tag("Tag")
        work_id, _ = self.work()
        folder_id = self.folder("Folder")
        self.sql("INSERT INTO prks.work_tags (library_id, work_id, tag_id) VALUES (%s, %s, %s)",
                 (self.lib, work_id, tag_id))
        self.sql("INSERT INTO prks.folder_tags (library_id, folder_id, tag_id) VALUES (%s, %s, %s)",
                 (self.lib, folder_id, tag_id))
        self.refused(psycopg.errors.UniqueViolation,
                     ("INSERT INTO prks.work_tags (library_id, work_id, tag_id) VALUES (%s, %s, %s)",
                      (self.lib, work_id, tag_id)))
        self.refused(psycopg.errors.UniqueViolation,
                     ("INSERT INTO prks.folder_tags (library_id, folder_id, tag_id) VALUES (%s, %s, %s)",
                      (self.lib, folder_id, tag_id)))


class TagKeyConcurrencyTests(OrganizationSchemaTestCase):
    """A Tag name and another Tag's alias never share a key under concurrency (§7.6)."""

    def assert_one_holder(self, key: str) -> None:
        self.assertEqual(self.value("SELECT count(*) FROM prks.tag_keys WHERE library_id = %s AND key = %s",
                                    (self.lib, key)), 1)

    def test_a_name_and_a_concurrent_alias_of_another_tag(self) -> None:
        other = self.tag("Other")
        errors = self.race(lambda conn: self.create_tag("Φιλοσοφία", conn=conn),
                           lambda conn: self.add_alias(other, "ΦΙΛΟΣΟΦΊΑ", conn=conn))
        self.assertEqual([type(error) for error in errors], [psycopg.errors.UniqueViolation])
        self.assert_one_holder("φιλοσοφία")
        self.assertEqual(self.count("tag_aliases", "tag_id", other), 0)

    def test_an_alias_and_a_concurrent_tag_of_that_name(self) -> None:
        other = self.tag("Other")
        errors = self.race(lambda conn: self.add_alias(other, "Филозофија", conn=conn),
                           lambda conn: self.create_tag("ФИЛОЗОФИЈА", conn=conn))
        self.assertEqual([type(error) for error in errors], [psycopg.errors.UniqueViolation])
        self.assert_one_holder("филозофија")
        self.assertEqual(self.count("tags", "library_id", self.lib), 1)

    def test_two_concurrent_tags_of_one_name(self) -> None:
        errors = self.race(lambda conn: self.create_tag("Logic", conn=conn),
                           lambda conn: self.create_tag("LOGIC", conn=conn))
        self.assertEqual([type(error) for error in errors], [psycopg.errors.UniqueViolation])
        self.assert_one_holder("logic")
        self.assertEqual(self.count("sync_tag_lifecycle", "library_id", self.lib), 1)

    def test_a_merge_and_a_concurrent_tag_of_the_source_name(self) -> None:
        source, target = self.tag("Source"), self.tag("Target")

        def merge(conn: psycopg.Connection[Any]) -> None:
            self.sql("UPDATE prks.sync_tag_lifecycle SET state = 'merged', target_tag_id = %s WHERE tag_id = %s",
                     (target, source), conn)
            self.sql("DELETE FROM prks.tags WHERE id = %s", (source,), conn)
            self.add_alias(target, "Source", conn=conn)

        errors = self.race(merge, lambda conn: self.create_tag("SOURCE", conn=conn))
        self.assertEqual([type(error) for error in errors], [psycopg.errors.UniqueViolation])
        self.assertEqual(self.keys(target), [("alias", "source"), ("name", "target")])


class FolderTests(OrganizationSchemaTestCase):
    def test_two_root_folders_of_one_title_key_are_refused(self) -> None:
        self.folder("Reading")
        for title in ("Reading", "READING", "  reading ", "\treading\n"):
            with self.subTest(title=title):
                self.assert_refused(psycopg.errors.UniqueViolation, lambda title=title: self.folder(title))
        # NFC, full case folding and the Library boundary decide, as for every name key.
        self.folder("Straße")
        self.assert_refused(psycopg.errors.UniqueViolation, lambda: self.folder("STRASSE"))
        self.folder("Réading")
        self.assert_refused(psycopg.errors.UniqueViolation, lambda: self.folder("Réading"))
        self.folder("Reading", lib=self.library())

    def test_one_title_per_parent_and_the_same_title_under_two_parents(self) -> None:
        first, second = self.folder("First"), self.folder("Second")
        self.folder("Notes", parent_id=first)
        self.folder("Notes", parent_id=second)
        self.folder("Notes")
        self.assert_refused(psycopg.errors.UniqueViolation, lambda: self.folder("NOTES", parent_id=first))
        # A move into a parent that already has the title is refused the same way.
        moved = self.folder("Notes ", parent_id=self.folder("Third"))
        self.refused(psycopg.errors.UniqueViolation,
                     ("UPDATE prks.folders SET parent_id = %s WHERE id = %s", (first, moved)))

    def test_titles_and_text_fields_are_bounded_and_never_null(self) -> None:
        for title in ("", "   ", "x" * 201):
            with self.subTest(title=title):
                self.assert_refused(psycopg.errors.CheckViolation, lambda title=title: self.folder(title))
        folder_id = self.folder("Folder")
        self.assertEqual(self.value("SELECT description || private_notes FROM prks.folders WHERE id = %s",
                                    (folder_id,)), "")
        for column in ("description", "private_notes"):
            with self.subTest(column=column):
                self.refused(psycopg.errors.CheckViolation,
                             (f"UPDATE prks.folders SET {column} = %s WHERE id = %s", ("é" * 2001, folder_id)))
                self.refused(psycopg.errors.NotNullViolation,
                             (f"UPDATE prks.folders SET {column} = NULL WHERE id = %s", (folder_id,)))
                self.sql(f"UPDATE prks.folders SET {column} = %s WHERE id = %s", ("é" * 2000, folder_id))
        self.refused(psycopg.errors.CheckViolation,
                     ("INSERT INTO prks.folders (library_id, id, title) VALUES (%s, 'F-1234ABCD', 'Legacy')",
                      (self.lib,)))

    def test_a_parent_is_another_folder_of_the_library(self) -> None:
        folder_id = self.folder("Folder")
        theirs = self.folder("Theirs", lib=self.library())
        self.refused(psycopg.errors.CheckViolation,
                     ("UPDATE prks.folders SET parent_id = id WHERE id = %s", (folder_id,)))
        for parent in (theirs, new_id("F")):
            with self.subTest(parent=parent):
                self.assert_refused(psycopg.errors.ForeignKeyViolation,
                                    lambda parent=parent: self.folder("Child", parent_id=parent))

    def test_a_folder_with_subfolders_files_or_tags_cannot_be_deleted(self) -> None:
        parent = self.folder("Parent")
        child = self.folder("Child", parent_id=parent)
        self.refused(psycopg.errors.ForeignKeyViolation, ("DELETE FROM prks.folders WHERE id = %s", (parent,)))
        work_id, _ = self.work()
        self.sql("INSERT INTO prks.folder_files (library_id, work_id, folder_id) VALUES (%s, %s, %s)",
                 (self.lib, work_id, child))
        self.refused(psycopg.errors.ForeignKeyViolation, ("DELETE FROM prks.folders WHERE id = %s", (child,)))
        self.sql("DELETE FROM prks.folder_files WHERE work_id = %s", (work_id,))
        tag_id = self.tag("Tag")
        self.sql("INSERT INTO prks.folder_tags (library_id, folder_id, tag_id) VALUES (%s, %s, %s)",
                 (self.lib, child, tag_id))
        self.refused(psycopg.errors.ForeignKeyViolation, ("DELETE FROM prks.folders WHERE id = %s", (child,)))
        self.sql("DELETE FROM prks.folder_tags WHERE folder_id = %s", (child,))
        self.sql("DELETE FROM prks.folders WHERE id = %s", (child,))
        self.sql("DELETE FROM prks.folders WHERE id = %s", (parent,))

    def test_a_work_is_in_at_most_one_folder_of_its_library(self) -> None:
        first, second = self.folder("First"), self.folder("Second")
        work_id, _ = self.work()
        insert = "INSERT INTO prks.folder_files (library_id, work_id, folder_id) VALUES (%s, %s, %s)"
        self.sql(insert, (self.lib, work_id, first))
        self.refused(psycopg.errors.UniqueViolation, (insert, (self.lib, work_id, second)))
        self.refused(psycopg.errors.UniqueViolation, (insert, (self.lib, work_id, first)))
        # A move is one update of the Work's row.
        self.sql("UPDATE prks.folder_files SET folder_id = %s WHERE work_id = %s", (second, work_id))
        other = self.library()
        theirs_folder = self.folder("Theirs", lib=other)
        theirs_work, _ = self.work(other)
        mine_work, _ = self.work()
        for library_id, work, folder in ((self.lib, mine_work, theirs_folder), (other, theirs_work, first),
                                         (self.lib, theirs_work, first)):
            with self.subTest(library=library_id, work=work, folder=folder):
                self.refused(psycopg.errors.ForeignKeyViolation, (insert, (library_id, work, folder)))


class FolderConcurrencyTests(OrganizationSchemaTestCase):
    def test_two_concurrent_creates_of_one_root_folder_leave_one_row(self) -> None:
        errors = self.race(lambda conn: self.folder("Inbox", conn=conn),
                           lambda conn: self.folder("INBOX", conn=conn))
        self.assertEqual([type(error) for error in errors], [psycopg.errors.UniqueViolation])
        self.assertEqual(self.value("SELECT count(*) FROM prks.folders WHERE library_id = %s AND parent_id IS NULL",
                                    (self.lib,)), 1)

    def test_two_concurrent_creates_under_one_parent_leave_one_row(self) -> None:
        parent = self.folder("Parent")
        errors = self.race(lambda conn: self.folder("Child", parent_id=parent, conn=conn),
                           lambda conn: self.folder("child", parent_id=parent, conn=conn))
        self.assertEqual([type(error) for error in errors], [psycopg.errors.UniqueViolation])
        self.assertEqual(self.count("folders", "parent_id", parent), 1)

    def test_concurrent_roots_of_one_title_in_two_libraries_both_commit(self) -> None:
        other = self.library()
        holder, contender = self.connect(), self.connect()
        with holder.transaction():
            self.folder("Inbox", conn=holder)
            with contender.transaction():
                self.folder("Inbox", lib=other, conn=contender)
        self.assertEqual(self.value("SELECT count(*) FROM prks.folders WHERE title = 'Inbox'"), 2)


class PlaylistTests(OrganizationSchemaTestCase):
    def test_a_work_is_in_at_most_one_playlist_of_its_library(self) -> None:
        first, second = self.playlist("First"), self.playlist("Second")
        work_id, _ = self.work()
        insert = "INSERT INTO prks.playlist_items (library_id, work_id, playlist_id, position) VALUES (%s, %s, %s, %s)"
        self.sql(insert, (self.lib, work_id, first, 0))
        self.refused(psycopg.errors.UniqueViolation, (insert, (self.lib, work_id, second, 0)))
        self.refused(psycopg.errors.UniqueViolation, (insert, (self.lib, work_id, first, 1)))
        self.refused(psycopg.errors.CheckViolation, (insert, (self.lib, self.work()[0], first, -1)))
        other = self.library()
        theirs, _ = self.work(other)
        their_playlist = self.playlist(lib=other)
        for library_id, work, playlist in ((self.lib, self.work()[0], their_playlist), (other, theirs, first)):
            with self.subTest(library=library_id):
                self.refused(psycopg.errors.ForeignKeyViolation, (insert, (library_id, work, playlist, 0)))

    def test_fields_are_bounded_and_never_null(self) -> None:
        for title in ("", "  ", "x" * 301):
            with self.subTest(title=title):
                self.assert_refused(psycopg.errors.CheckViolation, lambda title=title: self.playlist(title))
        playlist_id = self.playlist("é" * 150)
        for column in ("description", "original_url"):
            with self.subTest(column=column):
                self.refused(psycopg.errors.CheckViolation,
                             (f"UPDATE prks.playlists SET {column} = %s WHERE id = %s", ("x" * 4001, playlist_id)))
                self.refused(psycopg.errors.NotNullViolation,
                             (f"UPDATE prks.playlists SET {column} = NULL WHERE id = %s", (playlist_id,)))
        self.refused(psycopg.errors.CheckViolation,
                     ("INSERT INTO prks.playlists (library_id, id, title) VALUES (%s, 'PL-1234ABCD', 'Legacy')",
                      (self.lib,)))

    def test_a_playlist_with_items_cannot_be_deleted(self) -> None:
        playlist_id = self.playlist()
        work_id, _ = self.work()
        self.sql("INSERT INTO prks.playlist_items (library_id, work_id, playlist_id, position) "
                 "VALUES (%s, %s, %s, 0)", (self.lib, work_id, playlist_id))
        self.refused(psycopg.errors.ForeignKeyViolation,
                     ("DELETE FROM prks.playlists WHERE id = %s", (playlist_id,)))
        self.sql("DELETE FROM prks.playlist_items WHERE playlist_id = %s", (playlist_id,))
        self.sql("DELETE FROM prks.playlists WHERE id = %s", (playlist_id,))


class SavedViewTests(OrganizationSchemaTestCase):
    def test_each_mode_accepts_only_its_definition_shape(self) -> None:
        accepted = [
            ("all", "q", "", "", ""),
            ("advanced", "q", "", "", ""), ("advanced", "", "", "Plato", ""), ("advanced", "", "", "", "Penguin"),
            ("tag", "", "Ethics", "", ""), ("tag", "", "Ethics", "Plato", "Penguin"),
        ]
        refused = [
            ("all", "", "", "", ""), ("all", "q", "Ethics", "", ""), ("all", "q", "", "Plato", ""),
            ("all", "q", "", "", "Penguin"),
            ("advanced", "", "", "", ""), ("advanced", "q", "Ethics", "", ""),
            ("tag", "", "", "", ""), ("tag", "q", "Ethics", "", ""),
            ("other", "q", "", "", ""),
        ]
        for index, (mode, q, tag, author, publisher) in enumerate(accepted):
            with self.subTest(accepted=(mode, q, tag, author, publisher)):
                self.saved_view(f"View {index}", mode, q, tag, author, publisher)
        for mode, q, tag, author, publisher in refused:
            with self.subTest(refused=(mode, q, tag, author, publisher)):
                self.assert_refused(psycopg.errors.CheckViolation, lambda mode=mode, q=q, tag=tag, author=author,
                                    publisher=publisher: self.saved_view("Refused", mode, q, tag, author, publisher))

    def test_names_are_trimmed_bounded_and_without_control_characters(self) -> None:
        self.saved_view("ж" * 80)
        for name in ("", " padded", "padded ", "x" * 81, "tab\there", "bell\x07", "del\x7f"):
            with self.subTest(name=name):
                self.assert_refused(psycopg.errors.CheckViolation, lambda name=name: self.saved_view(name))
        for field, value in (("q", " padded"), ("q", "x" * 501), ("author", "x" * 201), ("q", "line\nbreak")):
            with self.subTest(field=field, value=value):
                self.assert_refused(psycopg.errors.CheckViolation, lambda field=field, value=value: self.saved_view(
                    "Bounded", "advanced", **{field: value}))
        self.saved_view("At the bounds", "advanced", "x" * 500, "", "a" * 200, "p" * 200)

    def test_names_are_unique_by_identity_key_per_library(self) -> None:
        self.saved_view("Ancient Ethics")
        for name in ("ANCIENT ETHICS", "Ancient  Ethics", "ancient ethics"):
            with self.subTest(name=name):
                self.assert_refused(psycopg.errors.UniqueViolation, lambda name=name: self.saved_view(name))
        self.saved_view("Ancient Éthics")
        self.saved_view("Ancient Ethics", lib=self.library())
        view_id = self.saved_view("Other")
        self.refused(psycopg.errors.UniqueViolation,
                     ("UPDATE prks.saved_views SET name = 'ANCIENT ETHICS' WHERE id = %s", (view_id,)))

    def test_two_concurrent_views_of_one_name(self) -> None:
        errors = self.race(lambda conn: self.saved_view("Reading List", conn=conn),
                           lambda conn: self.saved_view("reading list", conn=conn))
        self.assertEqual([type(error) for error in errors], [psycopg.errors.UniqueViolation])
        self.assertEqual(self.count("saved_views", "library_id", self.lib), 1)


class SettingsTests(OrganizationSchemaTestCase):
    def test_library_settings_are_per_library_and_keys_are_spelled_one_way(self) -> None:
        other = self.library()
        insert = "INSERT INTO prks.library_settings (library_id, key, value) VALUES (%s, %s, %s)"
        self.sql(insert, (self.lib, "annotation_author", "Me"))
        self.sql(insert, (other, "annotation_author", "Them"))
        self.refused(psycopg.errors.UniqueViolation, (insert, (self.lib, "annotation_author", "Again")))
        for key in ("", "Annotation_Author", "1st", "with-dash", "x" * 65, "trailing "):
            with self.subTest(key=key):
                self.refused(psycopg.errors.CheckViolation, (insert, (self.lib, key, "")))
        self.refused(psycopg.errors.NotNullViolation, (insert, (self.lib, "empty", None)))
        self.refused(psycopg.errors.ForeignKeyViolation, (insert, ("LIB-" + "0" * 32, "key", "")))
        self.assertEqual(self.value("SELECT value FROM prks.library_settings WHERE library_id = %s", (other,)),
                         "Them")

    def test_instance_settings_have_one_row_per_key(self) -> None:
        insert = "INSERT INTO prks.instance_settings (key, value) VALUES (%s, %s)"
        self.sql(insert, ("organization_test_key", "on"))
        self.refused(psycopg.errors.UniqueViolation, (insert, ("organization_test_key", "off")))
        self.refused(psycopg.errors.CheckViolation, (insert, ("Bad Key", "")))
        self.sql("DELETE FROM prks.instance_settings WHERE key = 'organization_test_key'")


class RevisionOwnershipTests(OrganizationSchemaTestCase):
    # Tables whose rows advance a revision scope when they change (`work-tag`,
    # `folder-tag`, `folder-field`, `work-folder`, `playlist-field`,
    # `playlist-order`, `work-playlist`).
    SCOPE_OWNERS = ("work_tags", "folder_tags", "folders", "folder_files", "playlists", "playlist_items")
    LISTED = {("prks.work_tags", "work_tags_work_fkey")}

    def test_only_the_listed_work_tag_key_cascades(self) -> None:
        rows = self.sql(
            "SELECT conrelid::regclass::text, conname FROM pg_constraint "
            "WHERE contype = 'f' AND confdeltype IN ('c', 'n', 'd') AND conrelid = ANY(%s::regclass[])",
            ([f"prks.{table}" for table in self.SCOPE_OWNERS],)).fetchall()
        self.assertEqual({(row[0], row[1]) for row in rows}, self.LISTED)

    def test_a_filed_work_cannot_be_deleted_until_its_filing_is_removed(self) -> None:
        # Filing writes no revision, so the Work delete must remove the
        # membership and advance `work-folder` / `playlist-order` itself.
        for table, parent, column in (("folder_files", self.folder("Folder"), "folder_id"),
                                      ("playlist_items", self.playlist(), "playlist_id")):
            with self.subTest(table=table):
                work_id, manifestation_id = self.work()
                extra = ", position" if table == "playlist_items" else ""
                value = ", 0" if extra else ""
                self.sql(f"INSERT INTO prks.{table} (library_id, work_id, {column}{extra}) "
                         f"VALUES (%s, %s, %s{value})", (self.lib, work_id, parent))
                delete = (("DELETE FROM prks.manifestations WHERE id = %s", (manifestation_id,)),
                          ("DELETE FROM prks.works WHERE id = %s", (work_id,)))
                self.refused(psycopg.errors.ForeignKeyViolation, *delete)
                with self.tx():
                    self.sql(f"DELETE FROM prks.{table} WHERE work_id = %s", (work_id,))
                    for statement, params in delete:
                        self.sql(statement, params)


class WorkDeleteTombstonesTagsTests(WorksFacadeTestCase):
    """The listed Work-Tag cascade: the Work delete removes the links and tombstones their scopes."""

    def test_deleting_a_work_tombstones_its_tag_scopes(self) -> None:
        work_id = self.library.add_work("Tagged")
        library_id = self.sql("SELECT library_id FROM prks.works WHERE id = %s", (work_id,))[0]["library_id"]
        tag_id = new_id("T")
        scope_id = f'["{work_id}","{tag_id}"]'
        # One statement is one transaction here, and a Tag commits only with its key and lifecycle rows.
        self.sql("WITH tag AS (INSERT INTO prks.tags (library_id, id, name) VALUES (%(lib)s, %(tag)s, 'Tag')),"
                 " name_key AS (INSERT INTO prks.tag_keys (library_id, key, tag_id) VALUES (%(lib)s, 'tag', %(tag)s)),"
                 " lifecycle AS (INSERT INTO prks.sync_tag_lifecycle (library_id, tag_id, state)"
                 "               VALUES (%(lib)s, %(tag)s, 'active'))"
                 " INSERT INTO prks.work_tags (library_id, work_id, tag_id) VALUES (%(lib)s, %(work)s, %(tag)s)",
                 {"lib": library_id, "tag": tag_id, "work": work_id})
        self.sql("INSERT INTO prks.entity_revisions (library_id, scope_type, scope_id, revision) "
                 "VALUES (%s, 'work-tag', %s, 1)", (library_id, scope_id))
        self.assertIsNotNone(self.library.delete_work_record(work_id))
        self.assertEqual(self.sql("SELECT count(*) AS n FROM prks.work_tags WHERE work_id = %s",
                                  (work_id,))[0]["n"], 0)
        self.assertEqual(self.sql("SELECT revision FROM prks.entity_revisions WHERE scope_id = %s",
                                  (scope_id,))[0]["revision"], 2)
        self.assertEqual(self.sql("SELECT count(*) AS n FROM prks.tags WHERE id = %s", (tag_id,))[0]["n"], 1)
