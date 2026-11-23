"""``prks_app.py --testing --reset-db`` empties only a testing instance, and only once confirmed.

``backend.testing_reset`` drops every PRKS schema of a database that
``backend.db.testing_guard`` accepts and deletes the data root's files, after
the person types ``RESET`` at a terminal. Anything else (another answer, no
terminal, an unmarked database, a root that is not this instance's, a running
process on the root) deletes nothing.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from typing import Any

import psycopg
from psycopg import sql

from backend.db.migrate import is_empty_database, migrate, verify_schema
from backend.storage.file_lock import SharedFileLock
from backend.storage.root_marker import (
    MAINTENANCE_DIRNAME,
    ROOT_LOCK_NAME,
    lease_path,
    marker_path,
    new_marker_document,
)
from backend.testing_reset import CONFIRM_WORD, ResetRefused, run, warning_text, plan_reset
from tests.pg.harness import get_harness

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_INSTANCE_ID = "IN-" + "A" * 32


class TestingResetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = get_harness()
        self.name = self.harness.create_database("reset", empty=True)
        self.addCleanup(self.harness.drop_database, self.name)
        self.conninfo = self.harness.conninfo(self.name)
        self.mark = self.harness.marker_of(self.name)
        tmp = tempfile.TemporaryDirectory(prefix="prks-reset-")
        self.addCleanup(tmp.cleanup)
        self.root = os.path.join(tmp.name, "root")
        self.build_instance()

    # --- fixtures -------------------------------------------------------------

    def build_instance(self, *, testing: bool = True) -> None:
        """A migrated database with an instance row, and a root paired with it holding files."""
        migrate(self.conninfo)
        with psycopg.connect(self.conninfo, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO prks.instance (id, storage_root_id, setup_state, testing, start_generation, "
                "start_token) VALUES (%s, 'SR-' || repeat('a', 32), 'ready', %s, 1, %s)",
                (_INSTANCE_ID, testing, "b" * 32),
            )
            # A stale restore staging schema is dropped too.
            conn.execute("CREATE SCHEMA restore_" + "c" * 32)
        os.makedirs(os.path.join(self.root, "pdfs"))
        os.makedirs(os.path.join(self.root, MAINTENANCE_DIRNAME))
        with open(os.path.join(self.root, "pdfs", "a.pdf"), "wb") as handle:
            handle.write(b"%PDF-1.7 synthetic")
        with open(os.path.join(self.root, MAINTENANCE_DIRNAME, ROOT_LOCK_NAME), "wb"):
            pass
        with open(os.path.join(self.root, MAINTENANCE_DIRNAME, "stale-job.json"), "w") as handle:
            handle.write("{}")
        document: dict[str, Any] = new_marker_document()
        document.update(instance_id=_INSTANCE_ID, start_generation=1, start_token="b" * 32)
        with open(marker_path(self.root), "w", encoding="utf-8") as handle:
            json.dump(document, handle)

    def comment(self, value: Any) -> None:
        with psycopg.connect(self.conninfo, autocommit=True) as conn:
            conn.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(sql.Identifier(self.name), sql.Literal(value)))

    def schemas(self) -> list[str]:
        with psycopg.connect(self.conninfo) as conn:
            return [str(row[0]) for row in conn.execute(
                "SELECT nspname FROM pg_namespace WHERE nspname !~ '^pg_' "
                "AND nspname NOT IN ('public', 'information_schema') ORDER BY 1").fetchall()]

    def files(self) -> list[str]:
        found = []
        for dirpath, _dirs, names in os.walk(self.root):
            for name in names:
                found.append(os.path.relpath(os.path.join(dirpath, name), self.root))
        return sorted(found)

    def reset(self, answer: str) -> bool:
        out = io.StringIO()
        done = run(self.conninfo, self.root, stdin=io.StringIO(), stdout=out, read=lambda: answer)
        self.prompt = out.getvalue()
        return done

    def assert_untouched(self) -> None:
        self.assertEqual(self.schemas(), ["derived", "ext", "prks", "restore_" + "c" * 32])
        self.assertIn(os.path.join("pdfs", "a.pdf"), self.files())
        self.assertIn("prks-root.json", self.files())

    # --- the reset ------------------------------------------------------------

    def test_a_confirmed_reset_empties_the_database_and_the_root(self) -> None:
        # The case --reset-db is for: an applied schema file changed since.
        with psycopg.connect(self.conninfo, autocommit=True) as conn:
            conn.execute("UPDATE prks.schema_migrations SET sha256 = repeat('0', 64) WHERE version = 0")
        with self.assertRaises(Exception):
            verify_schema(self.conninfo)

        self.assertTrue(self.reset(CONFIRM_WORD + "\n"))

        self.assertEqual(self.schemas(), [])
        with psycopg.connect(self.conninfo) as conn:
            self.assertTrue(is_empty_database(conn))
        self.assertEqual(self.harness.marker_of(self.name), self.mark, "the testing mark is kept")
        self.assertEqual(self.files(), [os.path.join(MAINTENANCE_DIRNAME, ROOT_LOCK_NAME)])
        # The start that follows applies the schema to the empty database.
        migrate(self.conninfo)
        verify_schema(self.conninfo)

    def test_the_prompt_says_what_is_deleted_and_that_it_cannot_be_undone(self) -> None:
        self.reset("no\n")
        self.assertIn(self.name, self.prompt)
        self.assertIn(self.root, self.prompt)
        self.assertIn("cannot be undone", self.prompt)
        self.assertIn("new, empty testing database", self.prompt)
        self.assertIn(f"Type {CONFIRM_WORD}", self.prompt)
        self.assertNotIn("password", self.prompt.lower())
        plan = plan_reset(self.conninfo, self.root)
        self.assertEqual(warning_text(plan), self.prompt.split("Type ")[0])

    def test_any_other_answer_deletes_nothing(self) -> None:
        for answer in ("", "\n", "reset\n", "yes\n", "RESET please\n"):
            with self.subTest(answer=answer):
                self.assertFalse(self.reset(answer))
                self.assert_untouched()

    def test_without_a_terminal_nothing_is_asked_or_deleted(self) -> None:
        with self.assertRaises(ResetRefused) as caught:
            run(self.conninfo, self.root, stdin=io.StringIO(CONFIRM_WORD + "\n"), stdout=io.StringIO())
        self.assertEqual(caught.exception.reason, "no_terminal")
        self.assert_untouched()

    def test_an_unmarked_database_is_refused(self) -> None:
        self.comment("the team's Library")
        self.addCleanup(self.comment, self.mark)
        with self.assertRaises(ResetRefused) as caught:
            self.reset(CONFIRM_WORD)
        self.assertEqual(caught.exception.reason, "not_a_testing_database")
        self.assert_untouched()

    def test_a_root_of_another_instance_is_refused(self) -> None:
        with psycopg.connect(self.conninfo, autocommit=True) as conn:
            conn.execute("UPDATE prks.instance SET id = %s", ("IN-" + "B" * 32,))
        with self.assertRaises(ResetRefused) as caught:
            self.reset(CONFIRM_WORD)
        self.assertEqual(caught.exception.reason, "root_not_paired")
        self.assert_untouched()

    def test_an_unmarked_root_with_files_is_refused(self) -> None:
        os.unlink(marker_path(self.root))
        with self.assertRaises(ResetRefused) as caught:
            self.reset(CONFIRM_WORD)
        self.assertEqual(caught.exception.reason, "root_not_paired")
        self.assertIn(os.path.join("pdfs", "a.pdf"), self.files())

    def test_a_running_process_on_the_root_is_refused(self) -> None:
        lease = SharedFileLock.acquire(lease_path(self.root))
        self.addCleanup(lease.release)
        with self.assertRaises(ResetRefused) as caught:
            self.reset(CONFIRM_WORD)
        self.assertEqual(caught.exception.reason, "root_in_use")
        self.assert_untouched()

    def test_a_missing_root_needs_only_the_database(self) -> None:
        missing = os.path.join(os.path.dirname(self.root), "missing")
        self.assertTrue(run(self.conninfo, missing, stdin=io.StringIO(), stdout=io.StringIO(),
                            read=lambda: CONFIRM_WORD))
        self.assertEqual(self.schemas(), [])
        self.assertFalse(os.path.lexists(missing))

    # --- prks_app.py ----------------------------------------------------------

    def run_app(self, *args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("PRKS_")}
        env["PRKS_DATABASE_URL"] = self.conninfo
        return subprocess.run(
            [sys.executable, "prks_app.py", *args, "--storage-root", self.root],
            cwd=_PROJECT_DIR, env=env, input=stdin, capture_output=True, text=True, timeout=120,
        )

    def test_the_app_refuses_reset_db_without_testing(self) -> None:
        done = self.run_app("--reset-db")
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("use it with --testing", done.stderr)
        self.assert_untouched()

    def test_a_changed_schema_file_points_a_testing_start_at_reset_db(self) -> None:
        with psycopg.connect(self.conninfo, autocommit=True) as conn:
            conn.execute("UPDATE prks.schema_migrations SET sha256 = repeat('0', 64) WHERE version = 0")
        done = self.run_app("--testing")
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("changed after this database applied it", done.stderr)
        self.assertIn("prks_app.py --testing --reset-db", done.stderr)
        self.assert_untouched()

    def test_the_app_never_reads_the_confirmation_from_a_pipe(self) -> None:
        done = self.run_app("--testing", "--reset-db", stdin=CONFIRM_WORD + "\n")
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("needs a terminal", done.stderr)
        self.assert_untouched()


if __name__ == "__main__":
    unittest.main()
