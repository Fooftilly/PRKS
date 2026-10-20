"""People reads on PostgreSQL (L-P-1 #632): the JSON of today's People endpoints.

Each function runs inside one
:func:`~backend.domain.identity.library_scope.library_query` snapshot (the
caller's), so a catalog and the memberships and counts on its rows come from
one consistent read. Orders are SQLite's: ``NOCASE`` (ASCII letters fold,
everything else by code point) is an ASCII-only ``translate()`` under
``COLLATE "C"``, and a tie SQLite leaves to chance is broken by id.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, LiteralString, Optional

from backend.domain.identity.library_scope import LibraryScope
from backend.domain.people import groups, persons

_NOCASE_NAME: LiteralString = groups.NAME_ORDER

_MEMBERSHIPS_SQL: LiteralString = """
SELECT m.person_id, g.id, g.name
FROM prks.person_group_members m
JOIN prks.person_groups g ON g.library_id = m.library_id AND g.id = m.group_id
WHERE m.library_id = %(library_id)s AND m.person_id = ANY(%(person_ids)s)
ORDER BY """ + _NOCASE_NAME + """, g.id COLLATE "C"
"""
_ALL_MEMBERSHIPS_SQL: LiteralString = """
SELECT m.person_id, g.id, g.name
FROM prks.person_group_members m
JOIN prks.person_groups g ON g.library_id = m.library_id AND g.id = m.group_id
WHERE m.library_id = %(library_id)s
ORDER BY """ + _NOCASE_NAME + """, g.id COLLATE "C"
"""
_GROUPS_FOR_PERSON_SQL: LiteralString = """
SELECT g.id, g.name, g.parent_id
FROM prks.person_groups g
JOIN prks.person_group_members m ON m.library_id = g.library_id AND m.group_id = g.id
WHERE g.library_id = %(library_id)s AND m.person_id = %(person_id)s
ORDER BY """ + _NOCASE_NAME + """, g.id COLLATE "C"
"""
# Each relationship is aggregated once and joined, never counted per row
# (scripts/check_correlated_catalog_counts.py).
_GROUP_CATALOG_SQL: LiteralString = """
SELECT g.id, g.name, g.parent_id, g.description,
       to_char(g.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at,
       to_char(g.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS updated_at,
       COALESCE(members.n, 0) AS member_count,
       COALESCE(children.n, 0) AS child_count
FROM prks.person_groups g
LEFT JOIN (SELECT group_id, count(*) AS n FROM prks.person_group_members
           WHERE library_id = %(library_id)s GROUP BY group_id) members ON members.group_id = g.id
LEFT JOIN (SELECT parent_id, count(*) AS n FROM prks.person_groups
           WHERE library_id = %(library_id)s AND parent_id IS NOT NULL GROUP BY parent_id) children
  ON children.parent_id = g.id
WHERE g.library_id = %(library_id)s
ORDER BY """ + _NOCASE_NAME + """, g.id COLLATE "C"
"""
_MEMBER_COUNT_SQL: LiteralString = """
SELECT count(*) AS n FROM prks.person_group_members
WHERE library_id = %(library_id)s AND group_id = %(group_id)s
"""
_CHILDREN_SQL: LiteralString = """
SELECT g.id, g.name, g.parent_id, g.description, COALESCE(members.n, 0) AS member_count
FROM prks.person_groups g
LEFT JOIN (SELECT group_id, count(*) AS n FROM prks.person_group_members
           WHERE library_id = %(library_id)s GROUP BY group_id) members ON members.group_id = g.id
WHERE g.library_id = %(library_id)s AND g.parent_id = %(group_id)s
ORDER BY """ + _NOCASE_NAME + """, g.id COLLATE "C"
"""
_MEMBER_IDS_SQL: LiteralString = """
SELECT p.id FROM prks.persons p
JOIN prks.person_group_members m ON m.library_id = p.library_id AND m.person_id = p.id
WHERE p.library_id = %(library_id)s AND m.group_id = %(group_id)s
ORDER BY translate(p.last_name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') COLLATE "C",
         translate(p.first_name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') COLLATE "C",
         p.id COLLATE "C"
"""


def _chips(scope: LibraryScope, person_ids: Optional[list[str]]) -> dict[str, list[dict[str, Any]]]:
    """Each Person's groups as ``{"id", "name"}``, by NOCASE name then id."""
    if person_ids is None:
        rows = scope.execute(_ALL_MEMBERSHIPS_SQL).fetchall()
    elif not person_ids:
        return {}
    else:
        rows = scope.execute(_MEMBERSHIPS_SQL, {"person_ids": person_ids}).fetchall()
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        out[str(row["person_id"])].append({"id": row["id"], "name": row["name"]})
    return out


def _decorate(scope: LibraryScope, rows: list[dict[str, Any]], *, every_person: bool) -> list[dict[str, Any]]:
    ids = [str(row["id"]) for row in rows]
    roles = persons.role_types(scope, ids)
    chips = _chips(scope, None if every_person else ids)
    for row in rows:
        row["assigned_roles"] = roles.get(row["id"], [])
        row["groups"] = chips.get(row["id"], [])
    return rows


def person_catalog(scope: LibraryScope) -> list[dict[str, Any]]:
    """``get_all_persons``: every profile with ``assigned_roles`` and ``groups``."""
    return _decorate(scope, persons.persons(scope), every_person=True)


def person_detail(scope: LibraryScope, person_id: str) -> Optional[tuple[dict[str, Any], int]]:
    """``get_person`` less ``works``, and the Person's role count (None when absent).

    ``works`` is the roles projection (L-P-1's roles slice); the caller
    refuses a Person who has roles rather than answer without them.
    """
    row = persons.person(scope, person_id)
    if row is None:
        return None
    row["groups"] = [dict(group) for group in
                     scope.execute(_GROUPS_FOR_PERSON_SQL, {"person_id": person_id}).fetchall()]
    return row, persons.role_count(scope, person_id)


def group_catalog(scope: LibraryScope) -> list[dict[str, Any]]:
    """``get_all_person_groups``: every group with ``member_count`` and ``child_count``."""
    return [dict(row) for row in scope.execute(_GROUP_CATALOG_SQL).fetchall()]


def group_detail(scope: LibraryScope, group_id: str) -> Optional[dict[str, Any]]:
    """``get_person_group``: the group, its children, its members and its parent."""
    out = groups.group(scope, group_id)
    if out is None:
        return None
    params = {"group_id": group_id}
    count = scope.execute(_MEMBER_COUNT_SQL, params).fetchone()
    out["member_count"] = 0 if count is None else int(count["n"])
    out["children"] = [dict(row) for row in scope.execute(_CHILDREN_SQL, params).fetchall()]
    member_ids = [str(row["id"]) for row in scope.execute(_MEMBER_IDS_SQL, params).fetchall()]
    by_id = persons.persons_by_id(scope, member_ids)
    out["members"] = _decorate(scope, [by_id[pid] for pid in member_ids if pid in by_id], every_person=False)
    parent = groups.group(scope, out["parent_id"]) if out["parent_id"] else None
    out["parent"] = None if parent is None else {"id": parent["id"], "name": parent["name"]}
    return out
