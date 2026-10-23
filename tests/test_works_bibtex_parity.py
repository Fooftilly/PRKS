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
from tests.bibtex_entry import assert_one_bibtex_entry
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
_PG_EDITED = ("UPDATE prks.works SET updated_at = %(at)s::timestamptz"
              " WHERE library_id = %(library_id)s AND id = %(work_id)s")
_SQLITE_EDITED = "UPDATE works SET updated_at = :at WHERE id = :work_id"


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

    def edited_on(self, work: Pair, day: str) -> str:
        """Set the Work's last edit to the end of ``day`` (UTC) on both sides; returns ``day``.

        The urldate is that day, never today's: a fixed past date catches a
        formatter that writes the current date, and the last second of the
        day one that converts to a local time zone.
        """
        for index, side in enumerate(self.sides()):
            zone = "+00" if side is self.leg and self.parity_backend == POSTGRES else ""
            self._row(side, _PG_EDITED, _SQLITE_EDITED, {"work_id": work[index], "at": f"{day} 23:59:59{zone}"})
        return day

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
        self.edited_on(online, "2001-02-03")
        self.assertEqual(self.bibtex(online), (
            "@online{Unknown2024,\n"
            "  title = {A Page},\n"
            "  year = {2024},\n"
            "  url = {https://example.org/a\\%20b?q=\\{x\\}},\n"
            "  urldate = {2001-02-03},\n"
            "}"))
        bare = self.work_both("Bare online", doc_type="online")
        self.edited_on(bare, "2002-03-04")
        self.assertEqual(self.bibtex(bare), (
            "@online{UnknownUnknownYear,\n"
            "  title = {Bare online},\n"
            "  urldate = {2002-03-04},\n"
            "}"))
        saved = self.work_both("Saved", doc_type="misc", source_kind="pdf", source_url="https://example.org/saved")
        self.edited_on(saved, "2003-04-05")
        self.assertEqual(self.bibtex(saved), (
            "@misc{UnknownUnknownYear,\n"
            "  title = {Saved},\n"
            "  url = {https://example.org/saved},\n"
            "  urldate = {2003-04-05},\n"
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
        # Without a linked Author, Author (text) is the credit: Person names
        # split at semicolons, the cite key from the first one's family name.
        text_only = self.work_both("Text only", year="1999", author_text=" Doe, Jane;; Roe, R. ; ")
        self.assertEqual(self.bibtex(text_only), (
            "@article{Doe1999,\n"
            "  title = {Text only},\n"
            "  author = {Doe, Jane and Roe, R.},\n"
            "  year = {1999},\n"
            "}"))
        plain = self.work_both("Plain", year="1999", author_text="Ada Lovelace")
        self.assertTrue(self.bibtex(plain).startswith("@article{Lovelace1999,\n"))
        self.patch_both({"author": False})
        self.assertEqual(self.bibtex(text_only), "@article{Doe1999,\n  title = {Text only},\n  year = {1999},\n}")
        self.patch_both({"author": True})
        both_kinds = self.work_both("Both", year="1999", author_text="Doe, Jane")
        self.credit(both_kinds, self.person_both("Linked", first_name="Lin", last_name="Ked"), "Author")
        entry = self.bibtex(both_kinds)
        self.assertTrue(entry.startswith("@article{Ked1999,\n"))
        self.assertIn("  author = {Ked, Lin},\n", entry)
        self.assertNotIn("Doe", entry)
        # A linked Author whose Person renders no name leaves the author field
        # and the cite key to Author (text) together.
        nameless = self.work_both("Nameless", year="1999", author_text="Doe, Jane")
        self.credit(nameless, self.person_both("Blank", first_name=" ", last_name=" "), "Author")
        entry = self.bibtex(nameless)
        self.assertTrue(entry.startswith("@article{Doe1999,\n"))
        self.assertIn("  author = {Doe, Jane},\n", entry)
        # Only a linked Author takes precedence: an Editor leaves the fallback.
        edited = self.work_both("Edited", year="1999", author_text="Doe, Jane")
        self.credit(edited, self.person_both("Ed", first_name="Ed", last_name="Itor"), "Editor")
        self.assertIn("  author = {Doe, Jane},\n", self.bibtex(edited))

    def test_a_video_channel_is_one_corporate_author(self) -> None:
        channel = self.work_both("Channel", doc_type="online", year="2025",
                                 author_text="Some Channel; Inc & {Co} 100% and Friends",
                                 source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.edited_on(channel, "2005-06-07")
        self.assertEqual(self.bibtex(channel), (
            "@online{Some2025,\n"
            "  title = {Channel},\n"
            "  author = {{Some Channel; Inc & \\{Co\\} 100\\% and Friends}},\n"
            "  year = {2025},\n"
            "  url = {https://www.youtube.com/watch?v=dQw4w9WgXcQ},\n"
            "  urldate = {2005-06-07},\n"
            "}"))
        explicit = self.work_both("Explicit", doc_type="online", author_text="The Royal Institution",
                                  source_kind="video", source_url="https://youtu.be/dQw4w9WgXcQ")
        self.assertIn("  author = {{The Royal Institution}},\n", self.bibtex(explicit))
        self.assertTrue(self.bibtex(explicit).startswith("@online{TheUnknownYear,\n"))
        # A linked Author still wins on a video.
        self.credit(explicit, self.person_both("Faraday", first_name="Michael", last_name="Faraday"), "Author")
        self.assertIn("  author = {Faraday, Michael},\n", self.bibtex(explicit))

    def test_a_video_whose_stored_kind_is_missing_or_cased_stays_a_video(self) -> None:
        # Legacy SQLite rows keep a NULL or oddly cased works.source_kind; their
        # stream Asset is what makes them a video. PostgreSQL has no such
        # column, only the Asset.
        for stored in (None, "Video", " VIDEO "):
            with self.subTest(stored=stored):
                video = self.work_both(f"Legacy {stored!r}", doc_type="online", year="2010",
                                       author_text="Numberphile; Brady Haran",
                                       source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
                for index, side in enumerate(self.sides()):
                    if side is self.leg and self.parity_backend == POSTGRES:
                        continue
                    with side.db.connection() as conn:
                        conn.execute("UPDATE works SET source_kind = ? WHERE id = ?", (stored, video[index]))
                entry = self.bibtex(video)
                self.assertTrue(entry.startswith("@online{Numberphile2010,\n"))
                self.assertIn("  author = {{Numberphile; Brady Haran}},\n", entry)

    def test_a_url_never_makes_a_pdf_a_video(self) -> None:
        # A fileless PDF keeps its URL as provenance, even a YouTube one, and
        # reads back on PostgreSQL with no source kind at all.
        for url in ("https://example.org/p", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"):
            with self.subTest(url=url):
                saved = self.work_both(f"Saved {url}", doc_type="online", year="2024",
                                       author_text="Given Family; Other Person", source_kind="pdf", source_url=url)
                entry = self.bibtex(saved)
                self.assertTrue(entry.startswith("@online{Family2024,\n"))
                self.assertIn("  author = {Given Family and Other Person},\n", entry)
        # A cited Manifestation with a video-shaped URL but no stream Asset is
        # not a video either, though the Work's primary is.
        video = self.work_both("Primary video", doc_type="online", year="2024", author_text="Chan Nel; Two",
                               source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        cited = self.manifestation_both(video, kind="web_page", title="Transcript", doc_type="misc",
                                        year="2024", url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertIn("  author = {{Chan Nel; Two}},\n", self.bibtex(video))
        self.cite_both(video, cited)
        entry = self.bibtex(video)
        self.assertTrue(entry.startswith("@misc{Nel2024,\n"))
        self.assertIn("  author = {Chan Nel and Two},\n", entry)

    def test_cite_keys_hold_no_entry_structure(self) -> None:
        linked: list[tuple[str, dict[str, str], str]] = [
            ("braced", {"first_name": "A", "last_name": "{Evil},Name\\x y"}, "EvilNamex2001"),
            ("delimiters only", {"first_name": "Real", "last_name": "#%@ ='\"()~"}, "Real2001"),
            ("nothing usable", {"first_name": ",,", "last_name": "{}"}, "Unknown2001"),
            ("hyphen and Unicode", {"first_name": "Ana", "last_name": "Smith-Jönes"}, "Smith-Jönes2001"),
            ("decomposed accent", {"first_name": "Jose", "last_name": "Nu\u0301n\u0303ez"}, "N\u00fa\u00f1ez2001"),
            ("apostrophe", {"first_name": "Pat", "last_name": "O'Brien"}, "OBrien2001"),
        ]
        for label, person, key in linked:
            with self.subTest(label=label):
                work = self.work_both(f"Linked {label}", year="2001")
                self.credit(work, self.person_both(label, **person), "Author")
                self.assertEqual(assert_one_bibtex_entry(self, self.bibtex(work)), key)
        texts: list[tuple[str, dict[str, str], str]] = [
            ("text braces", {"author_text": "Doe}\\,{x, Ann; } Roe {"}, "Doe2001"),
            ("text comma key", {"author_text": "A B,C"}, "A2001"),
            ("text whitespace", {"author_text": "Ann\tvan\u00a0Der\nBerg"}, "Berg2001"),
            ("text structure only", {"author_text": "{ } , @"}, "Unknown2001"),
            ("channel", {"author_text": "{Odd}\\Chan,nel% } and {", "doc_type": "online",
                         "source_url": "https://youtu.be/dQw4w9WgXcQ"}, "OddChannel2001"),
            ("year", {"author_text": "Doe, A", "year": "20 0{1}, @x"}, "Doe2001x"),
            ("year structure only", {"author_text": "Doe, A", "year": " {,} "}, "DoeUnknownYear"),
        ]
        for label, fields, key in texts:
            with self.subTest(label=label):
                fields = {"year": "2001", **fields}
                work = self.work_both(f"Text {label}", **fields)
                self.assertEqual(assert_one_bibtex_entry(self, self.bibtex(work)), key)

    def test_unpaired_braces_never_break_the_entry(self) -> None:
        work = self.work_both("Close} early {", doc_type="book", year="2002", publisher="P }{ Q",
                              author_text="Doe, J}; Roe, {R", abstract="a } b { c")
        self.credit(work, self.person_both("Ed", first_name="E{", last_name="Ditor"), "Editor",
                    credit_name="} Credit {")
        entry = self.bibtex(work)
        assert_one_bibtex_entry(self, entry)
        self.assertIn("  title = {Close{\\textbraceright} early {\\textbraceleft}},\n", entry)
        self.assertIn("  author = {Doe, J{\\textbraceright} and Roe, {\\textbraceleft}R},\n", entry)
        self.assertIn("  publisher = {P {\\textbraceright}{\\textbraceleft} Q},\n", entry)
        # Braces pair within one name only, so "and" still separates names.
        split = self.work_both("Split names", year="2002", author_text="Doe, {J; Roe, R}")
        for name in ("Lone {open", "Lone close}"):
            self.credit(split, self.person_both(name, first_name="", last_name=name), "Editor")
        entry = self.bibtex(split)
        assert_one_bibtex_entry(self, entry)
        self.assertIn("  author = {Doe, {\\textbraceleft}J and Roe, R{\\textbraceright}},\n", entry)
        self.assertIn("  editor = {Lone {\\textbraceleft}open and Lone close{\\textbraceright}},\n", entry)
        texts = self.work_both("Text braces", year="2001", author_text="Doe}\\,{x, Ann; } Roe {")
        self.assertIn("  author = {Doe{\\textbraceright}\\,{\\textbraceleft}x, Ann and "
                      "{\\textbraceright} Roe {\\textbraceleft}},\n", self.bibtex(texts))

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
        self.edited_on(work, "2004-05-06")
        self.assertEqual(self.bibtex(work), (
            "@book{Lovelace2026,\n"
            "  title = {Cited Edition},\n"
            "  author = {Lovelace, Ada},\n"
            "  editor = {Babbage, Charles},\n"
            "  year = {2026},\n"
            "  publisher = {Citation Press},\n"
            "  url = {https://example.org/cited},\n"
            "  urldate = {2004-05-06},\n"
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
