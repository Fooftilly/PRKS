"""Works HTTP controller (PG-C3 #642).

``GET /api/works`` (every Work summary, or with ``?projection=browse`` the
compact browse catalog; both with ETags), ``GET /api/works/:id`` (the detail
with its Research refs), ``GET /api/works/:id/metadata-state``,
``/source-state`` and ``/notes-state`` (with ETags), ``GET /api/recent``,
``GET /api/recently-added`` and ``GET /api/bibtex/:id``; ``PATCH`` and
``DELETE /api/works/:id``, ``POST /api/works/bulk`` and ``POST
/api/works/:id/opened``. Paths, codes and bodies are those ``server.py``
answered before PG-C3. :func:`create` and :func:`delete` serve the Work
create route, whose request shaping (upload, oEmbed enrichment, filing, roles,
Tags, Playlist) stays in ``server.py``; :func:`thumbnail_fields` and
:func:`holds_managed_pdf` serve the thumbnail, PDF and Folder routes.

The calls go to ``backend/domain/works/work_commands`` for the admitted
request's context on the served Library's database, so authorization stays
in ``library_query`` and ``library_command``. ``server.py`` calls these handlers after host/origin
and library-access checks, each where its route was in the dispatch order.
Each ``handle_*`` returns ``True`` when the path belongs to it, else
``False`` so dispatch continues.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from backend import work_source_sync
from backend.etags import etag_for_representation
from backend.log_safety import safe_error_type, safe_log_id

COLLECTION = "/api/works"
_PREFIX = COLLECTION + "/"
LOGGER = logging.getLogger("prks.server")


def _commands() -> Any:
    """``backend.domain.works.work_commands``, imported when a route runs.

    The served app loads no PostgreSQL module at import time
    (``tests/test_e2e_policy.py``).
    """
    from backend.domain.works import work_commands

    return work_commands


def _actor(handler: Any, db: Any) -> tuple[Any, Any]:
    """The served Library's database and the admitted request's context."""
    ctx = getattr(handler, "_prks_request_context", None)
    if ctx is None:
        raise RuntimeError("a Work route ran without an admitted request context")
    return db.database, ctx


def _pdfs_dir(db: Any) -> str:
    if db.storage is None:
        raise RuntimeError("this PostgreSQL library has no data root")
    return db.storage.pdfs_dir


def _state_id(path: str, leaf: str) -> Optional[str]:
    """The Work id of ``/api/works/:id/<leaf>``, else ``None``."""
    parts = path.split("/")
    if path.startswith(_PREFIX) and len(parts) == 5 and parts[4] == leaf:
        return parts[3]
    return None


def _send_revalidated(handler: Any, label: str, data: Any) -> None:
    etag = etag_for_representation(label, data)
    if handler._prks_if_none_match(etag):
        handler._send_json_not_modified(etag)
        return
    handler.send_json(200, data, etag=etag, precondition_checked=True)


def thumbnail_fields(handler: Any, db: Any, work_id: str) -> Optional[dict]:
    """The Work's primary Asset ``file_path`` and thumbnail fields; ``None`` for a missing Work."""
    return _commands().thumbnail_fields(*_actor(handler, db), work_id)


def holds_managed_pdf(handler: Any, db: Any, name: str) -> bool:
    """Whether the acting Library names the managed PDF ``name`` (the data root is shared)."""
    return _commands().holds_managed_pdf(*_actor(handler, db), name)


def handle_catalog(handler: Any, db: Any, path: str, query: Any) -> bool:
    if path != COLLECTION:
        return False
    database, ctx = _actor(handler, db)
    # ``?projection=browse`` is an explicit, additive contract: the compact
    # catalog the browse routes cache. The default keeps its semantics for
    # every other caller. Each list is built once: file_size_bytes is read
    # from the managed PDF, so the ETag comes from these rows.
    if (query.get("projection") or [""])[0] == "browse":
        _send_revalidated(handler, "works-browse", _commands().browse_catalog(database, ctx, _pdfs_dir(db)))
    else:
        _send_revalidated(handler, "works", _commands().catalog(database, ctx, _pdfs_dir(db)))
    return True


def handle_state(handler: Any, db: Any, path: str) -> bool:
    w_id = _state_id(path, "metadata-state")
    if w_id is not None:
        _send_state(handler, "work-metadata-state", _commands().metadata_state(*_actor(handler, db), w_id))
        return True
    w_id = _state_id(path, "source-state")
    if w_id is not None:
        # A REVISION ONLY. The Work record already carries every canonical
        # source value, so a URL here would double what this endpoint sends
        # and what IndexedDB stores for values the client already holds.
        data = _commands().source_state(*_actor(handler, db), w_id)
        if data == work_source_sync.INVALID_SOURCE_STATE:
            # The product reads this row as a video, but its identity cannot
            # be stated. Said plainly rather than answered with a revision the
            # mutation would then refuse to act on.
            handler.send_json(409, {
                "error": "This file's video source cannot be read. Its link is not a "
                         "supported video URL.",
                "code": work_source_sync.INVALID_SOURCE_STATE})
            return True
        _send_state(handler, "work-source-state", data)
        return True
    w_id = _state_id(path, "notes-state")
    if w_id is not None:
        _send_state(handler, "work-notes-state", _commands().notes_state(*_actor(handler, db), w_id))
        return True
    return False


def _send_state(handler: Any, label: str, data: Any) -> None:
    if data is None:
        handler.send_json(404, {"error": "Work not found"})
        return
    _send_revalidated(handler, label, data)


def handle_detail(handler: Any, db: Any, path: str, research_index: Any) -> bool:
    if not (path.startswith(_PREFIX) and len(path.split("/")) == 4):
        return False
    w_id = path.split("/")[-1]
    database, ctx = _actor(handler, db)
    data = _commands().detail(database, ctx, _pdfs_dir(db), w_id)
    if not data:
        handler.send_error(404, "Work not found")
        return True
    try:
        data["research_refs"] = research_index.work_research_refs(w_id, db)
    except Exception as e:  # noqa: BLE001 - the Work still answers without its refs
        LOGGER.warning("research_index_refs_failed work_id=%s error_type=%s",
                       safe_log_id(w_id), safe_error_type(e))
    handler.send_json(200, data)
    return True


def handle_browse(handler: Any, db: Any, path: str) -> bool:
    # Representation-derived ETags: a COUNT + MAX(last_opened_at) probe cannot
    # see a re-open of the most recent Work, nor a display field changing on a
    # row already in the result.
    if path == "/api/recent":
        _send_revalidated(handler, "recent", _commands().recent_browse(*_actor(handler, db), _pdfs_dir(db)))
        return True
    if path == "/api/recently-added":
        _send_revalidated(handler, "recently-added",
                          _commands().recently_added_browse(*_actor(handler, db), _pdfs_dir(db)))
        return True
    return False


def handle_bibtex(handler: Any, db: Any, path: str) -> bool:
    if not path.startswith("/api/bibtex/"):
        return False
    entry = _commands().bibtex(*_actor(handler, db), path.split("/")[-1])
    handler.send_response(200)
    handler.send_header("Content-Type", "text/plain")
    handler.end_headers()
    handler.wfile.write(entry.encode())
    return True


# -- writes -----------------------------------------------------------------------


def _objects(db: Any) -> Any:
    """The served Library's managed objects (the Work PDFs live there)."""
    from backend.storage.objects import LocalFilesystemStorage

    if db.storage is None:
        raise RuntimeError("this PostgreSQL library has no data root")
    return LocalFilesystemStorage.for_config(db.storage)


def create(handler: Any, db: Any, file_path: str, upload: Optional[tuple[str, bytes]], fields: dict) -> str:
    """Create a Work from an upload, an existing managed PDF, a stream or no file; its id.

    ``fields`` are the create's other fields (``work_commands.NEW_WORK_DEFAULTS``).
    No adoption lock: the command checks an existing file for an actor who
    may edit and adopts it in the create's transaction, and the settle that
    removes bytes re-checks every Asset under the per-object lock (§7.4,
    §11.2). An upload is stored under fresh keys.
    """
    commands = _commands()
    writing = None
    if upload is not None:
        name, data = upload

        def write(out: Any) -> None:
            out.write(data)

        writing = (name, write)
    database, ctx = _actor(handler, db)
    return commands.create(database, ctx, _pdfs_dir(db), _objects(db),
                           commands.new_work_fields(file_path=file_path, **fields), writing)


def delete(handler: Any, db: Any, work_id: str, *, keep_managed_pdf: bool = False) -> None:
    """Delete a Work, then its thumbnails (derived files under the root), best effort.

    The delete records its files' cleanup claims in its own transaction and
    the orphan sweep removes the bytes (§11.2); the text and Research indexes
    are the database's.
    """
    from backend.derived_caches import prks_delete_pdf_thumbnails_for_work_id

    deleted = _commands().delete(*_actor(handler, db), work_id, claim_pdf=not keep_managed_pdf)
    thumbs_dir = db.storage.thumbs_dir if db.storage is not None else None
    if deleted is None or not thumbs_dir:
        return
    # Log the id the database returned, never the request's own value.
    logged_id = safe_log_id(deleted.work_id)
    try:
        failed = prks_delete_pdf_thumbnails_for_work_id(deleted.work_id, thumbs_dir)
        if failed:
            LOGGER.warning("work_delete_thumbnails_cleanup_failed work_id=%s failed_count=%s",
                           logged_id, len(failed))
    except Exception as e:  # noqa: BLE001 - the Work is deleted; the thumbnails are best effort
        LOGGER.warning("work_delete_thumbnails_cleanup_failed work_id=%s error_type=%s",
                       logged_id, safe_error_type(e))


def _item_id(path: str) -> Optional[str]:
    """The Work id of ``/api/works/:id``, else ``None``."""
    if path.startswith(_PREFIX) and len(path.split("/")) == 4:
        return path.split("/")[-1]
    return None


def handle_delete(handler: Any, db: Any, path: str) -> bool:
    w_id = _item_id(path)
    if w_id is None:
        return False
    delete(handler, db, w_id)
    handler.send_json(200, {"status": "deleted"})
    return True


def _stored_file_path(handler: Any, db: Any, work_id: str) -> str:
    """The Work's stored ``file_path``, its primary Asset's (``""`` when none or no such Work)."""
    return str((thumbnail_fields(handler, db, work_id) or {}).get("file_path") or "")


def handle_patch(handler: Any, db: Any, path: str, data: Any) -> bool:
    """``PATCH /api/works/:id``: a Folder move, the Research Note and the metadata fields."""
    from backend.api import folders as folders_api
    from backend.api import research_notes as research_notes_api
    from backend.domain.research.rules import ResearchError

    w_id = _item_id(path)
    if w_id is None:
        return False
    if not isinstance(data, dict):
        handler.send_json(400, {"error": "JSON object body required"})
        return True
    body = dict(data)
    # Only a file_path that would change the stored one is refused. Echoing
    # the same file_path on a title/status PATCH must not fail when the
    # bytes are already gone; the echo is dropped below so this request
    # cannot rewrite it after a concurrent retarget or cleanup.
    if "file_path" in body and str(body.get("file_path") or "") != _stored_file_path(handler, db, w_id):
        # Adopting another file is not ported. Refuse before any part of the
        # PATCH (a folder move, notes) commits, so a 501 never hides an
        # applied change.
        handler._send_not_ported("Changing a Work's file_path is not ported to PostgreSQL yet.")
        return True
    if "folder_id" in body:
        raw_folder = body.pop("folder_id")
        try:
            folders_api.move_work(handler, db, w_id, raw_folder)
        except ValueError as e:
            handler.send_json(400, {"error": str(e)})
            return True
    body.pop("file_path", None)
    if "text_content" in body:
        notes_text = body.pop("text_content")
        try:
            research_notes_api.save(handler, db, w_id, notes_text)
        except ResearchError as e:
            handler.send_json(e.http_status, {"error": str(e), "code": e.code})
            return True
    if body:
        try:
            _commands().update_metadata(*_actor(handler, db), w_id, body)
        except ValueError as e:
            # A refused value or length is the caller's mistake, not a
            # server fault: say so rather than 500.
            handler.send_json(400, {"error": str(e)})
            return True
    handler.send_json(200, {"status": "updated"})
    return True


def handle_bulk(handler: Any, db: Any, path: str, data: Any) -> bool:
    """``POST /api/works/bulk``: one action on many Works, all or nothing."""
    from backend.domain.works.rules import BulkWorkError
    from backend.log_safety import safe_log_label

    if path != COLLECTION + "/bulk":
        return False
    if not isinstance(data, dict):
        handler.send_json(400, {"error": "JSON object body required"})
        return True
    try:
        result = _commands().bulk_update(*_actor(handler, db), data)
    except BulkWorkError as e:
        handler.send_json(int(e.http_status), {"error": str(e)})
        return True
    LOGGER.info(
        "bulk_work_update action=%s work_count=%s target_count=%s request_id=%s",
        safe_log_label(result.get("action")),
        int(result.get("requested") or 0),
        int(result.get("target_count") or 0),
        safe_log_id(handler._prks_request_id),
    )
    handler.send_json(200, {
        "status": result.get("status"),
        "action": result.get("action"),
        "requested": result.get("requested"),
        "updated": result.get("updated"),
    })
    return True


def handle_opened(handler: Any, db: Any, path: str) -> bool:
    """``POST /api/works/:id/opened``: an open now, with server time as its event time.

    ``GET /api/works/:id`` is a pure read. PRKS's own UI takes the durable
    ``MARK_WORK_OPENED`` path instead, so that an open made from cache while
    the server is unreachable is recorded identically; this endpoint remains
    for other canonical callers and shares the same max-register.
    """
    if not (path.startswith(_PREFIX) and path.endswith("/opened")):
        return False
    parts = path.split("/")
    if len(parts) != 5:
        handler.send_error(404, "API endpoint not found")
        return True
    try:
        marked = _commands().mark_opened(*_actor(handler, db), parts[3])
    except ValueError as e:
        handler.send_json(400, {"error": str(e)})
        return True
    if not marked:
        handler.send_error(404, "Work not found")
        return True
    handler.send_json(200, {"status": "opened"})
    return True
