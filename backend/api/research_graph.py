"""``GET /api/research-graph``: the Library's Research graph (``?people=1`` adds People).

A graph past its size bound is refused with its own status and the node
and edge counts (``GraphTooLargeError``).
"""

from __future__ import annotations

from backend.api_contract.operation import Operation, Param, ok
from backend.api_contract.research import GraphTooLarge, ResearchGraph
from backend.domain.research.research_graph import GraphTooLargeError
from backend.web.http import Request, Response, json_response
from backend.web.routing import route


def research_graph(req: Request) -> Response:
    from backend.domain.research import index_commands

    include_people = req.query_value("people").strip().lower() in ("1", "true", "yes", "on")
    try:
        snapshot = index_commands.graph(*req.actor, include_people=include_people)
    except GraphTooLargeError as e:
        return json_response(req, e.http_status, {
            "error": str(e),
            "code": e.code,
            "node_count": e.extra.get("node_count", 0),
            "edge_count": e.extra.get("edge_count", 0),
        })
    return json_response(req, 200, snapshot.to_dict())


ROUTES = (
    route("GET", "/api/research-graph", research_graph, contract=Operation(
        "getResearchGraph", "The Library's Research graph", "research-graph",
        {200: ok(ResearchGraph, "Concepts, Positions, Arguments and the Works they cite or mention."),
         413: ok(GraphTooLarge, "Past the node or edge bound (graph_too_large), with the counts.")},
        params=(Param("people", schema={"type": "string"},
                      description="``1``, ``true``, ``yes`` or ``on``: also the Works' Authors."),))),
)
