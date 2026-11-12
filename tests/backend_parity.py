"""The PostgreSQL leg of the parity tests (#625, PG-3; PG-C2 #641).

A parity test class opts in with :func:`parametrize_backends` and gets its
library object from :meth:`BackendParityTestCase.open_library`::

    @parametrize_backends
    class SavedViewTests(BackendParityTestCase):
        def setUp(self):
            self.db = self.open_library(StorageConfig.for_testing(tmpdir))

The library is :class:`backend.pg_facade.PostgresLibrary` over its own
harness-created database (one per test, cloned from the migrated template),
acting for a signed-in owner of a fresh Library. The SQLite side these tests
compared against is a recording since PG-C2 deleted SQLite
(``tests/parity_reference.py``), so a test compares the facade's answers with
the ones SQLite gave.

The harness needs ``PRKS_TEST_DATABASE_URL`` (``scripts/test-postgres
--parity`` sets it): without one every parity test is skipped, as the
``tests/pg`` tests are, and the Backend parity CI job runs them.
"""

from __future__ import annotations

import unittest
from typing import Any, ClassVar, TypeVar

POSTGRES = "postgres"


class BackendParityTestCase(unittest.TestCase):
    """Base of a test class whose library object is the PostgreSQL facade."""

    parity_backend: ClassVar[str] = POSTGRES

    def open_library(self, storage: Any) -> Any:
        """The facade over ``storage`` (a ``StorageConfig``) and a fresh database, dropped after the test."""
        from backend.db import Database, DatabaseConfig
        from backend.pg_facade import PostgresLibrary
        from backend.pg_facade import context as facade_context
        from tests.pg.facade_context import provision_actor
        from tests.pg.harness import get_harness

        harness = get_harness()
        name = harness.create_database("parity")
        self.addCleanup(harness.drop_database, name)
        database = Database(
            DatabaseConfig(conninfo=harness.conninfo(name), role="test", pool_min=0, pool_max=2)
        )
        self.addCleanup(database.close)
        # The facade acts for a bound request context (L-W-1 #628): a real
        # signed-in owner of a fresh Library, bound until the test ends.
        actor = provision_actor(database)
        self.addCleanup(facade_context.unbind, facade_context.bind(actor.context))
        return PostgresLibrary(database, storage=storage)


P = TypeVar("P", bound=BackendParityTestCase)


def parametrize_backends(cls: type[P]) -> type[P]:
    """Mark ``cls`` as a parity test class; it runs once, on PostgreSQL."""
    if BackendParityTestCase not in getattr(cls, "__mro__", ()):
        raise TypeError("parametrize_backends needs a BackendParityTestCase subclass")
    return cls
