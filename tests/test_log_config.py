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
    setattr(root, "_prks_logging_configured", False)
    try:
        yield
    finally:
        created = [h for h in root.handlers if h not in saved_handlers]
        for handler in created:
            root.removeHandler(handler)
            handler.close()
        root.handlers = list(saved_handlers)
        root.setLevel(saved_level)
        setattr(root, "_prks_logging_configured", saved_configured)


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
    def test_logging_initialized_omits_log_file_path(self):
        with tempfile.TemporaryDirectory(prefix="prks-log-") as tmp:
            private_root = os.path.join(tmp, "PRIVATE_X9Q7")
            os.makedirs(private_root)
            log_file = os.path.join(private_root, "prks-errors.log")
            with _env(testing="1", storage=tmp, log_file=log_file):
                cfg = StorageConfig.from_env()
                with _isolated_logging():
                    with self.assertLogs("prks.log_config", level="INFO") as cm:
                        setup_logging(cfg)
            joined = "\n".join(cm.output)
            self.assertIn("logging_initialized", joined)
            self.assertIn("retention_days=", joined)
            self.assertNotIn("log_file=", joined)
            self.assertNotIn(private_root, joined)
            self.assertNotIn("PRIVATE_X9Q7", joined)

    def test_posix_log_file_is_owner_only(self):
        if os.name != "posix":
            self.skipTest("POSIX file modes")
        with tempfile.TemporaryDirectory(prefix="prks-log-") as tmp:
            log_file = os.path.join(tmp, "prks-errors.log")
            with _env(testing="1", storage=tmp, log_file=log_file):
                cfg = StorageConfig.from_env()
                with _isolated_logging():
                    setup_logging(cfg)
                    mode = os.stat(log_file).st_mode
                    self.assertEqual(mode & 0o077, 0)
                    from logging.handlers import TimedRotatingFileHandler

                    handlers = [
                        h
                        for h in logging.getLogger().handlers
                        if isinstance(h, TimedRotatingFileHandler)
                    ]
                    self.assertTrue(handlers)
                    handlers[0].doRollover()
                    self.assertTrue(os.path.isfile(log_file))
                    self.assertEqual(os.stat(log_file).st_mode & 0o077, 0)

    def test_handlers_use_privacy_safe_formatter(self):
        from backend.log_safety import PrivacySafeFormatter

        with tempfile.TemporaryDirectory(prefix="prks-log-") as tmp:
            log_file = os.path.join(tmp, "prks-errors.log")
            with _env(testing="1", storage=tmp, log_file=log_file):
                cfg = StorageConfig.from_env()
                with _isolated_logging():
                    setup_logging(cfg)
                    self.assertTrue(logging.getLogger().handlers)
                    for handler in logging.getLogger().handlers:
                        self.assertIsInstance(handler.formatter, PrivacySafeFormatter)



class SharedLogRotationTests(unittest.TestCase):
    """Two processes' handlers on one error log (web processes beside each other)."""

    def setUp(self):
        from backend.log_config import _OwnerOnlyTimedRotatingFileHandler

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "prks-errors.log")
        self.handlers = [_OwnerOnlyTimedRotatingFileHandler(self.path, when="midnight", backupCount=7,
                                                            encoding="utf-8") for _ in range(2)]
        for handler in self.handlers:
            handler.setFormatter(logging.Formatter("%(message)s"))
            self.addCleanup(handler.close)

    def write(self, handler, message):
        handler.handle(logging.LogRecord("prks", logging.ERROR, __file__, 1, message, None, None))

    def read(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read().split()

    def dated(self):
        return [name for name in os.listdir(os.path.dirname(self.path)) if name != "prks-errors.log"]

    def test_a_day_is_rotated_once_and_every_process_follows_the_new_file(self):
        first, second = self.handlers
        self.write(first, "a1")
        self.write(second, "b1")
        for handler in self.handlers:
            handler.rolloverAt = 0  # midnight passed for both
        self.write(first, "a2")  # rotates
        self.write(second, "b2")  # finds the day rotated: reopens, rotates nothing
        self.write(first, "a3")
        (dated,) = self.dated()
        self.assertEqual(self.read(os.path.join(os.path.dirname(self.path), dated)), ["a1", "b1"])
        self.assertEqual(self.read(self.path), ["a2", "b2", "a3"])
        self.assertGreater(second.rolloverAt, 0)

    def test_a_process_reopens_the_log_another_rotated_before_its_own_midnight(self):
        first, second = self.handlers
        self.write(first, "a1")
        self.write(second, "b1")
        first.rolloverAt = 0
        self.write(first, "a2")
        self.write(second, "b2")  # its own rollover is not due, but its file was rotated away
        (dated,) = self.dated()
        self.assertEqual(self.read(os.path.join(os.path.dirname(self.path), dated)), ["a1", "b1"])
        self.assertEqual(self.read(self.path), ["a2", "b2"])

    def test_rotations_due_for_different_days_keep_every_record(self):
        first, second = self.handlers
        self.write(first, "a1")
        self.write(second, "b1")
        # A quiet writer is still due for an earlier day than a busy one.
        first.rolloverAt = 0
        second.rolloverAt = 86400
        real_link = os.link
        interleaved = []

        def link_then_let_the_other_rotate(src, dst):
            real_link(src, dst)
            if not interleaved:
                interleaved.append(dst)
                # The other process rotates while this one is between its
                # link and its unlink; it must wait for the lock instead.
                self.write(second, "b2")

        from unittest import mock

        with (mock.patch("backend.log_config.os.link", link_then_let_the_other_rotate),
              mock.patch("backend.log_config._ROTATE_LOCK_WAIT_SECONDS", 0.1)):
            self.write(first, "a2")
        self.write(second, "b3")
        records = []
        for name in os.listdir(os.path.dirname(self.path)):
            records += self.read(os.path.join(os.path.dirname(self.path), name))
        self.assertEqual(sorted(records), ["a1", "a2", "b1", "b2", "b3"])
