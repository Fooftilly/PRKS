import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.db_manager import PRKS_SCHEMA_VERSION, PRKSDatabase
from backend.db_migrations import (
    LATEST_SCHEMA_VERSION,
    LEGACY_BASELINE_VERSION,
    MIGRATIONS,
    Migration,
    MigrationError,
    application_schema_signature,
    apply_ordered_migrations,
    column_exists,
    index_exists,
    read_schema_version,
    table_exists,
    trigger_exists,
    validate_migration_registry,
)
from backend.log_safety import PrivacySafeFormatter
from backend.storage.config import StorageConfig

_SCHEMA_PATH = os.path.join(_PROJECT_DIR, "backend", "db_schema.sql")
_SECRET_TITLE = "PRIVATE_MIGRATION_TITLE_X9Q7"


@contextmanager
def _capture_logs(logger_name="prks.db"):
    logger = logging.getLogger(logger_name)
    records = []

    class _Handler(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    handler = _Handler()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(PrivacySafeFormatter("%(levelname)s %(name)s %(message)s"))
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)


def _raw(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _version(path):
    conn = _raw(path)
    try:
        return read_schema_version(conn)
    finally:
        conn.close()


class MigrationTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="prks-mig-")
        self.storage = StorageConfig.for_testing(self._tmpdir)
        os.makedirs(self.storage.pdfs_dir, exist_ok=True)
        os.makedirs(self.storage.thumbs_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.storage.db_path) or self._tmpdir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _open(self):
        return PRKSDatabase(storage=self.storage, schema_path=_SCHEMA_PATH)

    def _seed_legacy_core(self, conn, *, with_fts=True, work_id="W-LEGACY1", title="Legacy Work"):
        conn.execute(
            """
            CREATE TABLE works (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT DEFAULT 'Not Started',
                published_date TEXT,
                abstract TEXT,
                text_content TEXT,
                file_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE persons (
                id TEXT PRIMARY KEY,
                first_name TEXT,
                last_name TEXT NOT NULL,
                aliases TEXT,
                about TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE roles (
                person_id TEXT NOT NULL,
                work_id TEXT NOT NULL,
                role_type TEXT NOT NULL,
                order_index INTEGER DEFAULT 0,
                PRIMARY KEY (person_id, work_id, role_type, order_index)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE folders (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE tags (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                color TEXT DEFAULT '#6d6cf7',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE work_tags (
                work_id TEXT NOT NULL,
                tag_id TEXT NOT NULL,
                PRIMARY KEY (work_id, tag_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE folder_tags (
                folder_id TEXT NOT NULL,
                tag_id TEXT NOT NULL,
                PRIMARY KEY (folder_id, tag_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE playlists (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE playlist_items (
                playlist_id TEXT NOT NULL,
                work_id TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (playlist_id, work_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE processing_files (
                id TEXT PRIMARY KEY,
                rel_path TEXT NOT NULL UNIQUE,
                abs_path TEXT NOT NULL,
                filename TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            )
            """
        )
        conn.execute(
            "INSERT INTO works (id, title, abstract, text_content) VALUES (?, ?, ?, ?)",
            (work_id, title, "legacy abstract", "legacy body"),
        )
        if with_fts:
            conn.execute(
                """
                CREATE VIRTUAL TABLE works_fts USING fts5(
                    title,
                    abstract,
                    text_content,
                    content='works',
                    content_rowid='rowid'
                )
                """
            )
            conn.execute(
                """
                CREATE TRIGGER works_ai AFTER INSERT ON works BEGIN
                  INSERT INTO works_fts(rowid, title, abstract, text_content)
                  VALUES (new.rowid, new.title, new.abstract, new.text_content);
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER works_ad AFTER DELETE ON works BEGIN
                  INSERT INTO works_fts(works_fts, rowid, title, abstract, text_content)
                  VALUES ('delete', old.rowid, old.title, old.abstract, old.text_content);
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER works_au AFTER UPDATE ON works BEGIN
                  INSERT INTO works_fts(works_fts, rowid, title, abstract, text_content)
                  VALUES ('delete', old.rowid, old.title, old.abstract, old.text_content);
                  INSERT INTO works_fts(rowid, title, abstract, text_content)
                  VALUES (new.rowid, new.title, new.abstract, new.text_content);
                END
                """
            )
            conn.execute(
                """
                INSERT INTO works_fts(rowid, title, abstract, text_content)
                SELECT rowid, title, abstract, text_content FROM works
                """
            )


class TestRegistry(unittest.TestCase):
    def test_production_registry_is_contiguous(self):
        self.assertEqual(LATEST_SCHEMA_VERSION, 10)
        self.assertEqual(PRKS_SCHEMA_VERSION, 10)
        self.assertEqual(LEGACY_BASELINE_VERSION, 9)
        validate_migration_registry()
        self.assertEqual(MIGRATIONS[-1].target_version, LATEST_SCHEMA_VERSION)
        self.assertEqual(MIGRATIONS[0].name, "ordered_migration_baseline")

    def test_registry_rejects_gap_duplicate_and_out_of_order(self):
        def noop(_conn):
            return None

        with self.assertRaises(MigrationError) as gap:
            validate_migration_registry(
                (
                    Migration(10, "a", noop),
                    Migration(12, "b", noop),
                ),
                latest=12,
            )
        self.assertEqual(gap.exception.code, "invalid_registry")
        with self.assertRaises(MigrationError):
            validate_migration_registry(
                (
                    Migration(10, "a", noop),
                    Migration(10, "b", noop),
                ),
                latest=10,
            )
        with self.assertRaises(MigrationError):
            validate_migration_registry(
                (
                    Migration(11, "b", noop),
                    Migration(10, "a", noop),
                ),
                latest=11,
            )


class TestFreshDatabase(MigrationTestCase):
    def test_fresh_database_is_version_10_and_idempotent(self):
        db = self._open()
        self.assertEqual(_version(db.db_path), 10)
        conn = db.get_connection()
        try:
            for name in (
                "idx_folders_parent_id",
                "idx_folders_parent_title_nocase",
                "idx_tags_name_nocase",
                "idx_playlist_items_work_unique",
                "idx_publishers_name_nocase",
            ):
                self.assertTrue(index_exists(conn, name), name)
            self.assertTrue(column_exists(conn, "works", "author_text"))
            self.assertTrue(column_exists(conn, "roles", "credit_name"))
            self.assertTrue(table_exists(conn, "publishers"))
            self.assertTrue(trigger_exists(conn, "works_ai"))
            signature = application_schema_signature(conn)
            self.assertIn("author_text", signature["fts_columns"])
        finally:
            conn.close()
        work_id = db.add_work(title="Keep Me")
        with patch("backend.db_migrations.migrate_v9_to_v10") as spy:
            db2 = self._open()
            spy.assert_not_called()
        self.assertEqual(_version(db2.db_path), 10)
        row = db2.get_work(work_id)
        self.assertEqual(row["title"], "Keep Me")


class TestLegacyAndV9(MigrationTestCase):
    def test_unversioned_legacy_reaches_v10_and_keeps_rows(self):
        conn = _raw(self.storage.db_path)
        self._seed_legacy_core(conn, title=_SECRET_TITLE)
        conn.commit()
        conn.close()
        with _capture_logs() as logs:
            db = self._open()
        self.assertEqual(_version(db.db_path), 10)
        work = db.get_work("W-LEGACY1")
        self.assertEqual(work["title"], _SECRET_TITLE)
        self.assertEqual(work.get("doc_type"), "article")
        blob = "\n".join(logs)
        self.assertNotIn(_SECRET_TITLE, blob)
        self.assertIn("db_legacy_normalization_started", blob)
        self.assertIn("db_migration_completed", blob)

    def test_numeric_pre_v9_uses_legacy_bridge(self):
        conn = _raw(self.storage.db_path)
        self._seed_legacy_core(conn, title="Old Seven")
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (7)")
        conn.commit()
        conn.close()
        db = self._open()
        self.assertEqual(_version(db.db_path), 10)
        self.assertEqual(db.get_work("W-LEGACY1")["title"], "Old Seven")

    def test_duplicate_identical_version_rows_are_normalized(self):
        conn = _raw(self.storage.db_path)
        self._seed_legacy_core(conn)
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (9)")
        conn.execute("INSERT INTO schema_version (version) VALUES (9)")
        conn.execute("INSERT INTO schema_version (version) VALUES (9)")
        conn.commit()
        conn.close()
        db = self._open()
        self.assertEqual(_version(db.db_path), 10)
        check = _raw(db.db_path)
        try:
            count = check.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            check.close()

    def test_v9_missing_objects_are_reconciled(self):
        conn = _raw(self.storage.db_path)
        self._seed_legacy_core(conn)
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (9)")
        conn.commit()
        conn.close()
        probe = _raw(self.storage.db_path)
        try:
            self.assertFalse(column_exists(probe, "roles", "credit_name"))
            self.assertFalse(column_exists(probe, "processing_files", "target_folder_id"))
            self.assertFalse(table_exists(probe, "publishers"))
            self.assertFalse(index_exists(probe, "idx_folders_parent_id"))
            fts_cols = [
                row[1] for row in probe.execute("PRAGMA table_info(works_fts)").fetchall()
            ]
            self.assertNotIn("author_text", fts_cols)
        finally:
            probe.close()
        db = self._open()
        self.assertEqual(_version(db.db_path), 10)
        conn = db.get_connection()
        try:
            self.assertTrue(column_exists(conn, "roles", "credit_name"))
            self.assertTrue(column_exists(conn, "processing_files", "target_folder_id"))
            self.assertTrue(table_exists(conn, "publishers"))
            self.assertTrue(table_exists(conn, "publisher_aliases"))
            self.assertTrue(index_exists(conn, "idx_folders_parent_id"))
            self.assertTrue(index_exists(conn, "idx_folders_parent_title_nocase"))
            self.assertIn(
                "author_text",
                [row[1] for row in conn.execute("PRAGMA table_info(works_fts)").fetchall()],
            )
        finally:
            conn.close()

    def test_fresh_and_migrated_schema_signatures_match(self):
        fresh_root = tempfile.mkdtemp(prefix="prks-mig-fresh-")
        self.addCleanup(lambda: shutil.rmtree(fresh_root, ignore_errors=True))
        fresh_storage = StorageConfig.for_testing(fresh_root)
        os.makedirs(fresh_storage.pdfs_dir, exist_ok=True)
        fresh = PRKSDatabase(storage=fresh_storage, schema_path=_SCHEMA_PATH)
        conn = _raw(self.storage.db_path)
        self._seed_legacy_core(conn)
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (9)")
        conn.commit()
        conn.close()
        migrated = self._open()
        fresh_conn = fresh.get_connection()
        migrated_conn = migrated.get_connection()
        try:
            self.assertEqual(
                application_schema_signature(fresh_conn),
                application_schema_signature(migrated_conn),
            )
        finally:
            fresh_conn.close()
            migrated_conn.close()


class TestVersionRefusal(MigrationTestCase):
    def test_newer_version_is_refused_without_schema_changes(self):
        db = self._open()
        conn = _raw(db.db_path)
        conn.execute("CREATE TABLE canary_keep (id INTEGER)")
        conn.execute("INSERT INTO canary_keep (id) VALUES (1)")
        conn.execute("UPDATE schema_version SET version = 11")
        conn.commit()
        conn.close()
        with self.assertRaises(MigrationError) as ctx:
            self._open()
        self.assertEqual(ctx.exception.code, "newer_schema")
        self.assertIn("newer PRKS version", str(ctx.exception))
        check = _raw(db.db_path)
        try:
            self.assertEqual(
                check.execute("SELECT version FROM schema_version").fetchone()[0],
                11,
            )
            self.assertEqual(check.execute("SELECT id FROM canary_keep").fetchone()[0], 1)
        finally:
            check.close()

    def test_non_integer_negative_and_conflicting_versions_fail(self):
        cases = [
            ("not-an-int",),
            (-3,),
            (8, 9),
        ]
        for values in cases:
            with self.subTest(values=values):
                root = tempfile.mkdtemp(prefix="prks-mig-ver-")
                self.addCleanup(lambda path=root: shutil.rmtree(path, ignore_errors=True))
                storage = StorageConfig.for_testing(root)
                os.makedirs(os.path.dirname(storage.db_path) or root, exist_ok=True)
                conn = _raw(storage.db_path)
                conn.execute("CREATE TABLE works (id TEXT PRIMARY KEY, title TEXT NOT NULL)")
                conn.execute("CREATE TABLE schema_version (version)")
                for value in values:
                    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (value,))
                conn.commit()
                conn.close()
                with self.assertRaises(MigrationError) as ctx:
                    PRKSDatabase(storage=storage, schema_path=_SCHEMA_PATH)
                self.assertIn(ctx.exception.code, {"invalid_schema_version", "newer_schema"})


class TestTransactionsAndOrder(MigrationTestCase):
    def test_failed_migration_rolls_back_schema_and_version(self):
        db = self._open()

        def boom(conn):
            conn.execute("CREATE TABLE migration_probe (id INTEGER)")
            conn.execute("INSERT INTO migration_probe (id) VALUES (1)")
            raise RuntimeError("boom")

        conn = _raw(db.db_path)
        try:
            with self.assertRaises(RuntimeError):
                apply_ordered_migrations(
                    conn,
                    10,
                    (Migration(11, "exploding_probe", boom),),
                )
            self.assertEqual(read_schema_version(conn), 10)
            self.assertFalse(table_exists(conn, "migration_probe"))
        finally:
            conn.close()

    def test_migrations_run_in_order_and_commit_between_steps(self):
        db = self._open()
        seen_from_other = []

        def step_11(conn):
            conn.execute("CREATE TABLE mig_probe (step INTEGER)")
            conn.execute("INSERT INTO mig_probe (step) VALUES (11)")

        def step_12(conn):
            other = sqlite3.connect(db.db_path, timeout=5)
            try:
                seen_from_other.append(
                    other.execute("SELECT version FROM schema_version").fetchone()[0]
                )
                seen_from_other.append(
                    [row[0] for row in other.execute("SELECT step FROM mig_probe").fetchall()]
                )
            finally:
                other.close()
            conn.execute("INSERT INTO mig_probe (step) VALUES (12)")

        conn = _raw(db.db_path)
        try:
            apply_ordered_migrations(
                conn,
                10,
                (
                    Migration(11, "step_eleven", step_11),
                    Migration(12, "step_twelve", step_12),
                ),
            )
            self.assertEqual(
                conn.execute("SELECT version FROM schema_version").fetchone()[0],
                12,
            )
            steps = [row[0] for row in conn.execute("SELECT step FROM mig_probe ORDER BY rowid")]
            self.assertEqual(steps, [11, 12])
            self.assertEqual(seen_from_other[0], 11)
            self.assertEqual(seen_from_other[1], [11])
        finally:
            conn.close()


class TestFtsAndTags(MigrationTestCase):
    def test_fts_rebuild_reindexes_and_keeps_triggers_current(self):
        conn = _raw(self.storage.db_path)
        self._seed_legacy_core(conn, title="Searchable Kant")
        conn.execute("ALTER TABLE works ADD COLUMN author_text TEXT")
        conn.execute("UPDATE works SET author_text = 'Immanuel Kant' WHERE id = 'W-LEGACY1'")
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (9)")
        conn.commit()
        conn.close()
        db = self._open()
        conn = db.get_connection()
        try:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(works_fts)").fetchall()]
            self.assertIn("author_text", cols)
            for name in ("works_ai", "works_ad", "works_au"):
                sql = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
                    (name,),
                ).fetchone()[0]
                self.assertIn("author_text", sql)
            hit = conn.execute(
                "SELECT title FROM works_fts WHERE works_fts MATCH 'Immanuel'"
            ).fetchone()
            self.assertIsNotNone(hit)
            conn.execute(
                "INSERT INTO works (id, title, author_text) VALUES (?, ?, ?)",
                ("W-NEW1", "New FTS Work", "Ada Lovelace"),
            )
            self.assertIsNotNone(
                conn.execute(
                    "SELECT title FROM works_fts WHERE works_fts MATCH 'Lovelace'"
                ).fetchone()
            )
            conn.execute("UPDATE works SET author_text = 'Grace Hopper' WHERE id = 'W-NEW1'")
            self.assertIsNotNone(
                conn.execute(
                    "SELECT title FROM works_fts WHERE works_fts MATCH 'Hopper'"
                ).fetchone()
            )
            conn.execute("DELETE FROM works WHERE id = 'W-NEW1'")
            self.assertIsNone(
                conn.execute(
                    "SELECT title FROM works_fts WHERE works_fts MATCH 'Hopper'"
                ).fetchone()
            )
            conn.commit()
        finally:
            conn.close()

    def test_tag_case_dedupe_preserves_relationships(self):
        conn = _raw(self.storage.db_path)
        self._seed_legacy_core(conn)
        conn.execute(
            "INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)",
            ("T-KEEP", "Philosophy", "2020-01-01 00:00:00"),
        )
        conn.execute(
            "INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)",
            ("T-LOSE1", "philosophy", "2021-01-01 00:00:00"),
        )
        conn.execute(
            "INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)",
            ("T-LOSE2", "PHILOSOPHY", "2022-01-01 00:00:00"),
        )
        conn.execute("INSERT INTO work_tags (work_id, tag_id) VALUES ('W-LEGACY1', 'T-LOSE1')")
        conn.execute(
            "INSERT INTO folders (id, title) VALUES ('F-1', 'Folder One')"
        )
        conn.execute("INSERT INTO folder_tags (folder_id, tag_id) VALUES ('F-1', 'T-LOSE2')")
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (9)")
        conn.commit()
        conn.close()
        db = self._open()
        conn = db.get_connection()
        try:
            names = [row[0] for row in conn.execute("SELECT name FROM tags")]
            self.assertEqual(len(names), 1)
            self.assertEqual(names[0].lower(), "philosophy")
            keeper = conn.execute("SELECT id FROM tags").fetchone()[0]
            self.assertEqual(keeper, "T-KEEP")
            work_tags = [
                row[0]
                for row in conn.execute("SELECT tag_id FROM work_tags WHERE work_id = 'W-LEGACY1'")
            ]
            self.assertEqual(work_tags, ["T-KEEP"])
            folder_tags = [
                row[0]
                for row in conn.execute("SELECT tag_id FROM folder_tags WHERE folder_id = 'F-1'")
            ]
            self.assertEqual(folder_tags, ["T-KEEP"])
            self.assertTrue(index_exists(conn, "idx_tags_name_nocase"))
        finally:
            conn.close()


class TestClassification(MigrationTestCase):
    def test_unrelated_sqlite_db_is_refused_without_mutation(self):
        conn = _raw(self.storage.db_path)
        conn.execute("CREATE TABLE unrelated_table (id INTEGER PRIMARY KEY, label TEXT)")
        conn.execute("INSERT INTO unrelated_table (id, label) VALUES (1, 'keep')")
        conn.commit()
        conn.close()
        with self.assertRaises(MigrationError) as ctx:
            self._open()
        self.assertEqual(ctx.exception.code, "not_prks_database")
        check = _raw(self.storage.db_path)
        try:
            tables = {
                row[0]
                for row in check.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            self.assertEqual(tables, {"unrelated_table"})
            self.assertEqual(
                check.execute("SELECT label FROM unrelated_table").fetchone()[0],
                "keep",
            )
            self.assertFalse(table_exists(check, "schema_version"))
            self.assertFalse(table_exists(check, "works"))
        finally:
            check.close()


class TestConstraintAndDrift(MigrationTestCase):
    def test_playlist_unique_conflict_rolls_back_without_deleting(self):
        conn = _raw(self.storage.db_path)
        self._seed_legacy_core(conn)
        conn.execute("INSERT INTO playlists (id, title) VALUES ('P-1', 'One')")
        conn.execute("INSERT INTO playlists (id, title) VALUES ('P-2', 'Two')")
        conn.execute(
            "INSERT INTO playlist_items (playlist_id, work_id, position) VALUES ('P-1', 'W-LEGACY1', 0)"
        )
        conn.execute(
            "INSERT INTO playlist_items (playlist_id, work_id, position) VALUES ('P-2', 'W-LEGACY1', 0)"
        )
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (9)")
        conn.commit()
        conn.close()
        with self.assertRaises(MigrationError) as ctx:
            self._open()
        self.assertEqual(ctx.exception.code, "legacy_constraint_conflict")
        self.assertEqual(ctx.exception.details.get("constraint"), "playlist_items_work_unique")
        check = _raw(self.storage.db_path)
        try:
            self.assertEqual(read_schema_version(check), 9)
            rows = check.execute(
                "SELECT playlist_id FROM playlist_items WHERE work_id = 'W-LEGACY1' ORDER BY playlist_id"
            ).fetchall()
            self.assertEqual([row[0] for row in rows], ["P-1", "P-2"])
            self.assertFalse(index_exists(check, "idx_playlist_items_work_unique"))
        finally:
            check.close()

    def test_current_version_schema_drift_is_not_auto_repaired(self):
        db = self._open()
        conn = _raw(db.db_path)
        conn.execute("DROP INDEX idx_playlist_items_work_unique")
        conn.commit()
        conn.close()
        with self.assertRaises(MigrationError) as ctx:
            self._open()
        self.assertEqual(ctx.exception.code, "schema_drift")
        check = _raw(db.db_path)
        try:
            self.assertEqual(read_schema_version(check), 10)
            self.assertFalse(index_exists(check, "idx_playlist_items_work_unique"))
        finally:
            check.close()

    def test_v10_wrong_index_definition_is_schema_drift(self):
        db = self._open()
        conn = _raw(db.db_path)
        conn.execute("DROP INDEX idx_playlist_items_work_unique")
        conn.execute(
            "CREATE INDEX idx_playlist_items_work_unique ON playlist_items(playlist_id)"
        )
        conn.commit()
        conn.close()
        with self.assertRaises(MigrationError) as ctx:
            self._open()
        self.assertEqual(ctx.exception.code, "schema_drift")
        self.assertEqual(ctx.exception.details.get("object"), "idx_playlist_items_work_unique")
        check = _raw(db.db_path)
        try:
            self.assertEqual(
                check.execute("SELECT version FROM schema_version").fetchone()[0],
                10,
            )
            unique = None
            for row in check.execute("PRAGMA index_list(playlist_items)"):
                if row[1] == "idx_playlist_items_work_unique":
                    unique = int(row[2])
            self.assertEqual(unique, 0)
            cols = [
                row[2]
                for row in check.execute("PRAGMA index_xinfo(idx_playlist_items_work_unique)")
                if row[2] and int(row[5] if row[5] is not None else 1) != 0
            ]
            self.assertEqual(cols, ["playlist_id"])
        finally:
            check.close()

    def test_v10_missing_nocase_collation_is_schema_drift(self):
        db = self._open()
        conn = _raw(db.db_path)
        conn.execute("DROP INDEX idx_tags_name_nocase")
        conn.execute("CREATE UNIQUE INDEX idx_tags_name_nocase ON tags(name)")
        conn.commit()
        conn.close()
        with self.assertRaises(MigrationError) as ctx:
            self._open()
        self.assertEqual(ctx.exception.code, "schema_drift")
        self.assertEqual(ctx.exception.details.get("object"), "idx_tags_name_nocase")

    def test_v9_wrong_named_index_is_reconciled(self):
        conn = _raw(self.storage.db_path)
        self._seed_legacy_core(conn)
        conn.execute(
            "CREATE INDEX idx_playlist_items_work_unique ON playlist_items(playlist_id)"
        )
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (9)")
        conn.commit()
        conn.close()
        db = self._open()
        self.assertEqual(_version(db.db_path), 10)
        check = db.get_connection()
        try:
            unique = None
            for row in check.execute("PRAGMA index_list(playlist_items)"):
                if row[1] == "idx_playlist_items_work_unique":
                    unique = int(row[2])
            self.assertEqual(unique, 1)
            cols = [
                row[2]
                for row in check.execute("PRAGMA index_xinfo(idx_playlist_items_work_unique)")
                if row[2] and int(row[5] if row[5] is not None else 1) != 0
            ]
            self.assertEqual(cols, ["work_id"])
        finally:
            check.close()

    def test_v9_compatibility_table_missing_fk_is_refused(self):
        conn = _raw(self.storage.db_path)
        self._seed_legacy_core(conn)
        conn.execute(
            "CREATE TABLE publishers (id TEXT PRIMARY KEY, name TEXT NOT NULL)"
        )
        conn.execute(
            """
            CREATE TABLE publisher_aliases (
                id TEXT PRIMARY KEY,
                publisher_id TEXT NOT NULL,
                alias TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (9)")
        conn.commit()
        conn.close()
        with self.assertRaises(MigrationError) as ctx:
            self._open()
        self.assertEqual(ctx.exception.code, "incompatible_table")
        self.assertEqual(ctx.exception.details.get("object"), "publisher_aliases")
        check = _raw(self.storage.db_path)
        try:
            self.assertEqual(read_schema_version(check), 9)
            fks = list(check.execute("PRAGMA foreign_key_list(publisher_aliases)"))
            self.assertEqual(fks, [])
        finally:
            check.close()

    def test_db_manager_has_no_ad_hoc_schema_mutation(self):
        with open(os.path.join(_PROJECT_DIR, "backend", "db_manager.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("ALTER TABLE", source)
        self.assertNotIn("_migrate_works_fts_author_text", source)


if __name__ == "__main__":
    unittest.main()
