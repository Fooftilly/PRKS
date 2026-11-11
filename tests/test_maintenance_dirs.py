"""The PostgreSQL backup and restore directories under a storage root (PG-C2 #641).

Backup archives, staged uploads and private restore copies live in
``.prks-maintenance`` beside the root's own lease and claim files. These pin
that they share the root's one maintenance directory name, never take the
name of a root file, and that the startup and idle sweeps remove only their
own entries: the marker, the lease, the claim and anything they do not
recognise stay. No database is opened.
"""

import os
import shutil
import sys
import tempfile
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from backend.domain.maintenance import jobs, staged
from backend.storage import root_marker

_ROOT_FILES = (root_marker.ROOT_LOCK_NAME, root_marker.CLAIM_LOCK_NAME, root_marker.CONTENT_LOCK_NAME)


class MaintenanceDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="prks-maintenance-dirs-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_every_directory_is_inside_the_one_maintenance_directory(self):
        self.assertEqual(root_marker.MARKER_FILENAME, "prks-root.json")
        for name in (jobs.ARCHIVE_DIRNAME, jobs.RESTORE_DIRNAME, staged.STAGED_DIRNAME):
            with self.subTest(name=name):
                parent, leaf = os.path.split(name)
                self.assertEqual(parent, root_marker.MAINTENANCE_DIRNAME)
                self.assertNotIn(leaf, _ROOT_FILES)

    def test_the_sweeps_leave_the_root_files_and_unknown_entries_alone(self):
        maintenance = os.path.join(self.root, root_marker.MAINTENANCE_DIRNAME)
        kept = []
        for directory in (maintenance, jobs.restore_dir(self.root), staged.staged_dir(self.root)):
            for name in _ROOT_FILES + ("notes.txt",):
                path = os.path.join(directory, name)
                with open(path, "wb") as handle:
                    handle.write(b"x")
                os.utime(path, (0, 0))  # older than any time-to-live
                kept.append(path)
        marker = os.path.join(self.root, root_marker.MARKER_FILENAME)
        with open(marker, "wb") as handle:
            handle.write(b"{}")
        kept.append(marker)
        stale_copy = os.path.join(jobs.restore_dir(self.root), "abandoned.upload")
        old_upload = staged.staged_path(self.root, "a" * 32, "U-1")
        for path in (stale_copy, old_upload):
            with open(path, "wb") as handle:
                handle.write(b"x")
            os.utime(path, (0, 0))

        self.assertEqual(jobs.prune_restore_copies(self.root), 1)
        self.assertEqual(staged.prune_staged(self.root), 1)
        self.assertFalse(os.path.exists(stale_copy))
        self.assertFalse(os.path.exists(old_upload))
        for path in kept:
            with self.subTest(path=os.path.relpath(path, self.root)):
                self.assertTrue(os.path.isfile(path))

    def test_a_token_only_names_a_file_its_user_staged(self):
        path = staged.staged_path(self.root, "b" * 32, "U-1")
        with open(path, "wb") as handle:
            handle.write(b"x")
        self.assertEqual(staged.find_staged(self.root, "b" * 32, "U-1"), path)
        for token, user in (("b" * 32, "U-2"), ("../" + "b" * 29, "U-1"), ("B" * 32, "U-1"), ("", "U-1")):
            with self.subTest(token=token, user=user):
                self.assertIsNone(staged.find_staged(self.root, token, user))

    def test_no_maintenance_module_extracts_an_archive_wholesale(self):
        """Restore reads each member through the archive's checks, never ``extractall``."""
        directory = os.path.join(_PROJECT_DIR, "backend", "domain", "maintenance")
        for name in sorted(os.listdir(directory)):
            if name.endswith(".py"):
                with self.subTest(module=name), open(os.path.join(directory, name), encoding="utf-8") as handle:
                    self.assertNotIn("extractall", handle.read())


if __name__ == "__main__":
    unittest.main()
