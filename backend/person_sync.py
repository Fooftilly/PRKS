"""CREATE_PERSON: client-generated identity, same field contract as POST /api/persons.

Construction, not mutation: `base_revision` is null, and the id in the envelope
is the id SQLite stores. A replay of the same op_id is the ledger. A later
envelope for an id that already exists acknowledges the stored row without
overwriting it -- creation is not an update.
"""
import sqlite3

from backend.entity_ids import is_distributed
from backend.person_image import PersonImageUrlError, normalize_person_image_url

FIELDS = (
    "first_name",
    "last_name",
    "aliases",
    "about",
    "image_url",
    "link_wikipedia",
    "link_stanford_encyclopedia",
    "link_iep",
    "links_other",
    "birth_date",
    "death_date",
)


def canonical_fields(payload):
    if set(payload) != set(FIELDS):
        raise ValueError("INVALID_ENVELOPE")
    out = {}
    for name in FIELDS:
        value = payload[name]
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ValueError("INVALID_ENVELOPE")
        out[name] = value
    try:
        out["image_url"] = normalize_person_image_url(out["image_url"])
    except PersonImageUrlError:
        raise ValueError("INVALID_ENVELOPE") from None
    return out


def catalog_row_on_conn(conn, person_id):
    row = conn.execute("SELECT * FROM persons WHERE id = ?", (person_id,)).fetchone()
    if row is None:
        return None
    data = dict(row)
    roles = conn.execute(
        "SELECT DISTINCT role_type FROM roles WHERE person_id = ? ORDER BY role_type",
        (person_id,),
    ).fetchall()
    data["assigned_roles"] = [item[0] for item in roles if item[0]]
    data["works"] = []
    groups = conn.execute(
        """
        SELECT g.id, g.name FROM person_groups g
        JOIN person_group_members m ON m.group_id = g.id
        WHERE m.person_id = ?
        ORDER BY g.id
        """,
        (person_id,),
    ).fetchall()
    data["groups"] = [{"id": item[0], "name": item[1]} for item in groups]
    return data


def validate(op):
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")
    if not is_distributed(op["entity_id"], "P"):
        raise ValueError("INVALID_ENVELOPE")
    canonical_fields(op["payload"])


def apply(db, conn, op, received_at):
    person_id = op["entity_id"]
    fields = canonical_fields(op["payload"])
    existing = conn.execute("SELECT 1 FROM persons WHERE id = ?", (person_id,)).fetchone()
    if existing is None:
        try:
            db.insert_person_on_conn(conn, person_id, **fields)
        except sqlite3.IntegrityError:
            # A concurrent insert of the same id is still "this Person exists".
            pass
    row = catalog_row_on_conn(conn, person_id)
    return 200, {
        "code": "ACKNOWLEDGED",
        "person_id": person_id,
        "changed": existing is None,
        "person": row,
    }


class _Handler:
    validate = staticmethod(validate)
    apply = staticmethod(apply)


HANDLER = _Handler()


# --- deletion ---------------------------------------------------------------
#
# `DELETE_PERSON` carries no base revision, for the same reason
# `DELETE_PERSON_GROUP` does not: destruction addresses an IDENTITY rather than
# a value. Absence is idempotent, so there is no second state for two devices to
# disagree about, and a manufactured revision conflict would be a question with
# no answer.
#
# The protection is the ordinary endpoint's, not a new one: a Person who is
# credited on a file is not deletable, and the client is told so rather than the
# server cascading something the ordinary path would have refused.


def validate_delete(op):
    if op["payload"] != {}:
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")


def delete_person_on_conn(conn, person_id):
    """Remove a Person and the memberships that named them.

    Returns (deleted, linked_count). A Person with role links is NOT removed:
    that relationship is a real record of who wrote what, and dropping it
    silently is the one outcome neither path should produce.

    Memberships go through the group family's revision-aware boundary, so a
    device holding "this person is in that group" can discover it was overtaken
    rather than replaying an add against a Person who no longer exists.
    """
    from backend import person_group_sync

    if conn.execute("SELECT 1 FROM persons WHERE id = ?", (person_id,)).fetchone() is None:
        return False, 0
    linked = conn.execute(
        "SELECT COUNT(*) AS c FROM roles WHERE person_id = ?", (person_id,)).fetchone()[0]
    if linked:
        return False, int(linked)
    for row in conn.execute(
            "SELECT group_id FROM person_group_members WHERE person_id = ?",
            (person_id,)).fetchall():
        person_group_sync.set_member_on_conn(conn, row[0], person_id, False)
    conn.execute("DELETE FROM persons WHERE id = ?", (person_id,))
    return True, 0


def apply_delete(db, conn, op, received_at):
    person_id = op["entity_id"]
    existed = conn.execute(
        "SELECT 1 FROM persons WHERE id = ?", (person_id,)).fetchone() is not None
    deleted, linked = delete_person_on_conn(conn, person_id)
    if linked:
        return 409, {"code": "PERSON_HAS_LINKS", "person_id": person_id,
                     "current_revision": linked}
    # Deleting someone who is already gone is CONVERGENCE, not an error: the
    # user asked for their absence and they are absent.
    return 200, {"code": "ACKNOWLEDGED", "person_id": person_id,
                 "changed": bool(deleted and existed)}


class _DeleteHandler:
    validate = staticmethod(validate_delete)
    apply = staticmethod(apply_delete)


DELETE_HANDLER = _DeleteHandler()
