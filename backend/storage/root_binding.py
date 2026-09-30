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

import logging
import os
import shutil
import socket
import stat
import threading
from dataclasses import dataclass
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
from backend.storage.preflight import (
    PREFLIGHT_DIRNAME,
    preflight_is_scaffold,
    run_preflight,
)
from backend.storage.root_marker import (
    MARKER_FILENAME,
    RootMarker,
    binding_refusal,
    new_marker_document,
    read_marker,
    utc_timestamp,
    write_marker,
)

LOGGER = logging.getLogger("prks.storage")

MAINTENANCE_DIRNAME = ".prks-maintenance"
ROOT_LOCK_NAME = "root.lock"
# Hidden OS metadata that does not make a directory "non-empty" for V3.
OS_METADATA_NAMES = frozenset({".DS_Store", "desktop.ini", "Thumbs.db", "lost+found"})
# V10 (startup): warn below this much free space. Choose/relocate enforce a
# computed requirement in a later phase.
LOW_FREE_SPACE_WARNING_BYTES = 1024 * 1024 * 1024
# V9 (startup, warn only): filesystem types known to be network mounts, where
# SQLite locking and WAL are unsafe for a live library (§7.4).
NETWORK_FILESYSTEM_TYPES = frozenset(
    {
        "nfs",
        "nfs4",
        "cifs",
        "smbfs",
        "smb3",
        "afs",
        "ncpfs",
        "davfs",
        "fuse.sshfs",
        "fuse.rclone",
        "fuse.s3fs",
    }
)
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

    @property
    def storage_root_id(self) -> str:
        return self.marker.storage_root_id

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

    # V12: testing never touches /data or <repo>/data; production never binds
    # the testing tree.
    if testing:
        try:
            paths.assert_safe_testing_path(root, testing=True, what="storage root")
        except RuntimeError as exc:
            raise InvalidStorageRoot("testing_unsafe_root", str(exc)) from exc
    else:
        testing_tree = os.path.realpath(os.path.join(paths.repo_root(), "data_testing"))
        if _is_within(root_real, testing_tree):
            raise InvalidStorageRoot(
                "production_testing_root",
                "A non-testing PRKS run refuses the repository data_testing/ tree. "
                "Use --testing, or select a different storage root.",
            )

    # V11: never the filesystem root or the home directory itself.
    if os.path.dirname(root_real) == root_real:
        raise InvalidStorageRoot("root_is_filesystem_root", "The storage root cannot be a filesystem root.")
    home_dir = home if home is not None else os.path.expanduser("~")
    if home_dir and _same_path(root_real, os.path.realpath(home_dir)):
        raise InvalidStorageRoot("root_is_home", "The storage root cannot be your home directory itself.")

    # V11: never inside another root's maintenance area, never nested in or
    # around another PRKS root.
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

    # V11: the bootstrap file lives outside every root (the root may still sit
    # inside the configuration *directory*, §6).
    if config_file_path and _is_within(os.path.realpath(config_file_path), root_real):
        raise InvalidStorageRoot(
            "root_contains_config",
            "The storage root cannot contain the PRKS bootstrap configuration file.",
        )

    # V11: the install directory. A source checkout keeps its declared
    # development defaults (and, for existing self-hosted setups, other
    # locations inside the checkout); a packaged build refuses all of them.
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

    # V2: an existing root is a directory; a new one needs an existing parent.
    if os.path.lexists(root):
        if not os.path.isdir(root):
            raise InvalidStorageRoot("root_not_directory", f"The storage root {root} is not a directory.")
    elif not os.path.isdir(os.path.dirname(os.path.abspath(root))):
        raise InvalidStorageRoot(
            "root_parent_missing",
            f"The parent directory of the storage root {root} does not exist. "
            "PRKS does not create missing parents; check that the disk or mount is available.",
        )
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


def classify_unmarked_root(root: str, *, db_filename: str) -> str:
    """V3 for a directory without a marker: ``empty``, ``prks`` or ``foreign``.

    ``prks`` (adopt, §7.1): it holds the library database, a ``pdfs/`` entry,
    or maintenance state beyond a lock/preflight scaffold (for example an
    interrupted restore's journal, whose recovery must still be able to run).
    ``empty``: nothing but OS metadata, a preflight/lock scaffold, or a
    leftover marker-write temporary. Anything else is ``foreign``.
    """
    try:
        names = os.listdir(root)
    except FileNotFoundError:
        return UNMARKED_EMPTY
    except OSError as exc:
        raise StorageRootRefused("root_unreadable", f"The storage root {root} cannot be read.") from exc
    looks_like_prks = False
    foreign = False
    for name in names:
        if name in OS_METADATA_NAMES:
            continue
        if name.startswith(".prks-write-") and name.endswith(".tmp"):
            continue
        if name == MAINTENANCE_DIRNAME:
            if not _maintenance_is_scaffold(os.path.join(root, name)):
                looks_like_prks = True
            continue
        if name in (db_filename, "pdfs"):
            looks_like_prks = True
            continue
        foreign = True
    if looks_like_prks:
        return UNMARKED_PRKS
    return UNMARKED_FOREIGN if foreign else UNMARKED_EMPTY


def _foreign_root_error(root: str) -> StorageRootRefused:
    return StorageRootRefused(
        "root_foreign",
        f"The storage root {root} is not empty and does not look like a PRKS library "
        "(no database, no pdfs/ directory, no prks-root.json). PRKS will not create "
        "a new library there. Choose an empty directory or an existing PRKS library.",
    )


# --- V13, V7: component checks under the lease ------------------------------------

_INBOX = "inbox"
_LOG = "log"


def _component_entries(config: Any) -> Iterator[tuple[str, str]]:
    """``(kind, path)`` for every StorageConfig component that may live under the root."""
    db = config.db_path
    for path in (db, db + "-wal", db + "-shm", db + "-journal"):
        yield ("database", path)
    yield ("pdfs", config.pdfs_dir)
    yield ("people", config.people_dir)
    yield ("thumbs", config.thumbs_dir)
    for index in (config.index_db_path, config.research_index_db_path):
        for path in (index, index + "-wal", index + "-shm", index + "-journal"):
            yield ("index", path)
    yield (_INBOX, config.processing_dir)
    yield (_LOG, config.log_file)


def _check_links_and_devices(config: Any, root: str, root_real: str) -> None:
    """V13 (nothing inside the root is a link) and V7 (one filesystem).

    The processing inbox and the error log are warned about rather than
    refused: both may legitimately live elsewhere through their overrides, and
    a symlinked or separately mounted inbox is an existing deployment shape
    that Phase A must keep starting.
    """
    root_dev = os.stat(root_real).st_dev
    checked: set[str] = set()
    for kind, path in _component_entries(config):
        rel = _relative_components(path, root)
        if rel is None:
            continue
        current = root_real
        for part in rel.split(os.sep):
            current = os.path.join(current, part)
            if current in checked:
                continue
            checked.add(current)
            st = _lstat(current)
            if st is None:
                break
            if is_link_or_reparse_point(st):
                if kind in (_INBOX, _LOG):
                    LOGGER.warning("storage_root_component_is_link component=%s", kind)
                    break
                raise StorageRootRefused(
                    "root_contains_link",
                    f"{current} is a link. Links inside a storage root are not allowed; "
                    "the storage root itself may be a link.",
                )
            if stat.S_ISDIR(st.st_mode) and st.st_dev != root_dev:
                if kind in (_INBOX, _LOG):
                    LOGGER.warning("storage_root_component_other_filesystem component=%s", kind)
                    break
                raise StorageRootRefused(
                    "root_spans_filesystems",
                    f"{current} is on a different filesystem than the storage root. Every "
                    "PRKS component under the root must be on one filesystem.",
                )


# --- V9, V10 warnings ---------------------------------------------------------------


def _unescape_mount_field(value: str) -> str:
    return (
        value.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\")
    )


def detect_filesystem_type(path: str, *, mounts_file: str = "/proc/mounts") -> Optional[str]:
    """Filesystem type of ``path`` from ``/proc/mounts`` (Linux); None when unknown."""
    try:
        with open(mounts_file, encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return None
    best: Optional[tuple[int, str]] = None
    for line in lines:
        fields = line.split()
        if len(fields) < 3:
            continue
        mountpoint = _unescape_mount_field(fields[1])
        if _is_within(path, mountpoint):
            if best is None or len(mountpoint) >= best[0]:
                best = (len(mountpoint), fields[2])
    return best[1] if best else None


def _startup_warnings(root_real: str) -> None:
    fs_type = detect_filesystem_type(root_real)
    if fs_type is not None and fs_type.lower() in NETWORK_FILESYSTEM_TYPES:
        LOGGER.warning("storage_root_network_filesystem fs_type=%s", fs_type.lower())
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


def open_storage_root(
    config: Any,
    *,
    expected_storage_root_id: Optional[str] = None,
    config_file_path: Optional[str] = None,
    distribution: Optional[str] = None,
    home: Optional[str] = None,
    now: Optional[datetime] = None,
    register: bool = True,
) -> BoundRoot:
    """Validate, lease and mark ``config.root`` for this process.

    Raises a ``StorageRootError`` subclass with a stable ``reason`` on refusal,
    after releasing anything it acquired. With ``register`` (the default) the
    result becomes this process's bound root until released or exit.
    """
    testing = config.mode == "testing"
    root = config.root
    dist = _distribution.DISTRIBUTION if distribution is None else distribution
    db_filename = os.path.basename(config.db_path)
    if not _same_path(os.path.dirname(os.path.abspath(config.db_path)), os.path.abspath(root)):
        raise InvalidStorageRoot("database_outside_root", "The database must live directly in the storage root.")

    _check_placement(
        root,
        testing=testing,
        config_file_path=config_file_path,
        distribution=dist,
        home=home,
    )

    # Read-only look first: refuse foreign or non-bindable directories before
    # creating anything in them.
    if os.path.isdir(root):
        marker = read_marker(root)
        if marker is not None:
            _refuse_unless_bindable(marker, expected_storage_root_id)
        elif classify_unmarked_root(root, db_filename=db_filename) == UNMARKED_FOREIGN:
            raise _foreign_root_error(root)
    else:
        try:
            os.mkdir(root, 0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise InvalidStorageRoot(
                "root_not_creatable", f"The storage root {root} could not be created."
            ) from exc
        if not fsync_directory(os.path.dirname(os.path.abspath(root))):
            LOGGER.warning("storage_root_parent_sync_failed")

    root_real = os.path.realpath(root)
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
        global _ACTIVE
        with _ACTIVE_GUARD:
            previous = _ACTIVE
            _ACTIVE = bound
        if previous is not None and previous is not bound:
            previous.lease.release()
    LOGGER.info(
        "storage_root_bound source=%s created=%s adopted=%s",
        getattr(config, "root_source", None) or "direct",
        "true" if bound.created else "false",
        "true" if bound.adopted else "false",
    )
    return bound


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
        kind = classify_unmarked_root(root_real, db_filename=db_filename)
        if kind == UNMARKED_FOREIGN:
            raise _foreign_root_error(root)
        if expected_storage_root_id is not None:
            raise StorageRootRefused(
                "root_foreign",
                "This storage root has no prks-root.json, so it is not the library expected.",
            )
        document = new_marker_document(now=now)
        created = kind == UNMARKED_EMPTY
        adopted = kind == UNMARKED_PRKS

    _check_links_and_devices(config, root, root_real)

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
        durable = write_marker(root_real, document)
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
