import logging
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager


_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.log_config import setup_logging
from backend.storage.config import StorageConfig


@contextmanager
def _env(*, testing=None, storage=None, log_file=None):
    keys = ("PRKS_TESTING", "PRKS_STORAGE", "PRKS_LOG_FILE")
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
        if log_file is None:
            os.environ.pop("PRKS_LOG_FILE", None)
        else:
            os.environ["PRKS_LOG_FILE"] = log_file
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def _isolated_logging():
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_configured = getattr(root, "_prks_logging_configured", False)
    root._prks_logging_configured = False
    try:
        yield
    finally:
        created = [h for h in root.handlers if h not in saved_handlers]
        for handler in created:
            root.removeHandler(handler)
            handler.close()
        root.handlers = list(saved_handlers)
        root.setLevel(saved_level)
        root._prks_logging_configured = saved_configured


class TestSetupLogging(unittest.TestCase):
    def test_basename_only_log_file_succeeds(self):
        with tempfile.TemporaryDirectory(prefix="prks-log-") as tmp:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with _env(testing="1", storage=tmp, log_file="prks-errors.log"):
                    cfg = StorageConfig.from_env()
                    with _isolated_logging():
                        setup_logging(cfg)
                self.assertTrue(os.path.isfile(os.path.join(tmp, "prks-errors.log")))
            finally:
                os.chdir(old_cwd)

    def test_nested_temp_log_path_succeeds(self):
        with tempfile.TemporaryDirectory(prefix="prks-log-") as tmp:
            log_file = os.path.join(tmp, "nested", "prks-errors.log")
            with _env(testing="1", storage=tmp, log_file=log_file):
                cfg = StorageConfig.from_env()
                with _isolated_logging():
                    setup_logging(cfg)
            self.assertTrue(os.path.isfile(log_file))


if __name__ == "__main__":
    unittest.main()
