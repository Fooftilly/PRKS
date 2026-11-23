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

import contextlib
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping
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
from backend.storage.choose import CHOOSABLE_SOURCES, Chosen, choose_root, selection_fence
from backend.storage.errors import StorageRootError, StorageRootRefused
from backend.storage.file_lock import SharedFileLock

STATE_UNCONFIGURED = "unconfigured"
STATE_BOOTSTRAPPING = "bootstrapping"
STATE_READY = root_pairing.SETUP_READY

# Whether the setup page may choose the Library folder (#720).
STORAGE_CHOOSABLE = "choosable"
STORAGE_FIXED = "fixed"
STORAGE_RESTART_REQUIRED = "restart_required"
_RESTART_MESSAGE = "A new Library folder was chosen. Restart PRKS, then finish setup."

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
    if not passwords.usable_password(password):
        raise SetupInputError("invalid", "password")
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
        root_source: Optional[str] = None,
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
        # Held across the bootstrap commit and ``done``; the start check holds
        # it too (:meth:`settled`), so it never sees this process's commit
        # without also seeing ``done``.
        self._settle = threading.Lock()
        self._closed = False
        self._root_source = root_source
        # Set once a folder was chosen (#720): setup waits for a restart on it.
        self.chosen_root: Optional[str] = None
        self.done = threading.Event()

    def state(self) -> str:
        """The setup state; ``ready`` once this process finished setup even before a re-read."""
        if self.done.is_set():
            return STATE_READY
        return read_state(self.db)

    def storage_choice(self) -> str:
        """``choosable``, ``fixed`` or ``restart_required``: whether the setup page may choose a folder.

        ``choosable`` only for a non-testing process whose folder no higher
        source set and that setup has not claimed (``backend.storage.choose``).
        Reveals no path. Raises nothing: a folder it cannot read is ``fixed``.
        """
        if self.chosen_root is not None:
            return STORAGE_RESTART_REQUIRED
        if self._testing or self._root_source not in CHOOSABLE_SOURCES:
            return STORAGE_FIXED
        from backend.storage import resolver
        from backend.storage.choose import refusal_before_target

        try:
            current = resolver.resolve_storage_root(cli_root=None, environ=self._environ, testing=False).root
            if os.path.realpath(current) != os.path.realpath(self._root):
                # Another process chose a folder after this one started.
                return STORAGE_RESTART_REQUIRED
            refusal = refusal_before_target(self._environ, self._root)
            if refusal is None and read_state(self.db) != STATE_UNCONFIGURED:
                return STORAGE_FIXED  # setup has started: choose_folder would refuse
        except (StorageRootError, DatabaseError):
            return STORAGE_FIXED
        return STORAGE_FIXED if refusal is not None else STORAGE_CHOOSABLE

    def choose_folder(self, token: BootstrapToken, folder: str) -> Chosen:
        """Record ``folder`` as the Library folder for the next start (#720).

        Checks the bootstrap token first, like :meth:`complete`, and shares
        its one-at-a-time rule and attempt window. Once a folder is chosen,
        setup is refused until PRKS restarts on it. Raises
        :class:`RootPairingRefused`, :class:`SetupBusy`,
        :class:`TooManyAttempts` or ``StorageRootError``.
        """
        self._attempts.check()
        if not self._running.acquire(blocking=False):
            raise SetupBusy()
        try:
            if self._closed:
                raise SetupBusy()
            self._attempts.check()
            try:
                _token_usable(self.db, token)
            except RootPairingRefused as exc:
                if exc.reason in _TOKEN_REFUSALS:
                    self._attempts.refused()
                raise
            if self.chosen_root is not None:
                raise RootPairingRefused("restart_required", _RESTART_MESSAGE)
            if self._testing or self._root_source not in CHOOSABLE_SOURCES:
                raise StorageRootRefused(
                    "choose_set_by_host",
                    "The Library folder is set where PRKS is deployed; change it there.",
                )
            if read_state(self.db) != STATE_UNCONFIGURED:
                raise StorageRootRefused(
                    "choose_root_claimed", "Setup has already started on the current Library folder."
                )
            with self._settle:
                if self.done.is_set():
                    raise RootPairingRefused(
                        "already_ready", "This PRKS instance is already set up; bootstrap is closed."
                    )
                chosen = choose_root(folder, environ=self._environ, held_current_root=self._root)
                self.chosen_root = chosen.root
            return chosen
        finally:
            self._running.release()

    def close(self) -> None:
        """Wait for a setup request in flight, then refuse any later one.

        The setup server's worker threads can outlive its shutdown; the caller
        closes the pool and the root lease only after this returns.
        """
        with self._running:
            self._closed = True

    @contextlib.contextmanager
    def settled(self) -> Iterator[bool]:
        """Hold off a bootstrap commit in this process; yields whether this process already ran it."""
        with self._settle:
            yield self.done.is_set()

    def complete(self, request: SetupRequest) -> SetupDone:
        """Run setup for ``request``; raises :class:`RootPairingRefused`, :class:`SetupBusy`
        or :class:`TooManyAttempts`."""
        self._attempts.check()
        if not self._running.acquire(blocking=False):
            raise SetupBusy()
        try:
            if self._closed:
                raise SetupBusy()
            # A refusal recorded while this request waited for the lock counts.
            self._attempts.check()
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
        if self.chosen_root is not None:
            # Setup would claim the folder this process serves, which the
            # bootstrap file no longer selects.
            raise RootPairingRefused("restart_required", _RESTART_MESSAGE)
        owner = root_pairing.OwnerSpec(
            login_name=request.login,
            display_name=request.display_name,
            library_name=request.library_name,
            password_hash=passwords.hash_password(request.password, hasher=self._hasher),
        )
        with self._settle:
            # Under the config lock, so no choice interleaves with the claim,
            # and only while the bootstrap file still selects this root.
            try:
                with selection_fence(
                    self._environ, self._root,
                    require_selected=not self._testing and self._root_source in CHOOSABLE_SOURCES,
                ):
                    result = root_pairing.bootstrap(
                        self.db, owner, token=request.token, root=self._root, lease=self._lease,
                        testing=self._testing,
                    )
            except StorageRootError as exc:
                if exc.reason == "storage_selection_changed":
                    raise RootPairingRefused("restart_required", _RESTART_MESSAGE) from None
                raise RootPairingRefused(exc.reason, exc.message, retryable=True) from None
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
