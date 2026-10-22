"""Folders, their hierarchy and fields, and Work filing on PostgreSQL: what parity cannot show (L-O-1 #633).

``tests/test_folders_parity.py`` proves the facade returns SQLite's JSON.
These tests cover what only PostgreSQL has: titles compared by identity key
per parent (root folders in one scope), refusals of what PostgreSQL text
cannot hold, tombstones a delete leaves, the Work delete taking the Work
out of its Folder, Library isolation, and concurrent writers (each race
holds one transaction open until the other is waiting on it).
"""

from __future__ import annotations

import threading
import time
import unicodedata
from typing import Any, Callable, Optional
from unittest import mock

from backend.domain.identity.authorization import AccessRefused
from backend.domain.organization import folders, links, tags
from backend.domain.works import commands
from backend.entity_ids import generate
from backend.folder_sync import FolderRuleError, scope_key
from backend.pg_facade import NotPortedError
from backend.pg_facade import context as facade_context
from tests.pg.test_tags import OrganizationTestCase


class FolderTestCase(OrganizationTestCase):
    def add(self, title: str, parent_id: Optional[str] = None, **fields: Any) -> str:
        folder_id = self.library.add_folder(title, fields.pop("description", ""), parent_id)
        if fields:
            self.library.update_folder_metadata(folder_id, fields)
        return folder_id

    def refused(self, message: str, call: Any, *args: Any) -> None:
        with self.assertRaises(ValueError) as caught:
            call(*args)
        self.assertEqual(str(caught.exception), message)

    def field_revisions(self, folder_id: str) -> dict[str, int]:
        state = self.library.get_folder_sync_state(folder_id)
        assert state is not None
        return {field: value["revision"] for field, value in state["fields"].items()}

    def work_folder(self, work_id: str) -> tuple[str, int]:
        state = self.library.get_work_folder_state(work_id)
        assert state is not None
        return state["folder_id"], state["revision"]

    def filed(self) -> list[tuple[str, str]]:
        rows = self.sql("SELECT work_id, folder_id FROM prks.folder_files ORDER BY work_id")
        return [(row["work_id"], row["folder_id"]) for row in rows]


_TAKEN = "A folder with this name already exists in this location."


class IdentityTests(FolderTestCase):
    def test_titles_are_unique_per_parent_by_identity_key(self) -> None:
        logos = self.add("Λόγος")
        for title in ("ΛΌΓΟΣ", "  λόγος  "):
            with self.subTest(title=title):
                self.refused(_TAKEN, self.library.add_folder, title)
        strasse = self.add("Straße")
        self.refused(_TAKEN, self.library.add_folder, "STRASSE")
        self.add(unicodedata.normalize("NFC", "Café"))
        self.refused(_TAKEN, self.library.add_folder, unicodedata.normalize("NFD", "CAFÉ"))
        # Diacritics are not folded (§8.1).
        self.assertNotEqual(self.add("Resume"), self.add("Résumé"))
        # Two parents may each hold the title, and a child may share its parent's.
        self.add("Λόγος", logos)
        self.add("ΛΌΓΟΣ", strasse)
        self.refused(_TAKEN, self.library.add_folder, "λόγος", "", logos)
        self.assertEqual(self.count("folders"), 7)

    def test_a_move_or_rename_into_a_taken_title_is_refused(self) -> None:
        drafts, archive = self.add("Drafts"), self.add("Archive")
        nested = self.add("DRAFTS", archive)
        self.refused(_TAKEN, self.library.update_folder_metadata, archive, {"title": "drafts"})
        self.refused(_TAKEN, self.library.update_folder_metadata, nested, {"parent_id": ""})
        # Rename first, then move: the pair is judged against the new title.
        self.library.update_folder_metadata(nested, {"title": "Old drafts", "parent_id": ""})
        self.assertEqual(folders_named(self.library.get_all_folders()), ["Archive", "Drafts", "Old drafts"])
        self.assertEqual(self.field_revisions(nested),
                         {"description": 0, "parent_id": 1, "private_notes": 0, "title": 1})
        self.library.update_folder_metadata(drafts, {"title": "  DRAFTS "})
        self.assertEqual(self.library.get_folder(drafts)["title"], "DRAFTS")

    def test_what_postgresql_cannot_hold_or_the_schema_bounds_is_refused_before_any_write(self) -> None:
        folder_id = self.add("Folder")
        for call, args in ((self.library.add_folder, ("a\x00b",)), (self.library.add_folder, ("x" * 201,)),
                           (self.library.add_folder, ("Ok", "d\x00")),
                           (self.library.update_folder_metadata, (folder_id, {"private_notes": "é" * 2001})),
                           (self.library.update_folder_metadata, (folder_id, {"description": "\x00"}))):
            with self.subTest(args=[str(arg)[:12] for arg in args]), self.assertRaises(ValueError):
                call(*args)
        self.assertEqual(self.library.get_folder(self.add("é" * 100))["title"], "é" * 100)  # 200 bytes
        self.assertEqual(self.library.get_folder(self.add("   "))["title"], "Untitled Folder")
        self.assertEqual(self.field_revisions(folder_id), dict.fromkeys(("description", "parent_id",
                                                                         "private_notes", "title"), 0))


def folders_named(rows: list[dict[str, Any]]) -> list[str]:
    return [row["title"] for row in rows]


class HierarchyTests(FolderTestCase):
    def test_a_cycle_is_refused_at_any_depth(self) -> None:
        a = self.add("A")
        b = self.add("B", a)
        c = self.add("C", b)
        self.refused("Cannot set parent to a subfolder (cycle).", self.library.update_folder_metadata,
                     a, {"parent_id": c})
        self.refused("A folder cannot be its own parent.", self.library.update_folder_metadata,
                     a, {"parent_id": a})
        self.refused("Parent folder not found.", self.library.update_folder_metadata,
                     a, {"parent_id": generate("F")})
        self.refused("Parent folder not found.", self.library.add_folder, "D", "", generate("F"))
        self.library.update_folder_metadata(c, {"parent_id": ""})
        self.library.update_folder_metadata(a, {"parent_id": c})
        self.assertEqual(self.library.get_folder(c)["children"][0]["id"], a)
        self.assertEqual(self.library.get_folder(b)["parent"], {"id": a, "title": "A"})

    def test_only_a_change_advances_a_field(self) -> None:
        folder_id = self.add("Folder", description="About")
        before = self.library.get_folder(folder_id)
        self.library.update_folder_metadata(folder_id, {"description": " About ", "title": "Folder",
                                                        "parent_id": None, "private_notes": False})
        self.assertEqual(self.field_revisions(folder_id), dict.fromkeys(("description", "parent_id",
                                                                         "private_notes", "title"), 0))
        self.assertEqual(self.revisions("folder-field"), {})
        self.library.update_folder_metadata(folder_id, {"private_notes": "Mine", "ignored": "x"})
        self.library.update_folder_metadata(folder_id, {"private_notes": "Mine again"})
        self.assertEqual(self.field_revisions(folder_id)["private_notes"], 2)
        self.assertEqual(self.library.get_folder(folder_id)["created_at"], before["created_at"])
        self.refused("Folder not found.", self.library.update_folder_metadata, generate("F"), {"title": "X"})


class DeleteTests(FolderTestCase):
    def test_a_folder_with_files_or_subfolders_is_refused(self) -> None:
        parent = self.add("Parent")
        self.add("Child", parent)
        holder = self.add("Holder")
        self.library.add_work_to_folder(holder, self.library.add_work("W"))
        self.refused("Cannot delete folder: Folder has subfolders. Please move or delete them first.",
                     self.library.delete_empty_folder, parent)
        self.refused("Cannot delete folder: Folder is not empty. Please remove all files first.",
                     self.library.delete_empty_folder, holder)
        self.refused("Folder not found.", self.library.delete_empty_folder, generate("F"))
        self.refused("Folder not found.", self.library.delete_empty_folder, "  ")
        self.assertEqual(self.count("folders"), 3)

    def test_a_delete_removes_its_tag_links_and_tombstones_every_scope(self) -> None:
        folder_id = self.add("Folder")
        kept, removed = self.tag("Kept"), self.tag("Removed")
        self.library.add_tag_to_folder(folder_id, kept)
        self.library.add_tag_to_folder(folder_id, removed)
        self.library.remove_tag_from_folder(folder_id, removed)
        self.library.update_folder_metadata(folder_id, {"title": "Renamed"})
        self.library.delete_empty_folder(folder_id)
        self.assertEqual((self.count("folders"), self.count("folder_tags"), self.count("tags")), (0, 0, 2))
        # Every field scope, edited or not, and every Folder-Tag scope it ever had.
        self.assertEqual(self.revisions("folder-field"), {
            scope_key(folder_id, "title"): 2, scope_key(folder_id, "description"): 1,
            scope_key(folder_id, "parent_id"): 1, scope_key(folder_id, "private_notes"): 1})
        self.assertEqual(self.revisions("folder-tag"), {links.FOLDER.scope_id(folder_id, kept): 2,
                                                        links.FOLDER.scope_id(folder_id, removed): 3})
        self.assertEqual([row["name"] for row in self.library.get_tags_in_use()], [])
        # A Folder created again under the id starts from the tombstones.
        self.run_command(lambda scope: folders.insert(scope, folder_id, "Again"))
        self.assertEqual(self.field_revisions(folder_id)["description"], 1)
        self.assertEqual(self.library.get_folder_tag_options(folder_id)["known_absent"], {kept: 2, removed: 3})


class WorkFilingTests(FolderTestCase):
    def test_filing_moving_and_clearing_advance_the_work_folder(self) -> None:
        a, b = self.add("A"), self.add("B")
        work_id = self.library.add_work("Paper")
        self.assertEqual(self.work_folder(work_id), ("", 0))
        self.library.add_work_to_folder(a, work_id)
        self.library.add_work_to_folder(a, work_id)  # already there: nothing changes
        self.assertEqual(self.work_folder(work_id), (a, 1))
        self.refused("This file is already in another folder.", self.library.add_work_to_folder, b, work_id)
        detail = self.library.get_work(work_id)
        self.assertEqual((detail["folder_id"], detail["folder_title"]), (a, "A"))
        self.assertEqual(self.library.get_all_works()[0]["folder_id"], a)
        self.assertEqual(self.library.get_recently_added_browse()[0]["folder_id"], a)
        self.library.move_work_to_folder(work_id, b)
        self.library.move_work_to_folder(work_id, b)
        self.assertEqual(self.work_folder(work_id), (b, 2))
        self.assertEqual({row["title"]: row["work_count"] for row in self.library.get_all_folders()},
                         {"A": 0, "B": 1})
        self.assertEqual(self.library.get_folder_work_ids(b), [work_id])
        self.library.move_work_to_folder(work_id, None)
        self.assertEqual(self.work_folder(work_id), ("", 3))
        self.assertEqual(self.library.get_work(work_id)["folder_title"], None)
        self.assertEqual(self.filed(), [])

    def test_a_missing_work_or_folder_is_refused(self) -> None:
        folder_id, work_id = self.add("A"), self.library.add_work("Paper")
        missing_work, missing_folder = generate("W"), generate("F")
        self.refused("The selected folder no longer exists.", self.library.add_work_to_folder,
                     missing_folder, work_id)
        self.refused("This file no longer exists.", self.library.add_work_to_folder, folder_id, missing_work)
        self.refused("Folder not found.", self.library.move_work_to_folder, work_id, missing_folder)
        self.refused("work not found", self.library.move_work_to_folder, missing_work, folder_id)
        self.library.move_work_to_folder(missing_work, "")  # clearing a missing Work changes nothing
        self.refused("folder_id is required", self.library.add_work_to_folder, " ", work_id)
        self.refused("work_id is required", self.library.move_work_to_folder, " ", folder_id)
        self.assertEqual((self.filed(), self.revisions("work-folder")), ([], {}))

    def test_the_uncategorized_folder_is_one_root_folder_hidden_while_empty(self) -> None:
        first = self.library.ensure_default_uncategorized_folder_id()
        self.assertEqual(self.library.ensure_default_uncategorized_folder_id(), first)
        self.assertEqual(self.library.get_all_folders(), [])
        self.assertTrue(self.library.folder_exists(first))
        self.library.add_work_to_folder(first, self.library.add_work("W"))
        self.assertEqual(folders_named(self.library.get_all_folders()), ["Uncategorized"])
        self.refused(_TAKEN, self.library.add_folder, "UNCATEGORIZED")

    def test_another_spelling_of_uncategorized_is_that_folder(self) -> None:
        # SQLite fails to create "Uncategorized" here; one root holds the key.
        spelled = self.add("UNCATEGORIZED")
        self.assertEqual(self.library.ensure_default_uncategorized_folder_id(), spelled)


class WorkDeleteTests(FolderTestCase):
    def test_deleting_a_filed_work_unfiles_it_and_tombstones_its_folder_scope(self) -> None:
        folder_id = self.add("A")
        filed, never = self.library.add_work("Filed"), self.library.add_work("Never filed")
        self.library.move_work_to_folder(filed, folder_id)
        self.assertIsNotNone(self.library.delete_work_record(filed))
        self.assertIsNotNone(self.library.delete_work_record(never))
        self.assertEqual(self.filed(), [])
        # A never-filed Work's scope is tombstoned from revision 0 too.
        self.assertEqual(self.revisions("work-folder"), {filed: 2, never: 1})
        self.assertEqual(self.library.get_all_folders()[0]["work_count"], 0)
        self.library.delete_empty_folder(folder_id)

    def test_a_work_in_a_playlist_is_not_deleted_until_the_playlist_slice(self) -> None:
        folder_id, work_id = self.add("A"), self.library.add_work("Listed")
        self.library.move_work_to_folder(work_id, folder_id)
        playlist_id = "PL-" + generate("F")[2:]
        self.sql("INSERT INTO prks.playlists (library_id, id, title) VALUES (%s, %s, 'List')",
                 (self.actor.library_id, playlist_id))
        self.sql("INSERT INTO prks.playlist_items (library_id, work_id, playlist_id, position) "
                 "VALUES (%s, %s, %s, 0)", (self.actor.library_id, work_id, playlist_id))
        with self.assertRaises(NotPortedError):
            self.library.delete_work_record(work_id)
        self.assertEqual((self.count("works"), self.filed(), self.revisions("work-folder")),
                         (1, [(work_id, folder_id)], {work_id: 1}))


class IsolationTests(FolderTestCase):
    def test_another_librarys_folders_are_invisible(self) -> None:
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            theirs = self.library.add_folder("Theirs")
            their_work = self.library.add_work("Their work")
        mine = self.library.add_work("Mine")
        self.assertIsNone(self.library.get_folder(theirs))
        self.assertIsNone(self.library.get_folder_sync_state(theirs))
        self.assertIsNone(self.library.get_work_folder_state(their_work))
        self.assertFalse(self.library.folder_exists(theirs))
        self.refused("The selected folder no longer exists.", self.library.add_work_to_folder, theirs, mine)
        self.refused("Parent folder not found.", self.library.add_folder, "Child", "", theirs)
        self.refused("Folder not found.", self.library.delete_empty_folder, theirs)
        self.refused("Folder not found.", self.library.update_folder_metadata, theirs, {"title": "Mine"})
        # Titles are per Library.
        self.add("Theirs")
        self.assertEqual(self.library.get_all_folders()[0]["title"], "Theirs")
        with facade_context.acting_as(other.context):
            self.assertEqual([row["id"] for row in self.library.get_all_folders()], [theirs])

    def test_a_reader_reads_folders_but_cannot_change_them(self) -> None:
        folder_id, work_id = self.add("A"), self.library.add_work("W")
        with facade_context.acting_as(self.reader_context()):
            self.assertEqual(self.library.get_folder(folder_id)["title"], "A")
            for call in (lambda: self.library.add_folder("B"),
                         lambda: self.library.update_folder_metadata(folder_id, {"title": "B"}),
                         lambda: self.library.move_work_to_folder(work_id, folder_id),
                         lambda: self.library.delete_empty_folder(folder_id),
                         lambda: self.library.ensure_default_uncategorized_folder_id()):
                with self.assertRaises(AccessRefused):
                    call()
        self.assertEqual((self.count("folders"), self.filed()), (1, []))


class RaceTests(FolderTestCase):
    def test_two_creates_of_one_root_title_make_one_folder(self) -> None:
        holder, contender = self.race(lambda scope: folders.insert(scope, generate("F"), "Λόγος"),
                                      lambda: self.library.add_folder("ΛΌΓΟΣ"))
        self.assertIs(holder, True)
        self.assertIsInstance(contender, FolderRuleError)
        self.assertEqual((str(contender), self.count("folders")), (_TAKEN, 1))

    def test_two_first_uncategorized_lookups_make_one_folder(self) -> None:
        # READ COMMITTED, as the facade runs it: the loser finds the winner.
        holder, contender = self.race(lambda scope: folders.ensure_uncategorized(scope, generate("F")),
                                      self.library.ensure_default_uncategorized_folder_id)
        self.assertIsInstance(holder, str)
        self.assertEqual((contender, self.count("folders")), (holder, 1))

    def start_command(self, outcomes: dict[str, Any], name: str, fn: Callable[[Any], Any]) -> threading.Thread:
        """``fn`` as a command on a thread named ``name``; its result or exception lands in ``outcomes``."""
        def run() -> None:
            try:
                outcomes[name] = self.run_command(fn)
            except BaseException as exc:  # noqa: BLE001 - reported to the test thread
                outcomes[name] = exc
        thread = threading.Thread(target=run, name=name)
        thread.start()
        return thread

    def commit_while_paused(self, payload: dict[str, Any], pause_in: str, pause_at_call: int) -> Any:
        """A create of id X ("Shared", a root) held open; a second create of X
        with ``payload`` finds no X, pauses at call ``pause_at_call`` of
        ``folders.<pause_in>``, and resumes once the first has committed.
        Returns the second create's result."""
        folder_id = generate("F")
        inserted, release, paused, resume = (threading.Event() for _ in range(4))
        outcomes: dict[str, Any] = {}
        original = getattr(folders, pause_in)
        calls = {"n": 0}

        def pausing(*args: Any) -> Any:
            if threading.current_thread().name == "second":
                calls["n"] += 1
                if calls["n"] == pause_at_call:
                    paused.set()
                    resume.wait(10)
            return original(*args)

        def first(scope: Any) -> bool:
            created = folders.insert(scope, folder_id, "Shared")
            inserted.set()
            release.wait(10)
            return created

        with mock.patch.object(folders, pause_in, pausing):
            threads = [self.start_command(outcomes, "first", first)]
            inserted.wait(10)
            threads.append(self.start_command(
                outcomes, "second", lambda scope: folders.apply_create(scope, folder_id, payload)))
            self.assertTrue(paused.wait(10))
            release.set()
            threads[0].join(10)
            resume.set()
            threads[1].join(10)
        self.assertIs(outcomes["first"], True)
        self.assertNotIsInstance(outcomes["second"], BaseException)
        return outcomes["second"]

    def assert_converged_on_shared(self, outcome: Any) -> None:
        status, result = outcome
        self.assertEqual((status, result["changed"], result["folder"]["title"]), (200, False, "Shared"))

    def test_a_create_of_an_id_that_commits_before_the_title_check_converges(self) -> None:
        payload = {"title": "Shared", "description": "", "parent_id": "", "private_notes": ""}
        self.assert_converged_on_shared(self.commit_while_paused(payload, "_assert_title_free", 1))
        self.assertEqual(self.count("folders"), 1)

    def test_a_create_of_an_id_that_commits_first_wins_over_a_title_another_folder_holds(self) -> None:
        self.add("Taken")
        payload = {"title": "Taken", "description": "", "parent_id": "", "private_notes": ""}
        self.assert_converged_on_shared(self.commit_while_paused(payload, "_assert_title_free", 1))
        self.assertEqual(self.count("folders"), 2)

    def test_a_create_of_an_id_that_commits_first_wins_over_a_missing_parent(self) -> None:
        # The second ``share``: the first looked for the id itself.
        payload = {"title": "Child", "description": "", "parent_id": generate("F"), "private_notes": ""}
        self.assert_converged_on_shared(self.commit_while_paused(payload, "share", 2))
        self.assertEqual(self.count("folders"), 1)

    def test_a_fresh_id_still_gets_its_refusals(self) -> None:
        self.add("Taken")
        refusals = [self.run_command(lambda scope, p=payload: folders.apply_create(scope, generate("F"), p))
                    for payload in ({"title": "Taken", "description": "", "parent_id": "", "private_notes": ""},
                                    {"title": "Child", "description": "", "parent_id": generate("F"),
                                     "private_notes": ""})]
        self.assertEqual([(status, body["code"]) for status, body in refusals],
                         [(409, "TITLE_TAKEN"), (404, "PARENT_NOT_FOUND")])

    def create_create_delete(self, pause_in: str, pause_at_call: int) -> dict[str, Any]:
        """Three transactions on one new Folder id: a create; a create of the
        id that waits on it; and, once the first commits, a delete of the
        still-empty Folder while the second is paused at call ``pause_at_call``
        of ``folders.<pause_in>``. The delete runs until it is done or waits."""
        folder_id = generate("F")
        payload = {"title": "Shared", "description": "", "parent_id": "", "private_notes": ""}
        inserted, release, paused, resume = (threading.Event() for _ in range(4))
        outcomes: dict[str, Any] = {}
        original = getattr(folders, pause_in)
        calls = {"n": 0}

        def pausing(*args: Any) -> Any:
            if threading.current_thread().name == "second":
                calls["n"] += 1
                if calls["n"] == pause_at_call:
                    paused.set()
                    resume.wait(10)
            return original(*args)

        def first(scope: Any) -> bool:
            created = folders.insert(scope, folder_id, "Shared")
            inserted.set()
            release.wait(10)
            return created

        def start(name: str, fn: Callable[[Any], Any]) -> threading.Thread:
            return self.start_command(outcomes, name, fn)

        with mock.patch.object(folders, pause_in, pausing):
            threads = [start("first", first)]
            inserted.wait(10)
            threads.append(start("second", lambda scope: folders.apply_create(scope, folder_id, payload)))
            self.wait_for_lock_waiter()
            release.set()
            threads[0].join(10)
            self.assertTrue(paused.wait(10))
            threads.append(start("third", lambda scope: folders.delete(scope, folder_id)))
            deadline = time.monotonic() + 10
            while threads[2].is_alive() and time.monotonic() < deadline:
                if self.sql("SELECT count(*) AS n FROM pg_stat_activity WHERE datname = current_database()"
                            " AND wait_event_type = 'Lock'")[0]["n"]:
                    break
                time.sleep(0.02)
            resume.set()
            for thread in threads:
                thread.join(10)
        self.assertIs(outcomes["first"], True)
        self.assertNotIsInstance(outcomes["second"], BaseException)
        return outcomes

    def test_a_create_of_an_id_another_create_won_holds_that_folder_against_a_delete(self) -> None:
        # Paused before reading what it acknowledges: the delete waits for it.
        outcomes = self.create_create_delete("folder_row", 1)
        status, result = outcomes["second"]
        self.assertEqual((status, result["changed"], result["folder"]["title"]), (200, False, "Shared"))
        self.assertEqual((outcomes["third"], self.count("folders")), ((True, None), 0))

    def test_a_create_of_an_id_whose_winner_is_deleted_first_creates_it_again(self) -> None:
        # Paused before holding the winner (the second ``share``; the first
        # found nothing): the delete commits, and the id is free again.
        outcomes = self.create_create_delete("share", 2)
        status, result = outcomes["second"]
        self.assertEqual((status, result["changed"]), (200, True))
        self.assertEqual((outcomes["third"], self.count("folders")), ((True, None), 1))

    def test_a_rename_that_loses_a_title_race_decides_again(self) -> None:
        other = self.add("Other")
        holder, contender = self.race(lambda scope: folders.insert(scope, generate("F"), "Same"),
                                      lambda: self.library.update_folder_metadata(other, {"title": "SAME"}))
        self.assertIs(holder, True)
        self.assertIsInstance(contender, FolderRuleError)
        self.assertEqual(str(contender), _TAKEN)
        self.assertEqual((self.library.get_folder(other)["title"], self.field_revisions(other)["title"]),
                         ("Other", 0))

    def test_two_crossed_moves_cannot_close_a_cycle(self) -> None:
        a, b = self.add("A"), self.add("B")
        holder, contender = self.race(lambda scope: folders.set_field(scope, a, "parent_id", b),
                                      lambda: self.library.update_folder_metadata(b, {"parent_id": a}))
        self.assertEqual(holder, (True, 1))
        self.assertIsInstance(contender, FolderRuleError)
        self.assertEqual(str(contender), "Cannot set parent to a subfolder (cycle).")
        self.assertEqual(self.library.get_folder(b)["parent_id"], None)

    def test_a_move_under_a_folder_being_deleted_is_refused(self) -> None:
        doomed, moving = self.add("Doomed"), self.add("Moving")
        holder, contender = self.race(lambda scope: folders.delete(scope, doomed),
                                      lambda: self.library.update_folder_metadata(moving, {"parent_id": doomed}))
        self.assertEqual(holder, (True, None))
        self.assertIsInstance(contender, FolderRuleError)
        self.assertEqual(str(contender), "Parent folder not found.")

    def test_a_create_under_a_folder_being_deleted_is_refused(self) -> None:
        doomed = self.add("Doomed")
        holder, contender = self.race(lambda scope: folders.delete(scope, doomed),
                                      lambda: self.library.add_folder("Child", "", doomed))
        self.assertEqual(holder, (True, None))
        self.assertIsInstance(contender, ValueError)
        self.assertEqual(str(contender), "Parent folder not found.")

    def test_a_delete_waits_for_a_child_created_under_the_folder(self) -> None:
        parent = self.add("Parent")
        holder, contender = self.race(lambda scope: folders.insert(scope, generate("F"), "Child", "", parent),
                                      lambda: self.library.delete_empty_folder(parent))
        self.assertIs(holder, True)
        self.assertIsInstance(contender, ValueError)
        self.assertIn("Folder has subfolders", str(contender))

    def test_filing_into_a_folder_being_deleted_is_refused(self) -> None:
        doomed, work_id = self.add("Doomed"), self.library.add_work("W")
        holder, contender = self.race(lambda scope: folders.delete(scope, doomed),
                                      lambda: self.library.add_work_to_folder(doomed, work_id))
        self.assertEqual(holder, (True, None))
        self.assertIsInstance(contender, ValueError)
        self.assertEqual(str(contender), "The selected folder no longer exists.")
        self.assertEqual(self.filed(), [])

    def test_a_delete_waits_for_a_filing_into_the_folder(self) -> None:
        folder_id, work_id = self.add("Busy"), self.library.add_work("W")

        def file(scope: Any) -> bool:
            folders.lock_work(scope, work_id)
            return folders.set_work_folder(scope, work_id, folder_id)

        holder, contender = self.race(file, lambda: self.library.delete_empty_folder(folder_id),
                                      serializable=True)
        self.assertIs(holder, True)
        self.assertIsInstance(contender, ValueError)
        self.assertIn("Folder is not empty", str(contender))
        self.assertEqual(self.filed(), [(work_id, folder_id)])

    def test_two_moves_of_one_work_serialize(self) -> None:
        a, b, work_id = self.add("A"), self.add("B"), self.library.add_work("W")

        def move(scope: Any) -> bool:
            folders.lock_work(scope, work_id)
            return folders.set_work_folder(scope, work_id, a)

        holder, contender = self.race(move, lambda: self.library.move_work_to_folder(work_id, b),
                                      serializable=True)
        self.assertEqual((holder, contender), (True, None))
        self.assertEqual((self.work_folder(work_id), self.filed()), ((b, 2), [(work_id, b)]))

    def test_a_move_of_a_work_being_deleted_is_refused(self) -> None:
        folder_id, work_id = self.add("A"), self.library.add_work("W")
        holder, contender = self.race(lambda scope: commands.delete_work(scope, work_id) is not None,
                                      lambda: self.library.move_work_to_folder(work_id, folder_id))
        self.assertIs(holder, True)
        self.assertIsInstance(contender, ValueError)
        self.assertEqual(str(contender), "work not found")
        self.assertEqual((self.filed(), self.revisions("work-folder")), ([], {work_id: 1}))

    def test_a_work_delete_waits_for_a_move_and_then_unfiles_it(self) -> None:
        folder_id, work_id = self.add("A"), self.library.add_work("W")

        def move(scope: Any) -> bool:
            folders.lock_work(scope, work_id)
            return folders.set_work_folder(scope, work_id, folder_id)

        holder, contender = self.race(move, lambda: self.library.delete_work_record(work_id) is not None,
                                      serializable=True)
        self.assertEqual((holder, contender), (True, True))
        self.assertEqual((self.filed(), self.revisions("work-folder")), ([], {work_id: 2}))

    def test_a_folder_delete_and_a_tag_delete_never_deadlock(self) -> None:
        for folder_first in (True, False):
            with self.subTest(folder_first=folder_first):
                self.delete_folder_and_tag(folder_first)
        self.assertEqual((self.count("folders"), self.count("folder_tags"), self.count("tags")), (0, 0, 0))

    def delete_folder_and_tag(self, folder_first: bool) -> None:
        folder_id, tag_id = self.add(f"F{folder_first}"), self.tag(f"T{folder_first}")
        self.library.add_tag_to_folder(folder_id, tag_id)
        if folder_first:
            # The Tag delete waits on the Folder (FOR KEY SHARE), then finds no link.
            holder, contender = self.race(lambda scope: folders.delete(scope, folder_id),
                                          lambda: self.library.delete_tag(tag_id))
            self.assertEqual(holder, (True, None))
            self.assertEqual(contender["status"], "deleted")
        else:
            # The Folder delete waits on the Tag delete's share of the Folder.
            holder, contender = self.race(lambda scope: tags.delete_tag(scope, tag_id)[0],
                                          lambda: self.library.delete_empty_folder(folder_id))
            self.assertEqual((holder, contender), (True, None))
        # The removal advanced the link's scope once, the Folder's tombstone once.
        self.assertEqual(self.revisions("folder-tag")[links.FOLDER.scope_id(folder_id, tag_id)],
                         2 if folder_first else 3)
