"""The route table: ``(method, pattern) -> handler(request) -> Response`` (#1424).

Each family declares its routes in ``backend/api/<family>.py`` as a
``ROUTES`` tuple of :class:`Route`; :mod:`backend.api` joins them into the
served :class:`RouteTable`. A pattern is a path whose ``{name}`` segments
match one non-empty segment (as sent, not percent-decoded) and whose last
segment may be ``{name:path}``, the rest of the path. Literal segments win
over parameters, so ``/api/tags/merge`` is never ``/api/tags/{tag_id}``.

The table answers 404 for a path no route has and 405 (with ``Allow``) for
a path whose routes take other methods.

A route's ``body`` says how the dispatcher reads the request body before
the handler runs: ``JSON`` parses one JSON object (``Request.json``) and
refuses anything else with 400 (:mod:`backend.web.dispatch`), ``NONE``
reads nothing, and ``STREAM`` leaves the body to the handler (uploads);
an answer that leaves body unread closes the connection.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Union

from backend.web.http import Request, Response

Handler = Callable[[Request], Response]


class Body(enum.Enum):
    NONE = "none"
    JSON = "json"
    STREAM = "stream"


_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


@dataclass(frozen=True)
class _Segment:
    literal: Optional[str]  # None: a parameter
    name: str = ""
    rest: bool = False  # {name:path}: the rest of the path


def _compile(pattern: str) -> tuple[_Segment, ...]:
    if not pattern.startswith("/"):
        raise ValueError(f"route pattern must start with '/': {pattern!r}")
    segments: list[_Segment] = []
    parts = pattern.split("/")[1:]
    for index, part in enumerate(parts):
        if part.startswith("{") and part.endswith("}"):
            name, _, kind = part[1:-1].partition(":")
            if kind not in ("", "path") or not name.isidentifier():
                raise ValueError(f"bad route parameter {part!r} in {pattern!r}")
            if kind == "path" and index != len(parts) - 1:
                raise ValueError(f"{{{name}:path}} must be the last segment of {pattern!r}")
            segments.append(_Segment(None, name, rest=kind == "path"))
        else:
            if "{" in part or "}" in part:
                raise ValueError(f"bad route segment {part!r} in {pattern!r}")
            segments.append(_Segment(part))
    return tuple(segments)


@dataclass(frozen=True)
class Route:
    """One route. ``body`` defaults to ``JSON`` for POST/PUT/PATCH, else ``NONE``."""

    method: str
    pattern: str
    handler: Handler
    body: Optional[Body] = None
    segments: tuple[_Segment, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "method", self.method.upper())
        object.__setattr__(self, "segments", _compile(self.pattern))
        if self.body is None:
            object.__setattr__(self, "body", Body.JSON if self.method in _BODY_METHODS else Body.NONE)

    @property
    def body_kind(self) -> Body:
        assert self.body is not None
        return self.body

    def match(self, parts: list[str]) -> Optional[dict[str, str]]:
        """The parameters when the path's segments ``parts`` match, else ``None``."""
        params: dict[str, str] = {}
        segments = self.segments
        for index, segment in enumerate(segments):
            if segment.rest:
                rest = "/".join(parts[index:])
                if index >= len(parts) or not rest:
                    return None
                params[segment.name] = rest
                return params
            if index >= len(parts):
                return None
            part = parts[index]
            if segment.literal is not None:
                if part != segment.literal:
                    return None
            elif not part:
                return None
            else:
                params[segment.name] = part
        return params if len(parts) == len(segments) else None

    def rank(self) -> tuple[int, ...]:
        """Sort key: literal segments before parameters, a rest parameter last."""
        return tuple(0 if s.literal is not None else (2 if s.rest else 1) for s in self.segments)


def route(method: str, pattern: str, handler: Handler, body: Optional[Body] = None) -> Route:
    return Route(method, pattern, handler, body)


@dataclass(frozen=True)
class Match:
    route: Route
    params: dict[str, str]


@dataclass(frozen=True)
class NoRoute:
    """No route takes the path (404) or none takes the method (405, ``allowed`` set)."""

    allowed: tuple[str, ...] = ()


class RouteTable:
    """Every served route; :meth:`resolve` finds one for a method and path."""

    def __init__(self, routes: Iterable[Route]) -> None:
        self.routes: tuple[Route, ...] = tuple(sorted(routes, key=Route.rank))
        seen: set[tuple[str, tuple[Optional[str], ...]]] = set()
        for one in self.routes:
            key = (one.method, tuple(s.literal if s.literal is not None else ("*" if s.rest else "")
                                     for s in one.segments))
            if key in seen:
                raise ValueError(f"duplicate route {one.method} {one.pattern}")
            seen.add(key)

    def resolve(self, method: str, path: str) -> Union[Match, NoRoute]:
        parts = path.split("/")[1:]
        allowed: list[str] = []
        for one in self.routes:
            params = one.match(parts)
            if params is None:
                continue
            if one.method == method:
                return Match(one, params)
            if one.method not in allowed:
                allowed.append(one.method)
        return NoRoute(tuple(allowed))
