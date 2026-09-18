# Local-first rollout: what is durable, and what still needs a server

A running status of the "one path online and offline" work. `docs/local-first-sync.md`
is the design; this is the score.

A family counts as **durable** only when it is written to `prks-local-v1` before
success is shown, survives a reload, composes with other pending state, has
explicit conflict and dependency semantics, reconciles every affected
projection, and is proven by focused E2E coverage.

## Durable today

| Domain | Operations | Notes |
| --- | --- | --- |
| Work tags | `ADD_WORK_TAG`, `REMOVE_WORK_TAG` | element conflict unit `(work, tag)` |
| Tag vocabulary | `CREATE_TAG`, `DELETE_TAG`, `MERGE_TAG` | client-minted `T-` id; deletion is a tombstone; merge is an identity transform (null base; refuse while source is named in the queue) |
| Work creation (video) | `CREATE_WORK` | client-minted `W-` id; YouTube-only; PDF binary stays online-only |
| Work deletion | `DELETE_WORK` | destruction; cascade like the ordinary DELETE; absence is convergence |
| Work opens | `MARK_WORK_OPENED` | coalesced per Work |
| Work metadata | `SET_WORK_METADATA_FIELD` | per-field conflict unit |
| Work source | `SET_WORK_SOURCE` | aggregate: provider + id + url are one decision |
| Work-Person roles | `ADD_WORK_PERSON_ROLE`, `REMOVE_WORK_PERSON_ROLE`, `SET_WORK_PERSON_ROLE_CREDIT` | element conflict unit `(work, person, role)` |
| Person creation | `CREATE_PERSON` | client-minted `P-` id |
| Person profile | `SET_PERSON_METADATA_FIELD` | per-field; acknowledged/effective/draft kept apart |
| Person Groups | `CREATE_PERSON_GROUP`, `SET_PERSON_GROUP_FIELD`, `ADD_PERSON_GROUP_MEMBER`, `REMOVE_PERSON_GROUP_MEMBER`, `DELETE_PERSON_GROUP` | four shapes over one entity; deletion is a tombstone |
| Person deletion | `DELETE_PERSON` | tombstone; a Person credited on a file stays protected |
| Folders | `CREATE_FOLDER`, `SET_FOLDER_FIELD`, `DELETE_FOLDER`, `SET_WORK_FOLDER`, `ADD_FOLDER_TAG`, `REMOVE_FOLDER_TAG` | moving is a field; a Work's folder is a scalar on the Work; folder tags mirror Work tags `(folder, tag)` |
| Positions | `CREATE_POSITION`, `SET_POSITION_FIELD`, `DELETE_POSITION` | two INDEPENDENT fields; deletion refused while an Argument targets it |
| Arguments / Stances | `CREATE_ARGUMENT`, `SET_ARGUMENT_FIELD`, `SET_ARGUMENT_SOURCES`, `SET_ARGUMENT_TARGETS`, `DELETE_ARGUMENT` | permanent `A-` id; construction atomically includes initial sources/targets; later sources and targets are separate ordered aggregates |
| Concepts | `CREATE_CONCEPT`, `SET_CONCEPT_FIELD`, `SET_CONCEPT_IDENTITY`, `SET_CONCEPT_PARENTS`, `DELETE_CONCEPT` | name+aliases are one aggregate; the parent set is another |
| Playlists | `CREATE_PLAYLIST`, `SET_PLAYLIST_FIELD`, `REORDER_PLAYLIST_ITEMS`, `DELETE_PLAYLIST`, `SET_WORK_PLAYLIST` | the order is one aggregate; a Work's playlist is a scalar on the Work; detail page offers Delete playlist |
| Work notes | `SET_WORK_RESEARCH_NOTE`, `SET_WORK_PRIVATE_NOTE` | independent whole-document aggregates; Research ACK fences Concept/Argument/Graph when the body changed or the revision advanced past the observed base; Private ACK does not |
| PDF annotations | `CREATE_PDF_ANNOTATION`, `SET_PDF_ANNOTATION`, `DELETE_PDF_ANNOTATION` | per-annotation aggregate on already-available PDFs; PDF bytes never in the durable queue; offline edits require cached `prks-pdf-v1` bytes + acknowledged annotation base + durable store; materialization may lag (`ANNOTATION_MATERIALIZATION_STALE`) |

## What one overnight pass added

Person profile editing completed (acknowledged/effective/draft kept apart, and
only what the session changed is sent), then Person Groups, Person deletion, the
Tag vocabulary, Folders and Playlists. Six families' worth of surfaces moved off
the network, and the gate stayed green between each.

Playlists were the first family whose central decision is an AGGREGATE. An order
is not a collection of racing positions: two devices that each dragged one video
produced two whole orders, and merging them index by index would arrive at a
third that neither of them chose. One revision covers the structure, a second
drag on the same device replaces the first, and a second device's drag conflicts
so the user decides.

Four product defects surfaced along the way and were fixed where they lived,
not worked around:

* a Person-profile save measured its edit against the record on SCREEN, which
  is already overlaid, so editing a field back to the server's value left a
  pending operation asking for a value nobody changed;
* a Tag-creation refusal (`NAME_TAKEN`) was not recognized as a terminal answer
  and would have retried forever;
* a pending group rename never reached the group chips on a Person's page;
* a folder bookkeeping refresh ran on the Work route's paint path, and its late
  completion could replace the panel from a background tab.

## Still connection-required

Everything below still calls a canonical endpoint and is disabled or refused
while PRKS is unreachable. The list is the honest scope of what still needs
the server — not a claim that the rest of PRKS is read-only offline.

### Deliberately server-bound, and expected to stay so

* **Backup and restore**, **performance diagnostics**, **PDF reindex and
  linearization**, **Processing Files scanning and import**, and server settings
  that only make sense as server state. These are host operations, not content.
* **New PDF binary ingestion.** Creating a PDF Work requires transferring the
  binary. Doing that durably needs a complete design for Blob storage separate
  from the disposable cache, quota failure, browser-restart recovery, ownership
  and lifetime, large-file behaviour, acknowledgement, cleanup after ACK or
  discard, and duplicate/retry semantics. Until that design exists, this is an
  intentional binary boundary rather than an oversight. Annotation *metadata*
  on a PDF that is already on this device is durable (see table above); only
  bringing a new PDF binary into the library stays connection-required.
* **Saved Views and global Search.** Live execute is `/api/search` (FTS, tags,
  PDF text index). Offline routes refuse explicitly; CRUD is guarded. Do not
  approximate search over browse cards.
* **Tag aliases.** Add/remove alternate Tag names stays server-backed; guarded
  offline. Identity transform for names is `MERGE_TAG`.
* **Publishers vocabulary.** `#/publishers` create/delete/aliases are
  connection-required; the route refuses offline.
* **Bulk Organize.** `POST /api/works/bulk` (status / folder / tags) stays
  server-backed and guarded; per-Work durable paths cover the same decisions
  one file at a time.

### Not yet durable, no known blocker

_(none — remaining connection-required surfaces above are classified.)_

### Implemented, but with no control in the app

_(none — `DELETE_PLAYLIST` is offered from Playlist detail via Delete playlist.)_

### Deliberately not built, because it would be new product semantics

* **Renaming a Tag, and editing a Tag's colour.** PRKS has neither today:
  `POST /api/tags` either creates a Tag or hands back the existing one, every
  caller sends the same default colour, and no UI offers either action. Adding
  a rename means deciding what happens to the old name — does it become an
  alias, as a merge would make it, or simply disappear? — and that is a product
  decision rather than a synchronization one. `CREATE_TAG` and `DELETE_TAG` are
  the vocabulary's whole surface until that decision is made.

## Standing invariants the rollout must not trade away

1. Durable before success is shown. Save creates the intent and updates
   effective local state; the network is subsequent work.
2. The acknowledged cache never holds an optimistic value.
3. One semantic user decision is one operation and one conflict unit.
4. Construction is not mutation: a created entity has not "changed".
5. An envelope that may have been sent is never rewritten.
6. Unknown durable state is never "empty"; a cache miss is not valid empty data.
7. Reconciliation patches what it holds the value for and invalidates only what
   it cannot patch.
8. Every refusal is named per family. "Needs a decision" with no reason is a
   stranded user.
