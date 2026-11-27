"""``POST /api/sync/operations``: one durable operation (§8.4, §11.4).

``backend.sync.protocol`` claims, applies and ledgers the operation in one
transaction for the admitted context; a refusal (epoch, device, role)
propagates to the dispatcher, which answers it as admission does. The
Research index is kept by the database, so a note ACK only reads its refs,
``pending`` included (the Research-ACK fence). A deleted Work's files are
the orphan sweep's (§11.2), never this route's.
"""

from __future__ import annotations

import logging

from backend.api_contract.operation import Operation, body, error, ok
from backend.api_contract.sync import SyncOperation, SyncResult
from backend.domain.works import note_rules
from backend.runtime.log_safety import safe_error_type, safe_log_id
from backend.web.http import Request, Response, json_response
from backend.web.routing import route

LOGGER = logging.getLogger("prks.server")


def post_operation(req: Request) -> Response:
    from backend.sync.families import Environment
    from backend.sync.protocol import process_operation

    data = req.data
    database, ctx = req.actor
    status, result = process_operation(database, ctx, data, Environment())
    acknowledged = status == 200 and result.get("code") == "ACKNOWLEDGED"
    operation = data.get("operation")
    if acknowledged and operation == note_rules.RESEARCH_OPERATION:
        # The note and its ledger row are committed; a failed derived read
        # leaves the refs out (the client reads them later) and never turns
        # a saved note into an error.
        try:
            from backend.domain.research import index_commands

            result["research_refs"] = index_commands.research_refs(database, ctx, data.get("entity_id"))
        except Exception as e:
            LOGGER.warning("research_refs_read_failed request_id=%s error_type=%s",
                           safe_log_id(req.request_id), safe_error_type(e))
    if acknowledged and operation == "DELETE_WORK":
        # Paths are not durable client state; strip before the wire.
        result = {"code": result["code"], "work_id": result["work_id"], "changed": result["changed"]}
    return json_response(req, status, result)


ROUTES = (
    route("POST", "/api/sync/operations", post_operation, contract=Operation(
        "postSyncOperation", "Apply one durable operation, exactly once", "sync",
        {200: ok(SyncResult, "Acknowledged, or the ledgered answer to the same operation sent before."),
         400: error("A malformed envelope or payload (its `code`).", SyncResult),
         404: error("The entity does not exist (its `code`).", SyncResult),
         409: error("A revision conflict, a reused op_id or another family refusal (its `code`).", SyncResult)},
        request=body(SyncOperation),
        description="The operation, its effects and its ledger row commit in one transaction.")),
)
