"""``CREATE_CONCEPT``, ``SET_CONCEPT_FIELD``, ``SET_CONCEPT_IDENTITY``, ``SET_CONCEPT_PARENTS``
and ``DELETE_CONCEPT`` on PostgreSQL (L-R-1 #634).

Validated by ``backend/concept_sync.py``'s rules and decided by
:mod:`backend.domain.research.concepts`, result for result.

Creation is construction: the id is client-minted, a create of an id that
already exists acknowledges the stored Concept (``changed: false``), and a
name another Concept holds as name or alias is ``CONCEPT_EXISTS``, decided
by the key index, so two concurrent creates of one name make one Concept.
An id of another Library is refused unledgered, as for a Work.

A rename or an alias write that loses a race for a key fails on the index,
which aborts its transaction; the identity family names that index
(``retry_constraints``), so it runs once more and ledgers
``CONCEPT_EXISTS`` or ``ALIAS_CONFLICT`` from what it then reads.

Isolation (§7.1): every family runs READ COMMITTED. A Concept is no peer
table of a SERIALIZABLE command; its uniqueness is decided by the key index
and its hierarchy under the Library's Concept hierarchy lock.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend import concept_sync as rules
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.research import concepts
from backend.sync.families import Environment, FamilyHandler, Result


def apply_create(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                 env: Environment) -> Result:
    del received_at, env
    try:
        return concepts.apply_create(scope, op["entity_id"], op["payload"])
    except concepts.ForeignConceptId:
        # Ids are unique across Libraries: refused, and not ledgered.
        raise AccessRefused(Refusal.FORBIDDEN) from None


def apply_field(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                env: Environment) -> Result:
    del received_at, env
    payload = op["payload"]
    return concepts.apply_field_operation(scope, op["entity_id"], payload["field"], payload["value"],
                                          op["base_revision"])


def apply_identity(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                   env: Environment) -> Result:
    del received_at, env
    return concepts.apply_identity_operation(scope, op["entity_id"], op["payload"], op["base_revision"])


def apply_parents(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                  env: Environment) -> Result:
    del received_at, env
    return concepts.apply_parents_operation(scope, op["entity_id"], op["payload"]["parent_ids"],
                                            op["base_revision"])


def apply_delete(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                 env: Environment) -> Result:
    del received_at, env
    concept_id = op["entity_id"]
    deleted, refusal = concepts.delete(scope, concept_id)
    if refusal:
        return 409, {"concept_id": concept_id, "code": refusal}
    # Deleting a Concept that is already gone is convergence.
    return 200, {"concept_id": concept_id, "code": "ACKNOWLEDGED", "changed": deleted}


_KEYS = frozenset({concepts.KEY_INDEX})

CREATE_HANDLER = FamilyHandler(rules.validate_create, apply_create)
FIELD_HANDLER = FamilyHandler(rules.validate_field, apply_field)
IDENTITY_HANDLER = FamilyHandler(rules.validate_identity, apply_identity, retry_constraints=_KEYS)
PARENTS_HANDLER = FamilyHandler(rules.validate_parents, apply_parents)
DELETE_HANDLER = FamilyHandler(rules.validate_delete, apply_delete)
