"""The ``backup.create`` job and the backup/restore recovery task (L-B-1 #639; §11.4, §12.1).

- :func:`request_backup` enqueues ``backup.create`` for the actor's Library in
  an ``ADMIN_LIBRARY`` command, so it is authorized, maintenance-checked and
  epoch-fenced like every command; one is queued or running per Library at a
  time (its dedupe and singleton keys).
- :meth:`BackupMaintenance.backup` is its handler: it confirms the lease,
  then runs :func:`backup.write_backup` into the data root's private archive
  directory (:func:`archive_dir`), heartbeating at every phase, and records
  the ``backup_runs`` id in the job's progress. The backup holds its own
  run lease; the job's lease only decides which worker runs it.
- :meth:`BackupMaintenance.tick`, the worker's idle task, runs
  :func:`restore.recover` at most once per :data:`RECOVER_EVERY` (expired
  restore and backup runs, stale staging schemas) and deletes archive files
  whose backup run did not publish them, and private restore copies no live
  ``prks admin restore`` holds (:func:`private_copy`). The worker runs it
  once at startup, before its first claim.

The archive directory is ``<root>/.prks-maintenance/backups``, ``0700``;
archives are named by their run and created ``0600`` with exclusive create.
A restore reads its archive from a private copy in
``<root>/.prks-maintenance/restore``, locked by the restoring process for as
long as it exists, so a crashed restore's copy is removed by the next pass and
a live one never is.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import re
import shutil
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any, LiteralString, Optional

import psycopg

from backend.db.database import Database
from backend.db.errors import map_database_error
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command, library_query
from backend.domain.identity.request_context import RequestContext
from backend.domain.maintenance import backup, restore
from backend.domain.maintenance.connection import maintenance_connection
from backend.domain.maintenance.format import BACKUP_EXTENSION, PRKS_VERSION
from backend.jobs import queue
from backend.jobs.handlers import HandlerRegistry
from backend.jobs.runner import JobRun
from backend.storage.file_lock import ExclusiveFileLock, LockBusy, LockUnavailable
from backend.storage.objects import StorageBackend

BACKUP_KIND = "backup.create"
RECOVER_EVERY = timedelta(minutes=1)
ARCHIVE_DIRNAME = os.path.join(".prks-maintenance", "backups")
RESTORE_DIRNAME = os.path.join(".prks-maintenance", "restore")
_COPY_SUFFIX = ".upload"
_COPY_CHUNK = 1024 * 1024

LOGGER = logging.getLogger("prks.maintenance")

_ARCHIVE_NAME_RE = re.compile(r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
                              + re.escape(BACKUP_EXTENSION) + r"$")
_RUN_STATE_SQL: LiteralString = "SELECT state FROM prks.backup_runs WHERE id = %s"
_REQUEST_LOCK_SQL: LiteralString = "SELECT pg_advisory_xact_lock(hashtextextended('prks.backup_request:' || %s, 0))"
_ACTIVE_BACKUP_SQL: LiteralString = (
    "SELECT 1 FROM prks.jobs WHERE library_id = %(library_id)s AND kind = %(kind)s"
    " AND state IN ('queued', 'running') LIMIT 1"
)
_ACTIVE_BACKUP_JOB_SQL: LiteralString = (
    "SELECT id FROM prks.jobs WHERE library_id = %(library_id)s AND kind = %(kind)s"
    " AND state IN ('queued', 'running') ORDER BY created_at LIMIT 1"
)
_JOB_STATUS_SQL: LiteralString = (
    "SELECT state, progress FROM prks.jobs WHERE library_id = %(library_id)s AND id = %(job_id)s"
)
_LIBRARY_RUN_STATE_SQL: LiteralString = (
    "SELECT state FROM prks.backup_runs WHERE library_id = %(library_id)s AND id = %(run_id)s"
)


def archive_dir(root: str) -> str:
    """``<root>/.prks-maintenance/backups``, created ``0700`` if missing."""
    path = os.path.join(root, ARCHIVE_DIRNAME)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def restore_dir(root: str) -> str:
    """``<root>/.prks-maintenance/restore``, created ``0700`` if missing."""
    path = os.path.join(root, RESTORE_DIRNAME)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


class CopyFailed(Exception):
    """The private restore copy could not be made; ``reason`` is an errno or lock code, never a path."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _copy_reason(exc: BaseException) -> str:
    if isinstance(exc, LockUnavailable):
        return exc.reason
    if isinstance(exc, OSError) and exc.errno is not None:
        return errno.errorcode.get(exc.errno, "EIO")
    return "copy_failed"


@contextlib.contextmanager
def private_copy(path: str, root: str) -> Iterator[str]:
    """A ``0600`` copy of ``path`` in :func:`restore_dir`, so it cannot change underneath a restore.

    The copy is locked from the moment it exists until it is removed on exit,
    so :func:`prune_restore_copies` removes only the copies of a process that
    died before its own cleanup. Raises :class:`CopyFailed` when the copy
    cannot be made (a full disk, a permission, a lock refusal), with any
    partial copy removed; errors from the body pass through unchanged.
    """
    try:
        target = os.path.join(restore_dir(root), f"{uuid.uuid4()}{_COPY_SUFFIX}")
        lock = ExclusiveFileLock.acquire(target)  # creates the file 0600, locked
    except (OSError, LockBusy, LockUnavailable) as exc:
        raise CopyFailed(_copy_reason(exc)) from exc
    try:
        try:
            try:
                fd = os.open(target, os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(fd, "wb") as out, open(path, "rb") as source:
                    shutil.copyfileobj(source, out, _COPY_CHUNK)
            except OSError as exc:
                raise CopyFailed(_copy_reason(exc)) from exc
            yield target
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(target)
    finally:
        lock.release()


def prune_restore_copies(root: str) -> int:
    """Delete private restore copies whose restoring process is gone (no one holds their lock)."""
    directory = os.path.join(root, RESTORE_DIRNAME)
    removed = 0
    names = sorted(os.listdir(directory)) if os.path.isdir(directory) else []
    for name in names:
        if not name.endswith(_COPY_SUFFIX):
            continue
        path = os.path.join(directory, name)
        try:
            lock = ExclusiveFileLock.acquire(path)
        except (LockBusy, LockUnavailable):
            continue  # a live restore holds it, or it is not a regular file
        try:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(path)
                removed += 1
        finally:
            lock.release()
    return removed


def request_backup(db: Database, ctx: RequestContext) -> Optional[uuid.UUID]:
    """Queue a backup of the actor's Library; None when one is already queued or running.

    The dedupe key only matches a queued job, so the check for a queued or
    running one comes first, under a per-Library transaction lock: a request
    that waited on another sees its job whether a worker has claimed it yet
    or not.
    """
    def enqueue(scope: LibraryScope) -> Optional[uuid.UUID]:
        # Requests of one Library take turns, so the check below always sees a
        # competing request's job (READ COMMITTED: a fresh snapshot after the wait).
        scope.execute_unscoped(_REQUEST_LOCK_SQL, (scope.library_id,))
        if scope.execute(_ACTIVE_BACKUP_SQL, {"kind": BACKUP_KIND}).fetchone() is not None:
            return None
        key = f"backup:{scope.library_id}"
        return queue.enqueue_library_job(scope, BACKUP_KIND, dedupe_key=key, singleton_key=key, max_attempts=1,
                                         created_by=ctx.user_id)

    return library_command(db, ctx, Capability.ADMIN_LIBRARY, enqueue)


def authorize_admin(db: Database, ctx: RequestContext) -> None:
    """Refuse anyone who may not administer the actor's Library (raises ``AccessRefused``)."""
    library_query(db, ctx, lambda scope: None, capability=Capability.ADMIN_LIBRARY)


def active_backup_job(db: Database, ctx: RequestContext) -> Optional[uuid.UUID]:
    """The actor's Library's queued or running ``backup.create`` job, or None."""
    row = library_query(db, ctx, lambda scope: scope.execute(
        _ACTIVE_BACKUP_JOB_SQL, {"kind": BACKUP_KIND}).fetchone(), capability=Capability.ADMIN_LIBRARY)
    return row["id"] if row is not None else None


def backup_job_status(db: Database, ctx: RequestContext,
                      job_id: uuid.UUID) -> Optional[tuple[str, dict[str, Any]]]:
    """``(state, progress)`` of a job of the actor's Library, or None when it is gone."""
    row = library_query(db, ctx, lambda scope: scope.execute(
        _JOB_STATUS_SQL, {"job_id": job_id}).fetchone(), capability=Capability.ADMIN_LIBRARY)
    return (row["state"], row["progress"] or {}) if row is not None else None


def published_archive(db: Database, ctx: RequestContext, root: str, run_id: uuid.UUID) -> Optional[str]:
    """The archive of a ``published`` backup run of the actor's Library, or None."""
    row = library_query(db, ctx, lambda scope: scope.execute(
        _LIBRARY_RUN_STATE_SQL, {"run_id": run_id}).fetchone(), capability=Capability.ADMIN_LIBRARY)
    if row is None or row["state"] != "published":
        return None
    path = os.path.join(archive_dir(root), f"{run_id}{BACKUP_EXTENSION}")
    return path if os.path.isfile(path) else None


def prune_archives(conninfo: str, directory: str) -> int:
    """Delete archive files whose backup run is not ``published`` (failed, crashed or unknown)."""
    removed = 0
    names = sorted(os.listdir(directory)) if os.path.isdir(directory) else []
    with maintenance_connection(conninfo) as conn:
        for name in names:
            match = _ARCHIVE_NAME_RE.fullmatch(name)
            if match is None:
                continue
            row = conn.execute(_RUN_STATE_SQL, (uuid.UUID(match.group(1)),)).fetchone()
            if row is not None and row[0] in ("running", "published"):
                continue
            try:
                os.unlink(os.path.join(directory, name))
                removed += 1
            except FileNotFoundError:
                pass
    return removed


def recover_now(conninfo: str, root: Optional[str] = None) -> restore.Recovered:
    """One recovery pass (startup and the idle task)."""
    with maintenance_connection(conninfo) as conn:
        recovered = restore.recover(conn)
    if root is not None:
        prune_archives(conninfo, archive_dir(root))
        prune_restore_copies(root)
    return recovered


class BackupMaintenance:
    """The backup lane of one worker, bound to its paired data root."""

    def __init__(self, storage: StorageBackend, root: str, *, recover_every: timedelta = RECOVER_EVERY) -> None:
        self.storage = storage
        self.root = root
        self._recover_every = recover_every.total_seconds()
        self._last_recover: Optional[float] = None
        self._lock = threading.Lock()

    def backup(self, run: JobRun) -> None:
        """The ``backup.create`` handler."""
        library_id = run.job.library_id
        if library_id is None:
            raise ValueError("backup.create is a Library job")
        run.context()  # the lease is still this worker's

        def beat() -> None:
            run.heartbeat()

        hooks = {phase: beat for phase in (backup.PHASE_LOCKED, backup.PHASE_SNAPSHOT,
                                           backup.PHASE_OBJECTS, backup.PHASE_VERIFY)}
        result = backup.write_backup(run.db.config.conninfo, self.storage, library_id, archive_dir(self.root),
                                     prks_version=PRKS_VERSION, hooks=hooks)
        run.heartbeat({"backup_run": str(result.run_id), "archive_bytes": result.size})

    def tick(self, db: Database) -> None:
        """The idle task: one recovery pass at most once per interval."""
        now = time.monotonic()
        with self._lock:
            if self._last_recover is not None and now - self._last_recover < self._recover_every:
                return
            self._last_recover = now
        try:
            recovered = recover_now(db.config.conninfo, self.root)
        except psycopg.Error as exc:
            # The worker reports a DatabaseError and keeps running; the next
            # interval retries. A raw driver error would end its loop.
            raise map_database_error(exc) from exc
        except OSError as exc:
            LOGGER.warning("maintenance_recover_files_failed error=%s", type(exc).__name__)
            return
        if recovered != restore.Recovered(0, 0, 0, 0):
            LOGGER.info("maintenance_recovered restores_failed=%d flags_cleared=%d backups_failed=%d "
                        "schemas_dropped=%d", recovered.restores_failed, recovered.flags_cleared,
                        recovered.backups_failed, recovered.schemas_dropped)


def attach(registry: HandlerRegistry, storage: StorageBackend, root: str) -> BackupMaintenance:
    """Register ``backup.create`` and the recovery task on the worker's own ``registry``."""
    maintenance = BackupMaintenance(storage, root)
    registry.register(BACKUP_KIND, maintenance.backup)
    registry.add_idle_task(maintenance.tick)
    return maintenance
