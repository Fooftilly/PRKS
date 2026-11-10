"""The test's own handle on the Library a running E2E server serves (PG-C1 #640).

An E2E test asserts on stored state after the browser acts: a Work's saved
title, a durable operation's ledger row. :func:`library_for` gives it the
store the server itself writes, so the same assertion reads SQLite on the
default leg and PostgreSQL on the ``PRKS_E2E_LIBRARY_BACKEND=postgres`` leg.

The public ``PRKSDatabase`` method names work on both. ``query`` runs one
statement in the store's own dialect, so a test that reads a table directly
names which SQL it means (``sqlite=`` and ``postgres=``) rather than relying
on SQLite text that PostgreSQL would reject or read differently.
"""

from __future__ import annotations

import contextlib
from typing import Any, Optional

from backend.storage.config import StorageConfig
from tests.e2e.postgres_identity import owner_facade, postgres_library


# Research Network reads and writes: methods on the PostgreSQL facade, module
# functions taking the store on SQLite (backend.research_network).
_SQLITE_RESEARCH_FUNCTIONS = frozenset({
    "get_concept", "get_position", "update_position", "get_argument", "update_argument",
    "replace_argument_sources", "replace_argument_targets",
})


class LibraryStore:
    """Public Library methods, and dialect-explicit reads, for one running server."""

    def __init__(self, server) -> None:
        self._server = server
        self._stack = contextlib.ExitStack()
        self._sqlite = None
        self._facade = None
        self._ctx = None
        if postgres_library():
            self._facade, self._ctx = self._stack.enter_context(
                owner_facade(server.library, server.storage_root)
            )
        else:
            from backend.db_manager import PRKSDatabase
            from tests.e2e.fixtures import SCHEMA

            self._sqlite = PRKSDatabase(
                storage=StorageConfig.for_testing(server.storage_root), schema_path=str(SCHEMA)
            )

    @property
    def is_postgres(self) -> bool:
        return self._facade is not None

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if self._sqlite is not None:
            if name in _SQLITE_RESEARCH_FUNCTIONS:
                from backend import research_network

                function = getattr(research_network, name)
                return lambda *args, **kwargs: function(self._sqlite, *args, **kwargs)
            return getattr(self._sqlite, name)
        method = getattr(self._facade, name)
        if not callable(method):
            return method

        def call(*args, **kwargs):
            from backend.pg_facade.context import acting_as

            with acting_as(self._ctx):
                return method(*args, **kwargs)

        return call

    def query(self, *, sqlite: str, postgres: Optional[str] = None, params: tuple = ()) -> list[dict]:
        """Rows of one read in this store's dialect.

        ``postgres`` is the same read against the ``prks`` schema, with
        ``%s`` placeholders; omit it only for a test the PostgreSQL leg never
        runs (it then fails loudly there).
        """
        if self._sqlite is not None:
            return [dict(row) for row in self._sqlite.execute_query(sqlite, params)]
        if postgres is None:
            raise AssertionError("this read names no PostgreSQL query: %s" % sqlite)
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self._server.library.conninfo, row_factory=dict_row) as conn:
            return [dict(row) for row in conn.execute(postgres, params).fetchall()]

    def execute(self, *, sqlite: str, postgres: str, params: tuple = ()) -> None:
        """One write in this store's dialect, committed (a test arranging state the UI cannot)."""
        if self._sqlite is not None:
            self._sqlite.execute_query(sqlite, params)
            return
        import psycopg

        with psycopg.connect(self._server.library.conninfo) as conn:
            conn.execute(postgres, params)

    def command(self, *, sqlite, postgres):
        """One write through the store's own domain code, in its own transaction.

        For a test standing in for another device: ``sqlite(conn, db)`` runs
        the SQLite sync helper inside ``BEGIN IMMEDIATE``; ``postgres(scope)``
        runs the PostgreSQL domain function inside the facade's
        ``library_command`` transaction, as the owner.
        """
        if self._sqlite is not None:
            with self._sqlite.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                return sqlite(conn, self._sqlite)
        from backend.pg_facade.context import acting_as

        with acting_as(self._ctx):
            return self._facade._command(postgres)

    def close(self) -> None:
        self._stack.close()


def library_for(server) -> LibraryStore:
    """The server's :class:`LibraryStore`, opened once and closed by ``server.stop()``."""
    store = getattr(server, "_library_store", None)
    if store is None:
        store = LibraryStore(server)
        server._library_store = store
    return store
