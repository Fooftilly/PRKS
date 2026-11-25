"""The test's own handle on the Library a running E2E server serves (PG-C1 #640).

An E2E test asserts on stored state after the browser acts: a Work's saved
title, a durable operation's ledger row. :func:`library_for` gives it the
store the server itself writes: the server's PostgreSQL Library, acting as
the bootstrap owner.

``run`` calls a domain command or query with the server's database and the
owner's context. ``query`` and ``execute`` run one statement against the
``prks`` schema; ``command`` runs a domain function inside one
``library_command`` transaction.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, Callable, LiteralString, TypeVar

from tests.e2e.postgres_identity import owner_library

if TYPE_CHECKING:
    from backend.db.database import Database
    from backend.storage.config import StorageConfig

T = TypeVar("T")


class LibraryStore:
    """Domain calls, and direct reads, for one running server."""

    def __init__(self, server) -> None:
        self._server = server
        self._stack = contextlib.ExitStack()
        self._library, self._ctx = self._stack.enter_context(
            owner_library(server.library, server.storage_root)
        )

    @property
    def database(self) -> Database:
        """The server's database, through this store's own pool."""
        return self._library.database

    @property
    def storage(self) -> StorageConfig | None:
        """The server's storage root."""
        return self._library.storage

    def run(self, call: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """``call(database, owner context, *args, **kwargs)``: one domain command or query as the owner."""
        return call(self._library.database, self._ctx, *args, **kwargs)

    @property
    def pdfs_dir(self) -> str:
        """The server's PDF directory (a Folder or Playlist detail lists its Works' files)."""
        storage = self._library.storage
        assert storage is not None
        return storage.pdfs_dir

    def query(self, postgres: LiteralString, *, params: tuple = ()) -> list[dict]:
        """Rows of one read against the ``prks`` schema, with ``%s`` placeholders."""
        import psycopg
        from psycopg.rows import DictRow, dict_row

        with psycopg.Connection[DictRow].connect(self._server.library.conninfo, row_factory=dict_row) as conn:
            return [dict(row) for row in conn.execute(postgres, params).fetchall()]

    def execute(self, postgres: LiteralString, *, params: tuple = ()) -> None:
        """One write, committed (a test arranging state the UI cannot)."""
        import psycopg

        with psycopg.connect(self._server.library.conninfo) as conn:
            conn.execute(postgres, params)

    def command(self, postgres: Callable[[Any], T]) -> T:
        """``postgres(scope)`` inside one ``library_command`` transaction, as the owner.

        For a test standing in for another device: the write goes through
        the domain's own writer, so it moves revisions as the server would.
        """
        from backend.domain.identity.authorization import Capability
        from backend.domain.identity.library_scope import library_command

        return library_command(self._library.database, self._ctx, Capability.EDIT_LIBRARY, postgres)

    def close(self) -> None:
        self._stack.close()


def library_for(server) -> LibraryStore:
    """The server's :class:`LibraryStore`, opened once and closed by ``server.stop()``."""
    store = getattr(server, "_library_store", None)
    if store is None:
        store = LibraryStore(server)
        server._library_store = store
    return store
