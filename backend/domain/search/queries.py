"""Work search reads on PostgreSQL (L-S-1 #637; ``docs/postgresql-architecture.md`` §8.3).

The query side of ``derived.work_search``. Python only splits the query
(:func:`split_query`): every Unicode step, folding, accent stripping and
punctuation cleanup, happens in SQL through the same functions the refresh
indexed with, so the stored and queried sides cannot drift.

A keyword search finds what Phase 1 (L-S-1) found: Works whose ``doc``
holds every token as a prefix, Works whose ``text_norm`` holds every token
as a substring, and Works whose linked people (``people_norm``) hold the
whole query. L-S-2 (#638) orders them by one score (§8.3, Ranking):

* each token weighs the best field it matches in, counted once however
  often it repeats there: a prefix in the title 1.0, in people or author
  text 0.9, a substring of the title 0.8, of a linked person 0.6, a prefix
  in the abstract 0.4, in the Research Note 0.2, a substring anywhere else
  0.15; the Work's base score is the mean over the tokens;
* plus 0.3 times the trigram similarity of the displayed title to the
  whole query, so a short exact title comes before a longer one;
* plus 0.05 times ``ts_rank_cd`` normalized to below 1 (flag 32), a small
  density term that only separates otherwise equal Works.

Equal scores, and the filter-only lists, put the most recently updated Work
first, then the most recently created, then the id. The weights are
measured on ``tests/pg/search_corpus.py`` by ``tests/pg/search_ranking.py``.
"""

from __future__ import annotations

import re
from typing import LiteralString

from backend.domain.identity.library_scope import LibraryScope

# Today's token breaks: whitespace, "-" and "_" (``_prks_search_tokens``).
_BREAKS = re.compile(r"[\s\-_]+")


def split_query(text: str) -> list[str]:
    """The tokens of a free-text query, unchanged otherwise (§8.3).

    No lowercasing, NFC or character filter: those happen in SQL, so a
    decomposed letter reaches PostgreSQL whole.
    """
    return [token for token in _BREAKS.split(text or "") if token]


_KEYWORD_SQL: LiteralString = r"""
WITH terms AS (
  SELECT t, i FROM unnest(%(tokens)s::text[]) WITH ORDINALITY AS u(t, i)
),
q AS (
  SELECT to_tsquery('prks.prks_search'::regconfig,
                    string_agg(prks.tsquery_prefix(prks.search_fold(t)), ' & ' ORDER BY i)) AS query
  FROM terms
),
needles AS (
  SELECT array_agg('%%' || prks.like_escape(n) || '%%' ORDER BY i) AS p
  FROM (SELECT prks.search_token(t) AS n, i FROM terms) cleaned
  WHERE n <> ''
),
phrase AS (
  SELECT '%%' || prks.like_escape(prks.search_norm(%(phrase)s)) || '%%' AS p
  WHERE %(phrase)s <> ''
),
-- Which Works a query finds is unchanged from Phase 1 (L-S-1): a full-text
-- hit, every token as a substring, or the whole query in a linked person.
-- The substring branch keeps Phase 1's primary-Manifestation join, so the
-- set is exactly the one L-S-1 returned.
found AS (
  SELECT s.work_id
  FROM derived.work_search s, q
  WHERE s.library_id = %(library_id)s AND s.doc @@ q.query
  UNION
  -- The first needle is the trigram index condition; LIKE ALL checks them all.
  SELECT s.work_id
  FROM derived.work_search s
  CROSS JOIN needles
  JOIN prks.works w ON w.library_id = s.library_id AND w.id = s.work_id
  JOIN prks.manifestations m ON m.library_id = w.library_id AND m.id = w.primary_manifestation_id
  WHERE s.library_id = %(library_id)s AND w.library_id = %(library_id)s
    AND s.text_norm LIKE needles.p[1] AND s.text_norm LIKE ALL (needles.p)
  UNION
  SELECT s.work_id
  FROM derived.work_search s, phrase
  WHERE s.library_id = %(library_id)s AND s.people_norm LIKE phrase.p ESCAPE '\'
),
-- Each token on its own: its prefix query and its substring needle.
token AS (
  SELECT i,
         to_tsquery('prks.prks_search'::regconfig, prks.tsquery_prefix(prks.search_fold(t))) AS tq,
         CASE WHEN prks.search_token(t) <> ''
              THEN '%%' || prks.like_escape(prks.search_token(t)) || '%%' END AS needle
  FROM terms
),
-- A token's weight is the best field it matches in, counted once however
-- often it repeats there (§8.3, Ranking).
token_weight AS (
  SELECT f.work_id, k.i,
         CASE WHEN ts_filter(s.doc, '{a}') @@ k.tq THEN 1.0
              WHEN ts_filter(s.doc, '{b}') @@ k.tq THEN 0.9
              WHEN s.title_norm LIKE k.needle THEN 0.8
              WHEN s.people_norm LIKE k.needle THEN 0.6
              WHEN ts_filter(s.doc, '{c}') @@ k.tq THEN 0.4
              WHEN ts_filter(s.doc, '{d}') @@ k.tq THEN 0.2
              WHEN s.text_norm LIKE k.needle THEN 0.15
              ELSE 0.0 END AS w
  FROM found f
  JOIN derived.work_search s ON s.library_id = %(library_id)s AND s.work_id = f.work_id
  CROSS JOIN token k
),
base AS (
  SELECT work_id, avg(w) AS w FROM token_weight GROUP BY work_id
),
scored AS (
  SELECT b.work_id,
         b.w
         + 0.3 * ext.similarity(s.title_norm, prks.search_norm(%(phrase)s))
         + 0.05 * ts_rank_cd(s.doc, q.query, 32) AS score
  FROM base b
  JOIN derived.work_search s ON s.library_id = %(library_id)s AND s.work_id = b.work_id
  CROSS JOIN q
)
SELECT scored.work_id
FROM scored
JOIN prks.works w ON w.library_id = %(library_id)s AND w.id = scored.work_id
ORDER BY scored.score DESC, w.updated_at DESC, w.created_at DESC, scored.work_id COLLATE "C"
"""


def keyword_work_ids(scope: LibraryScope, text: str) -> list[str]:
    """The Works a free-text query finds, best score first (module docs)."""
    tokens = split_query(text)
    if not tokens:
        return []
    params = {"tokens": tokens, "phrase": " ".join(tokens)}
    return [str(row["work_id"]) for row in scope.execute(_KEYWORD_SQL, params)]


# A whole-string filter keeps punctuation on both sides (§8.3): the needle is
# search_norm of the trimmed filter, escaped after folding.
_AUTHOR_SQL: LiteralString = r"""
WITH needle AS (SELECT '%%' || prks.like_escape(prks.search_norm(%(author)s)) || '%%' AS p)
SELECT w.id AS work_id
FROM prks.works w, needle
WHERE w.library_id = %(library_id)s AND prks.search_norm(w.author_text) LIKE needle.p ESCAPE '\'
UNION
SELECT s.work_id
FROM derived.work_search s, needle
WHERE s.library_id = %(library_id)s AND s.people_norm LIKE needle.p ESCAPE '\'
"""


def author_work_ids(scope: LibraryScope, author: str) -> list[str]:
    """Works whose author text or a linked person's name or alias holds ``author``.

    The people side is ``people_norm``: each credited person's name and
    aliases, in credits scope, as SQLite's ``credits_scope_sql``.
    """
    needle = (author or "").strip()
    if not needle:
        return []
    rows = scope.execute(_AUTHOR_SQL, {"author": needle})
    return sorted(str(row["work_id"]) for row in rows)


# The displayed publisher is the primary Manifestation's. A publisher record
# whose name or an alias holds the filter also brings in every Work whose
# displayed publisher has the identity key of one of that record's labels.
_PUBLISHER_SQL: LiteralString = r"""
WITH needle AS (SELECT '%%' || prks.like_escape(prks.search_norm(%(publisher)s)) || '%%' AS p),
named AS (
  SELECT p.id FROM prks.publishers p, needle
  WHERE p.library_id = %(library_id)s AND prks.search_norm(p.name) LIKE needle.p ESCAPE '\'
  UNION
  SELECT a.publisher_id FROM prks.publisher_aliases a, needle
  WHERE a.library_id = %(library_id)s AND prks.search_norm(a.alias) LIKE needle.p ESCAPE '\'
),
labels AS (
  SELECT k.key FROM prks.publisher_keys k
  WHERE k.library_id = %(library_id)s AND k.publisher_id IN (SELECT id FROM named)
)
SELECT w.id AS work_id
FROM prks.works w
JOIN prks.manifestations m ON m.library_id = w.library_id AND m.id = w.primary_manifestation_id
CROSS JOIN needle
WHERE w.library_id = %(library_id)s
  AND (prks.search_norm(m.publisher) LIKE needle.p ESCAPE '\'
       OR prks.identity_key(m.publisher) IN (SELECT key FROM labels))
"""


def publisher_work_ids(scope: LibraryScope, publisher: str) -> list[str]:
    """Works whose displayed publisher holds ``publisher``, or names a matching publisher record."""
    needle = (publisher or "").strip()
    if not needle:
        return []
    rows = scope.execute(_PUBLISHER_SQL, {"publisher": needle})
    return sorted(str(row["work_id"]) for row in rows)


_BY_RECENCY_SQL: LiteralString = """
SELECT id FROM prks.works
WHERE library_id = %(library_id)s AND id = ANY(%(work_ids)s::text[])
ORDER BY updated_at DESC, created_at DESC, id COLLATE "C"
"""


def by_recency(scope: LibraryScope, work_ids: list[str]) -> list[str]:
    """The named Works of this Library, most recently updated first."""
    if not work_ids:
        return []
    return [str(row["id"]) for row in scope.execute(_BY_RECENCY_SQL, {"work_ids": work_ids})]


_TAGGED_SQL: LiteralString = """
SELECT w.id FROM prks.works w
JOIN prks.work_tags wt ON wt.library_id = w.library_id AND wt.work_id = w.id
WHERE w.library_id = %(library_id)s AND wt.tag_id = %(tag_id)s
ORDER BY w.created_at DESC, w.id COLLATE "C"
"""


def tagged_work_ids(scope: LibraryScope, label: str) -> list[str]:
    """``get_works_by_tag_name``: the Works tagged with the Tag whose name or alias is ``label``, newest first."""
    from backend.domain.organization import tags

    tag_id = tags.resolve_label(scope, (label or "").strip())
    if tag_id is None:
        return []
    return [str(row["id"]) for row in scope.execute(_TAGGED_SQL, {"tag_id": tag_id})]
