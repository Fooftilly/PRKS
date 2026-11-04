"""Import: a ``ready`` item becomes a Work in one transaction (§11.5 "Import").

:func:`import_item` runs in the caller's SERIALIZABLE Library command (§7.1:
the Work create and the Tag links are peer-table writes of the Work and Tag
merges), which retries it whole on a serialization failure. Nothing is
written or moved outside the database, so a retry repeats nothing, and the
ids of what it creates are fixed by the caller (:class:`NewIds`).

The item's key becomes the Asset's ``storage_locator``: ``create_work``
adopts it (the per-object lock, the stat), the Asset names it, and the item
stops naming it before COMMIT, so cleanup sees one of the two references at
every moment. A failure rolls everything back; the item, its draft and its
object stay as they were and the error is not stored.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, LiteralString, Optional

from backend.domain.identity.library_scope import LibraryScope
from backend.domain.processing import items
from backend.storage.objects import StorageBackend

_DRAFT_SQL: LiteralString = """
SELECT title, status_draft, published_date, abstract, source_url, author_text, year, publisher, location,
       edition, journal, volume, issue, pages, isbn, doi, doc_type, private_notes, thumb_page, target_folder_id
FROM prks.processing_item_drafts WHERE library_id = %(library_id)s AND item_id = %(item_id)s
"""
_ITEM_SQL: LiteralString = """
SELECT original_filename, object_locator, sha256, byte_size FROM prks.processing_items
WHERE library_id = %(library_id)s AND id = %(item_id)s
"""
_ROLES_SQL: LiteralString = """
SELECT person_id, role_type, order_index FROM prks.processing_item_roles
WHERE library_id = %(library_id)s AND item_id = %(item_id)s
ORDER BY order_index, seq
"""
_TAGS_SQL: LiteralString = """
SELECT tag_id FROM prks.processing_item_tags
WHERE library_id = %(library_id)s AND item_id = %(item_id)s
ORDER BY tag_id COLLATE "C"
"""
_MARK_IMPORTED_SQL: LiteralString = """
UPDATE prks.processing_items
SET status = 'imported', imported_at = now(), imported_work_id = %(work_id)s, object_locator = NULL,
    revision = revision + 1, updated_at = now()
WHERE library_id = %(library_id)s AND id = %(item_id)s
"""
_BIBLIOGRAPHIC: tuple[str, ...] = (
    "year", "published_date", "edition", "publisher", "location", "journal", "volume", "issue", "pages",
    "isbn", "doi",
)


@dataclass(frozen=True)
class NewIds:
    """The ids an import creates, minted once by the caller so a retry reuses them."""

    work_id: str
    manifestation_id: str
    asset_id: str
    folder_id: str  # used only when "Uncategorized" must be created

    @classmethod
    def mint(cls) -> NewIds:
        from backend.entity_ids import generate

        return cls(generate("W"), generate("MF"), generate("AS"), generate("F"))


@dataclass(frozen=True)
class Imported:
    """The Work an item became; ``created`` is False for a replay of a finished import."""

    item_id: str
    work_id: str
    created: bool


def _new_work(ids: NewIds, row: dict[str, Any], draft: dict[str, Any]) -> Any:
    """The Work the draft describes, with the item's object as its managed file."""
    from backend.domain.works import commands

    filename = str(row["original_filename"] or "")
    title = str(draft["title"] or "").strip() or os.path.splitext(filename)[0].strip() or "Untitled"
    # A PDF's source URL is provenance, kept as given, never a video identity
    # (``_canonical_new_source`` for a "pdf" kind); the edit normalized the doc type.
    url = str(draft["source_url"] or "").strip()
    bibliographic: dict[str, Optional[str]] = {name: draft[name] or "" for name in _BIBLIOGRAPHIC}
    bibliographic["doc_type"] = str(draft["doc_type"])
    bibliographic["urldate"] = None
    managed = commands.ManagedFile(
        storage_locator=str(row["object_locator"]), sha256=str(row["sha256"]), byte_size=int(row["byte_size"]),
        origin="processing_import", ingest_sha256=str(row["sha256"]))
    return commands.NewWork(
        work_id=ids.work_id,
        manifestation_id=ids.manifestation_id,
        title=title,
        status=draft["status_draft"],
        abstract=draft["abstract"],
        author_text=draft["author_text"],
        bibliographic=bibliographic,
        citation_url=url or None,
        asset_id=ids.asset_id,
        asset=commands.NewAsset(source=managed, thumb_page=draft["thumb_page"]),
        private_note=(draft["private_notes"] or "").strip() or None,
    )


def import_item(scope: LibraryScope, storage: StorageBackend, item_id: str, ids: NewIds) -> Imported:
    """Import the item, or answer for an import already done (§11.5 steps 1-5).

    Lock order: the item ``FOR UPDATE``, the target Folder ``FOR KEY SHARE``
    (or "Uncategorized"), then the Work create's, the roles', the Tag links'
    and the filing's own orders. Refused with ``NOT_FOUND``,
    ``PROCESSING_ITEM_DELETED``, or ``IMPORTED_WORK_DELETED`` for an
    imported marker whose Work is gone.
    """
    from backend.domain.organization import folders, links
    from backend.domain.people import roles
    from backend.domain.works import commands

    locked = items.lock_for_status(scope, item_id)
    if locked is None:
        raise items.Refused(items.NOT_FOUND)
    if locked["status"] == items.IMPORTED:
        if locked["imported_work_id"] is None:
            raise items.Refused(items.WORK_DELETED, items.item(scope, item_id))
        return Imported(item_id, str(locked["imported_work_id"]), False)
    if locked["status"] == items.DELETED:
        raise items.Refused(items.ITEM_DELETED, items.item(scope, item_id))
    row = scope.execute(_ITEM_SQL, {"item_id": item_id}).fetchone()
    draft = scope.execute(_DRAFT_SQL, {"item_id": item_id}).fetchone()
    if row is None or draft is None:  # pragma: no cover - a ready item has both, under the lock
        raise RuntimeError("a ready item has no draft")
    target = draft["target_folder_id"]
    # A Folder deleted since the draft named it leaves the Work in "Uncategorized".
    folder_id = target if target and folders.share(scope, target) else folders.ensure_uncategorized(
        scope, ids.folder_id)
    work_id = commands.create_work(scope, _new_work(ids, dict(row), dict(draft)), objects=storage)
    if work_id is None:  # pragma: no cover - only an ``if_absent`` create returns None
        raise RuntimeError("the Work create returned no id")
    staged = scope.execute(_ROLES_SQL, {"item_id": item_id}).fetchall()
    roles.insert_initial_roles(scope, work_id, [
        roles.InitialRole(str(role["person_id"]), str(role["role_type"]), int(role["order_index"]))
        for role in staged
    ], skip_invalid=True)
    for tag in scope.execute(_TAGS_SQL, {"item_id": item_id}).fetchall():
        links.edit(scope, links.WORK, work_id, str(tag["tag_id"]), True)
    folders.file_new_work(scope, work_id, folder_id)
    scope.execute(items.DROP_DRAFT_SQL, {"item_id": item_id})
    scope.execute(_MARK_IMPORTED_SQL, {"item_id": item_id, "work_id": work_id})
    return Imported(item_id, work_id, True)
