"""Exclusive, non-expiring OS advisory lock on one file (storage-architecture §12).

This is the one locking primitive the storage layer uses across processes: the
root lease (``<root>/.prks-maintenance/root.lock``) and the bootstrap
configuration lock (``<config file>.lock``) are both instances of it.

Semantics, on every platform:

- **Exclusive.** At most one holder at a time. A second acquisition fails --
  from another process *and* from another handle in this process, so a single
  process cannot bind one root twice.
- **Non-expiring.** There is no timeout, heartbeat or "stale lock" rule. Only
  the kernel releases the lock: when the handle is closed or the process exits.
  A suspended or slow holder keeps it. Nothing written *into* a file (PID, host,
  start time) is ever consulted to decide who holds a lock; the lock is the
  only authority.
- **No link traversal.** The lock file must be a regular file; a symlink (or a
  Windows reparse point) is refused rather than followed.

POSIX uses ``flock(LOCK_EX | LOCK_NB)``. ``flock`` locks belong to the open file
description, so -- unlike ``fcntl`` record locks -- they conflict between two
opens in one process and are not dropped when some unrelated descriptor for the
same file is closed. After locking, the path is re-checked to still name the
locked inode, so a lock file replaced underneath the opener is retried instead
of trusted.

Windows uses ``msvcrt.locking`` (``LockFile`` over one byte). The lock is per
handle, conflicts within a process as well, and is released when the handle
closes or the process exits. A held file cannot be deleted on Windows, which is
why removing a lock file is reserved for the terminal-teardown procedure of a
later phase and never happens here.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
import time
from typing import Optional


_BUSY_ERRNOS = frozenset(
    code
    for code in (
        getattr(errno, "EWOULDBLOCK", None),
        getattr(errno, "EAGAIN", None),
        getattr(errno, "EACCES", None),
        getattr(errno, "EDEADLK", None),
        getattr(errno, "EDEADLOCK", None),
    )
    if code is not None
)
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
# An identity mismatch after locking means the file was swapped; retrying a
# handful of times covers a racing replacement without looping forever.
_IDENTITY_RETRIES = 8


class LockBusy(Exception):
    """The lock is held by another handle (another process, or this one)."""


class LockUnavailable(Exception):
    """The lock file cannot be used at all (link, not a regular file, I/O error)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def is_link_or_reparse_point(st: os.stat_result) -> bool:
    """Whether an ``lstat`` result is a symlink or, on Windows, any reparse point."""
    if stat.S_ISLNK(st.st_mode):
        return True
    attributes = getattr(st, "st_file_attributes", 0) or 0
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


class ExclusiveFileLock:
    """A held exclusive lock on ``path``. Create with :meth:`acquire`."""

    def __init__(self, path: str, fd: int) -> None:
        self._path = path
        self._fd: Optional[int] = fd

    @property
    def path(self) -> str:
        return self._path

    @property
    def held(self) -> bool:
        return self._fd is not None

    @classmethod
    def acquire(
        cls,
        path: str,
        *,
        timeout: float = 0.0,
        poll_interval: float = 0.05,
    ) -> "ExclusiveFileLock":
        """Take the lock, creating the file (owner-only) if it is absent.

        ``timeout`` 0 is a single non-blocking attempt. A positive timeout polls
        until the deadline; it bounds how long this *caller* waits and has
        nothing to do with how long the holder may keep the lock.

        Raises ``LockBusy`` when another handle holds it, ``LockUnavailable``
        when the file is a link, not a regular file, or cannot be opened.
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        for _ in range(_IDENTITY_RETRIES):
            fd = _open_lock_file(path)
            try:
                while True:
                    try:
                        _try_lock(fd)
                        break
                    except LockBusy:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(poll_interval)
                if _still_names(path, fd):
                    return cls(path, fd)
            except BaseException:
                _close_quietly(fd)
                raise
            _close_quietly(fd)
        raise LockUnavailable("lock_file_unstable")

    def release(self) -> None:
        """Release the lock by closing its handle. Idempotent."""
        fd, self._fd = self._fd, None
        if fd is not None:
            _close_quietly(fd)

    def __enter__(self) -> "ExclusiveFileLock":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()

    def __del__(self) -> None:  # pragma: no cover - interpreter shutdown
        try:
            self.release()
        except Exception:
            pass


def _refuse_non_regular_existing(path: str) -> None:
    """Refuse an existing lock path that is a link or not a regular file."""
    try:
        existing = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise LockUnavailable("lock_file_unreadable") from exc
    if is_link_or_reparse_point(existing):
        raise LockUnavailable("lock_file_is_link")
    if not stat.S_ISREG(existing.st_mode):
        raise LockUnavailable("lock_file_not_regular")


_OPEN_FLAGS = (
    os.O_RDWR
    | os.O_CREAT
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_NOINHERIT", 0)
)


def _open_lock_file(path: str) -> int:
    _refuse_non_regular_existing(path)
    try:
        fd = os.open(path, _OPEN_FLAGS, 0o600)
    except OSError as exc:
        if exc.errno == getattr(errno, "ELOOP", None):
            raise LockUnavailable("lock_file_is_link") from exc
        raise LockUnavailable("lock_file_unopenable") from exc
    try:
        opened = os.fstat(fd)
    except OSError as exc:
        _close_quietly(fd)
        raise LockUnavailable("lock_file_unreadable") from exc
    if not stat.S_ISREG(opened.st_mode):
        _close_quietly(fd)
        raise LockUnavailable("lock_file_not_regular")
    return fd


def _try_lock(fd: int) -> None:
    if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
        import msvcrt

        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in _BUSY_ERRNOS:
                raise LockBusy() from exc
            raise LockUnavailable("lock_failed") from exc
    else:
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in _BUSY_ERRNOS:
                raise LockBusy() from exc
            raise LockUnavailable("lock_failed") from exc


def _still_names(path: str, fd: int) -> bool:
    """Whether ``path`` still names the file ``fd`` locked (POSIX identity check)."""
    if os.name != "posix":
        # Windows cannot delete or rename over a file another handle holds open
        # without share-delete, which Python does not request; the handle and
        # the name cannot diverge here.
        return True
    try:
        by_path = os.lstat(path)
        by_fd = os.fstat(fd)
    except OSError:
        return False
    if is_link_or_reparse_point(by_path):
        return False
    return (by_path.st_dev, by_path.st_ino) == (by_fd.st_dev, by_fd.st_ino)


def _close_quietly(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass
