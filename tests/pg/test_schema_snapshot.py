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


class TypeDefinitionsTests(PostgresTestCase):
    """A changed type definition changes the snapshot, not just a new type."""

    def setUp(self) -> None:
        self.conn = self.connect()
        self.addCleanup(self.conn.rollback)
        self.conn.execute(
            "CREATE DOMAIN prks.probe_d AS text NOT NULL DEFAULT 'x' "
            "CONSTRAINT probe_d_len CHECK (length(VALUE) < 10);"
            "CREATE TYPE prks.probe_e AS ENUM ('a', 'b');"
            "CREATE TYPE prks.probe_c AS (x int, y text);"
            "CREATE TYPE prks.probe_r AS RANGE (subtype = int4)"
        )

    def types(self) -> object:
        return catalog_snapshot(self.conn)["types"]

    def test_each_kind_records_its_definition(self) -> None:
        for change in (
            "ALTER DOMAIN prks.probe_d SET DEFAULT 'y'",
            "ALTER DOMAIN prks.probe_d DROP NOT NULL",
            "ALTER DOMAIN prks.probe_d DROP CONSTRAINT probe_d_len",
            "ALTER TYPE prks.probe_e ADD VALUE 'c'",
            "ALTER TYPE prks.probe_e RENAME VALUE 'a' TO 'z'",
            "ALTER TYPE prks.probe_c ADD ATTRIBUTE z bigint",
            "ALTER TYPE prks.probe_c ALTER ATTRIBUTE x TYPE bigint",
            "DROP TYPE prks.probe_r; CREATE TYPE prks.probe_r AS RANGE (subtype = int8)",
        ):
            with self.subTest(change):
                self.conn.execute("SAVEPOINT change")
                try:
                    before = self.types()
                    self.conn.execute(change)
                    self.assertNotEqual(self.types(), before)
                finally:
                    self.conn.execute("ROLLBACK TO SAVEPOINT change")


class SequenceDefinitionTests(PostgresTestCase):
    def test_sequence_options_are_recorded(self) -> None:
        conn = self.connect()
        self.addCleanup(conn.rollback)
        conn.execute("CREATE SEQUENCE prks.probe_s")
        for change in (
            "ALTER SEQUENCE prks.probe_s AS integer",
            "ALTER SEQUENCE prks.probe_s START WITH 5",
            "ALTER SEQUENCE prks.probe_s INCREMENT BY 2",
            "ALTER SEQUENCE prks.probe_s MINVALUE 0",
            "ALTER SEQUENCE prks.probe_s MAXVALUE 1000",
            "ALTER SEQUENCE prks.probe_s CACHE 10",
            "ALTER SEQUENCE prks.probe_s CYCLE",
        ):
            with self.subTest(change):
                conn.execute("SAVEPOINT change")
                try:
                    before = catalog_snapshot(conn)["tables"]
                    conn.execute(change)
                    self.assertNotEqual(catalog_snapshot(conn)["tables"], before)
                finally:
                    conn.execute("ROLLBACK TO SAVEPOINT change")


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

    def test_index_through_a_string_bodied_sql_wrapper_is_reported(self) -> None:
        # PostgreSQL records no dependency for a string body, so the guard
        # must fail closed on the wrapper itself.
        self.conn.execute(
            "CREATE FUNCTION guard_probe.swrap(s text) RETURNS text LANGUAGE sql IMMUTABLE "
            "AS $$ SELECT prks.search_norm(s) $$"
        )
        self.conn.execute("CREATE TABLE guard_probe.s (v text)")
        self.conn.execute("CREATE INDEX s_norm ON guard_probe.s (guard_probe.swrap(v))")
        self.assertTrue(any("s_norm" in d for d in self.dependents()), self.dependents())

    def test_index_through_a_plpgsql_wrapper_is_reported(self) -> None:
        self.conn.execute(
            "CREATE FUNCTION guard_probe.pwrap(s text) RETURNS text LANGUAGE plpgsql IMMUTABLE "
            "AS $$ BEGIN RETURN prks.search_norm(s); END $$"
        )
        self.conn.execute("CREATE TABLE guard_probe.pl (v text)")
        self.conn.execute("CREATE INDEX pl_norm ON guard_probe.pl (guard_probe.pwrap(v))")
        self.assertTrue(any("pl_norm" in d for d in self.dependents()), self.dependents())

    def test_generated_column_through_atomic_then_string_wrappers_is_reported(self) -> None:
        self.conn.execute(
            "CREATE FUNCTION guard_probe.inner_s(s text) RETURNS text LANGUAGE sql IMMUTABLE "
            "AS $$ SELECT prks.search_norm(s) $$"
        )
        self.conn.execute(
            "CREATE FUNCTION guard_probe.outer_a(s text) RETURNS text LANGUAGE sql IMMUTABLE "
            "BEGIN ATOMIC SELECT guard_probe.inner_s(s); END"
        )
        self.conn.execute(
            "CREATE TABLE guard_probe.chain (v text, n text GENERATED ALWAYS AS (guard_probe.outer_a(v)) STORED)"
        )
        self.assertNotEqual(self.dependents(), [])

    def test_unused_string_bodied_function_is_allowed(self) -> None:
        self.conn.execute(
            "CREATE FUNCTION guard_probe.helper(s text) RETURNS text LANGUAGE sql IMMUTABLE "
            "AS $$ SELECT prks.search_norm(s) $$"
        )
        self.conn.execute("CREATE TABLE guard_probe.h (v text, n text)")
        self.conn.execute("INSERT INTO guard_probe.h VALUES ('X', guard_probe.helper('X'))")
        self.assertEqual(self.dependents(), [])

    def test_plain_column_written_by_a_command_is_allowed(self) -> None:
        self.conn.execute("CREATE TABLE guard_probe.p (v text, n text)")
        self.conn.execute("INSERT INTO guard_probe.p VALUES ('X', prks.search_norm('X'))")
        self.assertEqual(self.dependents(), [])


if __name__ == "__main__":
    unittest.main()
