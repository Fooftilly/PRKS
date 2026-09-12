# PRKS local-first synchronization

PRKS has one semantic-operation protocol and **two families** on it:

| Family | Milestone | Shape |
| --- | --- | --- |
| `ADD_WORK_TAG` / `REMOVE_WORK_TAG` | 2B | Revisioned relationship. Two devices can genuinely disagree, so conflicts are real and the user resolves them. |
| `MARK_WORK_OPENED` | 2C | Max-register over normalized event time. Two devices cannot disagree, so there is no conflict and none is offered. |
| `SET_WORK_METADATA_FIELD` | 2D, 2E, 2F | Revisioned scalar, scoped to one FIELD. Devices disagree per field, so conflicts are real but narrow. |

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

Ten bibliographic scalars synchronize: `abstract`, `publisher`, `location`,
`edition`, `journal`, `volume`, `issue`, `pages`, `isbn`, `doi`. Each owns a revision scope
`work-field / ["<work id>", "<field>"]` -- the same structural JSON encoding the
Work-Tag scopes use, so no delimiter has to be excluded from either component.
A missing row is revision 0. No schema change was needed: these live in the
existing `sync_entity_revisions` table.

The envelope payload is `{"field": ..., "value": ...}`, one operation type for
all of them rather than one per field: the semantic act is "set one supported
field to one scalar value", so 2E added `publisher` and `location` as registry
entries and nothing else. The field must be in the server's own registry -- an
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
verbatim into its projection, and it needed three decisions the other nine did
not.

**Size.** `MAX_ABSTRACT_UTF8_BYTES` is **1 MiB**, a deliberate PRKS product
rule rather than a measurement — an abstract is bibliographic summary text, and
long-form material belongs in Research Notes. It is enforced identically by the
ordinary PATCH, the sync handler, the durable local store and the editor before
enqueue, because an Abstract savable online and refused offline would be exactly
the split contract that moving a field to local-first exists to remove. It is
measured in **UTF-8 bytes**, never `len()`: a limit documented in bytes but
enforced in characters does not exist for the users most likely to reach it.
An over-limit value is refused visibly — the draft stays, nothing is enqueued,
nothing is sent. Nothing is ever truncated. The nine small scalars keep their
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
Abstract.

## Projection transforms

`FIELD_PROJECTIONS` says *which* cached projections a field reaches;
`PROJECTION_COLUMNS` in `work-metadata-state.js` says *how*:

| Field | Projection | Column | Transform |
| --- | --- | --- | --- |
| `publisher` | `recently-added` | `publisher` | copied |
| `abstract` | `works-browse` | `abstract_excerpt` | **derived** (first 100 code points) |

Consumers ask for effective rows and never interpret durable operations
themselves, so a third field is a table entry rather than an `if` inside
whichever component happens to render it. The acknowledged patch applied at
reconciliation uses the *same* transform, so a row cannot visibly change when
the server answers.

## Fan-out: which fields reach which projections

Eight of the nine are rendered on the Work detail and nowhere else. The Work
summary projection carries them, so cached Folder, Person and Playlist details
hold them in their payloads -- but no Work card, browse catalog, Concept,
Argument or Graph surface displays them, so a pending value needs no optimistic
propagation beyond the Work itself, and an acknowledgement invalidates no browse
catalog.

`publisher` is the exception, and 2E exists because of it. **Being invisible on
a card is not the same as being unused:** `recently-added:index` selects
`works.publisher` because Home -> Recently Added filters LOCALLY over it, so a
pending publisher has to reach that projection's filtering. `FIELD_PROJECTIONS`
in `backend/work_metadata_sync.py` and `work-metadata-state.js` names that
dependency on both sides, and the parity is pinned by a test -- an earlier
version of this table claimed publisher reached nothing else, which was wrong.

The deferred fields do not share that property:

| Field | Also rendered or matched by |
| --- | --- |
| `source_url` | every Work card, via `prksInferWorkSourceKind()` deciding the thumbnail kind |
| `thumb_page` | every Work card, via the thumbnail URL |
| `year`, `published_date`, `author_text` | every Work card's meta and credit lines, in all three browse catalogs and in cached Folder / Person / Playlist details |
| `status` | Work card badges, the Progress route's grouping, and the same cached details |
| `doc_type` | Work card badges, the Types route's grouping, the Graph |
| `title` | all of the above, plus Concept details (mention titles), Argument details (source Works), Graph snapshots and the command palette |

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

## Deferred beyond 2F

No offline Tag creation/rename/merge/delete, Folder mutation, Work
creation/deletion, Playlist mutation, Concept editing or Research Notes
editing, and no synchronization for the high fan-out Work fields in the table
above. No CRDT, multi-user sync, batching, server push or automatic lifecycle
retargeting.

The remaining Work fields -- `title`, `status`, `doc_type`, `year`,
`published_date`, `source_url`, `author_text`, `thumb_page` -- are a separate
problem. Every one of them is rendered on Work cards across three browse
catalogs and inside cached Folder, Person and Playlist details, and `title`
additionally reaches Concept mention titles, Argument source Works, Graph
snapshots and the command palette. `status` and `doc_type` also decide which
*group* a card belongs to on Progress and Types, so an overlay would have to
move rows between sections rather than rewrite text in place. None of that is
answered by the transform table above.
