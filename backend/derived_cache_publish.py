"""Atomic publication for disposable derived cache files.

Library card thumbnails and Person portrait caches are derived, never
canonical backup state. They still need an atomic rename so readers never
see a half-written cache entry.

This module is the reviewed ``os.replace`` boundary for those derived
caches. New managed/canonical replacements must not land here — use
``backend.fs_durability`` / ``backend.services.work_pdf_replace``.

Concurrency: each publication uses a **unique** sibling temp (``mkstemp``),
not a shared ``<filename>.tmp``. Shared temps would let one writer
``os.replace`` while another still holds the same inode open and continues
writing into the published destination.
"""
from __future__ import annotations

import os
import tempfile


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


def publish_derived_cache_bytes(cache_dir: str, filename: str, body: bytes) -> str:
    """Write ``body`` under ``cache_dir/filename`` via a unique sibling temp + ``os.replace``.

    Returns the absolute path published.
    """
    fullpath = _contained_cache_path(cache_dir, filename)
    parent = os.path.dirname(fullpath)
    os.makedirs(parent, exist_ok=True)
    base_path = os.path.realpath(cache_dir)
    # Unique per writer — never share ``<basename>.tmp`` across concurrent calls.
    fd, tmp = tempfile.mkstemp(
        prefix=".prks-cache-",
        suffix=".tmp",
        dir=base_path,
    )
    try:
        with os.fdopen(fd, "wb") as fp:
            fp.write(body)
        os.replace(tmp, fullpath)
    except Exception:
        if tmp.startswith(base_path + os.sep):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise
    return fullpath
