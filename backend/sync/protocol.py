"""The semantic-operation ledger on PostgreSQL (``docs/postgresql-architecture.md`` §7.3).

What every operation family shares, moved from ``backend/sync_protocol.py``
without changing its meaning (``docs/local-first-sync.md``): the envelope is
normalized by the same function, so the request hash of an operation is the
same bytes on both backends; a malformed envelope or payload is refused
before the ledger and never burns an ``op_id``; an exception is never
ledgered; an ``op_id`` seen before replays its stored status and body, or is
``OP_ID_REUSE`` when the request differs; dependencies must be acknowledged
operations of the same device. One refusal is new: a NUL anywhere in the
envelope is ``INVALID_ENVELOPE``, because PostgreSQL text and ``jsonb``
cannot hold it (SQLite stores it).

What changes is how one operation is made exactly-once without SQLite's
single writer:

1. **Ledger before epoch.** An ``op_id`` already in the ledger is looked up
   first, in a read that is not fenced by epoch, and replayed whatever epoch
   it carries: the restored state already contains its effect (§11.4).
2. Otherwise the operation runs in one epoch-fenced
   :func:`~backend.domain.identity.library_scope.library_command`
   transaction, which first claims the ``op_id``
   (``INSERT … ON CONFLICT (op_id) DO NOTHING``). A concurrent duplicate
   waits on the primary key until the claimant commits, then finds the row
   and replays it; if the claimant rolled back, the duplicate owns the
   operation. (A duplicate that commits between the lookup and the claim is
   found by the claim, after the epoch check, so it can be refused
   ``LIBRARY_EPOCH_CHANGED`` once; its retry replays it.)
3. The claimant checks dependencies, applies the family's handler and
   writes the terminal result into its claim before COMMIT. A refusal that
   is not ledgered (an unsatisfied dependency) rolls the claim back.
4. When a constraint the family names (``retry_constraints``) refuses the
   transaction, a concurrent writer won a race the index or key arbitrates
   (§7.1). The whole operation runs once more: its claim was rolled back,
   and its reads now see the winner, so it ledgers the domain refusal or
   convergence instead of failing.

A ledger row records the Library and the user. An ``op_id`` stored for
another Library or account is ``OP_ID_REUSE`` and never discloses its
result, and the envelope's device must be one of the user's devices.

Only the families registered at the bottom run here; any other operation
the SQLite protocol accepts raises :class:`~backend.pg_facade.NotPortedError`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, LiteralString, Optional

from psycopg.types.json import Jsonb

from backend import sync_protocol as rules
from backend.db.database import Database
from backend.db.errors import ConstraintViolationError
from backend.domain.identity.authorization import AccessRefused, Capability, Refusal
from backend.domain.identity.library_scope import LibraryScope, library_command, library_query
from backend.domain.identity.request_context import RequestContext
from backend.pg_facade.registry import NotPortedError
from backend.sync.families import Environment, Handler, Result

_HANDLERS: dict[str, Handler] = {}


def register(operation: str, handler: Handler) -> None:
    if operation in _HANDLERS:
        raise RuntimeError("duplicate PostgreSQL sync handler: " + operation)
    if operation not in rules.supported_operations():
        raise RuntimeError("not an operation of the sync protocol: " + operation)
    _HANDLERS[operation] = handler


def supported_operations() -> frozenset[str]:
    return frozenset(_HANDLERS)


class _NotLedgered(Exception):
    """A refusal returned to the client with the claim rolled back."""

    def __init__(self, status: int, result: dict[str, Any]) -> None:
        super().__init__(result.get("code"))
        self.status = status
        self.result = result


_STORED: LiteralString = """
SELECT library_id = %(library_id)s AND user_id = %(user_id)s AS mine,
       request_sha256, http_status, result
FROM prks.sync_operations WHERE op_id = %(op_id)s
"""
_CLAIM: LiteralString = """
INSERT INTO prks.sync_operations (op_id, library_id, device_id, user_id, operation_type, entity_type,
                                  entity_id, request_sha256, status, http_status, result)
VALUES (%(op_id)s, %(library_id)s, %(device_id)s, %(user_id)s, %(operation)s, %(entity_type)s,
        %(entity_id)s, %(digest)s, 'applying', 0, '{}')
ON CONFLICT (op_id) DO NOTHING
RETURNING op_id
"""
_FINISH: LiteralString = """
UPDATE prks.sync_operations SET status = %(status)s, http_status = %(http_status)s, result = %(result)s
WHERE library_id = %(library_id)s AND op_id = %(op_id)s
"""
_DEPENDENCIES: LiteralString = """
SELECT op_id::text AS op_id, device_id::text AS device_id, status FROM prks.sync_operations
WHERE library_id = %(library_id)s AND op_id = ANY(%(op_ids)s::uuid[])
"""
_OWN_DEVICE: LiteralString = "SELECT 1 FROM prks.devices WHERE id = %s AND user_id = %s"


def process_operation(database: Database, ctx: RequestContext, data: object,
                      env: Environment) -> Result:
    """Execute one semantic operation exactly once, ever, for ``ctx``.

    Returns ``(http_status, result)`` as ``sync_protocol.process_operation``
    does. Raises :class:`AccessRefused` (nothing written) when the actor may
    not edit the Library, the epoch changed or the device is not the user's.
    """
    try:
        op = rules.normalize_envelope(data)
    except ValueError as exc:
        return 400, {"code": exc.args[0]}
    if _holds_nul(op):
        return 400, {"code": "INVALID_ENVELOPE"}  # PostgreSQL text and jsonb cannot hold it
    handler = _HANDLERS.get(op["operation"])
    if handler is None:
        raise NotPortedError("sync operation " + op["operation"])
    try:
        handler.validate(op)
    except ValueError as exc:
        return 400, {"code": exc.args[0]}
    if ctx.user_id is None:
        raise AccessRefused(Refusal.FORBIDDEN)  # the ledger records a person, never a job
    digest = bytes.fromhex(rules.request_hash(op))
    received_at = datetime.now(timezone.utc)
    seen = library_query(database, ctx, lambda scope: _stored(scope, ctx, op))
    if seen is not None:
        return _replay(seen, digest)

    def run(scope: LibraryScope) -> Result:
        return _claim_and_apply(scope, ctx, op, digest, handler, received_at, env)

    for attempt in (1, 2):
        try:
            return library_command(database, ctx, Capability.EDIT_LIBRARY, run,
                                   isolation=handler.isolation)
        except _NotLedgered as refusal:
            return refusal.status, refusal.result
        except ConstraintViolationError as violation:
            # The arbiter of a race refused this transaction (§7.1); decide
            # again with the winner visible, once.
            if attempt == 2 or violation.constraint not in handler.retry_constraints:
                raise
    raise AssertionError("unreachable")  # pragma: no cover


def _holds_nul(value: object) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_holds_nul(key) or _holds_nul(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_holds_nul(item) for item in value)
    return False


def _stored(scope: LibraryScope, ctx: RequestContext, op: dict[str, Any]) -> Optional[dict[str, Any]]:
    row = scope.execute(_STORED, {"op_id": op["op_id"], "user_id": ctx.user_id}).fetchone()
    return None if row is None else dict(row)


def _replay(seen: dict[str, Any], digest: bytes) -> Result:
    if not seen["mine"] or bytes(seen["request_sha256"]) != digest:
        return 409, {"code": "OP_ID_REUSE"}
    return int(seen["http_status"]), dict(seen["result"])


def _claim_and_apply(scope: LibraryScope, ctx: RequestContext, op: dict[str, Any], digest: bytes,
                     handler: Handler, received_at: datetime, env: Environment) -> Result:
    if scope.execute_unscoped(_OWN_DEVICE, (op["device_id"], ctx.user_id)).fetchone() is None:
        raise AccessRefused(Refusal.FORBIDDEN)
    claimed = scope.execute(_CLAIM, {
        "op_id": op["op_id"], "device_id": op["device_id"], "user_id": ctx.user_id,
        "operation": op["operation"], "entity_type": op["entity_type"],
        "entity_id": op["entity_id"], "digest": digest,
    }).fetchone()
    if claimed is None:
        seen = _stored(scope, ctx, op)
        if seen is None:  # pragma: no cover - the conflicting row is committed and never deleted
            raise RuntimeError("the ledger row that refused the claim is gone")
        return _replay(seen, digest)
    if not _dependencies_satisfied(scope, op):
        raise _NotLedgered(400, {"code": "UNSATISFIED_DEPENDENCY"})
    status, result = handler.apply(scope, op, received_at, env)
    scope.execute(_FINISH, {
        "op_id": op["op_id"], "status": result["code"], "http_status": status,
        "result": Jsonb(handler.ledgered(op, result)),
    })
    return status, result


def _dependencies_satisfied(scope: LibraryScope, op: dict[str, Any]) -> bool:
    wanted: list[str] = op["depends_on"]
    if not wanted:
        return True
    rows = scope.execute(_DEPENDENCIES, {"op_ids": wanted}).fetchall()
    found = {str(row["op_id"]): row for row in rows}
    return all(
        dep in found and found[dep]["device_id"] == op["device_id"]
        and found[dep]["status"] == rules.ACKNOWLEDGED
        for dep in wanted
    )


# Registration is explicit, as in the SQLite protocol: the families PostgreSQL
# accepts are readable in one place. Later lanes add theirs here.
from backend.sync.handlers import (  # noqa: E402
    person, person_group, tag, tag_link, work_lifecycle, work_metadata, work_notes, work_open,
    work_role, work_source,
)

register("MARK_WORK_OPENED", work_open.HANDLER)
register("SET_WORK_METADATA_FIELD", work_metadata.HANDLER)
register("SET_WORK_SOURCE", work_source.HANDLER)
register("CREATE_WORK", work_lifecycle.CREATE_HANDLER)
register("DELETE_WORK", work_lifecycle.DELETE_HANDLER)
register("SET_WORK_RESEARCH_NOTE", work_notes.HANDLER)
register("SET_WORK_PRIVATE_NOTE", work_notes.HANDLER)
register("ADD_WORK_PERSON_ROLE", work_role.ADD_HANDLER)
register("REMOVE_WORK_PERSON_ROLE", work_role.HANDLER)
register("SET_WORK_PERSON_ROLE_CREDIT", work_role.HANDLER)
register("CREATE_PERSON", person.CREATE_HANDLER)
register("DELETE_PERSON", person.DELETE_HANDLER)
register("SET_PERSON_METADATA_FIELD", person.FIELD_HANDLER)
register("CREATE_PERSON_GROUP", person_group.CREATE_HANDLER)
register("SET_PERSON_GROUP_FIELD", person_group.FIELD_HANDLER)
register("ADD_PERSON_GROUP_MEMBER", person_group.ADD_MEMBER_HANDLER)
register("REMOVE_PERSON_GROUP_MEMBER", person_group.REMOVE_MEMBER_HANDLER)
register("DELETE_PERSON_GROUP", person_group.DELETE_HANDLER)
register("CREATE_TAG", tag.CREATE_HANDLER)
register("DELETE_TAG", tag.DELETE_HANDLER)
register("MERGE_TAG", tag.MERGE_HANDLER)
register("ADD_WORK_TAG", tag_link.ADD_WORK_HANDLER)
register("REMOVE_WORK_TAG", tag_link.REMOVE_WORK_HANDLER)
register("ADD_FOLDER_TAG", tag_link.ADD_FOLDER_HANDLER)
register("REMOVE_FOLDER_TAG", tag_link.REMOVE_FOLDER_HANDLER)
