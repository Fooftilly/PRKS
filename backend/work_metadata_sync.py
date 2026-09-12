"""Work metadata fields: per-FIELD revisions and the SET_WORK_METADATA_FIELD handler.

The conflict unit here is one field, not one Work. Two devices editing `doi`
and `isbn` on the same Work have not disagreed about anything, and a single
Work-level revision would tell them they had -- forcing a resolution UI over a
conflict that does not exist. So each supported field carries its own revision
scope and they advance independently.

Nine scalars are synchronized. Eight of them reach no cached read model but the
Work detail: the Work summary projection carries them, yet Work cards, browse
catalogs, Folders, People, Playlists and the Graph display none of them.

`publisher` is the exception, and the reason it is here. `recently-added:index`
carries it because Home -> Recently Added filters LOCALLY over it, so a pending
publisher has to reach that projection's filtering even though no card renders
it. Being invisible is not the same as being unused. Higher fan-out fields
(title, status, doc_type, year, abstract) are a separate problem and are
deliberately not here.

Nothing in this module imports the database layer; the handler is handed the
`db` it needs, so the ordinary PATCH boundary can share the same helpers.
"""
import json

# Field -> maximum accepted length. The bound is the only validation this layer
# adds: synchronization is not a licence to start normalizing values PRKS never
# normalized. A DOI keeps its case, an ISBN keeps its punctuation, and a page
# range is not parsed -- the sync path must store exactly what a PATCH would.
# Abstract is a bibliographic summary, and 1 MiB is already thousands of times
# any real one -- long-form material belongs in Research Notes, which has its
# own storage. The number is a deliberate PRKS product rule, not a measurement:
# it is small enough that every `listOperations()`, diagnostics render and
# coordinator pass stays cheap, and large enough that no genuine abstract can
# reach it. The same limit is enforced on the ordinary PATCH, on the sync
# operation, in the durable local store and in the editor before enqueue --
# one contract, so a value can never be savable by one path and refused by
# another.
MAX_ABSTRACT_UTF8_BYTES = 1024 * 1024

SYNCED_FIELDS = {
    "abstract": MAX_ABSTRACT_UTF8_BYTES,
    "edition": 200,
    "journal": 500,
    "volume": 100,
    "issue": 100,
    "pages": 200,
    "isbn": 100,
    "doi": 500,
    "publisher": 500,
    "location": 500,
}

# Cached projections that carry a synchronized field and therefore have to be
# reconciled when it is acknowledged. Absent means "the Work detail only".
# `recently-added:index` selects `publisher` for its local filter, so this is a
# real dependency even though no Work card renders the value.
# Abstract's limit is measured in UTF-8 BYTES; the eight small scalars keep the
# code-point limits they have had since 2D. The distinction is deliberate, not
# an oversight. Abstract's bound exists to keep a durable operation and every
# read of it cheap, which is a storage and transport concern and therefore a
# byte concern. The others bound how much text a one-line field may hold, which
# is a display concern -- and switching them to bytes would quietly shorten
# every one of them by a factor of three for anyone writing CJK.
BYTE_LIMITED_FIELDS = frozenset({"abstract"})

# A conflict result is persisted in the browser's durable operation row, which
# bounds a structured result to 2 KB. Echoing two megabyte-scale Abstracts into
# it would mean the client could not store the conflict at all -- the operation
# would fail to settle rather than reach the user. So a byte-limited field
# reports bounded PREVIEWS and sizes instead, enough to show the user what the
# disagreement is; taking the server's version re-reads the authoritative value
# rather than trusting a truncated copy.
CONFLICT_PREVIEW_CHARS = 400


def preview(value):
    text = canonical(value)
    return text[:CONFLICT_PREVIEW_CHARS]


FIELD_PROJECTIONS = {
    "publisher": ("recently-added",),
    # DERIVED, unlike publisher: what reaches `works-browse:index` is not the
    # abstract but its first 100 code points, which Progress renders.
    "abstract": ("works-browse",),
}


def scope_key(work_id, field):
    # Structural encoding, like the Work-Tag scope: no delimiter has to be
    # excluded from either component for this to stay unambiguous.
    return json.dumps([work_id, field], ensure_ascii=True, separators=(",", ":"))


def within_limit(field, value):
    limit = SYNCED_FIELDS[field]
    text = canonical(value)
    if field not in BYTE_LIMITED_FIELDS:
        return len(text) <= limit
    # No string exceeds a byte limit without having at least limit/4
    # characters, so ordinary values never pay for the encode.
    if len(text) * 4 <= limit:
        return True
    return len(text.encode("utf-8")) <= limit


def canonical(value):
    """The one representation of a field value.

    SQLite holds NULL for rows created before a field existed and "" for one a
    user cleared; both mean "no value", so they compare equal. Whitespace is
    NOT stripped -- that would make the sync path disagree with PATCH about
    what the user typed, and the column has never been normalized that way.
    """
    return "" if value is None else str(value)


def get_field_state_on_conn(conn, work_id):
    """Every supported field's canonical value and revision, or None.

    A field with no revision row has never been changed since revisions
    existed, which is revision 0 -- the same "missing means zero" rule the
    Work-Tag scopes use.
    """
    # Byte-limited fields contribute their REVISION only. The Work record
    # already carries the acknowledged Abstract, and echoing up to a megabyte
    # of it into a second cached projection would double what this endpoint
    # sends, what IndexedDB stores and what every re-read costs -- for a value
    # the client already has. Small scalars stay inline; there is nothing to
    # save by splitting them.
    valued = sorted(set(SYNCED_FIELDS) - BYTE_LIMITED_FIELDS)
    columns = ", ".join(valued) if valued else "id"
    row = conn.execute("SELECT %s FROM works WHERE id = ?" % columns, (work_id,)).fetchone()
    if row is None:
        return None
    # Ask for this Work's own scopes by key rather than scanning every
    # `work-field` row in the library and discarding the ones that belong to
    # other Works: the cost of that scan grows with the library, while the
    # answer never does. `(scope_type, scope_id)` is the primary key, so this
    # is an indexed lookup of at most nine rows.
    by_key = {scope_key(work_id, field): field for field in SYNCED_FIELDS}
    placeholders = ", ".join("?" * len(by_key))
    revisions = {
        by_key[scope_id]: revision
        for scope_id, revision in conn.execute(
            "SELECT scope_id, revision FROM sync_entity_revisions "
            "WHERE scope_type = 'work-field' AND scope_id IN (%s)" % placeholders,
            tuple(by_key),
        ).fetchall()
    }
    fields = {}
    for field in sorted(SYNCED_FIELDS):
        entry = {"revision": revisions.get(field, 0)}
        if field not in BYTE_LIMITED_FIELDS:
            entry["value"] = canonical(row[field])
        fields[field] = entry
    return {"work_id": work_id, "fields": fields}


def get_revision(conn, work_id, field):
    row = conn.execute(
        "SELECT revision FROM sync_entity_revisions WHERE scope_type = 'work-field' AND scope_id = ?",
        (scope_key(work_id, field),)).fetchone()
    return row[0] if row else 0


def set_field_on_conn(conn, work_id, field, value):
    """Write one field and advance its revision together, in the caller's
    transaction. Returns (changed, revision_after).

    A no-op write advances nothing: a revision is a record of the value
    actually changing, and inflating it would manufacture staleness for every
    device that already holds the current value.
    """
    if field not in SYNCED_FIELDS:
        raise ValueError("unsupported synchronized field: %s" % field)
    desired = canonical(value)
    row = conn.execute("SELECT %s FROM works WHERE id = ?" % field, (work_id,)).fetchone()
    if row is None:
        return False, 0
    revision = get_revision(conn, work_id, field)
    if canonical(row[0]) == desired:
        return False, revision
    conn.execute(
        "UPDATE works SET %s = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?" % field,
        (desired, work_id))
    conn.execute("""INSERT INTO sync_entity_revisions (scope_type, scope_id, revision)
                    VALUES ('work-field', ?, 1) ON CONFLICT (scope_type, scope_id)
                    DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP""",
                 (scope_key(work_id, field),))
    return True, revision + 1


def validate(op):
    payload = op["payload"]
    if set(payload) != {"field", "value"}:
        raise ValueError("INVALID_ENVELOPE")
    field, value = payload["field"], payload["value"]
    # An arbitrary column name from a client would be an injection surface and
    # a way to reach fields this milestone deliberately does not synchronize.
    if not isinstance(field, str) or field not in SYNCED_FIELDS:
        raise ValueError("INVALID_ENVELOPE")
    if not isinstance(value, str) or not within_limit(field, value):
        raise ValueError("INVALID_ENVELOPE")
    # A scalar edit is optimistic-concurrency controlled: a null base revision
    # is a client that cannot detect a conflict, which would silently overwrite
    # whatever another device wrote.
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


def disagreement(field, current, desired):
    """What the two sides hold, in a form the client can durably store."""
    if field not in BYTE_LIMITED_FIELDS:
        return {"current_value": current, "requested_value": desired}
    return {
        "current_preview": preview(current),
        "current_bytes": len(current.encode("utf-8")),
        "requested_bytes": len(desired.encode("utf-8")),
    }


def apply(db, conn, op, received_at):
    work_id = op["entity_id"]
    field, desired = op["payload"]["field"], op["payload"]["value"]
    result = {"work_id": work_id, "field": field}
    row = conn.execute("SELECT %s FROM works WHERE id = ?" % field, (work_id,)).fetchone()
    if row is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    current = canonical(row[0])
    revision = get_revision(conn, work_id, field)
    base = op["base_revision"]
    if base > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      **disagreement(field, current, desired))
        return 400, result
    # A stale base is only a conflict when the two devices actually disagree.
    # Two people typing the same DOI have converged, not collided.
    if base < revision and current != desired:
        result.update(code="REVISION_CONFLICT", current_revision=revision,
                      **disagreement(field, current, desired))
        return 409, result
    changed, after = set_field_on_conn(conn, work_id, field, desired)
    result.update(code="ACKNOWLEDGED", value=desired, server_revision=after, changed=changed)
    return 200, result


class _Handler:
    validate = staticmethod(validate)
    apply = staticmethod(apply)


HANDLER = _Handler()
