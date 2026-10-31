"""Run the classic-renderer source regression on Node, alongside SW coverage."""
import os
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(ROOT, "tests", "browser", "run_security_render_selftest.js")


class UntrustedRenderingTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_classic_renderers_only_link_http_urls_and_use_safe_colors(self):
        done = subprocess.run(
            ["node", RUNNER], cwd=ROOT, capture_output=True, text=True, timeout=30, check=False
        )
        self.assertEqual(done.returncode, 0, done.stdout + "\n" + done.stderr)
