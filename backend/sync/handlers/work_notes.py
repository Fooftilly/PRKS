"""``SET_WORK_RESEARCH_NOTE`` and ``SET_WORK_PRIVATE_NOTE`` on PostgreSQL (L-W-1 #628).

Validated by ``backend/work_note_sync.py``'s rule and decided by
:func:`backend.domain.works.notes.apply_note_operation`. A Research Note's
markup side effects (Concepts, mentions) are the Research lane's (L-R-1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend import work_note_sync as rules
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.works import notes
from backend.sync.families import Environment, FamilyHandler, Result


def apply(scope: LibraryScope, op: dict[str, Any], received_at: datetime, env: Environment) -> Result:
    del received_at, env
    return notes.apply_note_operation(scope, op["operation"], op["entity_id"], op["payload"]["text"],
                                      op["base_revision"])


HANDLER = FamilyHandler(rules.validate, apply)
