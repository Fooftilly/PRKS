"""Password login (``docs/postgresql-architecture.md`` §9.3; L-ID-1 #626).

:func:`login` is the whole domain side of ``POST /api/login`` (the route is
PR 5/5). In order:

1. A login name or password that no account could have (not text, empty,
   too long, NUL) is refused at once. That decision never reads the
   database, so it says nothing about which accounts exist.
2. A ``prks_login_device`` token is looked up. It counts as a known device
   only when it is younger than :data:`DEVICE_TOKEN_LIFETIME` and belongs to
   the account the login names; a stale, unknown, malformed or other
   account's token is ignored and the login is counted as any other.
3. The attempt is counted (:func:`login_limiter.count_attempt`): under
   ``dev:`` for a known device, otherwise under ``acct:`` and ``ip:``. Each
   upsert commits on its own, so an attempt over a limit is refused
   (``THROTTLED``) before any Argon2 work.
4. Exactly one Argon2id verification runs. An unknown login, a disabled
   account, an account without a password and a stored hash that is not a
   valid Argon2id hash all verify the password against a dummy hash made
   with the same parameters, so every refused login costs the same.
5. A correct password commits one transaction that resets the key that
   gated the attempt, replaces the presented device token (if any) with a
   new one, rewrites the hash when its parameters are out of date, and
   creates the session. It re-reads the account under a row lock first: an
   account disabled or given a new password since step 4 is refused.

Results are stable codes (:class:`LoginOutcome`); :attr:`LoginResult.public_reason`
maps the ones that must look alike to callers (an invalid stored hash looks
like a wrong password). Nothing here logs; no result, exception or ``repr``
carries a password, hash or token.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Optional, TypeGuard

from argon2 import PasswordHasher

from backend.db.database import Database, Transaction
from backend.db.errors import DatabaseError
from backend.domain.identity import login_limiter, passwords
from backend.domain.identity.login_limiter import ClientAddress, LoginLimits
from backend.domain.identity.secret_tokens import LoginDeviceToken
from backend.domain.identity.sessions import DEFAULT_IDLE, NewSession, create_session
from backend.domain.text_identity import UnsupportedTextError, identity_key

# The prks_login_device cookie lives one year; its row is valid as long.
DEVICE_TOKEN_LIFETIME = timedelta(days=365)

# Longer than any login name PRKS accepts; bounds the work of a bad request.
MAX_LOGIN_NAME_CHARS = 256


class LoginOutcome(Enum):
    SUCCEEDED = "succeeded"
    INVALID_CREDENTIALS = "invalid_credentials"  # unknown, disabled, no password, wrong password
    STORED_HASH_INVALID = "stored_hash_invalid"  # the account's hash cannot be verified
    THROTTLED = "throttled"  # over a limiter key; no password check ran
    NO_LIBRARY = "no_library"  # password correct, but no Library membership to sign in to
    UNAVAILABLE = "unavailable"  # the database failed; nothing of the login was committed


# What a caller outside the process may learn. Everything that could tell an
# existing account from a missing one collapses into ``invalid_credentials``.
_PUBLIC = {
    LoginOutcome.SUCCEEDED: ("signed_in", 200),
    LoginOutcome.INVALID_CREDENTIALS: ("invalid_credentials", 401),
    LoginOutcome.STORED_HASH_INVALID: ("invalid_credentials", 401),
    LoginOutcome.THROTTLED: ("too_many_attempts", 429),
    LoginOutcome.NO_LIBRARY: ("no_library", 403),
    LoginOutcome.UNAVAILABLE: ("unavailable", 503),
}


@dataclass(frozen=True)
class LoginResult:
    """The outcome of one login attempt.

    ``session`` is set only on ``SUCCEEDED``. ``device_token`` is set whenever
    the password was correct (``SUCCEEDED`` and ``NO_LIBRARY``): the HTTP
    layer sets it as the new ``prks_login_device`` cookie, since the old one
    no longer works. ``retry_after_seconds`` and ``throttled_kind`` are set
    only on ``THROTTLED``.
    """

    outcome: LoginOutcome
    session: Optional[NewSession] = None
    device_token: Optional[LoginDeviceToken] = None
    retry_after_seconds: Optional[int] = None
    throttled_kind: Optional[str] = None

    @property
    def public_reason(self) -> str:
        return _PUBLIC[self.outcome][0]

    @property
    def http_status(self) -> int:
        return _PUBLIC[self.outcome][1]


# --- the dummy hash --------------------------------------------------------------------

_DUMMY_LOCK = threading.Lock()
_DUMMY_HASHES: dict[tuple[object, ...], str] = {}


def _hasher_key(hasher: PasswordHasher) -> tuple[object, ...]:
    return (
        hasher.type,
        hasher.time_cost,
        hasher.memory_cost,
        hasher.parallelism,
        hasher.hash_len,
        hasher.salt_len,
    )


def dummy_hash(hasher: PasswordHasher) -> str:
    """A hash of a random secret with ``hasher``'s parameters, made once per parameter set.

    Verifying a password against it costs what verifying against a real
    account's hash costs, and it matches no password.
    """
    key = _hasher_key(hasher)
    with _DUMMY_LOCK:
        stored = _DUMMY_HASHES.get(key)
        if stored is None:
            stored = hasher.hash(secrets.token_bytes(32))
            _DUMMY_HASHES[key] = stored
        return stored


def _burn(password: str, hasher: PasswordHasher) -> None:
    passwords.verify_password(dummy_hash(hasher), password, hasher=hasher)


# --- input -----------------------------------------------------------------------------


def _login_key(login_name: object) -> Optional[str]:
    if not isinstance(login_name, str) or not 0 < len(login_name) <= MAX_LOGIN_NAME_CHARS:
        return None
    try:
        key = identity_key(login_name)
    except UnsupportedTextError:
        return None
    return key or None


def _usable_password(password: object) -> TypeGuard[str]:
    if not isinstance(password, str) or password == "" or "\x00" in password:
        return False
    try:
        encoded = password.encode("utf-8")
    except UnicodeEncodeError:  # a lone surrogate is no password anyone can type
        return False
    return len(encoded) <= passwords.MAX_PASSWORD_BYTES


# --- device tokens ---------------------------------------------------------------------

_TOUCH_DEVICE_SQL = """
UPDATE prks.login_device_tokens d
   SET last_used_at = now()
  FROM prks.users u
 WHERE d.token_sha256 = %(digest)s
   AND u.id = d.user_id
   AND u.login_key = %(login_key)s
   AND d.created_at > now() - %(lifetime)s
RETURNING d.user_id
"""


def _known_device(
    db: Database, cookie: object, login_key: str
) -> Optional[LoginDeviceToken]:
    """The presented device token when it is valid for the account named; else ``None``."""
    token = LoginDeviceToken.parse(cookie)
    if token is None:
        return None
    row = db.command(
        lambda tx: tx.execute(
            _TOUCH_DEVICE_SQL,
            {"digest": token.digest, "login_key": login_key, "lifetime": DEVICE_TOKEN_LIFETIME},
        ).fetchone()
    )
    return token if row is not None else None


def issue_device_token(
    tx: Transaction, user_id: str, *, replacing: Optional[LoginDeviceToken] = None
) -> LoginDeviceToken:
    """Store a new device token for ``user_id``, deleting ``replacing`` if it is theirs."""
    if replacing is not None:
        tx.execute(
            "DELETE FROM prks.login_device_tokens WHERE token_sha256 = %s AND user_id = %s",
            (replacing.digest, user_id),
        )
    token = LoginDeviceToken.new()
    tx.execute(
        "INSERT INTO prks.login_device_tokens (token_sha256, user_id) VALUES (%s, %s)",
        (token.digest, user_id),
    )
    return token


def revoke_device_tokens(tx: Transaction, user_id: str) -> int:
    """Forget every login device of ``user_id`` (a password reset, a disabled account)."""
    return tx.execute(
        "DELETE FROM prks.login_device_tokens WHERE user_id = %s", (user_id,)
    ).rowcount


def prune_device_tokens(tx: Transaction) -> int:
    """Delete device tokens past their lifetime (the ``sessions.prune`` job)."""
    return tx.execute(
        "DELETE FROM prks.login_device_tokens WHERE created_at <= now() - %s",
        (DEVICE_TOKEN_LIFETIME,),
    ).rowcount


# --- login -----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Account:
    user_id: str
    password_hash: Optional[str]
    disabled: bool


def _account(db: Database, login_key: str) -> Optional[_Account]:
    row = db.query(
        lambda tx: tx.execute(
            "SELECT id, password_hash, disabled_at IS NOT NULL AS disabled "
            "FROM prks.users WHERE login_key = %s",
            (login_key,),
        ).fetchone()
    )
    if row is None:
        return None
    return _Account(row["id"], row["password_hash"], row["disabled"])


def _library_of(tx: Transaction, user_id: str, library_id: Optional[str]) -> Optional[str]:
    """The Library to sign in to: ``library_id`` if a member, else the only membership."""
    if library_id is not None:
        row = tx.execute(
            "SELECT library_id FROM prks.library_memberships WHERE user_id = %s AND library_id = %s",
            (user_id, library_id),
        ).fetchone()
        return None if row is None else row["library_id"]
    rows = tx.execute(
        "SELECT library_id FROM prks.library_memberships WHERE user_id = %s LIMIT 2",
        (user_id,),
    ).fetchall()
    return rows[0]["library_id"] if len(rows) == 1 else None


def _succeed(
    tx: Transaction,
    *,
    account: _Account,
    verified_hash: str,
    gating_key: str,
    device: Optional[LoginDeviceToken],
    new_hash: Optional[str],
    library_id: Optional[str],
    idle: timedelta,
) -> LoginResult:
    current = tx.execute(
        "SELECT 1 FROM prks.users WHERE id = %s AND disabled_at IS NULL AND password_hash = %s "
        "FOR UPDATE",
        (account.user_id, verified_hash),
    ).fetchone()
    if current is None:
        # Disabled, deleted or given a new password after the check: the
        # password just verified is no longer the account's.
        return LoginResult(LoginOutcome.INVALID_CREDENTIALS)
    login_limiter.reset(tx, gating_key)
    token = issue_device_token(tx, account.user_id, replacing=device)
    if new_hash is not None:
        tx.execute(
            "UPDATE prks.users SET password_hash = %s WHERE id = %s",
            (new_hash, account.user_id),
        )
    library = _library_of(tx, account.user_id, library_id)
    if library is None:
        return LoginResult(LoginOutcome.NO_LIBRARY, device_token=token)
    session = create_session(tx, user_id=account.user_id, library_id=library, idle=idle)
    return LoginResult(LoginOutcome.SUCCEEDED, session=session, device_token=token)


def login(
    db: Database,
    login_name: object,
    password: object,
    *,
    client: ClientAddress,
    device_cookie: object = None,
    library_id: Optional[str] = None,
    limits: Optional[LoginLimits] = None,
    idle: timedelta = DEFAULT_IDLE,
    hasher: Optional[PasswordHasher] = None,
) -> LoginResult:
    """Check one password login and, when it is right, create the session.

    ``client`` is the resolved address (:meth:`ClientAddress.resolve`);
    ``device_cookie`` the raw ``prks_login_device`` cookie value, if any;
    ``library_id`` the Library asked for (default: the account's only one).
    """
    limits = limits or LoginLimits()
    hasher = hasher or passwords.default_hasher()
    login_key = _login_key(login_name)
    if login_key is None or not _usable_password(password):
        return LoginResult(LoginOutcome.INVALID_CREDENTIALS)

    try:
        device = _known_device(db, device_cookie, login_key)
        if device is not None:
            gating_key = login_limiter.device_key(device.digest)
            keys = [gating_key]
        else:
            gating_key = login_limiter.account_key(login_key)
            keys = [gating_key, client.key]
        throttled = login_limiter.count_attempt(db, keys, limits)
        if throttled is not None:
            return LoginResult(
                LoginOutcome.THROTTLED,
                retry_after_seconds=throttled.retry_after_seconds,
                throttled_kind=throttled.kind,
            )
        account = _account(db, login_key)
    except DatabaseError:
        return LoginResult(LoginOutcome.UNAVAILABLE)

    if account is None or account.disabled or account.password_hash is None:
        _burn(password, hasher)
        return LoginResult(LoginOutcome.INVALID_CREDENTIALS)
    stored = account.password_hash
    try:
        verified = passwords.verify_password(stored, password, hasher=hasher)
    except passwords.PasswordHashInvalid:
        _burn(password, hasher)
        return LoginResult(LoginOutcome.STORED_HASH_INVALID)
    if not verified:
        return LoginResult(LoginOutcome.INVALID_CREDENTIALS)

    # Hash outside the transaction so the account row is never locked for
    # the length of an Argon2 call.
    new_hash = (
        passwords.hash_password(password, hasher=hasher)
        if passwords.needs_rehash(stored, hasher=hasher)
        else None
    )
    try:
        return db.command(
            lambda tx: _succeed(
                tx,
                account=account,
                verified_hash=stored,
                gating_key=gating_key,
                device=device,
                new_hash=new_hash,
                library_id=library_id,
                idle=idle,
            )
        )
    except DatabaseError:
        return LoginResult(LoginOutcome.UNAVAILABLE)
