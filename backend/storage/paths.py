import os
import re
from pathlib import Path
from typing import Optional

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_TEXT_INDEX_DB_NAME = "prks_text_index.db"
_RESEARCH_INDEX_DB_NAME = "prks_research_index.db"
_PRODUCTION_STORAGE = "/data"
PROCESSING_PROD_PREFERRED = "/data/for_processing"


def repo_root() -> str:
    return _REPO_ROOT


_WINDOWS_PATH_SEMANTICS = os.name == "nt"
_DRIVE_QUALIFIED_RE = re.compile(r"^[A-Za-z]:")


def windows_path_semantics(override: Optional[bool] = None) -> bool:
    """Whether Windows path semantics apply. ``override`` is for tests only."""
    return _WINDOWS_PATH_SEMANTICS if override is None else bool(override)


def resolved_child_path(
    root: str,
    *parts: str,
    windows_semantics: Optional[bool] = None,
) -> Optional[str]:
    """Resolve a filesystem child and prove that it remains beneath root.

    Every part must be a plain path segment. A drive-qualified segment is
    rejected here, at any depth, wherever the platform gives it drive
    semantics -- because ``joinpath()`` re-anchors on one. On Windows,
    ``joinpath("batch", "C:sample.pdf")`` yields ``<root>/batch/sample.pdf``:
    the segment is dropped and different bytes are targeted, and the
    containment check below cannot catch it because the rewritten target is
    still beneath root. A different drive (``"D:x.pdf"``) leaves root
    altogether. On POSIX such a name is an ordinary filename and is kept, so
    the stored spelling stays resolvable.

    The rule lives with the join rather than in each caller: it is the join
    that creates the hazard, so every present and future caller is covered.
    This is otherwise a containment primitive, not an input validator: callers
    should still enforce the shape of their own relative names before calling it.
    """
    if windows_path_semantics(windows_semantics) and any(
        _DRIVE_QUALIFIED_RE.match(str(part)) for part in parts
    ):
        return None
    try:
        base = Path(root).resolve(strict=False)
        candidate = base.joinpath(*parts).resolve(strict=False)
        candidate.relative_to(base)
    except (OSError, RuntimeError, ValueError):
        return None
    return str(candidate)


def testing_from_value(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes")


def parse_configured_root(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    root = str(raw).strip()
    if not root:
        return None
    return root


def assert_safe_testing_path(
    path: str, *, testing: bool, what: str = "PRKS_STORAGE"
) -> None:
    if not testing:
        return
    canonical = Path(path).resolve(strict=False)
    production = Path(_PRODUCTION_STORAGE).resolve(strict=False)
    if canonical == production or canonical.is_relative_to(production):
        raise RuntimeError(
            f"PRKS_TESTING is set: refusing to use {what} under /data"
        )
    repo_data = Path(os.path.join(_REPO_ROOT, "data")).resolve(strict=False)
    if canonical == repo_data or canonical.is_relative_to(repo_data):
        raise RuntimeError(
            f"PRKS_TESTING is set: refusing to use {what} under the repository data directory"
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
        return configured
    if configured_root:
        return os.path.join(configured_root, "for_processing")
    if testing:
        return os.path.join(_REPO_ROOT, "data_testing", "for_processing")
    return PROCESSING_PROD_PREFERRED


def derive_index_db_path(root: str) -> str:
    return os.path.join(root, _TEXT_INDEX_DB_NAME)


def derive_research_index_db_path(root: str) -> str:
    return os.path.join(root, _RESEARCH_INDEX_DB_NAME)


def derive_log_file(*, root: str, log_override: str) -> str:
    configured = (log_override or "").strip()
    if configured:
        return configured
    return os.path.join(root, "prks-errors.log")


def processing_prod_fallback() -> str:
    return os.path.join(_REPO_ROOT, "data", "for_processing")
