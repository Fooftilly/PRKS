"""BibTeX export on PostgreSQL: what parity cannot show (L-W-2 #631).

``tests/test_works_bibtex_parity.py`` proves the facade writes SQLite's
entry byte for byte. These tests cover what only PostgreSQL has: the bound
actor and its read authorization, Library isolation of the Work, its
credits and the export profile, ids PostgreSQL text cannot hold, and that
the record, the credits and the profile come from one snapshot.
"""

from __future__ import annotations

import contextvars
import uuid
from typing import Any
from unittest import mock

from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.works import citation
from backend.pg_facade import context as facade_context
from tests.pg.test_works_facade import WorksFacadeTestCase

_ENTRY = ("@book{Lovelace1843,\n"
          "  title = {Notes},\n"
          "  author = {Lovelace, Ada},\n"
          "  year = {1843},\n"
          "  publisher = {Taylor},\n"
          "}")


class BibtexTestCase(WorksFacadeTestCase):
    def book(self) -> str:
        work_id = self.library.add_work("Notes", doc_type="book", year="1843", publisher="Taylor")
        self.library.add_role(self.library.add_person("Ada", "Lovelace"), work_id, "Author")
        return work_id


class AuthorizationTests(BibtexTestCase):
    def test_a_reader_exports(self) -> None:
        work_id = self.book()
        with facade_context.acting_as(self.reader_context()):
            self.assertEqual(self.library.generate_bibtex(work_id), _ENTRY)

    def test_an_unbound_call_is_refused(self) -> None:
        work_id = self.book()
        with self.assertRaises(facade_context.NoRequestContext):
            contextvars.Context().run(self.library.generate_bibtex, work_id)

    def test_a_removed_member_is_refused(self) -> None:
        work_id = self.book()
        reader = self.reader_context()
        self.sql("DELETE FROM prks.library_memberships WHERE library_id = %s AND role = 'reader'",
                 (self.actor.library_id,))
        with facade_context.acting_as(reader):
            with self.assertRaises(AccessRefused) as caught:
                self.library.generate_bibtex(work_id)
        # Its session ends with the membership.
        self.assertIs(caught.exception.refusal, Refusal.NOT_AUTHENTICATED)


class IsolationTests(BibtexTestCase):
    def test_another_librarys_work_exports_nothing(self) -> None:
        mine = self.book()
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            theirs = self.library.add_work("Theirs", year="2000")
            self.assertEqual(self.library.generate_bibtex(mine), "")
            self.assertEqual(self.library.generate_bibtex(theirs),
                             "@article{Unknown2000,\n  title = {Theirs},\n  year = {2000},\n}")
        self.assertEqual(self.library.generate_bibtex(theirs), "")
        self.assertEqual(self.library.generate_bibtex(mine), _ENTRY)

    def test_each_library_exports_with_its_own_profile(self) -> None:
        mine = self.book()
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            self.library.patch_app_settings({"bibtex_export_fields": {"author": False, "publisher": False}})
        self.assertEqual(self.library.generate_bibtex(mine), _ENTRY)
        self.library.patch_app_settings({"bibtex_export_fields": {"year": False}})
        self.assertEqual(self.library.generate_bibtex(mine), _ENTRY.replace("  year = {1843},\n", ""))

    def test_credits_are_read_from_this_library_only(self) -> None:
        # Another Library's role rows on a Work with the same id would be a
        # key violation, so read the credits directly under the other scope.
        mine = self.book()
        target = self.sql("SELECT primary_manifestation_id AS m FROM prks.works WHERE id = %s", (mine,))[0]["m"]
        other = self.other_actor()
        from backend.domain.identity.library_scope import library_query

        found = library_query(self.db, other.context, lambda scope: (
            citation.citation_target(scope, mine), citation.citation_record(scope, mine),
            citation.credits(scope, mine, target), citation.bibtex_inputs(scope, mine)))
        self.assertEqual(found, (None, None, [], None))


class MissingTests(BibtexTestCase):
    def test_missing_deleted_and_unstorable_ids_export_nothing(self) -> None:
        deleted = self.book()
        self.assertIsNotNone(self.library.delete_work_record(deleted))
        for work_id in (deleted, "W-" + "0" * 32, "", "W-\x00", "\x00", "nonsense"):
            with self.subTest(work_id=repr(work_id)):
                self.assertEqual(self.library.generate_bibtex(work_id), "")

    def test_an_id_holding_nul_never_reaches_the_database(self) -> None:
        with mock.patch.object(citation, "bibtex_inputs", side_effect=AssertionError("queried")):
            self.assertEqual(self.library.generate_bibtex(self.book() + "\x00"), "")


class SnapshotTests(BibtexTestCase):
    def test_the_record_credits_and_profile_are_one_snapshot(self) -> None:
        work_id = self.book()
        person_id = self.library.add_person("Charles", "Babbage")
        original = citation.citation_target
        changed: list[bool] = []

        def target_then_a_concurrent_edit(scope: LibraryScope, *args: Any) -> Any:
            found = original(scope, *args)
            if not changed:
                # Committed by another transaction after this read's first
                # statement, before the record, profile and credits are read.
                self.library.add_role(person_id, work_id, "Editor")
                self.library.update_work_metadata(work_id, {"title": "Renamed"})
                self.library.patch_app_settings({"bibtex_export_fields": {"publisher": False}})
                changed.append(True)
            return found

        with mock.patch.object(citation, "citation_target", side_effect=target_then_a_concurrent_edit):
            self.assertEqual(self.library.generate_bibtex(work_id), _ENTRY)
        self.assertEqual(changed, [True])
        self.assertEqual(self.library.generate_bibtex(work_id), (
            "@book{Lovelace1843,\n"
            "  title = {Renamed},\n"
            "  author = {Lovelace, Ada},\n"
            "  editor = {Babbage, Charles},\n"
            "  year = {1843},\n"
            "}"))


class CitationTargetTests(BibtexTestCase):
    def test_the_target_is_the_citation_manifestation_else_the_primary(self) -> None:
        work_id = self.book()
        primary = self.sql("SELECT primary_manifestation_id AS m FROM prks.works WHERE id = %s",
                           (work_id,))[0]["m"]
        cited = "MF-" + uuid.uuid4().hex.upper()
        self.sql("INSERT INTO prks.manifestations (library_id, id, work_id, title, doc_type, year)"
                 " VALUES (%s, %s, %s, 'Cited', 'misc', '1900')", (self.actor.library_id, cited, work_id))

        def target() -> Any:
            from backend.domain.identity.library_scope import library_query

            return library_query(self.db, self.actor.context,
                                 lambda scope: citation.citation_target(scope, work_id))

        self.assertEqual(target(), primary)
        self.sql("UPDATE prks.works SET citation_manifestation_id = %s WHERE id = %s", (cited, work_id))
        self.assertEqual(target(), cited)
        self.assertEqual(self.library.generate_bibtex(work_id), (
            "@misc{Lovelace1900,\n"
            "  title = {Cited},\n"
            "  author = {Lovelace, Ada},\n"
            "  year = {1900},\n"
            "}"))
