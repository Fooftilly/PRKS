"""Persons and their aliases on PostgreSQL (L-P-1 #632).

The rules are SQLite's, imported rather than copied: the profile vocabulary
and its creation contract (``backend/person_sync.py``) and the per-field
revisions with their decision table (``backend/person_metadata_sync.py``):
one scope per field (``person-field / ["P-…","field"]``), only a change
advances it, a stale base conflicts only when the values differ.

What moves is ``aliases``. SQLite keeps one comma-separated text column that
every reader splits; here each alias is a ``person_aliases`` row and the
profile value is the rows joined by ``", "``. So the canonical spelling of
an ``aliases`` value is its split, stripped, de-duplicated (one spelling per
identity key, the first kept) and re-joined form, and a value that only
re-spaces the same aliases is no change. The database decides which
spellings are one alias (``prks.identity_key``), so Python never has to
agree with it.

A Person delete is SERIALIZABLE (§7.1, "Protected Person delete"): a role
or a membership added concurrently fails one of the two transactions
instead of being left pointing at nothing. Roles are the next slice; the
protection already counts them.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, LiteralString, Optional

from backend import person_metadata_sync as rules
from backend import work_metadata_sync as meta
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.people import groups
from backend.domain.works import revisions
from backend.person_image import PersonImageUrlError
from backend.person_sync import FIELDS
from backend.work_role_sync import parse_person_aliases

SCOPE_TYPE = rules.SCOPE_TYPE

# The profile columns of prks.persons: every field except aliases.
_COLUMNS: tuple[LiteralString, ...] = (
    "first_name", "last_name", "about", "image_url", "link_wikipedia",
    "link_stanford_encyclopedia", "link_iep", "links_other", "birth_date", "death_date",
)

_PERSON_SQL: LiteralString = """
SELECT p.id, p.first_name, p.last_name, COALESCE(al.aliases, '') AS aliases, p.about, p.image_url,
       p.link_wikipedia, p.link_stanford_encyclopedia, p.link_iep, p.links_other,
       p.birth_date, p.death_date,
       to_char(p.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at,
       to_char(p.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS updated_at
FROM prks.persons p
LEFT JOIN (SELECT person_id, string_agg(alias, ', ' ORDER BY position) AS aliases
           FROM prks.person_aliases WHERE library_id = %(library_id)s GROUP BY person_id) al
  ON al.person_id = p.id
WHERE p.library_id = %(library_id)s
"""
_BY_ID_SQL: LiteralString = " AND p.id = %(person_id)s"
_BY_IDS_SQL: LiteralString = " AND p.id = ANY(%(person_ids)s)"
# SQLite orders `last_name, id` by BINARY: code points, which COLLATE "C"
# gives whatever the database's collation.
_BY_LAST_NAME_SQL: LiteralString = ' ORDER BY p.last_name COLLATE "C", p.id COLLATE "C"'

_INSERT_SQL: LiteralString = """
INSERT INTO prks.persons (library_id, id, first_name, last_name, about, image_url, link_wikipedia,
                          link_stanford_encyclopedia, link_iep, links_other, birth_date, death_date)
VALUES (%(library_id)s, %(person_id)s, %(first_name)s, %(last_name)s, %(about)s, %(image_url)s,
        %(link_wikipedia)s, %(link_stanford_encyclopedia)s, %(link_iep)s, %(links_other)s,
        %(birth_date)s, %(death_date)s)
"""
# The first spelling of each identity key, in input order. A pure
# expression: it reads no table, so it has no Library to filter by.
_CANONICAL_ALIASES_SQL: LiteralString = """
SELECT COALESCE(array_agg(alias ORDER BY ord), ARRAY[]::text[]) AS aliases
FROM (SELECT DISTINCT ON (prks.identity_key(alias)) alias, ord
      FROM unnest(%(aliases)s::text[]) WITH ORDINALITY AS given(alias, ord)
      ORDER BY prks.identity_key(alias), ord) firsts
"""
_DELETE_ALIASES_SQL: LiteralString = """
DELETE FROM prks.person_aliases WHERE library_id = %(library_id)s AND person_id = %(person_id)s
"""
_INSERT_ALIASES_SQL: LiteralString = """
INSERT INTO prks.person_aliases (library_id, person_id, position, alias)
SELECT %(library_id)s, %(person_id)s, ord - 1, alias
FROM unnest(%(aliases)s::text[]) WITH ORDINALITY AS given(alias, ord)
"""
_SET_SQL: dict[str, LiteralString] = {
    column: "UPDATE prks.persons SET " + column + " = %(value)s, updated_at = now()"
            " WHERE library_id = %(library_id)s AND id = %(person_id)s"
    for column in _COLUMNS
}
_TOUCH_SQL: LiteralString = (
    "UPDATE prks.persons SET updated_at = now() WHERE library_id = %(library_id)s AND id = %(person_id)s"
)
_LOCK_SQL: LiteralString = (
    "SELECT id FROM prks.persons WHERE library_id = %(library_id)s AND id = %(person_id)s"
    " FOR NO KEY UPDATE"
)
_LOCK_FOR_DELETE_SQL: LiteralString = (
    "SELECT id FROM prks.persons WHERE library_id = %(library_id)s AND id = %(person_id)s FOR UPDATE"
)
_ROLE_COUNT_SQL: LiteralString = """
SELECT count(*) AS n FROM prks.roles WHERE library_id = %(library_id)s AND person_id = %(person_id)s
"""
_ROLE_TYPES_SQL: LiteralString = """
SELECT person_id, array_agg(DISTINCT role_type ORDER BY role_type) AS role_types
FROM prks.roles WHERE library_id = %(library_id)s AND person_id = ANY(%(person_ids)s)
GROUP BY person_id
"""
_DELETE_SQL: LiteralString = (
    "DELETE FROM prks.persons WHERE library_id = %(library_id)s AND id = %(person_id)s"
)
# A deleted Person's field scopes stay, one revision on (§5.2), like a
# deleted Work's: a device holding a base from before the delete is stale.
_TOMBSTONE_FIELDS_SQL: LiteralString = """
UPDATE prks.entity_revisions SET revision = revision + 1, updated_at = now()
WHERE library_id = %(library_id)s AND scope_type = %(scope_type)s AND scope_id = ANY(%(scope_ids)s)
"""


def scope_id(person_id: str, field: str) -> str:
    return rules.scope_key(person_id, field)


def canonical_field(field: str, value: Any) -> str:
    """The value ``field`` stores for ``value``, or ``ValueError`` with SQLite's message.

    ``person_metadata_sync``'s rule (any string; an ``image_url`` the
    portrait normalizer accepts, normalized), plus what PostgreSQL text
    cannot hold: NUL. ``aliases`` is canonicalized further by the database
    (:func:`canonical_aliases`).
    """
    if field not in rules.SYNCED_FIELDS:
        raise ValueError("unsupported synchronized field: %s" % field)
    wire = "" if value is None else str(value)
    if "\x00" in wire or not rules.is_valid_field_value(field, wire):
        raise ValueError("%s is not a valid value for %s" % (value, field))
    try:
        return rules.canonical_wire(field, wire)
    except PersonImageUrlError:  # pragma: no cover - is_valid_field_value refused it above
        raise ValueError("%s is not a valid value for %s" % (value, field)) from None


def canonical_fields(fields: Mapping[str, Any]) -> dict[str, str]:
    """Every profile field, canonical; a missing field is ``""``."""
    return {name: canonical_field(name, fields.get(name)) for name in FIELDS}


def canonical_aliases(scope: LibraryScope, text: str) -> list[str]:
    """The aliases ``text`` names: split, stripped, one spelling per identity key."""
    parts = parse_person_aliases(text)
    if not parts:
        return []
    row = scope.execute_unscoped(_CANONICAL_ALIASES_SQL, {"aliases": parts}).fetchone()
    return [] if row is None else list(row["aliases"])


def _write_aliases(scope: LibraryScope, person_id: str, aliases: list[str]) -> None:
    params = {"person_id": person_id, "aliases": aliases}
    scope.execute(_DELETE_ALIASES_SQL, params)
    if aliases:
        scope.execute(_INSERT_ALIASES_SQL, params)


# -- reads ----------------------------------------------------------------------


def person(scope: LibraryScope, person_id: str) -> Optional[dict[str, Any]]:
    """The profile row (``SELECT * FROM persons`` on SQLite), or None."""
    row = scope.execute(_PERSON_SQL + _BY_ID_SQL, {"person_id": person_id}).fetchone()
    return None if row is None else dict(row)


def persons(scope: LibraryScope) -> list[dict[str, Any]]:
    """Every profile row, by last name then id."""
    return [dict(row) for row in scope.execute(_PERSON_SQL + _BY_LAST_NAME_SQL).fetchall()]


def persons_by_id(scope: LibraryScope, person_ids: list[str]) -> dict[str, dict[str, Any]]:
    """The named Persons of this Library, by id (absent ids are left out)."""
    if not person_ids:
        return {}
    rows = scope.execute(_PERSON_SQL + _BY_IDS_SQL, {"person_ids": person_ids}).fetchall()
    return {str(row["id"]): dict(row) for row in rows}


def role_types(scope: LibraryScope, person_ids: list[str]) -> dict[str, list[str]]:
    """Each Person's distinct role types, sorted (``assigned_roles``)."""
    if not person_ids:
        return {}
    rows = scope.execute(_ROLE_TYPES_SQL, {"person_ids": person_ids}).fetchall()
    return {str(row["person_id"]): list(row["role_types"]) for row in rows}


def role_count(scope: LibraryScope, person_id: str) -> int:
    row = scope.execute(_ROLE_COUNT_SQL, {"person_id": person_id}).fetchone()
    return 0 if row is None else int(row["n"])


def field_state(scope: LibraryScope, person_id: str) -> Optional[dict[str, Any]]:
    """``get_person_metadata_state``: every field's revision, or None when gone."""
    if person(scope, person_id) is None:
        return None
    fields = sorted(rules.SYNCED_FIELDS)
    by_scope = revisions.read(scope, SCOPE_TYPE, [scope_id(person_id, field) for field in fields])
    return {"person_id": person_id,
            "fields": {field: {"revision": by_scope[scope_id(person_id, field)]} for field in fields}}


# -- commands -------------------------------------------------------------------


def create_person(scope: LibraryScope, person_id: str, fields: Mapping[str, str]) -> None:
    """Insert a Person from :func:`canonical_fields`. Construction: no revision moves."""
    scope.execute(_INSERT_SQL, {"person_id": person_id, **{name: fields[name] for name in _COLUMNS}})
    _write_aliases(scope, person_id, canonical_aliases(scope, fields["aliases"]))


def lock(scope: LibraryScope, person_id: str) -> bool:
    """Lock the Person row for a change; False when there is none."""
    return scope.execute(_LOCK_SQL, {"person_id": person_id}).fetchone() is not None


def stored_value(scope: LibraryScope, person_id: str, field: str) -> Optional[str]:
    row = person(scope, person_id)
    return None if row is None else str(row[field])


def _desired(scope: LibraryScope, field: str, value: str) -> tuple[str, list[str]]:
    """The stored spelling of ``value`` (and, for aliases, its rows)."""
    if field != "aliases":
        return value, []
    aliases = canonical_aliases(scope, value)
    return ", ".join(aliases), aliases


def set_field(scope: LibraryScope, person_id: str, field: str, value: str) -> tuple[bool, int]:
    """``set_field_on_conn``: write one field and advance its revision together.

    ``value`` comes from :func:`canonical_field`; the caller holds the
    Person row lock (:func:`lock`). Returns ``(changed, revision_after)``;
    a no-op advances nothing.
    """
    revision = revisions.lock(scope, SCOPE_TYPE, scope_id(person_id, field))
    current = stored_value(scope, person_id, field)
    if current is None:
        return False, 0
    desired, aliases = _desired(scope, field, value)
    if current == desired:
        return False, revision
    params = {"person_id": person_id, "value": desired}
    if field == "aliases":
        _write_aliases(scope, person_id, aliases)
        scope.execute(_TOUCH_SQL, params)
    else:
        scope.execute(_SET_SQL[field], params)
    return True, revisions.advance(scope, SCOPE_TYPE, scope_id(person_id, field))


def apply_field_operation(scope: LibraryScope, person_id: str, field: str, value: str,
                          base_revision: int) -> tuple[int, dict[str, Any]]:
    """``SET_PERSON_METADATA_FIELD``: SQLite's decision table, result for result.

    ``value`` must already pass :func:`canonical_field`. The comparison is
    between stored spellings, so an ``aliases`` value that only re-spaces
    the current aliases has converged.
    """
    result: dict[str, Any] = {"person_id": person_id, "field": field}
    if not lock(scope, person_id):
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    revision = revisions.lock(scope, SCOPE_TYPE, scope_id(person_id, field))
    current = stored_value(scope, person_id, field) or ""
    desired, _aliases = _desired(scope, field, value)
    if base_revision > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      **rules.disagreement(current, desired))
        return 400, meta.fit_terminal_result(result, current, desired)
    if base_revision < revision and current != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      **rules.disagreement(current, desired))
        return 409, meta.fit_terminal_result(result, current, desired)
    changed, after = set_field(scope, person_id, field, value)
    result.update(code="ACKNOWLEDGED", server_revision=after, changed=changed, value_omitted=True)
    return 200, result


def update_profile(scope: LibraryScope, person_id: str, fields: Mapping[str, str],
                   group_ids: Optional[list[str]]) -> bool:
    """Fields (from :func:`canonical_field`) and, when given, the exact group set.

    One transaction: an unknown group refuses everything before any write.
    Returns False when the Person does not exist (nothing written). With
    ``group_ids`` the caller runs SERIALIZABLE (a membership insert is a
    peer-table write of the protected Person delete, §7.1).
    """
    if group_ids is None:
        if not lock(scope, person_id):
            return False
    else:
        current = groups.groups_of_person(scope, person_id)
        groups.lock_groups(scope, sorted(set(current) | set(group_ids)))
        if not lock(scope, person_id):
            return False
        missing = groups.missing_groups(scope, group_ids)
        if missing:
            raise ValueError(f"Unknown group id: {missing[0]}")
    for field in sorted(fields):
        set_field(scope, person_id, field, fields[field])
    if group_ids is not None:
        groups.replace_memberships(scope, person_id, group_ids)
    return True


def delete_person(scope: LibraryScope, person_id: str) -> tuple[bool, int]:
    """``delete_person_on_conn``: ``(deleted, linked_role_count)``.

    A Person with roles is not removed. Otherwise the memberships go and
    every pair scope of the Person advances once, present or removed earlier
    (``groups.drop_memberships``), the field scopes are tombstoned, and the
    aliases go with the row. Run SERIALIZABLE.
    """
    member_of = groups.groups_of_person(scope, person_id)
    groups.lock_groups(scope, member_of)
    if scope.execute(_LOCK_FOR_DELETE_SQL, {"person_id": person_id}).fetchone() is None:
        return False, 0
    linked = role_count(scope, person_id)
    if linked:
        return False, linked
    groups.drop_memberships(scope, person_id=person_id)
    scope.execute(_TOMBSTONE_FIELDS_SQL, {
        "scope_type": SCOPE_TYPE,
        "scope_ids": [scope_id(person_id, field) for field in sorted(rules.SYNCED_FIELDS)],
    })
    scope.execute(_DELETE_SQL, {"person_id": person_id})
    return True, 0
