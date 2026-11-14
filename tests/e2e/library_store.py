"""The test's own handle on the Library a running E2E server serves (PG-C1 #640).

An E2E test asserts on stored state after the browser acts: a Work's saved
title, a durable operation's ledger row. :func:`library_for` gives it the
store the server itself writes: the server's PostgreSQL Library, through the
facade, acting as the bootstrap owner.

The public facade method names work as on the server. ``query`` and
``execute`` run one statement against the ``prks`` schema; ``command`` runs a
domain function inside the facade's ``library_command`` transaction.
"""

from __future__ import annotations

import contextlib
from typing import Any

from tests.e2e.postgres_identity import owner_facade


class LibraryStore:
    """Public Library methods, and direct reads, for one running server."""

    def __init__(self, server) -> None:
        self._server = server
        self._stack = contextlib.ExitStack()
        self._facade, self._ctx = self._stack.enter_context(
            owner_facade(server.library, server.storage_root)
        )

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        method = getattr(self._facade, name)
        if not callable(method):
            return method

        def call(*args, **kwargs):
            from tests.acting_context import acting_as

            with acting_as(self._ctx):
                return method(*args, **kwargs)

        return call

    def query(self, postgres: str, *, params: tuple = ()) -> list[dict]:
        """Rows of one read against the ``prks`` schema, with ``%s`` placeholders."""
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self._server.library.conninfo, row_factory=dict_row) as conn:
            return [dict(row) for row in conn.execute(postgres, params).fetchall()]

    def execute(self, postgres: str, *, params: tuple = ()) -> None:
        """One write, committed (a test arranging state the UI cannot)."""
        import psycopg

        with psycopg.connect(self._server.library.conninfo) as conn:
            conn.execute(postgres, params)

    def command(self, postgres):
        """``postgres(scope)`` inside the facade's ``library_command`` transaction, as the owner.

        For a test standing in for another device: the write goes through
        the domain's own writer, so it moves revisions as the server would.
        """
        from tests.acting_context import acting_as

        with acting_as(self._ctx):
            return self._facade._edit(postgres)

    def close(self) -> None:
        self._stack.close()


def library_for(server) -> LibraryStore:
    """The server's :class:`LibraryStore`, opened once and closed by ``server.stop()``."""
    store = getattr(server, "_library_store", None)
    if store is None:
        store = LibraryStore(server)
        server._library_store = store
    return store
