"""Schema snapshot and the search_norm guard (#624, §4.4, §8.3, §13.2)."""

from __future__ import annotations

import json
import unittest

import psycopg

from backend.db.migrate import SCHEMA_DIR, load_schema_files, migrate
from backend.db.schema_snapshot import (
    SNAPSHOT_PATH,
    catalog_snapshot,
    dumps,
    search_norm_dependents,
)
from tests.pg.harness import PostgresTestCase, get_harness


def checked_in_snapshot() -> dict[str, object]:
    with open(SNAPSHOT_PATH, encoding="utf-8") as handle:
        return json.load(handle)


class SchemaSnapshotTests(unittest.TestCase):
    def test_fresh_database_matches_the_snapshot(self) -> None:
        harness = get_harness()
        name = harness.create_database("snap", empty=True)
        self.addCleanup(harness.drop_database, name)
        migrate(harness.conninfo(name))
        with psycopg.connect(harness.conninfo(name)) as conn:
            snapshot = catalog_snapshot(conn)
            self.assertEqual(search_norm_dependents(conn), [])
        expected = checked_in_snapshot()
        # Compare the text form so a failure shows the checked-in diff.
        with open(SNAPSHOT_PATH, encoding="utf-8") as handle:
            self.assertEqual(dumps(snapshot), handle.read())
        self.assertEqual(snapshot, expected)
        self.assertEqual(
            snapshot["schema_files"], [f.filename for f in load_schema_files(SCHEMA_DIR)]
        )


class TemplateCloneMatchesSnapshotTests(PostgresTestCase):
    def test_harness_clone_is_the_migrated_schema(self) -> None:
        with self.connect() as conn:
            self.assertEqual(catalog_snapshot(conn), checked_in_snapshot())


class SearchNormGuardTests(PostgresTestCase):
    """The guard reports what would store search_norm output (§8.3)."""

    def setUp(self) -> None:
        self.conn = self.connect()
        self.addCleanup(self.conn.rollback)
        self.conn.execute("CREATE SCHEMA guard_probe")

    def dependents(self) -> list[str]:
        return search_norm_dependents(self.conn)

    def test_clean_schema_has_none(self) -> None:
        self.assertEqual(self.dependents(), [])

    def test_expression_index_is_reported(self) -> None:
        self.conn.execute("CREATE TABLE guard_probe.t (v text)")
        self.conn.execute("CREATE INDEX t_norm ON guard_probe.t (prks.search_norm(v))")
        self.assertTrue(any("t_norm" in d for d in self.dependents()), self.dependents())

    def test_generated_column_is_reported(self) -> None:
        self.conn.execute(
            "CREATE TABLE guard_probe.g (v text, n text GENERATED ALWAYS AS (prks.search_norm(v)) STORED)"
        )
        self.assertNotEqual(self.dependents(), [])

    def test_column_default_is_reported(self) -> None:
        self.conn.execute("CREATE TABLE guard_probe.d (n text DEFAULT prks.search_norm('x'))")
        self.assertNotEqual(self.dependents(), [])

    def test_index_through_a_wrapper_function_is_reported(self) -> None:
        self.conn.execute(
            "CREATE FUNCTION guard_probe.wrap(s text) RETURNS text LANGUAGE sql IMMUTABLE "
            "BEGIN ATOMIC SELECT prks.search_norm(s); END"
        )
        self.conn.execute("CREATE TABLE guard_probe.w (v text)")
        self.conn.execute("CREATE INDEX w_norm ON guard_probe.w (guard_probe.wrap(v))")
        self.assertTrue(any("w_norm" in d for d in self.dependents()), self.dependents())

    def test_plain_column_written_by_a_command_is_allowed(self) -> None:
        self.conn.execute("CREATE TABLE guard_probe.p (v text, n text)")
        self.conn.execute("INSERT INTO guard_probe.p VALUES ('X', prks.search_norm('X'))")
        self.assertEqual(self.dependents(), [])


if __name__ == "__main__":
    unittest.main()
