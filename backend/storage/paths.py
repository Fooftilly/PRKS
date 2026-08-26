"""Pure storage-path derivation. Path is only for the /data guard.

Env parsing lives in backend.storage.config. This module does not read env.
"""

import os
from pathlib import Path
from typing import Optional

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_TEXT_INDEX_DB_NAME = "prks_text_index.db"
_PRODUCTION_STORAGE = "/data"
PROCESSING_PROD_PREFERRED = "/data/for_processing"


def repo_root() -> str:
    return _REPO_ROOT


def testing_from_value(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes")


def parse_configured_root(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    root = str(raw).strip()
    if not root:
        return None
    return root


def assert_safe_testing_root(
    root: str, *, testing: bool, what: str = "PRKS_STORAGE"
) -> None:
    if not testing:
        return
    canonical = Path(root).resolve(strict=False)
    production = Path(_PRODUCTION_STORAGE).resolve(strict=False)
    if canonical == production or canonical.is_relative_to(production):
        raise RuntimeError(
            f"PRKS_TESTING is set: refusing to use {what} under /data"
        )


def defaulted_storage_root(*, testing: bool, configured_root: Optional[str]) -> str:
    if configured_root:
        return configured_root
    if testing:
        return os.path.join(_REPO_ROOT, "data_testing")
    return os.path.join(_REPO_ROOT, "data")


def default_prks_db_path_for_mode(testing: bool) -> str:
    if testing:
        return os.path.join(_REPO_ROOT, "data_testing", "prks_data_testing.db")
    return os.path.join(_REPO_ROOT, "data", "prks_data.db")


def default_local_pdfs_dir_for_mode(testing: bool) -> str:
    if testing:
        return os.path.join(_REPO_ROOT, "data_testing", "pdfs")
    return os.path.join(_REPO_ROOT, "data", "pdfs")


def derive_db_path(*, testing: bool, configured_root: Optional[str]) -> str:
    if configured_root:
        return os.path.join(configured_root, "prks_data.db")
    return default_prks_db_path_for_mode(testing)


def derive_pdfs_dir(*, testing: bool, configured_root: Optional[str]) -> str:
    if configured_root:
        return os.path.join(configured_root, "pdfs")
    return default_local_pdfs_dir_for_mode(testing)


def derive_thumbs_dir(*, testing: bool, configured_root: Optional[str]) -> str:
    if configured_root:
        return os.path.join(configured_root, "thumbs")
    if testing:
        return os.path.join(_REPO_ROOT, "data_testing", "thumbs")
    return os.path.join(_REPO_ROOT, "data", "thumbs")


def derive_people_dir(*, testing: bool, configured_root: Optional[str]) -> str:
    if configured_root:
        return os.path.join(configured_root, "people")
    if testing:
        return os.path.join(_REPO_ROOT, "data_testing", "people")
    return os.path.join(_REPO_ROOT, "data", "people")


def derive_processing_dir(
    *,
    testing: bool,
    configured_root: Optional[str],
    processing_override: str,
) -> str:
    configured = (processing_override or "").strip()
    if configured:
        assert_safe_testing_root(
            configured, testing=testing, what="PRKS_FOR_PROCESSING_DIR"
        )
        return configured
    if configured_root:
        return os.path.join(configured_root, "for_processing")
    if testing:
        return os.path.join(_REPO_ROOT, "data_testing", "for_processing")
    return PROCESSING_PROD_PREFERRED


def derive_index_db_path(root: str) -> str:
    return os.path.join(root, _TEXT_INDEX_DB_NAME)


def derive_log_file(*, root: str, log_override: str) -> str:
    configured = (log_override or "").strip()
    if configured:
        return configured
    return os.path.join(root, "prks-errors.log")


def processing_prod_fallback() -> str:
    return os.path.join(_REPO_ROOT, "data", "for_processing")
