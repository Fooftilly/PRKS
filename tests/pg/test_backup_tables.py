"""Backup table classification and the backup/restore run tables (L-B-1 #639; §11.4, §13.3).

"Backup table classification": every library-scoped table is on exactly one
of the content, excluded and derived lists; every content column has an
interchange type and generated columns are never exported; every content
foreign key has a staging route; and the content list is in dependency order
for every key that cannot be deferred. Then the run tables' own rules
(``170_backup.sql``).
"""

from __future__ import annotations

import psycopg

from backend.domain.maintenance import tables
from backend.domain.maintenance.tables import (
    BACKUP_CONTENT_TABLES,
    BACKUP_DERIVED_TABLES,
    BACKUP_EXCLUDED_TABLES,
    ParentRoute,
    UnroutedForeignKey,
    UnsupportedColumnType,
)
from tests.pg.harness import PostgresTestCase, fetch_one

LIB = "LIB-" + "1" * 32
OTHER_LIB = "LIB-" + "2" * 32


class BackupTableClassificationTests(PostgresTestCase):
    def setUp(self) -> None:
        self.conn = self.connect()
        self.addCleanup(self.conn.rollback)

    def test_every_library_scoped_table_is_on_exactly_one_list(self) -> None:
        lists = (set(BACKUP_CONTENT_TABLES), set(BACKUP_EXCLUDED_TABLES), set(BACKUP_DERIVED_TABLES))
        self.assertEqual(len(BACKUP_CONTENT_TABLES), len(lists[0]), "a content table is listed twice")
        for i, first in enumerate(lists):
            for second in lists[i + 1:]:
                self.assertEqual(first & second, set())
        classified = set().union(*lists)
        scoped = set(tables.library_scoped_tables(self.conn))
        self.assertEqual(scoped - classified, set(), "library-scoped tables on no backup list")
        # Every listed table exists. restore_run_objects is the one listed
        # table without a library_id (it belongs to its run).
        self.assertEqual(classified - scoped, {"prks.restore_run_objects"})
        for table in classified:
            with self.subTest(table=table):
                self.assertIsNotNone(fetch_one(self.conn.execute("SELECT to_regclass(%s)", (table,)))[0])

    def test_access_identity_and_operational_rows_are_never_content(self) -> None:
        for table in ("prks.library_memberships", "prks.sessions", "prks.jobs", "prks.backup_runs",
                      "prks.restore_runs", "prks.restore_run_objects", "prks.libraries", "prks.users",
                      "prks.devices", "prks.login_device_tokens", "prks.bootstrap_tokens",
                      "prks.instance", "prks.instance_settings"):
            with self.subTest(table=table):
                self.assertNotIn(table, BACKUP_CONTENT_TABLES)
        self.assertTrue(all(t.startswith("derived.") for t in BACKUP_DERIVED_TABLES))
        self.assertFalse(any(t.startswith("derived.") for t in BACKUP_CONTENT_TABLES))

    def test_every_content_column_has_an_interchange_type(self) -> None:
        contract = tables.interchange_contract(self.conn)
        self.assertEqual(list(contract), list(BACKUP_CONTENT_TABLES))
        vocabulary = {"text", "int64", "bool", "timestamptz", "json", "uuid", "bytes"}
        for table, columns in contract.items():
            with self.subTest(table=table):
                self.assertIn("library_id", [c["column"] for c in columns])
                self.assertLessEqual({c["type"] for c in columns}, vocabulary)

    def test_generated_columns_are_never_exported(self) -> None:
        generated = self.conn.execute(
            "SELECT n.nspname || '.' || c.relname, a.attname FROM pg_attribute a "
            "JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'prks' AND a.attgenerated <> '' AND NOT a.attisdropped").fetchall()
        content_generated = [(t, col) for t, col in generated if t in BACKUP_CONTENT_TABLES]
        # The identity keys of §8.1, such as tags.name_key, and the key rows' kind.
        self.assertIn(("prks.tags", "name_key"), content_generated)
        self.assertIn(("prks.tag_keys", "kind"), content_generated)
        for table, column in content_generated:
            with self.subTest(table=table, column=column):
                self.assertNotIn(column, [c.name for c in tables.exported_columns(self.conn, table)])

    def test_unsupported_types_are_refused_not_copied_as_text(self) -> None:
        with self.assertRaises(UnsupportedColumnType):
            tables.interchange_type("tsvector")
        with self.assertRaises(UnsupportedColumnType):
            tables.interchange_type("numeric")
        with self.assertRaises(ValueError):
            tables.exported_columns(self.conn, "prks.sessions")

    def test_every_content_foreign_key_has_a_staging_route(self) -> None:
        routes = {route: 0 for route in ParentRoute}
        for table in BACKUP_CONTENT_TABLES:
            for fk in tables.foreign_keys(self.conn, table):
                with self.subTest(fk=fk.name):
                    routes[fk.route] += 1
        # Each rule is in use: content parents, devices (the ledger), the
        # Library and users, and the global verdicts.
        self.assertTrue(all(routes.values()), routes)
        with self.assertRaises(UnroutedForeignKey):
            tables.parent_route("prks.sessions")

    def test_content_tables_are_in_dependency_order_for_immediate_keys(self) -> None:
        position = {table: i for i, table in enumerate(BACKUP_CONTENT_TABLES)}
        for table in BACKUP_CONTENT_TABLES:
            for fk in tables.foreign_keys(self.conn, table):
                if fk.deferrable or fk.route is not ParentRoute.STAGED or fk.parent == table:
                    continue
                with self.subTest(fk=fk.name):
                    self.assertLess(position[fk.parent], position[table],
                                    f"{fk.parent} must come before {table}")

    def test_attribution_identities_are_the_ledger_and_uploader_keys(self) -> None:
        found = tables.identity_columns(self.conn)
        users = {(fk.table, fk.columns) for fk in found["prks.users"]}
        devices = {(fk.table, fk.columns) for fk in found["prks.devices"]}
        self.assertEqual(users, {("prks.sync_operations", ("user_id",)),
                                 ("prks.processing_items", ("uploaded_by",))})
        self.assertEqual(devices, {("prks.sync_operations", ("device_id",))})


class RunTablesTests(PostgresTestCase):
    def setUp(self) -> None:
        self.conn = self.connect()
        self.addCleanup(self.conn.rollback)
        for lib in (LIB, OTHER_LIB):
            self.conn.execute("INSERT INTO prks.libraries (id, name) VALUES (%s, 'L')", (lib,))

    def refused(self, error: type[psycopg.Error], statement: str, params: tuple[object, ...] = ()) -> None:
        with self.assertRaises(error):
            with self.conn.transaction():
                self.conn.execute(statement.encode(), params)

    def test_a_restore_run_starts_validating_with_a_lease(self) -> None:
        row = fetch_one(self.conn.execute(
            "INSERT INTO prks.restore_runs (library_id) VALUES (%s) "
            "RETURNING state, phase, locked_until > now(), error_code", (LIB,)))
        self.assertEqual(row, ("running", "validate", True, None))

    def test_only_a_failed_run_carries_an_error(self) -> None:
        for table in ("prks.restore_runs", "prks.backup_runs"):
            with self.subTest(table=table):
                run = fetch_one(self.conn.execute(
                    f"INSERT INTO {table} (library_id) VALUES (%s) RETURNING id".encode(), (OTHER_LIB,)))[0]
                self.refused(psycopg.errors.CheckViolation,
                             f"UPDATE {table} SET error_code = 'x' WHERE id = %s", (run,))
                self.refused(psycopg.errors.CheckViolation,
                             f"UPDATE {table} SET state = 'failed', finished_at = now(), "
                             "error_message = 'no code' WHERE id = %s", (run,))
                self.refused(psycopg.errors.CheckViolation,
                             f"UPDATE {table} SET state = 'failed', finished_at = now(), "
                             "error_code = 'not a code!' WHERE id = %s", (run,))
                self.conn.execute(f"DELETE FROM {table} WHERE id = %s".encode(), (run,))

    def test_restore_phases_are_the_protocol_phases(self) -> None:
        run = fetch_one(self.conn.execute(
            "INSERT INTO prks.restore_runs (library_id) VALUES (%s) RETURNING id", (LIB,)))[0]
        for phase in ("validate", "stage", "enter", "drain", "commit", "release"):
            self.conn.execute("UPDATE prks.restore_runs SET phase = %s WHERE id = %s", (phase, run))
        self.refused(psycopg.errors.CheckViolation,
                     "UPDATE prks.restore_runs SET phase = 'R4' WHERE id = %s", (run,))

    def test_a_backup_is_published_only_with_its_verified_archive(self) -> None:
        run = fetch_one(self.conn.execute(
            "INSERT INTO prks.backup_runs (library_id) VALUES (%s) RETURNING id", (LIB,)))[0]
        self.refused(psycopg.errors.CheckViolation,
                     "UPDATE prks.backup_runs SET state = 'published', finished_at = now() WHERE id = %s",
                     (run,))
        self.refused(psycopg.errors.CheckViolation,
                     "UPDATE prks.backup_runs SET archive_bytes = 1, archive_sha256 = %s WHERE id = %s",
                     ("a" * 64, run))
        self.refused(psycopg.errors.CheckViolation,
                     "UPDATE prks.backup_runs SET state = 'published', finished_at = now(), "
                     "archive_bytes = 1, archive_sha256 = 'nothex' WHERE id = %s", (run,))
        self.conn.execute("UPDATE prks.backup_runs SET state = 'published', finished_at = now(), "
                          "archive_bytes = 10, archive_sha256 = %s WHERE id = %s", ("a" * 64, run))

    def test_run_rows_go_with_their_library(self) -> None:
        self.conn.execute("INSERT INTO prks.backup_runs (library_id) VALUES (%s)", (OTHER_LIB,))
        self.conn.execute("INSERT INTO prks.restore_runs (library_id) VALUES (%s)", (OTHER_LIB,))
        self.conn.execute("DELETE FROM prks.libraries WHERE id = %s", (OTHER_LIB,))
        for table in ("prks.backup_runs", "prks.restore_runs"):
            self.assertEqual(fetch_one(self.conn.execute(
                f"SELECT count(*) FROM {table} WHERE library_id = %s".encode(), (OTHER_LIB,)))[0], 0)
