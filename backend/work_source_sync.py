"""Work SOURCE identity: an aggregate conflict unit and the SET_WORK_SOURCE handler.

Every other synchronized Work value is a FIELD. Source identity is not: what a
video Work *is* spans `source_kind`, `provider`, `provider_id` and
`source_url`, and `provider_id` outranks the URL when the viewer builds its
embed. Three field-scoped operations would therefore let two ordinary edits
reach "the stored URL names video B while the viewer plays video A", and would
demand three resolutions for one decision nobody made three times.

So the conflict unit is the whole source, scoped `work-source / <work id>`:

    one user decision  ->  one operation  ->  one revision  ->  one conflict

The payload carries INTENT, not columns. `provider` and `provider_id` are
DERIVED here, inside the mutation boundary, from the same parser Work creation
uses -- a client that could assert them would be able to assert an identity its
own URL contradicts, which is the defect this operation exists to prevent.

Canonical identity is `provider` + `provider_id`, never the URL spelling.
`youtube.com/watch?v=A`, `youtu.be/A` and `youtube.com/embed/A` are ONE source,
so moving between them is a no-op: no revision, no conflict, no write.

See docs/work-source-identity.md for the audit this design came from.
"""
import json
from urllib.parse import parse_qs, urlparse

# Explicit recognized YouTube hostnames. Never substring-matched: that would
# accept "notyoutube.com" and "youtube.com.example.org". Shared contract with
# the frontend's prksIsRecognizedYoutubeHost() and works-video.js.
YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})

# The only provider PRKS supports today. Kept as a set so adding one is a
# registry entry and a parser, not a new branch in every caller.
SUPPORTED_PROVIDERS = frozenset({"youtube"})

SCOPE_TYPE = "work-source"


def youtube_host(netloc):
    host = (netloc or "").lower()
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    if ":" in host:
        host = host.rsplit(":", 1)[0]
    return host


def is_youtube_host(netloc):
    return youtube_host(netloc) in YOUTUBE_HOSTS


def youtube_video_id(url):
    """The video id in any URL spelling PRKS accepts, or None.

    One parser for Work creation and for source synchronization. A second one
    would eventually disagree with this, and the disagreement would show up as
    a Work whose stored URL and stored id name different videos.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    host = youtube_host(parsed.netloc)
    if not is_youtube_host(host):
        return None
    if host == "youtu.be":
        vid = (parsed.path or "").strip("/").split("/")[0].strip()
        return vid or None
    query = parse_qs(parsed.query or "")
    vid = (query.get("v") or [""])[0].strip()
    if vid:
        return vid
    parts = (parsed.path or "").strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "embed" and parts[1].strip():
        return parts[1].strip()
    return None


def validate_youtube_url(url):
    """The video id for a supported YouTube URL, else None."""
    text = str(url or "").strip()
    if not text:
        return None
    try:
        parsed = urlparse(text)
    except Exception:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if not is_youtube_host(parsed.netloc):
        return None
    return youtube_video_id(text)


def scope_key(work_id):
    # Structural encoding, like every other scope: no delimiter has to be
    # excluded from a Work id for this to stay unambiguous.
    return json.dumps([work_id], ensure_ascii=True, separators=(",", ":"))


# A URL is one line of text; the bound exists so the field has a contract
# rather than inheriting whichever storage layer refuses first.
MAX_SOURCE_URL_UTF8_BYTES = 64 * 1024


def canonical_source(payload_source):
    """User intent -> the canonical identity, or None when it is not one.

    Returns the columns the aggregate owns. `provider` and `provider_id` are
    derived here and never accepted from the client.
    """
    if not isinstance(payload_source, dict):
        return None
    if set(payload_source) != {"kind", "url"}:
        return None
    kind, url = payload_source.get("kind"), payload_source.get("url")
    if kind != "video" or not isinstance(url, str):
        return None
    if len(url.encode("utf-8")) > MAX_SOURCE_URL_UTF8_BYTES:
        return None
    video_id = validate_youtube_url(url)
    if not video_id:
        return None
    return {
        "source_kind": "video",
        "provider": "youtube",
        "provider_id": video_id,
        "source_url": url.strip(),
    }


def identity_of(source):
    """What makes two sources THE SAME source.

    Deliberately not the URL: three spellings name one video, and telling a
    user they collided because they pasted a share link rather than a watch
    link would be inventing a disagreement.
    """
    return (source["provider"], source["provider_id"])


def current_source(conn, work_id):
    row = conn.execute(
        "SELECT source_kind, provider, provider_id, source_url FROM works WHERE id = ?",
        (work_id,)).fetchone()
    if row is None:
        return None
    return {
        "source_kind": (row[0] or "").strip().lower(),
        "provider": (row[1] or "").strip().lower(),
        "provider_id": (row[2] or "").strip(),
        "source_url": row[3] or "",
    }


# Presentation columns `set_source_on_conn` also rewrites. They are not
# identity, but they belong to the same decision: a row claiming video B while
# serving video A's thumbnail is a lie the user can see, and `urldate` is the
# access date of the source that is there now.
DERIVED_COLUMNS = ("thumb_url", "urldate")

# The columns that MAY NOT be written independently on an existing Work. They
# are not a list of "source-ish" names: each one either carries identity
# (`source_kind`, `provider`, `provider_id`) or is derived from it
# (`thumb_url`), so writing any of them alone can make the row describe a
# different video than it plays. `source_url` is absent because the
# field-scoped registry already guards it per Work kind -- on a PDF it is
# provenance and stays editable, on a video it is refused there.
SOURCE_AGGREGATE_COLUMNS = frozenset(
    {"source_kind", "provider", "provider_id", "thumb_url"})


def stored_source(conn, work_id):
    """Every column this aggregate owns, exactly as the row holds it.

    This is what an acknowledgement reports. The alternative -- letting the
    client rebuild the stored state from its own operation -- is only correct
    while the two agree, and the case where they DO NOT is precisely the
    interesting one: a convergent write stores nothing, so a client
    reconstructing "what I asked for" would publish a value the server does not
    have. Reading the row back costs one query and cannot be wrong.
    """
    row = conn.execute(
        "SELECT source_kind, provider, provider_id, source_url, thumb_url, urldate "
        "FROM works WHERE id = ?", (work_id,)).fetchone()
    if row is None:
        return None
    return {
        "source_kind": (row[0] or "").strip().lower(),
        "provider": (row[1] or "").strip().lower(),
        "provider_id": (row[2] or "").strip(),
        "source_url": row[3] or "",
        "thumb_url": row[4],
        "urldate": row[5],
    }


def get_revision(conn, work_id):
    row = conn.execute(
        "SELECT revision FROM sync_entity_revisions WHERE scope_type = ? AND scope_id = ?",
        (SCOPE_TYPE, scope_key(work_id))).fetchone()
    return row[0] if row else 0


def get_source_state(db, work_id):
    """The synchronization bookkeeping for one Work's source.

    A REVISION ONLY. The Work record already carries every canonical source
    value, and duplicating a URL into a second cached projection would double
    what this endpoint sends, what IndexedDB stores and what every re-read
    costs -- for values the client already holds.
    """
    with db.connection() as conn:
        row = conn.execute("SELECT id FROM works WHERE id = ?", (work_id,)).fetchone()
        if row is None:
            return None
        return {"work_id": work_id, "revision": get_revision(conn, work_id)}


def set_source_on_conn(conn, work_id, source):
    """Write the whole identity and advance its revision, in one transaction.

    Returns (changed, revision_after). A no-op advances nothing: a revision
    records the source actually becoming a different source, and inflating it
    would manufacture staleness for every device already holding this one.

    `thumb_url` is PRESENTATION, not identity -- but it is the previous video's
    image, and a row claiming video B while serving video A's thumbnail is a
    lie the user can see. It is cleared rather than re-derived, because
    re-deriving means a network call and no canonical mutation may depend on
    one: the enrichment can fail, and a failed image fetch must never fail a
    source change. The next oEmbed refresh fills it in.
    """
    existing = current_source(conn, work_id)
    if existing is None:
        return False, 0
    revision = get_revision(conn, work_id)
    if existing["source_kind"] == source["source_kind"] and \
            identity_of(existing) == identity_of(source):
        # The same video. A different URL SPELLING is not a change, and must
        # not rewrite the column either -- that would be a write with no
        # revision, which is exactly what makes another device's staleness
        # check lie.
        return False, revision
    conn.execute(
        "UPDATE works SET source_kind = ?, provider = ?, provider_id = ?, source_url = ?, "
        "thumb_url = NULL, urldate = DATE('now'), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (source["source_kind"], source["provider"], source["provider_id"],
         source["source_url"], work_id))
    conn.execute("""INSERT INTO sync_entity_revisions (scope_type, scope_id, revision)
                    VALUES (?, ?, 1) ON CONFLICT (scope_type, scope_id)
                    DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP""",
                 (SCOPE_TYPE, scope_key(work_id)))
    return True, revision + 1


def validate(op):
    payload = op["payload"]
    if set(payload) != {"source"}:
        raise ValueError("INVALID_ENVELOPE")
    if canonical_source(payload["source"]) is None:
        raise ValueError("INVALID_ENVELOPE")
    # Optimistic concurrency: a null base revision is a client that cannot
    # detect a conflict, and would silently overwrite whatever another device
    # decided this Work's source was.
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


# Two URLs can each be far larger than the browser's 2 KB durable result
# bound, so a conflict reports bounded previews and sizes rather than the
# values -- the client already holds its own URL in its immutable operation.
CONFLICT_PREVIEW_CHARS = 400


def disagreement(current, desired):
    from backend import work_metadata_sync as meta
    return {
        "current_preview": meta.preview(current["source_url"])[:CONFLICT_PREVIEW_CHARS],
        "current_bytes": len(current["source_url"].encode("utf-8")),
        "requested_bytes": len(desired["source_url"].encode("utf-8")),
    }


def apply(db, conn, op, received_at):
    from backend import work_metadata_sync as meta
    work_id = op["entity_id"]
    desired = canonical_source(op["payload"]["source"])
    result = {"work_id": work_id}
    existing = current_source(conn, work_id)
    if existing is None:
        result["code"] = "ENTITY_NOT_FOUND"
        return 404, result
    # Only an existing VIDEO Work may have its source replaced. Turning a PDF
    # into a video, or the reverse, is a different decision with different
    # consequences for `file_path` and for which viewer renders -- there is no
    # UI for it and no defined product semantics, so it is refused rather than
    # invented. See docs/work-source-identity.md.
    if existing["source_kind"] != "video":
        result["code"] = "UNSUPPORTED_SOURCE_TRANSITION"
        return 409, result
    revision = get_revision(conn, work_id)
    base = op["base_revision"]
    if base > revision:
        result.update(code="FUTURE_REVISION", current_revision=revision,
                      **disagreement(existing, desired))
        return 400, meta.fit_terminal_result(
            result, existing["source_url"], desired["source_url"])
    # A stale base is only a conflict when the two devices chose DIFFERENT
    # videos. Two people who pasted the same one have converged, whichever
    # spelling each of them used.
    if base < revision and identity_of(existing) != identity_of(desired):
        result.update(code="SOURCE_REVISION_CONFLICT", current_revision=revision,
                      **disagreement(existing, desired))
        return 409, meta.fit_terminal_result(
            result, existing["source_url"], desired["source_url"])
    changed, after = set_source_on_conn(conn, work_id, desired)
    # The acknowledgement STATES the stored row rather than describing the
    # request, and the client copies it rather than deriving anything.
    #
    # Two of these columns the client CANNOT derive: `urldate` is the server's
    # own date and `thumb_url` is cleared by the write. And on a convergent
    # write -- same video, different spelling, so nothing is stored -- a client
    # reconstructing "what I asked for" would publish a `source_url` the server
    # does not have, and worse, would keep whatever identity it last cached
    # while the server has moved to the one it converged on.
    #
    # Echoing the URL does put a second copy of it in the ledger, which holds
    # only a request HASH otherwise. That is a real cost, accepted knowingly:
    # the value is bounded by MAX_SOURCE_URL_UTF8_BYTES, one row per operation,
    # and the alternative is a client that publishes acknowledged values the
    # server never stored.
    result.update(code="ACKNOWLEDGED", server_revision=after, changed=changed,
                  **stored_source(conn, work_id))
    return 200, result


class _Handler:
    validate = staticmethod(validate)
    apply = staticmethod(apply)


HANDLER = _Handler()
