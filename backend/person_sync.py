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
