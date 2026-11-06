"""Where a web process keeps its Library's content (PG-C1 #640, ``docs/postgresql-architecture.md`` §15.3).

``PRKS_LIBRARY_BACKEND=postgres`` makes a ``PRKS_AUTH`` web process serve
its Library from PostgreSQL: ``backend.server`` binds
:class:`~backend.pg_facade.PostgresLibrary` with the PostgreSQL research and
text index adapters, and every route acts for the admitted
``RequestContext``. Nothing falls back to SQLite: a route whose method is
not ported answers through ``NotPortedError``.

Unset, the web process keeps the SQLite content store under its root. The
choice exists only until PG-C2 (#641), which makes PostgreSQL the only
backend and removes this module.

The module imports nothing from PostgreSQL, so the SQLite runtime can read
the choice without loading psycopg.
"""

from __future__ import annotations

from collections.abc import Mapping

ENV = "PRKS_LIBRARY_BACKEND"
POSTGRES = "postgres"


class LibraryBackendError(ValueError):
    """``PRKS_LIBRARY_BACKEND`` holds a value this build does not know."""


def postgres_selected(environ: Mapping[str, str]) -> bool:
    """Whether ``environ`` asks for the PostgreSQL Library; refuses an unknown value."""
    value = (environ.get(ENV) or "").strip()
    if not value:
        return False
    if value == POSTGRES:
        return True
    raise LibraryBackendError(f"{ENV} must be unset or {POSTGRES!r}.")
