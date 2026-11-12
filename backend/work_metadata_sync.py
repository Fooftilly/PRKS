"""Work metadata fields: per-FIELD revisions and the SET_WORK_METADATA_FIELD handler.

The conflict unit here is one field, not one Work. Two devices editing `doi`
and `isbn` on the same Work have not disagreed about anything, and a single
Work-level revision would tell them they had -- forcing a resolution UI over a
conflict that does not exist. So each supported field carries its own revision
scope and they advance independently.

`SYNCED_FIELDS` is the authoritative registry of which fields synchronize;
`FIELD_PROJECTIONS` and `SUMMARY_FIELDS` say which cached read models each one
reaches. Most reach no cached read model but the Work detail: the Work summary
projection carries them, yet no Work card, browse catalog, Folder, Person,
Playlist or Graph surface displays them.

The exceptions are exceptions in different ways, and each one cost a milestone.
`recently-added:index` carries `publisher` because Home -> Recently Added
filters LOCALLY over it, so a pending publisher has to reach that projection's
filtering even though no card renders it -- being invisible is not the same as
being unused. `works-browse:index` carries `abstract_excerpt`, which Progress
renders, so a pending Abstract reaches it through a DERIVATION rather than a
copy. `abstract` and `author_text` are the BYTE_LIMITS fields: large enough
that their bound is a storage concern rather than a display one, so they are
measured in UTF-8 bytes, acknowledged without echoing the value back, and
carried by the metadata-state projection as a revision alone (see BYTE_LIMITS). `status` decides which GROUP a Work occupies on
Progress, so a pending value moves a card between lists rather than rewriting
text in it. `author_text` is the field whose stored value is not necessarily
its displayed value: a linked Author outranks it, and a linked Editor stands in
when it is empty, so this module synchronizes the FIELD and the existing credit
helper decides what becomes visible.

Fields deliberately still outside the registry are listed in
docs/local-first-sync.md; the registry above is what decides, not this
paragraph.

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

# `author_text` is an Author or Channel name, so 64 KiB is absurdly generous
# for it -- which is the point. The number exists to make the field's size a
# PRKS CONTRACT rather than an accident of whichever storage layer happened to
# refuse first: before this, the server accepted any length and the browser's
# durable envelope decided, so the same value could be savable online and
# impossible offline. That is the split contract this architecture removes.
MAX_AUTHOR_TEXT_UTF8_BYTES = 64 * 1024

# A Title is one line of text. 64 KiB is far beyond any real one -- which is
# again the point: the number exists so the field has a stated contract rather
# than inheriting whichever storage layer refuses first. The column has no
# length constraint and the ordinary PATCH accepted any length, so this is the
# first bound Title has ever had, chosen to match `author_text` rather than to
# restrict anything a user would type.
MAX_TITLE_UTF8_BYTES = 64 * 1024

# A provenance URL. Same reasoning and same number as the two above: the bound
# exists so the field has a contract rather than inheriting whichever storage
# layer refuses first.
MAX_SOURCE_URL_UTF8_BYTES = 64 * 1024

# Fields measured in UTF-8 BYTES rather than code points, and their limits.
# A byte bound is a STORAGE and TRANSPORT concern -- how much a durable
# operation, a cached projection and every read of them may cost -- so it is
# counted in the unit storage actually uses. The small one-line fields keep
# code-point limits, which bound how much TEXT a field may hold; switching
# those to bytes would quietly shorten each by a factor of three for anyone
# writing CJK.
#
# Membership of this registry is what makes a field byte-limited. Everything
# downstream -- compact acknowledgements, revision-only metadata-state entries,
# bounded conflict previews -- is derived from it, so a second large field is a
# registry entry rather than another special case threaded through five files.
BYTE_LIMITS = {
    "abstract": MAX_ABSTRACT_UTF8_BYTES,
    "author_text": MAX_AUTHOR_TEXT_UTF8_BYTES,
    "title": MAX_TITLE_UTF8_BYTES,
    "source_url": MAX_SOURCE_URL_UTF8_BYTES,
}

# The canonical Work statuses. This lives HERE, in the field-synchronization
# domain, rather than in db_manager: PATCH, the bulk action and the sync
# handler must all decide validity the same way, and db_manager already
# imports this module (the reverse would be a cycle). `db_manager` re-exports
# it as PRKS_WORK_STATUSES so existing callers are unaffected.
WORK_STATUSES = (
    "Not Started",
    "Planned",
    "In Progress",
    "Completed",
    "Paused",
)
WORK_STATUS_SET = frozenset(WORK_STATUSES)

# The BibLaTeX entry types a Work may claim. Here for the same reason the
# statuses are: PATCH, the bulk action and the synchronization handler must all
# decide validity the same way, and db_manager already imports this module.
# `db_manager` re-exports it as PRKS_BIBTEX_DOC_TYPES.
#
# Unlike the statuses, the ordinary PATCH NORMALIZES rather than refuses --
# anything unrecognized becomes "misc", and that is long-standing product
# behaviour this milestone does not get to change. The two paths still converge:
# PATCH normalizes first and then validates, so what it stores is always
# canonical, while the synchronization wire requires an already-canonical value
# because the editor's control offers nothing else. A wire value the server
# silently rewrote would also break the acknowledgement contract, which requires
# the echoed value to equal the one the operation carried.
DOC_TYPES = (
    "article", "book", "booklet", "inbook", "incollection", "inproceedings",
    "proceedings", "manual", "mastersthesis", "phdthesis", "techreport",
    "unpublished", "misc", "online",
)
DOC_TYPE_SET = frozenset(DOC_TYPES)
DEFAULT_DOC_TYPE = "misc"


def normalize_doc_type(value):
    """User/API input -> a whitelisted entry type; anything unknown -> misc."""
    if value is None:
        return DEFAULT_DOC_TYPE
    text = str(value).strip().lower()
    return text if text in DOC_TYPE_SET else DEFAULT_DOC_TYPE

# A synchronized field is validated either by SIZE or by an ALLOWLIST. Status
# is the first of the second kind: its legal values are an enumeration the
# whole application shares, and a free-text length bound would say nothing
# about whether a value is meaningful.
FIELD_ALLOWLISTS = {
    "status": WORK_STATUS_SET,
    "doc_type": DOC_TYPE_SET,
}

# A field's entry is its size limit -- in UTF-8 BYTES for a field listed in
# BYTE_LIMITS, in code points otherwise -- or None when the field has no size
# rule at all. `status` is the only None: it is validated by allowlist instead,
# and asking a five-value enumeration how long it may be is meaningless.
SYNCED_FIELDS = {
    "status": None,
    "title": MAX_TITLE_UTF8_BYTES,
    # PROVENANCE ONLY, and only on a Work whose kind is explicitly non-video.
    # See `guard_field_on_conn`: on a video Work this same column is one
    # spelling of an identity that spans several columns, and changing it
    # alone would leave the stored URL naming one video while `provider_id`
    # still plays another.
    "source_url": MAX_SOURCE_URL_UTF8_BYTES,
    # Validated by allowlist; canonicalized by `normalize_doc_type` on the
    # PATCH path, which is where unrecognized input has always become "misc".
    "doc_type": None,
    # Validated by its CODEC rather than by size or allowlist: a page number
    # is not long or short, it is a page number or it is not one.
    "thumb_page": None,
    "author_text": MAX_AUTHOR_TEXT_UTF8_BYTES,
    "abstract": MAX_ABSTRACT_UTF8_BYTES,
    "year": 50,
    "published_date": 40,
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
# A field in BYTE_LIMITS is measured in UTF-8 BYTES; every other size-limited
# field keeps the code-point limit it has had since 2D. The distinction is
# deliberate, not an oversight. A byte bound exists to keep a durable
# operation and every read of it cheap, which is a storage and transport
# concern and therefore a byte concern. A code-point bound says how much TEXT
# a one-line field may hold, which is a display concern -- and switching those
# to bytes would quietly shorten every one by a factor of three for anyone
# writing CJK.
BROWSE_LISTS = ("works-browse", "recent", "recently-added")

BYTE_LIMITED_FIELDS = frozenset(BYTE_LIMITS)

# A conflict result is persisted in the browser's durable operation row, which
# bounds a structured result to 2 KB. Echoing two values that may each be far
# larger than that would mean the client could not store the conflict at all --
# the operation would fail to settle rather than reach the user. So a byte-limited field
# reports bounded PREVIEWS and sizes instead, enough to show the user what the
# disagreement is; taking the server's version re-reads the authoritative value
# rather than trusting a truncated copy.
CONFLICT_PREVIEW_CHARS = 400


def preview(value):
    text = canonical(value)
    return text[:CONFLICT_PREVIEW_CHARS]


# Cached LIST projections that carry a synchronized field.
FIELD_PROJECTIONS = {
    "publisher": ("recently-added",),
    # DERIVED, unlike publisher: what reaches `works-browse:index` is not the
    # abstract but its first 100 code points, which Progress renders.
    "abstract": ("works-browse",),
    # Every Work card shows a year, so these reach all three browse catalogs.
    # They are also embedded in cached Folder/Person/Playlist details -- see
    # SUMMARY_FIELDS, which those entity snapshots carry verbatim.
    "year": BROWSE_LISTS,
    "published_date": BROWSE_LISTS,
    # Every Work card's CREDIT line can come from `author_text`, so it reaches
    # all three catalogs -- though whether it is what the user actually SEES is
    # decided afterwards, by the credit helper, from linked role data this
    # module knows nothing about.
    "author_text": BROWSE_LISTS,
    # Status is not only a badge on every Work card: it decides which GROUP a
    # Work belongs to on Progress, which reads `works-browse:index`. A pending
    # Status therefore has to reach these rows before the route filters them,
    # or the Work stays in the group the server last knew about.
    "status": BROWSE_LISTS,
    # Every Work card derives its thumbnail URL from this, so a pending page
    # has to reach the cached rows those cards are rendered from.
    "thumb_page": BROWSE_LISTS,
    # Every Work card shows a doc-type badge, and Types groups on it -- the
    # same shape `status` has for Progress.
    "doc_type": BROWSE_LISTS,
    # The widest field of all: every Work card shows a Title.
    "title": BROWSE_LISTS,
    # Browse rows carry it; `prksInferWorkSourceKind` consults it when a Work
    # has no explicit kind and no file.
    "source_url": BROWSE_LISTS,
}

# Fields that cached ENTITY snapshots embed as part of a Work summary:
# `folder.works[]`, `person.works[]`, `playlist.items[]`. A pending value has to
# reach those rows too, and an acknowledgement has to patch them.
SUMMARY_FIELDS = frozenset({"status", "title", "doc_type", "thumb_page", "author_text",
                           "year", "published_date", "publisher", "source_url"})
SUMMARY_ENTITY_KINDS = ("folder", "person", "playlist")


def scope_key(work_id, field):
    # Structural encoding, like the Work-Tag scope: no delimiter has to be
    # excluded from either component for this to stay unambiguous.
    return json.dumps([work_id, field], ensure_ascii=True, separators=(",", ":"))


def is_valid_field_value(field, value):
    """The ONE rule every mutation path asks. The sync handler, the ordinary
    PATCH and the bulk action must not be able to disagree about whether a
    value is acceptable: a value savable by one path and refused by another is
    the split contract moving a field to local-first exists to remove.

    The SQLite CHECK constraint is a last line of defence, not the first: it
    reports an IntegrityError rather than a field-specific refusal, and it
    cannot tell the client which value it should have sent.
    """
    allowed = FIELD_ALLOWLISTS.get(field)
    if allowed is not None:
        return canonical(value) in allowed
    if field in FIELD_CODECS:
        codec = codec_for(field)
        # A codec field is validated by its codec: a page number is not long
        # or short, it is a page number or it is not one.
        return codec.is_valid_input(value)
    if SYNCED_FIELDS[field] is None:
        return True
    return within_limit(field, value)


def within_limit(field, value):
    limit = SYNCED_FIELDS[field]
    if limit is None:
        # An allowlisted field has no size rule; asking for one is a bug.
        raise ValueError("%s is validated by allowlist, not by size" % field)
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


# ---- field codecs: the wire is not the column ------------------------------
#
# Every synchronized field so far has been a string in the editor, a string on
# the wire and a string in the column, so those three could be the same value
# without anyone having to say so. `thumb_page` is the first field where they
# genuinely differ: the column is INTEGER NULL, and the read models -- the Work
# record, the three browse catalogs, the embedded Folder/Person/Playlist
# summaries -- carry `integer | null` and are validated as such by the client.
# A wire string reaching one of those rows does not merely look odd; it makes
# the row fail its own shape validator and be discarded as corrupt.
#
# The wire stays a STRING regardless. The operation envelope is validated,
# hashed, compared and replayed as a string on both sides, and widening
# `payload.value` to a union type would mean touching every one of those for a
# single field. So the boundary converts, and the codec owns the conversion:
#
#     editor "3"  <-> wire "3"  <-> column 3   <-> entity 3
#     editor ""   <-> wire ""   <-> column NULL <-> entity null
#
# A field with no codec is a string everywhere, which is what `identity` says.


class _IdentityCodec:
    """Wire, column and entity are the same string."""

    @staticmethod
    def to_wire(value):
        return canonical(value)

    @staticmethod
    def to_database(wire):
        return canonical(wire)

    @staticmethod
    def is_valid_wire(wire):
        return isinstance(wire, str)

    @staticmethod
    def is_valid_input(value):
        # Identity fields use the same validation for native and wire input.
        return _IdentityCodec.is_valid_wire(value)


class _ThumbPageCodec:
    """A 1-based page number, or nothing at all.

    The wire form is the DECIMAL STRING of the page, or "" for "no explicit
    page". `""` is the only spelling of absence: a client cannot send null,
    because the envelope carries strings.
    """

    @staticmethod
    def _parse(text):
        """ONE strict rule, shared by validation and conversion.

        A validator that refuses a spelling while the converter quietly
        accepts it is two rules, and the second one only runs where the first
        was bypassed -- which is exactly where being surprising is worst.

        Digits only. `int()` alone would accept "+3", "３" (fullwidth), "3_0"
        and assorted Unicode spaces; none is a page number anyone typed on
        purpose, and accepting them would let two devices hold different
        spellings of the same page and disagree about having converged.
        """
        if not isinstance(text, str):
            return None
        stripped = text.strip()
        if not stripped.isascii() or not stripped.isdigit():
            return None
        page = int(stripped)
        return page if page >= 1 else None

    @classmethod
    def is_valid_wire(cls, wire):
        if not isinstance(wire, str):
            return False
        # "" is the only spelling of absence; everything else must parse.
        return wire.strip() == "" or cls._parse(wire) is not None

    @classmethod
    def is_valid_input(cls, value):
        """Accepts the wire form AND the native one.

        The synchronization envelope carries strings, so the sync path only
        ever offers `is_valid_wire`. An ordinary PATCH is a JSON API where a
        page is naturally an integer and absence is naturally null. Those are
        two SPELLINGS of one rule, not two rules: both are converted by this
        same codec to one canonical value before anything is compared or
        written, which is what stops the paths from drifting.
        """
        if value is None:
            return True
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return value >= 1
        return cls.is_valid_wire(value)

    @classmethod
    def to_wire(cls, value):
        """Column or entity value -> wire. NULL, and anything unusable,
        becomes "" -- which is what the thumbnail endpoint already treats a
        missing page as."""
        if value is None:
            return ""
        if isinstance(value, bool):
            return ""
        if isinstance(value, int):
            return str(value) if value >= 1 else ""
        page = cls._parse(value)
        return str(page) if page is not None else ""

    @classmethod
    def to_database(cls, wire):
        """Wire -> column. Never a string: the column is INTEGER NULL."""
        if isinstance(wire, bool):
            return None
        if isinstance(wire, int):
            return wire if wire >= 1 else None
        return cls._parse(wire)


FIELD_CODECS = {
    "thumb_page": _ThumbPageCodec,
}


def codec_for(field):
    return FIELD_CODECS.get(field, _IdentityCodec)


def canonical_wire(field, value):
    """The wire spelling of a value, whatever representation it arrives in.

    Canonicalizing here is what makes "003" and "3" the same state rather than
    two devices disagreeing about a page they both chose.
    """
    if field == "doc_type":
        # Long-standing PATCH behaviour: unknown becomes "misc" rather than
        # being refused. Applied here so PATCH and the sync handler still reach
        # the same stored value from the same input.
        return normalize_doc_type(value)
    return codec_for(field).to_wire(value)


def wire_to_database(field, wire):
    return codec_for(field).to_database(wire)


def database_to_wire(field, value):
    return codec_for(field).to_wire(value)


# A field-scoped operation is the wrong semantic unit for some (field, Work)
# pairs. `source_url` is the first: on a Work whose canonical kind is video it
# is one spelling of an identity spanning `source_kind`, `provider` and
# `provider_id`, and `provider_id` outranks it -- so changing the column alone
# would leave the stored URL naming one video while the viewer plays another.
# The guard lives HERE, on the mutation boundary, because a guard that lives
# only in a form is not a contract: the UI already hides the control for
# videos, and that has never stopped an API client.
FIELD_KIND_GUARDS = {
    "source_url": "video",
}


def validate(op):
    payload = op["payload"]
    if set(payload) != {"field", "value"}:
        raise ValueError("INVALID_ENVELOPE")
    field, value = payload["field"], payload["value"]
    # An arbitrary column name from a client would be an injection surface and
    # a way to reach fields this milestone deliberately does not synchronize.
    if not isinstance(field, str) or field not in SYNCED_FIELDS:
        raise ValueError("INVALID_ENVELOPE")
    if not isinstance(value, str) or not is_valid_field_value(field, value):
        raise ValueError("INVALID_ENVELOPE")
    # A scalar edit is optimistic-concurrency controlled: a null base revision
    # is a client that cannot detect a conflict, which would silently overwrite
    # whatever another device wrote.
    if op["base_revision"] is None:
        raise ValueError("INVALID_BASE_REVISION")


# The browser stores a terminal result in the durable operation row and bounds
# it to this many bytes of SERIALIZED JSON. Mirrors `MAX_RESULT_BYTES` in
# `local-store.js`; a test pins that the two agree.
#
# A result the client cannot store is worse than either value winning: the
# durable write fails, the coordinator reads that as a failed sync, and the
# operation goes back to pending and retries -- forever, deterministically,
# because the same oversized result comes back every time. The user never
# reaches the conflict UI and has no way to resolve anything. So the server's
# obligation is not "bound the preview" but "return something the client can
# store", and that has to be measured, not estimated.
MAX_DURABLE_RESULT_BYTES = 2048


def serialized_result_bytes(result):
    """Exactly what the client will measure.

    `JSON.stringify` does not escape non-ASCII, so `ensure_ascii=False` is
    required for this to be the same number -- with the default the server
    would count a CJK character as six bytes where the browser counts three,
    and would truncate previews nobody needed truncated. The separators match
    JS's, which emits no spaces. Verified against node across control
    characters, quotes, CJK, astral characters and combining marks.
    """
    return len(json.dumps(result, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8"))


def disagreement(field, current, desired):
    """What the two sides hold, in the PREFERRED form for this field.

    A small scalar reports both values, because the client can then offer
    "Use server" without a second request. A byte-limited field reports bounded
    previews and sizes instead. Either shape may still be too large to store --
    see `fit_terminal_result()`, which is what actually guarantees it.
    """
    if field not in BYTE_LIMITED_FIELDS:
        return {"current_value": current, "requested_value": desired}
    return bounded_disagreement(current, desired)


def bounded_disagreement(current, desired):
    return {
        "current_preview": preview(current),
        "current_bytes": len(current.encode("utf-8")),
        "requested_bytes": len(desired.encode("utf-8")),
    }


def fit_terminal_result(result, current, desired):
    """Shrink a conflict result until the client can durably store it.

    Character counts are not byte counts and neither is a serialized size. One
    control character occupies one code point, one byte in the column and SIX
    bytes as `\u0001` in JSON, so a 400-character preview can serialize to
    2400 bytes and a 500-code-point `journal` conflict carrying both values can
    reach 6 KB. Both were storable by the server and unstorable by the client.

    Two steps, in order of how much the user loses:

    1. A full-value result that does not fit degrades to the bounded preview
       shape. The client already renders that shape and re-reads the
       authoritative value when the user takes the server's version.
    2. The preview is then shortened until the WHOLE object fits -- the whole
       object, because the guarantee is about what gets stored, not about one
       field of it.
    """
    if serialized_result_bytes(result) <= MAX_DURABLE_RESULT_BYTES:
        return result
    if "current_value" in result:
        del result["current_value"]
        result.pop("requested_value", None)
        result.update(bounded_disagreement(current, desired))
        if serialized_result_bytes(result) <= MAX_DURABLE_RESULT_BYTES:
            return result
    if "current_preview" not in result:
        return result
    # The longest prefix that fits. Code points, so a surrogate pair is never
    # split -- a lone surrogate would serialize to six bytes and render as a
    # replacement glyph.
    points = list(result["current_preview"])
    low, high = 0, len(points)
    while low < high:
        middle = (low + high + 1) // 2
        result["current_preview"] = "".join(points[:middle])
        if serialized_result_bytes(result) <= MAX_DURABLE_RESULT_BYTES:
            low = middle
        else:
            high = middle - 1
    result["current_preview"] = "".join(points[:low])
    return result


