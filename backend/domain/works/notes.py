"""Work notes on PostgreSQL: two whole-document conflict units (L-W-1 #628).

``backend/work_note_sync.py``'s rules, on ``prks.work_notes``: the Research
Note and the Private Note are one row each (absent means empty), with the
revision scopes ``work-research-note`` and ``work-private-note`` keyed by
the bare Work id, as on SQLite. A body is validated by the same function
(size, and control characters in a Research Note) and replaced whole; only
a change advances its revision; a stale base conflicts only when the bodies
differ, and a conflict reports sizes, never prose.

Research markup side effects (Concept auto-creation, mentions) are the
Research lane's (L-R-1, ``research.reindex_work`` enqueued in this same
transaction, ``docs/postgresql-architecture.md`` §8.4); until it lands a
Research Note is stored as text only.
"""

from __future__ import annotations

from typing import Any, LiteralString, Optional

from backend import work_note_sync as rules
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.works import revisions

RESEARCH = "research"
PRIVATE = "private"
_SCOPE_TYPES = {RESEARCH: rules.RESEARCH_SCOPE_TYPE, PRIVATE: rules.PRIVATE_SCOPE_TYPE}
_OPERATIONS = {RESEARCH: rules.RESEARCH_OPERATION, PRIVATE: rules.PRIVATE_OPERATION}
KIND_OF_OPERATION = {operation: kind for kind, operation in _OPERATIONS.items()}

_LOCK_WORK: LiteralString = (
    "SELECT id FROM prks.works WHERE library_id = %(library_id)s AND id = %(work_id)s FOR NO KEY UPDATE"
)
_WORK_EXISTS: LiteralString = (
    "SELECT id FROM prks.works WHERE library_id = %(library_id)s AND id = %(work_id)s"
)
_BODY: LiteralString = (
    "SELECT body FROM prks.work_notes"
    " WHERE library_id = %(library_id)s AND work_id = %(work_id)s AND kind = %(kind)s"
)
_UPSERT: LiteralString = """
INSERT INTO prks.work_notes (library_id, work_id, kind, body)
VALUES (%(library_id)s, %(work_id)s, %(kind)s, %(body)s)
ON CONFLICT (work_id, kind) DO UPDATE SET body = EXCLUDED.body, updated_at = now()
"""
_DELETE: LiteralString = (
    "DELETE FROM prks.work_notes"
    " WHERE library_id = %(library_id)s AND work_id = %(work_id)s AND kind = %(kind)s"
)
_TOUCH_WORK: LiteralString = (
    "UPDATE prks.works SET updated_at = now() WHERE library_id = %(library_id)s AND id = %(work_id)s"
)


def validate(kind: str, text: object) -> str:
    """The SQLite rule; raises ``ValueError("INVALID_ENVELOPE")``."""
    return rules.validate_text(_OPERATIONS[kind], text)


def _current(scope: LibraryScope, work_id: str, kind: str) -> str:
    row = scope.execute(_BODY, {"work_id": work_id, "kind": kind}).fetchone()
    return "" if row is None else str(row["body"])


def set_note(scope: LibraryScope, work_id: str, kind: str, text: str) -> Optional[tuple[bool, int]]:
    """Replace one note; ``(changed, revision_after)``, or None when there is no such Work."""
    validate(kind, text)
    if scope.execute(_LOCK_WORK, {"work_id": work_id}).fetchone() is None:
        return None
    return _set_locked(scope, work_id, kind, text)


def _set_locked(scope: LibraryScope, work_id: str, kind: str, text: str) -> tuple[bool, int]:
    revision = revisions.lock(scope, _SCOPE_TYPES[kind], work_id)
    if _current(scope, work_id, kind) == text:
        return False, revision
    if kind == RESEARCH:
        # Held until COMMIT, so a Concept delete that reads the notes after
        # this one commits sees its references (CONCEPT_IN_USE).
        from backend.domain.research import concepts

        concepts.share_referenced(scope, text)
    params = {"work_id": work_id, "kind": kind, "body": text}
    scope.execute(_UPSERT if text else _DELETE, params)
    scope.execute(_TOUCH_WORK, params)
    return True, revisions.advance(scope, _SCOPE_TYPES[kind], work_id)


def apply_note_operation(scope: LibraryScope, operation: str, work_id: str, text: str,
                         base_revision: int) -> tuple[int, dict[str, Any]]:
    """``SET_WORK_RESEARCH_NOTE`` / ``SET_WORK_PRIVATE_NOTE``: SQLite's decision table."""
    kind = KIND_OF_OPERATION[operation]
    result: dict[str, Any] = {"work_id": work_id, "note_kind": _SCOPE_TYPES[kind]}
    if scope.execute(_LOCK_WORK, {"work_id": work_id}).fetchone() is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    revision = revisions.lock(scope, _SCOPE_TYPES[kind], work_id)
    current = _current(scope, work_id, kind)
    if base_revision > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      **rules._disagreement(current, text))
        return 400, result
    if base_revision < revision and current != text:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      **rules._disagreement(current, text))
        return 409, result
    changed, after = _set_locked(scope, work_id, kind, text)
    result.update(code="ACKNOWLEDGED", changed=changed, server_revision=after, value_omitted=True)
    return 200, result


def notes_state(scope: LibraryScope, work_id: str) -> Optional[dict[str, Any]]:
    """``get_work_notes_state``: the two revisions (the bodies are on the Work)."""
    if scope.execute(_WORK_EXISTS, {"work_id": work_id}).fetchone() is None:
        return None
    research = revisions.read(scope, rules.RESEARCH_SCOPE_TYPE, [work_id])[work_id]
    private = revisions.read(scope, rules.PRIVATE_SCOPE_TYPE, [work_id])[work_id]
    return {"work_id": work_id, "research_note_revision": research, "private_note_revision": private}
