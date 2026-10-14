"""SQLite/PostgreSQL parity fixture (#625, PG-3; ``docs/postgresql-architecture.md`` §13.1).

A selected test class opts in with :func:`parametrize_backends` and gets its
library object from :meth:`BackendParityTestCase.open_library` instead of
constructing ``PRKSDatabase`` itself::

    @parametrize_backends
    class SavedViewTests(BackendParityTestCase):
        def setUp(self):
            self.db = self.open_library(StorageConfig.for_testing(tmpdir))

By default (``python run_tests.py``) nothing changes: the class runs once, on
SQLite, and this module never imports psycopg. When
``PRKS_PARITY_BACKENDS=sqlite,postgres`` (set by
``scripts/run_backend_parity.py``), the decorator also adds a
``<Class>_postgres`` twin to the test's module. The twin runs the same tests
against :class:`backend.pg_facade.PostgresLibrary` over its own
harness-created database (one per test, cloned from the migrated template)
and its own temporary data root, so the two legs never share state.

Unported methods never produce a misleading result on the ``postgres`` leg:
every library attribute the test reaches is recorded, a method that is not
registered as ported raises :class:`~backend.pg_facade.NotPortedError` (also
when a ported method calls it internally), and a
test (or its ``setUp``/``tearDown`` or a cleanup) that touched one is reported as skipped
with the unported names, whether it then failed, errored or passed (an
exception the code under test swallowed). Only a test that touched nothing
but ported methods counts as a ``postgres`` pass.

Registration needs no edit here: a lane marks its method ``@ported`` in its
own mixin under ``backend/pg_facade/`` and the twin picks it up.
"""

from __future__ import annotations

import functools
import os
import sys
import unittest
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar, TypeVar

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCHEMA_PATH = os.path.join(_PROJECT_DIR, "backend", "db_schema.sql")

ENV_PARITY_BACKENDS = "PRKS_PARITY_BACKENDS"
SQLITE = "sqlite"
POSTGRES = "postgres"
BACKENDS = (SQLITE, POSTGRES)
TWIN_SUFFIX = "_postgres"
SKIP_PREFIX = "not ported to PostgreSQL: "
# Methods wrapped on the twin so an unported call anywhere in a test's
# lifecycle becomes a skip (cleanups are wrapped by its addCleanup).
_GUARDED_LIFECYCLE = ("setUp", "tearDown")

L = TypeVar("L")



def enabled_backends(environ: dict[str, str] | None = None) -> tuple[str, ...]:
    """The legs to build: ``("sqlite",)`` unless the parity runner asks for both."""
    env = os.environ if environ is None else environ
    raw = env.get(ENV_PARITY_BACKENDS, "").strip()
    if not raw:
        return (SQLITE,)
    names = {part.strip() for part in raw.split(",")} - {""}
    unknown = sorted(names - set(BACKENDS))
    if unknown:
        raise ValueError(f"{ENV_PARITY_BACKENDS} names unknown backends: {', '.join(unknown)}")
    if SQLITE not in names:
        raise ValueError(f"{ENV_PARITY_BACKENDS} must include {SQLITE}")
    return tuple(name for name in BACKENDS if name in names)


@dataclass
class ParityLog:
    """Library attributes one ``postgres`` test reached."""

    # Ported methods the test called (not merely looked up).
    ported: set[str] = field(default_factory=set[str])
    unported: set[str] = field(default_factory=set[str])
    # NotPortedError refusals raised so far; tells a part whether it hit one.
    refusals: int = 0
    # The test already ended as a skip; later parts must not report another.
    skipped: bool = False

    def skip_reason(self) -> str:
        return SKIP_PREFIX + ", ".join(sorted(self.unported))


def _reference_has(name: str) -> bool:
    from backend.db_manager import PRKSDatabase

    return hasattr(PRKSDatabase, name)


def _record_use(log: ParityLog, name: str, attr: Any) -> Any:
    """Count a ported method as used when it is called, not when looked up.

    A value attribute (a property's result) is used by reading it.
    """
    if not callable(attr):
        log.ported.add(name)
        return attr

    @functools.wraps(attr)
    def call(*args: Any, **kwargs: Any) -> Any:
        log.ported.add(name)
        return attr(*args, **kwargs)

    return call


def with_refusal_log(library_cls: type[L], log: ParityLog) -> type[L]:
    """``library_cls`` with every ``NotPortedError`` it raises recorded in ``log``.

    The recorder only sees what a test reaches on the library object; a ported
    method calling an unported one goes through ``self`` and never passes it.
    Recording at the facade's own refusal point catches that call too, even
    when the ported method (or the code under test) swallows the error.
    """
    from backend.pg_facade import NotPortedError

    class Recording(library_cls):
        def __getattr__(self, name: str) -> Any:
            fallback = getattr(super(), "__getattr__", None)
            if fallback is None:
                raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")
            try:
                return fallback(name)
            except NotPortedError as exc:
                log.unported.add(exc.method)
                log.refusals += 1
                raise

    Recording.__name__ = Recording.__qualname__ = library_cls.__name__
    return Recording


class ParityRecorder:
    """Wraps the facade on the ``postgres`` leg and records what a test reaches."""

    def __init__(self, library: Any, ported: frozenset[str], log: ParityLog) -> None:
        object.__setattr__(self, "_library", library)
        object.__setattr__(self, "_ported", ported)
        object.__setattr__(self, "_log", log)

    @property
    def parity_library(self) -> Any:
        """The wrapped facade (tests of the fixture itself use it)."""
        return object.__getattribute__(self, "_library")

    def __getattr__(self, name: str) -> Any:
        library = object.__getattribute__(self, "_library")
        log: ParityLog = object.__getattribute__(self, "_log")
        if name in object.__getattribute__(self, "_ported"):
            return _record_use(log, name, getattr(library, name))
        if not name.startswith("__") and _reference_has(name):
            from backend.pg_facade import NotPortedError

            log.unported.add(name)
            log.refusals += 1
            raise NotPortedError(name)
        return getattr(library, name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("the parity library object is read-only")


class BackendParityTestCase(unittest.TestCase):
    """Base of a test class whose library object comes from one backend leg."""

    parity_backend: ClassVar[str] = SQLITE

    @property
    def parity_log(self) -> ParityLog:
        log = self.__dict__.get("_parity_log")
        if log is None:
            log = ParityLog()
            self.__dict__["_parity_log"] = log
        return log

    def open_library(self, storage: Any) -> Any:
        """The library object of this leg over ``storage`` (a ``StorageConfig``).

        SQLite: ``PRKSDatabase`` as the tests built it before. PostgreSQL: the
        facade over a fresh harness database, dropped after the test.
        """
        if self.parity_backend == SQLITE:
            from backend.db_manager import PRKSDatabase

            return PRKSDatabase(storage=storage, schema_path=_SCHEMA_PATH)
        if self.parity_backend == POSTGRES:
            return self._open_postgres_library(storage)
        raise ValueError(f"unknown parity backend {self.parity_backend!r}")

    def _open_postgres_library(self, storage: Any) -> ParityRecorder:
        from backend.db import Database, DatabaseConfig
        from backend.pg_facade import PostgresLibrary, ported_methods
        from tests.pg.harness import get_harness

        harness = get_harness()
        name = harness.create_database("parity")
        self.addCleanup(harness.drop_database, name)
        database = Database(
            DatabaseConfig(conninfo=harness.conninfo(name), role="test", pool_min=0, pool_max=2)
        )
        self.addCleanup(database.close)
        library = with_refusal_log(PostgresLibrary, self.parity_log)(database, storage=storage)
        return ParityRecorder(library, frozenset(ported_methods(PostgresLibrary)), self.parity_log)


def _call_guarded(test: BackendParityTestCase, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run one part of a ``postgres`` test (setUp, body, tearDown or a cleanup).

    Any outcome of a part that reached an unported method becomes a skip: an
    exception (a failure that would mislead) and a normal return alike (the
    code under test swallowed the refusal, so its pass would not be evidence
    either). A test is reported as skipped once, by its first such part.
    Only a part that itself hit a refusal is converted: any other exception,
    including one from a cleanup after the test was skipped, still errors.
    """
    log = test.parity_log
    refusals_before = log.refusals
    try:
        result = fn(*args, **kwargs)
    except unittest.SkipTest:
        log.skipped = True
        raise
    except Exception as exc:
        if log.refusals == refusals_before:
            raise
        if log.skipped:
            return None
        log.skipped = True
        raise unittest.SkipTest(log.skip_reason()) from exc
    if log.refusals > refusals_before and not log.skipped:
        log.skipped = True
        raise unittest.SkipTest(log.skip_reason())
    return result


def _guard(fn: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(fn)
    def run(self: BackendParityTestCase, *args: Any, **kwargs: Any) -> Any:
        return _call_guarded(self, fn, self, *args, **kwargs)

    return run


def _guarded_add_cleanup(
    self: BackendParityTestCase, function: Callable[..., Any], /, *args: Any, **kwargs: Any
) -> None:
    unittest.TestCase.addCleanup(self, _call_guarded, self, function, *args, **kwargs)


def _postgres_twin(cls: type[BackendParityTestCase]) -> type[BackendParityTestCase]:
    loader = unittest.TestLoader()
    namespace: dict[str, Any] = {
        "parity_backend": POSTGRES,
        "__module__": cls.__module__,
        "__qualname__": cls.__qualname__ + TWIN_SUFFIX,
        "__doc__": cls.__doc__,
        "addCleanup": _guarded_add_cleanup,
    }
    for name in (*loader.getTestCaseNames(cls), *_GUARDED_LIFECYCLE):
        namespace[name] = _guard(getattr(cls, name))
    return type(cls.__name__ + TWIN_SUFFIX, (cls,), namespace)


P = TypeVar("P", bound=BackendParityTestCase)


def parametrize_backends(cls: type[P]) -> type[P]:
    """Run ``cls`` on SQLite, plus a ``postgres`` twin when the parity leg is on."""
    if BackendParityTestCase not in getattr(cls, "__mro__", ()):
        raise TypeError("parametrize_backends needs a BackendParityTestCase subclass")
    if cls.parity_backend != SQLITE:
        raise TypeError("parametrize the SQLite class, not a twin")
    if POSTGRES in enabled_backends():
        twin = _postgres_twin(cls)
        module = sys.modules[cls.__module__]
        if hasattr(module, twin.__name__):
            raise TypeError(f"{cls.__module__} already defines {twin.__name__}")
        setattr(module, twin.__name__, twin)
    return cls
