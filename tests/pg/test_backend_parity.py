"""The parity fixture's ``postgres`` leg over real harness databases (#625, PG-3).

Proves that the twin gets the PostgreSQL facade over its own harness-created
database (never the SQLite file, never another test's database), that the
database is dropped after the test, and that a ported method really runs
against PostgreSQL through the recorder.
"""

from __future__ import annotations

import os
import tempfile
import unittest
import unittest.mock
from typing import Any

from backend.db.database import Transaction
from backend.pg_facade import (
    FacadeMixin,
    NotPortedError,
    PostgresLibrary,
    ported,
    validate_registry,
)
from backend.storage.config import StorageConfig
from tests.backend_parity import (
    POSTGRES,
    SQLITE,
    BackendParityTestCase,
    ParityLog,
    ParityRecorder,
    with_refusal_log,
)
from tests.pg.harness import get_harness


def _probe(backend: str) -> BackendParityTestCase:
    """A fixture instance of one leg (a local class, so discovery never runs it)."""

    class Probe(BackendParityTestCase):
        parity_backend = backend

        def runTest(self) -> None:
            pass

    return Probe()


class _SavedViewsFromPostgres(FacadeMixin):
    @ported
    def get_saved_views(self) -> list[dict[str, Any]]:
        def read(tx: Transaction) -> list[dict[str, Any]]:
            row = tx.execute(
                "SELECT current_database() AS db, current_setting('transaction_read_only') AS ro"
            ).fetchone()
            assert row is not None
            return [dict(row)]

        return self._database.query(read)


class _Library(_SavedViewsFromPostgres, PostgresLibrary):
    pass


class _SavedViewsCallingUnported(FacadeMixin):
    @ported
    def get_saved_views(self) -> list[dict[str, Any]]:
        try:
            self.create_saved_view("n", {})
        except NotPortedError:
            pass
        return []


class _NestingLibrary(_SavedViewsCallingUnported, PostgresLibrary):
    pass


class PostgresLegTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="prks-parity-")
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.storage = StorageConfig.for_testing(self.root)

    def _open(self) -> tuple[BackendParityTestCase, ParityRecorder]:
        probe = _probe(POSTGRES)
        library = probe.open_library(self.storage)
        self.assertIsInstance(library, ParityRecorder)
        return probe, library

    def test_each_open_gets_its_own_harness_database_dropped_afterwards(self) -> None:
        harness = get_harness()
        first_probe, first = self._open()
        second_probe, second = self._open()
        dbnames = [
            harness_dbname(lib.parity_library.database.config.conninfo) for lib in (first, second)
        ]
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
        self.assertIsInstance(library.parity_library, PostgresLibrary)
        self.assertIs(library.parity_library.storage, self.storage)
        self.assertFalse(os.path.exists(self.storage.db_path))

    def test_sqlite_leg_never_touches_postgres(self) -> None:
        probe = _probe(SQLITE)
        with unittest.mock.patch(
            "tests.pg.harness.get_harness", side_effect=AssertionError("harness used")
        ):
            library = probe.open_library(self.storage)
        self.assertTrue(os.path.exists(self.storage.db_path))
        self.assertFalse(isinstance(library, ParityRecorder))

    def test_a_ported_method_runs_against_postgresql(self) -> None:
        self.assertEqual(validate_registry(_Library), [])
        harness = get_harness()
        name = harness.create_database("parity")
        self.addCleanup(harness.drop_database, name)
        from backend.db import Database, DatabaseConfig

        database = Database(
            DatabaseConfig(conninfo=harness.conninfo(name), role="test", pool_min=0, pool_max=1)
        )
        self.addCleanup(database.close)
        log = ParityLog()
        recorder = ParityRecorder(
            _Library(database, storage=self.storage), frozenset({"get_saved_views"}), log
        )
        self.assertEqual(recorder.get_saved_views(), [{"db": name, "ro": "on"}])
        self.assertEqual(log.ported, {"get_saved_views"})
        self.assertEqual(log.unported, set())

    def test_the_fixture_records_a_refusal_inside_a_ported_method(self) -> None:
        probe, library = self._open()
        self.addCleanup(probe.doCleanups)
        self.assertEqual(type(library.parity_library).__name__, "PostgresLibrary")
        log = ParityLog()
        facade = with_refusal_log(_NestingLibrary, log)(object(), storage=self.storage)  # type: ignore[arg-type]
        self.assertEqual(validate_registry(_NestingLibrary), [])
        recorder = ParityRecorder(facade, frozenset({"get_saved_views"}), log)
        self.assertEqual(recorder.get_saved_views(), [])
        self.assertEqual(log.unported, {"create_saved_view"})
        self.assertEqual(log.refusals, 1)


def harness_dbname(conninfo: str) -> str:
    from psycopg.conninfo import conninfo_to_dict

    return str(conninfo_to_dict(conninfo)["dbname"])


if __name__ == "__main__":
    unittest.main()
