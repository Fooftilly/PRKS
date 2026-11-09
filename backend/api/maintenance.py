"""Backup and restore over HTTP on the PostgreSQL Library (§11.4; PG-C1 #640).

The routes keep the SQLite runtime's contract, so the Settings page drives
both, but every step is the PostgreSQL maintenance lane's:

- ``POST /api/backups/progress`` queues ``backup.create`` for the actor's
  Library (:func:`backend.domain.maintenance.jobs.request_backup`, or follows
  the one already queued or running) and streams NDJSON progress lines until
  the job ends. The worker writes and verifies the archive; the last line is
  ``ready`` with the backup run's id as the download token, or ``failed``.
- ``GET /api/backups/download?token=<run>`` serves a ``published`` archive
  of the actor's Library from the root's archive directory.
- ``POST /api/backups/stage`` streams an uploaded archive into
  ``<root>/.prks-maintenance/restore-staged``, verifies its structure,
  hashes and schema (R0) and answers its summary and a token. The token is
  bound to the uploading user: another account's token resolves to nothing.
- ``POST /api/backups/restore`` ``{"token", "confirm": "RESTORE"}`` restores
  the staged archive over the actor's Library
  (:func:`backend.domain.maintenance.restore.restore_library`) from a private,
  locked copy, then removes the staged file. The answer carries the new
  epoch, so the browser's pending work from before is fenced (§10.4).

Every route requires ``ADMIN_LIBRARY`` in its own authorized snapshot first,
so only an owner learns whether a backup or a staged upload exists.
``AccessRefused`` propagates to ``server.py``, which answers it as every
PostgreSQL route does. psycopg is imported only once a route runs, never by
the SQLite runtime.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import uuid
from typing import Any, Callable, Optional

LOGGER = logging.getLogger("prks.api.maintenance")

STAGED_DIRNAME = os.path.join(".prks-maintenance", "restore-staged")
# A staged upload nobody restored is removed by the next stage after this long.
STAGED_TTL_SECONDS = 60 * 60
# How often progress polls the backup job, and how long it follows it.
POLL_SECONDS = 0.25
BACKUP_TIMEOUT_SECONDS = 30 * 60
CONFIRM = "RESTORE"
_STAGED_SUFFIX = ".staged"
_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_CHUNK = 64 * 1024

_JOB_SQL = (
    "SELECT state, progress FROM prks.jobs WHERE library_id = %(library_id)s AND id = %(job_id)s"
)
_ACTIVE_JOB_SQL = (
    "SELECT id FROM prks.jobs WHERE library_id = %(library_id)s AND kind = %(kind)s"
    " AND state IN ('queued', 'running') ORDER BY created_at LIMIT 1"
)
_RUN_SQL = "SELECT state FROM prks.backup_runs WHERE library_id = %(library_id)s AND id = %(run_id)s"


def staged_dir(root: str) -> str:
    """``<root>/.prks-maintenance/restore-staged``, ``0700``, created on first use."""
    path = os.path.join(root, STAGED_DIRNAME)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def _staged_path(root: str, token: str, user_id: str) -> str:
    owner = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:32]
    return os.path.join(staged_dir(root), f"{token}.{owner}{_STAGED_SUFFIX}")


def prune_staged(root: str, *, now: Optional[float] = None) -> int:
    """Remove staged uploads older than :data:`STAGED_TTL_SECONDS`."""
    removed = 0
    cutoff = (time.time() if now is None else now) - STAGED_TTL_SECONDS
    directory = staged_dir(root)
    for name in os.listdir(directory):
        if not name.endswith(_STAGED_SUFFIX):
            continue
        path = os.path.join(directory, name)
        try:
            if os.lstat(path).st_mtime < cutoff:
                os.unlink(path)
                removed += 1
        except FileNotFoundError:
            pass
    return removed


def _authorize(library: Any, ctx: Any) -> None:
    """Refuse anyone who may not administer the Library, before anything is read or written."""
    from backend.domain.identity.authorization import Capability
    from backend.domain.identity.library_scope import library_query

    library_query(library.database, ctx, lambda scope: None, capability=Capability.ADMIN_LIBRARY)


def _admin_read(library: Any, ctx: Any, fn: Callable[[Any], Any]) -> Any:
    from backend.domain.identity.authorization import Capability
    from backend.domain.identity.library_scope import library_query

    return library_query(library.database, ctx, fn, capability=Capability.ADMIN_LIBRARY)


# -- backup -----------------------------------------------------------------------------


def stream_backup(library: Any, ctx: Any, write_line: Callable[[dict[str, Any]], None], *,
                  timeout: float = BACKUP_TIMEOUT_SECONDS, poll: float = POLL_SECONDS) -> None:
    """Queue (or follow) the Library's backup job and write its progress until it ends."""
    from backend.domain.maintenance import jobs as backup_jobs
    from backend.domain.maintenance.format import BACKUP_EXTENSION

    job_id = backup_jobs.request_backup(library.database, ctx)
    if job_id is None:
        row = _admin_read(library, ctx, lambda scope: scope.execute(
            _ACTIVE_JOB_SQL, {"kind": backup_jobs.BACKUP_KIND}).fetchone())
        if row is None:  # it ended between the two reads: queue a new one
            job_id = backup_jobs.request_backup(library.database, ctx)
        else:
            job_id = row["id"]
    if job_id is None:
        write_line({"phase": "failed", "percent": 0, "error": "Another backup is starting; try again."})
        return
    write_line({"phase": "snapshot", "percent": 1})
    deadline = time.monotonic() + timeout
    percent = 1
    while True:
        row = _admin_read(library, ctx, lambda scope: scope.execute(
            _JOB_SQL, {"job_id": job_id}).fetchone())
        state = row["state"] if row is not None else "cancelled"
        if state == "succeeded":
            run = str(((row["progress"] or {}) if row is not None else {}).get("backup_run") or "")
            if not run:
                break
            token = uuid.UUID(run).hex
            write_line({"phase": "ready", "percent": 100, "token": token,
                        "filename": f"prks-{token}{BACKUP_EXTENSION}", "warnings": []})
            return
        if state not in ("queued", "running"):
            break
        if time.monotonic() >= deadline:
            write_line({"phase": "failed", "percent": 0,
                        "error": "The backup is still running; download it from Settings later."})
            return
        percent = min(95, percent + 1)
        write_line({"phase": "snapshot" if state == "queued" else "objects", "percent": percent})
        time.sleep(poll)
    write_line({"phase": "failed", "percent": 0, "error": "Backup could not be created."})


def archive_for_download(library: Any, ctx: Any, root: str, token: str) -> Optional[str]:
    """The published archive ``token`` names in the actor's Library, or None."""
    from backend.domain.maintenance import jobs as backup_jobs
    from backend.domain.maintenance.format import BACKUP_EXTENSION

    if not _TOKEN_RE.fullmatch(token or ""):
        _authorize(library, ctx)
        return None
    run_id = uuid.UUID(token)
    row = _admin_read(library, ctx, lambda scope: scope.execute(_RUN_SQL, {"run_id": run_id}).fetchone())
    if row is None or row["state"] != "published":
        return None
    path = os.path.join(backup_jobs.archive_dir(root), f"{run_id}{BACKUP_EXTENSION}")
    return path if os.path.isfile(path) else None


# -- restore ----------------------------------------------------------------------------


class StageRefused(Exception):
    def __init__(self, status: int, reason: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.message = message


def stage_upload(library: Any, ctx: Any, root: str, receive: Callable[[str], None]) -> dict[str, Any]:
    """Receive an archive with ``receive(path)``, verify it (R0) and keep it for a restore."""
    from backend.domain.maintenance import jobs as backup_jobs
    from backend.domain.maintenance.archive import ArchiveRefused, check_schema, open_archive
    from backend.domain.maintenance.connection import maintenance_connection

    _authorize(library, ctx)
    prune_staged(root)
    token = uuid.uuid4().hex
    path = _staged_path(root, token, ctx.user_id)
    try:
        receive(path)
        with maintenance_connection(library.database.config.conninfo) as conn, \
                open_archive(path, staging_dir=backup_jobs.restore_dir(root)) as archive:
            check_schema(conn, archive)
            summary = _summary(archive)
    except ArchiveRefused as exc:
        _discard(path)
        raise StageRefused(400, exc.reason, exc.message) from None
    except BaseException:
        _discard(path)
        raise
    return {"token": token, "verified": True, "summary": summary, "warnings": []}


def _summary(archive: Any) -> dict[str, Any]:
    from backend.storage.objects import NAMESPACE_ASSET_OBJECTS

    def rows(table: str) -> Optional[int]:
        member = archive.tables.get("prks." + table)
        return member.rows if member is not None else None

    return {"created_at": archive.manifest.get("created_at"), "works": rows("works"),
            # Only Asset bytes count as files; portraits and other objects do not.
            "pdf_files": sum(1 for key in archive.objects if key.namespace == NAMESPACE_ASSET_OBJECTS),
            "persons": rows("persons"),
            "annotations": rows("annotations")}


def _discard(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def restore_staged(library: Any, ctx: Any, root: str, data: Any) -> tuple[int, dict[str, Any], Optional[int]]:
    """Restore a staged archive over the actor's Library: ``(status, body, new epoch or None)``."""
    from backend.domain.maintenance import jobs as backup_jobs
    from backend.domain.maintenance import restore
    from backend.domain.objects.jobs import root_storage

    if not isinstance(data, dict):
        return 400, {"error": "JSON object body required"}, None
    _authorize(library, ctx)
    token = data.get("token")
    if data.get("confirm") != CONFIRM:
        return 400, {"error": "Type RESTORE to confirm.", "reason": "confirmation_required"}, None
    if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token.strip()):
        return 400, {"error": "Backup is not available.", "reason": "unknown_token"}, None
    staged = _staged_path(root, token.strip(), ctx.user_id)
    if not os.path.isfile(staged):
        return 400, {"error": "Backup is not available.", "reason": "unknown_token"}, None
    storage = root_storage(root)
    if storage is None:
        return 409, {"error": "This data root still holds a SQLite catalogue.", "reason": "sqlite_catalogue"}, None
    try:
        with backup_jobs.private_copy(staged, root) as private:
            result = restore.restore_library(library.database.config.conninfo, storage, private,
                                             ctx.library_id, owner_user_id=ctx.user_id,
                                             staging_dir=backup_jobs.restore_dir(root),
                                             expected_epoch=ctx.epoch)
    except restore.RestoreFailed as exc:
        return 409, {"error": f"Restore failed. Current PRKS data was not changed. {exc.message}",
                     "reason": exc.reason}, None
    except backup_jobs.CopyFailed as exc:
        return 507, {"error": "The backup could not be copied for the restore; nothing was changed.",
                     "reason": exc.reason}, None
    _discard(staged)
    LOGGER.info("restore_committed epoch=%d objects_held=%d", result.epoch, result.objects_held)
    return 200, {"status": "restored", "epoch": result.epoch, "objects_held": result.objects_held,
                 "warnings": []}, result.epoch


def ndjson(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
