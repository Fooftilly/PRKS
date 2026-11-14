"""The parity fixture over real harness databases (#625, PG-3; PG-C2 #641).

Proves that a parity test gets the PostgreSQL facade over its own
harness-created database (never another test's database), that the database
is dropped after the test, and that the facade really runs against it.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from backend.library import PostgresLibrary
from backend.storage.config import StorageConfig
from tests.backend_parity import BackendParityTestCase
from tests.pg.harness import get_harness


def _probe() -> BackendParityTestCase:
    """A fixture instance (a local class, so discovery never runs it)."""

    class Probe(BackendParityTestCase):
        def runTest(self) -> None:
            pass

    return Probe()


class PostgresLegTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="prks-parity-")
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.storage = StorageConfig.for_testing(self.root)

    def _open(self) -> tuple[BackendParityTestCase, PostgresLibrary]:
        probe = _probe()
        library = probe.open_library(self.storage)
        self.assertIsInstance(library, PostgresLibrary)
        return probe, library

    def test_each_open_gets_its_own_harness_database_dropped_afterwards(self) -> None:
        harness = get_harness()
        first_probe, first = self._open()
        second_probe, second = self._open()
        dbnames = [harness_dbname(lib.database.config.conninfo) for lib in (first, second)]
        self.assertNotEqual(dbnames[0], dbnames[1])
        for name in dbnames:
            self.assertTrue(name.startswith("prks_test_"))
            self.assertEqual(harness.marker_of(name), harness.marker)
        first_probe.doCleanups()
        second_probe.doCleanups()
        for name in dbnames:
            self.assertFalse(harness.exists(name))

    def test_postgres_leg_never_opens_the_sqlite_database(self) -> None:
        probe, library = self._open()
        self.addCleanup(probe.doCleanups)
        self.assertIs(library.storage, self.storage)
        self.assertEqual(os.listdir(self.root), [])

    def test_a_ported_method_runs_against_postgresql(self) -> None:
        probe, library = self._open()
        self.addCleanup(probe.doCleanups)
        self.assertEqual(library.get_all_tags(), [])
        tag_id = library.add_tag("Ethics")["id"]
        self.assertEqual([tag["id"] for tag in library.get_all_tags()], [tag_id])
        count = library.database.query(lambda tx: tx.execute(
            "SELECT count(*) AS n FROM prks.tags WHERE id = %s", (tag_id,)).fetchone()["n"])
        self.assertEqual(count, 1)


def harness_dbname(conninfo: str) -> str:
    from psycopg.conninfo import conninfo_to_dict

    return str(conninfo_to_dict(conninfo)["dbname"])


if __name__ == "__main__":
    unittest.main()
