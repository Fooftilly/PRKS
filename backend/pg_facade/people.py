"""People family of the PostgreSQL transition facade (L-P-1).

Owned by the People lane (``docs/postgresql-architecture.md`` §15.2):
persons, roles, person groups and publishers. Empty until that lane ports
its methods. Each ported method keeps the ``PRKSDatabase`` signature and
return shape and is registered by one line, ``@ported`` from
:mod:`backend.pg_facade.registry`, directly above it.
"""

from __future__ import annotations

from backend.pg_facade.registry import FacadeMixin


class PeopleMixin(FacadeMixin):
    """No People method is ported yet."""
