"""BibTeX export on PostgreSQL (L-W-2 #631).

:class:`AnswerTests` spells out the entries, byte for byte. The other tests
cover the bound actor and its read authorization, Library isolation of the Work, its
credits and the export profile, ids PostgreSQL text cannot hold, and that
the record, the credits and the profile come from one snapshot.
"""

from __future__ import annotations

import contextvars
import uuid
from typing import Any, Optional
from unittest import mock

from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.works import citation
from tests import acting_context as facade_context
from backend.domain.settings.rules import PRKS_BIBTEX_EXPORT_FIELD_IDS
from backend.domain.works.metadata_rules import DOC_TYPES
from tests.bibtex_entry import assert_one_bibtex_entry
from tests.pg.test_works_facade import WorksFacadeTestCase
from backend.domain.settings import commands as settings_commands
from backend.domain.people import role_commands
from tests import people_fixtures
from backend.domain.works import work_commands
from tests import works_fixtures

_ENTRY = ("@book{Lovelace1843,\n"
          "  title = {Notes},\n"
          "  author = {Lovelace, Ada},\n"
          "  year = {1843},\n"
          "  publisher = {Taylor},\n"
          "}")


class BibtexTestCase(WorksFacadeTestCase):
    def book(self) -> str:
        work_id = works_fixtures.add_work(self.db, self.ctx, self.storage, "Notes", year="1843", publisher="Taylor", doc_type="book")
        role_commands.add(self.db, self.ctx, people_fixtures.add_person(self.db, self.ctx, "Ada", "Lovelace"), work_id, "Author")
        return work_id


class AuthorizationTests(BibtexTestCase):
    def test_a_reader_exports(self) -> None:
        work_id = self.book()
        with facade_context.acting_as(self.reader_context()):
            self.assertEqual(work_commands.bibtex(self.db, self.ctx, work_id), _ENTRY)

    def test_an_unbound_call_is_refused(self) -> None:
        work_id = self.book()
        with self.assertRaises(facade_context.NoRequestContext):
            contextvars.Context().run(lambda: work_commands.bibtex(self.db, self.ctx, work_id))

    def test_a_removed_member_is_refused(self) -> None:
        work_id = self.book()
        reader = self.reader_context()
        self.sql("DELETE FROM prks.library_memberships WHERE library_id = %s AND role = 'reader'",
                 (self.actor.library_id,))
        with facade_context.acting_as(reader):
            with self.assertRaises(AccessRefused) as caught:
                work_commands.bibtex(self.db, self.ctx, work_id)
        # Its session ends with the membership.
        self.assertIs(caught.exception.refusal, Refusal.NOT_AUTHENTICATED)


class IsolationTests(BibtexTestCase):
    def test_another_librarys_work_exports_nothing(self) -> None:
        mine = self.book()
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            theirs = works_fixtures.add_work(self.db, self.ctx, self.storage, "Theirs", year="2000")
            self.assertEqual(work_commands.bibtex(self.db, self.ctx, mine), "")
            self.assertEqual(work_commands.bibtex(self.db, self.ctx, theirs),
                             "@article{Unknown2000,\n  title = {Theirs},\n  year = {2000},\n}")
        self.assertEqual(work_commands.bibtex(self.db, self.ctx, theirs), "")
        self.assertEqual(work_commands.bibtex(self.db, self.ctx, mine), _ENTRY)

    def test_each_library_exports_with_its_own_profile(self) -> None:
        mine = self.book()
        other = self.other_actor()
        with facade_context.acting_as(other.context):
            settings_commands.patch(self.db, self.ctx, {"bibtex_export_fields": {"author": False, "publisher": False}})
        self.assertEqual(work_commands.bibtex(self.db, self.ctx, mine), _ENTRY)
        settings_commands.patch(self.db, self.ctx, {"bibtex_export_fields": {"year": False}})
        self.assertEqual(work_commands.bibtex(self.db, self.ctx, mine), _ENTRY.replace("  year = {1843},\n", ""))

    def test_credits_are_read_from_this_library_only(self) -> None:
        # Another Library's role rows on a Work with the same id would be a
        # key violation, so read the credits directly under the other scope.
        mine = self.book()
        target = self.sql("SELECT primary_manifestation_id AS m FROM prks.works WHERE id = %s", (mine,))[0]["m"]
        other = self.other_actor()
        from backend.domain.identity.library_scope import library_query

        found = library_query(self.db, other.context, lambda scope: (
            citation.citation_target(scope, mine), citation.citation_record(scope, mine),
            citation.target_credits(scope, mine, target), citation.bibtex_inputs(scope, mine)))
        self.assertEqual(found, (None, None, [], None))


class CitationExportTests(BibtexTestCase):
    """``work_commands.citations`` (``POST /api/citations``, #41) on PostgreSQL."""

    def cite(self, ctx: Any, work_ids: list[str], fmt: str) -> dict[str, Any]:
        from backend.domain.works import work_commands

        return work_commands.citations(self.db, ctx, {"work_ids": work_ids, "format": fmt})

    def test_a_reader_exports(self) -> None:
        work_id = self.book()
        out = self.cite(self.reader_context(), [work_id], "bibtex")
        self.assertEqual(out, {"format": "bibtex", "text": _ENTRY, "missing": []})

    def test_another_librarys_work_is_missing_in_both_formats(self) -> None:
        mine = self.book()
        other = self.other_actor()
        for fmt in ("csl-json", "bibtex"):
            with self.subTest(fmt=fmt):
                out = self.cite(other.context, [mine], fmt)
                self.assertEqual(out["missing"], [mine])
                self.assertEqual(out.get("items", []), [])
                self.assertEqual(out.get("text", ""), "")
                self.assertNotIn("Notes", repr(out))

    def test_works_that_share_a_citation_key_get_distinct_keys(self) -> None:
        first, second, third = self.book(), self.book(), self.book()
        untitled = [works_fixtures.add_work(self.db, self.ctx, self.storage, "No credit"), works_fixtures.add_work(self.db, self.ctx, self.storage, "No credit either")]
        text = self.cite(self.actor.context, [first, second, third, *untitled], "bibtex")["text"]
        keys = [line.split("{", 1)[1].rstrip(",") for line in text.splitlines() if line.startswith("@")]
        self.assertEqual(keys, ["Lovelace1843", "Lovelace1843a", "Lovelace1843b",
                                "UnknownUnknownYear", "UnknownUnknownYeara"])
        # One Work alone keeps its plain key.
        self.assertEqual(self.cite(self.actor.context, [second], "bibtex")["text"], _ENTRY)


class MissingTests(BibtexTestCase):
    def test_missing_deleted_and_unstorable_ids_export_nothing(self) -> None:
        deleted = self.book()
        self.assertIsNotNone(work_commands.delete(self.db, self.ctx, deleted))
        for work_id in (deleted, "W-" + "0" * 32, "", "W-\x00", "\x00", "nonsense"):
            with self.subTest(work_id=repr(work_id)):
                self.assertEqual(work_commands.bibtex(self.db, self.ctx, work_id), "")

    def test_an_id_holding_nul_never_reaches_the_database(self) -> None:
        with mock.patch.object(citation, "bibtex_inputs", side_effect=AssertionError("queried")):
            self.assertEqual(work_commands.bibtex(self.db, self.ctx, self.book() + "\x00"), "")


class AuthorTextTests(BibtexTestCase):
    def test_the_author_text_fallback_escapes_special_characters(self) -> None:
        work_id = works_fixtures.add_work(self.db, self.ctx, self.storage, "Escaped Credit", author_text="  Doe, A%na {X}; ; Testerson, B  ", year="2026", doc_type="book")
        bibtex = work_commands.bibtex(self.db, self.ctx, work_id)
        self.assertIn("@book{Doe2026,", bibtex)
        self.assertIn(r"author = {Doe, A\%na \{X\} and Testerson, B}", bibtex)


class SnapshotTests(BibtexTestCase):
    def test_the_record_credits_and_profile_are_one_snapshot(self) -> None:
        work_id = self.book()
        person_id = people_fixtures.add_person(self.db, self.ctx, "Charles", "Babbage")
        original = citation.citation_target
        changed: list[bool] = []

        def target_then_a_concurrent_edit(scope: LibraryScope, *args: Any) -> Any:
            found = original(scope, *args)
            if not changed:
                # Committed by another transaction after this read's first
                # statement, before the record, profile and credits are read.
                role_commands.add(self.db, self.ctx, person_id, work_id, "Editor")
                work_commands.update_metadata(self.db, self.ctx, work_id, {"title": "Renamed"})
                settings_commands.patch(self.db, self.ctx, {"bibtex_export_fields": {"publisher": False}})
                changed.append(True)
            return found

        with mock.patch.object(citation, "citation_target", side_effect=target_then_a_concurrent_edit):
            self.assertEqual(work_commands.bibtex(self.db, self.ctx, work_id), _ENTRY)
        self.assertEqual(changed, [True])
        self.assertEqual(work_commands.bibtex(self.db, self.ctx, work_id), (
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
        self.assertEqual(work_commands.bibtex(self.db, self.ctx, work_id), (
            "@misc{Lovelace1900,\n"
            "  title = {Cited},\n"
            "  author = {Lovelace, Ada},\n"
            "  year = {1900},\n"
            "}"))

_MISSING_WORK = "W-" + "0" * 32


class AnswerTests(BibtexTestCase):
    """The entries, spelled out byte for byte."""

    def work(self, label: str, **fields: Any) -> str:
        fields.setdefault("title", label)
        return works_fixtures.add_work(self.db, self.ctx, self.storage, **fields)

    def person(self, label: str, first_name: str = "", last_name: str = "") -> str:
        del label  # names the Person in the test only
        return people_fixtures.add_person(self.db, self.ctx, first_name, last_name)

    def credit(self, work: str, person: str, role_type: str, credit_name: str = "") -> None:
        role_commands.add(self.db, self.ctx, person, work, role_type, credit_name)

    def manifestation(self, work: str, *, kind: str = "edition", title: Optional[str] = None,
                      doc_type: Optional[str] = None, year: Optional[str] = None, publisher: Optional[str] = None,
                      journal: Optional[str] = None, url: Optional[str] = None) -> str:
        """A further Manifestation of ``work``."""
        manifestation_id = "MF-" + uuid.uuid4().hex.upper()
        self.sql("INSERT INTO prks.manifestations (library_id, id, work_id, kind, title, doc_type, year, publisher,"
                 " journal, url) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                 (self.actor.library_id, manifestation_id, work, kind, title, doc_type, year, publisher, journal,
                  url))
        return manifestation_id

    def role_row(self, work: str, person: str, role_type: str, order_index: int, *,
                 credit_name: Optional[str] = None, manifestation_id: Optional[str] = None) -> None:
        self.sql("INSERT INTO prks.roles (library_id, work_id, person_id, role_type, order_index, credit_name,"
                 " manifestation_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                 (self.actor.library_id, work, person, role_type, order_index, credit_name, manifestation_id))

    def cite(self, work: str, manifestation_id: Optional[str]) -> None:
        self.sql("UPDATE prks.works SET citation_manifestation_id = %s WHERE library_id = %s AND id = %s",
                 (manifestation_id, self.actor.library_id, work))

    def bibtex(self, work: str) -> str:
        entry = work_commands.bibtex(self.db, self.ctx, work)
        self.assertIsInstance(entry, str)
        return entry

    def edited_on(self, work: str, day: str) -> str:
        """Set the Work's last edit to the end of ``day`` (UTC); returns ``day``.

        The urldate is that day, never today's: a fixed past date catches a
        formatter that writes the current date, and the last second of the
        day one that converts to a local time zone.
        """
        self.sql("UPDATE prks.works SET updated_at = %s::timestamptz WHERE library_id = %s AND id = %s",
                 (f"{day} 23:59:59+00", self.actor.library_id, work))
        return day

    def export_fields(self, fields: dict[str, bool]) -> None:
        settings_commands.patch(self.db, self.ctx, {"bibtex_export_fields": fields})

    # -- entries with every line fixed

    def test_a_book_credits_every_exported_role_in_order(self) -> None:
        book = self.work("Book", title="The Analytical Engine", doc_type="book", year="1843",
                              publisher="Taylor", location=" London ;  Paris ;; ", edition="2",
                              isbn="978-0-00-000000-2")
        ada = self.person("Lovelace", first_name="Ada", last_name="Lovelace")
        who = self.person("WHO", last_name="World Health Organization")
        charles = self.person("Babbage", first_name="Charles", last_name="Babbage")
        mary = self.person("Somerville", first_name="Mary", last_name="Somerville")
        plato = self.person("Plato", first_name="Plato", last_name="")
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
        article = self.work("Article", title="On Numbers", doc_type="article", published_date="2019-03-01",
                                 journal="Annals", volume="12", issue="3", pages="1--20",
                                 doi="10.1000/xyz", abstract="Line one.\n  Line two.  ")
        self.credit(article, self.person("Noether", first_name="Emmy", last_name="Noether"), "Author")
        self.credit(article, self.person("Hilbert", first_name="David", last_name="Hilbert"), "Author")
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
        online = self.work("Online", title="A Page", doc_type="online", year="2024",
                                source_kind="pdf", source_url="https://example.org/a%20b?q={x}")
        self.edited_on(online, "2001-02-03")
        self.assertEqual(self.bibtex(online), (
            "@online{Unknown2024,\n"
            "  title = {A Page},\n"
            "  year = {2024},\n"
            "  url = {https://example.org/a\\%20b?q=\\{x\\}},\n"
            "  urldate = {2001-02-03},\n"
            "}"))
        bare = self.work("Bare online", doc_type="online")
        self.edited_on(bare, "2002-03-04")
        self.assertEqual(self.bibtex(bare), (
            "@online{UnknownUnknownYear,\n"
            "  title = {Bare online},\n"
            "  urldate = {2002-03-04},\n"
            "}"))
        saved = self.work("Saved", doc_type="misc", source_kind="pdf", source_url="https://example.org/saved")
        self.edited_on(saved, "2003-04-05")
        self.assertEqual(self.bibtex(saved), (
            "@misc{UnknownUnknownYear,\n"
            "  title = {Saved},\n"
            "  url = {https://example.org/saved},\n"
            "  urldate = {2003-04-05},\n"
            "}"))
        video = self.work("Video", doc_type="online", source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertIn("  url = {https://www.youtube.com/watch?v=dQw4w9WgXcQ},\n", self.bibtex(video))

    def test_a_work_with_only_a_title(self) -> None:
        self.assertEqual(self.bibtex(self.work("Alone")),
                         "@article{UnknownUnknownYear,\n  title = {Alone},\n}")

    # -- the rules, compared across many inputs

    def test_every_entry_type_and_unknown_ones(self) -> None:
        def entry(entry_type: str, title: str, *lines: str) -> str:
            return "".join((f"@{entry_type}{{UnknownUnknownYear,\n  title = {{{title}}},\n", *lines, "}"))

        fields = ("  publisher = {P},\n", "  journal = {J},\n")
        accessed = "  urldate = {2001-02-03},\n"
        for doc_type, entry_type in (*((t, t) for t in DOC_TYPES), ("Book", "book"), (" ONLINE ", "online"),
                                     ("thesis", "misc"), ("", "misc")):
            with self.subTest(doc_type=doc_type):
                work = self.work(f"Type {doc_type!r}", doc_type=doc_type, journal="J", publisher="P")
                self.edited_on(work, "2001-02-03")
                urldate = (accessed,) if entry_type == "online" else ()
                self.assertEqual(self.bibtex(work), entry(entry_type, f"Type {doc_type!r}", *fields, *urldate))
        inferred = self.work("Inferred")
        for patch, lines in (({"doc_type": "online"}, ()), ({"journal": "J"}, fields[1:]),
                             ({"publisher": "P"}, fields)):
            work_commands.update_metadata(self.db, self.ctx, inferred, patch)
            self.edited_on(inferred, "2001-02-03")
            self.assertEqual(self.bibtex(inferred), entry("online", "Inferred", *lines, accessed))

    def test_escaping_whitespace_and_unicode(self) -> None:
        work = self.work("Escapes", title="100% {sure} — Ünïcödé 日本 \\emph{x}", doc_type="book",
                              publisher="  Presses {Universitaires}  ", edition="2e édition",
                              abstract="50% of {all}\ttabs\n\nand  spaces ", year="２０２０")
        self.credit(work, self.person("Gödel", first_name="Kurt", last_name="Gödel"), "Author")
        self.credit(work, self.person("Braces", first_name="{A}", last_name="B%"), "Editor",
                    credit_name="  The {Editor} 100%  ")
        self.assertEqual(self.bibtex(work), (
            "@book{Gödel２０２０,\n"
            "  title = {100\\% \\{sure\\} — Ünïcödé 日本 \\emph\\{x\\}},\n"
            "  author = {Gödel, Kurt},\n"
            "  editor = {The \\{Editor\\} 100\\%},\n"
            "  year = {２０２０},\n"
            "  publisher = {  Presses \\{Universitaires\\}  },\n"
            "  edition = {2e édition},\n"
            "  abstract = {50\\% of \\{all\\}\ttabs\n\nand  spaces },\n"
            "}"))

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
                work = self.work(f"Key {label}", year="2001")
                if person is not None:
                    self.credit(work, self.person(label, **person), "Author", credit_name=credit_name or "")
                self.assertTrue(self.bibtex(work).startswith(start + "\n"))
        edited = self.work("Editor only")
        self.credit(edited, self.person("Ed", first_name="Ed", last_name="Itor"), "Editor")
        self.assertTrue(self.bibtex(edited).startswith("@article{UnknownUnknownYear,\n"))

    def test_author_text_and_linked_authors(self) -> None:
        # Without a linked Author, Author (text) is the credit: Person names
        # split at semicolons, the cite key from the first one's family name.
        text_only = self.work("Text only", year="1999", author_text=" Doe, Jane;; Roe, R. ; ")
        self.assertEqual(self.bibtex(text_only), (
            "@article{Doe1999,\n"
            "  title = {Text only},\n"
            "  author = {Doe, Jane and Roe, R.},\n"
            "  year = {1999},\n"
            "}"))
        plain = self.work("Plain", year="1999", author_text="Ada Lovelace")
        self.assertTrue(self.bibtex(plain).startswith("@article{Lovelace1999,\n"))
        self.export_fields({"author": False})
        self.assertEqual(self.bibtex(text_only), "@article{Doe1999,\n  title = {Text only},\n  year = {1999},\n}")
        self.export_fields({"author": True})
        both_kinds = self.work("Both", year="1999", author_text="Doe, Jane")
        self.credit(both_kinds, self.person("Linked", first_name="Lin", last_name="Ked"), "Author")
        entry = self.bibtex(both_kinds)
        self.assertTrue(entry.startswith("@article{Ked1999,\n"))
        self.assertIn("  author = {Ked, Lin},\n", entry)
        self.assertNotIn("Doe", entry)
        # A linked Author whose Person renders no name leaves the author field
        # and the cite key to Author (text) together.
        nameless = self.work("Nameless", year="1999", author_text="Doe, Jane")
        self.credit(nameless, self.person("Blank", first_name=" ", last_name=" "), "Author")
        entry = self.bibtex(nameless)
        self.assertTrue(entry.startswith("@article{Doe1999,\n"))
        self.assertIn("  author = {Doe, Jane},\n", entry)
        # Nor does a nameless first Author decide the key for a named second.
        self.credit(nameless, self.person("Named", first_name="Ada", last_name="Lovelace"), "Author")
        entry = self.bibtex(nameless)
        self.assertTrue(entry.startswith("@article{Lovelace1999,\n"))
        self.assertIn("  author = {Lovelace, Ada},\n", entry)
        # A Person with no usable name keys on the credit name it exports
        # under, and still takes precedence over Author (text).
        for credit, key in (("Lovelace, Ada", "Lovelace"), ("Ada Lovelace", "Lovelace"),
                            ("{,}", "Unknown")):
            with self.subTest(credit=credit):
                credited = self.work(f"Credited {credit}", year="1999", author_text="Doe, Jane")
                self.credit(credited, self.person(f"Blank {credit}", first_name="", last_name=""),
                            "Author", credit_name=credit)
                entry = self.bibtex(credited)
                self.assertEqual(assert_one_bibtex_entry(self, entry), f"{key}1999")
                self.assertNotIn("Doe", entry)
        # Only a linked Author takes precedence: an Editor leaves the fallback.
        edited = self.work("Edited", year="1999", author_text="Doe, Jane")
        self.credit(edited, self.person("Ed", first_name="Ed", last_name="Itor"), "Editor")
        self.assertIn("  author = {Doe, Jane},\n", self.bibtex(edited))

    def test_a_video_channel_is_one_corporate_author(self) -> None:
        channel = self.work("Channel", doc_type="online", year="2025",
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
        explicit = self.work("Explicit", doc_type="online", author_text="The Royal Institution",
                                  source_kind="video", source_url="https://youtu.be/dQw4w9WgXcQ")
        self.assertIn("  author = {{The Royal Institution}},\n", self.bibtex(explicit))
        self.assertTrue(self.bibtex(explicit).startswith("@online{TheUnknownYear,\n"))
        # A linked Author still wins on a video.
        self.credit(explicit, self.person("Faraday", first_name="Michael", last_name="Faraday"), "Author")
        self.assertIn("  author = {Faraday, Michael},\n", self.bibtex(explicit))

    def test_a_video_is_known_by_its_stream(self) -> None:
        # The stream Asset makes a Work a video: PostgreSQL stores no source kind.
        video = self.work("Stream", doc_type="online", year="2010", author_text="Numberphile; Brady Haran",
                          source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        entry = self.bibtex(video)
        self.assertTrue(entry.startswith("@online{Numberphile2010,\n"))
        self.assertIn("  author = {{Numberphile; Brady Haran}},\n", entry)

    def test_a_url_never_makes_a_pdf_a_video(self) -> None:
        # A fileless PDF keeps its URL as provenance, even a YouTube one, and
        # reads back on PostgreSQL with no source kind at all.
        for url in ("https://example.org/p", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"):
            with self.subTest(url=url):
                saved = self.work(f"Saved {url}", doc_type="online", year="2024",
                                       author_text="Given Family; Other Person", source_kind="pdf", source_url=url)
                entry = self.bibtex(saved)
                self.assertTrue(entry.startswith("@online{Family2024,\n"))
                self.assertIn("  author = {Given Family and Other Person},\n", entry)
        # A cited Manifestation with a video-shaped URL but no stream Asset is
        # not a video either, though the Work's primary is.
        video = self.work("Primary video", doc_type="online", year="2024", author_text="Chan Nel; Two",
                               source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        cited = self.manifestation(video, kind="web_page", title="Transcript", doc_type="misc",
                                        year="2024", url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertIn("  author = {{Chan Nel; Two}},\n", self.bibtex(video))
        self.cite(video, cited)
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
                work = self.work(f"Linked {label}", year="2001")
                self.credit(work, self.person(label, **person), "Author")
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
                work = self.work(f"Text {label}", **fields)
                self.assertEqual(assert_one_bibtex_entry(self, self.bibtex(work)), key)

    def test_unpaired_braces_never_break_the_entry(self) -> None:
        work = self.work("Close} early {", doc_type="book", year="2002", publisher="P }{ Q",
                              author_text="Doe, J}; Roe, {R", abstract="a } b { c")
        self.credit(work, self.person("Ed", first_name="E{", last_name="Ditor"), "Editor",
                    credit_name="} Credit {")
        entry = self.bibtex(work)
        assert_one_bibtex_entry(self, entry)
        self.assertIn("  title = {Close{\\textbraceright} early {\\textbraceleft}},\n", entry)
        self.assertIn("  author = {Doe, J{\\textbraceright} and Roe, {\\textbraceleft}R},\n", entry)
        self.assertIn("  publisher = {P {\\textbraceright}{\\textbraceleft} Q},\n", entry)
        # Braces pair within one name only, so "and" still separates names.
        split = self.work("Split names", year="2002", author_text="Doe, {J; Roe, R}")
        for name in ("Lone {open", "Lone close}"):
            self.credit(split, self.person(name, first_name="", last_name=name), "Editor")
        entry = self.bibtex(split)
        assert_one_bibtex_entry(self, entry)
        self.assertIn("  author = {Doe, {\\textbraceleft}J and Roe, R{\\textbraceright}},\n", entry)
        self.assertIn("  editor = {Lone {\\textbraceleft}open and Lone close{\\textbraceright}},\n", entry)
        texts = self.work("Text braces", year="2001", author_text="Doe}\\,{x, Ann; } Roe {")
        self.assertIn("  author = {Doe{\\textbraceright}\\,{\\textbraceleft}x, Ann and "
                      "{\\textbraceright} Roe {\\textbraceleft}},\n", self.bibtex(texts))

    def test_roles_follow_order_index_then_insertion(self) -> None:
        work = self.work("Ordered", year="2010")
        people = [self.person(name, first_name="", last_name=name) for name in ("A", "B", "C", "D", "E")]
        for person, order_index in zip(people, (3, 1, 1, 0, 3)):
            self.role_row(work, person, "Author", order_index)
        self.role_row(work, people[0], "Editor", 2, credit_name="Credited A")
        self.role_row(work, people[1], "Editor", 0)
        entry = self.bibtex(work)
        self.assertTrue(entry.startswith("@article{D2010,\n"))
        self.assertIn("  author = {D and B and C and A and E},\n", entry)
        self.assertIn("  editor = {B and Credited A},\n", entry)

    def test_the_citation_manifestation_and_its_credits(self) -> None:
        work = self.work("Primary", year="2020", doc_type="article", journal="Primary Journal")
        ada = self.person("Lovelace", first_name="Ada", last_name="Lovelace")
        charles = self.person("Babbage", first_name="Charles", last_name="Babbage")
        self.credit(work, ada, "Author")
        cited = self.manifestation(work, kind="published", title="Cited Edition", doc_type="book",
                                        year="2026", publisher="Citation Press", url="https://example.org/cited")
        other = self.manifestation(work, kind="translation", title="Other")
        self.role_row(work, charles, "Editor", 5, manifestation_id=cited)
        self.role_row(work, charles, "Translator", 6, manifestation_id=other)
        primary_entry = self.bibtex(work)
        self.assertNotIn("Babbage", primary_entry)
        self.assertIn("  journal = {Primary Journal},\n", primary_entry)

        self.cite(work, cited)
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
        self.cite(work, other)
        entry = self.bibtex(work)
        self.assertIn("  translator = {Babbage, Charles},\n", entry)
        self.assertNotIn("editor", entry)
        self.cite(work, None)
        self.assertEqual(self.bibtex(work), primary_entry)

    def test_every_export_field_toggle(self) -> None:
        work = self.work("Everything", doc_type="misc", year="2000", publisher="P", location="L1; L2",
                              edition="3", journal="J", volume="V", issue="N", pages="1-2", isbn="I",
                              doi="D", abstract="A", source_kind="pdf", source_url="https://example.org/everything")
        for role in ("Author", "Editor", "Translator", "Introduction", "Foreword", "Afterword"):
            self.credit(work, self.person(role, first_name="F", last_name=role), role)
        full = self.bibtex(work)
        for field in PRKS_BIBTEX_EXPORT_FIELD_IDS:
            with self.subTest(field=field):
                self.export_fields({field: False})
                entry = self.bibtex(work)
                self.assertNotEqual(entry, full)
                self.assertNotIn(f"  {field} = ", entry)
                self.export_fields({field: True})
        self.assertEqual(self.bibtex(work), full)
        self.export_fields({"author": False, "url": False})
        self.export_fields({"doi": False})
        self.export_fields({"url": True})
        entry = self.bibtex(work)
        self.assertNotIn("  author = ", entry)
        self.assertNotIn("  doi = ", entry)
        self.assertIn("  url = ", entry)
        self.assertIn("  urldate = ", entry)
        self.assertTrue(entry.startswith("@misc{Author2000,\n  title = {Everything},\n"))
        self.export_fields({field: False for field in PRKS_BIBTEX_EXPORT_FIELD_IDS})
        self.assertEqual(self.bibtex(work), "@misc{Author2000,\n  title = {Everything},\n}")

    def test_missing_deleted_and_unstorable_works_export_nothing(self) -> None:
        deleted = self.work("Deleted", year="2000")
        self.assertIsNotNone(work_commands.delete(self.db, self.ctx, deleted))
        for work in (deleted, _MISSING_WORK, "", "W-\x00", "\x00", "nonsense"):
            with self.subTest(work=repr(work)):
                self.assertEqual(self.bibtex(work), "")

    def test_repeated_reads_are_identical(self) -> None:
        work = self.work("Stable", year="2005", doc_type="online", source_kind="pdf",
                               source_url="https://example.org/s")
        for name in ("A", "B", "C"):
            self.credit(work, self.person(name, first_name=name, last_name=name), "Author")
        first = self.bibtex(work)
        for _ in range(5):
            self.assertEqual(self.bibtex(work), first)
