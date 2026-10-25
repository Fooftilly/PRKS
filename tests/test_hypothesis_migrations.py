"""Bounded property: migrating a populated SQLite v16 library is stable on re-entry.

Use the repository's existing v16 downgrade fixture, real ordered migrations,
and small generated canonical libraries. This complements the deterministic
migration cases; no production data, new framework, or external service.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest

from hypothesis import example, given, settings, strategies as st

from backend.db_manager import PRKSDatabase
from backend.db_migrations import LATEST_SCHEMA_VERSION, read_schema_version
from backend.storage.config import StorageConfig
from tests.work_identity_fixtures import revert_to_v16_schema

EXAMPLES = 4
TITLE_CHARS = ("a", "Z", "0", " ", "/", ":", "|", "'", "é", "λ", "中", "🔥")
TITLES = st.lists(st.text(alphabet=TITLE_CHARS, max_size=12), min_size=1, max_size=3)

# Rows from the pre-v17 contract that must not be lost or rewritten, even when
# a work has both a Folder and Tag link.
LEGACY_ROWS = {
    "works": "SELECT id, title, status FROM works ORDER BY id",
    "tags": "SELECT id, name FROM tags ORDER BY id",
    "folders": "SELECT id, title FROM folders ORDER BY id",
    "work_tags": "SELECT work_id, tag_id FROM work_tags ORDER BY work_id, tag_id",
    "folder_files": "SELECT folder_id, work_id FROM folder_files ORDER BY folder_id, work_id",
}
# These are new v17 identity tables. Re-opening an already-migrated database
# must not duplicate or silently rewrite their rows.
IDENTITY_ROWS = {
    "manifestations": "SELECT * FROM manifestations ORDER BY id",
    "assets": "SELECT * FROM assets ORDER BY id",
    "manifestation_relations": "SELECT * FROM manifestation_relations ORDER BY rowid",
    "manifestation_identifiers": "SELECT * FROM manifestation_identifiers ORDER BY rowid",
}


def rows(conn, queries):
    return {name: conn.execute(query).fetchall() for name, query in queries.items()}


class MigrationReentryProperties(unittest.TestCase):
    @settings(max_examples=EXAMPLES, deadline=None, database=None)
    @given(suffixes=TITLES)
    @example(suffixes=[""])
    @example(suffixes=["λ/中", "🔥 | é", "A 'quoted' work"])
    def test_v16_upgrade_preserves_generated_records_and_second_start_is_stable(self, suffixes):
        with tempfile.TemporaryDirectory(prefix="prks-hypothesis-migrate-") as root:
            storage = StorageConfig.for_testing(root)
            db = PRKSDatabase(storage=storage)
            folder = db.add_folder("Migrating folder")
            tag = db.add_tag("Migrating tag")["id"]
            work_ids = []
            for index, suffix in enumerate(suffixes):
                work = db.add_work(title=f"Generated {index}: {suffix}")
                work_ids.append(work)
                db.add_work_to_folder(folder, work)
                db.add_tag_to_work(work, tag)

            with sqlite3.connect(storage.db_path) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                revert_to_v16_schema(conn)
                self.assertEqual(read_schema_version(conn), 16)
                legacy = rows(conn, LEGACY_ROWS)
                self.assertEqual(
                    conn.execute("PRAGMA foreign_key_check").fetchall(), [],
                )

            upgraded = PRKSDatabase(storage=storage)
            with sqlite3.connect(storage.db_path) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                self.assertEqual(read_schema_version(conn), LATEST_SCHEMA_VERSION)
                self.assertEqual(rows(conn, LEGACY_ROWS), legacy)
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                identity_after_upgrade = rows(conn, IDENTITY_ROWS)

            # Re-entry runs the ordinary production schema validation path.
            reopened = PRKSDatabase(storage=storage)
            with sqlite3.connect(storage.db_path) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                self.assertEqual(read_schema_version(conn), LATEST_SCHEMA_VERSION)
                self.assertEqual(rows(conn, LEGACY_ROWS), legacy)
                self.assertEqual(rows(conn, IDENTITY_ROWS), identity_after_upgrade)
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

            for index, work in enumerate(work_ids):
                wanted = f"Generated {index}: {suffixes[index]}"
                self.assertEqual(upgraded.get_work(work)["title"], wanted)
                self.assertEqual(reopened.get_work(work)["title"], wanted)
