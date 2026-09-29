# Offline Work synchronization families

This leaf contains current Work metadata/source/open/tag durable-operation rules, field conflict semantics, derived-value handling, and Work values referenced by other entities. Read `offline-foundations.md` first.

### Field-scoped Work metadata (Milestone 2D)

- **The conflict unit is a FIELD, not a Work.** Two devices editing `doi` and
  `isbn` on the same Work have not disagreed about anything; one Work-level
  revision would demand a resolution for a collision that never happened. Scope
  is `work-field / ["<work id>", "<field>"]` in the existing
  `sync_entity_revisions` table -- no schema change.
- `backend/work_metadata_sync.SYNCED_FIELDS` is the authoritative registry of
  which fields synchronize -- consult it rather than any count written in prose.
  It holds `status` (2H), `author_text` (2I), `year`, `published_date` (2G),
  `abstract`, `publisher`, `location`, `edition`, `journal`, `volume`, `issue`,
  `pages`, `isbn` and `doi`.
  `backend/work_metadata_sync.SYNCED_FIELDS` is the authority and the client
  list is pinned against it by `tests/test_frontend_work_metadata_sync.py`.
  Never accept a column name from a client.
- **`abstract` is the large one.** `MAX_ABSTRACT_UTF8_BYTES` is 1 MiB, enforced
  identically on PATCH, the sync handler, the durable store and the editor --
  in UTF-8 BYTES, never `len()`. Over-limit is refused visibly; nothing is ever
  truncated. It is in `BYTE_LIMITED_FIELDS`, so `metadata-state` carries its
  revision only (the Work record has the value) and a conflict reports bounded
  previews plus sizes rather than two megabyte values the 2 KB structured-result
  bound could not store. The eleven small scalars keep code-point limits.
- `prksAbstractExcerpt()` is the canonical excerpt rule: first 100 Unicode CODE
  POINTS, matching SQLite's `SUBSTR`, not UTF-16 units and not graphemes.
  `tests/test_abstract_excerpt.py` pins equality against the real engine. Never
  re-truncate an `abstract_excerpt` the server already bounded.
- Hydration is four-valued (`unread`/`loading`/`ready`/`unavailable`). A failed
  read is `unavailable`, NEVER `ready`: it proves nothing about what is stored,
  so the map is kept, an empty one stays untrusted, and the synchronized fields
  refuse to become editable rather than save over an edit this session could not
  see. Never fall back to a direct PATCH for them.
- **Store exactly what a PATCH would store.** No case folding, no ISBN
  punctuation rewriting, no page-range parsing, no whitespace stripping --
  synchronization is not a licence to start normalizing values PRKS never
  normalized. SQLite NULL and `""` are one logical value.
- `update_work_metadata()` advances the same revisions, for changed fields
  only, in one transaction with the value write. Revisions are canonical
  history, not sync-endpoint history: a value stored without its revision is
  exactly what makes every other device's staleness check lie.
- `GET /api/works/:id/metadata-state` is its own projection, cached as
  `work-metadata-state`. Do not move revisions onto the Work detail.
- One Save is one IndexedDB transaction across every changed field. "Changed"
  is measured against what the form was SHOWING (pending value, else server
  value), not against the server base -- otherwise editing back to the server
  value strands the pending operation.
- A conflicted or possibly-sent field is busy; the other six stay editable.
- **Being invisible on a card is not the same as being unused.**
  `recently-added:index` selects `works.publisher` because Home -> Recently
  Added filters locally over it, so a pending publisher must reach that
  projection's filtering. `FIELD_PROJECTIONS` names that dependency on both
  sides and the parity is pinned by a test. Before adding a field here, check
  the fan-out table in [docs/local-first-sync.md](../local-first-sync.md) --
  and check what *filters* on it, not only what renders it.
- The cross-projection overlay lives in `work-metadata-state.js`. Components
  say "repaint"; they must not grow a second opinion about which operations are
  pending or what they mean. The durable queue is read once into a map, never
  once per row, and pending values are never written into an acknowledged
  snapshot -- in IndexedDB or in a component's RAM copy.
- On acknowledgement a projection row is patched in place, not invalidated:
  dropping the snapshot costs offline availability for a change whose exact
  shape is already known.

### High fan-out fields (Milestone 2G)

- `year` and `published_date` are read by far more than the Work detail: every
  Work card shows a year, so both reach `works-browse:index`, `recent:index`
  and `recently-added:index`, AND the Work summaries embedded in every cached
  Folder, Person and Playlist detail.
- **Embedded summaries are patched, never invalidated.** Dropping three whole
  domains for a one-field edit would cost the user every cached Folder, profile
  and playlist -- and the exact new value is known, so there is nothing to
  re-fetch. `SUMMARY_FIELDS` says which fields those rows carry;
  `store.getEntitiesByKind()` finds them. A field no summary carries never
  touches those domains.
- Each domain's generation advances **before** its rows are read, so a GET that
  began earlier cannot publish its pre-acknowledgement body afterwards. The
  domain is not blocked: rows are being corrected, not invalidated.
- **A declared projection the runtime cannot address is a failure, not a skip.**
  `recent` shipped declared-but-unmapped in `FIELD_PROJECTION_LISTS`, so
  acknowledgements silently skipped `recent:index` and it kept serving a value
  the server no longer held. `reconcileFieldProjections` now returns false, and
  a test pins that every declared domain has a key.
- **The displayed year is derived**, not stored: an explicit `year` wins,
  `published_date` supplies it otherwise. A pending edit to either field can
  move what a card shows, so both are registry entries.
- The UI shows dates as `dd/mm/yyyy` and the column stores `yyyy-mm-dd`. That
  conversion lives in the field CODEC in `work-metadata-state.js` -- one place,
  used by the draft, the save and the dirty diff. An uninterpretable date is
  `null` and the save is refused before enqueueing; never enqueue a value the
  server would have to guess at.

### Group-changing fields (Milestone 2H)

- **`status` changes where a card IS, not what it says.** Progress renders one
  status at a time by filtering the rows the route hands it, so a pending
  Status must make a Work LEAVE its acknowledged group and JOIN the pending
  one, before sync and across a reload.
- That needed no new mechanism: the route already overlays
  `works-browse:index` with `prksEffectiveBrowseRows()` before handing the
  rows to the Vue Progress surface. Adding `status` to `FIELD_PROJECTIONS`,
  `PROJECTION_COLUMNS` and `SUMMARY_FIELDS` was the whole propagation change.
  **The Progress view must never learn what a durable operation is** -- a second
  opinion about pending state drifts from every other surface.
- **The bulk action is a canonical mutation like any other.**
  `bulk_update_works(action="set_status")` routes each Work through
  `set_field_on_conn` inside its existing transaction. Writing the column
  directly would change the value while leaving the revision, so a device
  holding the pre-bulk Status would compare equal revisions, believe itself
  current and overwrite the newer value. Only actual changes advance; values
  and revisions roll back together.
- **Validation is by ALLOWLIST, not length.** `is_valid_field_value()` is the
  one rule the sync handler, the PATCH and the bulk action all ask. The SQLite
  CHECK constraint is a last line of defence, not the first: it raises an
  IntegrityError instead of telling the client what it should have sent.
- **Status has its own bounded Save**, separate from "Save bibliographic
  details". One durable button covering both would be the mixed-atomicity
  contract 2D removed from the online save, rebuilt inside the durable path.
  Each group reads its own fields from the DOM and reports only its own
  conflicts.
- **Server-backed Search and Saved View results are overlaid too**
  (`prksEffectiveWorksSync()` inside `prksSearchResultCardsHtml()`). They are
  fetched fresh, so without it a card shows the acknowledged Status seconds
  after the user changed it everywhere else. It covers every synchronized
  field, not Status alone.

### Fields whose stored value is not what is shown (Milestone 2I)

- **`author_text` is one of three sources of a credit**, and not the first:
  `linked Author(s) -> author_text -> linked Editor -> nothing`. A pending
  value always changes the FIELD and only sometimes changes what the user
  sees. That is the existing composition; synchronization must not take it
  over.
- **Order: overlay, then compose.** `acknowledged row -> effective row ->
  prksWorkCardCreditLine() -> HTML`. Never patch `author_text` onto rendered
  credit: with a linked Author the patch must do nothing, and with the field
  cleared it must reveal a different person. The command palette had this
  ordering bug -- it read the credit off the acknowledged row and the overlay
  afterwards -- and 2I fixed it.
- **Never store a derived credit.** No `display_author` / `display_credit` /
  `effective_credit` field. Two sources of truth drift the moment either input
  changes, and a future role-sync milestone must be able to change which value
  is preferred without touching `author_text`.
- **Local filters index the RAW field.** Recently Added searches `author_text`
  separately from `linked_authors` / `primary_author` / `primary_editor`, so a
  card crediting a linked Author can still match on its hidden textual author.
  Preserved deliberately: this is synchronization, not a search redesign.
- **Server search decides membership.** A pending `author_text` does not make a
  Work discoverable; a returned Work is still RENDERED from effective local
  metadata. After ACK the FTS trigger on `works` carries the new value -- do
  not add manual index maintenance.
- **One size contract, on every path** (2I.1). `author_text` is byte-limited at
  64 KiB -- absurdly generous for an Author or Channel name, which is the
  point: the number exists so the field's size is a PRKS contract rather than
  an accident of whichever storage layer refused first. Before it, the server
  accepted any length and the browser's durable envelope decided, so the same
  value was savable online and impossible offline. The editor still trims
  before sending, exactly as it always did; the server stores what it is given.
- **Byte limits live in one registry.** `BYTE_LIMITS` on the server, mirrored
  in `work-metadata-state.js` and `local-store.js`, and a test compares all
  three at runtime. Membership is the whole mechanism: compact
  acknowledgements, revision-only metadata-state entries and bounded conflict
  previews are all derived from it, so a third large field is a registry entry
  rather than another special case threaded through five files.
- **A terminal result the client cannot store is worse than losing the edit.**
  The browser refuses a durable `server_result` over 2048 serialized bytes; the
  settle then fails, the coordinator reads a failed sync, and the operation
  retries forever on the same oversized result — the conflict UI is never
  reached. `fit_terminal_result()` guarantees the fit: a full-value result that
  does not fit degrades to the bounded preview shape, then the preview is cut
  to the longest prefix that fits. Measure with `ensure_ascii=False`
  (`JSON.stringify` does not escape non-ASCII) and measure the WHOLE object.
  Characters are not bytes and neither is a serialized size: one C0 control
  character is one code point, one column byte and SIX bytes as `\u0001`, which
  is how a 400-character preview reached ~2400 bytes and a 500-code-point
  `journal` conflict reached 6 KB.
- **The client validates the conflict shape it RECEIVED**, not the one the
  field's type implies, because a small scalar may now arrive bounded. A
  byte-limited field stays always-bounded regardless.
- **Bound the VALUE, not its JSON encoding.** Every quote and backslash doubles
  under escaping, so measuring the serialized payload would refuse an Author
  name full of quotation marks that is exactly at the stated limit -- a failure
  no user could see the cause of. The allowance is shape-scoped
  (`{field, value}` exactly, field in the registry, value a string) so it
  cannot be used to smuggle an unbounded payload.

### Typed fields and derived resources (Milestone 2J)

- **The wire is not the column.** `thumb_page` has four representations --
  editor string, wire string (`""` = no explicit page), `INTEGER NULL` column,
  and `integer | null` in every read model -- and `FIELD_CODECS` owns every
  conversion. The wire stays a STRING because the envelope is validated,
  hashed, compared and replayed as one; widening `payload.value` to a union
  type would mean changing all of that for one field.
- **A wire string in a cached row is corruption, not a cosmetic bug.** Browse
  rows are validated with `prksIsOptionalNonNegativeInteger(row.thumb_page)`,
  so `"3"` makes the row fail its own shape check and the catalog is discarded.
  Convert wherever a value enters a Work-like object: the effective-Work
  overlay, the three projection overlays, embedded summaries, and ACK
  reconciliation. `copy()` in `PROJECTION_COLUMNS` is a CONVERSION that was
  the identity for four milestones, not a copy.
- **`metadata-state` keeps the WIRE form deliberately** -- it is
  synchronization bookkeeping, and its `value` is what a base revision was
  observed against. The Work record is the entity. `"3"` there, `3` here.
- **Compare canonical MEANING.** Column `3`, wire `"3"` and `"003"` are one
  state; `NULL` and `""` are another. A spelling difference must never advance
  a revision or raise a conflict.
- **Refusing is not clearing.** `0`, `-1`, `1.5`, `abc` are refused visibly
  with the draft intact. PATCH used to silently clear all of them; that
  normalization was removed when the codec took over.
- **State the thumbnail page ALWAYS**, `?page=1` for null included. A page-less
  URL means "whatever the server stores", which is wrong while a clear is
  pending -- the card would keep rendering the old page. `?page=1` and a stored
  NULL are the same page and the same cache artifact.
- **Every effective-Work helper converts.** There is ONE rule
  (`applyPendingFrom` + `entityTransforms`); callers differ only in where
  their pending values come from -- the shared map, or an explicit operation
  list for the editor. A second loop that copied wire values straight into an
  entity is how `thumb_page` once came back as `"5"` from the helper the
  EDITOR uses and `5` from every other. A selftest asserts all five
  constructors agree, because one disagreeing is the defect.
- **`metadata-state` holds CANONICAL wire values**, and its shape validator
  asks the field's codec (`isCanonicalWire`) rather than growing special
  cases. `"003"` is as invalid there as `"abc"`, and an integer is invalid too
  -- that is the entity representation. Validation asks whether the stored
  value is valid, NEVER whether it could be repaired: silently canonicalizing
  corrupt acknowledged state hides the corruption and leaves the observed base
  disagreeing with the server.
- **Offline suppression is decided BEFORE any URL is derived.** A cached card
  emits no source at all; a pending edit is never a reason to request bytes
  that cannot arrive.
- `source_url` is synchronized only as PROVENANCE, and the server REFUSES a
  field-scoped write to it on a video Work (`FIELD_KIND_GUARDS`). There it is
  one spelling of an identity spanning four columns, and `prksYoutubeEmbedUrl()`
  short-circuits on `provider_id` -- so changing the URL alone would move the
  stored value while the video that plays stays the same.

### Source identity is an AGGREGATE (Milestone 2O)

- **`SET_WORK_SOURCE`, scope `work-source / <work id>`.** One user decision ->
  one operation -> one revision -> one conflict, rewriting `source_kind`,
  `provider`, `provider_id` and `source_url` together. Three field-scoped
  operations would let two ordinary edits reach "the stored URL names video B
  while the viewer plays video A".
- **The payload carries INTENT, not columns**: `{source: {kind, url}}`.
  `provider` and `provider_id` are DERIVED inside the mutation boundary. A
  client able to assert them could assert an identity its own URL contradicts.
- **Identity is `provider` + `provider_id`, never the URL spelling.**
  `watch?v=A`, `youtu.be/A` and `embed/A` are ONE source: moving between them
  is no revision, no conflict and no write.
- **ONE parser**, in `work_source_sync`. Work creation imports it from there.
  Two parsers would eventually disagree, and the disagreement would surface as
  a Work whose stored URL and stored id name different videos. A cross-language
  test drives 14 URL spellings through both implementations.
- **`thumb_url` is cleared on an identity change**, never re-derived: deriving
  it means a network call, and no canonical mutation may depend on one -- a
  failed image fetch must never fail a source change. A row claiming video B
  while serving video A's picture is a lie the user can see.
- **Only video -> video is supported.** PDF -> video, video -> PDF and
  video -> no source are refused with `UNSUPPORTED_SOURCE_TRANSITION`: no UI,
  and no defined semantics for `file_path` or viewer selection. Refused, not
  invented.
- **Online, a pending source plays immediately**: the embed URL is fully
  determined by `provider_id`, which the client derives itself. Offline, the
  intent still saves durably but no remote request is made -- saved local
  intent is not resource availability, exactly as with thumbnails.

### Work values held by REFERENCE in other entities (Milestone 2M)

- `title` lives inside caches that are not Work rows: Concept backlinks,
  Argument `sources[]`/`mentions[]`, and Graph node `label`s -- keyed by a
  foreign column, under property names that disagree with each other.
  `WORK_REFERENCE_SHAPES` describes all of them; a component never learns what
  a durable operation is, and the next field is a registry entry.
- **Patch, never invalidate.** `prksMarkWorkTitleChanged()` used to stale four
  domains after a Title PATCH. The durable ACK reconciles exact values instead
  -- destroying usable offline snapshots for a change whose shape is known is
  the opposite of the reconciler's purpose.
- The Playlist inline rename is a WORK TITLE change, not Playlist state: it
  uses the same durable Work-title operation and therefore works offline.
  Playlist create/edit/membership/reorder/delete are also local-first, but are
  owned by the Playlist rules in `offline-entity-coherence.md`; only the
  catalogue-search surfaces called out there remain online-only.

### Local-first Work opens (Milestone 2C)

- `last_opened_at` is a **max-register over event time**, not last-writer-wins.
  Arrival order must not decide it: a device that reconnects on Friday carrying
  Monday's open cannot drag the Work back to Monday, and must not claim it was
  opened on Friday. That is what makes the operation commutative, idempotent
  and order-independent, and why there is no conflict UI.
- Event time comes from the device. Server receive time is used only to clamp a
  timestamp more than `MAX_CLIENT_FUTURE_SKEW_SECONDS` in the future, so a fast
  clock cannot pin a Work to the top of Recent. An OLD timestamp is legitimate
  and is never rejected -- the max-register makes it a harmless no-op.
- Values are stored as `YYYY-MM-DD HH:MM:SS.mmm` UTC
  (`work_open_sync.format_moment`). Second-resolution rows written before this
  milestone stay valid and keep sorting correctly, because `12:00:00` is a
  prefix of `12:00:00.250`. No destructive migration for precision.
- `POST /api/works/:id/opened` and the sync handler share
  `work_open_sync.set_opened_at()`. One column, one meaning.
- Only never-sent open events coalesce, per Work, keeping the latest instant. A
  SENT event is left alone: it may already be ledgered, and it does not need
  cancelling, because applying two open events in either order converges.
- The acknowledgement carries the compact `/api/recent` row
  (`db.recent_item_on_conn`), so reconciliation needs no second request and the
  client never rebuilds server-derived author/display fields itself.
- Pending opens are an overlay computed at render time, never written into
  `recent:index`. Without a cached Recent snapshot, Recent stays honestly
  unavailable -- one open event is not a Recent page.

### Local-first Work Tags (Milestone 2B)

Existing Work Tag add/remove uses one durable-first path online and offline.
See `docs/local-first-rollout-status.md` for other families and for surfaces that
remain connection-required. The Work-Tag implementation contract is in
[docs/local-first-sync.md](../local-first-sync.md).

- `local-store.js` owns `prks-local-v1`, physically separate from the disposable
  `prks-offline-v1`. Clear offline cache must never touch durable operations.
  Writes request strict durability, resolve on transaction completion, and reject
  on failure. Never optimistically claim success before that commit.
- `work-tag-state.js` owns strict Tag catalog/tag-options validators and the pure
  overlay. `work-tag-editor.js` owns TabContext-local editing and conflict UI.
  `sync-runtime.js` alone owns semantic transport, one in-flight operation and
  bounded retry. Never queue arbitrary URLs/methods/request bodies.
- `POST /api/sync/operations` dispatches every registered durable operation
  (Work Tags among many others; see `backend/sync_protocol.py`).
  `backend/work_tag_sync.py` shares connection-aware
  canonical relationship helpers with direct add/remove, bulk, merge and delete.
  Domain writes, revision advancement and ledger insertion commit together.
- Schema 14 sync_operations, sync_entity_revisions and sync_tag_lifecycle are
  canonical main-DB backup state. No ledger pruning. Missing revision means 0;
  removed relationships retain tombstones. Scope keys are JSON arrays of IDs.
  Lifecycle history survives Tag deletion and resolves guarded merge chains.
- Idempotency replay precedes lifecycle/revision evaluation and preserves the
  original HTTP status and result. Reusing an op ID with a changed envelope
  never executes. Future revisions are protocol errors, not stale conflicts.
- At most one active operation per Work/Tag. Coalesce only never-sent pending
  rows; retries might already have reached the server. Never edit an immutable
  envelope. Syncing/retrying controls disable only that relationship. Explicit
  conflict reapplication creates a new ID against the reported server revision.
- ACK reconciliation must complete before retiring the durable operation.
  Cache-write failure leaves it retryable; no mandatory extra GET. Missing cache
  bases remain missing. Pending/conflicted overlays never enter cache records.
- `tags:index` hashes/validates the actual catalog representation; no global RAM
  Tag catalog copy. Catalog edits invalidate tags, relationship edits do not.
  `work-tag-options` is per Work and contains no catalog ETag. Only affected
  Work projections invalidate, including absent tombstones on delete/merge.
- The durable family list under "Offline / PWA" and
  `docs/local-first-rollout-status.md` are the running score. Still absent as
  product: CRDTs, multi-user sync and server push. New PDF binary ingestion
  stays connection-required; annotation metadata on already-cached PDFs is
  durable. `year` and `published_date` (2G) are the high fan-out case: they
  reach all three browse catalogs AND the Work summaries embedded in cached
  Folder, Person and Playlist details, which are patched -- never
  invalidated -- under a generation advanced before the read.

**Tag identity is persistent.** Only `delete_tag()` and `merge_tags_into()`
may destroy or transform a Tag. Removing a tag from a Work or Folder, deleting
a Work or Folder, and bulk tag removal all touch **relationships only** --
PRKS used to garbage-collect "unused" Tags inside every one of those paths.
That made Tags temporary values rather than a reusable vocabulary; it silently
destroyed `processing_file_tags` rows, because the "unused" test consulted
`work_tags` and `folder_tags` and never that third table; and it would have
turned ordinary edits into `ENTITY_NOT_FOUND` sync conflicts for any offline
device holding the Tag id. Unused Tags now simply stay in the catalog. Cleanup,
if ever added, must be an explicit user action -- never collection during an
unrelated operation. `tests/test_folder_atomicity.py` installs a trigger
forbidding any delete from `tags` during those paths.

`merge_tags_into()` moves relationships in **all three** tables --
`work_tags`, `folder_tags` and `processing_file_tags`. The third was missing
and had the same effect as the prune bug: the source row was deleted and the
FK cascade left a staged Processing File holding neither tag. A merge means
"replace S with T everywhere"; explicit `delete_tag()` is the one path where
cascading the relationship away is correct.


