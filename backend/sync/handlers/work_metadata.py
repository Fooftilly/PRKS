"""``SET_WORK_METADATA_FIELD`` on PostgreSQL (L-W-1 #628).

Validated by ``backend/work_metadata_sync.py``'s rule and decided by
:func:`backend.domain.works.metadata.apply_field_operation`, the same
decision table with the same results.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend import work_metadata_sync as rules
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.works import metadata
from backend.sync.families import Environment, FamilyHandler, Result


def apply(scope: LibraryScope, op: dict[str, Any], received_at: datetime, env: Environment) -> Result:
    del received_at, env
    payload = op["payload"]
    return metadata.apply_field_operation(scope, op["entity_id"], payload["field"], payload["value"],
                                          op["base_revision"])


HANDLER = FamilyHandler(rules.validate, apply)
