import inspect
import os
import sys
import unittest
from contextlib import contextmanager
from typing import Optional, get_type_hints
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from backend.storage import paths


@contextmanager
def _env(*, testing=None, storage=None, processing=None):
    keys = ("PRKS_TESTING", "PRKS_STORAGE", "PRKS_FOR_PROCESSING_DIR")
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
            self.assertIsNone(paths.resolve_storage_root())
        with _env(testing="1", storage=""):
            self.assertIsNone(paths.resolve_storage_root())
        with _env(testing="1", storage="   "):
            self.assertIsNone(paths.resolve_storage_root())

    def test_derived_defaults_when_root_is_none(self):
        with _env(testing=None, storage=None):
            self.assertEqual(
                paths.resolve_pdfs_dir(),
                os.path.join(_PROJECT_DIR, "data", "pdfs"),
            )
            self.assertEqual(
                paths.resolve_db_path(),
                os.path.join(_PROJECT_DIR, "data", "prks_data.db"),
            )
            self.assertEqual(
                paths.resolve_thumbs_dir(),
                os.path.join(_PROJECT_DIR, "data", "thumbs"),
            )
            self.assertEqual(
                paths.resolve_people_images_dir(),
                os.path.join(_PROJECT_DIR, "data", "people"),
            )
            self.assertEqual(
                paths.resolve_defaulted_storage_root(),
                os.path.join(_PROJECT_DIR, "data"),
            )
        with _env(testing="1", storage=None):
            self.assertEqual(
                paths.resolve_pdfs_dir(),
                os.path.join(_PROJECT_DIR, "data_testing", "pdfs"),
            )
            self.assertEqual(
                paths.resolve_db_path(),
                os.path.join(_PROJECT_DIR, "data_testing", "prks_data_testing.db"),
            )
            self.assertEqual(
                paths.resolve_thumbs_dir(),
                os.path.join(_PROJECT_DIR, "data_testing", "thumbs"),
            )
            self.assertEqual(
                paths.resolve_processing_dir(),
                os.path.join(_PROJECT_DIR, "data_testing", "for_processing"),
            )
            self.assertEqual(
                paths.resolve_defaulted_storage_root(),
                os.path.join(_PROJECT_DIR, "data_testing"),
            )

    def test_explicit_storage_keeps_configured_string(self):
        configured = "data_testing"
        with _env(testing="1", storage=configured):
            self.assertEqual(paths.resolve_storage_root(), configured)
            self.assertEqual(
                paths.resolve_pdfs_dir(),
                os.path.join(configured, "pdfs"),
            )
            self.assertEqual(
                paths.resolve_db_path(),
                os.path.join(configured, "prks_data.db"),
            )

    def test_signatures_and_return_types(self):
        self.assertEqual(list(inspect.signature(paths.resolve_storage_root).parameters), [])
        self.assertEqual(list(inspect.signature(paths.resolve_db_path).parameters), [])
        self.assertEqual(list(inspect.signature(paths.resolve_pdfs_dir).parameters), [])
        self.assertEqual(list(inspect.signature(paths.is_testing).parameters), [])
        self.assertEqual(
            get_type_hints(paths.resolve_storage_root).get("return"),
            Optional[str],
        )
        self.assertEqual(get_type_hints(paths.resolve_db_path).get("return"), str)
        self.assertEqual(get_type_hints(paths.resolve_pdfs_dir).get("return"), str)
        with _env(testing="1", storage=None):
            self.assertIsNone(paths.resolve_storage_root())
            self.assertIsInstance(paths.resolve_pdfs_dir(), str)
            self.assertIsInstance(paths.resolve_db_path(), str)

    def test_testing_refuses_data_and_equivalents(self):
        for value in ("/data", "/data/.", "/data/subdir", "/tmp/../data"):
            with self.subTest(storage=value):
                with _env(testing="1", storage=value):
                    with self.assertRaises(RuntimeError) as ctx:
                        paths.resolve_storage_root()
                    self.assertIn(
                        "refusing to use PRKS_STORAGE under /data",
                        str(ctx.exception),
                    )

    def test_testing_allows_data2(self):
        with _env(testing="1", storage="/data2"):
            self.assertEqual(paths.resolve_storage_root(), "/data2")
            self.assertEqual(paths.resolve_pdfs_dir(), os.path.join("/data2", "pdfs"))

    def test_processing_dir_guard_type_and_message(self):
        with _env(testing="1", storage=None, processing="/data"):
            with self.assertRaises(RuntimeError) as ctx:
                paths.resolve_processing_dir()
            self.assertIn(
                "refusing to use PRKS_FOR_PROCESSING_DIR under /data",
                str(ctx.exception),
            )
        with _env(testing="1", storage=None, processing="/data2"):
            self.assertEqual(paths.resolve_processing_dir(), "/data2")

    def test_prks_testing_normalization(self):
        for value in ("1", "true", "TRUE", " yes "):
            with self.subTest(testing=value):
                with _env(testing=value, storage=None):
                    self.assertTrue(paths.is_testing())
        for value in ("on", ""):
            with self.subTest(testing=value):
                with _env(testing=value, storage=None):
                    self.assertFalse(paths.is_testing())
        with _env(testing=None, storage=None):
            self.assertFalse(paths.is_testing())


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
