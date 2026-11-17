"""Linearization may decline, never damage, and the durability primitives it relied on.

EF-018 / #112 made the old in-place helper (``maybe_linearize_pdf_in_place``)
own the durability of the canonical managed PDF it rewrote. PostgreSQL stores
PDFs as immutable objects, so PG-C2 (#641) deleted that helper: the ingest now
calls ``linearize_pdf_to()``, which writes a linearized *copy* to scratch and
never touches its source; the caller stores the copy under a new key through
its ``StorageBackend``, which makes it durable. These tests pin that the source
is left untouched whatever qpdf does, the reasons it reports, and that the logs
never carry a path. The ``backend.fs_durability`` primitives stay below.

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
from backend.pdf_linearize import linearize_pdf_to


ORIGINAL = b"%PDF-1.4\n% original managed bytes\n%%EOF\n"
LINEARIZED = b"%PDF-1.4\n%\x80\x80\x80\x80\n/Linearized 1\n% qpdf output\n%%EOF\n"


def _fake_qpdf(output: bytes = LINEARIZED, returncode: int = 0, calls=None):
    """A ``subprocess.run`` stand-in that writes qpdf's output file."""

    def run(argv, **kwargs):
        if calls is not None:
            calls.append(list(argv))
        if returncode == 0:
            with open(argv[3], "wb") as fh:
                fh.write(output)
        return SimpleNamespace(returncode=returncode, stdout="", stderr="qpdf noise")

    return run


class _LinearizeCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="prks-linearize-")
        self.addCleanup(self._tmp.cleanup)
        self.work_dir = self._tmp.name
        self.pdf_path = os.path.join(self.work_dir, "source.pdf")
        self.out_path = os.path.join(self.work_dir, "scratch-linearized.pdf")
        with open(self.pdf_path, "wb") as fh:
            fh.write(ORIGINAL)

    def source_bytes(self):
        with open(self.pdf_path, "rb") as fh:
            return fh.read()

    def run_linearize(self, *, qpdf=None, context="processing-ingest"):
        with patch.object(pdf_linearize, "_linearize_enabled", return_value=True), \
                patch.object(pdf_linearize.shutil, "which", return_value="/usr/bin/qpdf"), \
                patch.object(pdf_linearize.subprocess, "run", qpdf or _fake_qpdf()):
            return linearize_pdf_to(self.pdf_path, self.out_path, context=context)


class TestLinearizeToACopy(_LinearizeCase):
    def test_the_copy_is_written_and_the_source_is_left_untouched(self):
        calls = []
        written, reason = self.run_linearize(qpdf=_fake_qpdf(calls=calls))

        self.assertEqual((written, reason), (True, "ok"))
        self.assertEqual(calls, [["/usr/bin/qpdf", "--linearize", self.pdf_path, self.out_path]])
        self.assertEqual(self.source_bytes(), ORIGINAL)
        with open(self.out_path, "rb") as fh:
            self.assertEqual(fh.read(), LINEARIZED)

    def test_a_failed_qpdf_leaves_the_source_and_reports_qpdf_failed(self):
        with self.assertLogs("prks.pdf", level=logging.WARNING) as logs:
            written, reason = self.run_linearize(qpdf=_fake_qpdf(returncode=3))

        self.assertEqual((written, reason), (False, "qpdf-failed"))
        self.assertEqual(self.source_bytes(), ORIGINAL)
        text = "\n".join(logs.output)
        self.assertIn("pdf_linearize_failed", text)
        self.assertIn("context=processing-ingest", text)
        self.assertIn("exit_code=3", text)
        self.assertNotIn(self.pdf_path, text)
        self.assertNotIn("source.pdf", text)

    def test_a_qpdf_that_cannot_run_is_reported_as_error_without_a_path(self):
        def boom(argv, **kwargs):
            raise OSError(errno.ENOEXEC, "Exec format error", argv[0])

        with self.assertLogs("prks.pdf", level=logging.WARNING) as logs:
            written, reason = self.run_linearize(qpdf=boom)

        self.assertEqual((written, reason), (False, "error"))
        self.assertEqual(self.source_bytes(), ORIGINAL)
        text = "\n".join(logs.output)
        self.assertIn("pdf_linearize_error", text)
        self.assertIn("error_type=OSError", text)
        self.assertNotIn(self.pdf_path, text)
        self.assertNotIn("/usr/bin/qpdf", text)


class TestLinearizeWithoutQpdf(_LinearizeCase):
    """Disabled/missing qpdf must stay a silent no-op."""

    def test_disabled_linearization_touches_nothing(self):
        with patch.object(pdf_linearize, "_linearize_enabled", return_value=False), \
                patch.object(pdf_linearize.subprocess, "run") as run:
            written, reason = linearize_pdf_to(
                self.pdf_path, self.out_path, context="processing-ingest"
            )
        self.assertEqual((written, reason), (False, "disabled"))
        run.assert_not_called()
        self.assertEqual(self.source_bytes(), ORIGINAL)
        self.assertFalse(os.path.exists(self.out_path))

    def test_a_missing_file_is_reported_before_qpdf_runs(self):
        missing = os.path.join(self.work_dir, "gone.pdf")
        with patch.object(pdf_linearize, "_linearize_enabled", return_value=True), \
                patch.object(pdf_linearize.shutil, "which", return_value="/usr/bin/qpdf"), \
                patch.object(pdf_linearize.subprocess, "run") as run:
            written, reason = linearize_pdf_to(missing, self.out_path, context="processing-ingest")
        self.assertEqual((written, reason), (False, "missing-file"))
        run.assert_not_called()
        self.assertFalse(os.path.exists(self.out_path))

    def test_a_missing_qpdf_is_reported_once_and_runs_nothing(self):
        with patch.object(pdf_linearize, "_linearize_enabled", return_value=True), \
                patch.object(pdf_linearize.shutil, "which", return_value=None), \
                patch.object(pdf_linearize, "_MISSING_QPDF_WARNED", False), \
                patch.object(pdf_linearize.subprocess, "run") as run:
            with self.assertLogs("prks.pdf", level=logging.WARNING) as logs:
                first = linearize_pdf_to(self.pdf_path, self.out_path, context="processing-ingest")
                second = linearize_pdf_to(self.pdf_path, self.out_path, context="processing-ingest")
                pdf_linearize.LOGGER.warning("sentinel")
        self.assertEqual(first, (False, "missing-qpdf"))
        self.assertEqual(second, (False, "missing-qpdf"))
        run.assert_not_called()
        warned = [line for line in logs.output if "pdf_linearize_skip_missing_qpdf" in line]
        self.assertEqual(len(warned), 1)
        self.assertEqual(self.source_bytes(), ORIGINAL)


class TestFsDurabilityPrimitives(unittest.TestCase):
    """The platforms where `os.fsync()` is itself the barrier.

    These drive behaviour by patching `os.fsync`, so they must pin
    `_FULLFSYNC` off: on macOS `_sync_descriptor()` returns after the full
    barrier and never reaches `os.fsync`, which would leave the injected
    errors unreached and the assertions failing there while staying green on
    Linux. `TestTheStrongestBarrierIsUsed` owns the other platform.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="prks-fs-durability-")
        self.addCleanup(self._tmp.cleanup)
        self.dir_path = self._tmp.name
        no_barrier = patch.object(fs_durability, "_FULLFSYNC", None)
        no_barrier.start()
        self.addCleanup(no_barrier.stop)

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
        """Some filesystems cannot synchronize a directory descriptor at all.
        There is nothing stronger to ask for there, so that is not a failure."""
        for code in (errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP):
            with self.subTest(errno=code):
                with patch.object(
                    fs_durability.os, "fsync", side_effect=OSError(code, "nope")
                ):
                    self.assertTrue(fs_durability.fsync_directory(self.dir_path))

    def test_a_denied_directory_sync_is_a_failure_not_an_unsupported_platform(self):
        """EPERM means the sync was refused, not that it does not exist. A
        refusal that did not happen must never be reported as durable."""
        for code in (errno.EPERM, errno.EACCES):
            with self.subTest(errno=code):
                with patch.object(
                    fs_durability.os, "fsync", side_effect=OSError(code, "denied")
                ):
                    self.assertFalse(fs_durability.fsync_directory(self.dir_path))

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


class TestTheStrongestBarrierIsUsed(unittest.TestCase):
    """`os.fsync()` is not a durability barrier on macOS -- it returns once the
    write reaches the drive, without waiting for the drive's own cache. These
    drive the `F_FULLFSYNC` branch on any host, the way the suite exercises the
    Windows path everywhere, so a Linux-only CI still covers it."""

    FAKE_FULLFSYNC = 51  # F_FULLFSYNC's value on Darwin.

    def setUp(self):
        if fs_durability.fcntl is None:  # pragma: no cover - Windows only
            self.skipTest("platform has no fcntl")
        self._tmp = tempfile.TemporaryDirectory(prefix="prks-full-barrier-")
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "written.bin")
        with open(self.path, "wb") as fh:
            fh.write(b"bytes")

    def test_the_full_barrier_is_preferred_where_the_platform_has_one(self):
        calls = []
        with patch.object(fs_durability, "_FULLFSYNC", self.FAKE_FULLFSYNC):
            with patch.object(
                fs_durability.fcntl, "fcntl",
                side_effect=lambda fd, op, *a: calls.append((fd, op)) or 0,
            ):
                with patch.object(fs_durability.os, "fsync") as plain:
                    fs_durability.fsync_file_path(self.path)
        self.assertEqual([op for _fd, op in calls], [self.FAKE_FULLFSYNC])
        plain.assert_not_called()

    def test_a_filesystem_without_the_full_barrier_falls_back_to_fsync(self):
        """Network mounts and disk images do not implement it. Falling back is
        the best that filesystem offers, not a silent downgrade elsewhere."""
        for code in (errno.ENOTSUP, errno.EINVAL, errno.ENOTTY):
            with self.subTest(errno=code):
                with patch.object(fs_durability, "_FULLFSYNC", self.FAKE_FULLFSYNC):
                    with patch.object(
                        fs_durability.fcntl, "fcntl",
                        side_effect=OSError(code, "unimplemented"),
                    ):
                        with patch.object(fs_durability.os, "fsync") as plain:
                            fs_durability.fsync_file_path(self.path)
                plain.assert_called_once()

    def test_a_failing_full_barrier_is_not_hidden_by_the_fallback(self):
        """Falling back on a genuine I/O error would turn a lost write into a
        durable-looking one, which is the bug this module exists to prevent."""
        with patch.object(fs_durability, "_FULLFSYNC", self.FAKE_FULLFSYNC):
            with patch.object(
                fs_durability.fcntl, "fcntl",
                side_effect=OSError(errno.EIO, "I/O error"),
            ):
                with patch.object(fs_durability.os, "fsync") as plain:
                    with self.assertRaises(OSError):
                        fs_durability.fsync_file_path(self.path)
        plain.assert_not_called()

    def test_the_directory_sync_reaches_for_it_too(self):
        calls = []
        with patch.object(fs_durability, "_FULLFSYNC", self.FAKE_FULLFSYNC):
            with patch.object(
                fs_durability.fcntl, "fcntl",
                side_effect=lambda fd, op, *a: calls.append((fd, op)) or 0,
            ):
                self.assertTrue(fs_durability.fsync_directory(self._tmp.name))
        self.assertEqual([op for _fd, op in calls], [self.FAKE_FULLFSYNC])


if __name__ == "__main__":
    unittest.main()
