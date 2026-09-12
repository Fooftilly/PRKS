"""Work metadata fields: per-FIELD revisions and the SET_WORK_METADATA_FIELD handler.

The conflict unit here is one field, not one Work. Two devices editing `doi`
and `isbn` on the same Work have not disagreed about anything, and a single
Work-level revision would tell them they had -- forcing a resolution UI over a
conflict that does not exist. So each supported field carries its own revision
scope and they advance independently.

Only the seven bibliographic scalars below are synchronized. They were chosen
because they are real user-editable metadata that no other cached read model
RENDERS: the Work summary projection carries them, but Work cards, browse
catalogs, Folders, People, Playlists and the Graph display none of them. A
pending value therefore needs no optimistic propagation beyond the Work itself.
Higher fan-out fields (title, status, doc_type, year, abstract) are a separate
problem and are deliberately not here.

Nothing in this module imports the database layer; the handler is handed the
`db` it needs, so the ordinary PATCH boundary can share the same helpers.
"""
import json

# Field -> maximum accepted length. The bound is the only validation this layer
# adds: synchronization is not a licence to start normalizing values PRKS never
# normalized. A DOI keeps its case, an ISBN keeps its punctuation, and a page
# range is not parsed -- the sync path must store exactly what a PATCH would.
SYNCED_FIELDS = {
    "edition": 200,
    "journal": 500,
    "volume": 100,
    "issue": 100,
    "pages": 200,
    "isbn": 100,
    "doi": 500,
}


def scope_key(work_id, field):
    # Structural encoding, like the Work-Tag scope: no delimiter has to be
    # excluded from either component for this to stay unambiguous.
    return json.dumps([work_id, field], ensure_ascii=True, separators=(",", ":"))


def canonical(value):
    """The one representation of a field value.

    SQLite holds NULL for rows created before a field existed and "" for one a
    user cleared; both mean "no value", so they compare equal. Whitespace is
    NOT stripped -- that would make the sync path disagree with PATCH about
    what the user typed, and the column has never been normalized that way.
    """
    return "" if value is None else str(value)


def get_field_state_on_conn(conn, work_id):
    """Every supported field's canonical value and revision, or None.

    A field with no revision row has never been changed since revisions
    existed, which is revision 0 -- the same "missing means zero" rule the
    Work-Tag scopes use.
    """
    columns = ", ".join(sorted(SYNCED_FIELDS))
    row = conn.execute("SELECT %s FROM works WHERE id = ?" % columns, (work_id,)).fetchone()
    if row is None:
        return None
    revisions = {}
    for scope_id, revision in conn.execute(
        "SELECT scope_id, revision FROM sync_entity_revisions WHERE scope_type = 'work-field'"
    ).fetchall():
        try:
            owner, field = json.loads(scope_id)
        except (ValueError, TypeError):
            continue
        if owner == work_id:
            revisions[field] = revision
    return {
        "work_id": work_id,
        "fields": {
            field: {"value": canonical(row[field]), "revision": revisions.get(field, 0)}
            for field in sorted(SYNCED_FIELDS)
        },
    }


def get_revision(conn, work_id, field):
    row = conn.execute(
        "SELECT revision FROM sync_entity_revisions WHERE scope_type = 'work-field' AND scope_id = ?",
        (scope_key(work_id, field),)).fetchone()
    return row[0] if row else 0


def set_field_on_conn(conn, work_id, field, value):
    """Write one field and advance its revision together, in the caller's
    transaction. Returns (changed, revision_after).

    A no-op write advances nothing: a revision is a record of the value
    actually changing, and inflating it would manufacture staleness for every
    device that already holds the current value.
    """
    if field not in SYNCED_FIELDS:
        raise ValueError("unsupported synchronized field: %s" % field)
    desired = canonical(value)
    row = conn.execute("SELECT %s FROM works WHERE id = ?" % field, (work_id,)).fetchone()
    if row is None:
        return False, 0
    revision = get_revision(conn, work_id, field)
    if canonical(row[0]) == desired:
        return False, revision
    conn.execute(
        "UPDATE works SET %s = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?" % field,
        (desired, work_id))
    conn.execute("""INSERT INTO sync_entity_revisions (scope_type, scope_id, revision)
                    VALUES ('work-field', ?, 1) ON CONFLICT (scope_type, scope_id)
                    DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP""",
                 (scope_key(work_id, field),))
    return True, revision + 1


def validate(op):
    payload = op["payload"]
    if set(payload) != {"field", "value"}:
        raise ValueError("INVALID_ENVELOPE")
    field, value = payload["field"], payload["value"]
    # An arbitrary column name from a client would be an injection surface and
    # a way to reach fields this milestone deliberately does not synchronize.
    if not isinstance(field, str) or field not in SYNCED_FIELDS:
        raise ValueError("INVALID_ENVELOPE")
    if not isinstance(value, str) or len(value) > SYNCED_FIELDS[field]:
        raise ValueError("INVALID_ENVELOPE")
    # A scalar edit is optimistic-concurrency controlled: a null base revision
    # is a client that cannot detect a conflict, which would silently overwrite
    # whatever another device wrote.
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def apply(db, conn, op, received_at):
    work_id = op["entity_id"]
    field, desired = op["payload"]["field"], op["payload"]["value"]
    result = {"work_id": work_id, "field": field}
    row = conn.execute("SELECT %s FROM works WHERE id = ?" % field, (work_id,)).fetchone()
    if row is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    current = canonical(row[0])
    revision = get_revision(conn, work_id, field)
    base = op["base_revision"]
    if base > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 400, result
    # A stale base is only a conflict when the two devices actually disagree.
    # Two people typing the same DOI have converged, not collided.
    if base < revision and current != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 409, result
    changed, after = set_field_on_conn(conn, work_id, field, desired)
    result.update(code="ACKNOWLEDGED", value=desired, server_revision=after, changed=changed)
    return 200, result


class _Handler:
    validate = staticmethod(validate)
    apply = staticmethod(apply)


HANDLER = _Handler()
