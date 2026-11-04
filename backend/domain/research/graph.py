"""The Research Graph on PostgreSQL (L-R-1 #634; ``docs/postgresql-architecture.md`` §8.4).

``backend/research_graph.py``'s snapshot of one Library, read in one
snapshot: the same node and edge ids, types, labels, routes, extras, order
and size bounds (its :func:`~backend.research_graph.finish_snapshot`), so
the response is SQLite's. Nodes are the Library's Concepts, Positions and
Arguments; edges are the Concept hierarchy, Argument targets with their
verdicts, citations (``argument_source``, one per Argument and Work, with
the pages of its last citation of that Work) and the derived note mentions
(``mentions_concept``, ``mentions_argument``, counted per Work). A Work is a
node when an Argument cites it or a mention names a live Concept or
Argument; with ``include_people``, a Person is a node when it is an Author
of one of those Works (``work_author``).

The joins resolve every reference in SQL, so a mention of a deleted Concept
or Argument, or one left by a Work deleted before its reindex ran, is no
edge. The mentions are the index's: a Work whose reindex is pending shows
its previous note's (``reindex.research_refs`` reports ``pending``). The
derived rows live in the same database, so ``derived_note_edges_available``
is always true. Nothing here writes, and the graph is never authority for
a mutation.
"""

from __future__ import annotations

from typing import Any, LiteralString

from backend.domain.identity.library_scope import LibraryScope
from backend.research_graph import (
    GraphSnapshot,
    finish_snapshot,
    graph_edge,
    graph_node,
    graph_node_id,
    person_label,
)

_CONCEPTS_SQL: LiteralString = "SELECT id, name FROM prks.concepts WHERE library_id = %(library_id)s"
_POSITIONS_SQL: LiteralString = "SELECT id, name FROM prks.positions WHERE library_id = %(library_id)s"
_ARGUMENTS_SQL: LiteralString = "SELECT id, name, kind FROM prks.arguments WHERE library_id = %(library_id)s"
_PARENTS_SQL: LiteralString = (
    "SELECT child_concept_id, parent_concept_id FROM prks.concept_parents WHERE library_id = %(library_id)s"
)
_TARGET_POSITIONS_SQL: LiteralString = """
SELECT t.argument_id, t.position_id AS target_id, t.verdict_id, v.label AS verdict_label
FROM prks.argument_target_positions t JOIN prks.argument_verdicts v ON v.id = t.verdict_id
WHERE t.library_id = %(library_id)s
"""
_TARGET_ARGUMENTS_SQL: LiteralString = """
SELECT t.argument_id, t.target_argument_id AS target_id, t.verdict_id, v.label AS verdict_label
FROM prks.argument_target_arguments t JOIN prks.argument_verdicts v ON v.id = t.verdict_id
WHERE t.library_id = %(library_id)s
"""
_TARGETS: tuple[tuple[str, str, LiteralString], ...] = (
    ("argument_position", "position", _TARGET_POSITIONS_SQL),
    ("argument_argument", "argument", _TARGET_ARGUMENTS_SQL),
)
# SQLite keeps the last row per Argument and Work, in list order.
_SOURCES_SQL: LiteralString = """
SELECT DISTINCT ON (argument_id, work_id) argument_id, work_id, pages
FROM prks.argument_sources WHERE library_id = %(library_id)s
ORDER BY argument_id, work_id, order_index DESC
"""
_CONCEPT_MENTIONS_SQL: LiteralString = """
SELECT m.work_id, m.concept_id AS record_id, count(*) AS count
FROM derived.concept_mentions m
JOIN prks.works w ON w.library_id = m.library_id AND w.id = m.work_id
JOIN prks.concepts c ON c.library_id = m.library_id AND c.id = m.concept_id
WHERE m.library_id = %(library_id)s
GROUP BY m.work_id, m.concept_id
"""
_ARGUMENT_MENTIONS_SQL: LiteralString = """
SELECT m.work_id, m.argument_id AS record_id, count(*) AS count
FROM derived.argument_mentions m
JOIN prks.works w ON w.library_id = m.library_id AND w.id = m.work_id
JOIN prks.arguments a ON a.library_id = m.library_id AND a.id = m.argument_id
WHERE m.library_id = %(library_id)s
GROUP BY m.work_id, m.argument_id
"""
# Title and type as the Work projection gives them: the primary Version's.
_WORKS_SQL: LiteralString = """
SELECT w.id, coalesce(m.title, w.title) AS title, m.doc_type
FROM prks.works w
JOIN prks.manifestations m ON m.library_id = w.library_id AND m.id = w.primary_manifestation_id
WHERE w.library_id = %(library_id)s AND w.id = ANY(%(work_ids)s)
"""
_AUTHORS_SQL: LiteralString = """
SELECT DISTINCT r.person_id, r.work_id, p.first_name, p.last_name
FROM prks.roles r JOIN prks.persons p ON p.library_id = r.library_id AND p.id = r.person_id
WHERE r.library_id = %(library_id)s AND r.role_type = 'Author' AND r.work_id = ANY(%(work_ids)s)
"""


def _rows(scope: LibraryScope, sql: LiteralString, params: dict[str, Any] | None = None) -> list[Any]:
    return scope.execute(sql, params or {}).fetchall()


def snapshot(scope: LibraryScope, *, include_people: bool = False) -> GraphSnapshot:
    """``ResearchGraphBuilder.build`` for this Library; raises ``GraphTooLargeError`` past the bounds."""
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}

    def put(edge: dict) -> None:
        edges[edge["id"]] = edge

    for row in _rows(scope, _CONCEPTS_SQL):
        cid = str(row["id"])
        nodes[graph_node_id("concept", cid)] = graph_node("concept", cid, row["name"] or cid, "#/concepts/" + cid)
    for row in _rows(scope, _POSITIONS_SQL):
        pid = str(row["id"])
        nodes[graph_node_id("position", pid)] = graph_node("position", pid, row["name"] or pid,
                                                           "#/positions/" + pid)
    for row in _rows(scope, _ARGUMENTS_SQL):
        aid = str(row["id"])
        nodes[graph_node_id("argument", aid)] = graph_node("argument", aid, row["name"] or aid,
                                                           "#/arguments/" + aid, {"kind": row["kind"]})

    for row in _rows(scope, _PARENTS_SQL):
        put(graph_edge("concept_parent", graph_node_id("concept", row["child_concept_id"]),
                       graph_node_id("concept", row["parent_concept_id"])))
    for edge_type, target_type, sql in _TARGETS:
        for row in _rows(scope, sql):
            put(graph_edge(edge_type, graph_node_id("argument", row["argument_id"]),
                           graph_node_id(target_type, row["target_id"]),
                           {"verdict_id": row["verdict_id"], "verdict_label": row["verdict_label"]}))

    sources = _rows(scope, _SOURCES_SQL)
    mentions = [("mentions_concept", "concept", row) for row in _rows(scope, _CONCEPT_MENTIONS_SQL)]
    mentions += [("mentions_argument", "argument", row) for row in _rows(scope, _ARGUMENT_MENTIONS_SQL)]
    work_ids = sorted({str(row["work_id"]) for row in sources} | {str(row["work_id"]) for _, _, row in mentions})
    if work_ids:
        for row in _rows(scope, _WORKS_SQL, {"work_ids": work_ids}):
            wid = str(row["id"])
            extra = {"doc_type": row["doc_type"]} if row["doc_type"] else None
            nodes[graph_node_id("work", wid)] = graph_node("work", wid, row["title"] or wid, "#/works/" + wid, extra)
    for row in sources:
        put(graph_edge("argument_source", graph_node_id("argument", row["argument_id"]),
                       graph_node_id("work", row["work_id"]),
                       {"pages": row["pages"]} if row["pages"] else None))
    for edge_type, target_type, row in mentions:
        put(graph_edge(edge_type, graph_node_id("work", row["work_id"]),
                       graph_node_id(target_type, row["record_id"]), {"count": int(row["count"])}))

    if include_people and work_ids:
        for row in _rows(scope, _AUTHORS_SQL, {"work_ids": work_ids}):
            pid = str(row["person_id"])
            nodes[graph_node_id("person", pid)] = graph_node(
                "person", pid, person_label(row["first_name"], row["last_name"]) or pid, "#/people/" + pid)
            put(graph_edge("work_author", graph_node_id("person", pid), graph_node_id("work", row["work_id"])))

    return finish_snapshot(nodes, edges, derived_note_edges_available=True, people_included=bool(include_people))
