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


class SearchQueryTestCase(SearchTestCase):
    def found(self, text: str, **filters: str) -> list[str]:
        return [row["id"] for row in self.library.search_works(text, **filters)]


class SplitTests(SearchQueryTestCase):
    def test_python_only_splits_the_query(self) -> None:
        self.assertEqual(queries.split_query("  Multi-Author  multi_word\tΠΟΛΙΤ "),
                         ["Multi", "Author", "multi", "word", "ΠΟΛΙΤ"])
        nfd = unicodedata.normalize("NFD", "й")
        self.assertEqual(queries.split_query(nfd), [nfd])
        self.assertEqual(queries.split_query(" - _ "), [])


class VectorTests(SearchQueryTestCase):
    def test_greek_case_tonos_and_prefix(self) -> None:
        work = self.library.add_work("Πολιτεία")
        for query in ("πολιτεια", "ΠΟΛΙΤΕΙΑ", "Πολιτ"):
            with self.subTest(query=query):
                self.assertEqual(self.found(query), [work])

    def test_final_sigma(self) -> None:
        work = self.library.add_work("Σωκράτης")
        self.assertEqual(self.found("σωκρατησ"), [work])
        self.assertEqual(self.found("ΣΩΚΡΑΤΗΣ"), [work])

    def test_serbian_scripts_are_not_transliterated(self) -> None:
        cyrillic = self.library.add_work("Држава")
        latin = self.library.add_work("Država")
        self.assertEqual(self.found("drzava"), [latin])
        self.assertEqual(self.found("држава"), [cyrillic])

    def test_diacritics_fold_both_ways(self) -> None:
        accented = self.library.add_work("ćevapi")
        plain = self.library.add_work("cevapi")
        self.assertEqual(set(self.found("cevapi")), {accented, plain})
        self.assertEqual(set(self.found("ćevapi")), {accented, plain})

    def test_an_infix_is_found_through_the_abstract_and_the_notes(self) -> None:
        by_abstract = self.library.add_work("One", abstract="An understatement")
        by_note = self.library.add_work("Two", text_content="Another understatement")
        self.assertEqual(set(self.found("state")), {by_abstract, by_note})

    def test_a_diacritic_infix_is_normalized_on_both_sides(self) -> None:
        noted = self.library.add_work("Fruit", text_content="Trešnja in bloom")
        titled = self.library.add_work("Σωκράτης")
        self.assertEqual(self.found("šnja"), [noted])
        self.assertEqual(self.found("snja"), [noted])
        self.assertEqual(self.found("ωκράτ"), [titled])

    def test_a_wildcard_in_a_query_stays_literal(self) -> None:
        self.library.add_work("Plain words")
        percent = self.library.add_work("Rate 50%")
        for query in ("%", "_", "\\", "%%", "_%_"):
            with self.subTest(query=query):
                self.assertEqual(self.found(query), [])
        self.assertEqual(self.found("50%"), [percent])

    def test_whole_string_filters_keep_punctuation(self) -> None:
        press = self.library.add_work("Book", publisher="O'Reilly Media")
        darcy = self.library.add_work("Paper", author_text="D'Arcy, J.")
        lumiere = self.library.add_work("Film", publisher="Éditions Lumière")
        self.assertEqual(self.library.work_ids_matching_publisher("O'Reilly"), [press])
        self.assertEqual(self.library.work_ids_matching_author("D'Arcy"), [darcy])
        self.assertEqual(self.library.work_ids_matching_publisher("lumière"), [lumiere])
        self.assertEqual(self.library.work_ids_matching_publisher("LUMIERE"), [lumiere])

    def test_a_quoted_or_backslashed_token_is_one_operand(self) -> None:
        work = self.library.add_work("O'Reilly state")
        both = self.library.add_work("a b")
        self.assertEqual(self.found("O'Reilly state"), [work])
        self.assertEqual(self.found("a\\b"), [both])

    def test_nfd_queries_find_what_nfc_queries_find(self) -> None:
        self.library.add_work("Чайка", text_content="Trešnja")
        self.library.add_work("İstanbul")
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
        by_person = self.library.add_work("Fourth")
        person = people_fixtures.add_person(self.db, self.ctx, "Malphan", "Quill")
        role_commands.add(self.db, self.ctx, person, by_person, "Author")
        by_override = self.library.add_work("Second")
        self.sql("UPDATE prks.manifestations SET abstract = 'xalphax'"
                 " WHERE id = (SELECT primary_manifestation_id FROM prks.works WHERE id = %s)", (by_override,))
        self.refresh(by_override)
        by_note = self.library.add_work("Third", text_content="zalphaz")
        by_prefix = self.library.add_work("Alphabet")
        self.assertEqual(self.found("alpha"), [by_prefix, by_person, by_note, by_override])

    def test_a_title_outranks_a_note_that_repeats_the_word(self) -> None:
        title = self.library.add_work("A Theory of Justice")
        self.library.add_work("Seminar", text_content=" ".join(["justice again"] * 20))
        self.assertEqual(self.found("justice")[0], title)

    def test_a_title_infix_outranks_a_note_prefix(self) -> None:
        compound = self.library.add_work("Erkenntnistheorie")
        note = self.library.add_work("Vorlesung", text_content="Theorie am Rande")
        self.assertEqual(self.found("theorie"), [compound, note])

    def test_a_short_exact_title_comes_before_a_longer_one(self) -> None:
        exact = self.library.add_work("The Republic")
        longer = self.library.add_work("A Companion to the Long Republic Tradition")
        self.assertEqual(self.found("republic"), [exact, longer])

    def test_each_token_scores_its_best_field(self) -> None:
        # Title and credited person together beat both tokens in a note.
        credited = self.library.add_work("Republic")
        person = people_fixtures.add_person(self.db, self.ctx, "Aristocles", "Platonides")
        role_commands.add(self.db, self.ctx, person, credited, "Author")
        noted = self.library.add_work("Log", text_content="republic platonides")
        self.assertEqual(self.found("republic platonides"), [credited, noted])

    def test_an_abstract_prefix_outranks_a_newer_note_prefix(self) -> None:
        # ts_filter reads the stored C and D weights (lowercase labels match
        # them): 0.4 beats 0.2 although recency and the note's density
        # (its repetitions) both favor the note.
        by_abstract = self.library.add_work("Essays", abstract="Quillon wrote it")
        by_note = self.library.add_work("Log", text_content=" ".join(["quillon again"] * 10))
        self.assertEqual(self.found("quillon"), [by_abstract, by_note])

    def test_density_breaks_a_tie_before_recency(self) -> None:
        # Same title, same field: the scores are equal, so the denser note
        # comes first although the other Work is newer.
        dense = self.library.add_work("Log", text_content="quillon and quillon and quillon")
        sparse = self.library.add_work("Log", text_content="quillon once")
        self.assertEqual(self.found("quillon"), [dense, sparse])

    def test_a_linked_person_outranks_an_abstract_mention(self) -> None:
        mention = self.library.add_work("Essays", abstract="Quillon is cited once")
        credited = self.library.add_work("Collected papers")
        person = people_fixtures.add_person(self.db, self.ctx, "Ann", "Quillon")
        role_commands.add(self.db, self.ctx, person, credited, "Author")
        self.assertEqual(self.found("quillon"), [credited, mention])

    def test_an_abstract_of_the_work_itself_is_an_infix_hit(self) -> None:
        # Tier 2 is the primary Manifestation's override only, as on SQLite.
        older = self.library.add_work("Older", abstract="xbetax")
        newer = self.library.add_work("Newer", text_content="ybetay")
        self.assertEqual(self.found("beta"), [newer, older])

    def test_inside_a_tier_the_most_recently_updated_comes_first(self) -> None:
        older = self.library.add_work("Gamma one")
        newer = self.library.add_work("Gamma two")
        self.assertEqual(self.found("", author_filter=""), [])
        self.library.update_work_metadata(older, {"author_text": "Ann"})
        self.library.update_work_metadata(newer, {"author_text": "Ann"})
        self.assertEqual(self.found("", author_filter="ann"), [newer, older])
        self.library.update_work_metadata(older, {"abstract": "touched"})
        self.assertEqual(self.found("", author_filter="ann"), [older, newer])

    def test_filters_restrict_a_keyword_search(self) -> None:
        kept = self.library.add_work("Delta", author_text="Ann", publisher="Press")
        self.library.add_work("Delta", author_text="Ann", publisher="Other")
        self.library.add_work("Delta", author_text="Bob", publisher="Press")
        self.assertEqual(self.found("delta", author_filter="ann", publisher_filter="press"), [kept])
        self.assertEqual(self.found("delta", author_filter="nobody"), [])

    def test_any_mode_adds_author_and_publisher_matches_after_the_keyword_hits(self) -> None:
        keyword = self.library.add_work("Orbis")
        by_author = self.library.add_work("Unrelated", author_text="Orbison")
        by_publisher = self.library.add_work("Other", publisher="Orbis Press")
        rows = self.library.search_works_any("orbis")
        ids = [row["id"] for row in rows]
        self.assertEqual(ids[0], keyword)
        self.assertEqual(set(ids), {keyword, by_author, by_publisher})
        self.assertNotIn("text_content", rows[0])


class PublisherRecordTests(SearchQueryTestCase):
    def test_a_matching_record_brings_in_every_label(self) -> None:
        publisher = publisher_commands.create(self.db, self.ctx, "Oxford University Press")
        publisher_commands.add_alias(self.db, self.ctx, publisher["id"], "OUP")
        by_alias = self.library.add_work("Short", publisher="oup")
        by_name = self.library.add_work("Long", publisher="Oxford University Press")
        self.library.add_work("Other", publisher="Cambridge")
        self.assertEqual(self.library.work_ids_matching_publisher("oxford"), sorted([by_alias, by_name]))
        self.assertEqual(self.library.work_ids_matching_publisher("OUP"), sorted([by_alias, by_name]))


class IsolationTests(SearchQueryTestCase):
    def test_another_librarys_works_are_never_found(self) -> None:
        mine = self.library.add_work("Shared title", author_text="Ann", publisher="Press")
        self.bind(self.other_actor().context)
        other = self.library.add_work("Shared title", author_text="Ann", publisher="Press")
        self.bind(self.actor.context)
        self.assertEqual(self.found("shared"), [mine])
        self.assertNotIn(other, self.library.work_ids_matching_author("ann"))
        self.assertEqual(self.library.work_ids_matching_publisher("press"), [mine])

    def test_another_librarys_tag_of_the_same_name_is_never_used(self) -> None:
        tag = tag_commands.create(self.db, self.ctx, "Physics")
        tag_commands.add_alias(self.db, self.ctx, tag["id"], "phys")
        mine = self.library.add_work("Mine")
        tag_commands.tag_work(self.db, self.ctx, mine, tag["id"])
        self.bind(self.other_actor().context)
        their_tag = tag_commands.create(self.db, self.ctx, "Physics")
        tag_commands.add_alias(self.db, self.ctx, their_tag["id"], "phys")
        theirs = self.library.add_work("Theirs")
        tag_commands.tag_work(self.db, self.ctx, theirs, their_tag["id"])
        self.assertEqual([row["id"] for row in self.library.get_works_by_tag_name("phys")], [theirs])
        self.bind(self.actor.context)
        for label in ("Physics", "phys"):
            with self.subTest(label=label):
                self.assertEqual([row["id"] for row in self.library.get_works_by_tag_name(label)], [mine])


class AnswerTests(AnswerAssertions, SearchTestCase):
    """What keyword search, the filters, the Tag filter and OR mode list, spelled out.

    Each Work is edited a minute after the previous one, so an order never
    rests on a tie.
    """

    def setUp(self) -> None:
        super().setUp()
        self.minute = 0

    def work(self, label: str, **fields: Any) -> str:
        fields.setdefault("title", label)
        work_id = self.labels.name(self.library.add_work(**fields), f"<W {label}>")
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
        self.assertEqual(sorted(self.listed(self.library.search_works("alpha"))),
                         ["<W Alphabet>", "<W Fourth>", "<W Second>", "<W Third again>", "<W Third>"])

    def test_tokens_split_on_hyphens_and_must_all_match(self) -> None:
        self.work("Multi-Author survey")
        self.work("Multi survey")
        for query in ("multi author", "Multi-Author", "MULTI_author"):
            with self.subTest(query=query):
                self.answer(self.library.search_works(query), [{
                    "id": "<W Multi-Author survey>", "title": "Multi-Author survey", "status": "Not Started",
                    "doc_type": "article", "created_at": "<s>", "updated_at": "<s>", "linked_people": []}])
        self.assertEqual(self.library.search_works("nothing here"), [])
        self.assertEqual(self.library.search_works("   "), [])

    def test_a_linked_person_is_found_by_name_alias_or_last_name(self) -> None:
        self.credit(self.work("Credited"), "Albrecht", "Zweistein", aliases="Doctor Z")
        self.work("Uncredited")
        for query in ("brecht zwei", "albrecht zweistein", "octor", "weistein"):
            with self.subTest(query=query):
                self.assertEqual(self.listed(self.library.search_works(query)), ["<W Credited>"])

    def test_author_and_publisher_filters(self) -> None:
        self.work("Kept", author_text="Ann Lee", publisher="TestPub")
        self.work("Wrong publisher", author_text="Ann Lee", publisher="Other")
        self.work("Wrong author", author_text="Bob", publisher="TestPub")
        self.credit(self.work("Credited", publisher="TestPub"), "Joan", "Annerl")
        search = self.library.search_works
        self.assertEqual(self.ids(self.library.work_ids_matching_author("ann")),
                         ["<W Credited>", "<W Kept>", "<W Wrong publisher>"])
        self.assertEqual(self.ids(self.library.work_ids_matching_publisher("testpub")),
                         ["<W Credited>", "<W Kept>", "<W Wrong author>"])
        self.assertEqual(self.listed(search("", author_filter="ann", publisher_filter="testpub")),
                         ["<W Credited>", "<W Kept>"])
        self.assertEqual(self.listed(search("", publisher_filter="other")), ["<W Wrong publisher>"])
        self.assertEqual(self.listed(search("", author_filter="ann", publisher_filter="none")), [])
        self.assertEqual(self.listed(search("kept", author_filter="ann")), ["<W Kept>"])
        self.assertEqual(self.listed(search("kept", author_filter="bob")), [])
        self.assertEqual(self.library.work_ids_matching_author("  "), [])

    def test_a_publisher_record_brings_in_its_names_and_aliases(self) -> None:
        record = publisher_commands.create(self.db, self.ctx, "Oxford University Press")
        publisher_commands.add_alias(self.db, self.ctx, record["id"], "OUP")
        self.work("Short", publisher="oup")
        self.work("Long", publisher="Oxford University Press")
        self.work("Other", publisher="Cambridge")
        for needle in ("oxford", "OUP", "university"):
            with self.subTest(needle=needle):
                self.assertEqual(self.ids(self.library.work_ids_matching_publisher(needle)), ["<W Long>", "<W Short>"])

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
                self.assertEqual(self.listed(self.library.get_works_by_tag_name(label)), ["<W Newer>", "<W Older>"])
        self.assertEqual(self.library.get_works_by_tag_name("unknown"), [])
        self.assertEqual(self.library.get_works_by_tag_name("  "), [])

    def test_any_mode_appends_author_and_publisher_matches(self) -> None:
        self.work("Orbis")
        self.work("By author", author_text="Morbison")
        self.work("By publisher", publisher="Orbis Press")
        self.work("Nothing")
        self.assertEqual(self.listed(self.library.search_works_any("orbis")),
                         ["<W Orbis>", "<W By author>", "<W By publisher>"])
        self.assertEqual(self.library.search_works_any(" "), [])

