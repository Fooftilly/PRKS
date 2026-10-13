"""PostgreSQL persistence plumbing (#623, PG-1; ``docs/postgresql-architecture.md`` §5.1).

Configuration, the bounded connection pool, the ``command()``/``query()``
transaction helpers and the SQLSTATE error mapping. Nothing in the running
application imports this package yet: SQLite stays the only backend until
the cutover slices (§15.3). Domain tables, schema files and the migration
runner belong to PG-2; the transition facade belongs to PG-3.
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
