"""Managed Asset bytes: ``GET``/``HEAD /api/assets/<asset id>/content`` (#1427).

The PDF viewer reads a Work's managed PDF with ``Range`` requests (required
for HTTP/1.1 WASM PDF engines), so :func:`pdf_response` answers ``206`` for
one satisfiable range, ``416`` for an unsatisfiable one and ``200`` for the
whole file; the Processing queue's preview uses it too. PDF answers are
never stored (``no-store``).

The Asset id is the identity: the route finds the storage key the acting
Library's Asset names (an id from another Library reads as missing) and
streams the bytes from the Library's ``StorageBackend``. The URL a Work
reports carries ``?v=<sha256 head>`` so it changes with the bytes
(``backend/domain/objects/content_url.py``).
"""

from __future__ import annotations

import io
import logging
from typing import BinaryIO

from backend.web.byte_range import parse_byte_range
from backend.storage.objects import (
    NAMESPACE_ASSET_OBJECTS,
    InvalidStorageKey,
    ObjectNotFound,
    StorageKey,
    StorageObjectError,
)
from backend.web.http import Request, Response, Write, error_response
from backend.web.routing import route

LOGGER = logging.getLogger("prks.server")

CHUNK_BYTES = 65536
_NO_STORE = (
    ("Cache-Control", "no-store, max-age=0, must-revalidate"),
    ("Pragma", "no-cache"),
    ("Expires", "0"),
)


def _skip_to(stream: BinaryIO, offset: int) -> None:
    """Position a read stream at ``offset``: seek when it can, else read and discard."""
    if stream.seekable():
        stream.seek(offset)
        return
    while offset > 0:
        chunk = stream.read(min(CHUNK_BYTES, offset))
        if not chunk:
            return
        offset -= len(chunk)


def pdf_response(req: Request, file_size: int, stream: BinaryIO) -> Response:
    """``file_size`` PDF bytes read from ``stream`` (positioned at 0), with ``Range`` support.

    The response owns ``stream`` and closes it once sent.
    """
    # Repeated field lines are one comma-separated list (RFC 9110 §5.3).
    answer = parse_byte_range(", ".join(req.headers.get_all("Range")) or None, file_size)
    fields = req.log_fields()
    if answer.status == 416:
        stream.close()
        return Response(416, [("Content-Range", f"bytes */{file_size}")])
    if answer.status == 206:
        start, length = answer.start, answer.end - answer.start + 1
        LOGGER.debug(
            "pdf_stream mode=partial method=%s route=%s start=%s end=%s file_size=%s request_id=%s",
            fields["method"], fields["route"], answer.start, answer.end, file_size, fields["request_id"],
        )
        headers = [("Content-Type", "application/pdf"), ("Accept-Ranges", "bytes"), *_NO_STORE,
                   ("Content-Length", str(length)), ("Content-Range", f"bytes {answer.start}-{answer.end}/{file_size}")]
        status = 206
    else:
        start, length = 0, file_size
        LOGGER.debug(
            "pdf_stream mode=full method=%s route=%s file_size=%s request_id=%s",
            fields["method"], fields["route"], file_size, fields["request_id"],
        )
        headers = [("Content-Type", "application/pdf"), ("Accept-Ranges", "bytes"), *_NO_STORE,
                   ("Content-Length", str(file_size))]
        status = 200

    def send(write: Write) -> None:
        if start:
            _skip_to(stream, start)
        remaining = length
        while remaining > 0:
            chunk = stream.read(min(CHUNK_BYTES, remaining))
            if not chunk:
                break
            write(chunk)
            remaining -= len(chunk)

    return Response(status, headers, stream=send, cleanup=stream.close)


def _object_size(stream: BinaryIO) -> int:
    """The byte size of an open object stream, read from the stream itself."""
    end = stream.seek(0, io.SEEK_END)
    stream.seek(0)
    return end


def asset_content(req: Request) -> Response:
    """``GET``/``HEAD /api/assets/<asset id>/content``: a managed Asset's bytes in the acting Library.

    A ``?v=`` that no longer names the Asset's bytes is 404: a viewer or a
    cache holding an earlier version never receives newer bytes (or ranges
    of them) under the older URL.
    """
    from backend.api.common import objects
    from backend.domain.works import work_commands

    versions = req.query.get("v") or [None]
    if len(versions) > 1:
        return error_response(404, "PDF not found")
    name = work_commands.asset_object(*req.actor, req.params["asset_id"], versions[0])
    try:
        key = StorageKey(NAMESPACE_ASSET_OBJECTS, name) if name else None
    except InvalidStorageKey:
        key = None
    if key is None:
        return error_response(404, "PDF not found")
    store = objects(req)
    try:
        stream = store.open_read(key)
    except (StorageObjectError, OSError):
        return error_response(404, "PDF not found")
    try:
        file_size = _object_size(stream) if stream.seekable() else None
        if file_size is None:
            info = store.stat(key)
            if info is None:
                raise ObjectNotFound("key does not exist")
            file_size = info.size
    except (StorageObjectError, OSError):
        stream.close()
        return error_response(404, "PDF not found")
    return pdf_response(req, file_size, stream)


ROUTES = (
    route("GET", "/api/assets/{asset_id}/content", asset_content),
    route("HEAD", "/api/assets/{asset_id}/content", asset_content),
)
