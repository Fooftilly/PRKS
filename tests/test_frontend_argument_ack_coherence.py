"""Fast production-ACK cache-domain checks; no browser, HTTP server or Chromium."""
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests" / "browser" / "run_argument_ack_coherence_selftest.js"


class ArgumentAckCoherenceContracts(unittest.TestCase):
    def test_real_ack_reconciler_domains(self):
        self.assertTrue(SCRIPT.is_file())
        result = subprocess.run(
            ["node", str(SCRIPT)], cwd=ROOT,
            capture_output=True, text=True, timeout=90, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "All 4 production Argument ACK coherence scenarios passed",
            result.stdout,
        )


if __name__ == "__main__":
    unittest.main()
