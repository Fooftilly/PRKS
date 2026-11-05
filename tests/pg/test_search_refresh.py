"""``derived.work_search`` and its one write path on PostgreSQL (L-S-1 #637; §8.3, §8.7).

The folding functions are checked against the §8.7 vectors (Greek tonos and
final sigma, Serbian Cyrillic and Latin, letters whose diacritic is part of
the letter, NFD input, query-token quoting), then ``refresh_work_search``:
the inputs a row is built from, Library isolation, rows of Works that are
gone, and its lock order against ``DELETE_WORK`` and against a concurrent
change of the inputs. Concurrent transactions are paused with events, never
with sleeps.
"""

from __future__ import annotations

import hashlib

import threading
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from backend.db.database import Isolation
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command
from backend.domain.search import refresh
from backend.domain.works import commands
from tests.pg.test_works_facade import WorksFacadeTestCase


class SearchTestCase(WorksFacadeTestCase):
    def command(self, fn: Callable[[LibraryScope], Any]) -> Any:
        return library_command(self.db, self.actor.context, Capability.EDIT_LIBRARY, fn,
                               isolation=Isolation.READ_COMMITTED)

    def refresh(self, *work_ids: str) -> list[str]:
        return self.command(lambda scope: refresh.refresh_work_search(scope, work_ids))

    def row(self, work_id: str) -> dict[str, Any] | None:
        rows = self.sql("SELECT library_id, title_norm, people_norm, text_norm, doc::text AS doc"
                        " FROM derived.work_search WHERE work_id = %s", (work_id,))
        return rows[0] if rows else None

    def scalar(self, query: str, *params: Any) -> Any:
        return next(iter(self.sql(query, params)[0].values()))

    def matches(self, work_id: str, *tokens: str) -> bool:
        """Whether the Work's ``doc`` matches the prefix query of ``tokens`` (§8.3)."""
        return bool(self.scalar(
            "SELECT doc @@ to_tsquery('prks.prks_search', (SELECT string_agg("
            " prks.tsquery_prefix(prks.search_fold(t)), ' & ') FROM unnest(%s::text[]) AS t))"
            " FROM derived.work_search WHERE work_id = %s", list(tokens), work_id))

    @contextmanager
    def paused(self, fn: Callable[[LibraryScope], Any]) -> Iterator[None]:
        """Run ``fn`` in a transaction that stays open while the block runs, then commits."""
        held, release = threading.Event(), threading.Event()
        errors: list[BaseException] = []

        def hold(scope: LibraryScope) -> None:
            fn(scope)
            held.set()
            release.wait(10)

        def run() -> None:
            try:
                self.command(hold)
            except BaseException as exc:  # noqa: BLE001 - reported to the test thread
                errors.append(exc)
                held.set()

        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(held.wait(10))
            yield
        finally:
            release.set()
            thread.join(10)
        self.assertEqual(errors, [])

    def started(self, fn: Callable[[LibraryScope], Any]) -> tuple[threading.Thread, list[Any]]:
        """Start ``fn`` in its own transaction; the list receives its result or error."""
        out: list[Any] = []

        def run() -> None:
            try:
                out.append(self.command(fn))
            except BaseException as exc:  # noqa: BLE001 - reported to the test thread
                out.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        return thread, out

    def wait_for_lock_waiter(self) -> None:
        for _ in range(500):
            if self.scalar("SELECT count(*) FROM pg_stat_activity"
                           " WHERE wait_event_type = 'Lock' AND datname = current_database()"):
                return
            threading.Event().wait(0.02)
        self.fail("no transaction ever waited for the lock")


class FoldingTests(SearchTestCase):
    """The §8.7 folding vectors, on PostgreSQL 18's stock ``unaccent`` rules."""

    def norm(self, text: str) -> str:
        return str(self.scalar("SELECT prks.search_norm(%s)", text))

    def test_search_norm_folds_case_and_diacritics(self) -> None:
        for raw, folded in [("Πολιτεία", "πολιτεια"), ("ΠΟΛΙΤΕΙΑ", "πολιτεια"), ("Σωκράτης", "σωκρατησ"),
                            ("Država", "drzava"), ("ćevapi", "cevapi"), ("Trešnja", "tresnja"),
                            ("Straße", "strasse"), ("Éditions Lumière", "editions lumiere"),
                            ("ϊ", "ι"), ("İstanbul", "istanbul")]:
            with self.subTest(raw=raw):
                self.assertEqual(self.norm(raw), folded)

    def test_letters_whose_diacritic_is_part_of_the_letter_are_kept(self) -> None:
        # й, ћ and ђ are letters of their own and are not folded; ё folds to
        # е, as Russian text commonly writes it. Cyrillic is never
        # transliterated (§16, decision 4).
        for raw, folded in [("йод", "йод"), ("ћевап", "ћевап"), ("Ђорђе", "ђорђе"), ("ёлка", "елка"),
                            ("Држава", "држава")]:
            with self.subTest(raw=raw):
                self.assertEqual(self.norm(raw), folded)

    def test_nfd_input_folds_like_nfc(self) -> None:
        for raw in ("й", "Trešnja", "İstanbul", "Σωκράτης"):
            with self.subTest(raw=raw):
                self.assertEqual(self.norm(unicodedata.normalize("NFD", raw)), self.norm(raw))

    def test_search_token_drops_punctuation_and_search_norm_keeps_it(self) -> None:
        self.assertEqual(self.scalar("SELECT prks.search_token(%s)", "O'Reilly"), "oreilly")
        self.assertEqual(self.norm("O'Reilly"), "o'reilly")
        self.assertEqual(self.scalar("SELECT prks.search_token(%s)", "x%y_z"), "xyz")
        self.assertEqual(self.scalar("SELECT prks.search_token(%s)", "%_"), "")

    def test_like_escape_keeps_wildcards_literal(self) -> None:
        self.assertEqual(self.scalar("SELECT prks.like_escape(%s)", "a\\b%c_d"), "a\\\\b\\%c\\_d")
        self.assertTrue(self.scalar(
            "SELECT 'x%%y' LIKE '%%' || prks.like_escape('%%') || '%%' ESCAPE '\\'"))
        self.assertFalse(self.scalar(
            "SELECT 'xyz' LIKE '%%' || prks.like_escape('_') || '%%' ESCAPE '\\'"))

    def test_query_tokens_are_quoted_by_tsquery_rules(self) -> None:
        query = self.scalar(
            "SELECT to_tsquery('prks.prks_search', (SELECT string_agg("
            " prks.tsquery_prefix(prks.search_fold(t)), ' & ') FROM unnest(%s::text[]) AS t))::text",
            ["a\\b", "O'Reilly", "state"])
        # Only a, b, o, reilly and state prefixes: no stray "e" from an E'…' literal.
        self.assertEqual(query, "'a':* <-> 'b':* & 'o':* <-> 'reilly':* & 'state':*")

    def test_the_configuration_folds_case_sigma_and_diacritics(self) -> None:
        for indexed, query in [("Σωκράτης", "ΣΩΚΡΑΤΗΣ"), ("Σωκράτης", "σωκρατησ"), ("Πολιτεία", "Πολιτ"),
                               ("ćevapi", "cevapi"), ("cevapi", "ćevapi"), ("café2026", "cafe2026"),
                               ("Čaj-2b", "caj-2b"), ("x-ćevap2", "cevap2")]:
            with self.subTest(indexed=indexed, query=query):
                self.assertTrue(self.scalar(
                    "SELECT to_tsvector('prks.prks_search', prks.search_fold(%s))"
                    " @@ to_tsquery('prks.prks_search', prks.tsquery_prefix(prks.search_fold(%s)))",
                    indexed, query))
        self.assertFalse(self.scalar(
            "SELECT to_tsvector('prks.prks_search', prks.search_fold('Држава'))"
            " @@ to_tsquery('prks.prks_search', prks.tsquery_prefix(prks.search_fold('drzava')))"))


class RefreshTests(SearchTestCase):
    def test_a_row_holds_the_displayed_metadata_people_and_research_note(self) -> None:
        work = self.library.add_work("Republic", abstract="On understatement", author_text="Plato, trans.",
                                     text_content="Notes on Trešnja", private_notes="secret plans")
        person = self.library.add_person("Σωκράτης", "Athenaios", aliases="Socrates, Sokrat")
        self.library.add_role(person, work, "Author")
        self.assertEqual(self.refresh(work), [work])
        row = self.row(work)
        assert row is not None
        self.assertEqual(row["library_id"], self.actor.library_id)
        self.assertEqual(row["title_norm"], "republic")
        self.assertEqual(row["people_norm"], "socrates\nsokrat\nσωκρατησ athenaios")
        self.assertEqual(row["text_norm"], "republic plato, trans. on understatement notes on tresnja")
        self.assertTrue(self.matches(work, "republ"))
        self.assertTrue(self.matches(work, "ΣΩΚΡ"))
        self.assertTrue(self.matches(work, "tresnja"))
        self.assertTrue(self.matches(work, "plato"))
        # The Private Note is not searched, as on SQLite.
        self.assertFalse(self.matches(work, "secret"))
        self.assertNotIn("secret", row["text_norm"])

    def test_title_people_abstract_and_notes_carry_their_weights(self) -> None:
        work = self.library.add_work("Alpha", abstract="Gamma", author_text="Beta", text_content="Delta")
        self.refresh(work)
        row = self.row(work)
        assert row is not None
        self.assertEqual(row["doc"], "'alpha':1A 'beta':2B 'delta':4 'gamma':3C")

    def test_a_research_note_too_long_for_a_tsvector_is_capped_in_doc_only(self) -> None:
        # 40000 distinct words (about 1.3 MB): a tsvector of all of them exceeds 1 MB.
        words = [f"w{i:05d}{hashlib.md5(str(i).encode()).hexdigest()}" for i in range(40000)]
        work = self.library.add_work("Long notes", text_content=" ".join(words))
        self.assertEqual(self.refresh(work), [work])
        self.assertTrue(self.matches(work, words[0]))
        self.assertFalse(self.matches(work, words[-1]))  # past the cap: not in doc
        row = self.row(work)
        assert row is not None
        self.assertTrue(row["text_norm"].endswith(words[-1]))  # the infix fallback keeps all of it

    def test_the_primary_manifestation_overrides_title_and_abstract(self) -> None:
        work = self.library.add_work("Work title", abstract="Work abstract")
        self.sql("UPDATE prks.manifestations SET title = 'Version title', abstract = 'Version abstract'"
                 " WHERE id = (SELECT primary_manifestation_id FROM prks.works WHERE id = %s)", (work,))
        self.refresh(work)
        row = self.row(work)
        assert row is not None
        self.assertEqual(row["title_norm"], "version title")
        self.assertIn("version abstract", row["text_norm"])
        self.assertNotIn("work", row["text_norm"])

    def test_people_are_the_works_credits(self) -> None:
        work = self.library.add_work("Credits")
        other = self.library.add_work("Elsewhere")
        credited = self.library.add_person("Ana", "Author")
        uncredited = self.library.add_person("Uma", "Unlinked")
        self.library.add_role(credited, work, "Translator")
        self.library.add_role(uncredited, other, "Author")
        self.refresh(work)
        row = self.row(work)
        assert row is not None
        self.assertEqual(row["people_norm"], "ana author")

    def test_a_refresh_recomputes_an_existing_row(self) -> None:
        work = self.library.add_work("Before")
        self.refresh(work)
        self.sql("UPDATE prks.works SET title = 'After' WHERE id = %s", (work,))
        self.refresh(work)
        row = self.row(work)
        assert row is not None
        self.assertEqual(row["title_norm"], "after")
        self.assertFalse(self.matches(work, "before"))
        self.assertEqual(self.scalar("SELECT count(*) FROM derived.work_search"), 1)

    def test_ids_of_another_library_or_unknown_get_no_row(self) -> None:
        mine = self.library.add_work("Mine")
        other_actor = self.other_actor()
        self.bind(other_actor.context)
        theirs = self.library.add_work("Theirs")
        self.bind(self.actor.context)
        missing = "W-" + "E" * 32
        before = self.row(theirs)
        self.assertEqual(self.refresh(mine, theirs, missing), [mine])
        # Their row (written by their own create) is neither read nor touched.
        self.assertEqual(self.row(theirs), before)
        assert before is not None
        self.assertNotEqual(before["library_id"], self.actor.library_id)
        self.assertIsNone(self.row(missing))

    def test_a_refresh_of_a_gone_work_deletes_its_row(self) -> None:
        work = self.library.add_work("Gone")
        self.refresh(work)
        # A row left behind (say, by a rebuild batch that read the Work
        # before it was deleted) is removed by the next refresh naming it.
        self.sql("INSERT INTO derived.work_search (library_id, work_id, doc, title_norm, people_norm, text_norm)"
                 " VALUES (%s, %s, ''::tsvector, 'stale', '', '')", (self.actor.library_id, "W-" + "D" * 32))
        self.assertEqual(self.refresh("W-" + "D" * 32), [])
        self.assertIsNone(self.row("W-" + "D" * 32))
        self.assertIsNotNone(self.row(work))

    def test_delete_work_deletes_the_row(self) -> None:
        work = self.library.add_work("Deleted")
        self.refresh(work)
        self.library.delete_work_record(work)
        self.assertIsNone(self.row(work))
        # A refresh whose list still names it never recreates it.
        self.assertEqual(self.refresh(work), [])
        self.assertIsNone(self.row(work))

    def test_another_librarys_row_is_never_touched(self) -> None:
        work = self.library.add_work("Shared id space")
        self.refresh(work)
        other_actor = self.other_actor()
        library_command(self.db, other_actor.context, Capability.EDIT_LIBRARY,
                        lambda scope: refresh.refresh_work_search(scope, [work]))
        self.assertIsNotNone(self.row(work))


class RefreshOrderingTests(SearchTestCase):
    """The lock order of §8.3, with deterministic pause points."""

    def test_a_delete_that_locks_first_makes_the_refresh_skip_the_work(self) -> None:
        work = self.library.add_work("Delete first")
        self.refresh(work)
        with self.paused(lambda scope: commands.delete_work(scope, work)):
            thread, out = self.started(lambda scope: refresh.refresh_work_search(scope, [work]))
            self.wait_for_lock_waiter()
        thread.join(10)
        self.assertEqual(out, [[]])
        self.assertIsNone(self.row(work))

    def test_a_refresh_that_locks_first_makes_the_delete_wait_then_remove_the_row(self) -> None:
        work = self.library.add_work("Refresh first")
        with self.paused(lambda scope: refresh.refresh_work_search(scope, [work])):
            thread, out = self.started(lambda scope: commands.delete_work(scope, work))
            self.wait_for_lock_waiter()
            self.assertTrue(thread.is_alive())
        thread.join(10)
        self.assertIsInstance(out[0], commands.DeletedWork)
        self.assertIsNone(self.row(work))

    def test_a_later_input_change_waits_and_recomputes_after_the_slower_refresh(self) -> None:
        work = self.library.add_work("Old title")
        self.refresh(work)

        def rename(scope: LibraryScope) -> list[str]:
            scope.execute("UPDATE prks.works SET title = 'New title'"
                          " WHERE library_id = %(library_id)s AND id = %(work_id)s", {"work_id": work})
            return refresh.refresh_work_search(scope, [work])

        # The paused refresh read the old title and holds its row; the
        # rename commits its title, then waits on that row and recomputes
        # once the old projection is committed, so the newer one wins.
        with self.paused(lambda scope: refresh.refresh_work_search(scope, [work])):
            thread, out = self.started(rename)
            self.wait_for_lock_waiter()
        thread.join(10)
        self.assertEqual(out, [[work]])
        row = self.row(work)
        assert row is not None
        self.assertEqual(row["title_norm"], "new title")

    def test_a_first_refresh_racing_another_inserts_one_row(self) -> None:
        work = self.library.add_work("Two first refreshes")
        with self.paused(lambda scope: refresh.refresh_work_search(scope, [work])):
            thread, out = self.started(lambda scope: refresh.refresh_work_search(scope, [work]))
            self.wait_for_lock_waiter()
        thread.join(10)
        self.assertEqual(out, [[work]])
        self.assertEqual(self.scalar("SELECT count(*) FROM derived.work_search WHERE work_id = %s", work), 1)
