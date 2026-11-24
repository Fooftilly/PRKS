"""Playlists: construction, fields, membership, ordering and deletion.

Five families, and the split follows what the schema already enforces.

  * `CREATE_PLAYLIST` is construction -- a client-minted id, no base revision.
  * `SET_PLAYLIST_FIELD` is scalar mutation with a conflict unit of one FIELD:
    `title`, `description` and `original_url` are independent decisions.
  * `SET_WORK_PLAYLIST` is scalar mutation of the WORK. A Work is in at most one
    playlist -- `add_work_to_playlist` deletes any other membership first -- so
    membership is a field on the Work, keyed by the Work, and `''` means "in no
    playlist". Modelling it from the playlist's end would have made moving one
    video between two playlists a change neither playlist's revision described.
  * `REORDER_PLAYLIST_ITEMS` is an AGGREGATE. An order is not a collection of
    independently racing `position` values: two devices that each dragged one
    video have produced two whole orders, and merging them index by index would
    invent a third that neither chose. One revision covers the structure.
  * `DELETE_PLAYLIST` is destruction, carrying no base revision.

Adding or removing a video changes the order too, so membership advances the
order revision as well -- without that, a reorder made before the add would
still look current.
"""
import json

from backend.entity_ids import is_distributed

FIELDS = ("title", "description", "original_url")
FIELD_SET = frozenset(FIELDS)

FIELD_SCOPE_TYPE = "playlist-field"
ORDER_SCOPE_TYPE = "playlist-order"
WORK_SCOPE_TYPE = "work-playlist"

MAX_TITLE_BYTES = 300
MAX_TEXT_BYTES = 4000
# An order travels as one payload, so this is bounded by what the durable
# store will accept: 1000 ids of 35 characters is roughly 36KB, comfortably
# inside the client's 64KB envelope bound. Mirrored as PLAYLIST_MAX_ITEMS in
# local-store.js, so an over-long order is refused for the reason it actually
# has rather than as a generic oversized payload.
MAX_ITEMS = 1000
# How long an order an acknowledgement will echo back. Past this the client is
# told only that the order changed and re-reads the playlist; below it, it can
# reorder what it already holds without a request.
MAX_ECHOED_ITEMS = 200
DEFAULT_TITLE = "Untitled playlist"


class PlaylistRuleError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def scope_key(playlist_id, field):
    return json.dumps([playlist_id, field], ensure_ascii=True, separators=(",", ":"))


def canonical_wire(field, value):
    """`title` gets the placeholder the ordinary endpoint substitutes; the rest
    are stored trimmed. Both paths normalize before comparing, or a device
    would keep disagreeing with itself."""
    text = ("" if value is None else str(value)).strip()
    if field == "title":
        return text or DEFAULT_TITLE
    return text


def validate_field_value(field, value):
    if field not in FIELD_SET:
        raise PlaylistRuleError("INVALID_ENVELOPE", "unsupported field: %s" % field)
    if not isinstance(value, str):
        raise PlaylistRuleError("INVALID_ENVELOPE", "field values are strings")
    text = canonical_wire(field, value)
    limit = MAX_TITLE_BYTES if field == "title" else MAX_TEXT_BYTES
    if len(text.encode("utf-8")) > limit:
        raise PlaylistRuleError("INVALID_ENVELOPE",
                                "%s exceeds %d bytes." % (field, limit))
    return text


def resolve_order(present, desired):
    """The order a request actually asks for, given what the playlist holds.

    Reordering is not a membership change: an id that is not in the playlist is
    ignored, and one that is but was omitted keeps its relative place at the
    end. That is the rule `reorder_playlist` has always applied, and a second
    one here would make the same drag mean two different things. The client's
    overlay applies the identical rule, so what it shows is what lands.
    """
    known = set(present)
    order, seen = [], set()
    for work_id in desired:
        if not work_id or work_id in seen or work_id not in known:
            continue
        seen.add(work_id)
        order.append(work_id)
    order.extend([w for w in present if w not in seen])
    return order


# ---- handlers -------------------------------------------------------------


def validate_create(op):
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")
    if not is_distributed(op["entity_id"], "PL"):
        raise ValueError("INVALID_ENVELOPE")
    payload = op["payload"]
    if set(payload) != FIELD_SET:
        raise ValueError("INVALID_ENVELOPE")
    for field in FIELDS:
        try:
            validate_field_value(field, payload[field])
        except PlaylistRuleError:
            raise ValueError("INVALID_ENVELOPE") from None


def validate_field(op):
    payload = op["payload"]
    if set(payload) != {"field", "value"}:
        raise ValueError("INVALID_ENVELOPE")
    field, value = payload["field"], payload["value"]
    if not isinstance(field, str) or field not in FIELD_SET:
        raise ValueError("INVALID_ENVELOPE")
    try:
        validate_field_value(field, value)
    except PlaylistRuleError:
        raise ValueError("INVALID_ENVELOPE") from None
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def validate_work_playlist(op):
    payload = op["payload"]
    if set(payload) != {"playlist_id"}:
        raise ValueError("INVALID_ENVELOPE")
    playlist_id = payload["playlist_id"]
    if not isinstance(playlist_id, str) or playlist_id != playlist_id.strip():
        raise ValueError("INVALID_ENVELOPE")
    if len(playlist_id) > MAX_TITLE_BYTES:
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def validate_order(op):
    payload = op["payload"]
    if set(payload) != {"work_ids"}:
        raise ValueError("INVALID_ENVELOPE")
    work_ids = payload["work_ids"]
    if not isinstance(work_ids, list) or len(work_ids) > MAX_ITEMS:
        raise ValueError("INVALID_ENVELOPE")
    for work_id in work_ids:
        if not isinstance(work_id, str) or not work_id.strip():
            raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def validate_delete(op):
    if op["payload"] != {}:
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")


