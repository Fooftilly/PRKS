"""Proof that a ``--testing`` process was given a testing database (PG-C2 #641).

A testing process applies the schema on start and, on a fresh database,
bootstraps an owner itself. Neither may ever happen to a real Library, so
before a ``--testing`` process writes anything it proves, with read-only
statements, that its database is a testing resource
(``docs/postgresql-architecture.md`` §13.1, R11):

- The database carries a testing mark in ``COMMENT ON DATABASE``:
  :data:`TESTING_MARK`, which ``scripts/dev-postgres`` sets, or the test
  harness's ``prks-test-harness:<run>`` ownership mark
  (``tests/pg/harness.py``). A database created any other way has neither,
  so a URL that names a real Library by mistake is refused even before its
  instance row exists.
- If the database already holds an instance, every row says ``testing``.
  The start check refuses the mismatch too (``root_pairing``), but only
  after the schema step; this check runs before it.

A database someone created by hand only for testing is marked with
``COMMENT ON DATABASE <name> IS 'prks-testing'``. Nothing here marks a
database: a process never decides on its own that a database is disposable.
"""

from __future__ import annotations

import psycopg

TESTING_MARK = "prks-testing"
HARNESS_MARK_PREFIX = "prks-test-harness:"

_APPLICATION_NAME = "prks-testing-guard"


class TestingDatabaseRefused(Exception):
    """The database is not proven to be a testing database; ``message`` says why and what to do."""

    __test__ = False  # not a unittest/pytest test class

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def is_testing_mark(comment: object) -> bool:
    """Whether a ``COMMENT ON DATABASE`` value marks a testing database."""
    if not isinstance(comment, str):
        return False
    return comment == TESTING_MARK or (
        comment.startswith(HARNESS_MARK_PREFIX) and len(comment) > len(HARNESS_MARK_PREFIX)
    )


def prove_testing_database(conninfo: str) -> None:
    """Return only when ``conninfo`` names a testing database; raise :class:`TestingDatabaseRefused` otherwise.

    Runs read-only statements on one short connection and writes nothing.
    """
    try:
        conn = psycopg.connect(conninfo, application_name=_APPLICATION_NAME)
    except psycopg.Error:
        raise TestingDatabaseRefused(
            "database_unavailable",
            "Cannot connect to the database. For --testing, start the local database with "
            "scripts/dev-postgres, or set PRKS_DATABASE_URL to a database marked for testing.",
        ) from None
    try:
        # One read-only transaction, rolled back: the proof cannot write.
        conn.read_only = True
        name, comment = conn.execute(
            "SELECT datname, shobj_description(oid, 'pg_database') FROM pg_database "
            "WHERE datname = current_database()"
        ).fetchone() or (None, None)
        if not is_testing_mark(comment):
            raise TestingDatabaseRefused(
                "not_a_testing_database",
                f"--testing only uses a database marked for testing, and {name!s} is not. "
                "Start the local testing database with scripts/dev-postgres, or, for a database "
                "you created only for testing, run "
                f"COMMENT ON DATABASE {_quoted(name)} IS '{TESTING_MARK}'. "
                "Never mark a database that holds a real Library.",
            )
        has_instance = conn.execute("SELECT to_regclass('prks.instance') IS NOT NULL").fetchone()
        if has_instance and has_instance[0]:
            row = conn.execute(
                "SELECT count(*) FILTER (WHERE NOT testing) FROM prks.instance"
            ).fetchone()
            if row and row[0]:
                raise TestingDatabaseRefused(
                    "testing_mismatch",
                    f"{name!s} is marked for testing but holds a non-testing PRKS instance; "
                    "--testing never serves it. Use another database.",
                )
    except psycopg.Error:
        raise TestingDatabaseRefused(
            "database_unavailable", "Could not check that the database is a testing database."
        ) from None
    finally:
        try:
            conn.rollback()
        except psycopg.Error:
            pass
        conn.close()


def _quoted(name: object) -> str:
    text = str(name)
    return '"' + text.replace('"', '""') + '"'
