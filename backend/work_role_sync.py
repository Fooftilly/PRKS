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

# People-navigation / Processing / bibliographic picker subset. `Mentioned` is
# a real Work-Person role (annotation-derived) but is intentionally not a
# navigable People filter and is not offered when staging a Processing File.
# Derived from ROLE_TYPES so a future role addition cannot silently omit the
# subset unless Mentioned is the only exclusion named here.
PEOPLE_ROLE_TYPES = tuple(role for role in ROLE_TYPES if role != "Mentioned")
PEOPLE_ROLE_TYPE_SET = frozenset(PEOPLE_ROLE_TYPES)

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


def parse_person_aliases(aliases_text):
    """Legacy `persons.aliases` reader: comma-delimited, stripped empties.

    Existing rows may already contain commas that were either delimiters or
    parts of a printed name. This never invents a new interpretation; it is
    the same split every profile/search consumer already uses.
    """
    return [x.strip() for x in (aliases_text or "").split(",") if x.strip()]


def credit_promotable_as_alias(credit_name):
    """Whether a role credit can be stored as ONE Person alias losslessly.

    `persons.aliases` is still a comma-delimited text field. A credit that
    itself contains a comma (`Smith, John`) cannot round-trip through that
    encoding as a single alias: every reader would split it. Commas stay legal
    on the role's `credit_name`; only auto-promotion into aliases is refused.
    """
    alias = canonical_credit_name(credit_name)
    if not alias:
        return False
    parts = parse_person_aliases(alias)
    return len(parts) == 1 and parts[0] == alias


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


