"""One-time bootstrap tokens (``docs/postgresql-architecture.md`` §9.2; L-ID-1 #626).

Setup does nothing without a token. A token is 256 random bits; the database
keeps only its SHA-256 (``prks.bootstrap_tokens``), with an expiry. At most
one token is outstanding: issuing one revokes the one before it, and once the
instance is ``ready`` no token is issued. Bootstrap checks the token before it
commits the ``bootstrapping`` row (step 1) and consumes it in the
SERIALIZABLE transaction that marks setup ``ready`` (step 3), so a token
opens setup exactly once, and a retry after a crash between the steps goes on
with the same token while it is unexpired.

A :class:`BootstrapToken` never prints its value: ``repr``, ``str`` and every
refusal are fixed text. The value leaves the process only on purpose, through
:meth:`BootstrapToken.reveal` (first-time terminal delivery) or
:func:`write_token_file` (an owner-only file in the configuration directory,
outside the data root).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from backend.db.database import Database, Transaction
from backend.db.errors import DatabaseError
from backend.domain.identity.errors import RootPairingRefused
from backend.fs_durability import fsync_directory, fsync_open_file
from backend.storage.resolver import bootstrap_config_path

TOKEN_BYTES = 32
DEFAULT_TOKEN_TTL = timedelta(hours=24)
TOKEN_FILE_NAME = "bootstrap-token"

# token_urlsafe(32) is 43 characters; an operator-supplied token needs at
# least as many (256 bits of the same alphabet).
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")

_REDACTED = "BootstrapToken(<redacted>)"


class BootstrapToken:
    """A bootstrap token held in memory; compares and prints without its value."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or _TOKEN_RE.fullmatch(value) is None:
            raise RootPairingRefused(
                "bootstrap_token_malformed",
                "A bootstrap token is 43 to 128 characters of A-Z, a-z, 0-9, '-' and '_'.",
            )
        self._value = value

    def reveal(self) -> str:
        """The token itself, for its one deliberate delivery to the operator."""
        return self._value

    @property
    def digest(self) -> bytes:
        return hashlib.sha256(self._value.encode("ascii")).digest()

    def __repr__(self) -> str:
        return _REDACTED

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BootstrapToken):
            return NotImplemented
        return hmac.compare_digest(self.digest, other.digest)

    def __hash__(self) -> int:
        return hash(self.digest)

    def __reduce__(self) -> Any:  # never pickled into a queue or a cache
        raise TypeError("a BootstrapToken is not serializable")


@dataclass(frozen=True)
class IssuedToken:
    token: BootstrapToken
    expires_at: datetime


def new_token() -> BootstrapToken:
    return BootstrapToken(secrets.token_urlsafe(TOKEN_BYTES))


def parse_token(text: str) -> BootstrapToken:
    """A token typed or read by the operator; surrounding whitespace is ignored."""
    return BootstrapToken(text.strip() if isinstance(text, str) else text)


# --- database ------------------------------------------------------------------------


def issue_token(
    db: Database,
    *,
    ttl: timedelta = DEFAULT_TOKEN_TTL,
    token: Optional[BootstrapToken] = None,
) -> IssuedToken:
    """Store a new outstanding token (``token``, or a fresh one) and return it with its expiry.

    Revokes any other outstanding token. Issuing the token that is already
    outstanding again keeps it (and its expiry), so a retried automated
    install is idempotent; a token that was consumed or revoked never comes
    back. Refused once setup is ``ready``.
    """
    if ttl <= timedelta(0):
        raise ValueError("ttl must be positive")
    issued = token if token is not None else new_token()

    def step(tx: Transaction) -> IssuedToken:
        # SHARE on the instance conflicts with the ROW EXCLUSIVE lock of the
        # step-3 UPDATE that marks setup ready, so a token is never issued
        # after (or while) setup becomes ready. The tokens lock conflicts with
        # itself and serializes issuers.
        tx.execute("LOCK TABLE prks.instance IN SHARE MODE")
        tx.execute("LOCK TABLE prks.bootstrap_tokens IN SHARE ROW EXCLUSIVE MODE")
        _refuse_when_ready(tx)
        existing = tx.execute(
            "SELECT consumed_at IS NULL AND revoked_at IS NULL AS outstanding, expires_at "
            "FROM prks.bootstrap_tokens WHERE token_sha256 = %s",
            (issued.digest,),
        ).fetchone()
        if existing is not None:
            if existing["outstanding"]:
                return IssuedToken(issued, existing["expires_at"])
            raise RootPairingRefused(
                "bootstrap_token_reused",
                "This bootstrap token was already used or replaced; issue a new one.",
            )
        tx.execute(
            "UPDATE prks.bootstrap_tokens SET revoked_at = now() "
            "WHERE consumed_at IS NULL AND revoked_at IS NULL"
        )
        row = tx.execute(
            "INSERT INTO prks.bootstrap_tokens (token_sha256, expires_at) "
            "VALUES (%s, now() + %s) RETURNING expires_at",
            (issued.digest, ttl),
        ).fetchone()
        assert row is not None
        return IssuedToken(issued, row["expires_at"])

    try:
        return db.command(step)
    except DatabaseError as exc:
        raise RootPairingRefused(
            "database_unavailable", "The database could not record the bootstrap token.",
            retryable=True,
        ) from exc


def refuse_when_ready(db: Database) -> None:
    """Refuse ``already_ready`` when setup is done: every bootstrap command checks this first."""
    try:
        db.query(_refuse_when_ready)
    except DatabaseError as exc:
        raise RootPairingRefused(
            "database_unavailable", "Cannot read the setup state.", retryable=True
        ) from exc


def _refuse_when_ready(tx: Transaction) -> None:
    row = tx.execute("SELECT setup_state FROM prks.instance").fetchone()
    if row is not None and row["setup_state"] == "ready":
        raise RootPairingRefused(
            "already_ready", "This PRKS instance is already set up; bootstrap is closed."
        )


def check_token(tx: Transaction, token: BootstrapToken) -> None:
    """Refuse unless ``token`` is outstanding and unexpired (bootstrap step 1)."""
    row = tx.execute(
        "SELECT consumed_at IS NULL AND revoked_at IS NULL AS outstanding, "
        "expires_at <= now() AS expired FROM prks.bootstrap_tokens WHERE token_sha256 = %s",
        (token.digest,),
    ).fetchone()
    if row is None or not row["outstanding"]:
        raise RootPairingRefused(
            "bootstrap_token_invalid",
            "The bootstrap token is not valid; use the latest one issued for this instance.",
        )
    if row["expired"]:
        raise RootPairingRefused(
            "bootstrap_token_expired",
            "The bootstrap token has expired; issue a new one with prks admin bootstrap.",
        )


def consume_token(tx: Transaction, token: BootstrapToken) -> None:
    """Mark ``token`` consumed inside the transaction that makes setup ready (step 3)."""
    row = tx.execute(
        "UPDATE prks.bootstrap_tokens SET consumed_at = now() WHERE token_sha256 = %s "
        "AND consumed_at IS NULL AND revoked_at IS NULL AND expires_at > now() RETURNING id",
        (token.digest,),
    ).fetchone()
    if row is None:
        check_token(tx, token)  # names the reason
        raise RootPairingRefused(  # pragma: no cover - check_token always raises here
            "bootstrap_token_invalid", "The bootstrap token is not valid."
        )


def consumed_token(tx: Transaction, token: BootstrapToken) -> bool:
    """True when ``token`` is the one that completed setup (a retried final step)."""
    row = tx.execute(
        "SELECT 1 FROM prks.bootstrap_tokens WHERE token_sha256 = %s AND consumed_at IS NOT NULL",
        (token.digest,),
    ).fetchone()
    return row is not None


# --- the token file ------------------------------------------------------------------


def default_token_file(environ: Optional[Mapping[str, str]] = None) -> str:
    """``bootstrap-token`` beside the bootstrap configuration file (§6 configuration directory)."""
    config = bootstrap_config_path(os.environ if environ is None else environ)
    return os.path.join(os.path.dirname(config), TOKEN_FILE_NAME)


def _inside(path: str, root: str) -> bool:
    real_path = os.path.realpath(path)
    real_root = os.path.realpath(root)
    try:
        return os.path.commonpath([real_path, real_root]) == real_root
    except ValueError:  # different drives
        return False


def write_token_file(token: BootstrapToken, path: str, *, data_root: str) -> str:
    """Create ``path`` holding ``token``, readable by its owner only (``0600``); return the path.

    Never overwrites: an existing file (or link) at ``path`` refuses with
    ``token_file_exists``, so a token an installer is about to read is never
    swapped under it. Refused too when ``path`` lies inside ``data_root``
    (backups and relocation copy the root). The file appears whole or not at
    all: a ``0600`` temporary is written and flushed, then hard-linked into
    place, which fails instead of replacing.
    """
    if os.name == "nt":
        raise RootPairingRefused(
            "token_file_unsupported",
            "An owner-only token file is not supported on Windows; use a terminal.",
        )
    directory = os.path.dirname(os.path.abspath(path))
    if _inside(path, data_root) or _inside(directory, data_root):
        raise RootPairingRefused(
            "token_file_in_data_root", "The bootstrap token file must be outside the data root."
        )
    os.makedirs(directory, mode=0o700, exist_ok=True)
    # mkstemp creates the temporary 0600 whatever the umask.
    fd, tmp = tempfile.mkstemp(prefix=f".{TOKEN_FILE_NAME}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write((token.reveal() + "\n").encode("ascii"))
            handle.flush()
            fsync_open_file(handle.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            raise RootPairingRefused(
                "token_file_exists",
                "A bootstrap token file already exists; use it, or remove it to issue a new one.",
            ) from None
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if not fsync_directory(directory):
        raise RootPairingRefused(
            "token_file_not_durable",
            "The bootstrap token file could not be made durable.",
            retryable=True,
        )
    return path


def remove_token_file_if(path: str, token: BootstrapToken) -> bool:
    """Delete ``path`` when it holds exactly ``token`` (consumed); True if it was removed."""
    try:
        held = read_token_file(path)
    except RootPairingRefused:
        return False
    if held != token:
        return False
    try:
        os.unlink(path)
    except OSError:
        return False
    fsync_directory(os.path.dirname(os.path.abspath(path)))
    return True


def read_owner_only_file(path: str, *, what: str, limit: int) -> bytes:
    """At most ``limit`` bytes of ``path``; refused unless it is a regular file only its owner can read.

    ``what`` names the file in the fixed refusal text (``token``, ``password``).
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise RootPairingRefused(f"{what}_file_unreadable", f"The {what} file cannot be read.") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise RootPairingRefused(
                f"{what}_file_unreadable", f"The {what} file is not a regular file."
            )
        if os.name != "nt" and info.st_mode & 0o077:
            raise RootPairingRefused(
                f"{what}_file_too_open",
                f"The {what} file must be readable by its owner only (chmod 600).",
            )
        return os.read(fd, limit)
    finally:
        os.close(fd)


def read_token_file(path: str) -> BootstrapToken:
    """The token in ``path``; refused unless the file is a regular file only its owner can read."""
    data = read_owner_only_file(path, what="token", limit=4096)
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        text = ""
    return parse_token(text)
