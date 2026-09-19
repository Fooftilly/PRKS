"""Non-browser regression tests for E2E performance helpers."""
import os
import tempfile
import unittest
from pathlib import Path

from tests.e2e import harness


class E2ESeedCacheTests(unittest.TestCase):
    def setUp(self):
        self._old_cache = os.environ.get("PRKS_E2E_SEED_CACHE")
        os.environ["PRKS_E2E_SEED_CACHE"] = "1"
        harness.clear_seed_cache()

    def tearDown(self):
        harness.clear_seed_cache()
        if self._old_cache is None:
            os.environ.pop("PRKS_E2E_SEED_CACHE", None)
        else:
            os.environ["PRKS_E2E_SEED_CACHE"] = self._old_cache

    def test_seed_snapshot_is_built_once_and_cloned_per_test(self):
        calls = []

        def seed(root):
            calls.append(root)
            Path(root, "fixture.txt").write_text("pristine", encoding="utf-8")
            return {"nested": {"values": ["original"]}}

        with tempfile.TemporaryDirectory(prefix="prks-cache-dest-a-") as a:
            ids_a, hit_a = harness._materialize_seed(seed, a)
            self.assertFalse(hit_a)
            self.assertEqual(Path(a, "fixture.txt").read_text(encoding="utf-8"), "pristine")
            Path(a, "fixture.txt").write_text("mutated", encoding="utf-8")
            ids_a["nested"]["values"].append("mutated")

        with tempfile.TemporaryDirectory(prefix="prks-cache-dest-b-") as b:
            ids_b, hit_b = harness._materialize_seed(seed, b)
            self.assertTrue(hit_b)
            self.assertEqual(Path(b, "fixture.txt").read_text(encoding="utf-8"), "pristine")
            self.assertEqual(ids_b, {"nested": {"values": ["original"]}})

        self.assertEqual(len(calls), 1)

    def test_cache_can_be_disabled_for_clean_ab_benchmark(self):
        os.environ["PRKS_E2E_SEED_CACHE"] = "0"
        calls = []

        def seed(root):
            calls.append(root)
            Path(root, "fixture.txt").write_text(str(len(calls)), encoding="utf-8")
            return {"call": len(calls)}

        for expected in (1, 2):
            with tempfile.TemporaryDirectory(prefix="prks-cache-off-") as dest:
                ids, hit = harness._materialize_seed(seed, dest)
                self.assertFalse(hit)
                self.assertEqual(ids["call"], expected)
                self.assertEqual(
                    Path(dest, "fixture.txt").read_text(encoding="utf-8"),
                    str(expected),
                )

        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
