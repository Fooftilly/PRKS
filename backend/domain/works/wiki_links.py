"""``[[wiki link]]`` resolution on PostgreSQL (L-W-2 #631).

``PRKSDatabase.resolve_wiki_links`` by the same rules, rendered by the shared
``backend.db_manager.render_wiki_links``, in the caller's one ``library_query``
snapshot:

- each distinct target, trimmed at its ends and matched exactly
  (case-sensitive), is looked up once;
- a **Work** matches by id or by its title (``prks.works.title``, the
  canonical title SQLite's ``works.title`` holds), and a Work match wins over
  a Person match;
- a **Person** matches by id or by last name; the link reads
  ``first_name || ' ' || last_name``;
- when several rows match one target, the **first** wins: SQLite takes the
  lowest ``rowid`` (insertion order), PostgreSQL the earliest ``created_at``,
  then ``id`` in byte order (``COLLATE "C"``). The two agree for rows
  created by separate transactions in the order they committed; rows created
  in one transaction share ``created_at`` and fall back to their ids, whose
  order is not their insertion order.

A target holding NUL, which PostgreSQL text cannot hold, matches nothing and
is not sent.

Lookups go in batches of at most ``PRKSDatabase._WIKI_LINK_LOOKUP_CHUNK``
targets, one statement per batch and table, as bound array parameters; the
Person lookup only sees the targets no Work claimed. Each statement has one
branch per way a row can match, so each probes an index (the Library's id
keys, the ``works_title`` hash index, ``persons_library_last_name``) rather
than scanning the Library, and ``DISTINCT ON`` keeps one row per target. Every statement filters by the scope's Library, so nothing of
another Library is found or hinted at.
"""

from __future__ import annotations

from typing import Any, LiteralString

from backend.domain.identity.library_scope import LibraryScope

# One branch per way a row can match, so each probes its own index; DISTINCT ON
# then keeps the first row per target.
_WORKS_SQL: LiteralString = """
SELECT DISTINCT ON (target) target, id, name FROM (
  SELECT id AS target, id, title AS name, created_at FROM prks.works
  WHERE library_id = %(library_id)s AND id = ANY(%(targets)s::text[])
  UNION ALL
  SELECT title, id, title, created_at FROM prks.works
  WHERE library_id = %(library_id)s AND title = ANY(%(targets)s::text[])
) AS matched
ORDER BY target, created_at, id COLLATE "C"
"""
_PERSONS_SQL: LiteralString = """
SELECT DISTINCT ON (target) target, id, first_name || ' ' || last_name AS name FROM (
  SELECT id AS target, id, first_name, last_name, created_at FROM prks.persons
  WHERE library_id = %(library_id)s AND id = ANY(%(targets)s::text[])
  UNION ALL
  SELECT last_name, id, first_name, last_name, created_at FROM prks.persons
  WHERE library_id = %(library_id)s AND last_name = ANY(%(targets)s::text[])
) AS matched
ORDER BY target, created_at, id COLLATE "C"
"""


def _first_matches(scope: LibraryScope, sql: LiteralString, targets: list[str]) -> dict[str, tuple[Any, Any]]:
    from backend.db_manager import PRKSDatabase

    chunk = PRKSDatabase._WIKI_LINK_LOOKUP_CHUNK
    found: dict[str, tuple[Any, Any]] = {}
    for start in range(0, len(targets), chunk):
        for row in scope.execute(sql, {"targets": targets[start:start + chunk]}).fetchall():
            found[row["target"]] = (row["id"], row["name"])
    return found


def resolve_targets(scope: LibraryScope, targets: list[str]) -> dict[str, tuple[str, Any, Any]]:
    """``{target: (kind, id, name)}`` for the targets this Library resolves."""
    targets = [target for target in targets if "\x00" not in target]
    resolved = {target: ("works", *hit) for target, hit in _first_matches(scope, _WORKS_SQL, targets).items()}
    rest = [target for target in targets if target not in resolved]
    if rest:
        for target, hit in _first_matches(scope, _PERSONS_SQL, rest).items():
            resolved[target] = ("people", *hit)
    return resolved


def render(scope: LibraryScope, text: str) -> str:
    """``resolve_wiki_links(text)`` read in ``scope``'s snapshot."""
    from backend.db_manager import render_wiki_links

    return render_wiki_links(text, lambda targets: resolve_targets(scope, targets))
