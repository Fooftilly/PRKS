import os
import sys
import unittest
import subprocess
import shutil
from unittest.mock import patch


# Ensure the repo root is importable as a module root.
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

os.environ.setdefault("PRKS_TESTING", "1")
os.environ.setdefault("PRKS_STORAGE", os.path.join(_PROJECT_DIR, "data_testing"))


class TestStorageGuards(unittest.TestCase):
    def test_db_manager_rejects_data_storage_when_testing(self):
        from backend import db_manager

        old_testing = os.environ.get("PRKS_TESTING")
        old_storage = os.environ.get("PRKS_STORAGE")
        try:
            os.environ["PRKS_TESTING"] = "1"
            os.environ["PRKS_STORAGE"] = "/data"
            with self.assertRaises(RuntimeError) as ctx:
                db_manager._get_storage_root()
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

    def test_server_import_rejects_data_storage_when_testing(self):
        # backend/server.py resolves storage dirs at import time, so validate in a fresh process.
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

        self.assertNotEqual(proc.returncode, 0)
        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        self.assertIn("refusing to use PRKS_STORAGE under /data", combined)

    def test_text_index_rejects_data_storage_when_testing(self):
        from backend import text_index

        old_testing = os.environ.get("PRKS_TESTING")
        old_storage = os.environ.get("PRKS_STORAGE")
        try:
            os.environ["PRKS_TESTING"] = "1"
            os.environ["PRKS_STORAGE"] = "/data"
            with self.assertRaises(RuntimeError) as ctx:
                text_index._resolve_storage_root()
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

    def test_consumers_delegate_to_paths(self):
        from backend import db_manager
        from backend import text_index
        from backend.storage import paths
        import backend.server as server_module

        self.assertIs(db_manager._get_storage_root, paths.resolve_storage_root)
        self.assertIs(db_manager._resolve_pdfs_dir, paths.resolve_pdfs_dir)
        self.assertIs(db_manager._resolve_thumbs_dir, paths.resolve_thumbs_dir)
        self.assertIs(server_module._get_storage_root, paths.resolve_storage_root)
        self.assertIs(server_module._resolve_db_path, paths.resolve_db_path)
        self.assertIs(server_module._resolve_pdfs_dir, paths.resolve_pdfs_dir)
        self.assertIs(text_index._resolve_storage_root, paths.resolve_defaulted_storage_root)
        self.assertIs(text_index._resolve_pdfs_dir, paths.resolve_pdfs_dir)
        self.assertIs(text_index._resolve_index_db_path, paths.resolve_index_db_path)

    def test_private_alias_monkeypatch_still_intercepts(self):
        from backend import db_manager
        from backend import text_index

        with patch.object(db_manager, "_resolve_pdfs_dir", return_value="/tmp/patched-pdfs"):
            self.assertEqual(db_manager._resolve_pdfs_dir(), "/tmp/patched-pdfs")
            rows = [{"file_path": "/api/pdfs/missing.pdf"}]
            db_manager.enrich_work_rows_pdf_file_size(rows)
            self.assertIsNone(rows[0]["file_size_bytes"])
        with patch.object(text_index, "_resolve_pdfs_dir", return_value="/tmp/patched-index-pdfs"):
            self.assertIsNone(text_index._safe_pdf_path("../x.pdf"))


if __name__ == "__main__":
    unittest.main()
