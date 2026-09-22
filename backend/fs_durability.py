"""Crash-durability primitives for rename-based file replacement.

PRKS replaces a managed file by writing a sibling temporary and renaming it over
the canonical path. Two syncs make that crash-safe, and only together:

1. fsync the finished temporary **before** ``os.replace()``, so the bytes the new
   directory entry will point at are already on stable storage;
2. fsync the containing directory **after** ``os.replace()``, so the entry itself
   survives a crash.

Skipping (1) can leave the canonical name pointing at contents that were never
flushed -- a replacement that is atomic but not durable. Skipping (2) can lose
the rename entirely and resurrect the previous file.

The two halves fail differently on purpose. File-content durability is a hard
requirement: ``fsync_file_path`` raises, and a caller that has not yet replaced
anything must abandon the replacement rather than present it as durable.
Directory durability is best-effort, because the operation does not exist on
every platform and some filesystems refuse it; ``fsync_directory`` returns
whether the entry is as durable as the platform can make it, so a caller can
report the weaker guarantee instead of assuming the stronger one. It never
raises: by the time it runs the rename has already happened and there is nothing
to unwind.
"""

from __future__ import annotations

import errno
import os


# A directory fsync the platform or filesystem does not implement is not a
# durability failure -- there is nothing stronger to ask for there. A genuine
# I/O error is, and must never be reported as a synced directory.
_DIR_FSYNC_UNSUPPORTED = frozenset(
    code
    for code in (
        getattr(errno, "EINVAL", None),
        getattr(errno, "ENOSYS", None),
        getattr(errno, "ENOTSUP", None),
        getattr(errno, "EOPNOTSUPP", None),
        getattr(errno, "EPERM", None),
    )
    if code is not None
)


def fsync_file_path(path: str) -> None:
    """Flush the completed file at ``path`` to stable storage.

    For a file another process produced -- qpdf's linearized output, say -- where
    the writing handle is gone and only the path is left. Opened read-write
    because Windows' ``_commit`` needs a writable handle; POSIX would accept a
    read-only one.

    Raises ``OSError`` when the contents could not be made durable. Callers must
    not treat the file as replaceable-from after that.
    """
    fd = os.open(path, os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fsync_directory(path: str) -> bool:
    """Best-effort fsync of ``path`` so an entry change inside it is durable.

    Returns True when the directory entry is as durable as this platform can make
    it, including platforms that have no directory-fsync concept at all, and
    False when a supported fsync was attempted and failed.

    Callers pass a directory they already trust -- the one they created the
    temporary in, or a verified storage root -- never a directory derived from
    user or DB input.
    """
    if os.name != "posix":
        # No directory handle to flush on Windows; the rename is already as
        # durable as the platform offers.
        return True
    try:
        dir_fd = os.open(path, os.O_RDONLY)
    except OSError as exc:
        return exc.errno in _DIR_FSYNC_UNSUPPORTED
    try:
        os.fsync(dir_fd)
    except OSError as exc:
        return exc.errno in _DIR_FSYNC_UNSUPPORTED
    finally:
        try:
            os.close(dir_fd)
        except OSError:
            pass
    return True
