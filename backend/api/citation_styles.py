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

from backend.api_contract.citations import (
    CitationStyle,
    CitationStyleDeleted,
    CitationStyleImportRequest,
    CitationStyleList,
    CitationStyleSummary,
)
from backend.api_contract.operation import Operation, body, error, ok
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


_TAG = "citations"
_NOT_FOUND = error("No such imported style.")

ROUTES = (
    route("GET", "/api/citation-styles", list_styles, contract=Operation(
        "listCitationStyles", "The Library's imported CSL styles", _TAG,
        {200: ok(CitationStyleList, "By title.")})),
    route("POST", "/api/citation-styles", create, contract=Operation(
        "importCitationStyle", "Import a CSL style", _TAG,
        {201: ok(CitationStyleSummary, "Imported."),
         400: error("Not a usable independent CSL style, too large, or the Library holds the most styles.")},
        request=body(CitationStyleImportRequest),
        description="Needs the Library owner.")),
    route("GET", "/api/citation-styles/{style_id}", read, contract=Operation(
        "getCitationStyle", "An imported style's CSL text", _TAG,
        {200: ok(CitationStyle, "The style."), 404: _NOT_FOUND})),
    route("DELETE", "/api/citation-styles/{style_id}", delete, contract=Operation(
        "deleteCitationStyle", "Delete an imported style", _TAG,
        {200: ok(CitationStyleDeleted, "Deleted; a Library setting that named it reads as the default."),
         404: _NOT_FOUND},
        description="Needs the Library owner.")),
)
