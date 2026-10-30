"""Uploads into the Processing queue (§11.5 "Upload").

:func:`store` is the storage half and touches no database: it streams one
PDF body into a fresh ``asset-objects`` key through ``put_new``, which
hashes and syncs it before publishing (§11.2). Nothing is linearized: the
original bytes are the item's object and, after import, the Asset's.

:func:`register` is the database half, in one READ COMMITTED Library
command: it adopts the key and inserts the item and its empty draft, or,
when a concurrent upload with the same idempotency key committed first,
releases its own key and answers with the winner. Bytes no command adopts
(a refused body, a disconnect, a crash before the insert) are orphans the
sweep claims after its grace period; nothing here deletes.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from typing import Any, BinaryIO, LiteralString

from backend.domain.identity.library_scope import LibraryScope
from backend.domain.objects import lifecycle
from backend.domain.objects.lifecycle import StoredObject
from backend.domain.processing import items
from backend.storage.objects import (
    IO_CHUNK_SIZE,
    NAMESPACE_ASSET_OBJECTS,
    StorageBackend,
    StorageKey,
    mint_name,
)

PDF_MAGIC = b"%PDF-"
MAX_FILENAME_BYTES = 1024
UPLOAD_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
FALLBACK_FILENAME = "upload.pdf"

# Refusals of the body. Each leaves at most an unreferenced object.
NOT_A_PDF = "PROCESSING_NOT_A_PDF"
TOO_LARGE = "PROCESSING_UPLOAD_TOO_LARGE"
INCOMPLETE = "PROCESSING_UPLOAD_INCOMPLETE"

_INSERT_ITEM_SQL: LiteralString = """
INSERT INTO prks.processing_items (library_id, id, original_filename, object_locator, sha256, byte_size,
                                   upload_key, uploaded_by)
VALUES (%(library_id)s, %(item_id)s, %(filename)s, %(locator)s, %(sha256)s, %(size)s, %(upload_key)s,
        %(uploaded_by)s)
ON CONFLICT (library_id, upload_key) DO NOTHING
RETURNING id
"""
_INSERT_DRAFT_SQL: LiteralString = """
INSERT INTO prks.processing_item_drafts (library_id, item_id) VALUES (%(library_id)s, %(item_id)s)
"""
_SAME_ITEMS_SQL: LiteralString = """
SELECT id, original_filename FROM prks.processing_items
WHERE library_id = %(library_id)s AND status = 'ready' AND sha256 = %(sha256)s AND id <> %(item_id)s
ORDER BY created_at, id COLLATE "C"
"""
_SAME_WORKS_SQL: LiteralString = """
SELECT DISTINCT w.id, w.title FROM prks.assets a
JOIN prks.works w ON w.library_id = a.library_id AND w.id = a.work_id
WHERE a.library_id = %(library_id)s AND (a.content_sha256 = %(sha256)s OR a.ingest_sha256 = %(sha256)s)
ORDER BY w.id
"""


class UploadRefused(Exception):
    """The body was refused: ``code`` is one of this module's refusals."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


EMPTY: dict[str, list[dict[str, Any]]] = {"items": [], "works": []}


@dataclass(frozen=True)
class Registered:
    """What an upload answers: the item, whether this request created it, and its duplicates."""

    item: dict[str, Any]
    created: bool
    duplicates: dict[str, list[dict[str, Any]]]


def check_upload_key(upload_key: Any) -> str:
    if not isinstance(upload_key, str) or not UPLOAD_KEY_RE.fullmatch(upload_key):
        raise ValueError("Idempotency-Key must be 1-128 letters, digits or . _ : -")
    return upload_key


def display_filename(raw: Any) -> str:
    """The name the user sees: the last path component of what the client sent, never a path."""
    name = posixpath.basename(str(raw or "").replace("\\", "/")).strip()
    if "\x00" in name or any(ord(char) < 32 for char in name):
        raise ValueError("The filename contains control characters.")
    if len(name.encode("utf-8")) > MAX_FILENAME_BYTES:
        raise ValueError("The filename is too long.")
    return name or FALLBACK_FILENAME


def check_length(content_length: int, max_bytes: int) -> None:
    """Refuse a declared size before any byte is read."""
    if content_length < len(PDF_MAGIC):
        raise UploadRefused(NOT_A_PDF if content_length >= 0 else INCOMPLETE)
    if content_length > max_bytes:
        raise UploadRefused(TOO_LARGE)


def store(storage: StorageBackend, filename: str, body: BinaryIO, content_length: int,
          max_bytes: int) -> StoredObject:
    """Stream exactly ``content_length`` bytes of ``body`` into a fresh key; what was written.

    Reads no byte past ``content_length``. Refuses (:class:`UploadRefused`,
    nothing published) a body that does not start with ``%PDF-``, a
    declared size over ``max_bytes``, or a body that ends early.
    """
    check_length(content_length, max_bytes)

    def write(sink: BinaryIO) -> None:
        remaining = content_length
        head = _read_exactly(body, len(PDF_MAGIC))
        if head != PDF_MAGIC:
            raise UploadRefused(NOT_A_PDF if len(head) == len(PDF_MAGIC) else INCOMPLETE)
        sink.write(head)
        remaining -= len(head)
        while remaining:
            chunk = body.read(min(IO_CHUNK_SIZE, remaining))
            if not chunk:
                raise UploadRefused(INCOMPLETE)
            sink.write(chunk)
            remaining -= len(chunk)

    key = StorageKey(NAMESPACE_ASSET_OBJECTS, mint_name(NAMESPACE_ASSET_OBJECTS, filename))
    info = storage.put_new(key, write)
    if info.sha256 is None:  # pragma: no cover - put_new hashes by default
        raise RuntimeError("the stored upload has no hash")
    return StoredObject(info.key, info.size, info.sha256, info.version)


def _read_exactly(body: BinaryIO, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = body.read(size - len(data))
        if not chunk:
            break
        data += chunk
    return data


def existing(scope: LibraryScope, upload_key: str) -> Registered | None:
    """The answer for an upload key already used, in any state; None when the key is free.

    A deleted marker is answered as it is: the caller refuses it with
    ``PROCESSING_ITEM_DELETED`` once its command has committed.
    """
    found = items.by_upload_key(scope, upload_key)
    if found is None:
        return None
    return Registered(found, False, duplicates(scope, found) if found["status"] == items.READY else EMPTY)


def duplicates(scope: LibraryScope, found: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Other ``ready`` items and Works of this Library with the same bytes; a warning only."""
    params = {"sha256": found["sha256"], "item_id": found["id"]}
    return {
        "items": [{"id": row["id"], "original_filename": row["original_filename"]}
                  for row in scope.execute(_SAME_ITEMS_SQL, params)],
        "works": [{"id": row["id"], "title": row["title"]} for row in scope.execute(_SAME_WORKS_SQL, params)],
    }


def register(scope: LibraryScope, storage: StorageBackend, obj: StoredObject, *, item_id: str,
             upload_key: str, filename: str, uploaded_by: str | None) -> Registered:
    """Adopt ``obj`` and insert its item and empty draft; or answer with the item that holds the key.

    Raises ``lifecycle.ObjectMissing`` (nothing committed) when the bytes
    are gone or not the ones written. A lost race commits the release of
    this upload's own key and returns the winner as :func:`existing` does.
    """
    if obj.key.namespace != NAMESPACE_ASSET_OBJECTS:
        raise ValueError("an upload lives in the asset-objects namespace")
    lifecycle.adopt(scope, storage, obj)
    inserted = scope.execute(_INSERT_ITEM_SQL, {
        "item_id": item_id, "filename": filename, "locator": obj.key.name, "sha256": obj.sha256,
        "size": obj.size, "upload_key": upload_key, "uploaded_by": uploaded_by,
    }).fetchone()
    if inserted is None:
        # A concurrent upload with this key committed first: ours is never named.
        lifecycle.release(scope, obj.key)
        found = existing(scope, upload_key)
        if found is None:  # pragma: no cover - the conflicting row is committed and markers stay
            raise RuntimeError("the upload key's item vanished")
        return found
    scope.execute(_INSERT_DRAFT_SQL, {"item_id": item_id})
    created = items.item(scope, item_id)
    assert created is not None
    return Registered(created, True, duplicates(scope, created))
