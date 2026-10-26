"""Research Note mentions and ``research.reindex_work`` (L-R-1 #634; ``docs/postgresql-architecture.md`` §8.4).

The derived index of a Work's Research Note: ``derived.concept_mentions``,
``derived.argument_mentions`` and ``derived.work_note_state``, the meaning
``backend/research_index.py`` gives them on SQLite. The canonical note in
``prks.work_notes`` stays the only authority (no destructive command reads
these rows), and the index is rebuildable from it: only
:func:`reindex_work` writes it.

**When it runs.** Every command that changes a Research Note enqueues one
``research.reindex_work`` job for the Work in its own transaction
(:func:`enqueue`: ``notes``, a Work create with a note, a Work delete), so
the job becomes claimable only once that change is committed. Jobs are
never coalesced: a queued twin could be claimed and run before the
enqueuing transaction commits, and a first note (an insert) would then be
invisible to it, so each change gets a job that runs after it.

**The transaction** (:func:`reindex_work`, READ COMMITTED):

1. ``pg_advisory_xact_lock`` on the Work's reindex key, so reindexes of one
   Work run one at a time even while the Work has no note row to lock, and
   each later step reads every note commit before it;
2. the Work row ``FOR KEY SHARE``: a Work delete in flight is waited for
   (it holds the row ``FOR UPDATE``), and a delete that starts later waits
   for this transaction. When the Work is gone (deleted, of another
   Library, never created) the Work's rows are deleted and nothing is
   written, so a stale job can never recreate them;
3. the note ``FOR SHARE``: a note command that already changed it is waited
   for, and one that starts later waits for this transaction (which then
   wrote the older note, and that command's own job follows);
4. the mentions and the note state are replaced in the same transaction.

Whichever reindex of a Work runs last therefore reads the latest note, in
whatever order duplicate or retried jobs run, and running one again changes
nothing. Concept references resolve through the ``concept_keys`` index in
one query; Argument references are recorded as written, as on SQLite.

**The fence.** A Research Note ACK no longer waits for the index (§8.4):
:func:`research_refs` reports ``pending`` while the stored state is not the
current note's, which is what a client waits on before refreshing Concept,
Argument and graph views.
"""

from __future__ import annotations

import hashlib
from typing import Any, LiteralString, Optional

from backend import research_network as network
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope
from backend.jobs import queue
from backend.jobs.handlers import handler
from backend.jobs.runner import JobRun
from backend.research_markup import parse_research_markup

KIND = "research.reindex_work"

_LOCK_SQL: LiteralString = (
    "SELECT pg_advisory_xact_lock("
    "hashtextextended('prks.research_reindex:' || %(library_id)s || ':' || %(work_id)s, 0))"
)
_WORK_SQL: LiteralString = (
    "SELECT id FROM prks.works WHERE library_id = %(library_id)s AND id = %(work_id)s FOR KEY SHARE"
)
_NOTE_SQL: LiteralString = """
SELECT body FROM prks.work_notes
WHERE library_id = %(library_id)s AND work_id = %(work_id)s AND kind = 'research'
FOR SHARE
"""
_RESOLVE_SQL: LiteralString = """
SELECT n.name, k.concept_id
FROM unnest(%(names)s::text[]) AS n(name)
JOIN prks.concept_keys k ON k.library_id = %(library_id)s AND k.key = prks.identity_key(n.name)
"""
_CLEAR_SQL: tuple[LiteralString, ...] = (
    "DELETE FROM derived.concept_mentions WHERE library_id = %(library_id)s AND work_id = %(work_id)s",
    "DELETE FROM derived.argument_mentions WHERE library_id = %(library_id)s AND work_id = %(work_id)s",
)
_CLEAR_STATE_SQL: LiteralString = (
    "DELETE FROM derived.work_note_state WHERE library_id = %(library_id)s AND work_id = %(work_id)s"
)
_STATE_SQL: LiteralString = """
INSERT INTO derived.work_note_state (library_id, work_id, content_hash)
VALUES (%(library_id)s, %(work_id)s, %(content_hash)s)
ON CONFLICT (work_id) DO UPDATE SET content_hash = EXCLUDED.content_hash
"""
_INSERT_CONCEPTS_SQL: LiteralString = """
INSERT INTO derived.concept_mentions (library_id, work_id, concept_id, start_offset, end_offset)
SELECT %(library_id)s, %(work_id)s, m.id, m.start_offset, m.end_offset
FROM unnest(%(ids)s::text[], %(starts)s::int[], %(ends)s::int[]) AS m(id, start_offset, end_offset)
"""
_INSERT_ARGUMENTS_SQL: LiteralString = """
INSERT INTO derived.argument_mentions (library_id, work_id, argument_id, start_offset, end_offset)
SELECT %(library_id)s, %(work_id)s, m.id, m.start_offset, m.end_offset
FROM unnest(%(ids)s::text[], %(starts)s::int[], %(ends)s::int[]) AS m(id, start_offset, end_offset)
"""

# The fence: the state is the current note's when its hash is the stored
# one; a Work never indexed is current only while it has no note.
_PENDING_SQL: LiteralString = """
SELECT CASE WHEN s.content_hash IS NULL THEN coalesce(n.body, '') <> ''
            ELSE s.content_hash <> encode(sha256(convert_to(coalesce(n.body, ''), 'UTF8')), 'hex')
       END AS pending
FROM prks.works w
LEFT JOIN prks.work_notes n ON n.library_id = w.library_id AND n.work_id = w.id AND n.kind = 'research'
LEFT JOIN derived.work_note_state s ON s.library_id = w.library_id AND s.work_id = w.id
WHERE w.library_id = %(library_id)s AND w.id = %(work_id)s
"""
# Mentioned records that still exist, in the order the note first names them.
_CONCEPT_REFS_SQL: LiteralString = """
SELECT c.id, c.name,
       coalesce((SELECT array_agg(a.alias ORDER BY a.alias COLLATE "C") FROM prks.concept_aliases a
                 WHERE a.library_id = c.library_id AND a.concept_id = c.id), '{}') AS aliases
FROM (SELECT concept_id, min(start_offset) AS first FROM derived.concept_mentions
      WHERE library_id = %(library_id)s AND work_id = %(work_id)s GROUP BY concept_id) m
JOIN prks.concepts c ON c.library_id = %(library_id)s AND c.id = m.concept_id
ORDER BY m.first
"""
_ARGUMENT_REFS_SQL: LiteralString = """
SELECT a.id, a.name
FROM (SELECT argument_id, min(start_offset) AS first FROM derived.argument_mentions
      WHERE library_id = %(library_id)s AND work_id = %(work_id)s GROUP BY argument_id) m
JOIN prks.arguments a ON a.library_id = %(library_id)s AND a.id = m.argument_id
ORDER BY m.first
"""


def enqueue(scope: LibraryScope, work_id: str) -> None:
    """Enqueue the Work's reindex in the caller's command transaction (never coalesced)."""
    queue.enqueue_library_job(scope, KIND, {"work_id": work_id})


def content_hash(text: str) -> str:
    """``research_index.content_hash``."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _concept_ids(scope: LibraryScope, names: list[str]) -> dict[str, str]:
    """Canonical reference name -> Concept id, by the key index (``resolve_concept_key``'s ``ok``)."""
    legal = sorted({name for name in names
                    if name and len(name) <= network.CONCEPT_NAME_MAX and "\x00" not in name})
    if not legal:
        return {}
    rows = scope.execute(_RESOLVE_SQL, {"names": legal}).fetchall()
    return {str(row["name"]): str(row["concept_id"]) for row in rows}


def _replace(scope: LibraryScope, statement: LiteralString, work_id: str,
             rows: list[tuple[str, int, int]]) -> None:
    if rows:
        scope.execute(statement, {"work_id": work_id, "ids": [row[0] for row in rows],
                                  "starts": [row[1] for row in rows], "ends": [row[2] for row in rows]})


def reindex_work(scope: LibraryScope, work_id: str) -> bool:
    """Rebuild the Work's mentions and note state from its note; False when the Work is gone.

    The ``research.reindex_work`` transaction (module docstring), also the
    primitive a full rebuild runs per Work (§8.6).
    """
    params = {"work_id": work_id}
    scope.execute(_LOCK_SQL, params)
    gone = scope.execute(_WORK_SQL, params).fetchone() is None
    for statement in _CLEAR_SQL:
        scope.execute(statement, params)
    if gone:
        scope.execute(_CLEAR_STATE_SQL, params)
        return False
    note = scope.execute(_NOTE_SQL, params).fetchone()
    text = "" if note is None else str(note["body"])
    markup = parse_research_markup(text)
    names = [network.canonical_concept_name(ref.name) for ref in markup.concept_refs]
    resolved = _concept_ids(scope, names)
    concepts = [(resolved[name], ref.start, ref.end)
                for name, ref in zip(names, markup.concept_refs) if name in resolved]
    arguments = [(ref.argument_id, ref.start, ref.end) for ref in markup.argument_refs]
    _replace(scope, _INSERT_CONCEPTS_SQL, work_id, concepts)
    _replace(scope, _INSERT_ARGUMENTS_SQL, work_id, arguments)
    scope.execute(_STATE_SQL, {**params, "content_hash": content_hash(text)})
    return True


@handler(KIND)
def run_reindex(run: JobRun) -> None:
    work_id = run.payload.get("work_id")
    if not isinstance(work_id, str) or not work_id:
        raise ValueError("research.reindex_work needs a work_id")
    run.library_command(Capability.EDIT_LIBRARY, lambda scope: reindex_work(scope, work_id))


def research_refs(scope: LibraryScope, work_id: str) -> Optional[dict[str, Any]]:
    """``work_research_refs`` with the fence: ``{concepts, arguments, pending}``; None when there is no such Work.

    The Concepts (``{id, name, aliases}``) and Arguments (``{id, name}``)
    the indexed note mentions, each once, in the order the note first names
    them; a mention of a record since deleted is left out. ``pending`` is
    true while the index is not the current note's.
    """
    params = {"work_id": work_id}
    state = scope.execute(_PENDING_SQL, params).fetchone()
    if state is None:
        return None
    concepts = [{"id": str(row["id"]), "name": str(row["name"]), "aliases": [str(a) for a in row["aliases"]]}
                for row in scope.execute(_CONCEPT_REFS_SQL, params)]
    arguments = [{"id": str(row["id"]), "name": str(row["name"])}
                 for row in scope.execute(_ARGUMENT_REFS_SQL, params)]
    return {"concepts": concepts, "arguments": arguments, "pending": bool(state["pending"])}
