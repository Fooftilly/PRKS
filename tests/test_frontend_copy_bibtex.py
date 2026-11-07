"""Work-detail BibTeX clipboard feedback must reflect a real copy (#1076)."""
import os
import shutil
import subprocess
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEST = os.path.join(_ROOT, "tests", "browser", "run_copy_bibtex_selftest.js")


class WorkBibTeXClipboardTests(unittest.TestCase):
    def test_browser_code_in_node(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required for BibTeX clipboard tests")
        result = subprocess.run(
            [node, _TEST],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)
        self.assertIn("8 passed, 0 failed", result.stdout)


if __name__ == "__main__":
    unittest.main()
