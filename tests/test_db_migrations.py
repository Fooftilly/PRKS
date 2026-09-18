import inspect
import json
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
    REQUIRED_TABLES,
    Migration,
    MigrationError,
    _LEGACY_MARKER_COMPANIONS,
    application_schema_signature,
    apply_ordered_migrations,
    column_exists,
    index_exists,
    is_legacy_prks_database,
    migrate_v12_to_v13,
    read_schema_version,
    table_exists,
    trigger_exists,
    validate_migration_registry,
)
from backend.log_safety import PrivacySafeFormatter
from backend.storage.config import StorageConfig

_SCHEMA_PATH = os.path.join(_PROJECT_DIR, "backend", "db_schema.sql")
_SECRET_TITLE = "PRIVATE_MIGRATION_TITLE_X9Q7"
_LEGACY_WORK_ANNOTATIONS_DDL = """
CREATE TABLE work_annotations (
    work_id TEXT PRIMARY KEY,
    annotations_json TEXT NOT NULL DEFAULT '[]',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE
)
"""
_REALISTIC_ANN = {
    "id": "ann-canonical",
    "type": 9,
    "pageIndex": 0,
    "contents": "Keep me",
    "color": "#FFCD45",
    "strokeColor": "#FFCD45",
    "opacity": 1,
    "blendMode": "Multiply",
    "segmentRects": [{"origin": {"x": 72, "y": 700}, "size": {"width": 160, "height": 14}}],
    "rect": {"origin": {"x": 72, "y": 700}, "size": {"width": 160, "height": 14}},
    "custom": {"prksComment": "Keep me"},
}


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
        self.assertEqual(LATEST_SCHEMA_VERSION, 15)
        self.assertEqual(PRKS_SCHEMA_VERSION, LATEST_SCHEMA_VERSION)
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
        self.assertEqual(_version(db.db_path), LATEST_SCHEMA_VERSION)
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
            self.assertTrue(table_exists(conn, "saved_views"))
            self.assertTrue(index_exists(conn, "idx_saved_views_name_nocase"))
            self.assertTrue(trigger_exists(conn, "works_ai"))
            self.assertFalse(table_exists(conn, "work_annotations"))
            signature = application_schema_signature(conn)
            self.assertIn("author_text", signature["fts_columns"])
        finally:
            conn.close()
        work_id = db.add_work(title="Keep Me")
        with patch("backend.db_migrations.migrate_v9_to_v10") as spy:
            db2 = self._open()
            spy.assert_not_called()
        self.assertEqual(_version(db2.db_path), LATEST_SCHEMA_VERSION)
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
        self.assertEqual(_version(db.db_path), LATEST_SCHEMA_VERSION)
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
        self.assertEqual(_version(db.db_path), LATEST_SCHEMA_VERSION)
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
        self.assertEqual(_version(db.db_path), LATEST_SCHEMA_VERSION)
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
        self.assertEqual(_version(db.db_path), LATEST_SCHEMA_VERSION)
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


class TestSavedViewsMigration(MigrationTestCase):
    def _downgrade_to_v10(self, db_path, *, title="Keep V10"):
        db = self._open()
        work_id = db.add_work(title=title)
        conn = _raw(db_path)
        conn.execute("DROP INDEX IF EXISTS idx_saved_views_name_nocase")
        conn.execute("DROP TABLE IF EXISTS saved_views")
        conn.execute("DROP TABLE IF EXISTS argument_target_arguments")
        conn.execute("DROP TABLE IF EXISTS argument_target_positions")
        conn.execute("DROP TABLE IF EXISTS argument_sources")
        conn.execute("DROP TABLE IF EXISTS argument_verdicts")
        conn.execute("DROP TABLE IF EXISTS positions")
        conn.execute("DROP TABLE IF EXISTS concept_parents")
        conn.execute("DROP TABLE IF EXISTS concept_aliases")
        conn.execute("DROP INDEX IF EXISTS idx_concept_aliases_normalized")
        conn.execute("DROP INDEX IF EXISTS idx_concept_parents_parent")
        conn.execute("DROP INDEX IF EXISTS idx_argument_sources_work_id")
        conn.execute("DROP INDEX IF EXISTS idx_argument_target_arguments_target")
        conn.execute("DROP INDEX IF EXISTS idx_argument_target_positions_position")
        for table in ("sync_operations", "sync_entity_revisions", "sync_tag_lifecycle"):
            conn.execute("DROP TABLE IF EXISTS " + table)
        conn.execute("UPDATE schema_version SET version = 10")
        conn.commit()
        conn.close()
        return work_id

    def test_v10_migrates_to_v11_and_keeps_rows(self):
        work_id = self._downgrade_to_v10(self.storage.db_path)
        probe = _raw(self.storage.db_path)
        try:
            self.assertEqual(read_schema_version(probe), 10)
            self.assertFalse(table_exists(probe, "saved_views"))
        finally:
            probe.close()
        with _capture_logs() as logs:
            db = self._open()
        self.assertEqual(_version(db.db_path), LATEST_SCHEMA_VERSION)
        self.assertEqual(db.get_work(work_id)["title"], "Keep V10")
        conn = db.get_connection()
        try:
            self.assertTrue(table_exists(conn, "saved_views"))
            self.assertTrue(index_exists(conn, "idx_saved_views_name_nocase"))
            spec_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' "
                "AND name='idx_saved_views_name_nocase'"
            ).fetchone()[0]
            self.assertIn("NOCASE", spec_sql.upper())
            self.assertIn("UNIQUE", spec_sql.upper())
        finally:
            conn.close()
        blob = "\n".join(logs)
        self.assertIn("db_migration_started from_version=10 to_version=11 migration=add_saved_views", blob)
        self.assertNotIn("Keep V10", blob)

    def test_fresh_v11_matches_v10_migrated_v11(self):
        fresh_root = tempfile.mkdtemp(prefix="prks-mig-sv-fresh-")
        self.addCleanup(lambda: shutil.rmtree(fresh_root, ignore_errors=True))
        fresh_storage = StorageConfig.for_testing(fresh_root)
        os.makedirs(fresh_storage.pdfs_dir, exist_ok=True)
        fresh = PRKSDatabase(storage=fresh_storage, schema_path=_SCHEMA_PATH)
        self._downgrade_to_v10(self.storage.db_path)
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

    def test_v11_wrong_saved_views_index_is_schema_drift(self):
        db = self._open()
        conn = _raw(db.db_path)
        conn.execute("DROP INDEX idx_saved_views_name_nocase")
        conn.execute("CREATE UNIQUE INDEX idx_saved_views_name_nocase ON saved_views(name)")
        conn.commit()
        conn.close()
        with self.assertRaises(MigrationError) as ctx:
            self._open()
        self.assertEqual(ctx.exception.code, "schema_drift")
        self.assertEqual(ctx.exception.details.get("object"), "idx_saved_views_name_nocase")


class TestResearchNetworkMigration(MigrationTestCase):
    def _downgrade_to_v11_with_legacy(self):
        db = self._open()
        work_id = db.add_work(title="Keep V11")
        concept_id = db.generate_id("C")
        db.execute_query(
            "INSERT INTO concepts (id, name, description) VALUES (?, ?, ?)",
            (concept_id, "Culture Industry", "dormant definition"),
        )
        arg_id = "A-LEGACY01"
        conn = _raw(self.storage.db_path)
        conn.execute("DROP TABLE IF EXISTS argument_target_arguments")
        conn.execute("DROP TABLE IF EXISTS argument_target_positions")
        conn.execute("DROP TABLE IF EXISTS argument_sources")
        conn.execute("DROP TABLE IF EXISTS argument_verdicts")
        conn.execute("DROP TABLE IF EXISTS positions")
        conn.execute("DROP TABLE IF EXISTS concept_parents")
        conn.execute("DROP TABLE IF EXISTS concept_aliases")
        conn.execute("DROP TABLE IF EXISTS arguments")
        conn.execute("DROP INDEX IF EXISTS idx_argument_sources_work_id")
        conn.execute("DROP INDEX IF EXISTS idx_argument_target_arguments_target")
        conn.execute("DROP INDEX IF EXISTS idx_argument_target_positions_position")
        conn.execute("DROP INDEX IF EXISTS idx_concept_aliases_normalized")
        conn.execute("DROP INDEX IF EXISTS idx_concept_parents_parent")
        conn.execute(
            """
            CREATE TABLE arguments (
                id TEXT PRIMARY KEY,
                work_id TEXT NOT NULL,
                premise TEXT,
                conclusion TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            INSERT INTO arguments (id, work_id, premise, conclusion)
            VALUES (?, ?, ?, ?)
            """,
            (
                arg_id,
                work_id,
                "Monopoly production standardizes cultural commodities.",
                "Pseudo-individualization conceals that sameness.",
            ),
        )
        for table in ("sync_operations", "sync_entity_revisions", "sync_tag_lifecycle"):
            conn.execute("DROP TABLE IF EXISTS " + table)
        conn.execute("UPDATE schema_version SET version = 11")
        conn.commit()
        conn.close()
        return work_id, concept_id, arg_id

    def test_v11_migrates_to_v12_preserving_concepts_and_arguments(self):
        work_id, concept_id, arg_id = self._downgrade_to_v11_with_legacy()
        with _capture_logs() as logs:
            db = self._open()
        self.assertEqual(_version(db.db_path), LATEST_SCHEMA_VERSION)
        conn = db.get_connection()
        try:
            self.assertTrue(table_exists(conn, "concept_aliases"))
            self.assertTrue(table_exists(conn, "concept_parents"))
            self.assertTrue(table_exists(conn, "positions"))
            self.assertTrue(table_exists(conn, "argument_verdicts"))
            self.assertTrue(table_exists(conn, "argument_sources"))
            self.assertTrue(column_exists(conn, "arguments", "main_text"))
            self.assertFalse(column_exists(conn, "arguments", "premise"))
            self.assertFalse(index_exists(conn, "idx_arguments_work_id"))
            concept = conn.execute(
                "SELECT id, name, description FROM concepts WHERE id = ?",
                (concept_id,),
            ).fetchone()
            self.assertEqual(concept[1], "Culture Industry")
            self.assertEqual(concept[2], "dormant definition")
            arg = conn.execute(
                "SELECT id, name, kind, main_text FROM arguments WHERE id = ?",
                (arg_id,),
            ).fetchone()
            self.assertEqual(arg[0], arg_id)
            self.assertEqual(arg[2], "argument")
            self.assertIn("### Premise", arg[3])
            self.assertIn("Monopoly production standardizes cultural commodities.", arg[3])
            self.assertIn("### Conclusion", arg[3])
            self.assertIn("Pseudo-individualization conceals that sameness.", arg[3])
            src = conn.execute(
                "SELECT work_id, pages FROM argument_sources WHERE argument_id = ?",
                (arg_id,),
            ).fetchone()
            self.assertEqual(src[0], work_id)
            self.assertEqual(src[1], "")
        finally:
            conn.close()
        blob = "\n".join(logs)
        self.assertIn(
            "db_migration_started from_version=11 to_version=12 migration=research_network",
            blob,
        )
        self.assertNotIn("Culture Industry", blob)
        self.assertNotIn("Pseudo-individualization", blob)

    def test_fresh_v12_matches_v11_migrated_v12(self):
        fresh_root = tempfile.mkdtemp(prefix="prks-mig-rn-fresh-")
        self.addCleanup(lambda: shutil.rmtree(fresh_root, ignore_errors=True))
        fresh_storage = StorageConfig.for_testing(fresh_root)
        os.makedirs(fresh_storage.pdfs_dir, exist_ok=True)
        fresh = PRKSDatabase(storage=fresh_storage, schema_path=_SCHEMA_PATH)
        self._downgrade_to_v11_with_legacy()
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

    def test_v12_wrong_concept_parent_fk_is_schema_drift(self):
        db = self._open()
        conn = _raw(db.db_path)
        conn.execute("DROP TABLE concept_parents")
        conn.execute(
            """
            CREATE TABLE concept_parents (
                child_concept_id TEXT NOT NULL,
                parent_concept_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (child_concept_id, parent_concept_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX idx_concept_parents_parent ON concept_parents(parent_concept_id)"
        )
        conn.commit()
        conn.close()
        with self.assertRaises(MigrationError) as ctx:
            self._open()
        self.assertEqual(ctx.exception.code, "schema_drift")
        self.assertEqual(ctx.exception.details.get("object"), "concept_parents")


class TestRemoveWorkAnnotationsMigration(MigrationTestCase):
    def _install_v12_blob(self, work_id, payload):
        conn = _raw(self.storage.db_path)
        conn.execute(_LEGACY_WORK_ANNOTATIONS_DDL)
        conn.execute(
            "INSERT INTO work_annotations (work_id, annotations_json) VALUES (?, ?)",
            (work_id, json.dumps(payload)),
        )
        for table in ("sync_operations", "sync_entity_revisions", "sync_tag_lifecycle"):
            conn.execute("DROP TABLE IF EXISTS " + table)
        conn.execute("UPDATE schema_version SET version = 12")
        conn.commit()
        conn.close()

    def test_legacy_marker_keeps_work_annotations_current_schema_does_not(self):
        self.assertNotIn("work_annotations", REQUIRED_TABLES)
        self.assertIn("work_annotations", _LEGACY_MARKER_COMPANIONS)
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE TABLE works (id TEXT PRIMARY KEY, title TEXT NOT NULL)")
            conn.execute(
                "CREATE TABLE work_annotations (work_id TEXT PRIMARY KEY, annotations_json TEXT)"
            )
            self.assertTrue(is_legacy_prks_database(conn))
        finally:
            conn.close()

    def test_v13_migration_sql_drops_without_reading_json(self):
        src = inspect.getsource(migrate_v12_to_v13)
        self.assertIn("DROP TABLE IF EXISTS work_annotations", src)
        self.assertNotIn("annotations_json", src)
        self.assertNotIn("SELECT", src.upper().replace("IF EXISTS", ""))

    def test_v12_migrates_to_v13_preserving_canonical_annotations(self):
        db = self._open()
        work_id = db.add_work(title="Keep V12")
        person_id = db.add_person("Ada", "Lovelace")
        db.add_role(person_id, work_id, "Author")
        db.save_work_annotations(work_id, json.dumps([_REALISTIC_ANN]))
        before = db.execute_query(
            """
            SELECT id, type, content, page_index, color, geometry_json
            FROM annotations WHERE work_id = ? ORDER BY id
            """,
            (work_id,),
        )
        self._install_v12_blob(
            work_id,
            [{"id": "ann-stale", "contents": "OLD-C", "pageIndex": 0}],
        )
        probe = _raw(self.storage.db_path)
        try:
            self.assertEqual(read_schema_version(probe), 12)
            self.assertTrue(table_exists(probe, "work_annotations"))
            ids = [
                row[0]
                for row in probe.execute(
                    "SELECT id FROM annotations WHERE work_id = ? ORDER BY id",
                    (work_id,),
                )
            ]
            self.assertEqual(ids, ["ann-canonical"])
        finally:
            probe.close()
        db = self._open()
        self.assertEqual(_version(db.db_path), LATEST_SCHEMA_VERSION)
        self.assertEqual(db.get_work(work_id)["title"], "Keep V12")
        self.assertEqual(db.get_work_roles(work_id)[0]["id"], person_id)
        after = db.execute_query(
            """
            SELECT id, type, content, page_index, color, geometry_json
            FROM annotations WHERE work_id = ? ORDER BY id
            """,
            (work_id,),
        )
        self.assertEqual(after, before)
        conn = db.get_connection()
        try:
            self.assertFalse(table_exists(conn, "work_annotations"))
        finally:
            conn.close()
        got = json.loads(db.get_work_annotations(work_id))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["id"], "ann-canonical")
        self.assertEqual(got[0]["contents"], "Keep me")
        self.assertEqual(got[0]["custom"]["prksComment"], "Keep me")
        self.assertNotIn("ann-stale", json.dumps(got))

    def test_divergent_stale_blob_is_discarded(self):
        db = self._open()
        work_id = db.add_work(title="Divergent")
        db.sync_work_annotations(
            work_id, [{"id": "ann-canonical", "contents": "keep", "pageIndex": 0}]
        )
        self._install_v12_blob(
            work_id,
            [{"id": "ann-stale", "contents": "resurrect", "pageIndex": 1}],
        )
        db = self._open()
        ids = [row["id"] for row in db.execute_query("SELECT id FROM annotations")]
        self.assertEqual(ids, ["ann-canonical"])
        got = json.loads(db.get_work_annotations(work_id))
        self.assertEqual([item["id"] for item in got], ["ann-canonical"])
        self.assertNotIn("ann-stale", json.dumps(got))
        conn = db.get_connection()
        try:
            self.assertFalse(table_exists(conn, "work_annotations"))
        finally:
            conn.close()

    def test_empty_canonical_stays_empty_when_blob_is_stale(self):
        db = self._open()
        work_id = db.add_work(title="Empty Canonical")
        self.assertEqual(json.loads(db.get_work_annotations(work_id)), [])
        self._install_v12_blob(
            work_id,
            [{"id": "ann-stale", "contents": "should-not-return", "pageIndex": 0}],
        )
        db = self._open()
        self.assertEqual(db.execute_query("SELECT id FROM annotations"), [])
        self.assertEqual(json.loads(db.get_work_annotations(work_id)), [])
        conn = db.get_connection()
        try:
            self.assertFalse(table_exists(conn, "work_annotations"))
        finally:
            conn.close()

    def test_fresh_v13_matches_v12_migrated_v13(self):
        fresh_root = tempfile.mkdtemp(prefix="prks-mig-ann-fresh-")
        self.addCleanup(lambda: shutil.rmtree(fresh_root, ignore_errors=True))
        fresh_storage = StorageConfig.for_testing(fresh_root)
        os.makedirs(fresh_storage.pdfs_dir, exist_ok=True)
        fresh = PRKSDatabase(storage=fresh_storage, schema_path=_SCHEMA_PATH)
        db = self._open()
        work_id = db.add_work(title="Sig")
        self._install_v12_blob(work_id, [{"id": "stale"}])
        migrated = self._open()
        fresh_conn = fresh.get_connection()
        migrated_conn = migrated.get_connection()
        try:
            self.assertEqual(
                application_schema_signature(fresh_conn),
                application_schema_signature(migrated_conn),
            )
            self.assertFalse(table_exists(fresh_conn, "work_annotations"))
            self.assertFalse(table_exists(migrated_conn, "work_annotations"))
        finally:
            fresh_conn.close()
            migrated_conn.close()

    def test_failed_v13_rolls_back_drop_and_version(self):
        db = self._open()
        work_id = db.add_work(title="Rollback V13")
        self._install_v12_blob(work_id, [{"id": "keep-blob"}])
        conn = _raw(self.storage.db_path)

        def boom(c):
            c.execute("DROP TABLE IF EXISTS work_annotations")
            raise RuntimeError("boom")

        try:
            with self.assertRaises(RuntimeError):
                apply_ordered_migrations(
                    conn,
                    12,
                    (Migration(13, "remove_legacy_work_annotations", boom),),
                )
            self.assertEqual(read_schema_version(conn), 12)
            self.assertTrue(table_exists(conn, "work_annotations"))
        finally:
            conn.close()


class TestVersionRefusal(MigrationTestCase):
    def test_newer_version_is_refused_without_schema_changes(self):
        db = self._open()
        conn = _raw(db.db_path)
        conn.execute("CREATE TABLE canary_keep (id INTEGER)")
        conn.execute("INSERT INTO canary_keep (id) VALUES (1)")
        conn.execute("UPDATE schema_version SET version = 16")
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
                16,
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
        from_version = read_schema_version(conn)
        try:
            with self.assertRaises(RuntimeError):
                apply_ordered_migrations(
                    conn,
                    from_version,
                    (Migration(from_version + 1, "exploding_probe", boom),),
                )
            self.assertEqual(read_schema_version(conn), from_version)
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
            self.assertEqual(read_schema_version(check), LATEST_SCHEMA_VERSION)
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
                LATEST_SCHEMA_VERSION,
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

    def test_v10_folder_parent_title_index_is_accepted(self):
        db = self._open()
        conn = db.get_connection()
        try:
            spec_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' "
                "AND name='idx_folders_parent_title_nocase'"
            ).fetchone()[0]
            self.assertIn("COALESCE", spec_sql.upper())
            cids = [
                int(row[1])
                for row in conn.execute("PRAGMA index_xinfo(idx_folders_parent_title_nocase)")
                if int(row[5] if row[5] is not None else 1) != 0
            ]
            self.assertEqual(cids, [-2, -2])
        finally:
            conn.close()
        self._open()
        self.assertEqual(_version(db.db_path), LATEST_SCHEMA_VERSION)

    def test_v10_folder_parent_title_index_rejects_extra_key(self):
        db = self._open()
        conn = _raw(db.db_path)
        conn.execute("DROP INDEX idx_folders_parent_title_nocase")
        conn.execute(
            "CREATE UNIQUE INDEX idx_folders_parent_title_nocase "
            "ON folders(COALESCE(parent_id, ''), LOWER(TRIM(title)), id)"
        )
        conn.commit()
        conn.close()
        with self.assertRaises(MigrationError) as ctx:
            self._open()
        self.assertEqual(ctx.exception.code, "schema_drift")
        self.assertEqual(ctx.exception.details.get("object"), "idx_folders_parent_title_nocase")

    def test_v10_folder_parent_title_index_rejects_wrong_expression(self):
        db = self._open()
        conn = _raw(db.db_path)
        conn.execute("DROP INDEX idx_folders_parent_title_nocase")
        conn.execute(
            "CREATE UNIQUE INDEX idx_folders_parent_title_nocase "
            "ON folders(COALESCE(parent_id, ''), LOWER(title))"
        )
        conn.commit()
        conn.close()
        with self.assertRaises(MigrationError) as ctx:
            self._open()
        self.assertEqual(ctx.exception.code, "schema_drift")
        self.assertEqual(ctx.exception.details.get("object"), "idx_folders_parent_title_nocase")

    def test_v10_folder_parent_title_index_rejects_reversed_keys(self):
        db = self._open()
        conn = _raw(db.db_path)
        conn.execute("DROP INDEX idx_folders_parent_title_nocase")
        conn.execute(
            "CREATE UNIQUE INDEX idx_folders_parent_title_nocase "
            "ON folders(LOWER(TRIM(title)), COALESCE(parent_id, ''))"
        )
        conn.commit()
        conn.close()
        with self.assertRaises(MigrationError) as ctx:
            self._open()
        self.assertEqual(ctx.exception.code, "schema_drift")
        self.assertEqual(ctx.exception.details.get("object"), "idx_folders_parent_title_nocase")

    def test_v10_unexpected_fk_is_schema_drift(self):
        db = self._open()
        conn = _raw(db.db_path)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("ALTER TABLE publisher_aliases RENAME TO publisher_aliases_old")
        conn.execute(
            """
            CREATE TABLE publisher_aliases (
                id TEXT PRIMARY KEY,
                publisher_id TEXT NOT NULL,
                alias TEXT NOT NULL,
                extra_work_id TEXT,
                FOREIGN KEY (publisher_id) REFERENCES publishers(id) ON DELETE CASCADE,
                FOREIGN KEY (extra_work_id) REFERENCES works(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "INSERT INTO publisher_aliases (id, publisher_id, alias) "
            "SELECT id, publisher_id, alias FROM publisher_aliases_old"
        )
        conn.execute("DROP TABLE publisher_aliases_old")
        conn.execute(
            "CREATE UNIQUE INDEX idx_publisher_aliases_alias_nocase "
            "ON publisher_aliases(alias COLLATE NOCASE)"
        )
        conn.commit()
        conn.close()
        with self.assertRaises(MigrationError) as ctx:
            self._open()
        self.assertEqual(ctx.exception.code, "schema_drift")
        self.assertEqual(ctx.exception.details.get("object"), "publisher_aliases")

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
        self.assertEqual(_version(db.db_path), LATEST_SCHEMA_VERSION)
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

    def test_v9_unexpected_fk_is_incompatible(self):
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
                alias TEXT NOT NULL,
                extra_work_id TEXT,
                FOREIGN KEY (publisher_id) REFERENCES publishers(id) ON DELETE CASCADE,
                FOREIGN KEY (extra_work_id) REFERENCES works(id) ON DELETE CASCADE
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
            self.assertEqual(len(list(check.execute("PRAGMA foreign_key_list(publisher_aliases)"))), 2)
        finally:
            check.close()

    def test_db_manager_has_no_ad_hoc_schema_mutation(self):
        with open(os.path.join(_PROJECT_DIR, "backend", "db_manager.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("ALTER TABLE", source)
        self.assertNotIn("_migrate_works_fts_author_text", source)


if __name__ == "__main__":
    unittest.main()
