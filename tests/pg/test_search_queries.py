"""Work search reads on PostgreSQL (L-S-1 #637; §8.3, §8.7).

:class:`AnswerTests` spells out what each search path lists. The other
tests cover the §8.7
vectors (Unicode folding on both sides of every match, the infix fallback,
whole-string filters that keep punctuation, wildcards that stay literal,
NFD queries), the L-S-2 ranking (#638), and Library isolation.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from backend.domain.search import queries
from tests.pg.answers import AnswerAssertions
from tests.pg.test_search_refresh import SearchTestCase
from backend.domain.organization import tag_commands
from backend.domain.people import publisher_commands
from backend.domain.people import role_commands
from tests import people_fixtures
from backend.domain.identity.library_scope import library_query
from backend.domain.search import search_commands
from tests import works_fixtures
from backend.domain.works import work_commands


class SearchQueryTestCase(SearchTestCase):
    def search(self, text: str, author_filter: str = "", publisher_filter: str = "") -> list[dict]:
        """The keyword search ``GET /api/search`` runs, with its author and publisher filters."""
        return works_fixtures.summaries(self.db, self.ctx, self.storage, lambda scope: search_commands.keyword_rows(
            scope, text, author_filter, publisher_filter))

    def found(self, text: str, **filters: str) -> list[str]:
        return [row["id"] for row in self.search(text, **filters)]


class SplitTests(SearchQueryTestCase):
    def test_python_only_splits_the_query(self) -> None:
        self.assertEqual(queries.split_query("  Multi-Author  multi_word\tΠΟΛΙΤ "),
                         ["Multi", "Author", "multi", "word", "ΠΟΛΙΤ"])
        nfd = unicodedata.normalize("NFD", "й")
        self.assertEqual(queries.split_query(nfd), [nfd])
        self.assertEqual(queries.split_query(" - _ "), [])


class VectorTests(SearchQueryTestCase):
    def test_greek_case_tonos_and_prefix(self) -> None:
        work = works_fixtures.add_work(self.db, self.ctx, self.storage, "Πολιτεία")
        for query in ("πολιτεια", "ΠΟΛΙΤΕΙΑ", "Πολιτ"):
            with self.subTest(query=query):
                self.assertEqual(self.found(query), [work])

    def test_final_sigma(self) -> None:
        work = works_fixtures.add_work(self.db, self.ctx, self.storage, "Σωκράτης")
        self.assertEqual(self.found("σωκρατησ"), [work])
        self.assertEqual(self.found("ΣΩΚΡΑΤΗΣ"), [work])

    def test_serbian_scripts_are_not_transliterated(self) -> None:
        cyrillic = works_fixtures.add_work(self.db, self.ctx, self.storage, "Држава")
        latin = works_fixtures.add_work(self.db, self.ctx, self.storage, "Država")
        self.assertEqual(self.found("drzava"), [latin])
        self.assertEqual(self.found("држава"), [cyrillic])

    def test_diacritics_fold_both_ways(self) -> None:
        accented = works_fixtures.add_work(self.db, self.ctx, self.storage, "ćevapi")
        plain = works_fixtures.add_work(self.db, self.ctx, self.storage, "cevapi")
        self.assertEqual(set(self.found("cevapi")), {accented, plain})
        self.assertEqual(set(self.found("ćevapi")), {accented, plain})

    def test_an_infix_is_found_through_the_abstract_and_the_notes(self) -> None:
        by_abstract = works_fixtures.add_work(self.db, self.ctx, self.storage, "One", abstract="An understatement")
        by_note = works_fixtures.add_work(self.db, self.ctx, self.storage, "Two", text_content="Another understatement")
        self.assertEqual(set(self.found("state")), {by_abstract, by_note})

    def test_a_diacritic_infix_is_normalized_on_both_sides(self) -> None:
        noted = works_fixtures.add_work(self.db, self.ctx, self.storage, "Fruit", text_content="Trešnja in bloom")
        titled = works_fixtures.add_work(self.db, self.ctx, self.storage, "Σωκράτης")
        self.assertEqual(self.found("šnja"), [noted])
        self.assertEqual(self.found("snja"), [noted])
        self.assertEqual(self.found("ωκράτ"), [titled])

    def test_a_wildcard_in_a_query_stays_literal(self) -> None:
        works_fixtures.add_work(self.db, self.ctx, self.storage, "Plain words")
        percent = works_fixtures.add_work(self.db, self.ctx, self.storage, "Rate 50%")
        for query in ("%", "_", "\\", "%%", "_%_"):
            with self.subTest(query=query):
                self.assertEqual(self.found(query), [])
        self.assertEqual(self.found("50%"), [percent])

    def test_whole_string_filters_keep_punctuation(self) -> None:
        press = works_fixtures.add_work(self.db, self.ctx, self.storage, "Book", publisher="O'Reilly Media")
        darcy = works_fixtures.add_work(self.db, self.ctx, self.storage, "Paper", author_text="D'Arcy, J.")
        lumiere = works_fixtures.add_work(self.db, self.ctx, self.storage, "Film", publisher="Éditions Lumière")
        self.assertEqual(library_query(self.db, self.ctx, lambda scope: queries.publisher_work_ids(scope, "O'Reilly")), [press])
        self.assertEqual(library_query(self.db, self.ctx, lambda scope: queries.author_work_ids(scope, "D'Arcy")), [darcy])
        self.assertEqual(library_query(self.db, self.ctx, lambda scope: queries.publisher_work_ids(scope, "lumière")), [lumiere])
        self.assertEqual(library_query(self.db, self.ctx, lambda scope: queries.publisher_work_ids(scope, "LUMIERE")), [lumiere])

    def test_a_quoted_or_backslashed_token_is_one_operand(self) -> None:
        work = works_fixtures.add_work(self.db, self.ctx, self.storage, "O'Reilly state")
        both = works_fixtures.add_work(self.db, self.ctx, self.storage, "a b")
        self.assertEqual(self.found("O'Reilly state"), [work])
        self.assertEqual(self.found("a\\b"), [both])

    def test_nfd_queries_find_what_nfc_queries_find(self) -> None:
        works_fixtures.add_work(self.db, self.ctx, self.storage, "Чайка", text_content="Trešnja")
        works_fixtures.add_work(self.db, self.ctx, self.storage, "İstanbul")
        for text in ("й", "чай", "Trešnja", "šnja", "İstanbul"):
            with self.subTest(text=text):
                nfc = self.found(unicodedata.normalize("NFC", text))
                self.assertEqual(self.found(unicodedata.normalize("NFD", text)), nfc)
        self.assertEqual(len(self.found(unicodedata.normalize("NFD", "й"))), 1)
        self.assertEqual(self.found(unicodedata.normalize("NFD", "и")), [])
        self.assertEqual(len(self.found(unicodedata.normalize("NFD", "šnja"))), 1)


class OrderTests(SearchQueryTestCase):
    def test_the_score_orders_every_phase_one_path(self) -> None:
        # The set is Phase 1's; the order is the L-S-2 score: a title prefix
        # (1.0), a substring of a linked person (0.6), then two infix-only
        # matches (0.15), the more recently updated first.
        by_person = works_fixtures.add_work(self.db, self.ctx, self.storage, "Fourth")
        person = people_fixtures.add_person(self.db, self.ctx, "Malphan", "Quill")
        role_commands.add(self.db, self.ctx, person, by_person, "Author")
        by_override = works_fixtures.add_work(self.db, self.ctx, self.storage, "Second")
        self.sql("UPDATE prks.manifestations SET abstract = 'xalphax'"
                 " WHERE id = (SELECT primary_manifestation_id FROM prks.works WHERE id = %s)", (by_override,))
        self.refresh(by_override)
        by_note = works_fixtures.add_work(self.db, self.ctx, self.storage, "Third", text_content="zalphaz")
        by_prefix = works_fixtures.add_work(self.db, self.ctx, self.storage, "Alphabet")
        self.assertEqual(self.found("alpha"), [by_prefix, by_person, by_note, by_override])

    def test_a_title_outranks_a_note_that_repeats_the_word(self) -> None:
        title = works_fixtures.add_work(self.db, self.ctx, self.storage, "A Theory of Justice")
        works_fixtures.add_work(self.db, self.ctx, self.storage, "Seminar", text_content=" ".join(["justice again"] * 20))
        self.assertEqual(self.found("justice")[0], title)

    def test_a_title_infix_outranks_a_note_prefix(self) -> None:
        compound = works_fixtures.add_work(self.db, self.ctx, self.storage, "Erkenntnistheorie")
        note = works_fixtures.add_work(self.db, self.ctx, self.storage, "Vorlesung", text_content="Theorie am Rande")
        self.assertEqual(self.found("theorie"), [compound, note])

    def test_a_short_exact_title_comes_before_a_longer_one(self) -> None:
        exact = works_fixtures.add_work(self.db, self.ctx, self.storage, "The Republic")
        longer = works_fixtures.add_work(self.db, self.ctx, self.storage, "A Companion to the Long Republic Tradition")
        self.assertEqual(self.found("republic"), [exact, longer])

    def test_each_token_scores_its_best_field(self) -> None:
        # Title and credited person together beat both tokens in a note.
        credited = works_fixtures.add_work(self.db, self.ctx, self.storage, "Republic")
        person = people_fixtures.add_person(self.db, self.ctx, "Aristocles", "Platonides")
        role_commands.add(self.db, self.ctx, person, credited, "Author")
        noted = works_fixtures.add_work(self.db, self.ctx, self.storage, "Log", text_content="republic platonides")
        self.assertEqual(self.found("republic platonides"), [credited, noted])

    def test_an_abstract_prefix_outranks_a_newer_note_prefix(self) -> None:
        # ts_filter reads the stored C and D weights (lowercase labels match
        # them): 0.4 beats 0.2 although recency and the note's density
        # (its repetitions) both favor the note.
        by_abstract = works_fixtures.add_work(self.db, self.ctx, self.storage, "Essays", abstract="Quillon wrote it")
        by_note = works_fixtures.add_work(self.db, self.ctx, self.storage, "Log", text_content=" ".join(["quillon again"] * 10))
        self.assertEqual(self.found("quillon"), [by_abstract, by_note])

    def test_density_breaks_a_tie_before_recency(self) -> None:
        # Same title, same field: the scores are equal, so the denser note
        # comes first although the other Work is newer.
        dense = works_fixtures.add_work(self.db, self.ctx, self.storage, "Log", text_content="quillon and quillon and quillon")
        sparse = works_fixtures.add_work(self.db, self.ctx, self.storage, "Log", text_content="quillon once")
        self.assertEqual(self.found("quillon"), [dense, sparse])

    def test_a_linked_person_outranks_an_abstract_mention(self) -> None:
        mention = works_fixtures.add_work(self.db, self.ctx, self.storage, "Essays", abstract="Quillon is cited once")
        credited = works_fixtures.add_work(self.db, self.ctx, self.storage, "Collected papers")
        person = people_fixtures.add_person(self.db, self.ctx, "Ann", "Quillon")
        role_commands.add(self.db, self.ctx, person, credited, "Author")
        self.assertEqual(self.found("quillon"), [credited, mention])

    def test_an_abstract_of_the_work_itself_is_an_infix_hit(self) -> None:
        # Tier 2 is the primary Manifestation's override only, as on SQLite.
        older = works_fixtures.add_work(self.db, self.ctx, self.storage, "Older", abstract="xbetax")
        newer = works_fixtures.add_work(self.db, self.ctx, self.storage, "Newer", text_content="ybetay")
        self.assertEqual(self.found("beta"), [newer, older])

    def test_inside_a_tier_the_most_recently_updated_comes_first(self) -> None:
        older = works_fixtures.add_work(self.db, self.ctx, self.storage, "Gamma one")
        newer = works_fixtures.add_work(self.db, self.ctx, self.storage, "Gamma two")
        self.assertEqual(self.found("", author_filter=""), [])
        work_commands.update_metadata(self.db, self.ctx, older, {"author_text": "Ann"})
        work_commands.update_metadata(self.db, self.ctx, newer, {"author_text": "Ann"})
        self.assertEqual(self.found("", author_filter="ann"), [newer, older])
        work_commands.update_metadata(self.db, self.ctx, older, {"abstract": "touched"})
        self.assertEqual(self.found("", author_filter="ann"), [older, newer])

    def test_filters_restrict_a_keyword_search(self) -> None:
        kept = works_fixtures.add_work(self.db, self.ctx, self.storage, "Delta", author_text="Ann", publisher="Press")
        works_fixtures.add_work(self.db, self.ctx, self.storage, "Delta", author_text="Ann", publisher="Other")
        works_fixtures.add_work(self.db, self.ctx, self.storage, "Delta", author_text="Bob", publisher="Press")
        self.assertEqual(self.found("delta", author_filter="ann", publisher_filter="press"), [kept])
        self.assertEqual(self.found("delta", author_filter="nobody"), [])

    def test_any_mode_adds_author_and_publisher_matches_after_the_keyword_hits(self) -> None:
        keyword = works_fixtures.add_work(self.db, self.ctx, self.storage, "Orbis")
        by_author = works_fixtures.add_work(self.db, self.ctx, self.storage, "Unrelated", author_text="Orbison")
        by_publisher = works_fixtures.add_work(self.db, self.ctx, self.storage, "Other", publisher="Orbis Press")
        rows = works_fixtures.summaries(self.db, self.ctx, self.storage, lambda scope: search_commands.any_rows(scope, "orbis"))
        ids = [row["id"] for row in rows]
        self.assertEqual(ids[0], keyword)
        self.assertEqual(set(ids), {keyword, by_author, by_publisher})
        self.assertNotIn("text_content", rows[0])


class PublisherRecordTests(SearchQueryTestCase):
    def test_a_matching_record_brings_in_every_label(self) -> None:
        publisher = publisher_commands.create(self.db, self.ctx, "Oxford University Press")
        publisher_commands.add_alias(self.db, self.ctx, publisher["id"], "OUP")
        by_alias = works_fixtures.add_work(self.db, self.ctx, self.storage, "Short", publisher="oup")
        by_name = works_fixtures.add_work(self.db, self.ctx, self.storage, "Long", publisher="Oxford University Press")
        works_fixtures.add_work(self.db, self.ctx, self.storage, "Other", publisher="Cambridge")
        self.assertEqual(library_query(self.db, self.ctx, lambda scope: queries.publisher_work_ids(scope, "oxford")), sorted([by_alias, by_name]))
        self.assertEqual(library_query(self.db, self.ctx, lambda scope: queries.publisher_work_ids(scope, "OUP")), sorted([by_alias, by_name]))


class IsolationTests(SearchQueryTestCase):
    def test_another_librarys_works_are_never_found(self) -> None:
        mine = works_fixtures.add_work(self.db, self.ctx, self.storage, "Shared title", author_text="Ann", publisher="Press")
        self.bind(self.other_actor().context)
        other = works_fixtures.add_work(self.db, self.ctx, self.storage, "Shared title", author_text="Ann", publisher="Press")
        self.bind(self.actor.context)
        self.assertEqual(self.found("shared"), [mine])
        self.assertNotIn(other, library_query(self.db, self.ctx, lambda scope: queries.author_work_ids(scope, "ann")))
        self.assertEqual(library_query(self.db, self.ctx, lambda scope: queries.publisher_work_ids(scope, "press")), [mine])

    def test_another_librarys_tag_of_the_same_name_is_never_used(self) -> None:
        tag = tag_commands.create(self.db, self.ctx, "Physics")
        tag_commands.add_alias(self.db, self.ctx, tag["id"], "phys")
        mine = works_fixtures.add_work(self.db, self.ctx, self.storage, "Mine")
        tag_commands.tag_work(self.db, self.ctx, mine, tag["id"])
        self.bind(self.other_actor().context)
        their_tag = tag_commands.create(self.db, self.ctx, "Physics")
        tag_commands.add_alias(self.db, self.ctx, their_tag["id"], "phys")
        theirs = works_fixtures.add_work(self.db, self.ctx, self.storage, "Theirs")
        tag_commands.tag_work(self.db, self.ctx, theirs, their_tag["id"])
        self.assertEqual([row["id"] for row in works_fixtures.summaries(self.db, self.ctx, self.storage, lambda scope: search_commands.tagged_rows(scope, "phys"))], [theirs])
        self.bind(self.actor.context)
        for label in ("Physics", "phys"):
            with self.subTest(label=label):
                self.assertEqual([row["id"] for row in works_fixtures.summaries(self.db, self.ctx, self.storage, lambda scope, label=label: search_commands.tagged_rows(scope, label))], [mine])


class AnswerTests(AnswerAssertions, SearchQueryTestCase):
    """What keyword search, the filters, the Tag filter and OR mode list, spelled out.

    Each Work is edited a minute after the previous one, so an order never
    rests on a tie.
    """

    def setUp(self) -> None:
        super().setUp()
        self.minute = 0

    def work(self, label: str, **fields: Any) -> str:
        fields.setdefault("title", label)
        work_id = self.labels.name(works_fixtures.add_work(self.db, self.ctx, self.storage, **fields), f"<W {label}>")
        self.minute += 1
        self.sql("UPDATE prks.works SET updated_at = %s::timestamptz WHERE id = %s",
                 (f"2026-01-01 10:{self.minute:02d}:00+00", work_id))
        return work_id

    def credit(self, work_id: str, first_name: str, last_name: str, aliases: str = "") -> None:
        role_commands.add(self.db, self.ctx, people_fixtures.add_person(self.db, self.ctx, first_name, last_name, aliases=aliases), work_id, "Author")

    def listed(self, rows: list[dict[str, Any]]) -> list[str]:
        return [self.labels.show(row["id"]) for row in rows]

    def ids(self, work_ids: list[str]) -> list[str]:
        return sorted(self.labels.show(work_ids))

    def test_keyword_search_finds_every_path(self) -> None:
        # A prefix, an override, a note and an abstract infix, and a linked person.
        self.credit(self.work("Fourth"), "Malphan", "Quill")
        second = self.work("Second")
        self.sql("UPDATE prks.manifestations SET abstract = 'xalphax' WHERE id ="
                 " (SELECT primary_manifestation_id FROM prks.works WHERE id = %s)", (second,))
        self.refresh(second)
        self.work("Third", text_content="zalphaz")
        self.work("Third again", abstract="yalphay")
        self.work("Alphabet")
        self.work("Unrelated")
        self.assertEqual(sorted(self.listed(works_fixtures.summaries(self.db, self.ctx, self.storage, lambda scope: search_commands.keyword_rows(scope, "alpha", '', '')))),
                         ["<W Alphabet>", "<W Fourth>", "<W Second>", "<W Third again>", "<W Third>"])

    def test_tokens_split_on_hyphens_and_must_all_match(self) -> None:
        self.work("Multi-Author survey")
        self.work("Multi survey")
        for query in ("multi author", "Multi-Author", "MULTI_author"):
            with self.subTest(query=query):
                self.answer(works_fixtures.summaries(self.db, self.ctx, self.storage, lambda scope, query=query: search_commands.keyword_rows(scope, query, '', '')), [{
                    "id": "<W Multi-Author survey>", "title": "Multi-Author survey", "status": "Not Started",
                    "doc_type": "article", "created_at": "<s>", "updated_at": "<s>", "linked_people": []}])
        self.assertEqual(works_fixtures.summaries(self.db, self.ctx, self.storage, lambda scope: search_commands.keyword_rows(scope, "nothing here", '', '')), [])
        self.assertEqual(works_fixtures.summaries(self.db, self.ctx, self.storage, lambda scope: search_commands.keyword_rows(scope, "   ", '', '')), [])

    def test_a_linked_person_is_found_by_name_alias_or_last_name(self) -> None:
        self.credit(self.work("Credited"), "Albrecht", "Zweistein", aliases="Doctor Z")
        self.work("Uncredited")
        for query in ("brecht zwei", "albrecht zweistein", "octor", "weistein"):
            with self.subTest(query=query):
                self.assertEqual(self.listed(works_fixtures.summaries(self.db, self.ctx, self.storage, lambda scope, query=query: search_commands.keyword_rows(scope, query, '', ''))), ["<W Credited>"])

    def test_author_and_publisher_filters(self) -> None:
        self.work("Kept", author_text="Ann Lee", publisher="TestPub")
        self.work("Wrong publisher", author_text="Ann Lee", publisher="Other")
        self.work("Wrong author", author_text="Bob", publisher="TestPub")
        self.credit(self.work("Credited", publisher="TestPub"), "Joan", "Annerl")
        search = self.search
        self.assertEqual(self.ids(library_query(self.db, self.ctx, lambda scope: queries.author_work_ids(scope, "ann"))),
                         ["<W Credited>", "<W Kept>", "<W Wrong publisher>"])
        self.assertEqual(self.ids(library_query(self.db, self.ctx, lambda scope: queries.publisher_work_ids(scope, "testpub"))),
                         ["<W Credited>", "<W Kept>", "<W Wrong author>"])
        self.assertEqual(self.listed(search("", author_filter="ann", publisher_filter="testpub")),
                         ["<W Credited>", "<W Kept>"])
        self.assertEqual(self.listed(search("", publisher_filter="other")), ["<W Wrong publisher>"])
        self.assertEqual(self.listed(search("", author_filter="ann", publisher_filter="none")), [])
        self.assertEqual(self.listed(search("kept", author_filter="ann")), ["<W Kept>"])
        self.assertEqual(self.listed(search("kept", author_filter="bob")), [])
        self.assertEqual(library_query(self.db, self.ctx, lambda scope: queries.author_work_ids(scope, "  ")), [])

    def test_a_publisher_record_brings_in_its_names_and_aliases(self) -> None:
        record = publisher_commands.create(self.db, self.ctx, "Oxford University Press")
        publisher_commands.add_alias(self.db, self.ctx, record["id"], "OUP")
        self.work("Short", publisher="oup")
        self.work("Long", publisher="Oxford University Press")
        self.work("Other", publisher="Cambridge")
        for needle in ("oxford", "OUP", "university"):
            with self.subTest(needle=needle):
                self.assertEqual(self.ids(library_query(self.db, self.ctx, lambda scope, needle=needle: queries.publisher_work_ids(scope, needle))), ["<W Long>", "<W Short>"])

    def test_the_tag_filter_lists_tagged_works_newest_first(self) -> None:
        works = [self.work(label) for label in ("Older", "Newer", "Untagged")]
        tag = tag_commands.create(self.db, self.ctx, "Physics")
        tag_commands.add_alias(self.db, self.ctx, tag["id"], "phys")
        for work_id in works[:2]:
            tag_commands.tag_work(self.db, self.ctx, work_id, tag["id"])
        for minute, work_id in enumerate(works, start=1):
            self.sql("UPDATE prks.works SET created_at = %s::timestamptz WHERE id = %s",
                     (f"2025-01-01 09:{minute:02d}:00+00", work_id))
        for label in ("physics", "PHYS", " Physics "):
            with self.subTest(label=label):
                self.assertEqual(self.listed(works_fixtures.summaries(self.db, self.ctx, self.storage, lambda scope, label=label: search_commands.tagged_rows(scope, label))), ["<W Newer>", "<W Older>"])
        self.assertEqual(works_fixtures.summaries(self.db, self.ctx, self.storage, lambda scope: search_commands.tagged_rows(scope, "unknown")), [])
        self.assertEqual(works_fixtures.summaries(self.db, self.ctx, self.storage, lambda scope: search_commands.tagged_rows(scope, "  ")), [])

    def test_any_mode_appends_author_and_publisher_matches(self) -> None:
        self.work("Orbis")
        self.work("By author", author_text="Morbison")
        self.work("By publisher", publisher="Orbis Press")
        self.work("Nothing")
        self.assertEqual(self.listed(works_fixtures.summaries(self.db, self.ctx, self.storage, lambda scope: search_commands.any_rows(scope, "orbis"))),
                         ["<W Orbis>", "<W By author>", "<W By publisher>"])
        self.assertEqual(works_fixtures.summaries(self.db, self.ctx, self.storage, lambda scope: search_commands.any_rows(scope, " ")), [])

