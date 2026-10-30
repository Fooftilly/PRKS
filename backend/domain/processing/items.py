"""Processing items: reads, the draft edit and delete (§11.5; ``160_processing.sql``).

Every function runs in the caller's Library command or query. An item's
public form carries its draft (fields, roles by ``order_index`` then
creation, tags by name) while it is ``ready``, and ``draft: None`` once it
is a marker. A refusal is :class:`Refused`, with the item as it now is when
the caller needs it (a revision conflict, a marker); a request that breaks a
field rule is ``ValueError``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, LiteralString, Optional

from backend.domain.identity.library_scope import LibraryScope
from backend.domain.objects import lifecycle
from backend.domain.organization import folders
from backend.storage.objects import NAMESPACE_ASSET_OBJECTS, StorageKey

READY = "ready"
IMPORTED = "imported"
DELETED = "deleted"

NOT_FOUND = "ENTITY_NOT_FOUND"
ITEM_IMPORTED = "PROCESSING_ITEM_IMPORTED"
ITEM_DELETED = "PROCESSING_ITEM_DELETED"
WORK_DELETED = "IMPORTED_WORK_DELETED"
REVISION_CONFLICT = "REVISION_CONFLICT"

TEXT_FIELDS: tuple[LiteralString, ...] = (
    "title", "published_date", "abstract", "source_url", "author_text", "year", "publisher",
    "location", "edition", "journal", "volume", "issue", "pages", "isbn", "doi", "private_notes",
)
DRAFT_FIELDS: tuple[LiteralString, ...] = (*TEXT_FIELDS, "status_draft", "doc_type", "thumb_page",
                                           "target_folder_id")
DRAFT_STATUSES = ("Planned", "In Progress", "Completed", "Paused", "Not Started")


class Refused(Exception):
    """The command changed nothing: ``code`` says why, ``item`` is the item as it now is (or None)."""

    def __init__(self, code: str, item: Optional[dict[str, Any]] = None) -> None:
        super().__init__(code)
        self.code = code
        self.item = item


_TIMESTAMP: LiteralString = "'YYYY-MM-DD HH24:MI:SS'"
_ITEM_COLUMNS: LiteralString = (
    "i.id, i.original_filename, i.object_locator, i.sha256, i.byte_size, i.status, i.revision,"
    " i.upload_key, i.imported_work_id,"
    " to_char(i.created_at AT TIME ZONE 'UTC', " + _TIMESTAMP + ") AS created_at,"
    " to_char(i.updated_at AT TIME ZONE 'UTC', " + _TIMESTAMP + ") AS updated_at,"
    " to_char(i.imported_at AT TIME ZONE 'UTC', " + _TIMESTAMP + ") AS imported_at,"
    " to_char(i.deleted_at AT TIME ZONE 'UTC', " + _TIMESTAMP + ") AS deleted_at"
)
_DRAFT_COLUMNS: LiteralString = (
    "d.title, d.status_draft, d.published_date, d.abstract, d.source_url, d.author_text, d.year,"
    " d.publisher, d.location, d.edition, d.journal, d.volume, d.issue, d.pages, d.isbn, d.doi,"
    " d.doc_type, d.private_notes, d.thumb_page, d.target_folder_id"
)
_SELECT: LiteralString = (
    "SELECT " + _ITEM_COLUMNS + ", d.item_id AS draft_id, " + _DRAFT_COLUMNS
    + " FROM prks.processing_items i"
    " LEFT JOIN prks.processing_item_drafts d ON d.library_id = i.library_id AND d.item_id = i.id"
    " WHERE i.library_id = %(library_id)s"
)
_READY_SQL: LiteralString = _SELECT + " AND i.status = 'ready' ORDER BY i.created_at, i.id COLLATE \"C\""
_ONE_SQL: LiteralString = _SELECT + " AND i.id = %(item_id)s"
# Shared with an upload replay: import and delete take the item FOR UPDATE,
# so a replay waits for them and then answers with what they committed.
_SHARE_BY_UPLOAD_KEY_SQL: LiteralString = """
SELECT id FROM prks.processing_items
WHERE library_id = %(library_id)s AND upload_key = %(upload_key)s
FOR KEY SHARE
"""
# An edit holds the item against import and delete; those change `status`,
# a key column (the draft's key), so they take the stronger lock.
_LOCK_FOR_EDIT_SQL: LiteralString = """
SELECT id, status, revision FROM prks.processing_items
WHERE library_id = %(library_id)s AND id = %(item_id)s
FOR NO KEY UPDATE
"""
_LOCK_FOR_STATUS_SQL: LiteralString = """
SELECT id, status, object_locator, imported_work_id FROM prks.processing_items
WHERE library_id = %(library_id)s AND id = %(item_id)s
FOR UPDATE
"""
_ROLES_SQL: LiteralString = """
SELECT r.item_id, p.id AS person_id, p.first_name, p.last_name, r.role_type, r.order_index
FROM prks.processing_item_roles r
JOIN prks.persons p ON p.library_id = r.library_id AND p.id = r.person_id
WHERE r.library_id = %(library_id)s AND r.item_id = ANY(%(item_ids)s::text[])
ORDER BY r.item_id COLLATE "C", r.order_index, r.seq
"""
# As every Tag listing: ASCII-only case folding, then bytes.
_TAGS_SQL: LiteralString = """
SELECT it.item_id, t.id, t.name, t.color,
       to_char(t.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at
FROM prks.processing_item_tags it
JOIN prks.tags t ON t.library_id = it.library_id AND t.id = it.tag_id
WHERE it.library_id = %(library_id)s AND it.item_id = ANY(%(item_ids)s::text[])
ORDER BY it.item_id COLLATE "C",
         translate(t.name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') COLLATE "C",
         t.id COLLATE "C"
"""
# Held until COMMIT, so a Person or Tag cannot be deleted under a link written here.
_PERSONS_SQL: LiteralString = """
SELECT id FROM prks.persons WHERE library_id = %(library_id)s AND id = ANY(%(ids)s::text[])
ORDER BY id COLLATE "C" FOR KEY SHARE
"""
_TAG_IDS_SQL: LiteralString = """
SELECT id FROM prks.tags WHERE library_id = %(library_id)s AND id = ANY(%(ids)s::text[])
ORDER BY id COLLATE "C" FOR KEY SHARE
"""
_CLEAR_ROLES_SQL: LiteralString = """
DELETE FROM prks.processing_item_roles WHERE library_id = %(library_id)s AND item_id = %(item_id)s
"""
_INSERT_ROLES_SQL: LiteralString = """
INSERT INTO prks.processing_item_roles (library_id, item_id, person_id, role_type, order_index)
SELECT %(library_id)s, %(item_id)s, person_id, role_type, order_index
FROM unnest(%(persons)s::text[], %(types)s::text[], %(orders)s::integer[])
     WITH ORDINALITY AS wanted(person_id, role_type, order_index, n)
ORDER BY n
"""
_CLEAR_TAGS_SQL: LiteralString = """
DELETE FROM prks.processing_item_tags WHERE library_id = %(library_id)s AND item_id = %(item_id)s
"""
_INSERT_TAGS_SQL: LiteralString = """
INSERT INTO prks.processing_item_tags (library_id, item_id, tag_id)
SELECT %(library_id)s, %(item_id)s, tag_id FROM unnest(%(tags)s::text[]) AS wanted(tag_id)
ON CONFLICT DO NOTHING
"""
_BUMP_SQL: LiteralString = """
UPDATE prks.processing_items SET revision = revision + 1, updated_at = now()
WHERE library_id = %(library_id)s AND id = %(item_id)s
"""
# The draft goes first: its key refuses the status change while it exists.
# Its roles and tags cascade with it.
DROP_DRAFT_SQL: LiteralString = """
DELETE FROM prks.processing_item_drafts WHERE library_id = %(library_id)s AND item_id = %(item_id)s
"""
_MARK_DELETED_SQL: LiteralString = """
UPDATE prks.processing_items
SET status = 'deleted', deleted_at = now(), object_locator = NULL, revision = revision + 1, updated_at = now()
WHERE library_id = %(library_id)s AND id = %(item_id)s
"""
# One statement per field; each column name comes from this table, never from the request.
_FIELD_SQL: dict[str, LiteralString] = {
    field: "UPDATE prks.processing_item_drafts SET " + field + " = %(value)s"
           " WHERE library_id = %(library_id)s AND item_id = %(item_id)s"
    for field in DRAFT_FIELDS
}


# -- reads ----------------------------------------------------------------------


def _role_public(row: Any) -> dict[str, Any]:
    first = (row["first_name"] or "").strip()
    last = (row["last_name"] or "").strip()
    name = " ".join(part for part in (first, last) if part).strip() or row["person_id"] or "Unknown"
    return {"person_id": row["person_id"], "person_name": name, "role_type": row["role_type"],
            "order_index": row["order_index"]}


def _tag_public(row: Any) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "color": row["color"], "created_at": row["created_at"]}


def _grouped(scope: LibraryScope, query: LiteralString, convert: Any,
             item_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if item_ids:
        for row in scope.execute(query, {"item_ids": item_ids}):
            out[str(row["item_id"])].append(convert(row))
    return out


def _public(row: Any, roles: list[dict[str, Any]], tags: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": row["id"],
        "original_filename": row["original_filename"],
        "sha256": row["sha256"],
        "byte_size": int(row["byte_size"]),
        "status": row["status"],
        "revision": int(row["revision"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "imported_at": row["imported_at"],
        "imported_work_id": row["imported_work_id"],
        "deleted_at": row["deleted_at"],
        "draft": None,
    }
    if row["draft_id"] is not None:
        draft: dict[str, Any] = {field: row[field] for field in TEXT_FIELDS}
        draft.update(status_draft=row["status_draft"], doc_type=row["doc_type"], thumb_page=row["thumb_page"],
                     target_folder_id=row["target_folder_id"] or "", roles=roles, tags=tags)
        out["draft"] = draft
    return out


def _publish(scope: LibraryScope, rows: list[Any]) -> list[dict[str, Any]]:
    drafted = [str(row["id"]) for row in rows if row["draft_id"] is not None]
    roles = _grouped(scope, _ROLES_SQL, _role_public, drafted)
    tags = _grouped(scope, _TAGS_SQL, _tag_public, drafted)
    return [_public(row, roles.get(str(row["id"]), []), tags.get(str(row["id"]), [])) for row in rows]


def queue(scope: LibraryScope) -> list[dict[str, Any]]:
    """The queue: every ``ready`` item, oldest upload first. Markers are not listed."""
    return _publish(scope, scope.execute(_READY_SQL).fetchall())


def item(scope: LibraryScope, item_id: str) -> Optional[dict[str, Any]]:
    """One item in any state, or None when this Library has none with that id."""
    row = scope.execute(_ONE_SQL, {"item_id": item_id}).fetchone()
    return None if row is None else _publish(scope, [row])[0]


def by_upload_key(scope: LibraryScope, upload_key: str) -> Optional[dict[str, Any]]:
    """The item holding ``upload_key``, read after any import or delete of it has finished."""
    row = scope.execute(_SHARE_BY_UPLOAD_KEY_SQL, {"upload_key": upload_key}).fetchone()
    return None if row is None else item(scope, str(row["id"]))


def object_of(scope: LibraryScope, item_id: str) -> Optional[tuple[StorageKey, int, str]]:
    """A ``ready`` item's object, size and hash for the preview; None for a marker or no item."""
    row = scope.execute(_ONE_SQL, {"item_id": item_id}).fetchone()
    if row is None or row["object_locator"] is None:
        return None
    return StorageKey(NAMESPACE_ASSET_OBJECTS, str(row["object_locator"])), int(row["byte_size"]), str(row["sha256"])


def refuse_marker(found: dict[str, Any]) -> None:
    """Raise the refusal a marker answers to an edit or a delete; nothing for a ``ready`` item."""
    if found["status"] == IMPORTED:
        raise Refused(ITEM_IMPORTED, found)
    if found["status"] == DELETED:
        raise Refused(ITEM_DELETED, found)


# -- edit -------------------------------------------------------------------------


@dataclass(frozen=True)
class Update:
    """A draft edit, checked before anything is read: the fields, and roles and tags when given."""

    fields: dict[str, Any]
    roles: Optional[list[Any]]
    tags: Optional[list[Any]]


def _thumb_page(raw: Any) -> Optional[int]:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 1 else None


def parse_update(fields: Any) -> Update:
    """The SQLite inbox's draft rules (``update_processing_file``): allow-list, ``status`` alias, normalization.

    Unknown keys are ignored. Raises ``ValueError`` for an invalid status or
    a NUL in a value (PostgreSQL text cannot hold it).
    """
    from backend.db_manager import normalize_doc_type

    if not isinstance(fields, dict):
        raise ValueError("JSON object body required.")
    updates = dict(fields)
    roles = updates.pop("roles", None)
    tags = updates.pop("tags", None)
    if "status" in updates and "status_draft" not in updates:
        updates["status_draft"] = updates.pop("status")
    updates = {key: value for key, value in updates.items() if key in DRAFT_FIELDS}
    if "doc_type" in updates:
        updates["doc_type"] = normalize_doc_type(updates.get("doc_type"))
    if "status_draft" in updates:
        status = str(updates.get("status_draft") or "").strip()
        if status not in DRAFT_STATUSES:
            raise ValueError("Invalid status_draft value.")
        updates["status_draft"] = status
    if "thumb_page" in updates:
        updates["thumb_page"] = _thumb_page(updates.get("thumb_page"))
    if "target_folder_id" in updates:
        raw = updates.get("target_folder_id")
        updates["target_folder_id"] = None if raw is None or str(raw).strip() == "" else str(raw).strip()
    for key in list(updates):
        if key in ("thumb_page", "target_folder_id"):
            continue
        updates[key] = "" if updates[key] is None else str(updates[key]).strip()
    for value in updates.values():
        if isinstance(value, str) and "\x00" in value:
            raise ValueError("Processing item fields cannot contain NUL.")
    if roles is not None and not isinstance(roles, list):
        raise ValueError("roles must be an array.")
    if tags is not None and not isinstance(tags, list):
        raise ValueError("tags must be an array.")
    return Update(updates, roles, tags)


def _existing(scope: LibraryScope, query: LiteralString, ids: Iterable[str]) -> set[str]:
    wanted = sorted(set(ids))
    if not wanted:
        return set()
    return {str(row["id"]) for row in scope.execute(query, {"ids": wanted})}


def _wanted_roles(scope: LibraryScope, roles: list[Any]) -> list[tuple[str, str, int]]:
    """The roles to stage, in payload order: (person, role_type, payload index)."""
    from backend.work_role_sync import PEOPLE_ROLE_TYPE_SET

    candidates = [(index, str(role.get("person_id") or "").strip(), str(role.get("role_type") or "").strip())
                  for index, role in enumerate(roles) if isinstance(role, dict)]
    candidates = [entry for entry in candidates if entry[1] and entry[2]]
    for _index, _person, role_type in candidates:
        if role_type not in PEOPLE_ROLE_TYPE_SET:
            raise ValueError(f"Unsupported role_type: {role_type}")
    found = _existing(scope, _PERSONS_SQL, (person for _index, person, _type in candidates))
    normalized: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str]] = set()
    for index, person_id, role_type in candidates:
        if person_id not in found:
            raise ValueError(f"Unknown person id: {person_id}")
        if (person_id, role_type) in seen:
            continue
        seen.add((person_id, role_type))
        normalized.append((person_id, role_type, index))
    return normalized


def _wanted_tags(scope: LibraryScope, tags: list[Any]) -> list[str]:
    """The Tags to stage, in payload order, each once."""
    candidates = [str(entry.get("id") or entry.get("tag_id") or "").strip()
                  for entry in tags if isinstance(entry, dict)]
    candidates = [tag_id for tag_id in candidates if tag_id]
    found = _existing(scope, _TAG_IDS_SQL, candidates)
    wanted: list[str] = []
    for tag_id in candidates:
        if tag_id in wanted:
            continue
        if tag_id not in found:
            raise ValueError(f"Unknown tag id: {tag_id}")
        wanted.append(tag_id)
    return wanted


def update(scope: LibraryScope, item_id: str, revision: int, request: Update) -> dict[str, Any]:
    """Apply ``request`` to the draft of the item at ``revision``; the item after the edit.

    All or nothing, and the revision advances once. Lock order
    (``__init__``): a target Folder ``FOR KEY SHARE``, the item ``FOR NO KEY
    UPDATE``, then the named Persons and Tags ``FOR KEY SHARE``. Refused with
    ``NOT_FOUND``, a marker's code whatever the revision, or
    ``REVISION_CONFLICT`` (with the current item). Adding a tag link writes
    a peer table of the Tag merge, so the caller runs the edit SERIALIZABLE
    when ``request.tags`` is given (§7.1).
    """
    target = request.fields.get("target_folder_id")
    folder_held = target is None or folders.share(scope, target)
    row = scope.execute(_LOCK_FOR_EDIT_SQL, {"item_id": item_id}).fetchone()
    if row is None:
        raise Refused(NOT_FOUND)
    if row["status"] != READY:
        current = item(scope, item_id)
        assert current is not None
        refuse_marker(current)
    if int(row["revision"]) != revision:
        raise Refused(REVISION_CONFLICT, item(scope, item_id))
    if not folder_held:
        raise ValueError("Unknown folder.")
    roles = _wanted_roles(scope, request.roles) if request.roles is not None else None
    tags = _wanted_tags(scope, request.tags) if request.tags is not None else None
    for field in sorted(request.fields):
        scope.execute(_FIELD_SQL[field], {"item_id": item_id, "value": request.fields[field]})
    if roles is not None:
        scope.execute(_CLEAR_ROLES_SQL, {"item_id": item_id})
        if roles:
            scope.execute(_INSERT_ROLES_SQL, {"item_id": item_id, "persons": [r[0] for r in roles],
                                              "types": [r[1] for r in roles], "orders": [r[2] for r in roles]})
    if tags is not None:
        scope.execute(_CLEAR_TAGS_SQL, {"item_id": item_id})
        if tags:
            scope.execute(_INSERT_TAGS_SQL, {"item_id": item_id, "tags": tags})
    scope.execute(_BUMP_SQL, {"item_id": item_id})
    after = item(scope, item_id)
    assert after is not None
    return after


# -- delete -----------------------------------------------------------------------


def lock_for_status(scope: LibraryScope, item_id: str) -> Optional[dict[str, Any]]:
    """The item row ``FOR UPDATE``, as import and delete take it; None when there is none."""
    row = scope.execute(_LOCK_FOR_STATUS_SQL, {"item_id": item_id}).fetchone()
    return None if row is None else dict(row)


def delete(scope: LibraryScope, item_id: str) -> dict[str, Any]:
    """Turn a ``ready`` item into a deleted marker and release its key; the marker.

    The draft goes with its roles and tags, and ``objects.cleanup`` deletes
    the bytes once nothing names them. Deleting a deleted marker again
    returns it unchanged; an imported marker is refused
    (``PROCESSING_ITEM_IMPORTED``).
    """
    row = lock_for_status(scope, item_id)
    if row is None:
        raise Refused(NOT_FOUND)
    if row["status"] == READY:
        scope.execute(DROP_DRAFT_SQL, {"item_id": item_id})
        scope.execute(_MARK_DELETED_SQL, {"item_id": item_id})
        lifecycle.release(scope, StorageKey(NAMESPACE_ASSET_OBJECTS, str(row["object_locator"])))
    current = item(scope, item_id)
    assert current is not None
    if current["status"] == IMPORTED:
        raise Refused(ITEM_IMPORTED, current)
    return current
