# PRKS local-first synchronization

PRKS has one semantic-operation protocol and **two families** on it:

| Family | Milestone | Shape |
| --- | --- | --- |
| `ADD_WORK_TAG` / `REMOVE_WORK_TAG` | 2B | Revisioned relationship. Two devices can genuinely disagree, so conflicts are real and the user resolves them. |
| `MARK_WORK_OPENED` | 2C | Max-register over normalized event time. Two devices cannot disagree, so there is no conflict and none is offered. |
| `SET_WORK_METADATA_FIELD` | 2D, 2E, 2F, 2G | Revisioned scalar, scoped to one FIELD. Devices disagree per field, so conflicts are real but narrow. |

Both commit to durable browser storage before the UI acts on them, survive
reloads and offline periods, and synchronize idempotently on reconnect. Other
mutations still require the server.

The second family exists to prove the protocol is a protocol. Its semantics are
deliberately unlike the first -- no concurrency control, no conflict UI, a
different result set, and a different failure posture -- so anything the two
share had to become generic rather than Work-Tag-shaped.

The third exists to decide **conflict granularity**, which every later family
inherits. A Work is not the unit: two devices editing `doi` and `isbn` on the
same Work have not disagreed about anything, and one Work-level revision would
tell them they had -- demanding a resolution for a collision that never
happened. So the scope is the field, and one conflicting field leaves the other
six editable.

## Layers

| Layer | Owns |
| --- | --- |
| `backend/sync_protocol.py` | Envelope normalization, request hashing, ledger, OP_ID_REUSE, exact replay, one transaction, dispatch |
| `backend/work_tag_sync.py` | Relationship revisions, Tag lifecycle, tag-options, ADD/REMOVE handler |
| `backend/work_open_sync.py` | `last_opened_at` max-register, skew policy, MARK_WORK_OPENED handler |
| `backend/work_metadata_sync.py` | The synchronized field registry, per-field revisions, SET_WORK_METADATA_FIELD handler |
| `frontend/js/sync-runtime.js` | Transport, claiming, backoff, locks, replay, status transitions, retirement |
| `frontend/js/work-tag-state.js` | Work-Tag overlay and sync handler |
| `frontend/js/work-open-state.js` | Recent overlay, acknowledged merge, sync handler |
| `frontend/js/work-metadata-state.js` | Field projection, field overlay, dirty-field diff, sync handler |
| `frontend/js/work-metadata-editor.js` | The bibliographic group's save and per-field conflict UI |
| `frontend/js/sync-diagnostics.js` | Settings -> Diagnostics, for every family |

A client handler answers three questions and nothing else: `isResult` (is this
a well-formed answer for this operation), `reconcile` (apply an acknowledgement
to the disposable cache), `terminal` (`{conflict}` for an outcome the user
resolves, `{discard}` for one with no resolution worth offering). The
coordinator names no family: adding one is a registration.

## Storage and ownership

- `prks-offline-v1` remains a disposable cache of acknowledged server data.
- `prks-local-v1` holds semantic operations and a random device UUID. Its
  IndexedDB writes request strict durability and resolve only on transaction
  completion. Failures reject; the UI cannot report success on a failed write.
- Clear offline cache never touches durable operations. Without a cached Work
  base, PRKS shows unavailable rather than inventing a complete Work. Pending
  changes remain in Settings → Diagnostics and synchronize when possible.
- A device ID is an identity for synchronization, never authentication or trust.
- Editor state and observed projections belong to the Work's TabContext.
  The sync coordinator owns transport and retry policy, with one in-flight
  operation. Web Locks coordinate browser tabs; IDB claims arbitrate edits.

## Canonical server state (schema 14)

All three new tables live in `prks_data.db` and are canonical backup state:

| Table | Purpose |
| --- | --- |
| `sync_operations` | Durable idempotency ledger: normalized request hash, terminal result and original HTTP status |
| `sync_entity_revisions` | Revision counters, including absent-relationship tombstones |
| `sync_tag_lifecycle` | Active Tags, merge redirects and explicit deletion history |

Migration 13 → 14 marks existing Tags active. Existing relationships have
revision zero until changed. Relationship scope IDs are compact JSON arrays
`[work_id, tag_id]`, not delimiter-concatenated strings. Lifecycle history has
no Tag foreign keys: deleting a target must not erase historical redirects.

The ledger is **not automatically pruned**. Compaction is deferred until a
coordinated acknowledgement horizon can prove old operation IDs cannot retry.
Restoring a backup restores its ledger, revisions and lifecycle together.

## Protocol

`POST /api/sync/operations` accepts exactly one semantic operation:

```json
{
  "op_id": "c4b288d5-2077-44c6-917e-8956cce58cc5",
  "device_id": "c9f72f0c-d033-4757-aec5-eac1c5d025a2",
  "operation": "ADD_WORK_TAG",
  "entity_type": "work",
  "entity_id": "W-01234567",
  "payload": { "tag_id": "T-01234567" },
  "base_revision": 0,
  "occurred_at": "2026-09-11T12:00:00Z",
  "created_at": "2026-09-11T12:00:00Z",
  "depends_on": []
}
```

`ADD_WORK_TAG`, `REMOVE_WORK_TAG`, `MARK_WORK_OPENED` and
`SET_WORK_METADATA_FIELD` are supported; an unregistered operation is
`INVALID_ENVELOPE` and never reaches a handler.
UUIDs, bounded IDs and timezone-aware timestamps are validated generically.
Unknown envelope fields are rejected. Dependencies must be empty in v1; there
is no batch or dependency executor. Hashes cover the normalized immutable
semantic envelope, with sorted JSON keys and UTC timestamps.

`base_revision` is structurally either a nonnegative safe integer or null, and
**which one is legal is the family's decision**. A Work-Tag edit requires a
counter: accepting null there would be accepting a client that cannot detect a
conflict, which silently overwrites another device. An open event requires
null: it has no prior state to be stale against. Payload validation is the
family's too -- `{"tag_id": ...}` for a relationship, `{}` for an open event.

One `BEGIN IMMEDIATE` transaction performs ledger lookup, domain/lifecycle
validation, revision validation, relationship mutation, revision advancement
and ledger insertion. Any failure before commit rolls everything back.

Seen ID + matching hash replays the exact recorded result and HTTP status,
even after later lifecycle changes. A changed envelope with the same ID gives
`OP_ID_REUSE` and never executes. Validation errors precede the ledger;
transient execution errors are not ledgered.

For unseen operations, Work existence and Tag lifecycle are checked first:
active continues, merged returns `TAG_MERGED` with the ultimate active target,
deleted returns `TAG_DELETED`, and unknown/missing returns `ENTITY_NOT_FOUND`.
Merge redirects are compressed during merge, and the resolver also guards
against cycles. A chain whose ultimate target is deleted resolves as deleted.

| Base vs server revision | Desired vs current state | Result |
| --- | --- | --- |
| Current | Different | Apply, advance revision, ACK |
| Current | Same | ACK without advancement |
| Stale | Same | Convergent ACK without advancement |
| Stale | Different | `REVISION_CONFLICT`, HTTP 409 |
| Future | Either | `FUTURE_REVISION`, HTTP 400 |

A Work-Tag ACK includes `work_id`, `tag_id`, `present`, `server_revision`,
`changed`, and the Tag display snapshot needed to reconcile without a mandatory
extra GET. Conflicts include current revision, current state and requested
state. Terminal semantic outcomes retain their original HTTP classification.

## Work open events

`last_opened_at` is a **max-register over normalized `occurred_at`**, not
last-writer-wins: arrival order does not decide it. A device that reconnects on
Friday carrying Monday's open cannot drag the Work back to Monday, and must not
claim it was opened on Friday either -- so server receive time is not the
canonical event time. That is what makes the operation commutative, idempotent
and order-independent, and why there is no conflict outcome at all.

| Current | Event | Result |
| --- | --- | --- |
| NULL | any | apply, `changed` |
| older | newer | apply, `changed` |
| newer | older | no mutation, ACK `changed: false` |
| equal | equal | no mutation, ACK `changed: false` |

`changed: false` is an ordinary success, not an error: the canonical state
already reflects the event.

An OLD timestamp is legitimate -- a Work really can have been opened days ago
on a device that was offline -- and is never rejected; the max-register makes an
obsolete event a harmless no-op. A FUTURE timestamp is not legitimate, but the
activity behind it is: beyond `MAX_CLIENT_FUTURE_SKEW_SECONDS` (300) the
effective value is clamped to the server's receive time rather than discarded,
so a fast clock cannot pin a Work to the top of Recent.

Values are stored as `YYYY-MM-DD HH:MM:SS.mmm` in UTC
(`work_open_sync.format_moment`), which sorts lexicographically in
chronological order. Second-resolution rows written before this milestone stay
valid and keep sorting correctly, because `12:00:00` is a prefix of
`12:00:00.250` -- the same rule SQLite applies, and the reason precision needed
no destructive migration. Recent's `id ASC` tie-break is unchanged.

`POST /api/works/:id/opened` remains available for other canonical callers and
shares `work_open_sync.set_opened_at()` with the sync handler -- server-now is
simply that caller's event time. One column, one meaning. The browser has
exactly one open-event path, because two would let online and offline opens
diverge.

The ACK carries `code`, `work_id`, `changed`, `effective_opened_at` and the
compact `/api/recent` row (`db.recent_item_on_conn`), so reconciliation needs no
second request and the client never rebuilds server-derived author or file
fields itself. `ENTITY_NOT_FOUND` is the only terminal outcome, and it is
consumed rather than parked: "apply my open event to a Work that no longer
exists" is not a choice anyone can make. Consumed events are counted in
Settings -> Diagnostics for the session, in memory and bounded.

## Recent: overlay and reconciliation

Pending open events are an overlay computed at render time
(`prksEffectiveRecent`), never written into `recent:index`: an unsynchronized
event must stay distinguishable from acknowledged server state, and Clear
offline cache must not erase it. The overlay reproduces the canonical order and
limit exactly, and is rebuilt from `prks-local-v1`, so it survives a reload
rather than living in a tab's memory.

A Work the cached list does not already carry needs a display row; the event's
own bounded `local_context` snapshot supplies one, derived from the Work that
was actually rendered. With neither, the event is skipped rather than rendered
as a fabricated card. Without a cached Recent snapshot at all, Recent stays
honestly unavailable offline -- one open event is not a Recent page -- and the
durable event still synchronizes later.

On acknowledgement the cached list is reconciled **in place** rather than
dropped, so an open no longer costs Recent its offline availability. The
`recent` domain generation is bumped before the read, so a `GET /api/recent`
that began earlier cannot publish its pre-acknowledgement body over it.

Only never-sent open events coalesce, per Work, keeping the latest instant. A
SENT event is left alone: it may already be ledgered, and it does not need
cancelling, because the server takes the maximum and applying two open events
in either order gives the same canonical result. A clock that jumped backwards
cannot make an older event replace a newer pending one.

Recording an open is best-effort in a way a Work-Tag edit deliberately is not.
A durable-write failure for a Tag edit must be reported, because the user made
a change and would otherwise believe it was saved. An open event is activity
metadata the user never asked for: losing it costs a Recent ordering, and
refusing to show the Work over it would cost them what they actually wanted.
A render PRKS decided to do -- the reconnect refresh -- is not an open either.

## Work metadata fields

`backend/work_metadata_sync.SYNCED_FIELDS` is the authoritative registry of
which Work fields synchronize; the client list is pinned against it by a test.
Today it holds `status`, `author_text`, `year`, `published_date`, `abstract`,
`publisher`, `location`, `edition`, `journal`, `volume`, `issue`, `pages` and
`doi`, plus `isbn`. Each owns a revision scope
`work-field / ["<work id>", "<field>"]` -- the same structural JSON encoding the
Work-Tag scopes use, so no delimiter has to be excluded from either component.
A missing row is revision 0. No schema change was needed: these live in the
existing `sync_entity_revisions` table.

The envelope payload is `{"field": ..., "value": ...}`, one operation type for
all of them rather than one per field: the semantic act is "set one supported
field to one scalar value", so 2E added `publisher` and `location` as registry
entries and nothing else, and 2G added `year` and `published_date` the same way
-- the cost of those two was never the operation, it was the fan-out below. The field must be in the server's own registry -- an
arbitrary column name from a client would be both an injection surface and a
way to reach fields these milestones deliberately exclude.

| Base vs field revision | Requested vs current value | Result |
| --- | --- | --- |
| Current | Different | Apply, advance that field's revision, ACK `changed: true` |
| Current | Same | ACK `changed: false`, no write |
| Stale | Same | Convergent ACK, no revision advance |
| Stale | Different | `REVISION_CONFLICT`, HTTP 409 |
| Future | Either | `FUTURE_REVISION`, HTTP 400 |

A stale base is only a conflict when the two devices actually disagree. Two
people typing the same DOI have converged, not collided.

**Values are stored exactly as a PATCH would store them.** Synchronization is
not a licence to start normalizing what PRKS never normalized: a DOI keeps its
case, an ISBN keeps its punctuation, a page range is not parsed, and whitespace
is not stripped. The only validation this layer adds is a per-field length
bound. SQLite NULL (a row older than the field) and `""` (one a user cleared)
are one logical value, so clearing an already-empty field is not a change.

The ACK carries `work_id`, `field`, `value`, `server_revision` and `changed`;
the conflict carries `current_revision`, `current_value` and `requested_value`.
Both are field-specific, so the UI can resolve one field without disturbing the
others. No second GET is needed before retiring the operation.

`GET /api/works/:id/metadata-state` returns every supported field's value and
revision, cached as entity kind `work-metadata-state`. It is deliberately its
own endpoint: revisions are synchronization bookkeeping, and putting them on
the Work detail would make every consumer of a Work pay for them and re-cache
on every change. Its ETag hashes the representation, so a value change, a
same-length replacement and a revision-only change all move it.

**The ordinary online PATCH advances the same revisions.** Revisions record
canonical history, not sync-endpoint history: if `PATCH /api/works/:id` could
change a DOI without moving `work-field / W:doi`, an offline device holding the
old value would have no way to discover it had been overtaken, and would
overwrite the newer value believing itself current. `update_work_metadata()`
compares canonical old and new values and advances only the fields that
actually changed, with values and revisions committing in one transaction -- a
stored value whose revision did not advance is exactly the state that makes
every other device's staleness check lie.

Work creation manufactures no revisions: initial values are revision 0.

## Hydration: four states, not a boolean

The synchronous overlay exists because Recently Added and Progress filter on
every keystroke. Its cost is that an empty pending map is ambiguous, so
hydration is four-valued -- `unread`, `loading`, `ready`, `unavailable` -- and
only `ready` licenses a caller to say "there is no pending value for this
field".

A failed IndexedDB read settles as **`unavailable`**, never `ready`. It proves
nothing about what is stored: operations persisted by an earlier session may
still be there. Waiters are released, because hanging the UI on a read that
already failed serves nobody, but the last known map is kept and an empty one
stays untrusted. The bibliographic fields then refuse to become editable -- a
save against an untrusted base could destroy a pending edit this session simply
could not see -- and the editor says so. Falling back to a direct PATCH is not
an option: online and offline keep the same durable-first contract. A later
successful refresh moves `unavailable` back to `ready` with no reload.

Entering Edit metadata before hydration settles waits for the read **already in
flight** -- one shared promise, never a second read, never a poll. Building the
draft from an un-hydrated map would show stale text after a reload and then ask
the user whether to discard changes they had already saved.

## Metadata: overlay, atomic save and per-field conflicts

### Cross-projection overlay (2E)

The effective Work is the acknowledged cached record plus durable
pending/conflicted field edits. Pending intent is never written into the cached
Work; the overlay is recomputed from `prks-local-v1`, so it survives a reload,
and it is applied on every Work detail render rather than only while editing.
One renderer produces the bibliographic rows for both the first paint and the
overlay repaint, so they cannot drift.

**One Save, one transaction, however many fields it touched.** The user pressed
a single button; durably storing three of their four edits and then reporting
"Saved locally" is a lie that only surfaces later. Every field in one save
commits together or none does.

Only fields the user actually changed become operations, and "changed" is
measured against what the form was **showing** -- the pending value if there is
one, otherwise the server's. Measuring against the server base instead is
subtly wrong in both directions: editing a field back to its server value would
look like no change and quietly strand the pending operation, and a field still
displaying an untouched pending value would look dirty on every save.

A field carried by another cached projection needs that projection's rows
overlaid too, and `publisher` is the only one today. The rules live in
`work-metadata-state.js`; the Folder component only says "repaint", so there is
one interpretation of operation semantics rather than two that can drift.

The durable queue is read ONCE into a `workId -> {field: value}` map, not once
per row: Recently Added filters up to 50 rows synchronously on every keystroke,
and a query per row would be a storm. Effective rows are produced by copying
only the rows an edit touches -- the acknowledged array, in memory and in
IndexedDB, stays exactly what the server said. That matters more than it
sounds: this tab already shipped a bug where its RAM copy outlived an IndexedDB
invalidation, and writing pending values into it would be the same mistake with
a longer fuse. The memoized render is refused when either the coherence
generation or the overlay generation moves, and an overlay change repaints
without refetching, because nothing on the server moved.

On acknowledgement the projection row is **patched in place** rather than the
list invalidated: dropping the snapshot would cost Recently Added its offline
availability for a change whose exact shape is already known. The
`recently-added` domain generation is bumped before the read, so a
`GET /api/recently-added` that began earlier cannot publish over it. A Work the
projection does not carry, or no cached projection at all, is nothing to
reconcile rather than a failure.

### Per-field save and conflicts

Coalescing is per field. Only never-sent rows may be rewritten, so a field whose
operation may already have reached the server, or whose operation is
conflicted, is temporarily busy -- and only that field. The other six stay
editable, which is the whole point of the scope.

A conflict belongs to the field. The editor shows this device's value and the
server's beside that input, with **Use server** and **Apply my value**. Use
server reconciles the reported state and discards the operation, creating
nothing. Apply my value creates a NEW operation against the reported current
revision; the conflicted id is never reused. Settings -> Diagnostics lists every
unsynchronized operation with its Work id, field, local value and the server's,
and can discard a conflicted one without the Work's page existing at all.

## Abstract: a large scalar with a derived projection

`abstract` is the first synchronized field that is neither small nor copied
verbatim into its projection, and it needed three decisions no earlier field
did.

**Size.** `MAX_ABSTRACT_UTF8_BYTES` is **1 MiB**, a deliberate PRKS product
rule rather than a measurement — an abstract is bibliographic summary text, and
long-form material belongs in Research Notes. It is enforced identically by the
ordinary PATCH, the sync handler, the durable local store and the editor before
enqueue, because an Abstract savable online and refused offline would be exactly
the split contract that moving a field to local-first exists to remove. It is
measured in **UTF-8 bytes**, never `len()`: a limit documented in bytes but
enforced in characters does not exist for the users most likely to reach it.
An over-limit value is refused visibly — the draft stays, nothing is enqueued,
nothing is sent. Nothing is ever truncated. The other small scalars keep their
code-point limits; switching those to bytes would quietly shorten each by a
factor of three for anyone writing CJK.

**The excerpt.** Progress renders `abstract_excerpt`, which the server derives
with `SUBSTR(COALESCE(abstract, ''), 1, 100)`. SQLite counts **code points**;
JavaScript's `slice`/`substring` count UTF-16 code units, and the two disagree
for every astral character. `prksAbstractExcerpt()` is the canonical client
rule — first 100 Unicode code points, matching the server, *not* grapheme
clusters — and `tests/test_abstract_excerpt.py` pins equality against the real
SQLite engine across ASCII, accented Latin, CJK, emoji, mixed BMP/astral,
combining marks and the boundary at 100. It bounds the input slice before
expanding, so a megabyte Abstract costs nothing per render.

A pre-existing bug fell out of this: Progress re-truncated the already-bounded
excerpt with `substring(0, 100)`, which cut it by UTF-16 units and could end
mid-surrogate-pair — a broken glyph. That second truncation is gone.

**Storage shape.** `abstract` is in `BYTE_LIMITED_FIELDS`, and the
`metadata-state` projection carries its **revision only**. The Work record
already holds the acknowledged value; echoing up to a megabyte into a second
cached entity would double what the endpoint sends, what IndexedDB stores and
what every re-read costs, for a value the client already has. The editor
resolves that field's base from the Work.

**Conflicts.** The durable operation row bounds a structured result to 2 KB, so
a byte-limited field reports `current_preview`, `current_bytes` and
`requested_bytes` instead of the two values — a conflict the browser cannot
store is a conflict the user never sees. Taking the server's version therefore
discards the local intent and invalidates the cached Work rather than trusting
a truncated copy. Diagnostics shows bounded previews and sizes, never a whole
value.

## Projection transforms

`FIELD_PROJECTIONS` says *which* cached projections a field reaches;
`PROJECTION_COLUMNS` in `work-metadata-state.js` says *how*:

| Field | Projection | Column | Transform |
| --- | --- | --- | --- |
| `publisher` | `recently-added` | `publisher` | copied |
| `abstract` | `works-browse` | `abstract_excerpt` | **derived** (first 100 code points) |
| `year` | `works-browse`, `recent`, `recently-added` | `year` | copied |
| `published_date` | `works-browse`, `recent`, `recently-added` | `published_date` | copied |
| `status` | `works-browse`, `recent`, `recently-added` | `status` | copied |
| `author_text` | `works-browse`, `recent`, `recently-added` | `author_text` | copied |
| `thumb_page` | `works-browse`, `recent`, `recently-added` | `thumb_page` | **converted** (wire string → `integer \| null`) |

A declared projection the runtime cannot address is a wiring error, not
something to step over: `reconcileFieldProjections` returns false rather than
skipping it, and a test pins that every domain in `FIELD_PROJECTIONS` has a
list key. 2G shipped that guard because `recent` was declared and unmapped --
acknowledgements silently skipped `recent:index`, leaving the Recent catalog
serving a value the server no longer held.

Consumers ask for effective rows and never interpret durable operations
themselves, so a third field is a table entry rather than an `if` inside
whichever component happens to render it. The acknowledged patch applied at
reconciliation uses the *same* transform, so a row cannot visibly change when
the server answers.

## Fan-out: which fields reach which projections

Most synchronized fields are rendered on the Work detail and nowhere else. The
Work summary projection carries them, so cached Folder, Person and Playlist
details hold them in their payloads -- but no Work card, browse catalog, Concept,
Argument or Graph surface displays them, so a pending value needs no optimistic
propagation beyond the Work itself, and an acknowledgement invalidates no browse
catalog.

The exceptions are listed below, and each one cost a milestone. `publisher` is COPIED into
`recently-added:index`, which 2E exists because of: **being invisible on a card
is not the same as being unused** -- that projection selects `works.publisher`
because Home -> Recently Added filters LOCALLY over it. `abstract` is DERIVED
into `works-browse:index.abstract_excerpt`, which Progress renders, and 2F
exists because of that. `FIELD_PROJECTIONS` in
`backend/work_metadata_sync.py` and `work-metadata-state.js` names both
dependencies on both sides, and the parity is pinned by a test -- an earlier
version of this table claimed publisher reached nothing else, which was wrong.

`year` and `published_date` are the HIGH FAN-OUT case, and 2G exists because of
them. Every Work card shows a year, so both reach all three browse catalogs,
and both are also embedded in cached Folder, Person and Playlist details, whose
rows are Work summaries. That is three cached lists plus an unbounded number of
cached entities per edit. Two mechanisms carry it:

| Reach | Mechanism | Where |
| --- | --- | --- |
| Three browse catalogs | `FIELD_PROJECTIONS` + `PROJECTION_COLUMNS` | `reconcileFieldProjections` |
| Embedded Work summaries in Folder / Person / Playlist details | `SUMMARY_FIELDS` + `store.getEntitiesByKind()` | `reconcileEmbeddedSummaries` |

Embedded rows are PATCHED rather than invalidated. Dropping three whole domains
for a one-field edit would cost the user every cached Folder, profile and
playlist -- and we know the exact new value, so there is nothing to re-fetch.
Each domain's generation advances BEFORE its rows are read, so a GET that began
earlier cannot publish its pre-acknowledgement body afterwards; the domain is
not blocked, because rows are being corrected, not invalidated. A field no
summary carries (`doi`, say) never touches those domains at all.

The displayed year is DERIVED, not stored: an explicit `year` wins, and
`published_date` supplies it otherwise. So a pending edit to either field can
move what a card shows, which is why both are registry entries rather than one.

The deferred fields do not share that property:

| Field | Also rendered or matched by |
| --- | --- |
| `source_url` | every Work card, via `prksInferWorkSourceKind()` deciding the thumbnail kind, and the Work detail's clickable "Original URL" row |
| `thumb_page` | every Work card, via the thumbnail URL |
| `author_text` | every Work card's credit line, in all three browse catalogs and in cached Folder / Person / Playlist details |
| `status` | Work card badges, the Progress route's grouping, and the same cached details |
| `doc_type` | Work card badges, the Types route's grouping, the Graph |
| `title` | all of the above, plus Concept details (mention titles), Argument details (source Works), Graph snapshots and the command palette |

## Status: a field that changes which GROUP a Work is in

Every field before `status` changed what a card SAID. Status changes where the
card IS. Progress renders one status at a time, selecting over the rows the
route hands it, so a pending Status has to make a Work **leave** the group the
server put it in and **join** the pending one -- before synchronization, and
across a reload.

That needs no new mechanism. The route already fetches `works-browse:index`,
overlays it with `prksEffectiveBrowseRows()` and hands the result to
`renderProgressByStatus()`, which filters `w.status === status`. Adding
`status` to `FIELD_PROJECTIONS` and `PROJECTION_COLUMNS` is therefore the whole
change: `progress.js` never learns what a durable operation is, and could not
disagree with any other surface about pending state if it wanted to.

### Where Status is read

| Surface | Reads | Overlay |
| --- | --- | --- |
| Work detail card and editor | the Work record | `prksEffectiveWorkSync()` |
| Progress groups | `works-browse:index` | `prksEffectiveBrowseRows()`, then the route's filter |
| Recently opened / Recently added cards | `recent:index`, `recently-added:index` | `prksEffectiveBrowseRows()` |
| Recently Added local search | `recently-added:index` | the same overlaid rows the cards use |
| Folder / Person / Playlist detail cards | embedded Work summaries | `prksEffectiveWorkSummaries()` |
| Search and Saved View result cards | a fresh server response | `prksEffectiveWorksSync()` in `prksSearchResultCardsHtml()` |
| Types, Concepts, Arguments, Graph, command palette | do not render a Status badge | — |

### Where Status is written

| Path | Revision-aware | Note |
| --- | --- | --- |
| `SET_WORK_METADATA_FIELD` | yes | the only path the UI uses, online and offline |
| `PATCH /api/works/:id` | yes | routed through `set_field_on_conn` like every synchronized field |
| `bulk_update_works(action="set_status")` | **yes, since 2H** | see below |
| `add_work()` (create, Processing import) | n/a | a new Work starts at revision 0; construction is not a change |

**The bulk action was the dangerous one.** It used to run
`UPDATE works SET status = ?` over the selection directly. Once Status carries
a revision, that is not merely inconsistent, it is a silent data-loss path: the
value changes while the counter does not, so a device holding the pre-bulk
Status reconnects, compares equal revisions, concludes it is current and
overwrites the newer value. It now routes each selected Work through
`set_field_on_conn` inside the transaction it already had -- exactly as the Tag
branch beside it already did -- so only Works whose Status actually changes
advance, and values and revisions roll back together.

### One validation rule

Status is the first synchronized field validated by an ALLOWLIST rather than a
length: a value outside `WORK_STATUSES` is not "too long", it is not a status.
`is_valid_field_value()` answers for every field and every path -- the sync
handler, the PATCH and the bulk action -- because a value savable by one path
and refused by another is the split contract that moving a field to
local-first exists to remove. The SQLite CHECK constraint remains a last line
of defence: it raises an IntegrityError rather than telling the client what it
should have sent.

## `author_text`: a stored value that is not necessarily the displayed one

Every field before this one was shown, or not shown, as itself. `author_text`
is one of three possible sources of a Work's CREDIT, and it is the weakest but
one:

```
linked Author(s)  ->  author_text  ->  linked Editor  ->  no credit
```

So a pending `author_text` always changes the FIELD and only sometimes changes
what the user sees. That is not a defect to design around; it is the existing
composition, and synchronization must not quietly take it over.

**The overlay produces the field; the existing credit helper decides the
rest.** `prksWorkCardCreditLine()` is unchanged and remains the single place
that rule lives. The ordering is the whole design:

```
acknowledged row -> apply pending fields -> effective row -> credit helper -> HTML
```

Never the reverse. Composing the credit first and patching `author_text` onto
the rendered string afterwards cannot work: with a linked Author the patch must
do nothing, and with the field cleared it must reveal a *different* person
entirely. The command palette had precisely this ordering bug and 2I fixed it.

There are deliberately no `display_author`, `display_credit` or
`effective_credit` stored fields. A derived value that is also stored is two
sources of truth that drift the first time either input changes -- and a future
role-synchronization milestone must be able to change which value is *preferred*
without touching `author_text` at all.

### Local filters index the raw field, not the credit

Recently Added's filter searches `author_text` *and* `linked_authors`,
`primary_author` and `primary_editor` separately. A Work whose card credits a
linked Author can therefore still match on its hidden textual author. That is
existing behavior and 2I preserves it: the filter runs over effective rows, so
it matches the pending raw value -- this milestone is synchronization, not a
search redesign.

### Search membership is server-authoritative

The server decides which Works a search RETURNS and cannot know about a value
that has not been sent. Before synchronization:

- searching for a pending `author_text` does **not** discover the Work;
- searching for the acknowledged value still **does**;
- but any Work the server returns is rendered from `prksEffectiveWorksSync()`,
  so its visible credit reflects effective local state.

Changing that would require a local search index and result merging, which is
not this milestone. After acknowledgement the ordinary FTS machinery carries
the new value: `author_text` is an FTS column maintained by an `AFTER UPDATE ON
works` trigger, and the synchronized write is an ordinary UPDATE, so no manual
index maintenance exists or should be added.

### Where `author_text` is written

| Path | Revision-aware | Note |
| --- | --- | --- |
| `SET_WORK_METADATA_FIELD` | yes | the only path the editor uses, online and offline |
| `PATCH /api/works/:id` | yes | routed through `set_field_on_conn` like every synchronized field |
| `add_work()` — creation, Processing import, video oEmbed fill | n/a | all CREATE a Work; construction is not a change, so revision starts at 0 |

The Phase A audit found no other writer that mutates an existing Work's
`author_text`, and a test pins that result rather than leaving it as a claim.

### Validation

`author_text` is byte-limited at **64 KiB** — absurdly generous for an Author
or Channel name, which is the point. The number exists so that the field's size
is a PRKS *contract* rather than an accident of whichever storage layer happened
to refuse first: before 2I.1 the server accepted any length while the browser's
durable envelope stopped at 64 KiB of *serialized JSON*, so the same value was
savable online and impossible offline, and an Author name full of quotation
marks could fail for a reason no user could see.

The limit is enforced identically by the sync handler, the ordinary PATCH, the
editor before enqueue and the durable store, and it is measured in UTF-8 bytes —
a limit documented in bytes but enforced in characters does not exist for the
users most likely to reach it. Nothing is ever truncated: an over-limit value is
refused visibly, the draft stays, and nothing is stored or sent.

The editor trims leading and trailing whitespace before sending — exactly as it
did through the old PATCH, so that rule moved location without changing
meaning — and the server stores what it is given.

### Byte limits are a registry, not a branch

`BYTE_LIMITS` (server) and its mirrors in `work-metadata-state.js` and
`local-store.js` name every field whose bound is a storage concern. Membership
is the entire mechanism, and everything else is derived from it:

| Consequence | Why |
| --- | --- |
| The acknowledgement omits the value (`value_omitted: true`) | `sync_operations.result_json` has no retention policy; echoing the value back would make every edit a permanent second copy of it. The client reconstructs from its own immutable operation payload, so replay stays exact. |
| `work-metadata-state` carries the revision alone | The Work record already has the value; duplicating it into a second cached projection would double what the endpoint sends, what IndexedDB stores and what every re-read costs. |
| A conflict reports `current_preview` + byte counts | The browser bounds a durable structured result to 2 KB. Two large values would mean the client could not store the conflict at all — see below. |
| The durable store bounds the VALUE, not the JSON | Escaping doubles quotes and backslashes; measuring the encoded form would refuse a value exactly at the stated limit. The allowance is shape-scoped so it cannot smuggle an unbounded payload. |

A third large field is an entry in that registry. If it ever needs
field-specific code in any of the four rows above, the abstraction is the thing
to fix.

### A terminal result the client cannot store is worse than losing the edit

The browser persists a conflict in the durable operation row and refuses
anything over **2048 bytes of serialized JSON**. When that refusal fires the
settle fails, the coordinator reads it as a failed sync, and the operation
returns to pending — and retries forever, because the same oversized result
comes back every time. The user never reaches the conflict UI and has no way to
resolve anything. That is strictly worse than either value simply winning.

Bounding the preview by CHARACTERS did not prevent it, because a character
count, a byte count and a serialized size are three different measurements:

| Input | Code points | Column bytes | Serialized JSON bytes |
| --- | --- | --- | --- |
| `A` | 1 | 1 | 1 |
| `é` | 1 | 2 | 2 |
| `日` | 1 | 3 | 3 |
| `🧪` | 1 | 4 | 4 |
| `"` or `\` | 1 | 1 | 2 |
| `\u0001` (any C0 control without a short escape) | 1 | 1 | **6** |

So a 400-character preview of control characters serialized to ~2400 bytes, and
a 500-code-point `journal` conflict — which carries BOTH values in full —
reached over 6 KB. Both were storable by the server and unstorable by the
client.

`fit_terminal_result()` now guarantees the whole object fits, in two steps
ordered by how much the user loses:

1. A full-value result that does not fit **degrades to the bounded preview
   shape**. The client already renders that shape, and re-reads the
   authoritative value when the user takes the server's version.
2. The preview is then shortened to the longest prefix that still fits —
   by code points, so a surrogate pair is never split.

Two details make this a guarantee rather than an estimate. The server measures
with `ensure_ascii=False`, because `JSON.stringify` does not escape non-ASCII:
with Python's default the server would count `日` as six bytes where the browser
counts three, and truncate previews nobody needed truncated. And the server
measures the WHOLE answer including `work_id` and `field`, which the client's
handler projects away before storing — so the server's bound is deliberately
conservative, erring on the side the user can survive.

Because a small scalar's conflict may now arrive in either shape, the client
validates **what it received** rather than what the field's type implies. A
byte-limited field is still always bounded: accepting the full shape there
would admit a megabyte into the durable row.

## `thumb_page`: the wire value is not the column value

Every synchronized field before this one was a string in the editor, a string
on the wire and a string in the column, so those could be the same value
without anyone having to say so. `thumb_page` is where that stops being true,
and it needs four representations named explicitly:

| Boundary | Representation | Examples |
| --- | --- | --- |
| Editor control | string | `""`, `"3"` |
| Sync wire | canonical decimal string; `""` means "no explicit page" | `""`, `"3"` |
| SQLite column | `INTEGER NULL` | `NULL`, `3` |
| Work / browse row / embedded summary | `integer \| null` | `null`, `3` |

**The wire stays a string.** The operation envelope is validated, hashed,
compared and replayed as a string on both sides; widening `payload.value` to a
union type would mean touching every one of those for one field. So the
boundary converts, and `FIELD_CODECS` owns the conversion. A field with no
codec is the same string everywhere, which is why this looked like a copy for
four milestones — `copy()` in `PROJECTION_COLUMNS` was always a *conversion*
that happened to be the identity.

**A wire string in a cached row is not a cosmetic problem.** Every browse row
is validated with `prksIsOptionalNonNegativeInteger(row.thumb_page)`, so `"3"`
makes the row fail its own shape check and the whole cached catalog is
discarded as corrupt. The conversion therefore runs everywhere a value is
written into a Work-like object: the effective-Work overlay, the three
projection overlays, the embedded summaries, and acknowledgement
reconciliation. A selftest asserts the *real* validators still accept the rows
a pending value produces, and that the string it must never produce would
indeed be rejected — so the assertion cannot pass for a trivial reason.

**`metadata-state` keeps the wire form on purpose.** It is synchronization
bookkeeping: its `value` is what a base revision was observed against, in the
representation the protocol uses. The Work record is the entity. So the same
page is `"3"` in one projection and `3` in the other, and that difference is
the point rather than an inconsistency.

### Comparison happens on canonical meaning

Column `3`, wire `"3"` and wire `"003"` are one state; `NULL` and `""` are
another. Revisions record a change of STATE, so a difference in spelling
advances nothing — otherwise two devices that chose the same page would be told
they had collided, and every device holding the value would be handed
manufactured staleness.

### Refusing is not clearing

`0`, `-1`, `1.5`, `abc` and `+3` are refused, visibly, with the draft intact
and nothing enqueued. The ordinary PATCH used to *silently clear* every one of
them — a client asking for an impossible page was told the field had been
emptied on purpose — and that normalization was removed when the codec took
over. It is the same conflation an uninterpretable Published Date used to have.

### Thumbnail resource identity

A thumbnail URL with no `?page=` means "whatever page the server currently has
stored". That is not an identity the client can reason about, and it is wrong
the moment an edit is pending: with page 5 stored and a clear pending, the
effective value is `null` — page 1 — but a page-less URL would still render
page 5 until the server heard about it.

So **the page is always stated**, including `?page=1` for `null`:

```
effective 3    ->  /api/works/W/thumbnail?page=3
effective null ->  /api/works/W/thumbnail?page=1
```

`?page=1` and a stored `NULL` select the same page and the same cached artifact
— `prks_thumb_cache_stem()` normalizes `None` and any value below 1 to 1 — so
this is the acknowledged behaviour written down rather than a change to it.
Stating it always means pending and acknowledged rendering run one code path.
The cost is one browser-cache miss per card the first time, because
`/thumbnail` and `/thumbnail?page=1` are different URLs to the browser while
being the same bytes to the server. The request coordinator classifies by
`URL.pathname`, so the query does not affect it.

Online, this also means a pending page renders immediately: the endpoint
already accepts an explicit page, so nothing waits for the column to change.
A thumbnail that fails to load is not a failed save — the durable operation and
the image request are separate, and a local edit is never rolled back because
an image fetch failed.

**Offline suppression still wins, absolutely.** `suppressThumbnail` is decided
*before* any URL is derived, so a cached card emits no thumbnail source at all
— a pending metadata edit is not a reason to start asking for bytes that cannot
arrive. Only the source is removed; the layout is untouched.

### Where `thumb_page` is written

| Path | Revision-aware | Note |
| --- | --- | --- |
| `SET_WORK_METADATA_FIELD` | yes | the only path the editor uses, online and offline |
| `PATCH /api/works/:id` | yes | validated, then canonicalized through the same codec |
| `add_work()` | n/a | creation starts at revision 0 |
| `update_processing_file()` | n/a | a *draft* on the `processing_files` staging table, which becomes a Work's value at import — never a mutation of an existing Work |

The audit found no other writer. `prune_orphan_pdf_thumbnails()` reads
`thumb_page`, but runs once at startup before the server accepts connections,
so it cannot race a pending client request.

## The metadata editor: four durable groups, no online-only save

The editor has no direct-PATCH mutation path left. Every user-editable Work
metadata value belongs to exactly one bounded save:

| Group | Fields | Operation |
| --- | --- | --- |
| **Identity** | `title`, `doc_type` | `SET_WORK_METADATA_FIELD` |
| **Progress** | `status` | `SET_WORK_METADATA_FIELD` |
| **Bibliographic details** | `year`, `published_date`, `author_text`, `publisher`, `location`, `edition`, `journal`, `volume`, `issue`, `pages`, `isbn`, `doi`, `abstract`, `thumb_page`, `source_url` (non-video) | `SET_WORK_METADATA_FIELD` |
| **Video source** (video Works) | the whole identity | `SET_WORK_SOURCE` |

Each group saves only its own fields, read from the DOM, and reports only its
own conflicts -- so a DOI conflict never tells the user their Status needs a
decision. One button covering several groups would rebuild the mixed-atomicity
contract 2D removed from the online save, inside the durable path.

`submitWorkMetaEdit()` is gone. Its last version sent an EMPTY payload, which
is what finishing the program looks like from the inside. A static test scans
every PATCH body in the frontend for a synchronized field name, because this
mistake has been made twice: the Playlist inline rename PATCHed `title`, and
the metadata editor PATCHed the whole bibliographic block.

## Work values held by reference in other entity families

`title` is the first field that lives inside caches which are not Work rows at
all. A Concept's backlinks, an Argument's sources and mentions, and a Research
Graph node each hold a Work value under their own key names, identified by a
foreign column:

| Cache | Collection | Key | Columns |
| --- | --- | --- | --- |
| `concept` | `mentions[]` | `work_id` | `title` |
| `argument` | `sources[]` | `work_id` | `title` -> **`work_title`** |
| `argument` | `mentions[]` | `work_id` | `title` |
| `research-graph-core` / `-people` | `nodes[]` (where `type === 'work'`) | `record_id` | `title` -> **`label`**, `doc_type` |

One registry (`WORK_REFERENCE_SHAPES`) describes all of them, so a component
never learns what a durable operation is and the next field is an entry rather
than another traversal. The same registry drives acknowledgement
reconciliation: a Title ACK patches every cached Concept, Argument and Graph
snapshot that names the Work, generation-fenced like every other domain, and
an unreadable cache blocks retirement rather than retiring on a guess.

**Patched, never invalidated.** `prksMarkWorkTitleChanged()` used to stale
Concepts, Arguments, People and Playlists after a Title PATCH. Destroying
usable offline snapshots for a change whose exact shape is already known is
the opposite of what the reconciler exists to do, so the durable ACK path
reconciles values instead.

## Every canonical mutation advances revisions

Public add/remove, transactional bulk add/remove, Processing import, Tag merge
and Tag delete all share the connection-aware relationship boundary. Only
actual state changes advance a revision. No-op calls leave it unchanged.
Removing a relationship never deletes its revision tombstone.

Merge S → T advances every present W:S removed; W:T advances only if it was
previously absent. Work, Folder and Processing File Tag relationships migrate
in the same merge transaction as lifecycle history. Explicit Tag deletion
advances present relationships before deletion and records deleted lifecycle.

## Read models and coherence

`GET /api/tags` hashes the actual serialized representation. Same-length
renames, colors and aliases change the ETag; relationship changes do not.
`tags:index`, in the `tags` coherence domain, caches this read-only catalog.
Picker-sensitive fields (`id`, `name`, `color`, `aliases`) are validated before
publication and after cache reads. Malformed 200 responses preserve good cache;
malformed cached values are discarded best-effort and shown as unavailable.
There is no separate global RAM Tag catalog cache.

`GET /api/works/:id/tag-options` is cached as entity kind `work-tag-options`,
ID Work ID. It contains assigned active Tags and their relationship revisions,
plus `known_absent` active Tags with positive tombstone revisions. An active
catalog Tag omitted from both structures means never touched, revision zero.
Its ETag hashes its representation. It contains **no catalog ETag**; a rename
or recolor cannot stale every Work's relationship projection.

Catalog changes invalidate `tags:index`. Work, Folder and Processing File
relationship changes do not. Work relationship changes affect only that Work's
read models. Delete/merge responses also identify Works whose absent tombstones
were removed from the active-Tag projection. Server GETs expose direct-client
changes through representation ETags; there is no push or polling protocol.

## Local coalescing and overlay (Work Tags)

A relationship has at most one active local operation. Repeated pending intent
creates no duplicate. Returning to the observed base cancels the pending row
atomically, including after reload. The immutable envelope is never edited;
new intent after conflict resolution receives a new UUID.

**Only never-sent pending rows may coalesce.** A pending retry after a lost
response might already be ledgered. Its ID must survive and that Tag control
stays disabled until it settles, like a syncing control. Other Tags stay usable.
Conflicted rows also require explicit resolution rather than coalescing.

Effective Tags are acknowledged Work state plus durable pending/conflicted
operations. Unsynchronized state is never baked into cache records. A bounded
local display snapshot preserves the intent's label after catalog changes.

## Coordinator recovery and acknowledgement

Startup recovers interrupted `syncing` rows to pending under the coordinator
lock, then retries the same ID, and clears `acknowledged` residue a crash left
behind rather than resending it. Sending waits for an **observed** connectivity
result. The offline runtime starts in a provisional `online` state and
`navigator.onLine` reports link state, not PRKS reachability; claiming on
either would move a never-sent operation's attempt count off zero, and a
possibly-sent operation can no longer be coalesced or canceled locally. So the
queue stays shut until the first real reachability result arrives. Transient
failures use bounded exponential backoff with jitter; components own no retry
loops. Transport faults, malformed bodies and unexpected 2xx payloads all
return the row to retryable pending under its original ID; only recognized
protocol errors are terminal.

On ACK, the coordinator first reconciles existing Work and tag-options cache
snapshots, fencing older in-flight reads with coherence generations. Only after
cache writes succeed is the operation marked acknowledged. A failure retains
it as pending; replay repeats reconciliation safely. Missing cache bases stay
missing, and reconciliation needs no second mandatory server request. Once the
live UI has seen the ACK, the local operation is **retired**: `sync_operations`
on the server is the durable idempotency history, so the browser keeps no
growing record of completed work. Retirement is strictly last, so a crash
anywhere earlier leaves a replayable row rather than a lost edit.

### An acknowledgement states the stored row; the client never rebuilds it

A field acknowledgement can omit a byte-limited value because the client
already holds the authoritative copy in its own immutable operation. The source
aggregate is the case where that reasoning **stops being true**, and it stops in
two ways at once:

- **Derived columns.** The write also clears `thumb_url` and rewrites `urldate`.
  `urldate` is the server's own date; there is nothing for the client to
  reconstruct it from. A cached row that kept the old thumbnail shows video A's
  picture on a card that says video B.
- **Convergent writes.** Two devices choosing the same video in different
  spellings is not a change, so the server stores *nothing*. A client writing
  back "what I asked for" would then hold an acknowledged `source_url` the
  server does not have — and, worse, would keep the identity it last cached
  while the server has already moved to the converged one.

So `SET_WORK_SOURCE` acknowledges with the row as stored: all four identity
columns plus `thumb_url` and `urldate`, read back after the write. The client
copies them. This does put a second copy of the URL in the ledger, which holds
only a request hash otherwise — a real cost, accepted knowingly, because the
alternative is publishing acknowledged values the server never stored.

Reconciliation patches **only the columns a row already carries**: a browse row
has `thumb_url` but no `urldate`, and writing a column that projection never
receives makes the row fail its own shape validator, which discards the whole
catalog.

### The base a second edit is measured against has to move

An acknowledgement advances the source revision, and two places hold that
number: the cached `work-source-state` projection, and `state.observed` in an
editor that is still open. Leave either behind and the *second* change is
created against a revision the server has already passed — so the user
conflicts with their own previous edit, on a Work nobody else has touched. Both
are updated on ACK, and the cached one never moves backwards.

### One unsynchronized intent per aggregate

Choosing video B and then video C before either is sent must leave **one**
operation naming C. A bare `enqueueOperation` per save left two immutable rows
sharing one base revision: the coordinator sends B, the revision advances, and
the user's own C then arrives stale — while the screen said C the whole time.
`saveWorkSource()` coalesces on the same rule the field and tag writers use:
only a NEVER SENT row may be rewritten, and editing back to the acknowledged
identity leaves no intent at all. A row that has been attempted might already be
ledgered, so it stays immutable and the save refuses with `scope_busy`.

Identity, not URL text, decides all of this — `A -> B -> A` is no change even
when the two spellings of A differ.

### Resolving a conflict has to converge the resolver too

Retiring the conflicting operation is the smaller half of a resolution. The
device that raised it is still holding the state it raised it *from*, and the
source aggregate makes that visible in a way small scalar conflicts do not: its
terminal result carries a **bounded URL preview**, which `fit_terminal_result`
may shorten further to keep the whole object inside the client's durable limit.
There is deliberately not enough there to rebuild an authoritative source.

**Use server** therefore discards the local intent and then *re-reads*:
both the Work and `work-source-state` are invalidated — invalidating only the
Work left the cached source REVISION at its pre-conflict value, which is the
base the next save would be measured against — and the result replaces what the
tab is holding. Without that, the pending overlay disappeared and the user was
left looking at video A while the server held video C, with nothing remaining to
correct it.

Nothing asks for a "force" flag, because invalidation is what makes the read
authoritative: a read-through always tries the server first, and an invalidated
entity reports `unavailable` rather than falling back to a stale row. So if the
server is unreachable at that moment the discard still stands and the editor
stays **explicitly unavailable** — it must not fall back to the source the user
just decided against. It re-establishes the base by itself when connectivity
returns, because the editor is still on screen.

**Apply my source** creates a replacement against the revision the server
reported, and that replacement is still never-sent — so it is still coalescible.
The editor's base has to move with it, **both halves**:

- the *revision*, or changing one's mind again rewrites the replacement against
  a revision the server has already passed, and it conflicts a second time with
  the same edit;
- the *identity*, or returning to the server's own video reads as a change
  rather than as the cancellation it is — and gets sent, colliding with the very
  video it was converging on.

This is why the editor's observed base is `{revision, identity}` rather than a
bare revision, and why `SOURCE_REVISION_CONFLICT` reports `current_provider` and
`current_provider_id` beside the preview. Deriving identity by parsing the
preview would mean deriving it from a value designed to be shortened.


### Creation is the other place an identity comes into existence

Bounding the synchronization parser alone left the whole invariant bypassable,
because `add_work()` stored caller-supplied `provider`/`provider_id` verbatim
and only derived them when they were blank. A Work could therefore be created
carrying video B's URL and video A's id — so the viewer, which reads
`provider_id` first, played a video the row did not name — or with a
3000-character id that becomes unresolvable months later, when a conflict copies
it into `current_provider_id` and `fit_terminal_result` cannot shorten an
identity.

So `add_work()` enforces the same canonical parser, for every Work the product
will *treat* as a video rather than only those whose caller said so —
`effective_source_kind()` mirrors the runtime's own `prksInferWorkSourceKind()`,
and `source_kind = "video"` is persisted rather than left NULL, so a row the UI
calls a video is one the aggregate can act on. It is the right boundary rather
than the HTTP handler because it also covers scripts, fixtures, imports and any
future internal caller. A contradictory pair is **refused, not
repaired**: a caller passing video B's URL with video A's id has a bug, and
rewriting it silently would hide that bug while leaving the caller believing it
had asserted an identity. `provider`/`provider_id` are refused outright on a
Work that is not a video — they *are* video identity, and the kind inference can
still reach a video branch for a Work with no explicit kind and no file, where
`provider_id` would outrank the URL again.

Creation advances no revision: construction is not mutation, and a Work begins
at source revision 0 like every other scope.

The invariant this completes, from creation through every later mutation and
conflict:

> No video Work can enter canonical storage unless its URL, provider and
> provider_id describe one parser-validated, bounded source identity.

### Legacy rows are read, not migrated

Canonical creation can no longer produce a video Work without an explicit kind
and a derived identity, but older databases can. Those rows are classified by
the same `effective_source_kind()` the product reads them with, and their
identity is derived from the URL they already carry when none is stored — so
the source endpoint and the source mutation never disagree about whether a row
is a video. A row whose identity cannot be stated at all answers
`INVALID_SOURCE_STATE` on both, rather than one inventing an answer the other
refuses. See docs/work-source-identity.md for what is deliberately not guessed.

### The URL has a bound too, on both sides

`canonical_source()` bounds the URL at `MAX_SOURCE_URL_UTF8_BYTES`, and the
client did not — so it accepted a URL the server refuses. Not an
acknowledged-state corruption, since the durable store rejects it eventually,
but the user was told "could not save the video source locally" rather than that
the URL was too long. Parity that holds for identity and not for size is not
parity.

Both sides now follow one order: **trim, measure what would be stored, then
parse**. Measuring the raw input rejected a URL whose canonical form fits, purely
for surrounding whitespace the product removes. The measurement is in UTF-8
bytes on both sides — JavaScript's `.length` counts UTF-16 code units, so a limit
documented in bytes would silently not exist for the URLs most likely to reach
it.

### Identity is exact *because* it is bounded

The conflict makes two promises: identity is reported **exactly** — a truncated
video id names a different video, or none — and the whole terminal result
**always fits** the client's 2 KiB durable bound, so a conflict can always be
stored and therefore always be resolved.

Those are not independently grantable. While `provider_id` was "whatever
followed `v=`", a 3000-character id produced a ~3.2 KB conflict that stayed over
the limit *with the preview deleted entirely* — an acknowledgement that could
neither be stored nor shown, on a Work the user could then never fix. 400
percent-encoded control characters did the same at ~2.6 KB, because each one
serializes to six bytes as `\u0001`.

So identity has a canonical representation: `MAX_PROVIDER_ID_CHARS` (512) over
`[A-Za-z0-9_-]`, enforced in the one YouTube parser on each side — the point
where an id comes into existence. A URL whose id is not a well-formed
identifier has no video id at all and is refused at the boundary, rather than
stored and discovered later by a conflict that cannot be delivered. The alphabet
also settles percent-encoding, which is where the two parsers could most easily
diverge (a query value is decoded on both sides, a path segment on neither):
`%` is not a legal identifier character, so every such spelling is refused by
both.

512 is generous on purpose — a real YouTube id is 11 characters, and the
arithmetic ceiling where a worst-case conflict stops fitting is 1842. The margin
is checked by a test that builds the largest legal id on both sides together
with a preview long enough to be capped, and requires the fitted result to fit
anyway; raising the constant into the danger zone fails it.

### An ambiguous local history is refused, not guessed

One active `SET_WORK_SOURCE` per Work is the invariant, but a store written
before coalescing existed can already hold several. `getAll()` order is not a
decision, so acting on whichever row comes back first would resolve that history
differently on different devices. `saveWorkSource()` refuses with the count
instead, and repairs nothing: the rows are immutable user intent, and collapsing
them by guesswork here would destroy a choice nobody reviewed. Each conflict or
send ahead of them reduces the history to one on its own.

### Reappliability is a per-family registry

"Apply my value" re-sends the same intent against the revision the server
reported, so a terminal code qualifies only when it names one. The store
hard-coded the single string `REVISION_CONFLICT`, which meant the aggregate's
`SOURCE_REVISION_CONFLICT` reached the user with a button that threw when
pressed — a decision the UI offered and the store refused. `REAPPLIABLE_RESULTS`
lists the codes per family, and a static test pins it to what the editors
actually offer. `FUTURE_REVISION` is deliberately absent: a base *ahead* of the
server is not a stale edit the user can choose to win.

### The aggregate boundary binds the legacy PATCH too

`PATCH /api/works/:id` accepted `source_kind`, `provider`, `provider_id` and
`thumb_url` by name and validated none of them, so a client could write
`provider_id` alone and recreate exactly the contradiction this aggregate
prevents — with no revision advanced, so no other device could discover it.
Editing an existing Work now refuses those columns and says which operation to
use. Creation and import are unaffected: they write the whole identity at once
through `add_work`, where there is no prior value to contradict.

### An acknowledgement may only write the panel it owns

The right panel is shared by every workspace tab, so `#panel-content` and every
id inside it — `#meta-title` among them — belong to whichever tab currently owns
it, not to the tab whose operation is being acknowledged. The cached record and
the tab's own entity are addressed by id and are safe to patch from anywhere;
the DOM is not. An acknowledgement for an unfocused Work therefore writes its
entity and cache unconditionally, and touches an input **only** when its own ctx
still owns and focuses the panel, and then only by querying inside that panel.
A global `document.querySelector` there would let a background acknowledgement
publish one Work's value into the editor of another — which is the same
cross-Work publication the online PATCH path had to be guarded against, arriving
by a different route.

The same rule applies to reading: any Work value the detail panel renders
itself, rather than through the synchronized rows the editor repaints, must be
read from the EFFECTIVE Work (`prksEffectiveWorkSync`). `source_url` is the case
that made this concrete — the detail's "Original URL" row is a second rendering
of a synchronized field, and rendering it from the acknowledged record would
have shown a stale address in a link the user can click.

## Work-Person roles: an element, and what an element contains

The conflict unit is `work-person-role / [work_id, person_id, role_type]`, chosen
from the schema rather than from convenience. `roles`'s primary key includes
`order_index`, so ordering had to be ruled out deliberately: `add_role()` already
refused a second row for an existing triple, `order_index` is assigned by the
server as "append after what is there", **nothing in the product updates it** —
there is no reorder operation — and nothing requires the values to be
contiguous. Independent element operations therefore cannot produce an invalid
author order, and an aggregate would have made every unrelated link on one Work
a single conflict. The database now enforces that identity too: a unique index
on `(person_id, work_id, role_type)`, because the primary key alone would permit
two rows for one revision scope.

### The element's state is not a boolean

A link carries `credit_name` — the name as printed on *this* work — and that
value reaches `linked_authors`, the card credit, BibTeX and the Person's
aliases. So an element's canonical state is **absent**, or **present with a
credit override**. Two devices that both link Jane as Author with the same
credit have converged; two that choose different names have not, and a model
comparing only presence would have called that agreement and silently kept one.

`ADD_WORK_PERSON_ROLE` therefore carries `credit_name` — linking with a custom
credit must not need two operations, which would briefly display the wrong name
— and `SET_WORK_PERSON_ROLE_CREDIT` edits it later under the same scope and
revision. An operation named ADD that silently edited an existing link would be
harder to reason about than a second named operation.

### Construction is not mutation

A Work born with two Authors has not changed twice. `insert_initial_role()` is
the construction boundary: it validates, preserves the order the caller states —
at creation the caller *is* the authority on author order — and creates **no
revision**. `set_role_state()` is the mutation boundary: it validates, appends
server-side, and advances the revision on any semantic change. Work creation and
processing-file import use the first; `POST /api/roles`, the role DELETE, the
annotation `Mentioned` link and every durable operation use the second.

Routing construction through the mutation boundary made every created Work start
at revision 1 and discarded the importer's author order.

### One role vocabulary

Eight roles, validated at the canonical boundary rather than in the envelope
validator alone — `Producer` used to be refused offline and accepted online, and
the accepted row then matched no filter, icon or BibTeX mapping. Refused, never
normalized: turning an unknown role into `Author` would assert a relationship
the user never described.

### What a browse row has to carry

The flattened credit columns (`linked_authors` joined, `primary_author`,
`primary_editor`) cannot support local relationship arithmetic: a pending
removal cannot be subtracted from `"Ann Lee, Bo Ng"` because names contain
commas, and a pending addition cannot tell whether an Author remains — which is
what decides Author vs `author_text`. So rows also carry `linked_people` with
`person_id`, `role_type`, `order_index`, `canonical_name`, `credit_name` and
`display_name`, in the same order the flattened columns use.

Both names, not just the resolved one: clearing an override has to reveal the
canonical name, and `"Mark Twain"` cannot be turned back into `"Samuel
Clemens"`. A row is self-sufficient for what it promises to render rather than
depending on a Person cache being present.

## User controls

Open a Work's **Manage tags** panel online once to prepare its catalog and
relationship snapshot. Offline Add needs a cached Work, catalog and tag-options.
Remove needs the Work and tag-options, so a missing catalog need not block it.
Creating a Tag still requires a connection and is never an actionable offline
picker result.

The panel reports Offline · saved locally, Waiting to sync, Syncing, Conflict,
Sync failed, or All changes synced. Settings → Diagnostics shows durable
unsynchronized changes independently of disposable cache availability.

A revision conflict keeps local intent visible. **Use server state** reconciles
the reported server state and explicitly discards the conflict. **Apply my
change** creates a new operation against the reported current revision.
Merged Tags are not automatically redirected; the user can discard the local
change. Deleted/unknown Tags are never recreated. Diagnostics also permits
explicit conflict discard when the original Work is no longer available.
Structured terminal results are allowlisted and size-bounded, separate from
short retry error messages. None of this content belongs in logs.

## Deferred beyond the current milestone

No offline Tag creation/rename/merge/delete, Folder mutation, Work
creation/deletion, Playlist mutation, Concept editing or Research Notes
editing, and no synchronization for the Work fields still listed as deferred
below. No CRDT, multi-user sync, batching, server push or automatic lifecycle
retargeting.

Every user-editable Work metadata value is now local-first. What remains
outside `SYNCED_FIELDS` is not a field but an **identity**: `source_kind`,
`provider` and `provider_id` describe, together with `source_url`, which video
a Work *is* -- and `provider_id` outranks the URL when the viewer builds its
embed. Three field-scoped operations would let two ordinary edits reach "the
stored URL names video B while the viewer plays video A", and would ask the
user to resolve one decision three times. So they are owned by the
`SET_WORK_SOURCE` aggregate instead; see below and
[work-source-identity.md](work-source-identity.md).

`source_url` itself is in the registry, but **guarded**: a field-scoped write
is refused on a Work whose kind is explicitly video, because there it is one
spelling of that identity rather than provenance.

### Source transitions this milestone deliberately does not support

`SET_WORK_SOURCE` replaces one YouTube video with another on an existing video
Work. It refuses, with `UNSUPPORTED_SOURCE_TRANSITION`, everything else:

| Transition | Status |
| --- | --- |
| YouTube A -> YouTube B | supported |
| A different URL spelling of the same video | a no-op: same identity, no revision |
| PDF -> video | **refused** |
| video -> PDF | **refused** |
| video -> no source | **refused** |

None of those has a UI, and none has defined product semantics for what should
happen to `file_path`, to which viewer renders, or to the PDF's own thumbnail
cache. They are refused rather than invented.

The registry is the authority on what synchronizes; this section is a note on
what is deliberately not a field.
