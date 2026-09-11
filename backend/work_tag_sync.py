"""Semantic Work-Tag protocol. All canonical writes share the caller's transaction.

The ledger, revisions and lifecycle history are canonical backup state. No retention
policy exists: dropping an old idempotency row could reapply an old operation.
"""
import hashlib
import json
import re
from datetime import datetime, timezone

UUID = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
FIELDS = frozenset(("op_id", "device_id", "operation", "entity_type", "entity_id",
                    "payload", "base_revision", "occurred_at", "created_at", "depends_on"))
MAX_REVISION = 9007199254740991


def scope_key(work_id, tag_id):
    # Structural encoding: IDs need not exclude any delimiter.
    return json.dumps([work_id, tag_id], ensure_ascii=True, separators=(",", ":"))


def get_revision(conn, work_id, tag_id):
    row = conn.execute("SELECT revision FROM sync_entity_revisions WHERE scope_type = 'work-tag' AND scope_id = ?",
                       (scope_key(work_id, tag_id),)).fetchone()
    return row[0] if row else 0


def set_state(conn, work_id, tag_id, present):
    if present:
        changed = conn.execute("INSERT INTO work_tags (work_id, tag_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
                               (work_id, tag_id)).rowcount > 0
    else:
        changed = conn.execute("DELETE FROM work_tags WHERE work_id = ? AND tag_id = ?",
                               (work_id, tag_id)).rowcount > 0
    if changed:
        conn.execute("""INSERT INTO sync_entity_revisions (scope_type, scope_id, revision)
                        VALUES ('work-tag', ?, 1) ON CONFLICT (scope_type, scope_id)
                        DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP""",
                     (scope_key(work_id, tag_id),))
    return changed


def resolve_lifecycle(conn, tag_id):
    current, seen = tag_id, set()
    while current not in seen:
        seen.add(current)
        row = conn.execute("SELECT state, target_tag_id FROM sync_tag_lifecycle WHERE tag_id = ?", (current,)).fetchone()
        if not row:
            return {"state": "UNKNOWN"}
        if row[0] == "deleted":
            return {"state": "DELETED"}
        if row[0] == "active":
            if not conn.execute("SELECT 1 FROM tags WHERE id = ?", (current,)).fetchone():
                return {"state": "UNKNOWN"}
            return {"state": "ACTIVE"} if current == tag_id else {"state": "MERGED", "target_tag_id": current}
        current = row[1]
    # Corrupt lifecycle is a server failure, never a ledgered semantic outcome.
    raise RuntimeError("Tag lifecycle cycle")


def tag_options(conn, work_id):
    if not conn.execute("SELECT 1 FROM works WHERE id = ?", (work_id,)).fetchone():
        return None
    assigned, absent = [], {}
    rows = conn.execute("""SELECT t.id, wt.work_id FROM tags t
        JOIN sync_tag_lifecycle l ON l.tag_id = t.id AND l.state = 'active'
        LEFT JOIN work_tags wt ON wt.tag_id = t.id AND wt.work_id = ? ORDER BY t.id""", (work_id,)).fetchall()
    for row in rows:
        revision = get_revision(conn, work_id, row[0])
        if row[1] is not None:
            assigned.append({"tag_id": row[0], "relation_revision": revision})
        elif revision:
            absent[row[0]] = revision
    return {"work_id": work_id, "assigned": assigned, "known_absent": absent}


def normalize_request(data):
    if not isinstance(data, dict) or set(data) != FIELDS:
        raise ValueError("INVALID_ENVELOPE")
    out = dict(data)
    for field in ("op_id", "device_id"):
        if not isinstance(data[field], str) or not UUID.fullmatch(data[field]):
            raise ValueError("INVALID_ENVELOPE")
        out[field] = data[field].lower()
    if data["operation"] not in ("ADD_WORK_TAG", "REMOVE_WORK_TAG") or data["entity_type"] != "work":
        raise ValueError("INVALID_ENVELOPE")
    payload = data["payload"]
    if not isinstance(payload, dict) or set(payload) != {"tag_id"}:
        raise ValueError("INVALID_ENVELOPE")
    for value in (data["entity_id"], payload["tag_id"]):
        if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 200:
            raise ValueError("INVALID_ENVELOPE")
    revision = data["base_revision"]
    if type(revision) is not int or not 0 <= revision <= MAX_REVISION:
        raise ValueError("INVALID_BASE_REVISION")
    # v1 deliberately supports no dependency execution.
    if data["depends_on"] != []:
        raise ValueError("UNSUPPORTED_DEPENDENCIES")
    for field in ("occurred_at", "created_at"):
        value = data[field]
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError("INVALID_ENVELOPE")
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError()
            out[field] = timestamp.astimezone(timezone.utc).isoformat()
        except ValueError:
            raise ValueError("INVALID_ENVELOPE") from None
    return out


def insert_result(conn, op, request_hash, http_status, result):
    conn.execute("""INSERT INTO sync_operations
        (op_id, device_id, operation_type, entity_type, entity_id, request_hash, status, http_status, result_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (op["op_id"], op["device_id"], op["operation"], op["entity_type"], op["entity_id"],
         request_hash, result["code"], http_status, json.dumps(result, sort_keys=True, separators=(",", ":"))))


def process_operation(db, data):
    try:
        op = normalize_request(data)
    except ValueError as exc:
        return 400, {"code": exc.args[0]}
    request_hash = hashlib.sha256(json.dumps(op, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        seen = conn.execute("SELECT request_hash, http_status, result_json FROM sync_operations WHERE op_id = ?",
                            (op["op_id"],)).fetchone()
        if seen:
            if seen[0] != request_hash:
                return 409, {"code": "OP_ID_REUSE"}
            return seen[1], json.loads(seen[2])
        work_id, tag_id = op["entity_id"], op["payload"]["tag_id"]
        result = {"work_id": work_id, "tag_id": tag_id}
        status = 409
        if not conn.execute("SELECT 1 FROM works WHERE id = ?", (work_id,)).fetchone():
            result["code"] = "ENTITY_NOT_FOUND"
            status = 404
        else:
            lifecycle = resolve_lifecycle(conn, tag_id)
            state = lifecycle["state"]
            if state == "UNKNOWN":
                result["code"] = "ENTITY_NOT_FOUND"
                status = 404
            elif state == "DELETED":
                result["code"] = "TAG_DELETED"
            elif state == "MERGED":
                result.update(code="TAG_MERGED", target_tag_id=lifecycle["target_tag_id"])
            else:
                revision = get_revision(conn, work_id, tag_id)
                present = bool(conn.execute("SELECT 1 FROM work_tags WHERE work_id = ? AND tag_id = ?", (work_id, tag_id)).fetchone())
                desired = op["operation"] == "ADD_WORK_TAG"
                base = op["base_revision"]
                if base > revision:
                    status = 400
                    result.update(code="FUTURE_REVISION", current_revision=revision, current_state=present, requested_state=desired)
                elif base < revision and present != desired:
                    result.update(code="REVISION_CONFLICT", current_revision=revision, current_state=present, requested_state=desired)
                else:
                    changed = set_state(conn, work_id, tag_id, desired)
                    status = 200
                    tag = conn.execute("SELECT id, name, color FROM tags WHERE id = ?", (tag_id,)).fetchone()
                    result.update(code="ACKNOWLEDGED", present=desired, server_revision=revision + int(changed),
                                  changed=changed, tag=dict(tag))
        insert_result(conn, op, request_hash, status, result)
        return status, result
