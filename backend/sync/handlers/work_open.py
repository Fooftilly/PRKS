"""``MARK_WORK_OPENED`` on PostgreSQL: the open max-register (L-W-1 #628).

The SQLite family's rules (``backend/work_open_sync.py``): an empty payload
and a null base, the event time clamped to arrival when it is too far in
the future, and an acknowledgement carrying the Work's compact Recent row.
The write is :func:`backend.domain.works.commands.mark_opened`, which the
REST path's ``mark_work_opened`` uses too.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from backend import work_open_sync as rules
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.works import commands, projection, queries
from backend.sync.families import Environment, FamilyHandler, Result


def recent_item(scope: LibraryScope, work_id: str, env: Environment) -> Optional[dict[str, Any]]:
    """The Work in the ``/api/recent`` row projection (``get_recent_browse``'s)."""
    from backend.db_manager import finish_work_summary_rows

    core = queries.works_by_ids(scope, [work_id]).get(work_id)
    if core is None:
        return None
    row = projection.work_browse(core, excerpt=False, extra=("last_opened_at",))
    finish_work_summary_rows([row], env.pdfs_dir)
    return row


def apply(scope: LibraryScope, op: dict[str, Any], received_at: datetime, env: Environment) -> Result:
    work_id = op["entity_id"]
    effective = rules.effective_opened_at(op["occurred_at"], received_at)
    moment = datetime.strptime(effective, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
    existed, changed, value = commands.mark_opened(scope, work_id, moment)
    if not existed:
        return 404, {"code": "ENTITY_NOT_FOUND", "work_id": work_id}
    return 200, {"code": "ACKNOWLEDGED", "work_id": work_id, "changed": changed,
                 "effective_opened_at": value, "recent_item": recent_item(scope, work_id, env)}


HANDLER = FamilyHandler(rules.validate, apply)
