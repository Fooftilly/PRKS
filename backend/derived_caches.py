"""Derived file caches under the storage root: PDF thumbnails and Person images (PG-C2 #641).

File names, paths and deletion of cache entries derived from Library
content. No database access. Moved from ``backend/db_manager.py``.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Optional


def prks_thumb_cache_safe_wid(work_id: str) -> str:
    """Sanitize work id for thumbnail filenames (must match server thumbnail handler)."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(work_id))


def prks_thumb_cache_safe_asset_id(asset_id: Optional[str]) -> str:
    """Sanitize Asset id for thumbnail filenames (must match server thumbnail handler)."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(asset_id or ""))


# Bump when thumbnail encode format changes (invalidates on-disk cache by filename).
PRKS_THUMB_CACHE_REV = 2


def prks_thumb_cache_stem(
    work_id: str, page: int, asset_id: Optional[str] = None
) -> str:
    """Cache filename stem for one PDF page thumbnail (no extension).

    Includes the selected primary Asset id when known so switching the primary
    PDF cannot reuse a cache file generated from a previous Asset (mtime alone
    is insufficient when the new PDF is older than an existing thumb).
    """
    safe = prks_thumb_cache_safe_wid(work_id)
    p = int(page) if page is not None else 1
    if p < 1:
        p = 1
    asset = prks_thumb_cache_safe_asset_id(asset_id)
    if asset:
        return f"{safe}_p{p}_a{asset}_v{PRKS_THUMB_CACHE_REV}"
    return f"{safe}_p{p}_v{PRKS_THUMB_CACHE_REV}"


def prks_person_image_cache_safe_id(person_id: str) -> str:
    """Sanitize person id for on-disk profile image cache filenames."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(person_id))


# Bump when portrait encode format changes (invalidates on-disk cache by filename).
PRKS_PERSON_IMAGE_CACHE_REV = 1


def prks_person_image_url_hash(image_url: str) -> str:
    """Stable short hash of normalized image_url for cache filenames."""
    norm = (image_url or "").strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]


def prks_person_image_cache_path(person_id: str, image_url: str, people_dir: str) -> str:
    """On-disk path for one person's compressed portrait (WebP)."""
    d = people_dir
    safe = prks_person_image_cache_safe_id(person_id)
    h = prks_person_image_url_hash(image_url)
    return os.path.join(
        d, f"{safe}_{h}_v{PRKS_PERSON_IMAGE_CACHE_REV}.webp"
    )


def prks_person_image_legacy_bin_path(person_id: str, people_dir: str) -> str:
    """Pre-compression cache file (raw bytes); migrated lazily to WebP."""
    d = people_dir
    safe = prks_person_image_cache_safe_id(person_id)
    return os.path.join(d, safe + ".bin")


def prks_delete_person_image_cache(person_id: str, people_dir: str) -> None:
    """Remove all cached portrait files for one person (best-effort)."""
    d = people_dir
    safe = prks_person_image_cache_safe_id(person_id)
    if not os.path.isdir(d):
        return
    legacy_bin = safe + ".bin"
    prefix = safe + "_"
    try:
        names = os.listdir(d)
    except OSError:
        return
    for fname in names:
        if fname != legacy_bin and not fname.startswith(prefix):
            continue
        try:
            os.remove(os.path.join(d, fname))
        except OSError:
            pass


_PRKS_THUMB_CACHE_FINAL_RE = re.compile(
    r"^(.+)_p(\d+)(?:_a([A-Za-z0-9_-]+))?_v(\d+)\.(webp|png|jpg|jpeg)$",
    re.IGNORECASE,
)


_PRKS_THUMB_CACHE_TMP_RE = re.compile(
    r"^(.+)_p(\d+)(?:_a([A-Za-z0-9_-]+))?_v(\d+)\.(webp|png|jpg|jpeg)\.tmp$",
    re.IGNORECASE,
)


# Pre-rev-2 filenames (no _vN suffix); pruned when not in allowed v2 stems.
_PRKS_THUMB_CACHE_LEGACY_FINAL_RE = re.compile(
    r"^(.+)_p(\d+)\.(webp|png)$", re.IGNORECASE
)


_PRKS_THUMB_CACHE_LEGACY_TMP_RE = re.compile(
    r"^(.+)_p(\d+)\.(webp|png)\.tmp$", re.IGNORECASE
)


def prks_delete_pdf_thumbnails_for_work_id(work_id: str, thumbs_dir: str) -> tuple[str, ...]:
    """Remove cached PDF thumbnails for one work (best-effort). Returns paths that could not be removed."""
    safe = prks_thumb_cache_safe_wid(work_id)
    td = thumbs_dir
    if not os.path.isdir(td):
        return ()
    pat_final = re.compile(
        r"^" + re.escape(safe) + r"_p\d+(_a[A-Za-z0-9_-]+)?(_v\d+)?\.(webp|png|jpg|jpeg)$",
        re.IGNORECASE,
    )
    pat_tmp = re.compile(
        r"^" + re.escape(safe) + r"_p\d+(_a[A-Za-z0-9_-]+)?(_v\d+)?\.(webp|png|jpg|jpeg)\.tmp$",
        re.IGNORECASE,
    )
    try:
        names = os.listdir(td)
    except OSError:
        return (td,)
    failed: list[str] = []
    for fname in names:
        if not pat_final.match(fname) and not pat_tmp.match(fname):
            continue
        path = os.path.join(td, fname)
        try:
            os.remove(path)
        except OSError:
            failed.append(path)
    return tuple(failed)
