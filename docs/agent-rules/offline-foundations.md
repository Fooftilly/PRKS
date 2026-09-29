# Offline / PWA foundations

This is the shared implementation contract for current PRKS offline/sync work. Read it for any change that touches offline behavior, durable local intent, service-worker behavior, client caches, reconciliation, or synchronization. Then use `docs/agent-rules/offline-pwa.md` / `docs/agent-context/sync-map.md` to load only the relevant domain-specific leaf rules.

**Architecture status:** these are the shared rules for the offline/sync implementation that exists today. Preserve them while changing those paths, but do not treat the current browser synchronization model as the permanent target for every PRKS client. Roadmap #310 may deliberately replace parts of it later; implemented behavior remains authoritative until that migration lands.

The root `AGENTS.md` remains authoritative for global repository safety, testing, architecture, storage, audit, and UI rules. Domain-specific behavior lives in the routed leaf files, not here.

This section describes the **disposable read cache**. It is not the whole
offline story any more: a growing set of content mutations is *local-first* and
runs through a separate durable store and sync coordinator (see
`offline-work-sync.md` and `docs/local-first-rollout-status.md`), never through
this cache.
Keep the two apart — durable user intent belongs in `local-store.js`, and a
pending or conflicted value must never be written into this cache.

**Which families are durable today** (create/edit/delete work with or without a
server, survive reload, and reconcile on acknowledgement):

- Work Tags: `ADD_WORK_TAG`, `REMOVE_WORK_TAG`
- the Tag vocabulary: `CREATE_TAG`, `DELETE_TAG`, `MERGE_TAG`
- Work creation (video / YouTube): `CREATE_WORK`
- Work deletion: `DELETE_WORK`
- Work opens: `MARK_WORK_OPENED`
- Work metadata: `SET_WORK_METADATA_FIELD`
- Work source identity: `SET_WORK_SOURCE`
- Work-Person roles: `ADD_WORK_PERSON_ROLE`, `REMOVE_WORK_PERSON_ROLE`,
  `SET_WORK_PERSON_ROLE_CREDIT`
- People: `CREATE_PERSON`, `SET_PERSON_METADATA_FIELD`, `DELETE_PERSON`
- Person Groups: `CREATE_PERSON_GROUP`, `SET_PERSON_GROUP_FIELD`,
  `ADD_PERSON_GROUP_MEMBER`, `REMOVE_PERSON_GROUP_MEMBER`,
  `DELETE_PERSON_GROUP`
- Folders: `CREATE_FOLDER`, `SET_FOLDER_FIELD`, `DELETE_FOLDER`,
  `SET_WORK_FOLDER`, `ADD_FOLDER_TAG`, `REMOVE_FOLDER_TAG`
- Playlists: `CREATE_PLAYLIST`, `SET_PLAYLIST_FIELD`,
  `REORDER_PLAYLIST_ITEMS`, `DELETE_PLAYLIST`, `SET_WORK_PLAYLIST`
- Concepts: `CREATE_CONCEPT`, `SET_CONCEPT_FIELD`, `SET_CONCEPT_IDENTITY`,
  `SET_CONCEPT_PARENTS`, `DELETE_CONCEPT`
- Positions: `CREATE_POSITION`, `SET_POSITION_FIELD`, `DELETE_POSITION`
- Arguments / Stances: `CREATE_ARGUMENT`, `SET_ARGUMENT_FIELD`,
  `SET_ARGUMENT_SOURCES`, `SET_ARGUMENT_TARGETS`, `DELETE_ARGUMENT`
- Work notes: `SET_WORK_RESEARCH_NOTE`, `SET_WORK_PRIVATE_NOTE`
- PDF annotations (already-available PDFs): `CREATE_PDF_ANNOTATION`,
  `SET_PDF_ANNOTATION`, `DELETE_PDF_ANNOTATION`

`docs/local-first-rollout-status.md` is the running score and is authoritative
when this file and it disagree. Anything not listed there still calls a
canonical endpoint and is refused while PRKS is unreachable; do not assume a
surface is durable because a neighbouring one is.

The read layer currently covers:

- Work detail pages and their managed PDFs
- the Concept index (`#/concepts`) and Concept detail (`#/concepts/:conceptId`)
- the Position index (`#/positions`) and Position detail (`#/positions/:positionId`)
- the Argument/Stance index (`#/arguments`, including `?kind=argument` and
  `?kind=stance`) and detail (`#/arguments/:argumentId`)
- the People index (`#/people`), its role views (`#/people/role/:role`) and
  Person detail (`#/people/:personId`)
- the Person Groups hierarchy (`#/people/groups`) and Group detail
  (`#/people/groups/:groupId`)
- the Playlist index (`#/playlists`) and Playlist detail
  (`#/playlists/:playlistId`)
- Research Graph (`#/graph`, including `?focus=<type>:<id>`)

The durable families above have an outbox, conflict resolution and
reconciliation; **this read cache must never grow one**. Durable user intent
belongs in `local-store.js` and the sync coordinator. There is still no CRDT or
character-level merging, no multi-user sync and no server push, and
PDF annotations on an already-available managed PDF are local-first
(metadata durable ops; PDF bytes stay in Cache Storage `prks-pdf-v1`).
New PDF binary ingestion remains connection-required.

A family becomes durable only by being *implemented* as one — a semantic
operation with validation, a revision or an explicit "no base revision" rule,
reconciliation and named refusals. See *Adding a family: the four shapes and
what each must declare* in `docs/local-first-sync.md` before starting one.

## Shared coherence-domain framework

Some cached read models span multiple canonical records, so per-entity
invalidation is not enough and must not be pretended to be. Those use an
explicit **offline coherence domain**: a named group of entity kinds and list
keys that are invalidated together.

`prksOfflineMarkDomainChanged(domain, { entityKinds, listKeys })` increments the
domain's generation and blocks its cached fallback **synchronously**, then
sweeps the disposable cache (`deleteEntitiesByKind()` / `deleteList()`) in the
background. During that window no cached value in the domain may be served. The
domain unblocks only when the sweep **for that same generation** completed
successfully: a superseded generation's completion may never unblock, reset, or
publish eligibility for a newer one, and a failed sweep leaves the domain
conservatively blocked for the life of the runtime. Safe degradation is
"unavailable offline," never "known-stale shown offline"; online PRKS keeps
working normally either way. A read associated with a domain captures its
generation when the authoritative request begins and may publish a cache write
only while that generation is still current — an authoritative read never waits
for the sweep before rendering, and a skipped cache write is acceptable because
a later normal read repopulates it.

Domains are **independent**. Each is keyed by name in the runtime's
generation map, blocked set, and pending-invalidation map, so invalidating one
must never increment another's generation, block another's fallback, delete
another's disposable cache, or settle/unblock another's pending invalidation. A
domain whose cleanup failed degrades only itself: the others keep serving
offline, and PRKS keeps working online regardless.
