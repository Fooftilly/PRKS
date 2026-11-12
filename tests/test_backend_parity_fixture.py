"""The parity fixture (#625, PG-3; PG-C2 #641).

No PostgreSQL server is needed here. ``tests/pg/test_backend_parity.py``
covers the facade the fixture opens over a harness database.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from tests.backend_parity import parametrize_backends

# The selection ``scripts/test-postgres --parity`` makes.
_SELECTION = "grep -l '^@parametrize_backends' tests/test_*.py"
_MARKER = re.compile(r"^@parametrize_backends$", re.MULTILINE)


def selected_modules() -> list[str]:
    tests_dir = os.path.join(_PROJECT_DIR, "tests")
    found = []
    for name in sorted(os.listdir(tests_dir)):
        if name.startswith("test_") and name.endswith(".py"):
            with open(os.path.join(tests_dir, name), encoding="utf-8") as handle:
                if _MARKER.search(handle.read()):
                    found.append("tests." + name[:-3])
    return found


class FixtureSelectionTests(unittest.TestCase):
    def test_only_parity_test_cases_can_be_parametrized(self):
        class Plain(unittest.TestCase):
            pass

        with self.assertRaises(TypeError):
            parametrize_backends(Plain)

    def test_parity_runner_selects_the_opted_in_modules(self):
        with open(os.path.join(_PROJECT_DIR, "scripts", "test-postgres"), encoding="utf-8") as handle:
            self.assertIn(_SELECTION, handle.read())
        modules = selected_modules()
        self.assertIn("tests.test_api_saved_views", modules)
        self.assertNotIn("tests.test_backend_parity_fixture", modules)

    def test_without_a_test_server_the_default_suite_skips_the_parity_tests(self):
        # What `python run_tests.py` does with a selected module when no
        # PRKS_TEST_DATABASE_URL names a disposable server.
        env = {k: v for k, v in os.environ.items() if k != "PRKS_TEST_DATABASE_URL"}
        code = (
            "import sys, unittest\n"
            "suite = unittest.TestLoader().discover('tests', pattern='test_api_saved_views.py')\n"
            "result = unittest.TestResult()\n"
            "suite.run(result)\n"
            "reasons = {reason for _, reason in result.skipped}\n"
            "ok = (result.testsRun > 0 and len(result.skipped) == result.testsRun\n"
            "      and reasons == {'PRKS_TEST_DATABASE_URL is not set'}\n"
            "      and not result.failures and not result.errors)\n"
            "sys.exit(0 if ok else 1)\n"
        )
        proc = subprocess.run([sys.executable, "-c", code], cwd=_PROJECT_DIR, env=env, check=False)
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
