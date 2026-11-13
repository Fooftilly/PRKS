"""The PostgreSQL backup and restore routes' rules that need no worker (PG-C1 #640, ``backend.api.maintenance``).

``tests/pg/test_postgres_library_start.py`` drives the round trip through a
real process. These tests cover who may use a route and what a staged
upload is bound to.
"""

from __future__ import annotations

import itertools
import os
import shutil
import tempfile
import time
import unittest
from typing import Any
from unittest import mock

import psycopg

from backend.api import maintenance
from backend.domain.maintenance import staged
from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.domain.identity.authorization import AccessRefused, Refusal
from tests.library_adapter import TestLibrary
from backend.storage.config import StorageConfig
from tests.pg.facade_context import provision_actor
from tests.pg.harness import PostgresHarness, get_harness


class MaintenanceApiTests(unittest.TestCase):
    harness: PostgresHarness

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.harness = get_harness()

    def setUp(self) -> None:
        super().setUp()
        name = self.harness.create_database("maint")
        self.addCleanup(self.harness.drop_database, name)
        self.db = Database(DatabaseConfig(conninfo=self.harness.conninfo(name), role="test", pool_min=0, pool_max=4))
        self.addCleanup(self.db.close)
        self.root = tempfile.mkdtemp(prefix="prks-pg-maint-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.library = TestLibrary(self.db, storage=StorageConfig.for_testing(self.root))
        self.owner = provision_actor(self.db)

    def test_a_reader_may_not_stage_restore_or_download(self) -> None:
        reader = provision_actor(self.db, name="Shared", role="reader").context
        received: list[str] = []
        calls = (
            lambda: maintenance.stage_upload(self.library, reader, self.root, received.append),
            lambda: maintenance.restore_staged(self.library, reader, self.root,
                                               {"token": "0" * 32, "confirm": "RESTORE"}),
            lambda: maintenance.archive_for_download(self.library, reader, self.root, "0" * 32),
        )
        for call in calls:
            with self.subTest(call=call), self.assertRaises(AccessRefused) as caught:
                call()
            self.assertIs(caught.exception.refusal, Refusal.FORBIDDEN)
        self.assertEqual(received, [])  # refused before the body is read

    def test_a_staged_token_is_bound_to_the_user_who_uploaded_it(self) -> None:
        token = "a" * 32
        path = staged.staged_path(self.root, token, self.owner.context.user_id)
        with open(path, "wb") as handle:
            handle.write(b"not read")
        other = provision_actor(self.db, name="Other").context
        status, body, epoch = maintenance.restore_staged(self.library, other, self.root,
                                                         {"token": token, "confirm": "RESTORE"})
        self.assertEqual((status, body["reason"], epoch), (400, "unknown_token", None))
        self.assertTrue(os.path.exists(path))

    def test_a_restore_needs_the_confirmation(self) -> None:
        status, body, _epoch = maintenance.restore_staged(self.library, self.owner.context, self.root,
                                                          {"token": "a" * 32})
        self.assertEqual((status, body["reason"]), (400, "confirmation_required"))

    def test_an_unpublished_run_is_not_downloadable(self) -> None:
        self.assertIsNone(maintenance.archive_for_download(self.library, self.owner.context, self.root,
                                                           "not-a-token"))
        self.assertIsNone(maintenance.archive_for_download(self.library, self.owner.context, self.root,
                                                           "0" * 32))

    def test_the_summary_counts_only_asset_objects_as_files(self) -> None:
        from types import SimpleNamespace

        from backend.storage.objects import NAMESPACE_ASSET_OBJECTS, NAMESPACE_PORTRAITS, StorageKey

        archive = SimpleNamespace(
            manifest={"created_at": "2026-11-09T00:00:00Z"},
            tables={"prks.works": SimpleNamespace(rows=2)},
            objects={StorageKey(NAMESPACE_ASSET_OBJECTS, "a" * 64): None,
                     StorageKey(NAMESPACE_ASSET_OBJECTS, "b" * 64): None,
                     StorageKey(NAMESPACE_PORTRAITS, "c" * 64): None})
        summary = staged.summarize(archive)
        self.assertEqual((summary["works"], summary["pdf_files"], summary["persons"]), (2, 2, None))

    def test_progress_follows_a_long_backup_until_it_publishes(self) -> None:
        # The token exists only in the ``ready`` line: the stream must not give
        # up on a backup that runs long, however many polls it takes.
        import json
        import uuid

        ctx = self.owner.context
        lines: list[dict[str, Any]] = []
        run = uuid.uuid4()

        def write_line(line: dict[str, Any]) -> None:
            lines.append(line)
            if len(lines) == 50:  # far past any old deadline in polls: the worker finishes
                with psycopg.connect(self.db.config.conninfo, autocommit=True) as conn:
                    conn.execute("UPDATE prks.jobs SET state = 'succeeded', progress = %s::jsonb"
                               " WHERE library_id = %s AND kind = 'backup.create'",
                               (json.dumps({"backup_run": str(run)}), ctx.library_id))

        with mock.patch.object(time, "monotonic", side_effect=itertools.count(0, 3600)):
            maintenance.stream_backup(self.library, ctx, write_line, poll=0)
        self.assertEqual(lines[-1]["phase"], "ready")
        self.assertEqual(lines[-1]["token"], run.hex)
        self.assertFalse(any(line["phase"] == "failed" for line in lines))

    def test_old_staged_uploads_are_pruned(self) -> None:
        old = staged.staged_path(self.root, "b" * 32, "u")
        fresh = staged.staged_path(self.root, "c" * 32, "u")
        for path in (old, fresh):
            with open(path, "wb"):
                pass
        past = time.time() - staged.STAGED_TTL_SECONDS - 5
        os.utime(old, (past, past))
        self.assertEqual(staged.prune_staged(self.root), 1)
        self.assertEqual((os.path.exists(old), os.path.exists(fresh)), (False, True))


if __name__ == "__main__":
    unittest.main()
