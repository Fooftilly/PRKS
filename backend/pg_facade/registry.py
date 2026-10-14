"""Ported-method registry of the PostgreSQL transition facade (#625, PG-3).

The facade (:class:`backend.pg_facade.PostgresLibrary`) exposes the
``PRKSDatabase`` methods the HTTP adapter calls, with the same signatures and
return shapes (``docs/postgresql-architecture.md`` §5.5). Each lane implements
its family in its own mixin file and registers a method by writing
``@ported`` directly above it; nothing else (no shared list, no test fixture)
needs editing. :func:`ported_methods` discovers the registrations from the
composed class, :func:`validate_registry` refuses a registration that would
make the parity report lie, and :func:`facade_surface` derives the set of
methods the facade must eventually cover from the adapter's source.

A method that is not registered is *unported*: on the facade it raises
:class:`NotPortedError` (never a silent fallback), and the parity fixture
(``tests/backend_parity.py``) skips a ``postgres`` test that touches one.
"""

from __future__ import annotations

import ast
import inspect
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from backend.db.database import Database
    from backend.storage.config import StorageConfig

F = TypeVar("F", bound=Callable[..., Any])

PORTED_MARKER = "__prks_ported__"

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Where the adapter calls the library object (named ``db`` in each of them).
_SURFACE_SOURCES = ("server.py", "api", "services")
_SURFACE_RECEIVER = "db"


class NotPortedError(RuntimeError):
    """A ``PRKSDatabase`` method the PostgreSQL facade does not implement yet."""

    def __init__(self, name: str) -> None:
        super().__init__(f"{name} is not ported to PostgreSQL yet")
        self.method = name


class RegistryError(RuntimeError):
    """Two mixins register the same method."""


def ported(fn: F) -> F:
    """Register ``fn`` as a ported facade method (one line in its mixin)."""
    setattr(fn, PORTED_MARKER, True)
    return fn


def _unwrap(attr: object) -> object:
    if isinstance(attr, (staticmethod, classmethod)):
        return getattr(attr, "__func__")
    if isinstance(attr, property):
        return attr.fget
    return attr


def is_ported(attr: object) -> bool:
    return bool(getattr(_unwrap(attr), PORTED_MARKER, False))


class FacadeMixin:
    """Base of every family mixin: the state the facade core provides."""

    _database: Database
    _storage: StorageConfig | None


def facade_mixins(library_cls: type) -> list[type]:
    """The family mixins composed into ``library_cls``, in MRO order."""
    return [
        cls
        for cls in library_cls.__mro__
        if cls is not FacadeMixin and issubclass(cls, FacadeMixin) and cls is not library_cls
    ]


def ported_methods(library_cls: type) -> dict[str, str]:
    """``{method name: mixin class name}`` for every ``@ported`` method."""
    found: dict[str, str] = {}
    for mixin in facade_mixins(library_cls):
        for name, attr in vars(mixin).items():
            if not is_ported(attr):
                continue
            if name in found:
                raise RegistryError(
                    f"{name} is registered by both {found[name]} and {mixin.__name__}"
                )
            found[name] = mixin.__name__
    return found


def _reference_class() -> type:
    from backend.db_manager import PRKSDatabase

    return PRKSDatabase


def _comparable(sig: inspect.Signature) -> list[tuple[str, Any, Any]]:
    return [(p.name, p.kind, p.default) for p in sig.parameters.values()]


def _registration_problems(library_cls: type, ref: type, registered: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for name, owner in sorted(registered.items()):
        if not hasattr(ref, name):
            problems.append(f"{owner}.{name} is registered but {ref.__name__} has no {name}")
            continue
        mine = _unwrap(inspect.getattr_static(library_cls, name))
        theirs = _unwrap(inspect.getattr_static(ref, name))
        if not (callable(mine) and callable(theirs)):
            continue
        if _comparable(inspect.signature(mine)) != _comparable(inspect.signature(theirs)):
            problems.append(f"{owner}.{name} does not have the signature of {ref.__name__}.{name}")
    return problems


def _unregistered_override_problems(library_cls: type, ref: type) -> list[str]:
    return [
        f"{mixin.__name__}.{name} overrides {ref.__name__}.{name} without @ported"
        for mixin in facade_mixins(library_cls)
        for name, attr in vars(mixin).items()
        if not name.startswith("_") and not is_ported(attr) and hasattr(ref, name)
    ]


def validate_registry(library_cls: type, reference: type | None = None) -> list[str]:
    """Problems that would make the facade or its parity report wrong.

    - a registered name that ``PRKSDatabase`` does not have;
    - a registered method whose parameters (names, kinds, defaults) differ
      from the ``PRKSDatabase`` method it stands in for;
    - a mixin method named like a ``PRKSDatabase`` method but not registered
      (it would serve production calls while the parity leg skips it).
    """
    ref = reference if reference is not None else _reference_class()
    try:
        registered = ported_methods(library_cls)
    except RegistryError as exc:
        return [str(exc)]
    return _registration_problems(library_cls, ref, registered) + (
        _unregistered_override_problems(library_cls, ref)
    )


def _surface_files(backend_dir: str) -> Iterable[str]:
    for entry in _SURFACE_SOURCES:
        path = os.path.join(backend_dir, entry)
        if os.path.isfile(path):
            yield path
        elif os.path.isdir(path):
            for name in sorted(os.listdir(path)):
                if name.endswith(".py"):
                    yield os.path.join(path, name)


def facade_surface(reference: type | None = None, backend_dir: str = _BACKEND_DIR) -> frozenset[str]:
    """``PRKSDatabase`` attributes the adapter reaches through ``db.<name>``.

    Read from ``server.py``, ``backend/api/`` and ``backend/services/`` (the
    code that receives the library object), so the count follows the adapter
    without a hand-kept list. PG-C1 needs every name here ported.
    """
    ref = reference if reference is not None else _reference_class()
    names: set[str] = set()
    for path in _surface_files(backend_dir):
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == _SURFACE_RECEIVER
                and not node.attr.startswith("__")
                and hasattr(ref, node.attr)
            ):
                names.add(node.attr)
    return frozenset(names)


@dataclass(frozen=True)
class Coverage:
    """Ported/unported counts of the facade against the adapter's surface."""

    surface: frozenset[str]
    ported: dict[str, str]

    @property
    def ported_in_surface(self) -> frozenset[str]:
        return self.surface & frozenset(self.ported)

    @property
    def unported(self) -> frozenset[str]:
        return self.surface - frozenset(self.ported)

    @property
    def ported_outside_surface(self) -> frozenset[str]:
        """Registered methods the adapter does not call directly (sync helpers etc.)."""
        return frozenset(self.ported) - self.surface


def coverage(library_cls: type | None = None) -> Coverage:
    if library_cls is None:
        from backend.pg_facade import PostgresLibrary

        library_cls = PostgresLibrary
    return Coverage(surface=facade_surface(), ported=ported_methods(library_cls))
