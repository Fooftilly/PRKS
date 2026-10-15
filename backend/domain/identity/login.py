"""Password login (``docs/postgresql-architecture.md`` §9.3; L-ID-1 #626).

:func:`login` is the whole domain side of ``POST /api/login`` (the route is
PR 5/5). In order:

1. A login name or password that no account could have is refused at once
   (bootstrap refuses the same ones: :mod:`login_names`,
   :func:`passwords.usable_password`). That decision never reads the
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
   creates the session; when there is no Library to sign in to it commits
   none of that (``NO_LIBRARY``, publicly ``invalid_credentials``). It re-reads the account under a row lock first: an
   account disabled since step 4 is refused, and when the hash changed (a
   concurrent login rehashed it, or the password was changed) the password
   is checked again against the new hash, outside the lock, before retrying.

Results are stable codes (:class:`LoginOutcome`); :attr:`LoginResult.public_reason`
maps the ones that must look alike to callers (an invalid stored hash looks
like a wrong password, and so does a correct password that cannot sign in). Nothing here logs; no result, exception or ``repr``
carries a password, hash or token.
"""

from __future__ import annotations

import functools
import secrets
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Optional, Union

from argon2 import PasswordHasher

from backend.db.database import Database, Transaction
from backend.db.errors import DatabaseError
from backend.domain.identity import login_limiter, login_names, passwords
from backend.domain.identity.login_limiter import ClientAddress, LoginLimits
from backend.domain.identity.secret_tokens import LoginDeviceToken
from backend.domain.identity.sessions import DEFAULT_IDLE, NewSession, create_session

# The prks_login_device cookie lives one year; its row is valid as long.
DEVICE_TOKEN_LIFETIME = timedelta(days=365)


class LoginOutcome(Enum):
    SUCCEEDED = "succeeded"
    INVALID_CREDENTIALS = "invalid_credentials"  # unknown, disabled, no password, wrong password
    STORED_HASH_INVALID = "stored_hash_invalid"  # the account's hash cannot be verified
    THROTTLED = "throttled"  # over a limiter key; no password check ran
    NO_LIBRARY = "no_library"  # password correct, no Library to sign in to; nothing committed
    # Password correct, but the device token that gated the attempt was
    # consumed by a concurrent login or revoked before the commit; nothing committed.
    DEVICE_REPLACED = "device_replaced"
    UNAVAILABLE = "unavailable"  # the database failed; nothing of the login was committed


# What a caller outside the process may learn. Everything that could tell an
# existing account from a missing one collapses into ``invalid_credentials``.
_PUBLIC = {
    LoginOutcome.SUCCEEDED: ("signed_in", 200),
    LoginOutcome.INVALID_CREDENTIALS: ("invalid_credentials", 401),
    LoginOutcome.STORED_HASH_INVALID: ("invalid_credentials", 401),
    LoginOutcome.THROTTLED: ("too_many_attempts", 429),
    # Not a distinct answer: it would confirm the password without a session.
    LoginOutcome.NO_LIBRARY: ("invalid_credentials", 401),
    LoginOutcome.DEVICE_REPLACED: ("invalid_credentials", 401),
    LoginOutcome.UNAVAILABLE: ("unavailable", 503),
}


@dataclass(frozen=True)
class LoginResult:
    """The outcome of one login attempt.

    ``session`` and ``device_token`` are set only on ``SUCCEEDED``; the HTTP
    layer sets the token as the new ``prks_login_device`` cookie, since the
    old one no longer works. ``retry_after_seconds`` and ``throttled_kind`` are set
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

# The dummy hash for passwords.PARAMETERS, made once from 32 random bytes
# that were then discarded, so the first failed login of a process does no
# extra Argon2 work for it (a test pins it to PARAMETERS). Any other
# parameter set is made on first use; ``warm_dummy_hash`` does that ahead
# of serving.
_DEFAULT_DUMMY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$JJoNNXGxf6UhdoUt9NBnSQ$WdVJw+ykKi/MISTNG5hJVk31on9msFQQmBZPhEWwupg"
)


def _hasher_key(hasher: PasswordHasher) -> tuple[object, ...]:
    return (
        hasher.type,
        hasher.time_cost,
        hasher.memory_cost,
        hasher.parallelism,
        hasher.hash_len,
        hasher.salt_len,
    )


_DUMMY_HASHES[_hasher_key(PasswordHasher.from_parameters(passwords.PARAMETERS))] = _DEFAULT_DUMMY_HASH


def warm_dummy_hash(hasher: Optional[PasswordHasher] = None) -> None:
    """Make the dummy hash for ``hasher`` now, before any login is served."""
    dummy_hash(hasher or passwords.default_hasher())


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
) -> Optional[LoginDeviceToken]:
    """Store a new device token for ``user_id``, consuming ``replacing``.

    ``None``, with nothing written, when ``replacing`` is no longer stored
    (a concurrent login consumed it, or it was revoked): a token is consumed
    at most once, so it never gets two successors.
    """
    if replacing is not None:
        consumed = tx.execute(
            "DELETE FROM prks.login_device_tokens WHERE token_sha256 = %s AND user_id = %s",
            (replacing.digest, user_id),
        ).rowcount
        if consumed == 0:
            return None
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
    libraries: tuple[str, ...]  # its memberships, read with the account

    def library(self, library_id: Optional[str]) -> Optional[str]:
        """The Library to sign in to: ``library_id`` if a member, else the only membership."""
        if library_id is not None:
            return library_id if library_id in self.libraries else None
        return self.libraries[0] if len(self.libraries) == 1 else None


# The memberships come in the same query as the account, so a correct
# password for an account with no Library to sign in to is refused after
# the same work as a wrong one: no extra round trip tells them apart.
_ACCOUNT_SQL = """
SELECT u.id, u.password_hash, u.disabled_at IS NOT NULL AS disabled,
       coalesce(array_agg(m.library_id ORDER BY m.library_id)
                FILTER (WHERE m.library_id IS NOT NULL), '{}') AS libraries
  FROM prks.users u
  LEFT JOIN prks.library_memberships m ON m.user_id = u.id
 WHERE u.login_key = %s
 GROUP BY u.id
"""


def _account(db: Database, login_key: str) -> Optional[_Account]:
    row = db.query(lambda tx: tx.execute(_ACCOUNT_SQL, (login_key,)).fetchone())
    if row is None:
        return None
    return _Account(row["id"], row["password_hash"], row["disabled"], tuple(row["libraries"]))


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


@dataclass(frozen=True)
class _HashChanged:
    """The account's hash is no longer the one the password was checked against."""

    current: str = field(repr=False)


# A hash that keeps changing under a login (each change needs one more
# Argon2 check) ends it as UNAVAILABLE after this many tries.
_SUCCESS_TRIES = 3


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
) -> Union[LoginResult, _HashChanged]:
    current = tx.execute(
        "SELECT password_hash FROM prks.users WHERE id = %s AND disabled_at IS NULL FOR UPDATE",
        (account.user_id,),
    ).fetchone()
    if current is None or current["password_hash"] is None:
        # Disabled, deleted or without a password since the check.
        return LoginResult(LoginOutcome.INVALID_CREDENTIALS)
    if current["password_hash"] != verified_hash:
        # A concurrent login rehashed it, or the password was changed: the
        # caller checks the password against the new hash, outside this lock.
        return _HashChanged(current["password_hash"])
    library = _library_of(tx, account.user_id, library_id)
    if library is None:
        # No session, so nothing a correct password earns: the count stays,
        # no device token is issued, and callers see invalid_credentials.
        return LoginResult(LoginOutcome.NO_LIBRARY)
    # The rotation goes first: when the presented token is already gone, the
    # attempt was gated by a device that is no longer valid, so the login
    # ends before it writes anything (no reset, no session, no new token).
    token = issue_device_token(tx, account.user_id, replacing=device)
    if token is None:
        return LoginResult(LoginOutcome.DEVICE_REPLACED)
    login_limiter.reset(tx, gating_key)
    if new_hash is not None:
        tx.execute(
            "UPDATE prks.users SET password_hash = %s WHERE id = %s",
            (new_hash, account.user_id),
        )
    session = create_session(tx, user_id=account.user_id, library_id=library, idle=idle)
    return LoginResult(LoginOutcome.SUCCEEDED, session=session, device_token=token)


@dataclass(frozen=True)
class _Gate:
    """A counted attempt: the presented device (if valid) and the key that gated it."""

    device: Optional[LoginDeviceToken]
    key: str


def _gate(
    db: Database, login_key: str, client: ClientAddress, device_cookie: object, limits: LoginLimits
) -> Union[_Gate, LoginResult]:
    """Count the attempt (step 2 and 3); a ``LoginResult`` when it is throttled."""
    device = _known_device(db, device_cookie, login_key)
    if device is not None:
        gate = _Gate(device, login_limiter.device_key(device.digest))
        keys = [gate.key]
    else:
        gate = _Gate(None, login_limiter.account_key(login_key))
        keys = [gate.key, client.key]
    throttled = login_limiter.count_attempt(db, keys, limits)
    if throttled is None:
        return gate
    return LoginResult(
        LoginOutcome.THROTTLED,
        retry_after_seconds=throttled.retry_after_seconds,
        throttled_kind=throttled.kind,
    )


def _refusal(stored: str, password: str, hasher: PasswordHasher) -> Optional[LoginResult]:
    """One Argon2id check of ``password``; ``None`` when it matches ``stored``."""
    try:
        if passwords.verify_password(stored, password, hasher=hasher):
            return None
    except passwords.PasswordHashInvalid:
        _burn(password, hasher)
        return LoginResult(LoginOutcome.STORED_HASH_INVALID)
    return LoginResult(LoginOutcome.INVALID_CREDENTIALS)


def _commit(
    db: Database,
    account: _Account,
    stored: str,
    password: str,
    hasher: PasswordHasher,
    gate: _Gate,
    library_id: Optional[str],
    idle: timedelta,
) -> LoginResult:
    """Step 5 for a password just verified against ``stored``; retried when the hash changes."""
    # Argon2 work (checks and rehashes) stays outside the transaction so the
    # account row is never locked for the length of an Argon2 call.
    for _ in range(_SUCCESS_TRIES):
        new_hash = (
            passwords.hash_password(password, hasher=hasher)
            if passwords.needs_rehash(stored, hasher=hasher)
            else None
        )
        try:
            outcome = db.command(
                functools.partial(
                    _succeed,
                    account=account,
                    verified_hash=stored,
                    gating_key=gate.key,
                    device=gate.device,
                    new_hash=new_hash,
                    library_id=library_id,
                    idle=idle,
                )
            )
        except DatabaseError:
            return LoginResult(LoginOutcome.UNAVAILABLE)
        if isinstance(outcome, LoginResult):
            return outcome
        stored = outcome.current
        refused = _refusal(stored, password, hasher)
        if refused is not None:
            return refused
    return LoginResult(LoginOutcome.UNAVAILABLE)


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
    hasher = hasher or passwords.default_hasher()
    login_key = login_names.login_key(login_name)
    if login_key is None or not passwords.usable_password(password):
        return LoginResult(LoginOutcome.INVALID_CREDENTIALS)
    try:
        gate = _gate(db, login_key, client, device_cookie, limits or LoginLimits())
        if isinstance(gate, LoginResult):
            return gate
        account = _account(db, login_key)
    except DatabaseError:
        return LoginResult(LoginOutcome.UNAVAILABLE)

    if account is None or account.disabled or account.password_hash is None:
        _burn(password, hasher)
        return LoginResult(LoginOutcome.INVALID_CREDENTIALS)
    stored = account.password_hash
    refused = _refusal(stored, password, hasher)
    if refused is not None:
        return refused
    if account.library(library_id) is None:
        return LoginResult(LoginOutcome.NO_LIBRARY)
    return _commit(db, account, stored, password, hasher, gate, library_id, idle)
