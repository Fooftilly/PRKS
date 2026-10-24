"""The PostgreSQL Work projection keeps SQLite's key lists (L-W-1 #628).

``backend/domain/works/projection.py`` copies the legacy column lists rather
than importing ``backend/db_manager.py`` into the domain layer; this test
keeps the copies equal, so a field added on SQLite cannot silently be
missing from the PostgreSQL JSON. It needs no PostgreSQL.
"""

from __future__ import annotations

import os
import sys
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend import db_manager, work_projection
from backend.domain.works import projection


class ProjectionShapeTests(unittest.TestCase):
    def test_key_lists_match_sqlite(self) -> None:
        self.assertEqual(projection.WORK_SUMMARY_COLUMNS, db_manager._PRKS_WORK_SUMMARY_COLUMNS)
        self.assertEqual(projection.WORK_BROWSE_COLUMNS, db_manager._PRKS_WORK_BROWSE_COLUMNS)
        self.assertEqual(projection.MANIFESTATION_FIELDS, work_projection.M_FIELDS)
        self.assertEqual(projection.ABSTRACT_EXCERPT_LEN, db_manager._PRKS_ABSTRACT_EXCERPT_LEN)

    def test_detail_columns_are_the_sqlite_works_columns(self) -> None:
        import sqlite3
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(os.path.join(tmp, "shape.db"))
            try:
                with open(os.path.join(_PROJECT_DIR, "backend", "db_schema.sql"), encoding="utf-8") as fh:
                    conn.executescript(fh.read())
                columns = {row[1] for row in conn.execute("PRAGMA table_info(works)")}
            finally:
                conn.close()
        self.assertEqual(set(projection.WORK_DETAIL_COLUMNS), columns - {"citation_manifestation_id"})

    def test_importing_the_projection_needs_no_psycopg(self) -> None:
        import subprocess

        code = ("import sys; import backend.domain.works.projection; "
                "sys.exit(1 if any(m.split('.')[0] in ('psycopg', 'psycopg_pool') for m in sys.modules) else 0)")
        proc = subprocess.run([sys.executable, "-c", code], cwd=_PROJECT_DIR, check=False)
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
