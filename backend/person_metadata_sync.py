"""Person profile fields: per-FIELD revisions and the SET_PERSON_METADATA_FIELD handler.

The conflict unit is one field, not one profile. That is a reading of the
Person schema rather than a copy of the Work-metadata family: every editable
column on `persons` is an independent scalar -- a biography, a birth date and a
Wikipedia link are separate decisions, and two devices that changed different
ones have not disagreed about anything. A profile-level revision would tell
them they had, and force a resolution UI over a conflict that does not exist.
The canonical writer is already field-shaped for the same reason:
`update_person_profile` applies whatever subset of `PERSON_METADATA_FIELDS` it
is handed.

The one form with one Save button in the UI is a grouping of controls, not an
atomicity requirement. Group membership IS atomic with metadata on the ordinary
PATCH, and is deliberately outside this family: it is a relationship, not a
scalar, and it belongs to its own milestone.

`SYNCED_FIELDS` here is the SAME vocabulary as `CREATE_PERSON`, because a field
that can be constructed and not edited -- or the reverse -- is a split contract
by another name. There are no per-field byte bounds for the same reason: the
creation family has none either, and the generic envelope bound already caps
what a durable operation may carry. Inventing a limit here would make a value
savable through one path and refused through another.

Nothing in this module imports the database layer; the handler is handed the
`db` it needs, so the ordinary PATCH boundary can share the same helpers.
"""
import json

from backend.person_image import PersonImageUrlError, normalize_person_image_url
from backend.person_sync import FIELDS as PERSON_FIELDS

# The editable profile columns, in the creation family's order. `id`,
# `created_at` and `updated_at` are not user-editable and are absent by
# construction rather than by filtering.
SYNCED_FIELDS = frozenset(PERSON_FIELDS)

SCOPE_TYPE = "person-field"


def scope_key(person_id, field):
    # Structural encoding, like every other scope in this protocol: no
    # delimiter has to be excluded from either component for it to stay
    # unambiguous.
    return json.dumps([person_id, field], ensure_ascii=True, separators=(",", ":"))


def canonical_wire(field, value):
    """The spelling two devices must agree on for this field.

    Only `image_url` has one: the portrait normalizer is what decides whether
    two spellings name the same picture, so comparing raw input would report a
    conflict between devices that chose the same image. Every other profile
    field is stored as typed.
    """
    text = "" if value is None else str(value)
    if field == "image_url":
        return normalize_person_image_url(text)
    return text


def is_valid_field_value(field, value):
    """The ONE rule every Person-field mutation path asks.

    Deliberately the same rule `CREATE_PERSON` applies, and no more: any string
    is acceptable except an `image_url` the portrait normalizer refuses. A
    stricter rule here than on creation or on the ordinary PATCH would be a
    second validation contract -- a value savable by one path and refused by
    another -- which is exactly what moving a field to local-first exists to
    remove.
    """
    if not isinstance(value, str):
        return False
    if field == "image_url":
        try:
            normalize_person_image_url(value)
        except PersonImageUrlError:
            return False
    return True


def disagreement(current, desired):
    """What the two sides hold.

    Both values in full, because the client can then offer "Use server"
    without a second request. `fit_terminal_result` degrades this to bounded
    previews when a profile field is long enough that the whole result would
    not fit the client's durable bound -- no Person field has a length limit
    of its own, so that degradation is the only thing standing between a long
    biography and a conflict the client cannot store.
    """
    return {"current_value": current, "requested_value": desired}


def validate(op):
    payload = op["payload"]
    if set(payload) != {"field", "value"}:
        raise ValueError("INVALID_ENVELOPE")
    field, value = payload["field"], payload["value"]
    # An arbitrary column name from a client would be an injection surface and
    # a way to reach columns that are not user-editable at all.
    if not isinstance(field, str) or field not in SYNCED_FIELDS:
        raise ValueError("INVALID_ENVELOPE")
    if not isinstance(value, str) or not is_valid_field_value(field, value):
        raise ValueError("INVALID_ENVELOPE")
    # Editing is mutation, not construction: a null base revision is a client
    # that cannot detect a conflict, and would silently overwrite whatever
    # another device wrote. `CREATE_PERSON` requires the opposite, which is the
    # whole difference between the two families.
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


