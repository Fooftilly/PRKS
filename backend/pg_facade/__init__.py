"""PostgreSQL transition facade (#625, PG-3; ``docs/postgresql-architecture.md`` §5.5).

:class:`PostgresLibrary` stands where ``server.py`` uses ``PRKSDatabase``
today: the same method names, signatures and return shapes, each delegating
to domain commands and queries once its lane ports it. It is composed of one
empty mixin per lane (``works.py``, ``people.py``, ...), so a lane adds its
methods in its own file and never edits this one or the parity fixture; the
``@ported`` decorator (:mod:`backend.pg_facade.registry`) is the one line
that registers a method.

Any ``PRKSDatabase`` method that is not ported raises :class:`NotPortedError`
here rather than falling back to SQLite. Nothing in the running application
constructs the facade yet: SQLite stays the only backend until the cutover
slices (PG-C1, PG-C2). This package must not import psycopg (the persistence
boundary keeps it in ``backend/db/`` and ``backend/domain/``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

from backend.pg_facade.annotations import AnnotationsMixin
from backend.pg_facade.organization import OrganizationMixin
from backend.pg_facade.people import PeopleMixin
from backend.pg_facade.processing import ProcessingMixin
from backend.pg_facade.registry import (
    Coverage,
    FacadeMixin,
    NotPortedError,
    RegistryError,
    coverage,
    facade_surface,
    ported,
    ported_methods,
    validate_registry,
)
from backend.pg_facade.research import ResearchMixin
from backend.pg_facade.search import SearchMixin
from backend.pg_facade.works import WorksMixin

if TYPE_CHECKING:
    from backend.db.database import Database
    from backend.storage.config import StorageConfig


def _reference_has(name: str) -> bool:
    from backend.db_manager import PRKSDatabase

    return hasattr(PRKSDatabase, name)


class PostgresLibrary(
    WorksMixin,
    PeopleMixin,
    OrganizationMixin,
    ResearchMixin,
    AnnotationsMixin,
    ProcessingMixin,
    SearchMixin,
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

    def __getattr__(self, name: str) -> NoReturn:
        # Only reached when normal lookup fails: an unported PRKSDatabase
        # method is a typed refusal, anything else a plain AttributeError.
        if not name.startswith("__") and _reference_has(name):
            raise NotPortedError(name)
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")


__all__ = [
    "Coverage",
    "FacadeMixin",
    "NotPortedError",
    "PostgresLibrary",
    "RegistryError",
    "coverage",
    "facade_surface",
    "ported",
    "ported_methods",
    "validate_registry",
]
