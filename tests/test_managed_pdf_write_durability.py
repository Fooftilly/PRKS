"""Managed-PDF bytes must be published under the platform's strongest barrier.

EF-031 / #134. #129 gave PRKS one durability convention and #133 extended it to
files this process still holds open, but the two paths that publish *canonical*
managed-PDF bytes were only half converted: their directory sync went through
``fs_durability`` while their content sync stayed a bare ``os.fsync``. On macOS
that is not a durability barrier -- it returns once the write reaches the drive,
without waiting for the drive's own cache -- so a successful save or upload could
name bytes that never reached stable storage. Linearization, which is optional
(``PRKS_PDF_LINEARIZE=0``, no ``qpdf``, or a rewrite it declines), was the only
step that applied the strong barrier, and an optimization must never be what
makes the first write durable.

These tests pin the *ordering* and the failure semantics of the content sync --
synced strictly before the bytes acquire a durable name, fail-closed when the
sync is refused -- and, with ``_FULLFSYNC`` forced the way
``tests/test_pdf_linearize_durability.py`` forces it, that the barrier these
paths reach for really is the strong one on a host that has it.

File-content durability and directory-entry durability stay distinct here, as
``backend/fs_durability.py`` defines them: content raises and is fatal, the
directory entry is best-effort and is reported rather than unwound.
"""

import errno
import logging
import os
import tempfile
import unittest
from unittest.mock import patch

from backend import fs_durability
from backend.services import work_pdf_replace


ORIGINAL = b"%PDF-1.4\n% previous canonical bytes\n%%EOF\n"
REPLACEMENT = b"%PDF-1.4\n% freshly materialized bytes\n%%EOF\n"
UPLOADED = b"%PDF-1.4\n% newly uploaded bytes\n%%EOF\n"

FAKE_FULLFSYNC = 51  # F_FULLFSYNC's value on Darwin.


class _WriteHarness:
    """Records the durability-relevant calls a managed-PDF write makes, in order.

    ``fsync_open_file`` also records the size the descriptor it was handed
    already had, so the assertions can prove the sync ran on the finished body
    rather than on an empty or half-written file. Size, not contents: the
    descriptor is write-only and shares its offset, so reading it back would
    say nothing.
    """

    def __init__(self):
        self._real_replace = os.replace
        self.events = []
        self.synced_sizes = []
        self.synced_fds = []
        self.sync_file_error = None
        self.dir_sync_ok = True

    def fsync_open_file(self, fd):
        if self.sync_file_error is not None:
            raise self.sync_file_error
        self.synced_fds.append(fd)
        self.synced_sizes.append(os.fstat(fd).st_size)
        self.events.append(("fsync-file", fd))
        fs_durability.fsync_open_file(fd)

    def fsync_directory(self, path):
        self.events.append(("fsync-dir", path))
        if not self.dir_sync_ok:
            return False
        return fs_durability.fsync_directory(path)

    def replace(self, src, dst):
        self.events.append(("replace", src, dst))
        return self._real_replace(src, dst)

    def linearize(self, path, *, context):
        self.events.append(("linearize", path))
        return False, "disabled"

    @property
    def steps(self):
        return [event[0] for event in self.events]


class _ManagedWriteCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="prks-managed-write-durability-")
        self.addCleanup(self._tmp.cleanup)
        self.pdfs_dir = self._tmp.name

    def temp_leftovers(self):
        return [n for n in os.listdir(self.pdfs_dir) if n.startswith(".prks-write-")]

    def stored_pdfs(self):
        return sorted(n for n in os.listdir(self.pdfs_dir) if n.endswith(".pdf"))

    def patched(self, harness):
        """Patch the names ``work_pdf_replace`` resolves at call time.

        The module imports ``fsync_open_file`` and ``fsync_directory`` by name,
        so the patches have to land on its globals; patching ``fs_durability``
        would leave the bound symbols in place and these assertions
        unfalsifiable.
        """
        return (
            patch.object(work_pdf_replace, "fsync_open_file", harness.fsync_open_file),
            patch.object(work_pdf_replace, "fsync_directory", harness.fsync_directory),
            patch.object(work_pdf_replace.os, "replace", harness.replace),
            patch.object(
                work_pdf_replace, "maybe_linearize_pdf_in_place", harness.linearize
            ),
        )

    def run_patched(self, harness, call):
        patches = self.patched(harness)
        for p in patches:
            p.start()
        try:
            return call()
        finally:
            for p in reversed(patches):
                p.stop()


class TestAtomicReplaceContentDurability(_ManagedWriteCase):
    """The temp has to be durable before ``os.replace`` gives it the live name."""

    def setUp(self):
        super().setUp()
        self.filename = "managed.pdf"
        self.pdf_path = os.path.join(os.path.realpath(self.pdfs_dir), self.filename)
        with open(self.pdf_path, "wb") as fh:
            fh.write(ORIGINAL)

    def canonical_bytes(self):
        with open(self.pdf_path, "rb") as fh:
            return fh.read()

    def test_the_temp_is_synced_through_the_shared_primitive_before_it_is_published(self):
        harness = _WriteHarness()

        written = self.run_patched(
            harness,
            lambda: work_pdf_replace.atomic_replace_managed_pdf_bytes(
                self.pdfs_dir, self.filename, REPLACEMENT
            ),
        )

        self.assertEqual(written, self.pdf_path)
        self.assertEqual(
            harness.steps,
            ["fsync-file", "replace", "fsync-dir"],
            "the bytes must be durable before a durable name points at them, "
            "and the directory entry durable only after the rename created it",
        )
        self.assertEqual(
            harness.synced_sizes, [len(REPLACEMENT)],
            "the whole body had reached the kernel before the sync, so the "
            "sync covers the bytes the rename publishes",
        )
        _sync, replace, sync_dir = harness.events
        self.assertEqual(replace[2], self.pdf_path)
        self.assertEqual(
            sync_dir[1], os.path.realpath(self.pdfs_dir),
            "the directory synced is the trusted storage root, never a dirname "
            "of a DB-derived path",
        )
        self.assertEqual(self.canonical_bytes(), REPLACEMENT)
        self.assertEqual(self.temp_leftovers(), [])

    def test_linearization_is_not_what_makes_the_replacement_durable(self):
        """Linearization declines here, and the write is durable anyway."""
        harness = _WriteHarness()

        self.run_patched(
            harness,
            lambda: work_pdf_replace.atomic_replace_managed_pdf_bytes(
                self.pdfs_dir, self.filename, REPLACEMENT
            ),
        )

        self.assertNotIn(
            "linearize", harness.steps,
            "the replace helper must not reach for the optional optimization",
        )
        self.assertEqual(len(harness.synced_fds), 1, "the write synced itself")

    def test_a_refused_content_sync_never_replaces_the_canonical_pdf(self):
        """Fail closed: bytes that are not durable must not acquire the name."""
        harness = _WriteHarness()
        harness.sync_file_error = OSError(errno.EIO, "I/O error")

        with self.assertRaises(OSError):
            self.run_patched(
                harness,
                lambda: work_pdf_replace.atomic_replace_managed_pdf_bytes(
                    self.pdfs_dir, self.filename, REPLACEMENT
                ),
            )

        self.assertEqual(harness.steps, [], "nothing was renamed and nothing synced")
        self.assertEqual(
            self.canonical_bytes(), ORIGINAL,
            "a durability failure must leave the known-good PDF in place",
        )
        self.assertEqual(self.temp_leftovers(), [], "the unusable temp was removed")

    def test_a_failed_directory_sync_is_reported_and_not_raised(self):
        """The distinction the durability module draws, kept at this call site.

        The rename has already happened by then, so there is nothing to unwind:
        the weaker guarantee is recorded rather than turned into a failure that
        would discard a durable write.
        """
        harness = _WriteHarness()
        harness.dir_sync_ok = False

        with self.assertLogs("prks", level=logging.WARNING) as logs:
            self.run_patched(
                harness,
                lambda: work_pdf_replace.atomic_replace_managed_pdf_bytes(
                    self.pdfs_dir, self.filename, REPLACEMENT
                ),
            )

        self.assertEqual(harness.steps, ["fsync-file", "replace", "fsync-dir"])
        self.assertEqual(self.canonical_bytes(), REPLACEMENT)
        text = "\n".join(logs.output)
        self.assertIn("pdf_replace_dir_sync_failed", text)
        self.assertNotIn(self.filename, text, "log lines stay metadata-only")


class TestNewManagedStoreContentDurability(_ManagedWriteCase):
    """An exclusive create is canonical the moment the store returns its name."""

    def test_the_created_file_is_synced_before_the_store_reports_success(self):
        harness = _WriteHarness()

        name = self.run_patched(
            harness,
            lambda: work_pdf_replace.store_new_managed_pdf_bytes(
                self.pdfs_dir, "paper.pdf", UPLOADED
            ),
        )

        self.assertEqual(
            harness.steps,
            ["fsync-file", "fsync-dir", "linearize"],
            "contents durable first, then the directory entry, and only then "
            "the optional optimization",
        )
        self.assertEqual(
            harness.synced_sizes, [len(UPLOADED)],
            "the whole body had reached the kernel before the sync, so the "
            "sync covers the bytes the returned name points at",
        )
        stored = os.path.join(os.path.realpath(self.pdfs_dir), name)
        self.assertEqual(harness.events[2][1], stored)
        with open(stored, "rb") as fh:
            self.assertEqual(fh.read(), UPLOADED)

    def test_a_refused_content_sync_reports_no_stored_pdf(self):
        """A name handed back is a name a Work will reference. Fail closed."""
        harness = _WriteHarness()
        harness.sync_file_error = OSError(errno.ENOSPC, "No space left on device")

        with self.assertLogs("prks", level=logging.ERROR) as logs:
            with self.assertRaises(work_pdf_replace.ManagedPdfStoreError) as raised:
                self.run_patched(
                    harness,
                    lambda: work_pdf_replace.store_new_managed_pdf_bytes(
                        self.pdfs_dir, "paper.pdf", UPLOADED
                    ),
                )

        self.assertEqual(raised.exception.reason, "write_failed")
        self.assertEqual(raised.exception.http_status, 500)
        self.assertEqual(harness.steps, [], "nothing was synced and nothing linearized")
        self.assertEqual(
            self.stored_pdfs(), [],
            "a PDF no Work can reference must not be left behind, least of all "
            "when the likeliest cause is a full disk",
        )
        text = "\n".join(logs.output)
        self.assertIn("pdf_upload_write_failed", text)
        self.assertIn("error_type=OSError", text)
        self.assertNotIn("paper.pdf", text, "log lines stay metadata-only")

    def test_a_failed_directory_sync_still_stores_the_upload(self):
        """Same distinction as the replace path: the bytes are durable, only the
        entry is weaker, so the upload is kept and the weakness recorded."""
        harness = _WriteHarness()
        harness.dir_sync_ok = False

        with self.assertLogs("prks", level=logging.WARNING) as logs:
            name = self.run_patched(
                harness,
                lambda: work_pdf_replace.store_new_managed_pdf_bytes(
                    self.pdfs_dir, "paper.pdf", UPLOADED
                ),
            )

        self.assertEqual(self.stored_pdfs(), [name])
        self.assertIn("pdf_upload_dir_sync_failed", "\n".join(logs.output))


class TestTheStrongestBarrierReachesManagedPdfWrites(_ManagedWriteCase):
    """The point of the finding: which syscall the content sync ends up making.

    ``_FULLFSYNC`` is forced on so the macOS branch is exercised on any host,
    the way ``tests/test_pdf_linearize_durability.py`` already drives it, and
    then forced off so the platforms where ``os.fsync()`` *is* the barrier stay
    covered too.
    """

    def setUp(self):
        super().setUp()
        if fs_durability.fcntl is None:  # pragma: no cover - Windows only
            self.skipTest("platform has no fcntl")
        self.filename = "managed.pdf"
        self.pdf_path = os.path.join(os.path.realpath(self.pdfs_dir), self.filename)
        with open(self.pdf_path, "wb") as fh:
            fh.write(ORIGINAL)

    def _content_fds_and_barriers(self, call):
        """Run ``call`` recording the content fds and every barrier issued."""
        content_fds = []
        real_sync = fs_durability.fsync_open_file

        def spy(fd):
            content_fds.append(fd)
            return real_sync(fd)

        barriers = []
        with patch.object(work_pdf_replace, "fsync_open_file", spy), \
                patch.object(
                    work_pdf_replace,
                    "maybe_linearize_pdf_in_place",
                    lambda path, *, context: (False, "disabled"),
                ), \
                patch.object(
                    fs_durability.fcntl, "fcntl",
                    side_effect=lambda fd, op, *a: barriers.append((fd, op)) or 0,
                ), \
                patch.object(fs_durability.os, "fsync") as plain:
            call()
        return content_fds, barriers, plain

    def test_a_managed_replacement_flushes_its_contents_with_the_full_barrier(self):
        with patch.object(fs_durability, "_FULLFSYNC", FAKE_FULLFSYNC):
            content_fds, barriers, plain = self._content_fds_and_barriers(
                lambda: work_pdf_replace.atomic_replace_managed_pdf_bytes(
                    self.pdfs_dir, self.filename, REPLACEMENT
                )
            )

        self.assertEqual(len(content_fds), 1, "exactly one content sync")
        self.assertIn(
            (content_fds[0], FAKE_FULLFSYNC), barriers,
            "the temp that becomes the canonical PDF got the full barrier",
        )
        plain.assert_not_called()

    def test_a_new_managed_store_flushes_its_contents_with_the_full_barrier(self):
        with patch.object(fs_durability, "_FULLFSYNC", FAKE_FULLFSYNC):
            content_fds, barriers, plain = self._content_fds_and_barriers(
                lambda: work_pdf_replace.store_new_managed_pdf_bytes(
                    self.pdfs_dir, "paper.pdf", UPLOADED
                )
            )

        self.assertEqual(len(content_fds), 1, "exactly one content sync")
        self.assertIn((content_fds[0], FAKE_FULLFSYNC), barriers)
        plain.assert_not_called()

    def _run_without_a_full_barrier(self, call):
        """Run ``call`` with ``_FULLFSYNC`` off, recording both sync layers."""
        content_fds = []
        synced = []
        real_sync = fs_durability.fsync_open_file
        real_fsync = os.fsync

        def spy(fd):
            content_fds.append(fd)
            return real_sync(fd)

        def plain(fd):
            synced.append(fd)
            return real_fsync(fd)

        with patch.object(fs_durability, "_FULLFSYNC", None), \
                patch.object(work_pdf_replace, "fsync_open_file", spy), \
                patch.object(
                    work_pdf_replace,
                    "maybe_linearize_pdf_in_place",
                    lambda path, *, context: (False, "disabled"),
                ), \
                patch.object(fs_durability.os, "fsync", plain):
            call()
        return content_fds, synced

    def test_without_a_full_barrier_the_plain_fsync_still_runs(self):
        """Linux, Windows, and macOS filesystems that do not implement it."""
        for label, call in (
            (
                "replace",
                lambda: work_pdf_replace.atomic_replace_managed_pdf_bytes(
                    self.pdfs_dir, self.filename, REPLACEMENT
                ),
            ),
            (
                "store",
                lambda: work_pdf_replace.store_new_managed_pdf_bytes(
                    self.pdfs_dir, "paper.pdf", UPLOADED
                ),
            ),
        ):
            with self.subTest(path=label):
                content_fds, synced = self._run_without_a_full_barrier(call)

                self.assertEqual(len(content_fds), 1)
                self.assertIn(
                    content_fds[0], synced,
                    "the fallback still flushes the bytes it published",
                )


class TestNoManagedPdfWriteFallsBackToABareFsync(unittest.TestCase):
    """The drift this finding recorded must not come back.

    The gap was not a missing sync -- both paths called ``os.fsync`` -- but a
    sync that bypassed the module that knows which barrier the platform needs.
    A bare ``os.fsync`` reappearing beside a managed-PDF write would be exactly
    that regression, and it is invisible to a behavioural test on Linux.
    """

    MODULE = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "backend", "services", "work_pdf_replace.py",
    )

    def test_the_managed_write_module_syncs_only_through_fs_durability(self):
        with open(self.MODULE, encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn(
            "os.fsync(", source,
            "managed-PDF writes must flush through backend.fs_durability, "
            "which knows about macOS F_FULLFSYNC, not call os.fsync directly",
        )
        self.assertIn("from backend.fs_durability import", source)
        self.assertIn("fsync_open_file(", source)

    def test_the_module_does_not_reimplement_the_platform_logic(self):
        """One owner for the barrier choice, or the contract splits again."""
        with open(self.MODULE, encoding="utf-8") as fh:
            source = fh.read()
        for forbidden in ("F_FULLFSYNC", "_sync_descriptor", "import fcntl"):
            self.assertNotIn(
                forbidden, source,
                "%s belongs to backend/fs_durability.py alone" % forbidden,
            )


if __name__ == "__main__":
    unittest.main()
