"""Storage-path resolution. Returns configured strings; Path is only for the /data guard."""

import os
from pathlib import Path
from typing import Optional

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_TEXT_INDEX_DB_NAME = "prks_text_index.db"
_PRODUCTION_STORAGE = "/data"


def is_testing() -> bool:
    v = os.environ.get("PRKS_TESTING", "")
    return str(v).strip().lower() in ("1", "true", "yes")


def _assert_safe_testing_root(root: str, what: str = "PRKS_STORAGE") -> None:
    canonical = Path(root).resolve(strict=False)
    production = Path(_PRODUCTION_STORAGE).resolve(strict=False)
    if is_testing() and (
        canonical == production or canonical.is_relative_to(production)
    ):
        raise RuntimeError(
            f"PRKS_TESTING is set: refusing to use {what} under /data"
        )


def resolve_storage_root() -> Optional[str]:
    raw = os.environ.get("PRKS_STORAGE")
    if raw is None:
        return None
    root = str(raw).strip()
    if not root:
        return None
    _assert_safe_testing_root(root)
    return root


def resolve_defaulted_storage_root() -> str:
    root = resolve_storage_root()
    if root:
        return root
    if is_testing():
        return os.path.join(_REPO_ROOT, "data_testing")
    return os.path.join(_REPO_ROOT, "data")


def default_prks_db_path() -> str:
    """SQLite file under repo data/ or data_testing/ when PRKS_TESTING is set."""
    if is_testing():
        return os.path.join(_REPO_ROOT, "data_testing", "prks_data_testing.db")
    return os.path.join(_REPO_ROOT, "data", "prks_data.db")


def default_local_pdfs_dir() -> str:
    """PDF directory when PRKS_STORAGE is unset (prod vs testing)."""
    if is_testing():
        return os.path.join(_REPO_ROOT, "data_testing", "pdfs")
    return os.path.join(_REPO_ROOT, "data", "pdfs")


def resolve_db_path() -> str:
    storage_root = resolve_storage_root()
    if storage_root:
        return os.path.join(storage_root, "prks_data.db")
    return default_prks_db_path()


def resolve_pdfs_dir() -> str:
    storage_root = resolve_storage_root()
    if storage_root:
        return os.path.join(storage_root, "pdfs")
    return default_local_pdfs_dir()


def resolve_processing_dir() -> str:
    configured = (os.environ.get("PRKS_FOR_PROCESSING_DIR") or "").strip()
    if configured:
        _assert_safe_testing_root(configured, what="PRKS_FOR_PROCESSING_DIR")
        return configured
    storage_root = resolve_storage_root()
    if storage_root:
        return os.path.join(storage_root, "for_processing")
    if is_testing():
        return os.path.join(_REPO_ROOT, "data_testing", "for_processing")
    preferred = "/data/for_processing"
    fallback = os.path.join(_REPO_ROOT, "data", "for_processing")
    try:
        os.makedirs(preferred, exist_ok=True)
        return preferred
    except OSError:
        os.makedirs(fallback, exist_ok=True)
        return fallback


def resolve_thumbs_dir() -> str:
    storage_root = resolve_storage_root()
    if storage_root:
        return os.path.join(storage_root, "thumbs")
    if is_testing():
        return os.path.join(_REPO_ROOT, "data_testing", "thumbs")
    return os.path.join(_REPO_ROOT, "data", "thumbs")


def resolve_people_images_dir() -> str:
    storage_root = resolve_storage_root()
    if storage_root:
        return os.path.join(storage_root, "people")
    if is_testing():
        return os.path.join(_REPO_ROOT, "data_testing", "people")
    return os.path.join(_REPO_ROOT, "data", "people")


def resolve_index_db_path() -> str:
    return os.path.join(resolve_defaulted_storage_root(), _TEXT_INDEX_DB_NAME)
