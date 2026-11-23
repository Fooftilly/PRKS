"""Browser first-run setup: the setup phase of a web process (#612; §9.2).

A web process that starts before setup is ``ready`` waits
(:func:`~backend.domain.identity.web_start.start_web_identity`). Meanwhile it
serves the **setup phase**: a setup page and two routes
(:mod:`backend.api.setup`), nothing of any Library. This module is what those
routes do.

The browser proves it may set the instance up with the same one-time
bootstrap token ``prks admin bootstrap issue-token`` gives the operator
(``docs/admin-bootstrap.md``): the token is the claim, so a visitor without it
can do nothing, and once setup is ``ready`` no token opens it again. The
setup itself is :func:`~backend.domain.identity.root_pairing.bootstrap`, the
same three idempotent steps the CLI runs, in this process under the shared
root lease it already holds. An interrupted setup is resumed by the next
attempt with a valid token, from the browser or the CLI.

Rules:

- The token is checked before the password is hashed or anything is
  written, so a wrong token costs one indexed read.
- One setup runs at a time in this process; a second request while one runs
  is refused ``setup_busy`` instead of queueing behind it. Across processes
  the bootstrap's own locks serialize (§9.2).
- Refused tokens are rate limited per process (:class:`TokenAttempts`). A
  token is 256 bits, so this only bounds the work a client can cause.
- Nothing here logs or returns a token, a password, a hash or a path.
- After a setup that created the owner, the owner gets a session in the
  served Library, so the browser that set the instance up is signed in.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Optional

from argon2 import PasswordHasher

from backend.db.database import Database, Transaction
from backend.db.errors import DatabaseError
from backend.domain.identity import bootstrap_tokens, passwords, root_pairing, sessions
from backend.domain.identity.bootstrap_tokens import BootstrapToken
from backend.domain.identity.errors import RootPairingRefused
from backend.domain.identity.sessions import NewSession
from backend.domain.identity.web_start import WebAuthSettings
from backend.storage.file_lock import SharedFileLock

STATE_UNCONFIGURED = "unconfigured"
STATE_BOOTSTRAPPING = "bootstrapping"
STATE_READY = root_pairing.SETUP_READY

DEFAULT_LIBRARY_NAME = "Library"
MAX_NAME_CHARS = 200

# Refused tokens per window before the setup routes answer 429.
TOKEN_ATTEMPT_LIMIT = 10
TOKEN_ATTEMPT_WINDOW_S = 60.0


class SetupInputError(ValueError):
    """A setup request that is not usable; ``reason`` is a stable code, ``field`` the input."""

    def __init__(self, reason: str, field_name: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.field = field_name


class SetupBusy(Exception):
    """Another setup request is running in this process."""


class TooManyAttempts(Exception):
    """Too many refused tokens recently; ``retry_after`` is whole seconds."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("too many setup attempts")
        self.retry_after = retry_after


@dataclass(frozen=True)
class SetupRequest:
    """What the browser sends; :func:`parse_request` builds it."""

    token: BootstrapToken
    login: str
    display_name: str
    password: str = field(repr=False)
    library_name: str


@dataclass(frozen=True)
class SetupDone:
    """A finished setup. ``session`` is the owner's new session, when one was created."""

    created: bool
    session: Optional[NewSession] = field(default=None, repr=False)


def _text(value: object, field_name: str, *, required: bool) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise SetupInputError("invalid_request", field_name)
    text = value.strip()
    if required and not text:
        raise SetupInputError("required", field_name)
    if len(text) > MAX_NAME_CHARS or "\x00" in text:
        raise SetupInputError("too_long" if len(text) > MAX_NAME_CHARS else "invalid", field_name)
    return text


def parse_request(data: object) -> SetupRequest:
    """Validate a ``POST /api/setup`` body; raises :class:`SetupInputError`.

    The display name defaults to the login and the Library name to
    ``Library``, as for ``admin bootstrap complete``.
    """
    if not isinstance(data, Mapping):
        raise SetupInputError("invalid_request", "body")
    raw_token = data.get("token")
    if not isinstance(raw_token, str) or not raw_token.strip():
        raise SetupInputError("required", "token")
    try:
        token = bootstrap_tokens.parse_token(raw_token)
    except RootPairingRefused:
        raise SetupInputError("bootstrap_token_malformed", "token") from None
    login = _text(data.get("login"), "login", required=True)
    try:
        root_pairing.check_owner_login(login)
    except RootPairingRefused:
        raise SetupInputError("invalid", "login") from None
    display_name = _text(data.get("display_name"), "display_name", required=False) or login
    library_name = _text(data.get("library_name"), "library_name", required=False) or DEFAULT_LIBRARY_NAME
    password = data.get("password")
    if not isinstance(password, str) or not password:
        raise SetupInputError("required", "password")
    if len(password.encode("utf-8", "surrogatepass")) > passwords.MAX_PASSWORD_BYTES:
        raise SetupInputError("too_long", "password")
    return SetupRequest(token, login, display_name, password, library_name)


class TokenAttempts:
    """A sliding window of refused tokens for this process."""

    def __init__(
        self,
        limit: int = TOKEN_ATTEMPT_LIMIT,
        window_s: float = TOKEN_ATTEMPT_WINDOW_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window = window_s
        self._clock = clock
        self._refused: deque[float] = deque()
        self._lock = threading.Lock()

    def _trim(self, now: float) -> None:
        while self._refused and now - self._refused[0] >= self._window:
            self._refused.popleft()

    def check(self) -> None:
        with self._lock:
            now = self._clock()
            self._trim(now)
            if len(self._refused) >= self._limit:
                wait = self._window - (now - self._refused[0])
                raise TooManyAttempts(max(1, int(wait + 0.999)))

    def refused(self) -> None:
        with self._lock:
            self._refused.append(self._clock())


def read_state(db: Database) -> str:
    """``unconfigured``, ``bootstrapping`` or ``ready``; raises :class:`DatabaseError`."""

    def read(tx: Transaction) -> str:
        row = tx.execute("SELECT setup_state FROM prks.instance").fetchone()
        return STATE_UNCONFIGURED if row is None else str(row["setup_state"])

    return db.query(read)


def _token_usable(db: Database, token: BootstrapToken) -> None:
    def check(tx: Transaction) -> None:
        row = tx.execute("SELECT setup_state FROM prks.instance").fetchone()
        if row is not None and row["setup_state"] == STATE_READY:
            raise RootPairingRefused(
                "already_ready", "This PRKS instance is already set up; bootstrap is closed."
            )
        bootstrap_tokens.check_token(tx, token)

    try:
        db.query(check)
    except DatabaseError as exc:
        raise RootPairingRefused(
            "database_unavailable", "Cannot read the setup state.", retryable=True
        ) from exc


# Token refusals that count against the attempt window.
_TOKEN_REFUSALS = frozenset({"bootstrap_token_invalid", "bootstrap_token_expired"})


class SetupPhase:
    """The setup a waiting web process offers the browser.

    ``done`` is set once setup is ``ready`` through this process, so the
    caller stops the setup server and continues its start.
    """

    def __init__(
        self,
        db: Database,
        settings: WebAuthSettings,
        *,
        root: str,
        lease: SharedFileLock,
        testing: bool,
        environ: Optional[Mapping[str, str]] = None,
        hasher: Optional[PasswordHasher] = None,
        attempts: Optional[TokenAttempts] = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self._root = root
        self._lease = lease
        self._testing = testing
        self._environ = os.environ if environ is None else environ
        self._hasher = hasher
        self._attempts = attempts or TokenAttempts()
        self._running = threading.Lock()
        self.done = threading.Event()

    def state(self) -> str:
        """The setup state; ``ready`` once this process finished setup even before a re-read."""
        if self.done.is_set():
            return STATE_READY
        return read_state(self.db)

    def complete(self, request: SetupRequest) -> SetupDone:
        """Run setup for ``request``; raises :class:`RootPairingRefused`, :class:`SetupBusy`
        or :class:`TooManyAttempts`."""
        self._attempts.check()
        if not self._running.acquire(blocking=False):
            raise SetupBusy()
        try:
            return self._complete(request)
        finally:
            self._running.release()

    def _complete(self, request: SetupRequest) -> SetupDone:
        try:
            _token_usable(self.db, request.token)
        except RootPairingRefused as exc:
            if exc.reason in _TOKEN_REFUSALS:
                self._attempts.refused()
            raise
        owner = root_pairing.OwnerSpec(
            login_name=request.login,
            display_name=request.display_name,
            library_name=request.library_name,
            password_hash=passwords.hash_password(request.password, hasher=self._hasher),
        )
        result = root_pairing.bootstrap(
            self.db, owner, token=request.token, root=self._root, lease=self._lease,
            testing=self._testing,
        )
        self.done.set()
        self._remove_token_files(request.token)
        if not result.created:
            return SetupDone(created=False)
        try:
            session = self.db.command(
                lambda tx: sessions.create_session(
                    tx, user_id=result.user_id, library_id=result.library_id, idle=self.settings.idle
                )
            )
        except DatabaseError:
            # Setup is done; the owner signs in instead.
            session = None
        return SetupDone(created=True, session=session)

    def _remove_token_files(self, token: BootstrapToken) -> None:
        """Delete the configuration-directory token file holding the used token, as ``complete`` does."""
        path = bootstrap_tokens.default_token_file(self._environ)
        bootstrap_tokens.remove_token_file_if(path, token)
        try:
            bootstrap_tokens.sweep_temporary_token_files(
                os.path.dirname(path), lambda held: bootstrap_tokens.issued_here(self.db, held)
            )
        except (RootPairingRefused, OSError):
            pass  # best effort, as for the CLI
