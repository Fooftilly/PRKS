"""Who a facade call acts for (L-W-1 #628; ``docs/postgresql-architecture.md`` §5.5, §9.4).

The facade keeps the ``PRKSDatabase`` signatures, which carry no actor, so
the actor travels beside the call: whoever admits a request binds its
:class:`~backend.domain.identity.request_context.RequestContext` with
:func:`acting_as` for the duration of the call, and every ported method that
reads or changes Library state takes it from :func:`current_context`.

A call with no bound context is refused with :class:`NoRequestContext`. It is
never run as an implicit owner, an administrator or "the only Library": the
domain helpers authorize the bound context inside each transaction
(``library_command``/``library_query``), so the facade adds no trust of its
own.

The binding is a :class:`contextvars.ContextVar`, so it is per thread (and per
asyncio task) and a context bound for one request is never seen by another.
Work started on another thread does not inherit it and must bind its own.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from backend.domain.identity.request_context import RequestContext

_CURRENT: ContextVar[Optional[RequestContext]] = ContextVar("prks_facade_context", default=None)


class NoRequestContext(RuntimeError):
    """A facade method that acts on a Library was called with no bound actor."""

    def __init__(self) -> None:
        super().__init__("no request context is bound for this PostgreSQL facade call")


def bind(ctx: RequestContext) -> Token[Optional[RequestContext]]:
    """Bind ``ctx`` until :func:`unbind` is called with the returned token."""
    return _CURRENT.set(ctx)


def unbind(token: Token[Optional[RequestContext]]) -> None:
    _CURRENT.reset(token)


@contextmanager
def acting_as(ctx: RequestContext) -> Iterator[RequestContext]:
    """Bind ``ctx`` for the calls made inside the ``with`` block."""
    token = bind(ctx)
    try:
        yield ctx
    finally:
        unbind(token)


def current_context() -> RequestContext:
    """The bound context; :class:`NoRequestContext` when there is none."""
    ctx = _CURRENT.get()
    if ctx is None:
        raise NoRequestContext()
    return ctx
