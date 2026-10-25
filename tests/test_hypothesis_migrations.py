"""Generated v16 -> v17 migration and re-entry invariants (#391).

Vary inputs consumed by the real Work identity migration: mirrored metadata,
PDF/stream origin Assets, annotation-backed Assets and rebuilt Work-role leaves.
Use disposable SQLite storage and the existing exact v16 downgrade fixture.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest

from hypothesis import example, given, settings, strategies as st

from backend import work_identity
from backend.db_manager import PRKSDatabase
from backend.db_migrations import LATEST_SCHEMA_VERSION, read_schema_version
from backend.storage.config import StorageConfig
from tests.work_identity_fixtures import revert_to_v16_schema

EXAMPLES = 4
YT_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
CHARS = ("a", "Z", "0", " ", "/", ":", "|", "'", "é", "λ", "中", "🔥")
TEXT = st.text(alphabet=CHARS, max_size=12)
MODES = ("notes", "pdf", "video", "annotation")
CASES = st.lists(
    st.tuples(st.sampled_from(MODES), TEXT, TEXT), min_size=1, max_size=4
)

LEGACY_ROWS = {
    "tags": "SELECT id, name FROM tags ORDER BY id",
    "folders": "SELECT id, title FROM folders ORDER BY id",
    "work_tags": "SELECT work_id, tag_id FROM work_tags ORDER BY work_id, tag_id",
    "folder_files": "SELECT folder_id, work_id FROM folder_files ORDER BY folder_id, work_id",
    # Both tables are rebuilt by v17; capture their surviving v16 columns.
    "annotations": (
        "SELECT id, work_id, type, content, page_index FROM annotations ORDER BY id"
    ),
    "roles": (
        "SELECT person_id, work_id, role_type, order_index, credit_name "
        "FROM roles ORDER BY person_id, work_id, role_type, order_index"
    ),
}
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
    @given(cases=CASES)
    @example(cases=[
        ("notes", "No asset", "10.1/note"),
        ("pdf", "λ / 中", "10.1/pdf"),
        ("video", "🔥 video", "10.1/video"),
        ("annotation", "Annotation", "10.1/ann"),
    ])
    @example(cases=[("pdf", "A 'quoted' work", "10.1/a"),
                    ("pdf", "Shared locator?", "10.1/b")])
    def test_v16_upgrade_preserves_generated_records_and_second_start_is_stable(self, cases):
        with tempfile.TemporaryDirectory(prefix="prks-hypothesis-migrate-") as root:
            storage = StorageConfig.for_testing(root)
            db = PRKSDatabase(storage=storage)
            folder = db.add_folder("Migrating folder")
            tag = db.add_tag("Migrating tag")["id"]
            person = db.add_person("Ada", "Migrator")
            work_cases = []
            for index, (mode, suffix, citation) in enumerate(cases):
                options = {"title": f"Generated {index}: {suffix}",
                           "doi": "10.1234/" + citation}
                if mode == "pdf":
                    options.update(source_kind="pdf",
                                   file_path=f"/api/pdfs/migration-{index}.pdf")
                elif mode == "video":
                    options.update(source_kind="video", source_url=YT_URL)
                work = db.add_work(**options)
                work_cases.append((work, mode, options["title"], options["doi"]))
                db.add_work_to_folder(folder, work)
                db.add_tag_to_work(work, tag)
                db.add_role(person, work, "Author")

            with sqlite3.connect(storage.db_path) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                revert_to_v16_schema(conn)
                # An annotation on an otherwise metadata-only Work still
                # needs a real origin Asset during v17 migration.
                for index, (work, mode, _, _) in enumerate(work_cases):
                    if mode == "annotation":
                        conn.execute(
                            "INSERT INTO annotations "
                            "(id, work_id, type, content, page_index) "
                            "VALUES (?, ?, 'highlight', ?, 0)",
                            (f"generated-ann-{index}", work, "Selection " + str(index)),
                        )
                conn.commit()
                self.assertEqual(read_schema_version(conn), 16)
                # Include *every* legacy Work column, not only title/status:
                # schema-17 adds two pointer columns but must preserve the
                # contents of the original Work metadata columns verbatim.
                work_columns = [row[1] for row in conn.execute("PRAGMA table_info(works)")]
                work_sql = ("SELECT " + ", ".join('"' + col + '"' for col in work_columns)
                            + " FROM works ORDER BY id")
                snapshot_queries = {**LEGACY_ROWS, "works": work_sql}
                legacy = rows(conn, snapshot_queries)
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

            upgraded = PRKSDatabase(storage=storage)
            with sqlite3.connect(storage.db_path) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                self.assertEqual(read_schema_version(conn), LATEST_SCHEMA_VERSION)
                self.assertEqual(rows(conn, snapshot_queries), legacy)
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(work_identity.integrity_violations(conn), [])
                self.assertEqual(work_identity.mirror_drift(conn), [])
                # Verify actual origin identity backfill, not just stability
                # of an empty identity-table snapshot.
                for work, mode, _, doi in work_cases:
                    mf_id = work_identity.backfill_manifestation_id(work)
                    self.assertEqual(
                        conn.execute(
                            "SELECT primary_manifestation_id FROM works WHERE id = ?",
                            (work,),
                        ).fetchone()[0], mf_id,
                    )
                    self.assertEqual(
                        conn.execute(
                            "SELECT id, work_id, doi FROM manifestations "
                            "WHERE origin_work_id = ?", (work,),
                        ).fetchall(), [(mf_id, work, doi)],
                    )
                    assets = conn.execute(
                        "SELECT id, manifestation_id, kind FROM assets "
                        "WHERE origin_work_id = ?", (work,),
                    ).fetchall()
                    if mode == "notes":
                        self.assertEqual(assets, [])
                    else:
                        self.assertEqual(
                            assets, [(work_identity.backfill_asset_id(work), mf_id,
                                      "external_stream" if mode == "video" else "managed_file")],
                        )
                    self.assertEqual(
                        conn.execute(
                            "SELECT primary_asset_id FROM manifestations WHERE id = ?",
                            (mf_id,),
                        ).fetchone()[0],
                        None if mode == "notes" else work_identity.backfill_asset_id(work),
                    )
                    if mode == "annotation":
                        self.assertEqual(
                            conn.execute(
                                "SELECT asset_id FROM annotations WHERE work_id = ?",
                                (work,),
                            ).fetchall(), [(work_identity.backfill_asset_id(work),)],
                        )
                identity_after_upgrade = rows(conn, IDENTITY_ROWS)

            # Re-entry follows the real production schema-validation path;
            # neither legacy payloads nor newly backfilled identity can drift.
            reopened = PRKSDatabase(storage=storage)
            with sqlite3.connect(storage.db_path) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                self.assertEqual(read_schema_version(conn), LATEST_SCHEMA_VERSION)
                self.assertEqual(rows(conn, snapshot_queries), legacy)
                self.assertEqual(rows(conn, IDENTITY_ROWS), identity_after_upgrade)
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(work_identity.integrity_violations(conn), [])
                self.assertEqual(work_identity.mirror_drift(conn), [])
            for work, _mode, title, _doi in work_cases:
                self.assertEqual(upgraded.get_work(work)["title"], title)
                self.assertEqual(reopened.get_work(work)["title"], title)
