"""Processing family of the PostgreSQL transition facade (L-PR-1).

Owned by the Processing lane (``docs/postgresql-architecture.md`` §15.2):
the processing inbox. Empty until that lane ports its methods. Each method keeps the signature and return shape of the
SQLite library it replaced.
"""

from __future__ import annotations

from backend.pg_facade.registry import FacadeMixin


class ProcessingMixin(FacadeMixin):
    """No Processing method is ported yet."""
