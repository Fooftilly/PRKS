"""Read-only Research Graph projection.

Canonical records and relationships remain the authority. This module only
reads them (plus fail-soft derived note-reference aggregates). It never
writes graph state, never infers edges from prose/PDF/tags, and never
authorizes deletion or other canonical mutation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from backend.db_manager import PRKSDatabase
from backend.log_safety import safe_error_type
from backend.performance import span as perf_span

LOGGER = logging.getLogger("prks.research_graph")

MAX_GRAPH_NODES = 2500
MAX_GRAPH_EDGES = 7500

NODE_TYPES = ("argument", "concept", "person", "position", "work")
EDGE_TYPES = (
    "argument_argument",
    "argument_position",
    "argument_source",
    "concept_parent",
    "mentions_argument",
    "mentions_concept",
    "work_author",
)

_AUTHOR_ROLE = "Author"


class ResearchGraphError(ValueError):
    """Controlled graph-projection failure."""

    def __init__(self, code: str, message: str, http_status: int = 400, extra: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.extra = extra or {}


class GraphTooLargeError(ResearchGraphError):
    def __init__(self, node_count: int, edge_count: int):
        super().__init__(
            "graph_too_large",
            "Graph is too large to render as a single snapshot.",
            413,
            {"node_count": int(node_count), "edge_count": int(edge_count)},
        )


@dataclass(frozen=True)
class GraphSnapshot:
    nodes: List[dict]
    edges: List[dict]
    derived_note_edges_available: bool
    people_included: bool

    def to_dict(self) -> dict:
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "meta": {
                "node_count": len(self.nodes),
                "edge_count": len(self.edges),
                "derived_note_edges_available": self.derived_note_edges_available,
                "people_included": self.people_included,
            },
        }


def graph_node_id(node_type: str, record_id: str) -> str:
    return "%s:%s" % (node_type, record_id)


def graph_edge_id(edge_type: str, source: str, target: str) -> str:
    return "%s:%s>%s" % (edge_type, source, target)


def _norm_label(label: str) -> str:
    return (label or "").casefold()


def _person_label(first_name, last_name) -> str:
    parts = []
    if first_name:
        parts.append(str(first_name).strip())
    if last_name:
        parts.append(str(last_name).strip())
    return " ".join(p for p in parts if p)


def _node(
    node_type: str,
    record_id: str,
    label: str,
    route: str,
    extra: Optional[dict] = None,
) -> dict:
    item = {
        "id": graph_node_id(node_type, record_id),
        "record_id": record_id,
        "type": node_type,
        "label": label or record_id,
        "route": route,
    }
    if extra:
        item.update(extra)
    return item


def _edge(edge_type: str, source: str, target: str, extra: Optional[dict] = None) -> dict:
    item = {
        "id": graph_edge_id(edge_type, source, target),
        "type": edge_type,
        "source": source,
        "target": target,
    }
    if extra:
        item.update(extra)
    return item


class ResearchGraphBuilder:
    """Bulk-read snapshot builder. No write methods."""

    def __init__(self) -> None:
        self.last_main_query_count = 0

    def build(
        self,
        db: PRKSDatabase,
        research_index,
        *,
        include_people: bool = False,
    ) -> GraphSnapshot:
        self.last_main_query_count = 0
        with perf_span("research_graph_build"):
            snapshot = self._build(db, research_index, include_people=bool(include_people))
        LOGGER.info(
            "research_graph_built node_count=%s edge_count=%s people_included=%s derived_note_edges_available=%s",
            len(snapshot.nodes),
            len(snapshot.edges),
            snapshot.people_included,
            snapshot.derived_note_edges_available,
        )
        return snapshot

    def _q(self, conn, sql: str, params: tuple = ()):
        self.last_main_query_count += 1
        return conn.execute(sql, params).fetchall()

    def _in_clause(self, ids: Sequence[str]) -> Tuple[str, tuple]:
        placeholders = ",".join("?" * len(ids))
        return placeholders, tuple(ids)

    def _build(self, db: PRKSDatabase, research_index, *, include_people: bool) -> GraphSnapshot:
        nodes: Dict[str, dict] = {}
        edges: Dict[str, dict] = {}

        with db.get_connection() as conn:
            concept_rows = self._q(conn, "SELECT id, name FROM concepts")
            parent_rows = self._q(
                conn,
                "SELECT child_concept_id, parent_concept_id FROM concept_parents",
            )
            position_rows = self._q(conn, "SELECT id, name FROM positions")
            argument_rows = self._q(conn, "SELECT id, name, kind FROM arguments")
            arg_pos_rows = self._q(
                conn,
                """
                SELECT argument_id, position_id, verdict_id
                FROM argument_target_positions
                """,
            )
            arg_arg_rows = self._q(
                conn,
                """
                SELECT argument_id, target_argument_id, verdict_id
                FROM argument_target_arguments
                """,
            )
            source_rows = self._q(
                conn,
                "SELECT argument_id, work_id, pages FROM argument_sources",
            )
            verdict_rows = self._q(conn, "SELECT id, label FROM argument_verdicts")

            for row in concept_rows:
                cid = row["id"]
                nodes[_nid("concept", cid)] = _node(
                    "concept",
                    cid,
                    row["name"] or cid,
                    "#/concepts/" + cid,
                )
            for row in position_rows:
                pid = row["id"]
                nodes[_nid("position", pid)] = _node(
                    "position",
                    pid,
                    row["name"] or pid,
                    "#/positions/" + pid,
                )
            for row in argument_rows:
                aid = row["id"]
                kind = row["kind"] if row["kind"] in ("argument", "stance") else "argument"
                nodes[_nid("argument", aid)] = _node(
                    "argument",
                    aid,
                    row["name"] or aid,
                    "#/arguments/" + aid,
                    {"kind": kind},
                )

            verdict_labels = {r["id"]: r["label"] or r["id"] for r in verdict_rows}

            for row in parent_rows:
                child = _nid("concept", row["child_concept_id"])
                parent = _nid("concept", row["parent_concept_id"])
                if child not in nodes or parent not in nodes:
                    continue
                self._put_edge(
                    edges,
                    _edge("concept_parent", child, parent),
                )

            for row in arg_pos_rows:
                src = _nid("argument", row["argument_id"])
                tgt = _nid("position", row["position_id"])
                if src not in nodes or tgt not in nodes:
                    continue
                vid = row["verdict_id"]
                self._put_edge(
                    edges,
                    _edge(
                        "argument_position",
                        src,
                        tgt,
                        {
                            "verdict_id": vid,
                            "verdict_label": verdict_labels.get(vid, vid),
                        },
                    ),
                )

            for row in arg_arg_rows:
                src = _nid("argument", row["argument_id"])
                tgt = _nid("argument", row["target_argument_id"])
                if src not in nodes or tgt not in nodes:
                    continue
                vid = row["verdict_id"]
                self._put_edge(
                    edges,
                    _edge(
                        "argument_argument",
                        src,
                        tgt,
                        {
                            "verdict_id": vid,
                            "verdict_label": verdict_labels.get(vid, vid),
                        },
                    ),
                )

            work_ids = set()
            pending_source_edges = []
            for row in source_rows:
                src = _nid("argument", row["argument_id"])
                if src not in nodes:
                    continue
                work_ids.add(row["work_id"])
                pending_source_edges.append(row)

            mention_concept_rows, mention_argument_rows, derived_ok = self._derived_mentions(
                research_index
            )
            if derived_ok:
                for row in mention_concept_rows:
                    if _nid("concept", row["concept_id"]) in nodes:
                        work_ids.add(row["work_id"])
                for row in mention_argument_rows:
                    if _nid("argument", row["argument_id"]) in nodes:
                        work_ids.add(row["work_id"])
            else:
                mention_concept_rows = []
                mention_argument_rows = []

            work_ids = {wid for wid in work_ids if wid}
            if work_ids:
                ph, params = self._in_clause(sorted(work_ids))
                work_rows = self._q(
                    conn,
                    "SELECT id, title, doc_type FROM works WHERE id IN (%s)" % ph,
                    params,
                )
            else:
                work_rows = []

            present_works = set()
            for row in work_rows:
                wid = row["id"]
                present_works.add(wid)
                extra = {}
                if row["doc_type"]:
                    extra["doc_type"] = row["doc_type"]
                nodes[_nid("work", wid)] = _node(
                    "work",
                    wid,
                    row["title"] or wid,
                    "#/works/" + wid,
                    extra or None,
                )

            for row in pending_source_edges:
                wid = row["work_id"]
                if wid not in present_works:
                    continue
                src = _nid("argument", row["argument_id"])
                tgt = _nid("work", wid)
                extra = {}
                pages = row["pages"] or ""
                if pages:
                    extra["pages"] = pages
                self._put_edge(edges, _edge("argument_source", src, tgt, extra or None))

            if derived_ok:
                for row in mention_concept_rows:
                    wid = row["work_id"]
                    cid = row["concept_id"]
                    if wid not in present_works:
                        continue
                    tgt = _nid("concept", cid)
                    if tgt not in nodes:
                        continue
                    self._put_edge(
                        edges,
                        _edge(
                            "mentions_concept",
                            _nid("work", wid),
                            tgt,
                            {"count": int(row["count"])},
                        ),
                    )
                for row in mention_argument_rows:
                    wid = row["work_id"]
                    aid = row["argument_id"]
                    if wid not in present_works:
                        continue
                    tgt = _nid("argument", aid)
                    if tgt not in nodes:
                        continue
                    self._put_edge(
                        edges,
                        _edge(
                            "mentions_argument",
                            _nid("work", wid),
                            tgt,
                            {"count": int(row["count"])},
                        ),
                    )

            if include_people and present_works:
                ph, params = self._in_clause(sorted(present_works))
                role_rows = self._q(
                    conn,
                    """
                    SELECT person_id, work_id
                    FROM roles
                    WHERE role_type = ? AND work_id IN (%s)
                    """
                    % ph,
                    (_AUTHOR_ROLE,) + params,
                )
                person_ids = sorted({r["person_id"] for r in role_rows if r["person_id"]})
                person_present: Dict[str, dict] = {}
                if person_ids:
                    pph, pparams = self._in_clause(person_ids)
                    person_rows = self._q(
                        conn,
                        "SELECT id, first_name, last_name FROM persons WHERE id IN (%s)"
                        % pph,
                        pparams,
                    )
                    for prow in person_rows:
                        pid = prow["id"]
                        label = _person_label(prow["first_name"], prow["last_name"]) or pid
                        person_present[pid] = _node(
                            "person",
                            pid,
                            label,
                            "#/people/" + pid,
                        )
                        nodes[_nid("person", pid)] = person_present[pid]
                for row in role_rows:
                    pid = row["person_id"]
                    wid = row["work_id"]
                    if pid not in person_present or wid not in present_works:
                        continue
                    self._put_edge(
                        edges,
                        _edge(
                            "work_author",
                            _nid("person", pid),
                            _nid("work", wid),
                        ),
                    )

        node_list = sorted(
            nodes.values(),
            key=lambda n: (n["type"], _norm_label(n["label"]), n["record_id"], n["id"]),
        )
        edge_list = sorted(
            edges.values(),
            key=lambda e: (e["type"], e["source"], e["target"], e["id"]),
        )
        if len(node_list) > MAX_GRAPH_NODES or len(edge_list) > MAX_GRAPH_EDGES:
            raise GraphTooLargeError(len(node_list), len(edge_list))
        return GraphSnapshot(
            nodes=node_list,
            edges=edge_list,
            derived_note_edges_available=derived_ok,
            people_included=bool(include_people),
        )

    def _put_edge(self, edges: Dict[str, dict], edge: dict) -> None:
        edges[edge["id"]] = edge

    def _derived_mentions(self, research_index) -> Tuple[List[dict], List[dict], bool]:
        if research_index is None:
            return [], [], False
        try:
            concept_rows = research_index.aggregate_concept_mention_edges()
            argument_rows = research_index.aggregate_argument_mention_edges()
            return list(concept_rows or []), list(argument_rows or []), True
        except Exception as exc:
            LOGGER.warning(
                "research_graph_derived_unavailable error_type=%s",
                safe_error_type(exc),
            )
            return [], [], False


def _nid(node_type: str, record_id: str) -> str:
    return graph_node_id(node_type, record_id)


def build_research_graph(
    db: PRKSDatabase,
    research_index,
    *,
    include_people: bool = False,
) -> GraphSnapshot:
    return ResearchGraphBuilder().build(db, research_index, include_people=include_people)
