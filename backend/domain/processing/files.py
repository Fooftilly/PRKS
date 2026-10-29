"""Staged inbox files: their rows, roles and tags (L-PR-1 #636; ``160_processing.sql``).

The reads answer ``PRKSDatabase``'s Processing methods with its shapes,
order and messages (``db_manager.py``, "Files for processing"): a row's
public form, the list (by status, then path, case-folded), roles by
``order_index`` then creation, tags by name. :func:`update` is
``update_processing_file``'s rules applied in one transaction.
:func:`reconcile` and :func:`mark_imported` are the database halves of the
scan and the import, which hold the inbox lock (``inbox.py``).
"""

from __future__ import annotations

import os
import posixpath
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, LiteralString, Optional

from backend.domain.identity.library_scope import LibraryScope
from backend.domain.organization import folders

PENDING = "pending"
ERROR = "error"
IMPORTED = "imported"
# The list order of a status (``_PROCESSING_STATUSES_ORDER``).
STATUS_ORDER = {PENDING: 0, "missing": 1, ERROR: 2, IMPORTED: 3}
TEXT_FIELDS: tuple[LiteralString, ...] = (
    "title", "published_date", "abstract", "source_url", "author_text", "year", "publisher",
    "location", "edition", "journal", "volume", "issue", "pages", "isbn", "doi", "private_notes",
)
DRAFT_STATUSES = ("Planned", "In Progress", "Completed", "Paused", "Not Started")
NOT_FOUND = "Processing file not found."

_TIMESTAMP: LiteralString = "'YYYY-MM-DD HH24:MI:SS'"
_COLUMNS: LiteralString = (
    "pf.id, pf.rel_path, pf.filename, pf.status, pf.last_error, pf.imported_work_id,"
    " to_char(pf.imported_at AT TIME ZONE 'UTC', " + _TIMESTAMP + ") AS imported_at,"
    " to_char(pf.discovered_at AT TIME ZONE 'UTC', " + _TIMESTAMP + ") AS discovered_at,"
    " to_char(pf.updated_at AT TIME ZONE 'UTC', " + _TIMESTAMP + ") AS updated_at,"
    " pf.title, pf.status_draft, pf.published_date, pf.abstract, pf.source_url, pf.author_text,"
    " pf.year, pf.publisher, pf.location, pf.edition, pf.journal, pf.volume, pf.issue, pf.pages,"
    " pf.isbn, pf.doi, pf.doc_type, pf.private_notes, pf.thumb_page, pf.target_folder_id"
)
_ALL_SQL: LiteralString = (
    "SELECT " + _COLUMNS + " FROM prks.processing_files pf WHERE pf.library_id = %(library_id)s"
    " AND (%(include_imported)s OR pf.status <> 'imported') ORDER BY pf.discovered_at, pf.id COLLATE \"C\""
)
_ONE_SQL: LiteralString = (
    "SELECT " + _COLUMNS + " FROM prks.processing_files pf WHERE pf.library_id = %(library_id)s"
    " AND pf.id = %(file_id)s"
)
_LOCK_SQL: LiteralString = """
SELECT id, rel_path, filename, status, target_folder_id FROM prks.processing_files
WHERE library_id = %(library_id)s AND id = %(file_id)s
FOR NO KEY UPDATE
"""
_ROLES_SQL: LiteralString = """
SELECT r.processing_file_id, p.id AS person_id, p.first_name, p.last_name, r.role_type, r.order_index
FROM prks.processing_file_roles r
JOIN prks.processing_files pf ON pf.library_id = r.library_id AND pf.id = r.processing_file_id
JOIN prks.persons p ON p.library_id = r.library_id AND p.id = r.person_id
WHERE r.library_id = %(library_id)s
  AND (%(file_id)s::text IS NULL OR r.processing_file_id = %(file_id)s)
  AND (%(include_imported)s OR pf.status <> 'imported')
ORDER BY r.processing_file_id COLLATE "C", r.order_index, r.seq
"""
# SQLite orders by LOWER(name): ASCII-only folding, then bytes.
_TAGS_SQL: LiteralString = """
SELECT pft.processing_file_id, t.id, t.name, t.color,
       to_char(t.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at
FROM prks.processing_file_tags pft
JOIN prks.processing_files pf ON pf.library_id = pft.library_id AND pf.id = pft.processing_file_id
JOIN prks.tags t ON t.library_id = pft.library_id AND t.id = pft.tag_id
WHERE pft.library_id = %(library_id)s
  AND (%(file_id)s::text IS NULL OR pft.processing_file_id = %(file_id)s)
  AND (%(include_imported)s OR pf.status <> 'imported')
ORDER BY pft.processing_file_id COLLATE "C",
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
DELETE FROM prks.processing_file_roles WHERE library_id = %(library_id)s AND processing_file_id = %(file_id)s
"""
_INSERT_ROLES_SQL: LiteralString = """
INSERT INTO prks.processing_file_roles (library_id, processing_file_id, person_id, role_type, order_index)
SELECT %(library_id)s, %(file_id)s, person_id, role_type, order_index
FROM unnest(%(persons)s::text[], %(types)s::text[], %(orders)s::integer[])
     WITH ORDINALITY AS wanted(person_id, role_type, order_index, n)
ORDER BY n
"""
_CLEAR_TAGS_SQL: LiteralString = """
DELETE FROM prks.processing_file_tags WHERE library_id = %(library_id)s AND processing_file_id = %(file_id)s
"""
_INSERT_TAGS_SQL: LiteralString = """
INSERT INTO prks.processing_file_tags (library_id, processing_file_id, tag_id)
SELECT %(library_id)s, %(file_id)s, tag_id FROM unnest(%(tags)s::text[]) AS wanted(tag_id)
ON CONFLICT DO NOTHING
"""
_SCAN_ROWS_SQL: LiteralString = """
SELECT id, rel_path, filename, status, import_sha256, restore_hold, restore_sha256
FROM prks.processing_files WHERE library_id = %(library_id)s
ORDER BY rel_path COLLATE "C"
"""
_FINISH_SQL: LiteralString = """
DELETE FROM prks.processing_files
WHERE library_id = %(library_id)s AND id = ANY(%(ids)s::text[]) AND status = 'imported'
"""
_CLEAR_HOLD_SQL: LiteralString = """
UPDATE prks.processing_files SET restore_hold = false, restore_sha256 = NULL, updated_at = now()
WHERE library_id = %(library_id)s AND id = ANY(%(ids)s::text[]) AND restore_hold
"""
_EXISTING_SQL: LiteralString = """
SELECT id, rel_path, filename, status, restore_hold FROM prks.processing_files
WHERE library_id = %(library_id)s
"""
_INSERT_SQL: LiteralString = """
INSERT INTO prks.processing_files (library_id, id, rel_path, filename)
SELECT %(library_id)s, id, rel_path, filename
FROM unnest(%(ids)s::text[], %(paths)s::text[], %(names)s::text[]) AS found(id, rel_path, filename)
ON CONFLICT (library_id, rel_path) DO NOTHING
"""
_REFRESH_SQL: LiteralString = """
UPDATE prks.processing_files SET status = 'pending', filename = %(filename)s, updated_at = now()
WHERE library_id = %(library_id)s AND id = %(file_id)s AND status <> 'imported'
"""
_VANISHED_SQL: LiteralString = """
DELETE FROM prks.processing_files
WHERE library_id = %(library_id)s AND id = ANY(%(ids)s::text[]) AND status <> 'imported' AND NOT restore_hold
"""
_DELETE_SQL: LiteralString = """
DELETE FROM prks.processing_files WHERE library_id = %(library_id)s AND id = %(file_id)s
"""
_ERROR_SQL: LiteralString = """
UPDATE prks.processing_files SET status = 'error', last_error = %(message)s, updated_at = now()
WHERE library_id = %(library_id)s AND id = %(file_id)s AND status <> 'imported'
"""
_IMPORTED_SQL: LiteralString = """
UPDATE prks.processing_files
SET status = 'imported', imported_work_id = %(work_id)s, imported_at = now(),
    import_sha256 = %(sha256)s, last_error = NULL, updated_at = now()
WHERE library_id = %(library_id)s AND id = %(file_id)s
"""
_PEEK_SQL: LiteralString = """
SELECT id, rel_path, filename, status, imported_work_id, target_folder_id FROM prks.processing_files
WHERE library_id = %(library_id)s AND id = %(file_id)s
"""
_FINISH_ONE_SQL: LiteralString = """
DELETE FROM prks.processing_files WHERE library_id = %(library_id)s AND id = %(file_id)s AND status = 'imported'
"""
_IMPORT_ROW_SQL: LiteralString = (
    "SELECT " + _COLUMNS + " FROM prks.processing_files pf WHERE pf.library_id = %(library_id)s"
    " AND pf.id = %(file_id)s FOR NO KEY UPDATE OF pf"
)


def _path_parts(rel_path: str) -> tuple[str, str]:
    """``(filename, folder)`` as SQLite derives them from the stored spelling."""
    folder = posixpath.dirname(rel_path)
    return posixpath.basename(rel_path), "/" if folder in ("", ".") else folder


def exists_in(inbox_dir: str, rel_path: str) -> bool:
    from backend.db_manager import safe_processing_path_under_dir

    path = safe_processing_path_under_dir(inbox_dir, rel_path)
    return bool(path) and os.path.isfile(str(path))


def _role_public(row: Any) -> dict[str, Any]:
    first = (row["first_name"] or "").strip()
    last = (row["last_name"] or "").strip()
    name = " ".join(part for part in (first, last) if part).strip() or row["person_id"] or "Unknown"
    return {"person_id": row["person_id"], "person_name": name, "role_type": row["role_type"],
            "order_index": row["order_index"]}


def _tag_public(row: Any) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "color": row["color"], "created_at": row["created_at"]}


def public(row: Any, roles: list[dict[str, Any]], tags: list[dict[str, Any]], inbox_dir: str) -> dict[str, Any]:
    """``_processing_row_to_public``: ``exists`` is whether the inbox holds a file at ``rel_path``."""
    rel_path = row["rel_path"] or ""
    filename, folder = _path_parts(rel_path)
    out: dict[str, Any] = {
        "id": row["id"],
        "rel_path": rel_path,
        "filename": row["filename"] or filename,
        "folder": folder,
        "status": row["status"] or PENDING,
        "last_error": row["last_error"],
        "imported_work_id": row["imported_work_id"],
        "imported_at": row["imported_at"],
        "discovered_at": row["discovered_at"],
        "updated_at": row["updated_at"],
        "exists": exists_in(inbox_dir, rel_path),
    }
    for field in TEXT_FIELDS:
        out[field] = row[field] or ""
    out["status_draft"] = row["status_draft"] or "Not Started"
    out["doc_type"] = row["doc_type"] or "article"
    out["thumb_page"] = row["thumb_page"]
    out["target_folder_id"] = row["target_folder_id"] or ""
    out["roles"] = list(roles)
    out["tags"] = list(tags)
    return out


def _grouped(scope: LibraryScope, query: LiteralString, convert: Any, file_id: Optional[str],
             include_imported: bool) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    params = {"file_id": file_id, "include_imported": include_imported}
    for row in scope.execute(query, params):
        out[str(row["processing_file_id"])].append(convert(row))
    return out


def all_files(scope: LibraryScope, inbox_dir: str, *, include_imported: bool = False) -> list[dict[str, Any]]:
    """``get_processing_files``: by status, then ``rel_path`` case-folded (``str.lower``), then discovery."""
    rows = scope.execute(_ALL_SQL, {"include_imported": include_imported}).fetchall()
    rows.sort(key=lambda row: (STATUS_ORDER.get(str(row["status"] or PENDING), 99), (row["rel_path"] or "").lower()))
    roles = _grouped(scope, _ROLES_SQL, _role_public, None, include_imported)
    tags = _grouped(scope, _TAGS_SQL, _tag_public, None, include_imported)
    return [public(row, roles.get(str(row["id"]), []), tags.get(str(row["id"]), []), inbox_dir) for row in rows]


def one_file(scope: LibraryScope, inbox_dir: str, file_id: str) -> Optional[dict[str, Any]]:
    """``get_processing_file``; None when this Library has no such file."""
    row = scope.execute(_ONE_SQL, {"file_id": file_id}).fetchone()
    if row is None:
        return None
    roles = _grouped(scope, _ROLES_SQL, _role_public, file_id, True)
    tags = _grouped(scope, _TAGS_SQL, _tag_public, file_id, True)
    return public(row, roles.get(file_id, []), tags.get(file_id, []), inbox_dir)


def rel_path_of(scope: LibraryScope, file_id: str) -> Optional[str]:
    row = scope.execute(_ONE_SQL, {"file_id": file_id}).fetchone()
    return None if row is None else str(row["rel_path"] or "")


# -- update ---------------------------------------------------------------------


@dataclass(frozen=True)
class Update:
    """``update_processing_file``'s request, checked before anything is read."""

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
    """``update_processing_file``'s field rules (allow-list, ``status`` alias, normalization).

    Raises ``ValueError`` with SQLite's messages, plus one for a NUL in a
    value (PostgreSQL text cannot hold it; SQLite stores it).
    """
    from backend.db_manager import normalize_doc_type

    if not isinstance(fields, dict):
        raise ValueError("JSON object body required.")
    updates = dict(fields)
    roles = updates.pop("roles", None)
    tags = updates.pop("tags", None)
    if "status" in updates and "status_draft" not in updates:
        updates["status_draft"] = updates.pop("status")
    allowed = set(TEXT_FIELDS) | {"status_draft", "doc_type", "thumb_page", "target_folder_id"}
    updates = {key: value for key, value in updates.items() if key in allowed}
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
            raise ValueError("Processing file fields cannot contain NUL.")
    return Update(updates, roles, tags)


def _existing(scope: LibraryScope, query: LiteralString, ids: Iterable[str]) -> set[str]:
    wanted = sorted(set(ids))
    if not wanted:
        return set()
    return {str(row["id"]) for row in scope.execute(query, {"ids": wanted})}


def _wanted_roles(scope: LibraryScope, roles: Any) -> list[tuple[str, str, int]]:
    """``_set_processing_roles``' checks, in payload order: (person, role_type, payload index)."""
    from backend.work_role_sync import PEOPLE_ROLE_TYPE_SET

    if not isinstance(roles, list):
        raise ValueError("roles must be an array.")
    candidates = [(index, str(role.get("person_id") or "").strip(), str(role.get("role_type") or "").strip())
                  for index, role in enumerate(roles) if isinstance(role, dict)]
    candidates = [item for item in candidates if item[1] and item[2]]
    found = _existing(scope, _PERSONS_SQL, (person for _index, person, _type in candidates))
    normalized: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str]] = set()
    for index, person_id, role_type in candidates:
        if role_type not in PEOPLE_ROLE_TYPE_SET:
            raise ValueError(f"Unsupported role_type: {role_type}")
        if person_id not in found:
            raise ValueError(f"Unknown person id: {person_id}")
        if (person_id, role_type) in seen:
            continue
        seen.add((person_id, role_type))
        normalized.append((person_id, role_type, index))
    return normalized


def _wanted_tags(scope: LibraryScope, tags: Any) -> list[str]:
    """``_set_processing_tags``' checks, in payload order."""
    if not isinstance(tags, list):
        raise ValueError("tags must be an array.")
    candidates = [str(item.get("id") or item.get("tag_id") or "").strip() for item in tags if isinstance(item, dict)]
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


def update(scope: LibraryScope, file_id: str, request: Update) -> bool:
    """Apply ``request`` to the file; False when this Library has no such file.

    All or nothing (SQLite commits the fields before it checks the roles and
    tags). Lock order (``__init__``): a target Folder ``FOR KEY SHARE``, so it
    cannot be deleted before COMMIT, then the file row, then the named
    Persons and Tags ``FOR KEY SHARE``, then the link rows. Adding a tag
    inserts into a peer table of the Tag merge, so the caller runs this
    SERIALIZABLE when ``request.tags`` is given (§7.1).
    """
    fields = dict(request.fields)
    target = fields.get("target_folder_id")
    if target is not None and not folders.share(scope, target):
        if scope.execute(_LOCK_SQL, {"file_id": file_id}).fetchone() is None:
            return False
        raise ValueError("Unknown folder.")
    if scope.execute(_LOCK_SQL, {"file_id": file_id}).fetchone() is None:
        return False
    roles = _wanted_roles(scope, request.roles) if request.roles is not None else None
    tags = _wanted_tags(scope, request.tags) if request.tags is not None else None
    if fields:
        _update_fields(scope, file_id, fields)
    if roles is not None:
        scope.execute(_CLEAR_ROLES_SQL, {"file_id": file_id})
        if roles:
            scope.execute(_INSERT_ROLES_SQL, {"file_id": file_id, "persons": [r[0] for r in roles],
                                              "types": [r[1] for r in roles], "orders": [r[2] for r in roles]})
    if tags is not None:
        scope.execute(_CLEAR_TAGS_SQL, {"file_id": file_id})
        if tags:
            scope.execute(_INSERT_TAGS_SQL, {"file_id": file_id, "tags": tags})
    return True


# One statement per field set; each column name comes from this table, never from the request.
_FIELD_SQL: dict[str, LiteralString] = {
    field: "UPDATE prks.processing_files SET " + field + " = %(value)s"
           " WHERE library_id = %(library_id)s AND id = %(file_id)s"
    for field in (*TEXT_FIELDS, "status_draft", "doc_type", "thumb_page", "target_folder_id")
}
_TOUCH_SQL: LiteralString = """
UPDATE prks.processing_files SET last_error = NULL, updated_at = now()
WHERE library_id = %(library_id)s AND id = %(file_id)s
"""


def _update_fields(scope: LibraryScope, file_id: str, fields: dict[str, Any]) -> None:
    for field in sorted(fields):
        scope.execute(_FIELD_SQL[field], {"file_id": file_id, "value": fields[field]})
    scope.execute(_TOUCH_SQL, {"file_id": file_id})


# -- the scan's database half ---------------------------------------------------


def scan_rows(scope: LibraryScope) -> list[dict[str, Any]]:
    return [dict(row) for row in scope.execute(_SCAN_ROWS_SQL)]


def reconcile(scope: LibraryScope, discovered: Sequence[tuple[str, str]], finished: Sequence[str],
              unheld: Sequence[str], new_ids: Sequence[str]) -> None:
    """The scan's one transaction: the inbox as it was found, under the inbox lock.

    ``finished`` are ``imported`` rows whose import the scan completed (their
    source is gone, or another file is at the path); ``unheld`` are held rows
    whose file has the recorded hash; ``discovered`` is ``(rel_path,
    filename)`` per PDF found, and ``new_ids`` one fresh id per discovered
    file. A discovered path with no row is staged ``pending``; a staged
    row's status returns to ``pending`` (an ``error`` is retried) and its
    filename follows the file; a row whose file is gone is deleted unless it
    is ``imported`` or held by a restore.
    """
    if finished:
        scope.execute(_FINISH_SQL, {"ids": list(finished)})
    if unheld:
        scope.execute(_CLEAR_HOLD_SQL, {"ids": list(unheld)})
    existing = {str(row["rel_path"]): row for row in scope.execute(_EXISTING_SQL)}
    found = {rel_path for rel_path, _filename in discovered}
    staged: list[tuple[str, str, str]] = []
    for (rel_path, filename), new_id in zip(discovered, new_ids):
        row = existing.get(rel_path)
        if row is None:
            staged.append((new_id, rel_path, filename))
        elif row["status"] != IMPORTED and (row["status"] != PENDING or row["filename"] != filename):
            scope.execute(_REFRESH_SQL, {"file_id": row["id"], "filename": filename})
    if staged:
        scope.execute(_INSERT_SQL, {"ids": [item[0] for item in staged], "paths": [item[1] for item in staged],
                                    "names": [item[2] for item in staged]})
    vanished = [str(row["id"]) for rel_path, row in existing.items()
                if rel_path not in found and row["status"] != IMPORTED and not row["restore_hold"]]
    if vanished:
        scope.execute(_VANISHED_SQL, {"ids": vanished})


# -- the import's database halves -------------------------------------------------


def peek(scope: LibraryScope, file_id: str) -> Optional[dict[str, Any]]:
    """The row's import inputs, unlocked (the inbox lock holds off other imports)."""
    row = scope.execute(_PEEK_SQL, {"file_id": file_id}).fetchone()
    return None if row is None else dict(row)


def finish(scope: LibraryScope, file_id: str) -> None:
    """Delete the row of an import whose source is removed."""
    scope.execute(_FINISH_ONE_SQL, {"file_id": file_id})


def delete(scope: LibraryScope, file_id: str) -> None:
    scope.execute(_DELETE_SQL, {"file_id": file_id})


def record_error(scope: LibraryScope, file_id: str, message: str) -> None:
    scope.execute(_ERROR_SQL, {"file_id": file_id, "message": message})


def import_row(scope: LibraryScope, file_id: str) -> Optional[dict[str, Any]]:
    """The file row, locked ``FOR NO KEY UPDATE``, with its roles (by order) and tags (by name)."""
    row = scope.execute(_IMPORT_ROW_SQL, {"file_id": file_id}).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["roles"] = _grouped(scope, _ROLES_SQL, _role_public, file_id, True).get(file_id, [])
    out["tags"] = _grouped(scope, _TAGS_SQL, _tag_public, file_id, True).get(file_id, [])
    return out


def mark_imported(scope: LibraryScope, file_id: str, work_id: str, sha256: str) -> None:
    scope.execute(_IMPORTED_SQL, {"file_id": file_id, "work_id": work_id, "sha256": sha256})
