"""``server.bind_storage`` binding the PostgreSQL Library (PG-C2 #641).

``bind_storage(config, library_database=...)`` builds the served
:class:`backend.web.app.App` with the Library handle
(:class:`backend.library.PostgresLibrary`) for a storage root. These tests
check that the binding is frozen against later environment changes, creates
no SQLite file and no legacy processing inbox, and that a failed binding
changes nothing already served.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from unittest.mock import patch

from backend.library import PostgresLibrary
from backend.storage.config import StorageConfig
from backend.web.app import App
from tests.pg.test_works_facade import WorksFacadeTestCase


class PostgresStorageBindTests(WorksFacadeTestCase):
    """``bind_storage`` builds one :class:`App`; no module state is published (#1424)."""

    def root(self) -> str:
        path = tempfile.mkdtemp(prefix="prks-pg-bind-")
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def bind_root(self, config: StorageConfig) -> App:
        from backend.server import bind_storage

        return bind_storage(config, library_database=self.db)

    def test_the_binding_is_frozen_against_later_environment_changes(self) -> None:
        config = StorageConfig.for_testing(self.root())
        app = self.bind_root(config)
        self.assertIs(app.storage, config)
        self.assertIsInstance(app.library, PostgresLibrary)
        with patch.dict(os.environ, {"PRKS_STORAGE": "/tmp/changed-after-bind", "PRKS_TESTING": "1",
                                     "PRKS_FOR_PROCESSING_DIR": "/tmp/inbox-after-bind"}):
            self.assertEqual(app.pdfs_dir, config.pdfs_dir)
            library = app.require_library()
            assert library.storage is not None
            self.assertEqual(library.storage.pdfs_dir, config.pdfs_dir)

    def test_the_binding_opens_no_sqlite_file_and_no_inbox(self) -> None:
        config = StorageConfig.for_testing(self.root())
        self.bind_root(config)
        # The SQLite-era inbox directory (``for_processing/``) is not made either.
        for path in (config.db_path, config.index_db_path, config.research_index_db_path,
                     os.path.join(config.root, "for_processing")):
            self.assertFalse(os.path.exists(path), path)
        self.assertTrue(os.path.isdir(config.thumbs_dir))

    def test_each_binding_is_its_own_app(self) -> None:
        first = self.bind_root(StorageConfig.for_testing(self.root()))
        second = self.bind_root(StorageConfig.for_testing(self.root()))
        self.assertIsNot(second.library, first.library)
        self.assertIs(second.require_library().storage, second.storage)
        self.assertNotEqual(second.pdfs_dir, first.pdfs_dir)

    def test_a_failed_binding_leaves_the_served_app_as_it_was(self) -> None:
        first = self.bind_root(StorageConfig.for_testing(self.root()))
        library, storage = first.library, first.storage
        # Building the Library handle fails after the root's directories were made.
        with patch("backend.library.PostgresLibrary", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.bind_root(StorageConfig.for_testing(self.root()))
        self.assertIs(first.library, library)
        self.assertIs(first.storage, storage)
        self.assertIsInstance(library, PostgresLibrary)
