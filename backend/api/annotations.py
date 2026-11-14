"""PDF annotations and Work PDF bytes HTTP controller (PG-C3 #642).

``GET /api/works/:id/annotations-state`` and ``/annotations-snapshot`` (with
ETags), ``GET /api/works/:id/pdf-materialization``, ``GET
/api/works/:id/annotations`` (the list as a JSON string), ``GET
/api/works/:id/save-confirm?token=``, ``POST /api/works/:id/annotations/adopt``
(byte-only user markup), ``POST /api/works/:id/pdf`` (replace the PDF, or with
``materialized_annotation_set_revision`` store the viewer's rendered
annotations) and ``POST /api/works/:id/annotations`` (the legacy full-list
replace at ``canonical_annotation_set_revision``). Paths, codes and bodies are
those ``server.py`` answered before PG-C3.

The calls go to ``backend/domain/annotations/annotation_commands`` for the
admitted request's context on the served Library's database, so authorization
stays in ``library_query`` and ``library_command``. The save tokens that
``save-confirm`` reports are this process's memory, as before: a save's
``save_token`` is recorded only once the save succeeded.

``server.py`` calls these handlers after host/origin, JSON body size, and
library-access checks. Each ``handle_*`` returns ``True`` when the path
belongs to this family (including refusals already sent), else ``False`` so
dispatch continues.
"""

from __future__ import annotations

import base64
import binascii
import threading
from typing import Any, Optional

from backend.etags import etag_for_representation
from backend.pdf_annotations import WorkAnnotationError
from backend.services.work_pdf_replace import stale_materialization_body

_PREFIX = "/api/works/"

_LAST_PDF_SAVE_TOKEN_BY_WORK: dict[str, str] = {}
_LAST_ANNOTATION_SAVE_TOKEN_BY_WORK: dict[str, str] = {}
_SAVE_TOKEN_LOCK = threading.Lock()


def _commands() -> Any:
    """``backend.domain.annotations.annotation_commands``, imported when a route runs.

    The served app loads no PostgreSQL module at import time
    (``tests/test_e2e_policy.py``).
    """
    from backend.domain.annotations import annotation_commands

    return annotation_commands


def _actor(handler: Any, db: Any) -> tuple[Any, Any]:
    """The served Library's database and the admitted request's context."""
    ctx = getattr(handler, "_prks_request_context", None)
    if ctx is None:
        raise RuntimeError("an Annotation route ran without an admitted request context")
    return db.database, ctx


def _objects(db: Any) -> Any:
    """The served Library's managed objects (the Work PDFs live there)."""
    from backend.storage.objects import LocalFilesystemStorage

    if db.storage is None:
        raise RuntimeError("this PostgreSQL library has no data root")
    return LocalFilesystemStorage.for_config(db.storage)


def _sub(path: str, leaf: str) -> Optional[str]:
    """The Work id of ``/api/works/:id/<leaf>``, else ``None``."""
    parts = path.split("/")
    if path.startswith(_PREFIX) and len(parts) == 5 and parts[4] == leaf:
        return parts[3]
    return None


def _send_revalidated(handler: Any, label: str, data: Any) -> None:
    if data is None:
        handler.send_json(404, {"error": "Work not found"})
        return
    etag = etag_for_representation(label, data)
    if handler._prks_if_none_match(etag):
        handler._send_json_not_modified(etag)
        return
    handler.send_json(200, data, etag=etag, precondition_checked=True)


def handle_get(handler: Any, db: Any, path: str, query: Any) -> bool:
    if not path.startswith(_PREFIX):
        return False
    w_id = _sub(path, "annotations-state")
    if w_id is not None:
        # The revision state alone; a subset of the snapshot.
        _send_revalidated(handler, "work-annotations-state", _commands().state(*_actor(handler, db), w_id))
        return True
    w_id = _sub(path, "annotations-snapshot")
    if w_id is not None:
        # One snapshot: items + revisions + materialization revisions.
        _send_revalidated(handler, "work-annotations-snapshot",
                          _commands().snapshot(*_actor(handler, db), w_id))
        return True
    w_id = _sub(path, "pdf-materialization")
    if w_id is not None:
        data = _commands().materialization(*_actor(handler, db), w_id)
        if data is None:
            handler.send_json(404, {"error": "Work not found"})
        else:
            handler.send_json(200, data)
        return True
    if path.endswith("/annotations"):
        w_id = path.split("/")[3]
        handler.send_json(200, {"work_id": w_id,
                                "annotations_json": _commands().annotations_json(*_actor(handler, db), w_id)})
        return True
    if path.endswith("/save-confirm"):
        w_id = _sub(path, "save-confirm")
        if w_id is None:
            handler.send_error(404, "API endpoint not found")
            return True
        _save_confirm(handler, w_id, query)
        return True
    return False


def _save_confirm(handler: Any, w_id: str, query: Any) -> None:
    token = (query.get("token", [""])[0] or "").strip()
    if not token:
        handler.send_json(400, {"error": "token is required"})
        return
    with _SAVE_TOKEN_LOCK:
        pdf_saved = _LAST_PDF_SAVE_TOKEN_BY_WORK.get(w_id) == token
        ann_saved = _LAST_ANNOTATION_SAVE_TOKEN_BY_WORK.get(w_id) == token
    handler.send_json(200, {
        "work_id": w_id,
        "token": token,
        "pdf_saved": pdf_saved,
        "annotations_saved": ann_saved,
        "saved": bool(pdf_saved and ann_saved),
    })


def handle_post(handler: Any, db: Any, path: str, data: Any) -> bool:
    if not path.startswith(_PREFIX):
        return False
    if path.endswith("/annotations/adopt"):
        _adopt(handler, db, path, data)
        return True
    if path.endswith("/pdf"):
        _post_pdf(handler, db, path.split("/")[3], data)
        return True
    if path.endswith("/annotations"):
        _replace_all(handler, db, path.split("/")[3], data)
        return True
    return False


def _adopt(handler: Any, db: Any, path: str, data: Any) -> None:
    parts = path.split("/")
    if len(parts) != 6 or parts[4] != "annotations" or parts[5] != "adopt":
        handler.send_error(404, "API endpoint not found")
        return
    if not isinstance(data, dict):
        handler.send_json(400, {"error": "JSON object body required"})
        return
    viewer_annotations = data.get("viewer_annotations")
    if not isinstance(viewer_annotations, list):
        handler.send_json(400, {"error": "viewer_annotations must be a JSON list",
                                "code": "malformed_annotation_payload"})
        return
    try:
        result = _commands().adopt(*_actor(handler, db), parts[3], viewer_annotations)
    except WorkAnnotationError as e:
        handler.send_json(e.http_status, {"error": str(e), "code": e.code})
        return
    handler.send_json(200, result)


def _post_pdf(handler: Any, db: Any, w_id: str, data: Any) -> None:
    file_b64 = data.get("file_b64", "")
    save_token = str(data.get("save_token", "") or "").strip()
    claimed_set_rev = data.get("materialized_annotation_set_revision", None)
    if not file_b64:
        handler.send_error(400, "No file_b64 provided")
        return
    try:
        pdf_bytes = base64.b64decode(file_b64, validate=True)
    except (binascii.Error, ValueError):
        handler.send_json(400, {"error": "Invalid file_b64 payload"})
        return
    status, body = _write_pdf(handler, db, w_id, pdf_bytes, claimed_set_rev)
    if status == 200 and save_token:
        with _SAVE_TOKEN_LOCK:
            _LAST_PDF_SAVE_TOKEN_BY_WORK[w_id] = save_token
    handler.send_json(status, body)


def _write_pdf(handler: Any, db: Any, work_id: str, pdf_bytes: bytes,
               claimed_set_rev: object) -> tuple[int, dict]:
    """``POST /api/works/{id}/pdf``'s status and body.

    With ``materialized_annotation_set_revision`` the bytes are the viewer's
    rendered annotations and mark the Work materialized; without it they
    replace the Work's PDF.
    """
    from backend.pdf_materialization import STALE_CODE

    def write(out) -> None:
        out.write(pdf_bytes)

    commands = _commands()
    database, ctx = _actor(handler, db)
    try:
        if claimed_set_rev is None:
            result = commands.replace_pdf(database, ctx, _objects(db), work_id, write)
        else:
            result = commands.materialize_pdf(database, ctx, _objects(db), work_id, write, claimed_set_rev)
    except LookupError:
        # The commands refuse a missing Work and a Work without managed PDF
        # bytes alike; answer each with its own 404.
        if db.get_work(work_id) is None:
            return 404, {"error": "Work not found"}
        return 404, {"error": "Work has no managed PDF"}
    except ValueError as e:
        if str(e) == STALE_CODE:
            return 409, stale_materialization_body(commands.materialization(database, ctx, work_id))
        return 400, {"error": "Invalid materialization revision"}
    return 200, {**result, "status": "success"}


def _replace_all(handler: Any, db: Any, w_id: str, data: Any) -> None:
    # Compat full-list replace. Product writes use durable
    # CREATE/SET/DELETE_PDF_ANNOTATION; this remains for online-legacy
    # when the durable store is unavailable (Slice G retirement).
    if not isinstance(data, dict) or "annotations_json" not in data:
        handler.send_json(400, {"error": "annotations_json is required",
                                "code": "malformed_annotation_payload"})
        return
    annotations_json = data.get("annotations_json")
    save_token = str(data.get("save_token", "") or "").strip()
    # Legacy handshake is annotations-first: this endpoint only replaces
    # metadata and returns the exact replace generation. Never mark PDF
    # materialization here: save_token is solely for save-confirm
    # bookkeeping, not proof of bytes. Marking belongs to POST /pdf with
    # materialized_annotation_set_revision (the same claim path as durable).
    # The client must send its acknowledged tip so a stale viewer cannot
    # overwrite newer annotations or delete siblings omitted locally.
    if "canonical_annotation_set_revision" not in data:
        handler.send_json(400, {"error": "canonical_annotation_set_revision is required",
                                "code": "malformed_annotation_payload"})
        return
    try:
        replace_gen = _commands().save(*_actor(handler, db), w_id, annotations_json,
                                       base_set_revision=data.get("canonical_annotation_set_revision"))
    except WorkAnnotationError as e:
        handler.send_json(e.http_status, {"error": str(e), "code": e.code})
        return
    if save_token:
        with _SAVE_TOKEN_LOCK:
            _LAST_ANNOTATION_SAVE_TOKEN_BY_WORK[w_id] = save_token
    handler.send_json(200, {
        "status": "saved",
        "path": "legacy-full-list",
        "canonical_annotation_set_revision": replace_gen,
    })
