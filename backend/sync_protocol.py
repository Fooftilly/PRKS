"""The semantic-operation envelope every sync family shares, and the operations PRKS accepts.

``backend.sync.protocol`` runs the protocol on PostgreSQL: it normalizes each
envelope here, hashes it here, and dispatches to the family handlers in
``backend.sync.handlers``, which may only implement an operation listed here.
Domain rules (which payloads are legal, what a revision means) live in the
per-family rule modules (``backend/*_sync.py``).
"""
import hashlib
import json
import re
from datetime import datetime, timezone

UUID = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
FIELDS = frozenset(("op_id", "device_id", "operation", "entity_type", "entity_id",
                    "payload", "base_revision", "occurred_at", "created_at", "depends_on"))
MAX_REVISION = 9007199254740991
MAX_ID_CHARS = 200
MAX_DEPENDENCIES = 16
ACKNOWLEDGED = "ACKNOWLEDGED"

_ENTITY_TYPES = {}


def register(operation, *, entity_type="work"):
    """Accept one operation name, with the entity type its envelope must name.

    ``entity_type`` is registered with the operation because prefixes are not
    unique across record families (Persons and Positions both use ``P-``).
    """
    if operation in _ENTITY_TYPES:
        raise RuntimeError("duplicate sync operation: " + operation)
    if not isinstance(entity_type, str) or not entity_type.strip():
        raise RuntimeError("sync operation entity_type is required")
    _ENTITY_TYPES[operation] = entity_type


def supported_operations():
    return frozenset(_ENTITY_TYPES)


def normalize_envelope(data):
    """Validate and canonicalize the fields every operation family shares.

    Normalization is what the request hash is taken over, so two spellings of
    the same operation (key order, `Z` vs `+00:00`, upper-case UUIDs) must
    reduce to identical bytes or a retry after a lost response would read as a
    different operation and be refused as OP_ID_REUSE.
    """
    if not isinstance(data, dict) or set(data) != FIELDS:
        raise ValueError("INVALID_ENVELOPE")
    out = dict(data)
    for field in ("op_id", "device_id"):
        if not isinstance(data[field], str) or not UUID.fullmatch(data[field]):
            raise ValueError("INVALID_ENVELOPE")
        out[field] = data[field].lower()
    if not isinstance(data["operation"], str) or data["operation"] not in _ENTITY_TYPES:
        raise ValueError("INVALID_ENVELOPE")
    if data["entity_type"] != _ENTITY_TYPES[data["operation"]]:
        raise ValueError("INVALID_ENVELOPE")
    if not isinstance(data["payload"], dict):
        raise ValueError("INVALID_ENVELOPE")
    value = data["entity_id"]
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > MAX_ID_CHARS:
        raise ValueError("INVALID_ENVELOPE")
    # Families that need no optimistic concurrency send null; families that do
    # send a counter. Which one is legal is the handler's decision, not this
    # layer's -- both spellings are structurally valid here.
    revision = data["base_revision"]
    if revision is not None and (type(revision) is not int or not 0 <= revision <= MAX_REVISION):
        raise ValueError("INVALID_BASE_REVISION")
    out["depends_on"] = normalize_dependencies(data["depends_on"], out["op_id"])
    for field in ("occurred_at", "created_at"):
        out[field] = normalize_timestamp(data[field])
    return out


def normalize_timestamp(value):
    """A bounded, timezone-aware ISO-8601 instant, canonicalized to UTC."""
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("INVALID_ENVELOPE")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError()
        return timestamp.astimezone(timezone.utc).isoformat()
    except ValueError:
        raise ValueError("INVALID_ENVELOPE") from None


def normalize_dependencies(value, op_id):
    """Same-device prerequisite op ids; empty is the common case.

    Self-dependency, duplicates and non-UUIDs are envelope errors. Whether
    those ops have actually been acknowledged is checked after the ledger
    lookup, because a retry of a well-formed dependent must not burn a new
    op_id merely because it arrived a moment too soon.
    """
    if not isinstance(value, list) or len(value) > MAX_DEPENDENCIES:
        raise ValueError("INVALID_ENVELOPE")
    seen = []
    for item in value:
        if not isinstance(item, str) or not UUID.fullmatch(item):
            raise ValueError("INVALID_ENVELOPE")
        canon = item.lower()
        if canon == op_id or canon in seen:
            raise ValueError("INVALID_ENVELOPE")
        seen.append(canon)
    return seen




def request_hash(op):
    return hashlib.sha256(json.dumps(op, sort_keys=True, separators=(",", ":")).encode()).hexdigest()






# Registration is explicit and lives here so the set of operations PRKS
# accepts is readable in one place.

# The Tag VOCABULARY, as opposed to the Work-Tag relationship below. Two
# shapes only: PRKS has no rename and no colour editor, so a field family would
# be inventing product semantics rather than moving existing ones off the wire.
register("CREATE_TAG", entity_type="tag")
register("DELETE_TAG", entity_type="tag")
register("MERGE_TAG", entity_type="tag")
register("ADD_WORK_TAG")
register("REMOVE_WORK_TAG")
register("ADD_FOLDER_TAG", entity_type="folder")
register("REMOVE_FOLDER_TAG", entity_type="folder")
register("MARK_WORK_OPENED")
register("SET_WORK_METADATA_FIELD")
# Source identity is an AGGREGATE, not a field: see work_source_sync's module
# docstring for why three field-scoped operations would be the wrong unit.
register("SET_WORK_SOURCE")
register("CREATE_WORK")
register("DELETE_WORK")
# Work-Person roles are ELEMENTS, not an ordered aggregate: see
# work_role_sync's module docstring for the schema evidence behind that.
register("ADD_WORK_PERSON_ROLE")
register("REMOVE_WORK_PERSON_ROLE")
# Editing the credit override on an existing link shares the relationship's
# scope and revision: it changes the same element's semantic state.
register("SET_WORK_PERSON_ROLE_CREDIT")
register("CREATE_PERSON", entity_type="person")
# Destruction, like the Group family's: it addresses an identity rather than a
# value, so it carries no base revision. The protection is the ordinary
# endpoint's -- a Person credited on a file is refused, never cascaded.
register("DELETE_PERSON", entity_type="person")
# Editing a Person is FIELD-scoped, not profile-scoped: see
# person_metadata_sync's module docstring for the schema evidence behind that.
register("SET_PERSON_METADATA_FIELD", entity_type="person")
# Person Groups: four shapes over one entity. Construction mints the id, the
# three editable columns are independent FIELDS, membership is an element of a
# SET, and deletion addresses the identity and so carries no base revision.
register("CREATE_PERSON_GROUP", entity_type="person-group")
register("SET_PERSON_GROUP_FIELD", entity_type="person-group")
register("ADD_PERSON_GROUP_MEMBER", entity_type="person-group")
register("REMOVE_PERSON_GROUP_MEMBER", entity_type="person-group")
register("DELETE_PERSON_GROUP", entity_type="person-group")
# Folders. Moving one is a FIELD, because the hierarchy is a parent pointer on
# one row -- and which folder a Work is in is a field on the WORK, because a
# Work is in at most one folder rather than a set of them.
register("CREATE_FOLDER", entity_type="folder")
register("SET_FOLDER_FIELD", entity_type="folder")
register("DELETE_FOLDER", entity_type="folder")
register("SET_WORK_FOLDER")
# Playlists. The three editable columns are FIELDS; which playlist a Work is in
# is a field on the WORK, because a Work is in at most one playlist; and the
# ORDER is an aggregate under one revision -- two devices that each dragged one
# video produced two whole orders, and merging them index by index would invent
# a third neither of them chose.
register("CREATE_PLAYLIST", entity_type="playlist")
register("SET_PLAYLIST_FIELD", entity_type="playlist")
register("REORDER_PLAYLIST_ITEMS", entity_type="playlist")
register("DELETE_PLAYLIST", entity_type="playlist")
register("SET_WORK_PLAYLIST")
# Concepts. `description` is the only column that is not part of a Concept's
# identity, so it is the only FIELD. Renaming writes an alias -- every note that
# already says the old name must go on resolving -- so `name` and `aliases` are
# ONE aggregate, and the hierarchy is another whose acyclicity only the server
# can see.
register("CREATE_CONCEPT", entity_type="concept")
register("SET_CONCEPT_FIELD", entity_type="concept")
register("SET_CONCEPT_IDENTITY", entity_type="concept")
register("SET_CONCEPT_PARENTS", entity_type="concept")
register("DELETE_CONCEPT", entity_type="concept")
# Positions. Deliberately the smallest domain: a claim record with two
# INDEPENDENT scalar fields. `positions.name` carries no UNIQUE constraint and
# renaming one writes nothing else, so nothing forces `name` and `description`
# into one judgement -- and joining them would make an unrelated description
# edit conflict with a rename.
register("CREATE_POSITION", entity_type="position")
register("SET_POSITION_FIELD", entity_type="position")
register("DELETE_POSITION", entity_type="position")
# Arguments and Stances. Construction carries the argument's INITIAL sources and
# targets because `create_argument` applies them in one transaction: "Create
# response" and "Create from Work" both produce an already-connected record, and
# splitting that would let the creation be acknowledged while its connection was
# refused. The three columns are independent FIELDS. Sources are one ordered
# AGGREGATE, and so are targets -- and targets are ONE aggregate across two
# tables, because `_write_targets` (backend/domain/research/arguments.py) takes a
# single mixed list and deletes from both tables, and acyclicity spans both.
register("CREATE_ARGUMENT", entity_type="argument")
register("SET_ARGUMENT_FIELD", entity_type="argument")
register("SET_ARGUMENT_SOURCES", entity_type="argument")
register("SET_ARGUMENT_TARGETS", entity_type="argument")
register("DELETE_ARGUMENT", entity_type="argument")
# Work notes. Each whole body is one aggregate and the two columns deliberately
# do not share a revision: Research Notes run semantic markup processing;
# Private Notes never do.
register("SET_WORK_RESEARCH_NOTE")
register("SET_WORK_PRIVATE_NOTE")
# PDF annotations: one revisioned aggregate per annotation under a Work.
# PDF bytes are never in these operations — see pdf_annotation_sync.
register("CREATE_PDF_ANNOTATION")
register("SET_PDF_ANNOTATION")
register("DELETE_PDF_ANNOTATION")
