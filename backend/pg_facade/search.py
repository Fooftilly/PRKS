"""Search family of the PostgreSQL transition facade (L-S-1).

Owned by the Search lane (``docs/postgresql-architecture.md`` §15.2): Work
search, catalogue filters and Saved View execution. Empty until that lane
ports its methods. Each ported method keeps the ``PRKSDatabase`` signature
and return shape and is registered by one line, ``@ported`` from
:mod:`backend.pg_facade.registry`, directly above it.
"""

from __future__ import annotations

from backend.pg_facade.registry import FacadeMixin


class SearchMixin(FacadeMixin):
    """No Search method is ported yet."""
