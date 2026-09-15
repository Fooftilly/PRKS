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
| Tag vocabulary | `CREATE_TAG`, `DELETE_TAG` | client-minted `T-` id; deletion is a tombstone |
| Work opens | `MARK_WORK_OPENED` | coalesced per Work |
| Work metadata | `SET_WORK_METADATA_FIELD` | per-field conflict unit |
| Work source | `SET_WORK_SOURCE` | aggregate: provider + id + url are one decision |
| Work-Person roles | `ADD_WORK_PERSON_ROLE`, `REMOVE_WORK_PERSON_ROLE`, `SET_WORK_PERSON_ROLE_CREDIT` | element conflict unit `(work, person, role)` |
| Person creation | `CREATE_PERSON` | client-minted `P-` id |
| Person profile | `SET_PERSON_METADATA_FIELD` | per-field; acknowledged/effective/draft kept apart |
| Person Groups | `CREATE_PERSON_GROUP`, `SET_PERSON_GROUP_FIELD`, `ADD_PERSON_GROUP_MEMBER`, `REMOVE_PERSON_GROUP_MEMBER`, `DELETE_PERSON_GROUP` | four shapes over one entity; deletion is a tombstone |
| Person deletion | `DELETE_PERSON` | tombstone; a Person credited on a file stays protected |

## Still connection-required

Everything below still calls a canonical endpoint and is disabled or refused
while PRKS is unreachable. The list is the honest scope of "read-only offline"
as it stands — not a claim that each is impossible.

### Deliberately server-bound, and expected to stay so

* **Backup and restore**, **performance diagnostics**, **PDF reindex and
  linearization**, **Processing Files scanning and import**, and server settings
  that only make sense as server state. These are host operations, not content.
* **New PDF binary ingestion.** Creating a PDF Work requires transferring the
  binary. Doing that durably needs a complete design for Blob storage separate
  from the disposable cache, quota failure, browser-restart recovery, ownership
  and lifetime, large-file behaviour, acknowledgement, cleanup after ACK or
  discard, and duplicate/retry semantics. Until that design exists, this is an
  intentional binary boundary rather than an oversight.

### Not yet durable, no known blocker

Each of these is a normal family that has simply not been built yet. The shapes
they should take, and what each must declare before implementation, are in
*Adding a family: the four shapes and what each must declare* in
`docs/local-first-sync.md`.

* **Tag merge** — `MERGE_TAG` is an identity transformation rather than a field
  change and needs care: a pending merge must not let new relationship intents
  target a doomed source identity, and must not rewrite an already-sent
  envelope. The rule this rollout would use is to refuse the merge while any
  unsynchronized operation still names the source, rather than retargeting
  intents whose base revision belongs to a scope that is about to change.
* **Folders** — create, field edits, move, delete, and the Work-side
  `SET_WORK_FOLDER`. Moving a folder is structural; cycle prevention stays
  canonical.
* **Playlists** — create, field edits, delete, item add/remove, and reordering.
  Ordering is an aggregate: an ordered list must not be modelled as independently
  racing `order_index` fields.
* **Concepts** — create, field edits, aliases, parents, delete. Aliases and
  parents are aggregates; note resolution depends on the complete vocabulary.
* **Positions** — create, edit, delete. Lightweight scalar claim records.
* **Arguments and stances** — create, field edits, `SET_ARGUMENT_SOURCES`,
  `SET_ARGUMENT_TARGETS`, delete. Sources and targets are coherent replacements
  on the server today and should stay one unit each.
* **Research notes and private notes** — one note body is one conflict unit,
  with revision-based optimistic concurrency. The ACK must keep using the
  canonical note-save boundary so Concept auto-creation and reference indexing
  behave exactly as they do online; the browser must not re-implement research
  markup parsing to decide canonical results.
* **PDF annotations** — only where the PDF is already cached. Annotation
  identity must be audited first: if annotations receive server-generated ids
  today, new ones need permanent distributed ids before offline creation is
  possible.
* **Work deletion and the remaining Work relationships**, then **offline Work
  creation** for types needing no binary ingestion.
* **Saved Views**, and a clearly-labelled cached-data search mode. Global search
  over uncached server records is not offered and should not be implied.

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
