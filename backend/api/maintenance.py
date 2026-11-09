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
  hashes and schema (R0) and answers its summary and a token
  (:mod:`backend.domain.maintenance.staged`). The token is bound to the
  uploading user: another account's token resolves to nothing.
- ``POST /api/backups/restore`` ``{"token", "confirm": "RESTORE"}`` restores
  the staged archive over the actor's Library
  (:func:`backend.domain.maintenance.restore.restore_library`) from a private,
  locked copy, then removes the staged file. The answer carries the new
  epoch, so the browser's pending work from before is fenced (§10.4).

Every route requires ``ADMIN_LIBRARY`` in its own authorized snapshot first,
so only an owner learns whether a backup or a staged upload exists.
``AccessRefused`` propagates to ``server.py``, which answers it as every
PostgreSQL route does. Maintenance SQL and the staged files stay in
``backend/domain/maintenance``; this module maps requests and answers. psycopg
is imported only once a route runs, never by the SQLite runtime.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Callable, Optional

LOGGER = logging.getLogger("prks.api.maintenance")

# How often progress polls the backup job, and how long it follows it.
POLL_SECONDS = 0.25
BACKUP_TIMEOUT_SECONDS = 30 * 60
CONFIRM = "RESTORE"


# -- backup -----------------------------------------------------------------------------


def stream_backup(library: Any, ctx: Any, write_line: Callable[[dict[str, Any]], None], *,
                  timeout: float = BACKUP_TIMEOUT_SECONDS, poll: float = POLL_SECONDS) -> None:
    """Queue (or follow) the Library's backup job and write its progress until it ends."""
    from backend.domain.maintenance import jobs as backup_jobs
    from backend.domain.maintenance.format import BACKUP_EXTENSION

    job_id = backup_jobs.request_backup(library.database, ctx)
    if job_id is None:
        job_id = backup_jobs.active_backup_job(library.database, ctx)
        if job_id is None:  # it ended between the two reads: queue a new one
            job_id = backup_jobs.request_backup(library.database, ctx)
    if job_id is None:
        write_line({"phase": "failed", "percent": 0, "error": "Another backup is starting; try again."})
        return
    write_line({"phase": "snapshot", "percent": 1})
    deadline = time.monotonic() + timeout
    percent = 1
    while True:
        status = backup_jobs.backup_job_status(library.database, ctx, job_id)
        state, progress = status if status is not None else ("cancelled", {})
        if state == "succeeded":
            run = str(progress.get("backup_run") or "")
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
    from backend.domain.maintenance.staged import TOKEN_RE

    if not TOKEN_RE.fullmatch(token or ""):
        backup_jobs.authorize_admin(library.database, ctx)
        return None
    return backup_jobs.published_archive(library.database, ctx, root, uuid.UUID(token))


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
    from backend.domain.maintenance import staged
    from backend.domain.maintenance.archive import ArchiveRefused

    backup_jobs.authorize_admin(library.database, ctx)
    try:
        result = staged.stage_archive(library.database.config.conninfo, root, ctx.user_id,
                                      backup_jobs.restore_dir(root), receive)
    except ArchiveRefused as exc:
        raise StageRefused(400, exc.reason, exc.message) from None
    return {"token": result.token, "verified": True, "summary": result.summary, "warnings": []}


def restore_staged(library: Any, ctx: Any, root: str, data: Any) -> tuple[int, dict[str, Any], Optional[int]]:
    """Restore a staged archive over the actor's Library: ``(status, body, new epoch or None)``."""
    from backend.domain.maintenance import jobs as backup_jobs
    from backend.domain.maintenance import restore, staged
    from backend.domain.objects.jobs import root_storage

    if not isinstance(data, dict):
        return 400, {"error": "JSON object body required"}, None
    backup_jobs.authorize_admin(library.database, ctx)
    token = data.get("token")
    if data.get("confirm") != CONFIRM:
        return 400, {"error": "Type RESTORE to confirm.", "reason": "confirmation_required"}, None
    archive = staged.find_staged(root, token.strip(), ctx.user_id) if isinstance(token, str) else None
    if archive is None:
        return 400, {"error": "Backup is not available.", "reason": "unknown_token"}, None
    storage = root_storage(root)
    if storage is None:
        return 409, {"error": "This data root still holds a SQLite catalogue.", "reason": "sqlite_catalogue"}, None
    try:
        with backup_jobs.private_copy(archive, root) as private:
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
    staged.discard(archive)
    LOGGER.info("restore_committed epoch=%d objects_held=%d", result.epoch, result.objects_held)
    return 200, {"status": "restored", "epoch": result.epoch, "objects_held": result.objects_held,
                 "warnings": []}, result.epoch


def ndjson(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
