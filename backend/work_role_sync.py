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

# A credit override is a person's name as printed on one work. Bounded so the
# field has a contract rather than inheriting whichever layer refuses first,
# and small enough that a terminal result carrying two of them still fits the
# client's 2 KiB durable bound without truncation.
MAX_CREDIT_NAME_BYTES = 500


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


def canonical_credit_name(credit_name):
    """'' means no override. NULL and whitespace are the same intent."""
    return (credit_name or "").strip()


def validate_role_type(role_type):
    """The one place a role name is judged.

    The durable envelope validator rejected unknown roles while `add_role()`
    accepted anything, so `Producer` was refused offline and accepted online --
    a split contract in the direction that matters least to catch and most to
    live with, since the accepted row then matched no filter, icon or BibTeX
    mapping forever.
    """
    if role_type not in ROLE_TYPE_SET:
        raise ValueError(
            "%r is not a Work role; use one of %s"
            % (role_type, ", ".join(ROLE_TYPES)))


def validate_credit_name(credit_name):
    """The 500-byte contract, at the write boundary.

    Durable envelopes already refuse oversized credits. Ordinary HTTP and
    construction went through this function's callers without the check, so a
    501-byte credit was impossible offline and fine online.
    """
    if credit_name is None:
        return
    if not isinstance(credit_name, str):
        raise ValueError("credit_name must be a string")
    if len(credit_name.encode("utf-8")) > MAX_CREDIT_NAME_BYTES:
        raise ValueError(
            "credit_name exceeds %d bytes" % MAX_CREDIT_NAME_BYTES)


def current_state(conn, work_id, person_id, role_type):
    """The element's canonical state: None when absent, else its credit name.

    Presence alone is not the state. A link carries `credit_name` -- "the name
    on THIS file" -- and that value reaches `linked_authors`, the card credit,
    BibTeX and Person aliases. Two devices that both make the relationship
    present but choose different credit names have not converged, and a model
    that compared only booleans would have called that agreement.
    """
    row = conn.execute(
        "SELECT credit_name FROM roles WHERE work_id = ? AND person_id = ? "
        "AND role_type = ? LIMIT 1", (work_id, person_id, role_type)).fetchone()
    if row is None:
        return None
    return canonical_credit_name(row[0])


def is_present(conn, work_id, person_id, role_type):
    return current_state(conn, work_id, person_id, role_type) is not None


def next_order_index(conn, work_id):
    """Append after the links already on this Work."""
    row = conn.execute(
        "SELECT COALESCE(MAX(order_index), -1) AS m FROM roles WHERE work_id = ?",
        (work_id,)).fetchone()
    return int(row[0]) + 1


def _append_person_alias(conn, person_id, alias):
    """"Mark Twain" typed on a link becomes one of Samuel Clemens's aliases.

    A long-standing side effect of linking with a credit override, and People
    search depends on it. It lives at this boundary now so it happens for every
    canonical write rather than only the one HTTP handler that remembered it.
    """
    alias = canonical_credit_name(alias)
    if not alias:
        return False
    row = conn.execute(
        "SELECT aliases, first_name, last_name FROM persons WHERE id = ?",
        (person_id,)).fetchone()
    if row is None:
        return False
    parts = [x.strip() for x in (row[0] or "").split(",") if x.strip()]
    if any(p.lower() == alias.lower() for p in parts):
        return False
    canonical = ("%s %s" % ((row[1] or "").strip(), (row[2] or "").strip())).strip()
    if canonical and canonical.lower() == alias.lower():
        return False
    parts.append(alias)
    conn.execute(
        "UPDATE persons SET aliases = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (", ".join(parts), person_id))
    return True


def insert_initial_role(conn, work_id, person_id, role_type, order_index=0,
                        credit_name=""):
    """CONSTRUCTION. A relationship a Work is BORN with.

    Deliberately not `set_role_state()`. Construction is not mutation: a Work
    created with two Authors has not "changed" twice, and manufacturing
    revision 1 for each would make every device's first read look like a missed
    change it has to reconcile.

    It also preserves the caller's `order_index`, because at construction the
    caller IS the authority on author order -- an importer replaying a BibTeX
    author list means the order it states. (After the Work exists, order is
    server-owned placement and mutations append.)
    """
    validate_role_type(role_type)
    validate_credit_name(credit_name)
    conn.execute(
        "INSERT INTO roles (person_id, work_id, role_type, order_index, credit_name) "
        "VALUES (?, ?, ?, ?, ?)",
        (person_id, work_id, role_type, int(order_index),
         canonical_credit_name(credit_name) or None))
    _append_person_alias(conn, person_id, credit_name)


def set_role_state(conn, work_id, person_id, role_type, present, credit_name=""):
    """MUTATION of an existing Work's relationship, with its revision.

    ONE boundary for the sync handler and for the ordinary HTTP endpoints, so a
    relationship can never change without its revision -- an offline device
    holding the old state would otherwise have no way to discover it had been
    overtaken, and would overwrite a decision it never saw.

    The revision survives the relationship: a removal has to leave a record, or
    an offline "remove" replayed after a remote "add" would look like it was
    based on the current state. This is why the revision lives in
    `sync_entity_revisions` keyed by the triple rather than being inferred from
    whether the row exists.

    `order_index` is server-owned here: a new link appends after what is already
    on the Work. Nothing in the product reorders, and a caller's index would be
    a claim about placement it cannot coordinate with other devices.
    """
    validate_role_type(role_type)
    if present:
        validate_credit_name(credit_name)
    desired = canonical_credit_name(credit_name) if present else None
    existing = current_state(conn, work_id, person_id, role_type)
    if existing == desired:
        return False
    if desired is None:
        conn.execute(
            "DELETE FROM roles WHERE work_id = ? AND person_id = ? AND role_type = ?",
            (work_id, person_id, role_type))
    elif existing is None:
        conn.execute(
            "INSERT INTO roles (person_id, work_id, role_type, order_index, credit_name) "
            "VALUES (?, ?, ?, ?, ?)",
            (person_id, work_id, role_type, next_order_index(conn, work_id),
             desired or None))
        _append_person_alias(conn, person_id, desired)
    else:
        conn.execute(
            "UPDATE roles SET credit_name = ? WHERE work_id = ? AND person_id = ? "
            "AND role_type = ?", (desired or None, work_id, person_id, role_type))
        _append_person_alias(conn, person_id, desired)
    conn.execute(
        """INSERT INTO sync_entity_revisions (scope_type, scope_id, revision)
           VALUES (?, ?, 1) ON CONFLICT (scope_type, scope_id)
           DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP""",
        (SCOPE_TYPE, scope_key(work_id, person_id, role_type)))
    # The catalog ETag counts role rows, and cards render the credit.
    conn.execute("UPDATE works SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                 (work_id,))
    return True


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


# `REMOVE` names absence and carries no credit; the other two name a PRESENT
# state, which includes the credit override.
CARRIES_CREDIT = ("ADD_WORK_PERSON_ROLE", "SET_WORK_PERSON_ROLE_CREDIT")


def validate(op):
    payload = op["payload"]
    expected = {"person_id", "role_type"}
    if op["operation"] in CARRIES_CREDIT:
        expected.add("credit_name")
    if set(payload) != expected:
        raise ValueError("INVALID_ENVELOPE")
    person_id, role_type = payload["person_id"], payload["role_type"]
    if not isinstance(person_id, str) or not person_id.strip():
        raise ValueError("INVALID_ENVELOPE")
    if role_type not in ROLE_TYPE_SET:
        raise ValueError("INVALID_ENVELOPE")
    if op["operation"] in CARRIES_CREDIT:
        credit = payload["credit_name"]
        if not isinstance(credit, str) or len(credit.encode("utf-8")) > MAX_CREDIT_NAME_BYTES:
            raise ValueError("INVALID_ENVELOPE")
    # Optimistic concurrency: a null base revision is a client that cannot
    # detect a conflict, and would silently overwrite whatever another device
    # decided this relationship was.
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def _reported(state):
    """A state, in the two scalars a terminal result carries.

    Kept as two fields rather than one nullable string because a client has to
    render "linked, credited as X" and "not linked" differently, and a `null`
    credit would otherwise be ambiguous with "linked, no override".
    """
    return {"present": state is not None, "credit_name": state or ""}


def apply(db, conn, op, received_at):
    work_id = op["entity_id"]
    person_id = op["payload"]["person_id"]
    role_type = op["payload"]["role_type"]
    result = {"work_id": work_id, "person_id": person_id, "role_type": role_type}
    if not conn.execute("SELECT 1 FROM works WHERE id = ?", (work_id,)).fetchone():
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    person = conn.execute(
        "SELECT first_name, last_name FROM persons WHERE id = ?", (person_id,)).fetchone()
    if person is None:
        # A relationship to someone who does not exist is not a conflict the
        # user can resolve here -- Person creation is not part of this family.
        result["code"] = "PERSON_NOT_FOUND"
        return 404, result

    revision = get_revision(conn, work_id, person_id, role_type)
    existing = current_state(conn, work_id, person_id, role_type)
    operation = op["operation"]
    desired = (canonical_credit_name(op["payload"].get("credit_name"))
               if operation in CARRIES_CREDIT else None)
    base = op["base_revision"]

    if base > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current=_reported(existing), requested=_reported(desired))
        return 400, result
    if operation == "SET_WORK_PERSON_ROLE_CREDIT" and existing is None:
        # Editing the credit on a link that is not there. Not a revision
        # disagreement to resolve -- there is nothing to edit -- so it is said
        # plainly and the client re-reads rather than being offered a choice
        # between two states one of which does not exist.
        result.update(code="ROLE_NOT_PRESENT", current_revision=revision)
        return 409, result
    # A stale base is only a conflict when the two devices actually DISAGREE.
    # Two that both linked Jane as Author with the same credit have converged,
    # however many revisions apart they started; two that chose different
    # credit names have not.
    if base < revision and existing != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current=_reported(existing), requested=_reported(desired))
        return 409, result
    changed = set_role_state(conn, work_id, person_id, role_type,
                             desired is not None, credit_name=desired or "")
    result.update(code="ACKNOWLEDGED", changed=changed,
                  server_revision=revision + int(changed), **_reported(desired))
    # The Person's own name travels with the acknowledgement. A client whose
    # cached Work detail does not yet hold this link cannot build its row
    # without it -- the panel renders a profile name, and the alternative is
    # either a blank chip or discarding the whole cached Work to re-read it.
    # Two short strings, and the client already displays them everywhere.
    result["first_name"] = person[0] or ""
    result["last_name"] = person[1] or ""
    return 200, result


class _Handler:
    validate = staticmethod(validate)
    apply = staticmethod(apply)


HANDLER = _Handler()
