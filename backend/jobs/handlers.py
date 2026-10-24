"""Job kind → handler registry (``docs/postgresql-architecture.md`` §12.1, §15.2; L-J-1 #627).

The jobs lane owns the runtime, not the handlers: each lane registers the
kinds it owns (``text.extract`` by Search, ``research.reindex_work`` by
Research, …) with one :func:`handler` decorator in its own module. A handler
receives a :class:`~backend.jobs.runner.JobRun` and must be idempotent, since
at-least-once is the only guarantee a reclaimed job gets (§12.1).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Optional

from backend.jobs.queue import check_kind

if TYPE_CHECKING:
    from backend.jobs.runner import JobRun

Handler = Callable[["JobRun"], None]


class HandlerRegistry:
    """The handlers one worker can run, by kind."""

    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}
        self._lock = threading.Lock()

    def register(self, kind: str, fn: Handler) -> Handler:
        check_kind(kind)
        with self._lock:
            if kind in self._handlers:
                raise ValueError(f"job kind {kind!r} already has a handler")
            self._handlers[kind] = fn
        return fn

    def get(self, kind: str) -> Optional[Handler]:
        with self._lock:
            return self._handlers.get(kind)

    def kinds(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._handlers)


# The process-wide registry the worker uses.
REGISTRY = HandlerRegistry()


def handler(kind: str, *, registry: HandlerRegistry = REGISTRY) -> Callable[[Handler], Handler]:
    """Register the decorated function as the handler of ``kind``."""

    def register(fn: Handler) -> Handler:
        return registry.register(kind, fn)

    return register
