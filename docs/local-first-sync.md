# PRKS local-first synchronization

PRKS has one semantic-operation protocol and **two families** on it:

| Family | Milestone | Shape |
| --- | --- | --- |
| `ADD_WORK_TAG` / `REMOVE_WORK_TAG` | 2B | Revisioned relationship. Two devices can genuinely disagree, so conflicts are real and the user resolves them. |
| `MARK_WORK_OPENED` | 2C | Max-register over normalized event time. Two devices cannot disagree, so there is no conflict and none is offered. |

Both commit to durable browser storage before the UI acts on them, survive
reloads and offline periods, and synchronize idempotently on reconnect. Other
mutations still require the server.

The second family exists to prove the protocol is a protocol. Its semantics are
deliberately unlike the first -- no concurrency control, no conflict UI, a
different result set, and a different failure posture -- so anything the two
share had to become generic rather than Work-Tag-shaped.

## Layers

| Layer | Owns |
| --- | --- |
| `backend/sync_protocol.py` | Envelope normalization, request hashing, ledger, OP_ID_REUSE, exact replay, one transaction, dispatch |
| `backend/work_tag_sync.py` | Relationship revisions, Tag lifecycle, tag-options, ADD/REMOVE handler |
| `backend/work_open_sync.py` | `last_opened_at` max-register, skew policy, MARK_WORK_OPENED handler |
| `frontend/js/sync-runtime.js` | Transport, claiming, backoff, locks, replay, status transitions, retirement |
| `frontend/js/work-tag-state.js` | Work-Tag overlay and sync handler |
| `frontend/js/work-open-state.js` | Recent overlay, acknowledged merge, sync handler |

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

`ADD_WORK_TAG`, `REMOVE_WORK_TAG` and `MARK_WORK_OPENED` are supported; an
unregistered operation is `INVALID_ENVELOPE` and never reaches a handler.
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

## Deferred beyond 2C

No offline Tag creation/rename/merge/delete, Folder mutation, Work metadata,
Work creation/deletion, Playlist mutation, Concept editing or Research Notes
editing. No CRDT, multi-user sync, batching, server push or automatic lifecycle
retargeting. A third family should be chosen from what these two taught us, and
must arrive as a handler registration rather than a second protocol.
