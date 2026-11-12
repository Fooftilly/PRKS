"""Arguments and Stances: construction, scalar fields, two aggregates, destruction.

An Argument is the record that connects everything else: it cites Works and it
answers Positions and other Arguments. Five families:

  * `CREATE_ARGUMENT` is construction -- a client-minted id, no base revision --
    and it carries the argument's INITIAL sources and targets.
  * `SET_ARGUMENT_FIELD` is scalar mutation with a conflict unit of one FIELD,
    over `name`, `kind` and `main_text`.
  * `SET_ARGUMENT_SOURCES` is an AGGREGATE over the whole ordered citation list.
  * `SET_ARGUMENT_TARGETS` is an AGGREGATE over the whole ordered target list.
  * `DELETE_ARGUMENT` is destruction, carrying no base revision.

Why construction carries its connections
----------------------------------------
`create_argument` accepts `sources` and `targets` and applies them inside the
same transaction as the INSERT, and two real flows depend on that: "Create
response" produces an Argument that already answers another one, and creating
an Argument from a Work produces one that already cites it. Decomposing those
into `CREATE_ARGUMENT` followed by `SET_ARGUMENT_TARGETS` would make the two
halves separately refusable -- the creation could be acknowledged and the
connection refused, leaving a standalone Argument nobody asked for, which is
not an outcome the online endpoint can produce.

Why the three columns are three fields
--------------------------------------
`update_argument` writes `name`, `kind` and `main_text` independently, they
share no constraint, and nothing derived changes when `kind` does -- an
Argument and a Stance are stored and projected identically. Joining them would
make editing the body conflict with a rename.

Why targets are ONE aggregate across TWO tables
-----------------------------------------------
`argument_target_positions` and `argument_target_arguments` are physical
storage for a single ordered list the user chose: `_write_targets`
(`backend/domain/research/arguments.py`) receives one mixed list and deletes
from BOTH tables before rewriting either.
Registering a family per table would mean each replacement silently discarded
the other table's half of that one decision, and the acyclicity rule spans
both. Order and verdict are part of the value, not decoration, so the whole
list is one judgement.
"""
import json

from backend.domain.research import rules as research_rules
from backend.entity_ids import is_distributed
from backend.domain.research.rules import ResearchError

FIELDS = ("name", "kind", "main_text")
FIELD_SET = frozenset(FIELDS)

FIELD_SCOPE_TYPE = "argument-field"
SOURCES_SCOPE_TYPE = "argument-sources"
TARGETS_SCOPE_TYPE = "argument-targets"

TARGET_TYPES = ("position", "argument")

# Hand-curated lists, not machine output. The bound exists so one malformed
# client cannot ask the server to rewrite an unbounded number of rows inside a
# single operation, and so an echoed aggregate stays inside the durable result.
MAX_SOURCES = 200
MAX_TARGETS = 200


def scope_key(argument_id, field):
    return json.dumps([argument_id, field], ensure_ascii=True, separators=(",", ":"))


def canonical_wire(field, value):
    """The normalization both paths apply before comparing.

    Without one shared answer a device would keep disagreeing with itself: it
    would send `"  A  "`, read back `"A"`, and see a change it had not made.
    """
    if field == "name":
        return research_rules._plain_name(value, max_len=research_rules.ARGUMENT_NAME_MAX)
    if field == "kind":
        return research_rules.validate_kind(value)
    return research_rules._optional_markdown(value, max_len=research_rules.ARGUMENT_TEXT_MAX)


# ---- current canonical values ---------------------------------------------


# ---- mutation primitives ---------------------------------------------------


# ---- refusal vocabulary ----------------------------------------------------

_RULE_STATUS = {
    "not_found": 404,
    "work_not_found": 404,
    "position_not_found": 404,
    "argument_not_found": 404,
    "argument_cycle": 409,
    "argument_in_use": 409,
    "argument_targeted": 409,
}

# Every refusal keeps its own name. Flattening these into one generic error
# would leave Diagnostics unable to say whether a target was missing, unknown
# or circular -- three different things for the user to do about it.
_RULE_CODES = {
    "not_found": "ENTITY_NOT_FOUND",
    "work_not_found": "WORK_NOT_FOUND",
    "position_not_found": "POSITION_NOT_FOUND",
    "argument_not_found": "TARGET_NOT_FOUND",
    "invalid_verdict": "INVALID_VERDICT",
    "argument_cycle": "ARGUMENT_CYCLE",
    "argument_in_use": "ARGUMENT_IN_USE",
    "argument_targeted": "ARGUMENT_TARGETED",
}


# ---- envelope validation ---------------------------------------------------

def _check_sources_shape(sources):
    """Everything about a citation list a client can check for itself.

    Whatever needs the database -- does this Work exist -- is a named refusal
    instead, because a client offline cannot know it.
    """
    if not isinstance(sources, list) or len(sources) > MAX_SOURCES:
        raise ValueError("INVALID_ENVELOPE")
    seen = set()
    for raw in sources:
        if not isinstance(raw, dict) or set(raw) != {"work_id", "pages"}:
            raise ValueError("INVALID_ENVELOPE")
        wid, pages = raw["work_id"], raw["pages"]
        if not isinstance(wid, str) or not wid.strip() or wid != wid.strip():
            raise ValueError("INVALID_ENVELOPE")
        if wid in seen:
            raise ValueError("INVALID_ENVELOPE")
        seen.add(wid)
        if not isinstance(pages, str):
            raise ValueError("INVALID_ENVELOPE")
        try:
            research_rules._pages(pages)
        except ResearchError:
            raise ValueError("INVALID_ENVELOPE") from None


def _check_targets_shape(targets):
    """Everything about a target list a client can check for itself.

    Existence, verdict vocabulary and acyclicity all need the server, so they
    stay named refusals rather than envelope rejections.
    """
    if not isinstance(targets, list) or len(targets) > MAX_TARGETS:
        raise ValueError("INVALID_ENVELOPE")
    seen = set()
    for raw in targets:
        if not isinstance(raw, dict) or set(raw) != {"type", "id", "verdict_id"}:
            raise ValueError("INVALID_ENVELOPE")
        ttype, tid, verdict = raw["type"], raw["id"], raw["verdict_id"]
        if ttype not in TARGET_TYPES:
            raise ValueError("INVALID_ENVELOPE")
        if not isinstance(tid, str) or not tid.strip() or tid != tid.strip():
            raise ValueError("INVALID_ENVELOPE")
        if (ttype, tid) in seen:
            raise ValueError("INVALID_ENVELOPE")
        seen.add((ttype, tid))
        if not isinstance(verdict, str) or not verdict.strip() or verdict != verdict.strip():
            raise ValueError("INVALID_ENVELOPE")


# ---- handlers --------------------------------------------------------------


def validate_create(op):
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")
    if not is_distributed(op["entity_id"], "A"):
        raise ValueError("INVALID_ENVELOPE")
    payload = op["payload"]
    if set(payload) != {"name", "kind", "main_text", "sources", "targets"}:
        raise ValueError("INVALID_ENVELOPE")
    for field in FIELDS:
        if not isinstance(payload[field], str):
            raise ValueError("INVALID_ENVELOPE")
        try:
            canonical_wire(field, payload[field])
        except ResearchError:
            raise ValueError("INVALID_ENVELOPE") from None
    _check_sources_shape(payload["sources"])
    _check_targets_shape(payload["targets"])


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


def validate_sources(op):
    payload = op["payload"]
    if set(payload) != {"sources"}:
        raise ValueError("INVALID_ENVELOPE")
    _check_sources_shape(payload["sources"])
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def validate_targets(op):
    payload = op["payload"]
    if set(payload) != {"targets"}:
        raise ValueError("INVALID_ENVELOPE")
    _check_targets_shape(payload["targets"])
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def validate_delete(op):
    if op["payload"] != {}:
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")


