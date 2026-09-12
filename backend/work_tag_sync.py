"""Work-Tag domain: relationship revisions, Tag lifecycle, and the
ADD_WORK_TAG / REMOVE_WORK_TAG handler.

The generic envelope, request hash, ledger and dispatch live in
`sync_protocol`. Everything here is specific to what a Work-Tag relationship
means. All canonical writes share the caller's transaction, so one `set_state()`
boundary serves the sync handler, the direct endpoints, bulk edits, Tag merge
and Tag delete -- a relationship change and its revision always advance
together.
"""
import json

MAX_TAG_ID_CHARS = 200


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


def validate(op):
    """Work-Tag payload and concurrency rules.

    A Work-Tag edit is optimistic-concurrency controlled, so a null
    `base_revision` is not "no opinion" -- it is a client that cannot detect a
    conflict, and accepting it would silently overwrite another device.
    """
    payload = op["payload"]
    if set(payload) != {"tag_id"}:
        raise ValueError("INVALID_ENVELOPE")
    tag_id = payload["tag_id"]
    if not isinstance(tag_id, str) or not tag_id.strip() or tag_id != tag_id.strip() \
            or len(tag_id) > MAX_TAG_ID_CHARS:
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def apply(db, conn, op, received_at):
    work_id, tag_id = op["entity_id"], op["payload"]["tag_id"]
    result = {"work_id": work_id, "tag_id": tag_id}
    status = 409
    if not conn.execute("SELECT 1 FROM works WHERE id = ?", (work_id,)).fetchone():
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
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
        present = bool(conn.execute(
            "SELECT 1 FROM work_tags WHERE work_id = ? AND tag_id = ?", (work_id, tag_id)).fetchone())
        desired = op["operation"] == "ADD_WORK_TAG"
        base = op["base_revision"]
        if base > revision:
            status = 400
            result.update(code="FUTURE_REVISION", current_revision=revision,
                          current_state=present, requested_state=desired)
        elif base < revision and present != desired:
            result.update(code="REVISION_CONFLICT", current_revision=revision,
                          current_state=present, requested_state=desired)
        else:
            changed = set_state(conn, work_id, tag_id, desired)
            status = 200
            tag = conn.execute("SELECT id, name, color FROM tags WHERE id = ?", (tag_id,)).fetchone()
            result.update(code="ACKNOWLEDGED", present=desired, server_revision=revision + int(changed),
                          changed=changed, tag=dict(tag))
    return status, result


class _Handler:
    validate = staticmethod(validate)
    apply = staticmethod(apply)


HANDLER = _Handler()
