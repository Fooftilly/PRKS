# Offline / PWA agent rules

This document is the detailed implementation contract for PRKS offline, local-first, sync, service-worker, and client-cache work.

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
the Graph route owns snapshot availability. `prksBindConceptOfflineState()`
survives with an empty server-bound selector: adding one back means asserting
that the action genuinely cannot be represented offline, not that disabling it
is easier.

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

`prksBindPositionOfflineState()` settles all of that live, on the same
TabContext-owned, one-binding-per-container contract as
`prksBindConceptOfflineState()`.

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

### Offline coherence domains

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

The first domain is `concepts` (`entityKinds: ['concept']`,
`listKeys: ['concepts:index']`), defined once in
`prksOfflineMarkConceptsChanged()` so every canonical caller invalidates the
same set. It is invalidated after success by: every Concept mutation
(create/update/delete/parents/aliases, at the `api.js` canonical helper boundary
— never gated on route, focused pane, ctx generation, or panel ownership); every
successful Research Notes save (notes are the canonical Work → Concept mention
source and unknown markup can create Concepts outright — canonical success
counts even when that save is stale for the UI); Work deletion; and **every**
canonical Work-title change. Unrelated Work mutations (tags, folders,
playlists, roles, progress, PDF annotations) do not touch it. A failed,
canceled, or aborted mutation never invalidates anything.

A cached Concept detail lists the titles of the Works that mention it, so a
Work rename stales the Concept read model even though no Concept record
changed. Every title-editing surface therefore goes through the one helper
`prksMarkWorkTitleChanged(workId)` (Work entity eviction *plus* Concepts-domain
invalidation), never `prksOfflineMarkEntityChanged('work', …)` alone — the Work
metadata editor and the Playlist inline video rename both do, and a new
title-editing surface must too. `tests/test_frontend_offline_runtime.py` scans
for Work-title PATCH sites that skip the helper. The policy is deliberately
conservative: a date-only metadata edit invalidates Concepts as well, rather
than relying on a field diff.

The second domain is `positions` (`entityKinds: ['position']`,
`listKeys: ['positions:index']`), defined once in
`prksOfflineMarkPositionsChanged()`. Position mutations reconcile through their
durable handlers. Because a cached Position detail embeds derived
Argument/Stance summaries plus its targeting list, acknowledged Argument
construction, name/kind changes, target replacement, and deletion reconcile or
fence this domain too.

Deliberately **not** invalidating Positions: `putArgumentSources` (Position
detail never displays an Argument's source Works), Research Notes saves (notes
drive Concept references and Argument mentions, not `positions` or
`argument_target_positions`), Work metadata/title/role changes, tags, folders,
playlists, progress, PDF annotations, and Concept mutations. None of those
change the Position index/detail read model, and copying the Concepts policy
onto Positions without checking would only shorten cache life for nothing.
Editing one Argument may legitimately enqueue several independent durable
operations. Do not invent cross-unit atomicity to avoid overlapping fences.

The third domain is `arguments` (`entityKinds: ['argument']`,
`listKeys: ['arguments:index']`), defined once in
`prksOfflineMarkArgumentsChanged()`. It has the widest dependency set in PRKS,
because a cached Argument embeds data owned by five other record families. It is
invalidated after canonical success by:

| Canonical change | Why it stales a cached Argument |
| --- | --- |
| `createArgument`, `updateArgument`, `deleteArgument` | name/kind appear in every other Argument that targets or answers it, and in the index |
| `putArgumentTargets` | this Argument's targets *and* the target's responses list |
| `putArgumentSources` | source Works, their titles, pages and authors |
| `updatePosition` | targets embed the Position's name |
| Work title change (via `prksMarkWorkTitleChanged`) | `sources[].work_title` and `mentions[].title` |
| successful Research Notes save | notes are the canonical source of `[[argument:…]]` mentions and `mention_count` |
| Work deletion | drops `argument_sources` rows *and* that Work's note backlinks |
| Work Author link/unlink/credit-name (via `prksMarkWorkAuthorDisplayChanged`) | `sources[].authors[]` carries person names and per-Work credit names |
| Person canonical-name change | those same author rows display `first_name`/`last_name` |

Deliberately **not** invalidating Arguments: `createPosition` (a new Position
cannot already be targeted) and Position deletion (a targeted Position cannot be
deleted); every Concept mutation; Work tags, folders, playlist membership,
progress and PDF annotations; non-Author Work roles; Person Group membership;
and Person profile fields that cannot change the displayed author — biography,
links, dates, portrait, groups. The Person hook does a plain first/last-name
diff precisely so a biography edit does not cost the user their cached
Arguments. Only `putArgumentTargets` (not `putArgumentSources`) also invalidates
Positions: source Works are in the Argument read model and not the Position one.

**Partial durable success still reconciles.** If one Argument conflict unit
acknowledges and another conflicts, the acknowledged unit keeps its
reconciliation and the conflicting unit remains in Diagnostics. A multi-unit
edit is not a transaction; only construction is atomic.

The fourth domain is `people` (`entityKinds: ['person']`,
`listKeys: ['people:index']`), defined once in `prksOfflineMarkPeopleChanged()`.
A cached Person carries whole Work-card summaries, its role assignments and its
Group memberships, so it is invalidated after canonical success by:

| Canonical change | Why it stales cached People |
| --- | --- |
| Person create / update / delete | every profile field is in the read model |
| **any** Work-role create / unlink / credit-name edit | `assigned_roles`, the Person's linked Work rows (role_type, order_index, credit_name), and `persons.aliases`, which the server may extend with a non-empty credit name |
| Work metadata/title save (via `prksMarkWorkTitleChanged`) | the Work card shows title, status, doc type, year, author text, thumbnail metadata and file size |
| bulk `set_status` | status is on that card |
| Work deletion | removes the role rows entirely |
| Work creation carrying `roles: [...]` | the create endpoint links roles without ever calling `POST /api/roles` |
| managed PDF save | changes `file_size_bytes`, and the backend can add `Mentioned` roles from annotation markup |
| Group membership add/remove, Group update, Group delete | Group chips and memberships are embedded in both People read models |

Role coherence is owned by one helper, `prksMarkWorkRoleChanged(workId,
roleType)`: People unconditionally, Arguments and People Graph only for `Author`. The former
`prksMarkWorkAuthorDisplayChanged` name survives purely as a delegate.

Deliberately **not** invalidating People: creating an unassigned Group (it
appears in no existing Person's read model, and the Person PATCH that later
assigns it invalidates People on its own); bulk `move_folder`/`add_tags`/
`remove_tags` and ordinary tag/folder/playlist-membership edits (none are on a
Person's Work cards); the annotations-JSON save, as distinct from the managed
PDF save; Research Notes saves; and every Concept, Position and Argument
mutation. Note the distinction: playlist *membership* does not invalidate
People, but a playlist inline Work *rename* does, because it goes through the
shared Work-title helper.

**Person profile PATCH is atomic.** `update_person_profile()` applies metadata
and group memberships in one transaction when `group_ids` is supplied, so an
unknown group id can no longer return 400 *after* the metadata was written.
Offline coherence rests on "a failed canonical request keeps the previous cache
eligible", which is only sound if a 4xx really means nothing changed. A
metadata-only PATCH (no `group_ids`) keeps its original behavior, and the
disposable portrait cache is cleared only after that transaction commits — its
failure never fails the PATCH.

Person Groups are **local-first**. Creating a group, renaming it, editing its
description, moving it, deleting it and changing who is in it are durable
operations (`CREATE_PERSON_GROUP`, `SET_PERSON_GROUP_FIELD`,
`ADD_PERSON_GROUP_MEMBER`, `REMOVE_PERSON_GROUP_MEMBER`,
`DELETE_PERSON_GROUP`). Membership is a **pair** `(group, person)`, so two
people joining one group never collide. Name uniqueness and acyclicity stay
canonical, and deletion is a tombstone that reparents children exactly as the
ordinary endpoint does.

The read cache below still backs the pages themselves. The hierarchy uses the
`lists` store
under `person-groups:index`; Group detail uses the `entities` store under
`kind: 'person-group'`. The two caches are independent and the index
deliberately does **not** prefetch Group details — seeing a Group in the cached
hierarchy is not a promise its detail was cached, and an unopened Group reports
"Group not available offline" rather than "Group not found". The hierarchy
tree, its local search, and expand/collapse all run entirely client-side over
the one cached array (zero API requests offline). A cached *empty* array is the
ordinary "No Person Groups yet." state; only a missing or invalid cached list is
offline-unavailable. Group members are ordinary People rows validated by the
shared `prksIsPeopleIndexRowShape()` — never a weaker Group-local duplicate —
and parent/subgroup links are ordinary Group routes, so each destination decides
for itself.

The Group validators (`prksIsPersonGroupSummaryShape`,
`prksIsPersonGroupsIndexShape`, `prksIsPersonGroupShape`) are deliberately split
rather than uniformly strict: `child_count` is required on **index** rows only,
because the canonical detail endpoint serialises it on neither the group itself
nor its `children[]`. `prksIsGroupCount()` stays strict (finite, non-negative
number) because every count is a SQL `COUNT(*)`. `parent` is always present on a
detail — `null` for a top-level group — so a missing key is a malformed
response, not a root group.

The fifth domain is `person-groups` (`entityKinds: ['person-group']`,
`listKeys: ['person-groups:index']`), defined once in
`prksOfflineMarkPersonGroupsChanged()`. **Person Group data is a domain-level
read model, not a per-Group cache**, and that is why the whole domain goes at
once:

- renaming child C stales the index, C's detail, *and* C's parent's
  `children[]`
- changing C's membership stales C's `member_count`, C's detail, *and* the
  parent detail's child `member_count`
- reparenting C stales the old parent, the new parent, *and* the hierarchy index

Its dependency table:

| Canonical change | Domains invalidated |
| --- | --- |
| Group create | Person Groups only |
| Group update | Person Groups + People |
| Group delete | Person Groups + People |
| Group member add/remove | Person Groups + People |
| Person profile update | People + Person Groups (+ Arguments on a canonical **name** change only) |
| Person delete | People + Person Groups |
| Person create | People only |
| any Work-role mutation | People + Person Groups (+ Arguments for `Author` only) |
| Work creation carrying `roles: [...]` | People + Person Groups — **never** Arguments, even for an `Author` role |
| Work deletion | Person Groups + Concepts, Arguments, People |
| managed PDF save | People + Person Groups |

Two asymmetries are deliberate, and both follow from the same rule: a record
that did not exist a moment ago cannot be inside anyone's cached read model. A
brand-new Group never invalidates People — the Person PATCH that later assigns
it does that on its own. And a brand-new Work carrying an `Author` role never
invalidates Arguments, unlike an Author link onto an *existing* Work: the new
Work is in no cached Argument's `sources[]` (those rows only come from
`putArgumentSources`) or `mentions[]` (those come from research notes, empty at
create), and no Person's displayed name changed. Do not "fix" that by routing
Work-create through `prksMarkWorkRoleChanged()`; it would shorten the Arguments
cache for nothing.

Everything else Work-side is inherited rather than invented: a cached Group
detail embeds whole People index rows, so anything that stales a Person's
`assigned_roles` stales the Group that Person is in. Role coherence therefore rides on the same one helper,
`prksMarkWorkRoleChanged(workId, roleType)` — Person Groups and People
unconditionally, Arguments only for `Author`.

Deliberately **not** invalidating Person Groups: Work metadata/title/status
saves, Work folder/tag/playlist membership, Research Notes saves, and every
Concept, Position and Argument/Stance mutation — none of them can change a
Group's name, hierarchy or membership rows. Ordinary *unassigned* Person
creation is excluded for the same reason Group creation does not invalidate
People. Person Groups must not become a catch-all invalidation domain; that
exclusion list is the point of the domain, not an oversight.

**Person Group mutations are canonically atomic.**
`add_person_group_with_parent_options`, `update_person_group` and
`delete_person_group` each run their multi-write work in **one** transaction:
typed-parent resolution/creation plus the requested create, typed-parent
resolution/creation plus the update, and child reparenting plus the deletion. A failed canonical Group request must therefore never leave a
partial mutation behind — no orphan typed parent survives a rejected create or
update, and a failed delete leaves the child hierarchy intact. This matters
independently of offline support, but offline coherence rests on it directly:
"a failed canonical request keeps the previous cache eligible" is only sound if
a 4xx really means nothing changed. Parent resolution lives only in
`db_manager.py`'s transaction-aware `_resolve_group_parent` /
`_insert_person_group` / `_update_person_group` helpers — the standalone
auto-committing versions (`resolve_or_create_parent_group_by_name`,
`_person_group_descendant_ids`) were the source of the orphan-parent bug and are
gone. Do not reintroduce either, and do not move parent resolution back into
`server.py`.

Every Group mutation surface routes through the durable writers, so there is
exactly one boundary per operation — including the standard New Group modal and
typed Group creation from the Person profile editor. None of them guards
connectivity: `openModal('group-modal')` opens offline because the id is minted
here, and Save/Delete/add/remove all enqueue durable intent. A group carrying a
pending deletion accepts nothing further — renaming something about to stop
existing is refused locally rather than sent for the server to reject.
`prksBindPersonGroupOfflineState()` settles the live half: a mounted Group
editor or membership manager keeps its unsaved draft with only its mutating
controls inert (**Cancel and Done stay live**) rather than being reloaded on a
connectivity change. Because that editor lives in the shared right panel, its
async picker setup re-checks ctx generation, that the editor is still mounted,
that this ctx still owns the panel, and that the same edit panel is still
present — never "whichever context is focused when the callback happens to
finish", which would mutate another tab's panel.

Playlists are **local-first**. Creating one, editing its title / description /
original URL, putting a video in one, taking it out, reordering it and deleting
it are all durable semantic operations (`CREATE_PLAYLIST`,
`SET_PLAYLIST_FIELD`, `SET_WORK_PLAYLIST`, `REORDER_PLAYLIST_ITEMS`,
`DELETE_PLAYLIST`) that work with or without a server — see *Playlists (3G)* in
`docs/local-first-sync.md`. The ORDER is one aggregate under one revision: an
ordered list must never be modelled as independently racing `order_index`
fields. Which playlist a video is in is a scalar on the **Work**, because a
video is in at most one.

Two Playlist surfaces are still online-only, and both are *searches* over
something no cache holds: the "Add video" search reads the whole Works
catalogue, and the Work card's playlist picker reads the Playlist catalogue.
Both only disable a search — every decision they lead to is durable.

The read cache below still backs the pages themselves. The index uses the
`lists` store under
`playlists:index` (`GET /api/playlists` already returns the complete catalog —
there is deliberately no second per-Playlist or item-level list key); Playlist
detail uses the `entities` store under `kind: 'playlist'`. The two caches are
independent and the index does **not** prefetch Playlist details — seeing a
Playlist in the cached list is not a promise its detail was cached, and an
unopened one reports "Playlist not available offline" rather than "Playlist not
found". A cached *empty* array is the ordinary "No playlists yet." state; only a
missing or invalid cached list is offline-unavailable. The Playlist index has no
user-facing search, and one must not be invented offline just because other
domains have one — offline behavior matches the online UI.

Navigation from a cached Playlist is deliberately untouched: each item is an
ordinary `#/works/:id` link and "All playlists" an ordinary route, so every
destination decides for itself whether it has cached data. `original_url` stays
an ordinary external link — PRKS being unreachable says nothing about the rest
of the internet.

The Playlist validators (`prksIsPlaylistsIndexShape`, `prksIsPlaylistShape`)
protect exactly what the renderers dereference, not the whole Work summary the
detail endpoint joins in: `id`/`title`/`item_count` on index rows, and
`id`/`title`/`description`/`original_url` plus each item's `id` (→
`#/works/:id`), `title`, `author_text` and `published_date` on a detail. An
item's `position` is `NOT NULL` in the schema and always selected by
`get_playlist()`, so it is validated as a non-negative integer — a row without
one is a malformed payload, not a sparse record. Do not extend these into a
full Work-summary schema. The rule across every domain is the same:
validation protects type/shape for what the renderer actually touches,
not business completeness, so sparse-but-usable rows stay valid.

The sixth domain is `playlists` (`entityKinds: ['playlist']`,
`listKeys: ['playlists:index']`), defined once in
`prksOfflineMarkPlaylistsChanged()`. Its dependency table:

| Canonical change | Playlists | Work entity |
| --- | --- | --- |
| Playlist create | YES | — |
| Playlist description / original URL edit | YES | — |
| Playlist **title** edit | YES | every current member Work |
| add / move a Work into a Playlist | YES | that Work |
| remove a Work from a Playlist | YES | that Work |
| reorder a Playlist | YES | — |
| Work metadata/title save (via `prksMarkWorkTitleChanged`) | YES | existing behavior |
| Work deletion | YES | existing behavior |
| Work creation carrying a non-blank `playlist_id` | YES | — (nothing cached yet) |

The Work-entity column exists because `get_work()` embeds `playlist_id` and
`playlist_title`. Renaming a Playlist therefore stales the cached Work entity of
every Work in it — the **reconciler** does that diff on acknowledgement, from
the playlist detail this device holds, so no call site can forget and a
description-only edit keeps those Works offline-available. Only the
*current* members need eviction: a Work moved in or out from elsewhere had its
snapshot evicted by that membership mutation. In the other direction one
Playlist per Work means moving a Work from A to B changes both, which
whole-domain invalidation already covers without per-Playlist bookkeeping.
Reorder is the one membership-shaped change that touches no Work field, but it
still bumps `updated_at`, which is the index's sort key.

Deliberately **not** invalidating Playlists: **any** Work-role mutation
(Author/Editor/Reviewer/Mentioned, credit names) — the endpoint returns broad
Work-summary person extras but the Playlist UI renders none of them; every
Person and Person Group mutation, for the same reason; managed PDF saves,
annotations and thumbnails (file-size metadata is in the row, not on screen);
bulk `set_status`, folders, tags and progress (Playlist detail shows no Work
status); and every Concept, Position, Argument/Stance and Research Notes
mutation. If the Playlist UI later starts rendering role-derived authors or
status, add that dependency **then** — not pre-emptively.

Every production Playlist write goes through the durable wrappers in
`playlists.js` (`createPlaylist`, `updatePlaylist`, `addWorkToPlaylist`,
`removeWorkFromPlaylist`, `reorderPlaylist`, `deletePlaylistCanonical`), so
there is exactly one boundary per operation. None of them guards connectivity —
the decision is durable — and none marks a domain changed, because the
reconcilers own the cache once the server answers. What a caller can still be
told is that the **base is unknown**: a playlist this device has never read has
no revision to measure an edit against, and guessing would silently overwrite
another device. That refusal is `prksPlaylistBaseUnavailable()`, and it is a
different thing from "no connection".

`openModal('playlist-modal')` carries no guard either: the id is minted here, so
the playlist is real before any server hears of it, and a video waiting to be
attached is ordered behind that creation by the generic dependency mechanism.
The inline Work rename inside a Playlist is a **Work** change and keeps using
the Work metadata family — a Playlist-specific title mutation would be a second,
non-revision-aware path to the same column.

`prksBindPlaylistOfflineState()` still settles the one online-only control on
the route's own TabContext (never a global Playlist singleton), and the
right-panel half only runs when `prksRightPanelOwnedBy()` says this context owns
that panel.

The Work detail page's own Playlist card (Set playlist / Clear / New…) is a
Playlist mutation surface living on a **Work** route, so it cannot ride on that
binding and has its own `prksApplyWorkPlaylistOfflineState()` plus a
live-tab-context subscription, in the same shape as the private-notes one. Two
rules there are easy to get wrong. First, **Edit is refused only when it would
*start* a session**: mounting the editor reads the Playlist catalogue, while
`Done` stays live so a user can always leave it. Clear and `New…` stay live too
— neither needs the catalogue, and both are ordinary durable decisions. Second,
mounting that editor calls `fetchPlaylists()`, and the Prev/Next block calls
`fetchPlaylistDetails()` — both are *raw* reads, not offline read-throughs, so
both are skipped entirely while non-online rather than left to fail. That is not
only about wasted requests: `mountPlaylistAttachControls()` is invoked with
`void`, so a rethrown transport failure would surface as an unhandled rejection.
The catalog read is additionally wrapped, because the connection can drop
*during* it. Finally, the `New…` handler still clears
`window.__prksPendingPlaylistAttach` on every open, so a `workId` left by an
abandoned flow is never picked up by the next Playlist creation from any
surface.

**Playlist membership removal is transactional.** `remove_work_from_playlist()`
deletes the `playlist_items` row and bumps the Playlist timestamp in one
transaction, matching `add_work_to_playlist()` and `reorder_playlist()`. Offline
coherence rests on "a failed canonical request keeps the previous cache
eligible", which is only sound if a failure really means nothing changed — two
auto-committing statements could otherwise drop the membership, fail the second
write, and return an error the client would correctly treat as a no-op. This is
the same invariant as the Person profile PATCH and the Person Group mutations
above.

Folders/Home are **local-first**. Creating a folder (including inside one just
created), renaming it, editing its description and private notes, moving it,
changing its tags, and deleting it are durable operations (`CREATE_FOLDER`,
`SET_FOLDER_FIELD`, `DELETE_FOLDER`, `ADD_FOLDER_TAG`, `REMOVE_FOLDER_TAG`), and
which folder a file is in is a scalar on the **Work** (`SET_WORK_FOLDER`)
because a file is in at most one. Name uniqueness within a parent and
acyclicity stay canonical, and the empty-only delete rule is unchanged —
`FOLDER_NOT_EMPTY` / `FOLDER_HAS_SUBFOLDERS` come back as named refusals.

The read cache below still backs the pages themselves. `#/folders` is PRKS's
default route, so
this is what makes an offline launch land somewhere useful rather than on an
empty library. The hierarchy uses the `lists` store under `folders:index`
(`GET /api/folders` already returns the complete catalog — there is deliberately
no per-parent or `folder-children:<id>` key); Folder detail uses the `entities`
store under `kind: 'folder'`. The two caches are independent and the index does
**not** prefetch Folder details — an unopened Folder reports "Folder not
available offline", never "Folder not found". A cached *empty* array is a
legitimately empty library; only a missing or invalid cached list is
offline-unavailable, and the two must not be collapsed. Folder search and
expand/collapse are local projections of the cached list and issue zero
requests offline.

The Folder Library's **Recently added** tab is backed by the disposable
`recently-added:index` list (coherence domain `recently-added`), the same
compact projection as `#/recent` and browse. Offline it reopens from cache when
this device has warmed it; a missing snapshot is offline-unavailable rather than
an empty library. Session-tab restore and mid-load disconnect checks still avoid
firing doomed requests when the list is not cached. Do not collapse "uncached"
with "empty", and do not imply every Home dashboard control is cached without
checking its domain.

The Folder validators (`prksIsFoldersIndexShape`, `prksIsFolderShape`) protect
what the renderers dereference: `id`/`title`/`description`/`parent_id` plus
non-negative integer `work_count`/`child_count` on hierarchy rows, and on a
detail additionally `private_notes`, a null-or-valid `parent` summary, and
`children`/`works`/`tags` arrays. Because Folder detail feeds `folder.works[]`
straight to `prksWorkCardHtml()`, `prksIsWorkCardRowShape()` validates that
card's row contract — `title`, `year`, `published_date`, `status`, `doc_type`,
`file_path`, `author_text`, `linked_authors`, `primary_author`,
`primary_editor`, `thumb_url` and a non-negative `file_size_bytes`. Validating
`id` alone would let a cached row render "NaN MB". This is still the card's
contract, not the whole Work detail schema. Backend hierarchy rules (cycle
detection, parent existence, unique titles, count correctness) stay canonical
and must not be re-implemented in the validator.

A cached Folder detail renders its Work cards with `suppressThumbnail: true`
and skips `prksInitLazyWorkThumbs()` entirely — a thumbnail is a PRKS-server
request that cannot succeed from IndexedDB, and a broken image is worse than
none. Online appearance is unchanged. Navigation is deliberately untouched:
parent/subfolder links are ordinary Folder routes and each Work card an
ordinary `#/works/:id` link, so every destination owns its own availability.

The seventh domain is `folders` (`entityKinds: ['folder']`,
`listKeys: ['folders:index']`), defined once in
`prksOfflineMarkFoldersChanged()`. Whole-domain invalidation is required rather
than per-Folder: moving a Work from A to B changes both details *and* both
`work_count`s in the index, and a reparent changes the hierarchy for every
ancestor. Its dependency table:

| Canonical change | Folders | Work entity |
| --- | --- | --- |
| Folder create | YES | — |
| Folder description / private-notes / parent edit | YES | — |
| Folder **title** edit | YES | every current member Work |
| Folder delete | YES | — |
| add / move / clear a Work's Folder | YES | that Work |
| bulk `move_folder` | YES | those Works |
| bulk `set_status` | YES | those Works |
| Folder tag add / remove | YES | — |
| **any** Work creation (incl. no folder chosen) | YES | — (nothing cached yet) |
| Work deletion | YES | existing behavior |
| Work metadata/title save (via `prksMarkWorkTitleChanged`) | YES | existing behavior |
| Author **or Editor** role change (via `prksMarkWorkRoleChanged`) | YES | existing behavior |
| Person canonical first/last-name change | YES | — |
| managed PDF save (changes `file_size_bytes`) | YES | existing behavior |

Two entries differ from Playlists and are easy to get wrong. First, **every**
successful Work creation invalidates Folders, unconditionally: unlike Playlist
membership, folder membership is not optional — the create endpoint files every
new Work into the requested folder or into the default "Uncategorized" one, so
a `work_count` always changes. Do not make this conditional on an explicit
`folder_id`. `importProcessingFile()` is the same canonical shape and owes the
same hooks. Second, **Editor** counts alongside Author, because a Work card's
credit line is `linked_authors` → `author_text` → `primary_editor`; other roles
(Reviewer, Translator, Mentioned) are not rendered there and deliberately leave
Folders eligible.

Folder **title** is the only Folder field embedded in a cached Work detail
(`folder_title`), so only a rename evicts member Work snapshots. The narrow
boundary is canonical, not UI-derived: `PATCH /api/folders/:id` collects the
members **before** the write (membership cannot change in that request) and
returns them as `member_work_ids`, which `patchFolder()` evicts. That is why a
description-, private-notes- or parent-only edit costs no Work cache and why
this does not depend on which page happened to be focused.

Deliberately **not** invalidating Folders: Playlist mutations, Research Notes
saves, Concept/Position/Argument/Stance changes, Work **tag**-only mutations,
Person Group changes, non-name Person edits, ordinary Person creation, and
non-Author/non-Editor role changes. If the Folder UI later renders one of
those, add the dependency **then**.

Every production Folder write goes through the `api.js` wrappers
(`createFolder`, `patchFolder`, `deleteFolderCanonical`, `addWorkToFolder`,
`patchWorkFolder`, `addTagToFolder`, `removeTagFromFolder`), so there is exactly
one boundary per operation. All of them are **durable** and carry no
connectivity guard; what they can still refuse is an unknown base (Folder fields
via `prksFolderSaveMessage()`, Folder tags when `folder-tag-options` has never
been prepared). Folder-tag add/remove enqueue `ADD_FOLDER_TAG` /
`REMOVE_FOLDER_TAG` through `coalesceFolderTag` / `prksFolderTagEdit`, mirroring
Work tags. The three former quick-create surfaces (the Folder modal
in `app.js`, `quickCreateFolder()` in `ui.js`, and the processing inbox) all
route through `createFolder()` rather than posting raw. The one documented
exception is the coalesced private-notes autosave in `ui.js`, which is gated by
its own runtime check and publishes Folder coherence on success;
`tests/test_frontend_offline_runtime.py` fails the build if any other module
pairs an `/api/folders` URL with a mutating method. Tag **merge** is durable
(`MERGE_TAG`): an identity transform with a null base that refuses while any
unsynchronized operation still names the source, rather than retargeting
intents.

`prksOpenFolderModalFromLibrarySearch()` no longer guards anything: the folder
is real the moment it is written, so the dashboard and the create-from-search
empty state both open offline. `prksBindFolderOfflineState()` settles what
genuinely still needs a server on the route's own
TabContext. The Work detail page's own Folder card is a Folder mutation surface
living on a **Work** route, so — exactly like the Playlist card — it has its own
`prksApplyWorkFolderOfflineState()` plus a live-tab-context subscription, Edit
is refused only when it would *start* a session (**Done stays live**), and
`mountFolderAttachControlsForWork()` skips its raw `fetchFolders()` catalog read
entirely while non-online rather than letting it fail under `void`.

**The `/api/folders` ETag is derived from the serialized catalog.** The
invariant is one-directional but absolute: if the body can change, the ETag
must change. A revision probe built from row *counts* plus `MAX(updated_at)`
could not satisfy it, and shipped two real holes — moving a Work from folder A
to B leaves the `folder_files` row count identical while both rows'
`work_count` change, and `CURRENT_TIMESTAMP` has one-second granularity so
`MAX(updated_at)` does not reliably move for a change made inside the same
second. Either hole lets a stale catalog revalidate as `304` and be
republished into `folders:index` *after* the offline domain was correctly
invalidated — a client-side invalidation cannot defend against a server that
says "unchanged" when the representation changed. `etag_folders_catalog(rows)`
now hashes the payload, so the invariant holds by construction and cannot
drift when a field is added to `get_all_folders()`; the handler builds the
catalog once and passes it in. `tests/test_server_api.py` asserts a direct
move, a bulk `move_folder`, and every index field mutated back-to-back inside
one second, all against a real `If-None-Match`.

**Tag delete and merge report what they staled.** A cached Work detail embeds
`work.tags[]` and a cached Folder detail `folder.tags[]`, so
`DELETE /api/tags/:id` and `POST /api/tags/merge` (and their durable
`DELETE_TAG` / `MERGE_TAG` counterparts) stale both read models.
Both collect the linked entities **before** the write (the FK cascade and the
link move respectively destroy the evidence) and return `affected_work_ids` /
`affected_folder_ids`. The ordinary HTTP merge path still publishes through
`prksPublishTagCoherence()`; durable delete/merge reconcile the same IDs at
acknowledgement (`reconcileDeletedTag` / `reconcileMergedTag`). Server-reported
IDs are what make this correct regardless of the active route, the focused tab,
which surface initiated the mutation, or whether this client had ever loaded
those relationships. For a merge the affected set is everything linked to the
**source**: its name disappears from the rendered list whether or not the
target was already present. Tag **alias** mutations deliberately publish
nothing — aliases live in `tag_aliases` and never appear in a cached `tags[]`.
Only acknowledged canonical success publishes: a transport failure, HTTP error,
validation error or abort leaves every snapshot eligible, because nothing
canonical changed.

**`parent_id` must be present, not merely nullish.** `get_all_folders()`
selects `f.*`, `get_folder()` selects `*`, and the children query names the
column, so the canonical API always carries it and spells a root folder as an
explicit `null`. The validator requires the own property: accepting `undefined`
would let a truncated HTTP-200 row silently reparent a folder to the top level
of someone's hierarchy. The `parent` summary is exempt — it selects only
`id`/`title`.

**Folder deletion and folder-tag removal are transactional.**
`delete_empty_folder()` deletes the folder row and prunes its newly-unused tags
in one transaction via `_prune_tag_if_unused_on_conn()`, and
`remove_tag_from_folder()` does the same for the membership row plus its prune.
`remove_tag_from_work()` had the identical defect and was fixed in the same
pass. Offline coherence rests on "a failed canonical request keeps the previous
cache eligible", which is only sound if a failure really means nothing changed
— two auto-committing statements could otherwise delete the folder, fail the
prune, and return an error the client would correctly treat as a no-op, leaving
a permanently stale cache. `tests/test_folder_atomicity.py` forces the prune to
fail and asserts the first write rolled back; those tests fail against the
pre-fix implementations.

### Offline browse catalogs

`#/progress`, `#/types`, `#/types/:type`, `#/recent` and Home -> **Recently
added** are backed by **three independent projections**, deliberately not one
Works catalog:

| List key | Domain | Endpoint | Routes |
| --- | --- | --- | --- |
| `works-browse:index` | `works-browse` | `GET /api/works?projection=browse` | Progress, Types, Type detail |
| `recent:index` | `recent` | `GET /api/recent` | `#/recent` |
| `recently-added:index` | `recently-added` | `GET /api/recently-added` | Home -> Recently added |

The split exists because **opening a Work is a canonical mutation** -- an
explicit one: `POST /api/works/:id/opened` stamps `last_opened_at`, and
`GET /api/works/:id` is a pure read (detailed below). A single
catalog carrying that field would mean opening one file invalidates Progress,
Types and Recently added too -- one surface dropping four unrelated ones. So
the stable catalog carries no `last_opened_at` at all, and only the explicit
open event marks `recent`.

`?projection=browse` is an **additive** contract: the default `/api/works`
response is unchanged for its seven other callers (pickers, the wiki title map,
the role modal, the Playlist panel). The projection drops the whole `abstract`
in favour of a server-bounded `abstract_excerpt` (100 chars, the only thing
`#/progress` renders) plus the bibliographic block no card shows -- measured at
**70.8% smaller, 82.6% gzipped**. Recent and Recently added are single-consumer
endpoints and were moved onto the same compact projection outright.

**Ordering is canonical, not approximated.** `last_opened_at` and `created_at`
have one-second resolution, so ties are ordinary; both projections order by
`<timestamp> DESC, id ASC` and the stable catalog by `title COLLATE NOCASE ASC,
id ASC`. Without an explicit tie-break the server order is unspecified and no
local projection could reproduce it. Recent and Recently added are **never**
recomputed from the stable catalog -- it carries neither ordering key.

All three ETags come from `etag_for_representation()`, which hashes the
serialized response. Two classes of defect had already shipped from
hand-maintained probes (a count a *move* leaves identical; `CURRENT_TIMESTAMP`
granularity hiding a same-second edit), and `file_size_bytes` is read from disk
at serialization time, so no SQL revision could ever see it change.

Dependency matrix (every entry is `YES` only after acknowledged canonical
success):

| Canonical change | works-browse | recent | recently-added |
| --- | --- | --- | --- |
| **Explicit open event** (`POST /api/works/:id/opened`) | — | YES | — |
| `GET /api/works/:id` (a pure read, incl. every internal refresh) | — | — | — |
| Work create | YES | — | YES |
| Work delete | YES | YES | YES |
| Work metadata (title / status / doc type / year / author) | YES | YES | YES |
| The ten detail-only synchronized fields (`abstract` reaches works-browse only) | — | — | — |
| Synchronized `year` / `published_date` | YES | YES | YES |
| Author **or Editor** role change | YES | YES | YES |
| Person canonical first/last-name change | YES | YES | YES |
| managed PDF save (`file_size_bytes`) | YES | YES | YES |
| Folder membership (single or bulk) | — | — | YES |
| bulk `set_status` | YES | YES | YES |
| Research Notes, Playlists, Concepts, Positions, Arguments, Work tags, Person Groups | — | — | — |

Folder membership reaches **only** Recently added, because it is the one
projection carrying `folder_id` (it filters locally over the folder title);
Progress and Types never render a folder. Work creation reaches the catalog and
Recently added but **not** Recent -- a new Work's `last_opened_at` is NULL.

**`GET /api/works/:id` is a pure read.** It used to stamp `last_opened_at`,
which made opening a Work a side effect of *any* detail read. Twelve
`fetchWorkDetails()` call sites are internal refreshes -- after a tag edit, a
folder move, a playlist change, a role edit, a metadata save, a notes save --
and every one of them silently reordered the server's Recent while the UI
correctly left `recent:index` eligible, because a tag edit genuinely has
nothing to do with Recent. That is both a cache-coherence bug (`recent:index`
stale with no invalidation) and a product bug (changing a tag made a file look
"recently opened").

Recording an open is the explicit semantic operation `MARK_WORK_OPENED`,
enqueued by `prksRecordWorkOpened()` from exactly one place: the `case 'work'`
route, which is the genuine foreground navigation. A render PRKS decided to do
is not an open -- `prksRenderTabRoute(..., { internalRefresh: true })` marks the
reconnect refresh, which must not reorder Recent behind the user's back.

The browser has exactly ONE open-event path, online and offline.
`POST /api/works/:id/opened` -> `db.mark_work_opened()` survives for other
canonical callers and shares the same max-register helper, but no frontend
module may call it: two client paths would mean online and offline opens
diverge. `tests/test_frontend_offline_runtime.py` asserts no module reaches
that endpoint and that only the Work route records an open.

Recording an open is best-effort in a way a Work-Tag edit deliberately is NOT.
A durable-write failure there must be reported, because the user made a change
and would otherwise believe it was saved. An open event is activity metadata
the user never asked for: losing it costs a Recent ordering, and refusing to
show the Work over it would cost them the thing they actually wanted.

Components must go through the semantic helpers
`prksMarkWorksBrowseChanged()`, `prksMarkRecentChanged()`,
`prksMarkRecentlyAddedChanged()` and the shared
`prksMarkWorkBrowseDisplayChanged()` -- never a direct
`deleteList('works-browse:index')`. That is enforced by
`tests/test_frontend_offline_runtime.py`. Recent is the one projection that now
also has the other half of that story: `prksEffectiveRecent()` applies pending
open events over the cached snapshot at render time, and an acknowledgement
reconciles the snapshot in place (`reconcileRecentOpen`) rather than dropping
it -- so an open no longer costs Recent its offline availability.

The Recently-added tab additionally records the coherence generation alongside
its in-memory copy, so an invalidation cannot leave that tab rendering
pre-mutation rows while the cache is already right.

### Synchronized operation families

PRKS has ONE semantic-operation protocol and two families on it. The split
between generic and domain is the load-bearing part: a third family must be a
registration, never another branch in a growing conditional.

| Layer | Owns |
| --- | --- |
| `backend/sync_protocol.py` | Envelope normalization, request hashing, the `sync_operations` ledger, OP_ID_REUSE, exact replay, `BEGIN IMMEDIATE`, dispatch |
| `backend/work_tag_sync.py` | Work-Tag relationship revisions, Tag lifecycle, tag-options, ADD/REMOVE handler |
| `backend/work_open_sync.py` | `last_opened_at` max-register, clock-skew policy, MARK_WORK_OPENED handler |
| `backend/work_metadata_sync.py` | Synchronized field registry, per-field revisions, SET_WORK_METADATA_FIELD handler |
| `frontend/js/sync-runtime.js` | Transport, claiming, backoff, Web Locks, replay, status transitions, retirement |
| `frontend/js/work-tag-state.js` | Work-Tag overlay + `prksWorkTagSyncHandler` |
| `frontend/js/work-open-state.js` | Recent overlay/merge + `prksWorkOpenSyncHandler` |
| `frontend/js/work-metadata-state.js` | Field overlay/diff + `prksWorkMetadataSyncHandler` |
| `frontend/js/work-metadata-editor.js` | Bibliographic group save + per-field conflict UI |
| `frontend/js/sync-diagnostics.js` | Settings -> Diagnostics for every family |

A handler decides what a server answer MEANS: `isResult` (is this a
well-formed answer for this operation), `reconcile` (apply an acknowledgement
to the disposable cache), `terminal` (`{conflict}` for an outcome the user
resolves, `{discard}` for one with no resolution worth offering). The
coordinator must name no family and no result code beyond the envelope-level
protocol errors; `tests/test_frontend_work_open_sync.py` pins that.

The families are deliberately different in kind, and that is the point:

| | Work Tags | Work opens | Work metadata fields |
| --- | --- | --- | --- |
| Conflict scope | one Work/Tag relationship | none | one FIELD of one Work |
| Concurrency | `base_revision` required | must be null | `base_revision` required |
| Two devices disagreeing | genuinely possible | impossible | possible, but only per field |
| Terminal outcomes | REVISION_CONFLICT / TAG_MERGED / TAG_DELETED / ENTITY_NOT_FOUND | ENTITY_NOT_FOUND only, consumed silently | REVISION_CONFLICT / FUTURE_REVISION / ENTITY_NOT_FOUND, all resolved by the user |
| Local failure to persist | must be reported | best-effort; never blocks opening the Work | must be reported |
| Convergence | last explicit resolution wins | max-register over `occurred_at` | last explicit resolution wins, per field |

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
  `works-browse:index` with `prksEffectiveBrowseRows()` before calling
  `renderProgressByStatus()`. Adding `status` to `FIELD_PROJECTIONS`,
  `PROJECTION_COLUMNS` and `SUMMARY_FIELDS` was the whole propagation change.
  **`progress.js` must never learn what a durable operation is** -- a second
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
  uses the same durable operation and therefore works offline, while every
  genuine Playlist mutation stays online-only.

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


