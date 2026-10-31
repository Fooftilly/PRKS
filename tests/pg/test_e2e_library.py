"""The disposable PostgreSQL Library behind every E2E server (#629 L-F-1, PR 1/3)."""

from __future__ import annotations

import os
import tempfile
import unittest

from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.domain.identity.errors import RootPairingRefused
from tests.e2e import postgres_identity
from tests.pg.harness import get_harness


class E2ELibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = get_harness()
        self.harness.template()  # made once per run, not by the Library under test
        tmp = tempfile.TemporaryDirectory(prefix="prks-e2e-pg-")
        self.addCleanup(tmp.cleanup)
        self.root = os.path.join(tmp.name, "storage")
        os.mkdir(self.root)

    def rows(self, conninfo: str, query: str) -> list[dict]:
        db = Database(DatabaseConfig(conninfo=conninfo, role="test", pool_min=0, pool_max=1))
        try:
            return db.command(lambda tx: tx.execute(query).fetchall())
        finally:
            db.close()

    def test_bootstraps_one_owner_and_pairs_the_empty_root(self) -> None:
        library = postgres_identity.provision_library(self.root)
        self.addCleanup(self._drop_quietly, library)

        users = self.rows(library.conninfo, "SELECT id, login_name, display_name FROM prks.users")
        self.assertEqual(
            users,
            [{"id": library.user_id, "login_name": "owner", "display_name": "E2E Owner"}],
        )
        memberships = self.rows(
            library.conninfo, "SELECT library_id, user_id, role FROM prks.library_memberships"
        )
        self.assertEqual(
            memberships,
            [{"library_id": library.library_id, "user_id": library.user_id, "role": "owner"}],
        )
        instance = self.rows(library.conninfo, "SELECT id FROM prks.instance")
        self.assertEqual([row["id"] for row in instance], [library.instance_id])
        self.assertEqual(
            library.server_env(),
            {
                "PRKS_AUTH": "loopback-owner",
                "PRKS_DATABASE_URL": library.conninfo,
                "PRKS_EMBEDDED_WORKER": "0",
            },
        )

    def test_drop_removes_the_database(self) -> None:
        library = postgres_identity.provision_library(self.root)
        self.assertTrue(self.harness.exists(library.database))
        library.drop()
        self.assertFalse(self.harness.exists(library.database))

    def test_a_root_already_holding_objects_is_refused_and_leaves_no_database(self) -> None:
        # Why the harness pairs the root before copying the seed in.
        os.makedirs(os.path.join(self.root, "pdfs"))
        with open(os.path.join(self.root, "pdfs", "seeded.pdf"), "wb") as handle:
            handle.write(b"%PDF-1.4\n")
        before = set(self.harness._created)  # noqa: SLF001 - the run's own bookkeeping
        with self.assertRaises(RootPairingRefused):
            postgres_identity.provision_library(self.root)
        self.assertEqual(set(self.harness._created), before)  # noqa: SLF001

    def _drop_quietly(self, library: postgres_identity.E2ELibrary) -> None:
        if self.harness.exists(library.database):
            library.drop()


if __name__ == "__main__":
    unittest.main()
