"""Diagnostics and the served API description (#1424).

- ``GET /api/diagnostics/performance``: this process's request counters
  (``backend.runtime.performance``); ``POST /api/diagnostics/performance/reset``
  ``{"confirm": ...}`` clears them.
- ``GET /api/diagnostics/jobs``: owner-only job diagnostics (§12.4). A
  server started without jobs answers it like any unknown route.
- ``POST /api/client-errors``: a browser error report, logged as metadata
  only (``backend.runtime.log_safety.client_error_log_fields``).
- ``GET /api/openapi.json``: the OpenAPI document of the route table
  (``backend.api_contract.openapi``, #1431).
"""

from __future__ import annotations

import logging
from typing import Any

from backend.api_contract.openapi import openapi_document
from backend.api_contract.operation import Operation, Reply, body, error, ok, plain
from backend.api_contract.boundary import dump_response, parse_request
from backend.api_contract.client_errors import ClientErrorLogged, ClientErrorReport
from backend.api_contract.jobs import JobDiagnostics
from backend.api_contract.performance import (
    PerformanceDiagnosticsReset,
    PerformanceDiagnosticsResetRequest,
    PerformanceSnapshot,
)
from backend.runtime.log_safety import client_error_log_fields, format_client_error_log
from backend.runtime.performance import reset as reset_performance
from backend.runtime.performance import snapshot as performance_snapshot
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


def document(req: Request) -> Response:
    return json_response(req, 200, openapi_document())


_TAG = "diagnostics"

ROUTES: tuple[Route, ...] = (
    route("GET", "/api/diagnostics/performance", performance, contract=Operation(
        "getPerformanceDiagnostics", "Runtime performance diagnostics snapshot", _TAG,
        {200: ok(PerformanceSnapshot, "Aggregate in-process measurements.")})),
    route("POST", "/api/diagnostics/performance/reset", performance_reset, contract=Operation(
        "resetPerformanceDiagnostics", "Clear the in-process performance window", _TAG,
        {200: ok(PerformanceDiagnosticsReset, "Measurements were reset."),
         400: error("No body, or a body that is not an empty object.")},
        request=body(PerformanceDiagnosticsResetRequest,
                     "Empty JSON object. Additional properties are rejected."))),
    route("GET", "/api/diagnostics/jobs", jobs, contract=Operation(
        "getJobDiagnostics", "Job worker and queue diagnostics", _TAG,
        {200: ok(JobDiagnostics, "The embedded worker's state and process-local counters, and the "
                                 "queue's depth and oldest age by job kind and state."),
         404: plain("The server runs without a jobs runtime (test servers only)."),
         503: error("The database cannot be read (unavailable).")})),
    route("POST", "/api/client-errors", client_error, contract=Operation(
        "reportClientError", "Log a browser error as metadata only", _TAG,
        {200: ok(ClientErrorLogged, "Logged.")},
        request=body(ClientErrorReport,
                     "Every field is optional; a value the log does not take is replaced, never refused."))),
    route("GET", "/api/openapi.json", document, contract=Operation(
        "getOpenApiDocument", "This OpenAPI document", "meta",
        {200: ok({"type": "object"}, "The OpenAPI 3.1 document of every typed route.")})),
)
