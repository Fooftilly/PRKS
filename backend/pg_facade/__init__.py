"""The PostgreSQL Library facade (#625, PG-3; ``docs/postgresql-architecture.md`` §5.5).

:class:`PostgresLibrary` is the library object the HTTP adapter (``server.py``,
``api/``, ``services/``) calls: one mixin per family (``works.py``,
``research.py``, ...), each delegating to domain commands and queries. A
capability the Library documents as not available yet raises
:class:`NotPortedError`. This package must not import psycopg (the
persistence boundary keeps it in ``backend/db/`` and ``backend/domain/``).

The facade is the temporary boundary between the HTTP adapter and the
domain, kept on purpose after the PostgreSQL cutover (PG-C2 #641). It is
the strangler seam of §5.5, not the target: PG-C3 (#642, v0.9.0) moves
route families to call domain commands and queries directly, one family per
PR, and the facade shrinks until it is gone. It holds only what a route
still calls; a method only tests call lives in ``tests/library_adapter.py``. Its method names and shapes
follow the retired SQLite ``PRKSDatabase``; the per-family modules list
where PostgreSQL deliberately differs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.pg_facade.annotations import AnnotationsMixin
from backend.pg_facade.registry import FacadeMixin, NotPortedError
from backend.pg_facade.works import WorksMixin

if TYPE_CHECKING:
    from backend.db.database import Database
    from backend.storage.config import StorageConfig


class PostgresLibrary(
    WorksMixin,
    AnnotationsMixin,
):
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
