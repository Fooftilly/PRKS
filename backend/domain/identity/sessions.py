"""Browser sessions (``docs/postgresql-architecture.md`` §6.2, §9.3; L-ID-1 #626).

A session is a row of ``prks.sessions`` for one user in one Library. The
``prks_session`` cookie holds a 256-bit token; the row holds only its
SHA-256. A session expires after an idle period (30 days by default,
``PRKS_SESSION_IDLE_DAYS``): every use that is at least
:data:`RENEW_AFTER` after the last renewal moves ``expires_at`` to now plus
the idle period. Sign-out revokes the row; an expired or revoked session,
one whose user is disabled, and one whose membership is gone (the foreign
key deletes it) are refused.

What a session may do is decided per operation by
:mod:`~backend.domain.identity.authorization` (``authorize()`` reads the
membership row inside the operation's transaction) and, for Library
mutations, the epoch check of :mod:`~backend.domain.identity.library_scope`;
this module only answers who holds the cookie.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from backend.db.database import Database, Transaction
from backend.domain.identity.secret_tokens import SessionToken

IDLE_ENV = "PRKS_SESSION_IDLE_DAYS"
DEFAULT_IDLE = timedelta(days=30)

# A session used again within this much of its last renewal is not written:
# sliding expiry costs at most one write per session per minute.
RENEW_AFTER = timedelta(seconds=60)


class SessionConfigError(ValueError):
    """``PRKS_SESSION_IDLE_DAYS`` is not usable; the process does not start."""


def idle_from_env(environ: Mapping[str, str]) -> timedelta:
    text = (environ.get(IDLE_ENV) or "").strip()
    if not text:
        return DEFAULT_IDLE
    try:
        days = int(text, 10)
    except ValueError:
        raise SessionConfigError(f"{IDLE_ENV} must be a whole number of days.") from None
    if not 1 <= days <= 365:
        raise SessionConfigError(f"{IDLE_ENV} must be from 1 to 365.")
    return timedelta(days=days)


@dataclass(frozen=True)
class NewSession:
    """A session just created; ``token`` goes into the cookie once."""

    session_id: uuid.UUID
    token: SessionToken
    user_id: str
    library_id: str
    expires_at: datetime


@dataclass(frozen=True)
class ValidSession:
    session_id: uuid.UUID
    user_id: str
    library_id: str
    expires_at: datetime


def create_session(
    tx: Transaction, *, user_id: str, library_id: str, idle: timedelta = DEFAULT_IDLE
) -> NewSession:
    """Insert a session for an existing membership, inside the caller's transaction."""
    token = SessionToken.new()
    row = tx.execute(
        "INSERT INTO prks.sessions (token_sha256, user_id, library_id, expires_at) "
        "VALUES (%s, %s, %s, now() + %s) RETURNING id, expires_at",
        (token.digest, user_id, library_id, idle),
    ).fetchone()
    if row is None:  # pragma: no cover - INSERT … RETURNING always returns the row
        raise RuntimeError("session insert returned no row")
    return NewSession(row["id"], token, user_id, library_id, row["expires_at"])


# Expiry is compared with ``clock_timestamp()`` (the time of the check), not
# the transaction-start ``now()``: see ``authorization`` for why it matters
# after a lock wait.
_LOOKUP_SQL = """
SELECT s.id, s.user_id, s.library_id, s.expires_at,
       s.last_used_at <= clock_timestamp() - %(renew_after)s AS renew
  FROM prks.sessions s
  JOIN prks.users u ON u.id = s.user_id
 WHERE s.token_sha256 = %(digest)s
   AND s.revoked_at IS NULL
   AND s.expires_at > clock_timestamp()
   AND u.disabled_at IS NULL
"""

# Renewal locks the row first and only then re-checks it, so a session that
# expired or was revoked while the renewal waited for the lock is never
# extended (a row that was only locked, not changed, is not re-read by an
# UPDATE that waited for it).
_LOCK_FOR_RENEWAL_SQL = """
SELECT expires_at
  FROM prks.sessions
 WHERE id = %(id)s AND revoked_at IS NULL
   FOR NO KEY UPDATE
"""

_RENEW_SQL = """
UPDATE prks.sessions
   SET last_used_at = clock_timestamp(), expires_at = clock_timestamp() + %(idle)s
 WHERE id = %(id)s
RETURNING expires_at
"""


def resolve_session(
    db: Database,
    cookie: object,
    *,
    idle: timedelta = DEFAULT_IDLE,
    library_id: Optional[str] = None,
) -> Optional[ValidSession]:
    """The live session a cookie value names, renewed if due; ``None`` otherwise.

    A malformed, unknown, expired or revoked token, and the token of a
    disabled user, all return ``None``: the caller answers 401 the same way.
    With ``library_id``, a session for any other Library is ``None`` too, and
    is not renewed.
    """
    token = SessionToken.parse(cookie)
    if token is None:
        return None

    def run(tx: Transaction) -> Optional[ValidSession]:
        row = tx.execute(
            _LOOKUP_SQL, {"digest": token.digest, "renew_after": RENEW_AFTER}
        ).fetchone()
        if row is None or (library_id is not None and row["library_id"] != library_id):
            return None
        expires_at = row["expires_at"]
        if row["renew"]:
            locked = tx.execute(_LOCK_FOR_RENEWAL_SQL, {"id": row["id"]}).fetchone()
            # A separate statement, so the clock is read after the lock wait.
            live = locked is not None and tx.execute(
                "SELECT %s > clock_timestamp() AS live", (locked["expires_at"],)
            ).fetchone()
            if not live or not live["live"]:  # revoked or expired since the lookup
                return None
            renewed = tx.execute(_RENEW_SQL, {"idle": idle, "id": row["id"]}).fetchone()
            if renewed is None:  # pragma: no cover - the row is locked by this transaction
                return None
            expires_at = renewed["expires_at"]
        return ValidSession(row["id"], row["user_id"], row["library_id"], expires_at)

    return db.command(run)


def revoke_session(db: Database, cookie: object) -> bool:
    """Sign out: revoke the session a cookie value names. True if a live one was revoked."""
    token = SessionToken.parse(cookie)
    if token is None:
        return False
    row = db.command(
        lambda tx: tx.execute(
            "UPDATE prks.sessions SET revoked_at = now() "
            "WHERE token_sha256 = %s AND revoked_at IS NULL RETURNING id",
            (token.digest,),
        ).fetchone()
    )
    return row is not None


def revoke_user_sessions(tx: Transaction, user_id: str) -> int:
    """Revoke every live session of ``user_id`` (a password change, a disabled account)."""
    cursor = tx.execute(
        "UPDATE prks.sessions SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL",
        (user_id,),
    )
    return cursor.rowcount


def prune_sessions(tx: Transaction) -> int:
    """Delete expired and revoked sessions (the ``sessions.prune`` job)."""
    cursor = tx.execute(
        "DELETE FROM prks.sessions WHERE expires_at <= now() OR revoked_at IS NOT NULL"
    )
    return cursor.rowcount
