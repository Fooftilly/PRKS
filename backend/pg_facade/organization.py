"""Organization family of the PostgreSQL transition facade (L-O-1).

Owned by the Organization lane (``docs/postgresql-architecture.md`` §15.2):
tags, folders, playlists, Saved Views and settings. Empty until that lane
ports its methods. Each ported method keeps the ``PRKSDatabase`` signature
and return shape and is registered by one line, ``@ported`` from
:mod:`backend.pg_facade.registry`, directly above it.
"""

from __future__ import annotations

from backend.pg_facade.registry import FacadeMixin


class OrganizationMixin(FacadeMixin):
    """No Organization method is ported yet."""
