"""Work-Tag and Folder-Tag relationships on PostgreSQL (L-O-1 #633).

The rules are SQLite's (``backend/work_tag_sync.py``,
``backend/folder_tag_sync.py``), imported rather than copied: one revision
scope per relationship (``work-tag / ["W-…","T-…"]``, ``folder-tag /
["F-…","T-…"]``), only a change advances it, the scope outlives the link,
and the decision table compares an operation's base with the current
revision (a stale base conflicts only when the states differ).

:func:`set_state` is the one writer of a link, as ``set_state`` is on
SQLite: the relationship operations, the facade's direct edits, and the Tag
delete and merge all change a link and advance its scope together. Every
caller holds the owner and the Tag first (the package lock order), so the
link cannot change between the read and the write, and a write that changes
nothing leaves no revision row behind.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, LiteralString, Optional

from backend import folder_tag_sync, work_tag_sync
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.organization import lifecycle
from backend.domain.works import revisions


@dataclass(frozen=True)
class Link:
    """One relationship family: a Work's tags or a Folder's tags."""

    scope_type: str
    owner_key: str  # the owner id's key in results ("work_id", "folder_id")
    scope_key: Callable[[str, str], str]  # SQLite's scope id of (owner, tag)
    owner_exists_sql: LiteralString
    lock_owner_sql: LiteralString
    share_owners_sql: LiteralString
    exists_sql: LiteralString
    add_sql: LiteralString
    remove_sql: LiteralString
    owners_of_tag_sql: LiteralString
    options_sql: LiteralString
    tags_sql: LiteralString

    def scope_id(self, owner_id: str, tag_id: str) -> str:
        return self.scope_key(owner_id, tag_id)


# The scope ids are SQLite's ``json.dumps([owner, tag])`` text; ids hold no
# quote or backslash, so concatenation builds the same text in SQL.
WORK = Link(
    scope_type="work-tag",
    owner_key="work_id",
    scope_key=work_tag_sync.scope_key,
    owner_exists_sql="""
SELECT 1 FROM prks.works WHERE library_id = %(library_id)s AND id = %(owner_id)s
""",
    lock_owner_sql="""
SELECT id FROM prks.works WHERE library_id = %(library_id)s AND id = %(owner_id)s FOR NO KEY UPDATE
""",
    share_owners_sql="""
SELECT id FROM prks.works WHERE library_id = %(library_id)s AND id = ANY(%(owner_ids)s)
ORDER BY id COLLATE "C" FOR KEY SHARE
""",
    exists_sql="""
SELECT 1 FROM prks.work_tags
WHERE library_id = %(library_id)s AND work_id = %(owner_id)s AND tag_id = %(tag_id)s
""",
    add_sql="""
INSERT INTO prks.work_tags (library_id, work_id, tag_id) VALUES (%(library_id)s, %(owner_id)s, %(tag_id)s)
""",
    remove_sql="""
DELETE FROM prks.work_tags
WHERE library_id = %(library_id)s AND work_id = %(owner_id)s AND tag_id = %(tag_id)s
""",
    owners_of_tag_sql="""
SELECT work_id AS owner_id FROM prks.work_tags WHERE library_id = %(library_id)s AND tag_id = %(tag_id)s
ORDER BY work_id COLLATE "C"
""",
    options_sql="""
SELECT t.id AS tag_id, (wt.tag_id IS NOT NULL) AS present, COALESCE(r.revision, 0) AS revision
FROM prks.tags t
JOIN prks.sync_tag_lifecycle l ON l.library_id = t.library_id AND l.tag_id = t.id AND l.state = 'active'
LEFT JOIN prks.work_tags wt
  ON wt.library_id = t.library_id AND wt.work_id = %(owner_id)s AND wt.tag_id = t.id
LEFT JOIN prks.entity_revisions r
  ON r.library_id = t.library_id AND r.scope_type = 'work-tag'
 AND r.scope_id = '["' || %(owner_id)s || '","' || t.id || '"]'
WHERE t.library_id = %(library_id)s
ORDER BY t.id COLLATE "C"
""",
    tags_sql="""
SELECT t.id, t.name, t.color,
       to_char(t.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at
FROM prks.tags t
JOIN prks.work_tags wt ON wt.library_id = t.library_id AND wt.tag_id = t.id
WHERE t.library_id = %(library_id)s AND wt.work_id = %(owner_id)s
ORDER BY t.id COLLATE "C"
""",
)

FOLDER = Link(
    scope_type="folder-tag",
    owner_key="folder_id",
    scope_key=folder_tag_sync.scope_key,
    owner_exists_sql="""
SELECT 1 FROM prks.folders WHERE library_id = %(library_id)s AND id = %(owner_id)s
""",
    lock_owner_sql="""
SELECT id FROM prks.folders WHERE library_id = %(library_id)s AND id = %(owner_id)s FOR NO KEY UPDATE
""",
    share_owners_sql="""
SELECT id FROM prks.folders WHERE library_id = %(library_id)s AND id = ANY(%(owner_ids)s)
ORDER BY id COLLATE "C" FOR KEY SHARE
""",
    exists_sql="""
SELECT 1 FROM prks.folder_tags
WHERE library_id = %(library_id)s AND folder_id = %(owner_id)s AND tag_id = %(tag_id)s
""",
    add_sql="""
INSERT INTO prks.folder_tags (library_id, folder_id, tag_id)
VALUES (%(library_id)s, %(owner_id)s, %(tag_id)s)
""",
    remove_sql="""
DELETE FROM prks.folder_tags
WHERE library_id = %(library_id)s AND folder_id = %(owner_id)s AND tag_id = %(tag_id)s
""",
    owners_of_tag_sql="""
SELECT folder_id AS owner_id FROM prks.folder_tags WHERE library_id = %(library_id)s AND tag_id = %(tag_id)s
ORDER BY folder_id COLLATE "C"
""",
    options_sql="""
SELECT t.id AS tag_id, (ft.tag_id IS NOT NULL) AS present, COALESCE(r.revision, 0) AS revision
FROM prks.tags t
JOIN prks.sync_tag_lifecycle l ON l.library_id = t.library_id AND l.tag_id = t.id AND l.state = 'active'
LEFT JOIN prks.folder_tags ft
  ON ft.library_id = t.library_id AND ft.folder_id = %(owner_id)s AND ft.tag_id = t.id
LEFT JOIN prks.entity_revisions r
  ON r.library_id = t.library_id AND r.scope_type = 'folder-tag'
 AND r.scope_id = '["' || %(owner_id)s || '","' || t.id || '"]'
WHERE t.library_id = %(library_id)s
ORDER BY t.id COLLATE "C"
""",
    tags_sql="""
SELECT t.id, t.name, t.color,
       to_char(t.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at
FROM prks.tags t
JOIN prks.folder_tags ft ON ft.library_id = t.library_id AND ft.tag_id = t.id
WHERE t.library_id = %(library_id)s AND ft.folder_id = %(owner_id)s
ORDER BY t.id COLLATE "C"
""",
)

LINKS = (WORK, FOLDER)

_SHARE_TAG_SQL: LiteralString = """
SELECT id FROM prks.tags WHERE library_id = %(library_id)s AND id = %(tag_id)s FOR SHARE
"""
_TAG_ROW_SQL: LiteralString = """
SELECT id, name, color FROM prks.tags WHERE library_id = %(library_id)s AND id = %(tag_id)s
"""
# Every scope of this family naming the Tag, linked or not: a device's cached
# tag options for those owners list the Tag (``tag_sync``'s affected lists).
_SCOPED_OWNERS_SQL: LiteralString = """
SELECT (scope_id::jsonb) ->> 0 AS owner_id FROM prks.entity_revisions
WHERE library_id = %(library_id)s AND scope_type = %(scope_type)s
  AND right(scope_id, %(suffix_len)s) = %(suffix)s
"""


def lock_owner(scope: LibraryScope, link: Link, owner_id: str) -> bool:
    """Step 1 of the lock order: hold the Work or Folder; False when it is not this Library's."""
    return scope.execute(link.lock_owner_sql, {"owner_id": owner_id}).fetchone() is not None


def share_owners(scope: LibraryScope, link: Link, owner_ids: list[str]) -> set[str]:
    """Step 3 of the lock order: hold the owners whose links a Tag delete or merge moves."""
    if not owner_ids:
        return set()
    rows = scope.execute(link.share_owners_sql, {"owner_ids": sorted(set(owner_ids))}).fetchall()
    return {str(row["id"]) for row in rows}


def share_tag(scope: LibraryScope, tag_id: str) -> bool:
    """Step 2 for a relationship command: hold the Tag against a delete or a merge."""
    return scope.execute(_SHARE_TAG_SQL, {"tag_id": tag_id}).fetchone() is not None


def tag_row(scope: LibraryScope, tag_id: str) -> Optional[dict[str, Any]]:
    row = scope.execute(_TAG_ROW_SQL, {"tag_id": tag_id}).fetchone()
    return None if row is None else dict(row)


def is_linked(scope: LibraryScope, link: Link, owner_id: str, tag_id: str) -> bool:
    return scope.execute(link.exists_sql, {"owner_id": owner_id, "tag_id": tag_id}).fetchone() is not None


def owners_of(scope: LibraryScope, link: Link, tag_id: str) -> list[str]:
    """The owners linked to the Tag now, by id."""
    return [str(row["owner_id"]) for row in scope.execute(link.owners_of_tag_sql, {"tag_id": tag_id})]


def scoped_owners(scope: LibraryScope, link: Link, tag_id: str) -> set[str]:
    """The owners with a revision scope naming the Tag."""
    suffix = ',"' + tag_id + '"]'
    rows = scope.execute(_SCOPED_OWNERS_SQL, {"scope_type": link.scope_type, "suffix": suffix,
                                              "suffix_len": len(suffix)}).fetchall()
    return {str(row["owner_id"]) for row in rows}


def set_state(scope: LibraryScope, link: Link, owner_id: str, tag_id: str, present: bool) -> bool:
    """``set_state``: make the link ``present`` or absent; True when it changed.

    The caller holds the owner and the Tag (the package lock order) and has
    checked both exist when adding. A change locks the scope (already held by
    a caller that locked several in order), writes, and advances it.
    """
    if is_linked(scope, link, owner_id, tag_id) == present:
        return False
    scope_id = link.scope_id(owner_id, tag_id)
    revisions.lock(scope, link.scope_type, scope_id)
    params = {"owner_id": owner_id, "tag_id": tag_id}
    scope.execute(link.add_sql if present else link.remove_sql, params)
    revisions.advance(scope, link.scope_type, scope_id)
    return True


def revision(scope: LibraryScope, link: Link, owner_id: str, tag_id: str) -> int:
    scope_id = link.scope_id(owner_id, tag_id)
    return revisions.read(scope, link.scope_type, [scope_id])[scope_id]


def edit(scope: LibraryScope, link: Link, owner_id: str, tag_id: str, present: bool) -> None:
    """The facade's direct add or remove (``add_tag_to_work`` and friends).

    SQLite's foreign keys refuse a link to a missing owner or Tag; here that
    is ``ValueError``. Removing a link that is not there changes nothing.
    """
    if not lock_owner(scope, link, owner_id):
        if present:
            raise ValueError(link.owner_key.removesuffix("_id") + " not found")
        return
    if not share_tag(scope, tag_id):
        if present:
            raise ValueError("tag not found")
        return
    set_state(scope, link, owner_id, tag_id, present)


def apply_operation(scope: LibraryScope, link: Link, owner_id: str, tag_id: str, desired: bool,
                    base_revision: int) -> tuple[int, dict[str, Any]]:
    """``ADD/REMOVE_WORK_TAG`` and ``ADD/REMOVE_FOLDER_TAG``: SQLite's decision table, result for result."""
    result: dict[str, Any] = {link.owner_key: owner_id, "tag_id": tag_id}
    if not lock_owner(scope, link, owner_id):
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    share_tag(scope, tag_id)
    state = lifecycle.resolve(scope, tag_id)
    if state["state"] == lifecycle.UNKNOWN:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    if state["state"] == lifecycle.DELETED:
        result["code"] = "TAG_DELETED"
        return 409, result
    if state["state"] == lifecycle.MERGED:
        result.update(code="TAG_MERGED", target_tag_id=state["target_tag_id"])
        return 409, result
    # Read, not ensured: the owner and Tag locks serialize every writer of
    # this link, and a refused or converged operation must not create a scope.
    current = revision(scope, link, owner_id, tag_id)
    present = is_linked(scope, link, owner_id, tag_id)
    if base_revision > current:
        result.update(code="FUTURE_REVISION", current_revision=current, current_state=present,
                      requested_state=desired)
        return 400, result
    if base_revision < current and present != desired:
        result.update(code="REVISION_CONFLICT", current_revision=current, current_state=present,
                      requested_state=desired)
        return 409, result
    changed = set_state(scope, link, owner_id, tag_id, desired)
    result.update(code="ACKNOWLEDGED", present=desired, server_revision=current + int(changed),
                  changed=changed, tag=tag_row(scope, tag_id))
    return 200, result


def options(scope: LibraryScope, link: Link, owner_id: str) -> Optional[dict[str, Any]]:
    """``tag_options``: the owner's assigned active Tags and the absent ones with a revision."""
    if scope.execute(link.owner_exists_sql, {"owner_id": owner_id}).fetchone() is None:
        return None
    assigned: list[dict[str, Any]] = []
    absent: dict[str, int] = {}
    for row in scope.execute(link.options_sql, {"owner_id": owner_id}).fetchall():
        if row["present"]:
            assigned.append({"tag_id": row["tag_id"], "relation_revision": int(row["revision"])})
        elif row["revision"]:
            absent[str(row["tag_id"])] = int(row["revision"])
    return {link.owner_key: owner_id, "assigned": assigned, "known_absent": absent}


def tags_of(scope: LibraryScope, link: Link, owner_id: str) -> list[dict[str, Any]]:
    """``get_work_tags`` / ``get_folder_tags``: the owner's Tag rows."""
    return [dict(row) for row in scope.execute(link.tags_sql, {"owner_id": owner_id}).fetchall()]
