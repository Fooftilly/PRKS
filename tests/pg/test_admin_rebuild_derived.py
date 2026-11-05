"""``prks admin rebuild-derived`` (§8.6, §11.1; L-S-1 #637).

The command queues one ``derived.rebuild`` per Library after the same start
check and bump as every data-changing admin command; a worker runs it.
"""

from __future__ import annotations

import io
from typing import Any

import psycopg

from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.domain.identity import admin
from tests.pg.test_root_pairing import RootPairingTestCase


class RebuildDerivedTests(RootPairingTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.name, self.root = self.database(), self.new_root("lib")
        self.bootstrap(self.name, self.root)

    def open_db(self, name: str) -> Database:
        return Database(DatabaseConfig(conninfo=self.harness.conninfo(name), role="test", pool_min=0))

    def run_cli(self, *argv: str, name: str | None = None, root: str | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        target = name or self.name
        code = admin.main(["rebuild-derived", *argv, "--root", root or self.root, "--testing"],
                          environ={}, stdout=out, stderr=err, open_database=lambda _env: self.open_db(target))
        return code, out.getvalue(), err.getvalue()

    def jobs(self, name: str | None = None) -> list[dict[str, Any]]:
        with psycopg.connect(self.harness.conninfo(name or self.name)) as conn:
            conn.row_factory = psycopg.rows.dict_row
            return conn.execute("SELECT j.payload, j.state, j.library_id = l.id AS ours FROM prks.jobs j"
                                " JOIN prks.libraries l ON true WHERE j.kind = 'derived.rebuild'"
                                " ORDER BY j.created_at").fetchall()

    def library_id(self) -> str:
        with psycopg.connect(self.harness.conninfo(self.name)) as conn:
            row = conn.execute("SELECT id FROM prks.libraries").fetchone()
            assert row is not None
            return str(row[0])

    def test_queues_a_full_rebuild_once(self) -> None:
        code, out, err = self.run_cli()
        self.assertEqual((code, err), (admin.EXIT_OK, ""))
        self.assertIn("Queued the research, search, text rebuild for 1 Library", out)
        code, out, _err = self.run_cli()
        self.assertEqual(code, admin.EXIT_OK)
        self.assertIn("already queued for 1 Library", out)
        self.assertEqual([(job["payload"], job["state"], job["ours"]) for job in self.jobs()],
                         [({"scopes": ["research", "search", "text"]}, "queued", True)])

    def test_only_one_scope_of_one_library(self) -> None:
        code, out, _err = self.run_cli("--only", "text", "--library", self.library_id())
        self.assertEqual(code, admin.EXIT_OK)
        self.assertIn("Queued the text rebuild", out)
        self.assertEqual([job["payload"] for job in self.jobs()], [{"scopes": ["text"]}])

    def test_an_unknown_library_or_scope_is_refused(self) -> None:
        code, out, err = self.run_cli("--library", "no-such-library")
        self.assertEqual((code, out), (admin.EXIT_REFUSED, ""))
        self.assertIn("No Library has that id.", err)
        code, _out, _err = self.run_cli("--only", "everything")
        self.assertEqual(code, admin.EXIT_USAGE)
        self.assertEqual(self.jobs(), [])

    def test_a_root_whose_setup_is_pending_is_refused(self) -> None:
        name, root = self.database(), self.new_root("pending")
        code, out, err = self.run_cli(name=name, root=root)
        self.assertEqual((code, out), (admin.EXIT_REFUSED, ""))
        self.assertTrue(err.startswith("rebuild-derived: "))
        self.assertEqual(self.jobs(name), [])
