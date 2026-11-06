"""The synthetic ranking corpus and its pinned order (L-S-2 #638).

``tests/pg/search_ranking.py`` explains the report. These tests keep the
corpus consistent, pin the live keyword order to
``tests/pg/search_ranking_baseline.json`` (so a ranking change shows up as a
diff of that file, which review approves), and pin recall: every graded
Work is found, whatever the order.
"""

from __future__ import annotations

import unittest

from tests.pg import search_corpus, search_ranking
from tests.pg.search_corpus import JudgedQuery
from tests.pg.test_works_facade import WorksFacadeTestCase


class CorpusTests(unittest.TestCase):
    def test_the_corpus_is_consistent(self) -> None:
        self.assertEqual(search_corpus.validate(), [])

    def test_the_baseline_covers_every_query(self) -> None:
        self.assertEqual(set(search_ranking.read_baseline()), {q.text for q in search_corpus.QUERIES})


class ScoreTests(unittest.TestCase):
    query = JudgedQuery("q", "en", {"a": 3, "b": 2, "c": 1})

    def test_the_ideal_order_scores_one(self) -> None:
        best = search_ranking.score(self.query, ["a", "b", "c"])
        self.assertAlmostEqual(best.ndcg10, 1.0)
        self.assertEqual((best.reciprocal_rank, best.inversions, best.missing), (1.0, 0, ()))

    def test_a_reversed_order_scores_lower_and_counts_inversions(self) -> None:
        worst = search_ranking.score(self.query, ["c", "b", "a"])
        self.assertLess(worst.ndcg10, 1.0)
        self.assertAlmostEqual(worst.reciprocal_rank, 1 / 3)
        self.assertEqual(worst.inversions, 3)

    def test_an_unjudged_work_is_grade_zero_and_a_missing_one_is_reported(self) -> None:
        found = search_ranking.score(self.query, ["x", "a", "b"])
        self.assertAlmostEqual(found.reciprocal_rank, 0.5)
        self.assertEqual(found.inversions, 2)
        self.assertEqual(found.missing, ("c",))


class RankingTests(WorksFacadeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ids = search_ranking.load(self.library, self.sql)
        self.orders = search_ranking.run(self.library, self.ids)

    def test_the_live_order_matches_the_baseline(self) -> None:
        # Regenerate with ``python -m tests.pg.search_ranking --write-baseline``
        # and review the diff with ``--compare`` before committing it.
        self.assertEqual(self.orders, search_ranking.read_baseline())

    def test_every_graded_work_is_found(self) -> None:
        for query in search_corpus.QUERIES:
            with self.subTest(query=query.text):
                self.assertEqual(search_ranking.score(query, self.orders[query.text]).missing, ())

    def test_the_report_lists_every_query(self) -> None:
        text = search_ranking.report(self.orders, search_ranking.read_baseline())
        for query in search_corpus.QUERIES:
            self.assertIn(f"## `{query.text}`", text)
        self.assertIn("**Mean / total**", text)
