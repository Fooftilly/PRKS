"""command()/query() helpers and the bounded pool against real PostgreSQL (#623)."""

from __future__ import annotations

import threading
import time
import unittest

from psycopg import sql

from backend.db.config import DatabaseConfig
from backend.db.database import Database, Isolation, Transaction
from backend.db.errors import PoolExhaustedError, RetryTransactionError, UnmappedDatabaseError
from tests.pg.harness import PostgresTestCase


class DomainRefusal(Exception):
    pass


def count(tx: Transaction) -> int:
    row = tx.execute("SELECT count(*) AS n FROM items").fetchone()
    assert row is not None
    return int(row["n"])


def inject(sqlstate: str) -> sql.Composed:
    return sql.SQL("DO $$ BEGIN RAISE EXCEPTION 'injected' USING ERRCODE = {}; END $$").format(
        sql.Literal(sqlstate)
    )


class TransactionHelperTests(PostgresTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        db = Database(DatabaseConfig(conninfo=cls.database_conninfo, role="test"))
        try:
            db.command(
                lambda tx: tx.execute(
                    "CREATE TABLE items (id serial PRIMARY KEY, kind int NOT NULL, label text)"
                )
            )
        finally:
            db.close()

    def setUp(self) -> None:
        self.sleeps: list[float] = []
        self.db = Database(
            DatabaseConfig(conninfo=self.database_conninfo, role="test", pool_max=4),
            sleep=self.sleeps.append,
        )
        self.addCleanup(self.db.close)
        self.db.command(lambda tx: tx.execute("TRUNCATE items"))

    def add(self, tx: Transaction, kind: int = 1, label: str | None = None) -> None:
        tx.execute("INSERT INTO items (kind, label) VALUES (%s, %s)", (kind, label))

    # -- commit and rollback --------------------------------------------------

    def test_command_commits_and_returns_result(self) -> None:
        def body(tx: Transaction) -> str:
            self.add(tx)
            return "done"

        self.assertEqual(self.db.command(body), "done")
        self.assertEqual(self.db.query(count), 1)

    def test_command_rolls_back_on_exception(self) -> None:
        def body(tx: Transaction) -> None:
            self.add(tx)
            raise DomainRefusal()

        with self.assertRaises(DomainRefusal):
            self.db.command(body)
        self.assertEqual(self.db.query(count), 0)

    def test_command_rolls_back_on_database_error(self) -> None:
        def body(tx: Transaction) -> None:
            self.add(tx)
            tx.execute("SELECT 1 / 0")

        with self.assertRaises(UnmappedDatabaseError):
            self.db.command(body)
        self.assertEqual(self.db.query(count), 0)

    def test_connection_is_reusable_after_failure(self) -> None:
        db = Database(DatabaseConfig(conninfo=self.database_conninfo, role="test", pool_max=1))
        self.addCleanup(db.close)
        def fail(tx: Transaction) -> None:
            self.add(tx)
            raise DomainRefusal()

        with self.assertRaises(DomainRefusal):
            db.command(fail)
        db.command(self.add)
        self.assertEqual(db.query(count), 1)

    # -- isolation -----------------------------------------------------------

    def settings(self, tx: Transaction) -> tuple[str, str]:
        row = tx.execute(
            "SELECT current_setting('transaction_isolation') AS iso, "
            "current_setting('transaction_read_only') AS ro"
        ).fetchone()
        assert row is not None
        return row["iso"], row["ro"]

    def test_command_defaults_to_read_committed_read_write(self) -> None:
        self.assertEqual(self.db.command(self.settings), ("read committed", "off"))

    def test_command_serializable_on_request(self) -> None:
        self.assertEqual(
            self.db.command(self.settings, isolation=Isolation.SERIALIZABLE), ("serializable", "off")
        )

    def test_query_is_repeatable_read_read_only(self) -> None:
        self.assertEqual(self.db.query(self.settings), ("repeatable read", "on"))

    def test_command_refuses_repeatable_read(self) -> None:
        with self.assertRaises(ValueError):
            self.db.command(self.settings, isolation=Isolation.REPEATABLE_READ)

    def test_query_refuses_writes(self) -> None:
        with self.assertRaises(UnmappedDatabaseError) as caught:
            self.db.query(self.add)
        self.assertEqual(caught.exception.code, "READ_ONLY_TRANSACTION")
        self.assertEqual(caught.exception.sqlstate, "25006")
        self.assertEqual(caught.exception.http_status, 500)
        self.assertEqual(self.db.query(count), 0)

    def test_query_sees_one_snapshot(self) -> None:
        other = self.connect(autocommit=True)

        def body(tx: Transaction) -> tuple[int, int]:
            first = count(tx)
            other.execute("INSERT INTO items (kind) VALUES (1)")
            return first, count(tx)

        self.assertEqual(self.db.query(body), (0, 0))
        self.assertEqual(self.db.query(count), 1)

    def test_pooled_session_settings(self) -> None:
        def read(tx: Transaction) -> dict:
            row = tx.execute(
                "SELECT current_setting('application_name') AS app, "
                "current_setting('statement_timeout') AS st, "
                "current_setting('lock_timeout') AS lt, "
                "current_setting('idle_in_transaction_session_timeout') AS it"
            ).fetchone()
            assert row is not None
            return dict(row)

        self.assertEqual(
            self.db.query(read), {"app": "prks-test", "st": "30s", "lt": "5s", "it": "1min"}
        )

    def test_set_local_does_not_leak_into_next_borrower(self) -> None:
        db = Database(DatabaseConfig(conninfo=self.database_conninfo, role="test", pool_max=1))
        self.addCleanup(db.close)
        db.command(lambda tx: tx.execute("SET LOCAL lock_timeout = '1ms'"))
        row = db.query(lambda tx: tx.execute("SELECT current_setting('lock_timeout') AS lt").fetchone())
        self.assertEqual(row, {"lt": "5s"})

    # -- serialization retry ------------------------------------------------

    def test_serializable_retries_injected_40001_then_commits_once(self) -> None:
        attempts: list[int] = []

        def body(tx: Transaction) -> int:
            attempts.append(tx.attempt)
            self.add(tx)
            if tx.attempt == 1:
                tx.execute(inject("40001"))
            return tx.attempt

        self.assertEqual(self.db.command(body, isolation=Isolation.SERIALIZABLE), 2)
        self.assertEqual(attempts, [1, 2])
        self.assertEqual(self.db.query(count), 1)
        self.assertEqual(len(self.sleeps), 1)

    def test_serializable_retries_deadlock_40p01(self) -> None:
        def body(tx: Transaction) -> int:
            if tx.attempt < 3:
                tx.execute(inject("40P01"))
            self.add(tx)
            return tx.attempt

        self.assertEqual(self.db.command(body, isolation=Isolation.SERIALIZABLE), 3)
        self.assertEqual(self.db.query(count), 1)

    def test_serializable_retry_is_bounded(self) -> None:
        attempts: list[int] = []

        def body(tx: Transaction) -> None:
            attempts.append(tx.attempt)
            self.add(tx)
            tx.execute(inject("40001"))

        with self.assertRaises(RetryTransactionError) as caught:
            self.db.command(body, isolation=Isolation.SERIALIZABLE)
        self.assertEqual(attempts, [1, 2, 3, 4])
        self.assertEqual(caught.exception.http_status, 503)
        self.assertEqual(self.db.query(count), 0)

        attempts.clear()
        with self.assertRaises(RetryTransactionError):
            self.db.command(body, isolation=Isolation.SERIALIZABLE, retries=0)
        self.assertEqual(attempts, [1])

    def test_read_committed_does_not_retry(self) -> None:
        attempts: list[int] = []

        def body(tx: Transaction) -> None:
            attempts.append(tx.attempt)
            tx.execute(inject("40001"))

        with self.assertRaises(RetryTransactionError):
            self.db.command(body)
        self.assertEqual(attempts, [1])
        self.assertEqual(self.sleeps, [])

    def test_other_errors_are_not_retried(self) -> None:
        attempts: list[int] = []

        def body(tx: Transaction) -> None:
            attempts.append(tx.attempt)
            raise DomainRefusal()

        with self.assertRaises(DomainRefusal):
            self.db.command(body, isolation=Isolation.SERIALIZABLE)
        self.assertEqual(attempts, [1])

    def test_real_write_skew_is_retried_by_serializable(self) -> None:
        """Two SERIALIZABLE commands each read the other's set, then write.

        Both read before either writes (a barrier on the first attempt), so
        PostgreSQL's SSI must fail one with 40001; the helper retries it and
        the retry sees the other's committed row.
        """
        barrier = threading.Barrier(2, timeout=10)
        results: dict[int, tuple[int, int]] = {}
        errors: list[BaseException] = []

        def make(kind: int, reads: int):
            def body(tx: Transaction) -> tuple[int, int]:
                row = tx.execute("SELECT count(*) AS n FROM items WHERE kind = %s", (reads,)).fetchone()
                assert row is not None
                if tx.attempt == 1:
                    barrier.wait()
                self.add(tx, kind=kind)
                return tx.attempt, int(row["n"])

            return body

        def run(kind: int, reads: int) -> None:
            try:
                results[kind] = self.db.command(make(kind, reads), isolation=Isolation.SERIALIZABLE)
            except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
                errors.append(exc)

        threads = [
            threading.Thread(target=run, args=(1, 2)),
            threading.Thread(target=run, args=(2, 1)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(errors, [])
        attempts = sorted(attempt for attempt, _seen in results.values())
        self.assertEqual(attempts, [1, 2])
        retried = next(seen for attempt, seen in results.values() if attempt == 2)
        self.assertEqual(retried, 1)
        self.assertEqual(self.db.query(count), 2)


class PoolExhaustionTests(PostgresTestCase):
    def test_exhausted_pool_raises_typed_503_without_hanging(self) -> None:
        db = Database(
            DatabaseConfig(
                conninfo=self.database_conninfo, role="test", pool_max=1, pool_timeout_seconds=0.3
            )
        )
        self.addCleanup(db.close)
        held = threading.Event()
        release = threading.Event()

        def hold(tx: Transaction) -> None:
            held.set()
            release.wait(10)

        holder = threading.Thread(target=lambda: db.command(hold))
        holder.start()
        self.addCleanup(holder.join, 10)
        self.addCleanup(release.set)
        self.assertTrue(held.wait(10))

        started = time.monotonic()
        with self.assertRaises(PoolExhaustedError) as caught:
            db.query(lambda tx: tx.execute("SELECT 1").fetchone())
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 3.0)
        self.assertEqual(caught.exception.http_status, 503)
        self.assertEqual(caught.exception.code, "DATABASE_BUSY")
        self.assertIsNotNone(caught.exception.retry_after)

        with self.assertRaises(PoolExhaustedError):
            db.command(lambda tx: None)

        release.set()
        holder.join(10)
        self.assertEqual(db.query(lambda tx: tx.execute("SELECT 1 AS one").fetchone()), {"one": 1})


if __name__ == "__main__":
    unittest.main()
