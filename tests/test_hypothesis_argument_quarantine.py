"""Generated SQLite v16→v17 orphan-citation quarantine invariants (#391).

A malformed legacy citation must survive in the quarantine record without
surviving as an invalid foreign key. Multiple orphans for one live Argument
advance its source revision exactly once, not once per orphan.
"""
from __future__ import annotations

import json
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
SOURCE = st.tuples(
    st.integers(min_value=0, max_value=8),
    st.sampled_from(("", "5", "9-10", "p.3")),
)
SOURCES = st.lists(SOURCE, min_size=1, max_size=3)


class ArgumentQuarantineProperties(unittest.TestCase):
    @settings(max_examples=EXAMPLES, deadline=None, database=None)
    @given(valid_sources=SOURCES, orphan_count=st.integers(min_value=1, max_value=2))
    @example(valid_sources=[(1, ""), (1, "9-10")], orphan_count=2)
    @example(valid_sources=[(8, "p.3"), (0, "5")], orphan_count=1)
    def test_orphans_preserved_once_and_live_sources_reindexed(
        self, valid_sources, orphan_count
    ):
        with tempfile.TemporaryDirectory(prefix="prks-hypothesis-quarantine-") as root:
            storage = StorageConfig.for_testing(root)
            db = PRKSDatabase(storage=storage)
            work_ids = [
                db.add_work(title=f"Live citation {index}")
                for index in range(len(valid_sources))
            ]
            argument_id = research_network.create_argument(
                db, name="Live argument", kind="argument"
            )["id"]

            with sqlite3.connect(storage.db_path) as conn:
                revert_to_v16_schema(conn)
                self.assertEqual(read_schema_version(conn), 16)
                # Corrupt legacy references are possible in old libraries with
                # FK enforcement disabled. The migration must quarantine them.
                conn.execute("PRAGMA foreign_keys = OFF")
                live_rows = []
                for index, (order, pages) in enumerate(valid_sources):
                    stamp = f"2020-01-01 00:00:{index:02d}"
                    work_id = work_ids[index]
                    conn.execute(
                        "INSERT INTO argument_sources "
                        "(argument_id, work_id, pages, order_index, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (argument_id, work_id, pages, order, stamp),
                    )
                    live_rows.append((order, work_id, pages, stamp))

                orphan_rows = []
                for index in range(orphan_count):
                    missing_work = f"W-MISSING-HYPOTHESIS-{index}"
                    stamp = f"2021-01-01 00:00:{index:02d}"
                    pages = "p.4" if index == 0 else "λ:5"
                    order = index - 1
                    cursor = conn.execute(
                        "INSERT INTO argument_sources "
                        "(argument_id, work_id, pages, order_index, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (argument_id, missing_work, pages, order, stamp),
                    )
                    orphan_rows.append((
                        cursor.lastrowid,
                        {"argument_id": argument_id, "work_id": missing_work,
                         "pages": pages, "order_index": order, "created_at": stamp},
                    ))
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
                    ).fetchone()[0],
                    len(valid_sources) + orphan_count,
                )
                self.assertEqual(argument_sync.get_sources_revision(conn, argument_id), 7)
                self.assertEqual(
                    len(conn.execute("PRAGMA foreign_key_check(argument_sources)").fetchall()),
                    orphan_count,
                )

            expected_live = [
                (index, wid,
                 work_identity.backfill_manifestation_id(wid) if pages else None,
                 pages, stamp)
                for index, (_, wid, pages, stamp)
                in enumerate(sorted(live_rows, key=lambda r: (r[0], r[1])))
            ]
            source_sql = (
                "SELECT order_index, work_id, manifestation_id, pages, created_at "
                "FROM argument_sources WHERE argument_id = ? ORDER BY order_index"
            )
            quarantine_sql = (
                "SELECT source_rowid, row_json, reason "
                "FROM migration_quarantine "
                "WHERE source_table = 'argument_sources' ORDER BY source_rowid"
            )

            # First open runs real v16→v17 migration and excludes each orphan.
            PRKSDatabase(storage=storage)
            with sqlite3.connect(storage.db_path) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                self.assertEqual(read_schema_version(conn), LATEST_SCHEMA_VERSION)
                self.assertEqual(conn.execute(source_sql, (argument_id,)).fetchall(),
                                 expected_live)
                self.assertEqual(argument_sync.get_sources_revision(conn, argument_id), 8)
                captured_quarantine = conn.execute(quarantine_sql).fetchall()
                self.assertEqual(len(captured_quarantine), orphan_count)
                for (rowid, body, reason), (wanted_id, wanted_body) in zip(
                    captured_quarantine, orphan_rows
                ):
                    self.assertEqual(rowid, wanted_id)
                    self.assertEqual(json.loads(body), wanted_body)
                    self.assertEqual(reason, "missing_parent:works")
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(work_identity.integrity_violations(conn), [])

            # Re-entry cannot insert quarantine duplicates or burn revisions.
            PRKSDatabase(storage=storage)
            with sqlite3.connect(storage.db_path) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                self.assertEqual(read_schema_version(conn), LATEST_SCHEMA_VERSION)
                self.assertEqual(conn.execute(source_sql, (argument_id,)).fetchall(),
                                 expected_live)
                self.assertEqual(conn.execute(quarantine_sql).fetchall(),
                                 captured_quarantine)
                self.assertEqual(argument_sync.get_sources_revision(conn, argument_id), 8)
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
