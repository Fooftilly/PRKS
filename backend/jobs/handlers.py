"""Job kind → handler registry (``docs/postgresql-architecture.md`` §12.1, §15.2; L-J-1 #627).

The jobs lane owns the runtime, not the handlers: each lane registers the
kinds it owns (``text.extract`` by Search, ``research.reindex_work`` by
Research, …) with one :func:`handler` decorator in its own module. A handler
receives a :class:`~backend.jobs.runner.JobRun` and must be idempotent, since
at-least-once is the only guarantee a reclaimed job gets (§12.1).

A lane whose work recurs also registers an **idle task**: the worker calls
it on its idle tick with its database, and the task enqueues whatever is due
(deduplicated, so any number of workers may call it). A lane that needs
something only the running process knows (the objects lane needs the bound
data root) registers into a :meth:`HandlerRegistry.copy` of the process-wide
registry, so the handler is bound to that worker alone.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Optional

from backend.jobs.queue import check_kind

if TYPE_CHECKING:
    from backend.db.database import Database
    from backend.jobs.runner import JobRun

Handler = Callable[["JobRun"], None]
# Runs on the worker's idle tick; raises only DatabaseError (logged, retried next tick).
IdleTask = Callable[["Database"], None]


class HandlerRegistry:
    """The handlers one worker can run, by kind."""

    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}
        self._idle_tasks: list[IdleTask] = []
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

    def add_idle_task(self, task: IdleTask) -> None:
        with self._lock:
            self._idle_tasks.append(task)

    def idle_tasks(self) -> tuple[IdleTask, ...]:
        with self._lock:
            return tuple(self._idle_tasks)

    def copy(self) -> HandlerRegistry:
        """A new registry with these handlers and idle tasks; later registrations stay apart."""
        other = HandlerRegistry()
        with self._lock:
            other._handlers = dict(self._handlers)
            other._idle_tasks = list(self._idle_tasks)
        return other


# The process-wide registry the worker uses.
REGISTRY = HandlerRegistry()


def handler(kind: str, *, registry: HandlerRegistry = REGISTRY) -> Callable[[Handler], Handler]:
    """Register the decorated function as the handler of ``kind``."""

    def register(fn: Handler) -> Handler:
        return registry.register(kind, fn)

    return register
