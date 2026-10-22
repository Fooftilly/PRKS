"""BibTeX export: PostgreSQL writes SQLite's entry byte for byte (L-W-2 #631).

Each Work is built the same way on this leg's library and on a reference
SQLite library, and ``generate_bibtex`` must return the identical string on
both. The entry holds no generated id, so the strings are compared as they
are, and the cases with a fixed expectation also assert the whole entry.
Manifestations other than the origin, Manifestation-scoped credits, the
citation pointer and roles with chosen ``order_index`` values have no
command yet, so they are written as rows on both sides.
"""

from __future__ import annotations

import os
import sys
import uuid
from typing import Any, Optional

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.db_manager import PRKS_BIBTEX_EXPORT_FIELD_IDS
from backend.work_metadata_sync import DOC_TYPES
from tests.backend_parity import POSTGRES, parametrize_backends
from tests.people_parity_helpers import PeopleParityTestCase, PeopleSide

Pair = tuple[str, str]
_MISSING_WORK = "W-" + "0" * 32

_PG_MANIFESTATION = """
INSERT INTO prks.manifestations (library_id, id, work_id, kind, title, doc_type, year, publisher, journal, url)
VALUES (%(library_id)s, %(id)s, %(work_id)s, %(kind)s, %(title)s, %(doc_type)s, %(year)s, %(publisher)s,
        %(journal)s, %(url)s)
"""
_SQLITE_MANIFESTATION = """
INSERT INTO manifestations (id, work_id, kind, title, doc_type, year, publisher, journal, url)
VALUES (:id, :work_id, :kind, :title, :doc_type, :year, :publisher, :journal, :url)
"""
_PG_ROLE = """
INSERT INTO prks.roles (library_id, work_id, person_id, role_type, order_index, credit_name, manifestation_id)
VALUES (%(library_id)s, %(work_id)s, %(person_id)s, %(role_type)s, %(order_index)s, %(credit_name)s,
        %(manifestation_id)s)
"""
_SQLITE_ROLE = """
INSERT INTO roles (work_id, person_id, role_type, order_index, credit_name, manifestation_id)
VALUES (:work_id, :person_id, :role_type, :order_index, :credit_name, :manifestation_id)
"""
_PG_CITE = ("UPDATE prks.works SET citation_manifestation_id = %(manifestation_id)s"
            " WHERE library_id = %(library_id)s AND id = %(work_id)s")
_SQLITE_CITE = "UPDATE works SET citation_manifestation_id = :manifestation_id WHERE id = :work_id"


@parametrize_backends
class BibtexParityTests(PeopleParityTestCase):
    # -- building the same Work on both sides

    def work_both(self, label: str, **fields: Any) -> Pair:
        fields.setdefault("title", label)
        return self.leg.work(label, **fields), self.ref.work(label, **fields)

    def credit(self, work: Pair, person: Pair, role_type: str, credit_name: str = "") -> None:
        for index, side in enumerate(self.sides()):
            side.db.add_role(person[index], work[index], role_type, credit_name=credit_name)

    def _row(self, side: PeopleSide, pg_sql: str, sqlite_sql: str, params: dict[str, Any]) -> None:
        if side is self.leg and self.parity_backend == POSTGRES:
            from backend.pg_facade.context import current_context

            values = {**params, "library_id": current_context().library_id}
            side.db.parity_library.database.command(lambda tx: tx.execute(pg_sql, values))
            return
        with side.db.connection() as conn:
            conn.execute(sqlite_sql, params)

    def manifestation_both(self, work: Pair, *, kind: str = "edition", title: Optional[str] = None,
                           doc_type: Optional[str] = None, year: Optional[str] = None,
                           publisher: Optional[str] = None, journal: Optional[str] = None,
                           url: Optional[str] = None) -> str:
        """A further Manifestation of ``work``, with one id on both sides."""
        manifestation_id = "MF-" + uuid.uuid4().hex.upper()
        for index, side in enumerate(self.sides()):
            self._row(side, _PG_MANIFESTATION, _SQLITE_MANIFESTATION, {
                "id": manifestation_id, "work_id": work[index], "kind": kind, "title": title,
                "doc_type": doc_type, "year": year, "publisher": publisher, "journal": journal, "url": url})
        return manifestation_id

    def role_row_both(self, work: Pair, person: Pair, role_type: str, order_index: int, *,
                      credit_name: Optional[str] = None, manifestation_id: Optional[str] = None) -> None:
        for index, side in enumerate(self.sides()):
            self._row(side, _PG_ROLE, _SQLITE_ROLE, {
                "work_id": work[index], "person_id": person[index], "role_type": role_type,
                "order_index": order_index, "credit_name": credit_name, "manifestation_id": manifestation_id})

    def cite_both(self, work: Pair, manifestation_id: Optional[str]) -> None:
        for index, side in enumerate(self.sides()):
            self._row(side, _PG_CITE, _SQLITE_CITE,
                      {"work_id": work[index], "manifestation_id": manifestation_id})

    # -- comparing

    def bibtex(self, work: Pair) -> str:
        """Both sides' entry; asserts they are the same bytes and returns it."""
        mine = self.leg.db.generate_bibtex(work[0])
        theirs = self.ref.db.generate_bibtex(work[1])
        self.assertIsInstance(mine, str)
        self.assertEqual(mine.encode("utf-8"), theirs.encode("utf-8"))
        return mine

    def accessed(self, work: Pair) -> str:
        """The urldate both sides write: the day the Work was last edited."""
        days = {side.db.get_work(work[index])["updated_at"][:10] for index, side in enumerate(self.sides())}
        self.assertEqual(len(days), 1)
        return days.pop()

    def patch_both(self, fields: dict[str, bool]) -> None:
        self.same("patch_app_settings", {"bibtex_export_fields": fields})

    # -- entries with every line fixed

    def test_a_book_credits_every_exported_role_in_order(self) -> None:
        book = self.work_both("Book", title="The Analytical Engine", doc_type="book", year="1843",
                              publisher="Taylor", location=" London ;  Paris ;; ", edition="2",
                              isbn="978-0-00-000000-2")
        ada = self.person_both("Lovelace", first_name="Ada", last_name="Lovelace")
        who = self.person_both("WHO", last_name="World Health Organization")
        charles = self.person_both("Babbage", first_name="Charles", last_name="Babbage")
        mary = self.person_both("Somerville", first_name="Mary", last_name="Somerville")
        plato = self.person_both("Plato", first_name="Plato", last_name="")
        for person, role in ((ada, "Author"), (who, "Author"), (charles, "Editor"), (mary, "Editor"),
                             (charles, "Translator"), (mary, "Introduction"), (plato, "Foreword"),
                             (ada, "Afterword"), (mary, "Reviewer"), (plato, "Mentioned")):
            self.credit(book, person, role)
        self.assertEqual(self.bibtex(book), (
            "@book{Lovelace1843,\n"
            "  title = {The Analytical Engine},\n"
            "  author = {Lovelace, Ada and World Health Organization},\n"
            "  editor = {Babbage, Charles and Somerville, Mary},\n"
            "  translator = {Babbage, Charles},\n"
            "  introduction = {Somerville, Mary},\n"
            "  foreword = {Plato},\n"
            "  afterword = {Lovelace, Ada},\n"
            "  year = {1843},\n"
            "  publisher = {Taylor},\n"
            "  location = {London and Paris},\n"
            "  edition = {2},\n"
            "  isbn = {978-0-00-000000-2},\n"
            "}"))

    def test_an_article_takes_its_year_from_the_published_date(self) -> None:
        article = self.work_both("Article", title="On Numbers", doc_type="article", published_date="2019-03-01",
                                 journal="Annals", volume="12", issue="3", pages="1--20",
                                 doi="10.1000/xyz", abstract="Line one.\n  Line two.  ")
        self.credit(article, self.person_both("Noether", first_name="Emmy", last_name="Noether"), "Author")
        self.credit(article, self.person_both("Hilbert", first_name="David", last_name="Hilbert"), "Author")
        self.assertEqual(self.bibtex(article), (
            "@article{Noether2019,\n"
            "  title = {On Numbers},\n"
            "  author = {Noether, Emmy and Hilbert, David},\n"
            "  year = {2019},\n"
            "  journal = {Annals},\n"
            "  volume = {12},\n"
            "  number = {3},\n"
            "  pages = {1--20},\n"
            "  doi = {10.1000/xyz},\n"
            "  abstract = {Line one.\n  Line two.  },\n"
            "}"))

    def test_online_and_other_entries_with_a_url_carry_the_access_date(self) -> None:
        online = self.work_both("Online", title="A Page", doc_type="online", year="2024",
                                source_kind="pdf", source_url="https://example.org/a%20b?q={x}")
        self.assertEqual(self.bibtex(online), (
            "@online{Unknown2024,\n"
            "  title = {A Page},\n"
            "  year = {2024},\n"
            "  url = {https://example.org/a\\%20b?q=\\{x\\}},\n"
            f"  urldate = {{{self.accessed(online)}}},\n"
            "}"))
        bare = self.work_both("Bare online", doc_type="online")
        self.assertEqual(self.bibtex(bare), (
            "@online{UnknownUnknownYear,\n"
            "  title = {Bare online},\n"
            f"  urldate = {{{self.accessed(bare)}}},\n"
            "}"))
        saved = self.work_both("Saved", doc_type="misc", source_kind="pdf", source_url="https://example.org/saved")
        self.assertEqual(self.bibtex(saved), (
            "@misc{UnknownUnknownYear,\n"
            "  title = {Saved},\n"
            "  url = {https://example.org/saved},\n"
            f"  urldate = {{{self.accessed(saved)}}},\n"
            "}"))
        video = self.work_both("Video", doc_type="online", source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertIn("  url = {https://www.youtube.com/watch?v=dQw4w9WgXcQ},\n", self.bibtex(video))

    def test_a_work_with_only_a_title(self) -> None:
        self.assertEqual(self.bibtex(self.work_both("Alone")),
                         "@article{UnknownUnknownYear,\n  title = {Alone},\n}")

    # -- the rules, compared across many inputs

    def test_every_entry_type_and_unknown_ones(self) -> None:
        for doc_type in (*DOC_TYPES, "Book", " ONLINE ", "thesis", ""):
            with self.subTest(doc_type=doc_type):
                work = self.work_both(f"Type {doc_type!r}", doc_type=doc_type, journal="J", publisher="P")
                self.bibtex(work)
        inferred = self.work_both("Inferred")
        for fields in ({"doc_type": "online"}, {"journal": "J"}, {"publisher": "P"}):
            self.same_for("update_work_metadata", inferred, fields)
            self.bibtex(inferred)

    def test_escaping_whitespace_and_unicode(self) -> None:
        work = self.work_both("Escapes", title="100% {sure} — Ünïcödé 日本 \\emph{x}", doc_type="book",
                              publisher="  Presses {Universitaires}  ", edition="2e édition",
                              abstract="50% of {all}\ttabs\n\nand  spaces ", year="２０２０")
        self.credit(work, self.person_both("Gödel", first_name="Kurt", last_name="Gödel"), "Author")
        self.credit(work, self.person_both("Braces", first_name="{A}", last_name="B%"), "Editor",
                    credit_name="  The {Editor} 100%  ")
        entry = self.bibtex(work)
        self.assertIn("  title = {100\\% \\{sure\\} — Ünïcödé 日本 \\emph\\{x\\}},\n", entry)
        self.assertIn("  editor = {The \\{Editor\\} 100\\%},\n", entry)

    def test_cite_keys(self) -> None:
        cases: list[tuple[str, Optional[dict[str, str]], Optional[str], str]] = [
            ("no author", None, None, "@article{Unknown2001,"),
            ("mononym", {"first_name": "Homer Poet", "last_name": ""}, None, "@article{Homer2001,"),
            ("particle", {"first_name": "Vincent", "last_name": "van Gogh"}, None, "@article{van2001,"),
            ("credited", {"first_name": "Mary", "last_name": "Shelley"}, "M. W. Godwin", "@article{Shelley2001,"),
            ("nameless", {"first_name": " ", "last_name": " "}, None, "@article{Unknown2001,"),
        ]
        for label, person, credit_name, start in cases:
            with self.subTest(label=label):
                work = self.work_both(f"Key {label}", year="2001")
                if person is not None:
                    self.credit(work, self.person_both(label, **person), "Author", credit_name=credit_name or "")
                self.assertTrue(self.bibtex(work).startswith(start + "\n"))
        edited = self.work_both("Editor only")
        self.credit(edited, self.person_both("Ed", first_name="Ed", last_name="Itor"), "Editor")
        self.assertTrue(self.bibtex(edited).startswith("@article{UnknownUnknownYear,\n"))

    def test_author_text_and_linked_authors(self) -> None:
        # Without a linked Author the entry follows the shared formatter's
        # rule for Author (text), whatever #719 makes it; a linked Author wins.
        text_only = self.work_both("Text only", year="1999", author_text="Doe, Jane; Roe, R.")
        self.bibtex(text_only)
        # #719 reads the source kind to tell a channel name from Person names.
        channel = self.work_both("Channel", doc_type="online", author_text="Some Channel; Inc",
                                 source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.bibtex(channel)
        # A fileless PDF Work keeps its URL as provenance, but PostgreSQL reads
        # its kind back as no source (L-W-1), so a rule that infers the kind
        # from the URL alone would export this entry differently.
        saved = self.work_both("Saved page", doc_type="online", author_text="Given Family",
                               source_kind="pdf", source_url="https://example.org/p")
        self.bibtex(saved)
        both_kinds = self.work_both("Both", year="1999", author_text="Doe, Jane")
        self.credit(both_kinds, self.person_both("Linked", first_name="Lin", last_name="Ked"), "Author")
        entry = self.bibtex(both_kinds)
        self.assertIn("  author = {Ked, Lin},\n", entry)
        self.assertNotIn("Doe", entry)

    def test_roles_follow_order_index_then_insertion(self) -> None:
        work = self.work_both("Ordered", year="2010")
        people = [self.person_both(name, first_name="", last_name=name) for name in ("A", "B", "C", "D", "E")]
        for person, order_index in zip(people, (3, 1, 1, 0, 3)):
            self.role_row_both(work, person, "Author", order_index)
        self.role_row_both(work, people[0], "Editor", 2, credit_name="Credited A")
        self.role_row_both(work, people[1], "Editor", 0)
        entry = self.bibtex(work)
        self.assertTrue(entry.startswith("@article{D2010,\n"))
        self.assertIn("  author = {D and B and C and A and E},\n", entry)
        self.assertIn("  editor = {B and Credited A},\n", entry)

    def test_the_citation_manifestation_and_its_credits(self) -> None:
        work = self.work_both("Primary", year="2020", doc_type="article", journal="Primary Journal")
        ada = self.person_both("Lovelace", first_name="Ada", last_name="Lovelace")
        charles = self.person_both("Babbage", first_name="Charles", last_name="Babbage")
        self.credit(work, ada, "Author")
        cited = self.manifestation_both(work, kind="published", title="Cited Edition", doc_type="book",
                                        year="2026", publisher="Citation Press", url="https://example.org/cited")
        other = self.manifestation_both(work, kind="translation", title="Other")
        self.role_row_both(work, charles, "Editor", 5, manifestation_id=cited)
        self.role_row_both(work, charles, "Translator", 6, manifestation_id=other)
        primary_entry = self.bibtex(work)
        self.assertNotIn("Babbage", primary_entry)
        self.assertIn("  journal = {Primary Journal},\n", primary_entry)

        self.cite_both(work, cited)
        self.assertEqual(self.bibtex(work), (
            "@book{Lovelace2026,\n"
            "  title = {Cited Edition},\n"
            "  author = {Lovelace, Ada},\n"
            "  editor = {Babbage, Charles},\n"
            "  year = {2026},\n"
            "  publisher = {Citation Press},\n"
            "  url = {https://example.org/cited},\n"
            f"  urldate = {{{self.accessed(work)}}},\n"
            "}"))
        self.cite_both(work, other)
        entry = self.bibtex(work)
        self.assertIn("  translator = {Babbage, Charles},\n", entry)
        self.assertNotIn("editor", entry)
        self.cite_both(work, None)
        self.assertEqual(self.bibtex(work), primary_entry)

    def test_every_export_field_toggle(self) -> None:
        work = self.work_both("Everything", doc_type="misc", year="2000", publisher="P", location="L1; L2",
                              edition="3", journal="J", volume="V", issue="N", pages="1-2", isbn="I",
                              doi="D", abstract="A", source_kind="pdf", source_url="https://example.org/everything")
        for role in ("Author", "Editor", "Translator", "Introduction", "Foreword", "Afterword"):
            self.credit(work, self.person_both(role, first_name="F", last_name=role), role)
        full = self.bibtex(work)
        for field in PRKS_BIBTEX_EXPORT_FIELD_IDS:
            with self.subTest(field=field):
                self.patch_both({field: False})
                entry = self.bibtex(work)
                self.assertNotEqual(entry, full)
                self.assertNotIn(f"  {field} = ", entry)
                self.patch_both({field: True})
        self.assertEqual(self.bibtex(work), full)
        self.patch_both({"author": False, "url": False})
        self.patch_both({"doi": False})
        self.patch_both({"url": True})
        entry = self.bibtex(work)
        self.assertNotIn("  author = ", entry)
        self.assertNotIn("  doi = ", entry)
        self.assertIn("  url = ", entry)
        self.assertIn("  urldate = ", entry)
        self.assertTrue(entry.startswith("@misc{Author2000,\n  title = {Everything},\n"))
        self.patch_both({field: False for field in PRKS_BIBTEX_EXPORT_FIELD_IDS})
        self.assertEqual(self.bibtex(work), "@misc{Author2000,\n  title = {Everything},\n}")

    def test_missing_deleted_and_unstorable_works_export_nothing(self) -> None:
        deleted = self.work_both("Deleted", year="2000")
        for index, side in enumerate(self.sides()):
            self.assertIsNotNone(side.db.delete_work_record(deleted[index]))
        for work in (deleted, (_MISSING_WORK, _MISSING_WORK), ("", ""), ("W-\x00", "W-\x00"),
                     ("\x00", "\x00"), ("nonsense", "nonsense")):
            with self.subTest(work=repr(work[0])):
                self.assertEqual(self.bibtex(work), "")

    def test_repeated_reads_are_identical(self) -> None:
        work = self.work_both("Stable", year="2005", doc_type="online", source_kind="pdf",
                               source_url="https://example.org/s")
        for name in ("A", "B", "C"):
            self.credit(work, self.person_both(name, first_name=name, last_name=name), "Author")
        first = self.bibtex(work)
        for _ in range(5):
            self.assertEqual(self.bibtex(work), first)
