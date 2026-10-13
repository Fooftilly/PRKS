"""PostgreSQL schema migration runner (``docs/postgresql-architecture.md`` §4.4, PG4).

``backend/db/schema/`` holds ordered plain-SQL files named ``NNN_name.sql``.
Before v1.0.0 they are per-domain *baseline* files applied in order to an empty
database; a database is never upgraded across an in-place edit of one (PG2,
clean start). :func:`migrate`:

1. opens its **own** connection (never a pooled one) with an empty
   ``search_path``, so every file must schema-qualify what it names;
2. takes a session-level ``pg_advisory_lock`` on :data:`MIGRATION_LOCK_KEY`
   with a bounded wait, so concurrent runs apply each file once;
3. creates ``prks.schema_migrations`` when the database is empty, and refuses
   a database that is newer than the code, applied files out of order, or
   applied a file whose bytes have since changed (the instance must be
   recreated);
4. applies each pending file and records its SHA-256 in one transaction per
   file; a failing file rolls back and stops the run, never swallowed;
5. closes the connection on every path, which releases the lock.

The runner never reads or writes the PRKS data root. The root claim, the
start check, the reconcile and the start-generation bump run after it, from
the identity lane (§11.1, L-ID-1), under a different advisory key.

:func:`verify_schema` is the startup validation: it compares
``schema_migrations`` with the files and nothing else (§4.4).

Errors carry a stable ``reason`` and fixed text naming at most a schema file;
they never include the connection string or the driver's message.

Run from the repository root: ``python -m backend.db.migrate`` (reads
``PRKS_DATABASE_URL`` or ``PRKS_DATABASE_URL_FILE``).
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import sql

SCHEMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema")

# Fixed session advisory-lock key of the schema runner ("PRKSMIGR" in ASCII).
# The identity lane's start key (§11.1) is a different constant.
MIGRATION_LOCK_KEY = 0x50524B534D494752

# Bounded wait for the runner's lock and for each DDL statement's locks.
DEFAULT_LOCK_TIMEOUT_MS = 60_000

APPLICATION_NAME = "prks-migrate"

_FILE_RE = re.compile(r"^([0-9]{3})_([a-z0-9][a-z0-9_]*)\.sql$")

_RECREATE_HINT = (
    "Before v1.0.0 an applied schema file is never changed under a database: "
    "recreate this instance (a new database and a new data root), then restore "
    "a library backup into it."
)


class MigrationError(Exception):
    """The runner refused or failed; ``reason`` is stable, ``message`` fixed text."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class SchemaFile:
    version: int
    name: str
    sha256: str
    body: bytes

    @property
    def filename(self) -> str:
        return f"{self.version:03d}_{self.name}.sql"


@dataclass(frozen=True)
class AppliedFile:
    version: int
    name: str
    sha256: str


@dataclass(frozen=True)
class MigrationResult:
    applied: tuple[str, ...]  # filenames this run applied, in order
    current: int  # number of files already applied before this run


def load_schema_files(directory: str = SCHEMA_DIR) -> list[SchemaFile]:
    """The schema files in version order; a malformed directory is refused."""
    files: list[SchemaFile] = []
    seen: dict[int, str] = {}
    for entry in sorted(os.listdir(directory)):
        if not entry.endswith(".sql"):
            continue
        match = _FILE_RE.fullmatch(entry)
        if match is None:
            raise MigrationError(
                "invalid_schema_file", f"Schema file {entry} is not named NNN_name.sql."
            )
        version = int(match.group(1))
        if version in seen:
            raise MigrationError(
                "invalid_schema_file",
                f"Schema files {seen[version]} and {entry} share version {version:03d}.",
            )
        seen[version] = entry
        with open(os.path.join(directory, entry), "rb") as handle:
            body = handle.read()
        try:
            body.decode("utf-8")
        except UnicodeDecodeError:
            raise MigrationError(
                "invalid_schema_file", f"Schema file {entry} is not UTF-8."
            ) from None
        files.append(
            SchemaFile(
                version=version,
                name=match.group(2),
                sha256=hashlib.sha256(body).hexdigest(),
                body=body,
            )
        )
    if not files:
        raise MigrationError("invalid_schema_file", "The schema directory has no files.")
    files.sort(key=lambda f: f.version)
    return files


def _applied(conn: psycopg.Connection[Any]) -> list[AppliedFile] | None:
    """Rows of ``prks.schema_migrations``, or ``None`` when the table is absent."""
    exists = conn.execute("SELECT to_regclass('prks.schema_migrations') IS NOT NULL").fetchone()
    if exists is None or not exists[0]:
        return None
    rows = conn.execute(
        "SELECT version, name, sha256 FROM prks.schema_migrations ORDER BY version"
    ).fetchall()
    return [AppliedFile(int(v), str(n), str(s)) for v, n, s in rows]


def _check_applied(row: AppliedFile, current: SchemaFile | None, newest: int) -> None:
    """Refuse one applied ledger row that the files on disk do not reproduce."""
    if current is None and row.version > newest:
        raise MigrationError(
            "newer_schema",
            f"The database has schema version {row.version:03d}, newer than this "
            f"PRKS ({newest:03d}). Run the PRKS release that migrated it.",
        )
    if current is None:
        raise MigrationError(
            "unknown_schema_file",
            f"The database applied schema version {row.version:03d}, which this PRKS "
            f"does not have. {_RECREATE_HINT}",
        )
    if current.name != row.name or current.sha256 != row.sha256:
        raise MigrationError(
            "schema_file_changed",
            f"Schema file {current.filename} changed after this database applied it. "
            f"{_RECREATE_HINT}",
        )


def plan(files: Sequence[SchemaFile], applied: Sequence[AppliedFile]) -> list[SchemaFile]:
    """The files still to apply, after refusing every inconsistent state.

    Refusals, checked in this order:

    - ``newer_schema``: the database applied a version this code does not have
      above its newest file (it was migrated by newer code);
    - ``unknown_schema_file``: an applied version this code does not have below
      its newest file (a file was removed or renumbered);
    - ``schema_file_changed``: an applied file's name or SHA-256 differs from the
      file on disk (a baseline file was edited after this database applied it);
    - ``out_of_order``: a pending file sorts before an applied one, so applying
      it would not reproduce a fresh database.
    """
    by_version = {f.version: f for f in files}
    newest = files[-1].version if files else -1
    for row in applied:
        _check_applied(row, by_version.get(row.version), newest)
    applied_versions = {row.version for row in applied}
    pending = [f for f in files if f.version not in applied_versions]
    if pending and applied and pending[0].version < max(applied_versions):
        raise MigrationError(
            "out_of_order",
            f"Schema file {pending[0].filename} sorts before files this database already "
            f"applied. {_RECREATE_HINT}",
        )
    return pending


def _connect(conninfo: str, lock_timeout_ms: int) -> psycopg.Connection[Any]:
    try:
        conn = psycopg.connect(conninfo, autocommit=True, application_name=APPLICATION_NAME)
    except psycopg.Error:
        raise MigrationError("database_unavailable", "Cannot connect to the database.") from None
    try:
        # An empty search_path makes every unqualified name in a schema file an
        # error instead of a silent write to whatever schema comes first.
        conn.execute("SELECT set_config('search_path', '', false)")
        conn.execute("SELECT set_config('lock_timeout', %s, false)", (str(int(lock_timeout_ms)),))
        # lock_timeout is the runner's only bound: a statement_timeout or
        # transaction_timeout inherited from the role or database (common on
        # managed servers) must not cancel the lock wait or a long file.
        conn.execute("SELECT set_config('statement_timeout', '0', false)")
        conn.execute("SELECT set_config('transaction_timeout', '0', false)")
    except psycopg.Error:
        conn.close()
        raise MigrationError("database_unavailable", "Cannot configure the database session.") from None
    return conn


def _lock(conn: psycopg.Connection[Any]) -> None:
    try:
        conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
    except (psycopg.errors.LockNotAvailable, psycopg.errors.QueryCanceled):
        raise MigrationError(
            "lock_timeout", "Another schema migration holds the lock; try again later."
        ) from None
    except psycopg.Error:
        # The server restarted or the waiting session was terminated.
        raise MigrationError(
            "database_unavailable", "Lost the database connection while waiting for the lock."
        ) from None


# initdb creates every built-in object below this OID; anything created later
# (by a user, an extension or a restore) gets one at or above it.
_FIRST_NORMAL_OBJECT_ID = 16384


def _is_empty(conn: psycopg.Connection[Any]) -> bool:
    """True when no object was created in this database after initdb.

    Every per-database catalog with an ``oid`` column is checked, so a
    collation, operator, cast, event trigger, publication or large object
    counts as well as tables, functions and types. Leftover temporary
    namespaces and a re-created or dropped ``public`` schema do not.
    """
    catalogs = [
        str(name)
        for (name,) in conn.execute(
            """
            SELECT c.relname
              FROM pg_catalog.pg_class c
              JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attname = 'oid'
             WHERE c.relnamespace = 'pg_catalog'::regnamespace
               AND c.relkind = 'r'
               AND NOT c.relisshared
             ORDER BY c.relname
            """
        ).fetchall()
    ]
    probes: list[sql.Composable] = []
    for name in catalogs:
        probe = sql.SQL("SELECT 1 FROM pg_catalog.{} WHERE oid >= {}").format(
            sql.Identifier(name), sql.Literal(_FIRST_NORMAL_OBJECT_ID)
        )
        if name == "pg_namespace":
            probe = sql.SQL(
                "{} AND nspname <> 'public' AND nspname !~ '^pg_(toast_)?temp_[0-9]+$'"
            ).format(probe)
        probes.append(sql.SQL("EXISTS ({})").format(probe))
    row = conn.execute(
        sql.SQL("SELECT NOT ({})").format(sql.SQL(" OR ").join(probes))
    ).fetchone()
    return bool(row is not None and row[0])


def _ensure_ledger(conn: psycopg.Connection[Any]) -> list[AppliedFile]:
    applied = _applied(conn)
    if applied is not None:
        return applied
    if not _is_empty(conn):
        raise MigrationError(
            "foreign_schema",
            "The database already holds objects and has no prks.schema_migrations; "
            "the runner only initializes an empty database.",
        )
    with conn.transaction():
        conn.execute("CREATE SCHEMA prks")
        conn.execute(
            """
            CREATE TABLE prks.schema_migrations (
              version    integer PRIMARY KEY CHECK (version >= 0),
              name       text NOT NULL,
              sha256     text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
              applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    return []


def _apply(conn: psycopg.Connection[Any], schema_file: SchemaFile) -> None:
    try:
        with conn.transaction():
            # The file is passed as bytes and without parameters, so psycopg
            # sends it unchanged as one simple-protocol multi-statement query.
            conn.execute(schema_file.body)
            conn.execute(
                "INSERT INTO prks.schema_migrations (version, name, sha256) VALUES (%s, %s, %s)",
                (schema_file.version, schema_file.name, schema_file.sha256),
            )
    except psycopg.errors.LockNotAvailable:
        raise MigrationError(
            "lock_timeout", f"Schema file {schema_file.filename} timed out waiting for a lock."
        ) from None
    except psycopg.Error as exc:
        # SQLSTATE only: the server's message can quote row values (an invalid
        # cast embeds its input), and a forward migration may touch library
        # data, so no driver text reaches the error or the log.
        sqlstate = exc.sqlstate or "unknown"
        raise MigrationError(
            "schema_file_failed",
            f"Schema file {schema_file.filename} failed (SQLSTATE {sqlstate}); "
            "nothing from it was applied.",
        ) from None


def migrate(
    conninfo: str,
    *,
    directory: str = SCHEMA_DIR,
    lock_timeout_ms: int = DEFAULT_LOCK_TIMEOUT_MS,
    before_apply: Callable[[SchemaFile], None] | None = None,
) -> MigrationResult:
    """Apply every pending schema file to the database at ``conninfo``.

    ``before_apply`` is a test seam called, under the lock, before each file.
    """
    files = load_schema_files(directory)
    conn = _connect(conninfo, lock_timeout_ms)
    try:
        _lock(conn)
        try:
            applied = _ensure_ledger(conn)
        except psycopg.Error:
            raise MigrationError(
                "schema_file_failed", "Creating prks.schema_migrations failed."
            ) from None
        pending = plan(files, applied)
        done: list[str] = []
        for schema_file in pending:
            if before_apply is not None:
                before_apply(schema_file)
            _apply(conn, schema_file)
            done.append(schema_file.filename)
        return MigrationResult(applied=tuple(done), current=len(applied))
    finally:
        # Closing the session releases the advisory lock on every path.
        conn.close()


def verify_schema(conninfo: str, *, directory: str = SCHEMA_DIR) -> None:
    """Startup validation (§4.4): the database applied exactly the files on disk.

    Raises ``MigrationError`` with ``not_migrated`` (empty database) or
    ``pending`` (files not applied yet), or any refusal of :func:`plan`.
    Reads only ``prks.schema_migrations``.
    """
    files = load_schema_files(directory)
    conn = _connect(conninfo, DEFAULT_LOCK_TIMEOUT_MS)
    try:
        try:
            applied = _applied(conn)
        except psycopg.Error:
            raise MigrationError("database_unavailable", "Cannot read the schema version.") from None
    finally:
        conn.close()
    if applied is None or not applied:
        raise MigrationError("not_migrated", "The database has no PRKS schema; run migrate.")
    pending = plan(files, applied)
    if pending:
        raise MigrationError(
            "pending", f"Schema file {pending[0].filename} is not applied yet; run migrate."
        )


def main(argv: Sequence[str] | None = None) -> int:
    from backend.db.config import load_database_config
    from backend.db.errors import DatabaseConfigError

    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print("usage: python -m backend.db.migrate", file=sys.stderr)
        return 2
    try:
        config = load_database_config(role="web")
        result = migrate(config.conninfo)
    except DatabaseConfigError as exc:
        print(f"migrate: {exc.message}", file=sys.stderr)
        return 2
    except MigrationError as exc:
        print(f"migrate: {exc.message}", file=sys.stderr)
        return 1
    for filename in result.applied:
        print(f"migrate: applied {filename}")
    if not result.applied:
        print("migrate: schema is up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
