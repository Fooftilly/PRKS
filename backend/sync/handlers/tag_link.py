"""``ADD/REMOVE_WORK_TAG`` and ``ADD/REMOVE_FOLDER_TAG`` on PostgreSQL (L-O-1 #633).

Validated by ``backend/work_tag_sync.py``'s and ``folder_tag_sync.py``'s
rules and decided by :func:`backend.domain.organization.links.apply_operation`,
result for result: a missing owner or Tag is ``ENTITY_NOT_FOUND``, a deleted
Tag ``TAG_DELETED``, a merged one ``TAG_MERGED`` with its target (never
redirected), then the revision decision table.

The owner row (``FOR NO KEY UPDATE``) and then the Tag (``FOR SHARE``) are
held before the decision, so every writer of one link queues, a Tag delete
or merge either waits for the operation or is seen by it, and a Work delete
either waits or leaves nothing to link. An add inserts into a peer table of
the Tag merge and the Work merge, so it runs SERIALIZABLE (§7.1); a remove
only deletes and runs READ COMMITTED behind the same locks.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from backend import folder_tag_sync, work_tag_sync
from backend.db.database import Isolation
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.organization import links
from backend.sync.families import Environment, FamilyHandler, Result

_ADDS = frozenset({"ADD_WORK_TAG", "ADD_FOLDER_TAG"})


def _applier(link: links.Link) -> Callable[[LibraryScope, dict[str, Any], datetime, Environment], Result]:
    def apply(scope: LibraryScope, op: dict[str, Any], received_at: datetime, env: Environment) -> Result:
        del received_at, env
        return links.apply_operation(scope, link, op["entity_id"], op["payload"]["tag_id"],
                                     op["operation"] in _ADDS, op["base_revision"])

    return apply


ADD_WORK_HANDLER = FamilyHandler(work_tag_sync.validate, _applier(links.WORK), isolation=Isolation.SERIALIZABLE)
REMOVE_WORK_HANDLER = FamilyHandler(work_tag_sync.validate, _applier(links.WORK))
ADD_FOLDER_HANDLER = FamilyHandler(folder_tag_sync.validate, _applier(links.FOLDER),
                                   isolation=Isolation.SERIALIZABLE)
REMOVE_FOLDER_HANDLER = FamilyHandler(folder_tag_sync.validate, _applier(links.FOLDER))
