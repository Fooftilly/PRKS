"""Work-Person roles on PostgreSQL (L-P-1 #632, PR 3).

A role links a Person to a Work as one of ``work_role_sync.ROLE_TYPES``,
optionally with a credit name ("the name on this file"). The rules are
``backend/work_role_sync.py``'s, imported rather than copied: the role
vocabulary, the 500-byte credit contract, desired-state semantics (adding a
present link or removing an absent one is convergence), credit promotion
into the Person's aliases, and the ``ADD/REMOVE/SET_WORK_PERSON_ROLE``
decision table.

**Scope** (``docs/work-identity-model.md`` §7). A role with no
``manifestation_id`` holds for the whole Work; its revision scope is
``work-person-role/[work, person, role]``, SQLite's. A role scoped to one
Manifestation of the Work has the scope
``manifestation-person-role/[manifestation, person, role]`` (§12.3). The
credits of a Manifestation M are the Work-scoped roles plus M's
(§7.5); the legacy Work JSON shows the credits of the primary
Manifestation. The ``PRKSDatabase`` methods name no Manifestation, so the
facade writes Work-scoped roles only, as SQLite does until the role-scope
migration (§7.3); the commands here take a ``manifestation_id`` for the
slices that will.

**Order** is ``ORDER BY order_index, seq``, SQLite's ``order_index, rowid``:
``seq`` is the row's identity in insertion order. A mutation appends after
every role of the Work. Construction keeps the caller's index, ties
included, so a tie lists in insertion order as on SQLite.

**Locks** (``docs/postgresql-architecture.md`` §8.3, fan-out inputs). A role
command locks the Work row (``FOR NO KEY UPDATE``: it touches
``updated_at``, and two appends on one Work queue), then the Person row,
then the revision scopes, sorted. Linking or unlinking takes the Person row
``FOR SHARE``, so a Person rename (``persons.lock``, ``FOR NO KEY UPDATE``)
and a link of the same Person queue in either order; a link whose credit
may become a new alias takes ``FOR NO KEY UPDATE`` instead, because it
writes the Person. A role add is a peer-table write of the protected Person
delete (§7.1), so its caller runs it SERIALIZABLE.

**Deletes.** A Work delete removes the Work's roles and advances every role
scope the Work or one of its Manifestations owns, the scopes of present
roles included even at revision 0 (:func:`drop_work_roles`). A Person with
roles is never deleted; a Person delete advances every role scope that
names the Person (:func:`tombstone_person_scopes`). A client-minted id can
be created again, and a base observed before a delete is then stale.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, LiteralString, Optional

from backend import work_role_sync as rules
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.people import persons
from backend.domain.works import revisions

SCOPE_TYPE = rules.SCOPE_TYPE
MANIFESTATION_SCOPE_TYPE = "manifestation-person-role"

_LOCK_WORK_SQL: LiteralString = (
    "SELECT id FROM prks.works WHERE library_id = %(library_id)s AND id = %(work_id)s FOR NO KEY UPDATE"
)
_WORK_EXISTS_SQL: LiteralString = (
    "SELECT 1 FROM prks.works WHERE library_id = %(library_id)s AND id = %(work_id)s"
)
_MANIFESTATION_OF_WORK_SQL: LiteralString = """
SELECT 1 FROM prks.manifestations
WHERE library_id = %(library_id)s AND id = %(manifestation_id)s AND work_id = %(work_id)s
"""
_SHARE_PERSON_SQL: LiteralString = """
SELECT first_name, last_name FROM prks.persons
WHERE library_id = %(library_id)s AND id = %(person_id)s FOR SHARE
"""
_PERSONS_EXIST_SQL: LiteralString = """
SELECT id FROM prks.persons WHERE library_id = %(library_id)s AND id = ANY(%(person_ids)s)
"""
_ROLE_SQL: LiteralString = """
SELECT credit_name FROM prks.roles
WHERE library_id = %(library_id)s AND work_id = %(work_id)s AND person_id = %(person_id)s
  AND role_type = %(role_type)s AND manifestation_id IS NOT DISTINCT FROM %(manifestation_id)s::text
"""
_HAS_ROLE_SQL: LiteralString = """
SELECT 1 FROM prks.roles
WHERE library_id = %(library_id)s AND work_id = %(work_id)s AND person_id = %(person_id)s
  AND role_type = %(role_type)s
LIMIT 1
"""
_NEXT_ORDER_SQL: LiteralString = """
SELECT COALESCE(max(order_index), -1) + 1 AS n FROM prks.roles
WHERE library_id = %(library_id)s AND work_id = %(work_id)s
"""
_INSERT_SQL: LiteralString = """
INSERT INTO prks.roles (library_id, work_id, person_id, role_type, order_index, credit_name,
                        manifestation_id)
VALUES (%(library_id)s, %(work_id)s, %(person_id)s, %(role_type)s, %(order_index)s, %(credit_name)s,
        %(manifestation_id)s)
"""
_UPDATE_CREDIT_SQL: LiteralString = """
UPDATE prks.roles SET credit_name = %(credit_name)s
WHERE library_id = %(library_id)s AND work_id = %(work_id)s AND person_id = %(person_id)s
  AND role_type = %(role_type)s AND manifestation_id IS NOT DISTINCT FROM %(manifestation_id)s::text
"""
_DELETE_SQL: LiteralString = """
DELETE FROM prks.roles
WHERE library_id = %(library_id)s AND work_id = %(work_id)s AND person_id = %(person_id)s
  AND role_type = %(role_type)s AND manifestation_id IS NOT DISTINCT FROM %(manifestation_id)s::text
"""
_TOUCH_WORK_SQL: LiteralString = (
    "UPDATE prks.works SET updated_at = now() WHERE library_id = %(library_id)s AND id = %(work_id)s"
)
_SAME_NAME_SQL: LiteralString = "SELECT prks.identity_key(%(a)s) = prks.identity_key(%(b)s) AS same"

# credits(primary M) of each named Work: its Work-scoped roles and the roles
# scoped to its primary Manifestation, in role order.
_CREDITS_SQL: LiteralString = """
SELECT r.work_id, r.person_id, r.role_type, r.order_index, r.credit_name, r.manifestation_id
FROM prks.roles r
JOIN prks.works w ON w.library_id = r.library_id AND w.id = r.work_id
WHERE r.library_id = %(library_id)s AND r.work_id = ANY(%(work_ids)s)
  AND (r.manifestation_id IS NULL OR r.manifestation_id = w.primary_manifestation_id)
ORDER BY r.work_id COLLATE "C", r.order_index, r.seq
"""
# One Person's credited roles, in SQLite's ``ORDER BY order_index, rowid``.
_PERSON_ROLES_SQL: LiteralString = """
SELECT r.work_id, r.person_id, r.role_type, r.order_index, r.credit_name, r.manifestation_id
FROM prks.roles r
JOIN prks.works w ON w.library_id = r.library_id AND w.id = r.work_id
WHERE r.library_id = %(library_id)s AND r.person_id = %(person_id)s
  AND (r.manifestation_id IS NULL OR r.manifestation_id = w.primary_manifestation_id)
ORDER BY r.order_index, r.seq
"""
_WORK_ROLE_KEYS_SQL: LiteralString = """
SELECT person_id, role_type, manifestation_id FROM prks.roles
WHERE library_id = %(library_id)s AND work_id = %(work_id)s
"""
_MANIFESTATIONS_OF_WORK_SQL: LiteralString = """
SELECT id FROM prks.manifestations WHERE library_id = %(library_id)s AND work_id = %(work_id)s
"""
_DELETE_WORK_ROLES_SQL: LiteralString = (
    "DELETE FROM prks.roles WHERE library_id = %(library_id)s AND work_id = %(work_id)s"
)
# Every Manifestation-scoped role scope of the named Manifestations: ids
# never contain a quote, so the prefix cannot reach another Manifestation.
_TOMBSTONE_MANIFESTATION_SCOPES_SQL: LiteralString = """
UPDATE prks.entity_revisions SET revision = revision + 1, updated_at = now()
WHERE library_id = %(library_id)s AND scope_type = %(scope_type)s
  AND left(scope_id, %(length)s) = ANY(%(prefixes)s)
"""
# Every role scope naming the Person: its id is the middle element.
_TOMBSTONE_PERSON_SCOPES_SQL: LiteralString = """
UPDATE prks.entity_revisions SET revision = revision + 1, updated_at = now()
WHERE library_id = %(library_id)s AND scope_type = ANY(%(scope_types)s)
  AND strpos(scope_id, %(needle)s) > 0
"""
_STATE_SCOPES_SQL: LiteralString = """
SELECT scope_id, revision FROM prks.entity_revisions
WHERE library_id = %(library_id)s AND scope_type = %(scope_type)s
  AND left(scope_id, %(length)s) = %(prefix)s
"""
_PRESENT_WORK_SCOPED_SQL: LiteralString = """
SELECT person_id, role_type FROM prks.roles
WHERE library_id = %(library_id)s AND work_id = %(work_id)s AND manifestation_id IS NULL
"""


class RoleTargetMissing(ValueError):
    """The Work, the Person or the Manifestation of a role does not exist here."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Role:
    """One role's identity: ``manifestation_id`` None is Work scope."""

    work_id: str
    person_id: str
    role_type: str
    manifestation_id: Optional[str] = None

    def revision_scope(self) -> tuple[str, str]:
        if self.manifestation_id is None:
            return SCOPE_TYPE, rules.scope_key(self.work_id, self.person_id, self.role_type)
        return MANIFESTATION_SCOPE_TYPE, rules.scope_key(self.manifestation_id, self.person_id,
                                                         self.role_type)

    def params(self) -> dict[str, Any]:
        return {"work_id": self.work_id, "person_id": self.person_id, "role_type": self.role_type,
                "manifestation_id": self.manifestation_id}


def _scope_prefix(owner_id: str) -> str:
    return '["' + owner_id + '",'


# -- reads ----------------------------------------------------------------------


def state(scope: LibraryScope, role: Role) -> Optional[str]:
    """``work_role_sync.current_state``: None when absent, else the credit ("" for none)."""
    row = scope.execute(_ROLE_SQL, role.params()).fetchone()
    return None if row is None else rules.canonical_credit_name(row["credit_name"])


def has_role(scope: LibraryScope, work_id: str, person_id: str, role_type: str) -> bool:
    """Whether the Person holds the role on the Work in any scope (``has_work_role``)."""
    params = {"work_id": work_id, "person_id": person_id, "role_type": role_type}
    return scope.execute(_HAS_ROLE_SQL, params).fetchone() is not None


def next_order_index(scope: LibraryScope, work_id: str) -> int:
    row = scope.execute(_NEXT_ORDER_SQL, {"work_id": work_id}).fetchone()
    return 0 if row is None else int(row["n"])


def missing_persons(scope: LibraryScope, person_ids: Iterable[str]) -> list[str]:
    """The given Person ids with no row in this Library, once each, in the order given."""
    unique = list(dict.fromkeys(person_ids))
    if not unique:
        return []
    rows = scope.execute(_PERSONS_EXIST_SQL, {"person_ids": unique}).fetchall()
    present = {str(row["id"]) for row in rows}
    return [pid for pid in unique if pid not in present]


def _credit_rows(scope: LibraryScope, query: LiteralString,
                 params: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [dict(row) for row in scope.execute(query, params).fetchall()]
    people = persons.persons_by_id(scope, sorted({str(row["person_id"]) for row in rows}))
    out = []
    for row in rows:
        person = people.get(str(row["person_id"]))
        if person is not None:
            out.append({**row, "person": person})
    return out


def credits_by_work(scope: LibraryScope, work_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """``credits(primary M)`` of each named Work, in role order (absent Works map to [])."""
    if not work_ids:
        return {}
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _credit_rows(scope, _CREDITS_SQL, {"work_ids": work_ids}):
        out[str(row["work_id"])].append(row)
    return out


def attach_credits(scope: LibraryScope, cores: list[dict[str, Any]]) -> None:
    """Set ``credits`` on each Work core (``backend.domain.works.projection``)."""
    by_work = credits_by_work(scope, [str(core["id"]) for core in cores])
    for core in cores:
        core["credits"] = by_work.get(str(core["id"]), [])


def person_credits(scope: LibraryScope, person_id: str) -> list[dict[str, Any]]:
    """The Person's credited roles across Works, in SQLite's order."""
    return _credit_rows(scope, _PERSON_ROLES_SQL, {"person_id": person_id})


def work_roles(scope: LibraryScope, work_id: str) -> list[dict[str, Any]]:
    """``get_work_roles``: each credit of the primary Manifestation as its Person's row plus the role."""
    from backend.domain.works import projection

    return [projection.role_row(credit) for credit in credits_by_work(scope, [work_id]).get(work_id, [])]


def roles_state(scope: LibraryScope, work_id: str) -> Optional[dict[str, Any]]:
    """``work_role_sync.get_roles_state``: the Work-scoped role revisions, tombstones included."""
    if scope.execute(_WORK_EXISTS_SQL, {"work_id": work_id}).fetchone() is None:
        return None
    prefix = _scope_prefix(work_id)
    rows = scope.execute(_STATE_SCOPES_SQL, {"scope_type": SCOPE_TYPE, "prefix": prefix,
                                             "length": len(prefix)}).fetchall()
    present = {(str(row["person_id"]), str(row["role_type"]))
               for row in scope.execute(_PRESENT_WORK_SCOPED_SQL, {"work_id": work_id})}
    scopes: list[dict[str, Any]] = []
    for row in rows:
        try:
            scoped_work, person_id, role_type = json.loads(str(row["scope_id"]))
        except (ValueError, TypeError):
            continue
        if scoped_work != work_id:
            continue
        scopes.append({"person_id": person_id, "role_type": role_type,
                       "revision": int(row["revision"]),
                       "present": (person_id, role_type) in present})
    known = {(s["person_id"], s["role_type"]) for s in scopes}
    for person_id, role_type in sorted(present - known):
        scopes.append({"person_id": person_id, "role_type": role_type, "revision": 0, "present": True})
    scopes.sort(key=lambda s: (s["person_id"], s["role_type"]))
    return {"work_id": work_id, "scopes": scopes}


# -- locks ----------------------------------------------------------------------


def lock_work(scope: LibraryScope, work_id: str) -> bool:
    """Lock the Work row for a role change; False when there is none."""
    return scope.execute(_LOCK_WORK_SQL, {"work_id": work_id}).fetchone() is not None


def lock_person(scope: LibraryScope, person_id: str, *, writes_person: bool) -> Optional[dict[str, Any]]:
    """The Person's names, its row locked as §8.3 asks; None when there is none."""
    if writes_person:
        if not persons.lock(scope, person_id):
            return None
        row = persons.person(scope, person_id)
        return None if row is None else {"first_name": row["first_name"], "last_name": row["last_name"]}
    row = scope.execute(_SHARE_PERSON_SQL, {"person_id": person_id}).fetchone()
    return None if row is None else dict(row)


def lock_targets(scope: LibraryScope, role: Role, *, writes_person: bool) -> dict[str, Any]:
    """Lock the Work, then the Person; raise :class:`RoleTargetMissing` for what is not there."""
    if not lock_work(scope, role.work_id):
        raise RoleTargetMissing("ENTITY_NOT_FOUND", "Work not found.")
    if role.manifestation_id is not None and scope.execute(
            _MANIFESTATION_OF_WORK_SQL, {"work_id": role.work_id,
                                         "manifestation_id": role.manifestation_id}).fetchone() is None:
        raise RoleTargetMissing("MANIFESTATION_NOT_FOUND", "Manifestation not found on this Work.")
    person = lock_person(scope, role.person_id, writes_person=writes_person)
    if person is None:
        raise RoleTargetMissing("PERSON_NOT_FOUND", "Person not found.")
    return person


# -- credit promotion -----------------------------------------------------------


def _same_name(scope: LibraryScope, a: str, b: str) -> bool:
    row = scope.execute_unscoped(_SAME_NAME_SQL, {"a": a, "b": b}).fetchone()
    return bool(row and row["same"])


def promoted_aliases(scope: LibraryScope, person_id: str, credit_name: str) -> Optional[str]:
    """The Person's aliases with ``credit_name`` added, or None when that changes nothing.

    ``work_role_sync._append_person_alias``'s rule, with names compared by
    identity key as Person aliases are here: a credit that is not one
    lossless alias, or that already is the Person's name or one of its
    aliases, is not promoted. The caller holds the Person row lock.
    """
    alias = rules.canonical_credit_name(credit_name)
    if not rules.credit_promotable_as_alias(alias):
        return None
    row = persons.person(scope, person_id)
    if row is None:
        return None
    canonical = ("%s %s" % (str(row["first_name"]).strip(), str(row["last_name"]).strip())).strip()
    if canonical and _same_name(scope, canonical, alias):
        return None
    current = str(row["aliases"])
    joined = current + ", " + alias if current else alias
    if persons.canonical_aliases(scope, joined) == persons.canonical_aliases(scope, current):
        return None
    return joined


def _promote(scope: LibraryScope, person_id: str, aliases: Optional[str]) -> None:
    if aliases is not None:
        persons.set_field(scope, person_id, "aliases", persons.canonical_field("aliases", aliases))


# -- commands -------------------------------------------------------------------


def _write(scope: LibraryScope, role: Role, existing: Optional[str], desired: Optional[str],
           aliases: Optional[str]) -> int:
    """Change one locked role from ``existing`` to ``desired``; returns its new revision."""
    scope_type, scope_id = role.revision_scope()
    keys = [(scope_type, scope_id)]
    if aliases is not None:
        keys.append((persons.SCOPE_TYPE, persons.scope_id(role.person_id, "aliases")))
    revisions.lock_many(scope, keys)
    params = {**role.params(), "credit_name": desired or None}
    if desired is None:
        scope.execute(_DELETE_SQL, params)
    elif existing is None:
        scope.execute(_INSERT_SQL, {**params, "order_index": next_order_index(scope, role.work_id)})
    else:
        scope.execute(_UPDATE_CREDIT_SQL, params)
    _promote(scope, role.person_id, aliases)
    scope.execute(_TOUCH_WORK_SQL, {"work_id": role.work_id})
    return revisions.advance(scope, scope_type, scope_id)


def _desired(present: bool, credit_name: str) -> Optional[str]:
    if not present:
        return None
    rules.validate_credit_name(credit_name)
    return rules.canonical_credit_name(credit_name)


def set_role(scope: LibraryScope, role: Role, present: bool, credit_name: str = "") -> bool:
    """``work_role_sync.set_role_state``: make the role present (with the credit) or absent.

    A mutation of an existing Work: a change advances the role's revision
    scope and touches the Work; a link appends after the Work's roles and
    promotes its credit into the Person's aliases. A no-op writes nothing,
    and creates no revision row. A missing Work or Person raises
    :class:`RoleTargetMissing` for a link and is a no-op for an unlink.
    """
    rules.validate_role_type(role.role_type)
    desired = _desired(present, credit_name)
    promotes = desired is not None and rules.credit_promotable_as_alias(desired)
    try:
        lock_targets(scope, role, writes_person=promotes)
    except RoleTargetMissing:
        if present:
            raise
        return False
    existing = state(scope, role)
    if existing == desired:
        return False
    aliases = promoted_aliases(scope, role.person_id, desired) if desired else None
    _write(scope, role, existing, desired, aliases)
    return True


def apply_role_operation(scope: LibraryScope, role: Role, operation: str, credit_name: Optional[str],
                         base_revision: int) -> tuple[int, dict[str, Any]]:
    """``ADD/REMOVE/SET_WORK_PERSON_ROLE``: SQLite's decision table, result for result.

    The envelope has passed ``work_role_sync.validate``. The caller runs an
    add SERIALIZABLE.
    """
    result: dict[str, Any] = {"work_id": role.work_id, "person_id": role.person_id,
                              "role_type": role.role_type}
    carries = operation in rules.CARRIES_CREDIT
    desired = rules.canonical_credit_name(credit_name) if carries else None
    promotes = desired is not None and rules.credit_promotable_as_alias(desired)
    try:
        person = lock_targets(scope, role, writes_person=promotes)
    except RoleTargetMissing as missing:
        result["code"] = "ENTITY_NOT_FOUND" if missing.code == "MANIFESTATION_NOT_FOUND" else missing.code
        return 404, result
    scope_type, scope_id = role.revision_scope()
    revision = revisions.read(scope, scope_type, [scope_id])[scope_id]
    existing = state(scope, role)
    if base_revision > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current=_reported(existing), requested=_reported(desired))
        return 400, result
    if operation == "SET_WORK_PERSON_ROLE_CREDIT" and existing is None:
        result.update(code="ROLE_NOT_PRESENT", current_revision=revision)
        return 409, result
    if base_revision < revision and existing != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current=_reported(existing), requested=_reported(desired))
        return 409, result
    aliases_scope = persons.scope_id(role.person_id, "aliases")
    aliases_before = revisions.read(scope, persons.SCOPE_TYPE, [aliases_scope])[aliases_scope]
    changed = existing != desired
    if changed:
        aliases = promoted_aliases(scope, role.person_id, desired) if desired else None
        _write(scope, role, existing, desired, aliases)
    result.update(code="ACKNOWLEDGED", changed=changed, server_revision=revision + int(changed),
                  **_reported(desired))
    result["first_name"] = person["first_name"] or ""
    result["last_name"] = person["last_name"] or ""
    aliases_after = revisions.read(scope, persons.SCOPE_TYPE, [aliases_scope])[aliases_scope]
    if aliases_after != aliases_before:
        result["aliases_revision"] = aliases_after
    return 200, result


def _reported(value: Optional[str]) -> dict[str, Any]:
    return {"present": value is not None, "credit_name": value or ""}


@dataclass(frozen=True)
class InitialRole:
    """A role a new Work is born with, at the caller's ``order_index``."""

    person_id: str
    role_type: str
    order_index: int
    credit_name: str = ""


def insert_initial_roles(scope: LibraryScope, work_id: str, wanted: list[InitialRole], *,
                         skip_invalid: bool) -> int:
    """CONSTRUCTION: roles a new Work is born with. Creates no role revision.

    Every named Person is locked first (``FOR SHARE``, or ``FOR NO KEY
    UPDATE`` when a credit may become an alias), in id order, after the
    Work; if any is missing nothing is written and
    :class:`~backend.db_manager.MissingPersonError` is raised. A role the
    Work already has, and an invalid role or credit, are skipped when
    ``skip_invalid`` (``insert_initial_roles``) and raise ``ValueError``
    otherwise (``insert_initial_role``). Returns how many roles were inserted.
    """
    from backend.db_manager import MissingPersonError

    if not wanted:
        return 0
    if not skip_invalid:
        for item in wanted:
            rules.validate_role_type(item.role_type)
            rules.validate_credit_name(item.credit_name)
    if not lock_work(scope, work_id):
        raise RoleTargetMissing("ENTITY_NOT_FOUND", "Work not found.")
    writers = {item.person_id for item in wanted if rules.credit_promotable_as_alias(item.credit_name)}
    # One pass in id order, each row in the mode it needs, so two
    # constructions naming the same people queue instead of deadlocking.
    for person_id in sorted({item.person_id for item in wanted}):
        if lock_person(scope, person_id, writes_person=person_id in writers) is None:
            raise MissingPersonError("A person on this file no longer exists.")
    inserted = 0
    for item in wanted:
        if item.role_type not in rules.ROLE_TYPE_SET:
            continue
        try:
            rules.validate_credit_name(item.credit_name)
        except ValueError:
            continue
        if has_role(scope, work_id, item.person_id, item.role_type):
            if not skip_invalid:
                # The singular constructor reports it, as SQLite's unique key does.
                raise ValueError(f"This person is already linked to this file as {item.role_type}.")
            continue
        credit = rules.canonical_credit_name(item.credit_name)
        scope.execute(_INSERT_SQL, {"work_id": work_id, "person_id": item.person_id,
                                    "role_type": item.role_type, "manifestation_id": None,
                                    "order_index": int(item.order_index),
                                    "credit_name": credit or None})
        if credit:
            _promote(scope, item.person_id, promoted_aliases(scope, item.person_id, credit))
        inserted += 1
    return inserted


# -- deletes --------------------------------------------------------------------


def drop_work_roles(scope: LibraryScope, work_id: str) -> None:
    """Delete the Work's roles and tombstone the role scopes of its Manifestations.

    The caller holds the Work row lock and then tombstones every scope whose
    id starts with the Work's (``revisions.tombstone_work_scopes``), which
    takes the Work-scoped role scopes. A present role's scope is ensured
    first, so one still at revision 0 (a constructed role) advances too, and
    every Manifestation-scoped role scope of the Work's Manifestations
    advances here, the removed ones included. Each advances exactly once.
    """
    present = [Role(work_id, str(row["person_id"]), str(row["role_type"]),
                    None if row["manifestation_id"] is None else str(row["manifestation_id"]))
               for row in scope.execute(_WORK_ROLE_KEYS_SQL, {"work_id": work_id})]
    revisions.lock_many(scope, [role.revision_scope() for role in present])
    scope.execute(_DELETE_WORK_ROLES_SQL, {"work_id": work_id})
    manifestations = [str(row["id"]) for row in
                      scope.execute(_MANIFESTATIONS_OF_WORK_SQL, {"work_id": work_id})]
    prefixes = [_scope_prefix(mid) for mid in manifestations]
    for length in sorted({len(prefix) for prefix in prefixes}):
        scope.execute(_TOMBSTONE_MANIFESTATION_SCOPES_SQL, {
            "scope_type": MANIFESTATION_SCOPE_TYPE, "length": length,
            "prefixes": [prefix for prefix in prefixes if len(prefix) == length],
        })


def tombstone_person_scopes(scope: LibraryScope, person_id: str) -> None:
    """Advance every role scope that names the Person (it holds no role: the delete refused one)."""
    scope.execute(_TOMBSTONE_PERSON_SCOPES_SQL, {
        "scope_types": [SCOPE_TYPE, MANIFESTATION_SCOPE_TYPE],
        "needle": ',"' + person_id + '",',
    })
