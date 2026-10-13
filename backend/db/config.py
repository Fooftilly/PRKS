"""PostgreSQL connection configuration (``docs/postgresql-architecture.md`` §4.3).

The connection string comes from ``PRKS_DATABASE_URL`` or from the file named
by ``PRKS_DATABASE_URL_FILE`` (a Compose secret, §12.3), never both. It is a
secret: it is kept out of ``repr``, error messages and logs. Refusals carry a
stable ``reason`` and name only the environment variable.

Session defaults are the request/worker defaults of §4.3. Backup, restore and
``rebuild-derived`` use their own dedicated connection with other settings;
that belongs to the lanes that build them, not to this pool configuration.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from backend.db.errors import DatabaseConfigError

ENV_DATABASE_URL = "PRKS_DATABASE_URL"
ENV_DATABASE_URL_FILE = "PRKS_DATABASE_URL_FILE"
ENV_POOL_MAX = "PRKS_DB_POOL_MAX"

# Process roles and their pool ceilings (§4.3). ``test`` is the harness.
DEFAULT_POOL_MAX = {"web": 10, "worker": 4, "test": 4}

# A secret file holds one connection string; anything larger is a mistake.
_MAX_URL_FILE_BYTES = 64 * 1024


@dataclass(frozen=True)
class DatabaseConfig:
    """Connection and pool settings for one process role."""

    conninfo: str = field(repr=False)
    role: str = "web"
    pool_min: int = 1
    pool_max: int = 10
    pool_timeout_seconds: float = 10.0
    max_idle_seconds: float = 300.0
    statement_timeout_ms: int = 30_000
    lock_timeout_ms: int = 5_000
    idle_in_transaction_timeout_ms: int = 60_000

    def __post_init__(self) -> None:
        if self.role not in DEFAULT_POOL_MAX:
            raise DatabaseConfigError("invalid_role", "Unknown database process role.")
        if not self.conninfo.strip():
            raise DatabaseConfigError("empty_url", "The database URL is empty.")
        if self.pool_min < 0 or self.pool_max < 1 or self.pool_min > self.pool_max:
            raise DatabaseConfigError("invalid_pool_size", "The database pool size is invalid.")
        if self.pool_timeout_seconds <= 0:
            raise DatabaseConfigError("invalid_pool_timeout", "The database pool timeout is invalid.")
        for value in (
            self.statement_timeout_ms,
            self.lock_timeout_ms,
            self.idle_in_transaction_timeout_ms,
        ):
            if value < 0:
                raise DatabaseConfigError("invalid_timeout", "A database timeout is invalid.")

    @property
    def application_name(self) -> str:
        return f"prks-{self.role}"

    def session_settings(self) -> dict[str, str]:
        """Session GUCs every pooled connection sets once (§4.3)."""
        return {
            "statement_timeout": str(self.statement_timeout_ms),
            "lock_timeout": str(self.lock_timeout_ms),
            "idle_in_transaction_session_timeout": str(self.idle_in_transaction_timeout_ms),
        }


def _read_url_file(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            raw = handle.read(_MAX_URL_FILE_BYTES + 1)
    except OSError:
        raise DatabaseConfigError(
            "url_file_unreadable",
            f"{ENV_DATABASE_URL_FILE} names a file that cannot be read.",
        ) from None
    if len(raw) > _MAX_URL_FILE_BYTES:
        raise DatabaseConfigError(
            "url_file_too_large",
            f"The file named by {ENV_DATABASE_URL_FILE} is too large.",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise DatabaseConfigError(
            "url_file_not_utf8",
            f"The file named by {ENV_DATABASE_URL_FILE} is not UTF-8.",
        ) from None
    # Secret files conventionally end with one newline; nothing else is trimmed
    # inside the value.
    return text.strip()


def _validate_conninfo(conninfo: str) -> None:
    """Parse with libpq's rules so a typo fails at start, without echoing the value."""
    from psycopg import ProgrammingError
    from psycopg.conninfo import conninfo_to_dict

    try:
        conninfo_to_dict(conninfo)
    except ProgrammingError:
        raise DatabaseConfigError("invalid_url", "The database URL cannot be parsed.") from None


def _pool_max(environ: Mapping[str, str], role: str) -> int:
    raw = environ.get(ENV_POOL_MAX)
    if raw is None or raw.strip() == "":
        return DEFAULT_POOL_MAX[role]
    try:
        value = int(raw.strip())
    except ValueError:
        raise DatabaseConfigError("invalid_pool_size", f"{ENV_POOL_MAX} must be an integer.") from None
    if value < 1:
        raise DatabaseConfigError("invalid_pool_size", f"{ENV_POOL_MAX} must be at least 1.")
    return value


def load_database_config(
    environ: Mapping[str, str] | None = None,
    *,
    role: str = "web",
) -> DatabaseConfig:
    """Build the configuration for ``role`` from the environment."""
    env = os.environ if environ is None else environ
    if role not in DEFAULT_POOL_MAX:
        raise DatabaseConfigError("invalid_role", "Unknown database process role.")
    url = env.get(ENV_DATABASE_URL)
    url_file = env.get(ENV_DATABASE_URL_FILE)
    has_url = bool(url and url.strip())
    has_file = bool(url_file and url_file.strip())
    if has_url and has_file:
        raise DatabaseConfigError(
            "url_conflict",
            f"Set {ENV_DATABASE_URL} or {ENV_DATABASE_URL_FILE}, not both.",
        )
    if url_file and has_file:
        conninfo = _read_url_file(url_file.strip())
    elif url and has_url:
        conninfo = url.strip()
    else:
        raise DatabaseConfigError(
            "url_missing",
            f"Set {ENV_DATABASE_URL} or {ENV_DATABASE_URL_FILE}.",
        )
    if not conninfo:
        raise DatabaseConfigError("empty_url", "The database URL is empty.")
    _validate_conninfo(conninfo)
    # PRKS_DB_POOL_MAX sizes the web pool only; the worker keeps its own
    # ceiling (§4.3).
    pool_max = _pool_max(env, role) if role == "web" else DEFAULT_POOL_MAX[role]
    return DatabaseConfig(conninfo=conninfo, role=role, pool_min=1, pool_max=pool_max)
