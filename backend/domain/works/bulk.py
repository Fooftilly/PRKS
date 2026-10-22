"""Bulk Work actions on PostgreSQL (L-W-2 #631; ``docs/postgresql-architecture.md`` §7.1).

``POST /api/works/bulk`` changes many Works in one transaction: their Status
(``set_status``), their Folder (``move_folder``), or their Tags (``add_tags``,
``remove_tags``). The request is validated by SQLite's
``parse_bulk_work_request`` before this module runs; here every selected
Work, and the Folder or the Tags it names, must exist in the Library before
anything is written, and then each Work goes through the same revision-aware
writer its single-Work command uses:

- Status: ``metadata.set_synced_field`` (``SET_WORK_METADATA_FIELD``'s);
- Folder: ``folders.set_work_folder`` (``SET_WORK_FOLDER``'s);
- Tags: ``links.set_state`` (``ADD/REMOVE_WORK_TAG``'s).

So only a Work whose state changes advances its scope (and its
``updated_at``), and adding a Tag a Work has (or removing one it lacks)
changes nothing, exactly as the single-Work command would. One failure
aborts the transaction, and with it every change made to an earlier Work.

**Isolation.** A bulk action spans many aggregates, so it runs SERIALIZABLE
with the database's bounded retry (§7.1, PG12). It also adds rows to
``work_tags`` and ``folder_files``, peer tables of the Tag and Work merges,
which is a second reason it must be SERIALIZABLE.

**Lock order.** The package order, applied to many rows at once:

1. every selected Work, ``FOR NO KEY UPDATE``, in one statement sorted by
   id (``COLLATE "C"``), so two bulk actions over overlapping Works take
   their common rows in the same order and queue rather than deadlock;
2. the target Folder ``FOR KEY SHARE`` (``move_folder``), or the Tags
   ``FOR SHARE``, again in one statement sorted by id (``add_tags``,
   ``remove_tags``);
3. revision scopes, one Work at a time in id order (the scope ids are JSON
   arrays starting with the Work id, so this is also their sorted order).

Every single-Work writer of these scopes holds the Work row first, and a
Work delete holds it ``FOR UPDATE``, so a concurrent edit, move, tag change
or delete of a selected Work either finishes before this action locks the
Work (and this SERIALIZABLE transaction then fails with ``40001`` and is
retried against what it committed) or waits until this action commits. A
Tag delete or merge holds its Tags before the Works (``FOR KEY SHARE``, which
does not wait for step 1), so it and a bulk Tag action meet on the Tag row
only, and a Folder delete holds its Folder ``FOR UPDATE``, which a filing's
``FOR KEY SHARE`` waits for.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import LiteralString, Optional

from backend import work_metadata_sync as rules
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.organization import folders, links
from backend.domain.works import metadata

SET_STATUS = "set_status"
MOVE_FOLDER = "move_folder"
ADD_TAGS = "add_tags"
REMOVE_TAGS = "remove_tags"

_LOCK_WORKS_SQL: LiteralString = """
SELECT id FROM prks.works WHERE library_id = %(library_id)s AND id = ANY(%(ids)s)
ORDER BY id COLLATE "C" FOR NO KEY UPDATE
"""
_SHARE_TAGS_SQL: LiteralString = """
SELECT id FROM prks.tags WHERE library_id = %(library_id)s AND id = ANY(%(ids)s)
ORDER BY id COLLATE "C" FOR SHARE
"""


class Missing(Exception):
    """A selected Work, the Folder or a Tag is not in this Library (nothing was written)."""

    WORKS = "works"
    FOLDER = "folder"
    TAGS = "tags"

    def __init__(self, what: str) -> None:
        super().__init__(what)
        self.what = what


def _locked(scope: LibraryScope, sql: LiteralString, ids: list[str]) -> bool:
    """Lock the rows of ``ids`` in id order; True when every one exists here."""
    rows = scope.execute(sql, {"ids": ids}).fetchall()
    return len(rows) == len(ids)


def apply(scope: LibraryScope, action: str, work_ids: Sequence[str], *,
          status: Optional[str] = None, folder_id: Optional[str] = None,
          tag_ids: Sequence[str] = ()) -> None:
    """Run one validated bulk action; raises :class:`Missing` before any write.

    ``work_ids`` and ``tag_ids`` are deduplicated, non-empty ids;
    ``folder_id`` ``None`` or ``""`` takes the Works out of their Folder.
    The checks run in SQLite's order: Works, then the Folder, then the Tags.
    """
    works = sorted(set(work_ids))
    if not _locked(scope, _LOCK_WORKS_SQL, works):
        raise Missing(Missing.WORKS)
    target = (folder_id or "").strip()
    if action == MOVE_FOLDER and target and not folders.share(scope, target):
        raise Missing(Missing.FOLDER)
    tags = sorted(set(tag_ids))
    if action in (ADD_TAGS, REMOVE_TAGS) and not _locked(scope, _SHARE_TAGS_SQL, tags):
        raise Missing(Missing.TAGS)

    if action == SET_STATUS:
        if status is None:
            raise ValueError("set_status needs a status")
        wire = rules.canonical_wire("status", status)
        for work_id in works:
            row = metadata.target(scope, work_id, for_update=True)
            if row is None:  # pragma: no cover - locked in step 1
                raise RuntimeError("a locked Work vanished")
            metadata.set_synced_field(scope, row, "status", wire)
    elif action == MOVE_FOLDER:
        for work_id in works:
            folders.set_work_folder(scope, work_id, target)
    elif action in (ADD_TAGS, REMOVE_TAGS):
        present = action == ADD_TAGS
        for work_id in works:
            for tag_id in tags:
                links.set_state(scope, links.WORK, work_id, tag_id, present)
    else:
        raise ValueError("unknown bulk action: %s" % action)
