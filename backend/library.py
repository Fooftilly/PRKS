"""The Library handle the HTTP adapter is handed, and its typed refusal (PG-C3 #642).

:class:`PostgresLibrary` is one PostgreSQL database and its data root: what
``server.py``, ``api/`` and ``services/`` hold as ``db``. It has no Library
methods. Every route family calls domain commands and queries
(``backend/api/<family>.py``) with ``db.database``, ``db.storage`` and the
admitted :class:`~backend.domain.identity.request_context.RequestContext`,
which authorizes inside each transaction (``library_command`` and
``library_query``); nothing binds an actor beside the call any more.

PG-C3 retired the ``backend/pg_facade`` package that this replaces
(``docs/postgresql-architecture.md`` §5.5). Tests keep the facade's old
method names over the same commands in ``tests/library_adapter.py``.

A capability the Library documents as not available yet raises
:class:`NotPortedError`, which the HTTP adapter answers with 501 and never
as a silent fallback. This module must not import psycopg (the persistence
boundary keeps it in ``backend/db/`` and ``backend/domain/``), so the served
app can load it before PostgreSQL is reachable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.db.database import Database
    from backend.storage.config import StorageConfig


class NotPortedError(RuntimeError):
    """A Library capability the PostgreSQL Library does not implement yet."""

    def __init__(self, name: str) -> None:
        super().__init__(f"{name} is not ported to PostgreSQL yet")
        self.method = name


class PostgresLibrary:
    """One PostgreSQL database and its data root."""

    def __init__(self, database: Database, *, storage: StorageConfig | None = None) -> None:
        self._database = database
        self._storage = storage

    @property
    def database(self) -> Database:
        return self._database

    @property
    def storage(self) -> StorageConfig | None:
        return self._storage


__all__ = ["NotPortedError", "PostgresLibrary"]
