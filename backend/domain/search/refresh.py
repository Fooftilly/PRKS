"""The one write path of ``derived.work_search`` (L-S-1 #637; ``docs/postgresql-architecture.md`` §8.3).

A Work's search row is derived from its displayed title (the primary
Manifestation's override, else the Work's), its free-text author credit, the
names and aliases of the people credited on it (Work-scoped roles and those
of its primary Manifestation, the credits SQLite searches), its displayed
abstract and its Research Note. The Private Note is not searched, as on
SQLite.

Commands that change any of those inputs, ``rebuild-derived`` and the repair
job all write the rows through :func:`refresh_work_search`, in the caller's
transaction (READ COMMITTED):

1. the given Works that still exist are locked ``FOR KEY SHARE`` in id
   order, and the rows of given ids whose Work is gone are deleted;
2. a missing row is inserted (``ON CONFLICT DO NOTHING``) only for a Work
   locked in step 1, never for one that is gone;
3. the rows are locked ``FOR UPDATE`` in ``work_id`` order;
4. one ``UPDATE … FROM`` recomputes them from the canonical tables.

The key-share lock orders a refresh against ``DELETE_WORK``
(:func:`backend.domain.works.commands.delete_work`, which deletes the Work
and then its row): a delete that starts first makes step 1 wait and then
skip the Work, and a refresh that locks first makes the delete wait for its
commit, after which the delete removes the row. So a refresh whose list
still names a deleted Work never recreates its row. Step 4's statement takes
its snapshot after the step 3 locks are granted, so it sees every refresh of
these rows committed before it, and a later change of their inputs waits at
step 3 and recomputes after it: a stale projection never overwrites a newer
one, whichever writer is slower.

All folding happens in SQL (``prks.search_fold``, ``prks.search_norm``), so
indexed text and query tokens use PostgreSQL's one Unicode implementation.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import LiteralString

from backend.domain.identity.library_scope import LibraryScope

_LOCK_WORKS: LiteralString = """
SELECT id FROM prks.works
WHERE library_id = %(library_id)s AND id = ANY(%(work_ids)s::text[])
ORDER BY id
FOR KEY SHARE
"""
_DELETE_GONE: LiteralString = """
DELETE FROM derived.work_search
WHERE library_id = %(library_id)s AND work_id = ANY(%(work_ids)s::text[])
  AND NOT (work_id = ANY(%(locked)s::text[]))
"""
_ENSURE_ROWS: LiteralString = """
INSERT INTO derived.work_search (library_id, work_id, doc, title_norm, people_norm, text_norm)
SELECT %(library_id)s, id, ''::tsvector, '', '', '' FROM unnest(%(locked)s::text[]) AS id
ON CONFLICT (work_id) DO NOTHING
"""
_LOCK_ROWS: LiteralString = """
SELECT work_id FROM derived.work_search
WHERE library_id = %(library_id)s AND work_id = ANY(%(locked)s::text[])
ORDER BY work_id
FOR UPDATE
"""
# The inputs, read under the locks above. People are the credits SQLite
# searches (``work_projection.credits_scope_sql``): Work-scoped roles and
# those of the primary Manifestation. Each person contributes the trimmed
# "first last" name and each alias, one per line, so a substring needle
# (whose whitespace the query side collapses to single spaces) never spans
# two people.
_RECOMPUTE: LiteralString = """
WITH src AS (
  SELECT w.id,
         coalesce(m.title, w.title) AS title,
         coalesce(w.author_text, '') AS author_text,
         coalesce(m.abstract, w.abstract, '') AS abstract,
         coalesce(n.body, '') AS notes,
         coalesce((
           SELECT string_agg(name, E'\\n' ORDER BY name COLLATE "C")
           FROM (
             SELECT DISTINCT btrim(p.first_name || ' ' || p.last_name) AS name
             FROM prks.roles r
             JOIN prks.persons p ON p.library_id = r.library_id AND p.id = r.person_id
             WHERE r.library_id = w.library_id AND r.work_id = w.id
               AND (r.manifestation_id IS NULL OR r.manifestation_id = w.primary_manifestation_id)
             UNION
             SELECT a.alias
             FROM prks.roles r
             JOIN prks.person_aliases a ON a.library_id = r.library_id AND a.person_id = r.person_id
             WHERE r.library_id = w.library_id AND r.work_id = w.id
               AND (r.manifestation_id IS NULL OR r.manifestation_id = w.primary_manifestation_id)
           ) names
           WHERE name <> ''
         ), '') AS people
  FROM prks.works w
  JOIN prks.manifestations m ON m.library_id = w.library_id AND m.id = w.primary_manifestation_id
  LEFT JOIN prks.work_notes n ON n.library_id = w.library_id AND n.work_id = w.id AND n.kind = 'research'
  WHERE w.library_id = %(library_id)s AND w.id = ANY(%(locked)s::text[])
)
UPDATE derived.work_search s
SET doc = setweight(to_tsvector('prks.prks_search', prks.search_fold(src.title)), 'A')
       || setweight(to_tsvector('prks.prks_search',
                                prks.search_fold(src.people || ' ' || src.author_text)), 'B')
       || setweight(to_tsvector('prks.prks_search', prks.search_fold(src.abstract)), 'C')
       || setweight(to_tsvector('prks.prks_search', prks.search_fold(src.notes)), 'D'),
    title_norm = prks.search_norm(src.title),
    people_norm = prks.search_norm(src.people),
    text_norm = prks.search_norm(src.title || ' ' || src.author_text || ' ' || src.abstract
                                 || ' ' || src.notes),
    updated_at = now()
FROM src
WHERE s.library_id = %(library_id)s AND s.work_id = src.id
"""


def refresh_work_search(scope: LibraryScope, work_ids: Iterable[str]) -> list[str]:
    """Bring the rows of ``work_ids`` up to date in the caller's transaction.

    Returns the ids of the Works that exist (and now have a current row), in
    id order. Ids of Works that are gone, or of another Library, have no row
    afterwards.
    """
    ids = sorted({str(work_id) for work_id in work_ids})
    if not ids:
        return []
    locked = [str(row["id"]) for row in scope.execute(_LOCK_WORKS, {"work_ids": ids}).fetchall()]
    scope.execute(_DELETE_GONE, {"work_ids": ids, "locked": locked})
    if not locked:
        return []
    params = {"locked": locked}
    scope.execute(_ENSURE_ROWS, params)
    scope.execute(_LOCK_ROWS, params).fetchall()
    scope.execute(_RECOMPUTE, params)
    return locked


_DELETE_ROW: LiteralString = (
    "DELETE FROM derived.work_search WHERE library_id = %(library_id)s AND work_id = %(work_id)s"
)


def drop_work(scope: LibraryScope, work_id: str) -> None:
    """Delete a Work's row; ``DELETE_WORK`` calls it after deleting the Work (§8.3)."""
    scope.execute(_DELETE_ROW, {"work_id": work_id})
