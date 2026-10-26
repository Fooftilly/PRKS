"""Positions on PostgreSQL (L-R-1 #634).

The rules are SQLite's (``backend/position_sync.py``,
``backend/research_network.py``), imported rather than copied: the name and
description rules (``canonical_wire``), the scope (``position-field /
["P-…","field"]``, one per field, since a Position's two fields are
independent) and the decision table. Only a change advances a scope, and
construction advances none.

What moves:

- **A targeted Position** refuses its delete (``POSITION_IN_USE``). The
  delete holds the row ``FOR UPDATE`` and every writer of a target holds
  the Positions it names ``FOR KEY SHARE`` (``arguments``), so the check
  sees every target committed before it, and a target written after it
  finds the Position gone.
- **A deleted Position** leaves both field scopes one revision on, a
  never-edited one too, so a base observed before the delete is stale on a
  Position created again under the id (SQLite leaves them as they were).

The lock order is the package's (``backend.domain.research``).
"""

from __future__ import annotations

from typing import Any, LiteralString, Optional

from backend import position_sync as rules
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.works import revisions
from backend.research_network import ResearchError

FIELD_SCOPE_TYPE = rules.FIELD_SCOPE_TYPE

_NOT_FOUND = ("not_found", "Position not found.", 404)
_IN_USE = ("position_in_use", "This Position is still targeted by an Argument or Stance.", 409)

# SQLite's LOWER() folds ASCII letters only; an ASCII-only translate() under
# COLLATE "C" is the same order.
_NAME_ORDER: LiteralString = (
    "translate(p.name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') COLLATE \"C\","
    " p.id COLLATE \"C\""
)
_COLUMNS: LiteralString = """
p.id, p.name, p.description,
to_char(p.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at,
to_char(p.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS updated_at
"""
_ROW_SQL: LiteralString = (
    "SELECT " + _COLUMNS + " FROM prks.positions p WHERE p.library_id = %(library_id)s AND p.id = %(position_id)s"
)
_ALL_SQL: LiteralString = (
    "SELECT " + _COLUMNS + " FROM prks.positions p WHERE p.library_id = %(library_id)s ORDER BY " + _NAME_ORDER
)
# ``_position_arguments``: the Arguments targeting it, by verdict, then name.
_ARGUMENTS_SQL: LiteralString = """
SELECT a.id, a.name, a.kind, v.id AS verdict_id, v.label AS verdict_label
FROM prks.argument_target_positions t
JOIN prks.arguments a ON a.library_id = t.library_id AND a.id = t.argument_id
JOIN prks.argument_verdicts v ON v.id = t.verdict_id
WHERE t.library_id = %(library_id)s AND t.position_id = %(position_id)s
ORDER BY v.sort_order,
         translate(a.name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') COLLATE "C",
         a.id COLLATE "C"
"""
_LOCK_SQL: LiteralString = """
SELECT id FROM prks.positions WHERE library_id = %(library_id)s AND id = %(position_id)s FOR NO KEY UPDATE
"""
_LOCK_KEY_SQL: LiteralString = """
SELECT id FROM prks.positions WHERE library_id = %(library_id)s AND id = %(position_id)s FOR UPDATE
"""
_SHARE_SQL: LiteralString = """
SELECT id FROM prks.positions WHERE library_id = %(library_id)s AND id = %(position_id)s FOR KEY SHARE
"""
_FIELDS_SQL: LiteralString = """
SELECT name, description FROM prks.positions WHERE library_id = %(library_id)s AND id = %(position_id)s
"""
_INSERT_SQL: LiteralString = """
INSERT INTO prks.positions (library_id, id, name, description)
VALUES (%(library_id)s, %(position_id)s, %(name)s, %(description)s)
ON CONFLICT DO NOTHING
RETURNING id
"""
_SET_SQL: dict[str, LiteralString] = {
    "name": "UPDATE prks.positions SET name = %(value)s, updated_at = now()"
            " WHERE library_id = %(library_id)s AND id = %(position_id)s",
    "description": "UPDATE prks.positions SET description = %(value)s, updated_at = now()"
                   " WHERE library_id = %(library_id)s AND id = %(position_id)s",
}
_TARGETED_SQL: LiteralString = """
SELECT 1 AS found FROM prks.argument_target_positions
WHERE library_id = %(library_id)s AND position_id = %(position_id)s LIMIT 1
"""
_DELETE_SQL: LiteralString = "DELETE FROM prks.positions WHERE library_id = %(library_id)s AND id = %(position_id)s"


class ForeignPositionId(Exception):
    """The id is a Position of another Library."""


def _refuse(rule: tuple[str, str, int]) -> ResearchError:
    return ResearchError(*rule)


def _usable(position_id: str) -> bool:
    # PostgreSQL text cannot hold NUL; no stored id does.
    return bool(position_id) and "\x00" not in position_id


def lock(scope: LibraryScope, position_id: str, *, key: bool = False) -> bool:
    """Hold the Position a command changes (``FOR UPDATE`` when ``key``); whether it exists."""
    if not _usable(position_id):
        return False
    return scope.execute(_LOCK_KEY_SQL if key else _LOCK_SQL, {"position_id": position_id}).fetchone() is not None


def _share(scope: LibraryScope, position_id: str) -> bool:
    return scope.execute(_SHARE_SQL, {"position_id": position_id}).fetchone() is not None


def _fields(scope: LibraryScope, position_id: str) -> Optional[dict[str, str]]:
    if not _usable(position_id):
        return None
    found = scope.execute(_FIELDS_SQL, {"position_id": position_id}).fetchone()
    return None if found is None else {"name": str(found["name"]), "description": str(found["description"])}


def _revision(scope: LibraryScope, position_id: str, field: str) -> int:
    scope_id = rules.scope_key(position_id, field)
    return revisions.read(scope, FIELD_SCOPE_TYPE, [scope_id])[scope_id]


def position_row(scope: LibraryScope, position_id: str) -> Optional[dict[str, str]]:
    """``{"id", "name", "description"}``: what ``CREATE_POSITION`` acknowledges."""
    found = _fields(scope, position_id)
    return None if found is None else {"id": position_id, **found}


def sync_state(scope: LibraryScope, position_id: str) -> Optional[dict[str, Any]]:
    """``get_position_state_on_conn``: the field revisions, read in one snapshot."""
    if _fields(scope, position_id) is None:
        return None
    scopes = {field: rules.scope_key(position_id, field) for field in sorted(rules.FIELDS)}
    found = revisions.read(scope, FIELD_SCOPE_TYPE, list(scopes.values()))
    return {"position_id": position_id,
            "fields": {field: {"revision": found[scope_id]} for field, scope_id in scopes.items()}}


# -- construction ---------------------------------------------------------------


def insert(scope: LibraryScope, position_id: str, name: Any, description: Any = "") -> bool:
    """``insert_position_on_conn``: the one construction primitive, no revision advanced.

    Returns False when the id is already a Position of this Library (a
    concurrent create of it committed first); that Position is then held
    ``FOR KEY SHARE`` until COMMIT. Raises ``ResearchError`` for an illegal
    name or description and :class:`ForeignPositionId` for an id of another
    Library.
    """
    values = {"position_id": position_id, "name": rules.canonical_wire("name", name),
              "description": rules.canonical_wire("description", description)}
    # Twice: the winner of a same-id race can be deleted between its COMMIT
    # and this hold, and then the id is free again.
    for _ in range(2):
        if scope.execute(_INSERT_SQL, values).fetchone() is not None:
            return True
        if _share(scope, position_id):
            return False
    raise ForeignPositionId(position_id)


def apply_create(scope: LibraryScope, position_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """``CREATE_POSITION``'s ``apply_create``: a known id acknowledges the stored Position.

    Raises :class:`ForeignPositionId` for an id of another Library.
    """
    result: dict[str, Any] = {"position_id": position_id}
    created = not _share(scope, position_id) and insert(scope, position_id, payload["name"],
                                                         payload["description"])
    result.update(code="ACKNOWLEDGED", changed=created, position=position_row(scope, position_id))
    return 200, result


# -- fields -------------------------------------------------------------------------


def set_field(scope: LibraryScope, position_id: str, field: str, value: Any) -> tuple[bool, int]:
    """``set_field_on_conn``: write one field and advance its revision together."""
    if field not in rules.FIELD_SET:
        raise ResearchError("invalid_field", "Not an editable Position field.")
    desired = rules.canonical_wire(field, value)
    if not lock(scope, position_id):
        raise _refuse(_NOT_FOUND)
    current = _fields(scope, position_id)
    assert current is not None
    scope_id = rules.scope_key(position_id, field)
    if current[field] == desired:
        return False, _revision(scope, position_id, field)
    revisions.lock(scope, FIELD_SCOPE_TYPE, scope_id)
    scope.execute(_SET_SQL[field], {"position_id": position_id, "value": desired})
    return True, revisions.advance(scope, FIELD_SCOPE_TYPE, scope_id)


def apply_field_operation(scope: LibraryScope, position_id: str, field: str, value: str,
                          base_revision: int) -> tuple[int, dict[str, Any]]:
    """``SET_POSITION_FIELD``'s ``apply_field``: SQLite's decision table, result for result."""
    from backend import work_metadata_sync as meta

    desired = rules.canonical_wire(field, value)
    result: dict[str, Any] = {"position_id": position_id, "field": field}
    if not lock(scope, position_id):
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    # Read, not ensured: the Position lock serializes every writer of the
    # field, and a refused or converged operation must not create a scope.
    revision = _revision(scope, position_id, field)
    fields = _fields(scope, position_id)
    assert fields is not None
    current = fields[field]
    if base_revision > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 400, meta.fit_terminal_result(result, current, desired)
    if base_revision < revision and current != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 409, meta.fit_terminal_result(result, current, desired)
    changed, after = set_field(scope, position_id, field, desired)
    result.update(code="ACKNOWLEDGED", server_revision=after, changed=changed, value_omitted=True)
    return 200, result


# -- delete -----------------------------------------------------------------------


def delete(scope: LibraryScope, position_id: str) -> tuple[bool, Optional[str]]:
    """``delete_position_on_conn``: remove the Position, or refuse; ``(deleted, refusal_code)``.

    A Position an Argument targets is ``POSITION_IN_USE``. Otherwise both
    field scopes advance once and the Position goes.
    """
    if not lock(scope, position_id, key=True):
        return False, None
    if scope.execute(_TARGETED_SQL, {"position_id": position_id}).fetchone() is not None:
        return False, "POSITION_IN_USE"
    # A scope no edit touched has no row and reads as revision 0, the base a
    # recreated Position starts from: ensured here, it advances with the rest.
    locked = revisions.lock_many(scope, [(FIELD_SCOPE_TYPE, rules.scope_key(position_id, field))
                                         for field in rules.FIELDS])
    for scope_type, scope_id in locked:
        revisions.advance(scope, scope_type, scope_id)
    scope.execute(_DELETE_SQL, {"position_id": position_id})
    return True, None


# -- the HTTP reads and commands ---------------------------------------------------


def detail(scope: LibraryScope, position_id: str) -> Optional[dict[str, Any]]:
    """``get_position``: the row with the Arguments targeting it; None when absent."""
    if not _usable(position_id):
        return None
    found = scope.execute(_ROW_SQL, {"position_id": position_id}).fetchone()
    if found is None:
        return None
    item = dict(found)
    item["arguments"] = [dict(row) for row in scope.execute(_ARGUMENTS_SQL, {"position_id": position_id})]
    return item


def _detail_or_refuse(scope: LibraryScope, position_id: str) -> dict[str, Any]:
    item = detail(scope, position_id)
    if item is None:
        raise _refuse(_NOT_FOUND)
    return item


def catalog(scope: LibraryScope) -> list[dict[str, Any]]:
    """``list_positions``: every Position by name."""
    return [dict(row) for row in scope.execute(_ALL_SQL)]


def create(scope: LibraryScope, new_id: str, name: Any, description: Any = "") -> dict[str, Any]:
    """``create_position``: construct under a fresh id; the detail."""
    if not insert(scope, new_id, name, description):  # pragma: no cover - a fresh id is never taken
        raise RuntimeError("a generated position id is taken")
    return _detail_or_refuse(scope, new_id)


def update(scope: LibraryScope, position_id: str, name: Any = None, description: Any = None) -> dict[str, Any]:
    """``update_position``: one field call per field supplied, each advancing its own scope."""
    pid = (position_id or "").strip()
    if not pid:
        raise _refuse(_NOT_FOUND)
    if name is None and description is None:
        raise ResearchError("nothing_to_update", "Nothing to update.")
    if not lock(scope, pid):
        raise _refuse(_NOT_FOUND)
    if name is not None:
        set_field(scope, pid, "name", name)
    if description is not None:
        set_field(scope, pid, "description", description)
    return _detail_or_refuse(scope, pid)


def remove(scope: LibraryScope, position_id: str) -> None:
    """``delete_position``: SQLite's refusals."""
    pid = (position_id or "").strip()
    deleted, refusal = delete(scope, pid)
    if refusal:
        raise _refuse(_IN_USE)
    if not deleted:
        raise _refuse(_NOT_FOUND)
