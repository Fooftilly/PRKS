"""The disposable PostgreSQL Library behind every E2E server (#629 L-F-1, PR 1/3)."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.domain.identity import passwords
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
        with mock.patch.dict(os.environ, {postgres_identity.LIBRARY_BACKEND_ENV: ""}):
            self.assertEqual(
                library.server_env(),
                {
                    "PRKS_AUTH": "loopback-owner",
                    "PRKS_DATABASE_URL": library.conninfo,
                    "PRKS_EMBEDDED_WORKER": "0",
                },
            )

    def test_session_mode_adds_editors_who_sign_in_with_the_e2e_password(self) -> None:
        library = postgres_identity.provision_library(self.root, auth="session", members=("reader",))
        self.addCleanup(self._drop_quietly, library)

        self.assertEqual(library.server_env()["PRKS_AUTH"], "session")
        self.assertEqual(set(library.accounts), {"owner", "reader"})
        self.assertEqual(library.accounts["owner"], library.user_id)
        memberships = self.rows(
            library.conninfo,
            "SELECT u.login_name, m.role FROM prks.library_memberships m "
            "JOIN prks.users u ON u.id = m.user_id ORDER BY u.login_name",
        )
        self.assertEqual(
            memberships, [{"login_name": "owner", "role": "owner"}, {"login_name": "reader", "role": "editor"}]
        )
        hashes = self.rows(library.conninfo, "SELECT login_name, password_hash FROM prks.users ORDER BY login_name")
        for row in hashes:
            with self.subTest(login=row["login_name"]):
                self.assertTrue(passwords.verify_password(row["password_hash"], postgres_identity.PASSWORD))

    def test_members_need_session_mode(self) -> None:
        with self.assertRaises(ValueError):
            postgres_identity.provision_library(self.root, members=("reader",))

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


class PostgresLibraryLegTests(unittest.TestCase):
    """``PRKS_E2E_LIBRARY_BACKEND=postgres``: the seed and the server on PostgreSQL (PG-C1 #640)."""

    setUp = E2ELibraryTests.setUp
    rows = E2ELibraryTests.rows

    def test_the_selection_is_read_from_the_environment(self) -> None:
        self.assertFalse(postgres_identity.postgres_library({}))
        self.assertTrue(postgres_identity.postgres_library({postgres_identity.LIBRARY_BACKEND_ENV: "postgres"}))
        with self.assertRaises(ValueError):
            postgres_identity.postgres_library({postgres_identity.LIBRARY_BACKEND_ENV: "sqlite"})

    def test_the_server_serves_postgresql_and_runs_its_worker(self) -> None:
        library = postgres_identity.provision_library(self.root)
        self.addCleanup(library.drop)
        with mock.patch.dict(os.environ, {postgres_identity.LIBRARY_BACKEND_ENV: "postgres"}):
            env = library.server_env()
        self.assertEqual(env, {
            "PRKS_AUTH": "loopback-owner",
            "PRKS_DATABASE_URL": library.conninfo,
            "PRKS_LIBRARY_BACKEND": "postgres",
        })

    def test_the_seed_writes_the_library_through_the_facade(self) -> None:
        from tests.e2e import fixtures

        library = postgres_identity.provision_library(self.root)
        self.addCleanup(library.drop)
        ids = postgres_identity.seed_postgres_library(fixtures.seed_arguments_library, self.root, library)
        works = self.rows(library.conninfo, "SELECT id, title FROM prks.works ORDER BY title")
        self.assertEqual({w["title"] for w in works}, {fixtures.WORK_A_TITLE, fixtures.WORK_B_TITLE})
        self.assertIn(ids["work_a"], {w["id"] for w in works})
        arguments = self.rows(library.conninfo, "SELECT id FROM prks.arguments")
        self.assertIn(ids["argument_unvisited"], {a["id"] for a in arguments})
        # Both managed PDFs were adopted; their text jobs wait for the server's worker.
        assets = self.rows(library.conninfo, "SELECT storage_locator FROM prks.assets ORDER BY 1")
        self.assertEqual([a["storage_locator"] for a in assets], ["e2e-related.pdf", fixtures.PDF_NAME])
        for name in ("prks_data.db", "prks_data_testing.db"):
            self.assertFalse(os.path.lexists(os.path.join(self.root, name)))

    def test_a_seeded_research_note_is_indexed_before_the_server_starts(self) -> None:
        """The seed runs the note's reindex itself: no test waits on the worker for its mentions.

        A Work is a Research Graph node only through mentions, so a graph or
        mention count read before the queued job ran showed none (the Research
        Graph E2E timeouts on the PostgreSQL leg).
        """
        from tests.e2e import fixtures

        library = postgres_identity.provision_library(self.root)
        self.addCleanup(library.drop)
        ids = postgres_identity.seed_postgres_library(fixtures.seed_graph_context_library, self.root, library)
        mentioned = self.rows(library.conninfo, "SELECT DISTINCT work_id FROM derived.concept_mentions")
        self.assertEqual([row["work_id"] for row in mentioned], [ids["work_a"]])
        states = self.rows(
            library.conninfo,
            "SELECT n.work_id, s.content_hash = encode(sha256(convert_to(n.body, 'UTF8')), 'hex') AS current "
            "FROM prks.work_notes n LEFT JOIN derived.work_note_state s USING (library_id, work_id) "
            "WHERE n.kind = 'research'",
        )
        current = {row["work_id"]: row["current"] for row in states}
        self.assertIs(current.get(ids["work_a"]), True, "the index is the current note's")

    def test_the_seeds_call_only_ported_facade_methods(self) -> None:
        import ast

        from backend.pg_facade import PostgresLibrary
        from backend.pg_facade.registry import ported_methods
        from tests.e2e import fixtures

        with open(fixtures.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        called = {
            node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "db"
        }
        self.assertTrue(called)
        self.assertEqual(sorted(called - set(ported_methods(PostgresLibrary))), [])

    def test_the_people_seeds_fill_the_postgresql_library(self) -> None:
        from tests.e2e import fixtures

        for seed in (fixtures.seed_people_library, fixtures.seed_person_profile_draft_library):
            with self.subTest(seed=seed.__name__):
                root = os.path.join(self.root, seed.__name__)
                os.makedirs(root)
                library = postgres_identity.provision_library(root)
                self.addCleanup(library.drop)
                postgres_identity.seed_postgres_library(seed, root, library)
                groups = self.rows(library.conninfo, "SELECT id FROM prks.person_groups")
                self.assertTrue(groups)

    def test_every_seeded_managed_pdf_exists_for_its_work(self) -> None:
        from tests.e2e import fixtures

        library = postgres_identity.provision_library(self.root)
        self.addCleanup(library.drop)
        ids = postgres_identity.seed_postgres_library(fixtures.seed_work_people_library, self.root, library)
        assets = self.rows(library.conninfo, "SELECT work_id, storage_locator FROM prks.assets")
        by_work = {a["work_id"]: a["storage_locator"] for a in assets}
        self.assertEqual(
            {by_work.get(ids[key]) for key in ("people_work", "credited_work", "editor_work")},
            {"e2e-people.pdf", "e2e-credited.pdf", "e2e-editor.pdf"},
        )

    def test_an_annotated_seed_pdf_is_final_before_its_asset_is_adopted(self) -> None:
        import hashlib

        from tests.e2e import fixtures

        library = postgres_identity.provision_library(self.root)
        self.addCleanup(library.drop)
        postgres_identity.seed_postgres_library(fixtures.seed_imported_highlight_library, self.root, library)
        assets = self.rows(library.conninfo, "SELECT storage_locator, content_sha256, byte_size FROM prks.assets")
        self.assertEqual(len(assets), 2)
        pdfs_dir = fixtures.StorageConfig.for_testing(self.root).pdfs_dir
        for asset in assets:
            with open(os.path.join(pdfs_dir, asset["storage_locator"]), "rb") as fh:
                body = fh.read()
            self.assertEqual((asset["content_sha256"], asset["byte_size"]),
                             (hashlib.sha256(body).hexdigest(), len(body)), asset["storage_locator"])
