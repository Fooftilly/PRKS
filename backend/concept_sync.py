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

from backend import research_network as network
from backend.entity_ids import is_distributed
from backend.research_network import (
    ResearchError,
    canonical_concept_name,
    normalize_concept_key,
)

FIELDS = ("description",)
FIELD_SET = frozenset(FIELDS)

FIELD_SCOPE_TYPE = "concept-field"
IDENTITY_SCOPE_TYPE = "concept-identity"
PARENTS_SCOPE_TYPE = "concept-parents"

MAX_ALIASES = 64
MAX_PARENTS = 64


def scope_key(concept_id, field):
    return json.dumps([concept_id, field], ensure_ascii=True, separators=(",", ":"))


def _revision(conn, scope_type, scope_id):
    row = conn.execute(
        "SELECT revision FROM sync_entity_revisions WHERE scope_type = ? AND scope_id = ?",
        (scope_type, scope_id)).fetchone()
    return row[0] if row else 0


def get_revision(conn, concept_id, field):
    return _revision(conn, FIELD_SCOPE_TYPE, scope_key(concept_id, field))


def get_identity_revision(conn, concept_id):
    return _revision(conn, IDENTITY_SCOPE_TYPE, concept_id)


def get_parents_revision(conn, concept_id):
    return _revision(conn, PARENTS_SCOPE_TYPE, concept_id)


def _advance(conn, scope_type, scope_id):
    conn.execute(
        """INSERT INTO sync_entity_revisions (scope_type, scope_id, revision)
           VALUES (?, ?, 1) ON CONFLICT (scope_type, scope_id)
           DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP""",
        (scope_type, scope_id))


def current_field(conn, concept_id, field):
    row = conn.execute(
        "SELECT %s FROM concepts WHERE id = ?" % field, (concept_id,)).fetchone()
    if row is None:
        return None
    return "" if row[0] is None else str(row[0])


def current_identity(conn, concept_id):
    """`{name, aliases}` as the server holds it, or None.

    Aliases come back in the same order `get_concept` renders them, so a client
    comparing what it is showing against what it last acknowledged compares two
    lists built the same way.
    """
    row = conn.execute("SELECT name FROM concepts WHERE id = ?", (concept_id,)).fetchone()
    if row is None:
        return None
    aliases = [r[0] for r in conn.execute(
        "SELECT alias FROM concept_aliases WHERE concept_id = ? ORDER BY LOWER(alias) ASC",
        (concept_id,))]
    return {"name": str(row[0]), "aliases": aliases}


def current_parents(conn, concept_id):
    return [r[0] for r in conn.execute(
        """SELECT p.id FROM concept_parents cp JOIN concepts p ON p.id = cp.parent_concept_id
           WHERE cp.child_concept_id = ? ORDER BY LOWER(p.name) ASC, p.id ASC""",
        (concept_id,))]


def set_field_on_conn(conn, concept_id, field, value):
    """Write one Concept field and advance its revision together."""
    if field not in FIELD_SET:
        raise ResearchError("invalid_field", "Not an editable Concept field.")
    desired = network._optional_markdown(value, max_len=network.CONCEPT_DEFINITION_MAX)
    desired = "" if desired is None else desired
    current = current_field(conn, concept_id, field)
    if current is None:
        raise ResearchError("not_found", "Concept not found.", 404)
    revision = get_revision(conn, concept_id, field)
    if current == desired:
        return False, revision
    conn.execute(
        "UPDATE concepts SET %s = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?" % field,
        (desired, concept_id))
    _advance(conn, FIELD_SCOPE_TYPE, scope_key(concept_id, field))
    return True, revision + 1


def insert_concept_on_conn(conn, concept_id, name, description=""):
    """CONSTRUCTION, through the identity rules the ordinary path applies.

    A Concept created with a name and a definition has not "changed" twice, so
    no revision is advanced here.
    """
    cname = network._concept_name(name)
    desc = network._optional_markdown(description, max_len=network.CONCEPT_DEFINITION_MAX)
    status, ids = network.resolve_concept_key(conn, cname)
    if status == "ok":
        raise ResearchError("concept_exists",
                            "A Concept with that name or alias already exists.", 409)
    if status == "ambiguous":
        raise ResearchError("ambiguous_concept", "Multiple Concepts match that name.", 409)
    conn.execute("INSERT INTO concepts (id, name, description) VALUES (?, ?, ?)",
                 (concept_id, cname, desc))
    return concept_id


def set_identity_on_conn(conn, concept_id, name, aliases):
    """Rewrite a Concept's NAME and ALIAS SET as one decision.

    Renaming keeps the old name as an alias, exactly as `update_concept` does --
    every note that already says `[[concept:Old Name]]` must go on resolving.
    That is also why this is one operation: the rename writes an alias, so an
    alias edit cannot be a separate conflict unit.
    """
    row = conn.execute("SELECT name FROM concepts WHERE id = ?", (concept_id,)).fetchone()
    if row is None:
        raise ResearchError("not_found", "Concept not found.", 404)
    old_name = str(row[0])
    new_name = network._concept_name(name)
    old_key = normalize_concept_key(old_name)
    new_key = normalize_concept_key(new_name)

    wanted = []
    seen = {new_key}
    for raw in aliases:
        alias = network._concept_name(raw)
        key = normalize_concept_key(alias)
        if key in seen:
            continue
        seen.add(key)
        wanted.append((alias, key))
    # A rename keeps the old name reachable, unless the caller has already said
    # where it belongs -- a request that drops it is dropping it deliberately.
    if new_key != old_key and old_key not in seen:
        wanted.append((old_name, old_key))
        seen.add(old_key)

    if new_key != old_key:
        status, ids = network.resolve_concept_key(conn, new_name)
        if status == "ok" and ids[0] != concept_id:
            raise ResearchError("concept_exists",
                                "A Concept with that name or alias already exists.", 409)
        if status == "ambiguous" and [i for i in ids if i != concept_id]:
            raise ResearchError("ambiguous_concept",
                                "Multiple Concepts match that name.", 409)
    for _alias, key in wanted:
        others = [i for i in network._normalized_hits(conn, key) if i != concept_id]
        if others:
            raise ResearchError("alias_conflict",
                                "That search key already belongs to another Concept.", 409)

    before = current_identity(conn, concept_id)
    if new_name != old_name:
        conn.execute("UPDATE concepts SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (new_name, concept_id))
    conn.execute("DELETE FROM concept_aliases WHERE concept_id = ?", (concept_id,))
    for alias, key in wanted:
        conn.execute(
            "INSERT INTO concept_aliases (concept_id, alias, normalized_alias) VALUES (?, ?, ?)",
            (concept_id, alias, key))
    after = current_identity(conn, concept_id)
    if after == before:
        return False, get_identity_revision(conn, concept_id)
    _advance(conn, IDENTITY_SCOPE_TYPE, concept_id)
    return True, get_identity_revision(conn, concept_id)


def set_parents_on_conn(conn, concept_id, parent_ids):
    """Rewrite the whole parent set. Acyclicity stays canonical."""
    if conn.execute("SELECT 1 FROM concepts WHERE id = ?", (concept_id,)).fetchone() is None:
        raise ResearchError("not_found", "Concept not found.", 404)
    wanted, seen = [], set()
    for raw in parent_ids:
        pid = (raw or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        wanted.append(pid)
    for pid in wanted:
        if conn.execute("SELECT 1 FROM concepts WHERE id = ?", (pid,)).fetchone() is None:
            raise ResearchError("parent_not_found", "Parent Concept not found.", 404)
    if network._parent_cycle(conn, concept_id, wanted):
        raise ResearchError("concept_cycle",
                            "That parent would create a Concept hierarchy cycle.", 409)
    before = current_parents(conn, concept_id)
    conn.execute("DELETE FROM concept_parents WHERE child_concept_id = ?", (concept_id,))
    for pid in wanted:
        conn.execute(
            "INSERT INTO concept_parents (child_concept_id, parent_concept_id) VALUES (?, ?)",
            (concept_id, pid))
    after = current_parents(conn, concept_id)
    if after == before:
        return False, get_parents_revision(conn, concept_id)
    _advance(conn, PARENTS_SCOPE_TYPE, concept_id)
    # A parent change moves BOTH ends of the edge: the parent's `children` is
    # rendered from the same table, and a device holding its page has to be
    # able to discover it was overtaken.
    for pid in set(before) ^ set(after):
        _advance(conn, PARENTS_SCOPE_TYPE, pid)
    return True, get_parents_revision(conn, concept_id)


def delete_concept_on_conn(conn, concept_id):
    """Remove a Concept, or refuse. Returns `(deleted, refusal_code)`.

    The protection is unchanged: a Concept still referenced by canonical
    research notes cannot be deleted, because deleting it would leave notes
    pointing at nothing. Its children are NOT reparented -- `ON DELETE CASCADE`
    drops the edges, exactly as the ordinary endpoint leaves them.
    """
    if conn.execute("SELECT 1 FROM concepts WHERE id = ?", (concept_id,)).fetchone() is None:
        return False, None
    if network._canonical_notes_reference_concept(conn, concept_id):
        return False, "CONCEPT_IN_USE"
    related = set(current_parents(conn, concept_id))
    related.update(r[0] for r in conn.execute(
        "SELECT child_concept_id FROM concept_parents WHERE parent_concept_id = ?",
        (concept_id,)))
    conn.execute("DELETE FROM concepts WHERE id = ?", (concept_id,))
    for pid in related:
        _advance(conn, PARENTS_SCOPE_TYPE, pid)
    _advance(conn, IDENTITY_SCOPE_TYPE, concept_id)
    return True, None


def get_concept_state_on_conn(conn, concept_id):
    """Revisions only, plus the two aggregate VALUES.

    The field revision is a number; the identity and the hierarchy are sets, and
    a client measuring an edit against them needs what it last acknowledged --
    the Concept detail carries `parents` as objects and would have to be
    reshaped, so the ids are stated here instead.
    """
    identity = current_identity(conn, concept_id)
    if identity is None:
        return None
    fields = {}
    for name in FIELDS:
        fields[name] = {"revision": get_revision(conn, concept_id, name)}
    return {
        "concept_id": concept_id,
        "fields": fields,
        "identity": identity,
        "identity_revision": get_identity_revision(conn, concept_id),
        "parent_ids": current_parents(conn, concept_id),
        "parents_revision": get_parents_revision(conn, concept_id),
    }


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


def _rule_result(error, result):
    code = _RULE_CODES.get(getattr(error, "code", ""), "ENTITY_NOT_FOUND")
    result["code"] = code
    return _RULE_STATUS.get(getattr(error, "code", ""), 409), result


def _concept_row(conn, concept_id):
    row = conn.execute(
        "SELECT id, name, description FROM concepts WHERE id = ?", (concept_id,)).fetchone()
    return dict(row) if row is not None else None


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
    if not canonical_concept_name(payload["name"]):
        raise ValueError("INVALID_ENVELOPE")


def apply_create(db, conn, op, received_at):
    concept_id = op["entity_id"]
    payload = op["payload"]
    result = {"concept_id": concept_id}
    existing = _concept_row(conn, concept_id)
    if existing is not None:
        # Ids are permanent and distributed, so a duplicate delivery of the
        # same creation is idempotent rather than a refusal.
        result.update(code="ACKNOWLEDGED", changed=False, concept=existing)
        return 200, result
    try:
        insert_concept_on_conn(conn, concept_id, payload["name"], payload["description"])
    except ResearchError as error:
        return _rule_result(error, result)
    result.update(code="ACKNOWLEDGED", changed=True, concept=_concept_row(conn, concept_id))
    return 200, result


def validate_field(op):
    payload = op["payload"]
    if set(payload) != {"field", "value"}:
        raise ValueError("INVALID_ENVELOPE")
    if payload["field"] not in FIELD_SET or not isinstance(payload["value"], str):
        raise ValueError("INVALID_ENVELOPE")
    if len(payload["value"]) > network.CONCEPT_DEFINITION_MAX:
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def apply_field(db, conn, op, received_at):
    from backend import work_metadata_sync as meta

    concept_id = op["entity_id"]
    field = op["payload"]["field"]
    desired = op["payload"]["value"]
    result = {"concept_id": concept_id, "field": field}
    current = current_field(conn, concept_id, field)
    if current is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    revision = get_revision(conn, concept_id, field)
    base = op["base_revision"]
    if base > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 400, meta.fit_terminal_result(result, current, desired)
    if base < revision and current != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 409, meta.fit_terminal_result(result, current, desired)
    try:
        changed, after = set_field_on_conn(conn, concept_id, field, desired)
    except ResearchError as error:
        return _rule_result(error, result)
    result.update(code="ACKNOWLEDGED", server_revision=after, changed=changed,
                  value_omitted=True)
    return 200, result


def validate_identity(op):
    payload = op["payload"]
    if set(payload) != {"name", "aliases"}:
        raise ValueError("INVALID_ENVELOPE")
    name, aliases = payload["name"], payload["aliases"]
    if not isinstance(name, str) or not canonical_concept_name(name):
        raise ValueError("INVALID_ENVELOPE")
    if not isinstance(aliases, list) or len(aliases) > MAX_ALIASES:
        raise ValueError("INVALID_ENVELOPE")
    for alias in aliases:
        if not isinstance(alias, str) or not canonical_concept_name(alias):
            raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def apply_identity(db, conn, op, received_at):
    concept_id = op["entity_id"]
    payload = op["payload"]
    result = {"concept_id": concept_id}
    current = current_identity(conn, concept_id)
    if current is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    revision = get_identity_revision(conn, concept_id)
    base = op["base_revision"]
    if base > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_value=current["name"], requested_value=payload["name"])
        return 400, result
    if base < revision:
        # Identity is NOT converged the way a scalar is. Two devices that gave
        # one Concept two different names made two claims about what it IS, and
        # agreeing by accident on the name says nothing about the aliases.
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_value=current["name"], requested_value=payload["name"])
        return 409, result
    try:
        changed, after = set_identity_on_conn(conn, concept_id, payload["name"],
                                              payload["aliases"])
    except ResearchError as error:
        return _rule_result(error, result)
    stored = current_identity(conn, concept_id)
    result.update(code="ACKNOWLEDGED", changed=changed, server_revision=after,
                  name=stored["name"], aliases=stored["aliases"])
    return 200, result


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


def apply_parents(db, conn, op, received_at):
    concept_id = op["entity_id"]
    desired = op["payload"]["parent_ids"]
    result = {"concept_id": concept_id}
    if conn.execute("SELECT 1 FROM concepts WHERE id = ?", (concept_id,)).fetchone() is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    revision = get_parents_revision(conn, concept_id)
    present = current_parents(conn, concept_id)
    base = op["base_revision"]
    if base > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_count=len(present), requested_count=len(set(desired)))
        return 400, result
    if base < revision and set(desired) != set(present):
        # The hierarchy is a SET, so agreeing on it is convergence even when the
        # revision moved -- two devices that chose the same parents made the
        # same decision.
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_count=len(present), requested_count=len(set(desired)))
        return 409, result
    try:
        changed, after = set_parents_on_conn(conn, concept_id, desired)
    except ResearchError as error:
        return _rule_result(error, result)
    result.update(code="ACKNOWLEDGED", changed=changed, server_revision=after,
                  parent_ids=current_parents(conn, concept_id))
    return 200, result


def validate_delete(op):
    if op["payload"] != {}:
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")


def apply_delete(db, conn, op, received_at):
    concept_id = op["entity_id"]
    result = {"concept_id": concept_id}
    deleted, refusal = delete_concept_on_conn(conn, concept_id)
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
IDENTITY_HANDLER = _handler(validate_identity, apply_identity)
PARENTS_HANDLER = _handler(validate_parents, apply_parents)
DELETE_HANDLER = _handler(validate_delete, apply_delete)
