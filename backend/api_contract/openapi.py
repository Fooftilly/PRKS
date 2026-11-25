"""The one OpenAPI 3.1 document, built from the route table (#1431).

Every route of ``backend.api.ROUTES`` that declares a ``contract``
(:class:`backend.api_contract.operation.Operation`) is one operation here.
Schemas are generated from the Pydantic boundary models in
``backend/api_contract/``, so a route, its models and its description come
from one declaration. ``GET /api/openapi.json`` serves this document and
``docs/api/openapi.json`` is its checked-in copy (the TypeScript types and
the Schemathesis runs read that file).

The builder adds the answers every route of a kind can send, so routes
declare only their own:

- a JSON body route: ``400 invalid_request`` (not one JSON object),
  ``413 request_too_large`` and ``415 unsupported_media_type``
  (``backend.web.dispatch.read_json_body``);
- an ``/api/`` route: admission (``backend/api/session.py``): ``401``
  without a live session, ``403`` without the capability or from another
  Origin, ``503`` when the Library is unavailable or in maintenance; a
  mutation also ``400 LIBRARY_EPOCH_REQUIRED`` and ``409`` for another
  Library epoch.
"""

from __future__ import annotations

import functools
import re
from typing import Any, Iterable, Optional

from pydantic import BaseModel, TypeAdapter

from backend.api_contract.errors import ApiErrorEnvelope
from backend.api_contract.operation import JSON, Operation, Reply, RequestBody, Schema

CONTRACT_VERSION = "0.2.0"
REF = "#/components/schemas/{model}"
_PATH_PARAMETER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::path)?\}")

# The answers every route of a kind can send: components.responses.
_SHARED: dict[str, str] = {
    "InvalidRequest": "The body is not one JSON object, or not JSON (invalid_request).",
    "RequestTooLarge": "The body is larger than the JSON body limit (request_too_large).",
    "UnsupportedMediaType": "No or another Content-Type than application/json (unsupported_media_type).",
    "NotAuthenticated": "No live session (not_authenticated).",
    "Forbidden": "The session's role lacks the route's capability, or the Origin is not allowed.",
    "Unavailable": "The Library is unavailable (unavailable).",
    "UnavailableOrMaintenance": "The Library is unavailable or in maintenance (unavailable, MAINTENANCE).",
    "EpochRequired": "No or a malformed PRKS-Library-Epoch header (LIBRARY_EPOCH_REQUIRED).",
    "BadRequest": ("The body is not one JSON object, or not JSON (invalid_request), or no or a "
                   "malformed PRKS-Library-Epoch header (LIBRARY_EPOCH_REQUIRED)."),
    "EpochChanged": "PRKS-Library-Epoch names another epoch than the Library's (LIBRARY_EPOCH_CHANGED).",
}


class SchemaConflict(ValueError):
    """Two different schemas were published under one component name."""


class _Components:
    def __init__(self) -> None:
        self.schemas: dict[str, Any] = {}

    def _add(self, name: str, body: dict[str, Any]) -> None:
        body = {k: v for k, v in body.items() if k != "title"}  # the component name says it
        known = self.schemas.get(name)
        if known is not None and known != body:
            raise SchemaConflict(f"two different schemas are named {name!r}")
        self.schemas[name] = body

    def _fold(self, schema: dict[str, Any]) -> dict[str, Any]:
        for name, body in (schema.pop("$defs", None) or {}).items():
            self._add(name, body)
        return schema

    def ref(self, spec: Schema) -> dict[str, Any]:
        """The JSON Schema for ``spec``, its named models folded into components."""
        if isinstance(spec, dict):
            return dict(spec)
        if isinstance(spec, type) and issubclass(spec, BaseModel):
            schema = self._fold(spec.model_json_schema(ref_template=REF))
            name = spec.__name__
            self._add(name, schema)
            return {"$ref": REF.format(model=name)}
        return self._fold(TypeAdapter(spec).json_schema(ref_template=REF))


def openapi_path(pattern: str) -> str:
    """The OpenAPI path of a route pattern: ``{name:path}`` is ``{name}``."""
    return _PATH_PARAMETER.sub(lambda m: "{" + m.group(1) + "}", pattern)


def _content(components: _Components, media_type: str | tuple[str, ...], spec: Schema) -> dict[str, Any]:
    schema = components.ref(spec)
    types = (media_type,) if isinstance(media_type, str) else media_type
    return {one: {"schema": dict(schema)} for one in types}


def _reply(components: _Components, reply: Reply) -> dict[str, Any]:
    out: dict[str, Any] = {"description": reply.description}
    if reply.headers:
        out["headers"] = {name: {"description": h.description, "schema": dict(h.schema),
                                 **({"required": True} if h.required else {})}
                          for name, h in reply.headers.items()}
    if reply.schema is not None:
        out["content"] = _content(components, reply.media_type, reply.schema)
    return out


_OPTIONAL_JSON = "Left out, the request still sends Content-Type: application/json (else 415)."


def _request(components: _Components, request: RequestBody) -> dict[str, Any]:
    out: dict[str, Any] = {"required": request.required,
                           "content": _content(components, request.media_type, request.schema)}
    description = request.description
    if not request.required and request.media_type == JSON:
        # The dispatcher reads an optional JSON body only under the JSON Content-Type (#1424).
        description = (f"{description} " if description else "") + _OPTIONAL_JSON
    if description:
        out["description"] = description
    return out


def _shared_for(route: Any) -> dict[str, str]:
    """``status -> components.responses name`` this route can send besides its own."""
    from backend.api.session import epoch_fenced
    from backend.web.routing import Body

    shared: dict[str, str] = {}
    if route.pattern == "/api" or route.pattern.startswith("/api/"):
        shared.update({"401": "NotAuthenticated", "403": "Forbidden", "503": "Unavailable"})
        if epoch_fenced(route.method, route.pattern):  # library_command: the maintenance lock and epoch fence
            shared.update({"400": "EpochRequired", "409": "EpochChanged", "503": "UnavailableOrMaintenance"})
    if route.body_kind is Body.JSON:
        bad = "BadRequest" if shared.get("400") == "EpochRequired" else "InvalidRequest"
        shared.update({"400": bad, "413": "RequestTooLarge", "415": "UnsupportedMediaType"})
    return shared


def _operation(components: _Components, route: Any, contract: Operation, shared: set[str]) -> dict[str, Any]:
    out: dict[str, Any] = {"operationId": contract.id, "summary": contract.summary, "tags": [contract.tag]}
    if contract.description:
        out["description"] = contract.description
    if contract.params:
        out["parameters"] = [
            {"name": p.name, "in": p.where, "required": p.required, "schema": dict(p.schema),
             **({"description": p.description} if p.description else {})}
            for p in contract.params
        ]
    if contract.request is not None:
        out["requestBody"] = _request(components, contract.request)
    responses = {str(status): _reply(components, reply) for status, reply in contract.responses.items()}
    for status, name in _shared_for(route).items():
        if status in responses:
            own = responses[status]
            if "$ref" not in own and route.method != "HEAD":  # the route's own answer, and the shared one
                own["description"] = f"{own['description']} Also: {_SHARED[name]}"
            continue
        if route.method == "HEAD":
            responses[status] = {"description": _SHARED[name]}  # a HEAD answer has no body
        else:
            responses[status] = {"$ref": f"#/components/responses/{name}"}
            shared.add(name)
    out["responses"] = dict(sorted(responses.items()))
    return out


def untyped(routes: Iterable[Any]) -> list[tuple[str, str]]:
    """``(method, pattern)`` of each route that declares no contract yet."""
    return [(r.method, r.pattern) for r in routes if r.contract is None]


def build_document(routes: Iterable[Any]) -> dict[str, Any]:
    """The OpenAPI document of ``routes`` (routes without a contract are left out)."""
    components = _Components()
    components.ref(ApiErrorEnvelope)
    paths: dict[str, dict[str, Any]] = {}
    seen_ids: dict[str, str] = {}
    tags: set[str] = set()
    shared: set[str] = set()
    templates: dict[str, str] = {}
    for route in sorted(routes, key=lambda r: (openapi_path(r.pattern), r.method)):
        contract: Optional[Operation] = route.contract
        if contract is None:
            continue
        where = f"{route.method} {route.pattern}"
        if contract.id in seen_ids:
            raise ValueError(f"operationId {contract.id!r} is used by {seen_ids[contract.id]} and {where}")
        seen_ids[contract.id] = where
        tags.add(contract.tag)
        path = openapi_path(route.pattern)
        template = _PATH_PARAMETER.sub("{}", route.pattern)
        if templates.setdefault(template, path) != path:
            raise ValueError(f"{where} uses {path!r}, a template equivalent to {templates[template]!r}")
        item = paths.setdefault(path, {})
        if route.method.lower() in item:
            raise ValueError(f"{where} duplicates an operation at {path!r}")
        names = _PATH_PARAMETER.findall(route.pattern)
        if names and "parameters" not in item:
            item["parameters"] = [{"name": n, "in": "path", "required": True, "schema": {"type": "string"}}
                                  for n in names]
        item[route.method.lower()] = _operation(components, route, contract, shared)
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "PRKS API",
            "version": CONTRACT_VERSION,
            "description": (
                "The PRKS HTTP API, generated from the route table (backend/api) and the "
                "Pydantic boundary models (backend/api_contract). Every /api/ request needs "
                "a live session; a mutation also sends the PRKS-Library-Epoch header."
            ),
        },
        "tags": [{"name": tag} for tag in sorted(tags)],
        "paths": paths,
        "components": {
            "schemas": dict(sorted(components.schemas.items())),
            "responses": {
                name: {"description": text,
                       "content": {"application/json": {"schema": {"$ref": REF.format(model="ApiErrorEnvelope")}}}}
                for name, text in _SHARED.items() if name in shared
            },
        },
    }


@functools.cache
def openapi_document() -> dict[str, Any]:
    """The served document: ``backend.api.ROUTES``. Callers must not change it."""
    from backend.api import ROUTES

    return build_document(ROUTES)
