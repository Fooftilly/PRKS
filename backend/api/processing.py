"""Processing queue HTTP controller (``docs/postgresql-architecture.md`` §11.5; L-PR-1 #636).

Routes, served by the PostgreSQL (``PRKS_AUTH``) runtime alone:

- ``GET /api/processing-items?limit=&cursor=``: one page of ``ready`` items.
- ``POST /api/processing-items``: upload one PDF (raw body, below).
- ``GET /api/processing-items/{id}``: the item or marker.
- ``PATCH /api/processing-items/{id}``: edit the draft at ``revision``.
- ``DELETE /api/processing-items/{id}``: remove a ``ready`` item.
- ``GET``/``HEAD /api/processing-items/{id}/pdf``: the uploaded bytes, with ``Range``.
- ``POST /api/processing-items/{id}/import``: import one item.
- ``POST /api/processing-items/import`` ``{"item_ids": [...]}``: a result per item.

An upload's body is the PDF itself (``Content-Type: application/pdf``) with
``Content-Length``, a client-minted ``Idempotency-Key`` and the original
filename, percent-encoded UTF-8, in ``PRKS-Filename``. ``server.py``
dispatches it before any body is read and marks the request's body unread
until :func:`handle_upload` has consumed it, so every answer decided earlier
(admission, a header refusal, a used key, a refused body) carries
``Connection: close``. The upload holds the Library's read gate while it
streams, which a restore waits out, so the body must arrive within a bound:
each read waits at most ``UPLOAD_IDLE_SECONDS``, and the whole body must
arrive by a deadline set from its declared length. A stalled or trickling
upload is answered 408 and its connection closed.

Each command runs through :mod:`backend.domain.processing.commands` for the
admitted :class:`~backend.domain.identity.request_context.RequestContext`, so
it is authorized, maintenance-checked and epoch-fenced in its own
transaction. ``AccessRefused`` and mapped database errors propagate to
``server.py``, which answers them as every PostgreSQL route does. On the
SQLite runtime no handler claims a path, and the server answers 404. The
domain modules (and so psycopg) are imported only after a handler has found
a PostgreSQL library: the SQLite runtime never loads psycopg.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional
from urllib.parse import parse_qs, unquote

from backend.log_safety import safe_log_label

LOGGER = logging.getLogger("prks.api.processing")

COLLECTION = "/api/processing-items"
BATCH_IMPORT = COLLECTION + "/import"
# The largest PDF one upload may carry.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_BATCH = 500
# How long one read of an upload's body may wait for a byte.
UPLOAD_IDLE_SECONDS = 30.0
# The whole body must arrive within the grace plus its length at this rate,
# so a trickle cannot hold the read gate (and so a restore) forever.
UPLOAD_GRACE_SECONDS = 60.0
UPLOAD_MIN_BYTES_PER_SECOND = 256 * 1024
FILENAME_HEADER = "PRKS-Filename"
IDEMPOTENCY_HEADER = "Idempotency-Key"

_MESSAGES = {
    "ENTITY_NOT_FOUND": "No such processing item.",
    "PROCESSING_ITEM_IMPORTED": "The item has already been imported.",
    "PROCESSING_ITEM_DELETED": "The item has been deleted.",
    "IMPORTED_WORK_DELETED": "The item was imported, and its Work has since been deleted.",
    "REVISION_CONFLICT": "The item changed since that revision.",
    "PROCESSING_NOT_A_PDF": "The upload is not a PDF.",
    "PROCESSING_UPLOAD_TOO_LARGE": "The upload is larger than the upload limit.",
    "PROCESSING_UPLOAD_INCOMPLETE": "The upload ended before Content-Length bytes.",
    "PROCESSING_UPLOAD_TIMEOUT": "The upload stalled or was too slow; send it again.",
    "PROCESSING_OBJECT_MISSING": "The item's stored file is missing.",
}
_UPLOAD_STATUS = {
    "PROCESSING_NOT_A_PDF": 415,
    "PROCESSING_UPLOAD_TOO_LARGE": 413,
    "PROCESSING_UPLOAD_INCOMPLETE": 400,
}


def handles(path: str) -> bool:
    return path == COLLECTION or path.startswith(COLLECTION + "/")


def _route(path: str) -> Optional[tuple[str, str]]:
    """``(item_id, tail)`` for ``/api/processing-items/{id}[/tail]``; None for anything else."""
    if not path.startswith(COLLECTION + "/"):
        return None
    parts = path[len(COLLECTION) + 1:].split("/")
    if len(parts) > 2 or not parts[0]:
        return None
    return unquote(parts[0]), parts[1] if len(parts) == 2 else ""


def _library(handler: Any, db: Any) -> Any:
    """The PostgreSQL library the route acts on; None on the SQLite runtime."""
    from backend.pg_facade import PostgresLibrary

    if getattr(handler, "_prks_request_context", None) is None or not isinstance(db, PostgresLibrary):
        return None
    return db


def _storage(library: Any) -> Any:
    from backend.storage.objects import LocalFilesystemStorage

    if library.storage is None:
        raise RuntimeError("this PostgreSQL library has no data root")
    return LocalFilesystemStorage.for_config(library.storage)


# -- answers ------------------------------------------------------------------------


def _error(handler: Any, status: int, code: str, message: Optional[str] = None,
           item: Optional[dict[str, Any]] = None) -> None:
    body: dict[str, Any] = {"error": code, "code": code, "message": message or _MESSAGES.get(code, code)}
    if item is not None:
        body["item"] = item
    handler.send_json(status, body)


def _invalid(handler: Any, message: str) -> None:
    _error(handler, 400, "VALIDATION_ERROR", message)


def _refused(handler: Any, exc: Any) -> None:
    """An ``items.Refused``: 404 for no item, else 409 with the item as it now is."""
    _error(handler, 404 if exc.code == "ENTITY_NOT_FOUND" else 409, exc.code, item=exc.item)


# -- reads --------------------------------------------------------------------------


def _page_args(query: str) -> tuple[int, Optional[str]]:
    from backend.domain.processing import items

    params = parse_qs(query, keep_blank_values=True)
    raw_limit = params.get("limit", [None])[-1]
    limit = items.DEFAULT_PAGE_SIZE
    if raw_limit is not None:
        if not raw_limit.isdigit() or len(raw_limit) > 6:
            raise ValueError(f"limit must be between 1 and {items.MAX_PAGE_SIZE}.")
        limit = int(raw_limit)
    cursor = params.get("cursor", [None])[-1]
    return limit, cursor or None


def handle_get(handler: Any, db: Any, path: str, query: str) -> bool:
    """The queue page, an item, or an item's PDF; False when the path is not this family's."""
    library = _library(handler, db)
    if library is None or not handles(path):
        return False
    from backend.domain.processing import commands

    ctx = handler._prks_request_context
    if path == COLLECTION:
        try:
            limit, cursor = _page_args(query)
            found, next_cursor = commands.queue(library.database, ctx, limit=limit, after=cursor)
        except ValueError as exc:
            _invalid(handler, str(exc))
            return True
        handler.send_json(200, {"items": found, "next_cursor": next_cursor})
        return True
    route = _route(path)
    if route is None:
        return False
    item_id, tail = route
    if tail == "pdf":
        return _send_pdf(handler, library, item_id, head_only=False)
    if tail:
        return False
    found_item = commands.item(library.database, ctx, item_id)
    if found_item is None:
        _error(handler, 404, "ENTITY_NOT_FOUND")
    else:
        handler.send_json(200, found_item)
    return True


def handle_head(handler: Any, db: Any, path: str) -> bool:
    """HEAD of an item's PDF; on the SQLite runtime every queue path is a 404, as for GET."""
    if not handles(path):
        return False
    library = _library(handler, db)
    if library is None:
        handler.send_error(404, "API endpoint not found")
        return True
    route = _route(path)
    if route is None or route[1] != "pdf":
        return False
    return _send_pdf(handler, library, route[0], head_only=True)


def _send_pdf(handler: Any, library: Any, item_id: str, *, head_only: bool) -> bool:
    """A ``ready`` item's object as uploaded, through the object store; 404 for a marker or no item."""
    from backend.domain.processing import commands
    from backend.storage.objects import ObjectNotFound

    found = commands.preview(library.database, handler._prks_request_context, item_id)
    if found is None:
        _error(handler, 404, "ENTITY_NOT_FOUND")
        return True
    key, size, _sha256 = found
    storage = _storage(library)
    try:
        # Opened before the headers: an object already removed (the item was
        # deleted or imported and cleaned since the read) is a 404, not a cut stream.
        stream = storage.open_read(key)
    except ObjectNotFound:
        _error(handler, 404, "ENTITY_NOT_FOUND")
        return True
    with stream:
        handler._send_pdf_stream(int(size), stream, head_only=head_only)
    return True


# -- upload -------------------------------------------------------------------------


def _single_header(handler: Any, name: str) -> Optional[str]:
    values = handler.headers.get_all(name) or []
    return values[0] if len(values) == 1 else None


class UploadTimedOut(Exception):
    """The body stalled for ``UPLOAD_IDLE_SECONDS`` or missed its deadline."""


class _TimedBody:
    """The request body, read with an idle timeout and an overall deadline.

    Each ``read`` makes at most one socket read (``read1``), so a client that
    sends a byte at a time cannot stretch one read past the deadline.
    """

    def __init__(self, rfile: Any, sock: Any, content_length: int) -> None:
        self._read1 = getattr(rfile, "read1", rfile.read)
        self._sock = sock
        self._deadline = (time.monotonic() + UPLOAD_GRACE_SECONDS
                          + content_length / UPLOAD_MIN_BYTES_PER_SECOND)

    def read(self, size: int) -> bytes:
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise UploadTimedOut()
        self._sock.settimeout(min(UPLOAD_IDLE_SECONDS, left))
        try:
            return self._read1(size)
        except TimeoutError as exc:
            raise UploadTimedOut() from exc


def handle_upload(handler: Any, db: Any) -> None:
    """``POST /api/processing-items``: one PDF into the queue (§11.5 "Upload").

    Clears ``handler._prks_unread_body`` once the whole body has been read.
    """
    library = _library(handler, db)
    if library is None:
        handler.send_error(404, "API endpoint not found")
        return
    from email.message import Message

    from backend.domain.objects.lifecycle import ObjectMissing
    from backend.domain.processing import commands, items, uploads

    if handler.headers.get_all("Transfer-Encoding"):
        _error(handler, 411, "PROCESSING_LENGTH_REQUIRED", "Send the PDF with Content-Length.")
        return
    raw_length = _single_header(handler, "Content-Length")
    if raw_length is None:
        _error(handler, 411, "PROCESSING_LENGTH_REQUIRED", "Send the PDF with Content-Length.")
        return
    if not raw_length.isdigit() or len(raw_length) > 19:
        _invalid(handler, "Invalid Content-Length.")
        return
    content_type = _single_header(handler, "Content-Type")
    parsed = Message()
    parsed["content-type"] = content_type or ""
    if content_type is None or parsed.get_content_type() != "application/pdf":
        _error(handler, 415, "unsupported_media_type", "Send the PDF as application/pdf.")
        return
    upload_key = _single_header(handler, IDEMPOTENCY_HEADER)
    raw_name = _single_header(handler, FILENAME_HEADER) or ""
    try:
        filename = unquote(raw_name, errors="strict")
    except UnicodeDecodeError:
        _invalid(handler, f"{FILENAME_HEADER} must be percent-encoded UTF-8.")
        return
    sock = handler.connection
    previous_timeout = sock.gettimeout()
    try:
        uploaded = commands.upload(
            library.database, handler._prks_request_context, _storage(library),
            upload_key=upload_key or "", filename=filename,
            body=_TimedBody(handler.rfile, sock, int(raw_length)),
            content_length=int(raw_length), max_bytes=MAX_UPLOAD_BYTES)
    except UploadTimedOut:
        # The body is unread, so this answer closes the connection.
        LOGGER.warning("processing_upload_timed_out")
        _error(handler, 408, "PROCESSING_UPLOAD_TIMEOUT")
        return
    except uploads.UploadRefused as exc:
        _error(handler, _UPLOAD_STATUS[exc.code], exc.code)
        return
    except items.Refused as exc:
        _refused(handler, exc)
        return
    except ObjectMissing:
        _error(handler, 409, "PROCESSING_OBJECT_MISSING")
        return
    except ValueError as exc:
        _invalid(handler, str(exc))
        return
    finally:
        try:
            sock.settimeout(previous_timeout)
        except OSError:
            pass
    if uploaded.body_read:
        handler._prks_unread_body = False
    LOGGER.info("processing_item_uploaded created=%s",
                safe_log_label("true" if uploaded.created else "false"))
    handler.send_json(201 if uploaded.created else 200,
                      {"item": uploaded.item, "created": uploaded.created, "duplicates": uploaded.duplicates})


# -- edit, delete, import -----------------------------------------------------------


def handle_patch(handler: Any, db: Any, path: str, data: Any) -> bool:
    library = _library(handler, db)
    route = _route(path) if library is not None else None
    if route is None or route[1]:
        return False
    from backend.domain.processing import commands, items

    if not isinstance(data, dict):
        _invalid(handler, "JSON object body required.")
        return True
    fields = dict(data)
    revision = fields.pop("revision", None)
    try:
        updated = commands.edit(library.database, handler._prks_request_context, route[0], revision, fields)
    except items.Refused as exc:
        _refused(handler, exc)
        return True
    except ValueError as exc:
        _invalid(handler, str(exc))
        return True
    handler.send_json(200, updated)
    return True


def handle_delete(handler: Any, db: Any, path: str) -> bool:
    library = _library(handler, db)
    route = _route(path) if library is not None else None
    if route is None or route[1]:
        return False
    from backend.domain.processing import commands, items

    try:
        marker = commands.delete(library.database, handler._prks_request_context, route[0])
    except items.Refused as exc:
        _refused(handler, exc)
        return True
    handler.send_json(200, marker)
    return True


def handle_post(handler: Any, db: Any, path: str, data: Any) -> bool:
    """One import or a batch; the upload is :func:`handle_upload`'s."""
    library = _library(handler, db)
    if library is None:
        return False
    if path == BATCH_IMPORT:
        _import_batch(handler, library, data)
        return True
    route = _route(path)
    if route is None or route[1] != "import":
        return False
    _import_one(handler, library, route[0])
    return True


def _import_one(handler: Any, library: Any, item_id: str) -> None:
    from backend.domain.objects.lifecycle import ObjectMissing
    from backend.domain.processing import commands, items

    try:
        done = commands.import_one(library.database, handler._prks_request_context, _storage(library), item_id)
    except items.Refused as exc:
        _refused(handler, exc)
        return
    except ObjectMissing:
        _error(handler, 409, "PROCESSING_OBJECT_MISSING")
        return
    except ValueError as exc:
        _invalid(handler, str(exc))
        return
    handler.send_json(201 if done.created else 200,
                      {"item_id": done.item_id, "work_id": done.work_id, "created": done.created})


def _import_batch(handler: Any, library: Any, data: Any) -> None:
    from backend.domain.processing import commands

    ids = data.get("item_ids") if isinstance(data, dict) else None
    if not isinstance(ids, list) or not ids or not all(isinstance(one, str) for one in ids):
        _invalid(handler, "item_ids must be a non-empty array of item ids.")
        return
    if len(ids) > MAX_BATCH:
        _invalid(handler, f"At most {MAX_BATCH} items per import.")
        return
    results = commands.import_many(library.database, handler._prks_request_context, _storage(library), ids)
    handler.send_json(200, {"results": [
        {"item_id": one.item_id, "ok": one.ok, "work_id": one.work_id, "created": one.created,
         "code": one.code, "message": one.message or (None if one.ok else _MESSAGES.get(one.code or "", one.code)),
         "item": one.item}
        for one in results
    ]})
