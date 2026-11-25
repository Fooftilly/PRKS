"""Hold every live answer of a typed route to its declared contract (#1431).

:func:`checked_routes` wraps each typed route of a :class:`RouteTable` so
that, while a test server serves it:

- the answer's status is one the route's operation documents (its own or a
  shared refusal of ``backend.api_contract.openapi``);
- a JSON answer validates against the documented schema for that status;
- the JSON body of a request answered with ``2xx`` validates against the
  route's request schema, so a request model is never stricter than what
  the route accepts.

A violation is recorded, not raised, so the route still answers as it
would; :class:`ContractCheck` fails the test that saw it. Exceptions a route
raises go to the dispatcher unchecked.
"""

from __future__ import annotations

import dataclasses
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
        operation = self._operation(route)
        reply = operation["responses"].get(str(response.status))
        if reply is None:
            self.violations.append(f"{where}: status not documented")
            return
        content = self._reply(reply).get("content") or {}
        media = (response.header("Content-Type") or "").split(";", 1)[0].strip()
        if response.stream is None and response.body and route.method != "HEAD":
            if media not in content:
                self.violations.append(f"{where}: {media or 'no Content-Type'} not documented")
            elif media == "application/json":
                for err in self._schema_errors(content[media]["schema"], json.loads(response.body)):
                    self.violations.append(f"{where}: answer {err}")
        body = operation.get("requestBody")
        if 200 <= response.status < 300 and body and not request.json_absent and request.json is not None:
            schema = body["content"].get("application/json", {}).get("schema")
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


def _rebase(schema: Any) -> Any:
    """``#/components/...`` refs point into the document resource."""
    if isinstance(schema, dict):
        return {k: (_ROOT + v if k == "$ref" and isinstance(v, str) and v.startswith("#/") else _rebase(v))
                for k, v in schema.items()}
    if isinstance(schema, list):
        return [_rebase(v) for v in schema]
    return schema
