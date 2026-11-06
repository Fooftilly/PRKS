"""Work search reads on PostgreSQL (L-S-1 #637; §8.3, §8.7).

``tests/test_search_parity.py`` proves the facade lists what SQLite lists
where both agree. These tests cover what only PostgreSQL does: the §8.7
vectors (Unicode folding on both sides of every match, the infix fallback,
whole-string filters that keep punctuation, wildcards that stay literal,
NFD queries), the L-S-2 ranking (#638), and Library isolation.
"""

from __future__ import annotations

import unicodedata

from backend.domain.search import queries
from tests.pg.test_search_refresh import SearchTestCase


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
        person = self.library.add_person("Malphan", "Quill")
        self.library.add_role(person, by_person, "Author")
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
        person = self.library.add_person("Aristocles", "Platonides")
        self.library.add_role(person, credited, "Author")
        noted = self.library.add_work("Log", text_content="republic platonides")
        self.assertEqual(self.found("republic platonides"), [credited, noted])

    def test_a_linked_person_outranks_an_abstract_mention(self) -> None:
        mention = self.library.add_work("Essays", abstract="Quillon is cited once")
        credited = self.library.add_work("Collected papers")
        person = self.library.add_person("Ann", "Quillon")
        self.library.add_role(person, credited, "Author")
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
        publisher = self.library.add_publisher("Oxford University Press")
        self.library.add_publisher_alias(publisher["id"], "OUP")
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
        tag = self.library.add_tag("Physics")
        self.library.add_tag_alias(tag["id"], "phys")
        mine = self.library.add_work("Mine")
        self.library.add_tag_to_work(mine, tag["id"])
        self.bind(self.other_actor().context)
        their_tag = self.library.add_tag("Physics")
        self.library.add_tag_alias(their_tag["id"], "phys")
        theirs = self.library.add_work("Theirs")
        self.library.add_tag_to_work(theirs, their_tag["id"])
        self.assertEqual([row["id"] for row in self.library.get_works_by_tag_name("phys")], [theirs])
        self.bind(self.actor.context)
        for label in ("Physics", "phys"):
            with self.subTest(label=label):
                self.assertEqual([row["id"] for row in self.library.get_works_by_tag_name(label)], [mine])
