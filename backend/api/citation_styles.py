"""Imported citation styles HTTP controller: ``/api/citation-styles`` (Citation V1, #41).

- ``GET /api/citation-styles``: ``{"styles": [{id, title, created_at}]}``, the
  Library's imported styles by title.
- ``GET /api/citation-styles/:id``: ``{id, title, csl}``, the style's text for
  the browser's formatter; ``404`` when the Library has no such style.
- ``POST /api/citation-styles`` with ``{"csl": "<style …>"}``: checks and stores
  the style (``backend.domain.works.custom_citation_styles``) and answers its
  summary with ``201``; a refused file is ``400`` with the reason.
- ``DELETE /api/citation-styles/:id``: ``{"deleted": id}``, or ``404``.

An import or a delete is Library administration (``backend/api/session.py``
admits only ``ADMIN_LIBRARY``); the commands open the transactions. They
are imported when a route runs (no PostgreSQL module at import time).
"""

from __future__ import annotations

from backend.web.http import Request, Response, json_response
from backend.web.routing import route


def _not_found(req: Request) -> Response:
    return json_response(req, 404, {"error": "Citation style not found."})


def list_styles(req: Request) -> Response:
    from backend.domain.works import custom_citation_styles

    return json_response(req, 200, {"styles": custom_citation_styles.listed(*req.actor)})


def read(req: Request) -> Response:
    from backend.domain.works import custom_citation_styles

    style = custom_citation_styles.read(*req.actor, req.params["style_id"])
    if style is None:
        return _not_found(req)
    return json_response(req, 200, style)


def create(req: Request) -> Response:
    from backend.domain.works import custom_citation_styles

    try:
        summary = custom_citation_styles.imported(*req.actor, req.data)
    except custom_citation_styles.StyleRefused as e:
        return json_response(req, 400, {"error": str(e)})
    return json_response(req, 201, summary)


def delete(req: Request) -> Response:
    from backend.domain.works import custom_citation_styles

    style_id = req.params["style_id"]
    if not custom_citation_styles.deleted(*req.actor, style_id):
        return _not_found(req)
    return json_response(req, 200, {"deleted": style_id})


ROUTES = (
    route("GET", "/api/citation-styles", list_styles),
    route("POST", "/api/citation-styles", create),
    route("GET", "/api/citation-styles/{style_id}", read),
    route("DELETE", "/api/citation-styles/{style_id}", delete),
)
