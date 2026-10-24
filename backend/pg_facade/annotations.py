"""Annotations and objects family of the PostgreSQL transition facade (L-A-1).

Owned by the Annotations and objects lane (``docs/postgresql-
architecture.md`` §15.2): PDF annotations, materialization and managed
objects. Empty until that lane ports its methods. Each ported method keeps
the ``PRKSDatabase`` signature and return shape and is registered by one
line, ``@ported`` from :mod:`backend.pg_facade.registry`, directly above it.
"""

from __future__ import annotations

from backend.pg_facade.registry import FacadeMixin


class AnnotationsMixin(FacadeMixin):
    """No Annotations and objects method is ported yet."""
