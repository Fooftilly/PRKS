"""Frozen storage snapshot. Env is parsed only here."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Optional

from backend.storage import paths


@dataclass(frozen=True)
class StorageConfig:
    mode: Literal["testing", "production"]
    configured_root: Optional[str]
    root: str
    db_path: str
    pdfs_dir: str
    thumbs_dir: str
    people_dir: str
    processing_dir: str
    index_db_path: str
    log_file: str
    processing_fallback_allowed: bool = False

    @classmethod
    def from_env(cls) -> StorageConfig:
        testing = paths.testing_from_value(os.environ.get("PRKS_TESTING", ""))
        configured_root = paths.parse_configured_root(os.environ.get("PRKS_STORAGE"))
        processing_override = (os.environ.get("PRKS_FOR_PROCESSING_DIR") or "").strip()
        log_override = (os.environ.get("PRKS_LOG_FILE") or "").strip()
        return cls._from_parts(
            testing=testing,
            configured_root=configured_root,
            processing_override=processing_override,
            log_override=log_override,
        )

    @classmethod
    def for_testing(cls, root: str) -> StorageConfig:
        configured = paths.parse_configured_root(root)
        if configured is None:
            raise ValueError("for_testing requires a non-empty root")
        paths.assert_safe_testing_path(configured, testing=True, what="PRKS_STORAGE")
        return cls._from_parts(
            testing=True,
            configured_root=configured,
            processing_override="",
            log_override="",
        )

    @classmethod
    def _from_parts(
        cls,
        *,
        testing: bool,
        configured_root: Optional[str],
        processing_override: str,
        log_override: str,
    ) -> StorageConfig:
        mode: Literal["testing", "production"] = "testing" if testing else "production"
        root = paths.defaulted_storage_root(testing=testing, configured_root=configured_root)
        db_path = paths.derive_db_path(testing=testing, configured_root=configured_root)
        pdfs_dir = paths.derive_pdfs_dir(testing=testing, configured_root=configured_root)
        thumbs_dir = paths.derive_thumbs_dir(testing=testing, configured_root=configured_root)
        people_dir = paths.derive_people_dir(testing=testing, configured_root=configured_root)
        processing_dir = paths.derive_processing_dir(
            testing=testing,
            configured_root=configured_root,
            processing_override=processing_override,
        )
        index_db_path = paths.derive_index_db_path(root)
        log_file = paths.derive_log_file(root=root, log_override=log_override)
        writable = (
            (
                root,
                "PRKS_STORAGE" if configured_root is not None else "testing storage root",
            ),
            (db_path, "db_path"),
            (pdfs_dir, "pdfs_dir"),
            (thumbs_dir, "thumbs_dir"),
            (people_dir, "people_dir"),
            (
                processing_dir,
                "PRKS_FOR_PROCESSING_DIR"
                if (processing_override or "").strip()
                else "processing_dir",
            ),
            (index_db_path, "index_db_path"),
            (
                log_file,
                "PRKS_LOG_FILE" if (log_override or "").strip() else "log_file",
            ),
        )
        for path, what in writable:
            paths.assert_safe_testing_path(path, testing=testing, what=what)
        processing_fallback_allowed = (
            not testing
            and configured_root is None
            and not (processing_override or "").strip()
            and processing_dir == paths.PROCESSING_PROD_PREFERRED
        )
        return cls(
            mode=mode,
            configured_root=configured_root,
            root=root,
            db_path=db_path,
            pdfs_dir=pdfs_dir,
            thumbs_dir=thumbs_dir,
            people_dir=people_dir,
            processing_dir=processing_dir,
            index_db_path=index_db_path,
            log_file=log_file,
            processing_fallback_allowed=processing_fallback_allowed,
        )
