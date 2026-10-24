"""Publishers and their aliases on PostgreSQL (L-P-1 #632).

SQLite's rules and messages, with names compared by identity key
(``prks.identity_key``, §8.1) instead of SQLite's ASCII-only ``LOWER``.

A publisher's name and every publisher's aliases are one namespace, held by
``publisher_keys`` (one row per name or alias, ``UNIQUE (library_id, key)``).
Each write inserts its key row with ``ON CONFLICT DO NOTHING``: a concurrent
writer of the same key makes this one wait until it commits, and then the
conflict names the owner, so the refusal is decided by the index and never
by a read that a race could make stale (§7.1). One rule is stricter than
SQLite's: a new publisher may not be named like another publisher's alias
(SQLite checks only names), because the namespace is shared.

Publishers own no revision scope, so the commands run READ COMMITTED and
an alias goes with its publisher by cascade.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, LiteralString, Optional

from backend.domain.identity.library_scope import LibraryScope

_OWNER_SQL: LiteralString = """
SELECT k.publisher_id, k.alias_id, p.name
FROM prks.publisher_keys k
JOIN prks.publishers p ON p.library_id = k.library_id AND p.id = k.publisher_id
WHERE k.library_id = %(library_id)s AND k.key = prks.identity_key(%(name)s)
"""
_INSERT_SQL: LiteralString = """
INSERT INTO prks.publishers (library_id, id, name) VALUES (%(library_id)s, %(publisher_id)s, %(name)s)
"""
_INSERT_NAME_KEY_SQL: LiteralString = """
INSERT INTO prks.publisher_keys (library_id, key, publisher_id)
SELECT library_id, name_key, id FROM prks.publishers
WHERE library_id = %(library_id)s AND id = %(publisher_id)s
ON CONFLICT (library_id, key) DO NOTHING
RETURNING key
"""
_INSERT_ALIAS_SQL: LiteralString = """
INSERT INTO prks.publisher_aliases (library_id, id, publisher_id, alias)
VALUES (%(library_id)s, %(alias_id)s, %(publisher_id)s, %(alias)s)
"""
_INSERT_ALIAS_KEY_SQL: LiteralString = """
INSERT INTO prks.publisher_keys (library_id, key, publisher_id, alias_id)
SELECT library_id, alias_key, publisher_id, id FROM prks.publisher_aliases
WHERE library_id = %(library_id)s AND id = %(alias_id)s
ON CONFLICT (library_id, key) DO NOTHING
RETURNING key
"""
_LOCK_SQL: LiteralString = """
SELECT name FROM prks.publishers WHERE library_id = %(library_id)s AND id = %(publisher_id)s
FOR NO KEY UPDATE
"""
_SAME_NAME_SQL: LiteralString = "SELECT prks.identity_key(%(a)s) = prks.identity_key(%(b)s) AS same"
_DELETE_ALIAS_ROW_SQL: LiteralString = """
DELETE FROM prks.publisher_aliases WHERE library_id = %(library_id)s AND id = %(alias_id)s
"""
_DELETE_PUBLISHER_ROW_SQL: LiteralString = """
DELETE FROM prks.publishers WHERE library_id = %(library_id)s AND id = %(publisher_id)s
"""
_DELETE_ALIAS_SQL: LiteralString = """
DELETE FROM prks.publisher_aliases
WHERE library_id = %(library_id)s AND publisher_id = %(publisher_id)s
  AND alias_key = prks.identity_key(%(alias)s)
"""
# SQLite orders by LOWER(), which folds ASCII letters only.
_PUBLISHERS_SQL: LiteralString = """
SELECT id, name FROM prks.publishers WHERE library_id = %(library_id)s
ORDER BY translate(name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') COLLATE "C",
         id COLLATE "C"
"""
_ALIASES_SQL: LiteralString = """
SELECT publisher_id, alias FROM prks.publisher_aliases WHERE library_id = %(library_id)s
ORDER BY translate(alias, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') COLLATE "C",
         id COLLATE "C"
"""
# Works per publisher: a Work's publisher is its primary Manifestation's, and
# it counts for the publisher whose name or alias has the same identity key.
# A Work has one label, so it matches at most one key of a publisher.
_WORK_COUNTS_SQL: LiteralString = """
SELECT k.publisher_id, sum(labels.n) AS n
FROM (SELECT prks.identity_key(m.publisher) AS key, count(*) AS n
      FROM prks.works w
      JOIN prks.manifestations m
        ON m.library_id = w.library_id AND m.id = w.primary_manifestation_id AND m.work_id = w.id
      WHERE w.library_id = %(library_id)s AND btrim(COALESCE(m.publisher, '')) <> ''
      GROUP BY 1) labels
JOIN prks.publisher_keys k ON k.library_id = %(library_id)s AND k.key = labels.key
GROUP BY k.publisher_id
"""


def _clean(value: Optional[str]) -> str:
    text = (value or "").strip()
    if "\x00" in text:
        raise ValueError("publisher names cannot contain NUL")
    return text


def _owner(scope: LibraryScope, name: str) -> Optional[dict[str, Any]]:
    row = scope.execute(_OWNER_SQL, {"name": name}).fetchone()
    return None if row is None else dict(row)


def create_publisher(scope: LibraryScope, publisher_id: str, name: Optional[str]) -> dict[str, Any]:
    """``add_publisher``: ``{"id", "name", "existed"}``; an existing name is that publisher."""
    raw = _clean(name)
    if not raw:
        raise ValueError("publisher name is empty")
    owner = _owner(scope, raw)
    if owner is None:
        scope.execute(_INSERT_SQL, {"publisher_id": publisher_id, "name": raw})
        if scope.execute(_INSERT_NAME_KEY_SQL, {"publisher_id": publisher_id}).fetchone() is not None:
            return {"id": publisher_id, "name": raw, "existed": False}
        # A concurrent writer committed the key first.
        scope.execute(_DELETE_PUBLISHER_ROW_SQL, {"publisher_id": publisher_id})
        owner = _owner(scope, raw)
        if owner is None:  # pragma: no cover - ON CONFLICT saw a committed key row
            raise ValueError("publisher name is taken")
    if owner["alias_id"] is not None:
        raise ValueError("publisher name matches another publisher's alias")
    return {"id": owner["publisher_id"], "name": owner["name"], "existed": True}


def add_alias(scope: LibraryScope, alias_id: str, publisher_id: str, alias: Optional[str]) -> None:
    """``add_publisher_alias``: SQLite's refusals, in its order; an alias it already has is a no-op."""
    al = _clean(alias)
    if not al:
        raise ValueError("alias is empty")
    row = scope.execute(_LOCK_SQL, {"publisher_id": publisher_id}).fetchone()
    if row is None:
        raise ValueError("publisher not found")
    same = scope.execute_unscoped(_SAME_NAME_SQL, {"a": str(row["name"]), "b": al}).fetchone()
    if same is not None and same["same"]:
        raise ValueError("alias matches canonical publisher name")
    owner = _owner(scope, al)
    if owner is None:
        params = {"alias_id": alias_id, "publisher_id": publisher_id, "alias": al}
        scope.execute(_INSERT_ALIAS_SQL, params)
        if scope.execute(_INSERT_ALIAS_KEY_SQL, params).fetchone() is not None:
            return
        scope.execute(_DELETE_ALIAS_ROW_SQL, params)
        owner = _owner(scope, al)
        if owner is None:  # pragma: no cover - ON CONFLICT saw a committed key row
            raise ValueError("alias already used")
    if owner["alias_id"] is None:
        raise ValueError("alias conflicts with another publisher name")
    if owner["publisher_id"] == publisher_id:
        return
    raise ValueError("alias already used")


def delete_alias(scope: LibraryScope, publisher_id: str, alias: Optional[str]) -> bool:
    """``delete_publisher_alias``: whether an alias with that identity key was removed."""
    al = _clean(alias)
    if not al:
        return False
    cursor = scope.execute(_DELETE_ALIAS_SQL, {"publisher_id": publisher_id, "alias": al})
    return cursor.rowcount > 0


def delete_publisher(scope: LibraryScope, publisher_id: str) -> None:
    """``delete_publisher``: the publisher, its aliases and its keys; absent is a no-op."""
    scope.execute(_DELETE_PUBLISHER_ROW_SQL, {"publisher_id": publisher_id})


def publishers_in_use(scope: LibraryScope) -> list[dict[str, Any]]:
    """``get_publishers_in_use``: every publisher with its aliases and Work count."""
    rows = scope.execute(_PUBLISHERS_SQL).fetchall()
    if not rows:
        return []
    aliases: dict[str, list[str]] = defaultdict(list)
    for alias_row in scope.execute(_ALIASES_SQL).fetchall():
        aliases[str(alias_row["publisher_id"])].append(str(alias_row["alias"]))
    counts = {str(row["publisher_id"]): int(row["n"]) for row in scope.execute(_WORK_COUNTS_SQL).fetchall()}
    return [{"id": row["id"], "name": str(row["name"]).strip(), "aliases": aliases.get(row["id"], []),
             "work_count": counts.get(str(row["id"]), 0)} for row in rows]
