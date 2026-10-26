"""``CREATE_ARGUMENT``, ``SET_ARGUMENT_FIELD``, ``SET_ARGUMENT_SOURCES``, ``SET_ARGUMENT_TARGETS``
and ``DELETE_ARGUMENT`` on PostgreSQL (L-R-1 #634).

Validated by ``backend/argument_sync.py``'s rules and decided by
:mod:`backend.domain.research.arguments`, result for result, with one
refusal SQLite never makes: a citation whose Version the legacy list cannot
name is ``ARGUMENT_SOURCE_VERSION_REQUIRED`` (``docs/work-identity-model.md``
§8.5).

Creation is construction: the id is client-minted, a create of an id that
already exists acknowledges it (``changed: false``), and a refused list
leaves no Argument behind. An id of another Library is refused unledgered.

Isolation (§7.1): ``argument_sources`` is a peer table of the Work merge,
so the two families that add citations (``CREATE_ARGUMENT``,
``SET_ARGUMENT_SOURCES``) run SERIALIZABLE (``merge.PEER_WRITERS``); the
protocol refuses either with ``WORK_MERGED`` when a cited Work was merged
away. The rest run READ COMMITTED behind the Argument's row lock, target
changes under the Library's Argument target lock as well.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend import argument_sync as rules
from backend.db.database import Isolation
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.research import arguments
from backend.sync.families import Environment, FamilyHandler, Result


def apply_create(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                 env: Environment) -> Result:
    del received_at, env
    try:
        return arguments.apply_create(scope, op["entity_id"], op["payload"])
    except arguments.ForeignArgumentId:
        # Ids are unique across Libraries: refused, and not ledgered.
        raise AccessRefused(Refusal.FORBIDDEN) from None


def apply_field(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                env: Environment) -> Result:
    del received_at, env
    payload = op["payload"]
    return arguments.apply_field_operation(scope, op["entity_id"], payload["field"], payload["value"],
                                           op["base_revision"])


def apply_sources(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                  env: Environment) -> Result:
    del received_at, env
    return arguments.apply_sources_operation(scope, op["entity_id"], op["payload"]["sources"],
                                             op["base_revision"])


def apply_targets(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                  env: Environment) -> Result:
    del received_at, env
    return arguments.apply_targets_operation(scope, op["entity_id"], op["payload"]["targets"],
                                             op["base_revision"])


def apply_delete(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                 env: Environment) -> Result:
    del received_at, env
    argument_id = op["entity_id"]
    deleted, refusal = arguments.delete(scope, argument_id)
    if refusal:
        return 409, {"argument_id": argument_id, "code": refusal}
    # Deleting an Argument that is already gone is convergence.
    return 200, {"argument_id": argument_id, "code": "ACKNOWLEDGED", "changed": deleted}


def cited_work_ids(op: dict[str, Any]) -> list[str]:
    """The Works a citing operation names, for the ``WORK_MERGED`` guard (validated payload)."""
    return sorted({source["work_id"] for source in op["payload"]["sources"]})


CREATE_HANDLER = FamilyHandler(rules.validate_create, apply_create, isolation=Isolation.SERIALIZABLE)
FIELD_HANDLER = FamilyHandler(rules.validate_field, apply_field)
SOURCES_HANDLER = FamilyHandler(rules.validate_sources, apply_sources, isolation=Isolation.SERIALIZABLE)
TARGETS_HANDLER = FamilyHandler(rules.validate_targets, apply_targets)
DELETE_HANDLER = FamilyHandler(rules.validate_delete, apply_delete)

# The operations whose payload names Works.
CITING = frozenset({"CREATE_ARGUMENT", "SET_ARGUMENT_SOURCES"})
