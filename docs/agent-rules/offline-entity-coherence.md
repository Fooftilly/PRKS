# Offline entity coherence

This leaf contains the current cross-entity/cache-coherence rules for People, Person Groups, Playlists, Concepts, Positions, Arguments/Stances, PDF annotations, and adjacent entity projections. Read `offline-foundations.md` first.

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
