import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from backend.storage import paths
from backend.storage.config import StorageConfig


@contextmanager
def _env(*, testing=None, storage=None, processing=None, log_file=None):
    keys = (
        "PRKS_TESTING",
        "PRKS_STORAGE",
        "PRKS_FOR_PROCESSING_DIR",
        "PRKS_LOG_FILE",
    )
    old = {k: os.environ.get(k) for k in keys}
    try:
        if testing is None:
            os.environ.pop("PRKS_TESTING", None)
        else:
            os.environ["PRKS_TESTING"] = testing
        if storage is None:
            os.environ.pop("PRKS_STORAGE", None)
        else:
            os.environ["PRKS_STORAGE"] = storage
        if processing is None:
            os.environ.pop("PRKS_FOR_PROCESSING_DIR", None)
        else:
            os.environ["PRKS_FOR_PROCESSING_DIR"] = processing
        if log_file is None:
            os.environ.pop("PRKS_LOG_FILE", None)
        else:
            os.environ["PRKS_LOG_FILE"] = log_file
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestStoragePaths(unittest.TestCase):
    def test_unset_and_blank_storage_root_is_none(self):
        with _env(testing="1", storage=None):
            self.assertIsNone(StorageConfig.from_env().configured_root)
        with _env(testing="1", storage=""):
            self.assertIsNone(StorageConfig.from_env().configured_root)
        with _env(testing="1", storage="   "):
            self.assertIsNone(StorageConfig.from_env().configured_root)

    def test_derived_defaults_when_root_is_none(self):
        with _env(testing=None, storage=None):
            cfg = StorageConfig.from_env()
            self.assertEqual(cfg.pdfs_dir, os.path.join(_PROJECT_DIR, "data", "pdfs"))
            self.assertEqual(cfg.db_path, os.path.join(_PROJECT_DIR, "data", "prks_data.db"))
            self.assertEqual(cfg.thumbs_dir, os.path.join(_PROJECT_DIR, "data", "thumbs"))
            self.assertEqual(cfg.people_dir, os.path.join(_PROJECT_DIR, "data", "people"))
            self.assertEqual(cfg.root, os.path.join(_PROJECT_DIR, "data"))
        with _env(testing="1", storage=None):
            cfg = StorageConfig.from_env()
            self.assertEqual(cfg.pdfs_dir, os.path.join(_PROJECT_DIR, "data_testing", "pdfs"))
            self.assertEqual(
                cfg.db_path,
                os.path.join(_PROJECT_DIR, "data_testing", "prks_data_testing.db"),
            )
            self.assertEqual(cfg.thumbs_dir, os.path.join(_PROJECT_DIR, "data_testing", "thumbs"))
            self.assertEqual(
                cfg.processing_dir,
                os.path.join(_PROJECT_DIR, "data_testing", "for_processing"),
            )
            self.assertEqual(cfg.root, os.path.join(_PROJECT_DIR, "data_testing"))

    def test_explicit_storage_keeps_configured_string(self):
        configured = "data_testing"
        with _env(testing="1", storage=configured):
            cfg = StorageConfig.from_env()
            self.assertEqual(cfg.configured_root, configured)
            self.assertEqual(cfg.pdfs_dir, os.path.join(configured, "pdfs"))
            self.assertEqual(cfg.db_path, os.path.join(configured, "prks_data.db"))

    def test_paths_has_no_env_wrappers(self):
        self.assertFalse(hasattr(paths, "resolve_storage_root"))
        self.assertFalse(hasattr(paths, "resolve_db_path"))
        self.assertFalse(hasattr(paths, "resolve_pdfs_dir"))
        self.assertFalse(hasattr(paths, "is_testing"))

    def test_testing_refuses_data_and_equivalents(self):
        for value in ("/data", "/data/.", "/data/subdir", "/tmp/../data"):
            with self.subTest(storage=value):
                with _env(testing="1", storage=value):
                    with self.assertRaises(RuntimeError) as ctx:
                        StorageConfig.from_env()
                    self.assertIn(
                        "refusing to use PRKS_STORAGE under /data",
                        str(ctx.exception),
                    )

    def test_testing_allows_data2(self):
        with _env(testing="1", storage="/data2"):
            cfg = StorageConfig.from_env()
            self.assertEqual(cfg.configured_root, "/data2")
            self.assertEqual(cfg.pdfs_dir, os.path.join("/data2", "pdfs"))

    def test_processing_dir_guard_type_and_message(self):
        with _env(testing="1", storage=None, processing="/data"):
            with self.assertRaises(RuntimeError) as ctx:
                StorageConfig.from_env()
            self.assertIn(
                "refusing to use PRKS_FOR_PROCESSING_DIR under /data",
                str(ctx.exception),
            )
        with _env(testing="1", storage=None, processing="/data2"):
            self.assertEqual(StorageConfig.from_env().processing_dir, "/data2")

    def test_prks_testing_normalization(self):
        for value in ("1", "true", "TRUE", " yes "):
            with self.subTest(testing=value):
                with _env(testing=value, storage=None):
                    self.assertEqual(StorageConfig.from_env().mode, "testing")
        for value in ("on", ""):
            with self.subTest(testing=value):
                with _env(testing=value, storage=None):
                    self.assertEqual(StorageConfig.from_env().mode, "production")
        with _env(testing=None, storage=None):
            self.assertEqual(StorageConfig.from_env().mode, "production")


class TestStorageConfig(unittest.TestCase):
    def test_unset_testing_uses_testing_db_filename(self):
        from backend.storage.config import StorageConfig

        with _env(testing="1", storage=None):
            cfg = StorageConfig.from_env()
        self.assertEqual(cfg.mode, "testing")
        self.assertIsNone(cfg.configured_root)
        self.assertEqual(cfg.root, os.path.join(_PROJECT_DIR, "data_testing"))
        self.assertEqual(
            cfg.db_path,
            os.path.join(_PROJECT_DIR, "data_testing", "prks_data_testing.db"),
        )
        self.assertEqual(
            cfg.log_file,
            os.path.join(_PROJECT_DIR, "data_testing", "prks-errors.log"),
        )

    def test_explicit_testing_root_uses_prks_data_db(self):
        from backend.storage.config import StorageConfig

        with _env(testing="1", storage="data_testing"):
            cfg = StorageConfig.from_env()
        self.assertEqual(cfg.configured_root, "data_testing")
        self.assertEqual(cfg.root, "data_testing")
        self.assertEqual(cfg.db_path, os.path.join("data_testing", "prks_data.db"))

    def test_for_testing_is_explicit_root(self):
        from backend.storage.config import StorageConfig

        cfg = StorageConfig.for_testing("/tmp/prks-test")
        self.assertEqual(cfg.mode, "testing")
        self.assertEqual(cfg.configured_root, "/tmp/prks-test")
        self.assertEqual(cfg.root, "/tmp/prks-test")
        self.assertEqual(cfg.db_path, "/tmp/prks-test/prks_data.db")
        self.assertNotIn("prks_data_testing.db", cfg.db_path)
        self.assertEqual(cfg.pdfs_dir, "/tmp/prks-test/pdfs")
        self.assertEqual(cfg.processing_dir, "/tmp/prks-test/for_processing")
        self.assertEqual(cfg.log_file, "/tmp/prks-test/prks-errors.log")

    def test_for_testing_ignores_env_overrides(self):
        from backend.storage.config import StorageConfig

        with _env(
            testing="1",
            storage="/tmp/other",
            processing="/tmp/inbox",
            log_file="/tmp/custom.log",
        ):
            cfg = StorageConfig.for_testing("/tmp/prks-test")
        self.assertEqual(cfg.root, "/tmp/prks-test")
        self.assertEqual(cfg.processing_dir, "/tmp/prks-test/for_processing")
        self.assertEqual(cfg.log_file, "/tmp/prks-test/prks-errors.log")

    def test_from_env_and_for_testing_refuse_data(self):
        from backend.storage.config import StorageConfig

        for value in ("/data", "/data/.", "/data/subdir", "/tmp/../data"):
            with self.subTest(storage=value):
                with _env(testing="1", storage=value):
                    with self.assertRaises(RuntimeError) as ctx:
                        StorageConfig.from_env()
                    self.assertIn(
                        "refusing to use PRKS_STORAGE under /data",
                        str(ctx.exception),
                    )
                with self.assertRaises(RuntimeError) as ctx:
                    StorageConfig.for_testing(value)
                self.assertIn(
                    "refusing to use PRKS_STORAGE under /data",
                    str(ctx.exception),
                )

    def test_for_testing_allows_data2(self):
        from backend.storage.config import StorageConfig

        cfg = StorageConfig.for_testing("/data2")
        self.assertEqual(cfg.mode, "testing")
        self.assertEqual(cfg.configured_root, "/data2")
        self.assertEqual(cfg.processing_dir, os.path.join("/data2", "for_processing"))

    def test_from_env_log_and_processing_overrides(self):
        from backend.storage.config import StorageConfig

        with _env(
            testing="1",
            storage=None,
            processing="/tmp/inbox",
            log_file="/tmp/custom.log",
        ):
            cfg = StorageConfig.from_env()
        self.assertEqual(cfg.processing_dir, "/tmp/inbox")
        self.assertEqual(cfg.log_file, "/tmp/custom.log")
        self.assertEqual(cfg.root, os.path.join(_PROJECT_DIR, "data_testing"))
        self.assertFalse(cfg.processing_fallback_allowed)

    def test_default_production_processing_allows_fallback(self):
        from backend.storage.config import StorageConfig

        with _env(testing=None, storage=None):
            cfg = StorageConfig.from_env()
        self.assertEqual(cfg.mode, "production")
        self.assertIsNone(cfg.configured_root)
        self.assertEqual(cfg.processing_dir, paths.PROCESSING_PROD_PREFERRED)
        self.assertTrue(cfg.processing_fallback_allowed)

    def test_explicit_processing_override_disallows_fallback(self):
        from backend.storage.config import StorageConfig

        with _env(
            testing=None,
            storage=None,
            processing=paths.PROCESSING_PROD_PREFERRED,
        ):
            cfg = StorageConfig.from_env()
        self.assertEqual(cfg.mode, "production")
        self.assertEqual(cfg.processing_dir, paths.PROCESSING_PROD_PREFERRED)
        self.assertFalse(cfg.processing_fallback_allowed)

    def test_snapshot_ignores_later_env_changes(self):
        from backend.storage.config import StorageConfig

        with _env(testing="1", storage="data_testing"):
            cfg = StorageConfig.from_env()
        with _env(
            testing="",
            storage="/tmp/changed",
            processing="/tmp/inbox",
            log_file="/tmp/changed.log",
        ):
            self.assertEqual(cfg.mode, "testing")
            self.assertEqual(cfg.configured_root, "data_testing")
            self.assertEqual(cfg.db_path, os.path.join("data_testing", "prks_data.db"))
            self.assertEqual(
                cfg.processing_dir, os.path.join("data_testing", "for_processing")
            )
            self.assertEqual(cfg.log_file, os.path.join("data_testing", "prks-errors.log"))

    def test_testing_refuses_repo_data_root(self):
        repo_data = os.path.join(_PROJECT_DIR, "data")
        repo_data_child = os.path.join(repo_data, "subdir")
        with _env(testing="1", storage=repo_data):
            with self.assertRaises(RuntimeError) as ctx:
                StorageConfig.from_env()
            self.assertIn("refusing to use PRKS_STORAGE", str(ctx.exception))
            self.assertIn("repository data directory", str(ctx.exception))
        with _env(testing="1", storage=repo_data_child):
            with self.assertRaises(RuntimeError) as ctx:
                StorageConfig.from_env()
            self.assertIn("refusing to use PRKS_STORAGE", str(ctx.exception))
        with self.assertRaises(RuntimeError) as ctx:
            StorageConfig.for_testing(repo_data)
        self.assertIn("refusing to use PRKS_STORAGE", str(ctx.exception))
        self.assertIn("repository data directory", str(ctx.exception))

    def test_testing_allows_repo_data2(self):
        repo_data2 = os.path.join(_PROJECT_DIR, "data2")
        with _env(testing="1", storage=repo_data2):
            cfg = StorageConfig.from_env()
        self.assertEqual(cfg.configured_root, repo_data2)
        cfg = StorageConfig.for_testing(repo_data2)
        self.assertEqual(cfg.root, repo_data2)

    def test_testing_processing_override_cannot_use_production_trees(self):
        repo_data_proc = os.path.join(_PROJECT_DIR, "data", "for_processing")
        with _env(testing="1", storage=None, processing=repo_data_proc):
            with self.assertRaises(RuntimeError) as ctx:
                StorageConfig.from_env()
            self.assertIn("PRKS_FOR_PROCESSING_DIR", str(ctx.exception))
        with _env(testing="1", storage=None, processing="/data/for_processing"):
            with self.assertRaises(RuntimeError) as ctx:
                StorageConfig.from_env()
            self.assertIn("PRKS_FOR_PROCESSING_DIR", str(ctx.exception))
            self.assertIn("under /data", str(ctx.exception))

    def test_testing_log_override_cannot_use_production_trees(self):
        cases = (
            ("/data/log.txt", "/data"),
            ("/data/sub/log.txt", "/data"),
            (os.path.join(_PROJECT_DIR, "data", "x.log"), "repository data directory"),
        )
        for log_file, marker in cases:
            with self.subTest(log_file=log_file):
                with _env(testing="1", storage=None, log_file=log_file):
                    with self.assertRaises(RuntimeError) as ctx:
                        StorageConfig.from_env()
                    self.assertIn("PRKS_LOG_FILE", str(ctx.exception))
                    self.assertIn(marker, str(ctx.exception))

    def test_testing_log_override_allows_data2_trees(self):
        with _env(testing="1", storage=None, log_file="/data2/log.txt"):
            cfg = StorageConfig.from_env()
        self.assertEqual(cfg.log_file, "/data2/log.txt")
        allowed = os.path.join(_PROJECT_DIR, "data2", "x.log")
        with _env(testing="1", storage=None, log_file=allowed):
            cfg = StorageConfig.from_env()
        self.assertEqual(cfg.log_file, allowed)

    def test_explicit_symlink_into_repo_data_is_rejected(self):
        repo_data = os.path.join(_PROJECT_DIR, "data")
        try:
            with tempfile.TemporaryDirectory(prefix="prks-safe-looking-") as tmp:
                link = os.path.join(tmp, "prks-safe-looking")
                os.symlink(repo_data, link)
                with self.assertRaises(RuntimeError) as ctx:
                    StorageConfig.for_testing(link)
                self.assertIn("refusing to use", str(ctx.exception))
                with _env(testing="1", storage=link):
                    with self.assertRaises(RuntimeError) as ctx:
                        StorageConfig.from_env()
                    self.assertIn("refusing to use PRKS_STORAGE", str(ctx.exception))
        except OSError as exc:
            self.skipTest(f"symlink containment test unavailable: {exc}")

    def test_defaulted_testing_root_symlink_into_repo_data_is_rejected(self):
        try:
            with tempfile.TemporaryDirectory(prefix="prks-fake-repo-") as fake_repo:
                prod_data = os.path.join(fake_repo, "data")
                os.makedirs(prod_data)
                testing_root = os.path.join(fake_repo, "data_testing")
                os.symlink(prod_data, testing_root)
                with patch.object(paths, "_REPO_ROOT", fake_repo):
                    with _env(testing="1", storage=None):
                        with self.assertRaises(RuntimeError) as ctx:
                            StorageConfig.from_env()
                        self.assertIn("refusing to use", str(ctx.exception))
                        self.assertIn("testing storage root", str(ctx.exception))
        except OSError as exc:
            self.skipTest(f"symlink containment test unavailable: {exc}")

    def test_derived_log_symlink_into_fake_repo_data_is_rejected(self):
        try:
            with tempfile.TemporaryDirectory(prefix="prks-fake-prod-") as fake_repo:
                prod_data = os.path.join(fake_repo, "data")
                os.makedirs(prod_data)
                prod_log = os.path.join(prod_data, "log")
                with open(prod_log, "w", encoding="utf-8") as fh:
                    fh.write("prod")
                with tempfile.TemporaryDirectory(prefix="prks-safe-root-") as safe_root:
                    os.symlink(prod_log, os.path.join(safe_root, "prks-errors.log"))
                    with patch.object(paths, "_REPO_ROOT", fake_repo):
                        with self.assertRaises(RuntimeError) as ctx:
                            StorageConfig.for_testing(safe_root)
                        self.assertIn("refusing to use", str(ctx.exception))
                        self.assertIn("log_file", str(ctx.exception))
        except OSError as exc:
            self.skipTest(f"symlink containment test unavailable: {exc}")

    def test_derived_pdfs_symlink_into_fake_repo_data_is_rejected(self):
        try:
            with tempfile.TemporaryDirectory(prefix="prks-fake-prod-") as fake_repo:
                prod_pdfs = os.path.join(fake_repo, "data", "pdfs")
                os.makedirs(prod_pdfs)
                with tempfile.TemporaryDirectory(prefix="prks-safe-root-") as safe_root:
                    os.symlink(prod_pdfs, os.path.join(safe_root, "pdfs"))
                    with patch.object(paths, "_REPO_ROOT", fake_repo):
                        with self.assertRaises(RuntimeError) as ctx:
                            StorageConfig.for_testing(safe_root)
                        self.assertIn("refusing to use", str(ctx.exception))
                        self.assertIn("pdfs_dir", str(ctx.exception))
        except OSError as exc:
            self.skipTest(f"symlink containment test unavailable: {exc}")

    def test_derived_db_and_index_symlinks_into_fake_repo_data_are_rejected(self):
        try:
            with tempfile.TemporaryDirectory(prefix="prks-fake-prod-") as fake_repo:
                prod_data = os.path.join(fake_repo, "data")
                os.makedirs(prod_data)
                prod_db = os.path.join(prod_data, "prks_data.db")
                prod_index = os.path.join(prod_data, "prks_text_index.db")
                for path in (prod_db, prod_index):
                    with open(path, "wb") as fh:
                        fh.write(b"")
                with tempfile.TemporaryDirectory(prefix="prks-safe-root-") as safe_root:
                    os.symlink(prod_db, os.path.join(safe_root, "prks_data.db"))
                    with patch.object(paths, "_REPO_ROOT", fake_repo):
                        with self.assertRaises(RuntimeError) as ctx:
                            StorageConfig.for_testing(safe_root)
                        self.assertIn("refusing to use", str(ctx.exception))
                        self.assertIn("db_path", str(ctx.exception))
                    os.remove(os.path.join(safe_root, "prks_data.db"))
                    os.symlink(prod_index, os.path.join(safe_root, "prks_text_index.db"))
                    with patch.object(paths, "_REPO_ROOT", fake_repo):
                        with self.assertRaises(RuntimeError) as ctx:
                            StorageConfig.for_testing(safe_root)
                        self.assertIn("refusing to use", str(ctx.exception))
                        self.assertIn("index_db_path", str(ctx.exception))
        except OSError as exc:
            self.skipTest(f"symlink containment test unavailable: {exc}")

    def test_derived_child_symlink_is_rejected_before_write(self):
        def boom_mkdir(*_a, **_k):
            raise AssertionError("mkdir")

        real_open = open

        def guarded_open(file, mode="r", *args, **kwargs):
            if any(flag in str(mode) for flag in "wax+"):
                raise AssertionError("write-open")
            return real_open(file, mode, *args, **kwargs)

        try:
            with tempfile.TemporaryDirectory(prefix="prks-fake-prod-") as fake_repo:
                prod_data = os.path.join(fake_repo, "data")
                os.makedirs(prod_data)
                prod_log = os.path.join(prod_data, "log")
                with open(prod_log, "w", encoding="utf-8") as fh:
                    fh.write("prod")
                with tempfile.TemporaryDirectory(prefix="prks-safe-root-") as safe_root:
                    os.symlink(prod_log, os.path.join(safe_root, "prks-errors.log"))
                    with (
                        patch.object(paths, "_REPO_ROOT", fake_repo),
                        patch("os.makedirs", boom_mkdir),
                        patch("builtins.open", guarded_open),
                    ):
                        with self.assertRaises(RuntimeError) as ctx:
                            StorageConfig.for_testing(safe_root)
                        self.assertIn("refusing to use", str(ctx.exception))
        except OSError as exc:
            self.skipTest(f"symlink containment test unavailable: {exc}")

    def test_from_env_and_for_testing_do_not_write(self):
        from backend.storage.config import StorageConfig

        def boom_mkdir(*_a, **_k):
            raise AssertionError("mkdir")

        def boom_connect(*_a, **_k):
            raise AssertionError("sqlite")

        real_open = open

        def guarded_open(file, mode="r", *args, **kwargs):
            if any(flag in str(mode) for flag in "wax+"):
                raise AssertionError("write-open")
            return real_open(file, mode, *args, **kwargs)

        with (
            _env(testing="1", storage=None),
            patch("os.makedirs", boom_mkdir),
            patch("sqlite3.connect", boom_connect),
            patch("builtins.open", guarded_open),
        ):
            StorageConfig.from_env()
            StorageConfig.for_testing("/tmp/prks-test")


class TestStoragePackageImport(unittest.TestCase):
    def test_storage_init_has_no_resolver_side_effects(self):
        import backend.storage as storage_pkg

        self.assertFalse(hasattr(storage_pkg, "resolve_storage_root"))
        self.assertFalse(hasattr(storage_pkg, "is_testing"))
        with patch.dict(os.environ, {"PRKS_STORAGE": "/data", "PRKS_TESTING": "1"}, clear=False):
            import importlib

            importlib.reload(storage_pkg)
        self.assertFalse(hasattr(storage_pkg, "resolve_storage_root"))


if __name__ == "__main__":
    unittest.main()
