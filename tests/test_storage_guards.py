import os
import sys
import unittest
import subprocess
import shutil


# Ensure the repo root is importable as a module root.
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

os.environ.setdefault("PRKS_TESTING", "1")
os.environ.setdefault("PRKS_STORAGE", os.path.join(_PROJECT_DIR, "data_testing"))


class TestStorageGuards(unittest.TestCase):
    def test_from_env_rejects_data_storage_when_testing(self):
        from backend.storage.config import StorageConfig

        old_testing = os.environ.get("PRKS_TESTING")
        old_storage = os.environ.get("PRKS_STORAGE")
        try:
            os.environ["PRKS_TESTING"] = "1"
            os.environ["PRKS_STORAGE"] = "/data"
            with self.assertRaises(RuntimeError) as ctx:
                StorageConfig.from_env()
            self.assertIn("refusing to use PRKS_STORAGE under /data", str(ctx.exception))
        finally:
            if old_testing is None:
                os.environ.pop("PRKS_TESTING", None)
            else:
                os.environ["PRKS_TESTING"] = old_testing
            if old_storage is None:
                os.environ.pop("PRKS_STORAGE", None)
            else:
                os.environ["PRKS_STORAGE"] = old_storage

    def test_server_import_does_not_enforce_data_guard(self):
        env = os.environ.copy()
        env["PRKS_TESTING"] = "1"
        env["PRKS_STORAGE"] = "/data"

        py = shutil.which("python3") or shutil.which("python") or sys.executable
        proc = subprocess.run(
            [py, "-c", "import backend.server"],
            cwd=_PROJECT_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)

    def test_for_testing_still_refuses_data(self):
        from backend.storage.config import StorageConfig

        with self.assertRaises(RuntimeError) as ctx:
            StorageConfig.for_testing("/data")
        self.assertIn("refusing to use PRKS_STORAGE under /data", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
