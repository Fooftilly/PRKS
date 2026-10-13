"""Disposable PostgreSQL databases for tests (``docs/postgresql-architecture.md`` §13.1).

``PRKS_TEST_DATABASE_URL`` names a disposable PostgreSQL 18 server through its
``postgres`` maintenance database (``scripts/test-postgres`` provides one).
The harness never runs anything in that database except catalog reads and
``CREATE``/``COMMENT ON``/``DROP DATABASE``. Tests only ever receive
connection strings for databases this harness created in this run.

Ownership is marked with ``COMMENT ON DATABASE`` (``prks-test-harness:<run>``).
``instance.testing`` cannot carry it: the instance row exists only after
bootstrap claims a data root (§9.2, L-ID-1), and a migrated database has none.
The harness refuses:

- a maintenance URL whose database is not ``postgres``, or one equal to
  ``PRKS_DATABASE_URL``;
- a server older than PostgreSQL 18;
- any database name that already exists (it never adopts or reuses one);
- handing out, cloning or dropping a database whose marker is not this run's.

Every database uses the builtin ``PG_UNICODE_FAST`` locale (§8.2). Each run
creates ``prks_test_<run>_template`` once, migrated from
``backend/db/schema/`` by the PRKS runner, and every test class gets its own
``CREATE DATABASE … TEMPLATE`` clone, dropped when the class finishes. Tests of
the runner itself ask for an empty database instead (``empty=True``).
"""

from __future__ import annotations

import atexit
import os
import re
import secrets
import threading
import unittest
from typing import Any, LiteralString

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ENV_TEST_DATABASE_URL = "PRKS_TEST_DATABASE_URL"
MAINTENANCE_DATABASE = "postgres"
MARKER_PREFIX = "prks-test-harness:"
MIN_SERVER_VERSION_NUM = 180000
DATABASE_PREFIX = "prks_test_"

_NAME_RE = re.compile(r"^prks_test_[0-9a-f]{12}_[a-z0-9_]{1,30}$")


# Raise a SQLSTATE on demand (constant statements, one per code the tests inject).
INJECT: dict[str, LiteralString] = {
    "40001": "DO $$ BEGIN RAISE EXCEPTION 'injected' USING ERRCODE = '40001'; END $$",
    "40P01": "DO $$ BEGIN RAISE EXCEPTION 'injected' USING ERRCODE = '40P01'; END $$",
}


def fetch_one(cursor: psycopg.Cursor[Any]) -> Any:
    """The cursor's next row; a missing row fails the test."""
    row = cursor.fetchone()
    if row is None:
        raise AssertionError("expected a row")
    return row


def compose(*parts: sql.Composable) -> sql.Composed:
    """One statement from SQL keywords and psycopg-quoted identifiers/literals."""
    return sql.Composed(list(parts))


class HarnessRefusal(RuntimeError):
    """The harness refuses to touch a database it cannot prove it owns."""


def _conninfo_dbname(conninfo: str) -> str | None:
    value = conninfo_to_dict(conninfo).get("dbname")
    return None if value is None else str(value)


class PostgresHarness:
    """Creates, hands out and drops the databases of one test run."""

    def __init__(self, admin_conninfo: str, *, run_id: str | None = None) -> None:
        dbname = _conninfo_dbname(admin_conninfo)
        if dbname != MAINTENANCE_DATABASE:
            raise HarnessRefusal(
                f"{ENV_TEST_DATABASE_URL} must name the '{MAINTENANCE_DATABASE}' "
                "maintenance database of a disposable server"
            )
        live_url = os.environ.get("PRKS_DATABASE_URL", "").strip()
        if live_url and live_url == admin_conninfo.strip():
            raise HarnessRefusal(f"{ENV_TEST_DATABASE_URL} must not equal PRKS_DATABASE_URL")
        self._admin_conninfo = admin_conninfo
        self.run_id = run_id or secrets.token_hex(6)
        if not re.fullmatch(r"[0-9a-f]{12}", self.run_id):
            raise ValueError("run_id must be 12 lowercase hex digits")
        self.marker = f"{MARKER_PREFIX}{self.run_id}"
        self._lock = threading.Lock()
        self._counter = 0
        self._created: set[str] = set()
        self._template: str | None = None
        with self._admin() as conn:
            row = conn.execute("SELECT current_setting('server_version_num')::int").fetchone()
            if row is None or int(row[0]) < MIN_SERVER_VERSION_NUM:
                raise HarnessRefusal("the test server must be PostgreSQL 18 or newer")

    # -- admin connection ---------------------------------------------------

    def _admin(self) -> psycopg.Connection[Any]:
        return psycopg.connect(
            self._admin_conninfo, autocommit=True, application_name="prks-test-harness"
        )

    # -- names and markers --------------------------------------------------

    def _new_name(self, label: str) -> str:
        with self._lock:
            self._counter += 1
            name = f"{DATABASE_PREFIX}{self.run_id}_{label}{self._counter}"
        if not _NAME_RE.match(name):
            raise ValueError("invalid test database label")
        return name

    def marker_of(self, name: str) -> str | None:
        """The database's comment, or ``None`` when it has none or does not exist."""
        with self._admin() as conn:
            row = conn.execute(
                "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = %s",
                (name,),
            ).fetchone()
        return None if row is None else row[0]

    def exists(self, name: str) -> bool:
        with self._admin() as conn:
            row = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        return row is not None

    def verify_owned(self, name: str) -> None:
        """Refuse unless ``name`` was created by this harness in this run."""
        if name not in self._created or self.marker_of(name) != self.marker:
            raise HarnessRefusal("the database was not created by this test run")

    # -- lifecycle -----------------------------------------------------------

    def _create(self, name: str, template: str) -> None:
        with self._admin() as conn:
            if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
                raise HarnessRefusal("refusing to use a pre-existing database")
            create = compose(
                sql.SQL("CREATE DATABASE "), sql.Identifier(name),
                sql.SQL(" TEMPLATE "), sql.Identifier(template),
            )
            if template == "template0":
                create = compose(
                    sql.SQL("CREATE DATABASE "), sql.Identifier(name),
                    sql.SQL(
                        " TEMPLATE template0 ENCODING 'UTF8' "
                        "LOCALE_PROVIDER builtin BUILTIN_LOCALE 'PG_UNICODE_FAST'"
                    ),
                )
            try:
                conn.execute(create)
            except psycopg.errors.DuplicateDatabase:
                raise HarnessRefusal("refusing to use a pre-existing database") from None
            self._created.add(name)
            conn.execute(
                compose(
                    sql.SQL("COMMENT ON DATABASE "), sql.Identifier(name),
                    sql.SQL(" IS "), sql.Literal(self.marker),
                )
            )

    def template(self) -> str:
        """The run's template database, created on first use."""
        with self._lock:
            existing = self._template
        if existing is not None:
            return existing
        name = self._new_name("template")
        self._create(name, "template0")
        from backend.db.migrate import migrate

        migrate(self.conninfo(name))
        with self._lock:
            self._template = name
        return name

    def create_database(self, label: str = "c", *, empty: bool = False) -> str:
        """A fresh database: a clone of the migrated template, or ``empty``."""
        name = self._new_name(label)
        if empty:
            self._create(name, "template0")
            return name
        template = self.template()
        self.verify_owned(template)
        self._create(name, template)
        return name

    def conninfo(self, name: str) -> str:
        """Connection string for a database this run owns."""
        self.verify_owned(name)
        return make_conninfo(self._admin_conninfo, dbname=name)

    def drop_database(self, name: str) -> None:
        self.verify_owned(name)
        with self._admin() as conn:
            conn.execute(
                compose(
                    sql.SQL("DROP DATABASE IF EXISTS "), sql.Identifier(name), sql.SQL(" WITH (FORCE)")
                )
            )
        self._created.discard(name)
        with self._lock:
            if self._template == name:
                self._template = None

    def drop_all(self) -> None:
        """Drop every database of this run (clones first, the template last)."""
        with self._lock:
            template = self._template
        for name in sorted(self._created - {template} if template else self._created):
            if self.exists(name):
                self.drop_database(name)
            else:
                self._created.discard(name)
        if template is not None and template in self._created:
            self.drop_database(template)


_HARNESS: PostgresHarness | None = None
_HARNESS_LOCK = threading.Lock()


def admin_conninfo_from_env() -> str | None:
    value = os.environ.get(ENV_TEST_DATABASE_URL, "").strip()
    return value or None


def get_harness() -> PostgresHarness:
    """The process-wide harness; its databases are dropped at exit."""
    global _HARNESS
    with _HARNESS_LOCK:
        if _HARNESS is None:
            url = admin_conninfo_from_env()
            if url is None:
                raise unittest.SkipTest(f"{ENV_TEST_DATABASE_URL} is not set")
            _HARNESS = PostgresHarness(url)
            atexit.register(_HARNESS.drop_all)
        return _HARNESS


class PostgresTestCase(unittest.TestCase):
    """Gives each test class its own harness-created database.

    ``cls.harness``, ``cls.database_name`` and ``cls.database_conninfo`` are
    set in ``setUpClass``; the database is dropped after the class.
    """

    harness: PostgresHarness
    database_name: str
    database_conninfo: str

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.harness = get_harness()
        cls.database_name = cls.harness.create_database()
        cls.addClassCleanup(cls.harness.drop_database, cls.database_name)
        cls.database_conninfo = cls.harness.conninfo(cls.database_name)

    def connect(self, **kwargs: Any) -> psycopg.Connection[Any]:
        """A direct connection to this class's database, closed after the test."""
        conn = psycopg.connect(self.harness.conninfo(self.database_name), **kwargs)
        self.addCleanup(conn.close)
        return conn
