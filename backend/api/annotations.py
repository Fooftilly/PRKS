"""PDF annotations and Work PDF bytes HTTP controller (PG-C3 #642).

``GET /api/works/:id/annotations-state`` and ``/annotations-snapshot`` (with
ETags), ``GET /api/works/:id/pdf-materialization``, ``GET
/api/works/:id/annotations`` (the list as a JSON string), ``POST
/api/works/:id/annotations/adopt``
(byte-only user markup), ``POST /api/works/:id/pdf`` (replace the PDF, or with
``materialized_annotation_set_revision`` store the viewer's rendered
annotations) and ``POST /api/works/:id/annotations`` (the legacy full-list
replace at ``canonical_annotation_set_revision``). Paths, codes and bodies are
those the HTTP adapter answered before PG-C3.

The calls go to ``backend/domain/annotations/annotation_commands`` for the
admitted request's context on the served Library's database, so authorization
stays in ``library_query`` and ``library_command``. A save is confirmed by
its own ``200``, which is sent only after its transaction committed; nothing
about a save is kept in this process (#1331).

The commands are imported when a route runs: the served app loads no
PostgreSQL module at import time (``tests/test_e2e_policy.py``).
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from backend.api.common import objects, revalidated
from backend.pdf.pdf_annotations import WorkAnnotationError
from backend.pdf.work_pdf_replace import stale_materialization_body
from backend.web.http import Request, Response, error_response, json_response
from backend.web.routing import route


def annotations_state(req: Request) -> Response:
    from backend.domain.annotations import annotation_commands

    # The revision state alone; a subset of the snapshot.
    return revalidated(req, "work-annotations-state",
                       annotation_commands.state(*req.actor, req.params["work_id"]))


def annotations_snapshot(req: Request) -> Response:
    from backend.domain.annotations import annotation_commands

    # One snapshot: items + revisions + materialization revisions.
    return revalidated(req, "work-annotations-snapshot",
                       annotation_commands.snapshot(*req.actor, req.params["work_id"]))


def pdf_materialization(req: Request) -> Response:
    from backend.domain.annotations import annotation_commands

    data = annotation_commands.materialization(*req.actor, req.params["work_id"])
    if data is None:
        return json_response(req, 404, {"error": "Work not found"})
    return json_response(req, 200, data)


def annotation_list(req: Request) -> Response:
    from backend.domain.annotations import annotation_commands

    w_id = req.params["work_id"]
    return json_response(req, 200, {"work_id": w_id,
                                    "annotations_json": annotation_commands.annotations_json(*req.actor, w_id)})


def adopt(req: Request) -> Response:
    from backend.domain.annotations import annotation_commands

    viewer_annotations = req.data.get("viewer_annotations")
    if not isinstance(viewer_annotations, list):
        return json_response(req, 400, {"error": "viewer_annotations must be a JSON list",
                                        "code": "malformed_annotation_payload"})
    try:
        result = annotation_commands.adopt(*req.actor, req.params["work_id"], viewer_annotations)
    except WorkAnnotationError as e:
        return json_response(req, e.http_status, {"error": str(e), "code": e.code})
    return json_response(req, 200, result)


def post_pdf(req: Request) -> Response:
    """``POST /api/works/{id}/pdf``: replace the PDF, or store the viewer's rendered annotations.

    With ``materialized_annotation_set_revision`` the bytes are the viewer's
    rendered annotations and mark the Work materialized; without it they
    replace the Work's PDF.
    """
    from backend.domain.annotations import annotation_commands
    from backend.pdf.pdf_materialization import STALE_CODE

    data = req.data
    file_b64 = data.get("file_b64", "")
    claimed_set_rev = data.get("materialized_annotation_set_revision", None)
    if not file_b64:
        return error_response(400, "No file_b64 provided")
    if not isinstance(file_b64, str):
        return json_response(req, 400, {"error": "Invalid file_b64 payload"})
    try:
        pdf_bytes = base64.b64decode(file_b64, validate=True)
    except (binascii.Error, ValueError):
        return json_response(req, 400, {"error": "Invalid file_b64 payload"})

    def write(out: Any) -> None:
        out.write(pdf_bytes)

    database, ctx = req.actor
    work_id = req.params["work_id"]
    try:
        if claimed_set_rev is None:
            result = annotation_commands.replace_pdf(database, ctx, objects(req), work_id, write)
        else:
            result = annotation_commands.materialize_pdf(database, ctx, objects(req), work_id, write,
                                                         claimed_set_rev)
    except LookupError:
        # The commands refuse a missing Work and a Work without managed PDF
        # bytes alike; answer each with its own 404.
        if annotation_commands.materialization(database, ctx, work_id) is None:
            return json_response(req, 404, {"error": "Work not found"})
        return json_response(req, 404, {"error": "Work has no managed PDF"})
    except ValueError as e:
        if str(e) == STALE_CODE:
            return json_response(req, 409, stale_materialization_body(
                annotation_commands.materialization(database, ctx, work_id)))
        return json_response(req, 400, {"error": "Invalid materialization revision"})
    return json_response(req, 200, {**result, "status": "success"})


def replace_all(req: Request) -> Response:
    from backend.domain.annotations import annotation_commands

    # Compat full-list replace. Product writes use durable
    # CREATE/SET/DELETE_PDF_ANNOTATION; this remains for online-legacy
    # when the durable store is unavailable (Slice G retirement).
    data = req.data
    if "annotations_json" not in data:
        return json_response(req, 400, {"error": "annotations_json is required",
                                        "code": "malformed_annotation_payload"})
    annotations_json = data.get("annotations_json")
    # Legacy saves are annotations-first: this endpoint only replaces
    # metadata and returns the exact replace generation. Never mark PDF
    # materialization here. Marking belongs to POST /pdf with
    # materialized_annotation_set_revision (the same claim path as durable).
    # The client must send its acknowledged tip so a stale viewer cannot
    # overwrite newer annotations or delete siblings omitted locally.
    if "canonical_annotation_set_revision" not in data:
        return json_response(req, 400, {"error": "canonical_annotation_set_revision is required",
                                        "code": "malformed_annotation_payload"})
    try:
        replace_gen = annotation_commands.save(*req.actor, req.params["work_id"], annotations_json,
                                               base_set_revision=data.get("canonical_annotation_set_revision"))
    except WorkAnnotationError as e:
        return json_response(req, e.http_status, {"error": str(e), "code": e.code})
    return json_response(req, 200, {
        "status": "saved",
        "path": "legacy-full-list",
        "canonical_annotation_set_revision": replace_gen,
    })


ROUTES = (
    route("GET", "/api/works/{work_id}/annotations-state", annotations_state),
    route("GET", "/api/works/{work_id}/annotations-snapshot", annotations_snapshot),
    route("GET", "/api/works/{work_id}/pdf-materialization", pdf_materialization),
    route("GET", "/api/works/{work_id}/annotations", annotation_list),
    route("POST", "/api/works/{work_id}/annotations", replace_all),
    route("POST", "/api/works/{work_id}/annotations/adopt", adopt),
    route("POST", "/api/works/{work_id}/pdf", post_pdf),
)
