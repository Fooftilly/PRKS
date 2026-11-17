"""Identity for the web process: settings, start check and loopback owner (§9.3, §11.1; L-ID-1 #626).

Every web process authenticates (PG-C2 #641). ``PRKS_AUTH`` takes two values:

- ``session`` (the default): password sign-in and ``prks_session`` cookies
  (:mod:`backend.api.session`).
- ``loopback-owner`` (the default of ``--testing``): every request acts as
  the bootstrap owner, through a real server-side session of the owner's
  Library, so authorization and the Library epoch fence work exactly as for
  a signed-in browser. Only a testing instance (``--testing``) bound to a
  loopback address may use it; anything else refuses to start.

Both need ``PRKS_DATABASE_URL`` (or ``PRKS_DATABASE_URL_FILE``); a testing
process without either uses ``TESTING_DEFAULT_DATABASE_URL``
(:mod:`backend.db.config`).

:func:`start_web_identity` runs once the process has opened its storage
root paired (``open_storage_root(paired=True)``): it holds the root's
``root.lock`` **shared** for its whole run, beside ``worker`` and
``migrate`` (L-D-1 #630). Under that lease it runs the start check and
start-generation bump of :mod:`~backend.domain.identity.root_pairing`
(§11.1). While setup is not ``ready`` it waits, when the caller lets it, and
checks again; a refused pairing or a failed reconcile stops the process
before it serves anything. A loopback-owner testing process that finds no
instance at all bootstraps one itself (:func:`bootstrap_testing_instance`),
so ``python prks_app.py --testing`` starts on a fresh database and root.
"""

from __future__ import annotations

import ipaddress
import secrets
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Optional

from argon2 import PasswordHasher

from backend.db.database import Database, Transaction
from backend.db.errors import DatabaseError
from backend.db.testing_guard import TestingDatabaseRefused, prove_testing_database
from backend.domain.identity import bootstrap_tokens, passwords, root_pairing, sessions
from backend.domain.identity.errors import RootPairingRefused
from backend.domain.identity.login import warm_dummy_hash
from backend.domain.identity.login_limiter import LoginLimits, TrustedProxies
from backend.domain.identity.sessions import ValidSession
from backend.storage.file_lock import SharedFileLock

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
) -> WebAuthSettings:
    """The authentication settings.

    ``PRKS_AUTH`` unset is ``session``, or ``loopback-owner`` for a testing
    process. Raises :class:`WebAuthConfigError` (or the limiter's and
    sessions' own config errors) for anything unusable.
    """
    raw = (environ.get(AUTH_ENV) or "").strip()
    if not raw:
        raw = AuthMode.LOOPBACK_OWNER.value if testing else AuthMode.SESSION.value
    try:
        mode = AuthMode(raw)
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
                f"(--host 127.0.0.1 or --host ::1); set {AUTH_ENV}=session to serve another address."
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
    # The one Library this process serves (0.8 has exactly one per instance),
    # so a session for any other Library is nobody here.
    library_id: str
    # The Argon2id hasher logins use; ``None`` is ``passwords.default_hasher()``.
    hasher: Optional[PasswordHasher] = field(default=None, repr=False)
    # Where the Library's content lives; always PostgreSQL. ``GET /api/session``
    # still names it until the browser drops the legacy Processing page that
    # read it (PG-C2 #641, its next PR removes both).
    library_content: str = "postgres"
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


SetupWait = Callable[[], bool]

# The owner and Library a fresh ``--testing`` instance is bootstrapped with.
TESTING_OWNER_LOGIN = "owner"
TESTING_OWNER_DISPLAY_NAME = "Testing Owner"
TESTING_LIBRARY_NAME = "Testing Library"


def bootstrap_testing_instance(
    db: Database, *, root: str, lease: SharedFileLock, hasher: Optional[PasswordHasher] = None
) -> None:
    """Bootstrap the owner and Library of a fresh testing instance (§9.2, §9.3).

    The first start of ``python prks_app.py --testing`` on a database with no
    instance row runs the ordinary three-step bootstrap with a token it
    issues itself, so a developer needs no ``admin bootstrap`` step. The
    owner's password is random and never shown: loopback-owner mode never
    signs in. It first proves the database is marked for testing
    (:mod:`backend.db.testing_guard`). Everything else is the normal bootstrap, so a non-empty root, a
    database paired with another root or an interrupted bootstrap of another
    kind is refused exactly as there.
    """
    # The proof prks_app.py ran before the schema step, again right before
    # the only writes a process makes on its own initiative.
    try:
        prove_testing_database(db.config.conninfo)
    except TestingDatabaseRefused as exc:
        raise RootPairingRefused(exc.reason, exc.message) from None
    owner = root_pairing.OwnerSpec(
        login_name=TESTING_OWNER_LOGIN,
        display_name=TESTING_OWNER_DISPLAY_NAME,
        library_name=TESTING_LIBRARY_NAME,
        password_hash=passwords.hash_password(secrets.token_urlsafe(32), hasher=hasher),
    )
    token = bootstrap_tokens.issue_token(db).token
    root_pairing.bootstrap(db, owner, token=token, root=root, lease=lease, testing=True)


def start_web_identity(
    db: Database,
    settings: WebAuthSettings,
    *,
    root: str,
    lease: SharedFileLock,
    testing: bool,
    hasher: Optional[PasswordHasher] = None,
    wait_for_setup: Optional[SetupWait] = None,
) -> WebAuth:
    """Run the start check and bump for this web process; refuse anything else.

    ``root`` is the real path of the storage root the process opened and
    ``lease`` its held, shared ``root.lock``. While setup is not ``ready``,
    ``wait_for_setup`` is called and the check runs again as long as it
    returns true (it waits between checks); without it, or once it returns
    false, the process is refused with ``setup_pending``. A loopback-owner
    testing process bootstraps an instance that does not exist yet
    (:func:`bootstrap_testing_instance`) instead of waiting. Raises
    :class:`RootPairingRefused` when the process must not serve.
    """
    bump = True
    while True:
        started = root_pairing.start_process(db, root=root, lease=lease, testing=testing, bump=bump)
        if started.status is root_pairing.StartStatus.READY and started.instance_id is not None:
            break
        if bump and testing and settings.mode is AuthMode.LOOPBACK_OWNER and not _has_instance(db):
            bootstrap_testing_instance(db, root=root, lease=lease, hasher=hasher)
            # Bootstrap's root claim counted as this process's bump (§11.1).
            bump = False
            continue
        if wait_for_setup is None or not wait_for_setup():
            raise RootPairingRefused(
                "setup_pending",
                "Setup is not complete. Create the owner and Library first "
                "(python prks_app.py admin bootstrap; docs/admin-bootstrap.md).",
            )
    libraries = db.query(lambda tx: tx.execute("SELECT id FROM prks.libraries LIMIT 2").fetchall())
    if len(libraries) != 1:
        # 0.8 serves one Library per instance; the data root's objects and the
        # process-wide routes are not partitioned by Library yet.
        raise RootPairingRefused(
            "library_not_unique", "This web process serves exactly one Library; the database has several."
        )
    auth = WebAuth(db, settings, started.instance_id, libraries[0]["id"], hasher=hasher)
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


def _has_instance(db: Database) -> bool:
    return db.query(lambda tx: tx.execute("SELECT 1 FROM prks.instance").fetchone()) is not None
