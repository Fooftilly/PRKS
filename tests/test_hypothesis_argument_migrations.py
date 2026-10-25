"""Bounded v16→v17 argument-source ordering/pin migration invariants (#391).

Generate supported legacy source rows with sparse/tied order indexes and
nonempty/empty page selections; exercise the real disposable SQLite migration.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest

from hypothesis import example, given, settings, strategies as st

from backend import argument_sync, research_network, work_identity
from backend.db_manager import PRKSDatabase
from backend.db_migrations import LATEST_SCHEMA_VERSION, read_schema_version
from backend.storage.config import StorageConfig
from tests.work_identity_fixtures import revert_to_v16_schema

EXAMPLES = 6
ENTRY = st.tuples(
    st.integers(min_value=-2, max_value=8),
    st.sampled_from(("", "1", "9-10", "p. 3", "λ:4")),
)
ENTRIES = st.lists(ENTRY, min_size=1, max_size=4)


class ArgumentSourceMigrationProperties(unittest.TestCase):
    @settings(max_examples=EXAMPLES, deadline=None, database=None)
    @given(entries=ENTRIES)
    @example(entries=[(0, ""), (0, "9-10"), (0, "1")])
    @example(entries=[(8, "p. 3"), (-2, ""), (4, "λ:4"), (4, "1")])
    def test_generated_legacy_order_and_page_pins_survive_upgrade(self, entries):
        """v17 renumbers canonical order, pins nonblank pages, retains revision."""
        with tempfile.TemporaryDirectory(prefix="prks-hypothesis-arg-migrate-") as root:
            storage = StorageConfig.for_testing(root)
            db = PRKSDatabase(storage=storage)
            work_ids = [db.add_work(title=f"Argument source {i}")
                        for i in range(len(entries))]
            argument_id = research_network.create_argument(
                db, name="Generated citations", kind="argument"
            )["id"]

            conn = sqlite3.connect(storage.db_path)
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                revert_to_v16_schema(conn)
                self.assertEqual(read_schema_version(conn), 16)
                original = []
                for index, (order, pages) in enumerate(entries):
                    timestamp = f"2020-01-01 00:00:{index:02d}"
                    work_id = work_ids[index]
                    conn.execute(
                        "INSERT INTO argument_sources "
                        "(argument_id, work_id, pages, order_index, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (argument_id, work_id, pages, order, timestamp),
                    )
                    original.append((order, work_id, pages, timestamp))
                conn.execute(
                    "INSERT INTO sync_entity_revisions "
                    "(scope_type, scope_id, revision) "
                    "VALUES ('argument-sources', ?, 7)",
                    (argument_id,),
                )
                conn.commit()
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM argument_sources "
                        "WHERE argument_id = ?", (argument_id,)
                    ).fetchone()[0], len(entries),
                )
                self.assertEqual(argument_sync.get_sources_revision(conn, argument_id), 7)
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            finally:
                conn.close()

            # With ties, v17 uses order_index then work_id, not insertion order.
            expected = [
                (position, work_id,
                 work_identity.backfill_manifestation_id(work_id) if pages else None,
                 pages, timestamp)
                for position, (_, work_id, pages, timestamp)
                in enumerate(sorted(original, key=lambda row: (row[0], row[1])))
            ]
            statement = (
                "SELECT order_index, work_id, manifestation_id, pages, created_at "
                "FROM argument_sources WHERE argument_id = ? ORDER BY order_index"
            )
            upgraded = PRKSDatabase(storage=storage)
            conn = sqlite3.connect(storage.db_path)
            try:
                self.assertEqual(read_schema_version(conn), LATEST_SCHEMA_VERSION)
                self.assertEqual(
                    conn.execute(statement, (argument_id,)).fetchall(), expected,
                )
                self.assertEqual(argument_sync.get_sources_revision(conn, argument_id), 7)
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(work_identity.integrity_violations(conn), [])
            finally:
                conn.close()

            # A second normal startup must not reorder again or alter pins.
            reopened = PRKSDatabase(storage=storage)
            conn = sqlite3.connect(storage.db_path)
            try:
                self.assertEqual(read_schema_version(conn), LATEST_SCHEMA_VERSION)
                self.assertEqual(
                    conn.execute(statement, (argument_id,)).fetchall(), expected,
                )
                self.assertEqual(argument_sync.get_sources_revision(conn, argument_id), 7)
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            finally:
                conn.close()
            self.assertIsNotNone(upgraded.get_work(work_ids[0]))
            self.assertIsNotNone(reopened.get_work(work_ids[0]))
