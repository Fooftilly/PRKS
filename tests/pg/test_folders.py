"""Folders, their hierarchy and fields, and Work filing on PostgreSQL (L-O-1 #633).

:class:`AnswerTests` spells out the JSON each command answers. The rest
cover titles compared by identity key
per parent (root folders in one scope), refusals of what PostgreSQL text
cannot hold, tombstones a delete leaves, the Work delete taking the Work
out of its Folder, Library isolation, and concurrent writers (each race
holds one transaction open until the other is waiting on it).
"""

from __future__ import annotations

import threading
import time
import unicodedata
from functools import partial
from typing import Any, Callable, Optional
from unittest import mock

from backend.domain.identity.authorization import AccessRefused
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.organization import folder_commands, folders, links, tags
from backend.domain.works import commands
from backend.domain.entity_ids import generate
from backend.domain.organization.folder_rules import FolderRuleError, scope_key
from tests import acting_context as facade_context
from tests.pg.test_tags import OrganizationTestCase
from backend.web.etags import etag_for_representation
from tests.narrowing import require
from tests.pg.answers import AnswerAssertions
from backend.domain.organization import tag_commands


class FolderTestCase(OrganizationTestCase):
    def add(self, title: str, parent_id: Optional[str] = None, **fields: Any) -> str:
        folder_id = folder_commands.create(self.db, self.ctx, title, fields.pop("description", ""), parent_id)
        if fields:
            folder_commands.update(self.db, self.ctx, folder_id, fields)
        return folder_id

    def refused(self, message: str, call: Any, *args: Any) -> None:
        with self.assertRaises(ValueError) as caught:
            call(*args)
        self.assertEqual(str(caught.exception), message)

    def field_revisions(self, folder_id: str) -> dict[str, int]:
        state = folder_commands.sync_state(self.db, self.ctx, folder_id)
        assert state is not None
        return {field: value["revision"] for field, value in state["fields"].items()}

    def work_folder(self, work_id: str) -> tuple[str, int]:
        state = folder_commands.work_state(self.db, self.ctx, work_id)
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
                self.refused(_TAKEN, partial(folder_commands.create, self.db, self.ctx), title)
        strasse = self.add("Straße")
        self.refused(_TAKEN, partial(folder_commands.create, self.db, self.ctx), "STRASSE")
        self.add(unicodedata.normalize("NFC", "Café"))
        self.refused(_TAKEN, partial(folder_commands.create, self.db, self.ctx), unicodedata.normalize("NFD", "CAFÉ"))
        # Diacritics are not folded (§8.1).
        self.assertNotEqual(self.add("Resume"), self.add("Résumé"))
        # Two parents may each hold the title, and a child may share its parent's.
        self.add("Λόγος", logos)
        self.add("ΛΌΓΟΣ", strasse)
        self.refused(_TAKEN, partial(folder_commands.create, self.db, self.ctx), "λόγος", "", logos)
        self.assertEqual(self.count("folders"), 7)

    def test_a_move_or_rename_into_a_taken_title_is_refused(self) -> None:
        drafts, archive = self.add("Drafts"), self.add("Archive")
        nested = self.add("DRAFTS", archive)
        self.refused(_TAKEN, partial(folder_commands.update, self.db, self.ctx), archive, {"title": "drafts"})
        self.refused(_TAKEN, partial(folder_commands.update, self.db, self.ctx), nested, {"parent_id": ""})
        # Rename first, then move: the pair is judged against the new title.
        folder_commands.update(self.db, self.ctx, nested, {"title": "Old drafts", "parent_id": ""})
        self.assertEqual(folders_named(folder_commands.catalog(self.db, self.ctx)), ["Archive", "Drafts", "Old drafts"])
        self.assertEqual(self.field_revisions(nested),
                         {"description": 0, "parent_id": 1, "private_notes": 0, "title": 1})
        folder_commands.update(self.db, self.ctx, drafts, {"title": "  DRAFTS "})
        self.assertEqual(require(folder_commands.detail(self.db, self.ctx, drafts, self.pdfs_dir))["title"], "DRAFTS")

    def test_what_postgresql_cannot_hold_or_the_schema_bounds_is_refused_before_any_write(self) -> None:
        folder_id = self.add("Folder")
        call: Callable[..., Any]
        args: tuple[Any, ...]
        for call, args in ((partial(folder_commands.create, self.db, self.ctx), ("a\x00b",)), (partial(folder_commands.create, self.db, self.ctx), ("x" * 201,)),
                           (partial(folder_commands.create, self.db, self.ctx), ("Ok", "d\x00")),
                           (partial(folder_commands.update, self.db, self.ctx), (folder_id, {"private_notes": "é" * 2001})),
                           (partial(folder_commands.update, self.db, self.ctx), (folder_id, {"description": "\x00"}))):
            with self.subTest(args=[str(arg)[:12] for arg in args]), self.assertRaises(ValueError):
                call(*args)
        self.assertEqual(require(folder_commands.detail(self.db, self.ctx, self.add("é" * 100), self.pdfs_dir))["title"], "é" * 100)  # 200 bytes
        self.assertEqual(require(folder_commands.detail(self.db, self.ctx, self.add("   "), self.pdfs_dir))["title"], "Untitled Folder")
        self.assertEqual(self.field_revisions(folder_id), dict.fromkeys(("description", "parent_id",
                                                                         "private_notes", "title"), 0))


def folders_named(rows: list[dict[str, Any]]) -> list[str]:
    return [row["title"] for row in rows]


class HierarchyTests(FolderTestCase):
    def test_a_cycle_is_refused_at_any_depth(self) -> None:
        a = self.add("A")
        b = self.add("B", a)
        c = self.add("C", b)
        self.refused("Cannot set parent to a subfolder (cycle).", partial(folder_commands.update, self.db, self.ctx),
                     a, {"parent_id": c})
        self.refused("A folder cannot be its own parent.", partial(folder_commands.update, self.db, self.ctx),
                     a, {"parent_id": a})
        self.refused("Parent folder not found.", partial(folder_commands.update, self.db, self.ctx),
                     a, {"parent_id": generate("F")})
        self.refused("Parent folder not found.", partial(folder_commands.create, self.db, self.ctx), "D", "", generate("F"))
        folder_commands.update(self.db, self.ctx, c, {"parent_id": ""})
        folder_commands.update(self.db, self.ctx, a, {"parent_id": c})
        self.assertEqual(require(folder_commands.detail(self.db, self.ctx, c, self.pdfs_dir))["children"][0]["id"], a)
        self.assertEqual(require(folder_commands.detail(self.db, self.ctx, b, self.pdfs_dir))["parent"], {"id": a, "title": "A"})

    def test_only_a_change_advances_a_field(self) -> None:
        folder_id = self.add("Folder", description="About")
        before = folder_commands.detail(self.db, self.ctx, folder_id, self.pdfs_dir)
        folder_commands.update(self.db, self.ctx, folder_id, {"description": " About ", "title": "Folder",
                                                        "parent_id": None, "private_notes": False})
        self.assertEqual(self.field_revisions(folder_id), dict.fromkeys(("description", "parent_id",
                                                                         "private_notes", "title"), 0))
        self.assertEqual(self.revisions("folder-field"), {})
        folder_commands.update(self.db, self.ctx, folder_id, {"private_notes": "Mine", "ignored": "x"})
        folder_commands.update(self.db, self.ctx, folder_id, {"private_notes": "Mine again"})
        self.assertEqual(self.field_revisions(folder_id)["private_notes"], 2)
        assert before is not None
        self.assertEqual(require(folder_commands.detail(self.db, self.ctx, folder_id, self.pdfs_dir))["created_at"], before["created_at"])
        self.refused("Folder not found.", partial(folder_commands.update, self.db, self.ctx), generate("F"), {"title": "X"})


class DeleteTests(FolderTestCase):
    def test_a_folder_with_files_or_subfolders_is_refused(self) -> None:
        parent = self.add("Parent")
        self.add("Child", parent)
        holder = self.add("Holder")
        folder_commands.file_work(self.db, self.ctx, holder, self.library.add_work("W"))
        self.refused("Cannot delete folder: Folder has subfolders. Please move or delete them first.",
                     partial(folder_commands.delete, self.db, self.ctx), parent)
        self.refused("Cannot delete folder: Folder is not empty. Please remove all files first.",
                     partial(folder_commands.delete, self.db, self.ctx), holder)
        self.refused("Folder not found.", partial(folder_commands.delete, self.db, self.ctx), generate("F"))
        self.refused("Folder not found.", partial(folder_commands.delete, self.db, self.ctx), "  ")
        self.assertEqual(self.count("folders"), 3)

    def test_a_delete_removes_its_tag_links_and_tombstones_every_scope(self) -> None:
        folder_id = self.add("Folder")
        kept, removed = self.tag("Kept"), self.tag("Removed")
        tag_commands.tag_folder(self.db, self.ctx, folder_id, kept)
        tag_commands.tag_folder(self.db, self.ctx, folder_id, removed)
        tag_commands.untag_folder(self.db, self.ctx, folder_id, removed)
        folder_commands.update(self.db, self.ctx, folder_id, {"title": "Renamed"})
        folder_commands.delete(self.db, self.ctx, folder_id)
        self.assertEqual((self.count("folders"), self.count("folder_tags"), self.count("tags")), (0, 0, 2))
        # Every field scope, edited or not, and every Folder-Tag scope it ever had.
        self.assertEqual(self.revisions("folder-field"), {
            scope_key(folder_id, "title"): 2, scope_key(folder_id, "description"): 1,
            scope_key(folder_id, "parent_id"): 1, scope_key(folder_id, "private_notes"): 1})
        self.assertEqual(self.revisions("folder-tag"), {links.FOLDER.scope_id(folder_id, kept): 2,
                                                        links.FOLDER.scope_id(folder_id, removed): 3})
        self.assertEqual([row["name"] for row in tag_commands.in_use(self.db, self.ctx)], [])
        # A Folder created again under the id starts from the tombstones.
        self.run_command(lambda scope: folders.insert(scope, folder_id, "Again"))
        self.assertEqual(self.field_revisions(folder_id)["description"], 1)
        self.assertEqual(require(tag_commands.folder_options(self.db, self.ctx, folder_id))["known_absent"], {kept: 2, removed: 3})


    def test_a_delete_in_a_failed_command_leaves_the_folder(self) -> None:
        folder_id = self.add("Drafts")

        class Abort(Exception):
            pass

        def delete_then_fail(scope: LibraryScope) -> None:
            self.assertEqual(folders.delete(scope, folder_id), (True, None))
            raise Abort()

        with self.assertRaises(Abort):
            self.run_command(delete_then_fail)
        self.assertEqual(require(folder_commands.detail(self.db, self.ctx, folder_id, self.pdfs_dir))["title"], "Drafts")

class WorkFilingTests(FolderTestCase):
    def test_filing_moving_and_clearing_advance_the_work_folder(self) -> None:
        a, b = self.add("A"), self.add("B")
        work_id = self.library.add_work("Paper")
        self.assertEqual(self.work_folder(work_id), ("", 0))
        folder_commands.file_work(self.db, self.ctx, a, work_id)
        folder_commands.file_work(self.db, self.ctx, a, work_id)  # already there: nothing changes
        self.assertEqual(self.work_folder(work_id), (a, 1))
        self.refused("This file is already in another folder.", partial(folder_commands.file_work, self.db, self.ctx), b, work_id)
        detail = self.library.get_work(work_id)
        assert detail is not None
        self.assertEqual((detail["folder_id"], detail["folder_title"]), (a, "A"))
        self.assertEqual(self.library.get_all_works()[0]["folder_id"], a)
        self.assertEqual(self.library.get_recently_added_browse()[0]["folder_id"], a)
        folder_commands.move_work(self.db, self.ctx, work_id, b)
        folder_commands.move_work(self.db, self.ctx, work_id, b)
        self.assertEqual(self.work_folder(work_id), (b, 2))
        self.assertEqual({row["title"]: row["work_count"] for row in folder_commands.catalog(self.db, self.ctx)},
                         {"A": 0, "B": 1})
        self.assertEqual(folder_commands.member_work_ids(self.db, self.ctx, b), [work_id])
        folder_commands.move_work(self.db, self.ctx, work_id, None)
        self.assertEqual(self.work_folder(work_id), ("", 3))
        self.assertEqual(require(self.library.get_work(work_id))["folder_title"], None)
        self.assertEqual(self.filed(), [])

    def test_a_missing_work_or_folder_is_refused(self) -> None:
        folder_id, work_id = self.add("A"), self.library.add_work("Paper")
        missing_work, missing_folder = generate("W"), generate("F")
        self.refused("The selected folder no longer exists.", partial(folder_commands.file_work, self.db, self.ctx),
                     missing_folder, work_id)
        self.refused("This file no longer exists.", partial(folder_commands.file_work, self.db, self.ctx), folder_id, missing_work)
        self.refused("Folder not found.", partial(folder_commands.move_work, self.db, self.ctx), work_id, missing_folder)
        self.refused("work not found", partial(folder_commands.move_work, self.db, self.ctx), missing_work, folder_id)
        folder_commands.move_work(self.db, self.ctx, missing_work, "")  # clearing a missing Work changes nothing
        self.refused("folder_id is required", partial(folder_commands.file_work, self.db, self.ctx), " ", work_id)
        self.refused("work_id is required", partial(folder_commands.move_work, self.db, self.ctx), " ", folder_id)
        self.assertEqual((self.filed(), self.revisions("work-folder")), ([], {}))

    def test_the_uncategorized_folder_is_one_root_folder_hidden_while_empty(self) -> None:
        first = folder_commands.ensure_uncategorized(self.db, self.ctx)
        self.assertEqual(folder_commands.ensure_uncategorized(self.db, self.ctx), first)
        self.assertEqual(folder_commands.catalog(self.db, self.ctx), [])
        self.assertTrue(folder_commands.exists(self.db, self.ctx, first))
        folder_commands.file_work(self.db, self.ctx, first, self.library.add_work("W"))
        self.assertEqual(folders_named(folder_commands.catalog(self.db, self.ctx)), ["Uncategorized"])
        self.refused(_TAKEN, partial(folder_commands.create, self.db, self.ctx), "UNCATEGORIZED")

    def test_another_spelling_of_uncategorized_is_that_folder(self) -> None:
        # SQLite fails to create "Uncategorized" here; one root holds the key.
        spelled = self.add("UNCATEGORIZED")
        self.assertEqual(folder_commands.ensure_uncategorized(self.db, self.ctx), spelled)


class WorkDeleteTests(FolderTestCase):
    def test_deleting_a_filed_work_unfiles_it_and_tombstones_its_folder_scope(self) -> None:
        folder_id = self.add("A")
        filed, never = self.library.add_work("Filed"), self.library.add_work("Never filed")
        folder_commands.move_work(self.db, self.ctx, filed, folder_id)
        self.assertIsNotNone(self.library.delete_work_record(filed))
        self.assertIsNotNone(self.library.delete_work_record(never))
        self.assertEqual(self.filed(), [])
        # A never-filed Work's scope is tombstoned from revision 0 too.
        self.assertEqual(self.revisions("work-folder"), {filed: 2, never: 1})
        self.assertEqual(folder_commands.catalog(self.db, self.ctx)[0]["work_count"], 0)
        folder_commands.delete(self.db, self.ctx, folder_id)

    def test_a_missing_folder_reads_as_missing_without_a_data_root(self) -> None:
        folder_id = self.add("A")
        folder_commands.move_work(self.db, self.ctx, self.library.add_work("Filed"), folder_id)
        self.assertIsNone(folder_commands.detail(self.db, self.ctx, "F-missing", None))
        # Finishing a Work summary needs the PDF directory.
        with self.assertRaisesRegex(RuntimeError, "no data root"):
            folder_commands.detail(self.db, self.ctx, folder_id, None)


class IsolationTests(FolderTestCase):
    def test_another_librarys_folders_are_invisible(self) -> None:
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            theirs = folder_commands.create(self.db, self.ctx, "Theirs")
            their_work = self.library.add_work("Their work")
        mine = self.library.add_work("Mine")
        self.assertIsNone(folder_commands.detail(self.db, self.ctx, theirs, self.pdfs_dir))
        self.assertIsNone(folder_commands.sync_state(self.db, self.ctx, theirs))
        self.assertIsNone(folder_commands.work_state(self.db, self.ctx, their_work))
        self.assertFalse(folder_commands.exists(self.db, self.ctx, theirs))
        self.refused("The selected folder no longer exists.", partial(folder_commands.file_work, self.db, self.ctx), theirs, mine)
        self.refused("Parent folder not found.", partial(folder_commands.create, self.db, self.ctx), "Child", "", theirs)
        self.refused("Folder not found.", partial(folder_commands.delete, self.db, self.ctx), theirs)
        self.refused("Folder not found.", partial(folder_commands.update, self.db, self.ctx), theirs, {"title": "Mine"})
        # Titles are per Library.
        self.add("Theirs")
        self.assertEqual(folder_commands.catalog(self.db, self.ctx)[0]["title"], "Theirs")
        with facade_context.acting_as(other.context):
            self.assertEqual([row["id"] for row in folder_commands.catalog(self.db, self.ctx)], [theirs])

    def test_a_reader_reads_folders_but_cannot_change_them(self) -> None:
        folder_id, work_id = self.add("A"), self.library.add_work("W")
        with facade_context.acting_as(self.reader_context()):
            self.assertEqual(require(folder_commands.detail(self.db, self.ctx, folder_id, self.pdfs_dir))["title"], "A")
            for call in (lambda: folder_commands.create(self.db, self.ctx, "B"),
                         lambda: folder_commands.update(self.db, self.ctx, folder_id, {"title": "B"}),
                         lambda: folder_commands.move_work(self.db, self.ctx, work_id, folder_id),
                         lambda: folder_commands.delete(self.db, self.ctx, folder_id),
                         lambda: folder_commands.ensure_uncategorized(self.db, self.ctx)):
                with self.assertRaises(AccessRefused):
                    call()
        self.assertEqual((self.count("folders"), self.filed()), (1, []))


class RaceTests(FolderTestCase):
    def test_two_creates_of_one_root_title_make_one_folder(self) -> None:
        holder, contender = self.race(lambda scope: folders.insert(scope, generate("F"), "Λόγος"),
                                      lambda: folder_commands.create(self.db, self.ctx, "ΛΌΓΟΣ"))
        self.assertIs(holder, True)
        self.assertIsInstance(contender, FolderRuleError)
        self.assertEqual((str(contender), self.count("folders")), (_TAKEN, 1))

    def test_two_first_uncategorized_lookups_make_one_folder(self) -> None:
        # READ COMMITTED, as the facade runs it: the loser finds the winner.
        holder, contender = self.race(lambda scope: folders.ensure_uncategorized(scope, generate("F")),
                                      partial(folder_commands.ensure_uncategorized, self.db, self.ctx))
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
                                      lambda: folder_commands.update(self.db, self.ctx, other, {"title": "SAME"}))
        self.assertIs(holder, True)
        self.assertIsInstance(contender, FolderRuleError)
        self.assertEqual(str(contender), _TAKEN)
        self.assertEqual((require(folder_commands.detail(self.db, self.ctx, other, self.pdfs_dir))["title"], self.field_revisions(other)["title"]),
                         ("Other", 0))

    def test_two_crossed_moves_cannot_close_a_cycle(self) -> None:
        a, b = self.add("A"), self.add("B")
        holder, contender = self.race(lambda scope: folders.set_field(scope, a, "parent_id", b),
                                      lambda: folder_commands.update(self.db, self.ctx, b, {"parent_id": a}))
        self.assertEqual(holder, (True, 1))
        self.assertIsInstance(contender, FolderRuleError)
        self.assertEqual(str(contender), "Cannot set parent to a subfolder (cycle).")
        self.assertEqual(require(folder_commands.detail(self.db, self.ctx, b, self.pdfs_dir))["parent_id"], None)

    def test_a_move_under_a_folder_being_deleted_is_refused(self) -> None:
        doomed, moving = self.add("Doomed"), self.add("Moving")
        holder, contender = self.race(lambda scope: folders.delete(scope, doomed),
                                      lambda: folder_commands.update(self.db, self.ctx, moving, {"parent_id": doomed}))
        self.assertEqual(holder, (True, None))
        self.assertIsInstance(contender, FolderRuleError)
        self.assertEqual(str(contender), "Parent folder not found.")

    def test_a_create_under_a_folder_being_deleted_is_refused(self) -> None:
        doomed = self.add("Doomed")
        holder, contender = self.race(lambda scope: folders.delete(scope, doomed),
                                      lambda: folder_commands.create(self.db, self.ctx, "Child", "", doomed))
        self.assertEqual(holder, (True, None))
        self.assertIsInstance(contender, ValueError)
        self.assertEqual(str(contender), "Parent folder not found.")

    def test_a_delete_waits_for_a_child_created_under_the_folder(self) -> None:
        parent = self.add("Parent")
        holder, contender = self.race(lambda scope: folders.insert(scope, generate("F"), "Child", "", parent),
                                      lambda: folder_commands.delete(self.db, self.ctx, parent))
        self.assertIs(holder, True)
        self.assertIsInstance(contender, ValueError)
        self.assertIn("Folder has subfolders", str(contender))

    def test_filing_into_a_folder_being_deleted_is_refused(self) -> None:
        doomed, work_id = self.add("Doomed"), self.library.add_work("W")
        holder, contender = self.race(lambda scope: folders.delete(scope, doomed),
                                      lambda: folder_commands.file_work(self.db, self.ctx, doomed, work_id))
        self.assertEqual(holder, (True, None))
        self.assertIsInstance(contender, ValueError)
        self.assertEqual(str(contender), "The selected folder no longer exists.")
        self.assertEqual(self.filed(), [])

    def test_a_delete_waits_for_a_filing_into_the_folder(self) -> None:
        folder_id, work_id = self.add("Busy"), self.library.add_work("W")

        def file(scope: Any) -> bool:
            folders.lock_work(scope, work_id)
            return folders.set_work_folder(scope, work_id, folder_id)

        holder, contender = self.race(file, lambda: folder_commands.delete(self.db, self.ctx, folder_id),
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

        holder, contender = self.race(move, lambda: folder_commands.move_work(self.db, self.ctx, work_id, b),
                                      serializable=True)
        self.assertEqual((holder, contender), (True, None))
        self.assertEqual((self.work_folder(work_id), self.filed()), ((b, 2), [(work_id, b)]))

    def test_a_move_of_a_work_being_deleted_is_refused(self) -> None:
        folder_id, work_id = self.add("A"), self.library.add_work("W")
        holder, contender = self.race(lambda scope: commands.delete_work(scope, work_id) is not None,
                                      lambda: folder_commands.move_work(self.db, self.ctx, work_id, folder_id))
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
        tag_commands.tag_folder(self.db, self.ctx, folder_id, tag_id)
        if folder_first:
            # The Tag delete waits on the Folder (FOR KEY SHARE), then finds no link.
            holder, contender = self.race(lambda scope: folders.delete(scope, folder_id),
                                          lambda: tag_commands.delete(self.db, self.ctx, tag_id))
            self.assertEqual(holder, (True, None))
            self.assertEqual(contender["status"], "deleted")
        else:
            # The Folder delete waits on the Tag delete's share of the Folder.
            holder, contender = self.race(lambda scope: tags.delete_tag(scope, tag_id)[0],
                                          lambda: folder_commands.delete(self.db, self.ctx, folder_id))
            self.assertEqual((holder, contender), (True, None))
        # The removal advanced the link's scope once, the Folder's tombstone once.
        self.assertEqual(self.revisions("folder-tag")[links.FOLDER.scope_id(folder_id, tag_id)],
                         2 if folder_first else 3)


_MISSING_FOLDER = "F-" + "0" * 32
_MISSING_WORK = "W-" + "0" * 32


class AnswerTests(AnswerAssertions, FolderTestCase):
    """The JSON each Folder command and read answers, spelled out."""

    def named(self, title: str, parent_id: Optional[str] = None, description: str = "") -> str:
        folder_id = folder_commands.create(self.db, self.actor.context, title, description, parent_id)
        return self.labels.name(folder_id, f"<F {title}>")

    def named_work(self, title: str) -> str:
        return self.labels.name(self.library.add_work(title), f"<W {title}>")

    def detail(self, folder_id: str) -> Optional[dict[str, Any]]:
        return folder_commands.detail(self.db, self.actor.context, folder_id, self.storage.pdfs_dir)

    def catalog(self) -> list[dict[str, Any]]:
        return folder_commands.catalog(self.db, self.actor.context)

    @staticmethod
    def row(label: str, title: str, parent: Optional[str] = None, **fields: Any) -> dict[str, Any]:
        return {"child_count": 0, "created_at": "<s>", "description": "", "id": label, "parent_id": parent,
                "private_notes": "", "title": title, "updated_at": "<s>", "work_count": 0, **fields}

    def test_create_read_and_list(self) -> None:
        db, ctx = self.db, self.actor.context
        root = self.named("Root", description="About")
        child = self.named("Child", root)
        other = self.named("Other")
        for title, parent, message in (("root", None, _TAKEN), ("CHILD", root, _TAKEN),
                                       ("Lost", _MISSING_FOLDER, "Parent folder not found.")):
            with self.subTest(title=title):
                self.assert_refused(lambda: folder_commands.create(db, ctx, title, "", parent), message)
        catalog = self.catalog()
        self.answer(catalog, [self.row("<F Child>", "Child", "<F Root>"), self.row("<F Other>", "Other"),
                              self.row("<F Root>", "Root", child_count=1, description="About")])
        self.assertRegex(etag_for_representation("folders", catalog), r'^W/"prks-folders-3-[0-9a-f]+"$')
        # The same title under another parent.
        self.labels.name(folder_commands.create(db, ctx, "Child", "", other), "<F Other/Child>")
        self.answer(self.detail(other), {
            "children": [{"child_count": 0, "description": "", "id": "<F Other/Child>", "parent_id": "<F Other>",
                          "title": "Child", "work_count": 0}],
            "created_at": "<s>", "description": "", "id": "<F Other>", "parent": None, "parent_id": None,
            "private_notes": "", "tags": [], "title": "Other", "updated_at": "<s>", "works": []})
        self.answer(self.detail(child), {
            "children": [], "id": "<F Child>", "parent": {"id": "<F Root>", "title": "Root"},
            "parent_id": "<F Root>", "title": "Child", "works": []})
        self.assertIsNone(self.detail(_MISSING_FOLDER))
        self.answer(folder_commands.sync_state(db, ctx, child), {
            "fields": {"description": {"revision": 0}, "parent_id": {"revision": 0},
                       "private_notes": {"revision": 0}, "title": {"revision": 0}},
            "folder_id": "<F Child>"})
        self.assertIsNone(folder_commands.sync_state(db, ctx, _MISSING_FOLDER))
        self.assertEqual([folder_commands.exists(db, ctx, folder_id) for folder_id in (root, _MISSING_FOLDER, "  ")],
                         [True, False, False])

    def test_fields_hierarchy_and_their_revisions(self) -> None:
        db, ctx = self.db, self.actor.context
        a = self.named("A")
        b = self.named("B", a)
        twin = self.named("b")
        self.assertIsNone(folder_commands.update(db, ctx, b, {"title": "Renamed", "description": " About ",
                                                             "private_notes": "Mine"}))
        self.assertIsNone(folder_commands.update(db, ctx, b, {"title": "Renamed", "ignored": "x"}))
        for folder_id, fields, message in (
                (a, {"parent_id": a}, "A folder cannot be its own parent."),
                (twin, {"title": "a"}, _TAKEN),
                (twin, {"title": "renamed", "parent_id": a}, _TAKEN),
                (a, {"parent_id": b}, "Cannot set parent to a subfolder (cycle)."),
                (_MISSING_FOLDER, {"title": "X"}, "Folder not found.")):
            with self.subTest(fields=fields):
                self.assert_refused(lambda: folder_commands.update(db, ctx, folder_id, fields), message)
        # Rename first, then move: the pair is judged against the new title.
        folder_commands.update(db, ctx, twin, {"title": "C", "parent_id": a})
        revisions = {folder_id: {field: value["revision"]
                                 for field, value in require(folder_commands.sync_state(db, ctx, folder_id))["fields"].items()}
                     for folder_id in (a, b, twin)}
        self.assertEqual(revisions, {
            a: {"description": 0, "parent_id": 0, "private_notes": 0, "title": 0},
            b: {"description": 1, "parent_id": 0, "private_notes": 1, "title": 1},
            twin: {"description": 0, "parent_id": 1, "private_notes": 0, "title": 1}})
        self.answer(require(self.detail(a))["children"], [
            {"child_count": 0, "description": "", "id": "<F b>", "parent_id": "<F A>", "title": "C", "work_count": 0},
            {"child_count": 0, "description": "About", "id": "<F B>", "parent_id": "<F A>", "title": "Renamed",
             "work_count": 0}])
        self.answer(self.catalog(), [self.row("<F A>", "A", child_count=2), self.row("<F b>", "C", "<F A>"),
                                     self.row("<F B>", "Renamed", "<F A>", description="About",
                                              private_notes="Mine")])

    def test_work_filing_and_the_work_projection(self) -> None:
        db, ctx = self.db, self.actor.context
        a, b = self.named("A"), self.named("B")
        work = self.named_work("Paper")
        self.answer(folder_commands.work_state(db, ctx, work), {"folder_id": "", "revision": 0, "work_id": "<W Paper>"})
        for call, message in ((lambda: folder_commands.file_work(db, ctx, _MISSING_FOLDER, work),
                               "The selected folder no longer exists."),
                              (lambda: folder_commands.move_work(db, ctx, work, _MISSING_FOLDER), "Folder not found."),
                              (lambda: folder_commands.file_work(db, ctx, " ", work), "folder_id is required"),
                              (lambda: folder_commands.move_work(db, ctx, " ", a), "work_id is required")):
            self.assert_refused(call, message)
        folder_commands.move_work(db, ctx, work, a)
        folder_commands.move_work(db, ctx, work, a)  # no change, no revision
        self.answer(folder_commands.work_state(db, ctx, work), {"folder_id": "<F A>", "revision": 1, "work_id": "<W Paper>"})
        self.assert_refused(lambda: folder_commands.file_work(db, ctx, b, work), "This file is already in another folder.")
        self.assertIsNone(folder_commands.file_work(db, ctx, a, work))
        self.answer(require(self.library.get_work(work)), {"folder_id": "<F A>", "folder_title": "A"})
        self.answer([row["folder_id"] for row in self.library.get_all_works()], ["<F A>"])
        self.answer([row["folder_id"] for row in self.library.get_recently_added_browse()], ["<F A>"])
        works = require(self.detail(a))["works"]
        self.answer([row["id"] for row in works], ["<W Paper>"])
        self.assertNotIn("folder_id", works[0])
        self.answer(folder_commands.member_work_ids(db, ctx, a), ["<W Paper>"])
        folder_commands.move_work(db, ctx, work, b)
        self.answer(folder_commands.work_state(db, ctx, work), {"folder_id": "<F B>", "revision": 2, "work_id": "<W Paper>"})
        self.answer([(row["id"], row["work_count"]) for row in self.catalog()], [("<F A>", 0), ("<F B>", 1)])
        folder_commands.move_work(db, ctx, work, None)
        self.answer(folder_commands.work_state(db, ctx, work), {"folder_id": "", "revision": 3, "work_id": "<W Paper>"})
        self.assertIsNone(folder_commands.work_state(db, ctx, _MISSING_WORK))
        self.answer(require(self.library.get_work(work)), {"folder_id": None, "folder_title": None})

    def test_uncategorized_and_related_folders(self) -> None:
        db, ctx = self.db, self.actor.context
        uncategorized = self.labels.name(folder_commands.ensure_uncategorized(db, ctx), "<F Uncategorized>")
        self.assertEqual(folder_commands.ensure_uncategorized(db, ctx), uncategorized)
        self.assertEqual(self.catalog(), [])  # hidden while empty
        talks = self.named("Talks")
        first, second = self.named_work("First"), self.named_work("Second")
        person = self.library.add_person("", "Lee")
        for work in (first, second):
            self.library.add_role(person, work, "Author")
        folder_commands.move_work(db, ctx, first, uncategorized)
        folder_commands.move_work(db, ctx, second, talks)
        self.answer(self.catalog(), [self.row("<F Talks>", "Talks", work_count=1),
                                     self.row("<F Uncategorized>", "Uncategorized", work_count=1)])
        related = {"created_at": "<s>", "description": "", "parent_id": None, "private_notes": "", "updated_at": "<s>"}
        # A Work's related Folders hold its authors' other Works.
        self.answer(folder_commands.related(db, ctx, first), [{**related, "id": "<F Talks>", "title": "Talks"}])
        self.answer(folder_commands.related(db, ctx, second),
                    [{**related, "id": "<F Uncategorized>", "title": "Uncategorized"}])

    def test_delete(self) -> None:
        db, ctx = self.db, self.actor.context
        parent = self.named("Parent")
        self.named("Child", parent)
        busy = self.named("Busy")
        work = self.named_work("W")
        folder_commands.move_work(db, ctx, work, busy)
        for folder_id, message in (
                (parent, "Cannot delete folder: Folder has subfolders. Please move or delete them first."),
                (busy, "Cannot delete folder: Folder is not empty. Please remove all files first."),
                (_MISSING_FOLDER, "Folder not found."), (" ", "Folder not found.")):
            with self.subTest(folder=folder_id):
                self.assert_refused(lambda: folder_commands.delete(db, ctx, folder_id), message)
        folder_commands.move_work(db, ctx, work, "")
        tag_commands.tag_folder(self.db, self.ctx, busy, self.tag("Ethics"))
        self.assertIsNone(folder_commands.delete(db, ctx, busy))
        self.assertIsNone(self.detail(busy))
        self.answer([row["id"] for row in self.catalog()], ["<F Child>", "<F Parent>"])
        self.assertEqual([row["name"] for row in tag_commands.all_tags(self.db, self.ctx)], ["Ethics"])
