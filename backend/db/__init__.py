"""PostgreSQL persistence plumbing (#623, PG-1; ``docs/postgresql-architecture.md`` §5.1).

Configuration, the bounded connection pool, the ``command()``/``query()``
transaction helpers and the SQLSTATE error mapping (PG-1), plus the schema
migration runner (:mod:`backend.db.migrate`), the baseline schema files in
``schema/`` and the catalog snapshot (PG-2, #624). PostgreSQL is the only
Library store (PG-C2 #641); since PG-C3 (#642) the HTTP routes call the
domain commands directly, holding :class:`backend.library.PostgresLibrary`.
"""

from backend.db.config import DatabaseConfig, load_database_config
from backend.db.database import Database, Isolation, Transaction
from backend.db.errors import (
    ConstraintRegistry,
    ConstraintViolationError,
    DatabaseConfigError,
    DatabaseError,
    DatabaseUnavailableError,
    LockTimeoutError,
    PoolExhaustedError,
    RetryTransactionError,
    StatementTimeoutError,
    TransientDatabaseError,
    UnmappedDatabaseError,
    map_database_error,
    register_constraint,
)

__all__ = [
    "ConstraintRegistry",
    "ConstraintViolationError",
    "Database",
    "DatabaseConfig",
    "DatabaseConfigError",
    "DatabaseError",
    "DatabaseUnavailableError",
    "Isolation",
    "LockTimeoutError",
    "PoolExhaustedError",
    "RetryTransactionError",
    "StatementTimeoutError",
    "Transaction",
    "TransientDatabaseError",
    "UnmappedDatabaseError",
    "load_database_config",
    "map_database_error",
    "register_constraint",
]
