"""Positions: construction, scalar fields and destruction.

Three shapes, and this domain is deliberately the smallest one yet. A Position
is a claim record: a `name` and a `description`, and nothing else.

  * `CREATE_POSITION` is construction -- a client-minted id, no base revision.
  * `SET_POSITION_FIELD` is scalar mutation with a conflict unit of one FIELD.
  * `DELETE_POSITION` is destruction, carrying no base revision.

`name` and `description` are INDEPENDENT fields, not an aggregate. That is a
reading of the schema rather than a default: unlike a Concept, a Position's name
is subject to no uniqueness rule (`positions.name` has no UNIQUE constraint and
`create_position` performs no lookup), renaming one writes nothing else, and
`update_position` already applies the two columns independently. There is no
second value whose meaning changes when the name does, so nothing forces them
into one judgement -- and joining them would make an unrelated description edit
conflict with a rename.

Ids are permanent and distributed, so a Position created offline can be the
target of an Argument before any server has heard of either.
"""
import json

from backend import research_network as network
from backend.entity_ids import is_distributed
from backend.research_network import ResearchError

FIELDS = ("name", "description")
FIELD_SET = frozenset(FIELDS)

FIELD_SCOPE_TYPE = "position-field"


def scope_key(position_id, field):
    return json.dumps([position_id, field], ensure_ascii=True, separators=(",", ":"))


def canonical_wire(field, value):
    """The normalization both paths apply before comparing.

    Without one shared answer a device would keep disagreeing with itself: it
    would send `"  A  "`, read back `"A"`, and see a change it had not made.
    """
    if field == "name":
        return network._plain_name(value, max_len=network.POSITION_NAME_MAX)
    return network._optional_markdown(value, max_len=network.POSITION_DESCRIPTION_MAX)


def _revision(conn, scope_type, scope_id):
    row = conn.execute(
        "SELECT revision FROM sync_entity_revisions WHERE scope_type = ? AND scope_id = ?",
        (scope_type, scope_id)).fetchone()
    return row[0] if row else 0


def get_revision(conn, position_id, field):
    return _revision(conn, FIELD_SCOPE_TYPE, scope_key(position_id, field))


def _advance(conn, scope_type, scope_id):
    conn.execute(
        """INSERT INTO sync_entity_revisions (scope_type, scope_id, revision)
           VALUES (?, ?, 1) ON CONFLICT (scope_type, scope_id)
           DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP""",
        (scope_type, scope_id))


def current_field(conn, position_id, field):
    row = conn.execute(
        "SELECT %s FROM positions WHERE id = ?" % field, (position_id,)).fetchone()
    if row is None:
        return None
    return "" if row[0] is None else str(row[0])


def set_field_on_conn(conn, position_id, field, value):
    """Write one Position field and advance its revision together."""
    if field not in FIELD_SET:
        raise ResearchError("invalid_field", "Not an editable Position field.")
    desired = canonical_wire(field, value)
    current = current_field(conn, position_id, field)
    if current is None:
        raise ResearchError("not_found", "Position not found.", 404)
    revision = get_revision(conn, position_id, field)
    if current == desired:
        return False, revision
    conn.execute(
        "UPDATE positions SET %s = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?" % field,
        (desired, position_id))
    _advance(conn, FIELD_SCOPE_TYPE, scope_key(position_id, field))
    return True, revision + 1


def insert_position_on_conn(conn, position_id, name, description=""):
    """The one Position construction primitive. Every path goes through it.

    A Position created with a name and a description has not "changed" twice, so
    no revision is advanced here -- construction is not mutation.
    """
    clean_name = canonical_wire("name", name)
    clean_description = canonical_wire("description", description)
    conn.execute("INSERT INTO positions (id, name, description) VALUES (?, ?, ?)",
                 (position_id, clean_name, clean_description))
    return position_id


def delete_position_on_conn(conn, position_id):
    """Remove a Position, or refuse. Returns `(deleted, refusal_code)`.

    The protection is the ordinary endpoint's and is unchanged: a Position an
    Argument still targets cannot be deleted, because deleting it would leave
    that Argument aimed at nothing.
    """
    if conn.execute("SELECT 1 FROM positions WHERE id = ?", (position_id,)).fetchone() is None:
        return False, None
    used = conn.execute(
        "SELECT 1 FROM argument_target_positions WHERE position_id = ? LIMIT 1",
        (position_id,)).fetchone()
    if used:
        return False, "POSITION_IN_USE"
    conn.execute("DELETE FROM positions WHERE id = ?", (position_id,))
    return True, None


def get_position_state_on_conn(conn, position_id):
    """Field revisions for one Position. Revisions only -- the Position detail
    already carries both values."""
    if conn.execute("SELECT 1 FROM positions WHERE id = ?", (position_id,)).fetchone() is None:
        return None
    return {
        "position_id": position_id,
        "fields": {field: {"revision": get_revision(conn, position_id, field)}
                   for field in sorted(FIELDS)},
    }


# ---- handlers -------------------------------------------------------------

def _position_row(conn, position_id):
    row = conn.execute(
        "SELECT id, name, description FROM positions WHERE id = ?", (position_id,)).fetchone()
    return dict(row) if row is not None else None


def validate_create(op):
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")
    if not is_distributed(op["entity_id"], "P"):
        raise ValueError("INVALID_ENVELOPE")
    payload = op["payload"]
    if set(payload) != {"name", "description"}:
        raise ValueError("INVALID_ENVELOPE")
    if not isinstance(payload["name"], str) or not isinstance(payload["description"], str):
        raise ValueError("INVALID_ENVELOPE")
    try:
        canonical_wire("name", payload["name"])
        canonical_wire("description", payload["description"])
    except ResearchError:
        raise ValueError("INVALID_ENVELOPE") from None


def apply_create(db, conn, op, received_at):
    position_id = op["entity_id"]
    payload = op["payload"]
    result = {"position_id": position_id}
    existing = _position_row(conn, position_id)
    if existing is not None:
        # Ids are permanent and distributed, so a duplicate delivery of the
        # same creation is idempotent rather than a refusal.
        result.update(code="ACKNOWLEDGED", changed=False, position=existing)
        return 200, result
    insert_position_on_conn(conn, position_id, payload["name"], payload["description"])
    result.update(code="ACKNOWLEDGED", changed=True, position=_position_row(conn, position_id))
    return 200, result


def validate_field(op):
    payload = op["payload"]
    if set(payload) != {"field", "value"}:
        raise ValueError("INVALID_ENVELOPE")
    field, value = payload["field"], payload["value"]
    if field not in FIELD_SET or not isinstance(value, str):
        raise ValueError("INVALID_ENVELOPE")
    try:
        canonical_wire(field, value)
    except ResearchError:
        raise ValueError("INVALID_ENVELOPE") from None
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def apply_field(db, conn, op, received_at):
    from backend import work_metadata_sync as meta

    position_id = op["entity_id"]
    field = op["payload"]["field"]
    desired = canonical_wire(field, op["payload"]["value"])
    result = {"position_id": position_id, "field": field}
    current = current_field(conn, position_id, field)
    if current is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    revision = get_revision(conn, position_id, field)
    base = op["base_revision"]
    if base > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 400, meta.fit_terminal_result(result, current, desired)
    if base < revision and current != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 409, meta.fit_terminal_result(result, current, desired)
    changed, after = set_field_on_conn(conn, position_id, field, desired)
    result.update(code="ACKNOWLEDGED", server_revision=after, changed=changed,
                  value_omitted=True)
    return 200, result


def validate_delete(op):
    if op["payload"] != {}:
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")


def apply_delete(db, conn, op, received_at):
    position_id = op["entity_id"]
    result = {"position_id": position_id}
    deleted, refusal = delete_position_on_conn(conn, position_id)
    if refusal:
        result["code"] = refusal
        return 409, result
    result.update(code="ACKNOWLEDGED", changed=bool(deleted))
    return 200, result


def _handler(validate_fn, apply_fn):
    return type("_Handler", (), {"validate": staticmethod(validate_fn),
                                 "apply": staticmethod(apply_fn)})()


CREATE_HANDLER = _handler(validate_create, apply_create)
FIELD_HANDLER = _handler(validate_field, apply_field)
DELETE_HANDLER = _handler(validate_delete, apply_delete)
