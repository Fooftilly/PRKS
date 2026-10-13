"""The schema migration runner against real PostgreSQL (#624, §4.4).

Each test gets its own empty harness database, so the runner starts from the
state a fresh install has.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import Callable

import psycopg
from psycopg.conninfo import make_conninfo

from backend.db.migrate import (
    MIGRATION_LOCK_KEY,
    SCHEMA_DIR,
    MigrationError,
    MigrationResult,
    SchemaFile,
    load_schema_files,
    migrate,
    verify_schema,
)
from tests.pg.harness import get_harness

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class EmptyDatabaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = get_harness()
        self.name = self.harness.create_database("mig", empty=True)
        self.addCleanup(self.harness.drop_database, self.name)
        self.conninfo = self.harness.conninfo(self.name)

    def query(self, statement: str, params: tuple[object, ...] | None = None) -> list[tuple[object, ...]]:
        with psycopg.connect(self.conninfo, autocommit=True) as conn:
            cursor = conn.execute(statement.encode(), params)
            return list(cursor.fetchall()) if cursor.description is not None else []

    def ledger(self) -> list[tuple[object, ...]]:
        return self.query("SELECT version, name, sha256 FROM prks.schema_migrations ORDER BY version")

    def schema_copy(self) -> str:
        directory = tempfile.mkdtemp(prefix="prks-schema-")
        self.addCleanup(shutil.rmtree, directory, True)
        for entry in os.listdir(SCHEMA_DIR):
            if entry.endswith(".sql"):
                shutil.copy(os.path.join(SCHEMA_DIR, entry), directory)
        return directory

    def write(self, directory: str, filename: str, body: str) -> None:
        with open(os.path.join(directory, filename), "w", encoding="utf-8") as handle:
            handle.write(body)

    def assert_refused(
        self, reason: str, fn: Callable[..., object], *args: object, **kwargs: object
    ) -> MigrationError:
        with self.assertRaises(MigrationError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.reason, reason, caught.exception.message)
        return caught.exception


class MigrateTests(EmptyDatabaseTestCase):
    def test_fresh_database_applies_every_file_once_in_order(self) -> None:
        files = load_schema_files()
        result = migrate(self.conninfo)
        self.assertEqual(result.applied, tuple(f.filename for f in files))
        self.assertEqual(result.current, 0)
        self.assertEqual(self.ledger(), [(f.version, f.name, f.sha256) for f in files])
        again = migrate(self.conninfo)
        self.assertEqual(again, MigrationResult(applied=(), current=len(files)))
        verify_schema(self.conninfo)

    def test_core_tables_exist_and_no_instance_row_is_created(self) -> None:
        migrate(self.conninfo)
        for table in (
            "prks.instance", "prks.users", "prks.libraries", "prks.library_memberships",
            "prks.sessions", "prks.jobs", "prks.devices", "prks.entity_revisions",
            "prks.sync_operations",
        ):
            with self.subTest(table):
                self.assertEqual(self.query("SELECT to_regclass(%s) IS NOT NULL", (table,)), [(True,)])
        self.assertEqual(self.query("SELECT count(*) FROM prks.instance"), [(0,)])
        columns = self.query(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'prks' AND table_name = 'instance' "
            "AND column_name IN ('start_generation', 'start_token', 'prev_start_token') "
            "ORDER BY column_name"
        )
        self.assertEqual(
            columns, [("prev_start_token", "YES"), ("start_generation", "NO"), ("start_token", "YES")]
        )

    def test_concurrent_runs_apply_each_file_once(self) -> None:
        files = load_schema_files()
        runners = 6
        barrier = threading.Barrier(runners)
        results: list[MigrationResult] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def run() -> None:
            try:
                barrier.wait(10)
                result = migrate(self.conninfo)
                with lock:
                    results.append(result)
            except BaseException as exc:  # noqa: BLE001 - reported below
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(runners)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), runners)
        applied = [name for result in results for name in result.applied]
        self.assertEqual(sorted(applied), sorted(f.filename for f in files))
        self.assertEqual(sum(1 for r in results if r.applied), 1)
        self.assertEqual(len(self.ledger()), len(files))

    def test_a_second_run_waits_for_the_lock_holder(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        first: list[MigrationResult] = []
        second: list[MigrationResult] = []

        def hold(schema_file: SchemaFile) -> None:
            if schema_file.version == 0:
                entered.set()
                release.wait(30)

        t1 = threading.Thread(target=lambda: first.append(migrate(self.conninfo, before_apply=hold)))
        t1.start()
        self.assertTrue(entered.wait(30))
        t2 = threading.Thread(target=lambda: second.append(migrate(self.conninfo)))
        t2.start()
        # The second runner is blocked on the advisory lock, not failing.
        waiting: list[tuple[object, ...]] = []
        for _ in range(200):
            waiting = self.query(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted "
                "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
            )
            if waiting == [(1,)]:
                break
            threading.Event().wait(0.05)
        self.assertEqual(waiting, [(1,)])
        self.assertEqual(second, [])
        release.set()
        t1.join(30)
        t2.join(30)
        self.assertEqual(len(first[0].applied), len(load_schema_files()))
        self.assertEqual(second, [MigrationResult(applied=(), current=len(load_schema_files()))])

    def test_lock_wait_is_bounded(self) -> None:
        with psycopg.connect(self.conninfo, autocommit=True) as holder:
            holder.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
            self.assert_refused("lock_timeout", migrate, self.conninfo, lock_timeout_ms=200)
        self.assertEqual(self.query("SELECT to_regnamespace('prks') IS NULL"), [(True,)])

    def test_inherited_statement_timeout_does_not_cut_the_lock_wait(self) -> None:
        # A role/database statement_timeout shorter than the lock wait is
        # neutralized; lock_timeout alone bounds the wait.
        conninfo = make_conninfo(self.conninfo, options="-c statement_timeout=200")
        with psycopg.connect(self.conninfo, autocommit=True) as holder:
            holder.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
            started = time.monotonic()
            self.assert_refused("lock_timeout", migrate, conninfo, lock_timeout_ms=1500)
            self.assertGreaterEqual(time.monotonic() - started, 1.2)
        self.assertEqual(migrate(conninfo).current, 0)

    def test_terminated_lock_wait_is_a_mapped_error(self) -> None:
        errors: list[BaseException] = []
        with psycopg.connect(self.conninfo, autocommit=True) as holder:
            holder.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))

            def run() -> None:
                try:
                    migrate(self.conninfo, lock_timeout_ms=30_000)
                except BaseException as exc:  # noqa: BLE001 - asserted below
                    errors.append(exc)

            thread = threading.Thread(target=run)
            thread.start()
            pid = None
            for _ in range(200):
                row = holder.execute(
                    "SELECT pid FROM pg_stat_activity WHERE application_name = 'prks-migrate' "
                    "AND wait_event_type = 'Lock' AND datname = current_database()"
                ).fetchone()
                if row is not None:
                    pid = row[0]
                    break
                time.sleep(0.05)
            self.assertIsNotNone(pid)
            holder.execute("SELECT pg_terminate_backend(%s)", (pid,))
            thread.join(30)
        self.assertEqual(len(errors), 1)
        error = errors[0]
        self.assertIsInstance(error, MigrationError)
        assert isinstance(error, MigrationError)
        self.assertEqual(error.reason, "database_unavailable")

    def test_newer_schema_database_is_refused(self) -> None:
        migrate(self.conninfo)
        self.query("INSERT INTO prks.schema_migrations (version, name, sha256) VALUES (999, 'future', %s)", ("0" * 64,))
        error = self.assert_refused("newer_schema", migrate, self.conninfo)
        self.assertIn("999", error.message)
        self.assert_refused("newer_schema", verify_schema, self.conninfo)

    def test_changed_applied_file_is_refused_with_the_recreate_message(self) -> None:
        directory = self.schema_copy()
        migrate(self.conninfo, directory=directory)
        with open(os.path.join(directory, "010_instance.sql"), "a", encoding="utf-8") as handle:
            handle.write("\n-- an in-place edit after this database applied the file\n")
        for fn in (migrate, verify_schema):
            with self.subTest(fn.__name__):
                error = self.assert_refused("schema_file_changed", fn, self.conninfo, directory=directory)
                self.assertIn("010_instance.sql", error.message)
                self.assertIn("recreate this instance", error.message)

    def test_renamed_applied_file_is_refused(self) -> None:
        directory = self.schema_copy()
        migrate(self.conninfo, directory=directory)
        os.rename(os.path.join(directory, "030_jobs.sql"), os.path.join(directory, "030_job_queue.sql"))
        self.assert_refused("schema_file_changed", migrate, self.conninfo, directory=directory)

    def test_removed_applied_file_is_refused(self) -> None:
        directory = self.schema_copy()
        migrate(self.conninfo, directory=directory)
        os.remove(os.path.join(directory, "020_identity.sql"))
        self.assert_refused("unknown_schema_file", migrate, self.conninfo, directory=directory)

    def test_new_file_sorting_before_applied_files_is_refused(self) -> None:
        directory = self.schema_copy()
        migrate(self.conninfo, directory=directory)
        self.write(directory, "015_late.sql", "CREATE TABLE prks.late (id int);\n")
        self.assert_refused("out_of_order", migrate, self.conninfo, directory=directory)
        self.assertEqual(self.query("SELECT to_regclass('prks.late') IS NULL"), [(True,)])

    def test_new_file_after_applied_files_is_applied(self) -> None:
        directory = self.schema_copy()
        migrate(self.conninfo, directory=directory)
        self.write(directory, "900_extra.sql", "CREATE TABLE prks.extra (id int);\n")
        self.assert_refused("pending", verify_schema, self.conninfo, directory=directory)
        result = migrate(self.conninfo, directory=directory)
        self.assertEqual(result.applied, ("900_extra.sql",))
        verify_schema(self.conninfo, directory=directory)

    def test_failing_file_rolls_back_whole_and_stops(self) -> None:
        directory = self.schema_copy()
        self.write(
            directory,
            "900_broken.sql",
            "CREATE TABLE prks.half (id int);\nCREATE TABLE prks.half (id int);\n",
        )
        self.write(directory, "910_after.sql", "CREATE TABLE prks.after (id int);\n")
        error = self.assert_refused("schema_file_failed", migrate, self.conninfo, directory=directory)
        self.assertIn("900_broken.sql", error.message)
        self.assertIn("42P07", error.message)
        self.assertEqual(self.query("SELECT to_regclass('prks.half') IS NULL"), [(True,)])
        self.assertEqual(self.query("SELECT to_regclass('prks.after') IS NULL"), [(True,)])
        self.assertEqual(self.query("SELECT max(version) FROM prks.schema_migrations"), [(40,)])
        self.write(directory, "900_broken.sql", "CREATE TABLE prks.half (id int);\n")
        self.assertEqual(
            migrate(self.conninfo, directory=directory).applied, ("900_broken.sql", "910_after.sql")
        )

    def test_failure_message_carries_no_server_text(self) -> None:
        # An invalid cast quotes its input in the server message; library
        # content must never reach the error (logging privacy).
        directory = self.schema_copy()
        self.write(directory, "900_cast.sql", "SELECT 'private-title-text'::integer;\n")
        error = self.assert_refused("schema_file_failed", migrate, self.conninfo, directory=directory)
        self.assertNotIn("private-title-text", error.message)
        self.assertNotIn("private-title-text", str(error))
        self.assertIsNone(error.__cause__)
        self.assertIn("22P02", error.message)

    def test_unqualified_names_fail_instead_of_landing_in_public(self) -> None:
        directory = self.schema_copy()
        self.write(directory, "900_unqualified.sql", "CREATE TABLE stray (id int);\n")
        self.assert_refused("schema_file_failed", migrate, self.conninfo, directory=directory)
        self.assertEqual(self.query("SELECT to_regclass('public.stray') IS NULL"), [(True,)])

    def test_non_empty_databases_are_refused_untouched(self) -> None:
        for setup, cleanup in (
            ("CREATE SCHEMA prks", "DROP SCHEMA prks"),
            ("CREATE SCHEMA derived", "DROP SCHEMA derived"),
            ("CREATE SCHEMA library_data", "DROP SCHEMA library_data"),
            ("CREATE TABLE public.precious (v text)", "DROP TABLE public.precious"),
            ("CREATE FUNCTION public.f() RETURNS int LANGUAGE sql AS 'SELECT 1'", "DROP FUNCTION public.f()"),
            ("CREATE TYPE public.mood AS ENUM ('ok')", "DROP TYPE public.mood"),
            ("CREATE EXTENSION pg_trgm SCHEMA public", "DROP EXTENSION pg_trgm"),
        ):
            with self.subTest(setup):
                self.query(setup)
                try:
                    self.assert_refused("foreign_schema", migrate, self.conninfo)
                    self.assertEqual(
                        self.query("SELECT to_regclass('prks.schema_migrations') IS NULL"), [(True,)]
                    )
                finally:
                    self.query(cleanup)
        self.assertEqual(len(migrate(self.conninfo).applied), len(load_schema_files()))

    def test_verify_refuses_an_empty_or_partial_database(self) -> None:
        self.assert_refused("not_migrated", verify_schema, self.conninfo)
        directory = self.schema_copy()
        os.remove(os.path.join(directory, "040_sync.sql"))
        migrate(self.conninfo, directory=directory)
        error = self.assert_refused("pending", verify_schema, self.conninfo)
        self.assertIn("040_sync.sql", error.message)


class SchemaFileLoadingTests(unittest.TestCase):
    def directory(self, *files: str) -> str:
        directory = tempfile.mkdtemp(prefix="prks-schema-")
        self.addCleanup(shutil.rmtree, directory, True)
        for name in files:
            with open(os.path.join(directory, name), "w", encoding="utf-8") as handle:
                handle.write("SELECT 1;\n")
        return directory

    def test_malformed_directories_are_refused(self) -> None:
        for files in (
            ("000_a.sql", "000_b.sql"),
            ("1_short.sql",),
            ("000_Upper.sql",),
            ("000-dash.sql",),
            (),
        ):
            with self.subTest(files=files):
                with self.assertRaises(MigrationError) as caught:
                    load_schema_files(self.directory(*files))
                self.assertEqual(caught.exception.reason, "invalid_schema_file")

    def test_non_sql_files_are_ignored_and_order_is_numeric(self) -> None:
        directory = self.directory("100_b.sql", "020_a.sql", "snapshot.json")
        self.assertEqual([f.filename for f in load_schema_files(directory)], ["020_a.sql", "100_b.sql"])

    def test_checked_in_files_are_the_pg2_baseline(self) -> None:
        self.assertEqual(
            [f.filename for f in load_schema_files()],
            ["000_extensions.sql", "010_instance.sql", "020_identity.sql", "030_jobs.sql", "040_sync.sql"],
        )


# Run as a child process: records any filesystem access under the root named
# by argv[1] (exit 3 if there was one) while it runs the module named by
# argv[2] as ``python -m``.
_AUDITED_MIGRATE = r"""
import os, runpy, sys
root = os.path.realpath(sys.argv[1])
touched = []
def under(path):
    try:
        path = os.fsdecode(path)
    except TypeError:
        return False
    return os.path.realpath(path) == root or os.path.realpath(path).startswith(root + os.sep)
def hook(event, args):
    if event in ("open", "os.mkdir", "os.listdir", "os.scandir", "os.remove", "os.rename",
                 "os.rmdir", "os.chmod", "os.utime", "os.truncate", "os.link", "os.symlink",
                 "shutil.copyfile", "shutil.rmtree", "os.walk"):
        if args and isinstance(args[0], (str, bytes, os.PathLike)) and under(args[0]):
            touched.append(event)
sys.addaudithook(hook)
module = sys.argv[2]
sys.argv = [module]
try:
    runpy.run_module(module, run_name="__main__", alter_sys=True)
except SystemExit as exc:
    code = exc.code
else:
    code = 0
if touched:
    sys.stderr.write("root touched: %s\n" % sorted(set(touched)))
    sys.exit(3)
sys.exit(code)
"""


class RunnerNeverTouchesTheDataRootTests(EmptyDatabaseTestCase):
    """§11.1, #624 post-merge correction: root claim and bump belong to L-ID-1."""

    def run_migrate(
        self, root: str, module: str = "backend.db.migrate", **extra_env: str
    ) -> subprocess.CompletedProcess[str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("PRKS_")
        }
        env.update(
            PRKS_DATABASE_URL=self.conninfo,
            PRKS_STORAGE=root,
            PRKS_STORAGE_ROOT=root,
            PRKS_DATA_ROOT=root,
        )
        env.update(extra_env)
        return subprocess.run(
            [sys.executable, "-c", _AUDITED_MIGRATE, root, module],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_absent_root_stays_absent(self) -> None:
        parent = tempfile.mkdtemp(prefix="prks-root-")
        self.addCleanup(shutil.rmtree, parent, True)
        root = os.path.join(parent, "absent")
        done = self.run_migrate(root)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("applied 040_sync.sql", done.stdout)
        self.assertFalse(os.path.exists(root))
        self.assertEqual(os.listdir(parent), [])

    def test_read_only_root_is_neither_read_nor_written(self) -> None:
        root = tempfile.mkdtemp(prefix="prks-root-")
        self.addCleanup(shutil.rmtree, root, True)
        marker = os.path.join(root, "prks-root.json")
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write('{"format": 1}\n')
        os.chmod(root, 0o500)
        self.addCleanup(os.chmod, root, 0o700)
        before = (sorted(os.listdir(root)), os.stat(marker).st_mtime_ns)
        done = self.run_migrate(root)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((sorted(os.listdir(root)), os.stat(marker).st_mtime_ns), before)
        self.assertEqual(self.query("SELECT count(*) FROM prks.instance"), [(0,)])

    def test_the_audit_detects_root_access(self) -> None:
        # Guards the guard: a module that lists the root is caught.
        root = tempfile.mkdtemp(prefix="prks-root-")
        self.addCleanup(shutil.rmtree, root, True)
        probe_dir = tempfile.mkdtemp(prefix="prks-probe-")
        self.addCleanup(shutil.rmtree, probe_dir, True)
        with open(os.path.join(probe_dir, "prks_root_probe.py"), "w", encoding="utf-8") as handle:
            handle.write("import os\nos.listdir(os.environ['PRKS_STORAGE'])\n")
        done = self.run_migrate(root, module="prks_root_probe", PYTHONPATH=probe_dir)
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertIn("os.listdir", done.stderr)

    def test_runner_module_imports_nothing_from_storage(self) -> None:
        with open(os.path.join(REPO_ROOT, "backend", "db", "migrate.py"), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        self.assertEqual(sorted(m for m in imported if m.startswith("backend.") and not m.startswith("backend.db")), [])


if __name__ == "__main__":
    unittest.main()
