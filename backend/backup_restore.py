"""First-party local backup and restore for the PRKS library.

Concurrency: PRKS serves HTTP with a threaded stdlib server. Ordinary reads may
overlap. Canonical mutations are serialized. Backup holds a maintenance scope
for the complete snapshot and archive so mutations cannot change the DB or
managed files while packing; ordinary reads may continue. Restore is exclusive
against all active-storage access, including reads, mutations, and backup, and
remains held across replacement and bind_storage(). SQLite connections stay
per-operation; there is no connection pool.

Progress and cancel use the same request: POST /api/backups/progress streams
NDJSON while packing. Aborting that connection cancels packing. Do not run
backup packing on a worker thread that skips the backup scope.
GET /api/backups/download requires a one-time token and never creates a backup.

Do not copy the live SQLite file while it may be in use. Snapshots use
sqlite3.Connection.backup. Never extract an unvalidated backup ZIP in bulk.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import stat
import threading
import time
import zipfile
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Callable, Iterator, Optional

from backend.db_manager import PRKS_SCHEMA_VERSION
from backend.log_safety import safe_error_type
from backend.performance import clock_ns, record_span, span as perf_span
from backend.storage import paths
from backend.storage.config import StorageConfig

LOGGER = logging.getLogger("prks.backup")

FORMAT_ID = "prks-backup"
FORMAT_VERSION = 1
BACKUP_EXTENSION = ".prks-backup"
MAINTENANCE_DIRNAME = ".prks-maintenance"
JOURNAL_FILENAME = "restore-journal.json"
ARCHIVE_DB_PATH = "data/prks_data.db"
MANIFEST_NAME = "manifest.json"
CONFIRM_RESTORE = "RESTORE"

IO_CHUNK_SIZE = 64 * 1024
STAGING_TTL_SECONDS = 60 * 60
READY_BACKUP_TTL_SECONDS = 60 * 60
_PROGRESS_EMIT_INTERVAL = 0.15
_PROGRESS_PHASES = frozenset(
    {"snapshot", "archiving", "verifying", "ready", "cancelled", "failed"}
)
DEFAULT_MAX_UPLOAD_BYTES = 64 * 1024 * 1024 * 1024
DEFAULT_MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024 * 1024
DEFAULT_MAX_ZIP_ENTRIES = 500_000
DEFAULT_MAX_COMPRESSION_RATIO = 200
DISK_MARGIN_BYTES = 64 * 1024 * 1024

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_STORED_SUFFIXES = frozenset({".pdf", ".jpg", ".jpeg", ".png", ".webp"})
_UNIX_IFMT = 0o170000
_UNIX_IFLNK = 0o120000
_UNIX_IFREG = 0o100000
_UNIX_IFDIR = 0o040000
_UNIX_SPECIAL = frozenset({0o010000, 0o020000, 0o060000, 0o140000})  # fifo, chr, blk, sock
_REQUIRED_TABLES = (
    "schema_version",
    "works",
    "persons",
    "annotations",
    "app_settings",
)
_NON_PATH_STORAGE_FIELDS = frozenset({"mode", "processing_fallback_allowed"})
_ALLOWED_PAYLOAD_PREFIXES = (
    "files/pdfs/",
    "files/people/",
    "files/for_processing/",
)
_PHASES = (
    "prepared",
    "moving_old",
    "installing_new",
    "canonical_installed",
    "committed",
)

RebindFn = Callable[[StorageConfig], StorageConfig]


@dataclass(frozen=True)
class BackupStorageInventory:
    canonical: tuple[str, ...]
    derived: tuple[str, ...]
    operational: tuple[str, ...]
    conditional: tuple[str, ...]
    container: tuple[str, ...]


def backup_storage_inventory() -> BackupStorageInventory:
    return BackupStorageInventory(
        canonical=("db_path", "pdfs_dir", "people_dir"),
        derived=("thumbs_dir", "index_db_path", "research_index_db_path"),
        operational=("log_file",),
        conditional=("processing_dir",),
        container=("root", "configured_root"),
    )


def classified_storage_field_names() -> frozenset[str]:
    inv = backup_storage_inventory()
    return frozenset(
        inv.canonical + inv.derived + inv.operational + inv.conditional + inv.container
    )


def storage_config_path_field_names() -> frozenset[str]:
    return frozenset(f.name for f in fields(StorageConfig)) - _NON_PATH_STORAGE_FIELDS


class BackupError(Exception):
    def __init__(self, reason: str, message: str, *, http_status: int = 500):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.http_status = http_status


class RestoreError(Exception):
    def __init__(self, reason: str, message: str, *, http_status: int = 400):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.http_status = http_status


class RestoreCrash(Exception):
    """Test-only simulated crash. apply_restore must not roll back."""


@dataclass
class BackupResult:
    archive_path: str
    filename: str
    verified: bool
    manifest: dict[str, Any]
    warnings: list[str]
    summary: dict[str, Any]


@dataclass
class StagingResult:
    token: str
    verified: bool
    summary: dict[str, Any]
    warnings: list[str]
    manifest: dict[str, Any]


_ready_lock = threading.Lock()
_ready_backups: dict[str, dict[str, Any]] = {}


class _BackupProgress:
    def __init__(
        self,
        callback: Optional[Callable[[dict[str, Any]], None]],
        cancel_event: Optional[threading.Event],
    ) -> None:
        self.callback = callback
        self.cancel_event = cancel_event
        self.phase = "snapshot"
        self.files_done = 0
        self.files_total = 0
        self.payload_bytes = 1
        self.archive_bytes = 0
        self.verify_bytes = 0
        self._last_emit = 0.0
        self._last_percent = -1

    def check(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise BackupError("cancelled", "Backup was cancelled.", http_status=400)

    def set_work(self, payload_bytes: int, files_total: int) -> None:
        self.payload_bytes = max(1, int(payload_bytes))
        self.files_total = max(0, int(files_total))

    def percent(self) -> int:
        if self.phase == "snapshot":
            return 2
        total = self.payload_bytes * 2
        done = self.archive_bytes + self.verify_bytes
        pct = int((100 * done) / total) if total else 100
        if self.phase == "ready":
            return 100
        return max(2, min(99, pct))

    def emit(self, *, phase: Optional[str] = None, force: bool = False) -> None:
        self.check()
        if phase:
            self.phase = phase
        now = time.monotonic()
        pct = self.percent()
        if (
            not force
            and phase is None
            and now - self._last_emit < _PROGRESS_EMIT_INTERVAL
            and pct == self._last_percent
        ):
            return
        self._last_emit = now
        self._last_percent = pct
        payload = {
            "phase": self.phase,
            "percent": pct,
            "files_done": self.files_done,
            "files_total": self.files_total,
            "bytes_done": self.archive_bytes + self.verify_bytes,
            "bytes_total": self.payload_bytes * 2,
        }
        if self.callback is not None:
            self.callback(payload)

    def add_archive_bytes(self, n: int) -> None:
        self.archive_bytes += max(0, int(n))
        self.emit()

    def add_verify_bytes(self, n: int) -> None:
        self.verify_bytes += max(0, int(n))
        self.emit()

    def file_done(self) -> None:
        self.files_done += 1
        self.emit(force=True)


def public_progress_payload(payload: dict[str, Any]) -> dict[str, Any]:
    phase = str(payload.get("phase") or "")
    if phase not in _PROGRESS_PHASES:
        phase = "archiving"
    out: dict[str, Any] = {
        "phase": phase,
        "percent": max(0, min(100, int(payload.get("percent") or 0))),
        "files_done": max(0, int(payload.get("files_done") or 0)),
        "files_total": max(0, int(payload.get("files_total") or 0)),
        "bytes_done": max(0, int(payload.get("bytes_done") or 0)),
        "bytes_total": max(0, int(payload.get("bytes_total") or 0)),
    }
    token = payload.get("token")
    if isinstance(token, str) and _TOKEN_RE.fullmatch(token):
        out["token"] = token
    filename = payload.get("filename")
    if isinstance(filename, str) and filename.startswith("prks-backup-") and filename.endswith(BACKUP_EXTENSION):
        out["filename"] = filename
    warnings = payload.get("warnings")
    if isinstance(warnings, list):
        out["warnings"] = [str(item) for item in warnings if isinstance(item, str)]
    error = payload.get("error")
    if isinstance(error, str) and error:
        out["error"] = error
    reason = payload.get("reason")
    if isinstance(reason, str) and _SAFE_REASON_RE.fullmatch(reason):
        out["reason"] = reason
    return out


_SAFE_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def stash_ready_backup(result: BackupResult) -> str:
    token = secrets.token_urlsafe(24)
    with _ready_lock:
        _expire_ready_backups_unlocked()
        _ready_backups[token] = {
            "path": result.archive_path,
            "filename": result.filename,
            "warnings": list(result.warnings),
            "created_unix": time.time(),
        }
    return token


def take_ready_backup(token: str) -> tuple[str, str, list[str]]:
    if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
        raise BackupError("unknown_token", "Backup is not available.", http_status=404)
    with _ready_lock:
        item = _ready_backups.pop(token, None)
    if not item:
        raise BackupError("unknown_token", "Backup is not available.", http_status=404)
    path = str(item.get("path") or "")
    filename = str(item.get("filename") or "")
    warnings = [str(w) for w in (item.get("warnings") or []) if isinstance(w, str)]
    if not path or not os.path.isfile(path):
        raise BackupError("unknown_token", "Backup is not available.", http_status=404)
    return path, filename, warnings


def _expire_ready_backups_unlocked(*, now: Optional[float] = None) -> None:
    current = time.time() if now is None else now
    expired = []
    for token, item in _ready_backups.items():
        created = float(item.get("created_unix") or 0)
        if created <= 0 or (current - created) >= READY_BACKUP_TTL_SECONDS:
            expired.append(token)
    for token in expired:
        item = _ready_backups.pop(token, None)
        if item and item.get("path"):
            _safe_remove(str(item["path"]))


def cleanup_expired_backup_jobs(config: StorageConfig, *, now: Optional[float] = None) -> None:
    _assert_testing_safe(config)
    with _ready_lock:
        _expire_ready_backups_unlocked(now=now)


def run_backup_with_progress(
    config: StorageConfig,
    write_line: Callable[[dict[str, Any]], None],
) -> None:
    """Pack a backup while emitting privacy-safe progress dicts. Client abort cancels."""
    _assert_testing_safe(config)
    cancel_event = threading.Event()

    def emit(payload: dict[str, Any]) -> None:
        if cancel_event.is_set():
            raise BackupError("cancelled", "Backup was cancelled.", http_status=400)
        try:
            write_line(public_progress_payload(payload))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError) as exc:
            cancel_event.set()
            raise BackupError("cancelled", "Backup was cancelled.", http_status=400) from exc

    try:
        result = create_backup(config, progress=emit, cancel_event=cancel_event)
        token = stash_ready_backup(result)
        emit(
            {
                "phase": "ready",
                "percent": 100,
                "files_done": result.summary.get("pdf_files") or 0,
                "files_total": result.summary.get("pdf_files") or 0,
                "bytes_done": 1,
                "bytes_total": 1,
                "token": token,
                "filename": result.filename,
                "warnings": list(result.warnings),
            }
        )
    except BackupError as exc:
        if cancel_event.is_set() or exc.reason == "cancelled":
            try:
                write_line(public_progress_payload({"phase": "cancelled", "percent": 0}))
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                return
            return
        try:
            write_line(
                public_progress_payload(
                    {
                        "phase": "failed",
                        "percent": 0,
                        "error": exc.message,
                        "reason": exc.reason,
                    }
                )
            )
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return
    except Exception as exc:
        LOGGER.error("backup_failed reason=internal error_type=%s", safe_error_type(exc))
        try:
            write_line(
                public_progress_payload(
                    {
                        "phase": "failed",
                        "percent": 0,
                        "error": "Backup could not be created.",
                        "reason": "internal",
                    }
                )
            )
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return


def backup_max_upload_bytes() -> int:
    raw = (os.environ.get("PRKS_BACKUP_MAX_UPLOAD_BYTES") or "").strip()
    if not raw:
        return DEFAULT_MAX_UPLOAD_BYTES
    try:
        value = int(raw, 10)
    except ValueError:
        return DEFAULT_MAX_UPLOAD_BYTES
    if value < 1:
        return DEFAULT_MAX_UPLOAD_BYTES
    return value


def iter_file_chunks(
    fileobj,
    *,
    chunk_size: int = IO_CHUNK_SIZE,
    cancel_event: Optional[threading.Event] = None,
) -> Iterator[bytes]:
    size = int(chunk_size)
    if size < 1:
        size = IO_CHUNK_SIZE
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise BackupError("cancelled", "Backup was cancelled.", http_status=400)
        chunk = fileobj.read(size)
        if not chunk:
            break
        yield chunk


def hash_file(path: str, *, chunk_size: int = IO_CHUNK_SIZE) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    with open(path, "rb") as handle:
        for chunk in iter_file_chunks(handle, chunk_size=chunk_size):
            digest.update(chunk)
            total += len(chunk)
    return total, digest.hexdigest()


def hash_and_copy(src_file, dst_file, *, chunk_size: int = IO_CHUNK_SIZE) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    for chunk in iter_file_chunks(src_file, chunk_size=chunk_size):
        digest.update(chunk)
        dst_file.write(chunk)
        total += len(chunk)
    return total, digest.hexdigest()


def maintenance_root(config: StorageConfig) -> str:
    return os.path.join(config.root, MAINTENANCE_DIRNAME)


def journal_path(config: StorageConfig) -> str:
    return os.path.join(maintenance_root(config), JOURNAL_FILENAME)


# The three destructive scopes restore cleanup is allowed to touch. Each is a
# fixed name under the maintenance root, never a caller-supplied string.
_STAGING_SUBROOT: tuple[str, ...] = ("restore-staging",)
_ROLLBACK_SUBROOT: tuple[str, ...] = ("rollback",)
_JOURNAL_SUBROOT: tuple[str, ...] = ()


def _maintenance_subroot(config: StorageConfig, *names: str) -> str:
    """Expected path of a maintenance subroot, anchored to the storage root.

    ``config.root`` is operator-supplied and may legitimately be a symlink, so
    it is resolved once and everything below it is expected to be real. The
    result is what a maintenance path *must* resolve to; it is not evidence
    that the directory on disk actually is that.
    """
    return _maintenance_subroot_from(_resolved_storage_root(config), *names)


def _resolved_storage_root(config: StorageConfig) -> str:
    """One canonical snapshot of the storage root.

    ``config.root`` is operator-supplied and may legitimately be a symlink. Each
    destructive operation resolves it exactly once and reuses the result, so a
    link or junction retargeted mid-operation cannot make authorization and
    removal refer to different trees.
    """
    try:
        return os.path.realpath(config.root)
    except OSError as exc:
        raise ValueError("storage root could not be resolved") from exc


def _maintenance_subroot_from(root_real: str, *names: str) -> str:
    """Expected maintenance path beneath an already-resolved storage root."""
    return os.path.join(root_real, MAINTENANCE_DIRNAME, *names)


def _assert_testing_safe(config: StorageConfig) -> None:
    testing = config.mode == "testing"
    paths.assert_safe_testing_path(config.root, testing=testing, what="PRKS_STORAGE")
    for path, what in (
        (config.db_path, "db_path"),
        (config.pdfs_dir, "pdfs_dir"),
        (config.people_dir, "people_dir"),
        (config.processing_dir, "processing_dir"),
        (config.thumbs_dir, "thumbs_dir"),
        (config.index_db_path, "index_db_path"),
        (config.research_index_db_path, "research_index_db_path"),
        (maintenance_root(config), "maintenance"),
    ):
        paths.assert_safe_testing_path(path, testing=testing, what=what)


def _chmod_dir(path: str) -> None:
    if os.name != "posix":
        return
    try:
        os.chmod(path, 0o700)
    except OSError:
        return


def _chmod_file(path: str) -> None:
    if os.name != "posix":
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        return


def _mkdir_owner(path: str) -> None:
    os.makedirs(path, mode=0o700, exist_ok=True)
    _chmod_dir(path)


def _ensure_maintenance_dirs(config: StorageConfig) -> str:
    _mkdir_owner(config.root)
    root = _maintenance_subroot(config)
    _mkdir_owner(root)
    _mkdir_owner(os.path.join(root, "backup"))
    _mkdir_owner(os.path.join(root, *_STAGING_SUBROOT))
    _mkdir_owner(os.path.join(root, *_ROLLBACK_SUBROOT))
    return root


def _path_is_under(child: str, parent: str) -> bool:
    """Return whether the real child is the parent or lies beneath it."""
    try:
        parent_real = os.path.realpath(parent)
        child_real = os.path.realpath(child)
        return os.path.commonpath((parent_real, child_real)) == parent_real
    except (OSError, ValueError):
        return False


def processing_is_under_storage(config: StorageConfig) -> bool:
    try:
        return _path_is_under(config.processing_dir, config.root)
    except OSError:
        return False


def _free_bytes(path: str) -> int:
    probe = path
    while probe and not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    if not probe or not os.path.isdir(probe):
        probe = os.path.abspath(".")
    return int(shutil.disk_usage(probe).free)


def _entry_compress_type(relpath: str) -> int:
    ext = os.path.splitext(relpath)[1].lower()
    if ext in _STORED_SUFFIXES:
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso_z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _backup_filename(dt: datetime) -> str:
    stamp = dt.strftime("%Y%m%dT%H%M%SZ")
    return f"prks-backup-{stamp}{BACKUP_EXTENSION}"


def _safe_remove(path: str) -> None:
    """Remove one already-authorized path without following directory symlinks."""
    if not os.path.lexists(path):
        return
    if os.path.islink(path) or os.path.isfile(path):
        os.unlink(path)
        return
    for dirpath, dirnames, filenames in os.walk(path, topdown=False, followlinks=False):
        for name in filenames:
            os.unlink(os.path.join(dirpath, name))
        for name in dirnames:
            child = os.path.join(dirpath, name)
            if os.path.islink(child):
                os.unlink(child)
            elif os.path.isdir(child):
                os.rmdir(child)
            else:
                os.unlink(child)
    os.rmdir(path)


# Descriptor-relative, no-follow removal needs POSIX-only open flags and fd
# support. On a platform without them (native Windows) every reference below
# would raise AttributeError, so the whole descriptor implementation is gated
# and the portable path-based fallback is used instead.
_SUPPORTS_DIR_FD = (
    os.unlink in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.open in os.supports_dir_fd
    and os.listdir in os.supports_fd
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and shutil.rmtree.avoids_symlink_attacks
)


def _is_directory_reparse_point(path: str) -> bool:
    """A directory reparse point that ``os.path.islink()`` does not report.

    NTFS junctions are the case that matters: ``islink()`` is False for them and
    ``os.walk(followlinks=False)`` does not treat them as links either, so a
    junction planted at a maintenance subroot would otherwise pass verification
    and let path-based cleanup reach its target. ``os.path.isjunction()`` is
    always False on POSIX, so this costs nothing there.
    """
    checker = getattr(os.path, "isjunction", None)
    if checker is None:
        return False
    try:
        return bool(checker(path))
    except OSError:
        return False


def _verified_maintenance_subroot(config: StorageConfig, *names: str) -> Optional[str]:
    """Portable proof that a maintenance subroot is a real directory.

    Returns its path, or None when it does not exist yet. Raises ValueError when
    something is there that is not a real directory -- notably a symlink, which
    a containment check cannot catch because it resolves both operands, so a
    link planted at ``restore-staging`` would silently move the whole cleanup
    scope outside the library. Directory reparse points are rejected too; see
    ``_is_directory_reparse_point()``.
    """
    return _verified_maintenance_subroot_from(_resolved_storage_root(config), *names)


def _verified_maintenance_subroot_from(
    root_real: str, *names: str
) -> Optional[str]:
    """``_verified_maintenance_subroot()`` against a caller's root snapshot."""
    current = root_real
    for name in (MAINTENANCE_DIRNAME, *names):
        current = os.path.join(current, name)
        if not os.path.lexists(current):
            return None
        if (
            os.path.islink(current)
            or _is_directory_reparse_point(current)
            or not os.path.isdir(current)
        ):
            raise ValueError("maintenance subroot is not a real directory")
    return current


def _open_maintenance_subroot(config: StorageConfig, *names: str) -> Optional[int]:
    """Descriptor for a maintenance subroot, reached from the storage root."""
    return _open_maintenance_subroot_from(_resolved_storage_root(config), *names)


def _storage_root_identity(root_real: str) -> Optional[os.stat_result]:
    """Identity of the canonical storage root at snapshot time, if it exists."""
    try:
        return os.stat(root_real)
    except OSError:
        return None


def _storage_root_keeps_identity(
    root_real: str, expected: Optional[os.stat_result]
) -> bool:
    """Whether ``root_real`` still names the directory captured as ``expected``.

    The portable branch has no descriptor to bind to, so this is the only way it
    can tell that the pathname it is about to delete through still refers to the
    directory the caller authorized. An absent expectation is not a pass: a
    destructive path that cannot confirm what it is acting on must refuse.
    """
    if expected is None:
        return False
    current = _storage_root_identity(root_real)
    return current is not None and os.path.samestat(current, expected)


def _open_maintenance_subroot_from(
    root_real: str,
    *names: str,
    expect_identity: Optional[os.stat_result] = None,
) -> Optional[int]:
    """``_open_maintenance_subroot()`` anchored to a caller's root snapshot.

    Every component below the root is opened relative to the previous descriptor
    with ``O_NOFOLLOW``, so the descriptor really is the directory at
    ``<storage>/.prks-maintenance/<names...>`` -- not whatever a symlink planted
    at any level points at, and not whatever replaces a component after a
    path-based check has passed.

    The descent starts from ``root_real``, never from ``config.root``. Opening
    the operator-supplied path here would re-resolve that symlink independently
    of the snapshot the caller authorized against, so a retarget between the two
    could bind this descriptor to a different maintenance tree.

    The root open itself is anchored twice over, because a pathname is not an
    identity. ``root_real`` is already fully resolved and so must not be a
    symlink: ``O_NOFOLLOW`` makes a link swapped in after resolution fail closed
    rather than be followed. And ``expect_identity``, captured by the caller
    before it authorized, is compared against the opened descriptor, which
    catches the swap ``O_NOFOLLOW`` cannot see -- a different *real* directory
    renamed into place. Either way the descent runs in the directory the caller
    authorized, or not at all.

    Returns None when the subroot does not exist or when the platform has no
    descriptor-relative removal; raises ValueError when a component exists but
    is not a real directory.
    """
    if not _SUPPORTS_DIR_FD:
        return None
    try:
        fd = os.open(
            root_real, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("storage root is not a real directory") from exc
    if expect_identity is not None and not os.path.samestat(
        os.fstat(fd), expect_identity
    ):
        os.close(fd)
        raise ValueError("storage root changed identity during removal")
    handed_over = False
    try:
        for name in (MAINTENANCE_DIRNAME, *names):
            try:
                nxt = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=fd,
                )
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise ValueError("maintenance subroot is not a real directory") from exc
            os.close(fd)
            fd = nxt
        handed_over = True
        return fd
    finally:
        # Every exit but a successful hand-off closes the descriptor. A bare
        # `except` is not enough: the early return for a missing component is
        # not an exception, so it leaked one descriptor per call -- and a
        # missing subroot is a normal path, e.g. discarding rollback state that
        # is already gone.
        if not handed_over:
            os.close(fd)


def _remove_link_entry(path: str) -> None:
    """Remove a link-like entry itself, never its target.

    A Windows directory symlink or junction is removed with ``rmdir``, not
    ``unlink``, so both are attempted.
    """
    try:
        os.unlink(path)
    except OSError:
        os.rmdir(path)


def _remove_reparse_aware(path: str) -> None:
    """Remove one already-authorized path, treating reparse points as links.

    The maintenance boundary's own teardown for the platform that has no
    descriptors. ``_safe_remove()`` is shared by a dozen callers outside this
    boundary and treats only ``os.path.islink()`` as link-like, which misses an
    NTFS junction: ``os.walk(followlinks=False)`` descends into one and would
    delete its target. Here a junction is removed as the reparse entry it is and
    never traversed, at the root or at any depth.

    The walk is iterative so a deep staging tree cannot exhaust the stack.
    """
    if not os.path.lexists(path):
        return
    if os.path.islink(path) or _is_directory_reparse_point(path):
        _remove_link_entry(path)
        return
    if not os.path.isdir(path):
        os.unlink(path)
        return
    pending = [path]
    directories: list[str] = []
    while pending:
        current = pending.pop()
        directories.append(current)
        with os.scandir(current) as entries:
            for entry in entries:
                child = entry.path
                if entry.is_symlink() or _is_directory_reparse_point(child):
                    _remove_link_entry(child)
                elif entry.is_dir(follow_symlinks=False):
                    pending.append(child)
                else:
                    os.unlink(child)
    for directory in reversed(directories):
        os.rmdir(directory)


def _remove_proven_child(root: str, leaf: str, dir_fd: Optional[int]) -> None:
    """Remove the entry of an already-proven subroot whose name equals ``leaf``.

    The name handed to the syscall is the one the directory itself reports; the
    caller's string only selects which enumerated entry to remove. Nothing
    outside the enumerated subroot can therefore be named, and the entry is
    never resolved, so a symlink is unlinked instead of followed.

    An enumeration failure propagates and is never swallowed: "the directory
    could not be listed" must not be indistinguishable from "the child is
    already gone", because callers read a quiet return as proof of removal.
    A leaf that is genuinely absent from the listing is still a no-op.
    """
    listing = os.listdir(root if dir_fd is None else dir_fd)
    for entry in listing:
        if entry != leaf:
            continue
        if dir_fd is None:
            _remove_reparse_aware(os.path.join(root, entry))
            return
        try:
            st = os.lstat(entry, dir_fd=dir_fd)
        except FileNotFoundError:
            return
        if stat.S_ISDIR(st.st_mode):
            shutil.rmtree(entry, dir_fd=dir_fd)
        else:
            os.unlink(entry, dir_fd=dir_fd)
        return


def _remove_maintenance_child(
    config: StorageConfig, subroot: tuple[str, ...], path: str
) -> None:
    """Remove exactly one direct child of a maintenance subroot.

    Every restore cleanup removes a single direct child of its own subroot -- a
    staging token directory, a rollback transaction directory, or the restore
    journal -- so the contract is stated that way rather than as the weaker
    "somewhere beneath a root". Naming the subroot by ``config`` plus its fixed
    name, instead of accepting a root string, keeps the authorization rule
    structural: a caller cannot widen its own scope, and a symlink planted at
    the subroot cannot redefine it (a path-based containment check resolves
    both operands, so such a link would otherwise pass).

    The removal is then performed relative to the subroot's descriptor, so
    nothing swapped in after the check can redirect it, and the child itself is
    never resolved: a symlink leaf is unlinked, not followed to its target.

    Where descriptors exist there is exactly one resolution: the O_NOFOLLOW
    descent that proves each component is a real directory produces the very
    descriptor the removal runs against, so no separately resolved path can
    disagree with what is acted on. The portable fallback cannot bind that way
    -- see the comment on its branch.
    """
    # One canonical snapshot for the whole operation. Resolving config.root
    # again between authorization and removal is what let a symlink or junction
    # retargeted at that moment point the two at different trees.
    root_real = _resolved_storage_root(config)
    # Captured before authorization: the descriptor descent is checked against
    # this, so a directory renamed into root_real's place afterwards cannot make
    # authorization refer to one directory and removal to another.
    root_identity = _storage_root_identity(root_real)
    expected_root = _maintenance_subroot_from(root_real, *subroot)
    normalized = os.path.abspath(path)
    leaf = os.path.basename(normalized)
    if not leaf or leaf in (".", ".."):
        raise ValueError("refusing ambiguous removal path")
    try:
        parent_real = os.path.realpath(os.path.dirname(normalized))
    except OSError as exc:
        raise ValueError("removal path could not be resolved") from exc
    if parent_real != expected_root:
        raise ValueError("removal path is not a direct child of its maintenance root")
    fd = _open_maintenance_subroot_from(
        root_real, *subroot, expect_identity=root_identity
    )
    if fd is not None:
        # Bound to the descriptor the O_NOFOLLOW descent produced, and that
        # descent starts from the same root_real the authorization check above
        # used. expected_root is inert here: _remove_proven_child() consults it
        # only without a descriptor. Re-resolving config.root for the open would
        # let a retarget between the check and the open bind this descriptor to
        # a different maintenance tree.
        try:
            _remove_proven_child(expected_root, leaf, fd)
        finally:
            os.close(fd)
        return
    # No descriptor support (native Windows). Verification and removal are both
    # path-based here, so they cannot be bound to one descriptor -- but they do
    # share the single root snapshot above, so retargeting config.root cannot
    # redirect the cleanup.
    verified_root = _verified_maintenance_subroot_from(root_real, *subroot)
    if verified_root is None:
        return
    # The snapshot is a pathname, and a pathname is not an identity: root_real
    # itself can be renamed away and another real directory moved into its
    # place, which every check above would follow without noticing. The
    # descriptor branch catches that by fstat-ing what it opened; here the same
    # captured identity is re-checked as late as possible instead.
    if not _storage_root_keeps_identity(root_real, root_identity):
        raise ValueError("storage root changed identity during removal")
    # What remains is the narrower case of an actor replacing directories
    # *inside* the already-authorized tree between this point and the unlink;
    # _remove_reparse_aware() shrinks that further by never traversing a link or
    # reparse point at any depth.
    _remove_proven_child(verified_root, leaf, None)


def _discard_maintenance_child(
    config: StorageConfig, subroot: tuple[str, ...], path: str
) -> None:
    """Best-effort cleanup of maintenance garbage: log and carry on.

    For entries whose survival is untidy but harmless. *Recovery's* journal
    removal must use ``_remove_journal_or_fail()`` instead: that is the point
    where a surviving journal would later be replayed against rollback state
    this pass has already cleaned.

    ``apply_restore()`` removes the journal through this helper deliberately. On
    its rollback path raising here would mask the failure that triggered the
    rollback, and after commit a surviving journal only ever replays as
    ``keep_restored``. Either way a refused removal has deleted nothing, so
    logging one is safe.
    """
    try:
        _remove_maintenance_child(config, subroot, path)
    except Exception as exc:
        LOGGER.error("restore_cleanup_failed error_type=%s", safe_error_type(exc))


def _remove_journal_or_fail(config: StorageConfig) -> None:
    """Remove the restore journal and prove it is gone.

    The journal is what a later startup replays, so its removal is the commit
    point of recovery: nothing that makes replay unsafe may happen until the
    file is confirmed absent. A silently-failed removal that left the journal
    behind while rollback state was cleaned would make the next startup roll
    back a library whose rollback material no longer exists.
    """
    path = journal_path(config)
    cause: Optional[Exception] = None
    try:
        _remove_maintenance_child(config, _JOURNAL_SUBROOT, path)
    except Exception as exc:
        cause = exc
    if cause is None and not os.path.lexists(path):
        return
    LOGGER.error(
        "restore_recovery_failed reason=journal_not_removed error_type=%s",
        safe_error_type(cause) if cause is not None else "JournalStillPresent",
    )
    raise RestoreError(
        "journal_not_removed",
        "Incomplete restore could not be recovered.",
        http_status=500,
    ) from cause


def _dir_size_bytes(path: str) -> int:
    if not os.path.isdir(path) or os.path.islink(path):
        if os.path.isfile(path) and not os.path.islink(path):
            try:
                return os.path.getsize(path)
            except OSError:
                return 0
        return 0
    total = 0
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        kept = []
        for name in dirnames:
            child = os.path.join(dirpath, name)
            if os.path.islink(child):
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            child = os.path.join(dirpath, name)
            if os.path.islink(child) or not os.path.isfile(child):
                continue
            try:
                total += os.path.getsize(child)
            except OSError:
                continue
    return total


def _db_on_disk_bytes(config: StorageConfig) -> int:
    total = 0
    for path in (config.db_path, config.db_path + "-wal", config.db_path + "-shm"):
        if os.path.isfile(path) and not os.path.islink(path):
            try:
                total += os.path.getsize(path)
            except OSError:
                pass
    return total


def backup_additional_bytes(db_bytes: int, file_bytes: int) -> int:
    """Extra bytes backup creation still needs: snapshot + archive + verify DB + margin."""
    db_bytes = max(0, int(db_bytes))
    file_bytes = max(0, int(file_bytes))
    snapshot = db_bytes
    archive = db_bytes + file_bytes
    verify_db = db_bytes
    return snapshot + archive + verify_db + DISK_MARGIN_BYTES


def require_restore_upload_space(config: StorageConfig, content_length: int) -> None:
    _assert_testing_safe(config)
    if int(content_length) < 0:
        raise RestoreError(
            "insufficient_storage",
            "Not enough free storage to restore this backup safely.",
            http_status=400,
        )
    if _free_bytes(config.root) < int(content_length) + DISK_MARGIN_BYTES:
        raise RestoreError(
            "insufficient_storage",
            "Not enough free storage to restore this backup safely.",
            http_status=400,
        )


def _atomic_write_json(path: str, payload: dict[str, Any]) -> None:
    parent = os.path.dirname(path)
    _mkdir_owner(parent)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    _chmod_file(path)


def _open_sqlite_ro(db_path: str) -> sqlite3.Connection:
    uri = "file:" + os.path.abspath(db_path) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def sqlite_snapshot(source_path: str, dest_path: str) -> None:
    parent = os.path.dirname(dest_path)
    _mkdir_owner(parent)
    if os.path.lexists(dest_path):
        _safe_remove(dest_path)
    source = sqlite3.connect(source_path)
    try:
        dest = sqlite3.connect(dest_path)
        try:
            source.backup(dest)
            dest.commit()
        finally:
            dest.close()
    finally:
        source.close()
    _chmod_file(dest_path)


def sqlite_integrity_report(db_path: str) -> tuple[bool, str]:
    """SQLite b-tree/page integrity only. Foreign-key orphans are source audit."""
    conn = sqlite3.connect(os.path.abspath(db_path))
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        integrity = "" if row is None else str(row[0])
        if integrity != "ok":
            return False, "integrity_failed"
        return True, "ok"
    finally:
        conn.close()


def sqlite_foreign_key_violation_count(db_path: str) -> int:
    conn = sqlite3.connect(os.path.abspath(db_path))
    try:
        return len(conn.execute("PRAGMA foreign_key_check").fetchall())
    finally:
        conn.close()


def _foreign_key_warning(count: int, *, during_restore: bool) -> str:
    n = int(count)
    unit = "foreign-key issue" if n == 1 else "foreign-key issues"
    if during_restore:
        return f"This backup was created while the database had {n} {unit}."
    return f"Backup created successfully, but the database has {n} {unit}."


def supported_schema_ceiling(config: Optional[StorageConfig] = None) -> int:
    """Highest schema this process may accept: code version, or live DB if already newer."""
    ceiling = int(PRKS_SCHEMA_VERSION)
    if config is None or not os.path.isfile(config.db_path):
        return ceiling
    try:
        return max(ceiling, read_schema_version(config.db_path))
    except RestoreError:
        return ceiling


def read_schema_version(db_path: str) -> int:
    conn = _open_sqlite_ro(db_path)
    try:
        try:
            row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        except sqlite3.DatabaseError as exc:
            raise RestoreError(
                "invalid_database",
                "Backup database could not be read.",
                http_status=400,
            ) from exc
        if row is None:
            return 0
        try:
            return int(row[0])
        except (TypeError, ValueError) as exc:
            raise RestoreError(
                "invalid_schema_version",
                "Backup database schema version is invalid.",
                http_status=400,
            ) from exc
    finally:
        conn.close()


def _required_tables_present(db_path: str) -> bool:
    conn = _open_sqlite_ro(db_path)
    try:
        names = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        return all(table in names for table in _REQUIRED_TABLES)
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()


def _count_query(conn: sqlite3.Connection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    if row is None:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return 0


def library_summary_from_db(db_path: str) -> dict[str, int]:
    conn = _open_sqlite_ro(db_path)
    try:
        return {
            "works": _count_query(conn, "SELECT COUNT(*) FROM works"),
            "persons": _count_query(conn, "SELECT COUNT(*) FROM persons"),
            "annotations": _count_query(conn, "SELECT COUNT(*) FROM annotations"),
            "managed_pdfs": _count_query(
                conn,
                "SELECT COUNT(*) FROM works WHERE COALESCE(file_path,'') LIKE '/api/pdfs/%'",
            ),
        }
    except sqlite3.DatabaseError:
        return {"works": 0, "persons": 0, "annotations": 0, "managed_pdfs": 0}
    finally:
        conn.close()


def audit_managed_pdfs(db_path: str, pdf_basenames: set[str]) -> dict[str, int]:
    conn = _open_sqlite_ro(db_path)
    try:
        rows = conn.execute(
            "SELECT file_path FROM works WHERE COALESCE(file_path,'') LIKE '/api/pdfs/%'"
        ).fetchall()
    except sqlite3.DatabaseError:
        return {
            "managed_pdf_references": 0,
            "managed_pdfs_present": 0,
            "managed_pdfs_missing": 0,
        }
    finally:
        conn.close()
    references = 0
    present = 0
    missing = 0
    for row in rows:
        references += 1
        raw = "" if row[0] is None else str(row[0]).strip()
        name = raw.rsplit("/", 1)[-1] if raw else ""
        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or "\x00" in name
        ):
            missing += 1
            continue
        if name in pdf_basenames:
            present += 1
        else:
            missing += 1
    return {
        "managed_pdf_references": references,
        "managed_pdfs_present": present,
        "managed_pdfs_missing": missing,
    }


def walk_regular_files(root: str) -> tuple[list[tuple[str, str]], int]:
    """Return (relpath, abspath) pairs and symlink skip count. Never follows links."""
    found: list[tuple[str, str]] = []
    skipped_links = 0
    if not os.path.isdir(root):
        return found, skipped_links
    root_real = os.path.realpath(root)
    if not os.path.isdir(root_real):
        return found, skipped_links
    for dirpath, dirnames, filenames in os.walk(root_real, followlinks=False):
        live_dirs: list[str] = []
        for name in dirnames:
            child = os.path.join(dirpath, name)
            if os.path.islink(child):
                skipped_links += 1
                continue
            live_dirs.append(name)
        dirnames[:] = live_dirs
        for name in filenames:
            child = os.path.join(dirpath, name)
            if os.path.islink(child):
                skipped_links += 1
                continue
            if not os.path.isfile(child):
                continue
            rel = os.path.relpath(child, root_real).replace(os.sep, "/")
            rel_path = PurePosixPath(rel)
            if ".." in rel_path.parts or rel_path.is_absolute():
                continue
            found.append((rel_path.as_posix(), child))
    found.sort(key=lambda item: item[0])
    return found, skipped_links


def _normalize_zip_name(name: str) -> str:
    if not isinstance(name, str) or not name:
        raise RestoreError("unsafe_archive_path", "Backup archive contains an unsafe path.")
    if "\x00" in name or "\\" in name:
        raise RestoreError("unsafe_archive_path", "Backup archive contains an unsafe path.")
    if name.startswith("/") or name.startswith("./") or name.startswith("../"):
        raise RestoreError("unsafe_archive_path", "Backup archive contains an unsafe path.")
    if len(name) >= 2 and name[1] == ":":
        raise RestoreError("unsafe_archive_path", "Backup archive contains an unsafe path.")
    if "//" in name:
        raise RestoreError("unsafe_archive_path", "Backup archive contains an unsafe path.")
    posix = PurePosixPath(name)
    if posix.is_absolute() or posix.anchor:
        raise RestoreError("unsafe_archive_path", "Backup archive contains an unsafe path.")
    parts = posix.parts
    if any(part in {".", ".."} for part in parts):
        raise RestoreError("unsafe_archive_path", "Backup archive contains an unsafe path.")
    is_dir = name.endswith("/")
    normalized = posix.as_posix()
    expect = name[:-1] if is_dir else name
    if normalized != expect:
        raise RestoreError("unsafe_archive_path", "Backup archive contains an unsafe path.")
    return name


def _zip_mode(info: zipfile.ZipInfo) -> int:
    return (info.external_attr >> 16) & 0xFFFF


def _zip_entry_kind(info: zipfile.ZipInfo) -> str:
    name = info.filename or ""
    if name.endswith("/"):
        return "dir"
    mode = _zip_mode(info)
    kind = stat.S_IFMT(mode) if mode else 0
    if kind == _UNIX_IFLNK or kind == 0o120000:
        return "symlink"
    if kind in _UNIX_SPECIAL:
        return "special"
    if kind == _UNIX_IFDIR:
        return "dir"
    if kind in (0, _UNIX_IFREG):
        return "file"
    return "special"


def _payload_allowed(path: str, *, processing_allowed: bool) -> bool:
    if path == ARCHIVE_DB_PATH:
        return True
    if path == MANIFEST_NAME:
        return False
    if path.startswith("files/pdfs/") and path != "files/pdfs/":
        return True
    if path.startswith("files/people/") and path != "files/people/":
        return True
    if path.startswith("files/for_processing/") and path != "files/for_processing/":
        return processing_allowed
    return False


def _add_file_to_zip(
    zf: zipfile.ZipFile,
    arcname: str,
    src_path: str,
    *,
    tracker: Optional[_BackupProgress] = None,
) -> dict[str, Any]:
    compress = _entry_compress_type(arcname)
    info = zipfile.ZipInfo(arcname, date_time=time.gmtime()[:6])
    info.compress_type = compress
    info.external_attr = (0o100600 & 0xFFFF) << 16
    digest = hashlib.sha256()
    size = 0
    cancel = None if tracker is None else tracker.cancel_event
    with open(src_path, "rb") as src, zf.open(info, "w") as dest:
        for chunk in iter_file_chunks(src, cancel_event=cancel):
            digest.update(chunk)
            dest.write(chunk)
            size += len(chunk)
            if tracker is not None:
                tracker.add_archive_bytes(len(chunk))
    if tracker is not None:
        tracker.file_done()
    return {"path": arcname, "size": size, "sha256": digest.hexdigest()}


def _write_manifest_member(zf: zipfile.ZipFile, manifest: dict[str, Any]) -> None:
    payload = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    info = zipfile.ZipInfo(MANIFEST_NAME, date_time=time.gmtime()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o100600 & 0xFFFF) << 16
    zf.writestr(info, payload)


def create_backup(
    config: StorageConfig,
    *,
    post_archive_hook: Optional[Callable[[str], None]] = None,
    progress: Optional[Callable[[dict[str, Any]], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> BackupResult:
    """Build a verified .prks-backup next to live storage, then self-verify."""
    _assert_testing_safe(config)
    LOGGER.info("backup_started request_id=none")
    created = _utc_now()
    filename = _backup_filename(created)
    tmp_paths: list[str] = []
    tracker = _BackupProgress(progress, cancel_event)
    t0 = clock_ns()
    try:
        tracker.check()
        if not os.path.isfile(config.db_path):
            raise BackupError(
                "missing_database",
                "PRKS database is not available.",
                http_status=500,
            )
        if not _path_is_under(config.pdfs_dir, config.root):
            raise BackupError(
                "canonical_path_outside_root",
                "Backup could not be created.",
                http_status=500,
            )
        if not _path_is_under(config.people_dir, config.root):
            raise BackupError(
                "canonical_path_outside_root",
                "Backup could not be created.",
                http_status=500,
            )

        processing_included = processing_is_under_storage(config)
        warnings: list[str] = []
        if not processing_included:
            warnings.append(
                "Processing queue files were not included because they are stored outside PRKS storage."
            )

        pdf_files, pdf_links = walk_regular_files(config.pdfs_dir)
        people_files, people_links = walk_regular_files(config.people_dir)
        processing_files: list[tuple[str, str]] = []
        processing_links = 0
        if processing_included:
            processing_files, processing_links = walk_regular_files(config.processing_dir)
        skipped_links = pdf_links + people_links + processing_links
        if skipped_links:
            n = skipped_links
            warnings.append(
                f"Backup skipped {n} symbolic link{'s' if n != 1 else ''} in managed storage."
            )

        db_bytes = _db_on_disk_bytes(config)
        file_bytes = (
            sum(os.path.getsize(p) for _, p in pdf_files)
            + sum(os.path.getsize(p) for _, p in people_files)
            + sum(os.path.getsize(p) for _, p in processing_files)
        )
        payload_bytes = db_bytes + file_bytes
        if _free_bytes(config.root) < backup_additional_bytes(db_bytes, file_bytes):
            raise BackupError(
                "insufficient_storage",
                "Not enough free storage to create a backup safely.",
                http_status=400,
            )

        file_total = 1 + len(pdf_files) + len(people_files) + len(processing_files)
        tracker.set_work(payload_bytes, file_total)
        tracker.emit(phase="snapshot", force=True)

        maint = _ensure_maintenance_dirs(config)
        work_dir = os.path.join(maint, "backup", secrets.token_urlsafe(12))
        _mkdir_owner(work_dir)
        tmp_paths.append(work_dir)
        snapshot_path = os.path.join(work_dir, "prks_data.db")
        archive_path = os.path.join(work_dir, filename)

        sqlite_snapshot(config.db_path, snapshot_path)
        tracker.check()
        ok, reason = sqlite_integrity_report(snapshot_path)
        if not ok:
            raise BackupError(
                reason,
                "Backup could not be verified.",
                http_status=500,
            )
        fk_violations = sqlite_foreign_key_violation_count(snapshot_path)
        if fk_violations:
            warnings.append(_foreign_key_warning(fk_violations, during_restore=False))
        db_schema = read_schema_version(snapshot_path)
        summary = library_summary_from_db(snapshot_path)
        pdf_basenames = {os.path.basename(rel) for rel, _ in pdf_files if "/" not in rel}
        # Nested managed files still count as present by basename under pdfs/.
        pdf_basenames.update(os.path.basename(rel) for rel, _ in pdf_files)
        audit = audit_managed_pdfs(snapshot_path, pdf_basenames)
        if audit["managed_pdfs_missing"]:
            n = audit["managed_pdfs_missing"]
            warnings.append(
                "Backup created successfully, but "
                f"{n} referenced PDF{'s were' if n != 1 else ' was'} already missing from the current library."
            )

        tracker.emit(phase="archiving", force=True)
        entries: list[dict[str, Any]] = []
        with zipfile.ZipFile(archive_path, "w", allowZip64=True) as zf:
            entries.append(_add_file_to_zip(zf, ARCHIVE_DB_PATH, snapshot_path, tracker=tracker))
            for rel, abs_path in pdf_files:
                entries.append(_add_file_to_zip(zf, f"files/pdfs/{rel}", abs_path, tracker=tracker))
            for rel, abs_path in people_files:
                entries.append(
                    _add_file_to_zip(zf, f"files/people/{rel}", abs_path, tracker=tracker)
                )
            for rel, abs_path in processing_files:
                entries.append(
                    _add_file_to_zip(zf, f"files/for_processing/{rel}", abs_path, tracker=tracker)
                )
            manifest = {
                "format": FORMAT_ID,
                "format_version": FORMAT_VERSION,
                "created_at": _iso_z(created),
                "source_mode": config.mode,
                "db_schema_version": db_schema,
                "components": {
                    "database": True,
                    "pdfs": True,
                    "people": True,
                    "processing": processing_included,
                },
                "summary": {
                    "works": summary["works"],
                    "persons": summary["persons"],
                    "annotations": summary["annotations"],
                    "managed_pdfs": summary["managed_pdfs"],
                    "pdf_files": len(pdf_files),
                    "people_files": len(people_files),
                    "processing_files": len(processing_files),
                },
                "audit": audit,
                "entries": entries,
            }
            _write_manifest_member(zf, manifest)
        _chmod_file(archive_path)

        if post_archive_hook is not None:
            post_archive_hook(archive_path)

        tracker.emit(phase="verifying", force=True)
        with perf_span("backup_verify"):
            verified = verify_backup(
                archive_path,
                current_schema_version=max(PRKS_SCHEMA_VERSION, db_schema),
                tracker=tracker,
            )
        if not verified.get("ok"):
            raise BackupError(
                verified.get("reason") or "verification_failed",
                "Backup could not be verified.",
                http_status=500,
            )
        final_dir = os.path.join(maint, "backup")
        final_path = os.path.join(final_dir, filename)
        if os.path.lexists(final_path):
            _safe_remove(final_path)
        os.replace(archive_path, final_path)
        _chmod_file(final_path)
        LOGGER.info(
            "backup_verified format_version=%s schema_version=%s works=%s pdfs=%s persons=%s missing_pdfs=%s fk_violations=%s processing_included=%s",
            FORMAT_VERSION,
            db_schema,
            summary["works"],
            len(pdf_files),
            summary["persons"],
            audit["managed_pdfs_missing"],
            fk_violations,
            "true" if processing_included else "false",
        )
        return BackupResult(
            archive_path=final_path,
            filename=filename,
            verified=True,
            manifest=manifest,
            warnings=warnings,
            summary={**summary, "warnings": list(warnings), "audit": audit},
        )
    except BackupError as exc:
        if exc.reason == "cancelled":
            LOGGER.info("backup_cancelled")
        else:
            LOGGER.error("backup_failed reason=%s error_type=%s", exc.reason, safe_error_type(exc))
        raise
    except RestoreError as exc:
        LOGGER.error("backup_failed reason=%s error_type=%s", exc.reason, safe_error_type(exc))
        raise BackupError(exc.reason, "Backup could not be verified.", http_status=500) from exc
    except Exception as exc:
        LOGGER.error("backup_failed reason=internal error_type=%s", safe_error_type(exc))
        raise BackupError("internal", "Backup could not be created.", http_status=500) from exc
    finally:
        try:
            record_span("backup_create", clock_ns() - t0)
        except Exception:
            pass
        for path in tmp_paths:
            _safe_remove(path)

def verify_backup(
    archive_path: str,
    *,
    current_schema_version: int = PRKS_SCHEMA_VERSION,
    extract_dir: Optional[str] = None,
    tracker: Optional[_BackupProgress] = None,
) -> dict[str, Any]:
    """Reopen an archive and fully verify it. Optionally extract payload into extract_dir."""
    try:
        return _verify_backup_inner(
            archive_path,
            current_schema_version=current_schema_version,
            extract_dir=extract_dir,
            tracker=tracker,
        )
    except BackupError:
        raise
    except RestoreError as exc:
        return {
            "ok": False,
            "reason": exc.reason,
            "message": exc.message,
            "warnings": [],
            "summary": {},
            "manifest": None,
        }


def _verify_backup_inner(
    archive_path: str,
    *,
    current_schema_version: int,
    extract_dir: Optional[str],
    tracker: Optional[_BackupProgress] = None,
) -> dict[str, Any]:
    if not os.path.isfile(archive_path):
        raise RestoreError("missing_archive", "Backup archive could not be read.")
    try:
        zf = zipfile.ZipFile(archive_path, "r")
    except zipfile.BadZipFile as exc:
        raise RestoreError("invalid_archive", "File is not a valid PRKS backup.") from exc
    try:
        names = zf.namelist()
        if len(names) != len(set(names)):
            raise RestoreError("duplicate_member", "Backup archive contains duplicate entries.")
        if len(names) > DEFAULT_MAX_ZIP_ENTRIES:
            raise RestoreError("too_many_entries", "Backup archive is not a valid PRKS backup.")
        declared_uncompressed = 0
        for info in zf.infolist():
            if info.file_size < 0 or info.compress_size < 0:
                raise RestoreError("invalid_size", "Backup archive is not a valid PRKS backup.")
            declared_uncompressed += int(info.file_size)
            if info.compress_size > 0:
                ratio = int(info.file_size) / float(info.compress_size)
                if ratio > DEFAULT_MAX_COMPRESSION_RATIO and int(info.file_size) > IO_CHUNK_SIZE:
                    raise RestoreError(
                        "compression_ratio",
                        "Backup archive is not a valid PRKS backup.",
                    )
        if declared_uncompressed > DEFAULT_MAX_UNCOMPRESSED_BYTES:
            raise RestoreError(
                "too_large_uncompressed",
                "Backup archive is not a valid PRKS backup.",
            )
        if extract_dir is not None:
            if _free_bytes(extract_dir) < declared_uncompressed + DISK_MARGIN_BYTES:
                raise RestoreError(
                    "insufficient_storage",
                    "Not enough free storage to restore this backup safely.",
                    http_status=400,
                )
        normalized_files: list[zipfile.ZipInfo] = []
        for info in zf.infolist():
            name = _normalize_zip_name(info.filename)
            kind = _zip_entry_kind(info)
            if kind == "symlink" or kind == "special":
                raise RestoreError(
                    "illegal_member_type",
                    "Backup archive contains an unsupported entry.",
                )
            if kind == "dir":
                dir_name = name if name.endswith("/") else name + "/"
                if dir_name not in (
                    "data/",
                    "files/",
                    "files/pdfs/",
                    "files/people/",
                    "files/for_processing/",
                ) and not any(
                    dir_name.startswith(prefix) for prefix in _ALLOWED_PAYLOAD_PREFIXES
                ):
                    raise RestoreError(
                        "unexpected_entry",
                        "Backup archive contains an unexpected path.",
                    )
                continue
            normalized_files.append(info)

        if MANIFEST_NAME not in names:
            raise RestoreError("missing_manifest", "Backup archive is not a valid PRKS backup.")
        try:
            raw_manifest = zf.read(MANIFEST_NAME)
        except Exception as exc:
            raise RestoreError("missing_manifest", "Backup archive is not a valid PRKS backup.") from exc
        try:
            manifest = json.loads(raw_manifest.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise RestoreError("invalid_manifest", "Backup archive is not a valid PRKS backup.") from exc
        _validate_manifest_schema(manifest)
        processing_allowed = bool(manifest["components"].get("processing"))
        entries = manifest["entries"]
        entry_paths = [item["path"] for item in entries]
        if len(entry_paths) != len(set(entry_paths)):
            raise RestoreError("duplicate_member", "Backup archive contains duplicate entries.")
        entry_map = {item["path"]: item for item in entries}

        payload_infos = []
        for info in normalized_files:
            path = info.filename
            if path == MANIFEST_NAME:
                continue
            if not _payload_allowed(path, processing_allowed=processing_allowed):
                raise RestoreError(
                    "unexpected_entry",
                    "Backup archive contains an unexpected path.",
                )
            payload_infos.append(info)

        zip_payload_paths = {info.filename for info in payload_infos}
        if zip_payload_paths != set(entry_paths):
            raise RestoreError(
                "manifest_mismatch",
                "Backup archive does not match its manifest.",
            )
        if ARCHIVE_DB_PATH not in zip_payload_paths:
            raise RestoreError("missing_database", "Backup archive is not a valid PRKS backup.")

        extract_root = None
        if extract_dir is not None:
            _mkdir_owner(extract_dir)
            extract_root = extract_dir

        total_written = 0
        for info in payload_infos:
            meta = entry_map[info.filename]
            dest_path = None
            if extract_root is not None:
                dest_path = _safe_extract_dest(extract_root, info.filename)
                parent = os.path.dirname(dest_path)
                _mkdir_owner(parent)
            digest = hashlib.sha256()
            actual = 0
            with zf.open(info, "r") as src:
                out = open(dest_path, "wb") if dest_path else None
                try:
                    cancel = None if tracker is None else tracker.cancel_event
                    for chunk in iter_file_chunks(src, cancel_event=cancel):
                        actual += len(chunk)
                        total_written += len(chunk)
                        if tracker is not None:
                            tracker.add_verify_bytes(len(chunk))
                        if actual > int(meta["size"]):
                            raise RestoreError(
                                "size_mismatch",
                                "Backup archive failed integrity verification.",
                            )
                        if total_written > DEFAULT_MAX_UNCOMPRESSED_BYTES:
                            raise RestoreError(
                                "too_large_uncompressed",
                                "Backup archive is not a valid PRKS backup.",
                            )
                        digest.update(chunk)
                        if out is not None:
                            out.write(chunk)
                finally:
                    if out is not None:
                        out.close()
            if dest_path is not None:
                _chmod_file(dest_path)
            if actual != int(meta["size"]):
                raise RestoreError(
                    "size_mismatch",
                    "Backup archive failed integrity verification.",
                )
            if digest.hexdigest().lower() != str(meta["sha256"]).lower():
                raise RestoreError(
                    "hash_mismatch",
                    "Backup archive failed integrity verification.",
                )

        db_path = None
        if extract_root is not None:
            db_path = os.path.join(extract_root, *PurePosixPath(ARCHIVE_DB_PATH).parts)
        else:
            tmp_parent = os.path.join(os.path.dirname(archive_path), ".verify-db")
            _mkdir_owner(tmp_parent)
            db_path = os.path.join(tmp_parent, "prks_data.db")
            try:
                with zf.open(ARCHIVE_DB_PATH) as src, open(db_path, "wb") as dest:
                    hash_and_copy(src, dest)
                _chmod_file(db_path)
            except Exception:
                _safe_remove(tmp_parent)
                raise

        isolated_db_dir = None if extract_root is not None else os.path.dirname(db_path)
        try:
            ok, reason = sqlite_integrity_report(db_path)
            if not ok:
                raise RestoreError(reason, "Backup database failed integrity verification.")
            fk_violations = sqlite_foreign_key_violation_count(db_path)
            if not _required_tables_present(db_path):
                raise RestoreError(
                    "missing_core_tables",
                    "Backup database is missing required tables.",
                )
            db_schema = read_schema_version(db_path)
            manifest_schema = int(manifest["db_schema_version"])
            if db_schema != manifest_schema:
                raise RestoreError(
                    "schema_version_mismatch",
                    "Backup database does not match its manifest.",
                )
            if db_schema > int(current_schema_version):
                raise RestoreError(
                    "schema_newer",
                    "This backup was created by a newer PRKS database version. Update PRKS before restoring it.",
                    http_status=400,
                )
            pdf_basenames: set[str] = set()
            for path in entry_paths:
                if path.startswith("files/pdfs/") and path != "files/pdfs/":
                    pdf_basenames.add(PurePosixPath(path).name)
            audit = audit_managed_pdfs(db_path, pdf_basenames)
            claimed_missing = int((manifest.get("audit") or {}).get("managed_pdfs_missing") or 0)
            if audit["managed_pdfs_missing"] and claimed_missing < audit["managed_pdfs_missing"]:
                # Archive claims a referenced PDF exists but the payload does not have it.
                raise RestoreError(
                    "managed_pdf_missing_from_archive",
                    "Backup archive failed integrity verification.",
                )
            summary = library_summary_from_db(db_path)
            warnings: list[str] = []
            if fk_violations:
                warnings.append(_foreign_key_warning(fk_violations, during_restore=True))
            if not processing_allowed:
                warnings.append(
                    "Processing queue files were not included because they are stored outside PRKS storage."
                )
            if audit["managed_pdfs_missing"]:
                n = audit["managed_pdfs_missing"]
                warnings.append(
                    "This backup was created while "
                    f"{n} referenced PDF{'s were' if n != 1 else ' was'} already missing from the library."
                )
            return {
                "ok": True,
                "reason": "ok",
                "message": "",
                "warnings": warnings,
                "summary": {
                    **summary,
                    "pdf_files": sum(
                        1 for p in entry_paths if p.startswith("files/pdfs/")
                    ),
                    "people_files": sum(
                        1 for p in entry_paths if p.startswith("files/people/")
                    ),
                    "processing_files": sum(
                        1 for p in entry_paths if p.startswith("files/for_processing/")
                    ),
                    "db_schema_version": db_schema,
                    "format_version": FORMAT_VERSION,
                    "created_at": manifest.get("created_at"),
                    "audit": audit,
                    "processing_included": processing_allowed,
                },
                "manifest": manifest,
            }
        finally:
            if isolated_db_dir is not None:
                _safe_remove(isolated_db_dir)
    finally:
        zf.close()


def _validate_manifest_schema(manifest: Any) -> None:
    if not isinstance(manifest, dict):
        raise RestoreError("invalid_manifest", "Backup archive is not a valid PRKS backup.")
    if manifest.get("format") != FORMAT_ID:
        raise RestoreError("invalid_format", "File is not a valid PRKS backup.")
    version = manifest.get("format_version")
    if version != FORMAT_VERSION:
        raise RestoreError(
            "unsupported_format_version",
            "This backup uses an unsupported PRKS backup format.",
        )
    created_at = manifest.get("created_at")
    if not isinstance(created_at, str) or not created_at:
        raise RestoreError("invalid_manifest", "Backup archive is not a valid PRKS backup.")
    schema = manifest.get("db_schema_version")
    if not isinstance(schema, int) or isinstance(schema, bool) or schema < 0:
        raise RestoreError("invalid_manifest", "Backup archive is not a valid PRKS backup.")
    components = manifest.get("components")
    if not isinstance(components, dict) or components.get("database") is not True:
        raise RestoreError("invalid_manifest", "Backup archive is not a valid PRKS backup.")
    for key in ("pdfs", "people", "processing"):
        if key not in components or not isinstance(components[key], bool):
            raise RestoreError("invalid_manifest", "Backup archive is not a valid PRKS backup.")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise RestoreError("invalid_manifest", "Backup archive is not a valid PRKS backup.")
    for item in entries:
        if not isinstance(item, dict):
            raise RestoreError("invalid_manifest", "Backup archive is not a valid PRKS backup.")
        path = item.get("path")
        size = item.get("size")
        sha = item.get("sha256")
        if not isinstance(path, str) or not path:
            raise RestoreError("invalid_manifest", "Backup archive is not a valid PRKS backup.")
        _normalize_zip_name(path)
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise RestoreError("invalid_size", "Backup archive is not a valid PRKS backup.")
        if not isinstance(sha, str) or not _SHA256_RE.fullmatch(sha):
            raise RestoreError("invalid_hash", "Backup archive is not a valid PRKS backup.")
        if path == MANIFEST_NAME:
            raise RestoreError("invalid_manifest", "Backup archive is not a valid PRKS backup.")
        processing_allowed = bool(components.get("processing"))
        if not _payload_allowed(path, processing_allowed=processing_allowed):
            raise RestoreError(
                "unexpected_entry",
                "Backup archive contains an unexpected path.",
            )
    summary = manifest.get("summary")
    if summary is not None and not isinstance(summary, dict):
        raise RestoreError("invalid_manifest", "Backup archive is not a valid PRKS backup.")


def _safe_extract_dest(extract_root: str, arcname: str) -> str:
    rel = PurePosixPath(_normalize_zip_name(arcname))
    dest = os.path.realpath(os.path.join(extract_root, *rel.parts))
    root_real = os.path.realpath(extract_root)
    if dest == root_real or not dest.startswith(root_real + os.sep):
        raise RestoreError("unsafe_archive_path", "Backup archive contains an unsafe path.")
    return dest


def cleanup_stale_staging(config: StorageConfig, *, now: Optional[float] = None) -> None:
    _assert_testing_safe(config)
    try:
        staging_root = _verified_maintenance_subroot(config, *_STAGING_SUBROOT)
    except ValueError:
        # Something is at restore-staging that is not a real directory. Skip
        # cleanup rather than delete through it; startup must not be blocked.
        LOGGER.error("restore_staging_cleanup_skipped reason=unsafe_staging_root")
        return
    if staging_root is None:
        return
    try:
        names = os.listdir(staging_root)
    except OSError:
        return
    current = time.time() if now is None else now
    for name in names:
        child = os.path.join(staging_root, name)
        if os.path.islink(child):
            _discard_maintenance_child(config, _STAGING_SUBROOT, child)
            continue
        if os.path.isfile(child):
            if not name.startswith(".upload-"):
                continue
            try:
                age = current - os.path.getmtime(child)
            except OSError:
                age = STAGING_TTL_SECONDS + 1
            if age >= STAGING_TTL_SECONDS:
                _discard_maintenance_child(config, _STAGING_SUBROOT, child)
            continue
        if not _TOKEN_RE.fullmatch(name):
            continue
        meta_path = os.path.join(child, "meta.json")
        expired = True
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as handle:
                    meta = json.load(handle)
                created = float(meta.get("created_unix") or 0)
                if created > 0 and (current - created) < STAGING_TTL_SECONDS:
                    expired = False
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                expired = True
        if expired:
            _discard_maintenance_child(config, _STAGING_SUBROOT, child)


def stage_restore(config: StorageConfig, upload_path: str) -> StagingResult:
    """Validate an uploaded archive in staging. Live storage is not modified."""
    _assert_testing_safe(config)
    cleanup_stale_staging(config)
    if not os.path.isfile(upload_path):
        raise RestoreError("missing_archive", "Backup archive could not be read.")
    token = secrets.token_urlsafe(24)
    staging_dir = _staging_dir(config, token)
    tree_dir = os.path.join(staging_dir, "tree")
    archive_dest = os.path.join(staging_dir, "archive" + BACKUP_EXTENSION)
    _mkdir_owner(staging_dir)
    try:
        if os.path.abspath(upload_path) != os.path.abspath(archive_dest):
            os.replace(upload_path, archive_dest)
        _chmod_file(archive_dest)
        with perf_span("restore_verify"):
            verified = verify_backup(
                archive_dest,
                current_schema_version=supported_schema_ceiling(config),
                extract_dir=tree_dir,
            )
        if not verified.get("ok"):
            raise RestoreError(
                verified.get("reason") or "verification_failed",
                verified.get("message") or "Backup could not be verified. Current PRKS data was not changed.",
                http_status=400,
            )
        manifest = verified["manifest"]
        summary = verified["summary"]
        warnings = list(verified.get("warnings") or [])
        meta = {
            "token": token,
            "created_unix": time.time(),
            "verified": True,
            "db_schema_version": summary.get("db_schema_version"),
            "processing_included": bool(manifest["components"].get("processing")),
        }
        _atomic_write_json(os.path.join(staging_dir, "meta.json"), meta)
        LOGGER.info(
            "restore_staged format_version=%s schema_version=%s works=%s",
            FORMAT_VERSION,
            summary.get("db_schema_version"),
            summary.get("works"),
        )
        LOGGER.info(
            "restore_verified format_version=%s schema_version=%s works=%s",
            FORMAT_VERSION,
            summary.get("db_schema_version"),
            summary.get("works"),
        )
        return StagingResult(
            token=token,
            verified=True,
            summary=summary,
            warnings=warnings,
            manifest=manifest,
        )
    except RestoreError as exc:
        LOGGER.error("restore_staged reason=%s error_type=%s", exc.reason, safe_error_type(exc))
        _discard_maintenance_child(config, _STAGING_SUBROOT, staging_dir)
        raise
    except Exception as exc:
        LOGGER.error("restore_staged reason=internal error_type=%s", safe_error_type(exc))
        _discard_maintenance_child(config, _STAGING_SUBROOT, staging_dir)
        raise RestoreError(
            "internal",
            "Backup could not be verified. Current PRKS data was not changed.",
            http_status=500,
        ) from exc


def _staging_dir(config: StorageConfig, token: str) -> str:
    if not _TOKEN_RE.fullmatch(token or ""):
        raise RestoreError("unknown_token", "Backup is not available for restore.", http_status=404)
    root = _maintenance_subroot(config, *_STAGING_SUBROOT)
    candidate = os.path.join(root, token)
    # Two separate escapes: the subroot itself being a symlink (a containment
    # check resolves both operands, so it would otherwise pass), and the token
    # directory being one.
    if os.path.realpath(os.path.dirname(candidate)) != root:
        raise RestoreError("unknown_token", "Backup is not available for restore.", http_status=404)
    if not _path_is_under(candidate, root):
        raise RestoreError("unknown_token", "Backup is not available for restore.", http_status=404)
    return candidate


def _load_staging_meta(config: StorageConfig, token: str) -> dict[str, Any]:
    staging_dir = _staging_dir(config, token)
    meta_path = os.path.join(staging_dir, "meta.json")
    if not os.path.isfile(meta_path):
        raise RestoreError("unknown_token", "Backup is not available for restore.", http_status=404)
    try:
        with open(meta_path, encoding="utf-8") as handle:
            meta = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RestoreError("unknown_token", "Backup is not available for restore.", http_status=404) from exc
    created = float(meta.get("created_unix") or 0)
    if created <= 0 or (time.time() - created) > STAGING_TTL_SECONDS:
        _discard_maintenance_child(config, _STAGING_SUBROOT, staging_dir)
        raise RestoreError("unknown_token", "Backup is not available for restore.", http_status=404)
    if not meta.get("verified"):
        _discard_maintenance_child(config, _STAGING_SUBROOT, staging_dir)
        raise RestoreError("unknown_token", "Backup is not available for restore.", http_status=404)
    return meta


def _component_live_path(config: StorageConfig, name: str) -> str:
    if name == "database":
        return config.db_path
    if name == "pdfs":
        return config.pdfs_dir
    if name == "people":
        return config.people_dir
    if name == "processing":
        return config.processing_dir
    raise RestoreError("internal", "Restore could not complete.", http_status=500)


def _rollback_dir(config: StorageConfig, transaction_id: str) -> str:
    if not _TOKEN_RE.fullmatch(transaction_id or ""):
        raise RestoreError("journal_invalid", "Incomplete restore could not be recovered.", http_status=500)
    root = _maintenance_subroot(config, *_ROLLBACK_SUBROOT)
    candidate = os.path.join(root, transaction_id)
    if os.path.realpath(os.path.dirname(candidate)) != root:
        raise RestoreError("journal_invalid", "Incomplete restore could not be recovered.", http_status=500)
    if not _path_is_under(candidate, root):
        raise RestoreError("journal_invalid", "Incomplete restore could not be recovered.", http_status=500)
    return candidate


def _component_staged_path(tree_dir: str, name: str) -> str:
    if name == "database":
        return os.path.join(tree_dir, *PurePosixPath(ARCHIVE_DB_PATH).parts)
    if name == "pdfs":
        return os.path.join(tree_dir, "files", "pdfs")
    if name == "people":
        return os.path.join(tree_dir, "files", "people")
    if name == "processing":
        return os.path.join(tree_dir, "files", "for_processing")
    raise RestoreError("internal", "Restore could not complete.", http_status=500)


def _db_sidecar_paths(db_path: str) -> list[str]:
    return [db_path + suffix for suffix in ("-wal", "-shm", "-journal")]


def _rename_replace(src: str, dest: str) -> None:
    parent = os.path.dirname(dest)
    if parent:
        _mkdir_owner(parent)
    os.replace(src, dest)


def clear_derived_storage(config: StorageConfig) -> None:
    thumbs = config.thumbs_dir
    if os.path.isdir(thumbs) and not os.path.islink(thumbs):
        for name in os.listdir(thumbs):
            _safe_remove(os.path.join(thumbs, name))
    else:
        if os.path.lexists(thumbs):
            _safe_remove(thumbs)
        _mkdir_owner(thumbs)
    for path in (
        config.index_db_path,
        config.index_db_path + "-wal",
        config.index_db_path + "-shm",
        config.index_db_path + "-journal",
        config.research_index_db_path,
        config.research_index_db_path + "-wal",
        config.research_index_db_path + "-shm",
        config.research_index_db_path + "-journal",
    ):
        if os.path.lexists(path):
            _safe_remove(path)


def _rewrite_processing_abs_paths(db_path: str, processing_dir: str) -> None:
    if not os.path.isfile(db_path):
        return
    conn = sqlite3.connect(db_path)
    try:
        try:
            rows = conn.execute("SELECT id, rel_path FROM processing_files").fetchall()
        except sqlite3.DatabaseError:
            return
        for row in rows:
            rel = "" if row[1] is None else str(row[1]).replace("\\", "/")
            posix = PurePosixPath(rel)
            if not rel or posix.is_absolute() or ".." in posix.parts:
                continue
            new_abs = os.path.join(processing_dir, *posix.parts)
            conn.execute(
                "UPDATE processing_files SET abs_path = ? WHERE id = ?",
                (new_abs, row[0]),
            )
        conn.commit()
    finally:
        conn.close()


def _write_journal(config: StorageConfig, journal: dict[str, Any]) -> None:
    _atomic_write_json(journal_path(config), journal)


def _read_journal_file(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise RestoreError("journal_invalid", "Incomplete restore could not be recovered.", http_status=500)
    if data.get("format") != "prks-restore-journal":
        raise RestoreError("journal_invalid", "Incomplete restore could not be recovered.", http_status=500)
    phase = data.get("phase")
    if phase not in _PHASES:
        raise RestoreError("journal_invalid", "Incomplete restore could not be recovered.", http_status=500)
    # The transaction id is a path segment for the rollback tree: recovery
    # joins it under maintenance_root() and then removes and re-installs
    # whatever it finds there. Validate it exactly like the staging token, so a
    # damaged or hand-edited journal can never point rollback outside that root.
    if not isinstance(data.get("transaction_id"), str) or not _TOKEN_RE.fullmatch(data["transaction_id"]):
        raise RestoreError("journal_invalid", "Incomplete restore could not be recovered.", http_status=500)
    components = data.get("components")
    if not isinstance(components, dict):
        raise RestoreError("journal_invalid", "Incomplete restore could not be recovered.", http_status=500)
    return data


def _empty_component_state() -> dict[str, bool]:
    return {
        "old_existed": False,
        "old_move_started": False,
        "old_moved": False,
        "new_install_started": False,
        "new_installed": False,
    }


def _component_flags(state: dict[str, Any]) -> dict[str, bool]:
    old_moved = bool(state.get("old_moved"))
    new_installed = bool(state.get("new_installed"))
    return {
        "old_existed": bool(state.get("old_existed") or old_moved),
        "old_move_started": bool(state.get("old_move_started") or old_moved),
        "old_moved": old_moved,
        "new_install_started": bool(state.get("new_install_started") or new_installed),
        "new_installed": new_installed,
    }


def _rollback_component_path(config: StorageConfig, rollback_root: str, name: str) -> str:
    if name == "database":
        return os.path.join(rollback_root, "database", os.path.basename(config.db_path))
    return os.path.join(rollback_root, name)


def _remove_live_component(config: StorageConfig, name: str) -> None:
    live = _component_live_path(config, name)
    if os.path.lexists(live):
        _safe_remove(live)
    if name == "database":
        for side in _db_sidecar_paths(live):
            if os.path.lexists(side):
                _safe_remove(side)


def _restore_rollback_sidecars(config: StorageConfig, rollback_root: str, name: str) -> None:
    """Move a component's remaining rollback sidecars onto the live path.

    Only the database has any. Each moves independently, so a pass that crashed
    between the main file and its sidecars can be completed by a later one --
    a WAL can hold committed pages, and abandoning it loses them.
    """
    if name != "database":
        return
    live = _component_live_path(config, name)
    rolled_dir = os.path.dirname(_rollback_component_path(config, rollback_root, name))
    base = os.path.basename(config.db_path)
    pending = [
        (os.path.join(rolled_dir, base + suffix), live + suffix)
        for suffix in ("-wal", "-shm", "-journal")
    ]
    pending = [(src, dest) for src, dest in pending if os.path.lexists(src)]
    if not pending:
        return
    parent = os.path.dirname(live)
    if parent:
        _mkdir_owner(parent)
    for src, dest in pending:
        os.replace(src, dest)


def _restore_rollback_component(config: StorageConfig, rollback_root: str, name: str) -> None:
    live = _component_live_path(config, name)
    rolled = _rollback_component_path(config, rollback_root, name)
    if not os.path.lexists(rolled):
        return
    parent = os.path.dirname(live)
    if parent:
        _mkdir_owner(parent)
    os.replace(rolled, live)
    _restore_rollback_sidecars(config, rollback_root, name)


def _rollback_from_journal(config: StorageConfig, journal: dict[str, Any]) -> None:
    txn = journal["transaction_id"]
    rollback_root = _rollback_dir(config, txn)
    components = journal.get("components") or {}
    for name, state in components.items():
        if not isinstance(state, dict):
            continue
        flags = _component_flags(state)
        # Never remove a live component this journal cannot put back. A first
        # pass consumes the rollback copy (os.replace moves it onto the live
        # path), so a replayed journal would delete the live component and then
        # find nothing to restore -- destroying canonical data. Skipping keeps
        # replay non-destructive and recovery re-runnable after a failure.
        # A component that did not exist before has nothing to put back, and
        # removing the newly installed one is the correct rollback for it.
        if flags["old_existed"] and not os.path.lexists(
            _rollback_component_path(config, rollback_root, name)
        ):
            # Partially applied: an earlier pass already moved this component's
            # main rollback file onto the live path. Removing live now would
            # destroy it with nothing to put back, so finish what remains
            # instead of abandoning it -- for the database that is its
            # WAL/journal sidecars, which would otherwise be deleted along with
            # the rollback tree.
            _restore_rollback_sidecars(config, rollback_root, name)
            continue
        if flags["new_install_started"]:
            _remove_live_component(config, name)
        if flags["old_existed"]:
            _restore_rollback_component(config, rollback_root, name)


def _raise_fail_after(fail_after: Optional[str], key: str) -> None:
    if fail_after != key:
        return
    raise RestoreCrash(key)


def _move_old_component(
    config: StorageConfig,
    name: str,
    rollback_root: str,
    state: dict[str, bool],
    persist,
    fail_after: Optional[str],
) -> None:
    live = _component_live_path(config, name)
    dest = _rollback_component_path(config, rollback_root, name)
    if name == "database":
        _mkdir_owner(os.path.dirname(dest))
    existed = os.path.lexists(live)
    state["old_existed"] = existed
    persist()
    if not existed:
        return
    state["old_move_started"] = True
    persist()
    _raise_fail_after(fail_after, f"old_move_started:{name}")
    if name == "database":
        os.replace(live, dest)
        base = os.path.basename(live)
        dest_dir = os.path.dirname(dest)
        for suffix in ("-wal", "-shm", "-journal"):
            side = live + suffix
            if os.path.lexists(side):
                os.replace(side, os.path.join(dest_dir, base + suffix))
    else:
        os.replace(live, dest)
    _raise_fail_after(fail_after, f"old_renamed:{name}")
    state["old_moved"] = True
    persist()
    if fail_after == "old_moved" and name == "database":
        raise RuntimeError("test_fail_after_old_moved")


def _install_new_component(
    config: StorageConfig,
    name: str,
    tree_dir: str,
    state: dict[str, bool],
    persist,
    fail_after: Optional[str],
    *,
    processing_in_backup: bool,
) -> None:
    live = _component_live_path(config, name)
    staged = _component_staged_path(tree_dir, name)
    state["new_install_started"] = True
    persist()
    _raise_fail_after(fail_after, f"new_install_started:{name}")

    def _install_empty_dir() -> None:
        _mkdir_owner(live)
        state["new_installed"] = True
        persist()

    if name == "processing" and not processing_in_backup:
        _install_empty_dir()
        return
    if name in {"pdfs", "people", "processing"} and not os.path.isdir(staged):
        _install_empty_dir()
        return
    if not os.path.lexists(staged):
        if name in {"pdfs", "people", "processing"}:
            _install_empty_dir()
            return
        raise RestoreError("missing_database", "Backup archive is not a valid PRKS backup.")
    parent = os.path.dirname(live)
    if parent:
        _mkdir_owner(parent)
    os.replace(staged, live)
    _raise_fail_after(fail_after, f"new_renamed:{name}")
    state["new_installed"] = True
    persist()
    if fail_after == "new_installed" and name == "pdfs":
        raise RuntimeError("test_fail_after_new_installed")


def apply_restore(
    config: StorageConfig,
    token: str,
    confirm: str,
    *,
    rebind: RebindFn,
    fail_after: Optional[str] = None,
) -> dict[str, Any]:
    """Replace live canonical storage with a verified staged backup."""
    _assert_testing_safe(config)
    if confirm != CONFIRM_RESTORE:
        raise RestoreError(
            "confirmation_required",
            "Restore was not confirmed.",
            http_status=400,
        )
    meta = _load_staging_meta(config, token)
    staging_dir = _staging_dir(config, token)
    tree_dir = os.path.join(staging_dir, "tree")
    staged_db = _component_staged_path(tree_dir, "database")
    if not os.path.isfile(staged_db):
        _discard_maintenance_child(config, _STAGING_SUBROOT, staging_dir)
        raise RestoreError("unknown_token", "Backup is not available for restore.", http_status=404)

    db_schema = read_schema_version(staged_db)
    if db_schema > supported_schema_ceiling(config):
        _discard_maintenance_child(config, _STAGING_SUBROOT, staging_dir)
        raise RestoreError(
            "schema_newer",
            "This backup was created by a newer PRKS database version. Update PRKS before restoring it.",
        )

    restore_processing = processing_is_under_storage(config)
    processing_in_backup = bool(meta.get("processing_included"))
    warnings: list[str] = []
    if processing_in_backup and not restore_processing:
        warnings.append(
            "Processing queue files in this backup were not restored because the current processing directory is outside PRKS storage."
        )

    if _free_bytes(config.root) < DISK_MARGIN_BYTES:
        raise RestoreError(
            "insufficient_storage",
            "Not enough free storage to restore this backup safely.",
        )

    txn = secrets.token_urlsafe(16)
    rollback_root = _rollback_dir(config, txn)
    _mkdir_owner(rollback_root)
    components: dict[str, dict[str, bool]] = {
        "database": _empty_component_state(),
        "pdfs": _empty_component_state(),
        "people": _empty_component_state(),
    }
    if restore_processing:
        components["processing"] = _empty_component_state()
    journal = {
        "format": "prks-restore-journal",
        "format_version": 1,
        "transaction_id": txn,
        "phase": "prepared",
        "components": components,
        "staging_token": token,
    }
    _write_journal(config, journal)
    LOGGER.info("restore_started schema_version=%s", db_schema)
    rolled_back = False
    committed = False

    def _mark(phase: str) -> None:
        journal["phase"] = phase
        journal["components"] = components
        _write_journal(config, journal)

    def _rollback_once(*, rebind_after: bool) -> None:
        nonlocal rolled_back
        if rolled_back:
            return
        rolled_back = True
        _rollback_from_journal(config, journal)
        if rebind_after:
            clear_derived_storage(config)
            try:
                rebind(config)
            except Exception as rebind_exc:
                LOGGER.error(
                    "restore_rolled_back reason=rebind_previous_failed error_type=%s",
                    safe_error_type(rebind_exc),
                )
        # Journal first, for the same reason recovery does: a surviving journal
        # whose rollback tree was already removed is the dangerous combination.
        _discard_maintenance_child(config, _JOURNAL_SUBROOT, journal_path(config))
        _discard_maintenance_child(config, _ROLLBACK_SUBROOT, rollback_root)

    t_commit = clock_ns()
    try:
        _mark("moving_old")
        for name in list(components):
            _move_old_component(
                config,
                name,
                rollback_root,
                components[name],
                lambda: _mark("moving_old"),
                fail_after,
            )

        _mark("installing_new")
        for name in list(components):
            _install_new_component(
                config,
                name,
                tree_dir,
                components[name],
                lambda: _mark("installing_new"),
                fail_after,
                processing_in_backup=processing_in_backup,
            )

        ok, reason = sqlite_integrity_report(config.db_path)
        if not ok:
            raise RestoreError(reason, "Backup database failed integrity verification.")
        if restore_processing:
            _rewrite_processing_abs_paths(config.db_path, config.processing_dir)
        _mark("canonical_installed")

        clear_derived_storage(config)
        try:
            rebind(config)
        except Exception as exc:
            LOGGER.error(
                "restore_rolled_back reason=rebind_failed error_type=%s",
                safe_error_type(exc),
            )
            raise RestoreError(
                "restore_failed",
                "Restore failed. Current PRKS data was not changed.",
                http_status=500,
            ) from exc

        import backend.server as server_module
        import backend.text_index as text_index_module
        import backend.research_index as research_index_module

        db_obj = server_module.db
        index_obj = text_index_module.get_text_index()
        index_summary = {"processed": 0, "indexed": 0, "failed": 0}
        try:
            index_summary = index_obj.reconcile_all(db_obj, force=False)
        except Exception as exc:
            LOGGER.error(
                "restore_reindex_failed error_type=%s",
                safe_error_type(exc),
            )
            warnings.append("Search index rebuild reported a failure for some PDFs.")
        else:
            failed = int(index_summary.get("failed") or 0)
            if failed:
                warnings.append(
                    f"Search index rebuilt with {failed} PDF text extraction failure{'s' if failed != 1 else ''}."
                )
        try:
            research_index_module.get_research_index().reconcile_all(db_obj)
        except Exception as exc:
            LOGGER.error(
                "restore_research_index_failed error_type=%s",
                safe_error_type(exc),
            )

        summary = library_summary_from_db(config.db_path)
        _mark("committed")
        committed = True
        LOGGER.info(
            "restore_committed schema_version=%s works=%s persons=%s",
            PRKS_SCHEMA_VERSION,
            summary["works"],
            summary["persons"],
        )
        _discard_maintenance_child(config, _JOURNAL_SUBROOT, journal_path(config))
        _discard_maintenance_child(config, _ROLLBACK_SUBROOT, rollback_root)
        _discard_maintenance_child(config, _STAGING_SUBROOT, staging_dir)
        return {
            "restored": True,
            "works": summary["works"],
            "persons": summary["persons"],
            "annotations": summary["annotations"],
            "pdfs": summary["managed_pdfs"],
            "warnings": warnings,
            "reload_required": True,
            "index": index_summary,
        }
    except RestoreCrash:
        raise
    except RestoreError:
        LOGGER.error("restore_rolled_back reason=restore_error")
        if not committed:
            try:
                _rollback_once(rebind_after=True)
            except Exception as exc:
                LOGGER.error("restore_rolled_back reason=rollback_failed error_type=%s", safe_error_type(exc))
            _discard_maintenance_child(config, _STAGING_SUBROOT, staging_dir)
        raise
    except Exception as exc:
        LOGGER.error("restore_rolled_back reason=internal error_type=%s", safe_error_type(exc))
        if committed:
            raise RestoreError(
                "restore_failed",
                "Restore failed. Current PRKS data was not changed.",
                http_status=500,
            ) from exc
        try:
            _rollback_once(rebind_after=True)
        except Exception as rollback_exc:
            LOGGER.error(
                "restore_rolled_back reason=rollback_failed error_type=%s",
                safe_error_type(rollback_exc),
            )
        _discard_maintenance_child(config, _STAGING_SUBROOT, staging_dir)
        raise RestoreError(
            "restore_failed",
            "Restore failed. Current PRKS data was not changed.",
            http_status=500,
        ) from exc
    finally:
        try:
            record_span("restore_commit", clock_ns() - t_commit)
        except Exception:
            pass


def recover_incomplete_restore(config: StorageConfig) -> dict[str, Any]:
    """Run before bind_storage. Do not open the main database here."""
    _assert_testing_safe(config)
    path = journal_path(config)
    result = {"performed": False, "outcome": None, "needs_reindex": False}
    if not os.path.lexists(path):
        cleanup_stale_staging(config)
        return result
    try:
        journal = _read_journal_file(path)
    except RestoreError:
        LOGGER.error("restore_recovery_failed reason=journal_invalid")
        raise
    except Exception as exc:
        LOGGER.error("restore_recovery_failed reason=journal_invalid error_type=%s", safe_error_type(exc))
        raise RestoreError(
            "journal_invalid",
            "Incomplete restore could not be recovered.",
            http_status=500,
        ) from exc

    phase = journal.get("phase")
    txn = journal["transaction_id"]
    rollback_root = _rollback_dir(config, txn)
    token = journal.get("staging_token")
    staging_dir = _staging_dir(config, token) if isinstance(token, str) and _TOKEN_RE.fullmatch(token) else None

    # The journal goes first in both branches and its removal must be confirmed.
    # Rollback and staging state outlive it, so a failure here leaves a
    # recoverable library instead of a journal that replays against material
    # that has already been cleaned away.
    if phase == "committed":
        _remove_journal_or_fail(config)
        _discard_maintenance_child(config, _ROLLBACK_SUBROOT, rollback_root)
        if staging_dir:
            _discard_maintenance_child(config, _STAGING_SUBROOT, staging_dir)
        cleanup_stale_staging(config)
        LOGGER.info("restore_recovery_completed outcome=keep_restored")
        return {"performed": True, "outcome": "keep_restored", "needs_reindex": False}

    _rollback_from_journal(config, journal)
    _remove_journal_or_fail(config)
    _discard_maintenance_child(config, _ROLLBACK_SUBROOT, rollback_root)
    if staging_dir:
        _discard_maintenance_child(config, _STAGING_SUBROOT, staging_dir)
    cleanup_stale_staging(config)
    LOGGER.info("restore_recovery_completed outcome=restored_previous")
    return {"performed": True, "outcome": "restored_previous", "needs_reindex": False}


def new_staging_upload_path(config: StorageConfig) -> str:
    _assert_testing_safe(config)
    root = _ensure_maintenance_dirs(config)
    return os.path.join(root, *_STAGING_SUBROOT, ".upload-" + secrets.token_urlsafe(8))


def discard_temp_path(path: str) -> None:
    _safe_remove(path)


def stream_upload_to_file(
    reader,
    dest_path: str,
    *,
    expected_length: int,
    max_bytes: int,
    chunk_size: int = IO_CHUNK_SIZE,
) -> int:
    if expected_length < 0:
        raise RestoreError("invalid_length", "invalid Content-Length")
    if expected_length > max_bytes:
        raise RestoreError("request_too_large", "Backup is larger than the configured upload limit.")
    parent = os.path.dirname(dest_path)
    _mkdir_owner(parent)
    written = 0
    try:
        with open(dest_path, "wb") as handle:
            remaining = expected_length
            while remaining > 0:
                chunk = reader.read(min(chunk_size, remaining))
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
                remaining -= len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        _chmod_file(dest_path)
    except Exception:
        _safe_remove(dest_path)
        raise
    if written != expected_length:
        _safe_remove(dest_path)
        raise RestoreError("incomplete_upload", "Backup upload was incomplete.")
    return written
