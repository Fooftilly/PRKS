import os
import sys
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

os.environ.setdefault("PRKS_TESTING", "1")
os.environ.setdefault("PRKS_STORAGE", os.path.join(_PROJECT_DIR, "data_testing"))

from backend.db_manager import PRKSDatabase
from backend.server import bind_storage, run_server
from backend.storage import paths
from backend.storage.config import StorageConfig
from backend.text_index import PRKSTextIndex, get_text_index, reset_text_index
import backend.server as server_module


_SCHEMA = os.path.join(_PROJECT_DIR, "backend", "db_schema.sql")


def _capture_bind():
    try:
        previous_index = get_text_index()
    except RuntimeError:
        previous_index = None
    return (
        server_module._bound_storage,
        server_module.pdfs_dir,
        server_module.thumbs_dir,
        server_module.processing_dir,
        server_module.db,
        server_module.text_index,
        previous_index,
    )


def _restore_bind(snapshot):
    (
        server_module._bound_storage,
        server_module.pdfs_dir,
        server_module.thumbs_dir,
        server_module.processing_dir,
        server_module.db,
        server_module.text_index,
        previous_index,
    ) = snapshot
    if previous_index is None:
        reset_text_index()
    else:
        from backend.text_index import replace_text_index

        replace_text_index(previous_index)


class TestStorageBind(unittest.TestCase):
    def setUp(self):
        self._prev = _capture_bind()
        self._tmps = []

    def tearDown(self):
        _restore_bind(self._prev)
        for path in self._tmps:
            try:
                import shutil

                shutil.rmtree(path, ignore_errors=True)
            except Exception:
                pass

    def _tmpdir(self):
        path = tempfile.mkdtemp(prefix="prks-bind-")
        self._tmps.append(path)
        return path

    def test_bind_freezes_paths_against_later_env(self):
        root = self._tmpdir()
        cfg = StorageConfig.for_testing(root)
        bound = bind_storage(cfg)
        self.assertEqual(server_module.pdfs_dir, cfg.pdfs_dir)
        self.assertEqual(server_module.pdfs_dir, bound.pdfs_dir)
        self.assertEqual(server_module.processing_dir, bound.processing_dir)
        self.assertEqual(server_module._bound_storage.processing_dir, bound.processing_dir)
        self.assertIs(server_module._bound_storage, bound)
        with patch.dict(
            os.environ,
            {
                "PRKS_STORAGE": "/tmp/changed-after-bind",
                "PRKS_TESTING": "1",
                "PRKS_FOR_PROCESSING_DIR": "/tmp/inbox-after-bind",
            },
            clear=False,
        ):
            self.assertEqual(server_module.pdfs_dir, cfg.pdfs_dir)
            self.assertEqual(server_module._bound_storage.pdfs_dir, cfg.pdfs_dir)
            self.assertEqual(server_module.db.storage.pdfs_dir, cfg.pdfs_dir)

    def test_processing_fallback_updates_returned_and_published_config(self):
        root = self._tmpdir()
        preferred = paths.PROCESSING_PROD_PREFERRED
        fallback = os.path.join(root, "fallback_processing")
        cfg = replace(
            StorageConfig.for_testing(root),
            mode="production",
            processing_dir=preferred,
        )
        real_makedirs = os.makedirs

        def fake_makedirs(path, exist_ok=True):
            if os.path.normpath(str(path)) == os.path.normpath(preferred):
                raise OSError("preferred processing dir unavailable")
            return real_makedirs(path, exist_ok=exist_ok)

        with (
            patch.object(paths, "processing_prod_fallback", return_value=fallback),
            patch("os.makedirs", fake_makedirs),
        ):
            bound = bind_storage(cfg)
        self.assertEqual(bound.processing_dir, fallback)
        self.assertEqual(server_module.processing_dir, fallback)
        self.assertEqual(server_module._bound_storage.processing_dir, fallback)

    def test_failed_rebind_keeps_previous_published_state(self):
        root_a = self._tmpdir()
        root_b = self._tmpdir()
        cfg_a = StorageConfig.for_testing(root_a)
        bound_a = bind_storage(cfg_a)
        db_a = server_module.db
        index_a = server_module.text_index
        self.assertIs(get_text_index(), index_a)

        cfg_b = StorageConfig.for_testing(root_b)
        with patch.object(server_module, "PRKSTextIndex", side_effect=RuntimeError("index boom")):
            with self.assertRaises(RuntimeError):
                bind_storage(cfg_b)

        self.assertIs(server_module._bound_storage, bound_a)
        self.assertIs(server_module.db, db_a)
        self.assertIs(server_module.text_index, index_a)
        self.assertIs(get_text_index(), index_a)
        self.assertEqual(server_module.pdfs_dir, cfg_a.pdfs_dir)
        self.assertNotEqual(server_module.pdfs_dir, cfg_b.pdfs_dir)

    def test_run_server_without_bind_raises(self):
        server_module._bound_storage = None
        with self.assertRaises(RuntimeError) as ctx:
            run_server()
        self.assertIn("not bound", str(ctx.exception))

    def test_successful_rebind_uses_new_index_path(self):
        root_a = self._tmpdir()
        root_b = self._tmpdir()
        cfg_a = StorageConfig.for_testing(root_a)
        bind_storage(cfg_a)
        index_a = get_text_index()
        cfg_b = StorageConfig.for_testing(root_b)
        bound_b = bind_storage(cfg_b)
        index_b = get_text_index()
        self.assertIsNot(index_b, index_a)
        self.assertEqual(index_b.db_path, bound_b.index_db_path)
        self.assertNotEqual(index_b.db_path, index_a.db_path)
        self.assertEqual(server_module.text_index.db_path, cfg_b.index_db_path)

    def test_constructor_conflict_db(self):
        root = self._tmpdir()
        cfg = StorageConfig.for_testing(root)
        PRKSDatabase(storage=cfg, db_path=cfg.db_path, schema_path=_SCHEMA)
        with self.assertRaises(ValueError) as ctx:
            PRKSDatabase(
                storage=cfg,
                db_path=os.path.join(root, "other.db"),
                schema_path=_SCHEMA,
            )
        self.assertIn("conflicts", str(ctx.exception))

    def test_constructor_conflict_text_index(self):
        root = self._tmpdir()
        cfg = StorageConfig.for_testing(root)
        PRKSTextIndex(storage=cfg, db_path=cfg.index_db_path)
        with self.assertRaises(ValueError) as ctx:
            PRKSTextIndex(storage=cfg, db_path=os.path.join(root, "other-index.db"))
        self.assertIn("conflicts", str(ctx.exception))

    def test_legacy_db_methods_do_not_reread_env(self):
        root = self._tmpdir()
        os.makedirs(os.path.join(root, "pdfs"), exist_ok=True)
        with patch.dict(
            os.environ,
            {"PRKS_TESTING": "1", "PRKS_STORAGE": root},
            clear=False,
        ):
            db = PRKSDatabase(
                db_path=os.path.join(root, "prks_data.db"),
                schema_path=_SCHEMA,
            )
            captured = db.storage.pdfs_dir
        fname = "legacy.pdf"
        payload = b"hello"
        with open(os.path.join(captured, fname), "wb") as fh:
            fh.write(payload)
        with patch.dict(
            os.environ,
            {"PRKS_STORAGE": "/tmp/changed-legacy-root", "PRKS_TESTING": "1"},
            clear=False,
        ):
            w_id = db.add_work(title="Legacy", file_path=f"/api/pdfs/{fname}")
            work = db.get_work(w_id)
        self.assertEqual(work.get("file_size_bytes"), len(payload))
        self.assertEqual(db.storage.pdfs_dir, captured)

    def test_for_testing_data_still_refused(self):
        with self.assertRaises(RuntimeError) as ctx:
            StorageConfig.for_testing("/data")
        self.assertIn("refusing to use PRKS_STORAGE under /data", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
