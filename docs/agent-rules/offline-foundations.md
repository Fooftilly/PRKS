# Offline / PWA foundations

This is the shared implementation contract for current PRKS offline/sync work. Read it for any change that touches offline behavior, durable local intent, service-worker behavior, client caches, reconciliation, or synchronization. Then use `docs/agent-rules/offline-pwa.md` / `docs/agent-context/sync-map.md` to load only the relevant domain-specific leaf rules.


This document is the detailed implementation contract for PRKS offline, local-first, sync, service-worker, and client-cache work.

**Architecture status:** this is the contract for the offline/sync implementation that exists today. Preserve it while changing those paths, but do not treat it as the permanent target for every PRKS client. Roadmap #310 explicitly allows the future authenticated central-server architecture to reassess Web/Android offline depth and synchronization machinery while preserving user-facing durability guarantees. Planned architecture never overrides implemented behavior until an approved migration lands.

It is routed from the root `AGENTS.md`. Read it before changing any of the following areas: `frontend/js/local-store.js`, `frontend/js/offline-store.js`, `frontend/js/offline-runtime.js`, `frontend/sw.js`, synchronization handlers/coordinators, offline projections, conflict/revision behavior, or tests that encode those contracts.

The root `AGENTS.md` remains authoritative for global repository safety, testing, architecture, storage, audit, and UI rules. Where this document discusses a domain-specific invariant, treat it as the detailed contract for that offline/local-first domain.

This section describes the **disposable read cache**. It is not the whole
offline story any more: a growing set of content mutations is *local-first* and
runs through a separate durable store and sync coordinator (see *Local-first
Work Tags* and `docs/local-first-rollout-status.md`), never through this cache.
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

Research Graph caches the **server-generated projection snapshot**, never a
local reconstruction from partial entity/index caches. The existing `entities`
store holds two independent fixed entities, `research-graph-core / snapshot`
and `research-graph-people / snapshot`, in coherence domains of the same names.
Neither domain contains list keys; no IndexedDB schema/version change is needed.
Core uses `/api/research-graph` (`people_included: false`); People uses
`/api/research-graph?people=1` (`people_included: true`). A Person focus requires
the People snapshot; other initial focus types use core. No fallback across
variants, no second-variant prefetch, no background rebuild after mutation, and
no service-worker Graph JSON caching. Cache only `{nodes, edges, meta}`. Layout,
positions, zoom/pan, selection/inspector, Find, filters and opened panels remain
ephemeral in the owning TabContext; Graph remains non-tileable.

`prksOfflineResearchGraphFetch()` in `app.js` delegates to
`prksOfflineDetailFetch()` and passes strict validation before authoritative
publication and before cached data reaches Cytoscape. Validate counts, bounds
(2,500 nodes / 7,500 edges), types, unique IDs, canonical routes, endpoint
existence/type compatibility and requested variant. A malformed 200 is a route
error and leaves the previous good snapshot intact. A corrupt cached snapshot
is discarded best-effort by exact kind/id and becomes offline-unavailable.
HTTP 413 retains `graph_too_large` at this adapter boundary; fail-soft
`derived_note_edges_available: false` remains authoritative/cacheable.

The route injects the snapshot loader and a provenance callback into the Graph.
Use the normal offline banner for every cache-served variant. Missing initial
snapshots are explicitly unavailable offline; a failed People toggle preserves
the current graph, restores its checkbox, and explains the missing variant.
Local Find/filter/layout/inspector interactions stay enabled offline. Graph
nodes and Concept/Position/Argument/Person "View in graph" actions are ordinary
`prksNavigate` navigation: destination routes own cache availability. Mutation
controls stay guarded.

Graph coherence follows the projection's canonical inputs, independently from
the entity-detail domains. `prksMarkResearchGraphCoreChanged()` synchronously
starts both Graph-domain invalidations without awaiting either best-effort sweep;
`prksMarkResearchGraphPeopleChanged()` invalidates only People Graph. Separate
generations preserve core after a People-only edit. Stale in-flight GETs cannot
repopulate invalidated snapshots; cleanup failure blocks only the affected domain.

- Core + People: Concept create/update/delete/parents; Position
  create/update/delete; Argument create/update/delete/sources/targets; every
  successful Research Notes save (including stale UI completions); Work
  metadata/display save through `prksMarkWorkTitleChanged()`; successful Work delete.
- People only: Person canonical first/last-name changes; existing Work Author
  role changes through `prksMarkWorkRoleChanged()` (credit-name edits may
  conservatively invalidate too).
- Neither: Concept aliases; non-name Person edits; ordinary Person creation;
  non-Author roles; Person Groups/memberships; Playlists/reorder; Work
  status/folders/tags/progress outside the conservative metadata-save helper;
  managed PDF save/annotations; plain Work creation, including Author roles on
  that new unreferenced Work. Playlist inline Work rename inherits the shared
  Work-title hook. Failed canonical mutations retain eligibility.

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

Work notes are **local-first**. Research Notes (`SET_WORK_RESEARCH_NOTE`) and
Reminders (`SET_WORK_PRIVATE_NOTE`) are independent whole-document revisioned
aggregates. One user decision is one body and one conflict. The ACK patches
the canonical Work field and the matching notes-state revision. A Research
ACK also fences Concept, Argument and Research Graph (core and People)
derived projections when the body changed *or* the canonical revision
advanced past the operation's observed base (stale convergence onto the
same text). A same-revision no-op does not. The browser must not parse
the body to decide that. A Private ACK never invalidates those
projections. Folder private notes are not this family.

Concepts are **local-first**. Creating one, editing its definition, renaming it,
changing its aliases, reparenting it and deleting it are durable operations
(`CREATE_CONCEPT`, `SET_CONCEPT_FIELD`, `SET_CONCEPT_IDENTITY`,
`SET_CONCEPT_PARENTS`, `DELETE_CONCEPT`) -- see *Concepts (3H)* in
`docs/local-first-sync.md`. The NAME and the ALIAS SET are ONE aggregate,
because renaming keeps the old name reachable as an alias so that existing notes
go on resolving; do not split them. The parent set is one structural judgement,
not a collection of edges. Name-or-alias uniqueness and acyclicity stay
canonical, and `CONCEPT_IN_USE` still refuses a Concept that notes name.

The read cache below still backs the pages themselves. The Concept index uses
the `lists`
store under the stable key `concepts:index`; Concept detail uses the `entities`
store under `kind: 'concept'`. The two caches are independent by design and the
index deliberately does **not** prefetch every Concept detail — seeing a Concept
in a cached index is not a promise that its detail was cached, and an unopened
Concept correctly reports "not available offline" rather than "Concept not
found." Index search stays entirely client-side over the already-loaded array
(zero API requests offline, and no offline FTS). Every Concept mutation surface
(New Concept — including the `prksCreateConceptFlow()` entry point used from
Work Research Notes — Rename, Delete, Definition, aliases, parents) is durable
and carries **no** connectivity guard. What they can still refuse is an unknown
base, via `prksConceptBaseUnavailable()`. "View in graph" navigates normally;
the Graph route owns snapshot availability. Concept mutation controls are not
connectivity-gated in the Vue Concepts surface; a future server-bound Concept
action would need an explicit offline decision rather than a default disable.

An unavailable cached list is not an empty one. A cached `[]` that the server
genuinely returned may render the ordinary "No Concepts yet." empty state (with
New Concept still disabled offline); *no* cached list must render an explicit
"Concepts not available offline / This list has not been cached on this device."
A vocabulary this device could not read is still a page when the durable queue
holds a Concept created here. The shape guarantees the old `fetchConcepts`/`fetchConcept` helpers provided are
not lost by routing through the runtime: a wrong-shaped *server* body is a route
error, while a wrong-shaped *cached* body makes the cache unavailable (and is
discarded best-effort), never a silent empty list or a false "not found."

Authoritative shape acceptance happens **before** cache publication. An
offline-capable read passes a `validate` callback to
`prksOfflineReadEntity`/`readList`; the runtime applies it the moment the body
parses and rejects a bad one as an ordinary non-404 domain error, so nothing is
written to the cache. A reachable server that answers HTTP 200 with the wrong
body must never overwrite a previously good snapshot — doing so turns one bad
response into a route error *now* plus an unavailable-offline Concept domain
*later*. A validator that itself throws counts as rejection, never acceptance.

Positions are **local-first**. Creating one, renaming it, editing its
description and deleting it are durable operations (`CREATE_POSITION`,
`SET_POSITION_FIELD`, `DELETE_POSITION`) -- see *Positions (3I)* in
`docs/local-first-sync.md`. `name` and `description` are INDEPENDENT fields, not
an aggregate: nothing in the schema links them, and joining them would make an
unrelated description edit conflict with a rename. `POSITION_IN_USE` still
refuses a Position an Argument targets, and the deletion is a tombstone, so a
refusal makes it visible again.

The read cache below still backs the pages themselves, and follows the Concept
pattern
exactly: the index uses the `lists` store under `positions:index`, detail uses
the `entities` store under `kind: 'position'`, the two caches are independent,
the index never prefetches details, and index search stays client-side over the
already-loaded array. New Position is guarded before its prompt opens *and*
re-checked immediately before `createPosition()`. Position detail's own
shape guarantee is stricter than a Concept's: the authoritative body must carry
an `arguments` array, while the per-Argument display fields (kind,
verdict_label, …) stay optional, matching what the server actually promises.

"View in graph" and a cached Position's Arguments & Stances rows are **ordinary
PRKS routes** in every runtime state — each destination works offline if that
Argument's own detail was previously cached, and otherwise reports the Argument
route's own "not available offline". Positions gained offline support before
Arguments did, and for that slice the rows were marked `aria-disabled` with an
"Arguments and Stances are not available offline yet" activation guard; that is
obsolete and `positions.js` must not reintroduce it. `POSITION_ARGUMENT_LINK_ROLE`
survives for styling and test identification only and is deliberately absent
from `POSITION_CONTROL_SELECTOR`. Ordinary workspace navigation already owns
plain/middle/modified clicks and route ownership, so a second Position-specific
policy layer could only get that wrong. What has not changed is the underlying
rule: an embedded summary in one entity's read model is not a cache of the
entity it summarises — a cached Position row is not a promise that the Argument
detail behind it was cached.

`prksBindPositionOfflineState()` settles all of that live on a
TabContext-owned, one-binding-per-container contract.

Arguments/Stances are **local-first**. Their five durable operations are
`CREATE_ARGUMENT`, `SET_ARGUMENT_FIELD`, `SET_ARGUMENT_SOURCES`,
`SET_ARGUMENT_TARGETS`, and `DELETE_ARGUMENT`. Both kinds are one record family
and one cache: entity `kind: 'argument'` covers Stances too (a Stance is an
Argument with `kind: 'stance'`), and there is no separate `stances` domain.
Construction mints a permanent distributed `A-` id and carries the initial
scalar state, sources, and targets in ONE atomic operation. Response creation
must include the parent target there; Create from Work must include the Work
source there. Never decompose either into a construction followed by an
aggregate mutation.

The three scalar fields (`name`, `kind`, `main_text`) are independent conflict
units. Sources are one ordered aggregate under
`argument-sources/[argument_id]`. Targets are one ordered aggregate under
`argument-targets/[argument_id]`, spanning both physical Position-target and
Argument-target tables; never split those tables into separate durable
families. Later editing intentionally produces independent scalar/source/target
operations. The editor must compare acknowledged base, effective displayed
state, and draft separately and enqueue only units the user changed.

The index caches the **complete unfiltered collection** under one key,
`arguments:index`. The route always fetches `/api/arguments` without a `kind`
parameter and applies `?kind=` locally via `prksFilterArgumentsByKind()`. That
is deliberate: visiting the Stances tab online warms the cache for All and
Arguments too, and switching tabs offline needs no separately cached
server-filtered list. Never introduce `arguments:index:argument` /
`arguments:index:stance` keys. A legitimately empty *filtered subset* (a cached
list with real Arguments and zero Stances) still renders the ordinary "No
Stances yet." empty state — only a missing cached list is offline-unavailable.

Cached relationship links are ordinary PRKS links, and every destination decides
for itself whether it has cached data: a target Position, a target or response
Argument, a source Work, and a note-mention Work each resolve through their own
route and their own offline state. There is no Argument-specific navigation
fallback. "View in graph" likewise delegates availability to the Graph route.

Validators require what the renderer and the links actually walk, and nothing
more. That includes every *nested* row a template iterates: a source's
`authors[]` entries need a usable Person id because the author label is built
from each of them, so an unusable row there is a crash rather than a cosmetic
gap — while `first_name`/`last_name`/`credit_name` stay optional, since any of
them may legitimately be empty. The same rule is why targets need a `type` and
responses a valid `kind`.

Argument mutation controls remain live offline. An acknowledged edit needs the
cached entity plus its cached sync-state revisions; without those, saving is
refused as an unknown-base condition rather than guessing revision zero. Opening
the editor online warms that sync state so a later disconnect is safe. A pending
local construction needs no server read. Pending/syncing deletes tombstone the
record; a canonical refusal restores it while Diagnostics retains the conflict.
Pending names overlay Position rows, Argument target/response rows, pickers, and
existing Graph nodes. Graph topology is never invented from pending state.

People are **local-first**. Creating a Person, editing a profile field by
field, changing their groups and deleting them are durable operations
(`CREATE_PERSON`, `SET_PERSON_METADATA_FIELD`, `DELETE_PERSON`, plus the Person
Group families below) that work with or without a server; `DELETE_PERSON` keeps
its canonical protection — a Person credited on a file is refused, never
cascaded. Editing which files a Person is linked to **from their profile page**
is still online-only; the same links are durable from the file's own People
panel.

The read cache below still backs the pages themselves. The index caches the
complete
collection under one key, `people:index`, and every role view is a local
projection of it via `filterPersonsByAssignedRole()` — both routes go through
the single `prksOfflinePeopleFetch()` helper so a future People route cannot
introduce a second, role-filtered cache. Visiting one role view online warms
every other view. A legitimately empty *role subset* (cached People, none
holding that role) renders the ordinary empty role state; only a missing cached
list is offline-unavailable. Person detail uses `kind: 'person'`, and the two
caches stay independent — the index never prefetches details.

Person validators accept a sparse profile: no first name, biography, dates or
links is normal. What they do require is a usable id on every nested row that
becomes a route — `works[]` (→ `#/works/:id`) and `groups[]` (→
`#/people/groups/:id`) — and that `assigned_roles[]` entries are strings without
an allow-list, since canonical data carries roles like `Mentioned` that are not
navigable filters. Optional Person display fields must also be null/omitted or
strings when renderers/search operate on them as strings; linked Work `year` and
`published_date` follow the same rule. Validation protects type/shape, not
business completeness, so empty strings remain valid.

A Person's "View in graph" uses the People-inclusive snapshot. A Person's Group
chips are **ordinary PRKS links** — a
cached Group detail opens offline, an uncached one reports "Group not available
offline" — so `people.js` must not reintroduce the old `aria-disabled` /
`click`+`auxclick` interception on `PERSON_GROUP_LINK_ROLE`; that role survives
for styling and test identification only and is deliberately absent from
`PERSON_CONTROL_SELECTOR`. Linked Work cards are ordinary PRKS links for the
same reason, so the Work route decides for itself whether it has cached data —
that is the main reason People is useful offline. There is no offline-specific
router: every one of these destinations is reached through the same
`prksNavigate` as online.

**Offline media policy.** Structured data only — portraits and Work thumbnails
are never part of the disposable cache. A Person route
mounted from cache sets `ctx.ui.personOfflineCached`, which suppresses the
portrait (`/api/persons/:id/profile-image`) and passes `suppressThumbnail: true`
to `prksWorkCardHtml()`, so a cached mount issues no PRKS media request and
shows the ordinary no-photo/empty-thumb presentation rather than broken images.
Portrait and thumbnail bytes are never cached — not in IndexedDB, not in the
service worker. Media already loaded online is not torn down when connectivity
drops, and re-hydrating it after reconnect is explicitly not required.

`prksBindPersonOfflineState()` settles all of that live. Because the Person
editor lives in the shared right panel, its portion only runs when
`prksRightPanelOwnedBy()` says this context owns that panel — a background
Person tab must never disable or rewrite another tab's panel. An editor open
when connectivity drops keeps its unsaved draft with only its mutating controls
inert (**Cancel stays live**). `openModal('person-modal')` carries **no**
connectivity guard: creating a Person is durable-first under an id this device
mints, so the modal opens and saves with or without a server, and
`person-template-modal` never had one because it only edits an unsaved local
draft. What can still be refused is an **unknown base** — a profile this device
has never read has no revision to measure an edit against — which is a
different refusal from "no connection".
