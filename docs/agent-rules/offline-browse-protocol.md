# Offline browse catalogs and synchronization protocol

This leaf contains current browse/recent projection rules plus the shared synchronized-operation family protocol and handler semantics. Read `offline-foundations.md` first.

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
