"""Person Groups: construction, fields, membership and deletion.

Four shapes in one domain, and the split is a reading of the schema rather
than a template:

  * `CREATE_PERSON_GROUP` is construction -- a client-minted id, no base
    revision, and a replay acknowledges the stored row instead of overwriting
    it.
  * `SET_PERSON_GROUP_FIELD` is scalar mutation with a conflict unit of one
    FIELD. `name`, `description` and `parent_id` are independent decisions:
    two devices that renamed a group and reparented it have not disagreed.
  * `ADD_PERSON_GROUP_MEMBER` / `REMOVE_PERSON_GROUP_MEMBER` are relationship
    mutations with a conflict unit of one `(group, person)` pair, exactly like
    Work-Person roles. Membership is a set, not an ordered aggregate: two
    devices adding different people have not collided.
  * `DELETE_PERSON_GROUP` is destruction. It carries NO base revision, because
    it addresses an identity rather than a value: absence is idempotent, and
    there is no second state for two devices to disagree about. A rename that
    arrives after the deletion is told the group is gone and becomes the
    user's decision, which is the honest answer rather than a manufactured
    revision conflict.

Uniqueness of the name and the acyclicity of the hierarchy are CANONICAL. A
client may refuse an obviously colliding name early, but only the server sees
every group, so both rules are enforced here and reported as terminal codes the
user can act on.

Nothing here imports the database layer; the ordinary HTTP endpoints share
these same helpers, so a group can never change without its revision.
"""
import json

from backend import work_metadata_sync as meta
from backend.entity_ids import is_distributed

# The editable columns on `person_groups`. `id`, `created_at` and `updated_at`
# are not user-editable and are absent by construction rather than by filtering.
FIELDS = ("name", "description", "parent_id")
FIELD_SET = frozenset(FIELDS)

FIELD_SCOPE_TYPE = "person-group-field"
MEMBER_SCOPE_TYPE = "person-group-member"

# A group name is a label in a picker, not prose. Bounded so the field has a
# contract of its own rather than inheriting whichever layer refuses first, and
# small enough that a terminal result carrying two of them still fits the
# client's durable bound.
MAX_NAME_BYTES = 200
MAX_DESCRIPTION_BYTES = 2000


def scope_key(group_id, field):
    # Structural encoding, like every other scope in this protocol: no id has
    # to exclude a delimiter for this to stay unambiguous.
    return json.dumps([group_id, field], ensure_ascii=True, separators=(",", ":"))


def member_scope_key(group_id, person_id):
    return json.dumps([group_id, person_id], ensure_ascii=True, separators=(",", ":"))


class GroupRuleError(ValueError):
    """A canonical rule the user can act on, carrying its terminal code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def canonical_wire(field, value):
    """The spelling two devices must agree on.

    A name differing only in surrounding space is the same name -- the
    uniqueness rule is already case-insensitive, so comparing raw input would
    report a conflict between devices that typed the same thing.
    """
    text = "" if value is None else str(value)
    if field == "description":
        return text.strip()
    return text.strip()


def validate_field_value(field, value):
    """The ONE rule every Person-Group field mutation path asks.

    Deliberately the same rule the ordinary PATCH applies, and no more: a
    stricter rule here would be a second validation contract -- a value savable
    by one path and refused by another -- which is what moving a family to
    local-first exists to remove.
    """
    if field not in FIELD_SET:
        raise GroupRuleError("INVALID_ENVELOPE", "unsupported field: %s" % field)
    if not isinstance(value, str):
        raise GroupRuleError("INVALID_ENVELOPE", "field values are strings")
    text = canonical_wire(field, value)
    if field == "name":
        if not text:
            raise GroupRuleError("INVALID_ENVELOPE", "Group name is required.")
        if len(text.encode("utf-8")) > MAX_NAME_BYTES:
            raise GroupRuleError("INVALID_ENVELOPE",
                                 "Group name exceeds %d bytes." % MAX_NAME_BYTES)
    if field == "description" and len(text.encode("utf-8")) > MAX_DESCRIPTION_BYTES:
        raise GroupRuleError("INVALID_ENVELOPE",
                             "Group description exceeds %d bytes." % MAX_DESCRIPTION_BYTES)
    return text


def get_revision(conn, group_id, field):
    row = conn.execute(
        "SELECT revision FROM sync_entity_revisions WHERE scope_type = ? AND scope_id = ?",
        (FIELD_SCOPE_TYPE, scope_key(group_id, field))).fetchone()
    return row[0] if row else 0


def get_member_revision(conn, group_id, person_id):
    row = conn.execute(
        "SELECT revision FROM sync_entity_revisions WHERE scope_type = ? AND scope_id = ?",
        (MEMBER_SCOPE_TYPE, member_scope_key(group_id, person_id))).fetchone()
    return row[0] if row else 0


def _advance(conn, scope_type, scope_id):
    conn.execute(
        """INSERT INTO sync_entity_revisions (scope_type, scope_id, revision)
           VALUES (?, ?, 1) ON CONFLICT (scope_type, scope_id)
           DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP""",
        (scope_type, scope_id))


def current_field(conn, group_id, field):
    row = conn.execute(
        "SELECT %s FROM person_groups WHERE id = ?" % field, (group_id,)).fetchone()
    if row is None:
        return None
    return "" if row[0] is None else str(row[0])


def assert_name_free(conn, name, exclude_group_id=None):
    row = conn.execute(
        "SELECT id FROM person_groups WHERE LOWER(name) = LOWER(?) LIMIT 1",
        (name,)).fetchone()
    if row is not None and row[0] != exclude_group_id:
        raise GroupRuleError("NAME_TAKEN", "A group with this name already exists.")


def descendants(conn, group_id):
    return {row[0] for row in conn.execute(
        """WITH RECURSIVE sub(id) AS (
               SELECT id FROM person_groups WHERE parent_id = ?
               UNION SELECT g.id FROM person_groups g JOIN sub ON g.parent_id = sub.id
           ) SELECT id FROM sub""", (group_id,))}


def assert_parent_usable(conn, group_id, parent_id):
    """Acyclicity is canonical, and only the server sees every group.

    A client may refuse an obviously local cycle early -- it is a better error,
    sooner -- but it holds a partial hierarchy, so its answer can never be the
    authoritative one.
    """
    if not parent_id:
        return
    if parent_id == group_id:
        raise GroupRuleError("PARENT_CYCLE", "A group cannot be its own parent.")
    if conn.execute("SELECT 1 FROM person_groups WHERE id = ?", (parent_id,)).fetchone() is None:
        raise GroupRuleError("PARENT_NOT_FOUND", "Parent group not found.")
    if parent_id in descendants(conn, group_id):
        raise GroupRuleError("PARENT_CYCLE", "Cannot set parent to a subgroup (cycle).")


def set_field_on_conn(conn, group_id, field, value):
    """Write one group field and advance its revision together, in the
    caller's transaction. Returns (changed, revision_after).

    A no-op write advances nothing: a revision records the value actually
    changing, and inflating it would manufacture staleness for every device
    that already holds the current value.
    """
    desired = validate_field_value(field, value)
    current = current_field(conn, group_id, field)
    if current is None:
        raise GroupRuleError("ENTITY_NOT_FOUND", "Group not found.")
    revision = get_revision(conn, group_id, field)
    if current == desired:
        return False, revision
    if field == "name":
        assert_name_free(conn, desired, exclude_group_id=group_id)
    if field == "parent_id":
        assert_parent_usable(conn, group_id, desired)
    # `parent_id` is a nullable foreign key: "no parent" is NULL in the column
    # and "" on the wire, and the two must not both become storable states.
    stored = (desired or None) if field == "parent_id" else desired
    conn.execute(
        "UPDATE person_groups SET %s = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?" % field,
        (stored, group_id))
    _advance(conn, FIELD_SCOPE_TYPE, scope_key(group_id, field))
    return True, revision + 1


def insert_group_on_conn(conn, group_id, name, parent_id="", description=""):
    """CONSTRUCTION. Deliberately not three `set_field_on_conn` calls.

    A group created with a name and a parent has not "changed" twice, and
    manufacturing revision 1 for each field would make every device's first
    read look like a missed change it has to reconcile.
    """
    clean_name = validate_field_value("name", name)
    clean_parent = validate_field_value("parent_id", parent_id)
    clean_description = validate_field_value("description", description)
    assert_name_free(conn, clean_name)
    if clean_parent and conn.execute(
            "SELECT 1 FROM person_groups WHERE id = ?", (clean_parent,)).fetchone() is None:
        raise GroupRuleError("PARENT_NOT_FOUND", "Parent group not found.")
    conn.execute(
        "INSERT INTO person_groups (id, name, parent_id, description) VALUES (?, ?, ?, ?)",
        (group_id, clean_name, clean_parent or None, clean_description))
    return group_id


def is_member(conn, group_id, person_id):
    return conn.execute(
        "SELECT 1 FROM person_group_members WHERE group_id = ? AND person_id = ?",
        (group_id, person_id)).fetchone() is not None


def set_member_on_conn(conn, group_id, person_id, present):
    """ONE boundary for the handler and for the ordinary HTTP endpoints.

    The revision survives the relationship: a removal has to leave a record, or
    an offline "remove" replayed after a remote "add" would look like it was
    based on the current state.
    """
    existing = is_member(conn, group_id, person_id)
    if existing == bool(present):
        return False
    if present:
        conn.execute(
            "INSERT OR IGNORE INTO person_group_members (person_id, group_id) VALUES (?, ?)",
            (person_id, group_id))
    else:
        conn.execute(
            "DELETE FROM person_group_members WHERE person_id = ? AND group_id = ?",
            (person_id, group_id))
    _advance(conn, MEMBER_SCOPE_TYPE, member_scope_key(group_id, person_id))
    # Group chips are embedded in the People index rows and in the Person detail.
    conn.execute("UPDATE persons SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                 (person_id,))
    conn.execute("UPDATE person_groups SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                 (group_id,))
    return True


def delete_group_on_conn(conn, group_id):
    """Remove the group, reparenting its children to its own parent.

    The reparenting is a canonical change to OTHER groups, so their
    `parent_id` revisions advance with it. Without that, a device holding a
    child's old parent would have no way to discover it had been overtaken --
    and its next reparent would overwrite a decision it never saw.
    """
    row = conn.execute(
        "SELECT parent_id FROM person_groups WHERE id = ?", (group_id,)).fetchone()
    if row is None:
        return False
    inherited = row[0]
    children = [r[0] for r in conn.execute(
        "SELECT id FROM person_groups WHERE parent_id = ?", (group_id,))]
    for child in children:
        conn.execute(
            "UPDATE person_groups SET parent_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (inherited, child))
        _advance(conn, FIELD_SCOPE_TYPE, scope_key(child, "parent_id"))
    members = [r[0] for r in conn.execute(
        "SELECT person_id FROM person_group_members WHERE group_id = ?", (group_id,))]
    conn.execute("DELETE FROM person_group_members WHERE group_id = ?", (group_id,))
    for person_id in members:
        conn.execute("UPDATE persons SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (person_id,))
    conn.execute("DELETE FROM person_groups WHERE id = ?", (group_id,))
    return True


def get_group_state_on_conn(conn, group_id):
    """Synchronization bookkeeping for one group: revisions only.

    The catalogue already carries every group's name, parent and description,
    and the group detail carries its members, so echoing values here would make
    a second cached copy of both. A scope with no row has never changed since
    revisions existed, which is revision 0 -- the same "missing means zero"
    rule every other scope uses.

    Membership tombstones are included: a scope with a revision and no live
    relationship is exactly the state an offline device needs in order to know
    that its own pending removal is current rather than stale.
    """
    if conn.execute("SELECT 1 FROM person_groups WHERE id = ?", (group_id,)).fetchone() is None:
        return None
    by_key = {scope_key(group_id, field): field for field in FIELDS}
    placeholders = ", ".join("?" * len(by_key))
    revisions = {
        by_key[scope_id]: revision
        for scope_id, revision in conn.execute(
            "SELECT scope_id, revision FROM sync_entity_revisions "
            "WHERE scope_type = ? AND scope_id IN (%s)" % placeholders,
            (FIELD_SCOPE_TYPE,) + tuple(by_key)).fetchall()
    }
    present = {r[0] for r in conn.execute(
        "SELECT person_id FROM person_group_members WHERE group_id = ?", (group_id,))}
    members = []
    seen = set()
    for scope_id, revision in conn.execute(
            "SELECT scope_id, revision FROM sync_entity_revisions WHERE scope_type = ?",
            (MEMBER_SCOPE_TYPE,)).fetchall():
        try:
            scoped_group, person_id = json.loads(scope_id)
        except (ValueError, TypeError):
            continue
        if scoped_group != group_id:
            continue
        seen.add(person_id)
        members.append({"person_id": person_id, "revision": revision,
                        "present": person_id in present})
    for person_id in sorted(present - seen):
        members.append({"person_id": person_id, "revision": 0, "present": True})
    members.sort(key=lambda m: m["person_id"])
    return {
        "group_id": group_id,
        "fields": {field: {"revision": revisions.get(field, 0)} for field in sorted(FIELDS)},
        "members": members,
    }


def get_person_group_state_on_conn(conn, person_id):
    """The same bookkeeping, keyed by PERSON.

    Membership is edited from both ends -- the Group's member list and the
    Person's own group chips -- and each end needs the revisions for the pairs
    it can change. A Person is offered every group in the picker, so this
    carries the tombstones too: a pair the device once removed has a revision,
    and adding it back is a mutation of that scope rather than a first write.
    """
    if conn.execute("SELECT 1 FROM persons WHERE id = ?", (person_id,)).fetchone() is None:
        return None
    present = {r[0] for r in conn.execute(
        "SELECT group_id FROM person_group_members WHERE person_id = ?", (person_id,))}
    groups = []
    seen = set()
    for scope_id, revision in conn.execute(
            "SELECT scope_id, revision FROM sync_entity_revisions WHERE scope_type = ?",
            (MEMBER_SCOPE_TYPE,)).fetchall():
        try:
            group_id, scoped_person = json.loads(scope_id)
        except (ValueError, TypeError):
            continue
        if scoped_person != person_id:
            continue
        seen.add(group_id)
        groups.append({"group_id": group_id, "revision": revision,
                       "present": group_id in present})
    for group_id in sorted(present - seen):
        groups.append({"group_id": group_id, "revision": 0, "present": True})
    groups.sort(key=lambda g: g["group_id"])
    return {"person_id": person_id, "groups": groups}


# ---- handlers -------------------------------------------------------------

def _rule_status(code):
    return 404 if code in ("ENTITY_NOT_FOUND", "PARENT_NOT_FOUND", "PERSON_NOT_FOUND") else 409


def validate_create(op):
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")
    if not is_distributed(op["entity_id"], "PG"):
        raise ValueError("INVALID_ENVELOPE")
    payload = op["payload"]
    if set(payload) != FIELD_SET:
        raise ValueError("INVALID_ENVELOPE")
    for field in FIELDS:
        try:
            validate_field_value(field, payload[field])
        except GroupRuleError:
            raise ValueError("INVALID_ENVELOPE") from None


def apply_create(db, conn, op, received_at):
    group_id = op["entity_id"]
    payload = op["payload"]
    existing = conn.execute(
        "SELECT 1 FROM person_groups WHERE id = ?", (group_id,)).fetchone()
    if existing is None:
        try:
            insert_group_on_conn(conn, group_id, payload["name"],
                                 payload["parent_id"], payload["description"])
        except GroupRuleError as error:
            return _rule_status(error.code), {"code": error.code, "group_id": group_id,
                                              "message": str(error)}
    row = conn.execute("SELECT * FROM person_groups WHERE id = ?", (group_id,)).fetchone()
    group = dict(row)
    group["member_count"] = 0 if existing is None else conn.execute(
        "SELECT COUNT(*) FROM person_group_members WHERE group_id = ?",
        (group_id,)).fetchone()[0]
    group["child_count"] = conn.execute(
        "SELECT COUNT(*) FROM person_groups WHERE parent_id = ?", (group_id,)).fetchone()[0]
    return 200, {"code": "ACKNOWLEDGED", "group_id": group_id,
                 "changed": existing is None, "group": group}


def validate_field(op):
    payload = op["payload"]
    if set(payload) != {"field", "value"}:
        raise ValueError("INVALID_ENVELOPE")
    field, value = payload["field"], payload["value"]
    if not isinstance(field, str) or field not in FIELD_SET:
        raise ValueError("INVALID_ENVELOPE")
    try:
        validate_field_value(field, value)
    except GroupRuleError:
        raise ValueError("INVALID_ENVELOPE") from None
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def apply_field(db, conn, op, received_at):
    group_id = op["entity_id"]
    field, desired = op["payload"]["field"], canonical_wire(op["payload"]["field"],
                                                            op["payload"]["value"])
    result = {"group_id": group_id, "field": field}
    current = current_field(conn, group_id, field)
    if current is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    revision = get_revision(conn, group_id, field)
    base = op["base_revision"]
    if base > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 400, meta.fit_terminal_result(result, current, desired)
    # A stale base is only a conflict when the two devices actually disagree.
    if base < revision and current != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 409, meta.fit_terminal_result(result, current, desired)
    try:
        changed, after = set_field_on_conn(conn, group_id, field, desired)
    except GroupRuleError as error:
        result.update(code=error.code, current_revision=revision, message=str(error))
        return _rule_status(error.code), result
    # A description can be 2 KiB on its own, so the acknowledgement does not
    # echo the value: a result the client cannot durably store is read as a
    # failed sync and retried forever, and the client already holds the
    # authoritative value in its own immutable payload.
    result.update(code="ACKNOWLEDGED", server_revision=after, changed=changed,
                  value_omitted=True)
    return 200, result


def validate_member(op):
    payload = op["payload"]
    if set(payload) != {"person_id"}:
        raise ValueError("INVALID_ENVELOPE")
    person_id = payload["person_id"]
    if not isinstance(person_id, str) or not person_id.strip():
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def apply_member(db, conn, op, received_at):
    group_id = op["entity_id"]
    person_id = op["payload"]["person_id"]
    desired = op["operation"] == "ADD_PERSON_GROUP_MEMBER"
    result = {"group_id": group_id, "person_id": person_id}
    if conn.execute("SELECT 1 FROM person_groups WHERE id = ?", (group_id,)).fetchone() is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    if conn.execute("SELECT 1 FROM persons WHERE id = ?", (person_id,)).fetchone() is None:
        # Person creation is not part of this family; the coordinator's
        # dependency ordering is what keeps a pending creation from arriving
        # here first, so reaching this line means the Person is genuinely gone.
        result["code"] = "PERSON_NOT_FOUND"
        return 404, result
    revision = get_member_revision(conn, group_id, person_id)
    existing = is_member(conn, group_id, person_id)
    base = op["base_revision"]
    if base > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_present=existing, requested_present=desired)
        return 400, result
    # Adding someone already in the group, or removing someone already out of
    # it, is CONVERGENCE and is acknowledged however many revisions apart the
    # two devices started.
    if base < revision and existing != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_present=existing, requested_present=desired)
        return 409, result
    changed = set_member_on_conn(conn, group_id, person_id, desired)
    result.update(code="ACKNOWLEDGED", changed=changed, present=desired,
                  server_revision=revision + int(changed))
    return 200, result


def validate_delete(op):
    if op["payload"] != {}:
        raise ValueError("INVALID_ENVELOPE")
    # Destruction addresses an IDENTITY, not a value: absence is idempotent and
    # there is no second state for two devices to disagree about.
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")


def apply_delete(db, conn, op, received_at):
    group_id = op["entity_id"]
    # Deleting a group that is already gone is CONVERGENCE, not an error: the
    # user asked for its absence and it is absent.
    changed = delete_group_on_conn(conn, group_id)
    return 200, {"code": "ACKNOWLEDGED", "group_id": group_id, "changed": changed}


def _handler(validate_fn, apply_fn):
    return type("_Handler", (), {"validate": staticmethod(validate_fn),
                                 "apply": staticmethod(apply_fn)})()


CREATE_HANDLER = _handler(validate_create, apply_create)
FIELD_HANDLER = _handler(validate_field, apply_field)
MEMBER_HANDLER = _handler(validate_member, apply_member)
DELETE_HANDLER = _handler(validate_delete, apply_delete)
