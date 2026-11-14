"""Work reads HTTP controller (PG-C3 #642).

``GET /api/works`` (every Work summary, or with ``?projection=browse`` the
compact browse catalog; both with ETags), ``GET /api/works/:id`` (the detail
with its Research refs), ``GET /api/works/:id/metadata-state``,
``/source-state`` and ``/notes-state`` (with ETags), ``GET /api/recent``,
``GET /api/recently-added`` and ``GET /api/bibtex/:id``. Paths, codes and
bodies are those ``server.py`` answered before PG-C3. :func:`thumbnail_fields`
and :func:`holds_managed_pdf` serve the thumbnail, PDF and Folder routes.

The calls go to ``backend/domain/works/work_commands`` for the admitted
request's context on the served Library's database, so authorization stays
in ``library_query``. ``server.py`` calls these handlers after host/origin
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
