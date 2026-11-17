"""Frozen storage snapshot. Env is parsed only here.

The root itself is chosen by ``backend.storage.resolver`` (the one function
with the storage-architecture §5.2 precedence); this module hands it the
process environment and derives every component path from its answer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Literal, Mapping, Optional

from backend.storage import paths, resolver
from backend.storage.errors import StorageRootError

# The SQLite processing inbox's directory override. The Processing queue
# keeps its PDFs as managed objects (docs/postgresql-architecture.md §11.5),
# so there is no inbox directory to place; a process that still finds the
# variable set refuses to start rather than ignore it (PG-C2 #641).
RETIRED_PROCESSING_DIR_ENV = "PRKS_FOR_PROCESSING_DIR"


@dataclass(frozen=True)
class StorageConfig:
    mode: Literal["testing", "production"]
    configured_root: Optional[str]
    root: str
    db_path: str
    pdfs_dir: str
    thumbs_dir: str
    people_dir: str
    index_db_path: str
    research_index_db_path: str
    log_file: str
    # Which §5.2 source selected ``root`` (``resolver.ROOT_SOURCES``); None for
    # snapshots built directly by tests (``for_testing``).
    root_source: Optional[str] = None

    @classmethod
    def from_env(
        cls,
        *,
        cli_root: Optional[str] = None,
        environ: Optional[Mapping[str, str]] = None,
    ) -> StorageConfig:
        """Resolve the root (§5.2) and derive every component from it.

        ``cli_root`` is the ``--storage-root`` value, when given. An explicitly
        selected root (CLI, ``PRKS_STORAGE``, bootstrap file, packaged platform
        default) is normalized to an absolute path once, here (V1). The source
        checkout's development default keeps its historical derivation exactly.

        A set ``PRKS_FOR_PROCESSING_DIR``, to any value, is refused
        (``StorageRootError`` with reason ``retired_setting``).
        """
        env = os.environ if environ is None else environ
        refuse_retired_processing_dir(env)
        testing = paths.testing_from_value(env.get("PRKS_TESTING", ""))
        resolved = resolver.resolve_storage_root(
            cli_root=cli_root, environ=env, testing=testing
        )
        configured_root = (
            None
            if resolved.source == resolver.SOURCE_DEVELOPMENT_DEFAULT
            else resolved.root
        )
        log_override = (env.get("PRKS_LOG_FILE") or "").strip()
        return cls._from_parts(
            testing=testing,
            configured_root=configured_root,
            log_override=log_override,
            root_source=resolved.source,
        )

    def anchored_to(self, root_real: str) -> StorageConfig:
        """This snapshot with every root-relative path re-anchored beneath ``root_real``.

        The root may be a link (§7.3). It is resolved once, when its lease is
        taken, and every later open must stay beneath that one resolved target:
        a link retargeted afterwards must not move the database, PDFs or indexes
        to a root whose lease and marker this process never checked.
        Components an override places outside the root keep their paths, and
        ``configured_root`` keeps the configured spelling for diagnostics.
        """
        root = self.root
        changes = {
            name: _anchored(getattr(self, name), root, root_real)
            for name in _ROOT_ANCHORED_FIELDS
        }
        return replace(self, root=root_real, **changes)

    @classmethod
    def for_testing(cls, root: str) -> StorageConfig:
        configured = paths.parse_configured_root(root)
        if configured is None:
            raise ValueError("for_testing requires a non-empty root")
        paths.assert_safe_testing_path(configured, testing=True, what="PRKS_STORAGE")
        return cls._from_parts(
            testing=True,
            configured_root=configured,
            log_override="",
        )

    @classmethod
    def _from_parts(
        cls,
        *,
        testing: bool,
        configured_root: Optional[str],
        log_override: str,
        root_source: Optional[str] = None,
    ) -> StorageConfig:
        mode: Literal["testing", "production"] = "testing" if testing else "production"
        root = paths.defaulted_storage_root(testing=testing, configured_root=configured_root)
        db_path = paths.derive_db_path(testing=testing, configured_root=configured_root)
        pdfs_dir = paths.derive_pdfs_dir(testing=testing, configured_root=configured_root)
        thumbs_dir = paths.derive_thumbs_dir(testing=testing, configured_root=configured_root)
        people_dir = paths.derive_people_dir(testing=testing, configured_root=configured_root)
        index_db_path = paths.derive_index_db_path(root)
        research_index_db_path = paths.derive_research_index_db_path(root)
        log_file = paths.derive_log_file(root=root, log_override=log_override)
        writable = (
            (root, _root_label(root_source, configured_root)),
            (db_path, "db_path"),
            (pdfs_dir, "pdfs_dir"),
            (thumbs_dir, "thumbs_dir"),
            (people_dir, "people_dir"),
            (index_db_path, "index_db_path"),
            (research_index_db_path, "research_index_db_path"),
            (
                log_file,
                "PRKS_LOG_FILE" if (log_override or "").strip() else "log_file",
            ),
        )
        for path, what in writable:
            paths.assert_safe_testing_path(path, testing=testing, what=what)
        return cls(
            mode=mode,
            configured_root=configured_root,
            root=root,
            db_path=db_path,
            pdfs_dir=pdfs_dir,
            thumbs_dir=thumbs_dir,
            people_dir=people_dir,
            index_db_path=index_db_path,
            research_index_db_path=research_index_db_path,
            log_file=log_file,
            root_source=root_source,
        )


_ROOT_ANCHORED_FIELDS = (
    "db_path",
    "pdfs_dir",
    "thumbs_dir",
    "people_dir",
    "index_db_path",
    "research_index_db_path",
    "log_file",
)


def refuse_retired_processing_dir(environ: Mapping[str, str]) -> None:
    """Raise ``StorageRootError`` (``retired_setting``) while ``PRKS_FOR_PROCESSING_DIR`` is set.

    Any value counts, an empty string included: an operator who set it
    expected PRKS to scan that directory for PDFs, which this version no
    longer does, and silently ignoring it would leave files there unimported.
    """
    if RETIRED_PROCESSING_DIR_ENV not in environ:
        return
    raise StorageRootError(
        "retired_setting",
        f"{RETIRED_PROCESSING_DIR_ENV} is no longer read: Files for Processing is an "
        "upload queue in the Library, not a directory PRKS scans. Remove "
        f"{RETIRED_PROCESSING_DIR_ENV} from the environment (and from any .env or "
        "Compose file that sets it), and upload any PDFs still in that directory "
        "from the Files for Processing page.",
    )


def _anchored(path: str, root: str, root_real: str) -> str:
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    except ValueError:  # another drive on Windows
        return path
    if rel == os.curdir:
        return root_real
    if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
        return path
    return os.path.join(root_real, rel)


def _root_label(root_source: Optional[str], configured_root: Optional[str]) -> str:
    """How testing-safety errors name the root (existing messages kept)."""
    if configured_root is None:
        return "testing storage root"
    if root_source == resolver.SOURCE_CLI:
        return "--storage-root"
    return "PRKS_STORAGE"
