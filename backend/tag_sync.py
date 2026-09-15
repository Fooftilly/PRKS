"""The Tag VOCABULARY: CREATE_TAG and DELETE_TAG.

The Work-Tag *relationship* has been durable since Phase 2 (`work_tag_sync`).
This module is the other half: the Tags themselves.

Two shapes, and only two. PRKS has no rename and no colour editor -- `POST
/api/tags` either creates a Tag or hands back the existing one, and every
caller sends the same default colour -- so a `SET_TAG_FIELD` family would be
inventing product semantics rather than moving existing ones off the network.
That decision is recorded in `docs/local-first-rollout-status.md` rather than
guessed at here.

Tag identity is PERSISTENT. `delete_tag` and `merge_tags_into` are the only
operations that may destroy or transform it, and this module adds no third one:
an unused Tag stays in the catalogue.

A name is unique across canonical names AND aliases, case-insensitively, and
only the server sees every Tag -- so a client that minted an id for a name
somebody else already used is refused with `NAME_TAKEN`. It is deliberately not
converged onto the existing Tag: operations already queued behind the creation
name the id this device minted, and silently redirecting them to a different
Tag is exactly what the Work-Tag family refuses to do when a Tag turns out to
have been merged.
"""
from backend.entity_ids import is_distributed

MAX_NAME_BYTES = 200
DEFAULT_COLOR = "#6d6cf7"


def canonical_name(value):
    return ("" if value is None else str(value)).strip()


def validate_create(op):
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")
    if not is_distributed(op["entity_id"], "T"):
        raise ValueError("INVALID_ENVELOPE")
    payload = op["payload"]
    if set(payload) != {"name", "color"}:
        raise ValueError("INVALID_ENVELOPE")
    name = payload["name"]
    if not isinstance(name, str) or not canonical_name(name):
        raise ValueError("INVALID_ENVELOPE")
    if len(canonical_name(name).encode("utf-8")) > MAX_NAME_BYTES:
        raise ValueError("INVALID_ENVELOPE")
    if not isinstance(payload["color"], str) or not payload["color"].strip():
        raise ValueError("INVALID_ENVELOPE")


def _row(conn, tag_id):
    row = conn.execute(
        "SELECT id, name, color FROM tags WHERE id = ?", (tag_id,)).fetchone()
    return dict(row) if row is not None else None


def _id_for_label(conn, label):
    """A canonical name or alias, any casing, mapped to its Tag id.

    The same rule `resolve_tag_id_by_label` applies, on the caller's
    transaction: uniqueness has to be decided inside the write that depends on
    it, or two devices creating the same name concurrently could both pass.
    """
    row = conn.execute(
        "SELECT id FROM tags WHERE LOWER(name) = LOWER(?) LIMIT 1", (label,)).fetchone()
    if row is not None:
        return row[0]
    row = conn.execute(
        "SELECT tag_id FROM tag_aliases WHERE LOWER(alias) = LOWER(?) LIMIT 1",
        (label,)).fetchone()
    return row[0] if row is not None else None


def insert_tag_on_conn(conn, tag_id, name, color=DEFAULT_COLOR):
    """Construction, with its lifecycle row.

    The lifecycle row is what every later answer about this Tag is read from --
    active, merged or deleted -- so a Tag inserted without one would come back
    as UNKNOWN to the very relationship family that depends on it.
    """
    clean = canonical_name(name)
    if not clean:
        raise ValueError("tag name is empty")
    conn.execute("INSERT INTO tags (id, name, color) VALUES (?, ?, ?)",
                 (tag_id, clean, color or DEFAULT_COLOR))
    conn.execute("INSERT INTO sync_tag_lifecycle (tag_id, state) VALUES (?, 'active')",
                 (tag_id,))
    return tag_id


def apply_create(db, conn, op, received_at):
    tag_id = op["entity_id"]
    name = canonical_name(op["payload"]["name"])
    existing = _row(conn, tag_id)
    if existing is not None:
        # A retry after a lost response. Creation is not an update: the stored
        # row is acknowledged as it stands, name and colour included.
        return 200, {"code": "ACKNOWLEDGED", "tag_id": tag_id, "changed": False,
                     "tag": existing}
    taken = _id_for_label(conn, name)
    if taken is not None:
        return 409, {"code": "NAME_TAKEN", "tag_id": tag_id,
                     "target_tag_id": taken}
    insert_tag_on_conn(conn, tag_id, name, op["payload"]["color"])
    return 200, {"code": "ACKNOWLEDGED", "tag_id": tag_id, "changed": True,
                 "tag": _row(conn, tag_id)}


def validate_delete(op):
    if op["payload"] != {}:
        raise ValueError("INVALID_ENVELOPE")
    # Destruction addresses an IDENTITY, not a value: absence is idempotent.
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")


def apply_delete(db, conn, op, received_at):
    from backend import work_tag_sync

    tag_id = op["entity_id"]
    lifecycle = work_tag_sync.resolve_lifecycle(conn, tag_id)
    state = lifecycle["state"]
    if state in ("DELETED", "UNKNOWN"):
        # Deleting a Tag that is already gone is CONVERGENCE: the user asked
        # for its absence and it is absent.
        return 200, {"code": "ACKNOWLEDGED", "tag_id": tag_id, "changed": False,
                     "affected_work_ids": []}
    if state == "MERGED":
        # It is not there to delete, and it is not gone either -- it became
        # another Tag. Silently deleting the target would destroy a Tag the
        # user never named.
        return 409, {"code": "TAG_MERGED", "tag_id": tag_id,
                     "target_tag_id": lifecycle["target_tag_id"]}
    affected = db._entities_linked_to_tag_on_conn(conn, tag_id)
    for work_id in affected["affected_work_ids"]:
        work_tag_sync.set_state(conn, work_id, tag_id, False)
    conn.execute(
        "UPDATE sync_tag_lifecycle SET state = 'deleted', target_tag_id = NULL, "
        "changed_at = CURRENT_TIMESTAMP WHERE tag_id = ?", (tag_id,))
    conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    return 200, {"code": "ACKNOWLEDGED", "tag_id": tag_id, "changed": True,
                 "affected_work_ids": list(affected["affected_work_ids"])}


def _handler(validate_fn, apply_fn):
    return type("_Handler", (), {"validate": staticmethod(validate_fn),
                                 "apply": staticmethod(apply_fn)})()


CREATE_HANDLER = _handler(validate_create, apply_create)
DELETE_HANDLER = _handler(validate_delete, apply_delete)
