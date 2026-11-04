"""Run shipped Work-role and People/Folder coherence policy in fast Node tests."""
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests/browser/run_work_role_domain_coherence_selftest.js"


class WorkRoleDomainCoherenceTests(unittest.TestCase):
    def test_production_coherence_matrix(self):
        self.assertTrue(RUNNER.is_file())
        result = subprocess.run(
            ["node", str(RUNNER)], cwd=ROOT,
            capture_output=True, text=True, timeout=90, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("All Work-role and biography domain-coherence checks passed",
                      result.stdout)


if __name__ == "__main__":
    unittest.main()
