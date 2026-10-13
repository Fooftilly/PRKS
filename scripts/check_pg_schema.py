#!/usr/bin/env python3
"""PostgreSQL schema snapshot gate (``docs/postgresql-architecture.md`` §4.4, §13.2).

Builds a fresh database from ``backend/db/schema/*.sql`` with the PRKS
migration runner and compares its normalized catalog with the checked-in
``backend/db/schema/snapshot.json``. It also fails when anything stored
depends on ``prks.search_norm`` (§8.3).

Codes:

- PG-SCHEMA-001: the schema files are malformed (name, duplicate version, not UTF-8).
- PG-SCHEMA-002: the snapshot's file list differs from the files on disk.
- PG-SCHEMA-003: the fresh catalog differs from the snapshot.
- PG-SCHEMA-004: an index, column default, generated column or constraint
  depends on ``prks.search_norm``.

The catalog comparison needs a disposable PostgreSQL 18 server: set
``PRKS_TEST_DATABASE_URL`` to its ``postgres`` maintenance database
(``scripts/test-postgres`` documents the options). The database is created and
dropped by the test harness, which refuses any database it did not create.
The Test Gate runs the same comparison as tests/pg/test_schema_snapshot.py
inside ``scripts/test-postgres``.

Usage:
  python scripts/check_pg_schema.py            # check
  python scripts/check_pg_schema.py --write    # regenerate snapshot.json after a reviewed schema change
  python scripts/check_pg_schema.py --static-only   # PG-SCHEMA-001/002 only, no server
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.db.migrate import MigrationError, load_schema_files  # noqa: E402
from backend.db.schema_snapshot import SNAPSHOT_PATH, dumps  # noqa: E402


def _fail(code: str, message: str) -> None:
    print(f"{code}: {message}", file=sys.stderr)


def _static_checks() -> bool:
    try:
        files = load_schema_files()
    except MigrationError as exc:
        _fail("PG-SCHEMA-001", exc.message)
        return False
    try:
        with open(SNAPSHOT_PATH, encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except (OSError, ValueError):
        _fail("PG-SCHEMA-002", "backend/db/schema/snapshot.json is missing or not JSON.")
        return False
    expected = [f.filename for f in files]
    if snapshot.get("schema_files") != expected:
        _fail(
            "PG-SCHEMA-002",
            "snapshot.json lists other schema files than backend/db/schema/; "
            "run scripts/check_pg_schema.py --write.",
        )
        return False
    return True


def build_fresh_snapshot() -> tuple[dict[str, object], list[str]]:
    """Catalog snapshot and search_norm dependents of a freshly migrated database."""
    import psycopg

    from backend.db.migrate import migrate
    from backend.db.schema_snapshot import catalog_snapshot, search_norm_dependents
    from tests.pg.harness import PostgresHarness, admin_conninfo_from_env

    admin = admin_conninfo_from_env()
    if admin is None:
        raise SystemExit(
            "check_pg_schema: set PRKS_TEST_DATABASE_URL to a disposable PostgreSQL 18 "
            "server's postgres database (see scripts/test-postgres), or pass --static-only."
        )
    harness = PostgresHarness(admin)
    try:
        name = harness.create_database("schema", empty=True)
        migrate(harness.conninfo(name))
        with psycopg.connect(harness.conninfo(name)) as conn:
            return catalog_snapshot(conn), search_norm_dependents(conn)
    finally:
        harness.drop_all()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PostgreSQL schema snapshot gate.")
    parser.add_argument("--write", action="store_true", help="rewrite snapshot.json")
    parser.add_argument("--static-only", action="store_true", help="skip the catalog comparison")
    args = parser.parse_args(argv)

    if args.write:
        try:
            load_schema_files()
        except MigrationError as exc:
            _fail("PG-SCHEMA-001", exc.message)
            return 1
        snapshot, dependents = build_fresh_snapshot()
        if dependents:
            _fail("PG-SCHEMA-004", "stored objects depend on prks.search_norm: " + ", ".join(dependents))
            return 1
        with open(SNAPSHOT_PATH, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(dumps(snapshot))
        print("check_pg_schema: wrote backend/db/schema/snapshot.json")
        return 0

    if not _static_checks():
        return 1
    if args.static_only:
        print("check_pg_schema: static checks passed")
        return 0

    snapshot, dependents = build_fresh_snapshot()
    ok = True
    if dependents:
        _fail("PG-SCHEMA-004", "stored objects depend on prks.search_norm: " + ", ".join(dependents))
        ok = False
    with open(SNAPSHOT_PATH, encoding="utf-8") as handle:
        expected_text = handle.read()
    actual_text = dumps(snapshot)
    if actual_text != expected_text:
        diff = difflib.unified_diff(
            expected_text.splitlines(keepends=True),
            actual_text.splitlines(keepends=True),
            fromfile="snapshot.json (checked in)",
            tofile="fresh database",
            n=2,
        )
        _fail(
            "PG-SCHEMA-003",
            "the fresh schema differs from backend/db/schema/snapshot.json; review the "
            "diff and run scripts/check_pg_schema.py --write:\n" + "".join(diff),
        )
        ok = False
    if ok:
        print("check_pg_schema: fresh schema matches snapshot.json")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
