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
filename, percent-encoded UTF-8, in ``PRKS-Filename``. The route reads its
own body (``Body.STREAM``), so every answer that leaves the body unread
(admission, a header refusal, a used key, a refused body) carries
``Connection: close``. The upload holds a request thread while it
streams, so the body must arrive within a bound:
each read waits at most ``UPLOAD_IDLE_SECONDS``, and the whole body must
arrive by a deadline set from its declared length. A stalled or trickling
upload is answered 408 and its connection closed.

Each command runs through :mod:`backend.domain.processing.commands` for the
admitted :class:`~backend.domain.identity.request_context.RequestContext`, so
it is authorized, maintenance-checked and epoch-fenced in its own
transaction. ``AccessRefused`` and mapped database errors propagate to the
dispatcher, which answers them as every PostgreSQL route does. The domain
modules are imported when a route runs.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import parse_qs, unquote

from backend.api_contract.operation import BINARY, PDF, Header, Operation, Param, Reply, body, error, ok
from backend.api_contract.processing_items import (
    ProcessingImportRequest,
    ProcessingImportResults,
    ProcessingItem,
    ProcessingItemImported,
    ProcessingItemPage,
    ProcessingItemRefusal,
    ProcessingItemUpdateRequest,
    ProcessingItemUploaded,
)
from backend.runtime.log_safety import safe_log_label
from backend.web.http import Request, RequestBody, Response, json_response
from backend.web.routing import Body, route

if TYPE_CHECKING:
    from backend.library import PostgresLibrary
    from backend.storage.objects import LocalFilesystemStorage

LOGGER = logging.getLogger("prks.api.processing")

COLLECTION = "/api/processing-items"
BATCH_IMPORT = COLLECTION + "/import"
# The largest PDF one upload may carry.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_BATCH = 500
# How long one read of an upload's body may wait for a byte.
UPLOAD_IDLE_SECONDS = 30.0
# The whole body must arrive within the grace plus its length at this rate,
# so a trickle cannot hold a request thread forever.
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


def _library(req: Request) -> "PostgresLibrary":
    """The served PostgreSQL Library (an admitted context is required)."""
    if req.context is None:
        raise RuntimeError("route reached without an admitted request context")
    return req.library


def _storage(library: "PostgresLibrary") -> "LocalFilesystemStorage":
    from backend.storage.objects import LocalFilesystemStorage

    if library.storage is None:
        raise RuntimeError("this PostgreSQL library has no data root")
    return LocalFilesystemStorage.for_config(library.storage)


def _item_id(req: Request) -> str:
    return unquote(req.params["item_id"])


# -- answers ------------------------------------------------------------------------


def _error(req: Request, status: int, code: str, message: Optional[str] = None,
           item: Optional[dict[str, Any]] = None) -> Response:
    body: dict[str, Any] = {"error": code, "code": code, "message": message or _MESSAGES.get(code, code)}
    if item is not None:
        body["item"] = item
    return json_response(req, status, body)


def _invalid(req: Request, message: str) -> Response:
    return _error(req, 400, "VALIDATION_ERROR", message)


def _refused(req: Request, exc: Any) -> Response:
    """An ``items.Refused``: 404 for no item, else 409 with the item as it now is."""
    return _error(req, 404 if exc.code == "ENTITY_NOT_FOUND" else 409, exc.code, item=exc.item)


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


def queue(req: Request) -> Response:
    """``GET /api/processing-items``: one page of ``ready`` items."""
    from backend.domain.processing import commands

    library = _library(req)
    try:
        limit, cursor = _page_args(req.query_string)
        found, next_cursor = commands.queue(library.database, req.ctx, limit=limit, after=cursor)
    except ValueError as exc:
        return _invalid(req, str(exc))
    return json_response(req, 200, {"items": found, "next_cursor": next_cursor})


def item(req: Request) -> Response:
    from backend.domain.processing import commands

    found_item = commands.item(_library(req).database, req.ctx, _item_id(req))
    if found_item is None:
        return _error(req, 404, "ENTITY_NOT_FOUND")
    return json_response(req, 200, found_item)


def pdf(req: Request) -> Response:
    """``GET``/``HEAD`` of a ``ready`` item's object as uploaded; 404 for a marker or no item."""
    from backend.api.pdfs import pdf_response
    from backend.domain.processing import commands
    from backend.storage.objects import ObjectNotFound

    library = _library(req)
    found = commands.preview(library.database, req.ctx, _item_id(req))
    if found is None:
        return _error(req, 404, "ENTITY_NOT_FOUND")
    key, size, _sha256 = found
    try:
        # Opened before the headers: an object already removed (the item was
        # deleted or imported and cleaned since the read) is a 404, not a cut stream.
        stream = _storage(library).open_read(key)
    except ObjectNotFound:
        return _error(req, 404, "ENTITY_NOT_FOUND")
    return pdf_response(req, int(size), stream)


# -- upload -------------------------------------------------------------------------


def _single_header(req: Request, name: str) -> Optional[str]:
    values = req.headers.get_all(name)
    return values[0] if len(values) == 1 else None


class UploadTimedOut(Exception):
    """The body stalled for ``UPLOAD_IDLE_SECONDS`` or missed its deadline."""


class TimedBody:
    """The request body, read with an idle timeout and an overall deadline.

    Each ``read`` makes at most one body read (``read1``), so a client that
    sends a byte at a time cannot stretch one read past the deadline.
    """

    def __init__(self, body: RequestBody, content_length: int) -> None:
        self._body = body
        self._deadline = (time.monotonic() + UPLOAD_GRACE_SECONDS
                          + content_length / UPLOAD_MIN_BYTES_PER_SECOND)

    def read(self, size: int) -> bytes:
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise UploadTimedOut()
        self._body.settimeout(min(UPLOAD_IDLE_SECONDS, left))
        try:
            return self._body.read1(size)
        except TimeoutError as exc:
            raise UploadTimedOut() from exc


def upload(req: Request) -> Response:
    """``POST /api/processing-items``: one PDF into the queue (§11.5 "Upload")."""
    from email.message import Message

    from backend.domain.objects.lifecycle import ObjectMissing
    from backend.domain.processing import commands, items, uploads

    library = _library(req)
    if req.headers.get_all("Transfer-Encoding"):
        return _error(req, 411, "PROCESSING_LENGTH_REQUIRED", "Send the PDF with Content-Length.")
    raw_length = _single_header(req, "Content-Length")
    if raw_length is None:
        return _error(req, 411, "PROCESSING_LENGTH_REQUIRED", "Send the PDF with Content-Length.")
    if not raw_length.isdigit() or len(raw_length) > 19:
        return _invalid(req, "Invalid Content-Length.")
    content_type = _single_header(req, "Content-Type")
    parsed = Message()
    parsed["content-type"] = content_type or ""
    if content_type is None or parsed.get_content_type() != "application/pdf":
        return _error(req, 415, "unsupported_media_type", "Send the PDF as application/pdf.")
    upload_key = _single_header(req, IDEMPOTENCY_HEADER)
    raw_name = _single_header(req, FILENAME_HEADER) or ""
    try:
        filename = unquote(raw_name, errors="strict")
    except UnicodeDecodeError:
        return _invalid(req, f"{FILENAME_HEADER} must be percent-encoded UTF-8.")
    previous_timeout = req.body.gettimeout()
    try:
        uploaded = commands.upload(
            library.database, req.ctx, _storage(library),
            upload_key=upload_key or "", filename=filename,
            body=TimedBody(req.body, int(raw_length)),
            content_length=int(raw_length), max_bytes=MAX_UPLOAD_BYTES)
    except UploadTimedOut:
        # The body is unread, so this answer closes the connection.
        LOGGER.warning("processing_upload_timed_out")
        return _error(req, 408, "PROCESSING_UPLOAD_TIMEOUT")
    except uploads.UploadRefused as exc:
        return _error(req, _UPLOAD_STATUS[exc.code], exc.code)
    except items.Refused as exc:
        return _refused(req, exc)
    except ObjectMissing:
        return _error(req, 409, "PROCESSING_OBJECT_MISSING")
    except ValueError as exc:
        return _invalid(req, str(exc))
    finally:
        req.body.settimeout(previous_timeout)
    LOGGER.info("processing_item_uploaded created=%s",
                safe_log_label("true" if uploaded.created else "false"))
    return json_response(req, 201 if uploaded.created else 200,
                         {"item": uploaded.item, "created": uploaded.created, "duplicates": uploaded.duplicates})


# -- edit, delete, import -----------------------------------------------------------


def edit(req: Request) -> Response:
    from backend.domain.processing import commands, items

    fields = dict(req.data)
    revision = fields.pop("revision", None)
    try:
        updated = commands.edit(_library(req).database, req.ctx, _item_id(req), revision, fields)
    except items.Refused as exc:
        return _refused(req, exc)
    except ValueError as exc:
        return _invalid(req, str(exc))
    return json_response(req, 200, updated)


def delete(req: Request) -> Response:
    from backend.domain.processing import commands, items

    try:
        marker = commands.delete(_library(req).database, req.ctx, _item_id(req))
    except items.Refused as exc:
        return _refused(req, exc)
    return json_response(req, 200, marker)


def import_one(req: Request) -> Response:
    from backend.domain.objects.lifecycle import ObjectMissing
    from backend.domain.processing import commands, items

    library = _library(req)
    try:
        done = commands.import_one(library.database, req.ctx, _storage(library), _item_id(req))
    except items.Refused as exc:
        return _refused(req, exc)
    except ObjectMissing:
        return _error(req, 409, "PROCESSING_OBJECT_MISSING")
    except ValueError as exc:
        return _invalid(req, str(exc))
    return json_response(req, 201 if done.created else 200,
                         {"item_id": done.item_id, "work_id": done.work_id, "created": done.created})


def import_batch(req: Request) -> Response:
    from backend.domain.processing import commands

    data = req.data
    if set(data) - {"item_ids"}:
        # ProcessingImportRequest forbids other keys; the served schema says so too.
        return _invalid(req, "Only item_ids may be sent.")
    ids = data.get("item_ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(one, str) for one in ids):
        return _invalid(req, "item_ids must be a non-empty array of item ids.")
    if len(ids) > MAX_BATCH:
        return _invalid(req, f"At most {MAX_BATCH} items per import.")
    library = _library(req)
    results = commands.import_many(library.database, req.ctx, _storage(library), ids)
    return json_response(req, 200, {"results": [
        {"item_id": one.item_id, "ok": one.ok, "work_id": one.work_id, "created": one.created,
         "code": one.code, "message": one.message or (None if one.ok else _MESSAGES.get(one.code or "", one.code)),
         "item": one.item}
        for one in results
    ]})


_TAG = "processing-items"


def _refusal(description: str) -> Reply:
    """Every refusal here is ``ProcessingItemRefusal``: ``{error[, code, message, item]}``."""
    return error(description, ProcessingItemRefusal)


_NOT_FOUND = _refusal("No such item in this Library (ENTITY_NOT_FOUND).")
_PDF = Operation(
    "getProcessingItemPdf", "A ready item's PDF as uploaded, with Range support", _TAG,
    {200: ok(BINARY, "The whole file.", media_type=PDF),
     206: ok(BINARY, "The requested range.", media_type=PDF,
             headers={"Content-Range": Header("The range sent, as `bytes <first>-<last>/<size>`.",
                                              required=True)}),
     404: _refusal("No ready item with that id (ENTITY_NOT_FOUND)."),
     416: Reply("The range is outside the file.")},
    params=(Param("Range", "header", description="One byte range: `bytes=<first>-[<last>]`, or `bytes=-<suffix length>` for the last bytes."),))

ROUTES = (
    route("GET", COLLECTION, queue, contract=Operation(
        "listProcessingItems", "One page of the queue: ready items, oldest upload first", _TAG,
        {200: ok(ProcessingItemPage, "The page."),
         400: _refusal("A limit out of range or a malformed cursor (VALIDATION_ERROR).")},
        params=(Param("limit", schema={"type": "integer", "minimum": 1, "maximum": 500, "default": 100}),
                Param("cursor", description="The previous page's `next_cursor`.")))),
    route("POST", COLLECTION, upload, Body.STREAM, contract=Operation(
        "uploadProcessingItem", "Upload one PDF into the queue", _TAG,
        {200: ok(ProcessingItemUploaded, "The key was used: its item."),
         201: ok(ProcessingItemUploaded, "The item was created."),
         400: _refusal("A bad Idempotency-Key, filename or Content-Length (VALIDATION_ERROR), or a body "
                       "shorter than Content-Length (PROCESSING_UPLOAD_INCOMPLETE)."),
         408: _refusal("The body stalled for 30 seconds, or did not arrive within 60 seconds plus its "
                       "length at 256 KiB/s (PROCESSING_UPLOAD_TIMEOUT)."),
         409: _refusal("The key's item was deleted (PROCESSING_ITEM_DELETED)."),
         411: _refusal("No Content-Length (PROCESSING_LENGTH_REQUIRED)."),
         413: _refusal("Larger than the upload limit (PROCESSING_UPLOAD_TOO_LARGE)."),
         415: _refusal("Not sent as application/pdf (unsupported_media_type), or the body does not start "
                       "with %PDF- (PROCESSING_NOT_A_PDF).")},
        request=body(BINARY, media_type=PDF),
        params=(Param("Idempotency-Key", "header", {"type": "string", "pattern": "^[A-Za-z0-9._:-]{1,128}$"},
                      required=True,
                      description="Minted by the client per file: 1-128 of `A-Z a-z 0-9 . _ : -`."),
                Param("PRKS-Filename", "header",
                      description="The original filename, percent-encoded UTF-8; display only."),
                Param("Content-Length", "header", {"type": "integer", "minimum": 5}, required=True)),
        description="The body is the PDF. A used `Idempotency-Key` answers with its item without reading "
                    "the body. Any answer sent before the whole body was read carries "
                    "`Connection: close`. Duplicates by content are a warning only.")),
    route("POST", BATCH_IMPORT, import_batch, contract=Operation(
        "importProcessingItems", "Import several items, each in its own transaction", _TAG,
        {200: ok(ProcessingImportResults, "A result per item, in order."),
         400: _refusal("item_ids missing, empty, not strings, or more than 500, or another key sent "
                       "(VALIDATION_ERROR).")},
        request=body(ProcessingImportRequest),
        description="One item's failure never affects another's: each has its own result.")),
    route("GET", COLLECTION + "/{item_id}", item, contract=Operation(
        "getProcessingItem", "An item, or the marker an import or delete left", _TAG,
        {200: ok(ProcessingItem, "The item."), 404: _NOT_FOUND})),
    route("PATCH", COLLECTION + "/{item_id}", edit, contract=Operation(
        "updateProcessingItem", "Edit a ready item's draft at its revision", _TAG,
        {200: ok(ProcessingItem, "The item, one revision on."),
         400: _refusal("No or a bad revision, or a refused draft value, folder, person, tag or role type "
                       "(VALIDATION_ERROR)."),
         404: _NOT_FOUND,
         409: _refusal("A stale revision (REVISION_CONFLICT), or a marker (PROCESSING_ITEM_IMPORTED, "
                       "PROCESSING_ITEM_DELETED); `item` is the item as it now is.")},
        request=body(ProcessingItemUpdateRequest))),
    route("DELETE", COLLECTION + "/{item_id}", delete, contract=Operation(
        "deleteProcessingItem", "Remove a ready item; deleting it again changes nothing", _TAG,
        {200: ok(ProcessingItem, "The deleted marker."), 404: _NOT_FOUND,
         409: _refusal("The item was imported (PROCESSING_ITEM_IMPORTED).")})),
    route("GET", COLLECTION + "/{item_id}/pdf", pdf, contract=_PDF),
    route("HEAD", COLLECTION + "/{item_id}/pdf", pdf, contract=_PDF.head()),
    route("POST", COLLECTION + "/{item_id}/import", import_one, contract=Operation(
        "importProcessingItem", "Turn a ready item into a Work, in one transaction", _TAG,
        {200: ok(ProcessingItemImported, "Imported before: its Work."),
         201: ok(ProcessingItemImported, "Imported."),
         400: _refusal("A refused draft value (VALIDATION_ERROR), or a body that is not a JSON object "
                       "(invalid_request)."),
         404: _NOT_FOUND,
         409: _refusal("A deleted item (PROCESSING_ITEM_DELETED), an imported item whose Work was deleted "
                       "(IMPORTED_WORK_DELETED), or missing bytes (PROCESSING_OBJECT_MISSING).")},
        request=body({"type": "object"}, "A JSON object, ignored; may be left out.", required=False),
        description="A retry of a finished import answers with the same Work and creates nothing.")),
)
