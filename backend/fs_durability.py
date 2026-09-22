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

A rename whose source and destination sit in different parent directories
changes an entry in each, so (2) means both of them; ``fsync_directories``
takes the whole set a move touched and reports one answer for it.

The two halves fail differently on purpose. File-content durability is a hard
requirement: ``fsync_file_path`` raises, and a caller that has not yet replaced
anything must abandon the replacement rather than present it as durable.
Directory durability is best-effort, because the operation does not exist on
every platform and some filesystems refuse it; ``fsync_directory`` returns
whether the entry is as durable as the platform can make it, so a caller can
report the weaker guarantee instead of assuming the stronger one. It never
raises: by the time it runs the rename has already happened and there is nothing
to unwind.

Both reach for the strongest barrier the platform offers, which on macOS is not
``os.fsync()`` -- see ``_sync_descriptor``.
"""

from __future__ import annotations

import errno
import os

try:
    import fcntl
except ImportError:  # Windows has no fcntl; _sync_descriptor falls back.
    fcntl = None  # type: ignore[assignment]


def _errnos(*names: str) -> frozenset[int]:
    """The subset of ``names`` this platform defines."""
    return frozenset(
        code for code in (getattr(errno, name, None) for name in names) if code is not None
    )


# A directory fsync the platform or filesystem does not implement is not a
# durability failure -- there is nothing stronger to ask for there. Only codes
# that unambiguously mean "this descriptor cannot be synchronized" belong here.
# EPERM does not: a sync denied by a security policy did not happen, and saying
# it did would be the silent weakening this module exists to prevent.
_DIR_FSYNC_UNSUPPORTED = _errnos("EINVAL", "ENOSYS", "ENOTSUP", "EOPNOTSUPP")

# macOS's full barrier is missing on some filesystems -- network mounts and disk
# images are the usual gaps -- and they report that like any other unimplemented
# operation. Falling back to fsync there is the best the filesystem offers;
# falling back on a genuine I/O error would be hiding one.
_FULLFSYNC_UNIMPLEMENTED = _errnos("EINVAL", "ENOSYS", "ENOTSUP", "EOPNOTSUPP", "ENOTTY")

_FULLFSYNC = getattr(fcntl, "F_FULLFSYNC", None) if fcntl is not None else None


def _sync_descriptor(fd: int) -> None:
    """Flush ``fd`` with the strongest barrier this platform offers.

    ``os.fsync()`` is not a durability barrier on macOS: it hands the write to
    the drive and returns without waiting for the drive's own cache to reach the
    platter, which is exactly the window a power loss falls into. ``F_FULLFSYNC``
    is the call that waits, so it is preferred wherever it exists. Everywhere
    else ``os.fsync()`` already is the barrier.

    Raises ``OSError`` if the flush fails. Callers decide whether that is fatal.
    """
    if _FULLFSYNC is not None and fcntl is not None:
        try:
            fcntl.fcntl(fd, _FULLFSYNC)
            return
        except OSError as exc:
            if exc.errno not in _FULLFSYNC_UNIMPLEMENTED:
                raise
    os.fsync(fd)


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
        _sync_descriptor(fd)
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
        _sync_descriptor(dir_fd)
    except OSError as exc:
        return exc.errno in _DIR_FSYNC_UNSUPPORTED
    finally:
        try:
            os.close(dir_fd)
        except OSError:
            pass
    return True


def fsync_open_file(fd: int) -> None:
    """Flush an open file's contents to stable storage.

    The ``fsync_file_path`` guarantee for a file this process is still holding:
    same barrier, without reopening a descriptor that is already in hand. Use it
    for a temporary this process wrote and is about to rename into place.

    Raises ``OSError`` when the contents could not be made durable. Callers must
    not treat the file as replaceable-from after that.
    """
    _sync_descriptor(fd)


def fsync_directories(*paths: str) -> bool:
    """Best-effort fsync of every directory a completed set of renames changed.

    ``os.replace(src, dest)`` changes a directory entry at both ends: the name
    leaves the source's parent and appears in the destination's. One fsync
    covers both ends when the two parents are the same directory, but when they
    differ, syncing only one can come back from a crash with the rename
    half-visible -- the file under both names, or under neither. Repeated paths
    collapse, so a batch of renames sharing two parents costs two syncs.

    Returns True when every directory is as durable as this platform can make
    it, False when a supported fsync was attempted and failed. Every directory
    is attempted either way: a caller that has to report the weaker guarantee
    still wants the syncs that can succeed to have happened.

    Callers pass directories they already trust, exactly as ``fsync_directory``
    requires, and it never raises for the same reason -- the renames it covers
    have already happened.
    """
    durable = True
    for path in dict.fromkeys(os.path.normpath(path) for path in paths):
        if not fsync_directory(path):
            durable = False
    return durable
