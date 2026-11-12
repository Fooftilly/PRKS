"""Concepts: construction, definition, identity, hierarchy and destruction.

Five families, and the one non-obvious split is forced by what `update_concept`
already does.

  * `CREATE_CONCEPT` is construction -- a client-minted id, no base revision.
  * `SET_CONCEPT_FIELD` is scalar mutation of `description`, and of nothing
    else. It is the only column a Concept has that is not part of its identity.
  * `SET_CONCEPT_IDENTITY` is an AGGREGATE over `name` AND `aliases`. Renaming a
    Concept is not a field write: when the normalized key changes, the ordinary
    endpoint inserts the OLD name into `concept_aliases`, so a rename mutates
    the alias set. The two also share one uniqueness rule -- `resolve_concept_key`
    matches name-or-alias over a single normalized space -- so they cannot be
    judged apart any more than a Folder's title and parent could.
  * `SET_CONCEPT_PARENTS` is an AGGREGATE. `replace_concept_parents` rewrites
    the whole set and validates acyclicity over the whole graph, which only the
    server can see.
  * `DELETE_CONCEPT` is destruction, carrying no base revision. The protection
    is the ordinary endpoint's: a Concept still named by canonical research
    notes is refused, never cascaded.

Identity is deliberately NOT converged the way a scalar is. Two devices that
gave one Concept two different names have made two claims about what it IS, and
picking one silently would lose a decision.
"""
import json

from backend.domain.research import rules as research_rules
from backend.entity_ids import is_distributed
from backend.domain.research.rules import ResearchError

FIELDS = ("description",)
FIELD_SET = frozenset(FIELDS)

FIELD_SCOPE_TYPE = "concept-field"
IDENTITY_SCOPE_TYPE = "concept-identity"
PARENTS_SCOPE_TYPE = "concept-parents"

MAX_ALIASES = 64
MAX_PARENTS = 64


def scope_key(concept_id, field):
    return json.dumps([concept_id, field], ensure_ascii=True, separators=(",", ":"))


# ---- handlers -------------------------------------------------------------

_RULE_STATUS = {
    "not_found": 404,
    "parent_not_found": 404,
    "concept_exists": 409,
    "ambiguous_concept": 409,
    "alias_conflict": 409,
    "concept_cycle": 409,
}

_RULE_CODES = {
    "not_found": "ENTITY_NOT_FOUND",
    "parent_not_found": "PARENT_NOT_FOUND",
    "concept_exists": "CONCEPT_EXISTS",
    "ambiguous_concept": "AMBIGUOUS_CONCEPT",
    "alias_conflict": "ALIAS_CONFLICT",
    "concept_cycle": "CONCEPT_CYCLE",
}


def validate_create(op):
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")
    if not is_distributed(op["entity_id"], "C"):
        raise ValueError("INVALID_ENVELOPE")
    payload = op["payload"]
    if set(payload) != {"name", "description"}:
        raise ValueError("INVALID_ENVELOPE")
    if not isinstance(payload["name"], str) or not isinstance(payload["description"], str):
        raise ValueError("INVALID_ENVELOPE")
    # Validated HERE rather than left to the domain rule, so a malformed name is
    # refused before the ledger records it as an outcome -- and so it reports
    # INVALID_ENVELOPE instead of being flattened into a domain code that says
    # something else entirely.
    try:
        research_rules._concept_name(payload["name"])
        research_rules._optional_markdown(payload["description"],
                                   max_len=research_rules.CONCEPT_DEFINITION_MAX)
    except ResearchError:
        raise ValueError("INVALID_ENVELOPE") from None


def validate_field(op):
    payload = op["payload"]
    if set(payload) != {"field", "value"}:
        raise ValueError("INVALID_ENVELOPE")
    if payload["field"] not in FIELD_SET or not isinstance(payload["value"], str):
        raise ValueError("INVALID_ENVELOPE")
    if len(payload["value"]) > research_rules.CONCEPT_DEFINITION_MAX:
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def validate_identity(op):
    payload = op["payload"]
    if set(payload) != {"name", "aliases"}:
        raise ValueError("INVALID_ENVELOPE")
    name, aliases = payload["name"], payload["aliases"]
    if not isinstance(name, str) or not isinstance(aliases, list):
        raise ValueError("INVALID_ENVELOPE")
    if len(aliases) > MAX_ALIASES:
        raise ValueError("INVALID_ENVELOPE")
    try:
        research_rules._concept_name(name)
        for alias in aliases:
            if not isinstance(alias, str):
                raise ValueError("INVALID_ENVELOPE")
            research_rules._concept_name(alias)
    except ResearchError:
        raise ValueError("INVALID_ENVELOPE") from None
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def validate_parents(op):
    payload = op["payload"]
    if set(payload) != {"parent_ids"}:
        raise ValueError("INVALID_ENVELOPE")
    parent_ids = payload["parent_ids"]
    if not isinstance(parent_ids, list) or len(parent_ids) > MAX_PARENTS:
        raise ValueError("INVALID_ENVELOPE")
    for pid in parent_ids:
        if not isinstance(pid, str) or not pid.strip() or pid != pid.strip():
            raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def validate_delete(op):
    if op["payload"] != {}:
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")


