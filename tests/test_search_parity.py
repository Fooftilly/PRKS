"""Work search: PostgreSQL lists what SQLite lists (L-S-1 #637; §8.3, §8.7).

Each library is built the same way on this leg's library object and on a
reference SQLite library, and ``search_works``, ``search_works_any``,
``work_ids_matching_author``, ``work_ids_matching_publisher`` and
``get_works_by_tag_name`` must return
the same rows in the same order. The inputs are ASCII, where SQLite's
``LOWER`` and FTS5 fold as PostgreSQL does; the Unicode cases where
PostgreSQL finds more are pinned in ``tests/pg/test_search_queries.py``.
Every Work gets its own last-edit time, so the recency order inside a tier
never rests on a tie, and each test keeps one full-text hit per query,
because SQLite ranks by bm25 and PostgreSQL by ``ts_rank_cd``.
"""

from __future__ import annotations

import os
import sys
from typing import Any

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from tests.backend_parity import POSTGRES, parametrize_backends
from tests.people_parity_helpers import PeopleParityTestCase, PeopleSide

Pair = tuple[str, str]

_PG_EDITED = ("UPDATE prks.works SET updated_at = %(at)s::timestamptz"
              " WHERE library_id = %(library_id)s AND id = %(work_id)s")
_SQLITE_EDITED = "UPDATE works SET updated_at = :at WHERE id = :work_id"
_PG_CREATED = ("UPDATE prks.works SET created_at = %(at)s::timestamptz"
               " WHERE library_id = %(library_id)s AND id = %(work_id)s")
_SQLITE_CREATED = "UPDATE works SET created_at = :at WHERE id = :work_id"
_PG_OVERRIDE = ("UPDATE prks.manifestations SET abstract = %(abstract)s"
                " WHERE library_id = %(library_id)s AND id ="
                " (SELECT primary_manifestation_id FROM prks.works"
                "  WHERE library_id = %(library_id)s AND id = %(work_id)s)")
_SQLITE_OVERRIDE = ("UPDATE manifestations SET abstract = :abstract"
                    " WHERE id = (SELECT primary_manifestation_id FROM works WHERE id = :work_id)")


@parametrize_backends
class SearchParityTests(PeopleParityTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.minute = 0

    # -- building the same library on both sides

    def work_both(self, label: str, **fields: Any) -> Pair:
        """A Work on both sides, edited one minute after the previous one."""
        fields.setdefault("title", label)
        work = self.leg.work(label, **fields), self.ref.work(label, **fields)
        self.minute += 1
        at = f"2026-01-01 10:{self.minute:02d}:00"
        for index, side in enumerate(self.sides()):
            zone = "+00" if self._on_postgres(side) else ""
            self._row(side, _PG_EDITED, _SQLITE_EDITED, {"work_id": work[index], "at": at + zone})
        return work

    def credit(self, work: Pair, person: Pair) -> None:
        for index, side in enumerate(self.sides()):
            side.db.add_role(person[index], work[index], "Author")

    def override_abstract(self, work: Pair, abstract: str) -> None:
        """Give the primary Manifestation its own abstract (no command writes one yet)."""
        for index, side in enumerate(self.sides()):
            self._row(side, _PG_OVERRIDE, _SQLITE_OVERRIDE, {"work_id": work[index], "abstract": abstract})
            if self._on_postgres(side):
                from backend.db.database import Isolation
                from backend.domain.identity.authorization import Capability
                from backend.domain.identity.library_scope import library_command
                from backend.domain.search import refresh
                from backend.pg_facade.context import current_context

                library_command(side.db.parity_library.database, current_context(), Capability.EDIT_LIBRARY,
                                lambda scope: refresh.refresh_work_search(scope, [work[0]]),
                                isolation=Isolation.READ_COMMITTED)

    def _on_postgres(self, side: PeopleSide) -> bool:
        return side is self.leg and self.parity_backend == POSTGRES

    def _row(self, side: PeopleSide, pg_sql: str, sqlite_sql: str, params: dict[str, Any]) -> None:
        if self._on_postgres(side):
            from backend.pg_facade.context import current_context

            values = {**params, "library_id": current_context().library_id}
            side.db.parity_library.database.command(lambda tx: tx.execute(pg_sql, values))
            return
        with side.db.connection() as conn:
            conn.execute(sqlite_sql, params)

    # -- comparing

    def listed(self, call: str, *args: Any, **kwargs: Any) -> list[str]:
        """Both sides' rows, equal in content and order; returns the labels in order."""
        rows = self.same(call, *args, **kwargs)
        return [self.leg.labels[row["id"]] for row in rows]

    def same_ids(self, call: str, *args: Any) -> list[str]:
        """Both sides' id lists, equal as sets (SQLite gives them no order)."""
        mine, theirs = self.both(call, *args)
        labels = sorted(self.leg.normal(mine))
        self.assertEqual(labels, sorted(self.ref.normal(theirs)), call)
        return labels

    # -- keyword search

    def test_the_tiers_keep_sqlites_union_order(self) -> None:
        person = self.person_both("Quill", first_name="Malphan")
        self.credit(self.work_both("Fourth"), person)
        self.override_abstract(self.work_both("Second"), "xalphax")
        self.work_both("Third", text_content="zalphaz")
        self.work_both("Third again", abstract="yalphay")
        self.work_both("Alphabet")
        self.work_both("Unrelated")
        self.assertEqual(self.listed("search_works", "alpha"),
                         ["<W Alphabet>", "<W Second>", "<W Third again>", "<W Third>", "<W Fourth>"])

    def test_tokens_split_on_hyphens_and_must_all_match(self) -> None:
        self.work_both("Multi-Author survey")
        self.work_both("Multi survey")
        for query in ("multi author", "Multi-Author", "MULTI_author"):
            with self.subTest(query=query):
                self.assertEqual(self.listed("search_works", query), ["<W Multi-Author survey>"])
        self.assertEqual(self.listed("search_works", "nothing here"), [])
        self.assertEqual(self.listed("search_works", "   "), [])

    def test_a_linked_person_is_found_by_name_alias_or_last_name(self) -> None:
        person = self.person_both("Zweistein", first_name="Albrecht", aliases="Doctor Z")
        self.credit(self.work_both("Credited"), person)
        self.work_both("Uncredited")
        for query in ("brecht zwei", "albrecht zweistein", "octor", "weistein"):
            with self.subTest(query=query):
                self.assertEqual(self.listed("search_works", query), ["<W Credited>"])

    # -- filters

    def test_author_and_publisher_filters(self) -> None:
        self.work_both("Kept", author_text="Ann Lee", publisher="TestPub")
        self.work_both("Wrong publisher", author_text="Ann Lee", publisher="Other")
        self.work_both("Wrong author", author_text="Bob", publisher="TestPub")
        person = self.person_both("Annerl", first_name="Joan")
        self.credit(self.work_both("Credited", publisher="TestPub"), person)
        self.assertEqual(self.same_ids("work_ids_matching_author", "ann"),
                         ["<W Credited>", "<W Kept>", "<W Wrong publisher>"])
        self.assertEqual(self.same_ids("work_ids_matching_publisher", "testpub"),
                         ["<W Credited>", "<W Kept>", "<W Wrong author>"])
        self.assertEqual(self.listed("search_works", "", author_filter="ann", publisher_filter="testpub"),
                         ["<W Credited>", "<W Kept>"])
        self.assertEqual(self.listed("search_works", "", publisher_filter="other"), ["<W Wrong publisher>"])
        self.assertEqual(self.listed("search_works", "", author_filter="ann", publisher_filter="none"), [])
        self.assertEqual(self.listed("search_works", "kept", author_filter="ann"), ["<W Kept>"])
        self.assertEqual(self.listed("search_works", "kept", author_filter="bob"), [])
        self.assertEqual(self.same_ids("work_ids_matching_author", "  "), [])

    def test_a_publisher_record_brings_in_its_names_and_aliases(self) -> None:
        for side in self.sides():
            record = side.publisher("OUP", "Oxford University Press")
            side.db.add_publisher_alias(record["id"], "OUP")
        self.work_both("Short", publisher="oup")
        self.work_both("Long", publisher="Oxford University Press")
        self.work_both("Other", publisher="Cambridge")
        for needle in ("oxford", "OUP", "university"):
            with self.subTest(needle=needle):
                self.assertEqual(self.same_ids("work_ids_matching_publisher", needle), ["<W Long>", "<W Short>"])

    # -- the Tag filter

    def test_the_tag_filter_lists_tagged_works_newest_first(self) -> None:
        works = [self.work_both(label) for label in ("Older", "Newer", "Untagged")]
        for index, side in enumerate(self.sides()):
            tag = side.db.add_tag("Physics")
            side.db.add_tag_alias(tag["id"], "phys")
            for minute, work in ((1, works[0]), (2, works[1])):
                side.db.add_tag_to_work(work[index], tag["id"])
            for minute, work in ((1, works[0]), (2, works[1]), (3, works[2])):
                zone = "+00" if self._on_postgres(side) else ""
                self._row(side, _PG_CREATED, _SQLITE_CREATED,
                          {"work_id": work[index], "at": f"2025-01-01 09:{minute:02d}:00{zone}"})
        for label in ("physics", "PHYS", " Physics "):
            with self.subTest(label=label):
                self.assertEqual(self.listed("get_works_by_tag_name", label), ["<W Newer>", "<W Older>"])
        self.assertEqual(self.listed("get_works_by_tag_name", "unknown"), [])
        self.assertEqual(self.listed("get_works_by_tag_name", "  "), [])

    # -- OR mode

    def test_any_mode_appends_author_and_publisher_matches(self) -> None:
        self.work_both("Orbis")
        # Author text is keyword text too: an infix keeps it out of the
        # full-text tier, where SQLite's bm25 and PostgreSQL's rank differ.
        self.work_both("By author", author_text="Morbison")
        self.work_both("By publisher", publisher="Orbis Press")
        self.work_both("Nothing")
        self.assertEqual(self.listed("search_works_any", "orbis"),
                         ["<W Orbis>", "<W By author>", "<W By publisher>"])
        self.assertEqual(self.listed("search_works_any", " "), [])
