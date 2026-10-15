"""Capabilities and ``authorize()`` (``docs/postgresql-architecture.md`` §9.4; L-ID-1 #626).

``authorize(tx, ctx, capability)`` runs inside the transaction of the
operation it protects and decides from the database as it is now: the
session row, the user row and the ``library_memberships`` row for a browser,
the ``prks.jobs`` row for a worker. A role cached anywhere else, or anything a
client sent, never grants a capability.

In a read-write transaction the session, membership and user rows (a job's
row for a worker) are locked ``FOR SHARE`` until commit, so a sign-out, a
demotion, a removed membership or a disabled account either commits before
the check (and the check sees it) or waits until the protected write has
committed. A read-only snapshot cannot lock;
it sees the state of its snapshot.

Refusals are :class:`AccessRefused` with a stable :class:`Refusal`. Their
messages are fixed text that names no user, Library, session or device, so a
refusal never tells whether another Library exists or what it holds. Nothing
here logs.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType
from typing import LiteralString

from backend.db.database import Transaction
from backend.domain.identity.request_context import RequestContext, Source


class Capability(Enum):
    READ_LIBRARY = "read_library"
    EDIT_LIBRARY = "edit_library"
    ADMIN_LIBRARY = "admin_library"  # backup, restore, Library settings and members
    ADMIN_INSTANCE = "admin_instance"  # instance-wide administration


LIBRARY_CAPABILITIES = frozenset(
    {Capability.READ_LIBRARY, Capability.EDIT_LIBRARY, Capability.ADMIN_LIBRARY}
)

# What a Library mutation may require; READ_LIBRARY never authorizes a write.
MUTATION_CAPABILITIES = frozenset({Capability.EDIT_LIBRARY, Capability.ADMIN_LIBRARY})

# Role -> capabilities (§9.4). No role grants ADMIN_INSTANCE: in 0.8 only the
# host admin CLI has it.
ROLE_CAPABILITIES: Mapping[str, frozenset[Capability]] = MappingProxyType(
    {
        "owner": LIBRARY_CAPABILITIES,
        "editor": frozenset({Capability.READ_LIBRARY, Capability.EDIT_LIBRARY}),
        "reader": frozenset({Capability.READ_LIBRARY}),
    }
)

# A job acts on its own Library's content (derived rows, follow-up writes),
# never on its administration.
WORKER_CAPABILITIES = frozenset({Capability.READ_LIBRARY, Capability.EDIT_LIBRARY})

ADMIN_CAPABILITIES = LIBRARY_CAPABILITIES | {Capability.ADMIN_INSTANCE}


class Refusal(Enum):
    """A stable refusal: (code, the HTTP status PR 5/5 maps it to, fixed message)."""

    NOT_AUTHENTICATED = ("not_authenticated", 401, "Sign in again.")
    FORBIDDEN = ("forbidden", 403, "You do not have permission to do that.")
    LIBRARY_EPOCH_REQUIRED = (
        "LIBRARY_EPOCH_REQUIRED",
        400,
        "This change does not say which version of the Library it was made for.",
    )
    LIBRARY_EPOCH_CHANGED = (
        "LIBRARY_EPOCH_CHANGED",
        409,
        "The Library was restored since this change was prepared; reload it.",
    )
    MAINTENANCE = ("MAINTENANCE", 503, "The Library is under maintenance; try again shortly.")

    @property
    def code(self) -> str:
        return self.value[0]

    @property
    def http_status(self) -> int:
        return self.value[1]

    @property
    def message(self) -> str:
        return self.value[2]


class AccessRefused(Exception):
    """An operation was refused before it changed anything."""

    def __init__(self, refusal: Refusal) -> None:
        super().__init__(refusal.message)
        self.refusal = refusal


_BROWSER_SQL = """
SELECT m.role
  FROM prks.sessions s
  JOIN prks.users u ON u.id = s.user_id
  JOIN prks.library_memberships m ON m.library_id = s.library_id AND m.user_id = s.user_id
 WHERE s.id = %(session_id)s
   AND s.user_id = %(user_id)s
   AND s.library_id = %(library_id)s
   AND s.revoked_at IS NULL
   AND s.expires_at > now()
   AND u.disabled_at IS NULL
"""

_WORKER_SQL = """
SELECT 1
  FROM prks.jobs
 WHERE id = %(job_id)s
   AND lease_id = %(lease_id)s
   AND state = 'running'
   AND locked_until > now()
   AND library_id IS NOT DISTINCT FROM %(library_id)s
"""


def _locking(tx: Transaction, query: LiteralString, lock: LiteralString) -> LiteralString:
    return query if tx.read_only else query + lock


def _browser_capabilities(tx: Transaction, ctx: RequestContext) -> frozenset[Capability]:
    if ctx.session_id is None or ctx.user_id is None or ctx.library_id is None:
        raise AccessRefused(Refusal.NOT_AUTHENTICATED)
    row = tx.execute(
        _locking(tx, _BROWSER_SQL, " FOR SHARE OF s, m, u"),
        {"session_id": ctx.session_id, "user_id": ctx.user_id, "library_id": ctx.library_id},
    ).fetchone()
    if row is None:
        # Signed out, expired, disabled, deleted, or no longer a member.
        raise AccessRefused(Refusal.NOT_AUTHENTICATED)
    if ctx.device_id is not None:
        device = tx.execute(
            "SELECT 1 FROM prks.devices WHERE id = %s AND user_id = %s",
            (ctx.device_id, ctx.user_id),
        ).fetchone()
        if device is None:
            raise AccessRefused(Refusal.FORBIDDEN)
    return ROLE_CAPABILITIES.get(row["role"], frozenset())


def _worker_capabilities(tx: Transaction, ctx: RequestContext) -> frozenset[Capability]:
    if ctx.job_id is None or ctx.lease_id is None:
        raise AccessRefused(Refusal.NOT_AUTHENTICATED)
    row = tx.execute(
        _locking(tx, _WORKER_SQL, " FOR SHARE"),
        {"job_id": ctx.job_id, "lease_id": ctx.lease_id, "library_id": ctx.library_id},
    ).fetchone()
    if row is None:
        # Reclaimed, expired, cancelled (a restore cancels the Library's jobs) or finished.
        raise AccessRefused(Refusal.NOT_AUTHENTICATED)
    return WORKER_CAPABILITIES if ctx.library_id is not None else frozenset()


def _admin_capabilities(tx: Transaction, ctx: RequestContext) -> frozenset[Capability]:
    if ctx.library_id is None:
        return frozenset({Capability.ADMIN_INSTANCE})
    exists = tx.execute(
        _locking(tx, "SELECT 1 FROM prks.libraries WHERE id = %s", " FOR SHARE"), (ctx.library_id,)
    ).fetchone()
    return ADMIN_CAPABILITIES if exists is not None else frozenset({Capability.ADMIN_INSTANCE})


def authorize(tx: Transaction, ctx: RequestContext, capability: Capability) -> None:
    """Refuse unless ``ctx`` holds ``capability`` now; call it before any write or read.

    Raises :class:`AccessRefused`: ``NOT_AUTHENTICATED`` when the session, the
    user, the membership or the job lease is gone, and ``FORBIDDEN`` when the
    actor is who it says but lacks the capability (or claims a device that is
    not its own). Library capabilities apply to ``ctx.library_id`` only.
    """
    if ctx.source is Source.BROWSER:
        granted = _browser_capabilities(tx, ctx)
    elif ctx.source is Source.WORKER:
        granted = _worker_capabilities(tx, ctx)
    else:
        granted = _admin_capabilities(tx, ctx)
    if capability not in granted:
        raise AccessRefused(Refusal.FORBIDDEN)
