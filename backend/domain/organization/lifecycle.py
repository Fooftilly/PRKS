"""The Tag lifecycle on PostgreSQL (L-O-1 #633).

``sync_tag_lifecycle`` outlives the Tag: every later answer about a Tag id
(active, merged into another Tag, deleted, or never known) is read from it,
with SQLite's resolution (``work_tag_sync.resolve_lifecycle``): a merged id
follows its targets to the active Tag at the end, a deleted one anywhere on
the way is ``DELETED``, an id without a row, or whose active row has no Tag,
is ``UNKNOWN``, and a cycle is a server failure, never a ledgered answer.
A merge flattens the chains that end at its source, so a chain is one step
long; the walk does not rely on that.

A row changes only under the lock of its Tag (merge and delete hold it),
except a flattened row, whose Tag is already gone; a reader that holds a
Tag therefore reads a lifecycle that cannot change under it.
"""

from __future__ import annotations

from typing import Any, LiteralString

from backend.domain.identity.library_scope import LibraryScope

ACTIVE = "ACTIVE"
MERGED = "MERGED"
DELETED = "DELETED"
UNKNOWN = "UNKNOWN"

# One statement, so the chain and the Tag at its end are one snapshot.
_RESOLVE_SQL: LiteralString = """
WITH RECURSIVE chain (tag_id, state, target_tag_id, depth) AS (
  SELECT tag_id, state, target_tag_id, 0 FROM prks.sync_tag_lifecycle
  WHERE library_id = %(library_id)s AND tag_id = %(tag_id)s
  UNION ALL
  SELECT l.tag_id, l.state, l.target_tag_id, c.depth + 1
  FROM chain c
  JOIN prks.sync_tag_lifecycle l ON l.library_id = %(library_id)s AND l.tag_id = c.target_tag_id
  WHERE c.state = 'merged'
) CYCLE tag_id SET looped USING path
SELECT c.tag_id, c.state, c.looped,
       EXISTS (SELECT 1 FROM prks.tags t WHERE t.library_id = %(library_id)s AND t.id = c.tag_id) AS has_tag
FROM chain c
ORDER BY c.looped DESC, c.depth DESC
LIMIT 1
"""
_INSERT_SQL: LiteralString = """
INSERT INTO prks.sync_tag_lifecycle (library_id, tag_id, state)
VALUES (%(library_id)s, %(tag_id)s, 'active')
ON CONFLICT (tag_id) DO NOTHING
RETURNING tag_id
"""
_DELETED_SQL: LiteralString = """
UPDATE prks.sync_tag_lifecycle SET state = 'deleted', target_tag_id = NULL, changed_at = now()
WHERE library_id = %(library_id)s AND tag_id = %(tag_id)s
"""
# The source, and every Tag merged into it earlier: SQLite's flattening.
_MERGED_SQL: LiteralString = """
UPDATE prks.sync_tag_lifecycle SET state = 'merged', target_tag_id = %(target)s, changed_at = now()
WHERE library_id = %(library_id)s
  AND (tag_id = %(source)s OR (state = 'merged' AND target_tag_id = %(source)s))
"""
_DELETE_ROW_SQL: LiteralString = """
DELETE FROM prks.sync_tag_lifecycle WHERE library_id = %(library_id)s AND tag_id = %(tag_id)s
"""


def resolve(scope: LibraryScope, tag_id: str) -> dict[str, Any]:
    """``{"state": …}``, with ``target_tag_id`` when ``MERGED``; SQLite's answer."""
    row = scope.execute(_RESOLVE_SQL, {"tag_id": tag_id}).fetchone()
    if row is None:
        return {"state": UNKNOWN}
    if row["looped"]:
        raise RuntimeError("Tag lifecycle cycle")
    if row["state"] == "deleted":
        return {"state": DELETED}
    if row["state"] == "merged":  # the chain names a Tag with no lifecycle row
        return {"state": UNKNOWN}
    if not row["has_tag"]:
        return {"state": UNKNOWN}
    end = str(row["tag_id"])
    return {"state": ACTIVE} if end == tag_id else {"state": MERGED, "target_tag_id": end}


def insert_active(scope: LibraryScope, tag_id: str) -> bool:
    """The row a new Tag commits with; False when the id already has one (any Library's)."""
    return scope.execute(_INSERT_SQL, {"tag_id": tag_id}).fetchone() is not None


def drop(scope: LibraryScope, tag_id: str) -> None:
    """Undo :func:`insert_active` of a Tag this transaction gives up creating."""
    scope.execute(_DELETE_ROW_SQL, {"tag_id": tag_id})


def mark_deleted(scope: LibraryScope, tag_id: str) -> None:
    scope.execute(_DELETED_SQL, {"tag_id": tag_id})


def mark_merged(scope: LibraryScope, source: str, target: str) -> None:
    scope.execute(_MERGED_SQL, {"source": source, "target": target})
