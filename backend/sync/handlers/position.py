"""``CREATE_POSITION``, ``SET_POSITION_FIELD`` and ``DELETE_POSITION`` on PostgreSQL (L-R-1 #634).

Validated by ``backend/position_sync.py``'s rules and decided by
:mod:`backend.domain.research.positions`, result for result.

Creation is construction: the id is client-minted, and a create of an id
that already exists acknowledges the stored Position (``changed: false``).
An id of another Library is refused unledgered, as for a Concept.

Isolation (§7.1): every family runs READ COMMITTED behind the Position's row
lock. A Position is no peer table of a SERIALIZABLE command.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend import position_sync as rules
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.research import positions
from backend.sync.families import Environment, FamilyHandler, Result


def apply_create(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                 env: Environment) -> Result:
    del received_at, env
    try:
        return positions.apply_create(scope, op["entity_id"], op["payload"])
    except positions.ForeignPositionId:
        # Ids are unique across Libraries: refused, and not ledgered.
        raise AccessRefused(Refusal.FORBIDDEN) from None


def apply_field(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                env: Environment) -> Result:
    del received_at, env
    payload = op["payload"]
    return positions.apply_field_operation(scope, op["entity_id"], payload["field"], payload["value"],
                                           op["base_revision"])


def apply_delete(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                 env: Environment) -> Result:
    del received_at, env
    position_id = op["entity_id"]
    deleted, refusal = positions.delete(scope, position_id)
    if refusal:
        return 409, {"position_id": position_id, "code": refusal}
    # Deleting a Position that is already gone is convergence.
    return 200, {"position_id": position_id, "code": "ACKNOWLEDGED", "changed": deleted}


CREATE_HANDLER = FamilyHandler(rules.validate_create, apply_create)
FIELD_HANDLER = FamilyHandler(rules.validate_field, apply_field)
DELETE_HANDLER = FamilyHandler(rules.validate_delete, apply_delete)
