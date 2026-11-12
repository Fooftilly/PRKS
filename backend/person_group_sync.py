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


# ---- handlers -------------------------------------------------------------


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


def validate_member(op):
    payload = op["payload"]
    if set(payload) != {"person_id"}:
        raise ValueError("INVALID_ENVELOPE")
    person_id = payload["person_id"]
    if not isinstance(person_id, str) or not person_id.strip():
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def validate_delete(op):
    if op["payload"] != {}:
        raise ValueError("INVALID_ENVELOPE")
    # Destruction addresses an IDENTITY, not a value: absence is idempotent and
    # there is no second state for two devices to disagree about.
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")


