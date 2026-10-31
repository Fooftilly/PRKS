"""Fast Node checks for the exact production read-model validators in app.js.

The predecessor Chromium cases only called these pure functions through
page.evaluate; offline cache/publication and browser-rendering cases stay E2E.
"""
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests" / "browser" / "run_offline_read_model_shape_selftest.js"


class OfflineReadModelShapeContracts(unittest.TestCase):
    def test_fast_production_validators(self):
        self.assertTrue(RUNNER.is_file())
        result = subprocess.run(
            ["node", str(RUNNER)],
            cwd=ROOT, capture_output=True, text=True, timeout=90, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("All 102 offline read-model shape checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
