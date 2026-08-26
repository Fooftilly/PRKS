import os
import shutil
import subprocess
import sys
import unittest


_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)


class TestRunTests(unittest.TestCase):
    def test_import_run_tests_does_not_mutate_env(self):
        env = os.environ.copy()
        env["PRKS_TESTING"] = "0"
        env["PRKS_STORAGE"] = "/tmp/hostile-production"
        env["PRKS_FOR_PROCESSING_DIR"] = "/tmp/hostile-processing"
        env["PRKS_LOG_FILE"] = "/tmp/hostile.log"
        env["PYTHONPATH"] = _PROJECT_DIR + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
        )
        py = shutil.which("python3") or shutil.which("python") or sys.executable
        proc = subprocess.run(
            [
                py,
                "-c",
                (
                    "import os, sys\n"
                    f"sys.path.insert(0, { _PROJECT_DIR !r })\n"
                    "from run_tests import apply_isolated_test_env\n"
                    "assert os.environ['PRKS_TESTING'] == '0'\n"
                    "assert os.environ['PRKS_STORAGE'] == '/tmp/hostile-production'\n"
                    "assert os.environ['PRKS_FOR_PROCESSING_DIR'] == '/tmp/hostile-processing'\n"
                    "assert os.environ['PRKS_LOG_FILE'] == '/tmp/hostile.log'\n"
                    "print('ok')\n"
                ),
            ],
            cwd=_PROJECT_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertIn("ok", proc.stdout)

    def test_apply_isolated_test_env_overrides_hostile_inherited_env(self):
        from run_tests import apply_isolated_test_env

        keys = (
            "PRKS_TESTING",
            "PRKS_STORAGE",
            "PRKS_FOR_PROCESSING_DIR",
            "PRKS_LOG_FILE",
        )
        old = {k: os.environ.get(k) for k in keys}
        try:
            os.environ["PRKS_TESTING"] = "0"
            os.environ["PRKS_STORAGE"] = "/tmp/hostile-production"
            os.environ["PRKS_FOR_PROCESSING_DIR"] = "/tmp/hostile-processing"
            os.environ["PRKS_LOG_FILE"] = "/tmp/hostile.log"
            apply_isolated_test_env(_PROJECT_DIR)
            self.assertEqual(os.environ["PRKS_TESTING"], "1")
            self.assertEqual(
                os.environ["PRKS_STORAGE"],
                os.path.join(_PROJECT_DIR, "data_testing"),
            )
            self.assertNotIn("PRKS_FOR_PROCESSING_DIR", os.environ)
            self.assertNotIn("PRKS_LOG_FILE", os.environ)
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
