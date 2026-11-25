import os
import sys
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

import backend.server as server_module
from backend.storage.config import StorageConfig


class TestListenPortGuard(unittest.TestCase):
    def test_non_testing_rejects_testing_default_port(self):
        storage = StorageConfig(
            mode="production",
            configured_root=None,
            root="/tmp/prks-listen-prod",
            db_path="/tmp/prks-listen-prod/prks_data.db",
            pdfs_dir="/tmp/prks-listen-prod/pdfs",
            thumbs_dir="/tmp/prks-listen-prod/thumbs",
            people_dir="/tmp/prks-listen-prod/people",
            index_db_path="/tmp/prks-listen-prod/prks_text_index.db",
            research_index_db_path="/tmp/prks-listen-prod/prks_research_index.db",
            log_file="/tmp/prks-listen-prod/prks-errors.log",
        )
        with self.assertRaises(RuntimeError) as ctx:
            server_module.validate_listen_port(
                server_module.PRKS_TESTING_DEFAULT_PORT, testing=storage.mode == "testing"
            )
        self.assertIn(str(server_module.PRKS_TESTING_DEFAULT_PORT), str(ctx.exception))

    def test_testing_env_allows_testing_default_port(self):
        storage = StorageConfig.for_testing("/tmp/prks-listen")
        server_module.validate_listen_port(server_module.PRKS_TESTING_DEFAULT_PORT, testing=storage.mode == "testing")

    def test_non_testing_allows_other_ports(self):
        storage = StorageConfig(
            mode="production",
            configured_root=None,
            root="/tmp/prks-listen-prod",
            db_path="/tmp/prks-listen-prod/prks_data.db",
            pdfs_dir="/tmp/prks-listen-prod/pdfs",
            thumbs_dir="/tmp/prks-listen-prod/thumbs",
            people_dir="/tmp/prks-listen-prod/people",
            index_db_path="/tmp/prks-listen-prod/prks_text_index.db",
            research_index_db_path="/tmp/prks-listen-prod/prks_research_index.db",
            log_file="/tmp/prks-listen-prod/prks-errors.log",
        )
        server_module.validate_listen_port(8080, testing=storage.mode == "testing")


if __name__ == "__main__":
    unittest.main()
