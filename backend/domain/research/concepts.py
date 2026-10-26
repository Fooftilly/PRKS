"""Concepts, their aliases and hierarchy on PostgreSQL (L-R-1 #634).

The rules are SQLite's (``backend/concept_sync.py``, ``backend/research_network.py``),
imported rather than copied: the name and definition rules
(``_concept_name``, ``_optional_markdown``, ``ResearchError`` and its codes
and messages), the scopes (``concept-field / ["C-…","description"]``,
``concept-identity / C-…``, ``concept-parents / C-…``) and their decision
tables. Only a change advances a scope, and construction advances none.

What moves:

- **Names and aliases** compare by identity key (``prks.identity_key``,
  §8.1), which is ``normalize_concept_key``. The name of every Concept and
  every alias share one namespace, ``concept_keys`` (``PRIMARY KEY
  (library_id, key)``), so resolution is one index lookup instead of
  SQLite's scan of every Concept in Python, and two Concepts can never
  share a key (SQLite's ``ambiguous_concept`` cannot arise). A create
  inserts its key with ``ON CONFLICT DO NOTHING``: a concurrent writer of
  the key makes it wait, and the conflict then names the owner. A rename or
  an alias write inserts plainly; losing a race fails on
  :data:`KEY_INDEX`, and the caller decides once more with the winner
  visible (``CONCEPT_EXISTS`` or ``ALIAS_CONFLICT``).
- **Acyclicity** needs the whole hierarchy, so a parent change takes the
  Library's Concept hierarchy lock and checks the ancestors of the new
  parents in one recursive query. A delete takes the lock too, because it
  removes edges and advances the scopes of the Concepts on their other
  end: every writer of a ``concept-parents`` scope holds the lock, so a
  revision read under it is final.
- **A deleted Concept** has its parent and child edges removed first
  (SQLite cascades them; here the keys are NO ACTION) and leaves every
  scope it owns one revision on, a never-edited one too, plus the
  ``concept-parents`` scope of every Concept on the other end of an edge
  (SQLite advances only the identity and those neighbours). A
  client-minted id can be created again, and a base observed before the
  delete is then stale.
- **Orderings** SQLite leaves to storage (aliases with one ``LOWER``,
  parents and children of ``get_concept_on_conn``) are broken by spelling
  and id.

The lock order is the package's (``backend.domain.research``).
"""

from __future__ import annotations

from typing import Any, LiteralString, Optional, Sequence

from backend import concept_sync as rules
from backend import research_network as network
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.works import revisions
from backend.research_markup import parse_research_markup
from backend.research_network import ResearchError

FIELD_SCOPE_TYPE = rules.FIELD_SCOPE_TYPE
IDENTITY_SCOPE_TYPE = rules.IDENTITY_SCOPE_TYPE
PARENTS_SCOPE_TYPE = rules.PARENTS_SCOPE_TYPE

# The arbiter of a race between writers of one name or alias key (registered
# in the package); a command that fails on it runs once more.
KEY_INDEX = "concept_keys_pkey"

_NOT_FOUND = ("not_found", "Concept not found.", 404)
_EXISTS = ("concept_exists", "A Concept with that name or alias already exists.", 409)
_ALIAS_CONFLICT = ("alias_conflict", "That search key already belongs to another Concept.", 409)
_PARENT_NOT_FOUND = ("parent_not_found", "Parent Concept not found.", 404)
_CYCLE = ("concept_cycle", "That parent would create a Concept hierarchy cycle.", 409)

_HIERARCHY_LOCK_SQL: LiteralString = (
    "SELECT pg_advisory_xact_lock(hashtextextended('prks.concept_parents:' || %(library_id)s, 0))"
)
# Identity writes take every key they write under this lock: a rename and an
# alias set write several keys, and two writers taking each other's keys in
# opposite orders would otherwise deadlock on the key index.
_KEYS_LOCK_SQL: LiteralString = (
    "SELECT pg_advisory_xact_lock(hashtextextended('prks.concept_keys:' || %(library_id)s, 0))"
)
# SQLite's LOWER() folds ASCII letters only; an ASCII-only translate() under
# COLLATE "C" is the same order.
_NAME_ORDER: LiteralString = (
    "translate(c.name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') COLLATE \"C\","
    " c.id COLLATE \"C\""
)
_ALIAS_ORDER: LiteralString = (
    "translate(a.alias, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') COLLATE \"C\","
    " a.alias COLLATE \"C\""
)
_COLUMNS: LiteralString = """
c.id, c.name, c.description,
to_char(c.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at,
to_char(c.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS updated_at
"""
_ROW_SQL: LiteralString = (
    "SELECT " + _COLUMNS + " FROM prks.concepts c WHERE c.library_id = %(library_id)s AND c.id = %(concept_id)s"
)
_ALL_SQL: LiteralString = (
    "SELECT " + _COLUMNS + " FROM prks.concepts c WHERE c.library_id = %(library_id)s ORDER BY " + _NAME_ORDER
)
_ALIASES_SQL: LiteralString = (
    "SELECT a.alias FROM prks.concept_aliases a"
    " WHERE a.library_id = %(library_id)s AND a.concept_id = %(concept_id)s ORDER BY " + _ALIAS_ORDER
)
_ALL_ALIASES_SQL: LiteralString = (
    "SELECT a.concept_id, a.alias FROM prks.concept_aliases a WHERE a.library_id = %(library_id)s"
    " ORDER BY " + _ALIAS_ORDER
)
_PARENTS_SQL: LiteralString = (
    "SELECT c.id, c.name FROM prks.concept_parents cp"
    " JOIN prks.concepts c ON c.library_id = cp.library_id AND c.id = cp.parent_concept_id"
    " WHERE cp.library_id = %(library_id)s AND cp.child_concept_id = %(concept_id)s ORDER BY " + _NAME_ORDER
)
_CHILDREN_SQL: LiteralString = (
    "SELECT c.id, c.name FROM prks.concept_parents cp"
    " JOIN prks.concepts c ON c.library_id = cp.library_id AND c.id = cp.child_concept_id"
    " WHERE cp.library_id = %(library_id)s AND cp.parent_concept_id = %(concept_id)s ORDER BY " + _NAME_ORDER
)
_ALL_PARENTS_SQL: LiteralString = (
    "SELECT cp.child_concept_id AS child_id, c.id, c.name FROM prks.concept_parents cp"
    " JOIN prks.concepts c ON c.library_id = cp.library_id AND c.id = cp.parent_concept_id"
    " WHERE cp.library_id = %(library_id)s ORDER BY " + _NAME_ORDER
)
_FIELD_SQL: LiteralString = """
SELECT description FROM prks.concepts WHERE library_id = %(library_id)s AND id = %(concept_id)s
"""
_LOCK_SQL: LiteralString = """
SELECT name FROM prks.concepts WHERE library_id = %(library_id)s AND id = %(concept_id)s FOR NO KEY UPDATE
"""
_LOCK_KEY_SQL: LiteralString = """
SELECT name FROM prks.concepts WHERE library_id = %(library_id)s AND id = %(concept_id)s FOR UPDATE
"""
_SHARE_SQL: LiteralString = """
SELECT id FROM prks.concepts WHERE library_id = %(library_id)s AND id = ANY(%(concept_ids)s)
ORDER BY id COLLATE "C" FOR KEY SHARE
"""
# The keys of names, in order, as the stored ones are computed. No table.
_KEYS_SQL: LiteralString = """
SELECT prks.identity_key(n.name) AS key FROM unnest(%(names)s::text[]) WITH ORDINALITY AS n(name, i)
ORDER BY n.i
"""
_OWNERS_SQL: LiteralString = """
SELECT key, concept_id FROM prks.concept_keys WHERE library_id = %(library_id)s AND key = ANY(%(keys)s)
"""
_RESOLVE_SQL: LiteralString = """
SELECT concept_id FROM prks.concept_keys
WHERE library_id = %(library_id)s AND key = prks.identity_key(%(name)s)
"""
_INSERT_SQL: LiteralString = """
INSERT INTO prks.concepts (library_id, id, name, description)
VALUES (%(library_id)s, %(concept_id)s, %(name)s, %(description)s)
ON CONFLICT DO NOTHING
RETURNING id
"""
_INSERT_NAME_KEY_SQL: LiteralString = """
INSERT INTO prks.concept_keys (library_id, key, concept_id)
SELECT library_id, name_key, id FROM prks.concepts WHERE library_id = %(library_id)s AND id = %(concept_id)s
ON CONFLICT (library_id, key) DO NOTHING
RETURNING key
"""
_DELETE_SQL: LiteralString = "DELETE FROM prks.concepts WHERE library_id = %(library_id)s AND id = %(concept_id)s"
_SET_DESCRIPTION_SQL: LiteralString = """
UPDATE prks.concepts SET description = %(value)s, updated_at = now()
WHERE library_id = %(library_id)s AND id = %(concept_id)s
"""
_RENAME_SQL: LiteralString = """
UPDATE prks.concepts SET name = %(name)s, updated_at = now()
WHERE library_id = %(library_id)s AND id = %(concept_id)s
"""
# The name row follows the name; a key another Concept holds fails on KEY_INDEX.
_MOVE_NAME_KEY_SQL: LiteralString = """
UPDATE prks.concept_keys k SET key = c.name_key
FROM prks.concepts c
WHERE k.library_id = %(library_id)s AND k.concept_id = %(concept_id)s AND k.alias_key IS NULL
  AND c.library_id = k.library_id AND c.id = k.concept_id AND k.key <> c.name_key
"""
# Their key rows follow them (ON DELETE CASCADE).
_DELETE_ALIASES_SQL: LiteralString = """
DELETE FROM prks.concept_aliases WHERE library_id = %(library_id)s AND concept_id = %(concept_id)s
"""
_INSERT_ALIASES_SQL: LiteralString = """
INSERT INTO prks.concept_aliases (library_id, concept_id, alias)
SELECT %(library_id)s, %(concept_id)s, a.alias FROM unnest(%(aliases)s::text[]) AS a(alias)
"""
_INSERT_ALIAS_KEYS_SQL: LiteralString = """
INSERT INTO prks.concept_keys (library_id, key, concept_id, alias_key)
SELECT library_id, alias_key, concept_id, alias_key FROM prks.concept_aliases
WHERE library_id = %(library_id)s AND concept_id = %(concept_id)s
"""
_PARENT_IDS_SQL: LiteralString = """
SELECT parent_concept_id AS id FROM prks.concept_parents
WHERE library_id = %(library_id)s AND child_concept_id = %(concept_id)s
"""
_CHILD_IDS_SQL: LiteralString = """
SELECT child_concept_id AS id FROM prks.concept_parents
WHERE library_id = %(library_id)s AND parent_concept_id = %(concept_id)s
"""
# Whether the Concept is one of the new parents or an ancestor of one.
_CYCLE_SQL: LiteralString = """
WITH RECURSIVE up(id) AS (
  SELECT unnest(%(parent_ids)s::text[])
  UNION
  SELECT cp.parent_concept_id FROM prks.concept_parents cp JOIN up ON cp.child_concept_id = up.id
  WHERE cp.library_id = %(library_id)s
)
SELECT 1 AS found FROM up WHERE id = %(concept_id)s LIMIT 1
"""
_UNLINK_PARENTS_SQL: LiteralString = """
DELETE FROM prks.concept_parents
WHERE library_id = %(library_id)s AND child_concept_id = %(concept_id)s
  AND parent_concept_id <> ALL(%(parent_ids)s::text[])
"""
_LINK_PARENTS_SQL: LiteralString = """
INSERT INTO prks.concept_parents (library_id, child_concept_id, parent_concept_id)
SELECT %(library_id)s, %(concept_id)s, p.id FROM unnest(%(parent_ids)s::text[]) AS p(id)
ON CONFLICT DO NOTHING
"""
_UNLINK_ALL_SQL: LiteralString = """
DELETE FROM prks.concept_parents
WHERE library_id = %(library_id)s AND (child_concept_id = %(concept_id)s OR parent_concept_id = %(concept_id)s)
"""
_RESEARCH_NOTES_SQL: LiteralString = """
SELECT body FROM prks.work_notes
WHERE library_id = %(library_id)s AND kind = 'research' AND strpos(body, '[[concept:') > 0
"""
_SHARE_REFERENCED_SQL: LiteralString = """
SELECT c.id FROM prks.concepts c
WHERE c.library_id = %(library_id)s AND c.id IN (
  SELECT k.concept_id FROM prks.concept_keys k
  WHERE k.library_id = %(library_id)s AND k.key IN (SELECT prks.identity_key(n) FROM unnest(%(names)s::text[]) AS n))
ORDER BY c.id COLLATE "C" FOR KEY SHARE
"""
_REFERENCED_SQL: LiteralString = """
SELECT 1 AS found FROM prks.concept_keys
WHERE library_id = %(library_id)s AND concept_id = %(concept_id)s
  AND key IN (SELECT prks.identity_key(n) FROM unnest(%(names)s::text[]) AS n)
LIMIT 1
"""


class ForeignConceptId(Exception):
    """The id is a Concept of another Library."""


def _refuse(rule: tuple[str, str, int]) -> ResearchError:
    return ResearchError(*rule)


def _usable(concept_id: str) -> bool:
    # PostgreSQL text cannot hold NUL; no stored id does.
    return bool(concept_id) and "\x00" not in concept_id


# -- locks and small reads ------------------------------------------------------


def lock_hierarchy(scope: LibraryScope) -> None:
    """Serialize this Library's parent-edge changes until the transaction ends."""
    scope.execute(_HIERARCHY_LOCK_SQL)


def lock_keys(scope: LibraryScope) -> None:
    """Hold the Library's Concept key lock until COMMIT (identity writes)."""
    scope.execute(_KEYS_LOCK_SQL)


def lock(scope: LibraryScope, concept_id: str, *, key: bool = False) -> Optional[str]:
    """Hold the Concept a command changes (``FOR UPDATE`` when ``key``); its name, or None."""
    if not _usable(concept_id):
        return None
    found = scope.execute(_LOCK_KEY_SQL if key else _LOCK_SQL, {"concept_id": concept_id}).fetchone()
    return None if found is None else str(found["name"])


def share(scope: LibraryScope, concept_ids: Sequence[str]) -> set[str]:
    """Hold the named Concepts against a delete until COMMIT; the ids found."""
    wanted = sorted({concept_id for concept_id in concept_ids if _usable(concept_id)})
    if not wanted:
        return set()
    return {str(found["id"]) for found in scope.execute(_SHARE_SQL, {"concept_ids": wanted})}


def concept_row(scope: LibraryScope, concept_id: str) -> Optional[dict[str, Any]]:
    """``{"id", "name", "description"}``: what ``CREATE_CONCEPT`` acknowledges."""
    found = _row(scope, concept_id)
    return None if found is None else {key: found[key] for key in ("id", "name", "description")}


def _row(scope: LibraryScope, concept_id: str) -> Optional[dict[str, Any]]:
    if not _usable(concept_id):
        return None
    found = scope.execute(_ROW_SQL, {"concept_id": concept_id}).fetchone()
    return None if found is None else dict(found)


def _keys(scope: LibraryScope, names: list[str]) -> list[str]:
    rows = scope.execute_unscoped(_KEYS_SQL, {"names": names}).fetchall()
    return [str(found["key"]) for found in rows]


def _owners(scope: LibraryScope, keys: list[str]) -> dict[str, str]:
    rows = scope.execute(_OWNERS_SQL, {"keys": keys}).fetchall()
    return {str(found["key"]): str(found["concept_id"]) for found in rows}


def resolve(scope: LibraryScope, name: str) -> Optional[str]:
    """The Concept whose name or alias has ``name``'s identity key, by the key index.

    ``resolve_concept_key``'s ``ok`` answer; a name that is not a legal
    reference (blank, over the length bound) resolves to nothing.
    """
    clean = network.canonical_concept_name(name)
    if not clean or len(clean) > network.CONCEPT_NAME_MAX or "\x00" in clean:
        return None
    found = scope.execute(_RESOLVE_SQL, {"name": clean}).fetchone()
    return None if found is None else str(found["concept_id"])


# -- the state a client measures against ----------------------------------------


def identity(scope: LibraryScope, concept_id: str) -> Optional[dict[str, Any]]:
    """``current_identity``: ``{name, aliases}``, aliases in ``get_concept``'s order; None when absent."""
    found = _row(scope, concept_id)
    if found is None:
        return None
    aliases = [str(alias["alias"]) for alias in scope.execute(_ALIASES_SQL, {"concept_id": concept_id})]
    return {"name": str(found["name"]), "aliases": aliases}


def parent_ids(scope: LibraryScope, concept_id: str) -> list[str]:
    """``current_parents``: the parent ids, by parent name."""
    return [str(parent["id"]) for parent in scope.execute(_PARENTS_SQL, {"concept_id": concept_id})]


def _revision(scope: LibraryScope, scope_type: str, scope_id: str) -> int:
    return revisions.read(scope, scope_type, [scope_id])[scope_id]


def sync_state(scope: LibraryScope, concept_id: str) -> Optional[dict[str, Any]]:
    """``get_concept_state_on_conn``: each value with its revision, read in one snapshot."""
    current = identity(scope, concept_id)
    if current is None:
        return None
    description = scope.execute(_FIELD_SQL, {"concept_id": concept_id}).fetchone()
    assert description is not None
    field_scopes = {field: rules.scope_key(concept_id, field) for field in rules.FIELDS}
    field_revisions = revisions.read(scope, FIELD_SCOPE_TYPE, list(field_scopes.values()))
    return {
        "concept_id": concept_id,
        "fields": {field: {"value": str(description[field]), "revision": field_revisions[scope_id]}
                   for field, scope_id in field_scopes.items()},
        "identity": current,
        "identity_revision": _revision(scope, IDENTITY_SCOPE_TYPE, concept_id),
        "parent_ids": parent_ids(scope, concept_id),
        "parents_revision": _revision(scope, PARENTS_SCOPE_TYPE, concept_id),
    }


# -- construction ---------------------------------------------------------------


def insert(scope: LibraryScope, concept_id: str, name: Any, description: Any = "") -> bool:
    """``insert_concept_on_conn``: the one construction primitive, no revision advanced.

    Returns False when the id is already a Concept of this Library (a
    concurrent create of it committed first); that Concept is then held
    ``FOR KEY SHARE`` until COMMIT. Raises ``ResearchError`` for an illegal
    name or definition and ``concept_exists`` for a key another Concept
    holds as name or alias, with nothing inserted, and
    :class:`ForeignConceptId` for an id of another Library.
    """
    clean = network._concept_name(name)
    text = network._optional_markdown(description, max_len=network.CONCEPT_DEFINITION_MAX)
    values = {"concept_id": concept_id, "name": clean, "description": text}
    # The id is decided before the name: an id of another Library is refused
    # as such (unledgered), never answered with this Library's name. Twice:
    # the winner of a same-id race can be deleted between its COMMIT and
    # this hold, and then the id is free again.
    for _ in range(2):
        if scope.execute(_INSERT_SQL, values).fetchone() is None:
            if share(scope, [concept_id]):
                return False
            continue
        if scope.execute(_INSERT_NAME_KEY_SQL, {"concept_id": concept_id}).fetchone() is not None:
            return True
        # Another Concept holds the key (a concurrent writer may have committed
        # it while this insert waited): give the Concept up.
        scope.execute(_DELETE_SQL, {"concept_id": concept_id})
        raise _refuse(_EXISTS)
    # The id is taken, but not by this Library.
    raise ForeignConceptId(concept_id)


def apply_create(scope: LibraryScope, concept_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """``CREATE_CONCEPT``'s ``apply_create``: SQLite's results.

    A create of an id that already exists acknowledges the stored Concept
    (``changed: false``), held until COMMIT. Raises
    :class:`ForeignConceptId` for an id of another Library.
    """
    result: dict[str, Any] = {"concept_id": concept_id}
    created = False
    if not share(scope, [concept_id]):
        try:
            created = insert(scope, concept_id, payload["name"], payload["description"])
        except ResearchError as error:
            return rule_result(error, result)
    result.update(code="ACKNOWLEDGED", changed=created, concept=concept_row(scope, concept_id))
    return 200, result


def rule_result(error: ResearchError, result: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """``_rule_result``: a rule's refusal as a terminal result."""
    result["code"] = rules._RULE_CODES.get(error.code, "ENTITY_NOT_FOUND")
    return rules._RULE_STATUS.get(error.code, 409), result


# -- the definition -------------------------------------------------------------


def set_field(scope: LibraryScope, concept_id: str, field: str, value: Any) -> tuple[bool, int]:
    """``set_field_on_conn``: write one field and advance its revision together."""
    if field not in rules.FIELD_SET:
        raise ResearchError("invalid_field", "Not an editable Concept field.")
    desired = network._optional_markdown(value, max_len=network.CONCEPT_DEFINITION_MAX)
    if lock(scope, concept_id) is None:
        raise _refuse(_NOT_FOUND)
    scope_id = rules.scope_key(concept_id, field)
    current = scope.execute(_FIELD_SQL, {"concept_id": concept_id}).fetchone()
    assert current is not None
    if str(current[field]) == desired:
        return False, _revision(scope, FIELD_SCOPE_TYPE, scope_id)
    revisions.lock(scope, FIELD_SCOPE_TYPE, scope_id)
    scope.execute(_SET_DESCRIPTION_SQL, {"concept_id": concept_id, "value": desired})
    return True, revisions.advance(scope, FIELD_SCOPE_TYPE, scope_id)


def apply_field_operation(scope: LibraryScope, concept_id: str, field: str, desired: str,
                          base_revision: int) -> tuple[int, dict[str, Any]]:
    """``SET_CONCEPT_FIELD``'s ``apply_field``: SQLite's decision table, result for result."""
    from backend import work_metadata_sync as meta

    result: dict[str, Any] = {"concept_id": concept_id, "field": field}
    if lock(scope, concept_id) is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    # Read, not ensured: the Concept lock serializes every writer of the
    # field, and a refused or converged operation must not create a scope.
    revision = _revision(scope, FIELD_SCOPE_TYPE, rules.scope_key(concept_id, field))
    row = scope.execute(_FIELD_SQL, {"concept_id": concept_id}).fetchone()
    assert row is not None
    current = str(row[field])
    if base_revision > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 400, meta.fit_terminal_result(result, current, desired)
    if base_revision < revision and current != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 409, meta.fit_terminal_result(result, current, desired)
    try:
        changed, after = set_field(scope, concept_id, field, desired)
    except ResearchError as error:
        return rule_result(error, result)
    result.update(code="ACKNOWLEDGED", server_revision=after, changed=changed, value_omitted=True)
    return 200, result


# -- identity: the name and the alias set, one decision ---------------------------


def set_identity(scope: LibraryScope, concept_id: str, name: Any, aliases: Sequence[Any]) -> tuple[bool, int]:
    """``set_identity_on_conn``: rewrite the name and the alias set as one decision.

    A rename keeps the old name as an alias unless the request already
    places its key. Refusals are SQLite's, decided by the key index: a new
    name another Concept holds is ``concept_exists``, an alias another
    Concept holds as name or alias ``alias_conflict``. A key a concurrent
    writer took after the check fails on :data:`KEY_INDEX`.
    """
    old_name = lock(scope, concept_id)
    if old_name is None:
        raise _refuse(_NOT_FOUND)
    new_name = network._concept_name(name)
    cleaned = [network._concept_name(raw) for raw in aliases]
    # Before the owners are read: the check and the writes are one step for
    # every other identity write of this Library.
    lock_keys(scope)
    keys = _keys(scope, [new_name, old_name, *cleaned])
    new_key, old_key = keys[0], keys[1]
    wanted: list[tuple[str, str]] = []
    seen = {new_key}
    for alias, key in zip(cleaned, keys[2:]):
        if key in seen:
            continue
        seen.add(key)
        wanted.append((alias, key))
    # A rename keeps the old name reachable, unless the caller has already said
    # where it belongs.
    if new_key != old_key and old_key not in seen:
        wanted.append((old_name, old_key))
    owners = _owners(scope, [new_key, *(key for _, key in wanted)])
    if new_key != old_key and owners.get(new_key, concept_id) != concept_id:
        raise _refuse(_EXISTS)
    if any(owners.get(key, concept_id) != concept_id for _, key in wanted):
        raise _refuse(_ALIAS_CONFLICT)
    before = identity(scope, concept_id)
    params = {"concept_id": concept_id}
    # The aliases go first: the new name may take the key one of them held.
    scope.execute(_DELETE_ALIASES_SQL, params)
    if new_name != old_name:
        scope.execute(_RENAME_SQL, {**params, "name": new_name})
        scope.execute(_MOVE_NAME_KEY_SQL, params)
    if wanted:
        scope.execute(_INSERT_ALIASES_SQL, {**params, "aliases": [alias for alias, _ in wanted]})
        scope.execute(_INSERT_ALIAS_KEYS_SQL, params)
    if identity(scope, concept_id) == before:
        return False, _revision(scope, IDENTITY_SCOPE_TYPE, concept_id)
    revisions.lock(scope, IDENTITY_SCOPE_TYPE, concept_id)
    return True, revisions.advance(scope, IDENTITY_SCOPE_TYPE, concept_id)


def apply_identity_operation(scope: LibraryScope, concept_id: str, payload: dict[str, Any],
                             base_revision: int) -> tuple[int, dict[str, Any]]:
    """``SET_CONCEPT_IDENTITY``'s ``apply_identity``: a stale base always conflicts."""
    result: dict[str, Any] = {"concept_id": concept_id}
    if lock(scope, concept_id) is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    current = identity(scope, concept_id)
    assert current is not None
    revision = _revision(scope, IDENTITY_SCOPE_TYPE, concept_id)
    if base_revision > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_value=current["name"], requested_value=payload["name"])
        return 400, result
    if base_revision < revision:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_value=current["name"], requested_value=payload["name"])
        return 409, result
    try:
        changed, after = set_identity(scope, concept_id, payload["name"], payload["aliases"])
    except ResearchError as error:
        return rule_result(error, result)
    stored = identity(scope, concept_id)
    assert stored is not None
    result.update(code="ACKNOWLEDGED", changed=changed, server_revision=after,
                  name=stored["name"], aliases=stored["aliases"])
    return 200, result


# -- the hierarchy ----------------------------------------------------------------


def set_parents(scope: LibraryScope, concept_id: str, raw_parent_ids: Sequence[Any]) -> tuple[bool, int]:
    """``set_parents_on_conn``: rewrite the whole parent set; acyclicity under the hierarchy lock.

    Advances the Concept's ``concept-parents`` and that of every parent
    added or removed: a parent's ``children`` are read from the same edges.
    """
    lock_hierarchy(scope)
    if lock(scope, concept_id) is None:
        raise _refuse(_NOT_FOUND)
    wanted: list[str] = []
    for raw in raw_parent_ids:
        parent_id = (raw or "").strip()
        if parent_id and parent_id not in wanted:
            wanted.append(parent_id)
    found = share(scope, [parent_id for parent_id in wanted if parent_id != concept_id])
    if concept_id in wanted:
        found.add(concept_id)
    if any(parent_id not in found for parent_id in wanted):
        raise _refuse(_PARENT_NOT_FOUND)
    if wanted and scope.execute(_CYCLE_SQL, {"concept_id": concept_id, "parent_ids": wanted}).fetchone():
        raise _refuse(_CYCLE)
    present = {str(row["id"]) for row in scope.execute(_PARENT_IDS_SQL, {"concept_id": concept_id})}
    moved = present ^ set(wanted)
    if not moved:
        return False, _revision(scope, PARENTS_SCOPE_TYPE, concept_id)
    locked = revisions.lock_many(scope, [(PARENTS_SCOPE_TYPE, other) for other in moved | {concept_id}])
    params = {"concept_id": concept_id, "parent_ids": wanted}
    scope.execute(_UNLINK_PARENTS_SQL, params)
    scope.execute(_LINK_PARENTS_SQL, params)
    for scope_type, scope_id in locked:
        revisions.advance(scope, scope_type, scope_id)
    return True, locked[(PARENTS_SCOPE_TYPE, concept_id)] + 1


def apply_parents_operation(scope: LibraryScope, concept_id: str, desired: list[str],
                            base_revision: int) -> tuple[int, dict[str, Any]]:
    """``SET_CONCEPT_PARENTS``'s ``apply_parents``: a stale base with the same set converges."""
    result: dict[str, Any] = {"concept_id": concept_id}
    lock_hierarchy(scope)
    if lock(scope, concept_id) is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    revision = _revision(scope, PARENTS_SCOPE_TYPE, concept_id)
    present = parent_ids(scope, concept_id)
    if base_revision > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_count=len(present), requested_count=len(set(desired)))
        return 400, result
    if base_revision < revision and set(desired) != set(present):
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_count=len(present), requested_count=len(set(desired)))
        return 409, result
    try:
        changed, after = set_parents(scope, concept_id, desired)
    except ResearchError as error:
        return rule_result(error, result)
    result.update(code="ACKNOWLEDGED", changed=changed, server_revision=after,
                  parent_ids=parent_ids(scope, concept_id))
    return 200, result


# -- delete -----------------------------------------------------------------------


def _reference_names(text: str) -> list[str]:
    return sorted({ref.name for ref in parse_research_markup(text).concept_refs})


def share_referenced(scope: LibraryScope, text: str) -> None:
    """Hold the Concepts a Research Note's markup names ``FOR KEY SHARE`` until COMMIT.

    Every writer of a Research Note calls it before the note is stored, so a
    Concept delete (``FOR UPDATE``) that overlaps the write waits for it and
    then finds the reference, or the write waits for the delete and the name
    resolves to nothing. The notes slice resolves (and creates) them.
    """
    names = _reference_names(text)
    if names:
        scope.execute(_SHARE_REFERENCED_SQL, {"names": names}).fetchall()


def _referenced_by_notes(scope: LibraryScope, concept_id: str) -> bool:
    """``_canonical_notes_reference_concept``: a Research Note names it by name or alias.

    Read from the notes themselves, never the derived mentions (§8.4). The
    references a note holds are resolved by the key index in one query.
    """
    names: set[str] = set()
    for note in scope.execute(_RESEARCH_NOTES_SQL):
        names.update(_reference_names(str(note["body"])))
    if not names:
        return False
    return scope.execute(_REFERENCED_SQL, {"concept_id": concept_id,
                                           "names": sorted(names)}).fetchone() is not None


def delete(scope: LibraryScope, concept_id: str) -> tuple[bool, Optional[str]]:
    """``delete_concept_on_conn``: remove the Concept, or refuse; ``(deleted, refusal_code)``.

    A Concept a Research Note still names is ``CONCEPT_IN_USE``. Otherwise
    its edges go first (its children are not reparented), every scope it
    owns and the ``concept-parents`` of each Concept on the other end of an
    edge advance once, and the Concept, its aliases and its keys go.
    """
    lock_hierarchy(scope)
    if lock(scope, concept_id, key=True) is None:
        return False, None
    if _referenced_by_notes(scope, concept_id):
        return False, "CONCEPT_IN_USE"
    params = {"concept_id": concept_id}
    related = {str(row["id"]) for query in (_PARENT_IDS_SQL, _CHILD_IDS_SQL)
               for row in scope.execute(query, params)}
    owned = [(FIELD_SCOPE_TYPE, rules.scope_key(concept_id, field)) for field in rules.FIELDS]
    owned += [(IDENTITY_SCOPE_TYPE, concept_id), (PARENTS_SCOPE_TYPE, concept_id)]
    # A scope no edit touched has no row and reads as revision 0, the base a
    # recreated Concept starts from: ensured here, it advances with the rest.
    locked = revisions.lock_many(scope, owned + [(PARENTS_SCOPE_TYPE, other) for other in related])
    scope.execute(_UNLINK_ALL_SQL, params)
    for scope_type, scope_id in locked:
        revisions.advance(scope, scope_type, scope_id)
    scope.execute(_DELETE_SQL, params)
    return True, None


# -- the HTTP reads and commands ---------------------------------------------------


def detail(scope: LibraryScope, concept_id: str) -> Optional[dict[str, Any]]:
    """``get_concept``: the row with its aliases, parents and children; None when absent."""
    item = _row(scope, concept_id)
    if item is None:
        return None
    params = {"concept_id": concept_id}
    item["aliases"] = [str(row["alias"]) for row in scope.execute(_ALIASES_SQL, params)]
    item["parents"] = [{"id": row["id"], "name": row["name"]} for row in scope.execute(_PARENTS_SQL, params)]
    item["children"] = [{"id": row["id"], "name": row["name"]} for row in scope.execute(_CHILDREN_SQL, params)]
    return item


def _detail_or_refuse(scope: LibraryScope, concept_id: str) -> dict[str, Any]:
    item = detail(scope, concept_id)
    if item is None:
        raise _refuse(_NOT_FOUND)
    return item


def catalog(scope: LibraryScope) -> list[dict[str, Any]]:
    """``list_concepts``: every Concept by name, with its child count, parents and aliases."""
    parents: dict[str, list[dict[str, Any]]] = {}
    child_counts: dict[str, int] = {}
    for row in scope.execute(_ALL_PARENTS_SQL):
        parents.setdefault(str(row["child_id"]), []).append({"id": row["id"], "name": row["name"]})
        child_counts[str(row["id"])] = child_counts.get(str(row["id"]), 0) + 1
    aliases: dict[str, list[str]] = {}
    for row in scope.execute(_ALL_ALIASES_SQL):
        aliases.setdefault(str(row["concept_id"]), []).append(str(row["alias"]))
    out = []
    for row in scope.execute(_ALL_SQL):
        item = dict(row)
        item["subconcept_count"] = child_counts.get(item["id"], 0)
        item["parents"] = parents.get(item["id"], [])
        item["aliases"] = aliases.get(item["id"], [])
        out.append(item)
    return out


def create(scope: LibraryScope, new_id: str, name: Any, description: Any = "") -> dict[str, Any]:
    """``create_concept``: construct under a fresh id; the detail."""
    if not insert(scope, new_id, name, description):  # pragma: no cover - a fresh id is never taken
        raise RuntimeError("a generated concept id is taken")
    return _detail_or_refuse(scope, new_id)


def update(scope: LibraryScope, concept_id: str, name: Any = None, description: Any = None) -> dict[str, Any]:
    """``update_concept``: a rename through the identity, the definition through its field."""
    cid = (concept_id or "").strip()
    if not cid:
        raise _refuse(_NOT_FOUND)
    if name is None and description is None:
        raise ResearchError("nothing_to_update", "Nothing to update.")
    current = identity(scope, cid) if lock(scope, cid) is not None else None
    if current is None:
        raise _refuse(_NOT_FOUND)
    if name is not None:
        # The alias set is carried through unchanged; the old name joins it
        # when the identity actually moves.
        set_identity(scope, cid, name, current["aliases"])
    if description is not None:
        set_field(scope, cid, "description", description)
    return _detail_or_refuse(scope, cid)


def replace_aliases(scope: LibraryScope, concept_id: str, aliases: Any) -> dict[str, Any]:
    """``replace_concept_aliases``: the alias set, through the identity."""
    cid = (concept_id or "").strip()
    if not isinstance(aliases, list):
        raise ResearchError("invalid_aliases", "Aliases must be a JSON array.")
    if any(not isinstance(raw, str) for raw in aliases):
        raise ResearchError("invalid_aliases", "Each alias must be a string.")
    current = identity(scope, cid) if lock(scope, cid) is not None else None
    if current is None:
        raise _refuse(_NOT_FOUND)
    set_identity(scope, cid, current["name"], aliases)
    return _detail_or_refuse(scope, cid)


def replace_parents(scope: LibraryScope, concept_id: str, raw_parent_ids: Any) -> dict[str, Any]:
    """``replace_concept_parents``: the whole parent set."""
    cid = (concept_id or "").strip()
    if not isinstance(raw_parent_ids, list):
        raise ResearchError("invalid_parents", "Parents must be a JSON array.")
    if any(not isinstance(raw, str) or not raw.strip() for raw in raw_parent_ids):
        raise ResearchError("invalid_parents", "Each parent id must be a string.")
    set_parents(scope, cid, raw_parent_ids)
    return _detail_or_refuse(scope, cid)


def remove(scope: LibraryScope, concept_id: str) -> None:
    """``delete_concept``: SQLite's refusals."""
    cid = (concept_id or "").strip()
    deleted, refusal = delete(scope, cid)
    if refusal:
        raise ResearchError(
            "concept_in_use",
            "This Concept is still referenced in research notes. Remove or replace those references"
            " before deleting it.",
            409,
        )
    if not deleted:
        raise _refuse(_NOT_FOUND)
