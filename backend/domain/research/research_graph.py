"""Read-only Research Graph projection.

Canonical records and relationships remain the authority. This module only
reads them (plus fail-soft derived note-reference aggregates). It never
writes graph state, never infers edges from prose/PDF/tags, and never
authorizes deletion or other canonical mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional



MAX_GRAPH_NODES = 2500
MAX_GRAPH_EDGES = 7500


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


def person_label(first_name, last_name) -> str:
    parts = []
    if first_name:
        parts.append(str(first_name).strip())
    if last_name:
        parts.append(str(last_name).strip())
    return " ".join(p for p in parts if p)


def graph_node(
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


def graph_edge(edge_type: str, source: str, target: str, extra: Optional[dict] = None) -> dict:
    item = {
        "id": graph_edge_id(edge_type, source, target),
        "type": edge_type,
        "source": source,
        "target": target,
    }
    if extra:
        item.update(extra)
    return item


def finish_snapshot(
    nodes: Dict[str, dict],
    edges: Dict[str, dict],
    *,
    derived_note_edges_available: bool,
    people_included: bool,
) -> GraphSnapshot:
    """Order the nodes and edges and enforce the size bounds (shared with PostgreSQL)."""
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
        derived_note_edges_available=derived_note_edges_available,
        people_included=people_included,
    )


