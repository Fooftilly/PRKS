"""Folders, their hierarchy and fields, and which Folder a Work is in, on PostgreSQL (L-O-1 #633).

The rules are ``backend/folder_sync.py``'s, imported rather than copied:
the field vocabulary and bounds, the canonical spelling (``canonical_wire``,
``DEFAULT_TITLE``), the rule codes (``FolderRuleError``) and the scopes, one
per field (``folder-field / ["F-…","field"]``) and one per Work
(``work-folder / W-…``, the bare id). Only a change advances a scope, and
construction (a new Folder, the filing of a new Work) advances none.

What moves:

- **Titles** compare by identity key (``prks.identity_key``, §8.1), not
  SQLite's ASCII-only ``LOWER(TRIM())``, unique per parent with the root
  folders in one scope (``NULLS NOT DISTINCT``). The unique index is the
  arbiter: a create inserts with ``ON CONFLICT DO NOTHING`` and reads the
  holder, and a rename or move that loses a race fails on the index
  (``TITLE_TAKEN``), so its caller decides once more with the winner
  visible.
- **Acyclicity** needs the whole tree, so a change of ``parent_id`` first
  takes the Library's folder hierarchy lock: two concurrent moves can then
  never each pass the cycle check against the tree the other is changing.
  A create cannot close a cycle (the new Folder has no children) and a
  delete refuses a Folder with subfolders, so neither takes it.
- **A Work's Folder** is a scalar of the Work (``folder_files`` has one row
  per Work at most). Every writer of it holds the Work row first, which
  serializes them, and the Folder it files into ``FOR KEY SHARE``, so a
  Folder delete (``FOR UPDATE``) and a filing wait for each other and the
  later one decides on what the first committed.
- **A deleted Folder** leaves its scopes one revision on (§5.2): every field
  scope, a never-edited one too (a revision-0 scope gets its row), and every
  Folder-Tag scope it ever had, its links removed in the same transaction
  (SQLite cascades them without advancing). A client-minted id can be
  created again, and a base observed before the delete is then stale.

**Lock order** (with the package order in ``backend.domain.organization``):

1. the Work row (``FOR NO KEY UPDATE``) of a command that files a Work;
2. the folder hierarchy lock, for a change of ``parent_id``;
3. the Folder row a command changes (``FOR UPDATE`` for its title, parent or
   delete, ``FOR NO KEY UPDATE`` otherwise), then the one other Folder it
   names (the new parent, or the Folder a Work goes into), ``FOR KEY SHARE``;
4. revision scopes, sorted.

No Folder command locks a Tag. A Tag delete or merge holds its Tags and
then the Folders it moves links of ``FOR KEY SHARE``, which waits for a
Folder delete's ``FOR UPDATE`` and never the other way round, so a Folder
delete removes its Tag links without the Tags.
"""

from __future__ import annotations

from typing import Any, LiteralString, Optional

from backend import folder_sync as rules
from backend import work_metadata_sync as meta
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.organization import links
from backend.domain.works import revisions
from backend.folder_sync import FolderRuleError
from backend.pg_facade.registry import NotPortedError

FIELD_SCOPE_TYPE = rules.FIELD_SCOPE_TYPE
WORK_SCOPE_TYPE = rules.WORK_SCOPE_TYPE
UNCATEGORIZED_TITLE = "Uncategorized"

# The unique index on (library_id, parent_id, title_key): the arbiter of a
# title race (registered as TITLE_TAKEN in the package).
TITLE_KEY = "folders_library_id_parent_id_title_key_key"

_HIERARCHY_LOCK_SQL: LiteralString = (
    "SELECT pg_advisory_xact_lock(hashtextextended('prks.folders:' || %(library_id)s, 0))"
)
_COLUMNS: LiteralString = """
f.id, f.title, f.description, f.private_notes, f.parent_id,
to_char(f.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at,
to_char(f.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS updated_at
"""
_FOLDER_SQL: LiteralString = (
    "SELECT " + _COLUMNS + " FROM prks.folders f WHERE f.library_id = %(library_id)s AND f.id = %(folder_id)s"
)
# SQLite's NOCASE: ASCII letters fold, everything else compares by code
# point. An ASCII-only translate() under COLLATE "C" is the same order.
TITLE_ORDER: LiteralString = (
    "translate(f.title, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') COLLATE \"C\""
)
# Counts are aggregated once and joined, never counted per row
# (scripts/check_correlated_catalog_counts.py).
_COUNTS: LiteralString = """
LEFT JOIN (SELECT folder_id, count(*) AS n FROM prks.folder_files
           WHERE library_id = %(library_id)s GROUP BY folder_id) wc ON wc.folder_id = f.id
LEFT JOIN (SELECT parent_id, count(*) AS n FROM prks.folders
           WHERE library_id = %(library_id)s AND parent_id IS NOT NULL GROUP BY parent_id) cc
  ON cc.parent_id = f.id
"""
# `get_all_folders`: every Folder but an empty root "Uncategorized", which
# SQLite hides until it holds something.
_CATALOG_SQL: LiteralString = (
    "SELECT " + _COLUMNS + ", COALESCE(wc.n, 0) AS work_count, COALESCE(cc.n, 0) AS child_count"
    " FROM prks.folders f" + _COUNTS
    + " WHERE f.library_id = %(library_id)s"
    " AND NOT (f.parent_id IS NULL AND btrim(f.title, ' ') = %(uncategorized)s"
    "          AND wc.n IS NULL AND cc.n IS NULL)"
    " ORDER BY " + TITLE_ORDER + ", date_trunc('second', f.created_at) DESC, f.id COLLATE \"C\""
)
_CHILDREN_SQL: LiteralString = (
    "SELECT f.id, f.title, f.parent_id, f.description,"
    " COALESCE(wc.n, 0) AS work_count, COALESCE(cc.n, 0) AS child_count"
    " FROM prks.folders f" + _COUNTS
    + " WHERE f.library_id = %(library_id)s AND f.parent_id = %(folder_id)s"
    " ORDER BY " + TITLE_ORDER + ", f.id COLLATE \"C\""
)
_PARENT_SQL: LiteralString = """
SELECT p.id, p.title FROM prks.folders f
JOIN prks.folders p ON p.library_id = f.library_id AND p.id = f.parent_id
WHERE f.library_id = %(library_id)s AND f.id = %(folder_id)s
"""
_LOCK_SQL: LiteralString = """
SELECT id FROM prks.folders WHERE library_id = %(library_id)s AND id = %(folder_id)s FOR NO KEY UPDATE
"""
_LOCK_KEY_SQL: LiteralString = """
SELECT id FROM prks.folders WHERE library_id = %(library_id)s AND id = %(folder_id)s FOR UPDATE
"""
_SHARE_SQL: LiteralString = """
SELECT id FROM prks.folders WHERE library_id = %(library_id)s AND id = %(folder_id)s FOR KEY SHARE
"""
_TITLE_HOLDER_SQL: LiteralString = """
SELECT id FROM prks.folders
WHERE library_id = %(library_id)s AND parent_id IS NOT DISTINCT FROM %(parent_id)s
  AND title_key = prks.identity_key(%(title)s)
"""
_INSERT_SQL: LiteralString = """
INSERT INTO prks.folders (library_id, id, title, description, parent_id, private_notes)
VALUES (%(library_id)s, %(folder_id)s, %(title)s, %(description)s, %(parent_id)s, %(private_notes)s)
ON CONFLICT DO NOTHING
RETURNING id
"""
_EXISTS_SQL: LiteralString = "SELECT 1 FROM prks.folders WHERE library_id = %(library_id)s AND id = %(folder_id)s"
_DESCENDANTS_SQL: LiteralString = """
WITH RECURSIVE sub(id) AS (
  SELECT id FROM prks.folders WHERE library_id = %(library_id)s AND parent_id = %(folder_id)s
  UNION
  SELECT f.id FROM prks.folders f JOIN sub ON f.parent_id = sub.id
  WHERE f.library_id = %(library_id)s
)
SELECT id FROM sub
"""
_SET_SQL: dict[str, LiteralString] = {
    "title": "UPDATE prks.folders SET title = %(value)s, updated_at = now()"
             " WHERE library_id = %(library_id)s AND id = %(folder_id)s",
    "description": "UPDATE prks.folders SET description = %(value)s, updated_at = now()"
                   " WHERE library_id = %(library_id)s AND id = %(folder_id)s",
    "private_notes": "UPDATE prks.folders SET private_notes = %(value)s, updated_at = now()"
                     " WHERE library_id = %(library_id)s AND id = %(folder_id)s",
    "parent_id": "UPDATE prks.folders SET parent_id = %(value)s, updated_at = now()"
                 " WHERE library_id = %(library_id)s AND id = %(folder_id)s",
}
_FILE_COUNT_SQL: LiteralString = """
SELECT count(*) AS n FROM prks.folder_files WHERE library_id = %(library_id)s AND folder_id = %(folder_id)s
"""
_CHILD_COUNT_SQL: LiteralString = """
SELECT count(*) AS n FROM prks.folders WHERE library_id = %(library_id)s AND parent_id = %(folder_id)s
"""
_MEMBERS_SQL: LiteralString = """
SELECT work_id FROM prks.folder_files WHERE library_id = %(library_id)s AND folder_id = %(folder_id)s
ORDER BY work_id COLLATE "C"
"""
_FOLDER_TAG_IDS_SQL: LiteralString = """
SELECT tag_id FROM prks.folder_tags WHERE library_id = %(library_id)s AND folder_id = %(folder_id)s
"""
_DELETE_FOLDER_TAGS_SQL: LiteralString = """
DELETE FROM prks.folder_tags WHERE library_id = %(library_id)s AND folder_id = %(folder_id)s
"""
# Every field and Folder-Tag scope of the Folder, ever: their ids are JSON
# arrays whose first element is the Folder. Ids hold no quote, so the prefix
# cannot reach another Folder.
_TOMBSTONE_SQL: LiteralString = """
UPDATE prks.entity_revisions SET revision = revision + 1, updated_at = now()
WHERE library_id = %(library_id)s AND scope_type = ANY(%(scope_types)s)
  AND left(scope_id, %(prefix_len)s) = %(prefix)s
"""
_DELETE_SQL: LiteralString = "DELETE FROM prks.folders WHERE library_id = %(library_id)s AND id = %(folder_id)s"
_WORK_FOLDER_SQL: LiteralString = """
SELECT folder_id FROM prks.folder_files WHERE library_id = %(library_id)s AND work_id = %(work_id)s
"""
_UNFILE_SQL: LiteralString = """
DELETE FROM prks.folder_files WHERE library_id = %(library_id)s AND work_id = %(work_id)s
"""
_FILE_SQL: LiteralString = """
INSERT INTO prks.folder_files (library_id, work_id, folder_id)
VALUES (%(library_id)s, %(work_id)s, %(folder_id)s)
"""
_FILE_NEW_SQL: LiteralString = """
INSERT INTO prks.folder_files (library_id, work_id, folder_id)
VALUES (%(library_id)s, %(work_id)s, %(folder_id)s)
ON CONFLICT DO NOTHING
"""
_TOUCH_WORK_SQL: LiteralString = """
UPDATE prks.works SET updated_at = now() WHERE library_id = %(library_id)s AND id = %(work_id)s
"""
_WORK_EXISTS_SQL: LiteralString = "SELECT 1 FROM prks.works WHERE library_id = %(library_id)s AND id = %(work_id)s"
_IN_PLAYLIST_SQL: LiteralString = """
SELECT 1 FROM prks.playlist_items WHERE library_id = %(library_id)s AND work_id = %(work_id)s
"""
_UNCATEGORIZED_SQL: LiteralString = """
SELECT id FROM prks.folders
WHERE library_id = %(library_id)s AND parent_id IS NULL AND title_key = prks.identity_key(%(title)s)
"""
_RELATED_SQL: LiteralString = (
    "SELECT DISTINCT " + _COLUMNS + " FROM prks.folders f"
    " JOIN prks.folder_files ff ON ff.library_id = f.library_id AND ff.folder_id = f.id"
    " JOIN prks.roles r1 ON r1.library_id = ff.library_id AND r1.work_id = ff.work_id"
    " JOIN prks.roles r2 ON r2.library_id = r1.library_id AND r2.person_id = r1.person_id"
    " WHERE f.library_id = %(library_id)s AND r2.work_id = %(work_id)s AND ff.work_id <> %(work_id)s"
    " ORDER BY f.id"
)


class ForeignFolderId(Exception):
    """The id is a Folder of another Library."""


# -- locks and small reads ------------------------------------------------------


def lock_hierarchy(scope: LibraryScope) -> None:
    """Serialize this Library's changes of a Folder parent until the transaction ends."""
    scope.execute(_HIERARCHY_LOCK_SQL)


def lock(scope: LibraryScope, folder_id: str, *, key: bool = False) -> bool:
    """Hold the Folder a command changes (``FOR UPDATE`` when ``key``); False when absent."""
    sql = _LOCK_KEY_SQL if key else _LOCK_SQL
    return scope.execute(sql, {"folder_id": folder_id}).fetchone() is not None


def share(scope: LibraryScope, folder_id: str) -> bool:
    """Hold a Folder a command names (a parent, a filing target) against its delete."""
    return scope.execute(_SHARE_SQL, {"folder_id": folder_id}).fetchone() is not None


def exists(scope: LibraryScope, folder_id: str) -> bool:
    return scope.execute(_EXISTS_SQL, {"folder_id": folder_id}).fetchone() is not None


def folder_row(scope: LibraryScope, folder_id: str) -> Optional[dict[str, Any]]:
    """The Folder row (``SELECT * FROM folders`` on SQLite), or None."""
    row = scope.execute(_FOLDER_SQL, {"folder_id": folder_id}).fetchone()
    return None if row is None else dict(row)


def title_holder(scope: LibraryScope, title: str, parent_id: Optional[str]) -> Optional[str]:
    """The Folder under ``parent_id`` (None: the roots) whose title has ``title``'s key."""
    row = scope.execute(_TITLE_HOLDER_SQL, {"title": title, "parent_id": parent_id or None}).fetchone()
    return None if row is None else str(row["id"])


def _descendants(scope: LibraryScope, folder_id: str) -> set[str]:
    return {str(row["id"]) for row in scope.execute(_DESCENDANTS_SQL, {"folder_id": folder_id}).fetchall()}


def _count(scope: LibraryScope, query: LiteralString, folder_id: str) -> int:
    row = scope.execute(query, {"folder_id": folder_id}).fetchone()
    return 0 if row is None else int(row["n"])


def member_work_ids(scope: LibraryScope, folder_id: str) -> list[str]:
    """``get_folder_work_ids``: the Works filed in the Folder, by id."""
    return [str(row["work_id"]) for row in scope.execute(_MEMBERS_SQL, {"folder_id": folder_id})]


def _validate(field: str, value: Any) -> str:
    text = rules.validate_field_value(field, value)
    if "\x00" in text:
        raise FolderRuleError("INVALID_ENVELOPE", "field values cannot contain NUL")
    return text


def _current_field(scope: LibraryScope, folder_id: str, field: str) -> Optional[str]:
    row = folder_row(scope, folder_id)
    if row is None:
        return None
    value = row[field]
    return "" if value is None else str(value)


def _assert_title_free(scope: LibraryScope, title: str, parent_id: Optional[str],
                       folder_id: Optional[str] = None) -> None:
    holder = title_holder(scope, title, parent_id)
    if holder is not None and holder != folder_id:
        raise FolderRuleError("TITLE_TAKEN", "A folder with this name already exists in this location.")


def rule_status(code: str) -> int:
    """The HTTP status of a ``FolderRuleError`` code in a terminal result (``_rule_status``)."""
    return 404 if code in ("ENTITY_NOT_FOUND", "PARENT_NOT_FOUND", "FOLDER_NOT_FOUND") else 409


# -- create ---------------------------------------------------------------------


def insert(scope: LibraryScope, folder_id: str, title: Any, description: Any = "", parent_id: Any = "",
           private_notes: Any = "") -> bool:
    """``insert_folder_on_conn``: construction, every field at revision 0.

    Returns False when the id is already a Folder of this Library (a
    concurrent create of it committed first). Raises ``FolderRuleError``
    (``INVALID_ENVELOPE``, ``PARENT_NOT_FOUND``, ``TITLE_TAKEN``) with
    nothing inserted, and :class:`ForeignFolderId` for an id of another
    Library. The parent is held against its delete until COMMIT.
    """
    clean_title = _validate("title", title)
    clean_parent = _validate("parent_id", parent_id)
    clean_description = _validate("description", description)
    clean_notes = _validate("private_notes", private_notes)
    if clean_parent and not share(scope, clean_parent):
        raise FolderRuleError("PARENT_NOT_FOUND", "Parent folder not found.")
    # A fast refusal; the unique index decides a race.
    _assert_title_free(scope, clean_title, clean_parent or None)
    row = scope.execute(_INSERT_SQL, {
        "folder_id": folder_id, "title": clean_title, "description": clean_description,
        "parent_id": clean_parent or None, "private_notes": clean_notes,
    }).fetchone()
    if row is not None:
        return True
    if exists(scope, folder_id):
        return False
    _assert_title_free(scope, clean_title, clean_parent or None)
    # Neither this Library's id nor its title: the id is another Library's.
    raise ForeignFolderId(folder_id)


def apply_create(scope: LibraryScope, folder_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """``CREATE_FOLDER``'s ``apply_create``: SQLite's results.

    A create of an id that already exists acknowledges the stored Folder
    (``changed: false``) with its current counts. Raises
    :class:`ForeignFolderId` for an id of another Library.
    """
    existing = share(scope, folder_id)
    if not existing:
        try:
            created = insert(scope, folder_id, payload["title"], payload["description"],
                             payload["parent_id"], payload["private_notes"])
        except FolderRuleError as error:
            return rule_status(error.code), {"code": error.code, "folder_id": folder_id}
        # A create of the same id committed while this one waited: convergence.
        existing = not created
    row = folder_row(scope, folder_id)
    if row is None:  # pragma: no cover - held or inserted above, in this transaction
        raise RuntimeError("the folder vanished under its lock")
    out = {key: row[key] for key in ("id", "title", "description", "parent_id", "private_notes")}
    out["work_count"] = _count(scope, _FILE_COUNT_SQL, folder_id) if existing else 0
    out["child_count"] = _count(scope, _CHILD_COUNT_SQL, folder_id)
    return 200, {"code": "ACKNOWLEDGED", "folder_id": folder_id, "changed": not existing, "folder": out}


def ensure_uncategorized(scope: LibraryScope, new_id: str) -> str:
    """``ensure_uncategorized_on_conn``: the root Folder that files a Work with no folder.

    SQLite looks for the exact title "Uncategorized" and fails to create one
    when a root Folder holds the title in another case; here that Folder is
    the one (one root Folder holds a title key). A concurrent create of it
    is waited for (or, SERIALIZABLE, retried) and then found.
    """
    found = scope.execute(_UNCATEGORIZED_SQL, {"title": UNCATEGORIZED_TITLE}).fetchone()
    if found is not None:
        return str(found["id"])
    if insert(scope, new_id, UNCATEGORIZED_TITLE):
        return new_id
    raise RuntimeError("a fresh folder id was taken")  # pragma: no cover - ids are random


# -- fields ---------------------------------------------------------------------


def set_field(scope: LibraryScope, folder_id: str, field: str, value: Any) -> tuple[bool, int]:
    """``set_field_on_conn``: write one field and advance its revision together.

    Returns ``(changed, revision_after)``; raises ``FolderRuleError`` for an
    invalid value, a missing Folder or parent, a cycle or a taken title. A
    title is unique within its parent, so a rename and a move are both
    judged against the Folder's other value. A title committed by a
    concurrent writer after the check fails on the index
    (``ConstraintViolationError`` on :data:`TITLE_KEY`); the caller decides
    once more.
    """
    desired = _validate(field, value)
    if field == "parent_id":
        lock_hierarchy(scope)
    if not lock(scope, folder_id, key=field in ("title", "parent_id")):
        raise FolderRuleError("ENTITY_NOT_FOUND", "Folder not found.")
    if field == "parent_id" and desired and desired != folder_id and not share(scope, desired):
        raise FolderRuleError("PARENT_NOT_FOUND", "Parent folder not found.")
    scope_id = rules.scope_key(folder_id, field)
    revision = revisions.read(scope, FIELD_SCOPE_TYPE, [scope_id])[scope_id]
    current = _current_field(scope, folder_id, field)
    if current == desired:
        return False, revision
    if field == "parent_id":
        _assert_parent_usable(scope, folder_id, desired)
    if field in ("title", "parent_id"):
        title = desired if field == "title" else _current_field(scope, folder_id, "title")
        parent = desired if field == "parent_id" else _current_field(scope, folder_id, "parent_id")
        _assert_title_free(scope, rules.canonical_wire("title", title), parent or None, folder_id)
    revisions.lock(scope, FIELD_SCOPE_TYPE, scope_id)
    stored = (desired or None) if field == "parent_id" else desired
    scope.execute(_SET_SQL[field], {"folder_id": folder_id, "value": stored})
    return True, revisions.advance(scope, FIELD_SCOPE_TYPE, scope_id)


def _assert_parent_usable(scope: LibraryScope, folder_id: str, parent_id: str) -> None:
    """``assert_parent_usable``, under the hierarchy lock the caller holds."""
    if not parent_id:
        return
    if parent_id == folder_id:
        raise FolderRuleError("PARENT_CYCLE", "A folder cannot be its own parent.")
    if not exists(scope, parent_id):
        raise FolderRuleError("PARENT_NOT_FOUND", "Parent folder not found.")
    if parent_id in _descendants(scope, folder_id):
        raise FolderRuleError("PARENT_CYCLE", "Cannot set parent to a subfolder (cycle).")


def apply_field_operation(scope: LibraryScope, folder_id: str, field: str, value: str,
                          base_revision: int) -> tuple[int, dict[str, Any]]:
    """``SET_FOLDER_FIELD``'s ``apply_field``: SQLite's decision table, result for result."""
    desired = rules.canonical_wire(field, value)
    result: dict[str, Any] = {"folder_id": folder_id, "field": field}
    if field == "parent_id":
        lock_hierarchy(scope)
    if not lock(scope, folder_id, key=field in ("title", "parent_id")):
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    # Read, not ensured: the Folder lock serializes every writer of its
    # fields, and a refused or converged operation must not create a scope.
    scope_id = rules.scope_key(folder_id, field)
    revision = revisions.read(scope, FIELD_SCOPE_TYPE, [scope_id])[scope_id]
    current = _current_field(scope, folder_id, field) or ""
    if base_revision > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 400, meta.fit_terminal_result(result, current, desired)
    if base_revision < revision and current != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 409, meta.fit_terminal_result(result, current, desired)
    try:
        changed, after = set_field(scope, folder_id, field, desired)
    except FolderRuleError as error:
        result.update(code=error.code, current_revision=revision)
        return rule_status(error.code), result
    # Private notes are not echoed into the ledger (folder_sync).
    result.update(code="ACKNOWLEDGED", server_revision=after, changed=changed, value_omitted=True)
    if field == "title" and changed:
        # A cached Work detail embeds `folder_title`: the answer names the
        # members a rename stales.
        result["member_work_ids"] = member_work_ids(scope, folder_id)
    return 200, result


# -- delete ---------------------------------------------------------------------


def delete(scope: LibraryScope, folder_id: str) -> tuple[bool, Optional[str]]:
    """``delete_folder_on_conn``: remove an EMPTY Folder; ``(deleted, refusal_code)``.

    The Folder is locked ``FOR UPDATE`` first, so a filing into it, a child
    created or moved under it and a Tag link of it wait for this command
    (each holds the Folder ``FOR KEY SHARE`` or ``FOR NO KEY UPDATE``), and
    the counts read after the lock are the committed ones. Its Tag links are
    removed and every scope of the Folder advances once (see the module
    docstring).
    """
    if not lock(scope, folder_id, key=True):
        return False, None
    if _count(scope, _FILE_COUNT_SQL, folder_id):
        return False, "FOLDER_NOT_EMPTY"
    if _count(scope, _CHILD_COUNT_SQL, folder_id):
        return False, "FOLDER_HAS_SUBFOLDERS"
    tag_ids = [str(row["tag_id"]) for row in scope.execute(_FOLDER_TAG_IDS_SQL, {"folder_id": folder_id})]
    owned = [(FIELD_SCOPE_TYPE, rules.scope_key(folder_id, field)) for field in rules.FIELDS]
    owned += [(links.FOLDER.scope_type, links.FOLDER.scope_id(folder_id, tag_id)) for tag_id in tag_ids]
    # A scope no edit touched has no row and reads as revision 0, the base a
    # recreated Folder starts from: ensured here, it advances with the rest.
    revisions.lock_many(scope, owned)
    scope.execute(_DELETE_FOLDER_TAGS_SQL, {"folder_id": folder_id})
    prefix = '["' + folder_id + '",'
    scope.execute(_TOMBSTONE_SQL, {"scope_types": [FIELD_SCOPE_TYPE, links.FOLDER.scope_type],
                                   "prefix": prefix, "prefix_len": len(prefix)})
    scope.execute(_DELETE_SQL, {"folder_id": folder_id})
    return True, None


# -- which Folder a Work is in --------------------------------------------------


def work_folder(scope: LibraryScope, work_id: str) -> str:
    """``current_work_folder``: the Work's Folder id, ``""`` for none."""
    row = scope.execute(_WORK_FOLDER_SQL, {"work_id": work_id}).fetchone()
    return "" if row is None else str(row["folder_id"])


def lock_work(scope: LibraryScope, work_id: str) -> bool:
    """Step 1: hold the Work whose Folder changes; False when it is not this Library's."""
    return links.lock_owner(scope, links.WORK, work_id)


def set_work_folder(scope: LibraryScope, work_id: str, folder_id: Optional[str]) -> bool:
    """``set_work_folder_on_conn``: file, move or clear (``""``); True when it changed.

    The caller holds the Work (:func:`lock_work`). The target Folder is held
    ``FOR KEY SHARE``, so a Folder delete cannot pass between the check and
    the filing. Run it SERIALIZABLE (§7.1: ``folder_files`` is a peer table
    of the Work merge).
    """
    desired = ("" if folder_id is None else str(folder_id)).strip()
    current = work_folder(scope, work_id)
    if current == desired:
        return False
    if desired and not share(scope, desired):
        raise FolderRuleError("FOLDER_NOT_FOUND", "Folder not found.")
    revisions.lock(scope, WORK_SCOPE_TYPE, work_id)
    scope.execute(_UNFILE_SQL, {"work_id": work_id})
    if desired:
        scope.execute(_FILE_SQL, {"work_id": work_id, "folder_id": desired})
    revisions.advance(scope, WORK_SCOPE_TYPE, work_id)
    # A cached Work detail embeds `folder_title`, and both Folders' counts move.
    scope.execute(_TOUCH_WORK_SQL, {"work_id": work_id})
    return True


def apply_work_folder_operation(scope: LibraryScope, work_id: str, desired: str,
                                base_revision: int) -> tuple[int, dict[str, Any]]:
    """``SET_WORK_FOLDER``'s ``apply_work_folder``: SQLite's decision table, result for result."""
    result: dict[str, Any] = {"work_id": work_id}
    if not lock_work(scope, work_id):
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    # Read, not ensured: the Work lock serializes every writer of the scope.
    revision = revisions.read(scope, WORK_SCOPE_TYPE, [work_id])[work_id]
    current = work_folder(scope, work_id)
    if base_revision > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 400, result
    # Two devices that filed the Work in the same Folder have converged.
    if base_revision < revision and current != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 409, result
    try:
        changed = set_work_folder(scope, work_id, desired)
    except FolderRuleError as error:
        result.update(code=error.code, current_revision=revision)
        return rule_status(error.code), result
    result.update(code="ACKNOWLEDGED", changed=changed, folder_id=desired,
                  server_revision=revision + int(changed))
    title = _current_field(scope, desired, "title") if desired else None
    result["folder_title"] = title or ""
    return 200, result


def file_new_work(scope: LibraryScope, work_id: str, folder_id: str) -> None:
    """File a Work this transaction created: construction, no ``work-folder`` revision."""
    scope.execute(_FILE_NEW_SQL, {"work_id": work_id, "folder_id": folder_id})


def release_work(scope: LibraryScope, work_id: str) -> None:
    """Take a Work being deleted out of its Folder (the Work delete's Organization part).

    The caller holds the Work ``FOR UPDATE``, which every filing writer
    waits for, and tombstones ``work-folder`` with the Work's other scopes
    (``commands.work_scopes``). A Work in a Playlist is not deleted yet:
    the Playlist slice removes its item and advances ``playlist-order`` and
    ``work-playlist``, and until then the delete refuses before anything is
    written (the item's key would refuse it too).
    """
    if scope.execute(_IN_PLAYLIST_SQL, {"work_id": work_id}).fetchone() is not None:
        raise NotPortedError("delete_work_record of a Work in a playlist")
    scope.execute(_UNFILE_SQL, {"work_id": work_id})


# -- reads ----------------------------------------------------------------------


def catalog(scope: LibraryScope) -> list[dict[str, Any]]:
    """``get_all_folders``."""
    rows = scope.execute(_CATALOG_SQL, {"uncategorized": UNCATEGORIZED_TITLE}).fetchall()
    return [_counted(row) for row in rows]


def _counted(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["work_count"] = int(out["work_count"])
    out["child_count"] = int(out["child_count"])
    return out


def children(scope: LibraryScope, folder_id: str) -> list[dict[str, Any]]:
    return [_counted(row) for row in scope.execute(_CHILDREN_SQL, {"folder_id": folder_id}).fetchall()]


def parent(scope: LibraryScope, folder_id: str) -> Optional[dict[str, Any]]:
    row = scope.execute(_PARENT_SQL, {"folder_id": folder_id}).fetchone()
    return None if row is None else dict(row)


def related(scope: LibraryScope, work_id: str) -> list[dict[str, Any]]:
    """``get_related_folders_for_work``: Folders holding a Work that shares a Person with this one."""
    return [dict(row) for row in scope.execute(_RELATED_SQL, {"work_id": work_id}).fetchall()]


def folder_state(scope: LibraryScope, folder_id: str) -> Optional[dict[str, Any]]:
    """``get_folder_state_on_conn``: field revisions only."""
    if not exists(scope, folder_id):
        return None
    fields = sorted(rules.FIELDS)
    by_scope = revisions.read(scope, FIELD_SCOPE_TYPE, [rules.scope_key(folder_id, f) for f in fields])
    return {"folder_id": folder_id,
            "fields": {f: {"revision": by_scope[rules.scope_key(folder_id, f)]} for f in fields}}


def work_folder_state(scope: LibraryScope, work_id: str) -> Optional[dict[str, Any]]:
    """``get_work_folder_state_on_conn``: which Folder a Work is in, and that scope's revision."""
    if scope.execute(_WORK_EXISTS_SQL, {"work_id": work_id}).fetchone() is None:
        return None
    return {"work_id": work_id, "folder_id": work_folder(scope, work_id),
            "revision": revisions.read(scope, WORK_SCOPE_TYPE, [work_id])[work_id]}
