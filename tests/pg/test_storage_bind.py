"""``server.bind_storage`` binding the PostgreSQL Library (PG-C2 #641).

``bind_storage(config, library_database=...)`` publishes the Library handle
(:class:`backend.library.PostgresLibrary`) for a storage root. These tests check
that the binding is frozen against later environment changes, creates no
SQLite file and no legacy processing inbox, and is published whole or not
at all. They replace the SQLite ``tests/test_storage_bind.py`` cases.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Any
from unittest.mock import patch

from backend.library import PostgresLibrary
from backend.storage.config import StorageConfig
from tests.pg.test_works_facade import WorksFacadeTestCase


class PostgresStorageBindTests(WorksFacadeTestCase):
    def setUp(self) -> None:
        super().setUp()
        import backend.server as server_module

        self.server = server_module
        published = (server_module._bound_storage, server_module.pdfs_dir, server_module.thumbs_dir,
                     server_module.db)
        self.addCleanup(self._restore, published)

    def _restore(self, published: tuple[Any, ...]) -> None:
        module = self.server
        module._bound_storage, module.pdfs_dir, module.thumbs_dir, module.db = published

    def root(self) -> str:
        path = tempfile.mkdtemp(prefix="prks-pg-bind-")
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def bind_root(self, config: StorageConfig) -> StorageConfig:
        return self.server.bind_storage(config, library_database=self.db)

    def test_the_binding_is_frozen_against_later_environment_changes(self) -> None:
        config = StorageConfig.for_testing(self.root())
        bound = self.bind_root(config)
        self.assertIs(self.server._bound_storage, bound)
        self.assertIsInstance(self.server.db, PostgresLibrary)
        with patch.dict(os.environ, {"PRKS_STORAGE": "/tmp/changed-after-bind", "PRKS_TESTING": "1",
                                     "PRKS_FOR_PROCESSING_DIR": "/tmp/inbox-after-bind"}):
            self.assertEqual(self.server.pdfs_dir, config.pdfs_dir)
            self.assertEqual(self.server._bound_storage.pdfs_dir, config.pdfs_dir)
            self.assertEqual(self.server.db.storage.pdfs_dir, config.pdfs_dir)

    def test_the_binding_opens_no_sqlite_file_and_no_inbox(self) -> None:
        config = StorageConfig.for_testing(self.root())
        self.bind_root(config)
        # The SQLite-era inbox directory (``for_processing/``) is not made either.
        for path in (config.db_path, config.index_db_path, config.research_index_db_path,
                     os.path.join(config.root, "for_processing")):
            self.assertFalse(os.path.exists(path), path)
        self.assertTrue(os.path.isdir(config.thumbs_dir))

    def test_a_rebind_publishes_the_new_root_whole(self) -> None:
        first = self.bind_root(StorageConfig.for_testing(self.root()))
        db = self.server.db
        second = self.bind_root(StorageConfig.for_testing(self.root()))
        self.assertIs(self.server._bound_storage, second)
        self.assertIsNot(self.server.db, db)
        self.assertIs(self.server.db.storage, second)
        self.assertEqual(self.server.pdfs_dir, second.pdfs_dir)
        self.assertNotEqual(second.pdfs_dir, first.pdfs_dir)

    def test_a_failed_rebind_keeps_the_previous_binding(self) -> None:
        first = self.bind_root(StorageConfig.for_testing(self.root()))
        db = self.server.db
        # Building the Library handle fails after the root's directories were made.
        with patch("backend.library.PostgresLibrary", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.bind_root(StorageConfig.for_testing(self.root()))
        self.assertIs(self.server._bound_storage, first)
        self.assertIs(self.server.db, db)
        self.assertIsInstance(db, PostgresLibrary)
        self.assertEqual(self.server.pdfs_dir, first.pdfs_dir)
