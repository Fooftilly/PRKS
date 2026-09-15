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

from backend import work_metadata_sync as meta
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


def _revision(conn, scope_type, scope_id):
    row = conn.execute(
        "SELECT revision FROM sync_entity_revisions WHERE scope_type = ? AND scope_id = ?",
        (scope_type, scope_id)).fetchone()
    return row[0] if row else 0


def get_revision(conn, playlist_id, field):
    return _revision(conn, FIELD_SCOPE_TYPE, scope_key(playlist_id, field))


def get_order_revision(conn, playlist_id):
    return _revision(conn, ORDER_SCOPE_TYPE, playlist_id)


def get_work_revision(conn, work_id):
    return _revision(conn, WORK_SCOPE_TYPE, work_id)


def _advance(conn, scope_type, scope_id):
    conn.execute(
        """INSERT INTO sync_entity_revisions (scope_type, scope_id, revision)
           VALUES (?, ?, 1) ON CONFLICT (scope_type, scope_id)
           DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP""",
        (scope_type, scope_id))


def current_field(conn, playlist_id, field):
    row = conn.execute(
        "SELECT %s FROM playlists WHERE id = ?" % field, (playlist_id,)).fetchone()
    if row is None:
        return None
    return "" if row[0] is None else str(row[0])


def set_field_on_conn(conn, playlist_id, field, value):
    """Write one playlist field and advance its revision together."""
    desired = validate_field_value(field, value)
    current = current_field(conn, playlist_id, field)
    if current is None:
        raise PlaylistRuleError("ENTITY_NOT_FOUND", "Playlist not found.")
    revision = get_revision(conn, playlist_id, field)
    if current == desired:
        return False, revision
    stored = (desired or None) if field == "original_url" else desired
    conn.execute(
        "UPDATE playlists SET %s = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?" % field,
        (stored, playlist_id))
    _advance(conn, FIELD_SCOPE_TYPE, scope_key(playlist_id, field))
    return True, revision + 1


def insert_playlist_on_conn(conn, playlist_id, title, description="", original_url=""):
    """CONSTRUCTION. A playlist created with a title and a description has not
    "changed" twice, so no field revision is advanced here."""
    clean_title = validate_field_value("title", title)
    clean_description = validate_field_value("description", description)
    clean_url = validate_field_value("original_url", original_url)
    conn.execute(
        "INSERT INTO playlists (id, title, description, original_url) VALUES (?, ?, ?, ?)",
        (playlist_id, clean_title, clean_description, clean_url or None))
    return playlist_id


def current_order(conn, playlist_id):
    return [row[0] for row in conn.execute(
        "SELECT work_id FROM playlist_items WHERE playlist_id = ? "
        "ORDER BY position ASC, work_id ASC", (playlist_id,))]


def current_work_playlist(conn, work_id):
    row = conn.execute(
        "SELECT playlist_id FROM playlist_items WHERE work_id = ? LIMIT 1",
        (work_id,)).fetchone()
    return "" if row is None else str(row[0])


def set_work_playlist_on_conn(conn, work_id, playlist_id):
    """ONE boundary for the handler and for the ordinary endpoints.

    A Work is in at most one playlist, so this is a SCALAR: adding, moving and
    removing are the same operation with different values. A video that LEAVES a
    playlist changes that playlist's order too, and so does one that joins --
    both orders advance, or a reorder made before the move would still look
    current.
    """
    desired = ("" if playlist_id is None else str(playlist_id)).strip()
    current = current_work_playlist(conn, work_id)
    if current == desired:
        return False
    if desired and conn.execute(
            "SELECT 1 FROM playlists WHERE id = ?", (desired,)).fetchone() is None:
        raise PlaylistRuleError("PLAYLIST_NOT_FOUND", "Playlist not found.")
    conn.execute("DELETE FROM playlist_items WHERE work_id = ?", (work_id,))
    if current:
        _advance(conn, ORDER_SCOPE_TYPE, current)
        conn.execute("UPDATE playlists SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (current,))
    if desired:
        row = conn.execute(
            "SELECT COALESCE(MAX(position), -1) AS m FROM playlist_items WHERE playlist_id = ?",
            (desired,)).fetchone()
        conn.execute(
            "INSERT INTO playlist_items (playlist_id, work_id, position) VALUES (?, ?, ?)",
            (desired, work_id, int(row[0]) + 1))
        _advance(conn, ORDER_SCOPE_TYPE, desired)
        conn.execute("UPDATE playlists SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (desired,))
    _advance(conn, WORK_SCOPE_TYPE, work_id)
    return True


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


def set_order_on_conn(conn, playlist_id, work_ids):
    """Rewrite the whole order, keeping membership exactly as it is."""
    if conn.execute("SELECT 1 FROM playlists WHERE id = ?", (playlist_id,)).fetchone() is None:
        raise PlaylistRuleError("ENTITY_NOT_FOUND", "Playlist not found.")
    present = current_order(conn, playlist_id)
    order = resolve_order(present, work_ids)
    if order == present:
        return False
    for index, work_id in enumerate(order):
        conn.execute(
            "UPDATE playlist_items SET position = ? WHERE playlist_id = ? AND work_id = ?",
            (index, playlist_id, work_id))
    _advance(conn, ORDER_SCOPE_TYPE, playlist_id)
    conn.execute("UPDATE playlists SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                 (playlist_id,))
    return True


def delete_playlist_on_conn(conn, playlist_id):
    """Remove a playlist. Its items go with it -- they are memberships, not the
    videos themselves -- so every member Work's own membership revision
    advances: a device holding "this video is in that playlist" has to be able
    to discover it was overtaken."""
    if conn.execute("SELECT 1 FROM playlists WHERE id = ?", (playlist_id,)).fetchone() is None:
        return False
    for work_id in current_order(conn, playlist_id):
        _advance(conn, WORK_SCOPE_TYPE, work_id)
    _advance(conn, ORDER_SCOPE_TYPE, playlist_id)
    conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
    return True


def get_playlist_state_on_conn(conn, playlist_id):
    """Field revisions and the ORDER revision: revisions only.

    The playlist detail already carries its items in order, so echoing them
    here would make a second cached copy of the list.
    """
    if conn.execute("SELECT 1 FROM playlists WHERE id = ?", (playlist_id,)).fetchone() is None:
        return None
    by_key = {scope_key(playlist_id, field): field for field in FIELDS}
    placeholders = ", ".join("?" * len(by_key))
    revisions = {
        by_key[scope_id]: revision
        for scope_id, revision in conn.execute(
            "SELECT scope_id, revision FROM sync_entity_revisions "
            "WHERE scope_type = ? AND scope_id IN (%s)" % placeholders,
            (FIELD_SCOPE_TYPE,) + tuple(by_key)).fetchall()
    }
    return {
        "playlist_id": playlist_id,
        "fields": {field: {"revision": revisions.get(field, 0)} for field in sorted(FIELDS)},
        "order_revision": get_order_revision(conn, playlist_id),
    }


def get_work_playlist_state_on_conn(conn, work_id):
    if conn.execute("SELECT 1 FROM works WHERE id = ?", (work_id,)).fetchone() is None:
        return None
    return {"work_id": work_id, "playlist_id": current_work_playlist(conn, work_id),
            "revision": get_work_revision(conn, work_id)}


# ---- handlers -------------------------------------------------------------

def _rule_status(code):
    return 404 if code in ("ENTITY_NOT_FOUND", "PLAYLIST_NOT_FOUND") else 409


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


def _playlist_row(conn, playlist_id):
    row = conn.execute(
        "SELECT id, title, description, original_url FROM playlists WHERE id = ?",
        (playlist_id,)).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["item_count"] = conn.execute(
        "SELECT COUNT(*) FROM playlist_items WHERE playlist_id = ?",
        (playlist_id,)).fetchone()[0]
    return data


def apply_create(db, conn, op, received_at):
    playlist_id = op["entity_id"]
    payload = op["payload"]
    existing = conn.execute(
        "SELECT 1 FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
    if existing is None:
        insert_playlist_on_conn(conn, playlist_id, payload["title"], payload["description"],
                                payload["original_url"])
    return 200, {"code": "ACKNOWLEDGED", "playlist_id": playlist_id,
                 "changed": existing is None, "playlist": _playlist_row(conn, playlist_id)}


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


def apply_field(db, conn, op, received_at):
    playlist_id = op["entity_id"]
    field = op["payload"]["field"]
    desired = canonical_wire(field, op["payload"]["value"])
    result = {"playlist_id": playlist_id, "field": field}
    current = current_field(conn, playlist_id, field)
    if current is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    revision = get_revision(conn, playlist_id, field)
    base = op["base_revision"]
    if base > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 400, meta.fit_terminal_result(result, current, desired)
    if base < revision and current != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 409, meta.fit_terminal_result(result, current, desired)
    changed, after = set_field_on_conn(conn, playlist_id, field, desired)
    result.update(code="ACKNOWLEDGED", server_revision=after, changed=changed,
                  value_omitted=True)
    return 200, result


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


def apply_work_playlist(db, conn, op, received_at):
    work_id = op["entity_id"]
    desired = op["payload"]["playlist_id"]
    result = {"work_id": work_id}
    if conn.execute("SELECT 1 FROM works WHERE id = ?", (work_id,)).fetchone() is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    revision = get_work_revision(conn, work_id)
    current = current_work_playlist(conn, work_id)
    base = op["base_revision"]
    if base > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 400, result
    if base < revision and current != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_value=current, requested_value=desired)
        return 409, result
    try:
        changed = set_work_playlist_on_conn(conn, work_id, desired)
    except PlaylistRuleError as error:
        result.update(code=error.code, current_revision=revision)
        return _rule_status(error.code), result
    result.update(code="ACKNOWLEDGED", changed=changed, playlist_id=desired,
                  server_revision=revision + int(changed))
    if desired:
        row = conn.execute("SELECT title FROM playlists WHERE id = ?", (desired,)).fetchone()
        result["playlist_title"] = (row[0] if row is not None else "") or ""
    else:
        result["playlist_title"] = ""
    return 200, result


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


def apply_order(db, conn, op, received_at):
    playlist_id = op["entity_id"]
    desired = op["payload"]["work_ids"]
    result = {"playlist_id": playlist_id}
    if conn.execute("SELECT 1 FROM playlists WHERE id = ?", (playlist_id,)).fetchone() is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    revision = get_order_revision(conn, playlist_id)
    present = current_order(conn, playlist_id)
    base = op["base_revision"]
    resolved = resolve_order(present, desired)
    if base > revision:
        # The conflict carries COUNTS, never the two orders: a long playlist's
        # ids would not fit the client's durable result bound, and the client
        # re-reads the playlist to show what the server has.
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      current_count=len(present), requested_count=len(resolved))
        return 400, result
    if base < revision and resolved != present:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      current_count=len(present), requested_count=len(resolved))
        return 409, result
    changed = set_order_on_conn(conn, playlist_id, desired)
    result.update(code="ACKNOWLEDGED", changed=changed,
                  server_revision=revision + int(changed))
    # The resulting order, so the client can reorder its cached playlist IN
    # PLACE instead of dropping the page it has just reordered -- but only
    # while it is small enough to be worth carrying in every acknowledgement
    # and in the ledger. Past that the client re-reads the playlist, which is
    # exactly what it does when the field is absent.
    final = current_order(conn, playlist_id)
    if len(final) <= MAX_ECHOED_ITEMS:
        result["work_ids"] = final
    return 200, result


def validate_delete(op):
    if op["payload"] != {}:
        raise ValueError("INVALID_ENVELOPE")
    if op["base_revision"] is not None:
        raise ValueError("INVALID_BASE_REVISION")


def apply_delete(db, conn, op, received_at):
    playlist_id = op["entity_id"]
    existed = conn.execute(
        "SELECT 1 FROM playlists WHERE id = ?", (playlist_id,)).fetchone() is not None
    deleted = delete_playlist_on_conn(conn, playlist_id)
    return 200, {"code": "ACKNOWLEDGED", "playlist_id": playlist_id,
                 "changed": bool(deleted and existed)}


def _handler(validate_fn, apply_fn):
    return type("_Handler", (), {"validate": staticmethod(validate_fn),
                                 "apply": staticmethod(apply_fn)})()


CREATE_HANDLER = _handler(validate_create, apply_create)
FIELD_HANDLER = _handler(validate_field, apply_field)
WORK_PLAYLIST_HANDLER = _handler(validate_work_playlist, apply_work_playlist)
ORDER_HANDLER = _handler(validate_order, apply_order)
DELETE_HANDLER = _handler(validate_delete, apply_delete)
