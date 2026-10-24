"""``CREATE_TAG``, ``DELETE_TAG`` and ``MERGE_TAG`` on PostgreSQL (L-O-1 #633).

Validated by ``backend/tag_sync.py``'s rules and decided by
:mod:`backend.domain.organization.tags`, result for result.

Creation is construction: the id is client-minted, a create of an id that
already exists acknowledges the stored Tag (``changed: false``) and holds
it until COMMIT, and a name another Tag holds as name or alias is
``NAME_TAKEN``, decided by the ``tag_keys`` index, so two concurrent creates
of one name make one Tag and the other device is told which. An id of
another Library is refused unledgered, as for a Work. One answer is new: a
create of an id whose Tag was deleted or merged is ``TAG_DELETED`` or
``TAG_MERGED`` (SQLite fails on the lifecycle row instead), because Tag
identity is persistent and the id is never constructed twice.

Delete and merge lock their Tags first (by id) and then read the lifecycle,
so an answer is never decided on a Tag a concurrent merge or delete is
changing: the later one waits and answers from what the first committed.

Isolation (§7.1): the merge is a multi-aggregate command and a create adds
to its peer tables (``tag_keys``), so both run SERIALIZABLE (a transaction
that loses a race on a row is retried on ``40001``); the delete only updates
and deletes rows and runs READ COMMITTED behind its locks. No unique index
is the arbiter of a refusal here (every key insert is ``ON CONFLICT DO
NOTHING``). A merge whose snapshot missed a link committed by a writer that
is not SERIALIZABLE is refused by the link's key to the source and runs once
more (``retry_constraints``), now seeing the link.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend import tag_sync as rules
from backend.db.database import Isolation
from backend.domain.identity.authorization import AccessRefused, Refusal
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.organization import lifecycle, tags
from backend.entity_ids import generate
from backend.sync.families import Environment, FamilyHandler, Result


def apply_create(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                 env: Environment) -> Result:
    del received_at, env
    payload = op["payload"]
    try:
        return tags.apply_create(scope, op["entity_id"], payload["name"], payload["color"])
    except tags.ForeignTagId:
        # Ids are unique across Libraries: refused, and not ledgered.
        raise AccessRefused(Refusal.FORBIDDEN) from None


def apply_delete(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                 env: Environment) -> Result:
    del received_at, env
    tag_id = op["entity_id"]
    tags.lock_for_change(scope, [tag_id])
    state = lifecycle.resolve(scope, tag_id)
    if state["state"] in (lifecycle.DELETED, lifecycle.UNKNOWN):
        # The Tag is absent, as asked: convergence.
        return 200, {"code": "ACKNOWLEDGED", "tag_id": tag_id, "changed": False, **_empty()}
    if state["state"] == lifecycle.MERGED:
        # It became another Tag; deleting that one would destroy a Tag the
        # user never named.
        return 409, {"code": "TAG_MERGED", "tag_id": tag_id, "target_tag_id": state["target_tag_id"]}
    deleted, affected = tags.delete_tag(scope, tag_id)
    return 200, {"code": "ACKNOWLEDGED", "tag_id": tag_id, "changed": deleted, **affected}


def apply_merge(scope: LibraryScope, op: dict[str, Any], received_at: datetime,
                env: Environment) -> Result:
    del received_at, env
    source = op["entity_id"]
    target = op["payload"]["target_tag_id"]
    tags.lock_for_change(scope, [source, target])
    state = lifecycle.resolve(scope, source)
    if state["state"] == lifecycle.MERGED:
        return 409, {"code": "TAG_MERGED", "tag_id": source, "target_tag_id": state["target_tag_id"]}
    if state["state"] == lifecycle.DELETED:
        return 409, {"code": "TAG_DELETED", "tag_id": source}
    if state["state"] == lifecycle.UNKNOWN:
        return 404, {"code": "ENTITY_NOT_FOUND", "tag_id": source}
    target_state = lifecycle.resolve(scope, target)
    if target_state["state"] == lifecycle.DELETED:
        return 409, {"code": "TAG_DELETED", "tag_id": target}
    if target_state["state"] != lifecycle.ACTIVE:
        # A merge into a redirect or an unknown id is not a defined decision.
        return 404, {"code": "ENTITY_NOT_FOUND", "tag_id": target}
    merged = tags.merge(scope, source, target, generate("L"))
    return 200, {"code": "ACKNOWLEDGED", "tag_id": source, "changed": True,
                 "canonical_tag_id": merged["canonical_tag_id"], "canonical_name": merged["canonical_name"],
                 **{key: list(merged[key]) for key in tags.EMPTY_AFFECTED}}


def _empty() -> dict[str, list[str]]:
    return {key: list(value) for key, value in tags.EMPTY_AFFECTED.items()}


CREATE_HANDLER = FamilyHandler(rules.validate_create, apply_create, isolation=Isolation.SERIALIZABLE)
DELETE_HANDLER = FamilyHandler(rules.validate_delete, apply_delete)
MERGE_HANDLER = FamilyHandler(rules.validate_merge, apply_merge, isolation=Isolation.SERIALIZABLE,
                              retry_constraints=tags.LINK_KEYS)
