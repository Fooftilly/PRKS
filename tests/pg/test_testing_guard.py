"""``--testing`` proves its database is a testing one before it writes (PG-C2 #641).

``backend.db.testing_guard`` accepts only a database marked for testing
(``prks-testing``, set by ``scripts/dev-postgres``, or the harness's own
mark) whose instance, if any, says ``testing``. A ``--testing`` web, migrate
or worker process given any other database exits 2 before the schema step:
nothing is created in it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import sql

from backend.db.migrate import migrate
from backend.db.testing_guard import (
    TESTING_MARK,
    TestingDatabaseRefused,
    is_testing_mark,
    prove_testing_database,
)
from tests.pg.harness import get_harness

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class MarkTests(unittest.TestCase):
    def test_only_the_two_testing_marks_count(self) -> None:
        self.assertTrue(is_testing_mark("prks-testing"))
        self.assertTrue(is_testing_mark("prks-test-harness:run1"))
        for comment in (None, "", "prks-test-harness:", "prks-testing ", "PRKS-TESTING", "production", 1):
            with self.subTest(comment=comment):
                self.assertFalse(is_testing_mark(comment))


class GuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = get_harness()
        self.name = self.harness.create_database("guard", empty=True)
        self.addCleanup(self.harness.drop_database, self.name)
        # Kept: once the mark changes the harness no longer hands it out.
        self.conninfo = self.harness.conninfo(self.name)
        self.marker = self.harness.marker_of(self.name)

    def comment(self, value: Any) -> None:
        with psycopg.connect(self.conninfo, autocommit=True) as conn:
            conn.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(sql.Identifier(self.name), sql.Literal(value)))

    @contextmanager
    def marked(self, value: Any) -> Iterator[None]:
        self.comment(value)
        try:
            yield
        finally:
            self.comment(self.marker)

    def refused(self, reason: str) -> TestingDatabaseRefused:
        with self.assertRaises(TestingDatabaseRefused) as caught:
            prove_testing_database(self.conninfo)
        self.assertEqual(caught.exception.reason, reason, caught.exception.message)
        return caught.exception

    def schemas(self) -> list[str]:
        with psycopg.connect(self.conninfo) as conn:
            return [row[0] for row in conn.execute(
                "SELECT nspname FROM pg_namespace WHERE nspname = 'prks'").fetchall()]

    def test_a_harness_or_dev_postgres_database_is_accepted(self) -> None:
        prove_testing_database(self.conninfo)
        with self.marked(TESTING_MARK):
            prove_testing_database(self.conninfo)

    def test_an_unmarked_database_is_refused_with_the_fix(self) -> None:
        for comment in (None, "the team's Library", "prks-testing-ish"):
            with self.subTest(comment=comment), self.marked(comment):
                message = self.refused("not_a_testing_database").message
                self.assertIn("scripts/dev-postgres", message)
                self.assertIn(f"COMMENT ON DATABASE \"{self.name}\" IS '{TESTING_MARK}'", message)

    def test_a_marked_database_with_a_real_instance_is_refused(self) -> None:
        migrate(self.conninfo)
        with psycopg.connect(self.conninfo, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO prks.instance (id, storage_root_id, setup_state, testing, start_generation, "
                "start_token) VALUES ('IN-' || repeat('A', 32), 'SR-' || repeat('a', 32), 'ready', false, 1, 'token')"
            )
        self.refused("testing_mismatch")

    def test_an_unreachable_server_is_refused(self) -> None:
        with self.assertRaises(TestingDatabaseRefused) as caught:
            prove_testing_database("postgresql://nobody@127.0.0.1:1/none?connect_timeout=2")
        self.assertEqual(caught.exception.reason, "database_unavailable")
        self.assertIn("scripts/dev-postgres", caught.exception.message)

    def test_the_proof_writes_nothing(self) -> None:
        prove_testing_database(self.conninfo)
        self.assertEqual(self.schemas(), [])

    def run_app(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("PRKS_")}
        env["PRKS_DATABASE_URL"] = self.conninfo
        with tempfile.TemporaryDirectory(prefix="prks-guard-") as root:
            return subprocess.run(
                [sys.executable, "prks_app.py", *args, "--testing", "--storage-root", root],
                cwd=_PROJECT_DIR, env=env, capture_output=True, text=True, timeout=120,
            )

    def test_testing_processes_refuse_an_unmarked_database_before_the_schema_step(self) -> None:
        with self.marked(None):
            for role in ([], ["migrate"], ["worker"]):
                with self.subTest(role=role):
                    done = self.run_app(*role)
                    self.assertEqual(done.returncode, 2, done.stderr)
                    self.assertIn("--testing only uses a database marked for testing", done.stderr)
                    self.assertEqual(self.schemas(), [], "nothing was created in the database")


if __name__ == "__main__":
    unittest.main()
