# Offline folder and tag coherence

This leaf contains the current Folders/Home, folder-tag, Work-tag coherence, transactional invalidation, and related cache rules. Read `offline-foundations.md` first.

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
straight to `PrksWorkCard`, `prksIsWorkCardRowShape()` validates that
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
`folder_id`. A Files for Processing import is the same canonical shape and owes
the same hooks (`prksMarkProcessingImportChanged()`, called by the Processing
records service after every import that was sent). Second, **Editor** counts alongside Author, because a Work card's
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

**Folder deletion and Work/Folder tag removal preserve Tag identity.**
`delete_empty_folder()` removes the Folder and lets its relationship rows
cascade without deleting Tag rows. `remove_tag_from_folder()` and
`remove_tag_from_work()` remove only their relationship rows. Ordinary
relationship edits must never garbage-collect an "unused" Tag: Tags are a
persistent reusable vocabulary, may still be referenced by Processing Files,
and a surprise delete can turn pending durable operations into
`ENTITY_NOT_FOUND` conflicts. Only explicit `delete_tag()` and
`merge_tags_into()` may destroy or transform Tag identity.
`tests/test_folder_atomicity.py` pins this by installing a trigger that forbids
Tag deletion and proving Folder deletion, Work/Folder relationship removal, and
bulk tag removal still succeed while the Tag row remains.
