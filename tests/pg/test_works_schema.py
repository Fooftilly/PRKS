"""Constraints of the Works schema (L-W-1 #628; ``100_works.sql``).

The #60 §4.1 negative tests as schema tests: every contradictory insert or
update is refused, the deferred pointer keys are checked at COMMIT (not at
the statement), every legal final state has a legal path, and no row pairs
one Library's child with another Library's parent.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Optional

import psycopg

from backend.domain.works.integrity import INTEGRITY_SQL
from tests.pg.harness import PostgresTestCase, fetch_one

SHA = "0" * 64


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex.upper()}"


class WorksSchemaTestCase(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.conn = self.connect(autocommit=True)
        self.lib = self.library()

    # -- helpers -----------------------------------------------------------

    def sql(self, statement: str, params: Any = None) -> psycopg.Cursor[Any]:
        return self.conn.execute(statement.encode(), params)

    @contextmanager
    def tx(self) -> Iterator[None]:
        with self.conn.transaction():
            yield

    def refused(self, error: type[psycopg.Error], *statements: tuple[str, Any]) -> None:
        """The statements fail with ``error``, at a statement or at COMMIT, and change nothing."""
        with self.assertRaises(error):
            with self.tx():
                for statement, params in statements:
                    self.sql(statement, params)

    def refused_at_commit(self, error: type[psycopg.Error], *statements: tuple[str, Any]) -> None:
        """Each statement runs; only COMMIT refuses (a deferred constraint)."""
        with self.assertRaises(error) as caught:
            with self.tx():
                for statement, params in statements:
                    self.sql(statement, params)
                self.reached_commit = True
        self.assertTrue(self.__dict__.pop("reached_commit", False), caught.exception)

    def library(self) -> str:
        library_id = "LIB-" + uuid.uuid4().hex.upper()
        self.sql("INSERT INTO prks.libraries (id, name) VALUES (%s, 'L')", (library_id,))
        return library_id

    def insert_work(self, work_id: str, manifestation_id: str, lib: Optional[str] = None) -> None:
        self.sql(
            "INSERT INTO prks.works (library_id, id, title, primary_manifestation_id) "
            "VALUES (%s, %s, 'Title', %s)",
            (lib or self.lib, work_id, manifestation_id),
        )

    def insert_manifestation(self, manifestation_id: str, work_id: str, lib: Optional[str] = None) -> None:
        self.sql(
            "INSERT INTO prks.manifestations (library_id, id, work_id) VALUES (%s, %s, %s)",
            (lib or self.lib, manifestation_id, work_id),
        )

    def insert_asset(self, asset_id: str, manifestation_id: str, work_id: str, *,
                     lib: Optional[str] = None, state: str = "active") -> None:
        self.sql(
            "INSERT INTO prks.assets (library_id, id, manifestation_id, work_id, kind, "
            "storage_locator, content_sha256, origin, state) "
            "VALUES (%s, %s, %s, %s, 'managed_file', %s, %s, 'upload', %s)",
            (lib or self.lib, asset_id, manifestation_id, work_id, asset_id.lower() + ".pdf", SHA, state),
        )

    def work(self, lib: Optional[str] = None) -> tuple[str, str]:
        """A committed Work with its primary Manifestation."""
        work_id, manifestation_id = new_id("W"), new_id("MF")
        with self.tx():
            self.insert_work(work_id, manifestation_id, lib)
            self.insert_manifestation(manifestation_id, work_id, lib)
        return work_id, manifestation_id

    def manifestation(self, work_id: str, lib: Optional[str] = None) -> str:
        manifestation_id = new_id("MF")
        self.insert_manifestation(manifestation_id, work_id, lib)
        return manifestation_id

    def primary_asset(self, manifestation_id: str, work_id: str) -> str:
        asset_id = new_id("AS")
        with self.tx():
            self.insert_asset(asset_id, manifestation_id, work_id)
            self.sql("UPDATE prks.manifestations SET primary_asset_id = %s WHERE id = %s",
                     (asset_id, manifestation_id))
        return asset_id

    def value(self, statement: str, params: Any = None) -> Any:
        row = fetch_one(self.sql(statement, params))
        return row[0]

    def count(self, table: str, column: str, value: str) -> int:
        return int(self.value(f"SELECT count(*) FROM prks.{table} WHERE {column} = %s", (value,)))

    def integrity(self, lib: Optional[str] = None) -> list[tuple[str, str]]:
        rows = self.conn.execute(INTEGRITY_SQL, {"library_id": lib or self.lib}).fetchall()
        return [(row[1], row[2]) for row in rows]


class WorkPointerTests(WorksSchemaTestCase):
    """``works.primary_manifestation_id`` and ``citation_manifestation_id`` (deferred)."""

    def test_work_and_first_manifestation_commit_together(self) -> None:
        work_id, manifestation_id = self.work()
        self.assertEqual(
            self.value("SELECT primary_manifestation_id FROM prks.works WHERE id = %s", (work_id,)),
            manifestation_id,
        )
        self.assertEqual(self.integrity(), [])

    def test_a_work_without_its_primary_fails_at_commit(self) -> None:
        work_id = new_id("W")
        self.refused_at_commit(
            psycopg.errors.ForeignKeyViolation,
            ("INSERT INTO prks.works (library_id, id, title, primary_manifestation_id) "
             "VALUES (%s, %s, 'T', %s)", (self.lib, work_id, new_id("MF"))),
        )
        self.assertEqual(self.count("works", "id", work_id), 0)

    def test_the_primary_is_required(self) -> None:
        self.refused(
            psycopg.errors.NotNullViolation,
            ("INSERT INTO prks.works (library_id, id, title, primary_manifestation_id) "
             "VALUES (%s, %s, 'T', NULL)", (self.lib, new_id("W"))),
        )
        work_id, _ = self.work()
        self.refused(
            psycopg.errors.NotNullViolation,
            ("UPDATE prks.works SET primary_manifestation_id = NULL WHERE id = %s", (work_id,)),
        )

    def test_a_pointer_to_another_works_manifestation_is_refused(self) -> None:
        work_a, _ = self.work()
        _, other = self.work()
        for column in ("primary_manifestation_id", "citation_manifestation_id"):
            with self.subTest(column=column):
                self.refused_at_commit(
                    psycopg.errors.ForeignKeyViolation,
                    (f"UPDATE prks.works SET {column} = %s WHERE id = %s", (other, work_a)),
                )

    def test_a_new_work_cannot_take_an_existing_manifestation(self) -> None:
        _, taken = self.work()
        self.refused_at_commit(
            psycopg.errors.ForeignKeyViolation,
            ("INSERT INTO prks.works (library_id, id, title, primary_manifestation_id) "
             "VALUES (%s, %s, 'T', %s)", (self.lib, new_id("W"), taken)),
        )

    def test_citation_is_optional_and_owned(self) -> None:
        work_id, primary = self.work()
        with self.tx():
            second = self.manifestation(work_id)
            self.sql("UPDATE prks.works SET citation_manifestation_id = %s WHERE id = %s", (second, work_id))
        self.sql("UPDATE prks.works SET citation_manifestation_id = NULL WHERE id = %s", (work_id,))
        self.assertIsNone(
            self.value("SELECT citation_manifestation_id FROM prks.works WHERE id = %s", (work_id,))
        )
        self.assertEqual(self.integrity(), [])
        del primary

    def test_a_pointer_target_cannot_move_away(self) -> None:
        work_a, primary = self.work()
        work_b, _ = self.work()
        self.refused_at_commit(
            psycopg.errors.ForeignKeyViolation,
            ("UPDATE prks.manifestations SET work_id = %s WHERE id = %s", (work_b, primary)),
        )
        with self.tx():
            citation = self.manifestation(work_a)
            self.sql("UPDATE prks.works SET citation_manifestation_id = %s WHERE id = %s", (citation, work_a))
        self.refused_at_commit(
            psycopg.errors.ForeignKeyViolation,
            ("UPDATE prks.manifestations SET work_id = %s WHERE id = %s", (work_b, citation)),
        )
        self.assertEqual(
            self.value("SELECT work_id FROM prks.manifestations WHERE id = %s", (citation,)), work_a
        )

    def test_a_pointer_target_cannot_be_deleted(self) -> None:
        _, primary = self.work()
        self.refused_at_commit(
            psycopg.errors.ForeignKeyViolation,
            ("DELETE FROM prks.manifestations WHERE id = %s", (primary,)),
        )
        self.assertEqual(self.count("manifestations", "id", primary), 1)

    def test_a_pointer_swap_then_move_commits(self) -> None:
        work_a, primary = self.work()
        work_b, _ = self.work()
        with self.tx():
            sibling = self.manifestation(work_a)
        asset = self.primary_asset(primary, work_a)
        with self.tx():
            # Either order works: the keys are checked at COMMIT.
            self.sql("UPDATE prks.manifestations SET work_id = %s WHERE id = %s", (work_b, primary))
            self.sql("UPDATE prks.works SET primary_manifestation_id = %s WHERE id = %s", (sibling, work_a))
        self.assertEqual(self.value("SELECT work_id FROM prks.assets WHERE id = %s", (asset,)), work_b)
        self.assertEqual(self.integrity(), [])

    def test_a_non_primary_moves_without_a_pointer_change_and_carries_its_assets(self) -> None:
        work_a, _ = self.work()
        work_b, _ = self.work()
        with self.tx():
            moving = self.manifestation(work_a)
        asset = self.primary_asset(moving, work_a)
        self.sql("UPDATE prks.manifestations SET work_id = %s WHERE id = %s", (work_b, moving))
        self.assertEqual(self.value("SELECT work_id FROM prks.assets WHERE id = %s", (asset,)), work_b)

    def test_moving_the_only_manifestation_out_needs_the_emptied_work_retired(self) -> None:
        work_a, only = self.work()
        work_b, _ = self.work()
        # A Work with no Version is not a valid final state.
        self.refused_at_commit(
            psycopg.errors.ForeignKeyViolation,
            ("UPDATE prks.manifestations SET work_id = %s WHERE id = %s", (work_b, only)),
        )
        # Merge: a lifecycle redirect, the move, then the emptied Work's delete.
        with self.tx():
            self.sql("INSERT INTO prks.sync_work_lifecycle (library_id, work_id, state, target_work_id) "
                     "VALUES (%s, %s, 'merged', %s)", (self.lib, work_a, work_b))
            self.sql("UPDATE prks.manifestations SET work_id = %s WHERE id = %s", (work_b, only))
            self.sql("DELETE FROM prks.works WHERE id = %s", (work_a,))
        self.assertEqual(self.value("SELECT work_id FROM prks.manifestations WHERE id = %s", (only,)), work_b)
        self.assertEqual(self.count("works", "id", work_a), 0)

    def test_moving_the_only_manifestation_out_with_delete_of_the_source(self) -> None:
        work_a, only = self.work()
        work_b, _ = self.work()
        with self.tx():
            self.sql("UPDATE prks.manifestations SET work_id = %s WHERE id = %s", (work_b, only))
            self.sql("DELETE FROM prks.works WHERE id = %s", (work_a,))
        self.assertEqual(self.count("manifestations", "id", only), 1)

    def test_a_failed_transition_changes_nothing(self) -> None:
        work_a, primary = self.work()
        work_b, _ = self.work()
        with self.tx():
            sibling = self.manifestation(work_a)
        self.refused_at_commit(
            psycopg.errors.ForeignKeyViolation,
            ("UPDATE prks.works SET primary_manifestation_id = %s WHERE id = %s", (sibling, work_a)),
            ("UPDATE prks.manifestations SET work_id = %s WHERE id = %s", (work_b, primary)),
            ("UPDATE prks.manifestations SET work_id = %s WHERE id = %s", (work_b, sibling)),
        )
        self.assertEqual(
            self.value("SELECT primary_manifestation_id FROM prks.works WHERE id = %s", (work_a,)), primary
        )
        self.assertEqual(
            sorted(r[0] for r in self.sql("SELECT work_id FROM prks.manifestations WHERE id IN (%s, %s)",
                                          (primary, sibling))),
            [work_a, work_a],
        )


class AssetPointerTests(WorksSchemaTestCase):
    """``manifestations.primary_asset_id`` (deferred) and Asset ownership."""

    def test_the_primary_asset_belongs_to_its_manifestation(self) -> None:
        work_id, primary = self.work()
        with self.tx():
            other = self.manifestation(work_id)
        foreign_asset = self.primary_asset(other, work_id)
        self.refused_at_commit(
            psycopg.errors.ForeignKeyViolation,
            ("UPDATE prks.manifestations SET primary_asset_id = %s WHERE id = %s", (foreign_asset, primary)),
        )

    def test_an_assets_work_must_be_its_manifestations_work(self) -> None:
        work_a, primary = self.work()
        work_b, _ = self.work()
        self.refused(
            psycopg.errors.ForeignKeyViolation,
            ("INSERT INTO prks.assets (library_id, id, manifestation_id, work_id, kind, storage_locator, "
             "content_sha256, origin) VALUES (%s, %s, %s, %s, 'managed_file', 'x.pdf', %s, 'upload')",
             (self.lib, new_id("AS"), primary, work_b, SHA)),
        )
        asset = self.primary_asset(primary, work_a)
        self.refused(
            psycopg.errors.ForeignKeyViolation,
            ("UPDATE prks.assets SET work_id = %s WHERE id = %s", (work_b, asset)),
        )

    def test_moving_the_only_primary_asset_out_commits_in_either_order(self) -> None:
        work_id, source = self.work()
        with self.tx():
            target = self.manifestation(work_id)
        for clear_first in (False, True):
            with self.subTest(clear_first=clear_first):
                asset = self.primary_asset(source, work_id)
                move = ("UPDATE prks.assets SET manifestation_id = %s WHERE id = %s", (target, asset))
                clear = ("UPDATE prks.manifestations SET primary_asset_id = NULL WHERE id = %s", (source,))
                with self.tx():
                    for statement, params in ((clear, move) if clear_first else (move, clear)):
                        self.sql(statement, params)
                self.assertIsNone(
                    self.value("SELECT primary_asset_id FROM prks.manifestations WHERE id = %s", (source,))
                )
                self.assertEqual(self.count("assets", "manifestation_id", source), 0)
                self.sql("DELETE FROM prks.assets WHERE id = %s", (asset,))
        self.assertEqual(self.integrity(), [])

    def test_moving_the_primary_asset_without_the_pointer_fails_at_commit(self) -> None:
        work_id, source = self.work()
        with self.tx():
            target = self.manifestation(work_id)
        asset = self.primary_asset(source, work_id)
        self.refused_at_commit(
            psycopg.errors.ForeignKeyViolation,
            ("UPDATE prks.assets SET manifestation_id = %s WHERE id = %s", (target, asset)),
        )
        self.refused_at_commit(
            psycopg.errors.ForeignKeyViolation,
            ("DELETE FROM prks.assets WHERE id = %s", (asset,)),
        )
        self.assertEqual(self.value("SELECT manifestation_id FROM prks.assets WHERE id = %s", (asset,)), source)

    def test_a_non_primary_asset_moves_without_a_pointer_change(self) -> None:
        work_id, source = self.work()
        with self.tx():
            target = self.manifestation(work_id)
        self.primary_asset(source, work_id)
        extra = new_id("AS")
        self.insert_asset(extra, source, work_id)
        self.sql("UPDATE prks.assets SET manifestation_id = %s WHERE id = %s", (target, extra))
        # The target now has an active Asset and no primary: the integrity query names it.
        self.assertEqual(self.integrity(), [(target, "active_assets_without_primary_asset")])
        self.sql("UPDATE prks.manifestations SET primary_asset_id = %s WHERE id = %s", (extra, target))
        self.assertEqual(self.integrity(), [])

    def test_integrity_query_names_an_inactive_primary(self) -> None:
        work_id, manifestation = self.work()
        asset = self.primary_asset(manifestation, work_id)
        self.sql("UPDATE prks.assets SET state = 'trashed' WHERE id = %s", (asset,))
        self.assertEqual(self.integrity(), [(manifestation, "primary_asset_not_active")])

    def test_kind_decides_which_storage_fields_are_allowed(self) -> None:
        work_id, manifestation = self.work()
        insert = ("INSERT INTO prks.assets (library_id, id, manifestation_id, work_id, kind, storage_locator, "
                  "source_locator, content_sha256, origin) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)")
        for kind, locator, source, digest, origin in (
            ("managed_file", None, None, SHA, "upload"),           # no storage key
            ("managed_file", "a.pdf", None, None, "upload"),       # no hash of the key's bytes
            ("managed_file", "a.pdf", None, "ABC", "upload"),      # not a lower-case SHA-256
            ("managed_file", "", None, SHA, "upload"),             # empty key
            ("managed_file", "a.pdf", None, SHA, None),            # bytes of no stated origin
            ("managed_file", "a.pdf", None, SHA, "legacy"),        # no legacy rows in PostgreSQL
            ("external_stream", "a.pdf", None, None, None),        # a stream stores no bytes
            ("external_stream", None, "a.pdf", None, None),
            ("external_stream", None, None, None, "upload"),       # ... so its bytes have no origin
            ("external_link", None, None, None, None),             # no such kind (#60 D8)
        ):
            with self.subTest(kind=kind, locator=locator, source=source, digest=digest, origin=origin):
                self.refused(psycopg.errors.CheckViolation,
                             (insert, (self.lib, new_id("AS"), manifestation, work_id, kind, locator, source,
                                       digest, origin)))
        # Nor any other fact about stored bytes.
        for column, value in (("byte_size", 10), ("ingest_sha256", SHA), ("content_sha256", SHA),
                              ("origin_url", "https://x/a.pdf"), ("origin_ref", "ref")):
            with self.subTest(stream_column=column):
                self.refused(psycopg.errors.CheckViolation, (
                    "INSERT INTO prks.assets (library_id, id, manifestation_id, work_id, kind, provider, provider_id,"
                    " url, " + column + ") VALUES (%s, %s, %s, %s, 'external_stream', 'youtube', 'abc',"
                    " 'https://x', %s)",
                    (self.lib, new_id("AS"), manifestation, work_id, value)))
        # A stream names its video: the provider, the provider's id and the URL.
        for provider, provider_id, url in ((None, "abc", "https://x"), ("youtube", None, "https://x"),
                                           ("youtube", "abc", None), ("", "abc", "https://x"),
                                           ("youtube", "", "https://x"), ("youtube", "abc", "")):
            with self.subTest(provider=provider, provider_id=provider_id, url=url):
                self.refused(psycopg.errors.CheckViolation, (
                    "INSERT INTO prks.assets (library_id, id, manifestation_id, work_id, kind, provider,"
                    " provider_id, url) VALUES (%s, %s, %s, %s, 'external_stream', %s, %s, %s)",
                    (self.lib, new_id("AS"), manifestation, work_id, provider, provider_id, url)))
        self.sql(
            "INSERT INTO prks.assets (library_id, id, manifestation_id, work_id, kind, provider, provider_id, "
            "url) VALUES (%s, %s, %s, %s, 'external_stream', 'youtube', 'abc', 'https://x')",
            (self.lib, new_id("AS"), manifestation, work_id),
        )

    def test_supersedes_stays_within_one_manifestation_and_relations_clear_on_delete(self) -> None:
        work_id, first = self.work()
        with self.tx():
            second = self.manifestation(work_id)
        old = self.primary_asset(first, work_id)
        elsewhere = self.primary_asset(second, work_id)
        new = new_id("AS")
        self.insert_asset(new, first, work_id)
        self.refused(
            psycopg.errors.ForeignKeyViolation,
            ("UPDATE prks.assets SET supersedes_asset_id = %s WHERE id = %s", (elsewhere, new)),
        )
        self.refused(
            psycopg.errors.CheckViolation,
            ("UPDATE prks.assets SET supersedes_asset_id = id WHERE id = %s", (new,)),
        )
        self.sql("UPDATE prks.assets SET supersedes_asset_id = %s, derived_from_asset_id = %s WHERE id = %s",
                 (old, elsewhere, new))
        with self.tx():
            self.sql("UPDATE prks.manifestations SET primary_asset_id = %s WHERE id = %s", (new, first))
            self.sql("DELETE FROM prks.assets WHERE id = %s", (old,))
        self.sql("UPDATE prks.manifestations SET primary_asset_id = NULL WHERE id = %s", (second,))
        self.sql("DELETE FROM prks.assets WHERE id = %s", (elsewhere,))
        row = fetch_one(self.sql(
            "SELECT supersedes_asset_id, derived_from_asset_id, manifestation_id FROM prks.assets WHERE id = %s",
            (new,)))
        self.assertEqual(tuple(row), (None, None, first))


    def supersedes_pair(self) -> tuple[str, str, str, str]:
        """Two Manifestations of one Work; ``new`` supersedes ``old``, both on the first."""
        work_id, first = self.work()
        with self.tx():
            second = self.manifestation(work_id)
        old = self.primary_asset(first, work_id)
        new = new_id("AS")
        self.insert_asset(new, first, work_id)
        self.sql("UPDATE prks.assets SET supersedes_asset_id = %s WHERE id = %s", (old, new))
        return first, second, old, new

    def test_moving_one_end_of_a_supersedes_pair_fails_at_commit(self) -> None:
        first, second, old, new = self.supersedes_pair()
        self.sql("UPDATE prks.manifestations SET primary_asset_id = NULL WHERE id = %s", (first,))
        for moving in (old, new):
            with self.subTest(moving="old" if moving == old else "new"):
                self.refused_at_commit(
                    psycopg.errors.ForeignKeyViolation,
                    ("UPDATE prks.assets SET manifestation_id = %s WHERE id = %s", (second, moving)),
                )
        self.assertEqual(self.value("SELECT supersedes_asset_id FROM prks.assets WHERE id = %s", (new,)), old)

    def test_a_supersedes_pair_moves_together_or_after_the_link_clears_in_either_order(self) -> None:
        first, second, old, new = self.supersedes_pair()
        self.sql("UPDATE prks.manifestations SET primary_asset_id = NULL WHERE id = %s", (first,))
        with self.tx():
            self.sql("UPDATE prks.assets SET manifestation_id = %s WHERE id = %s", (second, old))
            self.sql("UPDATE prks.assets SET manifestation_id = %s WHERE id = %s", (second, new))
        self.assertEqual(self.value("SELECT supersedes_asset_id FROM prks.assets WHERE id = %s", (new,)), old)
        with self.tx():  # the move first, the link cleared after it
            self.sql("UPDATE prks.assets SET manifestation_id = %s WHERE id = %s", (first, new))
            self.sql("UPDATE prks.assets SET supersedes_asset_id = NULL WHERE id = %s", (new,))
        self.assertEqual(self.value("SELECT manifestation_id FROM prks.assets WHERE id = %s", (new,)), first)


class RelationTests(WorksSchemaTestCase):
    """``manifestation_relations``: same-Work endpoints, never a self-relation."""

    def relate(self, work_id: str, from_id: str, to_id: str) -> tuple[str, Any]:
        return ("INSERT INTO prks.manifestation_relations (library_id, work_id, from_id, to_id, relation) "
                "VALUES (%s, %s, %s, %s, 'translation_of')", (self.lib, work_id, from_id, to_id))

    def test_endpoints_must_be_distinct_manifestations_of_the_rows_work(self) -> None:
        work_a, primary_a = self.work()
        work_b, primary_b = self.work()
        with self.tx():
            sibling = self.manifestation(work_a)
        self.refused(psycopg.errors.CheckViolation, self.relate(work_a, primary_a, primary_a))
        self.refused_at_commit(psycopg.errors.ForeignKeyViolation, self.relate(work_a, primary_a, primary_b))
        self.refused_at_commit(psycopg.errors.ForeignKeyViolation, self.relate(work_b, primary_a, sibling))
        self.refused(psycopg.errors.CheckViolation,
                     ("INSERT INTO prks.manifestation_relations (library_id, work_id, from_id, to_id, relation) "
                      "VALUES (%s, %s, %s, %s, 'sequel_of')", (self.lib, work_a, sibling, primary_a)))
        statement, params = self.relate(work_a, sibling, primary_a)
        self.sql(statement, params)

    def test_moving_one_endpoint_fails_and_moving_both_carries_the_relation(self) -> None:
        work_a, primary_a = self.work()
        work_b, _ = self.work()
        with self.tx():
            one = self.manifestation(work_a)
            two = self.manifestation(work_a)
        statement, params = self.relate(work_a, one, two)
        self.sql(statement, params)
        self.refused_at_commit(
            psycopg.errors.ForeignKeyViolation,
            ("UPDATE prks.manifestations SET work_id = %s WHERE id = %s", (work_b, one)),
        )
        with self.tx():
            self.sql("UPDATE prks.manifestations SET work_id = %s WHERE id IN (%s, %s)", (work_b, one, two))
        self.assertEqual(
            self.value("SELECT work_id FROM prks.manifestation_relations WHERE from_id = %s", (one,)), work_b
        )
        del primary_a

    def test_deleting_an_endpoint_removes_its_relations(self) -> None:
        work_id, primary = self.work()
        with self.tx():
            sibling = self.manifestation(work_id)
        statement, params = self.relate(work_id, sibling, primary)
        self.sql(statement, params)
        self.sql("DELETE FROM prks.manifestations WHERE id = %s", (sibling,))
        self.assertEqual(self.count("manifestation_relations", "to_id", primary), 0)


class WorkDeleteTests(WorksSchemaTestCase):
    """Rows that own revision scopes are deleted level by level, never by cascade (§5.2)."""

    # The Works tables whose rows own revision scopes (§13.2 revision-ownership gate).
    SCOPE_OWNERS = ("works", "manifestations", "assets", "work_notes")

    def test_no_key_of_a_scope_owning_row_cascades_its_delete(self) -> None:
        rows = self.sql(
            "SELECT conrelid::regclass::text, conname FROM pg_constraint "
            "WHERE contype = 'f' AND confdeltype = 'c' AND conrelid = ANY(%s::regclass[])",
            ([f"prks.{table}" for table in self.SCOPE_OWNERS],)).fetchall()
        self.assertEqual(rows, [])

    def owned_work(self) -> tuple[str, str, str]:
        work_id, primary = self.work()
        with self.tx():
            sibling = self.manifestation(work_id)
        self.primary_asset(primary, work_id)
        self.primary_asset(sibling, work_id)
        self.sql("INSERT INTO prks.manifestation_relations (library_id, work_id, from_id, to_id, relation) "
                 "VALUES (%s, %s, %s, %s, 'revision_of')", (self.lib, work_id, sibling, primary))
        self.sql("INSERT INTO prks.manifestation_identifiers (library_id, manifestation_id, scheme, value, "
                 "normalized) VALUES (%s, %s, 'doi', '10.1/X', '10.1/x')", (self.lib, primary))
        for kind in ("research", "private"):
            self.sql("INSERT INTO prks.work_notes (library_id, work_id, kind, body) VALUES (%s, %s, %s, 'n')",
                     (self.lib, work_id, kind))
        return work_id, primary, sibling

    def test_a_delete_that_skips_a_level_is_refused(self) -> None:
        work_id, primary, _ = self.owned_work()
        self.refused(psycopg.errors.ForeignKeyViolation,
                     ("DELETE FROM prks.work_notes WHERE work_id = %s", (work_id,)),
                     ("DELETE FROM prks.works WHERE id = %s", (work_id,)))
        self.refused(psycopg.errors.ForeignKeyViolation,
                     ("DELETE FROM prks.manifestations WHERE id = %s", (primary,)))
        for table in ("manifestations", "assets", "work_notes"):
            with self.subTest(table=table):
                self.assertGreater(self.count(table, "work_id", work_id), 0)

    def test_deleting_each_level_removes_the_work_and_keeps_its_lifecycle(self) -> None:
        work_id, primary, _ = self.owned_work()
        with self.tx():
            self.sql("INSERT INTO prks.sync_work_lifecycle (library_id, work_id, state) VALUES (%s, %s, 'deleted')",
                     (self.lib, work_id))
            for table in ("work_notes", "assets", "manifestations"):
                self.sql(f"DELETE FROM prks.{table} WHERE work_id = %s", (work_id,))
            self.sql("DELETE FROM prks.works WHERE id = %s", (work_id,))
        for table in ("manifestations", "assets", "manifestation_relations", "work_notes"):
            with self.subTest(table=table):
                self.assertEqual(self.count(table, "work_id", work_id), 0)
        self.assertEqual(self.count("manifestation_identifiers", "manifestation_id", primary), 0)
        self.assertEqual(self.count("sync_work_lifecycle", "work_id", work_id), 1)

    def test_a_library_with_works_is_not_deleted_silently(self) -> None:
        self.work()
        self.refused(psycopg.errors.ForeignKeyViolation,
                     ("DELETE FROM prks.libraries WHERE id = %s", (self.lib,)))


class LibraryIsolationTests(WorksSchemaTestCase):
    """No Works row pairs one Library's child with another Library's parent (PG6)."""

    def test_children_must_share_their_parents_library(self) -> None:
        other = self.library()
        work_id, primary = self.work()
        asset = self.primary_asset(primary, work_id)
        cases: list[tuple[str, type[psycopg.Error], tuple[str, Any]]] = [
            ("manifestation", psycopg.errors.ForeignKeyViolation,
             ("INSERT INTO prks.manifestations (library_id, id, work_id) VALUES (%s, %s, %s)",
              (other, new_id("MF"), work_id))),
            ("asset", psycopg.errors.ForeignKeyViolation,
             ("INSERT INTO prks.assets (library_id, id, manifestation_id, work_id, kind, storage_locator, "
              "content_sha256, origin) VALUES (%s, %s, %s, %s, 'managed_file', 'x.pdf', %s, 'upload')",
              (other, new_id("AS"), primary, work_id, SHA))),
            ("identifier", psycopg.errors.ForeignKeyViolation,
             ("INSERT INTO prks.manifestation_identifiers (library_id, manifestation_id, scheme, value, "
              "normalized) VALUES (%s, %s, 'isbn', '1', '1')", (other, primary))),
            ("note", psycopg.errors.ForeignKeyViolation,
             ("INSERT INTO prks.work_notes (library_id, work_id, kind, body) VALUES (%s, %s, 'research', 'x')",
              (other, work_id))),
        ]
        other_work, other_primary = self.work(other)
        cases.append(("derived_from across Libraries", psycopg.errors.ForeignKeyViolation,
                      ("INSERT INTO prks.assets (library_id, id, manifestation_id, work_id, kind, storage_locator, "
                       "content_sha256, origin, derived_from_asset_id) "
                       "VALUES (%s, %s, %s, %s, 'managed_file', 'y.pdf', %s, 'upload', %s)",
                       (other, new_id("AS"), other_primary, other_work, SHA, asset))))
        for label, error, statement in cases:
            with self.subTest(label):
                self.refused(error, statement)

    def test_pointers_and_relations_stay_in_one_library(self) -> None:
        other = self.library()
        work_id, primary = self.work()
        other_work, other_primary = self.work(other)
        self.refused_at_commit(
            psycopg.errors.ForeignKeyViolation,
            ("UPDATE prks.works SET citation_manifestation_id = %s WHERE id = %s", (other_primary, work_id)),
        )
        self.refused(
            psycopg.errors.ForeignKeyViolation,
            ("UPDATE prks.manifestations SET work_id = %s WHERE id = %s", (other_work, primary)),
        )
        self.refused(
            psycopg.errors.ForeignKeyViolation,
            ("UPDATE prks.works SET library_id = %s WHERE id = %s", (other, work_id)),
        )
        self.refused_at_commit(
            psycopg.errors.ForeignKeyViolation,
            ("INSERT INTO prks.manifestation_relations (library_id, work_id, from_id, to_id, relation) "
             "VALUES (%s, %s, %s, %s, 'reprint_of')", (self.lib, work_id, primary, other_primary)),
        )
        self.assertEqual(self.integrity(other), [])


class RecordTableTests(WorksSchemaTestCase):
    def test_id_formats_are_checked(self) -> None:
        for statement, params in (
            ("INSERT INTO prks.works (library_id, id, title, primary_manifestation_id) VALUES (%s, %s, 'T', %s)",
             (self.lib, "W-" + "a" * 32, new_id("MF"))),
            ("INSERT INTO prks.works (library_id, id, title, primary_manifestation_id) VALUES (%s, %s, 'T', %s)",
             (self.lib, "W-ABCDEF12", new_id("MF"))),  # the 8-hex legacy form is not accepted
            ("INSERT INTO prks.manifestations (library_id, id, work_id) VALUES (%s, %s, %s)",
             (self.lib, "M-" + "A" * 32, new_id("W"))),
            ("INSERT INTO prks.sync_work_lifecycle (library_id, work_id, state) VALUES (%s, %s, 'deleted')",
             (self.lib, "W-1")),
        ):
            with self.subTest(params=params):
                self.refused(psycopg.errors.CheckViolation, (statement, params))

    def test_value_checks(self) -> None:
        work_id, primary = self.work()
        for statement, params in (
            ("UPDATE prks.works SET status = 'Done' WHERE id = %s", (work_id,)),
            ("UPDATE prks.manifestations SET title = '' WHERE id = %s", (primary,)),
            ("UPDATE prks.manifestations SET abstract = '' WHERE id = %s", (primary,)),
            ("UPDATE prks.manifestations SET kind = 'expression' WHERE id = %s", (primary,)),
            ("INSERT INTO prks.work_notes (library_id, work_id, kind, body) VALUES (%s, %s, 'public', '')",
             (self.lib, work_id)),
            ("INSERT INTO prks.manifestation_identifiers (library_id, manifestation_id, scheme, value, normalized) "
             "VALUES (%s, %s, '', 'x', 'x')", (self.lib, primary)),
            ("INSERT INTO prks.manifestation_identifiers (library_id, manifestation_id, scheme, value, normalized) "
             "VALUES (%s, %s, 'doi', 'x', '')", (self.lib, primary)),
        ):
            with self.subTest(statement=statement):
                self.refused(psycopg.errors.CheckViolation, (statement, params))
        self.refused(
            psycopg.errors.UniqueViolation,
            ("INSERT INTO prks.work_notes (library_id, work_id, kind, body) VALUES (%s, %s, 'private', 'a')",
             (self.lib, work_id)),
            ("INSERT INTO prks.work_notes (library_id, work_id, kind, body) VALUES (%s, %s, 'private', 'b')",
             (self.lib, work_id)),
        )

    def test_lifecycle_redirects(self) -> None:
        source, target = new_id("W"), new_id("W")
        insert = ("INSERT INTO prks.sync_work_lifecycle (library_id, work_id, state, target_work_id) "
                  "VALUES (%s, %s, %s, %s)")
        for state, redirect in (("merged", None), ("deleted", target), ("active", target), ("merged", source),
                                ("gone", None)):
            with self.subTest(state=state, redirect=redirect):
                self.refused(psycopg.errors.CheckViolation, (insert, (self.lib, source, state, redirect)))
        # The record outlives any Work row: no Work has to exist.
        self.sql(insert, (self.lib, source, "merged", target))

    def test_duplicate_decisions_are_ordered_typed_pairs(self) -> None:
        low, high = sorted((new_id("W"), new_id("W")))
        insert = ("INSERT INTO prks.duplicate_decisions (library_id, entity_type, low_id, high_id, decision) "
                  "VALUES (%s, %s, %s, %s, %s)")
        for entity_type, a, b, decision in (
            ("work", high, low, "not_duplicate"),        # not ordered
            ("work", low, low, "not_duplicate"),
            ("manifestation", low, high, "not_duplicate"),  # IDs of another type
            ("work", low, high, "duplicate"),
            ("person", low, high, "not_duplicate"),
        ):
            with self.subTest(entity_type=entity_type, decision=decision):
                self.refused(psycopg.errors.CheckViolation, (insert, (self.lib, entity_type, a, b, decision)))
        self.sql(insert, (self.lib, "work", low, high, "not_duplicate"))
        self.refused(psycopg.errors.UniqueViolation, (insert, (self.lib, "work", low, high, "not_duplicate")))
