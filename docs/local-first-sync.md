# PRKS local-first synchronization

Milestone 2B implements **existing Work Tag add/remove** online and offline.
The operation commits to durable browser storage before the UI claims it is
saved locally. It survives reloads and offline periods, synchronizes
idempotently on reconnect, and detects conflicting changes to the same
Work/Tag relationship. Other mutations still require the server.

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

Only `ADD_WORK_TAG` and `REMOVE_WORK_TAG` are supported. UUIDs, bounded IDs,
nonnegative safe-integer revisions and timezone-aware timestamps are validated.
Unknown envelope/payload fields are rejected. Dependencies must be empty in v1;
there is no batch or dependency executor. Hashes cover the normalized immutable
semantic envelope, with sorted JSON keys and UTC timestamps.

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

ACK includes `work_id`, `tag_id`, `present`, `server_revision`, `changed`, and
the Tag display snapshot needed to reconcile without a mandatory extra GET.
Conflicts include current revision, current state and requested state.
Terminal semantic outcomes retain their original HTTP classification.

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

## Local coalescing, overlay and recovery

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

## Deferred beyond 2B

No offline Tag creation/rename/merge/delete, Folder mutation, Work metadata,
Work creation/deletion, open-event synchronization, Playlist mutation, Concept
editing or Research Notes editing. No CRDT, multi-user sync, batching, server
push or automatic lifecycle retargeting. Future work should be chosen from the
observed Work Tag behavior and tests, with a separate mutation contract.
