"""Diagnostics and the served API description (#1424).

- ``GET /api/diagnostics/performance``: this process's request counters
  (``backend.performance``); ``POST /api/diagnostics/performance/reset``
  ``{"confirm": ...}`` clears them.
- ``GET /api/diagnostics/jobs``: owner-only job diagnostics (§12.4). A
  server started without jobs answers it like any unknown route.
- ``POST /api/client-errors``: a browser error report, logged as metadata
  only (``backend.log_safety.client_error_log_fields``).
- ``GET /api/openapi.json`` and ``/api/openapi/<slice>.json``: the OpenAPI
  vertical slices (#180), not yet the full PRKS surface.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from backend.api_contract import openapi
from backend.api_contract.boundary import dump_response, parse_request
from backend.api_contract.jobs import JobDiagnostics
from backend.api_contract.performance import (
    PerformanceDiagnosticsReset,
    PerformanceDiagnosticsResetRequest,
    PerformanceSnapshot,
)
from backend.log_safety import client_error_log_fields, format_client_error_log
from backend.performance import reset as reset_performance
from backend.performance import snapshot as performance_snapshot
from backend.web.http import Request, Response, error_response, json_response
from backend.web.routing import Route, route

LOGGER = logging.getLogger("prks.server")


def performance(req: Request) -> Response:
    return json_response(req, 200, dump_response(PerformanceSnapshot, performance_snapshot()))


def performance_reset(req: Request) -> Response:
    if req.json_absent:
        return json_response(req, 400, {"error": "JSON object body required", "code": "invalid_request"})
    _parsed, err = parse_request(PerformanceDiagnosticsResetRequest, req.data)
    if err is not None:
        return json_response(req, 400, err)
    reset_performance()
    return json_response(req, 200, dump_response(PerformanceDiagnosticsReset, {"status": "reset"}))


def jobs(req: Request) -> Response:
    """Owner-only job diagnostics; ``backend.api.session`` admitted the request for ADMIN_LIBRARY."""
    runtime = req.app.jobs
    if runtime is None:
        return error_response(404, "API endpoint not found")
    body = runtime.diagnostics()
    if body is None:
        return json_response(req, 503, {"error": "unavailable", "code": "unavailable"})
    return json_response(req, 200, dump_response(JobDiagnostics, body))


def client_error(req: Request) -> Response:
    try:
        payload = client_error_log_fields(req.data)
    except ValueError as e:
        return json_response(req, 400, {"error": str(e)})
    LOGGER.error(format_client_error_log(payload, request_id=req.request_id))
    return json_response(req, 200, {"status": "logged", "request_id": req.request_id})


def _document(build: Callable[[], dict[str, Any]]) -> Callable[[Request], Response]:
    def serve(req: Request) -> Response:
        return json_response(req, 200, build())

    return serve


_DOCUMENTS: dict[str, Callable[[], dict[str, Any]]] = {
    "/api/openapi.json": openapi.positions_openapi_document,
    "/api/openapi/positions.json": openapi.positions_openapi_document,
    "/api/openapi/performance-diagnostics.json": openapi.performance_diagnostics_openapi_document,
    "/api/openapi/deployment-settings.json": openapi.deployment_settings_openapi_document,
    "/api/openapi/storage-status.json": openapi.storage_status_openapi_document,
    "/api/openapi/job-diagnostics.json": openapi.job_diagnostics_openapi_document,
    "/api/openapi/publishers.json": openapi.publishers_openapi_document,
    "/api/openapi/citations.json": openapi.citations_openapi_document,
    "/api/openapi/saved-views.json": openapi.saved_views_openapi_document,
    "/api/openapi/processing-items.json": openapi.processing_items_openapi_document,
}

ROUTES: tuple[Route, ...] = (
    route("GET", "/api/diagnostics/performance", performance),
    route("POST", "/api/diagnostics/performance/reset", performance_reset),
    route("GET", "/api/diagnostics/jobs", jobs),
    route("POST", "/api/client-errors", client_error),
    *(route("GET", path, _document(build)) for path, build in _DOCUMENTS.items()),
)
