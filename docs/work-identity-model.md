# Work identity, editions, versions and Assets: design (#60)

**Status: design proposal for review. Nothing here is implemented.** This
document does not add schema, migrations, API behavior or UI. It is the design
gate that #60 requires before large schema changes, and it follows the
architecture direction in #179. Once approved, the implementation slices in §17
become separate focused issues and PRs.

It follows the shape of [work-source-identity.md](work-source-identity.md):
audit the code first, then recommend. Every claim about current behavior
below was checked against `master` at `0e19b86` (schema version 16), not taken
from issue text.

Contents:

1. [Current-state audit](#1-current-state-audit)
2. [Problems with the overloaded Work row](#2-problems-with-the-overloaded-work-row)
3. [Terminology and proposed entities](#3-terminology-and-proposed-entities)
4. [Relationships and cardinality](#4-relationships-and-cardinality)
5. [Ownership matrix](#5-ownership-matrix)
6. [Annotations and notes](#6-annotations-and-notes)
7. [People and roles](#7-people-and-roles)
8. [Citations](#8-citations)
9. [Asset lifecycle and storage](#9-asset-lifecycle-and-storage)
10. [Identity and deduplication](#10-identity-and-deduplication)
11. [Stable IDs](#11-stable-ids)
12. [Migration and backfill](#12-migration-and-backfill)
13. [Compatibility and API projection](#13-compatibility-and-api-projection)
14. [Backup/restore and offline/durable operations](#14-backuprestore-and-offlinedurable-operations)
15. [Downstream roadmap implications](#15-downstream-roadmap-implications)
16. [Rejected alternatives and tradeoffs](#16-rejected-alternatives-and-tradeoffs)
17. [Proposed implementation slices](#17-proposed-implementation-slices)
18. [Open questions for the maintainer](#18-open-questions-for-the-maintainer)

---

## 1. Current-state audit

### 1.1 The `works` row

One `works` row (`backend/db_schema.sql`) currently holds every concept below.
The table groups its 34 columns by what each one actually describes, which is
not the same as how the schema groups them.

| What it describes | Columns |
| --- | --- |
| Identity | `id` (`W-` + 8 or 32 hex, see `backend/entity_ids.py`) |
| Display title | `title` (NOT NULL; also FTS-indexed) |
| User workflow | `status` (Progress groups), `last_opened_at` (Recent) |
| Bibliographic record | `doc_type`, `year`, `published_date`, `publisher`, `location`, `edition`, `journal`, `volume`, `issue`, `pages`, `isbn`, `doi`, `urldate`, `abstract` |
| Free-text credit | `author_text` (search, cards, video channel; loses to linked Author roles) |
| Source identity | `source_kind`, `file_path`, `source_url`, `provider`, `provider_id`, `source_mime` |
| Presentation | `thumb_url`, `thumb_page`, `hide_pdf_link_annotations` |
| Research content | `text_content` (the Research Note; the schema comment says "Markdown notes or OCR text", but it is only the note) |
| Private content | `private_notes` |
| Annotation materialization | `canonical_annotation_set_revision`, `materialized_pdf_annotation_revision` |
| Bookkeeping | `created_at`, `updated_at` |

`works_fts` (FTS5, external content, kept current by triggers `works_ai`,
`works_ad`, `works_au`) indexes `title`, `abstract`, `text_content` and
`author_text`.

### 1.2 Everything that holds a Work ID

A redesign has to account for every one of these. The list is the reason §11
keeps Work IDs unchanged.

| Holder | Where | Kind of reference |
| --- | --- | --- |
| `roles.work_id` | main DB, FK CASCADE | Person↔Work credit |
| `annotations.work_id` | main DB, FK CASCADE | PDF annotation owner |
| `work_tags.work_id` | main DB, FK CASCADE | Tag attachment |
| `folder_files.work_id` | main DB, FK CASCADE | Folder membership (one folder per Work, `work-folder` scalar) |
| `playlist_items.work_id` | main DB, FK CASCADE, UNIQUE | One playlist per Work |
| `argument_sources.work_id` (+ `pages`) | main DB, FK CASCADE | Research Argument cites a Work, optionally with a page pinpoint |
| `processing_files.imported_work_id` | main DB, FK SET NULL | Import provenance |
| `sync_entity_revisions.scope_id` | main DB | `work-field/[work, field]`, `work-source/work`, `work-tag`, `work-folder`, `work-playlist`, `work-person-role`, `work-research-note`, `work-private-note`, `pdf-annotation/[work, annotation]` |
| `sync_operations.entity_id` | main DB | Durable-operation ledger (`entity_type` `work`) |
| `concept_mentions`, `argument_mentions`, `work_note_state` | `prks_research_index.db` (derived) | Research Note mentions |
| `work_text_index` | `prks_text_index.db` (derived) | PDF text, keyed by `work_id` + `source_ref_hash` of `file_path` |
| `thumbs/` filenames | derived | `prks_thumb_cache_stem(work_id, page)` |
| Research Note text | canonical text | `[[W-…]]` or `[[Title]]` wiki links (`resolve_wiki_links`) |
| URLs / workspace tabs | browser | `#/works/W-…`, persisted workspace layout |
| `prks.pdf.lastPage.<workId>` | browser `localStorage` | Last-read PDF page, per device |
| `prks-local-v1` (IndexedDB) | browser, durable | Pending operations carrying `work_id` and base revisions |
| Offline cache entities | browser, disposable | `work:*`, `work-annotations`, `works-browse:index`, `recent:index`, `recently-added:index`, summaries |
| Backups | archive | The main DB carries all of the above |

These do **not** hold a Work ID: the managed PDF file name
(`<unix>_<8hex>_<name>.pdf`, from `mint_managed_pdf_filename`),
`pending_pdf_cleanup` (keyed by basename), and the service-worker PDF cache
`prks-pdf-v1` (keyed by `/api/pdfs/<basename>`).

### 1.3 How the model is used, by subsystem

**Schema and migrations.** `backend/db_migrations.py` runs ordered migrations
up to `LATEST_SCHEMA_VERSION = 16`, validates the final shape
(`validate_current_schema`, `_assert_known_table_shapes`), and never touches
the filesystem. v15 added the two materialization revisions to `works`. v16
added `pending_pdf_cleanup`.

**`backend/db_manager.py`.** `add_work()` writes one row with every column and
canonicalizes the source through `_canonical_new_source` and
`effective_source_kind`. `get_work()` is a pure read (the stamp on
`last_opened_at` was moved into `mark_work_opened`); it attaches roles, tags,
annotations, and the one playlist and one folder. `update_work_metadata()` is
the PATCH path. `generate_bibtex()` builds a single entry from the row plus
roles. The summary projections (`_PRKS_WORK_SUMMARY_COLUMNS`,
`_prks_work_browse_select`, `finish_work_summary_rows`) read `works` columns
directly and `stat()` the managed PDF to produce `file_size_bytes`.

**`*_sync.py`.** Each durable family declares a conflict unit keyed by
`work_id`:

- `work_metadata_sync`: one revision **per field** (`work-field/[work, field]`),
  covering `SYNCED_FIELDS`: status, title, source_url (provenance only),
  doc_type, thumb_page, author_text, abstract, year, published_date, edition,
  journal, volume, issue, pages, isbn, doi, publisher, location.
- `work_source_sync`: `SET_WORK_SOURCE` is **one aggregate** for video identity
  (`source_kind` + `provider` + `provider_id` + `source_url`), because
  `provider_id` outranks the URL in the viewer.
- `work_role_sync`: `work-person-role/[work, person, role]`; the role types are
  `Author, Editor, Reviewer, Mentioned, Translator, Introduction, Foreword,
  Afterword`.
- `work_note_sync`: Research Note (`text_content`) and Private Note
  (`private_notes`) are separate whole-document aggregates.
- `work_tag_sync`, `folder_sync` (`SET_WORK_FOLDER`), `playlist_sync`
  (`SET_WORK_PLAYLIST`), `work_open_sync` (a max-register on
  `last_opened_at`), `work_lifecycle_sync` (`CREATE_WORK` for video only, and
  `DELETE_WORK`).
- `pdf_annotation_sync`: one revisioned aggregate per annotation, scope
  `pdf-annotation/[work, annotation]`.

**HTTP.** `backend/server.py` handles `/api/works` (create, with upload or
adoption of an existing `/api/pdfs/<name>`), `/api/works/:id` (GET/PATCH/
DELETE), `/api/works/:id/pdf` (replace bytes, used by annotation
materialization), `/annotations*`, `*-state` endpoints for each sync family,
`/opened`, `/thumbnail`, `/api/bibtex/:id`, `/api/pdfs/:name`,
`/api/processing-files/*`, `/api/works/bulk`, and the backup endpoints. Only
Positions has typed DTOs so far (`backend/api_contract/`).

**Processing Files / import.** `processing_files` is a staging copy of most
`works` bibliographic columns plus draft roles and tags. `import_processing_file`
checks the `%PDF-` header, streams the file into managed storage, linearizes
it, creates one Work with `add_work()`, and records `imported_work_id`. It
does **no duplicate check**.

**Managed PDFs.** Bytes live in `pdfs/` under a minted, unique basename.
`works.file_path = "/api/pdfs/<basename>"` is the only link from the DB to the
bytes. Several Works may point at the same basename (creation can adopt an
existing managed name), which is why `work_pdf_replace` does
**copy-on-write** before it overwrites shared bytes. Deletion commits the row
and writes a `pending_pdf_cleanup` claim in the same transaction. Every write
follows `backend/fs_durability.py`.

**Annotation bytes vs metadata.** The `annotations` table is canonical. The
managed PDF bytes are a **materialized** copy that embeds the same markup:
`POST /api/works/:id/pdf` **overwrites the managed file in place** with the
annotated export. `canonical_annotation_set_revision` > `materialized_…`
means the bytes are stale. Linearization also rewrites bytes in place. So
**PRKS does not keep the original bytes it ingested**, and the managed file's
hash changes every time annotations are saved.

**Work source.** See [work-source-identity.md](work-source-identity.md). The
proposed invariants there (`video` ⇒ provider identity; `pdf` ⇒ identity is
`file_path`, and `source_url` is only provenance) hold across current data.

**Bibliographic metadata.** It is flat on `works`. `author_text` is a fallback
credit. Publisher normalization (`publishers`, `publisher_aliases`) resolves
spellings for search only. There is no `language`, `subtitle`, arXiv ID,
generic identifier table, or provenance for any value.

**People, roles and credit names.** `roles(person_id, work_id, role_type,
order_index, credit_name)`, with UNIQUE `(person_id, work_id, role_type)`.
`credit_name` is how that person is credited **on this Work**. `Mentioned`
roles are also written automatically by `_sync_mentioned_roles()` from
`[[Name]]` markup found **inside PDF bytes** on replace, so they come from an
asset.

**Tags and folders.** Both attach to the Work. A Work is in at most one folder.
Folder and Tag vocabulary are separate entities.

**Research Notes.** `works.text_content`. Concept and Argument mentions are
indexed per Work in the derived research index. Notes can contain
`[[pdf:<annotationId>]]` markers (`prksReplacePdfAnnotationWikiMarkers`) that
jump to a PDF annotation, and `[[W-…]]`/`[[Title]]` Work links.

**Private Notes.** `works.private_notes`. Reminders are capped at 8,000
characters in the UI and 64 KiB on the wire. They are not indexed.

**Reading and open state.** `status` is the user's reading progress (Progress
page). `last_opened_at` is a server-side max-register (Recent). The last-read
PDF page lives only in the browser (`localStorage prks.pdf.lastPage.<workId>`,
behind the "remember last page" setting).

**Playlists.** These are ordered Works, mainly videos. The UNIQUE index on
`playlist_items(work_id)` limits each Work to one playlist.

**Citation and export.** `generate_bibtex()` is the only exporter. The cite
key is **computed, never stored**: first Author family name + year. Authors,
editors, translators, and introduction/foreword/afterword come from roles. The
entry type is `doc_type`, or, when that is empty, guessed from journal or
publisher. `url` is `source_url`. `urldate` is taken from **`updated_at`**, not
from the `urldate` column. A server-wide profile selects which fields are
exported.

**Duplicates and source identity.** Nothing detects duplicates today. Video
identity is canonicalized, so two spellings of one YouTube ID are recognized
as the same **within one Work**, but not across Works.

**Offline and durable operation.** Durable intent in `prks-local-v1` is kept
apart from the disposable acknowledged cache. Server revisions are
per-scope. PDF binary ingestion and replacement require a connection.
Annotation metadata can be edited offline when the PDF bytes are cached
(`docs/local-first-sync.md`, "PDF annotations (V2 local-first boundary)").

**Backup and restore.** A backup is the main DB plus `files/pdfs/*` plus
person images, with a manifest (`FORMAT_VERSION = 1`, `db_schema_version`).
Restore refuses a backup with a newer schema. `audit_managed_pdfs` counts
referenced-but-missing PDFs by scanning `works.file_path`. Thumbnails and both
indexes are derived and are rebuilt, not backed up.

**Frontend.** `prksInferWorkSourceKind` picks the viewer. Work cards, browse
lists, Folder/Person/Playlist summaries, and the metadata/role/source/notes/
tag editors all consume the flat Work shape. Every `work-*-state.js` module
mirrors one backend sync family.

### 1.4 Current invariants to preserve

These hold today. The replacement design must keep each one or say explicitly
why it changes.

1. **I1: Work IDs are immutable.** They are never rewritten. Legacy 8-hex IDs
   remain valid forever.
2. **I2:** `get_work()` is a pure read. Opening a Work is an explicit
   max-register operation.
3. **I3: Source identity is an aggregate.** For video, `provider_id` outranks
   `source_url`. Identity is never split across independent field scopes.
4. **I4: The PDF text index is derived.** Any change to a Work's managed PDF
   identity resynchronizes it or relies on reconciliation.
5. **I5: Managed bytes are never lost to a crash.** A basename is minted
   unique, published durably, never trusted as a path
   (`safe_pdf_path_under_dir`), copy-on-written when shared, and claimed in
   `pending_pdf_cleanup` inside the delete transaction.
6. **I6: Annotation metadata is canonical.** The bytes are a materialization.
   Semantic ACK never waits on bytes. Canonical > materialized means "rebuild",
   never a user-facing binary conflict.
7. **I7: Conflict units are deliberately chosen.** Metadata conflicts per field,
   notes per whole document, roles per (person, role), annotations per
   annotation. Merging them into a coarser unit is a regression.
8. **I8: One folder and one playlist per Work.**
9. **I9: One role row per (person, work, role_type).**
10. **I10: Migrations touch SQLite only.** Managed files are never modified by a
    migration.
11. **I11: Backups are self-contained.** Every referenced managed file travels
    with the DB that references it.
12. **I12: Durable intent is not acknowledged state.** An offline edit never
    mutates the acknowledged cache before ACK.
13. **I13: PDF ingestion is online-only.**

---

## 2. Problems with the overloaded Work row

1. **One row means four different things**: the intellectual work ("Plato's
   *Republic*"), one citeable publication ("Grube/Reeve, Hackett 1992"), one
   file ("this scan"), and the user's relationship to it (status, notes,
   tags). Adding a second translation today means either creating an
   unrelated Work, which scatters notes, tags and research links, or
   overwriting the first translation's citation metadata.
2. **`title` means two things.** It is both the name the user files the work
   under and the citation title. A Serbian edition's title "Država" cannot
   coexist with "Republic".
3. **`source_url` means three things**: video identity (§3 of the source
   report), PDF download provenance, and the BibTeX `url`. The current UI
   already exposes only the safe meaning.
4. **`file_path` means three things**: storage locator, file identity, and
   "this Work is a PDF". It is the only record of which bytes belong to the
   Work, which is why deletion needs `pending_pdf_cleanup`.
5. **Annotation state is keyed by Work but depends on one file's pagination.**
   `annotations.page_index`, `geometry_json` and the two materialization
   revisions are only meaningful for one specific PDF. A second PDF on the
   same Work would make every coordinate ambiguous.
6. **The managed file is both the original and the annotated export.**
   Materialization and linearization overwrite it in place. PRKS cannot say
   what it originally ingested, and a content hash of the current file is not
   a stable duplicate key.
7. **Roles mix intellectual and edition credits.** Author (the work) and
   Translator/Editor/Foreword (one edition) sit in one table, and
   `credit_name` ("Platon" vs "Plato") is really a per-edition fact.
8. **`argument_sources.pages` is a pinpoint with no edition.** "Republic,
   p. 45" means nothing once two paginations exist.
9. **`thumb_page`, `hide_pdf_link_annotations` and the browser's last-page key**
   describe one file and are stored against the Work.
10. **`updated_at` is also the BibTeX `urldate`**, so any tag or status edit
    changes a citation's access date.
11. **`year` and `published_date` overlap.** BibTeX takes `year`, then falls
    back to a prefix of `published_date`.
12. **`edition` is free text** ("2nd rev. ed."), not version identity.
13. **Nothing detects duplicates.** Import creates a new Work even when the
    same bytes already exist.

---

## 3. Terminology and proposed entities

Three core entities, plus a few relationship entities. The internal names are
precise. The UI names are chosen so that users with one paper and one PDF never
see the internal ones (§13.4).

### 3.1 Work (logical Work): internal `work`, UI "Work"

This is the intellectual identity the user reasons about: what they file,
tag, annotate in notes, argue with, and read about. It is the **existing
`works` entity with its existing ID** (§11). A Work always has at least one
Manifestation, and exactly one of them is **primary**.

### 3.2 Manifestation: internal `manifestation`, UI "Version"

This is **one citeable form of a Work**: a specific edition, revision,
translation, or publication form with its own bibliographic record.
Examples: arXiv v1; arXiv v2; the accepted manuscript; the journal version;
the 1968 Bloom translation; the Serbian translation; the Greek text in the
OCT; a web page as captured; a YouTube video. It owns citation metadata and
edition-scoped credits.

A Manifestation has a `kind`, which is a descriptive classification and not
a separate entity type:

```
kind ∈ { unspecified, preprint, accepted_manuscript, published, edition,
         translation, reprint, web_page, video, other }
```

Two orthogonal fields cover translation identity: `language` (BCP 47) and the
relations in §3.4. `kind` never decides behavior. It labels the version in the
UI and gives the citation layer a hint.

**Why not "Edition" or "Version" internally?** "Edition" is wrong for arXiv
revisions and for translations. "Version" is the right *user* word but is
ambiguous in code, where it collides with schema version, revision and asset
replacement. "Manifestation" is the library-science term for "a specific
embodiment someone can cite", and it is used here **without** adopting the
rest of FRBR/LRM. See §16 for the rejected Expression layer.

### 3.3 Asset: internal `asset`, UI "File"

This is **one concrete content object that belongs to one Manifestation**:
a managed PDF, a web snapshot, a future attachment, or an external
provider-hosted stream (a YouTube video). It owns the storage locator, media
type, byte size, content hashes, provenance of the bytes, and every piece of
state defined by that content's own coordinates: PDF annotations, page
positions and page thumbnails.

`asset.kind ∈ { managed_file, external_stream, external_link }`:

- `managed_file`: bytes stored by PRKS under managed storage (PDFs today,
  web snapshots and attachments later).
- `external_stream`: identified by a provider and a provider ID (YouTube).
  This is where today's `SET_WORK_SOURCE` aggregate moves.
- `external_link`: a URL PRKS does not store (for example "the publisher's
  page"). This is optional and may never be needed. §18 Q8 asks.

### 3.4 Relationship and supporting entities

| Entity | Purpose |
| --- | --- |
| `manifestation_relations(from_id, to_id, relation)` | Typed links between Manifestations of the **same** Work: `revision_of`, `published_version_of`, `new_edition_of`, `translation_of`, `reprint_of`. These carry "arXiv v2 revises v1", "journal version of the accepted manuscript", and "Serbian translation of the Greek text". They carry no page correspondence (#61 owns assisted correspondence). |
| `manifestation_identifiers(manifestation_id, scheme, value, normalized)` | DOI, ISBN, arXiv ID (versioned and versionless), PMID, and similar. `works.doi`/`isbn` backfill into these later. This is the entity duplicate detection keys on. |
| `roles` gaining a nullable `manifestation_id` | Edition-scoped credit (§7). |
| `argument_sources` gaining a nullable `manifestation_id`, with row identity `(argument_id, work_id, manifestation_id)` | A pinpoint citation is pinned to a pagination, and one Argument can cite several Versions of one Work (§8.5). |
| `work_lifecycle(work_id, state, target_work_id)` | Merge record, modeled like `sync_tag_lifecycle`: it redirects **reads** and refuses **writes** with `WORK_MERGED` (§10.4, §14.2). |
| `duplicate_decisions` | Durable record of "not a duplicate" answers, so a declined suggestion does not return (§10.5). |
| Metadata provenance | A per-field record of detected, retrieved or user-edited origin on Manifestation fields (#42). This design only reserves the ownership: provenance belongs to the Manifestation field it describes, never to the Work. |

---

## 4. Relationships and cardinality

```
                          ┌─────────────────────────────┐
  tags, folder, playlist ─┤            Work             ├─ Research Note, Private Note
  status, last_opened     │  W-…  (existing ID, kept)   │  Research links (Concepts,
  Work-level roles        │  canonical title, abstract  │  Arguments via mentions)
  (Author, Mentioned)     └──────┬───────────────▲──────┘
                                 │ 1..*          │ primary_manifestation_id (exactly 1)
                                 ▼               │
                  ┌──────────────────────────────┴──┐     manifestation_relations
 edition roles ───┤          Manifestation          ├──── (revision_of, translation_of,
 (Translator,     │  MF-…  kind, language, citation │      published_version_of, …)
 Editor, Foreword)│  metadata, identifiers          │
                  └──────┬───────────────▲──────────┘     argument_sources.pages
                         │ 0..*          │ primary_asset_id (0 or 1)   pinned here
                         ▼               │
                  ┌──────────────────────┴──────────┐
 annotations ─────┤             Asset               ├──── managed bytes /pdfs/<basename>
 page position    │  AS-…  kind, locator, media     │     or provider identity
 page thumbnails  │  type, size, hashes, state      │     derived: text index, thumbs
 materialization  └─────────────────────────────────┘
```

| Relationship | Cardinality | Notes |
| --- | --- | --- |
| Work → Manifestation | 1 → 1..* | Never zero. A notes-only or metadata-only Work still has one empty primary Manifestation, so projections and citations always resolve. |
| Work → primary Manifestation | exactly 1 | `works.primary_manifestation_id`, NOT NULL after backfill, and must belong to that Work. |
| Manifestation → Asset | 1 → 0..* | Zero means "metadata, no file". |
| Manifestation → primary Asset | 0 or 1 | `manifestations.primary_asset_id`. It is required to be non-NULL when the Manifestation has any active Asset. |
| Asset → Manifestation | exactly 1 | An Asset is never shared by two Manifestations. Shared **bytes** are a storage fact handled below the Asset (§9.3). |
| Annotation → Asset | exactly 1 | §6. |
| Manifestation relations | same-Work only | Cross-Work links are research relationships, not version relationships. |

### Worked cases

| Case | Shape |
| --- | --- |
| Ordinary single-PDF paper | 1 Work → 1 Manifestation → 1 Asset. This is every existing PDF Work after backfill. |
| arXiv v1 → v2 | 1 Work → MF(v1, preprint) + MF(v2, preprint, `revision_of` v1). Each has its own Asset. Annotations stay with their own PDF. |
| Preprint → accepted manuscript → journal | 1 Work → 3 MFs chained by `published_version_of`. The journal MF is usually primary and the citation target. |
| First → revised edition | 1 Work → 2 MFs (`new_edition_of`), each with its own ISBN, date and pages. |
| Original → translations | 1 Work → MF(grc, published) + MF(en, translation, `translation_of` grc) + MF(sr, translation, `translation_of` grc). The Translator role is on each translation MF. |
| Two English translations | Two `translation` MFs with `language = en`, each with its own translator. |
| Several scans of one exact edition | 1 MF → several Assets. One of them is primary. |
| Publisher PDF + locally annotated copy | 1 MF → Asset(publisher original) + Asset(annotated export, `derived_from_asset_id` = original). Today these are the same file (§9.4). |
| Future Web Work | 1 Work → MF(web_page, url, site, accessed date) → Asset(sanitized HTML snapshot) and optionally a readable-text Asset. A re-capture is a new Asset in the same MF. |
| Metadata but no file | 1 Work → 1 MF → 0 Assets. |
| Video | 1 Work → MF(video) → Asset(external_stream: youtube, provider_id). |
| Exact duplicate file | Two Assets with the same `ingest_sha256`. This is detected (§10.2) and never merged silently. |
| Probable bibliographic duplicate | Two Works whose MFs share a normalized DOI/ISBN or have similar title/author/year. They become a **candidate**, and only the user decides (§10.3). |
| Genuinely separate works | Two Works. A declined candidate is recorded in `duplicate_decisions`. |

---

## 5. Ownership matrix

Legend: **W** = Work, **M** = Manifestation, **A** = Asset, **J** =
relationship/join entity, **D** = dedicated domain object. "Backfill"
describes where today's value goes; §12 covers how.

| Concept (current column/table) | Owner | Backfill of current value | Rationale / tradeoff |
| --- | --- | --- | --- |
| Canonical title (`title`) | **W** | stays on `works.title` | The name the user files the work under. |
| Publication title | **M** (`manifestations.title`, NULL = inherit Work title) | NULL (inherit) | For one version, editing the title changes both, as today. A translation sets its own. Inheritance avoids copying every title. |
| Subtitle | **M** (new `subtitle`) | NULL | Differs by edition. There is no current column. |
| Document type (`doc_type`) | **M** | copied | This is a BibTeX/CSL entry type: a preprint is `misc`/`online`, a journal version is `article`. The Types page derives from the primary M. **Tradeoff:** the Types page may later want a Work-level "kind of intellectual work" (monograph, paper, lecture). It is not needed now. |
| Year / date (`year`, `published_date`) | **M** | copied | Publication date of this form. An optional Work-level `original_date` (for example "c. 375 BC") is a future addition. |
| Edition statement (`edition`) | **M** | copied | Free text on the version it describes. |
| Language | **M** (new `language`) | NULL | Translation identity is the M's language plus a `translation_of` relation. A Work-level `original_language` is optional and future. |
| Translation identity | **J** (`manifestation_relations.translation_of`) + M.`language` + M-scoped Translator role | none | §3.4. There is no Expression entity (§16). |
| Publisher, location (`publisher`, `location`) | **M** | copied | Per edition. |
| Journal, volume, issue, pages (`journal`, `volume`, `issue`, `pages`) | **M** | copied | Per publication form. |
| DOI (`doi`) | **M** (+ `manifestation_identifiers`) | copied | DOIs identify a publication form. A preprint and the journal version have different DOIs. |
| ISBN (`isbn`) | **M** (+ identifiers) | copied | Per edition and binding. |
| URL for citation (`source_url` on a non-video Work) | **M** (`url`) | copied for non-video Works | This is the BibTeX `url` today. **Tradeoff:** it is also "where I downloaded the PDF from" (Asset provenance). Keeping one value on M preserves the current single field. Asset `origin_url` is populated going forward by capture/import, not by the backfill. |
| URL as video identity (`source_url` + `provider*` on a video Work) | **A** (external_stream aggregate) | moved into the Asset | Keeps I3: `SET_WORK_SOURCE` becomes an Asset-scoped aggregate. The M's citation `url` is derived from it for `@online`. |
| Access date (`urldate`) | **M** | copied | This is a citation fact about the version. The current BibTeX use of `updated_at` is a quirk the projection preserves until #41 decides (§8.6). |
| Abstract (`abstract`) | **W**, with optional **M** override (NULL = inherit) | stays on `works.abstract` | **Genuinely ambiguous.** Usually one summary of the intellectual work, but preprint and published abstracts can differ, and translations are in another language. Keeping it on W avoids an FTS rewrite (`works_fts` indexes it). The override covers the cases where it differs. |
| Free-text author (`author_text`) | **W** | stays | Search/card/channel fallback for the work's creators. Edition credits come from roles. |
| People / roles (`roles`) | **J**, scoped to W (NULL `manifestation_id`) or M | by role type, §7.3 | §7. |
| Credit names (`roles.credit_name`) | **J** row. For a W-level role, M can override. | stays on the row | "Platon" on the Serbian edition and "Plato" on the English one. §7.2. |
| Tags (`work_tags`) | **W** | unchanged | Topical classification of the intellectual work. **Tradeoff:** a user tag like "needs-OCR" is really about a file. That is acceptable. File-level status belongs to Asset state, not to Tags. |
| Folders (`folder_files`) | **W** | unchanged | One folder per Work (I8). |
| Playlists (`playlist_items`) | **W** | unchanged | Ordered viewing sequence. One per Work (I8). |
| Research Notes (`text_content`) | **W** | unchanged | §6.4. |
| Private Notes (`private_notes`) | **W** | unchanged | §6.5, with an open question. |
| Annotations / comments (`annotations`) | **A** | `annotations.asset_id` = the Work's backfilled Asset | §6.1. Coordinates belong to one file. |
| PDF page references in notes (`[[pdf:<annotationId>]]`) | resolve through the annotation's **A** | nothing to move | Annotation IDs are globally unique, so existing markers keep resolving. The viewer opens the owning Asset. |
| Pinpoint pages (`argument_sources.pages`) | **J**, pinned to **M** | `manifestation_id` set to the default M when `pages` is non-empty | §8.5. |
| Reading progress (`status`) | **W** | unchanged | "Have I read this work?" **Tradeoff:** someone who has read translation A but not the original may want per-version status. Deferred, §18 Q6. |
| Last-read page (`localStorage prks.pdf.lastPage.<workId>`) | **A** (per device) | client falls back from the Asset key to the legacy Work key for the default Asset | Page N means nothing in another file. #61 needs an independent location per version. |
| Open/recent (`last_opened_at`) | **W** (max-register) | unchanged | Recent lists Works. An Asset-level `last_opened_at` may be added so "open" reopens the last-used version (§18 Q6). |
| Thumbnail page (`thumb_page`) | **A** | copied to the default Asset | It is a page of that file. The Work card uses the primary Asset. |
| Remote thumbnail (`thumb_url`) | **A** | copied to the default Asset | Presentation of the video asset. |
| Hide link annotations (`hide_pdf_link_annotations`) | **W** (viewer preference) | unchanged | A display preference for the sidebar list that applies to whichever PDF is shown. Low stakes. |
| Citation / cite key | **M** (a stored `cite_key` is optional future) | none (it is computed today) | §8. |
| Source / provenance | **A** for bytes (`origin`, `origin_url`, `imported_from_processing_file_id`). **M** field provenance for metadata. | `origin = 'legacy'` | Bytes and metadata have different provenance. |
| File path (`file_path`) | **A** (`storage_locator`) | copied | The Work projection keeps `file_path` (§13). |
| MIME / media type (`source_mime`) | **A** (`media_type`) | Copy `source_mime` when it is non-empty. Otherwise infer `application/pdf` for managed PDFs, and leave it NULL for everything else. | `POST /api/works` accepts and stores a caller-supplied `source_mime`, so a user database may hold values even though the audited library had none. The backfill must never overwrite them. |
| Byte size | **A** (`byte_size`) | filled by the fingerprint pass (§12.4) | It replaces the per-row `stat()` in `finish_work_summary_rows` once trusted. |
| Content hash | **A** (`ingest_sha256`, `content_sha256`) | NULL until the fingerprint pass | §9.4 and §10.2. |
| Derived text / OCR / thumbnails | **A**, derived | the rebuilt index is keyed by `asset_id` | They stay derived and are never backed up (I4). |
| Annotation materialization revisions | **A** | copied from `works` | They describe one file's bytes. |
| Source kind (`source_kind`) | **A** (`kind` + `media_type`). The Work projection derives it. | derived | `prksInferWorkSourceKind` keeps working on the projection. |
| `created_at` / `updated_at` | each entity has its own | copied | `works.updated_at` stays the Work's. |
| Revision state (`sync_entity_revisions`) | the entity that owns the conflict unit | §14.2 | Scopes move with ownership, and revision counters are carried over. |
| Mentioned roles auto-extracted from PDF bytes | **J** at W scope, with provenance from **A** | unchanged | §7.4. |

---

## 6. Annotations and notes

### 6.1 Annotations belong to an Asset

Every PDF annotation has `page_index` and geometry measured in one file's
coordinate space. Those only make sense against the exact bytes they were drawn
on (strictly, against the original bytes plus the materializations PRKS
itself produced). They therefore attach to the **Asset**, never to the
Manifestation or the Work:

- `annotations.asset_id` NOT NULL (after backfill), FK to `assets`.
- `annotations.work_id` is kept during the transition as a denormalized value
  equal to the Asset's Work, so existing queries and the
  `pdf-annotation/[work, annotation]` scope keep working (§14.2).
- `canonical_annotation_set_revision` and `materialized_pdf_annotation_revision`
  move to the Asset.

The viewer shows the annotations of **the Asset it is displaying, and only
those**. When #61 puts two versions side by side, each pane shows its own set.

### 6.2 What happens to annotations in each scenario

The rule: **page-specific state never moves to a different pagination
implicitly.** Carrying annotations over is allowed only when PRKS can prove the
coordinate space is identical. Anything else is an explicit, reviewed action.

| Scenario | What happens | Why |
| --- | --- | --- |
| Replace the PDF with an **exact byte duplicate** of its original | No new Asset. The upload is recognized as the same content (§10.2) and nothing changes. | The coordinate space is identical. There is nothing to migrate. |
| Replace with a **newer revision** (arXiv v2) | A **new Manifestation** (`revision_of`) with a new Asset. The old Asset and all its annotations remain, still readable. v2 may become primary. | The pagination changed. Moving coordinates would silently point highlights at the wrong text. |
| Preprint → publisher version | The same: a new M (`published_version_of`) and a new A. The preprint annotations stay on the preprint. | The same reason. The PDFs are completely different. |
| **Another scan** of the same edition | A new Asset in the **same** Manifestation. Annotations stay on the scan they were drawn on. | Same edition, but the scans differ in page offsets, cropping and rotation. |
| **Merging** duplicate Works | Assets move **whole** with their Manifestation, and their annotations go with them unchanged. | An annotation's Asset never changes, so its coordinates stay valid. |
| Merging two Assets that are **exact duplicates** (same `ingest_sha256`) | The user chooses: keep one Asset and **union** the annotation sets (the coordinates are provably identical), or keep both. | Content identity proves the coordinates are compatible. It is still never automatic. |
| Replacing a file **in place** (a "correct scan" in the same M) | A new Asset that `supersedes` the old one. The old Asset's annotations stay on it, and the UI offers "copy annotations to the new file" only as an assisted action (below). | |

**Assisted annotation transfer (future, #61):** a copy is created with
text-anchor matching (for example a quoted passage plus prefix/suffix
captured from the source text layer) and a review list of matched, uncertain and unmatched items.
It is never a move, so the originals remain on their Asset. It is never offered
for translations.

### 6.3 Materialized bytes and "the annotated copy"

Today the managed file **is** the annotated export (§1.3). The design keeps
that behavior for the default Asset: materialization still rewrites that
Asset's bytes (copy-on-write when shared). §9.4 explains why `ingest_sha256`
exists, and §18 Q3 asks whether PRKS should start keeping the pristine
original as a separate Asset. Either way, the annotated export and the
original are different **bytes** of one Asset lineage, and their annotations
are the same canonical metadata rows.

### 6.4 Research Notes belong to the Work

The Research Note is the user's thinking about the intellectual work. Concept
and Argument mentions, the Research Graph and backlinks all reason about Works,
and the note already survives any change of file. It stays on `works`
(`text_content`, scope `work-research-note/<work>`) with **no change**.

Version-specific passages inside a note are expressed as references that carry
their own target:

- `[[pdf:<annotationId>]]` already resolves through the annotation to its
  Asset. With several Assets, the link opens that Asset. It never opens "the
  current file".
- Any future page-pinpoint syntax (for example `[[page:MF-…#p45]]`) **must name
  a Manifestation or Asset**. A bare page number inside a Work-level note is
  ambiguous and must not be introduced.

Per-version notes ("this translation mistranslates §4") are not supported by
this design. §18 Q5 asks whether they are needed. If they are, the answer is a
separate, small Manifestation-level note field, not a change of scope for the
Research Note.

### 6.5 Private Notes belong to the Work (with a stated tradeoff)

Private Notes are reminders ("chapter 3 is relevant to X", "ask Y about
this"). Most of them concern the work, so they stay on the Work, unchanged.
**Tradeoff:** some reminders are file facts ("this scan is missing pp. 40–45").
The right home for those is Asset state (a short Asset `note` or condition
flag), not moving Private Notes. The notes deliberately do not share a scope:
Research and Private Notes stay separate conflict units on the Work, as they
are today.

---

## 7. People and roles

### 7.1 Scope by role type

A role describes either **who made the intellectual work** or **who made
this edition**. The default scope for each current role type:

| Role type | Default scope | Reasoning |
| --- | --- | --- |
| Author | **Work** | The creator of the intellectual work, the same for every translation. |
| Editor | **Manifestation** | Usually edition-specific: a critical edition's editor, or a volume editor of one printing. **Ambiguous for anthologies**, where the editor is the creator of the compiled work. The user can move it to Work scope. |
| Translator | **Manifestation** | Identifies one translation. |
| Introduction / Foreword / Afterword | **Manifestation** | They belong to one edition's apparatus. |
| Reviewer | **Manifestation** | A review, or a peer review, addresses a specific publication form. **Ambiguous in PRKS today.** The role's current product meaning is not documented, so §18 Q7 asks. |
| Mentioned | **Work** | "Person discussed in this work" is a research relationship. See §7.4 about its asset-derived origin. |

These are **defaults applied at backfill and at role creation**. Scope is
stored per row, so a user can override a default.

### 7.2 Storage

- `roles` gains a nullable `manifestation_id` (FK to `manifestations`, CASCADE).
  NULL means Work scope. Keeping one table, rather than adding
  `manifestation_roles`, means every current `roles` reader (People pages,
  credit helper, Research Graph, BibTeX) keeps working with a
  filter instead of a UNION.
- The uniqueness (I9) becomes
  `UNIQUE(person_id, work_id, role_type, COALESCE(manifestation_id, ''))`, so
  one person can translate two different translations of one Work.
- **Credit-name override:** the credit on a Work-scoped Author row is the
  default. A Manifestation may override how that person is credited on it
  through `manifestation_credit_overrides(manifestation_id, person_id,
  role_type, credit_name)`. This is optional and ships only when a UI needs it.
  Backfill writes none.

### 7.3 Backfill rule

Every existing Work has exactly one Manifestation, so scope is invisible today.
The backfill therefore sets `manifestation_id` on the edition-scoped role types
(Editor, Translator, Introduction, Foreword, Afterword, Reviewer) and leaves
Author and Mentioned at Work scope. **Nothing visible changes.** The legacy
projection (§13) includes Work-scoped roles plus the primary Manifestation's
roles, which is exactly today's set. The first time a user adds a second
Manifestation, Translator and Editor correctly do **not** appear on it.

### 7.4 Mentioned roles from PDF bytes

`_sync_mentioned_roles` adds `Mentioned` roles from `[[Name]]` markup found in
a PDF when its bytes are replaced. In the new model the extraction runs per
Asset and records the Asset as provenance. The resulting role stays at Work
scope, and it is **never removed automatically** because another Asset lacks
the markup. This matches today's add-only behavior.

### 7.5 What a Manifestation's full credit list is

```
credits(M) = Work-scoped roles of M.work  (with M's credit overrides applied)
           ∪ roles where manifestation_id = M
```

This is the single definition the citation projection (§8), the Work detail
people panel, and future CSL output all use.

---

## 8. Citations

Coordinated with #41. This design fixes **what a citation points to**. #41
owns formatting, CSL and Citation.js.

### 8.1 Ownership

All citation metadata is owned by the **Manifestation**: entry type, title and
subtitle (inherited from the Work when NULL), date, edition, publisher,
location, journal, volume, issue, pages, identifiers, URL and access date,
language, and edition-scoped credits. The Work supplies only what the
Manifestation inherits (title, abstract) and its Work-scoped creators.

### 8.2 What a citation points to

**A citation targets a Manifestation**, never the abstract Work. A pinpoint
("p. 45") is a citation target plus a locator, and it is only meaningful
together with that Manifestation's pagination.

### 8.3 Preferred Manifestation

`works.primary_manifestation_id` is the version shown in lists, opened by
default, and **cited by default**. The single pointer is deliberately simple:

- Copy citation / BibTeX / CSL on a Work cites its primary Manifestation.
- An explicit "cite this version" on any Manifestation cites that one.
- Batch export resolves **each Work to exactly one Manifestation** (the primary,
  unless the caller names another) and never flattens several bibliographic
  records into one entry.
- If users need a separate citation default (read the translation but cite the
  original), a nullable `works.citation_manifestation_id` (NULL = primary) can
  be added without migration pain. §18 Q2 asks whether it is needed now.

### 8.4 Coexisting citeable Manifestations

Each Manifestation produces its own complete entry. Two translations produce
two entries with different titles, translators, publishers and ISBNs. Cite
keys must be unique across Manifestations. The current computed key
(`<FirstAuthor><year>`) already collides across Works and would collide across
versions. #41 decides between a stored `cite_key` per Manifestation (stable for
LaTeX users) and a deterministic key with disambiguation. This design reserves
a nullable `manifestations.cite_key` and nothing else.

### 8.5 Research citations inside PRKS

`argument_sources(argument_id, work_id, pages)` is PRKS's own citation. It
gains a nullable `manifestation_id`:

- `pages` empty → the Argument cites the **Work**. `manifestation_id` stays
  NULL and renders against the primary.
- `pages` non-empty → the pinpoint was written against one pagination, so the
  backfill **pins** it to the default Manifestation, and new pinpoints are
  pinned to the Manifestation the user is looking at. Changing the primary
  Manifestation later never moves an existing pinpoint.

**Row identity must change with it.** Today the primary key is
`(argument_id, work_id)`, so a nullable column alone would still stop one
Argument from citing two Manifestations of the same Work (for example, a
passage in the Greek text and the same passage in a translation). It would also
make `MERGE_WORKS` collide when an Argument already cites both the source and
the target Work. The citation identity becomes
`(argument_id, work_id, COALESCE(manifestation_id, ''))`, enforced by a unique
index. The table is rebuilt in the same migration that adds the column (SQLite
cannot alter a primary key). The `argument-sources` durable scope is the whole
source list of one Argument, so it is unaffected. On merge, if re-pointing
would produce two rows with the same identity, the preview shows both and the
user keeps one or both pinpoints. Neither is dropped silently.

### 8.6 BibTeX compatibility projection

During the migration `/api/bibtex/:id` keeps its URL, which takes a Work ID,
and its output. The generator is split into:

```
citation_record(manifestation_id) -> plain dict (entry type, fields, credits)
bibtex_from_record(record, export_profile) -> str
/api/bibtex/<work_id> = bibtex_from_record(citation_record(primary(work_id)))
```

The record builder is where #41's CSL-JSON adapter will attach. A
**byte-identical output test** over a fixture library proves parity before the
old generator is deleted. The current `urldate`-from-`updated_at` behavior is
preserved **deliberately** in the projection, documented as a known quirk, and
left for #41 to change with its own review, because changing citation output is
not a #60 concern.

---

## 9. Asset lifecycle and storage

### 9.1 Asset record

| Field | Meaning |
| --- | --- |
| `id` | `AS-` + 32 hex (§11). Stable for the Asset's life. It survives merges and moves. |
| `manifestation_id` | Owner (FK). Moving an Asset to another Manifestation is an explicit operation. |
| `kind` | `managed_file` \| `external_stream` \| `external_link`. |
| `role` | `original` \| `annotated_export` \| `snapshot` \| `readable_text` \| `attachment` \| `other`. It describes what the content is, not how it is stored. |
| `storage_locator` | For `managed_file`: the managed **basename** under its storage area (`pdfs/` today). **Never an absolute path**, the same reasoning as `pending_pdf_cleanup`. The legacy `/api/pdfs/<basename>` form stays in the Work projection. |
| `provider`, `provider_id`, `url` | For `external_stream` / `external_link`. The `SET_WORK_SOURCE` aggregate moves here unchanged (I3). |
| `media_type` | `application/pdf`, `text/html`, `video/*` hint, … |
| `byte_size` | Size of the current bytes. Filled at write time, or by the fingerprint pass. |
| `ingest_sha256` | SHA-256 of the bytes **as first ingested**, before linearization or materialization. Immutable once set. NULL for legacy Assets (§9.4). |
| `content_sha256` | SHA-256 of the **current** bytes. Updated whenever PRKS rewrites them. Serves integrity checks and client cache validation. |
| `origin` | `upload` \| `processing_import` \| `adopted` \| `web_capture` \| `materialized` \| `legacy`. |
| `origin_url`, `origin_ref` | Where the bytes came from (a download URL, or a Processing File ID). This is provenance, not a citation. |
| `derived_from_asset_id` | Lineage, for example an annotated export of a publisher PDF. |
| `supersedes_asset_id` | "This file replaces that one" within one Manifestation. |
| `state` | `active` \| `trashed` (reserved for #57). Deletion removes the row (§9.2). |
| `thumb_page`, `thumb_url` | Presentation (from `works`). |
| `canonical_annotation_set_revision`, `materialized_annotation_revision` | From `works` (§6.1). |
| `captured_at` | For snapshots (Web Works). |
| `created_at`, `updated_at` | |

**PDF bytes never go into SQLite.** Web snapshots are also stored as managed
files, not as DB blobs.

### 9.2 Lifecycle

```
            ingest (upload / import / capture / adopt)
                        │  bytes durable first (fs_durability), then row commits
                        ▼
                 ┌─────────────┐  materialize / linearize: content_sha256 changes,
                 │   active    │  ingest_sha256 does not
                 └──┬───────┬──┘
     detach/trash   │       │ delete (Asset, or cascade from M/Work)
     (#57, future)  ▼       ▼
              ┌─────────┐   row deleted + pending_pdf_cleanup claim in the SAME txn
              │ trashed │   (only if no other Asset/row references the basename)
              └────┬────┘         │
      restore ◄────┘ purge ───────┤
                                  ▼
                        bytes removed post-commit, retried by the existing
                        bounded retry pass (unchanged rules, AGENTS.md)
```

- **Availability is observed, not canonical.** "Missing file" means the
  locator resolves to nothing on disk. It is computed by the same checks that
  produce `file_size_bytes` today, may be cached as derived state, and **never
  deletes or rewrites an Asset row**. A missing Asset still owns its
  annotations. Backup reports it (as `managed_pdfs_missing` does now), and
  restoring the bytes heals it.
- **Deletion keeps the cleanup model.** The reference check behind
  `pending_pdf_cleanup` (three-valued, re-asked under the basename lock, and
  settled in one transaction with the check) is extended to count
  `assets.storage_locator` references in addition to `works.file_path` while
  both exist. The claim still stores a basename.
- **Replacement.** "Replace file" in the UI creates a new Asset. The old one
  becomes non-primary and remains, or is trashed if the user chooses. Only
  materialization and linearization rewrite bytes in place, and both are PRKS
  operations on the same content.

### 9.3 Shared bytes

Today, creation may adopt an existing managed basename, so two Works can share
bytes, and `work_pdf_replace` does copy-on-write. In the new model this is
**two Assets with the same `storage_locator`**. Copy-on-write stays exactly
where it is: before rewriting a locator that another Asset references, the
writing Asset is retargeted to a fresh minted name. Shared locators are
allowed but never created by new features. New ingestion always mints a
name, and duplicates are detected by hash instead (§10.2).

### 9.4 Why two hashes

PRKS rewrites managed PDFs in place when it linearizes them and when it
materializes annotations (§1.3). A single "content hash" would therefore
change every time the user saves a highlight, making it useless as an
exact-duplicate key. So:

- `ingest_sha256` is the identity of what was brought in. It is computed on
  the incoming stream **before** linearization, and it is what "have I already
  got this file?" compares against.
- `content_sha256` is the identity of what is stored now. It is used for
  integrity, backup verification, and client cache validation (#52, #58).

Legacy Assets never had their original bytes preserved, so their
`ingest_sha256` stays **NULL**. PRKS must not invent it from bytes that have
since been materialized. Exact-duplicate detection against legacy files uses
`content_sha256` and is documented as weaker: it matches an unannotated legacy
file, and misses one whose bytes PRKS has rewritten.

### 9.5 Backup and restore

- The archive layout is unchanged: the DB plus `files/pdfs/*`. Asset rows live
  in the canonical DB and are covered by it. No new filesystem component, so
  the backup inventory needs no new classification. When Web Works add a
  second managed area (for example `snapshots/`), it must be classified as
  canonical in the inventory in the same PR.
- `audit_managed_pdfs` switches from `works.file_path` to the `assets` locators
  when Assets become authoritative (slice D), and during the transition it
  checks both.
- Restore verification may also check `content_sha256` for each Asset in the
  archive. That is a stronger integrity check than today's presence check, and
  it is optional.
- Derived per-Asset artifacts (the text index keyed by `asset_id`, thumbnails)
  stay derived and are rebuilt after restore.

---

## 10. Identity and deduplication

Three different questions, answered by three different mechanisms, and **none
of them merges anything by itself**.

```
 incoming file ──► ingest_sha256 ──► exact Asset duplicate?      (certain)
       │
 metadata ──► normalized identifiers (DOI / ISBN-13 / arXiv) ──► strong candidate
       │
       └────► normalized title + creators + year (RapidFuzz later) ─► weak candidate
                                                                          │
                          every candidate ──► user decision ◄─────────────┘
                              merge Works │ attach as Version │ attach as File │ not a duplicate
```

### 10.1 Levels

| Level | Evidence | Confidence | Allowed automation |
| --- | --- | --- | --- |
| Exact Asset duplicate | equal `ingest_sha256` (or `content_sha256` for legacy files) | Certain for the content | **Warn before import**, showing the existing Work. Never auto-link or auto-delete. |
| Identifier match | equal normalized DOI, ISBN-13, or arXiv ID (versionless → same work, versioned → same revision) | Strong for "same publication", but **not** proof of "same Work" (a DOI identifies one form) | Suggest. The user decides. |
| Metadata similarity | normalized title, creator family names, year, publisher/journal | Weak | Suggest in a review list only. RapidFuzz may rank candidates (#42/#179). **Never decides.** |

### 10.2 Exact duplicates

- Computed at ingestion (`POST /api/works` upload, Processing import, and future
  capture) **before** the Work is created, so the warning can offer choices:
  - open the existing Work;
  - import anyway as a separate Work;
  - add as another File of an existing Version;
  - add as a new Version of an existing Work.
- Identical content inside **one Manifestation** is harmless and allowed, and
  is shown as "identical copy".
- There is no UNIQUE constraint on hashes: users may legitimately keep
  duplicates.

### 10.3 Bibliographic candidates

- Normalization rules are **pure functions** with unit tests: DOI is lower-cased
  and stripped of `https://doi.org/` and `doi:`; ISBN-10 is converted to
  ISBN-13 with hyphens removed; an arXiv ID is split into versionless and
  version parts; titles are NFC-normalized, case-folded, and have punctuation
  and whitespace collapsed.
- Candidates are computed on demand or by a bounded pass. They are **derived**,
  never canonical, and can be rebuilt.
- A DOI match between a *preprint* and a *journal* Manifestation is the
  canonical "attach as Version" suggestion, not "merge".

### 10.4 User-confirmed operations

Each is a canonical domain command in the #182/#199 style, with a preview
before mutation. None of them is silent.

| Operation | Effect | IDs |
| --- | --- | --- |
| `MERGE_WORKS(source → target)` | Moves all source Manifestations (with their Assets and annotations) under the target. Unions tags. Folder, playlist and status: target wins unless the user picks. Research/Private Notes: **the user chooses** (keep target, keep source, or concatenate with a visible separator); never silently concatenated. Roles are unioned, with duplicates collapsed. Argument sources and research mentions are re-pointed, and any citation-identity collision is shown in the preview (§8.5). `last_opened_at` is max. | The source `W-…` gets `work_lifecycle(state = merged, target)`. Old links, tabs and `[[W-…]]` **reads** follow the redirect. Pending **writes** to the source are refused with `WORK_MERGED` + `target_work_id`, like `TAG_MERGED`, and are never retargeted (§14.2). |
| `MOVE_MANIFESTATION(M → Work)` | "This is really a Version of that Work." Relations to Manifestations of the old Work are dropped, with a preview. | `MF-…` unchanged. If the old Work is left with no Manifestation, it is merged into the target or deleted, and the user chooses. |
| `MOVE_ASSET(A → Manifestation)` | "This file is another scan of that edition." Annotations stay with the Asset. | `AS-…` unchanged. |
| `DECLINE_DUPLICATE(a, b)` | Records "not a duplicate". | `duplicate_decisions(entity_type, low_id, high_id, decision, decided_at)`. |

### 10.5 Declined suggestions

`duplicate_decisions` is canonical (it is a user decision), is backed up, and is
consulted by every candidate generator. A decision is on an ordered pair of
entity IDs, so it survives unrelated edits. It is revisited only if an ID is
merged away.

---

## 11. Stable IDs

### 11.1 Decision: `W-…` keeps meaning "the logical Work"

Every existing Work ID continues to identify **the same user-facing thing**:
the item in lists, the target of tags, folders, notes, playlists, argument
sources, research mentions, URLs and workspace tabs. After backfill each Work
has exactly one Manifestation and at most one Asset. Therefore:

- every Work-owned reference (tags, folder, playlist, notes, status, open
  state, research links) **needs no change**;
- every reference to Manifestation- or Asset-owned state that currently goes
  through the Work (annotations, pages pinpoints, file path, materialization,
  text index, thumbnails, last-read page) is re-pointed **once, at backfill**,
  to the Work's single Manifestation or Asset. That is a 1:1 mapping, so no
  meaning changes for existing data.

The alternative, reusing `W-…` as the Manifestation ID, is rejected in §16.

### 11.2 New IDs

| Entity | Format | Minted by |
| --- | --- | --- |
| Manifestation | `MF-` + 32 hex (distributed form per `entity_ids`) | server, or an offline client later |
| Asset | `AS-` + 32 hex | server (binary ingestion is online-only, I13) |
| Relation, identifier and decision rows | natural composite keys | — |

`MF` and `AS` are unused. `M` is taken by publisher aliases and `A` by
Arguments, and `entity_ids` warns against inferring type from prefix. APIs
always pair an ID with its entity type.

**Backfill IDs are deterministic**: `MF-` + `uuid5(NS_PRKS_BACKFILL,
"manifestation:" + work_id)`, and `AS-` + `uuid5(…, "asset:" + work_id)`,
upper-case hex. This format matches `entity_ids.is_distributed`. Consequences:

- the migration is **reproducible**: restoring the same pre-migration backup
  twice, or migrating two copies, yields identical IDs;
- the mapping from a legacy `work_id`-keyed operation to "the Manifestation
  it originally meant" is **computable forever**, even after the Work gains
  more versions (§14.2);
- clients must still **never derive** these IDs themselves. The server returns
  them. Deterministic IDs are a migration property, not a protocol.

---

## 12. Migration and backfill

### 12.1 Principles

- **Additive first.** New tables and new nullable columns. No column is dropped
  or renamed until every reader has moved (the strangler approach from #179).
- **One authority per field at every moment.** At any given schema version,
  each field has exactly one canonical home. The other copy is a projection
  kept current **in the same transaction**, by triggers or by the single
  canonical mutation boundary. Moving authority is its own migration.
- **No filesystem work in migrations** (I10). Hashing is a separate bounded,
  resumable pass (§12.4).
- **No user classification.** Every existing Work becomes Work → 1 Manifestation
  → 0 or 1 Asset automatically.
- Each step leaves `master` deployable, and every step follows `backend/AGENTS.md`
  "Database schema changes" (one migration, a matching `db_schema.sql`, and
  fresh-DB plus upgraded-DB tests).

### 12.2 The common case

```
before (v16)                          after backfill
────────────                          ──────────────
works W-1                             works W-1  (Work-owned columns authoritative;
  title, status, notes, tags…           legacy bibliographic/source columns still present)
  doi, publisher, year, …               primary_manifestation_id = MF-uuid5(W-1)
  file_path=/api/pdfs/x.pdf               │
  canonical/materialized revs             ▼
annotations(work_id=W-1)              manifestations MF-uuid5(W-1)
roles(W-1, Translator)                  work_id=W-1, kind='unspecified', title=NULL (inherit)
argument_sources(W-1, pages='45')       doi, publisher, year, … (copied)
                                        primary_asset_id = AS-uuid5(W-1)
                                          │
                                          ▼
                                      assets AS-uuid5(W-1)
                                        kind='managed_file', storage_locator='x.pdf',
                                        media_type='application/pdf', origin='legacy',
                                        ingest_sha256=NULL, content_sha256=NULL (pass fills),
                                        materialization revisions (copied), thumb_page (copied)
                                      annotations.asset_id = AS-uuid5(W-1)
                                      roles(W-1, Translator).manifestation_id = MF-uuid5(W-1)
                                      argument_sources(W-1,'45').manifestation_id = MF-uuid5(W-1)
```

The backfill rules:

- A **video** Work gets an `external_stream` Asset carrying `provider`,
  `provider_id`, `source_url` and `thumb_url`.
- A Work with **no file and no video** gets a Manifestation and **no** Asset.
- A non-video `source_url` goes to `manifestations.url`.
- `assets.media_type` takes the stored `source_mime` when one is present, and
  is inferred only when it is absent.
- A **legacy inferred-video row** (kind NULL, no file, a URL) is classified with
  `effective_source_kind()`, the same rule every reader uses. When the URL
  cannot be parsed, the row gets no Asset and keeps its URL on the
  Manifestation. Nothing is guessed (see work-source-identity.md §10).

### 12.3 Order of migrations

The versions below are illustrative. Each is one migration in one PR (see
§17).

1. **vN: create the entities.** Create `manifestations`, `assets`,
   `manifestation_relations`, and `manifestation_identifiers`. Add
   `works.primary_manifestation_id`, `annotations.asset_id`,
   `roles.manifestation_id`, and `argument_sources.manifestation_id` (a table
   rebuild that widens its key, §8.5). Backfill
   deterministically, then validate (every Work has exactly one primary
   Manifestation that belongs to it, and so on). Install **mirror triggers**
   so that `works` (still authoritative for everything) keeps the default
   Manifestation and Asset current on INSERT/UPDATE, and the child FKs
   cascade on DELETE. **No reader changes.**
2. **vN+1: Asset authority.** Asset-owned state (locator, source aggregate,
   materialization revisions, thumbnail page, annotation ownership) becomes
   authoritative on `assets`. The migration drops those mirror triggers and
   installs the reverse projection (`assets` → `works.file_path` and the other
   legacy columns) for readers not yet migrated. Only one direction exists at
   any version. The text index moves to `asset_id` keys, which is derived and
   rebuilt, not migrated.
3. **vN+2: Manifestation authority.** The same move for bibliographic fields.
   The migration **copies revision counters** from `work-field/[work, f]` to
   `manifestation-field/[MF-uuid5(work), f]` (§14.2).
4. **vN+3: Roles scope enforced.** Swap the uniqueness index to include
   `manifestation_id`, and set role scope by role type (§7.3).
5. **Later: retire legacy columns.** Drop them only after every reader uses the
   projection module. Because SQLite `DROP COLUMN` has restrictions (FTS
   triggers, indexes), this may require a table rebuild. It is optional, and the
   columns can simply stay as the maintained projection.

### 12.4 Fingerprint pass (not a migration)

This is a bounded, resumable pass modeled on `retry_pending_pdf_cleanup`: a
fixed number of Assets per run, at startup or on demand. It streams each
managed file through SHA-256, records `content_sha256`, `byte_size` and a
`fingerprinted_at`, and skips missing files, which are reported, never
treated as empty (the same rule as the text index). It never modifies bytes.
It stores hashes in the canonical DB because they describe canonical bytes, and
they are re-verifiable, so a restore can recompute them.

### 12.5 Rollback and recovery

- PRKS has **no down migrations**, and this design does not add any. A newer
  schema is refused by older code and by restore (`schema_newer`). So the
  recovery path after an upgrade is the **pre-upgrade backup**. Slice A should
  make sure the upgrade flow recommends or creates one (§18 Q9).
- Each migration is transactional (`_run_in_transaction`). A failure leaves the
  previous version intact, and a crash mid-migration rolls back.
- Additive steps are **forward-fixable**: a bad backfill can be corrected by a
  later migration, because the legacy columns were not removed.
- Mirror and reverse triggers are validated by `validate_current_schema`. A
  parity test asserts, for every Work in fixture libraries, that the legacy
  columns equal the projection of the new entities.

### 12.6 Old and new code coexisting

Within one PRKS server there is exactly one code version, so "coexistence"
means **old call sites next to new call sites**, in two forms:

- Unmigrated readers keep reading `works` columns, which the triggers or
  projection keep correct.
- Browser clients running an older cached app shell keep sending legacy
  operations. The server keeps accepting them (§14.2) until the durable
  outbox of every supported client has drained. A new client never sends a
  legacy operation for a Work with more than one Manifestation.

---

## 13. Compatibility and API projection

### 13.1 One projection module

A new focused module, for example `backend/work_projection.py`, owns:

```
legacy_work(work_id)       = Work
                           + primary Manifestation (fields; title/abstract inherited)
                           + primary Asset (file_path, source_kind, provider…, thumb_*)
                           + credits(primary M)                       → today's Work dict
legacy_work_summary(rows)  = same, for list/browse/recent/folder/person/playlist rows
```

Every endpoint that returns a Work today moves onto it, **one family at a time**,
with a parity test showing the JSON is identical to the pre-migration output
for the fixture library. The legacy dict gains only **additive** fields:
`primary_manifestation_id`, `primary_asset_id`, `manifestation_count`,
`asset_count`. Clients that do not know these fields ignore them.

### 13.2 Writes through the legacy shape

| Legacy write | Routed to |
| --- | --- |
| `PATCH /api/works/:id` with a Work-owned field | the Work |
| … with a Manifestation-owned field | the **primary** Manifestation. The editor shows the primary, so this is what the user sees. The response names the `manifestation_id` it wrote. |
| … with a source field | the primary Asset's source aggregate (`SET_WORK_SOURCE` semantics unchanged) |
| `POST /api/works` | creates Work + Manifestation + Asset in one transaction |
| `POST /api/works/:id/pdf` (materialization) | the **primary Asset**. The client must send `asset_id` once more than one exists. Without it the request is refused (`ASSET_AMBIGUOUS`) rather than guessed. |
| Annotation endpoints | the annotation's own Asset. A new annotation needs the displayed `asset_id`, which defaults to the primary only while exactly one PDF Asset exists. |
| `/api/bibtex/:id` | `citation_record(primary)`, §8.6 |

### 13.3 New API surface (typed, #185 pattern)

New endpoints use Pydantic request/response DTOs, the OpenAPI fragment, and
openapi-core tests from the start. They are the surface MCP (#47) and Android
(#52) consume:

```
GET  /api/works/:id/manifestations            list (with assets)
GET  /api/manifestations/:id                  detail + citation record
POST /api/works/:id/manifestations            add a Version
PATCH/DELETE /api/manifestations/:id
POST /api/manifestations/:id/assets           add a File (online; binary)
PATCH/DELETE /api/assets/:id
POST /api/works/:id/primary-manifestation     set primary
GET  /api/assets/:id/content                  serves bytes (the /api/pdfs route stays)
```

Nothing here is implemented in this PR.

### 13.4 Keeping the simple case simple

- A Work with one Manifestation and at most one Asset **looks exactly as it does
  today**. No "Versions" UI, no new fields in the editor, no new words.
- The richer model appears only when it has something to show:
  - a "Versions" section appears on Work detail once a second Manifestation or
    Asset exists, or from an explicit "Add another version / file…" action;
  - a version picker appears in Copy citation and in the viewer only when more
    than one choice exists;
  - duplicate warnings appear at the moment of import;
  - #61's Secondary Reading View offers "open another version".
- UI words are "Version" (Manifestation) and "File" (Asset). "Manifestation",
  "Asset" and "FRBR" stay out of the UI.

---

## 14. Backup/restore and offline/durable operations

### 14.1 Backup/restore

- Backup `FORMAT_VERSION` stays 1. The manifest already carries
  `db_schema_version`, which is what distinguishes pre- and post-#60 archives.
- A **pre-#60 backup restored into post-#60 PRKS** is migrated on open like any
  older DB. Deterministic backfill IDs mean the result is identical to what the
  original library became.
- A **post-#60 backup into pre-#60 PRKS** is refused (`schema_newer`). This is
  the existing rule.
- `pending_pdf_cleanup` claims travel with the DB, and after restore they are
  re-evaluated against a catalogue that now includes Asset locators.
- The fingerprint pass reruns after restore for Assets with a NULL or suspect
  hash.

### 14.2 Durable operations and revisions

The architecture must not assume a permanent local-first target (#179), but the
existing durable-operation guarantees (I7, I12) are compatibility constraints
and are kept.

**Scopes follow ownership, and revisions are carried, not reset.**

| Current scope | Future scope | Migration |
| --- | --- | --- |
| `work-field/[W, f]` for M-owned `f` | `manifestation-field/[MF, f]` | Copy the revision to `MF-uuid5(W)` in the authority migration. |
| `work-field/[W, f]` for W-owned `f` (status, title, abstract, author_text) | unchanged | — |
| `work-source/W` | `asset-source/AS` | Copy to `AS-uuid5(W)`. |
| `pdf-annotation/[W, ann]` | `asset-annotation/[AS, ann]` | Copy to `AS-uuid5(W)`. Deletion tombstones are copied too, so resurrection protection survives. |
| `work-person-role/[W, P, r]` | unchanged for W-scope; `manifestation-person-role/[MF, P, r]` for M-scope | Copy for backfilled M-scoped roles. |
| notes, tags, folder, playlist, open | unchanged | — |

**Legacy operations after the move.** A pending `SET_WORK_METADATA_FIELD(W,
doi, base=7)` enqueued before the upgrade is applied to
**`MF-uuid5(W)`**, the Manifestation the user was editing when they enqueued
it, **not to "whatever is primary now"**. If that Manifestation has since
moved to another Work or been deleted, the operation is refused with an
explicit conflict code. It is never retargeted. The same rule applies to
annotation operations (→ `AS-uuid5(W)`) and to `SET_WORK_SOURCE`.

**New durable families** (Manifestation fields, M-scoped roles, set-primary,
relations) each declare their shape per "Adding a family" in
`docs/local-first-sync.md`. Asset creation stays online-only (I13). Durable
file ingestion is a separate future design, not assumed here.

**Client caches.** `work:*` and the browse projections stay keyed by Work and
carry the additive fields. New cache entities are keyed by `MF-`/`AS-` IDs.
The service-worker PDF cache is already keyed by basename, which is per
Asset. The `prks.pdf.lastPage.<workId>` key becomes
`prks.pdf.lastPage.asset.<assetId>`. The default Asset reads the legacy key
once as a fallback.

**Merges and durable operations.** A pending operation's `base_revision`
belongs to a scope keyed by the **source** Work. It says nothing about the
target Work's scope, so retargeting the operation would either accept a stale
write when the counters happen to match, or report a false conflict when they
do not. The rule therefore follows what `TAG_MERGED` already does:

- A pending write to Work-owned state (notes, status, tags, folder, playlist,
  Work-scoped roles, Work fields) that names a merged `W-…` is **refused**
  with `409 WORK_MERGED` and `target_work_id`. It is never retargeted. The
  client keeps the user's intended value and presents it as an explicit merge
  conflict against the target ("apply to the merged Work?"). If the user
  accepts, the client sends a **new** operation based on the target's current
  revision.
- The merge itself does not rebase or combine revision counters. Source scopes
  are tombstoned, so a stale device cannot resurrect them, and target scopes
  advance normally for whatever the merge changed.
- `MARK_WORK_OPENED` is a max-register with no base revision (I7), so it may be
  applied to the target without conflict.
- Operations on the source Work's Manifestations and Assets are unaffected.
  Their scopes are keyed by `MF-…`/`AS-…` IDs, which survive the merge, so their
  base revisions are still meaningful.
- **Reads** (links, tabs, `[[W-…]]`, cached detail requests) follow the redirect
  to the target.

---

## 15. Downstream roadmap implications

These are the constraints each epic should adopt, so that none of them invents
its own identity or asset model.

| Issue | Implications |
| --- | --- |
| **#41 Citation V2** | Cite a **Manifestation**. Default to the Work's primary. Batch export resolves each Work to exactly one Manifestation. Build CSL-JSON from `citation_record(manifestation_id)` (§8.6), not from `works` columns. Decide cite-key persistence (§8.4) and the `urldate` quirk. Do not add citation fields to `works`. |
| **#42 Ingestion / Web Works** | Every ingest creates, or attaches to, Work → Manifestation → Asset through one canonical command. Web Work = Manifestation(`web_page`) + snapshot Asset(s) under a new managed area, classified as canonical in the backup inventory. Duplicate warnings use §10 (hash, then identifiers, then optional RapidFuzz ranking). Provenance attaches to Manifestation fields and Asset origin. BibTeX/RIS import creates Manifestations and uses identifier normalization. |
| **#57 History / Trash** | Trash granularity is Work, Manifestation, or Asset. Asset `state = trashed` keeps bytes until purge, and purge uses the `pending_pdf_cleanup` rules. Merge and move are history events with previews. `work_lifecycle` redirects are the durable record of merges. |
| **#58 Offline UX** | "Available offline" is a property of **Assets** (bytes) plus the Work's metadata. Default to the primary Asset. Validate cached bytes with `content_sha256`. Pending edits stay keyed to the entity they were made on (§14.2). |
| **#59 Capture** | Capture sends intent (URL, selection, snapshot) to the same ingestion command as #42. Dedup by normalized canonical URL, then the snapshot hash. No capture-specific Work shape. |
| **#61 Reading Workflow** | The Secondary Reading View opens **another Asset**, of the same Manifestation or of another Manifestation of the same Work, and reads `manifestation_relations` for labels. Reading location is per Asset. Each pane shows its own Asset's annotations. "Open corresponding passage" and annotation transfer are assisted, reviewed actions (§6.2). Never assume page correspondence. |
| **#47 MCP** | Tools take Work IDs for Work-level operations and **must** report which Manifestation or Asset a citation, quote or page came from. Use the typed endpoints (§13.3) rather than the legacy projection for anything version-sensitive. |
| **#52 Android** | Consume the typed API. Cache files by Asset ID, validated by `content_sha256`, never by `file_path`. Treat the legacy Work shape as display-only. |

**Standing rule until slices D and E land** (restating #60's #179 note): new
feature work must not attach new file- or edition-specific state to the `works`
row.

---

## 16. Rejected alternatives and tradeoffs

1. **Reuse `W-…` as the Manifestation ID and mint new logical Work IDs.** This
   matches the fact that today's rows are mostly bibliographic records, and it
   would keep annotations and BibTeX attached for free. **Rejected:** tags,
   folders, notes, playlists, research mentions, argument sources, URLs,
   workspace tabs, `[[W-…]]` links and pending durable operations all mean "the
   thing the user files". Every one of them would have to be re-pointed to a new
   ID, and each re-pointing is a chance to lose data or break an offline client.
   Keeping `W-…` as the Work re-points only the file-specific references, and
   those are the ones that have to change semantically anyway.
2. **Give the backfilled Manifestation the same ID as its Work.** This would make
   legacy scopes trivially compatible. **Rejected:** once a Manifestation moves
   to another Work (merge), `W-123` would name a Work in one table and a
   Manifestation of a different Work in another. Deterministic uuid5 IDs give
   the same compatibility without the ambiguity.
3. **A mandatory FRBR/LRM four-level model (Work → Expression → Manifestation →
   Item).** "Expression" (a specific translation or text) is real, but for
   PRKS's use cases it is carried by `language` + `translation_of` +
   M-scoped Translator. Two editions of the same translation are
   `new_edition_of` links between two translation Manifestations. **Deferred,
   not ruled out:** if users need to group "all editions of the Bloom
   translation", a nullable `expression_key` on Manifestation can be added
   without restructuring.
4. **Embed the default Manifestation in the `works` row permanently** (only
   extra versions get rows). **Rejected:** it creates two kinds of
   Manifestation with different code paths forever, and every consumer would
   have to handle both.
5. **Separate `manifestation_roles` table.** **Rejected** in favor of a nullable
   scope column (§7.2), so current role readers keep working with a filter.
6. **Annotations on the Manifestation.** Scans of one edition have different
   coordinates. **Rejected.** Annotations go on the Asset.
7. **Research Notes on the Manifestation.** This would scatter one line of
   thought across versions and break Concept/Argument mentions. **Rejected.**
8. **One content hash of the current bytes as identity.** This breaks on
   PRKS's own materialization and linearization (§9.4). **Rejected** in favor
   of `ingest_sha256` + `content_sha256`.
9. **Automatic merge on DOI/ISBN or high fuzzy score.** A DOI identifies one
   publication form, and fuzzy matches are wrong often enough to destroy data.
   **Rejected.** Every merge is user-confirmed with a preview.
10. **Store bytes in SQLite, or content-addressed storage replacing basenames.**
    Blobs in SQLite are ruled out by #60. Content-addressed storage conflicts
    with in-place materialization and the existing COW, cleanup and backup
    model. **Rejected for now.** Hashes are metadata, and basenames stay the
    locator.
11. **Dual-writing new and old columns from application code.** Every missed
    write path would silently diverge. **Rejected** in favor of one authority
    per field, with triggers or the single boundary maintaining the other copy
    in the same transaction (§12.1).
12. **An ORM or a new sync framework for the new entities.** Out of scope per
    #179 and this issue's boundaries.

---

## 17. Proposed implementation slices

These are derived from the repository's constraints: the migration rules in
`backend/AGENTS.md`, per-family durable scopes, the strangler pattern of
#182/#185/#199, and I10's ban on filesystem work in migrations. Each slice is
one PR (or a small sequence) and leaves `master` deployable with no
user-visible change until slice H.

**Gating rule:** no UI or API may create a **second** Manifestation or Asset for
a Work until slices D, E and F have moved every M- and A-owned state off the
Work. Until then the 1:1 backfill guarantees that the legacy behavior is
exactly right.

| Slice | Content | User-visible? | Depends on |
| --- | --- | --- | --- |
| **A. Entities + deterministic backfill + mirror triggers** | Migration vN (§12.3 step 1). `db_schema.sql`, schema validation, fresh and upgraded tests, and a parity test (legacy columns = the new rows) over fixture libraries, including video, inferred-video, no-file and shared-basename rows. No readers. | No | this design |
| **B. Asset fingerprint pass + ingest hashing** | Bounded, resumable hashing pass (§12.4). Compute `ingest_sha256` on the upload/import stream **before** linearization, for new Assets. No duplicate UI yet. | No | A |
| **C. Projection module** | `work_projection.legacy_work` / `legacy_work_summary`. Move readers family by family (detail, browse/recent, folder/person/playlist summaries, BibTeX via `citation_record`), each with JSON parity tests and a byte-identical BibTeX test. | No | A |
| **D. Asset authority** | vN+1: locator, source aggregate, materialization revisions and `thumb_page` owned by `assets`. `annotations.asset_id` authoritative. Text index keyed by Asset. Cleanup and backup audit count Asset locators. Scope and revision copies (`asset-source`, `asset-annotation`). Legacy operations mapped to `AS-uuid5(W)`. Browser last-page key migration. | No | C |
| **E. Manifestation authority** | vN+2: bibliographic fields owned by `manifestations`. `manifestation-field` scopes with carried revisions. `SET_WORK_METADATA_FIELD` mapped to `MF-uuid5(W)`. `argument_sources.manifestation_id` pinning enforced for new pinpoints. Identifier table populated from DOI/ISBN. | No | C |
| **F. Role scope** | vN+3: role-type scope defaults, the uniqueness swap, `credits(M)`, and the role-sync scope for M-scoped roles. | No | E |
| **G. Typed Version/File API** | §13.3 read endpoints first, then mutations as canonical domain commands (#182/#199 pattern) with OpenAPI and openapi-core tests. New durable families declared per the sync guide. | API only | D, E, F |
| **H. Versions UI** | "Add another version / file", set primary, open a specific Version or File, and a version picker for Copy citation, all behind §13.4's progressive disclosure. Follow `DESIGN.md` for Work detail composition. #61's secondary view builds on this. | **Yes** | G |
| **I. Exact-duplicate warning at ingestion** | Uses `ingest_sha256` (and `content_sha256` for legacy files). Offers the four choices in §10.2. | Yes | B, H |
| **J. Identifier candidates + decline** | Normalized DOI/ISBN/arXiv candidates, a review list, and `duplicate_decisions`. | Yes | E, I |
| **K. Merge / move workflow** | `MERGE_WORKS`, `MOVE_MANIFESTATION`, `MOVE_ASSET` with previews, and `work_lifecycle` redirects. Coordinate with #57. | Yes | J |
| **L. RapidFuzz evaluation** | A separate research/evaluation issue (dependency review, ranking quality on synthetic data). It only ranks; it never decides. | — | J |

A, B and C can proceed in parallel after A's migration merges. D and E are
the risky slices, because they move durable-operation scopes. Each should run
the affected sync feature E2E groups before merge, and the full E2E gate once.

---

## 18. Open questions for the maintainer

Each question has a recommendation. Nothing in slice A depends on the answers
except Q1 and Q9.

1. **Naming.** Internal `manifestation` / `asset`, UI "Version" / "File".
   *Recommendation:* approve as proposed. Q1 blocks slice A, because the table
   names become permanent.
2. **Separate citation default.** Is `works.primary_manifestation_id` enough,
   or is `citation_manifestation_id` needed from the start? *Recommendation:*
   primary only. Add the second pointer when #41 shows a need.
3. **Keep pristine originals?** Today materialization overwrites the ingested
   PDF. Should new ingestion keep the original as its own Asset
   (`role = original`) and materialize into a derived `annotated_export`
   Asset? That costs up to twice the storage, but gives the user back a
   "publisher PDF + my annotated copy" pair and a stable duplicate key.
   *Recommendation:* yes for new ingestion, behind a setting. Legacy files stay
   as they are.
4. **Abstract ownership.** Work-level with a Manifestation override (proposed),
   or Manifestation-only? *Recommendation:* as proposed, which keeps FTS
   unchanged.
5. **Per-version notes.** Are Manifestation- or Asset-level notes wanted
   ("this translation is unreliable", "scan missing pp. 40–45")?
   *Recommendation:* not now. If needed, add a small Version note field. Never
   move Research or Private Notes.
6. **Per-version reading state.** Should `status` or "last opened" also exist per
   Manifestation or Asset? *Recommendation:* Work-level `status` only. Add an
   Asset-level `last_opened_at` in slice H so "open" returns to the last-used
   file.
7. **Reviewer and Editor semantics.** What does the `Reviewer` role mean in
   PRKS today, and should Editor default to Manifestation scope?
   *Recommendation:* both default to Manifestation scope. Revisit if anthologies
   are common in real libraries.
8. **`external_link` Assets.** Is a non-managed "link to the publisher page"
   Asset useful, or is `manifestations.url` enough? *Recommendation:* the
   `url` field is enough. Keep the `external_link` kind reserved but
   unimplemented.
9. **Upgrade safety net.** Should the first #60 migration refuse to run, or
   force an automatic backup, when no recent backup exists? *Recommendation:*
   create an automatic pre-migration backup into the existing backup location
   before running vN, and log only counts. This needs your call because it
   writes to storage at startup.
10. **Cite keys.** Stored per Manifestation, or computed with disambiguation?
    This is owned by #41, and the design only reserves the column.
11. **Retiring legacy `works` columns.** Keep them indefinitely as the
    maintained projection, or plan a table rebuild once every reader has moved?
    *Recommendation:* keep them until Android and MCP ship on the typed API,
    then decide.
