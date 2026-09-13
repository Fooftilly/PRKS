# Work source / video identity — 2K design report

Written as design and audit for 2K, and **implemented in 2O** along the lines
it recommends: §9's Model B with Model C's boundary is what
`backend/work_source_sync.py` and `frontend/js/work-source-state.js` now
do. The audit sections below are preserved as the evidence for that choice and
still describe the data as it was measured. No rows were migrated and viewer
precedence is unchanged; §10 records what the implementation had to propagate.

The question this answers: *what is the canonical identity of a Work's source*,
and can its fields safely become independent synchronized scalars? The short
answer is **no** — and the evidence for that is precedence in code, not a
hypothetical.

---

## 1. Source-field inventory

All columns on `works`. "Canonical identity" means a reader uses it to decide
*what this Work is* rather than how to describe it.

| Field | Type | Null | User-editable today | Derived at ingest | Canonical identity | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `source_kind` | TEXT | yes | no (API only) | yes — from the create request | **yes** — first term of every inference | `pdf` \| `video`; nullable for old rows |
| `file_path` | TEXT | yes | no (API only) | yes — upload/import | **yes** for PDFs | `/api/pdfs/...` |
| `source_url` | TEXT | **yes, for non-video Works only** | partly | yes for video | **context-dependent** — see §7 | labelled "Original URL (optional)" |
| `provider` | TEXT | no | no | yes — host detection | **yes** for video | only `youtube` exists |
| `provider_id` | TEXT | no | no | yes — extracted from URL | **yes** for video, and it *outranks* the URL | never recomputed after creation |
| `thumb_url` | TEXT | no | no | yes — oEmbed | no — presentation | remote image |
| `source_mime` | TEXT | yes | no | optional hint | no | **0 rows populated** |
| `urldate` | TEXT | no | no | yes — creation date | no — bibliographic | BibLaTeX `@online` |
| `thumb_page` | INTEGER | yes | **yes, synchronized (2J)** | no | no — presentation | PDF only |

`PATCH /api/works/:id` accepts *every* one of these by name and validates
**none** of them. The editor exposes only `source_url`, and only for non-video
Works.

## 2. Reader / writer map

**Writers**

| Path | Fields | Validated | Derivation |
| --- | --- | --- | --- |
| Work create (`POST /api/works`) | all | **yes** — `_validate_youtube_url()` refuses a non-YouTube URL *before any mutation* | `provider` from host, `provider_id` from URL, `thumb_url`/`title`/`author_text` from oEmbed |
| `PATCH /api/works/:id` | all, independently | **no** | none |
| Processing import | `file_path`, `source_url` | n/a | creates a PDF Work |

There is **no repair or re-extraction logic anywhere**: `provider_id` is written
once at creation and never recomputed. Nothing today notices if it stops
matching `source_url`.

**Readers**

| Consumer | Uses |
| --- | --- |
| Viewer selection | `source_kind` → `file_path` → `source_url` |
| Video embed | `provider`, `provider_id`, `source_url` |
| Work card thumbnail | `file_path` → `thumb_page`, else `thumb_url` |
| Card source-kind class | `prksInferWorkSourceKind()` |
| `works-browse:index` | `source_kind`, `source_url`, `file_path`, `thumb_url`, `thumb_page` — **not** `provider`/`provider_id` |
| `recent:index`, `recently-added:index` | same as browse |
| Folder / Person / Playlist summaries | all source fields, including `provider`/`provider_id` |

**That asymmetry matters.** A browse row cannot reconstruct video identity the
way the viewer does, because it has no provider columns. Any future overlay that
put a new `source_url` into a browse row would change what
`prksInferWorkSourceKind()` returns *there* while the provider fields that
normally dominate are absent.

## 3. Viewer-selection precedence (traced, not inferred)

`prksInferWorkSourceKind(work)` — `frontend/js/api.js`:

```
1. source_kind == 'video'        -> video
2. source_kind == 'pdf'          -> pdf
3. file_path non-empty           -> pdf
4. source_url non-empty          -> video
5. otherwise                     -> source_kind verbatim (usually '')
```

Then `components/works.js`:

```
kind == 'pdf'    -> PDF pane if file_path, else "No PDF file attached."
kind == 'video'  -> renderVideoViewerPane(work)
otherwise        -> "No file attached."
```

And `prksYoutubeEmbedUrl(source_url, provider_id)`:

```
provider_id present  -> https://www.youtube.com/embed/<provider_id>   [STOPS HERE]
otherwise            -> parse source_url (youtube.com/watch?v=, youtu.be/, /embed/)
neither               -> "No embeddable video URL" + a plain link
```

**`provider_id` short-circuits the URL entirely.** This single line is why
`source_url` cannot be an independent synchronized scalar.

## 4. Video identity extraction

`_youtube_video_id()` accepts three URL forms and maps them to one id:

```
youtube.com/watch?v=ABC
youtu.be/ABC              ->  ABC
youtube.com/embed/ABC
```

So **the raw URL is not the identity** — many URLs denote one video. The id is
the identity; the URL is one spelling of it. Reconstruction is asymmetric:
`url -> provider_id` is reliable, `provider_id -> url` is a *choice* of form.

## 5. Legacy-data audit (36 Works, read-only)

| Measure | Count |
| --- | --- |
| `source_kind` = video / pdf / NULL | 19 / 17 / **0** |
| Video Works with `provider_id` | **19 of 19** |
| Video Works with `source_url` | 19 of 19 |
| Video Works also carrying `file_path` | 0 |
| `provider` set but `provider_id` blank (or reverse) | 0 |
| **URL-derived id agrees with stored `provider_id`** | **19 of 19** |
| URL hosts / forms | all `www.youtube.com`, all `/watch?v=` |
| `thumb_url` containing the `provider_id` | 18 of 19 |
| `source_mime` populated | **0** |
| **PDF Works carrying a `source_url` as well** | **3** |

Two conclusions. First, **there is no contradictory data today** — the hazard is
entirely prospective, and synchronizing `source_url` alone would be the first
mechanism capable of creating one. Second, the 3 PDFs with a `source_url`
confirm that "Original URL" is genuinely *provenance* on those Works, not
identity: their `source_kind` is explicitly `pdf`, so step 1 of the inference
settles the kind before the URL is ever consulted.

## 6. Transition matrix

Derived from the precedence above. "Viewer" is what the user would see
**if only the named field changed**.

| Transition | Allowed today | Fields that must change together | Viewer result if only `source_url` changes |
| --- | --- | --- | --- |
| no source → URL, kind NULL, no file | **no longer creatable** (see §10) | `source_kind`, `provider`, `provider_id` | becomes video (step 4); embed parses the URL — coherent, but identity-less |
| URL A → URL B (generic, no provider_id) | via PATCH | — | follows the URL — coherent |
| YouTube A → YouTube B | via PATCH | **`provider_id` too** | **stored URL says B, viewer plays A** ← the defect |
| YouTube → blank URL | via PATCH | `provider_id`, `provider`, `source_kind`, `thumb_url` | viewer *still plays A* from `provider_id`; card keeps A's thumbnail |
| Different spelling of the same video | via PATCH | — | no visible change; id identical — should be a **no-op**, but a per-field revision would record a change |
| PDF Work → video | via PATCH | `source_kind`, `file_path`, provider fields | explicit `source_kind='pdf'` wins; nothing changes |
| video → PDF | via PATCH | `source_kind`, `file_path`, provider fields | explicit `source_kind='video'` wins; nothing changes |
| Add provenance URL to a PDF | **yes, in the UI** | — | nothing changes — the intended case, 3 rows today |
| `provider_id` present, URL cleared | via PATCH | — | viewer keeps playing; only provenance is lost |

Rows 3, 4 and 5 are the argument: one user intention ("this Work is now video
B") requires **three columns** to move together, and one *non*-intention (a
different spelling) must move none.

## 7. What does editing `source_url` mean?

**Context-dependent, and the current UI already encodes which context it means.**

- On a **video** Work it is the user-facing spelling of identity — and the
  editor does **not** expose it (`bibFields` is empty when `isVideo`).
- On a **non-video** Work it is provenance: "where this PDF came from". The
  editor exposes it precisely here, labelled "Original URL (optional)".

So the existing product already has two meanings and correctly offers only the
safe one. A synchronization design must not flatten them back together.

## 8. Identity vs derived vs presentation

| Class | Fields | Rationale |
| --- | --- | --- |
| **Canonical identity** | `source_kind`, `provider`, `provider_id`, `file_path` | every viewer/thumbnail decision reads these |
| **User intent** | the URL the user pastes; the kind they choose | what a conflict is actually *about* |
| **Derived** | `provider`, `provider_id`, `thumb_url`, `urldate` | computed from intent at ingest; never user-typed |
| **Provenance** | `source_url` on a non-video Work | affects nothing that renders |
| **Presentation** | `thumb_url`, `thumb_page` | replaceable without changing what the Work *is* |

`provider` and `provider_id` appear as both identity and derived — that is the
point. They are **canonical but not user-owned**: authoritative for readers,
yet never independently decided by a person. Derived values must not create
their own conflicts; nobody can meaningfully answer "keep server's
`provider_id` or yours?" when neither was typed.

## 9. Recommendation — **Model B, with Model C's boundary**

**One aggregate operation for video identity; `source_url` stays an ordinary
field only where it provably cannot affect identity.**

```
SET_WORK_SOURCE        scope: work-source / <work_id>      one revision
```

Rationale, in order of weight:

1. **`provider_id` short-circuits `source_url`.** Independent fields make
   "stored URL says B, viewer plays A" reachable with two ordinary edits.
2. **One intention, three columns.** A revision must record a change of state;
   three revisions for one decision would demand three resolutions for one
   disagreement.
3. **Derived fields must not conflict independently** (§8).
4. **Identity is the id, not the URL** (§4) — so a spelling change must be a
   no-op, which only a canonicalizing aggregate can decide.
5. **Creation already has the canonical builder** (`_validate_youtube_url` +
   `_youtube_video_id` + oEmbed). The synchronized path must converge on it
   rather than become a second source parser — the mistake `thumb_page`'s
   pre-normalization made and 2J removed.

### Payload — user intent, not columns

```json
{ "source": { "kind": "video", "url": "https://www.youtube.com/watch?v=ABC" } }
```

`provider`, `provider_id` and `thumb_url` are **absent by design**: they are
recomputed inside the canonical mutation boundary. Putting them in the payload
would let a client assert an identity the URL contradicts, which is the defect
restated in a new place. Clearing is `{"source": {"kind": "video", "url": ""}}`
or an explicit null form — to be pinned when the operation is specified.

### Conflict semantics

One unit, one question: *which source should this Work have?* — presented as
the two URLs, resolved with the existing **Use server** / **Apply my value**.
Never three simultaneous conflicts for one decision. Two devices that pasted
different spellings of the same video have **converged**, exactly as `"003"`
and `"3"` converge for `thumb_page`.

### Optimistic and offline behaviour

The 2J separation applies directly: **saved local intent is not resource
availability.**

- **Effective source** = acknowledged Work + pending aggregate, with
  `provider_id` derived client-side by the same extraction rule. Viewer and
  card read the effective value; the acknowledged cache is never mutated.
- **Online, pending:** the embed URL is fully determined by
  `provider_id`, which the client can derive itself, so **the new video can
  play before ACK** — the analogue of `?page=N`. No server state is consulted.
- **Offline:** the edit saves durably, but a YouTube embed needs the network.
  The viewer must report the ordinary offline-unavailable state rather than a
  broken frame — and, as with thumbnails, **a pending source must never cause a
  cached card to request bytes it cannot obtain.**
- **`thumb_url` is presentation and is oEmbed-derived**, so a pending identity
  change should leave the card's image *stale but intact* until ACK, rather
  than blanking it on speculation. It should never be part of the operation.

### Where `source_url` stays an ordinary field

On a Work whose `source_kind` is explicitly non-video, `source_url` is
provenance and cannot reach any identity decision (step 1 of the inference
settles the kind first). It may therefore remain a normal synchronized scalar
**guarded by the operation's validator, not by the UI** — the current control
is already correctly scoped, but a guard that lives only in a form is not a
contract.

### Proposed invariants (to add later, not now)

```
source_kind = 'video'  ->  provider and provider_id both present
provider = 'youtube'   ->  provider_id extractable and equal to id(source_url) when source_url is present
source_kind = 'pdf'    ->  identity is file_path; source_url is provenance only
```

All three hold across all 36 current rows, so no migration is implied. They are
proposals; **no constraint was added.**

## 10. Cache propagation plan (for the implementing milestone)

| Representation | Carries | Needs the overlay |
| --- | --- | --- |
| Work entity | all source fields | yes |
| `works-browse` / `recent` / `recently-added` | `source_kind`, `source_url`, `file_path`, `thumb_url` — **no provider columns** | yes, and see the §2 warning |
| Folder / Person / Playlist summaries | all, including provider columns | yes |
| Work cards | inferred kind + thumbnail | derived, after the overlay |
| Viewer | provider/provider_id/source_url | derived, after the overlay |
| Command palette, search cards | title/credit only | no |

The browse projections would need `provider`/`provider_id` added, or the
overlay must write a *derived* `source_kind` — the same
"convert-at-the-boundary" decision `thumb_page` faced, and a reason to prefer
extending the projection over teaching cards to re-derive.

### Canonical new rows vs legacy inferred-video rows

Two distinct things, and the difference matters when reading the tables above.

**Canonical new rows.** Everything `add_work()` creates stores an explicit,
lower-case `source_kind` (`pdf` or `video`) and, for a video, the full derived
identity. That includes an *inferred* PDF: a file-backed Work created without a
declared kind stores `pdf`, so the rule is literal rather than "unless the
caller omitted it". The one row that stores nothing is the one with no source
at all — no declared kind, no file, no URL — where inventing a kind would be a
claim about a file it does not have. `PDF`/`Pdf`/`VIDEO` are normalized at the boundary rather than left
for every consumer to lower-case — a reader that forgets sees a kind matching
nothing. An unknown kind is refused.

**Legacy inferred-video rows.** Older databases may hold `source_kind` NULL, no
file and a valid video URL. The product reads that as a video (§3, step 4), so
the source APIs must too — reading the *column* made this module disagree with
the rest of the product about the same row: shown as a Video, offered the Video
source editor, then refused as `UNSUPPORTED_SOURCE_TRANSITION`.

`current_source()` therefore classifies with `effective_source_kind()` and, when
no identity is stored at all, derives it from the URL the row already carries.
That overrides nothing — it is the same answer every other reader reaches.

Three things it deliberately does **not** do:

- **Guess.** A video-classified row whose URL today's parser refuses has no
  identity, and both the state endpoint and the mutation answer
  `INVALID_SOURCE_STATE` rather than inventing one.
- **Resolve a contradiction.** If an identity *is* stored but is not one this
  module can state — a malformed or unreportably large `provider_id` — that is
  `INVALID_SOURCE_STATE` too. Preferring the URL there would change which video
  plays, because `provider_id` outranks the URL in the viewer (§3).
- **Migrate.** The data audit found no such rows, so there is no startup
  migration and no bulk rewrite. A legacy row is upgraded to the canonical
  shape by its first successful `SET_WORK_SOURCE`. A convergent write — naming
  the video the row already has — fills in the classification columns from that
  row's own URL and advances **no revision**: nothing any device believes about
  which video this is has changed.

### Creation classifies by the same rule the runtime reads by

`source_kind` was never required for a Work to BE a video: §3's precedence
infers one, and "no file, has a URL" is step 4. Creation originally canonicalized
only what the caller *declared*, so a Work created with a URL and no kind was
stored with `source_kind` NULL and no provider identity — and then displayed as
a Video, offered the Video source editor, and refused by `SET_WORK_SOURCE` as
`UNSUPPORTED_SOURCE_TRANSITION`. The UI and the mutation boundary disagreed
about the same row.

`PRKSDatabase.add_work()` now decides with `effective_source_kind()`, which
mirrors `prksInferWorkSourceKind()` (a test pins the two against the shipped
function text). Whenever the effective kind is video, the canonical parser runs
and `source_kind = "video"` is **persisted** rather than left NULL — deriving the
id alone would leave the row un-actionable by the aggregate, which is the whole
problem.

`source_kind` is also a domain now, not free text: `pdf` or `video`. Anything
else — `web`, say — fell through step 4 of the inference into the video viewer
while the creation boundary treated it as not-a-video.

An inferred **PDF** is unaffected: a file makes it a PDF, and its `source_url`
stays provenance even when YouTube-shaped. Treating that as identity would turn
a paper into a video because of where it was downloaded from.

The row shape in §6's first transition — kind NULL, no file, a URL — therefore
remains possible as **legacy data**, but is no longer a state new creation can
produce.

### Only a video's render may wait on the overlay

The viewer must never be handed a URL naming one video and an id naming
another, so the Work detail resolves the effective source before it renders —
and that read is asynchronous, because pending intent lives in IndexedDB.

That wait is scoped to Works that are ALREADY videos. The aggregate refuses
every other transition (`UNSUPPORTED_SOURCE_TRANSITION`) and no control offers
one, so a PDF cannot have a pending source and has nothing to wait for. Making
every Work detail block on a durable-store read would mean a slow or stuck
IndexedDB leaves the user looking at an empty panel — for a value that Work
could not have had. The acknowledged `source_kind` is what decides, and it is
authoritative for this question precisely because the kind itself is not
editable.

## 11. Migration and compatibility

No migration is required for the recommended model: every current row already
satisfies the proposed invariants. `source_mime` is unpopulated and could be
excluded from the aggregate entirely. `PATCH` must eventually stop accepting
these columns individually, or route them through the same canonical builder —
otherwise the aggregate becomes a second mutation path, which is the split
contract every milestone since 2D has removed.

## 12. Recommended next milestone

**2L — `doc_type`**, before source identity.

`doc_type` is well understood: Types membership is the mechanism 2H already
proved for Progress, and the Research Graph is the only genuinely new surface.
Source identity is now *designed* but it is the largest single step the
protocol has taken — a new operation family, a new revision scope, a derived
aggregate and viewer-level optimistic rendering. Doing `doc_type` next keeps
the graph-propagation question separate from the aggregate-operation question,
so that when `SET_WORK_SOURCE` is implemented it is the only new thing in it.
