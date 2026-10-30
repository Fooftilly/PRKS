"""The Processing queue's operations for one actor (§11.5): what the HTTP routes call.

Each runs its Library transactions through
:func:`~backend.domain.identity.library_scope.library_command` or
:func:`~backend.domain.identity.library_scope.library_query`, so every one is
authorized and fenced by maintenance and the epoch. Refusals are
:class:`~backend.domain.processing.items.Refused`,
:class:`~backend.domain.processing.uploads.UploadRefused`,
``AccessRefused`` and ``ValueError``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, BinaryIO, Callable, Optional, TypeVar

from backend.db.database import Database, Isolation
from backend.db.errors import DatabaseError
from backend.domain.identity.authorization import AccessRefused, Capability
from backend.domain.identity.library_scope import LibraryScope, library_command, library_query
from backend.domain.identity.request_context import RequestContext
from backend.domain.objects.lifecycle import ObjectMissing
from backend.domain.processing import imports, items, uploads
from backend.storage.objects import StorageBackend

T = TypeVar("T")


def _command(db: Database, ctx: RequestContext, fn: Callable[[LibraryScope], T], *,
             serializable: bool = False) -> T:
    isolation = Isolation.SERIALIZABLE if serializable else Isolation.READ_COMMITTED
    return library_command(db, ctx, Capability.EDIT_LIBRARY, fn, isolation=isolation)


def queue(db: Database, ctx: RequestContext, *, limit: int = items.DEFAULT_PAGE_SIZE,
          after: Optional[str] = None) -> tuple[list[dict[str, Any]], Optional[str]]:
    """One page of the queue and the opaque cursor of the next (None on the last page)."""
    cursor = items.Cursor.decode(after) if after is not None else None
    page = library_query(db, ctx, lambda scope: items.queue(scope, limit=limit, after=cursor))
    return page.items, page.next.encode() if page.next is not None else None


def item(db: Database, ctx: RequestContext, item_id: str) -> Optional[dict[str, Any]]:
    return library_query(db, ctx, lambda scope: items.item(scope, item_id))


def preview(db: Database, ctx: RequestContext, item_id: str) -> Any:
    """``(key, size, sha256)`` of a ``ready`` item's object; None for a marker or no item."""
    return library_query(db, ctx, lambda scope: items.object_of(scope, item_id))


# -- upload -------------------------------------------------------------------------


@dataclass(frozen=True)
class Uploaded:
    """An upload's answer; ``body_read`` is False when it was decided before the body was read."""

    item: dict[str, Any]
    created: bool
    duplicates: dict[str, list[dict[str, Any]]]
    body_read: bool


def _answer(found: uploads.Registered, body_read: bool) -> Uploaded:
    if found.item["status"] == items.DELETED:
        raise items.Refused(items.ITEM_DELETED, found.item)
    return Uploaded(found.item, found.created, found.duplicates, body_read)


def upload(db: Database, ctx: RequestContext, storage: StorageBackend, *, upload_key: str, filename: str,
           body: BinaryIO, content_length: int, max_bytes: int, item_id: Optional[str] = None) -> Uploaded:
    """One file into the queue (§11.5 steps 1-5).

    Before reading the body: the key and filename are checked, the actor's
    edit right, maintenance and the epoch are checked by a command that also
    looks the key up, and a used key is answered from its item (a deleted
    marker is refused ``PROCESSING_ITEM_DELETED``). Any refusal raised
    before the store has read nothing of ``body``.
    """
    from backend.entity_ids import generate

    upload_key = uploads.check_upload_key(upload_key)
    filename = uploads.display_filename(filename)
    uploads.check_length(content_length, max_bytes)
    found = _command(db, ctx, lambda scope: uploads.existing(scope, upload_key))
    if found is not None:
        return _answer(found, False)
    stored = uploads.store(storage, filename, body, content_length, max_bytes)
    new_id = item_id or generate("PI")
    registered = _command(db, ctx, lambda scope: uploads.register(
        scope, storage, stored, item_id=new_id, upload_key=upload_key, filename=filename,
        uploaded_by=ctx.user_id))
    return _answer(registered, True)


# -- edit and delete ----------------------------------------------------------------


def edit(db: Database, ctx: RequestContext, item_id: str, revision: Any, fields: Any) -> dict[str, Any]:
    """The draft edit at ``revision``; SERIALIZABLE when it sets tags (§7.1)."""
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("revision must be a non-negative integer.")
    request = items.parse_update(fields)
    return _command(db, ctx, lambda scope: items.update(scope, item_id, revision, request),
                    serializable=request.tags is not None)


def delete(db: Database, ctx: RequestContext, item_id: str) -> dict[str, Any]:
    return _command(db, ctx, lambda scope: items.delete(scope, item_id))


# -- import -------------------------------------------------------------------------


def import_one(db: Database, ctx: RequestContext, storage: StorageBackend, item_id: str) -> imports.Imported:
    ids = imports.NewIds.mint()
    return _command(db, ctx, lambda scope: imports.import_item(scope, storage, item_id, ids), serializable=True)


@dataclass(frozen=True)
class ItemResult:
    """One item's outcome in a batch: the Work, or the refusal and what it says."""

    item_id: str
    work_id: Optional[str] = None
    created: bool = False
    code: Optional[str] = None
    message: Optional[str] = None
    item: Optional[dict[str, Any]] = None

    @property
    def ok(self) -> bool:
        return self.code is None


def import_many(db: Database, ctx: RequestContext, storage: StorageBackend,
                item_ids: Sequence[str]) -> list[ItemResult]:
    """Import each item in its own transaction, in the order given; a result per item.

    One item's failure never affects another's: each refusal or database
    error is reported for its item and the batch goes on. An id given twice is
    imported once and answered twice (the second as a replay).
    """
    results: list[ItemResult] = []
    for item_id in item_ids:
        try:
            done = import_one(db, ctx, storage, item_id)
        except items.Refused as exc:
            results.append(ItemResult(item_id, code=exc.code, item=exc.item))
        except AccessRefused as exc:
            results.append(ItemResult(item_id, code=exc.refusal.code, message=exc.refusal.message))
        except ObjectMissing as exc:
            results.append(ItemResult(item_id, code="PROCESSING_OBJECT_MISSING", message=str(exc)))
        except ValueError as exc:
            results.append(ItemResult(item_id, code="VALIDATION_ERROR", message=str(exc)))
        except DatabaseError as exc:
            # A refused write or an exhausted retry: the item's own failure.
            results.append(ItemResult(item_id, code=exc.code, message=exc.message))
        else:
            results.append(ItemResult(item_id, work_id=done.work_id, created=done.created))
    return results
