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
storage for a single ordered list the user chose: `_replace_targets_on_conn`
receives one mixed list and deletes from BOTH tables before rewriting either.
Registering a family per table would mean each replacement silently discarded
the other table's half of that one decision, and the acyclicity rule spans
both. Order and verdict are part of the value, not decoration, so the whole
list is one judgement.
"""
import json

from backend import research_network as network
from backend.entity_ids import is_distributed
from backend.research_network import ResearchError

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
        return network._plain_name(value, max_len=network.ARGUMENT_NAME_MAX)
    if field == "kind":
        return network._validate_kind(value)
    return network._optional_markdown(value, max_len=network.ARGUMENT_TEXT_MAX)


def _revision(conn, scope_type, scope_id):
    row = conn.execute(
        "SELECT revision FROM sync_entity_revisions WHERE scope_type = ? AND scope_id = ?",
        (scope_type, scope_id)).fetchone()
    return row[0] if row else 0


def _advance(conn, scope_type, scope_id):
    conn.execute(
        """INSERT INTO sync_entity_revisions (scope_type, scope_id, revision)
           VALUES (?, ?, 1) ON CONFLICT (scope_type, scope_id)
           DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP""",
        (scope_type, scope_id))


def get_revision(conn, argument_id, field):
    return _revision(conn, FIELD_SCOPE_TYPE, scope_key(argument_id, field))


def get_sources_revision(conn, argument_id):
    return _revision(conn, SOURCES_SCOPE_TYPE, argument_id)


def get_targets_revision(conn, argument_id):
    return _revision(conn, TARGETS_SCOPE_TYPE, argument_id)


def exists(conn, argument_id):
    return conn.execute(
        "SELECT 1 FROM arguments WHERE id = ?", (argument_id,)).fetchone() is not None


# ---- current canonical values ---------------------------------------------

def current_field(conn, argument_id, field):
    row = conn.execute(
        "SELECT %s FROM arguments WHERE id = ?" % field, (argument_id,)).fetchone()
    if row is None:
        return None
    return "" if row[0] is None else str(row[0])


def current_sources(conn, argument_id):
    """The ordered citation list, in the same shape the wire uses."""
    rows = conn.execute(
        """SELECT work_id, pages FROM argument_sources
           WHERE argument_id = ? ORDER BY order_index, work_id""",
        (argument_id,)).fetchall()
    return [{"work_id": r[0], "pages": r[1] or ""} for r in rows]


def current_targets(conn, argument_id):
    """The ordered target list, in the same shape the wire uses.

    One list spanning two tables, ordered by the shared `order_index` the
    ordinary endpoint wrote -- which is what makes this one aggregate rather
    than two.
    """
    rows = conn.execute(
        """SELECT 'position' AS type, position_id AS id, verdict_id, order_index
             FROM argument_target_positions WHERE argument_id = ?
           UNION ALL
           SELECT 'argument' AS type, target_argument_id AS id, verdict_id, order_index
             FROM argument_target_arguments WHERE argument_id = ?
           ORDER BY order_index, type, id""",
        (argument_id, argument_id)).fetchall()
    return [{"type": r[0], "id": r[1], "verdict_id": r[2]} for r in rows]


# ---- mutation primitives ---------------------------------------------------

def set_field_on_conn(conn, argument_id, field, value):
    """Write one Argument field and advance its revision together."""
    if field not in FIELD_SET:
        raise ResearchError("invalid_field", "Not an editable Argument field.")
    desired = canonical_wire(field, value)
    current = current_field(conn, argument_id, field)
    if current is None:
        raise ResearchError("not_found", "Argument not found.", 404)
    revision = get_revision(conn, argument_id, field)
    if current == desired:
        return False, revision
    conn.execute(
        "UPDATE arguments SET %s = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?" % field,
        (desired, argument_id))
    _advance(conn, FIELD_SCOPE_TYPE, scope_key(argument_id, field))
    return True, revision + 1


def set_sources_on_conn(conn, argument_id, sources):
    """Replace the whole citation list and advance its revision together."""
    if not exists(conn, argument_id):
        raise ResearchError("not_found", "Argument not found.", 404)
    revision = get_sources_revision(conn, argument_id)
    before = current_sources(conn, argument_id)
    # Write first, then compare. Short-circuiting an apparently unchanged list
    # would skip the endpoint's validation with it, and a request naming a Work
    # that no longer exists would be accepted for looking equal.
    network._replace_sources_on_conn(conn, argument_id, sources)
    after = current_sources(conn, argument_id)
    if after == before:
        return False, revision
    conn.execute(
        "UPDATE arguments SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (argument_id,))
    _advance(conn, SOURCES_SCOPE_TYPE, argument_id)
    return True, revision + 1


def set_targets_on_conn(conn, argument_id, targets):
    """Replace the whole target list and advance its revision together."""
    if not exists(conn, argument_id):
        raise ResearchError("not_found", "Argument not found.", 404)
    revision = get_targets_revision(conn, argument_id)
    before = current_targets(conn, argument_id)
    network._replace_targets_on_conn(conn, argument_id, targets)
    after = current_targets(conn, argument_id)
    if after == before:
        return False, revision
    conn.execute(
        "UPDATE arguments SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (argument_id,))
    _advance(conn, TARGETS_SCOPE_TYPE, argument_id)
    return True, revision + 1


def insert_argument_on_conn(conn, argument_id, name, kind, main_text="",
                            sources=None, targets=None):
    """The one Argument construction primitive. Every path goes through it.

    Scalar columns, sources and targets are applied together, so a refused
    connection refuses the whole creation rather than leaving a disconnected
    Argument behind. No revision is advanced: an Argument created with three
    citations has not "changed" three times -- construction is not mutation.

    The SAVEPOINT is what makes that true for the DURABLE path specifically. A
    connection can only be validated once its Argument row exists -- the target
    rows have a foreign key to it -- so the INSERT necessarily comes first, and
    the sync handler reports a refusal as a result rather than by raising out of
    the transaction. Without the savepoint that handler would commit the row it
    had already written and hand the user the standalone Argument this
    endpoint has never been able to produce.
    """
    clean_name = canonical_wire("name", name)
    clean_kind = canonical_wire("kind", kind)
    clean_text = canonical_wire("main_text", main_text)
    conn.execute("SAVEPOINT prks_argument_create")
    try:
        conn.execute(
            "INSERT INTO arguments (id, name, kind, main_text) VALUES (?, ?, ?, ?)",
            (argument_id, clean_name, clean_kind, clean_text))
        if sources is not None:
            network._replace_sources_on_conn(conn, argument_id, sources)
        if targets is not None:
            network._replace_targets_on_conn(conn, argument_id, targets)
    except Exception:
        conn.execute("ROLLBACK TO prks_argument_create")
        conn.execute("RELEASE prks_argument_create")
        raise
    conn.execute("RELEASE prks_argument_create")
    return argument_id


def delete_argument_on_conn(conn, argument_id):
    """Remove an Argument, or refuse. Returns `(deleted, refusal_code)`.

    Both protections are the ordinary endpoint's and are unchanged: an
    Argument canonical notes still name cannot be deleted, and neither can one
    another Argument still answers, because either would leave a reference
    pointing at nothing.
    """
    if not exists(conn, argument_id):
        return False, None
    if network._canonical_notes_reference_argument(conn, argument_id):
        return False, "ARGUMENT_IN_USE"
    targeted = conn.execute(
        "SELECT 1 FROM argument_target_arguments WHERE target_argument_id = ? LIMIT 1",
        (argument_id,)).fetchone()
    if targeted:
        return False, "ARGUMENT_TARGETED"
    conn.execute("DELETE FROM arguments WHERE id = ?", (argument_id,))
    return True, None


def get_argument_state_on_conn(conn, argument_id):
    """Revisions for one Argument: three fields and two aggregates.

    Revisions only -- the Argument detail already carries every value.
    """
    if not exists(conn, argument_id):
        return None
    return {
        "argument_id": argument_id,
        "fields": {field: {"revision": get_revision(conn, argument_id, field)}
                   for field in sorted(FIELDS)},
        "sources": {"revision": get_sources_revision(conn, argument_id)},
        "targets": {"revision": get_targets_revision(conn, argument_id)},
    }


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


def _rule_result(error, result):
    code = getattr(error, "code", "")
    result["code"] = _RULE_CODES.get(code, "INVALID_REQUEST")
    return _RULE_STATUS.get(code, 409), result


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
            network._pages(pages)
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

def _argument_row(conn, argument_id):
    row = conn.execute(
        "SELECT id, name, kind, main_text FROM arguments WHERE id = ?",
        (argument_id,)).fetchone()
    return dict(row) if row is not None else None


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


def apply_create(db, conn, op, received_at):
    argument_id = op["entity_id"]
    payload = op["payload"]
    result = {"argument_id": argument_id}
    existing = _argument_row(conn, argument_id)
    if existing is not None:
        # Ids are permanent and distributed, so a duplicate delivery of the
        # same creation is idempotent rather than a refusal.
        result.update(code="ACKNOWLEDGED", changed=False)
        return 200, result
    try:
        insert_argument_on_conn(conn, argument_id, payload["name"], payload["kind"],
                                payload["main_text"], payload["sources"],
                                payload["targets"])
    except ResearchError as error:
        return _rule_result(error, result)
    result.update(code="ACKNOWLEDGED", changed=True)
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

    argument_id = op["entity_id"]
    field = op["payload"]["field"]
    desired = canonical_wire(field, op["payload"]["value"])
    result = {"argument_id": argument_id, "field": field}
    current = current_field(conn, argument_id, field)
    if current is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    revision = get_revision(conn, argument_id, field)
    base = op["base_revision"]
    if base > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 400, meta.fit_terminal_result(result, current, desired)
    if base < revision and current != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 409, meta.fit_terminal_result(result, current, desired)
    changed, after = set_field_on_conn(conn, argument_id, field, desired)
    result.update(code="ACKNOWLEDGED", server_revision=after, changed=changed,
                  value_omitted=True)
    return 200, result


def validate_sources(op):
    payload = op["payload"]
    if set(payload) != {"sources"}:
        raise ValueError("INVALID_ENVELOPE")
    _check_sources_shape(payload["sources"])
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def apply_sources(db, conn, op, received_at):
    argument_id = op["entity_id"]
    desired = op["payload"]["sources"]
    result = {"argument_id": argument_id}
    if not exists(conn, argument_id):
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    revision = get_sources_revision(conn, argument_id)
    present = current_sources(conn, argument_id)
    base = op["base_revision"]
    if base > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_count=len(present), requested_count=len(desired))
        return 400, result
    if base < revision and desired != present:
        # An ORDERED list, so agreeing on it means agreeing on the order too:
        # the same Works in a different order is a different citation list.
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_count=len(present), requested_count=len(desired))
        return 409, result
    try:
        changed, after = set_sources_on_conn(conn, argument_id, desired)
    except ResearchError as error:
        return _rule_result(error, result)
    result.update(code="ACKNOWLEDGED", changed=changed, server_revision=after)
    return 200, result


def validate_targets(op):
    payload = op["payload"]
    if set(payload) != {"targets"}:
        raise ValueError("INVALID_ENVELOPE")
    _check_targets_shape(payload["targets"])
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def apply_targets(db, conn, op, received_at):
    argument_id = op["entity_id"]
    desired = op["payload"]["targets"]
    result = {"argument_id": argument_id}
    if not exists(conn, argument_id):
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    revision = get_targets_revision(conn, argument_id)
    present = current_targets(conn, argument_id)
    base = op["base_revision"]
    if base > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_count=len(present), requested_count=len(desired))
        return 400, result
    if base < revision and desired != present:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_count=len(present), requested_count=len(desired))
        return 409, result
    try:
        changed, after = set_targets_on_conn(conn, argument_id, desired)
    except ResearchError as error:
        return _rule_result(error, result)
    result.update(code="ACKNOWLEDGED", changed=changed, server_revision=after)
    return 200, result


def validate_delete(op):
    if op["payload"] != {}:
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")


def apply_delete(db, conn, op, received_at):
    argument_id = op["entity_id"]
    result = {"argument_id": argument_id}
    deleted, refusal = delete_argument_on_conn(conn, argument_id)
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
SOURCES_HANDLER = _handler(validate_sources, apply_sources)
TARGETS_HANDLER = _handler(validate_targets, apply_targets)
DELETE_HANDLER = _handler(validate_delete, apply_delete)
