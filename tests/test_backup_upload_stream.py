"""The backup upload stream the PostgreSQL stage route reads with (PG-C2 #641).

``POST /api/backups/stage`` on the PostgreSQL Library streams its body with
``stream_upload_to_file`` and bounds it with ``backup_max_upload_bytes``.
These pin what the route relies on: the body is copied in bounded reads to
an owner-only file synced before it is used, a body over the limit or
shorter than its declared length leaves no file, and the limit comes from
``PRKS_BACKUP_MAX_UPLOAD_BYTES``. They need no database.
"""

import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

import backend.backup_restore as upload
from backend.backup_restore import RestoreError, backup_max_upload_bytes, stream_upload_to_file


class _Reader:
    def __init__(self, data: bytes, fail_after: int = -1):
        self._source = io.BytesIO(data)
        self.reads: list[int] = []
        self._fail_after = fail_after

    def read(self, n=-1):
        if n is None or n < 0:
            raise AssertionError("unbounded read")
        if self._fail_after >= 0 and len(self.reads) >= self._fail_after:
            raise ConnectionResetError("client went away")
        self.reads.append(n)
        return self._source.read(n)


class StreamUploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prks-upload-stream-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dest = os.path.join(self.tmp, "staging", "upload.zip")

    def test_the_body_is_copied_in_bounded_reads_and_synced(self):
        payload = b"PK" + b"x" * 100
        reader = _Reader(payload)
        with patch.object(upload, "fsync_open_file") as synced:
            written = stream_upload_to_file(reader, self.dest, expected_length=len(payload), max_bytes=1000,
                                            chunk_size=16)
        self.assertEqual(written, len(payload))
        with open(self.dest, "rb") as handle:
            self.assertEqual(handle.read(), payload)
        self.assertTrue(reader.reads)
        self.assertLessEqual(max(reader.reads), 16)
        synced.assert_called_once()
        if os.name == "posix":
            self.assertEqual(os.stat(self.dest).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(os.path.dirname(self.dest)).st_mode & 0o777, 0o700)

    def test_a_body_over_the_limit_is_refused_before_it_is_read(self):
        reader = _Reader(b"x" * 10)
        with self.assertRaises(RestoreError) as caught:
            stream_upload_to_file(reader, self.dest, expected_length=10, max_bytes=9)
        self.assertEqual(caught.exception.reason, "request_too_large")
        self.assertEqual(reader.reads, [])
        self.assertFalse(os.path.exists(self.dest))

    def test_a_negative_length_is_refused(self):
        with self.assertRaises(RestoreError) as caught:
            stream_upload_to_file(_Reader(b""), self.dest, expected_length=-1, max_bytes=9)
        self.assertEqual(caught.exception.reason, "invalid_length")
        self.assertFalse(os.path.exists(self.dest))

    def test_a_body_shorter_than_its_length_leaves_no_file(self):
        with self.assertRaises(RestoreError) as caught:
            stream_upload_to_file(_Reader(b"abc"), self.dest, expected_length=10, max_bytes=100)
        self.assertEqual(caught.exception.reason, "incomplete_upload")
        self.assertFalse(os.path.exists(self.dest))

    def test_a_reader_that_fails_leaves_no_file(self):
        with self.assertRaises(ConnectionResetError):
            stream_upload_to_file(_Reader(b"x" * 64, fail_after=1), self.dest, expected_length=64, max_bytes=100,
                                  chunk_size=16)
        self.assertFalse(os.path.exists(self.dest))


class UploadLimitTests(unittest.TestCase):
    def test_the_limit_comes_from_the_environment_or_the_default(self):
        for raw, expected in (("", upload.DEFAULT_MAX_UPLOAD_BYTES), ("12345", 12345),
                              ("0", upload.DEFAULT_MAX_UPLOAD_BYTES), ("-5", upload.DEFAULT_MAX_UPLOAD_BYTES),
                              ("lots", upload.DEFAULT_MAX_UPLOAD_BYTES), (" 77 ", 77)):
            with self.subTest(raw=raw), patch.dict(os.environ, {"PRKS_BACKUP_MAX_UPLOAD_BYTES": raw}):
                self.assertEqual(backup_max_upload_bytes(), expected)


if __name__ == "__main__":
    unittest.main()
