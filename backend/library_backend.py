"""The retired ``PRKS_LIBRARY_BACKEND`` selection (PG-C2 #641).

Until 0.8 a web process kept its Library in a SQLite store under the root
unless ``PRKS_LIBRARY_BACKEND=postgres`` asked for PostgreSQL (PG-C1 #640).
PostgreSQL is now the only backend, so the variable chooses nothing. A
process that still finds it set refuses to start instead of ignoring it:
an operator who set it, to either value, expected a choice this version no
longer offers, and an environment or Compose file that carries it was
written for an earlier release.

The module imports nothing from PostgreSQL, so every process role can run
the check before it loads psycopg.
"""

from __future__ import annotations

from collections.abc import Mapping

ENV = "PRKS_LIBRARY_BACKEND"


class LibraryBackendError(ValueError):
    """``PRKS_LIBRARY_BACKEND`` is set; this version no longer reads it."""


def refuse_stale_selection(environ: Mapping[str, str]) -> None:
    """Raise :class:`LibraryBackendError` when ``environ`` still sets ``PRKS_LIBRARY_BACKEND``.

    Any value counts, ``postgres`` and an empty string included: the
    variable is stale configuration wherever it appears.
    """
    if ENV not in environ:
        return
    raise LibraryBackendError(
        f"{ENV} is no longer read: PostgreSQL is the only Library backend. "
        f"Remove {ENV} from the environment (and from any .env or Compose file that sets it); "
        "the Library's database is PRKS_DATABASE_URL."
    )
