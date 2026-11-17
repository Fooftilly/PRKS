"""``backend.fs_durability``: the directory and file barriers every durable write uses.

Moved from ``tests/test_restore_durability.py`` (PG-C2 #641): the SQLite
restore journal goes with SQLite, but these primitives stay in use (the
PostgreSQL backup archive, managed PDF writes, root binding). Nothing here
opens a database.
"""

import errno
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from backend import fs_durability


class TestDurabilityPrimitives(unittest.TestCase):
    """The shared helpers #110 added to the #129 convention."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="prks-fs-durability-")
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        self.left = os.path.join(self.root, "left")
        self.right = os.path.join(self.root, "right")
        os.makedirs(self.left)
        os.makedirs(self.right)
        # macOS reaches for F_FULLFSYNC, so the os.fsync spies below would see
        # nothing there. Pin the plain-fsync platform the assertions describe.
        patcher = patch.object(fs_durability, "_FULLFSYNC", None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_rename_inside_one_directory_is_synced_once(self):
        with patch.object(fs_durability, "fsync_directory", return_value=True) as sync:
            self.assertTrue(fs_durability.fsync_directories(self.left, self.left + "/"))

        self.assertEqual(sync.call_count, 1)

    def test_a_rename_across_directories_syncs_both_parents(self):
        with patch.object(fs_durability, "fsync_directory", return_value=True) as sync:
            self.assertTrue(fs_durability.fsync_directories(self.left, self.right))

        self.assertEqual(
            [call.args[0] for call in sync.call_args_list], [self.left, self.right]
        )

    def test_every_directory_is_attempted_even_after_one_refuses(self):
        attempted = []

        def refuse_left(path):
            attempted.append(path)
            return path != self.left

        with patch.object(fs_durability, "fsync_directory", refuse_left):
            self.assertFalse(fs_durability.fsync_directories(self.left, self.right))

        self.assertEqual(attempted, [self.left, self.right])

    def test_a_directory_fsync_the_platform_lacks_is_not_a_failure(self):
        for code in (errno.EINVAL, errno.ENOSYS, errno.ENOTSUP):
            with self.subTest(code=code):
                with patch.object(
                    fs_durability.os, "fsync", side_effect=OSError(code, "nope")
                ):
                    self.assertTrue(
                        fs_durability.fsync_directories(self.left, self.right)
                    )

    def test_a_refused_directory_fsync_is_reported(self):
        with patch.object(
            fs_durability.os, "fsync", side_effect=OSError(errno.EPERM, "denied")
        ):
            self.assertFalse(fs_durability.fsync_directories(self.left))

    def test_an_open_file_is_flushed_through_the_platform_barrier(self):
        path = os.path.join(self.left, "note.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("x")
            handle.flush()
            with patch.object(fs_durability.os, "fsync") as fsync:
                fs_durability.fsync_open_file(handle.fileno())
        self.assertEqual(fsync.call_count, 1)

    def test_an_open_file_that_cannot_be_flushed_raises(self):
        path = os.path.join(self.left, "note.txt")
        with open(path, "w", encoding="utf-8") as handle:
            with patch.object(
                fs_durability.os, "fsync", side_effect=OSError(errno.EIO, "I/O error")
            ):
                with self.assertRaises(OSError):
                    fs_durability.fsync_open_file(handle.fileno())


if __name__ == "__main__":
    unittest.main()
