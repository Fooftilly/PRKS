"""Revision scopes on PostgreSQL (``docs/postgresql-architecture.md`` §7.2; L-W-1 #628).

A revision counts changes to one conflict unit (a Work field, a note, a
source). The rules are SQLite's (``docs/local-first-sync.md``): a scope with
no row is at revision 0, only a change advances it, and the decision table
compares an operation's base with the current revision. What moves is the
locking: instead of SQLite's one global ``BEGIN IMMEDIATE`` writer, a
command locks only the scopes it decides on, with

1. ``INSERT … revision 0 … ON CONFLICT DO NOTHING``, so the row exists;
2. ``SELECT revision … FOR UPDATE``, which waits for any other command on
   the same scope and then reads the revision it committed;
3. the decision, and ``advance`` when the value changed.

A command that locks several scopes locks them in sorted order, so two such
commands never deadlock on each other. Scope ids keep SQLite's text form
(the bare Work id for notes, canonical JSON arrays for fields and sources),
so a client's stored bases mean the same on both backends.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import LiteralString

from backend.domain.identity.library_scope import LibraryScope

_ENSURE: LiteralString = """
INSERT INTO prks.entity_revisions (library_id, scope_type, scope_id, revision)
VALUES (%(library_id)s, %(scope_type)s, %(scope_id)s, 0)
ON CONFLICT DO NOTHING
"""
_LOCK: LiteralString = """
SELECT revision FROM prks.entity_revisions
WHERE library_id = %(library_id)s AND scope_type = %(scope_type)s AND scope_id = %(scope_id)s
FOR UPDATE
"""
_ADVANCE: LiteralString = """
UPDATE prks.entity_revisions SET revision = revision + 1, updated_at = now()
WHERE library_id = %(library_id)s AND scope_type = %(scope_type)s AND scope_id = %(scope_id)s
RETURNING revision
"""
_READ: LiteralString = """
SELECT scope_id, revision FROM prks.entity_revisions
WHERE library_id = %(library_id)s AND scope_type = %(scope_type)s AND scope_id = ANY(%(scope_ids)s)
"""
# Every scope a Work owns: the bare-id note scopes and the JSON-array scopes
# whose first element is the Work (`["W-…"]`, `["W-…","field"]`, …). Ids
# never contain a quote, so the prefix match cannot reach another Work.
_ADVANCE_WORK_SCOPES: LiteralString = """
UPDATE prks.entity_revisions SET revision = revision + 1, updated_at = now()
WHERE library_id = %(library_id)s
  AND (scope_id = %(work_id)s
       OR scope_id = %(array_scope)s
       OR left(scope_id, %(prefix_len)s) = %(array_prefix)s)
"""


def lock(scope: LibraryScope, scope_type: str, scope_id: str) -> int:
    """Lock one scope for this transaction and return its committed revision."""
    params = {"scope_type": scope_type, "scope_id": scope_id}
    scope.execute(_ENSURE, params)
    row = scope.execute(_LOCK, params).fetchone()
    if row is None:  # pragma: no cover - the row was ensured above in this transaction
        raise RuntimeError("revision row vanished under its lock")
    return int(row["revision"])


def lock_many(scope: LibraryScope, scopes: Iterable[tuple[str, str]]) -> dict[tuple[str, str], int]:
    """Lock several scopes in one fixed (sorted) order."""
    return {key: lock(scope, *key) for key in sorted(set(scopes))}


def advance(scope: LibraryScope, scope_type: str, scope_id: str) -> int:
    """Advance a scope this transaction has locked; returns the new revision."""
    row = scope.execute(_ADVANCE, {"scope_type": scope_type, "scope_id": scope_id}).fetchone()
    if row is None:
        raise RuntimeError("advance() needs the scope locked first")
    return int(row["revision"])


def read(scope: LibraryScope, scope_type: str, scope_ids: list[str]) -> dict[str, int]:
    """Committed revisions of the named scopes; a missing scope is 0."""
    rows = scope.execute(_READ, {"scope_type": scope_type, "scope_ids": scope_ids}).fetchall()
    found = {str(row["scope_id"]): int(row["revision"]) for row in rows}
    return {scope_id: found.get(scope_id, 0) for scope_id in scope_ids}


def tombstone_work_scopes(scope: LibraryScope, work_id: str) -> None:
    """Advance every existing scope of a deleted Work by one (§5.2).

    The rows stay, one revision on: a device holding a base observed before
    the delete is stale against every one of them, whatever it sends later.
    Only rows that exist advance, so the caller ensures (``lock_many``) any
    scope that must be tombstoned even if it was never edited.
    """
    array_prefix = '["' + work_id + '",'
    scope.execute(_ADVANCE_WORK_SCOPES, {
        "work_id": work_id,
        "array_scope": '["' + work_id + '"]',
        "array_prefix": array_prefix,
        "prefix_len": len(array_prefix),
    })
