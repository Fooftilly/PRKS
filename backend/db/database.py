"""Connection pool and transaction helpers (``docs/postgresql-architecture.md`` §4.3, §5.2, §7.1).

``Database`` owns one bounded ``psycopg_pool.ConnectionPool`` per process and
offers the two ways domain code touches PostgreSQL:

- :meth:`Database.command` runs a function in **one** transaction at READ
  COMMITTED (the default) or SERIALIZABLE. A SERIALIZABLE command that fails
  with ``40001``/``40P01`` is rolled back and run again from the start, a
  bounded number of times. That is safe because a command has no side
  effects outside its transaction (§5.2): the function must not do file,
  network or other external work, and it must expect to be called more than
  once.
- :meth:`Database.query` runs a function in one ``REPEATABLE READ READ ONLY``
  snapshot, so a projection built from several statements never mixes pre-
  and post-write state.

Both commit when the function returns, roll back when it raises, and turn
psycopg errors into the PRKS errors of :mod:`backend.db.errors`. Pool
exhaustion is a typed 503 (:class:`PoolExhaustedError`), never a hang.

Pooled connections run in autocommit mode, so a connection is only ever in a
transaction inside one of these helpers, and the helpers open that
transaction explicitly with its isolation level.
"""

from __future__ import annotations

import time
from random import SystemRandom
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from enum import Enum
from typing import Any, TypeVar

import psycopg
from psycopg import sql
from psycopg.abc import Params, QueryNoTemplate
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from backend.db.config import DatabaseConfig
from backend.db.errors import (
    ConstraintRegistry,
    DatabaseUnavailableError,
    is_retryable,
    map_database_error,
)

T = TypeVar("T")

# Retries after the first attempt for a SERIALIZABLE command (§7.1: "retried
# up to 3 times on 40001/40P01").
DEFAULT_SERIALIZABLE_RETRIES = 3

# Backoff before retry n (1-based) is uniform in [0, base * 2**(n-1)].
_RETRY_BACKOFF_BASE_SECONDS = 0.02
_JITTER = SystemRandom()


class Isolation(Enum):
    READ_COMMITTED = "READ COMMITTED"
    REPEATABLE_READ = "REPEATABLE READ"
    SERIALIZABLE = "SERIALIZABLE"


_COMMAND_ISOLATIONS = (Isolation.READ_COMMITTED, Isolation.SERIALIZABLE)


class Transaction:
    """The handle a command or query function receives.

    ``execute`` returns a cursor whose rows are dicts. ``attempt`` is 1 on the
    first run of a command and grows on each serialization retry.
    """

    def __init__(
        self,
        connection: psycopg.Connection[Any],
        *,
        isolation: Isolation,
        read_only: bool,
        attempt: int,
    ) -> None:
        self._connection = connection
        self.isolation = isolation
        self.read_only = read_only
        self.attempt = attempt

    @property
    def connection(self) -> psycopg.Connection[Any]:
        return self._connection

    def execute(self, query: QueryNoTemplate, params: Params | None = None) -> psycopg.Cursor[DictRow]:
        cursor = self._connection.cursor(row_factory=dict_row)
        cursor.execute(query, params)
        return cursor


def _begin_statement(isolation: Isolation, read_only: bool) -> sql.Composed:
    access = "READ ONLY" if read_only else "READ WRITE"
    return sql.SQL("SET TRANSACTION ISOLATION LEVEL {} {}").format(
        sql.SQL(isolation.value), sql.SQL(access)
    )


class Database:
    """One bounded connection pool plus the command/query helpers."""

    def __init__(
        self,
        config: DatabaseConfig,
        *,
        registry: ConstraintRegistry | None = None,
        open_pool: bool = True,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._registry = registry
        self._sleep = sleep
        self._pool = ConnectionPool(
            conninfo=config.conninfo,
            kwargs={"autocommit": True, "application_name": config.application_name},
            min_size=config.pool_min,
            max_size=config.pool_max,
            timeout=config.pool_timeout_seconds,
            max_idle=config.max_idle_seconds,
            check=ConnectionPool.check_connection,
            configure=self._configure_connection,
            name=config.application_name,
            open=False,
        )
        if open_pool:
            self.open()

    @property
    def config(self) -> DatabaseConfig:
        return self._config

    def open(self) -> None:
        """Open the pool and wait until its minimum connections exist."""
        try:
            self._pool.open(wait=True, timeout=self._config.pool_timeout_seconds)
        except PoolTimeout:
            self._pool.close()
            raise DatabaseUnavailableError() from None

    def close(self) -> None:
        self._pool.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _configure_connection(self, conn: psycopg.Connection[Any]) -> None:
        # Session-level settings, set once per physical connection. The
        # connection is in autocommit mode, so they persist.
        for name, value in self._config.session_settings().items():
            conn.execute("SELECT set_config(%s, %s, false)", (name, value))

    @contextmanager
    def _borrow(self) -> Iterator[psycopg.Connection[Any]]:
        try:
            with self._pool.connection() as conn:
                yield conn
        except PoolTimeout as exc:
            raise map_database_error(exc, self._registry) from exc

    def _run_once(
        self,
        fn: Callable[[Transaction], T],
        *,
        isolation: Isolation,
        read_only: bool,
        attempt: int,
    ) -> T:
        with self._borrow() as conn:
            with conn.transaction():
                conn.execute(_begin_statement(isolation, read_only))
                return fn(
                    Transaction(conn, isolation=isolation, read_only=read_only, attempt=attempt)
                )

    def command(
        self,
        fn: Callable[[Transaction], T],
        *,
        isolation: Isolation = Isolation.READ_COMMITTED,
        retries: int = DEFAULT_SERIALIZABLE_RETRIES,
    ) -> T:
        """Run ``fn`` in one read-write transaction and return its result.

        Only SERIALIZABLE commands are retried; at READ COMMITTED a
        ``40001``/``40P01`` becomes 503 ``RETRY`` at once (§5.4).
        """
        if isolation not in _COMMAND_ISOLATIONS:
            raise ValueError("a command runs at READ COMMITTED or SERIALIZABLE")
        if retries < 0:
            raise ValueError("retries must be >= 0")
        attempts = 1 + (retries if isolation is Isolation.SERIALIZABLE else 0)
        attempt = 1
        while True:
            try:
                return self._run_once(fn, isolation=isolation, read_only=False, attempt=attempt)
            except psycopg.Error as exc:
                if attempt < attempts and is_retryable(exc):
                    self._sleep(_JITTER.uniform(0, _RETRY_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)))
                    attempt += 1
                    continue
                raise map_database_error(exc, self._registry) from exc

    def query(self, fn: Callable[[Transaction], T]) -> T:
        """Run ``fn`` in one REPEATABLE READ READ ONLY snapshot."""
        try:
            return self._run_once(
                fn, isolation=Isolation.REPEATABLE_READ, read_only=True, attempt=1
            )
        except psycopg.Error as exc:
            raise map_database_error(exc, self._registry) from exc
