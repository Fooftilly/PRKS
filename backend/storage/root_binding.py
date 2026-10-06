"""Open a storage root for this process (storage-architecture §7, §12; Phase A).

``open_storage_root(config)`` runs once at process start, **before** restore
recovery and ``server.bind_storage()``:

1. path-level checks that need no writes: V1 (already normalized by the
   resolver), V2, V11 nesting/collision, V12 testing safety in both directions;
2. a read-only look at the marker, or, without one, at the directory contents,
   so a foreign or non-bindable directory is refused before anything is
   created in it;
3. the **single-process lease**: an exclusive, non-expiring OS lock on
   ``<root>/.prks-maintenance/root.lock`` (``file_lock``), held until the
   process exits. A second PRKS process -- or a second installation pointed at
   the same directory -- is refused. The marker's ``active_process`` names the
   last holder for the error message only; it is never the authority;
4. under the lease, the authoritative marker check, adoption of an unmarked
   PRKS library or creation of a new root (§7.1), V13 links, V7 one filesystem,
   the V4 write probe, the once-per-device V5/V6/V8 capability probes, and
   the cheap V9/V10 warnings;
5. one marker write recording the diagnostic holder and the probe cache.

What is deliberately **not** here (later phases): relocation recovery and
finalizers (§8), terminal teardown, hot rebind, ``unbind_storage()``, the
Settings writer of the bootstrap file, and the full choose/relocate §7.2 set
(V10 space requirement, V14, V15).

Nothing here logs a path, a storage root ID, or an exception string.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import socket
import stat
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator, Optional

from backend.fs_durability import fsync_directory
from backend.storage import distribution as _distribution
from backend.storage import paths
from backend.storage.errors import (
    InvalidStorageRoot,
    StorageRootError,
    StorageRootInUse,
    StorageRootRefused,
)
from backend.storage.file_lock import (
    ExclusiveFileLock,
    LockBusy,
    LockUnavailable,
    is_link_or_reparse_point,
)
from backend.storage.bootstrap_config import LOCK_SUFFIX
from backend.storage.resolver import SOURCE_CONFIG_FILE
from backend.storage.preflight import (
    PREFLIGHT_DIRNAME,
    preflight_is_scaffold,
    run_preflight,
)
from backend.storage.root_marker import (
    MAINTENANCE_DIRNAME,
    MARKER_FILENAME,
    ROOT_LOCK_NAME,
    RootMarker,
    binding_refusal,
    new_marker_document,
    read_marker,
    utc_timestamp,
    write_marker,
)

LOGGER = logging.getLogger("prks.storage")

# Hidden OS metadata that does not make a directory "non-empty" for V3.
OS_METADATA_NAMES = frozenset({".DS_Store", "desktop.ini", "Thumbs.db", "lost+found"})
# Volume metadata the OS keeps at the top of a mounted volume, so a library at
# the root of an external disk (which may be a mount point, §7.2) still opens.
# PRKS never reads or writes these; they are often unlistable by the owner
# (macOS ``.Trashes``, privacy-protected ``.Spotlight-V100``), so the V7/V13
# walk skips them like ``lost+found``. Matched case-insensitively at the top
# level only.
VOLUME_METADATA_NAMES = frozenset(
    name.casefold()
    for name in (
        # macOS
        ".Trashes",
        ".Spotlight-V100",
        ".fseventsd",
        ".TemporaryItems",
        ".DocumentRevisions-V100",
        ".VolumeIcon.icns",
        ".com.apple.timemachine.donotpresent",
        ".apdisk",
        # Linux desktops (the per-user form is ``.Trash-<uid>``)
        ".Trash",
        # Windows, NTFS/exFAT
        "System Volume Information",
        "$RECYCLE.BIN",
    )
)
_PER_USER_TRASH = re.compile(r"\.Trash-[0-9]+\Z")


def is_top_level_os_metadata(name: str) -> bool:
    """Whether a top-level root entry is OS or volume metadata PRKS ignores."""
    return (
        name in OS_METADATA_NAMES
        or name.casefold() in VOLUME_METADATA_NAMES
        or _PER_USER_TRASH.match(name) is not None
    )
# V10 (startup): warn below this much free space. Choose/relocate enforce a
# computed requirement in a later phase.
LOW_FREE_SPACE_WARNING_BYTES = 1024 * 1024 * 1024
# V9: filesystem types known with certainty to be network mounts, where SQLite
# locking and WAL are unsafe for a live library (§7.4). Linux names come from
# /proc/mounts, macOS names from mount(8), and ``windows-remote`` is a UNC path
# or a drive Windows reports as DRIVE_REMOTE.
WINDOWS_REMOTE = "windows-remote"
WINDOWS_LOCAL = "windows-local"
NETWORK_FILESYSTEM_TYPES = frozenset(
    {
        "nfs",
        "nfs4",
        "cifs",
        "smbfs",
        "smb3",
        "afs",
        "afpfs",
        "ncpfs",
        "davfs",
        "webdav",
        "fuse.sshfs",
        "fuse.rclone",
        "fuse.s3fs",
        WINDOWS_REMOTE,
    }
)
# overlayfs may report the lower layer's device for unmodified *files*, so on
# an overlay root only directories are held to V7's one-device rule.
OVERLAY_FILESYSTEM_TYPES = frozenset({"overlay", "overlayfs"})
# V11: how many directory entries the bounded scan for nested markers reads.
_NESTED_MARKER_SCAN_LIMIT = 2000

UNMARKED_EMPTY = "empty"
UNMARKED_PRKS = "prks"
UNMARKED_FOREIGN = "foreign"


@dataclass
class BoundRoot:
    """A storage root this process has opened and holds the lease on."""

    root: str
    root_real: str
    source: Optional[str]
    marker: RootMarker
    lease: ExclusiveFileLock
    created: bool = False
    adopted: bool = False
    # Warnings raised while opening, held back by ``defer_logs`` until
    # ``log_binding()`` runs with logging configured.
    deferred_records: list[logging.LogRecord] = field(default_factory=list, repr=False)

    @property
    def storage_root_id(self) -> str:
        return self.marker.storage_root_id

    def anchor(self, config: Any) -> Any:
        """``config`` with its root-relative paths pinned to this lease's resolved root."""
        return config.anchored_to(self.root_real)

    def log_binding(self) -> None:
        """Report the startup diagnostics and which source selected this root.

        The process entry calls this once logging is configured: opening the
        root necessarily happens first, and records emitted then would reach
        only ``logging.lastResort`` (warnings, bare, on stderr) or nowhere
        (INFO). Warnings deferred by ``open_storage_root(defer_logs=True)`` are
        replayed first, then the path-free binding summary.
        """
        records, self.deferred_records = self.deferred_records, []
        for record in records:
            LOGGER.handle(record)
        LOGGER.info(
            "storage_root_bound source=%s created=%s adopted=%s",
            self.source or "direct",
            "true" if self.created else "false",
            "true" if self.adopted else "false",
        )

    def release(self) -> None:
        """Release the lease (tests and orderly shutdown; the kernel does it on exit)."""
        global _ACTIVE
        with _ACTIVE_GUARD:
            if _ACTIVE is self:
                _ACTIVE = None
        self.lease.release()


_ACTIVE: Optional[BoundRoot] = None
_ACTIVE_GUARD = threading.Lock()


def active_bound_root() -> Optional[BoundRoot]:
    """The root this process opened with ``open_storage_root``, if any."""
    return _ACTIVE


def assert_config_matches_bound_root(root: str) -> None:
    """Refuse to publish a storage binding for a root this process has not leased.

    Only enforced once the process entry has opened a root: in-process tests
    that call ``bind_storage()`` directly on scratch roots are unaffected.
    """
    active = _ACTIVE
    if active is None:
        return
    try:
        candidate = os.path.realpath(root)
    except OSError as exc:
        raise StorageRootRefused("root_not_leased", "Storage root could not be resolved.") from exc
    if os.path.normcase(candidate) != os.path.normcase(active.root_real):
        raise StorageRootRefused(
            "root_not_leased",
            "Refusing to bind a storage root this process does not hold the lease for.",
        )


# --- path helpers ---------------------------------------------------------------


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _is_within(child: str, parent: str) -> bool:
    """Lexical containment of two already-resolved absolute paths (equal counts)."""
    try:
        child_n = os.path.normcase(os.path.normpath(child))
        parent_n = os.path.normcase(os.path.normpath(parent))
        return os.path.commonpath((child_n, parent_n)) == parent_n
    except ValueError:  # different drives on Windows
        return False


def _lstat(path: str) -> Optional[os.stat_result]:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _relative_components(path: str, root: str) -> Optional[str]:
    """``path`` relative to ``root`` when it lies strictly beneath it, else None."""
    if not _is_within(os.path.abspath(path), os.path.abspath(root)):
        return None
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    return None if rel in (os.curdir, "") else rel


# --- V2, V11, V12: path-level checks (no writes) ----------------------------------


def _check_testing_boundary(root: str, root_real: str, *, testing: bool) -> None:
    """V12: testing never touches /data or <repo>/data; production never binds the testing tree."""
    if testing:
        try:
            paths.assert_safe_testing_path(root, testing=True, what="storage root")
        except RuntimeError as exc:
            raise InvalidStorageRoot("testing_unsafe_root", str(exc)) from exc
        return
    testing_tree = os.path.realpath(os.path.join(paths.repo_root(), "data_testing"))
    if _is_within(root_real, testing_tree):
        raise InvalidStorageRoot(
            "production_testing_root",
            "A non-testing PRKS run refuses the repository data_testing/ tree. "
            "Use --testing, or select a different storage root.",
        )


def _check_not_special(root_real: str, *, home: Optional[str]) -> None:
    """V11: never the filesystem root or the home directory itself."""
    if os.path.dirname(root_real) == root_real:
        raise InvalidStorageRoot("root_is_filesystem_root", "The storage root cannot be a filesystem root.")
    home_dir = home if home is not None else os.path.expanduser("~")
    if home_dir and _same_path(root_real, os.path.realpath(home_dir)):
        raise InvalidStorageRoot("root_is_home", "The storage root cannot be your home directory itself.")


def _check_not_nested(root: str, root_real: str) -> None:
    """V11: never inside another root's maintenance area, never nested in or around another root."""
    parent = os.path.dirname(root_real)
    while True:
        if os.path.basename(parent) == MAINTENANCE_DIRNAME:
            raise InvalidStorageRoot(
                "root_inside_maintenance",
                "The storage root cannot be inside another storage root's .prks-maintenance/.",
            )
        if os.path.lexists(os.path.join(parent, MARKER_FILENAME)):
            raise InvalidStorageRoot(
                "root_nested",
                f"The storage root {root} is inside another PRKS storage root ({parent}).",
            )
        up = os.path.dirname(parent)
        if up == parent:
            break
        parent = up
    nested = _find_nested_marker(root_real)
    if nested is not None:
        raise InvalidStorageRoot(
            "root_contains_root",
            f"The storage root {root} contains another PRKS storage root ({nested}).",
        )


def _check_not_over_config_or_install(
    root_real: str, *, config_file_path: Optional[str], distribution: str
) -> None:
    """V11: the bootstrap file and the install directory stay outside the root.

    The root may still sit inside the configuration *directory* (§6). A source
    checkout keeps its declared development defaults (and, for existing
    self-hosted setups, other locations inside the checkout); a packaged build
    refuses all of them.
    """
    if config_file_path and _is_within(os.path.realpath(config_file_path), root_real):
        raise InvalidStorageRoot(
            "root_contains_config",
            "The storage root cannot contain the PRKS bootstrap configuration file.",
        )
    install = os.path.realpath(paths.repo_root())
    if _is_within(install, root_real):
        raise InvalidStorageRoot(
            "root_contains_install",
            "The storage root cannot be (or contain) the PRKS installation directory.",
        )
    if distribution == _distribution.PACKAGED and _is_within(root_real, install):
        raise InvalidStorageRoot(
            "root_inside_install",
            "The storage root cannot be inside the PRKS installation directory.",
        )


def _check_log_file_not_operational(
    log_file: Optional[str], root_real: str, *, config_file_path: Optional[str]
) -> None:
    """The error log never aliases the marker, the lease or the bootstrap file.

    Logging appends text to ``log_file``; landing on one of those would corrupt
    it, and the next startup would refuse the root or the configuration as
    malformed. Compared by real path, so a symlink or an override spelling
    the same file another way is caught, and by file identity, so a hard link
    is too, before anything is written.
    """
    if not (log_file or "").strip():
        return
    log_real = os.path.realpath(os.path.abspath(log_file or ""))
    maintenance = os.path.join(root_real, MAINTENANCE_DIRNAME)
    operational = [
        os.path.join(root_real, MARKER_FILENAME),
        os.path.join(maintenance, ROOT_LOCK_NAME),
    ]
    if config_file_path:
        config_real = os.path.realpath(config_file_path)
        operational += [config_real, config_real + LOCK_SUFFIX]
    if (
        any(_same_path(log_real, path) for path in operational)
        or _is_within(log_real, maintenance)
        or _shares_a_file(log_real, operational)
    ):
        raise InvalidStorageRoot(
            "log_file_operational",
            f"The log file {log_file} cannot be the storage root marker, a file in "
            f"{MAINTENANCE_DIRNAME}/, or the PRKS bootstrap configuration file or its lock.",
        )


def _shares_a_file(path: str, others: list[str]) -> bool:
    """Whether an existing ``path`` is the same file as any existing ``others``."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    for other in others:
        try:
            if os.path.samestat(st, os.stat(other)):
                return True
        except OSError:
            continue
    return False


def _check_exists_or_creatable(root: str) -> None:
    """V2: an existing root is a directory; a new one needs an existing parent."""
    if os.path.lexists(root):
        if not os.path.isdir(root):
            raise InvalidStorageRoot("root_not_directory", f"The storage root {root} is not a directory.")
    elif not os.path.isdir(os.path.dirname(os.path.abspath(root))):
        raise InvalidStorageRoot(
            "root_parent_missing",
            f"The parent directory of the storage root {root} does not exist. "
            "PRKS does not create missing parents; check that the disk or mount is available.",
        )


def _check_placement(
    root: str,
    *,
    testing: bool,
    config_file_path: Optional[str],
    distribution: str,
    home: Optional[str],
) -> str:
    """V2/V11/V12 before anything is written. Returns the resolved root path."""
    if not os.path.isabs(root):
        raise InvalidStorageRoot("root_not_absolute", "The storage root must be an absolute path.")
    try:
        root_real = os.path.realpath(root)
    except OSError as exc:
        raise InvalidStorageRoot("root_unresolvable", f"The storage root {root} cannot be resolved.") from exc
    _check_testing_boundary(root, root_real, testing=testing)
    _check_not_special(root_real, home=home)
    _check_not_nested(root, root_real)
    _check_not_over_config_or_install(
        root_real, config_file_path=config_file_path, distribution=distribution
    )
    _check_exists_or_creatable(root)
    return root_real


def _find_nested_marker(root_real: str) -> Optional[str]:
    """Bounded scan for a marker one or two levels below ``root_real``."""
    if not os.path.isdir(root_real):
        return None
    budget = _NESTED_MARKER_SCAN_LIMIT
    frontier = [(root_real, 0)]
    while frontier:
        directory, depth = frontier.pop()
        try:
            with os.scandir(directory) as it:
                for entry in it:
                    budget -= 1
                    if budget < 0:
                        return None
                    if depth == 0 and entry.name == MAINTENANCE_DIRNAME:
                        continue
                    try:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    if os.path.lexists(os.path.join(entry.path, MARKER_FILENAME)):
                        return entry.path
                    if depth + 1 < 2:
                        frontier.append((entry.path, depth + 1))
        except OSError:
            continue
    return None


# --- V3: what an unmarked directory holds -----------------------------------------


def _maintenance_is_scaffold(path: str) -> bool:
    """A ``.prks-maintenance/`` holding only a lone ``root.lock`` and/or preflight scaffold."""
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
        if name == ROOT_LOCK_NAME:
            child_st = _lstat(child)
            if child_st is not None and (
                is_link_or_reparse_point(child_st) or not stat.S_ISREG(child_st.st_mode)
            ):
                return False
            continue
        if name == PREFLIGHT_DIRNAME and preflight_is_scaffold(child):
            continue
        return False
    return True


SQLITE_HEADER = b"SQLite format 3\x00"
# Maintenance names only PRKS creates (backup_restore): an interrupted restore
# whose recovery must still be able to run leaves one of these behind.
RECOVERABLE_MAINTENANCE_FILES = frozenset({"restore-journal.json"})
RECOVERABLE_MAINTENANCE_DIRS = frozenset({"rollback", "restore-staging", "backup"})


def _is_plain_dir(path: str) -> bool:
    st = _lstat(path)
    return st is not None and not is_link_or_reparse_point(st) and stat.S_ISDIR(st.st_mode)


def _is_plain_file(path: str) -> bool:
    st = _lstat(path)
    return st is not None and not is_link_or_reparse_point(st) and stat.S_ISREG(st.st_mode)


def _is_sqlite_database(path: str) -> bool:
    if not _is_plain_file(path):
        return False
    try:
        with open(path, "rb") as handle:
            return handle.read(len(SQLITE_HEADER)) == SQLITE_HEADER
    except OSError:
        return False


def _has_recoverable_maintenance_state(path: str) -> bool:
    """``.prks-maintenance/`` holding state PRKS itself writes (a restore journal or its trees)."""
    if not _is_plain_dir(path):
        return False
    for name in RECOVERABLE_MAINTENANCE_FILES:
        if _is_plain_file(os.path.join(path, name)):
            return True
    return any(_is_plain_dir(os.path.join(path, name)) for name in RECOVERABLE_MAINTENANCE_DIRS)


_COMPONENT_DIR_FIELDS = ("pdfs_dir", "thumbs_dir", "people_dir", "processing_dir")
_COMPONENT_FILE_FIELDS = ("index_db_path", "research_index_db_path", "log_file")


def component_root_names(config: Any) -> dict[str, frozenset[str]]:
    """Top-level names ``config`` derives directly under its root, by kind.

    Read from the same ``StorageConfig`` the server binds, so a new component
    cannot drift out of :func:`classify_unmarked_root`. Overrides that place a
    component elsewhere add nothing. The development inbox fallback counts when
    it may be used.
    """
    root = os.path.abspath(config.root)

    def top_level(path: Optional[str]) -> Optional[str]:
        if not path:
            return None
        path = os.path.abspath(path)
        return os.path.basename(path) if _same_path(os.path.dirname(path), root) else None

    dir_paths = [getattr(config, name, None) for name in _COMPONENT_DIR_FIELDS]
    if getattr(config, "processing_fallback_allowed", False):
        dir_paths.append(paths.processing_prod_fallback(config.root))
    file_paths = [getattr(config, name, None) for name in _COMPONENT_FILE_FIELDS]
    return {
        "component_dirs": frozenset(n for n in map(top_level, dir_paths) if n is not None),
        "component_files": frozenset(n for n in map(top_level, file_paths) if n is not None),
    }


def classify_unmarked_root(
    root: str,
    *,
    db_filename: str,
    component_dirs: frozenset[str] = frozenset({"pdfs"}),
    component_files: frozenset[str] = frozenset(),
) -> str:
    """V3 for a directory without a marker: ``empty``, ``prks`` or ``foreign``.

    ``prks`` (adopt, §7.1) needs proof by type and content, not by name: the
    library database as a regular file with a SQLite header, ``pdfs/`` as a
    plain directory, or recognized recoverable restore state under
    ``.prks-maintenance/`` (an interrupted restore may have moved the database
    and ``pdfs/`` away, and its recovery must still be able to run).
    ``empty``: nothing but OS metadata, a lock/preflight scaffold, or a
    leftover marker-write temporary. Anything else is ``foreign``.

    A reserved name of the wrong type -- the database as a directory, link or
    non-SQLite file; ``.prks-maintenance`` or a component directory
    (``component_dirs``: ``pdfs``, ``thumbs``, ``people``, the inbox) as
    anything but a plain directory; a component file (``component_files``:
    the index databases, the log) as anything but a plain file -- is
    ``foreign`` on its own, and next to otherwise valid proof it is refused
    (``root_malformed``, or ``root_contains_link`` for a link) rather than
    adopted, so such a directory is never marked as a library. An empty
    database file, which SQLite itself can leave, is not proof but is not
    malformed either.
    """
    try:
        names = os.listdir(root)
    except FileNotFoundError:
        return UNMARKED_EMPTY
    except OSError as exc:
        raise StorageRootRefused("root_unreadable", f"The storage root {root} cannot be read.") from exc
    looks_like_prks = False
    foreign = False
    malformed: list[str] = []
    for name in names:
        path = os.path.join(root, name)
        if is_top_level_os_metadata(name):
            continue
        if name.startswith(".prks-write-") and name.endswith(".tmp") and _is_plain_file(path):
            continue
        if name == MAINTENANCE_DIRNAME:
            if not _is_plain_dir(path):
                malformed.append(path)
                foreign = True
                continue
            if _maintenance_is_scaffold(path):
                continue
            if _has_recoverable_maintenance_state(path):
                looks_like_prks = True
            else:
                foreign = True
            continue
        if name == db_filename:
            if _is_sqlite_database(path):
                looks_like_prks = True
            else:
                if not (_is_plain_file(path) and os.path.getsize(path) == 0):
                    malformed.append(path)
                foreign = True
            continue
        if name in component_dirs:
            if not _is_plain_dir(path):
                malformed.append(path)
            elif name == "pdfs":
                looks_like_prks = True
                continue
            foreign = True
            continue
        if name in component_files and not _is_plain_file(path):
            malformed.append(path)
        foreign = True
    if looks_like_prks:
        for path in malformed:
            st = _lstat(path)
            if st is not None and is_link_or_reparse_point(st):
                raise _link_error(path)
            raise _malformed_entry_error(path)
        return UNMARKED_PRKS
    return UNMARKED_FOREIGN if foreign else UNMARKED_EMPTY


def _malformed_entry_error(path: str) -> StorageRootRefused:
    return StorageRootRefused(
        "root_malformed",
        f"{path} uses a name PRKS reserves in its storage root, but it is not what "
        "PRKS would have written there. PRKS will not adopt this directory as a "
        "library; move that entry away or choose another directory.",
    )


def _missing_selected_root(root: str) -> StorageRootRefused:
    return StorageRootRefused(
        "root_missing",
        f"The storage root {root} selected in the PRKS bootstrap configuration is "
        "missing or empty and has no prks-root.json. If it lives on a removable "
        "disk or network share, make sure it is mounted. PRKS does not start a new, "
        "empty library in its place.",
    )


def _foreign_root_error(root: str) -> StorageRootRefused:
    return StorageRootRefused(
        "root_foreign",
        f"The storage root {root} is not empty and does not look like a PRKS library "
        "(no database, no pdfs/ directory, no prks-root.json). PRKS will not create "
        "a new library there. Choose an empty directory or an existing PRKS library.",
    )


# --- V13, V7: the whole root, under the lease ------------------------------------


def _link_error(path: str) -> StorageRootRefused:
    return StorageRootRefused(
        "root_contains_link",
        f"{path} is a link. Nothing inside a storage root may be a link; the storage "
        "root itself may be a link, and the inbox and log may be placed elsewhere "
        "with PRKS_FOR_PROCESSING_DIR and PRKS_LOG_FILE.",
    )


def _device_error(path: str) -> StorageRootRefused:
    return StorageRootRefused(
        "root_spans_filesystems",
        f"{path} is on a different filesystem than the storage root. Nothing inside "
        "the root may be a mount point, so renames between maintenance and "
        "components stay atomic.",
    )


MOUNTINFO_FILE = "/proc/self/mountinfo"


def parse_mountinfo(text: str) -> list[str]:
    """Mount points from Linux ``/proc/self/mountinfo`` (field 5, octal-unescaped)."""
    points = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 5:
            points.append(_unescape_mount_field(fields[4]))
    return points


def _mount_points(*, mountinfo_file: str = MOUNTINFO_FILE) -> Optional[list[str]]:
    """Every mount point this process can see, or None when the platform cannot list them.

    Linux reads ``/proc/self/mountinfo``, which includes bind mounts of single
    files; macOS parses ``mount(8)``. Windows has no separate list: a volume
    mounted on a folder is a reparse point, which the tree walk refuses.
    """
    if sys.platform == "win32":  # pragma: no cover - Windows only
        return None
    if sys.platform == "darwin":  # pragma: no cover - macOS only
        import subprocess

        try:
            output = subprocess.run(
                ["/sbin/mount"], capture_output=True, text=True, timeout=10, check=True
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        return [point for point, _type in parse_bsd_mount_output(output)]
    try:
        with open(mountinfo_file, encoding="utf-8", errors="replace") as handle:
            return parse_mountinfo(handle.read())
    except OSError:
        return None


def _check_no_mount_points(root_real: str) -> None:
    """V7: no mount boundary strictly inside the root, whatever its device.

    A bind mount of a directory or file from the same filesystem keeps the
    root's ``st_dev``, yet ``rename()`` across the two mounts still fails with
    ``EXDEV``, which would break restore and relocation. So device numbers are
    not enough: the mount table itself is consulted. The root may itself be a
    mount point. When no table is available the walk's device check remains.
    """
    points = _mount_points()
    if points is None:
        return
    for point in points:
        if not _same_path(point, root_real) and _is_within(point, root_real):
            raise StorageRootRefused(
                "root_contains_mount_point",
                f"{point} is a mount point inside the storage root. Nothing inside "
                "the root may be mounted, even from the same filesystem, because "
                "renames across mounts are not atomic.",
            )


def _check_tree(root_real: str, *, file_devices: bool = True) -> None:
    """V13 and V7 for everything beneath the root (§7.3): no links, no mount points.

    A complete, no-follow walk of the resolved root, excluding the root path
    itself: every entry is ``lstat``-ed, a link or Windows reparse point is
    refused, and so is any entry -- directory or file (a file bind mount) --
    whose device differs from the root's. ``file_devices=False`` relaxes the
    device rule for regular files only, for overlayfs (see
    ``OVERLAY_FILESYSTEM_TYPES``). OS metadata at the top level (a volume's
    ``lost+found``, or ``.Trashes`` at the top of a mounted volume, see
    ``VOLUME_METADATA_NAMES``) is not PRKS's to walk. A directory that cannot be listed,
    or an entry that cannot be ``lstat``-ed, is refused: the invariant cannot
    be proven for it.
    """
    root_dev = os.stat(root_real).st_dev
    pending = [root_real]
    while pending:
        current = pending.pop()
        try:
            names = os.listdir(current)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise StorageRootRefused(
                "root_unreadable", f"{current} inside the storage root cannot be read."
            ) from exc
        for name in names:
            if current == root_real and is_top_level_os_metadata(name):
                continue
            path = os.path.join(current, name)
            try:
                st = _lstat(path)
            except OSError as exc:
                raise StorageRootRefused(
                    "root_unreadable", f"{path} inside the storage root cannot be read."
                ) from exc
            if st is None:
                continue
            if is_link_or_reparse_point(st):
                raise _link_error(path)
            is_dir = stat.S_ISDIR(st.st_mode)
            if st.st_dev != root_dev and (is_dir or file_devices):
                raise _device_error(path)
            if is_dir:
                pending.append(path)


# --- V9, V10 warnings ---------------------------------------------------------------


def _unescape_mount_field(value: str) -> str:
    return (
        value.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\")
    )


def _longest_mount_match(path: str, mounts: list[tuple[str, str]]) -> Optional[str]:
    best: Optional[tuple[int, str]] = None
    for mountpoint, fs_type in mounts:
        if _is_within(path, mountpoint) and (best is None or len(mountpoint) >= best[0]):
            best = (len(mountpoint), fs_type)
    return best[1] if best else None


def _proc_mounts_type(path: str, mounts_file: str) -> Optional[str]:
    try:
        with open(mounts_file, encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return None
    mounts = []
    for line in lines:
        fields = line.split()
        if len(fields) >= 3:
            mounts.append((_unescape_mount_field(fields[1]), fields[2]))
    return _longest_mount_match(path, mounts)


def parse_bsd_mount_output(text: str) -> list[tuple[str, str]]:
    """``(mountpoint, type)`` from BSD/macOS ``mount`` output: ``src on /mnt (type, …)``."""
    mounts = []
    for line in text.splitlines():
        head, sep, tail = line.rpartition(" (")
        if not sep or " on " not in head:
            continue
        mountpoint = head.split(" on ", 1)[1]
        fs_type = tail.split(",", 1)[0].rstrip(")").strip()
        if mountpoint and fs_type:
            mounts.append((mountpoint, fs_type))
    return mounts


def _darwin_filesystem_type(path: str, *, mount_output: Optional[str] = None) -> Optional[str]:
    if mount_output is None:
        import subprocess

        try:
            mount_output = subprocess.run(
                ["/sbin/mount"], capture_output=True, text=True, timeout=10, check=True
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return None
    return _longest_mount_match(path, parse_bsd_mount_output(mount_output))


_WINDOWS_DRIVE_REMOTE = 4


def windows_unc_or_drive(path: str) -> tuple[bool, Optional[str]]:
    """``(is_unc, drive)`` for a Windows path, handling ``\\\\?\\`` prefixes."""
    import ntpath

    if path.startswith("\\\\?\\UNC\\"):
        return True, None
    if path.startswith("\\\\?\\"):
        path = path[4:]
    drive = ntpath.splitdrive(path)[0]
    if drive.startswith("\\\\"):
        return True, None
    return False, drive or None


def _windows_filesystem_type(path: str, *, drive_type=None) -> Optional[str]:
    is_unc, drive = windows_unc_or_drive(path)
    if is_unc:
        return WINDOWS_REMOTE
    if drive is None:
        return None
    if drive_type is None:
        if sys.platform != "win32":  # pragma: no cover - guarded by the caller
            return None
        import ctypes

        drive_type = ctypes.windll.kernel32.GetDriveTypeW
    kind = drive_type(drive + "\\")
    return WINDOWS_REMOTE if kind == _WINDOWS_DRIVE_REMOTE else WINDOWS_LOCAL


def detect_filesystem_type(path: str, *, mounts_file: str = "/proc/mounts") -> Optional[str]:
    """Filesystem type of ``path``, or None when this platform cannot classify it.

    Linux reads ``/proc/mounts``, macOS parses ``mount(8)``, Windows treats a
    UNC path or a ``DRIVE_REMOTE`` drive as ``windows-remote``.
    """
    if sys.platform == "win32":  # pragma: no cover - Windows only
        return _windows_filesystem_type(path)
    if sys.platform == "darwin":  # pragma: no cover - macOS only
        return _darwin_filesystem_type(path)
    return _proc_mounts_type(path, mounts_file)


def check_filesystem_type(path: str) -> None:
    """V9 (§7.4): refuse a live SQLite-era root on a filesystem known to be unsafe.

    A type known with certainty to be a network or network-backed FUSE mount
    is refused; SQLite's WAL and locking are not reliable there. Any other
    FUSE type is uncertain and only warned about, and so is a filesystem this
    platform cannot classify at all.
    """
    fs_type = detect_filesystem_type(path)
    if fs_type is None:
        # Not evidence of a local disk: say so rather than assume it.
        LOGGER.warning("storage_root_filesystem_unclassified")
        return
    fs_type = fs_type.lower()
    if fs_type in NETWORK_FILESYSTEM_TYPES:
        raise StorageRootRefused(
            "root_network_filesystem",
            f"The storage root is on a {fs_type} network filesystem. The live SQLite "
            "library and its indexes are not safe there; use a local disk.",
        )
    if fs_type.startswith("fuse"):
        LOGGER.warning("storage_root_uncertain_filesystem fs_type=%s", safe_fs_label(fs_type))


def safe_fs_label(fs_type: str) -> str:
    """A filesystem type reduced to a short, log-safe label."""
    cleaned = "".join(ch for ch in fs_type if ch.isalnum() or ch in "._-")
    return cleaned[:32] or "unknown"


def _nearest_existing(path: str) -> str:
    probe = os.path.abspath(path)
    while not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return os.path.realpath(probe)


def _startup_warnings(root_real: str) -> None:
    try:
        free = shutil.disk_usage(root_real).free
    except OSError:
        return
    if free < LOW_FREE_SPACE_WARNING_BYTES:
        LOGGER.warning("storage_root_low_free_space")


# --- the lease ----------------------------------------------------------------------


def _ensure_maintenance_dir(root_real: str) -> str:
    maintenance = os.path.join(root_real, MAINTENANCE_DIRNAME)
    st = _lstat(maintenance)
    if st is None:
        try:
            os.mkdir(maintenance, 0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise StorageRootRefused(
                "root_not_writable", "The storage root is not writable."
            ) from exc
        st = _lstat(maintenance)
        if st is None:
            raise StorageRootRefused("root_not_writable", "The storage root is not writable.")
    if is_link_or_reparse_point(st):
        raise StorageRootRefused(
            "root_contains_link",
            f"{maintenance} is a link. Links inside a storage root are not allowed.",
        )
    if not stat.S_ISDIR(st.st_mode):
        raise StorageRootRefused("maintenance_not_directory", f"{maintenance} is not a directory.")
    return maintenance


def _holder_hint(root_real: str) -> str:
    """Diagnostic text naming the last recorded holder. Never lock authority."""
    try:
        marker = read_marker(root_real)
    except StorageRootError:
        return ""
    holder = marker.document.get("active_process") if marker else None
    if not isinstance(holder, dict):
        return ""
    return (
        " Last recorded holder (diagnostic only): process {pid} on {host}, started {started}.".format(
            pid=holder.get("pid", "?"),
            host=holder.get("host", "?"),
            started=holder.get("started_at", "?"),
        )
    )


def acquire_root_lease(root_real: str) -> ExclusiveFileLock:
    """Take ``<root>/.prks-maintenance/root.lock`` without waiting."""
    maintenance = _ensure_maintenance_dir(root_real)
    try:
        return ExclusiveFileLock.acquire(os.path.join(maintenance, ROOT_LOCK_NAME), timeout=0.0)
    except LockBusy as exc:
        raise StorageRootInUse(
            "root_in_use",
            "This PRKS storage root is already open in another PRKS process. PRKS "
            "supports exactly one server process per storage root." + _holder_hint(root_real),
        ) from exc
    except LockUnavailable as exc:
        raise StorageRootRefused(
            exc.reason,
            "The storage root lock file .prks-maintenance/root.lock is not usable.",
        ) from exc


# --- entry point -------------------------------------------------------------------


class _RecordBuffer(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextlib.contextmanager
def _buffered_storage_logs(buffer: _RecordBuffer) -> Iterator[None]:
    """Hold ``prks.storage`` records in ``buffer`` instead of propagating them."""
    propagate = LOGGER.propagate
    LOGGER.addHandler(buffer)
    LOGGER.propagate = False
    try:
        yield
    finally:
        LOGGER.removeHandler(buffer)
        LOGGER.propagate = propagate


def open_storage_root(
    config: Any,
    *,
    expected_storage_root_id: Optional[str] = None,
    config_file_path: Optional[str] = None,
    distribution: Optional[str] = None,
    home: Optional[str] = None,
    now: Optional[datetime] = None,
    register: bool = True,
    defer_logs: bool = False,
) -> BoundRoot:
    """Validate, lease and mark ``config.root`` for this process.

    Raises a ``StorageRootError`` subclass with a stable ``reason`` on refusal,
    after releasing anything it acquired. With ``register`` (the default) the
    result becomes this process's bound root until released or exit.

    The process entry must open the root before logging is configured (the
    log file lives in it). With ``defer_logs`` the V9/V10 and durability
    warnings raised meanwhile are kept on the result and emitted by
    ``BoundRoot.log_binding()``; on refusal they are emitted at once.
    """
    if not defer_logs:
        return _open_storage_root(
            config,
            expected_storage_root_id=expected_storage_root_id,
            config_file_path=config_file_path,
            distribution=distribution,
            home=home,
            now=now,
            register=register,
        )
    buffer = _RecordBuffer()
    try:
        with _buffered_storage_logs(buffer):
            bound = _open_storage_root(
                config,
                expected_storage_root_id=expected_storage_root_id,
                config_file_path=config_file_path,
                distribution=distribution,
                home=home,
                now=now,
                register=register,
            )
    except BaseException:
        # A refusal goes out now, with the warnings that led up to it.
        for record in buffer.records:
            LOGGER.handle(record)
        raise
    bound.deferred_records = buffer.records
    return bound


def _open_storage_root(
    config: Any,
    *,
    expected_storage_root_id: Optional[str],
    config_file_path: Optional[str],
    distribution: Optional[str],
    home: Optional[str],
    now: Optional[datetime],
    register: bool,
) -> BoundRoot:
    testing = config.mode == "testing"
    root = config.root
    dist = _distribution.DISTRIBUTION if distribution is None else distribution
    db_filename = os.path.basename(config.db_path)
    if not _same_path(os.path.dirname(os.path.abspath(config.db_path)), os.path.abspath(root)):
        raise InvalidStorageRoot("database_outside_root", "The database must live directly in the storage root.")

    # The root may be a link (§7.3). It is resolved exactly once, here, and
    # every later step -- the read-only look, the lease, the marker, the
    # probes -- uses this snapshot, so a link retargeted mid-bind cannot split
    # validation across two directories.
    root_real = _check_placement(
        root,
        testing=testing,
        config_file_path=config_file_path,
        distribution=dist,
        home=home,
    )

    _check_log_file_not_operational(
        getattr(config, "log_file", None), root_real, config_file_path=config_file_path
    )

    check_filesystem_type(_nearest_existing(root_real))

    _look_before_leasing(
        config,
        root=root,
        root_real=root_real,
        db_filename=db_filename,
        expected_storage_root_id=expected_storage_root_id,
    )

    lease = acquire_root_lease(root_real)
    try:
        bound = _open_under_lease(
            config,
            root=root,
            root_real=root_real,
            lease=lease,
            db_filename=db_filename,
            expected_storage_root_id=expected_storage_root_id,
            now=now,
        )
    except BaseException:
        lease.release()
        raise
    if register:
        _register(bound)
    return bound


def _look_before_leasing(
    config: Any,
    *,
    root: str,
    root_real: str,
    db_filename: str,
    expected_storage_root_id: Optional[str],
) -> None:
    """Read-only look (and first-run creation) before the lease is taken.

    Foreign or non-bindable directories are refused before anything is created
    in them. A root the bootstrap file selects was chosen earlier and must
    still be there: an absent or empty directory is far more likely an
    unmounted disk than a wish for a new library, so it is never created or
    minted here (§7.1). Choosing a genuinely new root is a separate command
    (Phase D).
    """
    may_create = getattr(config, "root_source", None) != SOURCE_CONFIG_FILE
    if os.path.isdir(root_real):
        marker = read_marker(root_real)
        if marker is not None:
            _refuse_unless_bindable(marker, expected_storage_root_id)
            return
        kind = classify_unmarked_root(
            root_real, db_filename=db_filename, **component_root_names(config)
        )
        if kind == UNMARKED_FOREIGN:
            raise _foreign_root_error(root)
        if kind == UNMARKED_EMPTY and not may_create:
            raise _missing_selected_root(root)
        return
    if not may_create:
        raise _missing_selected_root(root)
    _create_new_root(root, root_real)


def _create_new_root(root: str, root_real: str) -> None:
    """Create an absent root (owner-only). V2 already proved the spelling is no link."""
    try:
        os.mkdir(root_real, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise InvalidStorageRoot(
            "root_not_creatable", f"The storage root {root} could not be created."
        ) from exc
    if not fsync_directory(os.path.dirname(root_real)):
        LOGGER.warning("storage_root_parent_sync_failed")
    if not os.path.isdir(root_real) or os.path.islink(root_real):
        raise InvalidStorageRoot("root_not_creatable", f"The storage root {root} could not be created.")


def _register(bound: BoundRoot) -> None:
    """Make ``bound`` this process's root, releasing any earlier registration."""
    global _ACTIVE
    with _ACTIVE_GUARD:
        previous = _ACTIVE
        _ACTIVE = bound
    if previous is not None and previous is not bound:
        previous.lease.release()


def _refuse_unless_bindable(marker: RootMarker, expected: Optional[str]) -> None:
    refusal = binding_refusal(marker)
    if refusal is not None:
        raise refusal
    if expected is not None and marker.storage_root_id != expected:
        raise StorageRootRefused(
            "root_foreign",
            "This storage root belongs to a different PRKS library than the one expected.",
        )


def _open_under_lease(
    config: Any,
    *,
    root: str,
    root_real: str,
    lease: ExclusiveFileLock,
    db_filename: str,
    expected_storage_root_id: Optional[str],
    now: Optional[datetime],
) -> BoundRoot:
    # Authoritative re-read: only a lease holder decides and writes.
    marker = read_marker(root_real)
    created = adopted = False
    if marker is not None:
        _refuse_unless_bindable(marker, expected_storage_root_id)
        document: dict[str, Any] = dict(marker.document)
    else:
        kind = classify_unmarked_root(
            root_real, db_filename=db_filename, **component_root_names(config)
        )
        if kind == UNMARKED_FOREIGN:
            raise _foreign_root_error(root)
        if expected_storage_root_id is not None:
            raise StorageRootRefused(
                "root_foreign",
                "This storage root has no prks-root.json, so it is not the library expected.",
            )
        if kind == UNMARKED_EMPTY and getattr(config, "root_source", None) == SOURCE_CONFIG_FILE:
            raise _missing_selected_root(root)
        document = new_marker_document(now=now)
        created = kind == UNMARKED_EMPTY
        adopted = kind == UNMARKED_PRKS

    fs_type = (detect_filesystem_type(root_real) or "").lower()
    if fs_type in OVERLAY_FILESYSTEM_TYPES:
        LOGGER.warning("storage_root_overlay_file_devices_unchecked")
    _check_no_mount_points(root_real)
    _check_tree(root_real, file_devices=fs_type not in OVERLAY_FILESYSTEM_TYPES)

    maintenance = os.path.join(root_real, MAINTENANCE_DIRNAME)
    device = os.stat(root_real).st_dev
    cached = document.get("filesystem_probe")
    need_capabilities = not (isinstance(cached, dict) and cached.get("device") == device)
    directory_fsync = run_preflight(maintenance, capabilities=need_capabilities)
    if need_capabilities:
        document["filesystem_probe"] = {
            "device": device,
            "directory_fsync": bool(directory_fsync),
            "probed_at": utc_timestamp(now),
        }
    document["active_process"] = {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "started_at": utc_timestamp(now),
    }

    try:
        durable = write_marker(root_real, document, lease=lease)
    except OSError as exc:
        raise StorageRootRefused(
            "marker_write_failed", "The storage root marker prks-root.json could not be written."
        ) from exc
    if not durable:
        if marker is None:
            # A new identity that may not survive a crash is not an identity.
            raise StorageRootRefused(
                "marker_not_durable",
                "The storage root marker prks-root.json could not be made crash-safe.",
            )
        LOGGER.warning("storage_root_marker_sync_failed")
    written = read_marker(root_real)
    if written is None:  # pragma: no cover - defensive
        raise StorageRootRefused("marker_write_failed", "The storage root marker vanished after writing.")
    _startup_warnings(root_real)
    return BoundRoot(
        root=root,
        root_real=root_real,
        source=getattr(config, "root_source", None),
        marker=written,
        lease=lease,
        created=created,
        adopted=adopted,
    )
