"""Folder-Tag domain: relationship revisions and the ADD_FOLDER_TAG /
REMOVE_FOLDER_TAG handler.

Mirrors `work_tag_sync` with conflict unit `(folder, tag)`. The Tag lifecycle
table is shared. Canonical writes share the caller's transaction so one
`set_state()` boundary serves the sync handler, the direct endpoints, Tag merge
and Tag delete.
"""
import json

MAX_TAG_ID_CHARS = 200

SCOPE_TYPE = "folder-tag"


def scope_key(folder_id, tag_id):
    return json.dumps([folder_id, tag_id], ensure_ascii=True, separators=(",", ":"))


def get_revision(conn, folder_id, tag_id):
    row = conn.execute(
        "SELECT revision FROM sync_entity_revisions WHERE scope_type = ? AND scope_id = ?",
        (SCOPE_TYPE, scope_key(folder_id, tag_id)),
    ).fetchone()
    return row[0] if row else 0


def set_state(conn, folder_id, tag_id, present):
    if present:
        changed = conn.execute(
            "INSERT INTO folder_tags (folder_id, tag_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
            (folder_id, tag_id),
        ).rowcount > 0
    else:
        changed = conn.execute(
            "DELETE FROM folder_tags WHERE folder_id = ? AND tag_id = ?",
            (folder_id, tag_id),
        ).rowcount > 0
    if changed:
        conn.execute(
            """INSERT INTO sync_entity_revisions (scope_type, scope_id, revision)
               VALUES (?, ?, 1) ON CONFLICT (scope_type, scope_id)
               DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP""",
            (SCOPE_TYPE, scope_key(folder_id, tag_id)),
        )
    return changed


def resolve_lifecycle(conn, tag_id):
    # Shared with Work-Tag: one lifecycle history for the Tag vocabulary.
    from backend import work_tag_sync
    return work_tag_sync.resolve_lifecycle(conn, tag_id)


def tag_options(conn, folder_id):
    if not conn.execute("SELECT 1 FROM folders WHERE id = ?", (folder_id,)).fetchone():
        return None
    assigned, absent = [], {}
    rows = conn.execute(
        """SELECT t.id, ft.folder_id FROM tags t
        JOIN sync_tag_lifecycle l ON l.tag_id = t.id AND l.state = 'active'
        LEFT JOIN folder_tags ft ON ft.tag_id = t.id AND ft.folder_id = ?
        ORDER BY t.id""",
        (folder_id,),
    ).fetchall()
    for row in rows:
        revision = get_revision(conn, folder_id, row[0])
        if row[1] is not None:
            assigned.append({"tag_id": row[0], "relation_revision": revision})
        elif revision:
            absent[row[0]] = revision
    return {"folder_id": folder_id, "assigned": assigned, "known_absent": absent}


def validate(op):
    """Folder-Tag payload and concurrency rules (same posture as Work-Tag)."""
    payload = op["payload"]
    if set(payload) != {"tag_id"}:
        raise ValueError("INVALID_ENVELOPE")
    tag_id = payload["tag_id"]
    if (not isinstance(tag_id, str) or not tag_id.strip() or tag_id != tag_id.strip()
            or len(tag_id) > MAX_TAG_ID_CHARS):
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def apply(db, conn, op, received_at):
    folder_id, tag_id = op["entity_id"], op["payload"]["tag_id"]
    result = {"folder_id": folder_id, "tag_id": tag_id}
    status = 409
    if not conn.execute("SELECT 1 FROM folders WHERE id = ?", (folder_id,)).fetchone():
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
        revision = get_revision(conn, folder_id, tag_id)
        present = bool(conn.execute(
            "SELECT 1 FROM folder_tags WHERE folder_id = ? AND tag_id = ?",
            (folder_id, tag_id),
        ).fetchone())
        desired = op["operation"] == "ADD_FOLDER_TAG"
        base = op["base_revision"]
        if base > revision:
            status = 400
            result.update(code="FUTURE_REVISION", current_revision=revision,
                          current_state=present, requested_state=desired)
        elif base < revision and present != desired:
            result.update(code="REVISION_CONFLICT", current_revision=revision,
                          current_state=present, requested_state=desired)
        else:
            changed = set_state(conn, folder_id, tag_id, desired)
            status = 200
            tag = conn.execute(
                "SELECT id, name, color FROM tags WHERE id = ?", (tag_id,)
            ).fetchone()
            result.update(
                code="ACKNOWLEDGED", present=desired,
                server_revision=revision + int(changed),
                changed=changed, tag=dict(tag),
            )
    return status, result


class _Handler:
    validate = staticmethod(validate)
    apply = staticmethod(apply)


HANDLER = _Handler()
