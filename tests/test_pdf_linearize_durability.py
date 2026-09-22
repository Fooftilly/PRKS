"""Linearization must not hand back a PDF less durable than the one it replaced.

EF-018 / #112. qpdf's temporary output was renamed over a canonical managed PDF
without ever being fsynced, and the directory entry that rename created was
synced only by the single caller that remembered to compensate. Both halves now
belong to ``maybe_linearize_pdf_in_place()``, so these tests pin the *ordering*
(temp sync strictly before the replace, directory sync strictly after it) and the
failure semantics, rather than only checking that linearized bytes came out.

qpdf is not installed in the test environment and must not be required: every
test here drives a fake ``subprocess.run`` that writes the "linearized" output
the real binary would.
"""

import errno
import logging
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend import fs_durability
from backend import pdf_linearize
from backend.pdf_linearize import maybe_linearize_pdf_in_place
from backend.services import work_pdf_replace


ORIGINAL = b"%PDF-1.4\n% original managed bytes\n%%EOF\n"
LINEARIZED = b"%PDF-1.4\n%\x80\x80\x80\x80\n/Linearized 1\n% qpdf output\n%%EOF\n"

# Every production call site of the helper. Durability is a property of the
# helper, so none of these may behave differently from the others.
PRODUCTION_CONTEXTS = (
    "work-pdf-overwrite",   # ordinary Work PDF overwrite
    "work-create-upload",   # new managed upload
    "processing-import",    # Processing inbox import
    "settings-bulk",        # Settings bulk linearization
)


def _fake_qpdf(output: bytes = LINEARIZED, returncode: int = 0):
    """A ``subprocess.run`` stand-in that writes qpdf's output file."""

    def run(argv, **kwargs):
        if returncode == 0:
            with open(argv[3], "wb") as fh:
                fh.write(output)
        return SimpleNamespace(returncode=returncode, stdout="", stderr="qpdf noise")

    return run


class _LinearizeHarness:
    """Records the durability-relevant calls the helper makes, in order."""

    def __init__(self, pdf_path):
        self.pdf_path = pdf_path
        # Captured before the patch: ``os.replace`` is what the spy replaces.
        self._real_replace = os.replace
        self.events = []
        self.synced_bytes = None
        self.sync_file_error = None
        self.dir_sync_ok = True

    def fsync_file_path(self, path):
        if self.sync_file_error is not None:
            raise self.sync_file_error
        # Prove the file synced is the one that becomes canonical, not a
        # stale or already-replaced path.
        with open(path, "rb") as fh:
            self.synced_bytes = fh.read()
        self.events.append(("fsync-file", path))
        fs_durability.fsync_file_path(path)

    def fsync_directory(self, path):
        self.events.append(("fsync-dir", path))
        if not self.dir_sync_ok:
            return False
        return fs_durability.fsync_directory(path)

    def replace(self, src, dst):
        self.events.append(("replace", src, dst))
        return self._real_replace(src, dst)

    @property
    def steps(self):
        return [event[0] for event in self.events]


class _LinearizeCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="prks-linearize-durability-")
        self.addCleanup(self._tmp.cleanup)
        self.pdfs_dir = self._tmp.name
        self.pdf_path = os.path.join(self.pdfs_dir, "managed.pdf")
        with open(self.pdf_path, "wb") as fh:
            fh.write(ORIGINAL)

    def canonical_bytes(self):
        with open(self.pdf_path, "rb") as fh:
            return fh.read()

    def temp_leftovers(self):
        return [n for n in os.listdir(self.pdfs_dir) if n.startswith(".linearized_")]

    def run_linearize(self, harness, *, context="work-pdf-overwrite", qpdf=None):
        real_replace = os.replace
        with patch.object(pdf_linearize, "_linearize_enabled", return_value=True), \
                patch.object(pdf_linearize.shutil, "which", return_value="/usr/bin/qpdf"), \
                patch.object(pdf_linearize.subprocess, "run", qpdf or _fake_qpdf()), \
                patch.object(pdf_linearize, "fsync_file_path", harness.fsync_file_path), \
                patch.object(pdf_linearize, "fsync_directory", harness.fsync_directory), \
                patch.object(pdf_linearize.os, "replace", harness.replace):
            result = maybe_linearize_pdf_in_place(self.pdf_path, context=context)
        self.assertIs(os.replace, real_replace, "os.replace stayed patched")
        return result


class TestLinearizeDurabilityOrdering(_LinearizeCase):
    def test_temp_output_is_synced_before_replace_and_directory_after(self):
        harness = _LinearizeHarness(self.pdf_path)

        changed, reason = self.run_linearize(harness)

        self.assertEqual((changed, reason), (True, "ok"))
        self.assertEqual(
            harness.steps,
            ["fsync-file", "replace", "fsync-dir"],
            "the qpdf output must be durable before it becomes canonical, and "
            "the directory entry durable only after the rename created it",
        )
        sync_file, replace, sync_dir = harness.events
        self.assertEqual(
            sync_file[1], replace[1],
            "the file that was synced is the file that was renamed into place",
        )
        self.assertEqual(replace[2], self.pdf_path)
        self.assertEqual(
            sync_dir[1], os.path.dirname(self.pdf_path),
            "the directory synced is the one the rename changed",
        )
        self.assertEqual(
            harness.synced_bytes, LINEARIZED,
            "the sync happened after qpdf finished writing, not before",
        )
        self.assertEqual(self.canonical_bytes(), LINEARIZED)
        self.assertEqual(self.temp_leftovers(), [])

    def test_every_production_context_gets_the_same_durability(self):
        """Durability belongs to the helper, so no caller can opt out of it."""
        for context in PRODUCTION_CONTEXTS:
            with self.subTest(context=context):
                with open(self.pdf_path, "wb") as fh:
                    fh.write(ORIGINAL)
                harness = _LinearizeHarness(self.pdf_path)

                changed, reason = self.run_linearize(harness, context=context)

                self.assertEqual((changed, reason), (True, "ok"))
                self.assertEqual(harness.steps, ["fsync-file", "replace", "fsync-dir"])
                self.assertEqual(self.canonical_bytes(), LINEARIZED)


class TestLinearizeDurabilityFailures(_LinearizeCase):
    def test_a_temp_that_cannot_be_synced_never_replaces_the_canonical_pdf(self):
        """Linearization is an optimization: it may decline, never damage."""
        harness = _LinearizeHarness(self.pdf_path)
        harness.sync_file_error = OSError(errno.EIO, "I/O error")

        with self.assertLogs("prks.pdf", level=logging.WARNING) as logs:
            changed, reason = self.run_linearize(harness)

        self.assertEqual((changed, reason), (False, "sync-failed"))
        self.assertEqual(harness.steps, [], "nothing was renamed")
        self.assertEqual(
            self.canonical_bytes(), ORIGINAL,
            "a durability failure must leave the known-good PDF in place",
        )
        self.assertEqual(self.temp_leftovers(), [], "the unusable temp was removed")
        text = "\n".join(logs.output)
        self.assertIn("pdf_linearize_sync_failed", text)
        self.assertIn("context=work-pdf-overwrite", text)
        self.assertIn("error_type=OSError", text)
        self.assertNotIn(self.pdf_path, text)
        self.assertNotIn("managed.pdf", text)

    def test_a_failed_directory_sync_is_reported_rather_than_claimed_ok(self):
        """The rename already happened and cannot be unwound, so the helper
        reports the weaker guarantee instead of answering plain ``ok``."""
        harness = _LinearizeHarness(self.pdf_path)
        harness.dir_sync_ok = False

        with self.assertLogs("prks.pdf", level=logging.WARNING) as logs:
            changed, reason = self.run_linearize(harness)

        self.assertEqual((changed, reason), (True, "ok-unsynced-dir"))
        self.assertNotEqual(reason, "ok", "durability that was not achieved is not ok")
        self.assertEqual(harness.steps, ["fsync-file", "replace", "fsync-dir"])
        self.assertEqual(self.canonical_bytes(), LINEARIZED)
        text = "\n".join(logs.output)
        self.assertIn("pdf_linearize_dir_sync_failed", text)
        self.assertIn("context=work-pdf-overwrite", text)
        self.assertNotIn(self.pdf_path, text)

    def test_qpdf_failure_still_leaves_the_canonical_pdf_and_no_temp(self):
        harness = _LinearizeHarness(self.pdf_path)

        with self.assertLogs("prks.pdf", level=logging.WARNING) as logs:
            changed, reason = self.run_linearize(
                harness, qpdf=_fake_qpdf(returncode=3)
            )

        self.assertEqual((changed, reason), (False, "qpdf-failed"))
        self.assertEqual(harness.steps, [], "a failed qpdf syncs and renames nothing")
        self.assertEqual(self.canonical_bytes(), ORIGINAL)
        self.assertEqual(self.temp_leftovers(), [])
        self.assertIn("pdf_linearize_failed", "\n".join(logs.output))

    def test_a_replace_failure_is_still_reported_as_error(self):
        """Existing failure semantics survive the added syncs."""
        harness = _LinearizeHarness(self.pdf_path)

        def boom(src, dst):
            harness.events.append(("replace", src, dst))
            raise OSError(errno.EXDEV, "Invalid cross-device link")

        harness.replace = boom

        with self.assertLogs("prks.pdf", level=logging.WARNING) as logs:
            changed, reason = self.run_linearize(harness)

        self.assertEqual((changed, reason), (False, "error"))
        self.assertEqual(harness.steps, ["fsync-file", "replace"])
        self.assertEqual(self.canonical_bytes(), ORIGINAL)
        self.assertEqual(self.temp_leftovers(), [])
        self.assertIn("pdf_linearize_error", "\n".join(logs.output))


class TestLinearizeWithoutQpdf(_LinearizeCase):
    """Disabled/missing qpdf must stay a silent no-op, not a durability path."""

    def test_disabled_linearization_touches_nothing(self):
        with patch.object(pdf_linearize, "_linearize_enabled", return_value=False):
            with patch.object(fs_durability, "fsync_directory") as dir_sync:
                changed, reason = maybe_linearize_pdf_in_place(
                    self.pdf_path, context="settings-bulk"
                )
        self.assertEqual((changed, reason), (False, "disabled"))
        dir_sync.assert_not_called()
        self.assertEqual(self.canonical_bytes(), ORIGINAL)

    def test_a_missing_file_is_reported_before_any_sync(self):
        missing = os.path.join(self.pdfs_dir, "gone.pdf")
        with patch.object(pdf_linearize, "_linearize_enabled", return_value=True):
            with patch.object(pdf_linearize.shutil, "which", return_value="/usr/bin/qpdf"):
                with patch.object(pdf_linearize, "fsync_file_path") as file_sync:
                    changed, reason = maybe_linearize_pdf_in_place(
                        missing, context="settings-bulk"
                    )
        self.assertEqual((changed, reason), (False, "missing-file"))
        file_sync.assert_not_called()


class TestUploadPathInheritsHelperDurability(unittest.TestCase):
    """A caller that never mentions fsync still gets the full ordering.

    The new-upload path is the one the finding called out as uncompensated: it
    fsynced the bytes it wrote itself and then let linearization rename over
    them. It adds nothing now, and is durable because the helper is.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="prks-upload-durability-")
        self.addCleanup(self._tmp.cleanup)
        self.pdfs_dir = self._tmp.name

    def test_a_new_managed_upload_is_linearized_durably(self):
        harness = _LinearizeHarness(self.pdfs_dir)

        with patch.object(pdf_linearize, "_linearize_enabled", return_value=True), \
                patch.object(pdf_linearize.shutil, "which", return_value="/usr/bin/qpdf"), \
                patch.object(pdf_linearize.subprocess, "run", _fake_qpdf()), \
                patch.object(pdf_linearize, "fsync_file_path", harness.fsync_file_path), \
                patch.object(pdf_linearize, "fsync_directory", harness.fsync_directory), \
                patch.object(pdf_linearize.os, "replace", harness.replace):
            name = work_pdf_replace.store_new_managed_pdf_bytes(
                self.pdfs_dir, "paper.pdf", ORIGINAL
            )

        self.assertEqual(harness.steps, ["fsync-file", "replace", "fsync-dir"])
        stored = os.path.join(os.path.realpath(self.pdfs_dir), name)
        self.assertEqual(harness.events[1][2], stored)
        with open(stored, "rb") as fh:
            self.assertEqual(fh.read(), LINEARIZED)


class TestNoCallerCompensatesForLinearization(unittest.TestCase):
    """The drift this finding recorded must not come back.

    One caller used to re-fsync the managed parent directory after
    linearization while the others did not, which is what made the missing
    guarantee visible. The boundary belongs to the helper, so a second fsync
    beside a call site means the contract has split again.
    """

    BACKEND = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"
    )

    def test_no_production_call_site_re_syncs_after_linearizing(self):
        checked = 0
        for dirpath, _dirnames, filenames in os.walk(self.BACKEND):
            for filename in sorted(filenames):
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(dirpath, filename)
                with open(path, encoding="utf-8") as fh:
                    lines = fh.read().splitlines()
                for index, line in enumerate(lines):
                    if "maybe_linearize_pdf_in_place(" not in line:
                        continue
                    if line.lstrip().startswith(("#", "from ", "import ")):
                        continue
                    checked += 1
                    window = "\n".join(lines[index:index + 8])
                    self.assertNotIn(
                        "fsync_managed_pdf_parent(", window,
                        "%s:%d compensates for a boundary the linearization "
                        "helper already owns" % (filename, index + 1),
                    )
        self.assertGreaterEqual(checked, 3, "production call sites were not found")


class TestFsDurabilityPrimitives(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="prks-fs-durability-")
        self.addCleanup(self._tmp.cleanup)
        self.dir_path = self._tmp.name

    def test_fsync_file_path_syncs_a_file_written_by_another_process(self):
        path = os.path.join(self.dir_path, "written.bin")
        with open(path, "wb") as fh:
            fh.write(b"bytes")
        with patch.object(fs_durability.os, "fsync") as fsync:
            fs_durability.fsync_file_path(path)
        fsync.assert_called_once()

    def test_fsync_file_path_raises_when_contents_cannot_be_made_durable(self):
        path = os.path.join(self.dir_path, "written.bin")
        with open(path, "wb") as fh:
            fh.write(b"bytes")
        with patch.object(
            fs_durability.os, "fsync", side_effect=OSError(errno.EIO, "I/O error")
        ):
            with self.assertRaises(OSError):
                fs_durability.fsync_file_path(path)

    def test_fsync_file_path_raises_for_a_file_that_is_not_there(self):
        with self.assertRaises(OSError):
            fs_durability.fsync_file_path(os.path.join(self.dir_path, "absent.bin"))

    def test_fsync_directory_reports_success_on_a_real_directory(self):
        self.assertTrue(fs_durability.fsync_directory(self.dir_path))

    def test_fsync_directory_treats_an_unsupported_fsync_as_durable(self):
        """Some filesystems refuse a directory fsync. There is nothing stronger
        to ask for there, so that is not a durability failure."""
        for code in (errno.EINVAL, errno.ENOSYS, errno.EPERM, errno.EOPNOTSUPP):
            with self.subTest(errno=code):
                with patch.object(
                    fs_durability.os, "fsync", side_effect=OSError(code, "nope")
                ):
                    self.assertTrue(fs_durability.fsync_directory(self.dir_path))

    def test_fsync_directory_reports_a_genuine_io_failure(self):
        with patch.object(
            fs_durability.os, "fsync", side_effect=OSError(errno.EIO, "I/O error")
        ):
            self.assertFalse(fs_durability.fsync_directory(self.dir_path))

    def test_fsync_directory_never_raises_for_a_directory_that_is_gone(self):
        self.assertFalse(
            fs_durability.fsync_directory(os.path.join(self.dir_path, "absent"))
        )

    def test_fsync_directory_is_durable_where_the_platform_has_no_directory_handle(self):
        """Windows has no directory handle to flush; the rename is as durable as
        the platform offers, so the caller is not told durability was lost."""
        with patch.object(fs_durability.os, "name", "nt"):
            with patch.object(fs_durability.os, "open") as opener:
                self.assertTrue(fs_durability.fsync_directory(self.dir_path))
            opener.assert_not_called()

    def test_fsync_directory_closes_the_descriptor_it_opened(self):
        opened = []
        real_open = fs_durability.os.open
        real_close = fs_durability.os.close
        closed = []

        def tracking_open(path, flags, *a, **kw):
            fd = real_open(path, flags, *a, **kw)
            opened.append(fd)
            return fd

        with patch.object(fs_durability.os, "open", tracking_open):
            with patch.object(
                fs_durability.os, "close", lambda fd: (closed.append(fd), real_close(fd))[1]
            ):
                fs_durability.fsync_directory(self.dir_path)
        self.assertEqual(opened, closed, "the directory descriptor leaked")


class TestManagedPdfParentSyncUsesTheSharedConvention(unittest.TestCase):
    """The managed-PDF helper keeps its containment check and delegates the
    syscall convention, so linearization and byte replacement cannot drift."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="prks-managed-parent-")
        self.addCleanup(self._tmp.cleanup)
        self.pdfs_dir = self._tmp.name

    def test_it_syncs_the_trusted_storage_root(self):
        with patch.object(
            work_pdf_replace, "fsync_directory", return_value=True
        ) as dir_sync:
            self.assertTrue(
                work_pdf_replace.fsync_managed_pdf_parent(self.pdfs_dir, "managed.pdf")
            )
        dir_sync.assert_called_once_with(os.path.realpath(self.pdfs_dir))

    def test_an_unsafe_name_syncs_nothing_and_reports_no_durability(self):
        with patch.object(work_pdf_replace, "fsync_directory") as dir_sync:
            self.assertFalse(
                work_pdf_replace.fsync_managed_pdf_parent(self.pdfs_dir, "../escape.pdf")
            )
        dir_sync.assert_not_called()

    def test_it_forwards_a_failed_directory_sync(self):
        with patch.object(work_pdf_replace, "fsync_directory", return_value=False):
            self.assertFalse(
                work_pdf_replace.fsync_managed_pdf_parent(self.pdfs_dir, "managed.pdf")
            )


if __name__ == "__main__":
    unittest.main()
