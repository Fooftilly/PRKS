"""Identity for the web process: settings, start check and loopback owner (§9.3, §11.1; L-ID-1 #626).

Authentication is off unless ``PRKS_AUTH`` asks for it, so the SQLite
application keeps running exactly as before until the PostgreSQL cutover.
``PRKS_AUTH`` takes two values:

- ``session``: password sign-in and ``prks_session`` cookies
  (:mod:`backend.api.session`).
- ``loopback-owner``: every request acts as the bootstrap owner, through a
  real server-side session of the owner's Library, so authorization and the
  Library epoch fence work exactly as for a signed-in browser. Only a testing
  instance (``--testing``) bound to a loopback address may use it; anything
  else refuses to start. It is never a default.

Either value needs ``PRKS_DATABASE_URL`` (or ``PRKS_DATABASE_URL_FILE``).

:func:`start_web_identity` runs before the process opens its storage root.
It runs the start check and start-generation bump of
:mod:`~backend.domain.identity.root_pairing` (§11.1) under the root's
**shared** lease, then releases that lease, because the SQLite runtime then
takes the root's exclusive single-process lease for its own files. A
refused pairing, a database whose setup is not ``ready`` or a failed
reconcile stops the process before it serves anything; loopback-owner mode
runs the same check.
"""

from __future__ import annotations

import ipaddress
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Optional

from argon2 import PasswordHasher

from backend.db.database import Database, Transaction
from backend.db.errors import DatabaseError
from backend.domain.identity import root_pairing, sessions
from backend.domain.identity.errors import RootPairingRefused
from backend.domain.identity.login import warm_dummy_hash
from backend.domain.identity.login_limiter import LoginLimits, TrustedProxies
from backend.domain.identity.sessions import ValidSession
from backend.storage.errors import StorageRootError
from backend.storage.root_binding import acquire_shared_root_lease

AUTH_ENV = "PRKS_AUTH"
PUBLIC_ORIGIN_ENV = "PRKS_PUBLIC_ORIGIN"

# (scheme, hostname, port) as ``backend.server.parse_request_origin`` returns it.
Origin = tuple[str, str, Optional[int]]
OriginParser = Callable[[str], Optional[Origin]]


class AuthMode(Enum):
    SESSION = "session"
    LOOPBACK_OWNER = "loopback-owner"


class WebAuthConfigError(ValueError):
    """The authentication settings are unusable; the process does not start."""


@dataclass(frozen=True)
class WebAuthSettings:
    mode: AuthMode
    limits: LoginLimits
    idle: timedelta
    trusted_proxies: TrustedProxies
    # The origin browsers use when it is not the server's own address (an
    # HTTPS reverse proxy). ``None``: only the request's own http origin.
    public_origin: Optional[Origin] = None

    @property
    def secure_cookies(self) -> bool:
        return self.public_origin is not None and self.public_origin[0] == "https"


def _is_loopback_literal(host: str) -> bool:
    candidate = host.strip()
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def _public_origin(environ: Mapping[str, str], parse_origin: OriginParser) -> Optional[Origin]:
    text = (environ.get(PUBLIC_ORIGIN_ENV) or "").strip()
    if not text:
        return None
    origin = parse_origin(text.rstrip("/"))
    if origin is None:
        raise WebAuthConfigError(
            f"{PUBLIC_ORIGIN_ENV} must be an origin such as https://prks.example.org "
            "(scheme, host and optional port; nothing else)."
        )
    return origin


def settings_from_env(
    environ: Mapping[str, str], *, testing: bool, host: str, parse_origin: OriginParser
) -> Optional[WebAuthSettings]:
    """The authentication settings, or ``None`` when ``PRKS_AUTH`` is not set.

    Raises :class:`WebAuthConfigError` (or the limiter's and sessions' own
    config errors) for anything unusable, never falling back to a default.
    """
    raw = environ.get(AUTH_ENV)
    if raw is None or not raw.strip():
        return None
    try:
        mode = AuthMode(raw.strip())
    except ValueError:
        raise WebAuthConfigError(f"{AUTH_ENV} must be 'session' or 'loopback-owner'.") from None
    if mode is AuthMode.LOOPBACK_OWNER:
        if not testing:
            raise WebAuthConfigError(
                f"{AUTH_ENV}=loopback-owner is only for a testing instance (--testing)."
            )
        if not _is_loopback_literal(host):
            raise WebAuthConfigError(
                f"{AUTH_ENV}=loopback-owner needs the server bound to a loopback address "
                "(--host 127.0.0.1 or --host ::1)."
            )
    return WebAuthSettings(
        mode=mode,
        limits=LoginLimits.from_env(environ),
        idle=sessions.idle_from_env(environ),
        trusted_proxies=TrustedProxies.from_env(environ),
        public_origin=_public_origin(environ, parse_origin),
    )


# --- the running process ------------------------------------------------------------


_OWNER_SQL = """
SELECT m.user_id, m.library_id
  FROM prks.library_memberships m
  JOIN prks.users u ON u.id = m.user_id
 WHERE m.role = 'owner' AND u.disabled_at IS NULL
 ORDER BY m.library_id, m.user_id
 LIMIT 2
"""


def _bootstrap_owner(tx: Transaction) -> Optional[tuple[str, str]]:
    """The one owner membership a 0.8 instance has; ``None`` unless there is exactly one."""
    rows = tx.execute(_OWNER_SQL).fetchall()
    if len(rows) != 1:
        return None
    return rows[0]["user_id"], rows[0]["library_id"]


@dataclass
class WebAuth:
    """What the HTTP layer needs once the start check has passed."""

    db: Database
    settings: WebAuthSettings
    instance_id: str
    # The Argon2id hasher logins use; ``None`` is ``passwords.default_hasher()``.
    hasher: Optional[PasswordHasher] = field(default=None, repr=False)
    _owner_cookie: Optional[str] = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def mode(self) -> AuthMode:
        return self.settings.mode

    def loopback_session(self) -> Optional[ValidSession]:
        """The bootstrap owner's live session (loopback-owner mode only).

        The session is an ordinary ``prks.sessions`` row created here, whose
        token never leaves the process. A revoked or expired one is replaced;
        when the owner is disabled or no longer the one owner, there is none.
        """
        if self.settings.mode is not AuthMode.LOOPBACK_OWNER:
            return None
        with self._lock:
            if self._owner_cookie is not None:
                valid = sessions.resolve_session(self.db, self._owner_cookie, idle=self.settings.idle)
                if valid is not None:
                    return valid
            self._owner_cookie = self.db.command(self._new_owner_session)
            if self._owner_cookie is None:
                return None
            return sessions.resolve_session(self.db, self._owner_cookie, idle=self.settings.idle)

    def _new_owner_session(self, tx: Transaction) -> Optional[str]:
        owner = _bootstrap_owner(tx)
        if owner is None:
            return None
        user_id, library_id = owner
        new = sessions.create_session(tx, user_id=user_id, library_id=library_id, idle=self.settings.idle)
        return new.token.reveal()


def start_web_identity(
    db: Database,
    settings: WebAuthSettings,
    *,
    root: str,
    testing: bool,
    hasher: Optional[PasswordHasher] = None,
) -> WebAuth:
    """Run the start check and bump for this web process; refuse anything else.

    ``root`` is the real path of the storage root the process is about to
    open. Raises :class:`RootPairingRefused` when the process must not serve.
    """
    try:
        lease = acquire_shared_root_lease(root)
    except StorageRootError as exc:
        raise RootPairingRefused(
            exc.reason, f"The storage root cannot be used ({exc.reason})."
        ) from None
    try:
        started = root_pairing.start_process(db, root=root, lease=lease, testing=testing, bump=True)
    finally:
        lease.release()
    if started.status is not root_pairing.StartStatus.READY or started.instance_id is None:
        raise RootPairingRefused(
            "setup_pending",
            "Setup is not complete. Create the owner and Library first "
            "(python -m backend.domain.identity.admin bootstrap; docs/admin-bootstrap.md).",
        )
    auth = WebAuth(db, settings, started.instance_id, hasher=hasher)
    if settings.mode is AuthMode.SESSION:
        warm_dummy_hash(hasher)
        return auth
    try:
        owner = auth.loopback_session()
    except DatabaseError:
        owner = None
    if owner is None:
        raise RootPairingRefused(
            "loopback_owner_missing",
            "loopback-owner mode needs exactly one enabled owner; this database has none or several.",
        )
    return auth
