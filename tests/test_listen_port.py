import os
import sys
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

os.environ.setdefault("PRKS_TESTING", "1")
os.environ.setdefault("PRKS_STORAGE", os.path.join(_PROJECT_DIR, "data_testing"))

import backend.server as server_module
from backend.storage.config import StorageConfig


class TestListenPortGuard(unittest.TestCase):
    def setUp(self):
        self._prev = server_module._bound_storage

    def tearDown(self):
        server_module._bound_storage = self._prev

    def test_non_testing_rejects_testing_default_port(self):
        server_module._bound_storage = StorageConfig(
            mode="production",
            configured_root=None,
            root="/tmp/prks-listen-prod",
            db_path="/tmp/prks-listen-prod/prks_data.db",
            pdfs_dir="/tmp/prks-listen-prod/pdfs",
            thumbs_dir="/tmp/prks-listen-prod/thumbs",
            people_dir="/tmp/prks-listen-prod/people",
            processing_dir="/tmp/prks-listen-prod/for_processing",
            index_db_path="/tmp/prks-listen-prod/prks_text_index.db",
            log_file="/tmp/prks-listen-prod/prks-errors.log",
        )
        with self.assertRaises(RuntimeError) as ctx:
            server_module._validate_listen_port(server_module.PRKS_TESTING_DEFAULT_PORT)
        self.assertIn(str(server_module.PRKS_TESTING_DEFAULT_PORT), str(ctx.exception))

    def test_testing_env_allows_testing_default_port(self):
        server_module._bound_storage = StorageConfig.for_testing("/tmp/prks-listen")
        server_module._validate_listen_port(server_module.PRKS_TESTING_DEFAULT_PORT)

    def test_non_testing_allows_other_ports(self):
        server_module._bound_storage = StorageConfig(
            mode="production",
            configured_root=None,
            root="/tmp/prks-listen-prod",
            db_path="/tmp/prks-listen-prod/prks_data.db",
            pdfs_dir="/tmp/prks-listen-prod/pdfs",
            thumbs_dir="/tmp/prks-listen-prod/thumbs",
            people_dir="/tmp/prks-listen-prod/people",
            processing_dir="/tmp/prks-listen-prod/for_processing",
            index_db_path="/tmp/prks-listen-prod/prks_text_index.db",
            log_file="/tmp/prks-listen-prod/prks-errors.log",
        )
        server_module._validate_listen_port(8080)


if __name__ == "__main__":
    unittest.main()
