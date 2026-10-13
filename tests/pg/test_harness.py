"""The test-database harness refuses anything it did not create (#623, §13.1)."""

from __future__ import annotations

import unittest
import unittest.mock

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from tests.pg.harness import (
    MARKER_PREFIX,
    HarnessRefusal,
    PostgresHarness,
    PostgresTestCase,
    admin_conninfo_from_env,
    compose,
    fetch_one,
)


class HarnessTests(PostgresTestCase):
    def setUp(self) -> None:
        admin = admin_conninfo_from_env()
        if admin is None:
            self.fail("PRKS_TEST_DATABASE_URL is not set")
        self.admin_conninfo = admin
        self.foreign: list[str] = []

    def admin(self) -> psycopg.Connection:
        conn = psycopg.connect(self.admin_conninfo, autocommit=True)
        self.addCleanup(conn.close)
        return conn

    def create_foreign(self, name: str, comment: str | None = None) -> None:
        """A database the harness under test did not create (a stand-in for a real library)."""
        conn = self.admin()
        conn.execute(compose(sql.SQL("CREATE DATABASE "), sql.Identifier(name)))
        self.addCleanup(self.drop_foreign, name)
        if comment is not None:
            conn.execute(
                compose(
                    sql.SQL("COMMENT ON DATABASE "), sql.Identifier(name),
                    sql.SQL(" IS "), sql.Literal(comment),
                )
            )
        with psycopg.connect(make_conninfo(self.admin_conninfo, dbname=name), autocommit=True) as db:
            db.execute("CREATE TABLE precious (v text)")
            db.execute("INSERT INTO precious VALUES ('keep')")

    def drop_foreign(self, name: str) -> None:
        self.admin().execute(
            compose(sql.SQL("DROP DATABASE IF EXISTS "), sql.Identifier(name), sql.SQL(" WITH (FORCE)"))
        )

    def assert_untouched(self, name: str, comment: str | None) -> None:
        conn = self.admin()
        row = conn.execute(
            "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = %s", (name,)
        ).fetchone()
        if row is None:
            self.fail("the foreign database must still exist")
        self.assertEqual(row[0], comment)
        with psycopg.connect(make_conninfo(self.admin_conninfo, dbname=name)) as db:
            self.assertEqual(db.execute("SELECT v FROM precious").fetchall(), [("keep",)])

    def new_harness(self, run_id: str) -> PostgresHarness:
        harness = PostgresHarness(self.admin_conninfo, run_id=run_id)
        self.addCleanup(harness.drop_all)
        return harness

    # -- ownership ----------------------------------------------------------

    def test_class_database_is_marked_with_the_run(self) -> None:
        self.assertEqual(self.harness.marker_of(self.database_name), self.harness.marker)
        self.assertTrue(self.harness.marker.startswith(MARKER_PREFIX))

    def test_refuses_pre_existing_database_with_the_name_it_would_create(self) -> None:
        harness = self.new_harness("0123456789ab")
        name = "prks_test_0123456789ab_template1"
        self.create_foreign(name)
        with self.assertRaises(HarnessRefusal):
            harness.template()
        self.assert_untouched(name, None)

    def test_refuses_pre_existing_database_even_with_a_harness_marker(self) -> None:
        harness = self.new_harness("0123456789ac")
        name = "prks_test_0123456789ac_template1"
        self.create_foreign(name, comment=harness.marker)
        with self.assertRaises(HarnessRefusal):
            harness.template()
        with self.assertRaises(HarnessRefusal):
            harness.drop_database(name)
        self.assert_untouched(name, harness.marker)

    def test_refuses_to_hand_out_or_drop_a_foreign_database(self) -> None:
        name = f"prks_test_{self.harness.run_id}_foreign99"
        self.create_foreign(name, comment=self.harness.marker)
        with self.assertRaises(HarnessRefusal):
            self.harness.conninfo(name)
        with self.assertRaises(HarnessRefusal):
            self.harness.drop_database(name)
        with self.assertRaises(HarnessRefusal):
            self.harness.conninfo("postgres")
        self.assert_untouched(name, self.harness.marker)

    def test_refuses_a_database_whose_marker_changed(self) -> None:
        name = self.harness.create_database("m")
        self.addCleanup(self.cleanup_retagged, name)
        self.admin().execute(
            compose(sql.SQL("COMMENT ON DATABASE "), sql.Identifier(name), sql.SQL(" IS 'someone else'"))
        )
        with self.assertRaises(HarnessRefusal):
            self.harness.conninfo(name)
        with self.assertRaises(HarnessRefusal):
            self.harness.drop_database(name)

    def cleanup_retagged(self, name: str) -> None:
        self.harness._created.discard(name)
        self.drop_foreign(name)

    def test_drop_all_drops_only_its_own_databases(self) -> None:
        harness = self.new_harness("0123456789ad")
        other = f"prks_test_{self.harness.run_id}_foreign98"
        self.create_foreign(other, comment=self.harness.marker)
        first = harness.create_database()
        second = harness.create_database()
        template = harness.template()
        harness.drop_all()
        for name in (first, second, template):
            self.assertFalse(harness.exists(name))
        self.assert_untouched(other, self.harness.marker)
        self.assertTrue(self.harness.exists(self.database_name))

    # -- maintenance URL ------------------------------------------------------

    def test_refuses_a_non_maintenance_url(self) -> None:
        with self.assertRaises(HarnessRefusal):
            PostgresHarness(make_conninfo(self.admin_conninfo, dbname="prks"))
        with self.assertRaises(HarnessRefusal):
            PostgresHarness(self.database_conninfo)

    def test_refuses_the_live_database_url(self) -> None:
        with unittest.mock.patch.dict("os.environ", {"PRKS_DATABASE_URL": self.admin_conninfo}):
            with self.assertRaises(HarnessRefusal):
                PostgresHarness(self.admin_conninfo)

    def test_rejects_malformed_run_id(self) -> None:
        with self.assertRaises(ValueError):
            PostgresHarness(self.admin_conninfo, run_id="Robert'); DROP")

    # -- isolation -------------------------------------------------------------

    def test_clones_are_isolated_and_use_the_builtin_locale(self) -> None:
        first = self.harness.create_database("iso")
        second = self.harness.create_database("iso")
        self.addCleanup(self.harness.drop_database, first)
        self.addCleanup(self.harness.drop_database, second)
        with psycopg.connect(self.harness.conninfo(first), autocommit=True) as conn:
            conn.execute("CREATE TABLE only_here (v int)")
        with psycopg.connect(self.harness.conninfo(second)) as conn:
            row = conn.execute("SELECT to_regclass('only_here') IS NULL").fetchone()
            self.assertEqual(row, (True,))
            locale = conn.execute(
                "SELECT datlocprovider, datlocale FROM pg_database WHERE datname = current_database()"
            ).fetchone()
            self.assertEqual(locale, ("b", "C.UTF-8"))
        self.assertEqual(self.harness.marker_of(second), self.harness.marker)


if __name__ == "__main__":
    unittest.main()
