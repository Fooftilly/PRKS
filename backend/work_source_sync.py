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
import re
from urllib.parse import parse_qs, urlparse

# Explicit recognized YouTube hostnames. Never substring-matched: that would
# accept "notyoutube.com" and "youtube.com.example.org". Shared contract with
# the frontend's prksIsRecognizedYoutubeHost() and video-embed.ts.
YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})


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


# The canonical provider-id contract, enforced by the one parser below.
#
# `provider_id` is an IDENTIFIER, and this is what makes it one: a bounded
# token, not "whatever sat after v=". Two promises depend on it being bounded,
# and they are only simultaneously keepable if it is:
#
#   * a conflict reports the server's identity EXACTLY, never shortened,
#     because a truncated video id names a different video (or none);
#   * a terminal result always fits the client's durable 2 KiB bound, so a
#     conflict can always be stored and therefore always be resolved.
#
# Unbounded, those contradict. A 3000-character id produced a ~3.2 KB conflict
# that stayed over the limit with the preview deleted entirely, so the
# acknowledgement could neither be stored nor surfaced. 512 is far above any
# real id -- YouTube's are 11 characters -- and leaves ample margin under
# 2 KiB. The alphabet is the URL-safe base64 set YouTube actually uses; it also
# settles percent-encoding, since `%` is not in it, so a spelling one parser
# decodes and the other does not is refused by both.
MAX_PROVIDER_ID_CHARS = 512
_PROVIDER_ID_RE = re.compile(r"\A[A-Za-z0-9_-]{1,%d}\Z" % MAX_PROVIDER_ID_CHARS)


def is_provider_id(value):
    """True for a value this system is willing to call a video identity."""
    return isinstance(value, str) and bool(_PROVIDER_ID_RE.match(value))


def youtube_video_id(url):
    """The video id in any URL spelling PRKS accepts, or None.

    One parser for Work creation and for source synchronization. A second one
    would eventually disagree with this, and the disagreement would show up as
    a Work whose stored URL and stored id name different videos.

    A URL whose id is not a well-formed identifier has no video id here -- it
    is refused rather than stored, so no Work can carry an identity the
    protocol cannot report back intact.
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
        return vid if is_provider_id(vid) else None
    query = parse_qs(parsed.query or "")
    vid = (query.get("v") or [""])[0].strip()
    if vid:
        return vid if is_provider_id(vid) else None
    parts = (parsed.path or "").strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "embed" and parts[1].strip():
        vid = parts[1].strip()
        return vid if is_provider_id(vid) else None
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
    # Trim first, then measure what would actually be STORED. Measuring the raw
    # input rejected a URL whose canonical form fits, purely for surrounding
    # whitespace the product removes -- and the client, which mirrors this, has
    # to be able to reach the same answer from the same rule.
    text = url.strip()
    if len(text.encode("utf-8")) > MAX_SOURCE_URL_UTF8_BYTES:
        return None
    video_id = validate_youtube_url(text)
    if not video_id:
        return None
    return {
        "source_kind": "video",
        "provider": "youtube",
        "provider_id": video_id,
        "source_url": text,
    }


def identity_of(source):
    """What makes two sources THE SAME source.

    Deliberately not the URL: three spellings name one video, and telling a
    user they collided because they pasted a share link rather than a watch
    link would be inventing a disagreement.
    """
    return (source["provider"], source["provider_id"])


# A stored row that the product treats as a video but whose identity cannot be
# established -- no usable provider fields AND a `source_url` today's parser
# refuses. Nothing is guessed or repaired: an id invented from an unparseable
# URL would be a claim about which video this is, and a stored id too large or
# malformed to report back would make every later conflict undeliverable.
INVALID_SOURCE_STATE = "INVALID_SOURCE_STATE"


# The columns that MAY NOT be written independently on an existing Work. They
# are not a list of "source-ish" names: each one either carries identity
# (`source_kind`, `provider`, `provider_id`) or is derived from it
# (`thumb_url`), so writing any of them alone can make the row describe a
# different video than it plays. `source_url` is absent because the
# field-scoped registry already guards it per Work kind -- on a PDF it is
# provenance and stays editable, on a video it is refused there.
SOURCE_AGGREGATE_COLUMNS = frozenset(
    {"source_kind", "provider", "provider_id", "thumb_url"})


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
    """What the two devices disagree about, in a form the client can act on.

    The URL is reported as a BOUNDED PREVIEW, so it is display text and nothing
    may be derived from it -- `fit_terminal_result` is free to shorten it
    further to keep the whole object inside the client's durable result limit.

    The IDENTITY is reported exactly and is never shortened: a truncated video
    id names a different video, or none. A client that resolves the conflict by
    reapplying its own choice needs the server's identity to measure its NEXT
    edit against -- without it the editor keeps comparing against the
    pre-conflict video, and returning to the server's video reads as a change
    rather than as a cancellation.

    Reporting it exactly is only POSSIBLE because identity is canonically
    bounded: `MAX_PROVIDER_ID_CHARS` over a fixed alphabet, enforced in the
    parser where an id comes into existence. "Never truncated" and "the whole
    result always fits the client's durable limit" are not independently
    grantable promises -- an unbounded id made them contradict, and a conflict
    the client cannot store is one the user can never resolve.
    """
    from backend import work_metadata_sync as meta
    return {
        "current_provider": current["provider"],
        "current_provider_id": current["provider_id"],
        "current_preview": meta.preview(current["source_url"])[:CONFLICT_PREVIEW_CHARS],
        "current_bytes": len(current["source_url"].encode("utf-8")),
        "requested_bytes": len(desired["source_url"].encode("utf-8")),
    }


