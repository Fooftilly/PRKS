"""``SET_WORK_SOURCE`` on PostgreSQL (L-W-1 #628).

Validated by ``backend/work_source_sync.py``'s rule and decided by
:func:`backend.domain.works.source.apply_source_operation`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend import work_source_sync as rules
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.works import source
from backend.sync.families import Environment, FamilyHandler, Result


def apply(scope: LibraryScope, op: dict[str, Any], received_at: datetime, env: Environment) -> Result:
    del received_at, env
    return source.apply_source_operation(scope, op["entity_id"], op["payload"]["source"],
                                         op["base_revision"])


HANDLER = FamilyHandler(rules.validate, apply)
