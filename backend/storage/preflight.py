"""Filesystem capability probes for a storage root (storage-architecture §7.2).

V4 (every start): create, write, fsync, rename and delete a probe file -- by
doing it, not by reading permission bits. V5 (exclusive create), V6 (atomic
rename over an existing file, within a directory and across directories) and
V8 (file fsync is fatal, directory fsync is recorded) run once per device, and
the binder caches the answer in the root marker.

Every probe runs inside ``<root>/.prks-maintenance/preflight/``, which holds
only ``.prks-probe-*`` files and the two probe subdirectories ``a/`` and
``b/``. It is removed on every exit; a copy left by a crash is recognized and
removed at the next start, and anything else found there is refused rather
than deleted.

This module performs raw ``os.replace`` calls on purpose: V6 exists to prove
that the filesystem honors exactly that primitive. It only ever renames its
own probe files inside the preflight scaffold, never library data, which is why
it is the one storage-root module on the INV-DURABILITY-001 allowlist.
"""

from __future__ import annotations

import logging
import os
import secrets
import stat
from typing import Optional

from backend.fs_durability import fsync_directory, fsync_open_file
from backend.storage.errors import StorageRootError, StorageRootRefused
from backend.storage.file_lock import is_link_or_reparse_point

LOGGER = logging.getLogger("prks.storage")

PREFLIGHT_DIRNAME = "preflight"
PROBE_PREFIX = ".prks-probe-"


def _lstat(path: str) -> Optional[os.stat_result]:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _is_probe_name(name: str) -> bool:
    return name.startswith(PROBE_PREFIX)


def preflight_is_scaffold(path: str) -> bool:
    st = _lstat(path)
    if st is None:
        return True
    if is_link_or_reparse_point(st) or not stat.S_ISDIR(st.st_mode):
        return False
    try:
        names = os.listdir(path)
    except OSError:
        return False
    for name in names:
        child = os.path.join(path, name)
        child_st = _lstat(child)
        if child_st is None:
            continue
        if is_link_or_reparse_point(child_st):
            return False
        if name in ("a", "b") and stat.S_ISDIR(child_st.st_mode):
            try:
                inner = os.listdir(child)
            except OSError:
                return False
            for inner_name in inner:
                inner_st = _lstat(os.path.join(child, inner_name))
                if inner_st is None:
                    continue
                if not (_is_probe_name(inner_name) and stat.S_ISREG(inner_st.st_mode)):
                    return False
            continue
        if not (_is_probe_name(name) and stat.S_ISREG(child_st.st_mode)):
            return False
    return True


def _probe_name() -> str:
    return PROBE_PREFIX + secrets.token_hex(8)


def _write_probe(path: str, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, payload)
        fsync_open_file(fd)  # V8: a file fsync that fails is fatal
    finally:
        os.close(fd)


def _read_probe(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _remove_preflight(preflight: str) -> None:
    """Remove a preflight scaffold, touching only what preflight creates."""
    if not os.path.lexists(preflight):
        return
    if not preflight_is_scaffold(preflight):
        raise StorageRootRefused(
            "preflight_unexpected_content",
            f"{preflight} holds files PRKS did not create; it will not delete them.",
        )
    for sub in ("a", "b"):
        sub_path = os.path.join(preflight, sub)
        if os.path.isdir(sub_path) and not os.path.islink(sub_path):
            for name in os.listdir(sub_path):
                if _is_probe_name(name):
                    os.remove(os.path.join(sub_path, name))
            os.rmdir(sub_path)
    for name in os.listdir(preflight):
        if _is_probe_name(name):
            os.remove(os.path.join(preflight, name))
    os.rmdir(preflight)


def run_preflight(maintenance: str, *, capabilities: bool) -> Optional[bool]:
    """V4 always; V5, V6 and V8 when ``capabilities``. Returns directory-fsync support.

    Every probe runs inside ``.prks-maintenance/preflight/``, which is removed
    on every exit, and which a crash-left copy of is recognized and removed at
    the next start. Returns ``None`` when capabilities were not probed.
    """
    preflight = os.path.join(maintenance, PREFLIGHT_DIRNAME)
    _remove_preflight(preflight)
    directory_fsync: Optional[bool] = None
    try:
        os.mkdir(preflight, 0o700)
        # V4: create, write, fsync, rename, delete -- by doing it.
        first = os.path.join(preflight, _probe_name())
        _write_probe(first, b"prks-probe-v4")
        renamed = os.path.join(preflight, _probe_name())
        os.replace(first, renamed)
        if _read_probe(renamed) != b"prks-probe-v4":
            raise StorageRootRefused("probe_readback_failed", "The storage root did not read back a probe file.")
        if capabilities:
            # V5: exclusive create refuses an existing name.
            try:
                fd = os.open(renamed, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
                raise StorageRootRefused(
                    "exclusive_create_unsupported",
                    "The storage root's filesystem does not honor exclusive file creation.",
                )
            # V6: atomic rename over an existing file, within one directory...
            replacement = os.path.join(preflight, _probe_name())
            _write_probe(replacement, b"prks-probe-v6")
            os.replace(replacement, renamed)
            # ...and across directories within the root.
            dir_a = os.path.join(preflight, "a")
            dir_b = os.path.join(preflight, "b")
            os.mkdir(dir_a, 0o700)
            os.mkdir(dir_b, 0o700)
            name = _probe_name()
            _write_probe(os.path.join(dir_a, name), b"prks-probe-v6-cross")
            _write_probe(os.path.join(dir_b, name), b"stale")
            os.replace(os.path.join(dir_a, name), os.path.join(dir_b, name))
            if (
                _read_probe(renamed) != b"prks-probe-v6"
                or _read_probe(os.path.join(dir_b, name)) != b"prks-probe-v6-cross"
                or os.path.lexists(os.path.join(dir_a, name))
            ):
                raise StorageRootRefused(
                    "atomic_rename_unsupported",
                    "The storage root's filesystem did not perform an atomic rename.",
                )
            # V8: directory fsync is best-effort and only recorded.
            directory_fsync = fsync_directory(dir_b)
        os.remove(renamed)
    except StorageRootError:
        raise
    except OSError as exc:
        raise StorageRootRefused(
            "root_not_writable",
            "The storage root is not writable (PRKS could not create, sync, rename "
            "and delete a probe file in it).",
        ) from exc
    finally:
        try:
            _remove_preflight(preflight)
        except (OSError, StorageRootError):
            LOGGER.warning("storage_preflight_cleanup_failed")
    return directory_fsync
