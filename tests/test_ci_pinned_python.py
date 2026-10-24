"""Contract tests for the exact-pin CI installation helper."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.ci.install_pinned_python import install, pins_for_profile


class CIPinsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "requirements.txt").write_text(
            "PyMuPDF==1.28.2\nPillow==12.3.0\npydantic==2.13.5\n", encoding="utf-8"
        )
        (self.root / "requirements-dev.txt").write_text(
            "playwright==1.63.0\nopenapi-core==0.23.1\nhypothesis==6.170.9\n"
            "psycopg==3.3.6\npsycopg-binary==3.3.6\n"
            "psycopg-pool==3.3.3\nargon2-cffi==25.1.0\n",
            encoding="utf-8",
        )

    def test_e2e_reads_live_pins_instead_of_inlined_workflow_versions(self) -> None:
        pins = pins_for_profile(self.root, "e2e")
        self.assertEqual(pins[-1], "playwright==1.63.0")
        (self.root / "requirements-dev.txt").write_text(
            (self.root / "requirements-dev.txt").read_text().replace(
                "playwright==1.63.0", "playwright==1.64.0"
            ),
            encoding="utf-8",
        )
        self.assertIn("playwright==1.64.0", pins_for_profile(self.root, "e2e"))

    def test_plan_is_minimal(self) -> None:
        self.assertEqual(pins_for_profile(self.root, "plan"), ["playwright==1.63.0"])

    def test_missing_extra_fails_closed(self) -> None:
        (self.root / "requirements-dev.txt").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "missing required development pin"):
            pins_for_profile(self.root, "unit")

    def test_unpinned_requirement_fails_closed(self) -> None:
        (self.root / "requirements.txt").write_text("PyMuPDF>=1.28\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            pins_for_profile(self.root, "runtime")

    @patch("scripts.ci.install_pinned_python.subprocess.run")
    def test_install_uses_argument_list_and_fixed_interpreter(self, run) -> None:
        install(self.root, "unit")
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[1:4], ["-m", "pip", "install"])
        self.assertIn("--isolated", cmd)
        self.assertIn("--only-binary=:all:", cmd)
        self.assertIn("openapi-core==0.23.1", cmd)
        self.assertIn("hypothesis==6.170.9", cmd)
        self.assertEqual(run.call_args.kwargs["cwd"], self.root)
        self.assertTrue(run.call_args.kwargs["check"])


if __name__ == "__main__":
    unittest.main()
