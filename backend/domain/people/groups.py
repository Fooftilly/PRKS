"""Person Groups and their members on PostgreSQL (L-P-1 #632).

The rules are ``backend/person_group_sync.py``'s, imported rather than
copied: the field vocabulary and bounds, the canonical spelling, the rule
codes (``GroupRuleError``) and the decision tables. One scope per field
(``person-group-field / ["PG-…","field"]``) and one per membership pair
(``person-group-member / ["PG-…","P-…"]``); only a change advances one.

What moves:

- **Names** are compared by identity key (``prks.identity_key``, §8.1), not
  SQLite's ASCII-only ``LOWER``, and the unique index is the arbiter: a
  create uses ``ON CONFLICT DO NOTHING`` and reads the winner, and a rename
  that loses a race fails on the index with ``NAME_TAKEN``.
- **Acyclicity** needs the whole tree, so every command that can change it
  (create under a parent, change a parent, delete) first takes the Library's
  hierarchy lock. Two concurrent reparents can then never each pass the
  cycle check against the tree the other is changing.
- **A deleted group** leaves its scopes one revision on (§5.2): every field
  scope, edited or not (a revision-0 scope gets its row), and every pair scope it ever had, present or removed earlier
  (SQLite removes the members without advancing them). A deleted Person's
  pair scopes advance the same way. Its children move to its
  parent, advancing their ``parent_id`` scopes, as on SQLite.

Locks follow the package order (``backend.domain.people``): hierarchy, then
group rows, then Person rows, then revision scopes.
"""

from __future__ import annotations

import json
from typing import Any, LiteralString, Optional

from backend import person_group_sync as rules
from backend import work_metadata_sync as meta
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.works import revisions
from backend.person_group_sync import GroupRuleError

FIELD_SCOPE_TYPE = rules.FIELD_SCOPE_TYPE
MEMBER_SCOPE_TYPE = rules.MEMBER_SCOPE_TYPE

_HIERARCHY_LOCK_SQL: LiteralString = (
    "SELECT pg_advisory_xact_lock(hashtextextended('prks.person-groups:' || %(library_id)s, 0))"
)
_GROUP_COLUMNS: LiteralString = """
g.id, g.name, g.parent_id, g.description,
to_char(g.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at,
to_char(g.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS updated_at
"""
_GROUP_SQL: LiteralString = (
    "SELECT " + _GROUP_COLUMNS + " FROM prks.person_groups g"
    " WHERE g.library_id = %(library_id)s AND g.id = %(group_id)s"
)
# SQLite's NOCASE: ASCII letters fold, everything else compares by code
# point. An ASCII-only translate() under COLLATE "C" is the same order.
NAME_ORDER: LiteralString = (
    "translate(g.name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') COLLATE \"C\""
)
_LOCK_GROUPS_SQL: LiteralString = """
SELECT id FROM prks.person_groups
WHERE library_id = %(library_id)s AND id = ANY(%(group_ids)s)
ORDER BY id COLLATE "C" FOR NO KEY UPDATE
"""
_LOCK_FOR_DELETE_SQL: LiteralString = """
SELECT id FROM prks.person_groups
WHERE library_id = %(library_id)s AND id = ANY(%(group_ids)s)
ORDER BY id COLLATE "C" FOR UPDATE
"""
_LOCK_PERSONS_SQL: LiteralString = """
SELECT id FROM prks.persons
WHERE library_id = %(library_id)s AND id = ANY(%(person_ids)s)
ORDER BY id COLLATE "C" FOR NO KEY UPDATE
"""
_EXISTING_SQL: LiteralString = """
SELECT id FROM prks.person_groups WHERE library_id = %(library_id)s AND id = ANY(%(group_ids)s)
"""
_PARENT_SQL: LiteralString = """
SELECT parent_id FROM prks.person_groups WHERE library_id = %(library_id)s AND id = %(group_id)s
"""
_CHILDREN_SQL: LiteralString = """
SELECT id FROM prks.person_groups WHERE library_id = %(library_id)s AND parent_id = %(group_id)s
ORDER BY id COLLATE "C"
"""
_BY_NAME_SQL: LiteralString = """
SELECT id FROM prks.person_groups
WHERE library_id = %(library_id)s AND name_key = prks.identity_key(%(name)s)
"""
_SAME_NAME_SQL: LiteralString = "SELECT prks.identity_key(%(a)s) = prks.identity_key(%(b)s) AS same"
_INSERT_SQL: LiteralString = """
INSERT INTO prks.person_groups (library_id, id, name, parent_id, description)
VALUES (%(library_id)s, %(group_id)s, %(name)s, %(parent_id)s, %(description)s)
ON CONFLICT (library_id, name_key) DO NOTHING
RETURNING id
"""
_DESCENDANTS_SQL: LiteralString = """
WITH RECURSIVE sub(id) AS (
  SELECT id FROM prks.person_groups WHERE library_id = %(library_id)s AND parent_id = %(group_id)s
  UNION
  SELECT g.id FROM prks.person_groups g JOIN sub ON g.parent_id = sub.id
  WHERE g.library_id = %(library_id)s
)
SELECT id FROM sub
"""
_SET_SQL: dict[str, LiteralString] = {
    column: "UPDATE prks.person_groups SET " + column + " = %(value)s, updated_at = now()"
            " WHERE library_id = %(library_id)s AND id = %(group_id)s"
    for column in ("name", "description", "parent_id")
}
_REPARENT_SQL: LiteralString = """
UPDATE prks.person_groups SET parent_id = %(parent_id)s, updated_at = now()
WHERE library_id = %(library_id)s AND parent_id = %(group_id)s
"""
_DELETE_SQL: LiteralString = (
    "DELETE FROM prks.person_groups WHERE library_id = %(library_id)s AND id = %(group_id)s"
)
_IS_MEMBER_SQL: LiteralString = """
SELECT 1 FROM prks.person_group_members
WHERE library_id = %(library_id)s AND group_id = %(group_id)s AND person_id = %(person_id)s
"""
_ADD_MEMBER_SQL: LiteralString = """
INSERT INTO prks.person_group_members (library_id, group_id, person_id)
VALUES (%(library_id)s, %(group_id)s, %(person_id)s)
ON CONFLICT DO NOTHING
"""
_REMOVE_MEMBER_SQL: LiteralString = """
DELETE FROM prks.person_group_members
WHERE library_id = %(library_id)s AND group_id = %(group_id)s AND person_id = %(person_id)s
"""
_MEMBERS_SQL: LiteralString = """
SELECT person_id FROM prks.person_group_members
WHERE library_id = %(library_id)s AND group_id = %(group_id)s ORDER BY person_id COLLATE "C"
"""
_GROUPS_OF_PERSON_SQL: LiteralString = """
SELECT group_id FROM prks.person_group_members
WHERE library_id = %(library_id)s AND person_id = %(person_id)s ORDER BY group_id COLLATE "C"
"""
_TOUCH_GROUP_SQL: LiteralString = (
    "UPDATE prks.person_groups SET updated_at = now()"
    " WHERE library_id = %(library_id)s AND id = %(group_id)s"
)
_TOUCH_PERSON_SQL: LiteralString = (
    "UPDATE prks.persons SET updated_at = now() WHERE library_id = %(library_id)s AND id = %(person_id)s"
)
_PERSON_EXISTS_SQL: LiteralString = (
    "SELECT 1 FROM prks.persons WHERE library_id = %(library_id)s AND id = %(person_id)s"
)
_REMOVE_GROUP_MEMBERS_SQL: LiteralString = """
DELETE FROM prks.person_group_members WHERE library_id = %(library_id)s AND group_id = %(group_id)s
"""
_REMOVE_PERSON_MEMBERSHIPS_SQL: LiteralString = """
DELETE FROM prks.person_group_members WHERE library_id = %(library_id)s AND person_id = %(person_id)s
"""
_TOUCH_PERSONS_SQL: LiteralString = """
UPDATE prks.persons SET updated_at = now() WHERE library_id = %(library_id)s AND id = ANY(%(ids)s)
"""
_TOUCH_GROUPS_SQL: LiteralString = """
UPDATE prks.person_groups SET updated_at = now() WHERE library_id = %(library_id)s AND id = ANY(%(ids)s)
"""
_TOMBSTONE_GROUP_PAIRS_SQL: LiteralString = """
UPDATE prks.entity_revisions SET revision = revision + 1, updated_at = now()
WHERE library_id = %(library_id)s AND scope_type = %(scope_type)s
  AND left(scope_id, %(length)s) = %(affix)s
"""
_TOMBSTONE_PERSON_PAIRS_SQL: LiteralString = """
UPDATE prks.entity_revisions SET revision = revision + 1, updated_at = now()
WHERE library_id = %(library_id)s AND scope_type = %(scope_type)s
  AND right(scope_id, %(length)s) = %(affix)s
"""
# Pair scopes are `["PG-…","P-…"]` and ids never hold a quote, so a prefix
# or suffix match names exactly one side's pairs.
_GROUP_PAIRS_SQL: LiteralString = """
SELECT scope_id, revision FROM prks.entity_revisions
WHERE library_id = %(library_id)s AND scope_type = %(scope_type)s
  AND left(scope_id, %(length)s) = %(affix)s
"""
_PERSON_PAIRS_SQL: LiteralString = """
SELECT scope_id, revision FROM prks.entity_revisions
WHERE library_id = %(library_id)s AND scope_type = %(scope_type)s
  AND right(scope_id, %(length)s) = %(affix)s
"""


def field_scope(group_id: str, field: str) -> str:
    return rules.scope_key(group_id, field)


def member_scope(group_id: str, person_id: str) -> str:
    return rules.member_scope_key(group_id, person_id)


# -- locks and small reads ------------------------------------------------------


def lock_hierarchy(scope: LibraryScope) -> None:
    """Serialize this Library's hierarchy changes until the transaction ends."""
    scope.execute(_HIERARCHY_LOCK_SQL)


def lock_groups(scope: LibraryScope, group_ids: list[str]) -> set[str]:
    """Lock the named groups (absent ids are skipped); returns those found."""
    if not group_ids:
        return set()
    rows = scope.execute(_LOCK_GROUPS_SQL, {"group_ids": sorted(set(group_ids))}).fetchall()
    return {str(row["id"]) for row in rows}


def _lock_persons(scope: LibraryScope, person_ids: list[str]) -> set[str]:
    if not person_ids:
        return set()
    rows = scope.execute(_LOCK_PERSONS_SQL, {"person_ids": sorted(set(person_ids))}).fetchall()
    return {str(row["id"]) for row in rows}


def missing_groups(scope: LibraryScope, group_ids: list[str]) -> list[str]:
    """The ids of ``group_ids`` naming no group of this Library, in order."""
    if not group_ids:
        return []
    found = {str(row["id"]) for row in scope.execute(_EXISTING_SQL, {"group_ids": group_ids}).fetchall()}
    return [group_id for group_id in group_ids if group_id not in found]


def group(scope: LibraryScope, group_id: str) -> Optional[dict[str, Any]]:
    """The group row (``SELECT * FROM person_groups`` on SQLite), or None."""
    row = scope.execute(_GROUP_SQL, {"group_id": group_id}).fetchone()
    return None if row is None else dict(row)


def id_for_name(scope: LibraryScope, name: str) -> Optional[str]:
    row = scope.execute(_BY_NAME_SQL, {"name": name}).fetchone()
    return None if row is None else str(row["id"])


def same_name(scope: LibraryScope, first: str, second: str) -> bool:
    """Whether two names have one identity key (a pure expression, no table)."""
    row = scope.execute_unscoped(_SAME_NAME_SQL, {"a": first, "b": second}).fetchone()
    return row is not None and bool(row["same"])


def groups_of_person(scope: LibraryScope, person_id: str) -> list[str]:
    rows = scope.execute(_GROUPS_OF_PERSON_SQL, {"person_id": person_id}).fetchall()
    return [str(row["group_id"]) for row in rows]


def _members(scope: LibraryScope, group_id: str) -> list[str]:
    return [str(row["person_id"]) for row in scope.execute(_MEMBERS_SQL, {"group_id": group_id}).fetchall()]


def _descendants(scope: LibraryScope, group_id: str) -> set[str]:
    return {str(row["id"]) for row in scope.execute(_DESCENDANTS_SQL, {"group_id": group_id}).fetchall()}


def _current_field(scope: LibraryScope, group_id: str, field: str) -> Optional[str]:
    row = group(scope, group_id)
    if row is None:
        return None
    value = row[field]
    return "" if value is None else str(value)


def _validate(field: str, value: Any) -> str:
    text = rules.validate_field_value(field, value)
    if "\x00" in text:
        raise GroupRuleError("INVALID_ENVELOPE", "field values cannot contain NUL")
    return text


# -- create ---------------------------------------------------------------------


def insert_group(scope: LibraryScope, group_id: str, name: str, parent_id: str = "",
                 description: str = "") -> str:
    """``insert_group_on_conn``: construction, every field at revision 0.

    Raises ``GroupRuleError`` (``INVALID_ENVELOPE``, ``NAME_TAKEN``,
    ``PARENT_NOT_FOUND``) with nothing inserted.
    """
    clean_name = _validate("name", name)
    clean_parent = _validate("parent_id", parent_id)
    clean_description = _validate("description", description)
    if clean_parent:
        lock_hierarchy(scope)
    if id_for_name(scope, clean_name) is not None:
        raise GroupRuleError("NAME_TAKEN", "A group with this name already exists.")
    if clean_parent and group(scope, clean_parent) is None:
        raise GroupRuleError("PARENT_NOT_FOUND", "Parent group not found.")
    # The read above is a fast refusal; the unique index decides a race.
    row = scope.execute(_INSERT_SQL, {"group_id": group_id, "name": clean_name,
                                      "parent_id": clean_parent or None,
                                      "description": clean_description}).fetchone()
    if row is None:
        raise GroupRuleError("NAME_TAKEN", "A group with this name already exists.")
    return group_id


def resolve_parent(scope: LibraryScope, name: str, new_id: str) -> str:
    """The group named ``name``, created (as ``new_id``) when there is none."""
    found = id_for_name(scope, name)
    if found is not None:
        return found
    try:
        return insert_group(scope, new_id, name)
    except GroupRuleError as error:
        if error.code != "NAME_TAKEN":
            raise
    # A concurrent create committed the name first: that group is the parent.
    found = id_for_name(scope, name)
    if found is None:  # pragma: no cover - ON CONFLICT saw a committed row
        raise GroupRuleError("NAME_TAKEN", "A group with this name already exists.")
    return found


# -- fields ---------------------------------------------------------------------


def set_field(scope: LibraryScope, group_id: str, field: str, value: Any) -> tuple[bool, int]:
    """``set_field_on_conn``: write one field and advance its revision together.

    Returns ``(changed, revision_after)``; raises ``GroupRuleError`` for an
    invalid value, a missing group, a taken name or an unusable parent.
    """
    desired = _validate(field, value)
    if field == "parent_id":
        lock_hierarchy(scope)
    if not lock_groups(scope, [group_id]):
        raise GroupRuleError("ENTITY_NOT_FOUND", "Group not found.")
    revision = revisions.lock(scope, FIELD_SCOPE_TYPE, field_scope(group_id, field))
    if _current_field(scope, group_id, field) == desired:
        return False, revision
    if field == "name":
        other = id_for_name(scope, desired)
        if other is not None and other != group_id:
            raise GroupRuleError("NAME_TAKEN", "A group with this name already exists.")
    if field == "parent_id":
        _assert_parent_usable(scope, group_id, desired)
    stored = (desired or None) if field == "parent_id" else desired
    scope.execute(_SET_SQL[field], {"group_id": group_id, "value": stored})
    return True, revisions.advance(scope, FIELD_SCOPE_TYPE, field_scope(group_id, field))


def _assert_parent_usable(scope: LibraryScope, group_id: str, parent_id: str) -> None:
    """``assert_parent_usable``, under the hierarchy lock the caller holds."""
    if not parent_id:
        return
    if parent_id == group_id:
        raise GroupRuleError("PARENT_CYCLE", "A group cannot be its own parent.")
    if group(scope, parent_id) is None:
        raise GroupRuleError("PARENT_NOT_FOUND", "Parent group not found.")
    if parent_id in _descendants(scope, group_id):
        raise GroupRuleError("PARENT_CYCLE", "Cannot set parent to a subgroup (cycle).")


def _rule_status(code: str) -> int:
    return 404 if code in ("ENTITY_NOT_FOUND", "PARENT_NOT_FOUND", "PERSON_NOT_FOUND") else 409


def apply_field_operation(scope: LibraryScope, group_id: str, field: str, value: str,
                          base_revision: int) -> tuple[int, dict[str, Any]]:
    """``SET_PERSON_GROUP_FIELD``: SQLite's decision table, result for result."""
    desired = rules.canonical_wire(field, value)
    result: dict[str, Any] = {"group_id": group_id, "field": field}
    if field == "parent_id":
        lock_hierarchy(scope)
    if not lock_groups(scope, [group_id]):
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    revision = revisions.lock(scope, FIELD_SCOPE_TYPE, field_scope(group_id, field))
    current = _current_field(scope, group_id, field) or ""
    if base_revision > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 400, meta.fit_terminal_result(result, current, desired)
    if base_revision < revision and current != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 409, meta.fit_terminal_result(result, current, desired)
    try:
        changed, after = set_field(scope, group_id, field, desired)
    except GroupRuleError as error:
        result.update(code=error.code, current_revision=revision, message=str(error))
        return _rule_status(error.code), result
    result.update(code="ACKNOWLEDGED", server_revision=after, changed=changed, value_omitted=True)
    return 200, result


# -- membership -----------------------------------------------------------------


def lock_pair(scope: LibraryScope, group_id: str, person_id: str) -> tuple[bool, bool]:
    """Lock the group, then the Person; whether each exists."""
    return bool(lock_groups(scope, [group_id])), bool(_lock_persons(scope, [person_id]))


def is_member(scope: LibraryScope, group_id: str, person_id: str) -> bool:
    params = {"group_id": group_id, "person_id": person_id}
    return scope.execute(_IS_MEMBER_SQL, params).fetchone() is not None


def set_member(scope: LibraryScope, group_id: str, person_id: str, present: bool) -> bool:
    """``set_member_on_conn``: the one membership boundary; True when it changed.

    The caller has checked that both exist when adding. The pair's scope
    advances on every change and outlives the relationship. Run an add
    SERIALIZABLE (§7.1: a peer-table write of the protected Person delete).
    """
    # Every membership writer holds both rows first, so the membership cannot
    # change under this check, and a no-op leaves no revision row behind
    # (the state readers would list one as a removed pair).
    lock_pair(scope, group_id, person_id)
    if is_member(scope, group_id, person_id) == present:
        return False
    revisions.lock(scope, MEMBER_SCOPE_TYPE, member_scope(group_id, person_id))
    params = {"group_id": group_id, "person_id": person_id}
    scope.execute(_ADD_MEMBER_SQL if present else _REMOVE_MEMBER_SQL, params)
    revisions.advance(scope, MEMBER_SCOPE_TYPE, member_scope(group_id, person_id))
    # Group chips are embedded in the People rows and the Person detail.
    scope.execute(_TOUCH_PERSON_SQL, params)
    scope.execute(_TOUCH_GROUP_SQL, params)
    return True


def apply_member_operation(scope: LibraryScope, group_id: str, person_id: str, desired: bool,
                           base_revision: int) -> tuple[int, dict[str, Any]]:
    """``ADD/REMOVE_PERSON_GROUP_MEMBER``: SQLite's decision table, result for result."""
    result: dict[str, Any] = {"group_id": group_id, "person_id": person_id}
    group_found, person_found = lock_pair(scope, group_id, person_id)
    if not group_found:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    if not person_found:
        result["code"] = "PERSON_NOT_FOUND"
        return 404, result
    # Read, not ensured: the row locks above serialize every writer of this
    # pair, and a refused or converged operation must not create its scope.
    pair = member_scope(group_id, person_id)
    revision = revisions.read(scope, MEMBER_SCOPE_TYPE, [pair])[pair]
    existing = is_member(scope, group_id, person_id)
    if base_revision > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_present=existing, requested_present=desired)
        return 400, result
    if base_revision < revision and existing != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_present=existing, requested_present=desired)
        return 409, result
    changed = set_member(scope, group_id, person_id, desired)
    result.update(code="ACKNOWLEDGED", changed=changed, present=desired,
                  server_revision=revision + int(changed))
    return 200, result


def replace_memberships(scope: LibraryScope, person_id: str, group_ids: list[str]) -> None:
    """Make ``group_ids`` the Person's exact set, by diffing (unchanged pairs advance nothing).

    The caller holds the group locks, then the Person lock, and has checked
    every group exists.
    """
    desired = set(group_ids)
    current = set(groups_of_person(scope, person_id))
    for group_id in sorted(current - desired):
        set_member(scope, group_id, person_id, False)
    for group_id in sorted(desired - current):
        set_member(scope, group_id, person_id, True)


def _group_affix(group_id: str) -> str:
    return '["' + group_id + '",'


def _person_affix(person_id: str) -> str:
    return ',"' + person_id + '"]'


def drop_memberships(scope: LibraryScope, *, group_id: Optional[str] = None,
                     person_id: Optional[str] = None) -> None:
    """Remove every membership of a group or a Person being deleted, and tombstone its pairs.

    Exactly one of ``group_id`` and ``person_id``. Every pair scope of that
    identity advances once, the pairs removed now and the ones removed
    earlier alike (§5.2): a client-minted id can be created again, and a base
    observed before the delete must then be stale. The caller holds the
    identity's row lock, which every writer of its pairs takes first.
    """
    if (group_id is None) == (person_id is None):
        raise ValueError("drop_memberships takes a group or a Person")
    if group_id is not None:
        others = _members(scope, group_id)
        pairs = [(MEMBER_SCOPE_TYPE, member_scope(group_id, other)) for other in others]
        tombstone, affix = _TOMBSTONE_GROUP_PAIRS_SQL, _group_affix(group_id)
        remove, touch = _REMOVE_GROUP_MEMBERS_SQL, _TOUCH_PERSONS_SQL
    else:
        assert person_id is not None
        others = groups_of_person(scope, person_id)
        pairs = [(MEMBER_SCOPE_TYPE, member_scope(other, person_id)) for other in others]
        tombstone, affix = _TOMBSTONE_PERSON_PAIRS_SQL, _person_affix(person_id)
        remove, touch = _REMOVE_PERSON_MEMBERSHIPS_SQL, _TOUCH_GROUPS_SQL
    # A present pair always has a scope row (its add advanced it); ensuring
    # one anyway keeps "every pair advances" true for any row.
    revisions.lock_many(scope, pairs)
    scope.execute(remove, {"group_id": group_id, "person_id": person_id})
    if others:
        scope.execute(touch, {"ids": others})
    scope.execute(tombstone, {"scope_type": MEMBER_SCOPE_TYPE, "affix": affix, "length": len(affix)})


# -- delete ---------------------------------------------------------------------


def delete_group(scope: LibraryScope, group_id: str) -> bool:
    """``delete_group_on_conn``: children move to the parent; False when there is no group."""
    lock_hierarchy(scope)
    row = scope.execute(_PARENT_SQL, {"group_id": group_id}).fetchone()
    if row is None:
        return False
    inherited = row["parent_id"]
    children = [str(child["id"]) for child in scope.execute(_CHILDREN_SQL, {"group_id": group_id})]
    scope.execute(_LOCK_FOR_DELETE_SQL, {"group_ids": sorted([group_id, *children])})
    _lock_persons(scope, _members(scope, group_id))
    drop_memberships(scope, group_id=group_id)
    child_scopes = [(FIELD_SCOPE_TYPE, field_scope(child, "parent_id")) for child in children]
    revisions.lock_many(scope, child_scopes)
    scope.execute(_REPARENT_SQL, {"group_id": group_id, "parent_id": inherited})
    for scope_type, scope_id in sorted(child_scopes):
        revisions.advance(scope, scope_type, scope_id)
    # Every field scope advances, a never-edited one (revision 0, no row) too.
    for key in revisions.lock_many(scope, [(FIELD_SCOPE_TYPE, field_scope(group_id, f)) for f in rules.FIELDS]):
        revisions.advance(scope, *key)
    scope.execute(_DELETE_SQL, {"group_id": group_id})
    return True


# -- revision state -------------------------------------------------------------


def _pairs(scope: LibraryScope, query: LiteralString, affix: str) -> list[tuple[list[str], int]]:
    rows = scope.execute(query, {"scope_type": MEMBER_SCOPE_TYPE, "affix": affix,
                                 "length": len(affix)}).fetchall()
    out: list[tuple[list[str], int]] = []
    for row in rows:
        try:
            pair = json.loads(str(row["scope_id"]))
        except ValueError:
            continue
        if isinstance(pair, list) and len(pair) == 2:
            out.append(([str(pair[0]), str(pair[1])], int(row["revision"])))
    return out


def group_state(scope: LibraryScope, group_id: str) -> Optional[dict[str, Any]]:
    """``get_group_state_on_conn``: field and membership revisions, tombstones included."""
    if group(scope, group_id) is None:
        return None
    fields = sorted(rules.FIELDS)
    by_scope = revisions.read(scope, FIELD_SCOPE_TYPE, [field_scope(group_id, f) for f in fields])
    present = set(_members(scope, group_id))
    members: list[dict[str, Any]] = []
    seen: set[str] = set()
    for (scoped_group, person_id), revision in _pairs(scope, _GROUP_PAIRS_SQL, _group_affix(group_id)):
        if scoped_group != group_id:
            continue
        seen.add(person_id)
        members.append({"person_id": person_id, "revision": revision, "present": person_id in present})
    for person_id in sorted(present - seen):
        members.append({"person_id": person_id, "revision": 0, "present": True})
    members.sort(key=lambda member: member["person_id"])
    return {"group_id": group_id,
            "fields": {f: {"revision": by_scope[field_scope(group_id, f)]} for f in fields},
            "members": members}


def person_group_state(scope: LibraryScope, person_id: str) -> Optional[dict[str, Any]]:
    """``get_person_group_state_on_conn``: the same bookkeeping, keyed by Person."""
    if scope.execute(_PERSON_EXISTS_SQL, {"person_id": person_id}).fetchone() is None:
        return None
    present = set(groups_of_person(scope, person_id))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for (group_id, scoped_person), revision in _pairs(scope, _PERSON_PAIRS_SQL, _person_affix(person_id)):
        if scoped_person != person_id:
            continue
        seen.add(group_id)
        out.append({"group_id": group_id, "revision": revision, "present": group_id in present})
    for group_id in sorted(present - seen):
        out.append({"group_id": group_id, "revision": 0, "present": True})
    out.sort(key=lambda entry: entry["group_id"])
    return {"person_id": person_id, "groups": out}
