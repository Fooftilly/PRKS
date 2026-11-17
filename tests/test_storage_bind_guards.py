"""Storage binding guards that hold whatever Library backend is bound.

Moved from ``tests/test_storage_bind.py`` (PG-C2 #641): serving without a
bound root is refused, and a testing root under ``/data`` is refused.
"""

import os
import sys
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

import backend.server as server_module
from backend.server import run_server
from backend.storage.config import StorageConfig


class StorageBindGuardTests(unittest.TestCase):
    def test_run_server_without_bind_raises(self):
        previous = server_module._bound_storage
        self.addCleanup(setattr, server_module, "_bound_storage", previous)
        server_module._bound_storage = None
        with self.assertRaises(RuntimeError) as ctx:
            run_server()
        self.assertIn("not bound", str(ctx.exception))

    def test_for_testing_data_still_refused(self):
        with self.assertRaises(RuntimeError) as ctx:
            StorageConfig.for_testing("/data")
        self.assertIn("refusing to use PRKS_STORAGE under /data", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
