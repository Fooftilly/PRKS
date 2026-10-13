"""PostgreSQL error mapping: SQLSTATE plus constraint name to PRKS errors.

``docs/postgresql-architecture.md`` §5.4 is the contract. psycopg errors are
mapped here once, so no caller matches driver exception text the way the
SQLite code matches ``sqlite3.IntegrityError`` strings today.

Every mapped error carries a stable ``code`` and the HTTP status the adapter
answers with. Messages are fixed text: they never include the driver's
message, which can quote row values (``Key (name)=(...) already exists``),
nor the connection string. Log ``code``, ``sqlstate`` and ``constraint``
only.

Constraint names are part of the schema contract (§6.1 naming:
``<table>_<cols>_key``, ``_fkey``, ``_check``, ``_excl``). Each domain lane
registers the unique and foreign-key constraints it owns with
:func:`register_constraint`; a violation of a constraint nobody registered
is a 500, never a silent success.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from threading import Lock

import psycopg
from psycopg_pool import PoolTimeout

# SQLSTATEs the mapping names (PostgreSQL Appendix A).
UNIQUE_VIOLATION = "23505"
FOREIGN_KEY_VIOLATION = "23503"
CHECK_VIOLATION = "23514"
SERIALIZATION_FAILURE = "40001"
DEADLOCK_DETECTED = "40P01"
LOCK_NOT_AVAILABLE = "55P03"
QUERY_CANCELED = "57014"
READ_ONLY_SQL_TRANSACTION = "25006"

RETRYABLE_SQLSTATES = frozenset({SERIALIZATION_FAILURE, DEADLOCK_DETECTED})

# The server is going away or cannot take the session: the outcome of the
# statement is unknown or it never ran, so it is a 503 and never a terminal
# sync result. Class 08 (connection exception) is matched by prefix.
_UNAVAILABLE_SQLSTATES = frozenset(
    {
        "53300",  # too_many_connections
        "57P01",  # admin_shutdown
        "57P02",  # crash_shutdown
        "57P03",  # cannot_connect_now
        "25P03",  # idle_in_transaction_session_timeout
    }
)

DEFAULT_RETRY_AFTER_SECONDS = 1
UNAVAILABLE_RETRY_AFTER_SECONDS = 5


class DatabaseError(Exception):
    """Base class: a database operation failed with a mapped PRKS error.

    ``code`` and ``http_status`` are what the HTTP adapter returns;
    ``retry_after`` is set for 503 answers (seconds). ``sqlstate`` and
    ``constraint`` are safe to log; ``message`` is fixed text.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int,
        retry_after: int | None = None,
        sqlstate: str | None = None,
        constraint: str | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retry_after = retry_after
        self.sqlstate = sqlstate
        self.constraint = constraint
        self.field = field


class DatabaseConfigError(DatabaseError):
    """The database configuration is missing or unusable. Never names the URL."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__("DATABASE_CONFIG", message, http_status=500)
        self.reason = reason


class TransientDatabaseError(DatabaseError):
    """A 503: the caller may retry after ``retry_after`` seconds."""


class DatabaseUnavailableError(TransientDatabaseError):
    """The server cannot be reached or dropped the session."""

    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__(
            "DATABASE_UNAVAILABLE",
            "The database is unavailable.",
            http_status=503,
            retry_after=UNAVAILABLE_RETRY_AFTER_SECONDS,
            sqlstate=sqlstate,
        )


class PoolExhaustedError(TransientDatabaseError):
    """No pooled connection became free within the pool timeout (§4.3)."""

    def __init__(self) -> None:
        super().__init__(
            "DATABASE_BUSY",
            "No database connection is free.",
            http_status=503,
            retry_after=DEFAULT_RETRY_AFTER_SECONDS,
        )


class RetryTransactionError(TransientDatabaseError):
    """``40001``/``40P01`` outside the retry budget: 503 ``RETRY``."""

    def __init__(self, *, sqlstate: str) -> None:
        super().__init__(
            "RETRY",
            "The transaction conflicted with another one; retry it.",
            http_status=503,
            retry_after=DEFAULT_RETRY_AFTER_SECONDS,
            sqlstate=sqlstate,
        )


class LockTimeoutError(TransientDatabaseError):
    """``55P03``: a lock was not granted within ``lock_timeout``."""

    def __init__(self) -> None:
        super().__init__(
            "LOCK_TIMEOUT",
            "A database lock was not available in time.",
            http_status=503,
            retry_after=DEFAULT_RETRY_AFTER_SECONDS,
            sqlstate=LOCK_NOT_AVAILABLE,
        )


class StatementTimeoutError(TransientDatabaseError):
    """``57014``: the statement ran past ``statement_timeout``."""

    def __init__(self) -> None:
        super().__init__(
            "STATEMENT_TIMEOUT",
            "The database statement took too long.",
            http_status=503,
            retry_after=DEFAULT_RETRY_AFTER_SECONDS,
            sqlstate=QUERY_CANCELED,
        )


class ConstraintViolationError(DatabaseError):
    """A registered unique/foreign-key constraint or a check constraint refused a write."""


class UnmappedDatabaseError(DatabaseError):
    """Anything the table does not map, including an unregistered constraint: a 500."""

    def __init__(
        self,
        *,
        sqlstate: str | None,
        constraint: str | None = None,
        code: str = "DATABASE_ERROR",
    ) -> None:
        super().__init__(
            code,
            "The database refused the operation.",
            http_status=500,
            sqlstate=sqlstate,
            constraint=constraint,
        )


@dataclass(frozen=True)
class ConstraintMapping:
    """What a registered constraint violation becomes."""

    code: str
    http_status: int


class ConstraintRegistry:
    """Constraint name → domain code for ``23505`` and ``23503`` (§5.4).

    Registration is idempotent for an identical mapping and refuses a
    conflicting one, so two lanes cannot silently disagree about a name.
    """

    def __init__(self, mappings: Mapping[str, ConstraintMapping] | None = None) -> None:
        self._lock = Lock()
        self._mappings: dict[str, ConstraintMapping] = dict(mappings or {})

    def register(self, constraint: str, code: str, *, http_status: int = 409) -> None:
        if not constraint or not code:
            raise ValueError("constraint and code are required")
        mapping = ConstraintMapping(code=code, http_status=http_status)
        with self._lock:
            existing = self._mappings.get(constraint)
            if existing is not None and existing != mapping:
                raise ValueError(f"constraint {constraint!r} is already mapped differently")
            self._mappings[constraint] = mapping

    def lookup(self, constraint: str | None) -> ConstraintMapping | None:
        if not constraint:
            return None
        with self._lock:
            return self._mappings.get(constraint)


DEFAULT_REGISTRY = ConstraintRegistry()


def register_constraint(constraint: str, code: str, *, http_status: int = 409) -> None:
    """Register a constraint in the process-wide registry."""
    DEFAULT_REGISTRY.register(constraint, code, http_status=http_status)


def check_constraint_field(constraint: str | None, table: str | None) -> str | None:
    """``<table>_<cols>_check`` → ``<cols>``; ``None`` when the name does not follow §6.1."""
    if not constraint or not table:
        return None
    prefix = f"{table}_"
    suffix = "_check"
    if not (constraint.startswith(prefix) and constraint.endswith(suffix)):
        return None
    field = constraint[len(prefix) : -len(suffix)]
    return field or None


def sqlstate_of(exc: BaseException) -> str | None:
    if isinstance(exc, psycopg.Error):
        return exc.sqlstate
    return None


def is_retryable(exc: BaseException) -> bool:
    """``40001``/``40P01``: safe to retry because a command has no outside effects (§7.1)."""
    return sqlstate_of(exc) in RETRYABLE_SQLSTATES


def _is_unavailable(exc: psycopg.Error, sqlstate: str | None) -> bool:
    if sqlstate is None:
        # No SQLSTATE: the driver lost or never had a connection.
        return isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError))
    return sqlstate.startswith("08") or sqlstate in _UNAVAILABLE_SQLSTATES


def map_database_error(
    exc: BaseException,
    registry: ConstraintRegistry | None = None,
) -> DatabaseError:
    """Return the PRKS error for ``exc``. The caller raises it ``from exc``."""
    if isinstance(exc, DatabaseError):
        return exc
    if isinstance(exc, PoolTimeout):
        return PoolExhaustedError()
    if not isinstance(exc, psycopg.Error):
        return UnmappedDatabaseError(sqlstate=None)

    reg = registry if registry is not None else DEFAULT_REGISTRY
    sqlstate = exc.sqlstate
    diag = exc.diag
    constraint = diag.constraint_name if sqlstate is not None else None

    if sqlstate in RETRYABLE_SQLSTATES:
        return RetryTransactionError(sqlstate=sqlstate)
    if sqlstate == LOCK_NOT_AVAILABLE:
        return LockTimeoutError()
    if sqlstate == QUERY_CANCELED:
        return StatementTimeoutError()
    if _is_unavailable(exc, sqlstate):
        return DatabaseUnavailableError(sqlstate=sqlstate)
    if sqlstate in (UNIQUE_VIOLATION, FOREIGN_KEY_VIOLATION):
        mapping = reg.lookup(constraint)
        if mapping is None:
            return UnmappedDatabaseError(sqlstate=sqlstate, constraint=constraint)
        return ConstraintViolationError(
            mapping.code,
            "The change conflicts with existing data.",
            http_status=mapping.http_status,
            sqlstate=sqlstate,
            constraint=constraint,
        )
    if sqlstate == CHECK_VIOLATION:
        return ConstraintViolationError(
            "INVALID_VALUE",
            "A value is not allowed.",
            http_status=400,
            sqlstate=sqlstate,
            constraint=constraint,
            field=check_constraint_field(constraint, diag.table_name),
        )
    if sqlstate == READ_ONLY_SQL_TRANSACTION:
        # A write inside query(): a programming error, never a client error.
        return UnmappedDatabaseError(sqlstate=sqlstate, code="READ_ONLY_TRANSACTION")
    return UnmappedDatabaseError(sqlstate=sqlstate, constraint=constraint)
