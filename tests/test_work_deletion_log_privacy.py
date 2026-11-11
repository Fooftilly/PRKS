"""The SQLite ``work_deletion.delete_work`` cleanup logs: error types and counts, never paths.

Split from tests/test_logging_privacy.py so that module needs no SQLite
runtime. The PostgreSQL delete's thumbnail log is
tests/pg/test_http_work_delete.py and its object cleanup log
tests/pg/test_objects_lifecycle.py.
"""
import contextlib
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from tests.test_logging_privacy import SECRET_CLEANUP, SECRET_TITLE, _capture_formatted, _formatted_text
from backend.work_deletion import delete_work


class TestCleanupPrivacy(unittest.TestCase):
    def test_pdf_cleanup_logs_error_type_not_path(self):
        class _Storage:
            pdfs_dir = "/tmp"
            thumbs_dir = "/tmp"

        class _Db:
            storage = _Storage()

            def delete_work_record(self, work_id):
                return SimpleNamespace(
                    file_path="/api/pdfs/book.pdf",
                    managed_pdf_still_referenced=False,
                )

            def execute_query(self, sql, params=()):
                return []

            def connection(self):
                # The live-reference check runs in its own transaction. No
                # survivors, so cleanup reaches os.remove and fails there --
                # which is the path this test is about.
                @contextlib.contextmanager
                def _open():
                    yield _Conn()

                return _open()

        class _Cursor:
            @staticmethod
            def fetchall():
                return []

        class _Conn:
            @staticmethod
            def execute(sql, params=()):
                return _Cursor()

        class _Index:
            def remove_work(self, work_id):
                return 0

        def _raise_secret(path, *args, **kwargs):
            raise OSError(SECRET_CLEANUP)

        with patch(
            "backend.work_deletion.prks_delete_pdf_thumbnails_for_work_id",
            return_value=(),
        ):
            with patch("backend.work_deletion.os.remove", side_effect=_raise_secret):
                with _capture_formatted("prks.work_deletion") as records:
                    result = delete_work(_Db(), _Index(), "W-ABC123")
        self.assertEqual(result.cleanup_failures, ("pdf",))
        text = _formatted_text(records)
        self.assertIn("work_delete_pdf_cleanup_failed", text)
        self.assertIn("error_type=OSError", text)
        self.assertIn("work_id=W-ABC123", text)
        self.assertNotIn("SECRET_PATH_X9Q7", text)
        self.assertNotIn("/home/private/file.pdf", text)

    def test_thumbnail_cleanup_logs_failed_count_not_paths(self):
        class _Storage:
            pdfs_dir = "/tmp"
            thumbs_dir = "/tmp"

        class _Db:
            storage = _Storage()

            def delete_work_record(self, work_id):
                return None

        class _Index:
            def remove_work(self, work_id):
                return 0

        failed = (
            f"/home/private/{SECRET_TITLE}.webp",
            "/home/private/file.pdf",
        )
        with patch(
            "backend.work_deletion.prks_delete_pdf_thumbnails_for_work_id",
            return_value=failed,
        ):
            with _capture_formatted("prks.work_deletion") as records:
                result = delete_work(_Db(), _Index(), "W-ABC123")
        self.assertEqual(result.cleanup_failures, ("thumbnails",))
        text = _formatted_text(records)
        self.assertIn("failed_count=2", text)
        self.assertNotIn("failed=(", text)
        self.assertNotIn(SECRET_TITLE, text)
        self.assertNotIn("/home/private", text)


if __name__ == "__main__":
    unittest.main()
