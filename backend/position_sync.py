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

from backend.domain.research import rules as research_rules
from backend.entity_ids import is_distributed
from backend.domain.research.rules import ResearchError

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
        return research_rules._plain_name(value, max_len=research_rules.POSITION_NAME_MAX)
    return research_rules._optional_markdown(value, max_len=research_rules.POSITION_DESCRIPTION_MAX)


# ---- handlers -------------------------------------------------------------


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


def validate_delete(op):
    if op["payload"] != {}:
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")


