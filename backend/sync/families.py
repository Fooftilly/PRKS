"""What a PostgreSQL operation family is made of (``docs/postgresql-architecture.md`` §7.3).

Kept apart from :mod:`backend.sync.protocol`, which registers the handlers,
so a handler module can import these without importing the registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional, Protocol

from backend.db.database import Isolation
from backend.domain.identity.library_scope import LibraryScope
from backend.pg_facade.registry import NotPortedError

Result = tuple[int, dict[str, Any]]


class WorkFiling(Protocol):
    """Where ``CREATE_WORK`` files a new Work: its folder, playlist and roles.

    Those rows belong to the Organization and People lanes (L-O-1, L-P-1),
    which provide the implementation; until they land the default refuses
    with ``NotPortedError``.
    """

    def prepare(self, scope: LibraryScope, work_id: str,
                fields: dict[str, Any]) -> dict[str, Any] | Result:
        """Check and resolve the filing before the Work exists.

        Returns the result fields to report (``folder_id``, ``playlist_id``,
        ``role_count``), or the terminal refusal (``FOLDER_NOT_FOUND``, …).
        """
        ...

    def file(self, scope: LibraryScope, work_id: str, fields: dict[str, Any],
             prepared: dict[str, Any]) -> dict[str, Any]:
        """File the Work just created; returns further result fields."""
        ...


class UnportedFiling:
    """The filing until Organization and People are ported."""

    def prepare(self, scope: LibraryScope, work_id: str,
                fields: dict[str, Any]) -> dict[str, Any] | Result:
        raise NotPortedError("CREATE_WORK filing (folders, playlists, roles)")

    def file(self, scope: LibraryScope, work_id: str, fields: dict[str, Any],
             prepared: dict[str, Any]) -> dict[str, Any]:
        raise NotPortedError("CREATE_WORK filing (folders, playlists, roles)")


@dataclass(frozen=True)
class Environment:
    """What a handler takes from outside the database."""

    pdfs_dir: str  # the managed PDFs, for the projected rows' file sizes
    filing: WorkFiling = UnportedFiling()


class Handler(Protocol):
    """One operation family on PostgreSQL."""

    @property
    def isolation(self) -> Isolation:
        """The isolation of the transaction that applies it (§7.1)."""
        ...

    def validate(self, op: dict[str, Any]) -> None:
        """Raise ``ValueError(code)`` for what the family forbids (the SQLite rule)."""
        ...

    def apply(self, scope: LibraryScope, op: dict[str, Any], received_at: datetime,
              env: Environment) -> Result:
        """Apply the operation in the claimant's transaction."""
        ...

    def ledgered(self, op: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        """The part of ``result`` the ledger keeps for replay."""
        ...


@dataclass(frozen=True)
class FamilyHandler:
    """A handler from its parts; ``ledgered`` defaults to the whole result."""

    validate_fn: Callable[[dict[str, Any]], None]
    apply_fn: Callable[[LibraryScope, dict[str, Any], datetime, Environment], Result]
    isolation: Isolation = Isolation.READ_COMMITTED
    ledger_fn: Optional[Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] = None

    def validate(self, op: dict[str, Any]) -> None:
        self.validate_fn(op)

    def apply(self, scope: LibraryScope, op: dict[str, Any], received_at: datetime,
              env: Environment) -> Result:
        return self.apply_fn(scope, op, received_at, env)

    def ledgered(self, op: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        return result if self.ledger_fn is None else self.ledger_fn(op, result)
