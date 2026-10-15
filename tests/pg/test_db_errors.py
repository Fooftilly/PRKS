"""SQLSTATE → PRKS error mapping, one real error per row of §5.4 (#623)."""

from __future__ import annotations

import socket
import unittest

import psycopg
from psycopg_pool import PoolTimeout

from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.db.errors import (
    ConstraintRegistry,
    ConstraintViolationError,
    DatabaseError,
    DatabaseUnavailableError,
    LockTimeoutError,
    PoolExhaustedError,
    RetryTransactionError,
    StatementTimeoutError,
    UnmappedDatabaseError,
    check_constraint_field,
    map_database_error,
)
from tests.pg.harness import INJECT, PostgresTestCase

# A row value that must never surface in a mapped error.
ROW_VALUE = "private-library-title"


class ErrorMappingTests(PostgresTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with psycopg.connect(cls.database_conninfo, autocommit=True) as conn:
            # Test-only tables in the class's own throwaway database.
            conn.execute(
                """
                CREATE SCHEMA pg1_test;
                CREATE TABLE pg1_test.parents (id int PRIMARY KEY);
                CREATE TABLE pg1_test.things (
                    id int PRIMARY KEY,
                    parent_id int CONSTRAINT things_parent_id_fkey REFERENCES pg1_test.parents (id),
                    name text,
                    other text,
                    amount int,
                    CONSTRAINT things_name_key UNIQUE (name),
                    CONSTRAINT things_other_key UNIQUE (other),
                    CONSTRAINT things_amount_check CHECK (amount >= 0),
                    CONSTRAINT oddly_named CHECK (amount <> 13)
                );
                CREATE TABLE pg1_test.children (
                    id int PRIMARY KEY,
                    thing_id int CONSTRAINT children_thing_id_fkey REFERENCES pg1_test.things (id)
                );
                INSERT INTO pg1_test.parents VALUES (1);
                """
            )

    def setUp(self) -> None:
        self.registry = ConstraintRegistry()
        self.registry.register("things_name_key", "NAME_TAKEN")
        self.registry.register("things_parent_id_fkey", "NOT_FOUND", http_status=404)
        self.registry.register("children_thing_id_fkey", "IN_USE")
        self.db = Database(
            DatabaseConfig(conninfo=self.database_conninfo, role="test", pool_max=2),
            registry=self.registry,
        )
        self.addCleanup(self.db.close)
        self.db.command(lambda tx: tx.execute("TRUNCATE pg1_test.children, pg1_test.things"))

    def raises(self, fn, error_type: type[DatabaseError] = DatabaseError) -> DatabaseError:
        with self.assertRaises(error_type) as caught:
            self.db.command(fn)
        error = caught.exception
        self.assertNotIn(ROW_VALUE, str(error))
        self.assertNotIn(ROW_VALUE, error.message)
        self.assertIsInstance(error.__cause__, (psycopg.Error, PoolTimeout))
        return error

    def insert(self, tx, id_: int, *, name: str | None = None, other: str | None = None,
               amount: int = 0, parent: int | None = None) -> None:
        tx.execute(
            "INSERT INTO pg1_test.things (id, parent_id, name, other, amount) VALUES (%s, %s, %s, %s, %s)",
            (id_, parent, name, other, amount),
        )

    def test_23505_registered_constraint_maps_to_domain_code(self) -> None:
        self.db.command(lambda tx: self.insert(tx, 1, name=ROW_VALUE))
        error = self.raises(lambda tx: self.insert(tx, 2, name=ROW_VALUE), ConstraintViolationError)
        self.assertEqual((error.code, error.http_status), ("NAME_TAKEN", 409))
        self.assertEqual((error.sqlstate, error.constraint), ("23505", "things_name_key"))

    def test_23505_unregistered_constraint_is_500(self) -> None:
        self.db.command(lambda tx: self.insert(tx, 1, other=ROW_VALUE))
        error = self.raises(lambda tx: self.insert(tx, 2, other=ROW_VALUE), UnmappedDatabaseError)
        self.assertEqual((error.code, error.http_status), ("DATABASE_ERROR", 500))
        self.assertEqual(error.constraint, "things_other_key")

    def test_23505_primary_key_not_registered_is_500(self) -> None:
        self.db.command(lambda tx: self.insert(tx, 1))
        error = self.raises(lambda tx: self.insert(tx, 1), UnmappedDatabaseError)
        self.assertEqual(error.constraint, "things_pkey")

    def test_23503_missing_parent_maps_to_not_found(self) -> None:
        error = self.raises(lambda tx: self.insert(tx, 1, parent=99), ConstraintViolationError)
        self.assertEqual((error.code, error.http_status, error.sqlstate), ("NOT_FOUND", 404, "23503"))

    def test_23503_referenced_row_maps_to_in_use(self) -> None:
        def setup(tx) -> None:
            self.insert(tx, 1)
            tx.execute("INSERT INTO pg1_test.children VALUES (1, 1)")

        self.db.command(setup)
        error = self.raises(lambda tx: tx.execute("DELETE FROM pg1_test.things WHERE id = 1"), ConstraintViolationError)
        self.assertEqual((error.code, error.http_status), ("IN_USE", 409))
        self.assertEqual(error.constraint, "children_thing_id_fkey")

    def test_23514_check_maps_to_invalid_value_with_field(self) -> None:
        error = self.raises(lambda tx: self.insert(tx, 1, amount=-1), ConstraintViolationError)
        self.assertEqual((error.code, error.http_status, error.sqlstate), ("INVALID_VALUE", 400, "23514"))
        self.assertEqual(error.field, "amount")

    def test_23514_unconventional_name_has_no_field(self) -> None:
        error = self.raises(lambda tx: self.insert(tx, 1, amount=13), ConstraintViolationError)
        self.assertEqual(error.code, "INVALID_VALUE")
        self.assertIsNone(error.field)

    def test_40001_and_40p01_at_read_committed_are_503_retry(self) -> None:
        for sqlstate in ("40001", "40P01"):
            with self.subTest(sqlstate=sqlstate):
                error = self.raises(
                    lambda tx, s=sqlstate: tx.execute(INJECT[s]),
                    RetryTransactionError,
                )
                self.assertEqual((error.code, error.http_status, error.retry_after), ("RETRY", 503, 1))
                self.assertEqual(error.sqlstate, sqlstate)

    def test_55p03_lock_not_available_is_503(self) -> None:
        self.db.command(lambda tx: self.insert(tx, 1))
        holder = self.connect()
        holder.execute("SELECT * FROM pg1_test.things WHERE id = 1 FOR UPDATE")
        self.addCleanup(holder.rollback)
        error = self.raises(
            lambda tx: tx.execute("SELECT * FROM pg1_test.things WHERE id = 1 FOR UPDATE NOWAIT"),
            LockTimeoutError,
        )
        self.assertEqual((error.code, error.http_status, error.sqlstate), ("LOCK_TIMEOUT", 503, "55P03"))
        self.assertIsNotNone(error.retry_after)

    def test_55p03_lock_timeout_is_503(self) -> None:
        self.db.command(lambda tx: self.insert(tx, 1))
        holder = self.connect()
        holder.execute("SELECT * FROM pg1_test.things WHERE id = 1 FOR UPDATE")
        self.addCleanup(holder.rollback)

        def wait_for_lock(tx) -> None:
            tx.execute("SET LOCAL lock_timeout = '50ms'")
            tx.execute("SELECT * FROM pg1_test.things WHERE id = 1 FOR UPDATE")

        self.raises(wait_for_lock, LockTimeoutError)

    def test_57014_statement_timeout_is_503(self) -> None:
        def slow(tx) -> None:
            tx.execute("SET LOCAL statement_timeout = '50ms'")
            tx.execute("SELECT pg_sleep(5)")

        error = self.raises(slow, StatementTimeoutError)
        self.assertEqual((error.code, error.http_status, error.sqlstate), ("STATEMENT_TIMEOUT", 503, "57014"))

    def test_other_sqlstate_is_500(self) -> None:
        error = self.raises(lambda tx: tx.execute("SELECT 1 / 0"), UnmappedDatabaseError)
        self.assertEqual((error.sqlstate, error.http_status), ("22012", 500))

    def test_terminated_session_is_503_unavailable(self) -> None:
        def terminate_self(tx) -> None:
            tx.execute("SELECT pg_terminate_backend(pg_backend_pid())")

        error = self.raises(terminate_self, DatabaseUnavailableError)
        self.assertEqual((error.code, error.http_status), ("DATABASE_UNAVAILABLE", 503))
        # The pool replaced the dead connection.
        self.assertEqual(self.db.query(lambda tx: tx.execute("SELECT 1 AS one").fetchone()), {"one": 1})


class ConnectionErrorMappingTests(unittest.TestCase):
    def test_unreachable_server_is_503_and_never_echoes_the_url(self) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        sentinel = "sentinel-pw-x9q"
        config = DatabaseConfig(
            conninfo=f"postgresql://prks:{sentinel}@127.0.0.1:{port}/prks?connect_timeout=1",
            role="test",
            pool_timeout_seconds=0.5,
        )
        with self.assertRaises(DatabaseUnavailableError) as caught:
            Database(config)
        self.assertEqual(caught.exception.http_status, 503)
        self.assertNotIn(sentinel, str(caught.exception))

    def test_operational_error_without_sqlstate_is_unavailable(self) -> None:
        error = map_database_error(psycopg.OperationalError("connection lost"))
        self.assertIsInstance(error, DatabaseUnavailableError)

    def test_pool_timeout_is_pool_exhausted(self) -> None:
        error = map_database_error(PoolTimeout("timeout"))
        self.assertIsInstance(error, PoolExhaustedError)
        self.assertEqual((error.code, error.http_status), ("DATABASE_BUSY", 503))

    def test_constraint_errors_without_diagnostics_fail_closed(self) -> None:
        class NoDiagnosticsError(psycopg.Error):
            def __init__(self, state: str) -> None:
                super().__init__("private constraint value")
                self._state = state

            @property
            def sqlstate(self) -> str:
                return self._state

            @property
            def diag(self) -> None:
                return None

        registry = ConstraintRegistry()
        registry.register("things_name_key", "NAME_TAKEN")
        for state in ("23505", "23503"):
            with self.subTest(sqlstate=state):
                error = map_database_error(NoDiagnosticsError(state), registry)
                self.assertIsInstance(error, UnmappedDatabaseError)
                self.assertEqual(
                    (error.code, error.http_status, error.sqlstate, error.constraint),
                    ("DATABASE_ERROR", 500, state, None),
                )
                self.assertNotIn("private constraint value", str(error))

        check_error = map_database_error(NoDiagnosticsError("23514"), registry)
        self.assertIsInstance(check_error, ConstraintViolationError)
        self.assertEqual((check_error.code, check_error.http_status), ("INVALID_VALUE", 400))
        self.assertEqual((check_error.sqlstate, check_error.constraint, check_error.field),
                         ("23514", None, None))
        self.assertNotIn("private constraint value", str(check_error))

    def test_non_database_error_is_500(self) -> None:
        self.assertIsInstance(map_database_error(RuntimeError("x")), UnmappedDatabaseError)

    def test_mapped_error_passes_through(self) -> None:
        error = PoolExhaustedError()
        self.assertIs(map_database_error(error), error)


class RegistryTests(unittest.TestCase):
    def test_identical_registration_is_idempotent(self) -> None:
        registry = ConstraintRegistry()
        registry.register("tags_name_key", "NAME_TAKEN")
        registry.register("tags_name_key", "NAME_TAKEN")
        mapping = registry.lookup("tags_name_key")
        self.assertIsNotNone(mapping)

    def test_conflicting_registration_refused(self) -> None:
        registry = ConstraintRegistry()
        registry.register("tags_name_key", "NAME_TAKEN")
        with self.assertRaises(ValueError):
            registry.register("tags_name_key", "ALIAS_TAKEN")
        with self.assertRaises(ValueError):
            registry.register("tags_name_key", "NAME_TAKEN", http_status=400)

    def test_check_constraint_field(self) -> None:
        self.assertEqual(check_constraint_field("works_title_check", "works"), "title")
        self.assertEqual(check_constraint_field("works_year_month_check", "works"), "year_month")
        self.assertIsNone(check_constraint_field("other_title_check", "works"))
        self.assertIsNone(check_constraint_field("works_check", "works"))
        self.assertIsNone(check_constraint_field(None, "works"))


if __name__ == "__main__":
    unittest.main()
