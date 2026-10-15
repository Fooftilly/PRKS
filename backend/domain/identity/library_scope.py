"""Library-scoped commands and queries with epoch fencing (``docs/postgresql-architecture.md`` §5.2, §9.4, §11.4; L-ID-1 #626).

Every Library-scoped mutation goes through :func:`library_command`, which
runs the caller's function in **one** transaction, in this order:

1. ``SET LOCAL prks.library_id`` to ``ctx.library_id`` (``set_config(…,
   true)``), ready for row-level security later (§9.4; RLS is not enabled).
2. ``pg_try_advisory_xact_lock_shared`` on the Library maintenance key. A
   restore takes the same key exclusively (R3) before it commits its
   replacement and advances the epoch (R4); if it holds or waits for it
   now, the command refuses ``MAINTENANCE`` instead of queueing behind it.
3. ``authorize(tx, ctx, capability)``, which locks the membership and user
   rows ``FOR SHARE``.
4. ``SELECT epoch, maintenance FROM prks.libraries … FOR SHARE``: a committed
   maintenance flag refuses ``MAINTENANCE``; an epoch other than
   ``ctx.epoch`` (older or newer) refuses ``LIBRARY_EPOCH_CHANGED``.
5. The caller's function, given a :class:`LibraryScope`.

Each refusal raises inside the transaction, so it rolls back with nothing
written. Once step 4 passes, the epoch cannot change before the command
commits, for two independent reasons: the shared advisory lock makes a
restore's exclusive lock (and so its epoch change) wait for this command, and
the ``FOR SHARE`` row lock makes any ``UPDATE`` of the Library row wait for
it as well. A command prepared against an older epoch therefore never
commits, whether it comes from a browser, a durable operation or a job.

A mutation without an acted-on epoch is refused (``LIBRARY_EPOCH_REQUIRED``)
before any database work. Identity and instance operations act on no Library
and take no epoch: bootstrap, login, sign-out and session renewal keep their
own transactions, and instance administration uses :func:`instance_command`.

:func:`library_query` runs a read in one ``REPEATABLE READ READ ONLY``
snapshot, authorized for ``READ_LIBRARY``, and gives the current epoch for
the response to carry. Reads are not fenced by epoch.

:meth:`LibraryScope.execute` only runs statements that filter by
``%(library_id)s`` and fills that parameter from the context, so a caller
cannot omit its Library or pick another one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, LiteralString, TypeVar

import psycopg
from psycopg.rows import DictRow

from backend.db.database import Database, Isolation, Transaction
from backend.domain.identity.authorization import (
    LIBRARY_CAPABILITIES,
    AccessRefused,
    Capability,
    Refusal,
    authorize,
)
from backend.domain.identity.request_context import RequestContext

T = TypeVar("T")

# The Library maintenance lock key, ``lib_key(L)`` in §11.4: a 64-bit hash of
# the Library id. Restore and its recovery take it exclusively with this same
# expression.
LIBRARY_LOCK_KEY_SQL: LiteralString = "hashtextextended('prks.library:' || %s, 0)"

_SCOPE_PARAMETER = "%(library_id)s"


@dataclass(frozen=True)
class LibraryScope:
    """The transaction of an authorized, epoch-checked Library operation."""

    tx: Transaction
    library_id: str
    epoch: int  # the Library's current epoch (equal to ctx.epoch in a command)

    def execute(
        self, query: LiteralString, params: Mapping[str, Any] | None = None
    ) -> psycopg.Cursor[DictRow]:
        """Run a statement that filters by ``%(library_id)s``, bound to this Library."""
        if _SCOPE_PARAMETER not in query:
            raise ValueError("a Library-scoped statement must filter by %(library_id)s")
        if params is not None and "library_id" in params:
            raise ValueError("library_id comes from the request context, not the caller")
        return self.tx.execute(query, {**(params or {}), "library_id": self.library_id})


def _enter_library(tx: Transaction, library_id: str) -> None:
    tx.execute("SELECT set_config('prks.library_id', %s, true)", (library_id,))


def _take_maintenance_lock(tx: Transaction, library_id: str) -> None:
    row = tx.execute(
        "SELECT pg_try_advisory_xact_lock_shared(" + LIBRARY_LOCK_KEY_SQL + ") AS locked",
        (library_id,),
    ).fetchone()
    if row is None or not row["locked"]:
        raise AccessRefused(Refusal.MAINTENANCE)


def _current_epoch(tx: Transaction, library_id: str, *, lock: bool) -> tuple[int, bool]:
    query: LiteralString = "SELECT epoch, maintenance IS NOT NULL AS in_maintenance FROM prks.libraries WHERE id = %s"
    row = tx.execute(query + " FOR SHARE" if lock else query, (library_id,)).fetchone()
    if row is None:  # authorize() has just seen it; only a trusted context can get here
        raise AccessRefused(Refusal.FORBIDDEN)
    return int(row["epoch"]), bool(row["in_maintenance"])


def library_command(
    db: Database,
    ctx: RequestContext,
    capability: Capability,
    fn: Callable[[LibraryScope], T],
    *,
    isolation: Isolation = Isolation.READ_COMMITTED,
) -> T:
    """Run the mutation ``fn`` for ``ctx`` after the lock, authorization and epoch checks.

    Raises :class:`AccessRefused` with nothing written when a check fails.
    ``fn`` may be called again when a SERIALIZABLE command is retried.
    """
    if capability not in LIBRARY_CAPABILITIES:
        raise ValueError("library_command takes a Library capability")
    library_id = ctx.library_id
    if library_id is None:
        raise AccessRefused(Refusal.FORBIDDEN)
    acted_on = ctx.epoch
    if acted_on is None:
        raise AccessRefused(Refusal.LIBRARY_EPOCH_REQUIRED)

    def run(tx: Transaction) -> T:
        _enter_library(tx, library_id)
        _take_maintenance_lock(tx, library_id)
        authorize(tx, ctx, capability)
        epoch, in_maintenance = _current_epoch(tx, library_id, lock=True)
        if in_maintenance:
            raise AccessRefused(Refusal.MAINTENANCE)
        if epoch != acted_on:
            raise AccessRefused(Refusal.LIBRARY_EPOCH_CHANGED)
        return fn(LibraryScope(tx, library_id, epoch))

    return db.command(run, isolation=isolation)


def library_query(
    db: Database,
    ctx: RequestContext,
    fn: Callable[[LibraryScope], T],
    *,
    capability: Capability = Capability.READ_LIBRARY,
) -> T:
    """Run the read ``fn`` for ``ctx`` in one snapshot, after authorization."""
    if capability not in LIBRARY_CAPABILITIES:
        raise ValueError("library_query takes a Library capability")
    library_id = ctx.library_id
    if library_id is None:
        raise AccessRefused(Refusal.FORBIDDEN)

    def run(tx: Transaction) -> T:
        _enter_library(tx, library_id)
        authorize(tx, ctx, capability)
        epoch, _in_maintenance = _current_epoch(tx, library_id, lock=False)
        return fn(LibraryScope(tx, library_id, epoch))

    return db.query(run)


def instance_command(db: Database, ctx: RequestContext, fn: Callable[[Transaction], T]) -> T:
    """Run an instance-wide administrative mutation (``ADMIN_INSTANCE``); no Library, no epoch."""

    def run(tx: Transaction) -> T:
        authorize(tx, ctx, Capability.ADMIN_INSTANCE)
        return fn(tx)

    return db.command(run)
