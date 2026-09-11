# PRKS local-first synchronization design

Authoritative design document for the local-first transition. Written during
**Milestone 2A**, which implemented the durable local-storage foundation and
settled the architectural contracts below.

**PRKS does not support offline mutations today.** Every mutation is still
refused while the server is unreachable. This document describes what is being
built toward and the decisions already taken, so that later milestones
implement a design rather than rediscover one.

Status key used throughout: **DECIDED** (settled, implement as written) /
**DEFERRED** (deliberately open, with the reason).

---

## 1. Storage boundary — **DECIDED**

Two IndexedDB databases, physically separate:

| Database | Module | Contents | Lifecycle |
| --- | --- | --- | --- |
| `prks-offline-v1` | `offline-store.js` | Downloaded server data: entities, lists, browse/graph snapshots | **Disposable.** May be discarded when corrupt; "Clear offline cache" empties it. |
| `prks-local-v1` | `local-store.js` | Durable user-owned state: `operations`, `metadata` (device id, sequence) | **Durable.** Removed only by an explicit, separately-confirmed reset. |

The separation is physical rather than conventional, and that is the point: a
bug in `offline-store.clearAll()` operates on a different database and
*cannot* reach pending user work. `tests/test_frontend_local_store.py` fails
the build if either module references the other's database name in code, or if
the offline runtime learns about `createPrksLocalStore` /
`resetDurableLocalState`. `run_local_store_selftest.js` additionally proves
operations and the device id survive both `clearAll()` and outright deletion of
the cache database.

**Failure contract — deliberately inverted.** `offline-store.js` never rejects;
a missing cache is survivable, so it degrades to "unavailable" and the app
continues. `local-store.js` does the opposite: every write either resolves
having **committed**, or rejects with a coded error. A dropped cache entry
costs a refetch; a dropped operation is a lost user change. Nothing may render
"Saved locally" on a write this module did not commit.

**Commit semantics.** Writes resolve from the transaction's `oncomplete`, never
a request's `onsuccess` — a request can succeed inside a transaction that later
aborts and rolls the row back. The selftest forces exactly that case and
asserts `enqueueOperation()` rejects and the row is unreadable afterwards.

Settings will eventually show **Cached data** and **Unsynchronized changes** as
two separate things. "Clear offline cache" must never offer to remove the
second.

## 2. Operation envelope — **DECIDED**

Stored in `prks-local-v1 / operations`, keyed by `op_id`.

```json
{
  "op_id": "f81d4fae-7dec-41d0-a765-00a0c91e6bf6",
  "device_id": "…",
  "operation": "ADD_WORK_TAG",
  "entity_type": "work",
  "entity_id": "W-A1B2C3D4",
  "payload": { "tag_id": "T-12345678" },
  "base_revision": 17,
  "occurred_at": "2026-09-11T16:04:05.123Z",
  "created_at": "2026-09-11T16:04:05.123Z",
  "sequence": 42,
  "depends_on": [],

  "status": "pending",
  "attempt_count": 0,
  "last_attempt_at": null,
  "last_error": null,
  "acknowledged_at": null,
  "server_revision": null
}
```

Everything above the blank line is the **immutable semantic envelope**; only
the fields below it may be updated, through `updateOperationSyncState()`. The
selftest asserts that attempting to rewrite `operation`, `entity_id`,
`payload`, `base_revision`, `created_at` or `depends_on` through that method
has no effect. Rewriting history in place would make a partially-synced queue
unreconstructable; coalescing (§8) must therefore write new rows in an explicit
transaction, not mutate old ones.

`sequence` is a monotonic counter advanced **in the same transaction** as the
operation row, so a crash cannot hand two operations the same value. It gives
durable user-action order, which dependency resolution and coalescing both
need — `created_at` alone cannot, since wall-clock time is not monotonic.

**Operation types are an allow-list**, not free text. Milestone 2A registers
only `MARK_WORK_OPENED`, `ADD_WORK_TAG`, `REMOVE_WORK_TAG` — the ones whose
semantics are settled below. An unregistered type is refused at enqueue, so a
typo or half-built feature cannot persist an envelope no coordinator can send.

**Never serialized HTTP requests.** An envelope carrying `{method, url, body}`
would make synchronization an HTTP replay queue: unmergeable, unversionable,
and impossible to reason about when two devices touch the same record. The
server owns the mapping from semantic operation to SQL transaction. A static
test forbids `method`/`url` keys in the module's code.

**Bounds.** Payloads are capped at 64 KB and `last_error` at 500 characters.
Both exist now, before text/CRDT operations make unbounded payloads tempting.
`last_error` is diagnostics only and must never carry credentials or raw
response bodies.

## 3. Device identity — **DECIDED**

A random UUID in `prks-local-v1 / metadata`, created on first use. It survives
reload, browser restart and "Clear offline cache"; only an explicit durable
reset removes it.

It is a **synchronization and diagnostics identity only** — never trust, login,
or authorization. PRKS authentication remains an entirely separate concern, and
nothing in the sync protocol may treat `device_id` as evidence of anything.

It is **random, never derived** from user agent, platform, hostname, screen or
any other characteristic: it identifies an install, and must not become a
fingerprint. A static test forbids those sources.

## 4. Operation ids — **DECIDED**

Full UUIDs via `crypto.randomUUID()`, with a `getRandomValues()` fallback. If
neither exists the module **throws rather than minting a weak id** — an id that
must stay globally unique forever is not a place to degrade gracefully.

PRKS's short entity-id format is deliberately *not* used here (see §5).

## 5. Offline entity ids — **RECOMMENDATION, not implemented**

`generate_id(prefix)` produces `prefix + uuid4().hex[:8].upper()` — e.g.
`W-A1B2C3D4`. That is **32 bits** of randomness.

Measured birthday collision probability:

| Entities | 32 bits (today) | 64 bits | 80 bits |
| --- | --- | --- | --- |
| 1,000 | 0.012% | ~0 | ~0 |
| 10,000 | **1.16%** | ~0 | ~0 |
| 100,000 | **68.8%** | 0.0000000271% | ~0 |

Two findings:

1. **Even server-side, 32 bits is thinner than it looks.** No `generate_id()`
   call site retries on collision — the four `sqlite3.IntegrityError` handlers
   in `db_manager.py` cover saved-view names and annotation ownership, not id
   reuse. A collision today surfaces as an unhandled `IntegrityError`. At
   personal-library scale (hundreds to low thousands) that is unlikely but not
   structurally prevented.
2. **It is clearly insufficient for independent client generation.** With no
   central allocator, a collision is not detected until sync, by which point
   two devices each hold a record they believe is theirs.

**Recommendation:** when offline creation is enabled, new ids use a longer
random suffix (≥64 bits, e.g. `W-A1B2C3D4E5F67890`) generated identically by
server and clients. Existing short ids stay valid **forever**; nothing is
migrated. The audit found no fixed-length assumption that would break: the
graph focus regexes (`app.js`, `navigation.js`) accept
`[A-Za-z0-9][A-Za-z0-9._-]*`, and every schema column is `TEXT`.

Before enabling this, re-check exports/imports, wiki-link references and any
BibTeX key derivation. **Not in scope for 2A or 2B.**

## 6. Server idempotency — **DECIDED (design)**, not implemented

The case that must be safe:

```
client sends operation  →  server commits  →  network dies
                        →  client retries the same op_id
```

A durable ledger, `sync_operations`:

| Column | Purpose |
| --- | --- |
| `op_id` | PRIMARY KEY |
| `device_id` | diagnostics |
| `operation_type`, `entity_type`, `entity_id` | what was applied |
| `request_hash` | canonical hash of the immutable envelope |
| `status` | acknowledged / conflict / failed |
| `result_json` | the acknowledgement to replay |
| `applied_at` | when |

In-memory deduplication is **insufficient** — the acknowledgement must survive
a server restart.

**The application and the ledger row are ONE transaction:**

```
BEGIN
  SELECT … FROM sync_operations WHERE op_id = ?      -- replay?
  apply the canonical semantic mutation
  advance the affected revision(s)
  INSERT INTO sync_operations …
COMMIT
```

Applying the mutation in one transaction and recording the op in another
reintroduces exactly the failure the ledger exists to prevent: a crash between
them leaves a mutation applied and unacknowledged, and the retry applies it
twice. This is the same invariant already enforced for folder deletion and tag
removal (`tests/test_folder_atomicity.py`).

**Same `op_id`, different payload** must NOT return idempotent success. The
stored `request_hash` detects it and the server returns a controlled protocol
error — this is client corruption, and silently accepting either version would
hide it.

## 7. Revision model — **RECOMMENDATION**

**Not a `revision` column on every table.** A PRKS logical entity spans several:
a Work touches `works`, `folder_files`, `work_tags` and `roles`.

**Not one revision per Work either.** With a single `work` revision:

```
Device A (offline):  ADD_WORK_TAG(W, X)     base_revision = 17
Device B:            rename W               revision 17 → 18
```

A's tag operation would be reported as conflicted despite touching logically
independent state. That teaches users to dismiss conflict prompts, which is
worse than having none.

**Recommended: revision scope per operation type**, in a
`sync_entity_revisions` table keyed `(scope_type, scope_id)`:

| Operation | Revision scope |
| --- | --- |
| `UPDATE_WORK_METADATA` | `work-metadata` / `W-…` |
| `MOVE_WORK_TO_FOLDER` | `work-folder` / `W-…` |
| `ADD_WORK_TAG`, `REMOVE_WORK_TAG` | `work-tag` / `W-…:T-…` (see §9) |
| `ADD_PLAYLIST_MEMBER`, `REMOVE_PLAYLIST_MEMBER` | `playlist-membership` / `PL-…` |
| `UPDATE_CONCEPT` | `concept` / `C-…` |
| `DELETE_WORK` | every `work-*` scope for that Work |
| `MARK_WORK_OPENED` | none — see §8 |

Do not fragment further without a case that needs it. Each scope is a place
conflicts can be reported, and an empty one is pure cost.

**Revisions are not ETags — DECIDED.** They answer different questions:

- **ETag:** "has this HTTP *representation* changed?" Derived from the
  serialized response (`etag_for_representation()`), covers derived and joined
  fields, and changes for reasons that are not mutations at all — e.g.
  `file_size_bytes` is read from disk at serialization time.
- **Revision:** "has this logical synchronization *aggregate* changed since my
  base state?"

Reusing ETags as mutation revisions would report a conflict whenever any joined
display field moved. Keep them separate.

## 8. `MARK_WORK_OPENED` — **RECOMMENDATION**

The smallest useful semantic operation, and a good protocol exercise: simple,
non-destructive, already explicit
(`POST /api/works/:id/opened` → `db.mark_work_opened()`), and naturally
coalescible.

### Timestamp semantics

`last_opened_at` is `CURRENT_TIMESTAMP` — **one-second resolution**. Opening A
at `17:00:00.100` and B at `17:00:00.800` lands both in the same second, after
which the `id ASC` tie-break decides, not actual order. That is deterministic
and adequate for a single-writer read-only cache, but once offline opens
synchronize the ordering key has to mean something.

| Option | Problem |
| --- | --- |
| A — server application time | An offline open synced hours later appears artificially recent. Wrong for "recently opened". |
| B — trusted client time | Device clocks can be badly wrong; one skewed device pins itself to the top forever. |
| **C — client event time, server-validated** | Needs skew handling, but is the only option that preserves user meaning. |
| D — logical ordering (Lamport/vector) | Correct, but overkill for a convenience list, and unhelpful for a UI that shows a date. |

**Recommended: option C.** The operation carries `occurred_at` (client event
time); the server stores it *and* its own `received_at`. Recent orders by
`occurred_at` with a deterministic secondary key. The server clamps values
beyond a bounded future skew, and stores sub-second precision so ties become
rare rather than routine.

This also means `last_opened_at` should gain sub-second resolution when this
lands — a schema/format change to settle in 2B, not now.

### Coalescing

For `open W → open X → open W`, only the latest timestamp per Work is
observable in the representation, so keeping three operations is waste.
**Recommended:** coalesce to one `MARK_WORK_OPENED` per `entity_id` holding the
latest `occurred_at`, while preserving relative order between *different*
Works. Because envelopes are immutable, coalescing writes a new row and
supersedes the old ones in one explicit transaction — never an in-place edit.

`MARK_WORK_OPENED` takes **no `base_revision` and has no revision scope**: it
is a last-writer-wins activity stamp by nature, cannot conflict meaningfully,
and giving it a revision would manufacture conflicts over a convenience list.

## 9. Conflict policy — **RECOMMENDATION**

Categories, and where each existing PRKS mutation falls. Transactionality below
is from an audit of `backend/db_manager.py`; single-statement methods are
atomic by SQLite autocommit.

| Operation | Aggregate | Category | Transactional | Offline priority |
| --- | --- | --- | --- | --- |
| `ADD_WORK_TAG` / `REMOVE_WORK_TAG` | work-tag | A/B | yes | **1st** |
| `MARK_WORK_OPENED` | — | A | yes | 1st (protocol exercise) |
| `UPDATE_WORK_METADATA` (title, status, doc type) | work-metadata | C | yes | 2nd |
| `MOVE_WORK_TO_FOLDER` | work-folder | C (exclusive) | yes | 3rd |
| `ADD_PLAYLIST_MEMBER` / `REMOVE_PLAYLIST_MEMBER` | playlist-membership | A/B | yes | 4th |
| Folder create / update | folder | C | yes | later |
| Folder tags | folder-tag | A/B | yes | later |
| Roles (add/remove/credit) | work-roles | A/B | yes | later |
| Concept / Position / Argument create, update | per-entity | C | yes | later |
| Argument sources / targets | per-relationship | A/B | yes | later |
| Person update, Person Groups | per-entity | C | yes | later |
| Any `DELETE_*` | per-entity | **D** | mostly | late |
| Playlist reorder | playlist-order | **E** | yes | last |
| Research Notes | notes | **F** | yes | CRDT milestone |
| Managed PDF save | work-file | C | n/a | not offline |

- **A — commutative / set-like.** Different elements merge automatically.
- **B — same-element semantic conflict.** `ADD X` vs `REMOVE X`: see below.
- **C — exclusive scalar or reference.** Competing edits need detection.
- **D — delete vs anything.** Explicit conflict initially. Never silently
  resurrect and never silently discard.
- **E — ordered structure.** Reorder is substantially harder than membership
  and must be separated from it. Not the first offline mutation.
- **F — long-form text.** CRDT candidate, after structured sync is reliable.

**One caveat found during the audit:** `import_processing_file()` performs
several writes (work, roles, tags, folder) without one enclosing transaction,
relying on a compensating `delete_work_record()` on exception. That is weaker
than the all-or-nothing guarantee the sync engine assumes. It is not on the
offline path, but it should be tightened before any operation depends on it.

## 10. Work Tags — first candidate, **DESIGNED, not enabled**

Modelled as `ADD_WORK_TAG(work_id, tag_id)` / `REMOVE_WORK_TAG(work_id, tag_id)`
— never "replace tags with `[...]`", which cannot merge.

| Case | A | B | Result | Automatic? |
| --- | --- | --- | --- | --- |
| Different tags | `ADD X` | `ADD Y` | `{X, Y}` | yes |
| Same add | `ADD X` | `ADD X` | `{X}` | yes (idempotent) |
| Same remove | `REMOVE X` | `REMOVE X` | `{}` | yes (idempotent) |
| **Add vs remove, same tag** | `ADD X` | `REMOVE X` | **see below** | **no** |

**Recommended revision scope: the relationship itself, `(work_id, tag_id)`** —
not the Work's whole tag collection. This gives exactly the behavior wanted
without a CRDT:

- `ADD X` and `ADD Y` touch different scopes → independent, merge naturally.
- `ADD X` and `REMOVE X` touch the **same** scope → detected as a conflict via
  `base_revision`, which is precisely the one case that needs it.

**Add-vs-remove-same-tag: recommend explicit conflict for the first
implementation.** Add-wins and remove-wins are both defensible, and a causal
observed-state rule (remove only wins over adds it causally observed) is the
principled answer — but it requires causality metadata the protocol does not
have yet. Explicit conflict is honest, rare in practice, and does not foreclose
a better rule later. **Do not leave this implicit.**

## 9a. Tag identity is persistent — **DECIDED, implemented (2A.2)**

Only `delete_tag()` and `merge_tags_into()` may destroy or transform a Tag.
Relationship edits change relationships and nothing else:

| Operation | Effect |
| --- | --- |
| `ADD/REMOVE` a Work or Folder tag | the relationship only |
| Delete a Work | its `work_tags` rows cascade; Tag survives |
| Delete a Folder | its `folder_tags` rows cascade; Tag survives |
| bulk `remove_tags` | relationships only |
| **`delete_tag()`** | the Tag, relationships cascade |
| **`merge_tags_into()`** | the source Tag; target survives. Relationships move in **all three** tables: `work_tags`, `folder_tags`, `processing_file_tags`. |

PRKS previously garbage-collected an "unused" Tag inside all five of the
ordinary paths above. That was wrong for three independent reasons:

1. **Product semantics.** Tags became temporary values whose existence
   depended on current usage, rather than a reusable vocabulary. Removing the
   last use of "Security" deleted the concept of "Security".
2. **Data loss, present-tense.** The `unused` test consulted `work_tags` and
   `folder_tags` but never **`processing_file_tags`**, so a Tag still attached
   to a staged Processing File could be deleted by an unrelated Work edit —
   and the FK cascade then destroyed that staged relationship. Reproduced
   before the fix; now covered by a regression.
3. **Hostile to synchronization.** An offline device holding a Tag id would
   find it gone because another device merely removed the last relationship:

   ```
   A caches Tag T, revision W:T = 4       B removes T from its last Work
   A offline: ADD_WORK_TAG(W2, T)         → PRKS deletes Tag T entirely
   A reconnects → ENTITY_NOT_FOUND, from an edit nobody framed as a deletion
   ```

This is why the §10 revision model is now clean: **Tag entity lifecycle is
explicit, Work-Tag relationship lifecycle is independent, and the
`(work_id, tag_id)` tombstone is stable across add/remove cycles.**

Cleanup, if ever wanted, must be an explicit user action — an "unused tags"
list with its own delete — never collection during an unrelated operation.

The obsolete prune-atomicity tests were **removed rather than preserved**:
they asserted that a relationship delete and a tag prune committed together,
and there is no longer a second write. `tests/test_folder_atomicity.py` now
installs a trigger that forbids *any* delete from `tags` during those paths,
which is a stronger statement than checking the row survived.

### Tag lifecycle events vs. pending operations — **REQUIREMENT for 2B**

Explicit Tag deletion and merge are entity-lifecycle events relative to a
queued Work-Tag operation:

| Pending operation references | Result |
| --- | --- |
| an **active** Tag | normal processing |
| a **merged** source Tag | `TAG_MERGED`, returning the canonical `target_tag_id` |
| an explicitly **deleted** Tag | `TAG_DELETED` → surface as a conflict. **Never recreate the Tag.** |
| an **unknown** id | `ENTITY_NOT_FOUND` |

The operation is **not** automatically rewritten onto the merge target in the
first version. Automatic rebasing is a reasonable later feature once the
protocol is established; doing it before then would silently redirect a user's
intent to an entity they never chose.

**This needs a durable lifecycle record, which does not exist today.** After
`merge_tags_into()` completes the source id is gone from `tags` entirely.
PRKS preserves the source *name* as an alias of the target, but an alias does
not preserve `old source tag id → canonical target tag id`. So when an offline
device sends `ADD_WORK_TAG(W, old_source_id)` hours later, the server cannot
tell "merged into T" from "never existed" from "explicitly deleted" — the
three rows above collapse into one indistinguishable `ENTITY_NOT_FOUND`.

Conceptually:

```
sync_tag_lifecycle
  tag_id         PRIMARY KEY
  state          active | merged | deleted
  target_tag_id  nullable (set only for `merged`)
  changed_at
```

Written inside the same transaction as the merge or delete, so the lifecycle
record cannot disagree with the `tags` table. A narrower pair of
tombstone/redirect structures would do equally well; what matters is that the
three outcomes stay distinguishable durably.

**Merge chains must resolve to the end.** Given `A → B` then `B → C`, an
operation referencing `A` must return `TAG_MERGED → C`, never stranding the
client on another obsolete id. Two viable implementations:

- **resolve at request time** — follow `target_tag_id` until `active`,
  `deleted`, or a cycle guard trips; or
- **path-compress on merge** — when merging `B → C`, transactionally rewrite
  every lifecycle row already pointing at `B` to point at `C`.

Path compression keeps request handling O(1) and is preferred, but either is
acceptable provided the resolver is bounded against cycles.

**A chain ending in deletion resolves to deletion.** For `A → B → C` where `C`
is later explicitly deleted, an old operation against `A` resolves to
`TAG_DELETED` — it must **not** recreate `C`, and must not report a merge to a
target that no longer exists.

## 10a. Revision visibility — **REQUIREMENT for 2B**

The per-relationship scope in §10 is only usable if an offline client can
learn the base revision of a relationship that **does not currently exist**.

```
Work W does not have Tag T.
Server:  work-tag / W:T  =  8      (T was added and removed before)

Device caches W while T is absent.
Later, offline:  ADD_WORK_TAG(W, T)   →  base_revision = ?
```

Seeing that `T` is absent does **not** imply revision `0` — the relationship
may have been toggled many times. Without the real value the client either
invents one (and the server's staleness check becomes meaningless) or omits it
(and every add is unconditional), which defeats the whole point:

```
Device A holds stale absence state
        → another device changes W:T
        → A goes offline and performs ADD/REMOVE
        → A has no correct base revision → the conflict slips through
```

**Requirements:**

1. **`sync_entity_revisions` keeps the `work-tag / W:T` row after the
   relationship is deleted.** The revision is a durable tombstone; only the
   `work_tags` row goes away. A scope row is small and bounded by *relationships
   ever touched*, not by current ones.
2. **Absent-but-known relationships must be observable.** A client persists the
   revision it actually observed, for present *and* absent relationships.
3. **Never-touched relationships are revision `0`** by definition. A scope with
   no row has never changed, so `base_revision = 0` is correct and the server
   treats a missing row as `0`.

### Delivering it — **RECOMMENDED: a dedicated projection**

Two candidate carriers were considered:

**A. Extend the Work detail payload.** `get_work()` already returns `tags[]`,
so relationship revisions could ride along plus a map of tombstones. Rejected
as the primary mechanism: Work detail is a large, widely-cached read model
consumed by many surfaces that do not edit tags, and the tombstone map grows
with *every tag ever attached and removed* from that Work. It would bloat a hot
payload for a rare editing case, and would entangle the Work entity cache with
sync bookkeeping.

**B. A dedicated Work-tag-options projection** — **recommended**:

```
GET /api/works/:id/tag-options
{
  "work_id": "W-A1B2C3D4",
  "assigned":  [ { "tag_id": "T-A", "relation_revision": 7 } ],
  "known_absent": { "T-B": 4, "T-C": 9 },
  "catalog_etag": "W/\"prks-tags-…\""
}
```

Reasons it is the better carrier:

- It is fetched **only when the tag editor opens**, so the hot Work payload is
  untouched and the Work entity cache keeps meaning "the Work read model".
- `assigned` and `known_absent` together are exactly the client's observed
  base state — nothing else has to be inferred.
- A tag not listed in either is revision `0` (never touched), which is both
  correct and keeps the payload proportional to history rather than to the
  catalog.
- It gives a natural place for the catalog ETag so the picker can revalidate
  cheaply.

Cached offline under its own key (`work-tag-options:<work_id>`) in the
**disposable** cache — it is downloaded server state, not user work. Investigate
during 2B whether it should instead be folded into the existing Work entity
envelope for cache-coherence simplicity; the recommendation above is the
starting point, not a settled schema.

## 10b. Revision advancement belongs to the canonical mutation boundary — **DECIDED**

**Critical, and easy to get wrong.** If only `POST /api/sync` advanced
revisions, an ordinary *online* tag change through the existing UI would move
the data without moving `work-tag / W:T`. An offline client could then hold
`base_revision = 8`, the server could already be at a different state, and the
client's operation would be accepted as current. The conflict system would be
silently blind to every change made through the normal app.

Therefore revision advancement lives in the **database mutation methods** —
`add_tag_to_work()`, `remove_tag_from_work()` — not in the sync endpoint, and
the relationship change plus its revision bump are **one transaction**. The
sync endpoint is then just another caller of the same canonical boundary.

Two existing paths need the same treatment, because they destroy or transform
Work-tag relationships:

- `delete_tag()` — removes every `work_tags` row for that tag via cascade.
  Every affected `work-tag / W:T` scope must advance. The canonical boundary
  already collects the affected work ids (for cache coherence), so the same
  transaction has what it needs.
- `merge_tags_into()` — moves relationships from source to target. **The two
  scopes do not advance symmetrically.** For every Work holding the source:

  | Scope | Transition | Advance? |
  | --- | --- | --- |
  | `W:source` | `true → false` | **always** |
  | `W:target` | `false → true` | **yes** |
  | `W:target` | `true → true` (the Work already had both) | **no** |

  Advancing `W:target` when the Work already had the target would manufacture
  a synchronization change where no logical relationship changed, and would
  make another device holding a perfectly current `W:target = true`
  observation appear stale. This is the same no-op rule as §10d: **a
  transition that does not change state does not advance a revision.**

`bulk_update_works()` with `add_tags` / `remove_tags` is the same requirement
at scale, with the same no-op rule — a Work that already had the tag being
added must not advance.

## 10c. The offline tag picker needs a tag catalog — **REQUIREMENT for 2B**

Work Tags cannot honestly be the first offline mutation if the user cannot pick
a tag offline. The Work tag combobox calls `fetchTags({ used: false })` against
`/api/tags`, which has no cache today.

**Recommended:** a small read-only `tags:index` cache in the disposable store,
backed by the existing `GET /api/tags`, with its own coherence domain advanced
by tag create/rename/delete/merge. The catalog is genuinely small — `tags` is
`id`, `name`, `color`, `created_at` plus aliases — so no compact projection is
needed, unlike the Works catalog.

This explicitly does **not** require making the `#/tags` management page
offline-capable. That page is mutation-heavy (rename, merge, delete, aliases)
and stays online-only; only the picker's catalog is cached.

Creating a *new* tag offline is a separate, harder problem (it needs offline
entity ids, §5). **2B should support attaching and detaching existing tags
only**, and refuse offline tag creation with the ordinary
requires-a-connection message.

## 10d. The first synchronization rule, precisely — **DECIDED**

For `ADD_WORK_TAG` / `REMOVE_WORK_TAG` against scope `work-tag / W:T`:

| Client base revision | Server state vs. requested | Result |
| --- | --- | --- |
| current | differs | apply, advance revision, `ACKNOWLEDGED` |
| current | already the requested state | `ACKNOWLEDGED` (no-op, no advance) |
| **stale** | already the requested final state | `ACKNOWLEDGED` — convergent; return current revision |
| **stale** | opposite final state | **`CONFLICT`** |

This yields exactly the behavior wanted, with no CRDT:

- `ADD X` + `ADD X` from two devices → the second is stale but convergent →
  automatic.
- `REMOVE X` + `REMOVE X` → same.
- `ADD X` vs `REMOVE X` across different observed revisions → **conflict**,
  surfaced rather than silently resolved.

The no-op-at-current-revision row matters: an operation that asks for a state
already true must not advance the revision, or every replayed intent would
manufacture staleness for other devices.

## 11. Optimistic read-model overlay — **RECOMMENDATION**

The cache holds *last acknowledged server state*. The UI needs *effective
state*. These must stay distinguishable:

```
cached server snapshot  +  pending operations  →  effective value  →  renderer
```

Pending edits must **not** be baked into disposable snapshots with no
provenance: the cache would stop meaning "what the server last told us",
rollback would become impossible, and a cache clear would silently change what
the user sees.

Overlays are **driven by semantic operations, not a generic JSON patch engine**
— for Tags, `server tags + pending ADD/REMOVE = effective tags`. Seams roughly
`prksLocalApplyEntityOverlay(kind, id, value)` and
`prksLocalApplyListOverlay(listKey, value)`, each dispatching per operation type.

**Reload/crash invariant.** After an offline tag change, closing and reopening
the browser must still show it. The overlay therefore reconstructs from durable
operations on every mount — never from RAM.

**Cache-clear invariant.** If the user clears the cache while a tag operation is
pending, the operation survives but its base Work snapshot is gone.
**Recommended behavior:** keep the operation, show it under
Settings → Unsynchronized changes, re-fetch the base when online, and
reconcile then. Do **not** invent a partial Work detail page from a single tag
operation, and do **not** discard the operation.

## 12. Sync coordinator — **RECOMMENDATION**

One module (`sync-runtime.js`). Components never implement their own retry
loops — the same rule that keeps connectivity policy in `offline-runtime.js`.

State machine, deliberately minimal:

```
pending ──▶ syncing ──▶ acknowledged
   ▲           │
   └───────────┤  retryable failure (attempt_count++, last_error set)
               ├──▶ conflict     (server says base_revision is stale)
               └──▶ failed       (protocol error / client corruption)
```

A retryable failure is **not a separate persisted status** — it is `pending`
plus `last_error` and `attempt_count`, which is what `local-store.js`
implements. Fewer states, fewer ways to get stuck.

**Backoff:** bounded exponential with jitter. Coming online or an explicit user
retry wakes it immediately. No polling.

**Dependencies:** an operation is runnable when every `depends_on` op is
acknowledged. Not a global FIFO — independent operations should not block each
other. A dependency that conflicts or fails blocks its dependents *visibly*
rather than silently dropping them.

### Protocol result categories — **DECIDED**

```
ACKNOWLEDGED         applied now
IDEMPOTENT_REPLAY    already applied; previous acknowledgement replayed
CONFLICT             base_revision stale
VALIDATION_ERROR     malformed/illegal operation — not retryable
ENTITY_NOT_FOUND     target is gone
DEPENDENCY_ERROR     a prerequisite operation did not land
RETRYABLE_SERVER_ERROR
```

The client must never infer "conflict" from an HTTP status or error string.

**Conflict detection is authoritative server-side.** A client may *propose* a
`base_revision`; only the server decides.

**Security.** Operation types are dispatched from a fixed server-side
allow-list — never dynamic dispatch on a client-supplied name. Each operation
gets its own payload validator, reusing existing canonical validation. Payload
size is bounded.

## 13. Milestone sequence

| Milestone | Contents | Status |
| --- | --- | --- |
| 2A | Durable local store, device identity, operation envelope, this document | **done** |
| 2A.1 | `enqueueOperation` owns device identity; UTF-8 byte limit; reset closes its own connection; tighter envelope invariants; §10a–§10d | **done** |
| 2A.2 | Stable Tag identity: no automatic pruning; `processing_file_tags` preserved on prune **and merge**; §9a | **done** |
| 2B | Server sync protocol: `sync_operations` ledger, revision table (incl. tombstones), revision advancement in canonical boundaries, `tags:index`, tag-options projection, Work Tags offline | next, review first |
| 2C | Optimistic overlay + Settings "unsynchronized changes" surface | after 2B |
| 2D | More operation families in the §9 priority order | — |
| 3 | Conflict UX | — |
| 4 | Research Notes CRDT evaluation | — |

**Automerge stays out of Milestone 2 entirely.** Structured synchronization —
semantic operations, revisions, idempotency, durable storage, conflict handling
— must be reliable before long-form text merging is attempted. Research Notes
is then the first serious CRDT experiment, with CRDT state stored separately
from the relational model and from the disposable cache.

## 14. Deferred, with reasons

| Question | Status | Reason |
| --- | --- | --- |
| Offline tag *creation* | **DEFERRED** | Needs client-generated entity ids (§5). 2B attaches/detaches existing tags only. |
| Durable Tag lifecycle / merge-redirect table | **REQUIRED in 2B (§9a)** | Without it a merged source id is indistinguishable from a deleted or unknown one, so `TAG_MERGED` cannot be returned at all. |
| Whether tag-options rides on Work detail or its own endpoint | **RECOMMENDED (§10a)** | Dedicated projection preferred; confirm the schema during 2B implementation. |
| Offline entity ids / longer id format | **DEFERRED** | Recommendation in §5; needs an export/import and wiki-link audit, and no offline creation exists to need it. |
| `protocol_version` negotiation | **DEFERRED** | Single-client-per-server today; add when the first incompatible envelope change is real, not speculatively. |
| Add-vs-remove causal rule | **DEFERRED** | §10 recommends explicit conflict first; a causal rule needs metadata the protocol lacks. |
| Playlist reorder policy | **DEFERRED** | Category E; must not be the first offline mutation. |
| Sub-second `last_opened_at` | **DEFERRED** | Settle with §8 in 2B — it is a storage-format change, not a local one. |
| Multi-user / multi-account sync | **DEFERRED** | PRKS is single-user today; `device_id` is explicitly not an identity. |
| Server push / live updates | **DEFERRED** | Out of scope; polling plus ETag revalidation remains adequate. |
