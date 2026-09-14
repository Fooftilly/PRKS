"""Person profile fields: per-FIELD revisions and the SET_PERSON_METADATA_FIELD handler.

The conflict unit is one field, not one profile. That is a reading of the
Person schema rather than a copy of the Work-metadata family: every editable
column on `persons` is an independent scalar -- a biography, a birth date and a
Wikipedia link are separate decisions, and two devices that changed different
ones have not disagreed about anything. A profile-level revision would tell
them they had, and force a resolution UI over a conflict that does not exist.
The canonical writer is already field-shaped for the same reason:
`update_person_profile` applies whatever subset of `PERSON_METADATA_FIELDS` it
is handed.

The one form with one Save button in the UI is a grouping of controls, not an
atomicity requirement. Group membership IS atomic with metadata on the ordinary
PATCH, and is deliberately outside this family: it is a relationship, not a
scalar, and it belongs to its own milestone.

`SYNCED_FIELDS` here is the SAME vocabulary as `CREATE_PERSON`, because a field
that can be constructed and not edited -- or the reverse -- is a split contract
by another name. There are no per-field byte bounds for the same reason: the
creation family has none either, and the generic envelope bound already caps
what a durable operation may carry. Inventing a limit here would make a value
savable through one path and refused through another.

Nothing in this module imports the database layer; the handler is handed the
`db` it needs, so the ordinary PATCH boundary can share the same helpers.
"""
import json

from backend import work_metadata_sync as meta
from backend.person_image import PersonImageUrlError, normalize_person_image_url
from backend.person_sync import FIELDS as PERSON_FIELDS

# The editable profile columns, in the creation family's order. `id`,
# `created_at` and `updated_at` are not user-editable and are absent by
# construction rather than by filtering.
SYNCED_FIELDS = frozenset(PERSON_FIELDS)

SCOPE_TYPE = "person-field"


def scope_key(person_id, field):
    # Structural encoding, like every other scope in this protocol: no
    # delimiter has to be excluded from either component for it to stay
    # unambiguous.
    return json.dumps([person_id, field], ensure_ascii=True, separators=(",", ":"))


def canonical_wire(field, value):
    """The spelling two devices must agree on for this field.

    Only `image_url` has one: the portrait normalizer is what decides whether
    two spellings name the same picture, so comparing raw input would report a
    conflict between devices that chose the same image. Every other profile
    field is stored as typed.
    """
    text = "" if value is None else str(value)
    if field == "image_url":
        return normalize_person_image_url(text)
    return text


def is_valid_field_value(field, value):
    """The ONE rule every Person-field mutation path asks.

    Deliberately the same rule `CREATE_PERSON` applies, and no more: any string
    is acceptable except an `image_url` the portrait normalizer refuses. A
    stricter rule here than on creation or on the ordinary PATCH would be a
    second validation contract -- a value savable by one path and refused by
    another -- which is exactly what moving a field to local-first exists to
    remove.
    """
    if not isinstance(value, str):
        return False
    if field == "image_url":
        try:
            normalize_person_image_url(value)
        except PersonImageUrlError:
            return False
    return True


def disagreement(current, desired):
    """What the two sides hold.

    Both values in full, because the client can then offer "Use server"
    without a second request. `fit_terminal_result` degrades this to bounded
    previews when a profile field is long enough that the whole result would
    not fit the client's durable bound -- no Person field has a length limit
    of its own, so that degradation is the only thing standing between a long
    biography and a conflict the client cannot store.
    """
    return {"current_value": current, "requested_value": desired}


def get_revision(conn, person_id, field):
    row = conn.execute(
        "SELECT revision FROM sync_entity_revisions WHERE scope_type = ? AND scope_id = ?",
        (SCOPE_TYPE, scope_key(person_id, field))).fetchone()
    return row[0] if row else 0


def set_field_on_conn(conn, person_id, field, value):
    """Write one profile field and advance its revision together, in the
    caller's transaction. Returns (changed, revision_after).

    A no-op write advances nothing: a revision records the value actually
    changing, and inflating it would manufacture staleness for every device
    that already holds the current value. `persons` columns are nullable, so
    NULL and "" are compared as the same state -- a device that cleared a
    field it had never set has not changed anything.
    """
    if field not in SYNCED_FIELDS:
        raise ValueError("unsupported synchronized field: %s" % field)
    desired = canonical_wire(field, value)
    row = conn.execute("SELECT %s FROM persons WHERE id = ?" % field, (person_id,)).fetchone()
    if row is None:
        return False, 0
    revision = get_revision(conn, person_id, field)
    if ("" if row[0] is None else str(row[0])) == desired:
        return False, revision
    conn.execute(
        "UPDATE persons SET %s = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?" % field,
        (desired, person_id))
    conn.execute("""INSERT INTO sync_entity_revisions (scope_type, scope_id, revision)
                    VALUES (?, ?, 1) ON CONFLICT (scope_type, scope_id)
                    DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP""",
                 (SCOPE_TYPE, scope_key(person_id, field)))
    return True, revision + 1


def get_field_state_on_conn(conn, person_id):
    """Every supported field's revision, or None when the Person is gone.

    Revisions ONLY, unlike the Work equivalent, and for a reason specific to
    this entity: the Person detail the client already caches carries all eleven
    values, and none of them has a length bound, so echoing them here would
    make a second copy of the whole profile -- biography included -- in what
    this endpoint sends, what IndexedDB stores, and what every re-read costs.
    A field with no revision row has never changed since revisions existed,
    which is revision 0 -- the same "missing means zero" rule every other scope
    uses.
    """
    if conn.execute("SELECT 1 FROM persons WHERE id = ?", (person_id,)).fetchone() is None:
        return None
    # Asked for by key rather than by scanning every `person-field` row in the
    # library: `(scope_type, scope_id)` is the primary key, so this is an
    # indexed lookup of at most one row per supported field, and its cost does
    # not grow with the number of people.
    by_key = {scope_key(person_id, field): field for field in SYNCED_FIELDS}
    placeholders = ", ".join("?" * len(by_key))
    revisions = {
        by_key[scope_id]: revision
        for scope_id, revision in conn.execute(
            "SELECT scope_id, revision FROM sync_entity_revisions "
            "WHERE scope_type = ? AND scope_id IN (%s)" % placeholders,
            (SCOPE_TYPE,) + tuple(by_key),
        ).fetchall()
    }
    return {
        "person_id": person_id,
        "fields": {field: {"revision": revisions.get(field, 0)}
                   for field in sorted(SYNCED_FIELDS)},
    }


def validate(op):
    payload = op["payload"]
    if set(payload) != {"field", "value"}:
        raise ValueError("INVALID_ENVELOPE")
    field, value = payload["field"], payload["value"]
    # An arbitrary column name from a client would be an injection surface and
    # a way to reach columns that are not user-editable at all.
    if not isinstance(field, str) or field not in SYNCED_FIELDS:
        raise ValueError("INVALID_ENVELOPE")
    if not isinstance(value, str) or not is_valid_field_value(field, value):
        raise ValueError("INVALID_ENVELOPE")
    # Editing is mutation, not construction: a null base revision is a client
    # that cannot detect a conflict, and would silently overwrite whatever
    # another device wrote. `CREATE_PERSON` requires the opposite, which is the
    # whole difference between the two families.
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def apply(db, conn, op, received_at):
    person_id = op["entity_id"]
    field, desired = op["payload"]["field"], op["payload"]["value"]
    result = {"person_id": person_id, "field": field}
    row = conn.execute("SELECT %s FROM persons WHERE id = ?" % field, (person_id,)).fetchone()
    if row is None:
        # A Person this device created may still be waiting on its own
        # CREATE_PERSON; the coordinator's dependency ordering is what keeps
        # that from arriving here, so reaching this line means the Person is
        # genuinely gone.
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    current = "" if row[0] is None else str(row[0])
    desired = canonical_wire(field, desired)
    revision = get_revision(conn, person_id, field)
    base = op["base_revision"]
    if base > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      **disagreement(current, desired))
        return 400, meta.fit_terminal_result(result, current, desired)
    # A stale base is only a conflict when the two devices actually disagree.
    # Two people correcting the same spelling have converged, not collided.
    if base < revision and current != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      **disagreement(current, desired))
        return 409, meta.fit_terminal_result(result, current, desired)
    changed, after = set_field_on_conn(conn, person_id, field, desired)
    # The acknowledgement does NOT echo the value back. No Person field has a
    # length bound, the ledger has no retention policy, and a client that
    # cannot durably store an oversized result reads it as a failed sync and
    # retries the same operation forever. So the ACK says only that the value
    # was applied -- the client already holds the authoritative copy in its
    # immutable payload, and the canonical form is reachable from it (the only
    # field whose stored spelling differs from what was typed is `image_url`,
    # and the difference is a trim the client performs before enqueueing).
    result.update(code="ACKNOWLEDGED", server_revision=after, changed=changed,
                  value_omitted=True)
    return 200, result


class _Handler:
    validate = staticmethod(validate)
    apply = staticmethod(apply)


HANDLER = _Handler()
