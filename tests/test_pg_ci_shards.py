"""PG helper sharding preserves the exact test-module inventory."""
import tempfile
import unittest
from pathlib import Path

from scripts.ci.pg_test_shards import discover_modules, shard_modules


class PGHelperShardsTests(unittest.TestCase):
    def test_partition_complete_no_overlap(self):
        names = discover_modules()
        self.assertGreater(len(names), 2)
        left, right = shard_modules(names, 1, 2), shard_modules(names, 2, 2)
        self.assertEqual(set(left) | set(right), set(names))
        self.assertFalse(set(left) & set(right))
        self.assertEqual(len(left) + len(right), len(names))

    def test_future_test_module_is_auto_discovered(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            (directory / "test_one.py").write_text("", encoding="utf-8")
            (directory / "test_two.py").write_text("", encoding="utf-8")
            self.assertEqual(len(discover_modules(directory)), 2)
            (directory / "test_three.py").write_text("", encoding="utf-8")
            self.assertEqual(len(discover_modules(directory)), 3)

    def test_malformed_shards_fail_closed(self):
        for index, total in [(0, 2), (3, 2), (1, 0), (1, 13)]:
            with self.subTest(index=index, total=total):
                with self.assertRaises(ValueError):
                    shard_modules(["x", "y"], index, total)
        with self.assertRaises(ValueError):
            shard_modules([], 1, 2)
