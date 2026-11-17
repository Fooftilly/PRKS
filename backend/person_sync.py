"""CREATE_PERSON: client-generated identity, same field contract as POST /api/persons.

Construction, not mutation: `base_revision` is null, and the id in the envelope
is the id the Library stores. A replay of the same op_id is the ledger. A later
envelope for an id that already exists acknowledges the stored row without
overwriting it -- creation is not an update.
"""

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


def validate(op):
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")
    if not is_distributed(op["entity_id"], "P"):
        raise ValueError("INVALID_ENVELOPE")
    canonical_fields(op["payload"])


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


