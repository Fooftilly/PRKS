import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backend.server as server_module


class TestListenPortGuard(unittest.TestCase):
    def test_non_testing_rejects_testing_default_port(self):
        with patch.dict(os.environ, {"PRKS_TESTING": ""}, clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                server_module._validate_listen_port(server_module.PRKS_TESTING_DEFAULT_PORT)
            self.assertIn(str(server_module.PRKS_TESTING_DEFAULT_PORT), str(ctx.exception))

    def test_testing_env_allows_testing_default_port(self):
        with patch.dict(os.environ, {"PRKS_TESTING": "1"}, clear=False):
            server_module._validate_listen_port(server_module.PRKS_TESTING_DEFAULT_PORT)

    def test_non_testing_allows_other_ports(self):
        with patch.dict(os.environ, {"PRKS_TESTING": ""}, clear=False):
            server_module._validate_listen_port(8080)


if __name__ == "__main__":
    unittest.main()
