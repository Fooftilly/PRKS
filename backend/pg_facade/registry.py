"""The facade's shared base and its typed refusal (#625, PG-3; PG-C2 #641).

Each family of :class:`backend.pg_facade.PostgresLibrary` lives in its own
mixin file, built on :class:`FacadeMixin`. A capability the PostgreSQL
Library documents as not available yet raises :class:`NotPortedError`, which
the HTTP adapter answers with 501 and never as a silent fallback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.db.database import Database
    from backend.storage.config import StorageConfig


class NotPortedError(RuntimeError):
    """A Library capability the PostgreSQL facade does not implement yet."""

    def __init__(self, name: str) -> None:
        super().__init__(f"{name} is not ported to PostgreSQL yet")
        self.method = name


class FacadeMixin:
    """Base of every family mixin: the state the facade core provides."""

    _database: Database
    _storage: StorageConfig | None
