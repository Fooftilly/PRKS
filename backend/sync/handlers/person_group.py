"""The Person Group families on PostgreSQL (L-P-1 #632).

``CREATE_PERSON_GROUP``, ``SET_PERSON_GROUP_FIELD``,
``ADD/REMOVE_PERSON_GROUP_MEMBER`` and ``DELETE_PERSON_GROUP``, validated by
``backend/person_group_sync.py``'s rules and decided by
:mod:`backend.domain.people.groups`, result for result.

Names are unique per Library by identity key, and the unique index decides
a race (§7.1). A create waits for a concurrent create of the same name or
id and then reads the winner: the same id is a convergent acknowledgement,
another id holding the name is ``NAME_TAKEN``. The acknowledged group is
held until COMMIT, so a concurrent delete never leaves a create
acknowledging a group that is gone. A rename that loses the race
fails on the index; the operation then runs once more and ledgers the
``NAME_TAKEN`` its read now finds.

A membership add is a peer-table write of the protected Person delete, so
it runs SERIALIZABLE; the rest run READ COMMITTED behind row locks.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend import person_group_sync as rules
from backend.db.database import Isolation
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.people import groups, queries
from backend.person_group_sync import GroupRuleError
from backend.sync.families import Environment, FamilyHandler, Result

_NAME_KEY = "person_groups_library_id_name_key_key"
# How often a create finds the id taken but no group of its own Library
# before it concludes the id is another Library's.
_ATTEMPTS = 2


def apply_create(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                 env: Environment) -> Result:
    del received_at, env
    group_id = op["entity_id"]
    payload = op["payload"]
    for _ in range(_ATTEMPTS):
        # An existing group is acknowledged with its counts, so it is held
        # (hierarchy first, then the row: the package lock order) against a
        # delete, a member change and a new child until COMMIT. A new group
        # is ours until then.
        groups.lock_hierarchy(scope)
        if groups.share_group(scope, group_id):
            return _acknowledged(scope, group_id, False)
        try:
            if groups.insert_group_if_absent(scope, group_id, payload["name"], payload["parent_id"],
                                             payload["description"]):
                return _acknowledged(scope, group_id, True)
        except GroupRuleError as error:
            return groups.rule_status(error.code), {"code": error.code, "group_id": group_id,
                                                    "message": str(error)}
        # The id exists: a concurrent create of it committed (and may since
        # have been deleted), or it is another Library's. Look again.
    # Another Library's group id. Refused, and not ledgered.
    raise AccessRefused(Refusal.FORBIDDEN)


def _acknowledged(scope: LibraryScope, group_id: str, created: bool) -> Result:
    return 200, {"code": "ACKNOWLEDGED", "group_id": group_id, "changed": created,
                 "group": queries.group_counts_row(scope, group_id)}


def apply_field(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                env: Environment) -> Result:
    del received_at, env
    payload = op["payload"]
    return groups.apply_field_operation(scope, op["entity_id"], payload["field"], payload["value"],
                                        op["base_revision"])


def apply_member(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                 env: Environment) -> Result:
    del received_at, env
    return groups.apply_member_operation(scope, op["entity_id"], op["payload"]["person_id"],
                                         op["operation"] == "ADD_PERSON_GROUP_MEMBER",
                                         op["base_revision"])


def apply_delete(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                 env: Environment) -> Result:
    del received_at, env
    group_id = op["entity_id"]
    return 200, {"code": "ACKNOWLEDGED", "group_id": group_id,
                 "changed": groups.delete_group(scope, group_id)}


CREATE_HANDLER = FamilyHandler(rules.validate_create, apply_create)
FIELD_HANDLER = FamilyHandler(rules.validate_field, apply_field,
                              retry_constraints=frozenset({_NAME_KEY}))
ADD_MEMBER_HANDLER = FamilyHandler(rules.validate_member, apply_member, isolation=Isolation.SERIALIZABLE)
REMOVE_MEMBER_HANDLER = FamilyHandler(rules.validate_member, apply_member)
DELETE_HANDLER = FamilyHandler(rules.validate_delete, apply_delete)
