"""The served PostgreSQL Library binding (#625, PG-3; ``docs/postgresql-architecture.md`` §5.5).

:class:`PostgresLibrary` is the library object the HTTP adapter (``server.py``,
``api/``, ``services/``) is handed: one PostgreSQL database and its data
root. It no longer has methods of its own. PG-C3 (#642, v0.9.0) moved every
route family to call domain commands and queries directly
(``backend/api/<family>.py`` with ``db.database``, ``db.storage`` and the
admitted request context), and each family's facade mixin went with it; the
old method names the tests still call live in ``tests/library_adapter.py``.
The last PG-C3 PR retires this package. A capability the Library documents
as not available yet raises :class:`NotPortedError`, answered 501. This
package must not import psycopg (the persistence boundary keeps it in
``backend/db/`` and ``backend/domain/``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.pg_facade.registry import FacadeMixin, NotPortedError

if TYPE_CHECKING:
    from backend.db.database import Database
    from backend.storage.config import StorageConfig


class PostgresLibrary:
    """The library object of one PostgreSQL database and data root."""

    def __init__(self, database: Database, *, storage: StorageConfig | None = None) -> None:
        self._database = database
        self._storage = storage

    @property
    def database(self) -> Database:
        return self._database

    @property
    def storage(self) -> StorageConfig | None:
        return self._storage


__all__ = [
    "FacadeMixin",
    "NotPortedError",
    "PostgresLibrary",
]
