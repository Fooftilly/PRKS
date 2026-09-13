"""Work-Person ROLE relationships: revisions and the
ADD_WORK_PERSON_ROLE / REMOVE_WORK_PERSON_ROLE handler.

The generic envelope, request hash, ledger and dispatch live in
`sync_protocol`. Everything here is what a Work-Person role MEANS.

Why an element conflict unit rather than an ordered aggregate
-------------------------------------------------------------
The `roles` table's primary key includes `order_index`, so ordering is
structurally present and the choice deserved evidence rather than convenience.
The evidence says the mutation identity is `(work, person, role)`:

  * `add_role()` refuses a second row for an existing
    `(person, work, role_type)` regardless of `order_index`, so at most one
    row exists per triple;
  * `order_index` is assigned by the SERVER at insert time as "append after
    what is already there", never chosen by a user;
  * nothing in the product updates it -- there is no reorder operation, so no
    atomicity depends on ordering;
  * nothing requires the indexes to be contiguous. `ORDER BY order_index,
    rowid` is a total order whatever values exist.

So two devices adding different people concurrently produce an append order
decided by arrival at the server, which is a legitimate order rather than an
invalid one -- and they never collide, because they are different scopes. An
aggregate would have made every independent link on one Work one conflict.

Desired-state semantics, mirroring Work-Tags: adding a relationship that is
already there, or removing one that is already gone, is CONVERGENCE and is
acknowledged. A conflict exists only when the server's state and the desired
state actually differ across a revision the device did not see.
"""
import json

SCOPE_TYPE = "work-person-role"

# The role vocabulary, shared with the client.
#
# `POST /api/roles` accepted any non-empty string, while the link picker has
# only ever offered these eight. A durable operation cannot be that loose: an
# envelope is replayed exactly, so a typo'd role would be stored forever and
# would match no filter, no icon and no BibTeX mapping. Formalized here rather
# than normalized -- silently turning an unknown role into "Author" would
# assert a relationship the user never described.
ROLE_TYPES = ("Author", "Editor", "Reviewer", "Mentioned", "Translator",
              "Introduction", "Foreword", "Afterword")
ROLE_TYPE_SET = frozenset(ROLE_TYPES)


def scope_key(work_id, person_id, role_type):
    # Structural encoding, like every other scope: no id has to exclude a
    # delimiter for this to stay unambiguous.
    return json.dumps([work_id, person_id, role_type], ensure_ascii=True,
                      separators=(",", ":"))


def get_revision(conn, work_id, person_id, role_type):
    row = conn.execute(
        "SELECT revision FROM sync_entity_revisions WHERE scope_type = ? AND scope_id = ?",
        (SCOPE_TYPE, scope_key(work_id, person_id, role_type))).fetchone()
    return row[0] if row else 0


def is_present(conn, work_id, person_id, role_type):
    return bool(conn.execute(
        "SELECT 1 FROM roles WHERE work_id = ? AND person_id = ? AND role_type = ? LIMIT 1",
        (work_id, person_id, role_type)).fetchone())


def next_order_index(conn, work_id):
    """Append after the links already on this Work."""
    row = conn.execute(
        "SELECT COALESCE(MAX(order_index), -1) AS m FROM roles WHERE work_id = ?",
        (work_id,)).fetchone()
    return int(row[0]) + 1


def set_state(conn, work_id, person_id, role_type, present, credit_name=None):
    """Write the relationship and advance its revision together.

    ONE boundary for the sync handler and for the ordinary HTTP endpoints, so a
    relationship can never change without its revision -- an offline device
    holding the old state would otherwise have no way to discover it had been
    overtaken, and would overwrite a decision it never saw.

    The revision survives the relationship: a removal has to leave a record, or
    an offline "remove" replayed after a remote "add" would look like it was
    based on the current state. This is why the revision lives in
    `sync_entity_revisions` keyed by the triple rather than being inferred from
    whether the row exists.
    """
    if present:
        if is_present(conn, work_id, person_id, role_type):
            changed = False
        else:
            cn = (credit_name or "").strip() or None
            conn.execute(
                "INSERT INTO roles (person_id, work_id, role_type, order_index, credit_name) "
                "VALUES (?, ?, ?, ?, ?)",
                (person_id, work_id, role_type, next_order_index(conn, work_id), cn))
            changed = True
    else:
        changed = conn.execute(
            "DELETE FROM roles WHERE work_id = ? AND person_id = ? AND role_type = ?",
            (work_id, person_id, role_type)).rowcount > 0
    if changed:
        conn.execute(
            """INSERT INTO sync_entity_revisions (scope_type, scope_id, revision)
               VALUES (?, ?, 1) ON CONFLICT (scope_type, scope_id)
               DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP""",
            (SCOPE_TYPE, scope_key(work_id, person_id, role_type)))
        # The catalog ETag counts role rows, and cards render the credit.
        conn.execute("UPDATE works SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (work_id,))
    return changed


def get_roles_state(db, work_id):
    """Synchronization bookkeeping for one Work's people.

    Revisions only, plus the triple each belongs to. The Work detail already
    carries the linked people themselves; duplicating Person objects into a
    second cached projection would double what every read costs for values the
    client already holds.

    Tombstones are included: a scope with a revision and no live relationship is
    exactly the state an offline device needs in order to know that its own
    pending removal is current rather than stale.
    """
    with db.connection() as conn:
        if not conn.execute("SELECT 1 FROM works WHERE id = ?", (work_id,)).fetchone():
            return None
        rows = conn.execute(
            "SELECT scope_id, revision FROM sync_entity_revisions WHERE scope_type = ?",
            (SCOPE_TYPE,)).fetchall()
        present = {
            (r[0], r[1]) for r in conn.execute(
                "SELECT person_id, role_type FROM roles WHERE work_id = ?", (work_id,))
        }
        scopes = []
        for scope_id, revision in rows:
            try:
                scoped_work, person_id, role_type = json.loads(scope_id)
            except (ValueError, TypeError):
                continue
            if scoped_work != work_id:
                continue
            scopes.append({"person_id": person_id, "role_type": role_type,
                           "revision": revision,
                           "present": (person_id, role_type) in present})
        # Relationships that exist but have never been through a revision-aware
        # write -- created with the Work, or imported. Revision 0 is the honest
        # answer: construction is not mutation.
        known = {(s["person_id"], s["role_type"]) for s in scopes}
        for person_id, role_type in sorted(present - known):
            scopes.append({"person_id": person_id, "role_type": role_type,
                           "revision": 0, "present": True})
        scopes.sort(key=lambda s: (s["person_id"], s["role_type"]))
        return {"work_id": work_id, "scopes": scopes}


def validate(op):
    payload = op["payload"]
    if set(payload) != {"person_id", "role_type"}:
        raise ValueError("INVALID_ENVELOPE")
    person_id, role_type = payload["person_id"], payload["role_type"]
    if not isinstance(person_id, str) or not person_id.strip():
        raise ValueError("INVALID_ENVELOPE")
    if role_type not in ROLE_TYPE_SET:
        raise ValueError("INVALID_ENVELOPE")
    # Optimistic concurrency: a null base revision is a client that cannot
    # detect a conflict, and would silently overwrite whatever another device
    # decided this relationship was.
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def apply(db, conn, op, received_at):
    work_id = op["entity_id"]
    person_id = op["payload"]["person_id"]
    role_type = op["payload"]["role_type"]
    result = {"work_id": work_id, "person_id": person_id, "role_type": role_type}
    if not conn.execute("SELECT 1 FROM works WHERE id = ?", (work_id,)).fetchone():
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    if not conn.execute("SELECT 1 FROM persons WHERE id = ?", (person_id,)).fetchone():
        # A relationship to someone who does not exist is not a conflict the
        # user can resolve here -- Person creation is not part of this family.
        result["code"] = "PERSON_NOT_FOUND"
        return 404, result

    revision = get_revision(conn, work_id, person_id, role_type)
    present = is_present(conn, work_id, person_id, role_type)
    desired = op["operation"] == "ADD_WORK_PERSON_ROLE"
    base = op["base_revision"]
    if base > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_state=present, requested_state=desired)
        return 400, result
    # A stale base is only a conflict when the two devices actually disagree.
    # Two people who both linked Jane as Author have converged, however many
    # revisions apart they started.
    if base < revision and present != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_state=present, requested_state=desired)
        return 409, result
    changed = set_state(conn, work_id, person_id, role_type, desired)
    result.update(code="ACKNOWLEDGED", present=desired, changed=changed,
                  server_revision=revision + int(changed))
    return 200, result


class _Handler:
    validate = staticmethod(validate)
    apply = staticmethod(apply)


HANDLER = _Handler()
