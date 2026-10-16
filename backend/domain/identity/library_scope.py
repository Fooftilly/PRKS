"""Library-scoped commands and queries with epoch fencing (``docs/postgresql-architecture.md`` §5.2, §9.4, §11.4; L-ID-1 #626).

Every Library-scoped mutation goes through :func:`library_command`, which
runs the caller's function in **one** transaction. It takes every lock it
needs first, in one fixed order, and only then decides:

1. ``SET LOCAL prks.library_id`` to ``ctx.library_id`` (``set_config(…,
   true)``), ready for row-level security later (§9.4; RLS is not enabled).
2. ``pg_try_advisory_xact_lock_shared`` on the Library maintenance key. A
   restore takes the same key exclusively (R3) before it commits its
   replacement and advances the epoch (R4); if it holds or waits for it
   now, the command will refuse ``MAINTENANCE`` instead of queueing behind
   it (step 5).
3. ``SELECT epoch, maintenance FROM prks.libraries … FOR SHARE`` (``FOR NO KEY
   UPDATE`` for a command that changes the Library row itself, below). It is
   skipped when step 2 did not get the lock, so a command never queues
   behind a restore that holds the row.
4. ``authorize(tx, ctx, capability)``, which locks the session, membership,
   user and claimed device rows ``FOR SHARE`` and then checks expiry against
   ``clock_timestamp()``, after every lock wait of steps 2–4. A command needs
   ``EDIT_LIBRARY`` or ``ADMIN_LIBRARY``; ``READ_LIBRARY`` never authorizes
   a write.
5. Only an authorized actor gets past step 4, so only an authorized actor
   learns the Library's state: a held restore lock or a committed
   maintenance flag refuses ``MAINTENANCE``, and an epoch other than
   ``ctx.epoch`` (older or newer) refuses ``LIBRARY_EPOCH_CHANGED``.
6. The caller's function, given a :class:`LibraryScope`.

**Refusal precedence.** An actor that is not signed in (or whose session,
membership, account or job lease is gone) gets ``NOT_AUTHENTICATED``; one
without the capability, or claiming a device that is not its own, gets
``FORBIDDEN``; only then can an authorized actor get ``MAINTENANCE`` or
``LIBRARY_EPOCH_CHANGED``. A missing or malformed epoch is refused
(``LIBRARY_EPOCH_REQUIRED``) before any of that, because it needs no
database: it says nothing about the Library.

Each refusal raises inside the transaction, so it rolls back with nothing
written. Once step 5 passes, the epoch cannot change before the command
commits, for two independent reasons: the shared advisory lock makes a
restore's exclusive lock (and so its epoch change) wait for this command, and
the Library row lock makes any ``UPDATE`` of the Library row wait for it as
well. A command prepared against an older epoch therefore never commits,
whether it comes from a browser, a durable operation or a job.

**Changing the Library row.** Content commands share the Library row
(``FOR SHARE``), so they run concurrently. A command that updates
``prks.libraries`` itself (settings, its name) must say so with
``updates_library=True``: it locks the row ``FOR NO KEY UPDATE`` at step 3
instead, so two such commands queue on the row rather than both holding a
share lock and deadlocking when each tries to upgrade it. It waits for
content commands in flight and holds new ones only for its own short
transaction. Without the flag, :class:`LibraryScope` refuses a statement
that updates or deletes ``prks.libraries``. Lock order everywhere: the
maintenance key, the Library row, then the identity rows.

**Scope lifetime.** A :class:`LibraryScope` (and the transaction handle and
cursors behind it) is valid only while the caller's function runs. It is
closed when the function returns or raises, on every attempt of a retried
SERIALIZABLE command, so a scope kept past that point cannot run SQL outside
the checks it was issued under.

A mutation without an acted-on epoch is refused (``LIBRARY_EPOCH_REQUIRED``)
before any database work. Identity and instance operations act on no Library
and take no epoch: bootstrap, login, sign-out and session renewal keep their
own transactions, and instance administration uses :func:`instance_command`.

:func:`library_query` runs a read in one ``REPEATABLE READ READ ONLY``
snapshot, authorized for ``READ_LIBRARY``, and gives the current epoch for
the response to carry. Reads are not fenced by epoch.

**Trust boundary.** These helpers enforce the authenticated actor, the
capability, the maintenance lock and the epoch. They do **not** enforce
that a statement's rows belong to the Library: without row-level security
(deferred past 0.8, §9.4) nothing in PostgreSQL does. The caller owns its
SQL predicates. :meth:`LibraryScope.execute` only refuses a statement that
does not mention ``%(library_id)s`` and binds that parameter from the
context, which catches a forgotten or caller-chosen Library, not a
predicate that fails to filter (``WHERE %(library_id)s = %(library_id)s``
passes and touches every Library). :meth:`LibraryScope.execute_unscoped`
bypasses even that check and is for trusted statements on tables without a
Library column. So every command and query that later lanes build on these
helpers must filter Library-scoped rows by ``library_id`` itself, have each
``execute_unscoped`` use reviewed, and come with a cross-Library negative
test (another Library's rows are neither returned nor changed).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, LiteralString, Optional, TypeVar

import psycopg
from psycopg.rows import DictRow

from backend.db.database import Database, Isolation, Transaction
from backend.domain.identity.authorization import (
    LIBRARY_CAPABILITIES,
    MUTATION_CAPABILITIES,
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

# The commands' shared try-lock on that key, written out in full (a test
# pins it to LIBRARY_LOCK_KEY_SQL).
_TRY_LOCK_SHARED_SQL: LiteralString = (
    "SELECT pg_try_advisory_xact_lock_shared(hashtextextended('prks.library:' || %s, 0)) AS locked"
)

_EPOCH_SQL: LiteralString = (
    "SELECT epoch, maintenance IS NOT NULL AS in_maintenance FROM prks.libraries WHERE id = %s"
)
_EPOCH_FOR_SHARE_SQL: LiteralString = (
    "SELECT epoch, maintenance IS NOT NULL AS in_maintenance FROM prks.libraries WHERE id = %s FOR SHARE"
)
_EPOCH_FOR_UPDATE_SQL: LiteralString = (
    "SELECT epoch, maintenance IS NOT NULL AS in_maintenance FROM prks.libraries WHERE id = %s "
    "FOR NO KEY UPDATE"
)

_SCOPE_PARAMETER = "%(library_id)s"

# A statement that changes the Library row (textual, like the scope check).
_LIBRARY_ROW_WRITE = re.compile(
    r"\b(?:UPDATE|DELETE\s+FROM)\s+(?:ONLY\s+)?(?:\"?prks\"?\.)?\"?libraries\b", re.IGNORECASE
)


class ScopeClosed(RuntimeError):
    """A :class:`LibraryScope` was used after the function it was given to returned."""


@dataclass
class _Lifetime:
    open: bool = True


@dataclass(frozen=True)
class LibraryScope:
    """The transaction of an authorized, epoch-checked Library operation, while its function runs."""

    _tx: Transaction
    library_id: str
    epoch: int  # the Library's current epoch (equal to ctx.epoch in a command)
    _updates_library: bool = False
    _lifetime: _Lifetime = field(default_factory=_Lifetime, repr=False, compare=False)

    def execute(
        self, query: LiteralString, params: Mapping[str, Any] | None = None
    ) -> psycopg.Cursor[DictRow]:
        """Run a statement that mentions ``%(library_id)s``, with that parameter bound to this Library.

        The check is textual: the caller must use the parameter as a real row
        filter on every Library-scoped table it touches (see the module docs).
        """
        self._check(query)
        if _SCOPE_PARAMETER not in query:
            raise ValueError("a Library-scoped statement must filter by %(library_id)s")
        if params is not None and "library_id" in params:
            raise ValueError("library_id comes from the request context, not the caller")
        return self._tx.execute(query, {**(params or {}), "library_id": self.library_id})

    def execute_unscoped(
        self, query: LiteralString, params: Mapping[str, Any] | tuple[Any, ...] | None = None
    ) -> psycopg.Cursor[DictRow]:
        """Trusted only: run a statement with no Library check at all.

        For tables without a Library column (users, devices, instance). Every
        use is reviewed, and it must never touch a Library-scoped table.
        """
        self._check(query)
        return self._tx.execute(query, params)

    def _check(self, query: str) -> None:
        if not self._lifetime.open:
            raise ScopeClosed("this Library scope has ended")
        if not self._updates_library and _LIBRARY_ROW_WRITE.search(query):
            raise ValueError("a command that changes prks.libraries must pass updates_library=True")

    def _close(self) -> None:
        self._lifetime.open = False


def _within(scope: LibraryScope, fn: Callable[[LibraryScope], T]) -> T:
    try:
        return fn(scope)
    finally:
        scope._close()


def _enter_library(tx: Transaction, library_id: str) -> None:
    tx.execute("SELECT set_config('prks.library_id', %s, true)", (library_id,))


def _take_maintenance_lock(tx: Transaction, library_id: str) -> bool:
    row = tx.execute(_TRY_LOCK_SHARED_SQL, (library_id,)).fetchone()
    return row is not None and bool(row["locked"])


def _current_epoch(tx: Transaction, library_id: str, *, lock: LiteralString) -> Optional[tuple[int, bool]]:
    row = tx.execute(lock, (library_id,)).fetchone()
    return None if row is None else (int(row["epoch"]), bool(row["in_maintenance"]))


def library_command(
    db: Database,
    ctx: RequestContext,
    capability: Capability,
    fn: Callable[[LibraryScope], T],
    *,
    isolation: Isolation = Isolation.READ_COMMITTED,
    updates_library: bool = False,
) -> T:
    """Run the mutation ``fn`` for ``ctx`` after the lock, authorization and epoch checks.

    Raises :class:`AccessRefused` with nothing written when a check fails.
    ``fn`` may be called again when a SERIALIZABLE command is retried, each
    time with a new scope. ``updates_library=True`` (an ``ADMIN_LIBRARY``
    command that changes the ``prks.libraries`` row) locks that row for
    update before authorizing; see the module docs.
    """
    if capability not in MUTATION_CAPABILITIES:
        raise ValueError("library_command takes EDIT_LIBRARY or ADMIN_LIBRARY")
    if updates_library and capability is not Capability.ADMIN_LIBRARY:
        raise ValueError("only an ADMIN_LIBRARY command changes the Library row")
    row_lock = _EPOCH_FOR_UPDATE_SQL if updates_library else _EPOCH_FOR_SHARE_SQL
    library_id = ctx.library_id
    if library_id is None:
        raise AccessRefused(Refusal.FORBIDDEN)
    acted_on = ctx.epoch
    if acted_on is None:
        raise AccessRefused(Refusal.LIBRARY_EPOCH_REQUIRED)

    def run(tx: Transaction) -> T:
        _enter_library(tx, library_id)
        locked = _take_maintenance_lock(tx, library_id)
        # Without the maintenance lock, a restore may hold the row: never wait for it.
        current = _current_epoch(tx, library_id, lock=row_lock) if locked else None
        authorize(tx, ctx, capability)
        if not locked:
            raise AccessRefused(Refusal.MAINTENANCE)
        if current is None:  # authorize() found the membership; only a trusted context gets here
            raise AccessRefused(Refusal.FORBIDDEN)
        epoch, in_maintenance = current
        if in_maintenance:
            raise AccessRefused(Refusal.MAINTENANCE)
        if epoch != acted_on:
            raise AccessRefused(Refusal.LIBRARY_EPOCH_CHANGED)
        return _within(LibraryScope(tx, library_id, epoch, updates_library), fn)

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
        current = _current_epoch(tx, library_id, lock=_EPOCH_SQL)
        if current is None:  # authorize() found the membership; only a trusted context gets here
            raise AccessRefused(Refusal.FORBIDDEN)
        return _within(LibraryScope(tx, library_id, current[0]), fn)

    return db.query(run)


def instance_command(db: Database, ctx: RequestContext, fn: Callable[[Transaction], T]) -> T:
    """Run an instance-wide administrative mutation (``ADMIN_INSTANCE``); no Library, no epoch."""

    def run(tx: Transaction) -> T:
        authorize(tx, ctx, Capability.ADMIN_INSTANCE)
        return fn(tx)

    return db.command(run)
