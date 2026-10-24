"""Research family of the PostgreSQL transition facade (L-R-1).

Owned by the Research lane (``docs/postgresql-architecture.md`` §15.2):
Concepts, Positions, Arguments and derived mentions. Empty until that lane
ports its methods. Each ported method keeps the ``PRKSDatabase`` signature
and return shape and is registered by one line, ``@ported`` from
:mod:`backend.pg_facade.registry`, directly above it.
"""

from __future__ import annotations

from backend.pg_facade.registry import FacadeMixin


class ResearchMixin(FacadeMixin):
    """No Research method is ported yet."""
