"""``server.bind_storage`` binding the PostgreSQL Library (PG-C2 #641).

``bind_storage(config, library_database=...)`` publishes the facade and its
text and research index adapters for a storage root. These tests check
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

from backend.pg_facade import PostgresLibrary
from backend.pg_facade.text_index import PostgresTextIndex
from backend.research_index import get_research_index, replace_research_index, reset_research_index
from backend.storage.config import StorageConfig
from backend.text_index import get_text_index, replace_text_index, reset_text_index
from tests.pg.test_works_facade import WorksFacadeTestCase


class PostgresStorageBindTests(WorksFacadeTestCase):
    def setUp(self) -> None:
        super().setUp()
        import backend.server as server_module

        self.server = server_module
        published = (server_module._bound_storage, server_module.pdfs_dir, server_module.thumbs_dir,
                     server_module.processing_dir, server_module.db, server_module.text_index,
                     server_module.research_index)
        self.addCleanup(self._restore, published, self._current(get_text_index),
                        self._current(get_research_index))

    @staticmethod
    def _current(getter: Any) -> Any:
        try:
            return getter()
        except RuntimeError:
            return None

    def _restore(self, published: tuple[Any, ...], index: Any, research: Any) -> None:
        module = self.server
        (module._bound_storage, module.pdfs_dir, module.thumbs_dir, module.processing_dir, module.db,
         module.text_index, module.research_index) = published
        replace_text_index(index) if index is not None else reset_text_index()
        replace_research_index(research) if research is not None else reset_research_index()

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
        self.assertIs(get_text_index(), self.server.text_index)
        self.assertIs(get_research_index(), self.server.research_index)
        with patch.dict(os.environ, {"PRKS_STORAGE": "/tmp/changed-after-bind", "PRKS_TESTING": "1",
                                     "PRKS_FOR_PROCESSING_DIR": "/tmp/inbox-after-bind"}):
            self.assertEqual(self.server.pdfs_dir, config.pdfs_dir)
            self.assertEqual(self.server._bound_storage.pdfs_dir, config.pdfs_dir)
            self.assertEqual(self.server.db.storage.pdfs_dir, config.pdfs_dir)

    def test_the_binding_opens_no_sqlite_file_and_no_inbox(self) -> None:
        config = StorageConfig.for_testing(self.root())
        self.bind_root(config)
        for path in (config.db_path, config.index_db_path, config.research_index_db_path,
                     config.processing_dir):
            self.assertFalse(os.path.exists(path), path)
        self.assertTrue(os.path.isdir(config.thumbs_dir))

    def test_a_rebind_publishes_the_new_root_whole(self) -> None:
        first = self.bind_root(StorageConfig.for_testing(self.root()))
        db, index = self.server.db, self.server.text_index
        second = self.bind_root(StorageConfig.for_testing(self.root()))
        self.assertIs(self.server._bound_storage, second)
        self.assertIsNot(self.server.db, db)
        self.assertIsNot(self.server.text_index, index)
        self.assertIs(get_text_index(), self.server.text_index)
        self.assertEqual(self.server.pdfs_dir, second.pdfs_dir)
        self.assertNotEqual(second.pdfs_dir, first.pdfs_dir)

    def test_a_failed_rebind_keeps_the_previous_binding(self) -> None:
        first = self.bind_root(StorageConfig.for_testing(self.root()))
        db, index, research = self.server.db, self.server.text_index, self.server.research_index
        # Building the adapters fails, or publishing them fails halfway.
        for target in ("backend.pg_facade.text_index.PostgresTextIndex", "backend.server.replace_research_index"):
            with self.subTest(failing=target), patch(target, side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    self.bind_root(StorageConfig.for_testing(self.root()))
        self.assertIs(self.server._bound_storage, first)
        self.assertIs(self.server.db, db)
        self.assertIs(self.server.text_index, index)
        self.assertIs(self.server.research_index, research)
        self.assertIs(get_text_index(), index)
        self.assertIsInstance(index, PostgresTextIndex)
        self.assertEqual(self.server.pdfs_dir, first.pdfs_dir)
