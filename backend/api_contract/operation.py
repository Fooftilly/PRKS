"""What a route declares about its wire contract (#1431).

A :class:`backend.web.routing.Route` carries an :class:`Operation`: its
OpenAPI operation id, summary, tag, query and header parameters, request
body and the replies it sends by status. :mod:`backend.api_contract.openapi`
builds the one OpenAPI document from the route table, so a route and its
description cannot be declared in two places.

A schema is a Pydantic model class (published as a named component and
referenced), any other type Pydantic describes (``list[Model]``, a
discriminated union), or a plain JSON Schema ``dict`` used as written.

The route's path parameters come from its pattern. The refusals every route
of a kind shares (the JSON body gate, admission) are added by the builder;
a route declares only its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal, Mapping, Optional

from backend.api_contract.errors import ApiErrorEnvelope

Schema = Any
"""A Pydantic model class, another type Pydantic describes, or a JSON Schema dict."""

JSON = "application/json"
PDF = "application/pdf"
BINARY: dict[str, Any] = {"type": "string", "format": "binary"}


@dataclass(frozen=True)
class Header:
    description: str
    schema: Mapping[str, Any] = field(default_factory=lambda: {"type": "string"})
    required: bool = False


@dataclass(frozen=True)
class Reply:
    """One response: ``schema`` ``None`` sends no body.

    ``media_type`` may name several types the body is sent as (one schema).
    """

    description: str
    schema: Schema = None
    media_type: str | tuple[str, ...] = JSON
    headers: Mapping[str, Header] = field(default_factory=dict)
    #: For a stream of JSON texts (NDJSON): the shape of each line, published
    #: as the media type's ``x-line-schema``.
    line_schema: Schema = None


@dataclass(frozen=True)
class Param:
    """A query or header parameter (path parameters come from the pattern)."""

    name: str
    where: Literal["query", "header"] = "query"
    schema: Mapping[str, Any] = field(default_factory=lambda: {"type": "string"})
    required: bool = False
    description: str = ""


@dataclass(frozen=True)
class RequestBody:
    schema: Schema
    description: str = ""
    media_type: str = JSON
    required: bool = True


@dataclass(frozen=True)
class Operation:
    id: str
    summary: str
    tag: str
    responses: Mapping[int, Reply]
    request: Optional[RequestBody] = None
    params: tuple[Param, ...] = ()
    description: str = ""

    def head(self) -> Operation:
        """The same operation answered to ``HEAD``: no bodies, the same headers."""
        return replace(
            self,
            id=f"{self.id}Head",
            summary=f"{self.summary} (headers only)",
            request=None,
            responses={status: replace(reply, schema=None) for status, reply in self.responses.items()},
        )


IF_NONE_MATCH = Param(
    "If-None-Match", "header",
    description="Conditional read: when equal to the current ETag, responds 304 Not Modified (bodyless).",
)


def ok(schema: Schema, description: str, **kwargs: Any) -> Reply:
    return Reply(description, schema, **kwargs)


def error(description: str, schema: Schema = ApiErrorEnvelope) -> Reply:
    """A refusal with the common envelope ``{"error"[, "code", "message"]}``."""
    return Reply(description, schema)


def plain(description: str) -> Reply:
    """A ``text/plain`` refusal from :func:`backend.web.http.error_response` (``"<status> <message>"``)."""
    return Reply(description, {"type": "string"}, media_type="text/plain")


def body(schema: Schema, description: str = "", **kwargs: Any) -> RequestBody:
    return RequestBody(schema, description, **kwargs)


ETAG = Header("Opaque representation token for conditional GETs.")
NOT_MODIFIED = Reply("Not modified: If-None-Match matched the current ETag. Bodyless.", headers={"ETag": ETAG})


def revalidated(op_id: str, summary: str, tag: str, schema: Schema, description: str,
                more: Optional[Mapping[int, Reply]] = None, params: tuple[Param, ...] = ()) -> Operation:
    """A read answered with a representation ETag (``backend.api.common.revalidated``): 200 or 304."""
    return Operation(op_id, summary, tag,
                     {200: ok(schema, description, headers={"ETag": ETAG}), 304: NOT_MODIFIED, **(more or {})},
                     params=(*params, IF_NONE_MATCH))
