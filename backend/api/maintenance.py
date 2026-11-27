"""Backup and restore over HTTP on the PostgreSQL Library (§11.4; PG-C1 #640).

Every step is the PostgreSQL maintenance lane's:

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
``AccessRefused`` propagates to the dispatcher (``backend.web.dispatch``),
which answers it as every PostgreSQL route does. Maintenance SQL and the staged files stay in
``backend/domain/maintenance``; this module maps requests and answers.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Callable, Optional

from backend.api_contract.maintenance import (
    BackupProgressLine,
    BackupRefusal,
    BackupStaged,
    Restored,
    RestoreRequest,
)
from backend.api_contract.operation import BINARY, Header, Operation, Param, body, error, ok
from backend.domain.maintenance.upload import RestoreError, backup_max_upload_bytes, stream_upload_to_file
from backend.runtime.log_safety import safe_error_type, safe_log_id
from backend.web.hosts import octet_stream_content_type_allowed
from backend.web.http import Request, Response, Write, json_response
from backend.web.routing import Body, route

LOGGER = logging.getLogger("prks.api.maintenance")

# How often progress polls the backup job.
POLL_SECONDS = 0.25
CONFIRM = "RESTORE"


# -- backup -----------------------------------------------------------------------------


def stream_backup(library: Any, ctx: Any, write_line: Callable[[dict[str, Any]], None], *,
                  poll: float = POLL_SECONDS) -> None:
    """Queue (or follow) the Library's backup job and write its progress until it ends.

    There is no deadline: the download token exists only in the ``ready``
    line, so the stream follows the job for as long as it runs, however
    large the Library. It ends when the job does (a job whose worker died
    fails when its lease is reaped) or when the browser disconnects, which
    ``write_line`` raises; a later request follows the same job while it is
    queued or running.
    """
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


def restore_staged(library: Any, ctx: Any, root: str, data: dict[str, Any]) -> tuple[int, dict[str, Any], Optional[int]]:
    """Restore a staged archive over the actor's Library: ``(status, body, new epoch or None)``."""
    from backend.domain.maintenance import jobs as backup_jobs
    from backend.domain.maintenance import restore, staged
    from backend.domain.objects.jobs import root_storage

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
    try:
        staged.discard(archive)
    except OSError as exc:
        # The restore is committed: a staged file left behind never turns it
        # into a failure the client would answer by keeping the old epoch. The
        # next upload's stage_archive prunes it once its TTL has passed.
        LOGGER.warning("restore_staged_discard_failed error_type=%s", safe_error_type(exc))
    LOGGER.info("restore_committed epoch=%d objects_held=%d", result.epoch, result.objects_held)
    return 200, {"status": "restored", "epoch": result.epoch, "objects_held": result.objects_held,
                 "warnings": []}, result.epoch


def ndjson(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


# -- routes -----------------------------------------------------------------------------


def progress(req: Request) -> Response:
    """``POST /api/backups/progress``: NDJSON progress lines until the backup job ends."""
    from backend.domain.maintenance import jobs as backup_jobs

    library, ctx = req.library, req.ctx
    # Refused before the stream starts, so a non-admin gets the dispatcher's
    # 403 rather than a 200 whose only line says the backup failed.
    backup_jobs.authorize_admin(library.database, ctx)

    def stream(write: Write) -> None:
        def write_line(payload: dict[str, Any]) -> None:
            # Each line is held to the published per-line shape.
            write(ndjson(BackupProgressLine.model_validate(payload).model_dump()))

        try:
            stream_backup(library, ctx, write_line)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            LOGGER.error("backup_progress_failed error_type=%s request_id=%s",
                         safe_error_type(exc), safe_log_id(req.request_id))
            write_line({"phase": "failed", "percent": 0, "error": "Backup could not be created."})

    return Response(200, [("Content-Type", "application/x-ndjson; charset=utf-8"),
                          ("Cache-Control", "no-store")], stream=stream)


def _stage_length(req: Request) -> "int | Response":
    """The upload's declared length, or the refusal of its headers."""
    types = req.headers.get_all("Content-Type")
    if len(types) != 1 or not octet_stream_content_type_allowed(types[0]):
        LOGGER.info("request_rejected reason=unsupported_media_type request_id=%s", safe_log_id(req.request_id))
        return json_response(req, 415, {"error": "unsupported_media_type"})
    raw_length = req.headers.get("Content-Length")
    try:
        content_length = int(raw_length) if raw_length is not None else -1
    except ValueError:
        content_length = -1
    if content_length < 0:
        return json_response(req, 400, {"error": "invalid Content-Length", "code": "invalid_request"})
    if content_length > backup_max_upload_bytes():
        return json_response(req, 413, {"error": "request_too_large"})
    return content_length


def stage(req: Request) -> Response:
    """``POST /api/backups/stage``: stream the uploaded archive to the staging directory.

    The body is read with the Processing upload's idle timeout and deadline,
    so a stalled client cannot hold a request thread; every answer that
    leaves it unread closes the connection.
    """
    from backend.api import processing as processing_api

    root = req.app.require_storage().root
    content_length = _stage_length(req)
    if isinstance(content_length, Response):
        return content_length
    previous_timeout = req.body.gettimeout()

    def receive(dest: str) -> None:
        body = processing_api.TimedBody(req.body, content_length)
        stream_upload_to_file(body, dest, expected_length=content_length, max_bytes=backup_max_upload_bytes())

    try:
        summary = stage_upload(req.library, req.ctx, root, receive)
    except processing_api.UploadTimedOut:
        return json_response(req, 408, {"error": "The backup upload stalled; try again.",
                                        "reason": "upload_timeout", "request_id": req.request_id})
    except StageRefused as exc:
        return json_response(req, exc.status, {"error": exc.message, "reason": exc.reason,
                                               "request_id": req.request_id})
    except RestoreError as exc:
        return json_response(req, exc.http_status, {"error": exc.message, "reason": exc.reason,
                                                    "request_id": req.request_id})
    finally:
        req.body.settimeout(previous_timeout)
    return json_response(req, 200, summary)


def restore(req: Request) -> Response:
    """``POST /api/backups/restore``: restore the staged archive over the actor's Library."""
    status, body, epoch = restore_staged(req.library, req.ctx, req.app.require_storage().root, req.data)
    if epoch is not None:
        # The Library the browser knew is gone: it reads the new epoch.
        req.library_epoch = epoch
    if status != 200:
        body = {**body, "request_id": req.request_id}
    return json_response(req, status, body)


def download(req: Request) -> Response:
    """``GET /api/backups/download?token=<run>``: a published archive of the actor's Library."""
    root = req.app.require_storage().root
    path = archive_for_download(req.library, req.ctx, root, req.query_value("token"))
    if path is None:
        return json_response(req, 404, {"error": "Backup is not available.", "reason": "unknown_token",
                                        "request_id": req.request_id})
    handle = open(path, "rb")
    try:
        size = os.fstat(handle.fileno()).st_size
    except BaseException:
        handle.close()
        raise

    def stream(write: Write) -> None:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                return
            write(chunk)

    return Response(200, [
        ("Content-Type", "application/octet-stream"),
        ("Content-Disposition", f'attachment; filename="{os.path.basename(path)}"'),
        ("Cache-Control", "no-store"),
        ("Content-Length", str(size)),
    ], stream=stream, cleanup=handle.close)


_TAG = "backups"
_NO_STORE = {"Cache-Control": Header("Always no-store.")}

ROUTES = (
    route("POST", "/api/backups/progress", progress, contract=Operation(
        "backupProgress", "Make a backup and stream its progress", _TAG,
        {200: ok({"type": "string", "description": "Newline-delimited JSON: each line is one "
                                                   "BackupProgressLine object."},
                 "One JSON object per line until `ready` (with the download token) or `failed`.",
                 media_type="application/x-ndjson", headers=_NO_STORE, line_schema=BackupProgressLine)},
        request=body({"type": "object"}, "A JSON object, ignored; may be left out.", required=False),
        description="Queues the Library's backup job, or follows the one already queued or running.")),
    route("POST", "/api/backups/stage", stage, Body.STREAM, contract=Operation(
        "stageBackup", "Upload a backup archive and verify it for a restore", _TAG,
        {200: ok(BackupStaged, "Verified; the token names it to the restore."),
         400: error("A bad Content-Length (invalid_request), or an archive that failed verification "
                    "(`reason`).", BackupRefusal),
         408: error("The upload stalled (upload_timeout).", BackupRefusal),
         413: error("Larger than the upload limit (request_too_large).", BackupRefusal),
         415: error("Not sent as application/octet-stream (unsupported_media_type).", BackupRefusal)},
        request=body(BINARY, media_type="application/octet-stream"),
        params=(Param("Content-Length", "header", {"type": "integer", "minimum": 0}, required=True),))),
    route("POST", "/api/backups/restore", restore, contract=Operation(
        "restoreBackup", "Restore a staged archive over the Library", _TAG,
        {200: ok(Restored, "Restored. The answer carries the new PRKS-Library-Epoch."),
         400: error("No `confirm: RESTORE` (confirmation_required) or an unknown token (unknown_token).",
                    BackupRefusal),
         409: error("The restore failed and nothing changed (`reason`).", BackupRefusal),
         507: error("The archive could not be copied for the restore; nothing changed.", BackupRefusal)},
        request=body(RestoreRequest))),
    route("GET", "/api/backups/download", download, contract=Operation(
        "downloadBackup", "Download a published backup archive", _TAG,
        {200: ok(BINARY, "The archive.", media_type="application/octet-stream",
                 headers={**_NO_STORE, "Content-Disposition": Header("attachment; filename=...")}),
         404: error("No published archive with that token (unknown_token).", BackupRefusal)},
        params=(Param("token", required=True, description="The `ready` line's token."),))),
)
