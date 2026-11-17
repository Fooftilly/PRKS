"""Who a test's old-name Library call acts for (L-W-1 #628; PG-C3 #642).

Served routes pass the admitted
:class:`~backend.domain.identity.request_context.RequestContext` to the
domain commands themselves, so nothing in ``backend/`` binds an actor any
more. The test adapters keep the retired facade's names and signatures, which
carry no actor (``tests/library_adapter.py`` ``TestLibrary``, the index
adapters), so a test binds its context with :func:`acting_as` for the
duration of the calls, and each adapter method takes it from
:func:`current_context`. A call with no bound context is refused with
:class:`NoRequestContext`, never run as an implicit owner.

The binding is a :class:`contextvars.ContextVar`, so it is per thread (and per
asyncio task). Work started on another thread does not inherit it and must
bind its own. This was ``backend/pg_facade/context.py`` until PG-C3 retired
the facade package. It imports nothing from PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from backend.domain.identity.request_context import RequestContext

_CURRENT: ContextVar[Optional[RequestContext]] = ContextVar("prks_test_context", default=None)


class NoRequestContext(RuntimeError):
    """An adapter method that acts on a Library was called with no bound actor."""

    def __init__(self) -> None:
        super().__init__("no request context is bound for this Library call")


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
