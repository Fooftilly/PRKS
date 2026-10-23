"""The Work lifecycle on PostgreSQL: merged ids and ``WORK_MERGED`` (L-W-2 #631).

``prks.sync_work_lifecycle`` outlives the Work row (``docs/work-identity-model.md``
§10.4, §14.2). A live Work has no row: the Work row itself says it is
active. A row in state ``merged`` names the Work it was merged into, and is
written by the Work merge in the transaction that deletes the source
(:func:`mark_merged`). No command writes ``deleted`` yet: a deleted Work is
simply absent, as on SQLite.

The rule this module carries: **an operation naming a merged-away Work is
refused with ``WORK_MERGED`` and the target, never applied to the target**.
The source's revisions say nothing about the target's, so a redirect would
either accept a stale write or report a false conflict. Reads may follow the
redirect; that belongs to the merge command that exposes merges, not here.

:func:`resolve` follows a chain of merges to its end, as the Tag lifecycle
does. :func:`mark_merged` flattens the Works merged into the source earlier,
so a chain is one step long; :func:`resolve` does not rely on that, and a
cycle is a server failure, never a ledgered answer.

:func:`guard` is what an operation runs before it applies: it takes ``FOR KEY
SHARE`` on the Work row, so a merge (which deletes the row) either waits for
the operation to commit or has committed before it, in which case the row is
gone and the lifecycle row the merge wrote is read instead. An operation
whose handler locks the Work ``FOR UPDATE`` (:data:`LOCKS_FOR_UPDATE`) takes
that lock here instead: two of them holding ``FOR KEY SHARE`` and each
upgrading would deadlock.
"""

from __future__ import annotations

from typing import Any, LiteralString, Optional

from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope

ACTIVE = "ACTIVE"
MERGED = "MERGED"
UNKNOWN = "UNKNOWN"

MERGED_CODE = "WORK_MERGED"
MERGED_STATUS = 409

_LOCK_LIVE: LiteralString = (
    "SELECT id FROM prks.works WHERE library_id = %(library_id)s AND id = %(work_id)s FOR KEY SHARE"
)
_LOCK_LIVE_FOR_UPDATE: LiteralString = (
    "SELECT id FROM prks.works WHERE library_id = %(library_id)s AND id = %(work_id)s FOR UPDATE"
)
# The operations whose handler locks the Work FOR UPDATE (commands.delete_work).
LOCKS_FOR_UPDATE = frozenset({"DELETE_WORK"})
_LIVE: LiteralString = "SELECT id FROM prks.works WHERE library_id = %(library_id)s AND id = %(work_id)s"
# Unscoped on purpose: Work ids are unique across Libraries, and so is this
# table's key. Only existence is read, and it is answered FORBIDDEN, as a
# create of another Library's live Work is (work_lifecycle.apply_create).
_RECORDED_ANYWHERE: LiteralString = "SELECT 1 FROM prks.sync_work_lifecycle WHERE work_id = %s"
# One statement, so the chain is one snapshot.
_RESOLVE: LiteralString = """
WITH RECURSIVE chain (work_id, state, target_work_id, depth) AS (
  SELECT work_id, state, target_work_id, 0 FROM prks.sync_work_lifecycle
  WHERE library_id = %(library_id)s AND work_id = %(work_id)s
  UNION ALL
  SELECT l.work_id, l.state, l.target_work_id, c.depth + 1
  FROM chain c
  JOIN prks.sync_work_lifecycle l ON l.library_id = %(library_id)s AND l.work_id = c.target_work_id
  WHERE c.state = 'merged'
) CYCLE work_id SET looped USING path
SELECT work_id, state, target_work_id, depth, looped FROM chain
ORDER BY looped DESC, depth DESC
LIMIT 1
"""
# The source, and every Work merged into it earlier.
_MARK_MERGED: LiteralString = """
INSERT INTO prks.sync_work_lifecycle (library_id, work_id, state, target_work_id)
VALUES (%(library_id)s, %(source)s, 'merged', %(target)s)
"""
_FLATTEN: LiteralString = """
UPDATE prks.sync_work_lifecycle SET target_work_id = %(target)s, changed_at = now()
WHERE library_id = %(library_id)s AND state = 'merged' AND target_work_id = %(source)s
"""


def resolve(scope: LibraryScope, work_id: str) -> dict[str, Any]:
    """``{"state": …}``, with ``target_work_id`` when ``MERGED``.

    ``ACTIVE`` is a live Work of this Library; ``MERGED`` an id merged away,
    with the Work at the end of its chain (whether or not that Work still
    exists: the id stays merged); ``UNKNOWN`` anything else, including an
    id of another Library, which is never disclosed.
    """
    if scope.execute(_LIVE, {"work_id": work_id}).fetchone() is not None:
        return {"state": ACTIVE}
    return _lifecycle(scope, work_id)


def _lifecycle(scope: LibraryScope, work_id: str) -> dict[str, Any]:
    row = scope.execute(_RESOLVE, {"work_id": work_id}).fetchone()
    if row is None:
        return {"state": UNKNOWN}
    if row["looped"]:
        raise RuntimeError("Work lifecycle cycle")
    if row["state"] == "merged":
        return {"state": MERGED, "target_work_id": str(row["target_work_id"])}
    if row["depth"] == 0:  # the id's own row records no merge
        return {"state": UNKNOWN}
    # The chain ends at a Work whose own row records no merge.
    return {"state": MERGED, "target_work_id": str(row["work_id"])}


def refusal(work_id: str, target_work_id: str) -> tuple[int, dict[str, Any]]:
    """The terminal result of an operation that names a merged-away Work."""
    return MERGED_STATUS, {"code": MERGED_CODE, "work_id": work_id, "target_work_id": target_work_id}


def guard(scope: LibraryScope, work_id: str,
          operation: Optional[str] = None) -> Optional[tuple[int, dict[str, Any]]]:
    """The ``WORK_MERGED`` refusal for ``operation`` naming ``work_id``, or None to go on.

    A live Work is locked ``FOR KEY SHARE`` (``FOR UPDATE`` for
    :data:`LOCKS_FOR_UPDATE`) for the rest of the transaction, so it cannot
    be merged away while the operation applies. An id that is
    neither live nor merged goes on: the operation's own rule answers it
    (``CREATE_WORK`` creates it, the others report it missing), except a
    ``CREATE_WORK`` of an id another Library merged away, which is refused
    (``AccessRefused(FORBIDDEN)``, not ledgered): the id is never reused.
    """
    lock = _LOCK_LIVE_FOR_UPDATE if operation in LOCKS_FOR_UPDATE else _LOCK_LIVE
    if scope.execute(lock, {"work_id": work_id}).fetchone() is not None:
        return None
    state = _lifecycle(scope, work_id)
    if state["state"] == MERGED:
        return refusal(work_id, state["target_work_id"])
    if operation == "CREATE_WORK" and scope.execute_unscoped(_RECORDED_ANYWHERE, (work_id,)).fetchone():
        raise AccessRefused(Refusal.FORBIDDEN)
    return None


def mark_merged(scope: LibraryScope, source: str, target: str) -> None:
    """Record that ``source`` was merged into ``target``, in the merge's transaction.

    Works merged into ``source`` earlier now name ``target``. The insert
    fails (``sync_work_lifecycle_pkey``) if ``source`` already has a row: a
    Work is merged away once.
    """
    if source == target:
        raise ValueError("a Work is not merged into itself")
    params = {"source": source, "target": target}
    scope.execute(_FLATTEN, params)
    scope.execute(_MARK_MERGED, params)
