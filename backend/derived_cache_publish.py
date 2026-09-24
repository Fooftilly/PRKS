"""Atomic publication for disposable derived cache files.

Library card thumbnails and Person portrait caches are derived, never
canonical backup state. They still need an atomic rename so readers never
see a half-written cache entry.

This module is the reviewed ``os.replace`` boundary for those derived
caches. New managed/canonical replacements must not land here — use
``backend.fs_durability`` / ``backend.services.work_pdf_replace``.
"""
from __future__ import annotations

import os


def publish_derived_cache_bytes(cache_path: str, body: bytes) -> None:
    """Write ``body`` to ``cache_path`` via a sibling temp + ``os.replace``."""
    parent = os.path.dirname(cache_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = cache_path + ".tmp"
    with open(tmp, "wb") as fp:
        fp.write(body)
    os.replace(tmp, cache_path)
