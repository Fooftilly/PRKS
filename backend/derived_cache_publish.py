"""Atomic publication for disposable derived cache files.

Library card thumbnails and Person portrait caches are derived, never
canonical backup state. They still need an atomic rename so readers never
see a half-written cache entry.

This module is the reviewed ``os.replace`` boundary for those derived
caches. New managed/canonical replacements must not land here — use
``backend.fs_durability`` / ``backend.services.work_pdf_replace``.

Concurrency: each publication uses a **unique** sibling temp (exclusive
create under a random ``.prks-cache-*.tmp`` name), not a shared
``<filename>.tmp``. Shared temps would let one writer ``os.replace`` while
another still holds the same inode open and continues writing into the
published destination.

Temp paths are rebuilt with the same join+normpath+startswith containment
as the destination so CodeQL does not treat ``mkstemp(dir=...)`` /
``os.replace(tmp, ...)`` as unsanitized path sinks.
"""
from __future__ import annotations

import os
import secrets


def _contained_cache_path(cache_dir: str, filename: str) -> str:
    """Return ``cache_dir/filename`` after CodeQL path-injection containment.

    Runtime callers already build cache basenames from sanitized ids
    (``prks_thumb_cache_stem`` / ``prks_person_image_cache_path``). The sink
    still rebuilds with join+normpath+startswith so CodeQL does not treat a
    helper return as still-tainted user data at the FS boundary.
    """
    base_path = os.path.realpath(cache_dir)
    # CodeQL py/path-injection documented sanitizer (query help user_picture3):
    # build with join+normpath, then startswith the root before any FS sink.
    name = os.path.basename(str(filename))
    if not name or name in {".", ".."}:
        raise ValueError("Invalid or unsafe derived cache path")
    fullpath = os.path.normpath(os.path.join(base_path, name))
    if fullpath == base_path or not fullpath.startswith(base_path + os.sep):
        raise ValueError("Invalid or unsafe derived cache path")
    return fullpath


def _exclusive_sibling_temp(cache_dir: str, base_path: str) -> tuple[int, str]:
    """Create an exclusive sibling temp under ``cache_dir``; return ``(fd, path)``."""
    for _ in range(64):
        candidate = f".prks-cache-{secrets.token_hex(8)}.tmp"
        tmp_path = _contained_cache_path(cache_dir, candidate)
        try:
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        if not tmp_path.startswith(base_path + os.sep):
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise ValueError("Invalid or unsafe derived cache path")
        return fd, tmp_path
    raise OSError("Could not allocate exclusive derived-cache temp")


def publish_derived_cache_bytes(cache_dir: str, filename: str, body: bytes) -> str:
    """Write ``body`` under ``cache_dir/filename`` via a unique sibling temp + ``os.replace``.

    Returns the absolute path published.
    """
    fullpath = _contained_cache_path(cache_dir, filename)
    parent = os.path.dirname(fullpath)
    os.makedirs(parent, exist_ok=True)
    base_path = os.path.realpath(cache_dir)
    fd, tmp = _exclusive_sibling_temp(cache_dir, base_path)
    try:
        with os.fdopen(fd, "wb") as fp:
            fd = -1  # ownership transferred to the file object
            fp.write(body)
        if not tmp.startswith(base_path + os.sep):
            raise ValueError("Invalid or unsafe derived cache path")
        if not fullpath.startswith(base_path + os.sep):
            raise ValueError("Invalid or unsafe derived cache path")
        os.replace(tmp, fullpath)
        tmp = ""  # published; nothing to clean up
    except Exception:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp and tmp.startswith(base_path + os.sep):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise
    return fullpath
