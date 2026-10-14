"""Works family of the PostgreSQL transition facade (L-W-1, L-W-2).

Owned by the Works lane (``docs/postgresql-architecture.md`` §15.2): Works,
Manifestations, notes, lifecycle, metadata, bulk actions, BibTeX and wiki
links. Empty until that lane ports its methods. Each ported method keeps the
``PRKSDatabase`` signature and return shape and is registered by one line,
``@ported`` from :mod:`backend.pg_facade.registry`, directly above it.
"""

from __future__ import annotations

from backend.pg_facade.registry import FacadeMixin


class WorksMixin(FacadeMixin):
    """No Works method is ported yet."""
