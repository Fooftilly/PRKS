"""``LocalFilesystemStorage``: the shared contract plus local-only specifics (§10.3)."""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from backend.storage import objects
from backend.storage.config import StorageConfig
from backend.storage.objects import (
    NAMESPACE_ASSET_OBJECTS,
    NAMESPACE_PORTRAITS,
    InvalidStorageKey,
    LocalFilesystemStorage,
    LocalPathCapable,
    ObjectIntegrityError,
    ObjectNotFound,
    StorageKey,
)
from tests.storage_backend_contract import StorageBackendContract, _writer


class LocalStorageTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="prks-objects-")
        self.root = os.path.realpath(self._tmp.name)
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self._tmp.cleanup()

    def make_backend(self):
        return LocalFilesystemStorage(self.root)


class TestLocalFilesystemStorageContract(StorageBackendContract, LocalStorageTestCase):
    """The same suite a future object-storage backend must pass."""


class TestLocalSpecifics(LocalStorageTestCase):
    def setUp(self):
        super().setUp()
        self.backend = self.make_backend()
        self.key = StorageKey(NAMESPACE_ASSET_OBJECTS, "1700000000_abcd1234_paper.pdf")

    def test_layout_1_directories_are_unchanged(self):
        self.backend.put_new(self.key, _writer(b"pdf"))
        self.backend.put_new(StorageKey(NAMESPACE_PORTRAITS, "p_hash.webp"), _writer(b"img"))
        with open(os.path.join(self.root, "pdfs", self.key.name), "rb") as handle:
            self.assertEqual(handle.read(), b"pdf")
        self.assertTrue(os.path.isfile(os.path.join(self.root, "people", "p_hash.webp")))
        self.assertEqual(self.backend.backend_type, "local")

    def test_for_config_matches_storage_config_paths(self):
        cfg = StorageConfig.for_testing(self.root)
        backend = LocalFilesystemStorage.for_config(cfg)
        backend.put_new(self.key, _writer(b"x"))
        self.assertTrue(os.path.isfile(os.path.join(cfg.pdfs_dir, self.key.name)))

    def test_created_namespace_directories_are_owner_only(self):
        self.backend.put_new(self.key, _writer(b"x"))
        if os.name == "posix":
            self.assertEqual(os.stat(os.path.join(self.root, "pdfs")).st_mode & 0o777, 0o700)

    def test_no_temporaries_left_behind(self):
        self.backend.put_new(self.key, _writer(b"x"))
        self.backend.replace(self.key, _writer(b"y"))

        def boom(stream):
            stream.write(b"z")
            raise RuntimeError("fail")

        with self.assertRaises(RuntimeError):
            self.backend.replace(self.key, boom)
        with self.assertRaises(RuntimeError):
            self.backend.put_new(StorageKey(NAMESPACE_ASSET_OBJECTS, "other.pdf"), boom)
        self.assertEqual(os.listdir(os.path.join(self.root, "pdfs")), [self.key.name])

    def test_content_barrier_precedes_publication(self):
        events = []
        real_sync = objects.fsync_open_file
        real_link = os.link
        real_replace = os.replace

        def sync(fd):
            events.append("fsync")
            real_sync(fd)

        def link(src, dst, *a, **k):
            events.append("publish")
            return real_link(src, dst, *a, **k)

        def replace(src, dst, *a, **k):
            events.append("publish")
            return real_replace(src, dst, *a, **k)

        def dir_sync(path):
            events.append("dirsync")
            return True

        with (
            patch.object(objects, "fsync_open_file", sync),
            patch.object(objects, "fsync_directory", dir_sync),
            patch.object(objects.os, "link", link),
            patch.object(objects.os, "replace", replace),
        ):
            self.backend.put_new(self.key, _writer(b"x"))
            self.assertEqual(events[-3:], ["fsync", "publish", "dirsync"])
            events.clear()
            self.backend.replace(self.key, _writer(b"y"))
            self.assertEqual(events, ["fsync", "publish", "dirsync"])

    def test_failed_content_sync_publishes_nothing(self):
        def fail(_fd):
            raise OSError(5, "EIO")

        with patch.object(objects, "fsync_open_file", fail):
            with self.assertRaises(OSError):
                self.backend.put_new(self.key, _writer(b"x"))
        self.assertIsNone(self.backend.stat(self.key))
        self.backend.put_new(self.key, _writer(b"old"))
        with patch.object(objects, "fsync_open_file", fail):
            with self.assertRaises(OSError):
                self.backend.replace(self.key, _writer(b"new"))
        with self.backend.open_read(self.key) as handle:
            self.assertEqual(handle.read(), b"old")

    def test_directory_sync_failure_is_reported_not_fatal(self):
        with patch.object(objects, "fsync_directory", return_value=False):
            with self.assertLogs("prks.storage", level="WARNING") as logs:
                self.backend.put_new(self.key, _writer(b"x"))
        self.assertIn("storage_dir_sync_failed op=put_new", "\n".join(logs.output))
        self.assertNotIn(self.key.name, "\n".join(logs.output))

    def test_filesystem_without_hard_links_uses_no_replace_rename(self):
        observed = []
        real_rename = objects.rename_noreplace

        def no_links(*_a, **_k):
            raise OSError(1, "EPERM")

        def watched(src, dst):
            # Nothing -- not even an empty reservation -- is under the key
            # until the single atomic publication.
            observed.append(os.path.lexists(dst))
            return real_rename(src, dst)

        with patch.object(objects.os, "link", no_links), patch.object(objects, "rename_noreplace", watched):
            self.backend.put_new(self.key, _writer(b"x"))
            with self.assertRaises(objects.ObjectExists):
                self.backend.put_new(self.key, _writer(b"y"))
        # The second attempt is refused before it writes anything.
        self.assertEqual(observed, [False])
        with self.backend.open_read(self.key) as handle:
            self.assertEqual(handle.read(), b"x")
        self.assertEqual(os.listdir(os.path.join(self.root, "pdfs")), [self.key.name])

    def test_without_any_exclusive_primitive_put_new_fails_closed(self):
        def no_links(*_a, **_k):
            raise OSError(1, "EPERM")

        def unsupported(_src, _dst):
            raise objects.ExclusivePublishUnsupported("none")

        with patch.object(objects.os, "link", no_links), patch.object(objects, "rename_noreplace", unsupported):
            with self.assertRaises(objects.ExclusivePublishUnsupported):
                self.backend.put_new(self.key, _writer(b"x"))
        self.assertIsNone(self.backend.stat(self.key))
        self.assertEqual(os.listdir(os.path.join(self.root, "pdfs")), [])

    def test_no_replace_rename_primitive(self):
        src = os.path.join(self.root, "src")
        dst = os.path.join(self.root, "dst")
        for path, body in ((src, b"new"), (dst, b"old")):
            with open(path, "wb") as handle:
                handle.write(body)
        try:
            with self.assertRaises(FileExistsError):
                objects.rename_noreplace(src, dst)
        except objects.ExclusivePublishUnsupported:  # pragma: no cover - platform
            self.skipTest("no-replace rename unavailable here")
        with open(dst, "rb") as handle:
            self.assertEqual(handle.read(), b"old")
        os.remove(dst)
        objects.rename_noreplace(src, dst)
        self.assertFalse(os.path.exists(src))

    def test_links_are_refused_everywhere(self):
        pdfs = os.path.join(self.root, "pdfs")
        os.mkdir(pdfs)
        outside = os.path.join(self.root, "outside.pdf")
        with open(outside, "wb") as handle:
            handle.write(b"secret")
        try:
            os.symlink(outside, os.path.join(pdfs, self.key.name))
        except (OSError, NotImplementedError) as exc:  # pragma: no cover
            self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaises(ObjectIntegrityError):
            self.backend.stat(self.key)
        with self.assertRaises(ObjectIntegrityError):
            self.backend.open_read(self.key)
        with self.assertRaises(ObjectIntegrityError):
            self.backend.delete(self.key)
        with self.assertRaises(ObjectIntegrityError):
            self.backend.replace(self.key, _writer(b"x"))
        with self.assertRaises(ObjectIntegrityError):
            self.backend.put_new(self.key, _writer(b"x"))
        self.assertIsNone(self.backend.verify(self.key, "0" * 64))
        self.assertEqual(list(self.backend.iter_keys(NAMESPACE_ASSET_OBJECTS)), [])
        with open(outside, "rb") as handle:
            self.assertEqual(handle.read(), b"secret")

    def test_namespace_directory_link_is_refused(self):
        elsewhere = os.path.join(self.root, "elsewhere")
        os.mkdir(elsewhere)
        try:
            os.symlink(elsewhere, os.path.join(self.root, "pdfs"))
        except (OSError, NotImplementedError) as exc:  # pragma: no cover
            self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaises(ObjectIntegrityError):
            self.backend.put_new(self.key, _writer(b"x"))
        with self.assertRaises(ObjectIntegrityError):
            list(self.backend.iter_keys(NAMESPACE_ASSET_OBJECTS))
        self.assertEqual(os.listdir(elsewhere), [])

    def test_root_is_resolved_once_at_construction(self):
        real = os.path.join(self.root, "real")
        os.mkdir(real)
        link = os.path.join(self.root, "link")
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError) as exc:  # pragma: no cover
            self.skipTest(f"symlinks unavailable: {exc}")
        backend = LocalFilesystemStorage(link)
        other = os.path.join(self.root, "other")
        os.mkdir(other)
        os.remove(link)
        os.symlink(other, link)
        backend.put_new(self.key, _writer(b"x"))
        self.assertTrue(os.path.isfile(os.path.join(real, "pdfs", self.key.name)))
        self.assertFalse(os.path.exists(os.path.join(other, "pdfs")))

    def test_iter_keys_skips_temporaries_directories_and_reserved_names(self):
        self.backend.put_new(self.key, _writer(b"x"))
        pdfs = os.path.join(self.root, "pdfs")
        for name in (".prks-write-abc.tmp", ".linearized_x.pdf", ".prks-cache-1.tmp"):
            open(os.path.join(pdfs, name), "w").close()
        os.mkdir(os.path.join(pdfs, "subdir"))
        self.assertEqual([k.name for k in self.backend.iter_keys(NAMESPACE_ASSET_OBJECTS)], [self.key.name])

    def test_drive_qualified_names_are_refused_with_windows_semantics(self):
        key = StorageKey(NAMESPACE_ASSET_OBJECTS, "C:paper.pdf")
        with patch.object(objects.paths, "windows_path_semantics", return_value=True):
            with self.assertRaises(InvalidStorageKey):
                self.backend.put_new(key, _writer(b"x"))
        self.assertFalse(os.path.exists(os.path.join(self.root, "pdfs", "paper.pdf")))

    def test_local_path_extension(self):
        self.assertIsInstance(self.backend, LocalPathCapable)
        with self.assertRaises(ObjectNotFound):
            with self.backend.local_path(self.key):
                pass
        self.backend.put_new(self.key, _writer(b"x"))
        with self.backend.local_path(self.key) as path:
            self.assertEqual(path, os.path.join(self.root, "pdfs", self.key.name))

    def test_version_token_is_local_stat_shape(self):
        info = self.backend.put_new(self.key, _writer(b"abc"))
        st = os.lstat(os.path.join(self.root, "pdfs", self.key.name))
        self.assertEqual(info.version, f"{st.st_mtime_ns}:{st.st_size}:{st.st_ino}")

    def test_backend_is_not_wired_into_production_paths_yet(self):
        # Phase A adds the abstraction only; Phase B routes operations through it.
        import subprocess

        out = subprocess.run(
            ["git", "grep", "-l", "LocalFilesystemStorage", "--", "backend", "prks_app.py"],
            cwd=_PROJECT_DIR, capture_output=True, text=True,
        )
        if out.returncode not in (0, 1):  # pragma: no cover - no git
            self.skipTest("git unavailable")
        self.assertEqual(out.stdout.split(), ["backend/storage/objects.py"])


if __name__ == "__main__":
    unittest.main()
