"""Commands keep ``derived.work_search`` current (L-S-1 #637; §8.3).

Every command that changes a search input refreshes the Work's row in its
own transaction: a Work create (with its initial roles), a title, abstract
or author-text change, a Research Note change, a role link or unlink, and a
Person's name or aliases (every Work credited to the Person, including a
credit promoted into an alias). The fan-out races of §8.3 are run with
paused transactions: a rename against a role link of the same Person, in
both orders, and a metadata command committed while a refresh holds the
row.
"""

from __future__ import annotations

from backend.domain.identity.library_scope import LibraryScope
from backend.domain.people import persons, roles
from backend.domain.search import refresh
from backend.domain.works import metadata
from tests.pg.test_search_refresh import SearchTestCase


class CommandRefreshTests(SearchTestCase):
    def norm(self, work_id: str, column: str) -> str:
        row = self.row(work_id)
        assert row is not None, work_id
        return str(row[column])

    def test_a_created_work_has_its_row(self) -> None:
        work = self.library.add_work("Created", abstract="Its abstract", author_text="Its author",
                                     text_content="Its note")
        self.assertEqual(self.norm(work, "text_norm"), "created its author its abstract its note")
        self.assertTrue(self.matches(work, "creat"))

    def test_initial_roles_are_in_the_row(self) -> None:
        work = self.library.add_work("With people")
        person = self.library.add_person("Ana", "Initial")
        self.library.insert_initial_roles(work, [{"person_id": person, "role_type": "Author"}])
        self.assertEqual(self.norm(work, "people_norm"), "ana initial")

    def test_title_abstract_and_author_text_changes_refresh_the_row(self) -> None:
        work = self.library.add_work("Old")
        self.library.update_work_metadata(work, {"title": "Πολιτεία", "abstract": "Infix understatement",
                                                 "author_text": "Plato"})
        self.assertEqual(self.norm(work, "title_norm"), "πολιτεια")
        self.assertEqual(self.norm(work, "text_norm"), "πολιτεια plato infix understatement")
        self.assertTrue(self.matches(work, "ΠΟΛΙΤ"))

    def test_a_field_search_does_not_read_leaves_the_row_alone(self) -> None:
        work = self.library.add_work("Steady")
        before = self.sql("SELECT updated_at FROM derived.work_search WHERE work_id = %s", (work,))
        self.library.update_work_metadata(work, {"status": "In Progress", "year": "1999"})
        after = self.sql("SELECT updated_at FROM derived.work_search WHERE work_id = %s", (work,))
        self.assertEqual(before, after)
        self.assertEqual(metadata.SEARCHED_WORK_COLUMNS, {"title", "abstract", "author_text"})

    def test_a_research_note_change_refreshes_the_row_and_a_private_note_does_not(self) -> None:
        work = self.library.add_work("Noted")
        self.library.update_work_metadata(work, {"text_content": "Trešnja in bloom"})
        self.assertIn("tresnja in bloom", self.norm(work, "text_norm"))
        self.library.update_work_metadata(work, {"private_notes": "hidden words"})
        self.assertNotIn("hidden", self.norm(work, "text_norm"))
        self.library.update_work_metadata(work, {"text_content": ""})
        self.assertEqual(self.norm(work, "text_norm"), "noted")

    def test_role_link_and_unlink_refresh_the_row(self) -> None:
        work = self.library.add_work("Linked")
        person = self.library.add_person("Λόγος", "Writer")
        self.library.add_role(person, work, "Author")
        self.assertEqual(self.norm(work, "people_norm"), "λογοσ writer")
        self.assertTrue(self.matches(work, "λογ"))
        self.library.delete_work_role(work, person, "Author")
        self.assertEqual(self.norm(work, "people_norm"), "")

    def test_a_person_rename_or_alias_change_refreshes_every_credited_work(self) -> None:
        first = self.library.add_work("First")
        second = self.library.add_work("Second")
        unrelated = self.library.add_work("Unrelated")
        person = self.library.add_person("Old", "Name")
        self.library.add_role(person, first, "Author")
        self.library.add_role(person, second, "Editor")
        self.library.update_person_profile(person, {"first_name": "Ćiril", "aliases": "Kyrill"})
        for work in (first, second):
            self.assertEqual(self.norm(work, "people_norm"), "kyrill\nciril name")
        self.assertEqual(self.norm(unrelated, "people_norm"), "")

    def test_a_credit_promoted_into_an_alias_reaches_every_credited_work(self) -> None:
        first = self.library.add_work("First")
        second = self.library.add_work("Second")
        person = self.library.add_person("Fyodor", "Dostoevsky")
        self.library.add_role(person, first, "Author")
        self.library.add_role(person, second, "Author", credit_name="Ф. М. Достоевский")
        # й is a letter of its own and is kept (§8.7).
        promoted = "ф. м. достоевский"
        self.assertIn(promoted, self.norm(first, "people_norm").split("\n"))
        self.assertIn(promoted, self.norm(second, "people_norm").split("\n"))

    def test_a_deleted_work_has_no_row(self) -> None:
        work = self.library.add_work("Doomed")
        self.library.delete_work_record(work)
        self.assertIsNone(self.row(work))


class FanOutRaceTests(SearchTestCase):
    """§8.3 fan-out inputs: the Person row is where a rename and a link meet."""

    def setUp(self) -> None:
        super().setUp()
        self.first = self.library.add_work("First")
        self.second = self.library.add_work("Second")
        self.person = self.library.add_person("Before", "Rename")
        self.library.add_role(self.person, self.first, "Author")

    def rename(self, scope: LibraryScope) -> None:
        self.assertTrue(persons.lock(scope, self.person))
        persons.set_field(scope, self.person, "first_name", "After")

    def link_second(self, scope: LibraryScope) -> None:
        roles.set_role(scope, roles.Role(self.second, self.person, "Author"), True)

    def assert_both_renamed(self) -> None:
        for work in (self.first, self.second):
            row = self.row(work)
            assert row is not None
            self.assertEqual(row["people_norm"], "after rename", work)

    def test_a_rename_committed_while_a_link_waits_reaches_the_new_link(self) -> None:
        with self.paused(self.rename):
            thread, out = self.started(self.link_second)
            self.wait_for_lock_waiter()
        thread.join(10)
        self.assertEqual(out, [None])
        self.assert_both_renamed()

    def test_a_link_committed_while_a_rename_waits_is_renamed_too(self) -> None:
        with self.paused(self.link_second):
            # The link refreshed the second Work with the old name and holds
            # the Person FOR SHARE; the rename waits, then collects it.
            thread, out = self.started(self.rename)
            self.wait_for_lock_waiter()
        thread.join(10)
        self.assertEqual(out, [None])
        self.assert_both_renamed()

    def test_a_metadata_command_committed_while_a_refresh_holds_the_row_wins(self) -> None:
        def retitle(scope: LibraryScope) -> None:
            row = metadata.target(scope, self.first, for_update=True)
            assert row is not None
            metadata.set_synced_field(scope, row, "title", "Retitled")

        # A rebuild batch (any refresh) read the old title and holds the row.
        with self.paused(lambda scope: refresh.refresh_work_search(scope, [self.first])):
            thread, out = self.started(retitle)
            self.wait_for_lock_waiter()
        thread.join(10)
        self.assertEqual(len(out), 1)
        row = self.row(self.first)
        assert row is not None
        self.assertEqual(row["title_norm"], "retitled")
