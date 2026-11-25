"""Hold every live answer of a typed route to its declared contract (#1431).

:func:`checked_routes` wraps each typed route of a :class:`RouteTable` so
that, while a test server serves it:

- the answer's status is one the route's operation documents (its own or a
  shared refusal of ``backend.api_contract.openapi``), with every header
  that status documents as required;
- a JSON answer validates against the documented schema for that status
  (a ``gzip`` Content-Encoding is undone first), and an answer with no body
  is one whose status documents none;
- the JSON body of a request answered with ``2xx`` validates against the
  route's request schema, and a request answered with ``2xx`` without a
  body is one whose body the contract marks optional, so a request model is
  never stricter than what the route accepts.

A violation, or an answer the checker cannot read, is recorded, not
raised, so the route still answers as it would; :class:`ContractCheck` fails the test that saw it. Exceptions a route
raises go to the dispatcher unchecked, and so do the answers the dispatcher
gives before or around a route (admission refusals, the JSON body gate, the
exception mapper): those are the shared ``components.responses``, whose
presence ``tests/test_openapi_document.py`` checks per route.
"""

from __future__ import annotations

import dataclasses
import gzip
import json
from typing import Any, Optional

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from backend.api_contract.openapi import openapi_document, openapi_path
from backend.web.http import Request, Response
from backend.web.routing import Route, RouteTable

_ROOT = "urn:prks:openapi"


class ContractCheck:
    def __init__(self) -> None:
        self.document = openapi_document()
        self.violations: list[str] = []
        self._registry = Registry().with_resource(
            _ROOT, Resource.from_contents(self.document, default_specification=DRAFT202012))
        self._validators: dict[str, Draft202012Validator] = {}

    def _schema_errors(self, schema: dict[str, Any], value: Any) -> list[str]:
        key = json.dumps(schema, sort_keys=True)
        validator = self._validators.get(key)
        if validator is None:
            wrapped = {"$id": "urn:prks:check", "allOf": [_rebase(schema)]}
            validator = Draft202012Validator(wrapped, registry=self._registry)
            self._validators[key] = validator
        return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message[:200]}"
                for e in validator.iter_errors(value)][:3]

    def _operation(self, route: Route) -> dict[str, Any]:
        return self.document["paths"][openapi_path(route.pattern)][route.method.lower()]

    def _reply(self, reply: dict[str, Any]) -> dict[str, Any]:
        ref = reply.get("$ref")
        if ref is not None:
            return self.document["components"]["responses"][ref.rsplit("/", 1)[1]]
        return reply

    def check(self, route: Route, request: Request, response: Response) -> None:
        where = f"{route.method} {route.pattern} -> {response.status}"
        try:
            self._check(where, route, request, response)
        except Exception as exc:  # the checker never changes what the route answers
            self.violations.append(f"{where}: could not be checked ({type(exc).__name__}: {exc})")

    def _check(self, where: str, route: Route, request: Request, response: Response) -> None:
        operation = self._operation(route)
        reply = operation["responses"].get(str(response.status))
        if reply is None:
            self.violations.append(f"{where}: status not documented")
            return
        for name, header in (self._reply(reply).get("headers") or {}).items():
            if header.get("required") and response.header(name) is None:
                self.violations.append(f"{where}: required header {name} missing")
        content = self._reply(reply).get("content") or {}
        media = (response.header("Content-Type") or "").split(";", 1)[0].strip().lower()
        if response.stream is None and route.method != "HEAD":
            payload = _decoded(response)
            if payload:
                if media not in content:
                    self.violations.append(f"{where}: {media or 'no Content-Type'} not documented")
                elif media == "application/json":
                    for err in self._schema_errors(content[media]["schema"], json.loads(payload)):
                        self.violations.append(f"{where}: answer {err}")
            elif "application/json" in content:  # an empty text answer (an empty BibTeX entry) is one
                self.violations.append(f"{where}: no body, but the contract documents JSON")
            elif content and media not in content:
                self.violations.append(f"{where}: empty {media or 'answer without Content-Type'} not documented")
        body = operation.get("requestBody")
        if body and "application/json" not in body["content"]:
            body = None  # a byte stream (an upload) is not read as JSON
        if 200 <= response.status < 300 and body and request.json_absent and body.get("required"):
            self.violations.append(f"{where}: request had no body, but the contract requires one")
        elif 200 <= response.status < 300 and body and not request.json_absent and request.json is not None:
            schema = body["content"]["application/json"].get("schema")
            if schema is not None:
                for err in self._schema_errors(schema, request.json):
                    self.violations.append(f"{where}: request {err}")

    def wrap(self, table: RouteTable) -> RouteTable:
        return RouteTable(self._wrap(one) for one in table.routes)

    def _wrap(self, route: Route) -> Route:
        if route.contract is None:
            return route
        handler = route.handler

        def checked(request: Request) -> Response:
            response = handler(request)
            self.check(route, request, response)
            return response

        return dataclasses.replace(route, handler=checked)

    def report(self) -> Optional[str]:
        if not self.violations:
            return None
        return "answers outside the OpenAPI contract:\n  " + "\n  ".join(dict.fromkeys(self.violations))


def _decoded(response: Response) -> bytes:
    """The body as the route meant it: a ``gzip`` Content-Encoding undone."""
    body = response.body or b""
    if body and (response.header("Content-Encoding") or "").strip().lower() == "gzip":
        return gzip.decompress(body)
    return body


def _rebase(schema: Any) -> Any:
    """``#/components/...`` refs point into the document resource."""
    if isinstance(schema, dict):
        return {k: (_ROOT + v if k == "$ref" and isinstance(v, str) and v.startswith("#/") else _rebase(v))
                for k, v in schema.items()}
    if isinstance(schema, list):
        return [_rebase(v) for v in schema]
    return schema
