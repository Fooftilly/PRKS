# Work identity, editions, versions and Assets: design (#60)

**Status: design proposal, revised after maintainer review. Nothing here is
implemented.** The three-level model has been approved in direction, and the
maintainer decisions are recorded in [§0](#0-maintainer-decisions). This
document does not add schema, migrations, API behavior or UI. It is the design
gate that #60 requires before large schema changes, and it follows the
architecture direction in #179. Once approved, the implementation slices in §17
become separate focused issues and PRs. Slice A must not start until this
revision is approved.

It follows the shape of [work-source-identity.md](work-source-identity.md):
audit the code first, then recommend. Every claim about current behavior
below was checked against `master` at `0e19b86` (schema version 16), not taken
from issue text.

Contents:

0. [Maintainer decisions](#0-maintainer-decisions)
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
18. [Remaining open questions](#18-remaining-open-questions)

---

## 0. Maintainer decisions

These were decided in the review of this PR. The rest of the document is
written to match them.

| # | Topic | Decision |
| --- | --- | --- |
| D1 | Naming | Internal entities and tables are `manifestation`/`manifestations` and `asset`/`assets`. In the UI a Manifestation is a **Version**. An Asset is not called "File" everywhere: a managed file is a **File**, and an external stream such as YouTube is a **Source** (or a stream, depending on context). |
| D2 | Upgrade backup | Slice A does **not** create or require a full automatic backup before migrating. The migration adds tables and columns only, runs inside one SQLite transaction, and changes nothing on the filesystem. An automatic upgrade snapshot that covers only the DB, sized to what the upgrade touches, may be evaluated separately. PRKS never silently packs the whole library at startup. |
| D3 | Pristine originals | The architecture must allow keeping pristine originals. PRKS's own materialized (annotated) PDF is **not** a separate user-visible Asset. An Asset is one stable file lineage, and its original bytes and its generated materialization are storage slots inside that Asset (§9.4). No extra entity is added for this in Slice A. |
| D4 | Citation default | Add a nullable `works.citation_manifestation_id` from the start. NULL means "cite the primary". `primary_manifestation_id` is the Version the user normally reads and sees; `citation_manifestation_id` is the preferred Version for citing. There is no UI for it yet, because #41 owns that. |
| D5 | Abstract | The canonical abstract lives on the Work. A Manifestation may override it. |
| D6 | Roles | Author and Mentioned are Work-scoped. Translator, Introduction, Foreword and Afterword default to Manifestation scope. Editor defaults to Manifestation scope, and each row can be overridden (for anthologies and compiled works). **Reviewer stays Work-scoped by default**, because its current meaning is not documented well enough to assign every existing Reviewer to one Version. |
| D7 | Notes and reading state | No per-Version Research Notes, Private Notes or Progress/status for now. All three stay on the Work. Page and reading location belong to the Asset. An Asset-level "last opened" or last-used-Version mechanism may come later with #61. |
| D8 | `external_link` Assets | Not implemented. `manifestations.url` is enough. |
| D9 | Cite keys | Whether cite keys are stored, and how they are disambiguated, stays with #41. |
| D10 | Legacy `works` columns | They stay as a maintained compatibility projection until the typed API and future external clients are established. When to remove them is decided later. |
| D11 | Review fixes | (A) `argument_sources` gets a new row identity (§8.5). (B) The backfill keeps `source_mime` (§5, §12.2). (C) After a merge, pending operations are refused with `WORK_MERGED` and are never redirected (§10.4, §14.2). (D) Ownership consistency is enforced by the database (§4.1). |

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
Manifestation. Exactly one of them is **primary**, meaning the Version the user
normally reads and sees. Optionally, one is the **citation** Version (D4).

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

### 3.3 Asset: internal `asset`, UI "File" or "Source"

This is **one stable source or file lineage that belongs to one
Manifestation**: a PDF the user added, a web snapshot, a future attachment,
or an external provider-hosted stream (a YouTube video). It owns:

- the storage slots for its bytes (§9.4);
- media type, byte size and content hashes;
- provenance of the bytes;
- every piece of state defined by that content's own coordinates: PDF
  annotations, page positions and page thumbnails.

An Asset is **one lineage, not one byte string**. When PRKS linearizes a PDF or
materializes annotations into it, the bytes change but the Asset does not: no
new Asset is created, and nothing new appears to the user (D3). A new Asset
exists only when the user brings in another file or source: another scan, a
file from elsewhere, a re-captured snapshot.

`asset.kind ∈ { managed_file, external_stream }`:

- `managed_file`: bytes stored by PRKS under managed storage (PDFs today,
  web snapshots and attachments later). The UI calls it a **File**.
- `external_stream`: identified by a provider and a provider ID (YouTube).
  This is where today's `SET_WORK_SOURCE` aggregate moves. The UI calls it a
  **Source** or stream (D1).

Links that PRKS does not store are **not** Assets. The Manifestation's `url` is
enough (D8).

### 3.4 Relationship and supporting entities

| Entity | Purpose |
| --- | --- |
| `manifestation_relations(work_id, from_id, to_id, relation)` | Typed links between Manifestations of the **same** Work. The rule is enforced by composite FKs on both endpoints and `CHECK (from_id <> to_id)` (§4.1). Relation types: `revision_of`, `published_version_of`, `new_edition_of`, `translation_of`, `reprint_of`. These carry "arXiv v2 revises v1", "journal version of the accepted manuscript", and "Serbian translation of the Greek text". They carry no page correspondence (#61 owns assisted correspondence). |
| `manifestation_identifiers(manifestation_id, scheme, value, normalized)` | DOI, ISBN, arXiv ID (versioned and versionless), PMID, and similar. `works.doi`/`isbn` backfill into these later. This is the entity duplicate detection keys on. |
| `roles` gaining a nullable `manifestation_id` | Edition-scoped credit (§7). The owner is enforced by a composite FK (§4.1). |
| `argument_sources` rebuilt with `PRIMARY KEY (argument_id, order_index)`, a nullable `manifestation_id`, and citation identity `UNIQUE (argument_id, work_id, COALESCE(manifestation_id, ''), pages)` | One Argument can cite the Work in general **and** several Versions of it, each with its own pinpoint (§8.5). |
| `sync_work_lifecycle(work_id, state, target_work_id)` | Merge record, named and modeled like `sync_tag_lifecycle`. It is distinct from the existing `work_lifecycle_sync` module (`CREATE_WORK`/`DELETE_WORK`). The table it redirects **reads** and refuses **every pending operation** naming the merged Work with `WORK_MERGED` (§10.4, §14.2). |
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
                                 │               │ citation_manifestation_id (0 or 1)
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
| Work → primary Manifestation | exactly 1 | `works.primary_manifestation_id`. The Version the user normally reads and sees. It must belong to the same Work (§4.1). |
| Work → citation Manifestation | 0 or 1 | `works.citation_manifestation_id`. NULL means "cite the primary". If set, it must belong to the same Work (§4.1). |
| Manifestation → Asset | 1 → 0..* | Zero means "metadata, no file". |
| Manifestation → primary Asset | 0 or 1 | `manifestations.primary_asset_id`. It must be non-NULL whenever the Manifestation has an active Asset, and it must belong to that Manifestation (§4.1). |
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
| Publisher PDF + PRKS annotations | 1 MF → **1** Asset. The pristine publisher bytes (when kept) and PRKS's annotated materialization are two storage slots of that one Asset, not two Assets (D3, §9.4). |
| Publisher PDF + a copy annotated in another tool | 1 MF → 2 Assets. The user brought in a second file, and `derived_from_asset_id` may record where it came from. |
| Future Web Work | 1 Work → MF(web_page, url, site, accessed date) → Asset(sanitized HTML snapshot) and optionally a readable-text Asset. A re-capture is a new Asset in the same MF. |
| Metadata but no file | 1 Work → 1 MF → 0 Assets. |
| Video | 1 Work → MF(video) → Asset(external_stream: youtube, provider_id). |
| Exact duplicate file | Two Assets with the same `ingest_sha256`. This is detected (§10.2) and never merged silently. |
| Probable bibliographic duplicate | Two Works whose MFs share a normalized DOI/ISBN or have similar title/author/year. They become a **candidate**, and only the user decides (§10.3). |
| Genuinely separate works | Two Works. A declined candidate is recorded in `duplicate_decisions`. |

### 4.1 Ownership integrity (enforced by the database)

The compatibility model keeps legacy parent IDs (`work_id`) next to the new
entity IDs (`manifestation_id`, `asset_id`). If those were separate FKs, every
referenced row could exist while the combination is impossible, for example a
role on `W1` scoped to a Manifestation of `W2`. The legacy projection, cascades,
moves, merges, sync scopes and cleanup would then disagree about who owns what.
So **same-owner consistency is a database invariant**. Canonical commands still
validate it first so they can return a useful error, but the database is the
final guard.

**Mechanism.** SQLite gives two tools, and the design uses each where it
fits:

1. **Composite ownership FKs** wherever the child table can be created or
   rebuilt safely: the new tables, and the **leaf** tables `roles`,
   `annotations` and `argument_sources`, which nothing references. A child
   refers to the pair `(entity id, owner id)`, and that pair only exists on the
   parent row if the ownership is real.
2. **Narrow integrity triggers** on `works`. Adding a composite FK to `works`
   means rebuilding it, and it is the parent of about ten tables. PRKS
   migrations run inside one `BEGIN IMMEDIATE` transaction with
   `foreign_keys = ON`, and SQLite cannot turn foreign keys off inside a
   transaction. Its `DROP TABLE works` step would therefore perform an
   implicit cascading delete of every child row. `ALTER TABLE ADD COLUMN`
   cannot add a table-level composite FK, so the two pointer columns are added
   plainly and guarded by triggers instead.

```sql
-- parent pairs (new tables)
manifestations: UNIQUE (id, work_id)
assets:         UNIQUE (id, manifestation_id), UNIQUE (id, work_id)
                -- assets.work_id is a denormalized copy of its Manifestation's Work

-- composite ownership FKs
assets         FOREIGN KEY (manifestation_id, work_id) REFERENCES manifestations(id, work_id)
                   ON UPDATE CASCADE ON DELETE CASCADE
manifestations FOREIGN KEY (primary_asset_id, id) REFERENCES assets(id, manifestation_id)
                   ON UPDATE NO ACTION ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
                   -- NO ACTION, not RESTRICT: SQLite applies RESTRICT immediately even
                   -- on a deferred FK, which would deadlock moving an only child
-- leaf tables, rebuilt in the same migration (SQLite cannot add a table constraint in place)
annotations    FOREIGN KEY (asset_id, work_id)         REFERENCES assets(id, work_id)
                   ON UPDATE CASCADE ON DELETE CASCADE
roles          FOREIGN KEY (manifestation_id, work_id) REFERENCES manifestations(id, work_id)
                   ON UPDATE CASCADE ON DELETE CASCADE
argument_sources
               FOREIGN KEY (manifestation_id, work_id) REFERENCES manifestations(id, work_id)
                   ON UPDATE CASCADE ON DELETE RESTRICT
-- new table: both endpoints must be Manifestations of the row's own work_id
manifestation_relations (work_id, from_id, to_id, relation,
                   PRIMARY KEY (from_id, to_id, relation), CHECK (from_id <> to_id))
               FOREIGN KEY (from_id, work_id) REFERENCES manifestations(id, work_id)
                   ON UPDATE CASCADE ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
               FOREIGN KEY (to_id,   work_id) REFERENCES manifestations(id, work_id)
                   ON UPDATE CASCADE ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
                   -- deferred so a merge can move both endpoints one statement at a time

-- works pointers: ALTER TABLE ADD COLUMN + triggers
works.primary_manifestation_id  TEXT
works.citation_manifestation_id TEXT

-- transaction-local Work retirement marker (see "Transitions" below)
work_retirement_guard (id INTEGER PRIMARY KEY CHECK (0)) -- can never hold a row
work_retirement (
    work_id    TEXT PRIMARY KEY,
    must_clear INTEGER NOT NULL DEFAULT 1
        REFERENCES work_retirement_guard(id) DEFERRABLE INITIALLY DEFERRED
)   -- any row still present at COMMIT violates the FK, so the commit fails
```

The existing single-column FKs (`roles.work_id → works`,
`annotations.work_id → works`, and so on) stay as they are.

| Trigger | Rule |
| --- | --- |
| `works_manifestation_pointers_owned` (BEFORE UPDATE OF `primary_manifestation_id`, `citation_manifestation_id` ON `works`) | A non-NULL pointer must name a Manifestation whose `work_id` is this Work (`MANIFESTATION_OWNER_MISMATCH`). The primary may not be set back to NULL (`WORK_PRIMARY_MANIFESTATION_REQUIRED`), **unless the Work is marked in `work_retirement`**, meaning the same transaction deletes or merges it (see "Transitions" below). |
| `works_retirement_clear` (AFTER DELETE ON `works`) | Removes the Work's `work_retirement` marker when its row is deleted. |
| `work_retirement_delete_only_after_work` (BEFORE DELETE ON `work_retirement`) | A marker cannot be deleted while its Work row still exists (`WORK_RETIREMENT_WORK_STILL_EXISTS`). The only path out is `works_retirement_clear`, which runs after the Work row is gone. |
| `work_retirement_no_update` (BEFORE UPDATE ON `work_retirement`) and `work_retirement_guard_no_update` | Markers and the guard are immutable, so a marker cannot be renamed to a missing Work and then deleted. |
| `works_manifestation_pointers_insert` (BEFORE INSERT ON `works`) | A Work is inserted with NULL pointers. Its Manifestation cannot exist before the Work, because `manifestations.work_id` references it. |
| `manifestations_pointer_target_move` (BEFORE UPDATE OF `work_id` ON `manifestations`) | A Manifestation that its Work names as primary or citation cannot be moved away (`MANIFESTATION_IS_POINTER_TARGET`). The pointer must change first, in the same transaction. |
| `manifestations_pointer_target_delete` (BEFORE DELETE ON `manifestations`) | The same rule for deletion. A whole-Work delete is unaffected: the Work row is gone before its Manifestations cascade. |
| `manifestation_origin_immutable`, `asset_origin_immutable` (BEFORE UPDATE OF `origin_work_id`) | The legacy-operation mapping (§11.2) can never be rewritten. |

What each required invariant maps to:

| Invariant | Enforced by |
| --- | --- |
| `works.primary_manifestation_id` belongs to that Work | `works_manifestation_pointers_owned` plus the two `manifestations_pointer_target_*` triggers, so neither side of the pair can drift |
| `works.citation_manifestation_id`, if set, belongs to that Work | the same triggers. NULL means "use primary". |
| `manifestations.primary_asset_id`, if set, belongs to that Manifestation | composite FK `(primary_asset_id, id) → assets(id, manifestation_id)` |
| `roles(work_id, manifestation_id)` has a matching owner | composite FK. A NULL `manifestation_id` is a Work-scoped role, and the FK does not apply. |
| `argument_sources(work_id, manifestation_id)` has a matching owner | composite FK. A NULL `manifestation_id` is a Work-level citation. |
| `annotations(work_id, asset_id)`: the Asset's Manifestation belongs to that Work | `annotations → assets(id, work_id)`, and `assets(manifestation_id, work_id) → manifestations(id, work_id)`. The chain carries ownership from the Asset through its Manifestation. |
| An Asset's denormalized `work_id` matches its Manifestation's Work | composite FK on `assets` |
| `manifestation_relations` never crosses Works and never relates a Manifestation to itself | two deferred composite FKs, `(from_id, work_id)` and `(to_id, work_id)`, plus `CHECK (from_id <> to_id)`. If one endpoint moves to another Work while the relation remains, the commit fails. When a merge moves **both** endpoints, the relation's `work_id` follows by cascade. `MOVE_MANIFESTATION` deletes the moved Version's relations first (§10.4). |

**How the cascade and restrict choices behave:**

- **Moves keep owners consistent.** Moving a Manifestation to another Work is
  one `UPDATE manifestations SET work_id`. `ON UPDATE CASCADE` carries the new
  `work_id` to that Manifestation's Assets, then to their annotations, and to
  its scoped roles and pinned argument sources. No application code has to
  re-copy owner columns.
- **A pointer target cannot leave silently.** A Manifestation that is its
  Work's primary or citation Version cannot be moved away or deleted until the
  pointer changes (triggers, immediate). A primary Asset may be moved or
  deleted inside a transaction, but that transaction cannot commit unless the
  pointer has also changed (the deferred `NO ACTION` FK).
- **A pinned citation blocks deleting a Version.** Deleting a Manifestation that
  an Argument pins is refused (`RESTRICT`). The canonical command reports it as
  in use, like `ARGUMENT_IN_USE` today. Deleting a whole Work still cascades
  cleanly: every child row goes with it.

**Transitions: every legal final state has a legal path.** Pointer rules must
not deadlock a move whose *result* is valid, and they must not need transient
placeholder Versions or Files. The two "only child" cases:

- **Moving the only (primary) Asset out of a Manifestation.** The final state,
  a Manifestation with zero Assets and a NULL `primary_asset_id`, is valid. In
  one transaction: `UPDATE assets SET manifestation_id, work_id`, then
  `UPDATE manifestations SET primary_asset_id = NULL` (either order works). The
  deferred `NO ACTION` FK is satisfied at COMMIT. There is no "may not clear
  while an Asset is active" trigger, because that rule deadlocked this case.
  The final-state rule it expressed now lives in the canonical command's
  final-state check and in the integrity query (below).
- **Moving the only Manifestation out of a Work.** A Work with zero Versions is
  **not** a valid final state, so the emptied source Work has to be retired in
  the same transaction. There are exactly two supported outcomes:
  - **delete** the empty Work (`MOVE_MANIFESTATION` with
    `empty_source = delete`);
  - **merge** it into the target (`MERGE_WORKS`, §10.4).

  Both use the same explicit transition, which the triggers recognize:
  1. `INSERT INTO work_retirement (work_id)`. For a merge, the durable
     `sync_work_lifecycle(merged)` row is also inserted.
  2. Clear the Work's pointers. The trigger allows this only because of step 1.
  3. Move the Manifestation(s). The owner columns follow by cascade.
  4. `DELETE FROM works` for the retired Work. `works_retirement_clear` removes
     the marker.

  If step 4 never happens, the marker's guard FK makes COMMIT fail and
  everything rolls back. The machinery cannot be bypassed:
  - the guard table has `CHECK (0)`, so no row can be inserted to satisfy the
    FK;
  - `must_clear` is `NOT NULL`;
  - a marker cannot be deleted while its Work exists;
  - a marker cannot be updated.

  **No transaction can commit a live Work with a NULL
  `primary_manifestation_id`.** A retirement marker never outlives its
  transaction.

Moving a **primary** Asset or Manifestation while siblings remain simply
re-points the pointer to a sibling first (or, for the Asset, in either order).
Moving a non-primary child needs no pointer change at all.

**What SQLite cannot enforce at commit.** SQLite has no deferred triggers or
CHECKs. So "every Work *has* a primary Manifestation" (at creation) and "a
Manifestation with an active Asset *has* a primary Asset, and it is active"
cannot be required as database constraints at commit. Both are set by the single statement or canonical command that
creates the rows. In Slice A the mirror trigger does it: it inserts the
Manifestation (and Asset) and sets the pointers inside the Work's own INSERT.
The triggers above then make sure a Work's primary, once set, can only be
cleared by retiring the Work, and that no pointer ever names a foreign row.
Every canonical command that touches Versions or Files runs a **final-state
check** on the rows it touched before committing, and refuses the command if
the check fails. The same check exists as an **integrity query** (Works with a
NULL primary; Manifestations with active Assets but no primary Asset, or whose
primary Asset is not active), which runs in the schema tests, after the
backfill, and in backup verification.

All of the above was prototyped against SQLite 3.45 (the version used in
development). Contradictory rows for each invariant were refused, and legal
moves, pointer swaps and whole-Work deletes left `PRAGMA foreign_key_check`
clean. Adding the columns and triggers inside a transaction with foreign keys
on also left existing child rows intact. PRKS already enables
`PRAGMA foreign_keys` on every connection (`db_manager`, `db_migrations`).

**Validation.** `validate_current_schema` / `_assert_known_table_shapes` gain
the composite FK tuples and the triggers, so a DB missing any of them fails
validation (the FK-tuple checks already exist). Slice A's tests cover fresh and
upgraded DBs:

- each invariant's contradictory insert or update is refused;
- legal moves cascade their owner columns;
- a pointer target cannot be deleted or moved away while referenced, and a
  transaction that moves a primary Asset without changing the pointer fails at
  COMMIT;
- **transition tests** (Slice A for the schema, Slice K for the commands):
  moving the only primary Asset out leaves the source Manifestation with zero
  Assets and a NULL primary; moving a non-primary Asset needs no pointer
  change; moving the only Manifestation out succeeds with each supported
  outcome for the emptied Work (delete, merge); clearing a primary without
  retirement is refused; a retirement marker left behind makes COMMIT fail;
  **the direct bypasses are refused**: inserting a guard row (`CHECK`),
  deleting a marker while its Work exists, updating or renaming a marker, and
  a `NULL` `must_clear`;
  and every failed transition rolls back with all pointers and owner columns
  unchanged;
- after the backfill, `PRAGMA foreign_key_check` reports nothing for the
  rebuilt tables, and the integrity query returns no rows;
- a fixture with legacy orphaned annotations, roles and argument sources
  upgrades successfully. Its orphans end up in `migration_quarantine`,
  byte-for-byte equal to the originals, and are absent from the rebuilt
  tables.

---

## 5. Ownership matrix

Legend: **W** = Work, **M** = Manifestation, **A** = Asset, **J** =
relationship/join entity, **D** = dedicated domain object. "Backfill"
describes where today's value goes; §12 covers how.

| Concept (current column/table) | Owner | Backfill of current value | Rationale / tradeoff |
| --- | --- | --- | --- |
| Canonical title (`title`) | **W** | stays on `works.title` | The name the user files the work under. |
| Publication title | **M** (`manifestations.title`, NULL = inherit Work title; never `''`, enforced by `CHECK (title IS NULL OR title <> '')`, and the same for the `abstract` override) | NULL (inherit) | For one version, editing the title changes both, as today. A translation sets its own. Inheritance avoids copying every title. |
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
| Private Notes (`private_notes`) | **W** | unchanged | §6.5. No per-Version notes (D7). |
| Annotations / comments (`annotations`) | **A** | `annotations.asset_id` = the Work's backfilled Asset | §6.1. Coordinates belong to one file. |
| PDF page references in notes (`[[pdf:<annotationId>]]`) | resolve through the annotation's **A** | nothing to move | Annotation IDs are globally unique, so existing markers keep resolving. The viewer opens the owning Asset. |
| Pinpoint pages (`argument_sources.pages`) | **J**, pinned to **M** | `manifestation_id` set to the default M when `pages` is non-empty. The table is rebuilt with a new key. | §8.5. |
| Reading progress (`status`) | **W** | unchanged | "Have I read this work?" No per-Version status (D7). **Tradeoff:** someone who has read translation A but not the original cannot record that yet. |
| Last-read page (`localStorage prks.pdf.lastPage.<workId>`) | **A** (per device) | client falls back from the Asset key to the legacy Work key for the default Asset | Page N means nothing in another file. #61 needs an independent location per version. |
| Open/recent (`last_opened_at`) | **W** (max-register) | unchanged | Recent lists Works. An Asset-level "last opened" or last-used-Version value may come later with #61 (D7). |
| Thumbnail page (`thumb_page`) | **A** | copied to the default Asset | It is a page of that file. The Work card uses the primary Asset. |
| Remote thumbnail (`thumb_url`) | **A** | copied to the default Asset | Presentation of the video asset. |
| Hide link annotations (`hide_pdf_link_annotations`) | **W** (viewer preference) | unchanged | A display preference for the sidebar list that applies to whichever PDF is shown. Low stakes. |
| Citation target default (new) | **W** pointer `citation_manifestation_id` → **M** | NULL (= primary) | Deliberately separate from `primary_manifestation_id` (D4, §8.3). |
| Citation / cite key | **M** (whether a `cite_key` is stored belongs to #41, D9) | none (it is computed today) | §8. |
| Source / provenance | **A** for bytes (`origin`, `origin_url`, `imported_from_processing_file_id`). **M** field provenance for metadata. | `origin = 'legacy'` | Bytes and metadata have different provenance. |
| File path (`file_path`) | **A** (`storage_locator`, the working/served bytes) | copied | The Work projection keeps `file_path` (§13). `source_locator` (pristine bytes) stays NULL for legacy files (§9.4). |
| MIME / media type (`source_mime`) | **A** (`media_type`) | Copy `source_mime` when it is non-empty. Otherwise infer `application/pdf` for managed PDFs, and leave it NULL for everything else. | `POST /api/works` accepts and stores a caller-supplied `source_mime`, so a user database may hold values even though the audited library had none. The backfill must never overwrite them. |
| Byte size | **A** (`byte_size`) | filled by the fingerprint pass (§12.4) | It replaces the per-row `stat()` in `finish_work_summary_rows` once trusted. |
| Content hash | **A** (`ingest_sha256` for the source bytes, `content_sha256` for the working bytes) | `content_sha256` from the fingerprint pass. `ingest_sha256` stays NULL for legacy files. | §9.4 and §10.2. |
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

- `annotations.asset_id` NOT NULL (after backfill), with the composite FK
  `(asset_id, work_id) → assets(id, work_id)` (§4.1).
- `annotations.work_id` is kept as a denormalized value that must equal the
  Asset's Work. The composite FK enforces this, and moves keep it current by
  cascade. Existing queries and the `pdf-annotation/[work, annotation]` scope
  keep working (§14.2).
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
| **Merging** duplicate Works | Assets move **whole** with their Manifestation. Each annotation keeps its ID, content, coordinates and `asset_id`. Only its denormalized `work_id` changes, rewritten by `ON UPDATE CASCADE` from the Manifestation move (§4.1). | An annotation's Asset never changes, so its coordinates stay valid, and the database keeps the owner columns consistent. |
| Merging two Assets that are **exact duplicates** (same `ingest_sha256`) | The user chooses: keep one Asset and **union** the annotation sets (the coordinates are provably identical), or keep both. | Content identity proves the coordinates are compatible. It is still never automatic. |
| Replacing a file **in place** (a "correct scan" in the same M) | A new Asset that `supersedes` the old one. The old Asset's annotations stay on it, and the UI offers "copy annotations to the new file" only as an assisted action (below). | |

**Assisted annotation transfer (future, #61):** a copy is created with
text-anchor matching (for example a quoted passage plus prefix/suffix
captured from the source text layer) and a review list of matched, uncertain and unmatched items.
It is never a move, so the originals remain on their Asset. It is never offered
for translations.

### 6.3 Materialized bytes and "the annotated copy"

Today the managed file **is** the annotated export (§1.3). In the new model,
PRKS's materialization is **derived storage of the same Asset**, not a peer
Asset (D3):

- the annotations are the canonical rows on that Asset;
- the materialized PDF is those annotations rendered into the Asset's working
  bytes (`storage_locator`);
- the pristine bytes, when kept, sit in the same Asset's `source_locator`.

The user sees one File, with or without its original preserved. Keeping
pristine originals is permitted by this model but is not required by any slice
(§9.4). A copy that the user annotated in **another** tool and brought in is a
different matter: it is a genuinely separate file, and therefore a separate
Asset.

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

Per-Version notes ("this translation mistranslates §4") are out of scope for
now (D7). If they are ever added, they will be a separate small
Manifestation-level field, never a change of scope for the Research Note.

### 6.5 Private Notes belong to the Work (with a stated tradeoff)

Private Notes are reminders ("chapter 3 is relevant to X", "ask Y about
this"). Most of them concern the work, so they stay on the Work, unchanged.
**Tradeoff:** some reminders are file facts ("this scan is missing pp. 40–45").
If PRKS ever needs to record those, they belong in Asset state (for
example a condition flag), not in moved Private Notes. Nothing like that is
planned now (D7). The notes deliberately do not share a scope:
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
| Editor | **Manifestation** (D6) | Usually edition-specific: a critical edition's editor, or a volume editor of one printing. **Ambiguous for anthologies**, where the editor is the creator of the compiled work. Each row's scope can be overridden, so such an Editor moves to Work scope. |
| Translator | **Manifestation** | Identifies one translation. |
| Introduction / Foreword / Afterword | **Manifestation** | They belong to one edition's apparatus. |
| Reviewer | **Work** (D6) | The role's current product meaning in PRKS is not documented well enough to assign every existing Reviewer to one publication Version. A user may explicitly create or move a Reviewer role to Manifestation scope when that is what the relationship means. |
| Mentioned | **Work** | "Person discussed in this work" is a research relationship. See §7.4 about its asset-derived origin. |

These are **defaults applied at backfill and at role creation**. Scope is
stored per row, so a user can override a default.

### 7.2 Storage

- `roles` gains a nullable `manifestation_id` with the composite FK
  `(manifestation_id, work_id) → manifestations(id, work_id)` (§4.1). A role
  cannot be scoped to another Work's Version. NULL means Work scope. Keeping one table, rather than adding
  `manifestation_roles`, means every current `roles` reader (People pages,
  credit helper, Research Graph, BibTeX) keeps working with a
  filter instead of a UNION.
- The uniqueness (I9) becomes
  `UNIQUE(person_id, work_id, role_type, COALESCE(manifestation_id, ''))`, so
  one person can translate two different translations of one Work.
- **Credit-name override:** the credit on a Work-scoped Author row is the
  default. A Manifestation may override how that person is credited on it
  through `manifestation_credit_overrides(manifestation_id, person_id,
  role_type, credit_name)`. `credits(M)` (§7.5) applies these overrides. The
  backfill writes none, and no UI for editing them is needed early. **The
  table is nevertheless required by the time `MERGE_WORKS` ships (Slice K)**,
  because a merge preserves colliding credit spellings there (§10.4, step 4).
  Without it, a merge would have to drop a credit.

### 7.3 Backfill rule

Every existing Work has exactly one Manifestation, so scope is invisible today.
Slice A adds the `manifestation_id` column (NULL everywhere) and the composite
FK. The **role-scope migration** (§12.3 step 4), which runs together with the
role-sync scope change, then sets `manifestation_id` on the edition-scoped role types
(Editor, Translator, Introduction, Foreword, Afterword). It leaves Author,
Mentioned and Reviewer at Work scope (D6). **Nothing visible changes.** The legacy
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

### 8.3 Primary and citation Versions

A Work has two pointers, deliberately kept separate so that "what I read and
open" is not tied to "what I cite" (D4):

| Pointer | Meaning | NULL |
| --- | --- | --- |
| `works.primary_manifestation_id` | The Version normally shown, listed and opened. | Never NULL (§4.1). |
| `works.citation_manifestation_id` | The preferred Version for citing (for example, read the translation but cite the critical edition). | Means "use the primary". The backfill leaves it NULL everywhere, so citation output is unchanged. |

```
citation_target(work) = COALESCE(works.citation_manifestation_id,
                                 works.primary_manifestation_id)
```

- Copy citation, BibTeX and CSL on a Work cite `citation_target(work)`.
- An explicit "cite this Version" on any Manifestation cites that one.
- Batch export resolves **each Work to exactly one Manifestation**
  (`citation_target`, unless the caller names another) and never flattens
  several bibliographic records into one entry.
- Both pointers must belong to the Work (§4.1). A Manifestation that either
  pointer names cannot be deleted or moved away until the pointer is changed.
- No UI sets the citation pointer yet. #41 owns that.

### 8.4 Coexisting citeable Manifestations

Each Manifestation produces its own complete entry. Two translations produce
two entries with different titles, translators, publishers and ISBNs. Cite
keys must be unique across Manifestations. The current computed key
(`<FirstAuthor><year>`) already collides across Works and would collide across
Versions. Whether keys are stored, and how they are disambiguated, is owned
by #41 (D9). This design adds no cite-key column.

### 8.5 Research citations inside PRKS: `argument_sources` identity

`argument_sources` is PRKS's own citation. What its call sites do today:

| Call site | What it does |
| --- | --- |
| `research_network._replace_sources_on_conn` | Replaces an Argument's **whole list**: validates, then DELETEs all rows and INSERTs them again with `order_index = position`. It refuses a repeated `work_id` ("Duplicate source Work"). |
| `argument_sync` (`current_sources`, `set_sources_on_conn`) | The durable unit is `argument-sources/<argument>`, the **whole ordered list**. The wire shape is `[{work_id, pages}]`, ordered by `order_index, work_id`. |
| `research_network._argument_sources` | Reads the list for Argument detail, joining Work title and Authors. |
| `research_graph` | Reads `(argument_id, work_id, pages)` to build Argument→Work edges. |
| migration v11→v12 | Created legacy rows with `order_index = 0`, so several rows of one Argument can share an `order_index`. |

No call site addresses a single row. A citation is an **item in an ordered
list** that is always replaced as a whole.

**New row model:**

```sql
CREATE TABLE argument_sources (
    argument_id      TEXT NOT NULL REFERENCES arguments(id) ON DELETE CASCADE,
    order_index      INTEGER NOT NULL,          -- position in the list
    work_id          TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    manifestation_id TEXT,                      -- NULL = the Work in general
    pages            TEXT NOT NULL DEFAULT '',  -- pinpoint / locator
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (argument_id, order_index),
    FOREIGN KEY (manifestation_id, work_id) REFERENCES manifestations(id, work_id)
        ON UPDATE CASCADE ON DELETE RESTRICT
);
CREATE UNIQUE INDEX idx_argument_sources_citation
    ON argument_sources(argument_id, work_id, COALESCE(manifestation_id, ''), pages);
CREATE INDEX idx_argument_sources_work_id ON argument_sources(work_id);  -- kept
```

- **The row key is the list position**, `(argument_id, order_index)`. That is
  the identity the list-replacement semantics already give a row.
- **Citation identity** is `(work, Version or none, pinpoint)`, and only
  **exact duplicates** are refused. This meets every requirement:
  - one Argument may cite the Work in general (`manifestation_id` NULL);
  - it may also, or instead, cite several Versions of that Work, each with its
    own pinpoint;
  - it may give several pinpoints for the same Version or Work;
  - an identical repeated row carries no information.
- **Work-level rows may carry a locator.** Classical citations often use
  locators that do not depend on the edition (Stephanus "514a", Bekker
  numbers), so there is no rule that "pages ⇒ Version".
- The owner is enforced by the composite FK (§4.1). Deleting a Version that an
  Argument pins is refused and reported as in use, never silently unpinned.

**Rejected: a stable surrogate row ID.** Rows are rewritten on every list
replacement, so an ID would not stay stable unless the protocol were extended
to carry it, and nothing references an individual citation today. If a future
feature needs one (for example, quote anchors inside a citation), it can be
added then without changing the citation identity above.

**Backfill (table rebuild, in the Slice A migration).** SQLite cannot change a
primary key in place, so the migration rebuilds the table:

- It copies rows in the canonical read order `(order_index, work_id)` and
  renumbers `order_index` as `0..n-1` for each Argument. This fixes the legacy
  `0, 0, 0` rows **without changing the order any reader sees**, so the
  `argument-sources` list value, and therefore its revision, is unchanged.
  **Exception:** an Argument that lost a row to quarantine (§12.3 step 1.1,
  for example a citation of a Work that no longer exists). `current_sources()`
  reads rows without joining `works`, so such orphans are part of today's
  synced list. That Argument's surviving rows are renumbered, and its
  `argument-sources/<A>` revision is **advanced** in the same transaction, so
  an offline replace based on the old list conflicts instead of silently
  succeeding.
- It **pins** each row with non-empty `pages` to the Work's backfilled
  Manifestation, because that pinpoint was written against that pagination.
  It leaves rows with empty `pages` at Work level. A user can later unpin a
  locator that does not depend on the edition.
- It cannot produce collisions, because the old key already made
  `(argument_id, work_id)` unique.

**Legacy wire compatibility.** Clients that send `[{work_id, pages}]` keep
working:

- An entry that matches **exactly one** existing row's `(work_id, pages)`
  keeps that row's `manifestation_id`.
- **Ambiguous match is refused.** If a `(work_id, pages)` key matches **more
  than one** existing row (a Work-level locator and a pinned row with the same
  pages, or pins to two Versions), the whole legacy list is refused with
  `ARGUMENT_SOURCE_VERSION_REQUIRED`. The legacy shape cannot say which row it
  means, so PRKS does not prefer either one. Only a Version-aware client can
  create such rows, so only a Version-aware client can edit that list. A legacy
  list containing the same `(work_id, pages)` twice is refused as a duplicate,
  as today.
- A new entry with empty `pages` is Work-level.
- A new entry with `pages` is pinned to the Work's only Manifestation when it
  has exactly one. When it has several, the entry is **refused** with
  `ARGUMENT_SOURCE_VERSION_REQUIRED`, never guessed.
- A legacy list that would silently drop Version-specific rows it cannot
  express is refused the same way.

New clients send an optional `manifestation_id` on each entry. The "Duplicate
source Work" validation becomes "duplicate identical citation".

**Merge behavior.** `MERGE_WORKS` never discards a non-identical citation:

- Pinned rows move with their Manifestation (the owner cascades), keep their
  `manifestation_id`, and cannot collide.
- Work-level rows of the source Work are re-pointed to the target. A row that
  becomes **identical** to an existing row (same work, both Work-level, same
  `pages`) is collapsed, keeping the earlier position. This loses nothing,
  because the rows are equal.
- Any other row is kept, and positions are renumbered in list order.
- Every affected Argument's `argument-sources` revision advances, so an
  offline edit based on the pre-merge list conflicts instead of overwriting
  it (§14.2).
- The merge preview lists each Argument whose citations change.

`research_graph` keeps one Argument→Work edge per `(argument, work)`,
collapsing several citations of the same Work into one edge.

### 8.6 BibTeX compatibility projection

During the migration `/api/bibtex/:id` keeps its URL, which takes a Work ID,
and its output. The generator is split into:

```
citation_record(manifestation_id) -> plain dict (entry type, fields, credits)
bibtex_from_record(record, export_profile) -> str
/api/bibtex/<work_id> = bibtex_from_record(citation_record(citation_target(work_id)))
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
| `manifestation_id` | Owner. Moving an Asset to another Manifestation is an explicit operation. |
| `work_id` | A denormalized copy of the Manifestation's Work, kept equal by the composite FK and by cascade (§4.1). It exists so annotations can prove their owner. |
| `origin_work_id` | The legacy Work whose row this Asset was backfilled or mirrored from. It is immutable, and it maps legacy Work-keyed operations (§11.2, §14.2). NULL for Assets created by new commands. |
| `kind` | `managed_file` \| `external_stream` (D8: no `external_link`). |
| `role` | `document` \| `snapshot` \| `readable_text` \| `attachment` \| `other`. It describes what the content is for the user. PRKS's own materialized export is **not** a role (D3, §9.4). |
| `storage_locator` | For `managed_file`: the managed **basename** of the **working bytes**, the bytes PRKS serves and may rewrite (linearization, annotation materialization). **Never an absolute path**, the same reasoning as `pending_pdf_cleanup`. The legacy `/api/pdfs/<basename>` form stays in the Work projection. |
| `source_locator` | Optional managed basename of the **pristine source bytes**, exactly as ingested and never rewritten. NULL when no original is preserved, which is true of every legacy Asset. |
| `provider`, `provider_id`, `url` | For `external_stream`. The `SET_WORK_SOURCE` aggregate moves here unchanged (I3). |
| `media_type` | `application/pdf`, `text/html`, `video/*` hint, … |
| `byte_size` | Size of the current bytes. Filled at write time, or by the fingerprint pass. |
| `ingest_sha256` | SHA-256 of the source bytes **as first ingested**, before linearization or materialization. Immutable once set. It is the content identity of the lineage. NULL for legacy Assets (§9.4). |
| `content_sha256` | SHA-256 of the **working** bytes at `storage_locator`. Updated whenever PRKS rewrites them. Serves integrity checks and client cache validation. |
| `content_generation` | `INTEGER NOT NULL DEFAULT 0`, set to 0 on creation and by the backfill. It is incremented (`content_generation + 1`) by every in-place rewrite of the working bytes. Because it is never NULL, the compare-and-set can always match. Otherwise `NULL = ?` never matches and `NULL + 1` stays NULL. Hash writers commit only if it is unchanged since they read it (§9.4, §12.4). |
| `origin` | `upload` \| `processing_import` \| `adopted` \| `web_capture` \| `legacy`. |
| `origin_url`, `origin_ref` | Where the bytes came from (a download URL, or a Processing File ID). This is provenance, not a citation. |
| `derived_from_asset_id` | A user-visible relation between **separate** Assets, for example a copy annotated in another tool that the user brought in. It is never used for PRKS's own materialization. |
| `supersedes_asset_id` | "This file replaces that one" within one Manifestation. |
| `state` | `active` \| `trashed` (reserved for #57). Deletion removes the row (§9.2). |
| `thumb_page`, `thumb_url` | Presentation (from `works`). |
| `canonical_annotation_set_revision`, `materialized_pdf_annotation_revision` | Copied from `works` **under the same column names** (§6.1), so there is no third spelling. |
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
  `assets.storage_locator` **and** `assets.source_locator` references, in
  addition to `works.file_path` while both exist. Deleting an Asset claims
  both of its basenames. A claim still stores a basename.
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

### 9.4 Asset identity versus source and working bytes

An Asset is **one lineage**: "this scan", "this publisher PDF", "this
snapshot". Its bytes can change without a new Asset being created:

```
Asset AS-…  (one File in the UI; owns annotations, page state, thumbnails)
 ├─ source slot   source_locator   pristine bytes as ingested     ingest_sha256
 │                                 (optional; never rewritten)
 └─ working slot  storage_locator  bytes PRKS serves and rewrites content_sha256
                                   (linearized; annotations materialized)
```

- **Materialization is derived storage, not a peer Asset** (D3). The
  canonical annotation rows belong to the Asset. The materialized PDF is those
  rows rendered into the working slot. Its freshness is still tracked by the
  canonical and materialized revision pair (I6), now held on the Asset.
- **Pristine originals are permitted, not required.** When `source_locator` is
  set, the working bytes can always be regenerated from it (source bytes →
  linearize → materialize the current annotations). "Download original" then
  returns the source slot. When it is NULL, which is true of every legacy
  Asset and of any Asset ingested while preservation is off, the working
  slot is the only copy, exactly as today. Whether new ingestion keeps
  originals, and under what setting, is a later decision (§18). The
  schema makes it possible without a new entity.
- **The Asset has two hashes because PRKS rewrites the working bytes.**
  Linearization and every annotation save change them (§1.3), so their hash
  cannot be a duplicate key.
  - `ingest_sha256` is the lineage's content identity. It is computed on the
    incoming stream before linearization and compared against for "have I
    already got this file?".
  - `content_sha256` identifies the current working bytes. It is used for
    integrity, backup verification, and client cache validation (#52, #58).
- **Hash updates are crash-safe and race-free.** Replacing bytes on disk and
  updating the hash in SQLite cannot be one atomic step, so every in-place
  rewrite (materialization, linearization, copy-on-write retarget) follows a
  fixed order, **holding the basename's `managed_pdf_path_lock()` for all
  three steps**. The current COW and replace paths already take that lock. The
  fingerprint pass takes the same lock, so it can never hash between step 1
  and step 3:
  1. commit `content_sha256 = NULL`, `byte_size = NULL` and
     `fingerprinted_at = NULL`, and increment `content_generation` (the
     working bytes are **pending**);
  2. replace the bytes durably (`fs_durability`);
  3. hash what was written and commit the new values, with the same
     compare-and-set on `(storage_locator, content_generation)` that the
     fingerprint pass uses (§12.4).

  A crash anywhere in between leaves NULL, meaning "unknown", never a stale
  hash that clients or backup would trust. The fingerprint pass (§12.4) treats
  NULL as work to do and fills it on the next start. Backup verification and
  client cache validation skip, and never trust, an Asset whose hash is NULL.
  `ingest_sha256` is written once, before the first working copy exists, and
  it never changes.
- **Legacy Assets** never had their original bytes preserved, so their
  `ingest_sha256` stays **NULL**. PRKS must not invent it from bytes that have
  since been materialized. Exact-duplicate detection against legacy files uses
  `content_sha256` and is documented as weaker: it matches an unannotated legacy
  file, and misses one whose bytes PRKS has rewritten.
- **When a separate Asset is correct:** a different file the user brings in
  (another scan, another download, a copy annotated elsewhere, a re-captured
  snapshot). Such Assets have their own annotations and page state, and the
  user may relate them with `derived_from_asset_id` or `supersedes_asset_id`.

This does not need a separate "stored blob" entity. Slice A adds the
`source_locator` column (NULL for all backfilled rows) so that preserving
originals later is data, not schema, work.

### 9.5 Backup and restore

- The archive layout is unchanged: the DB plus `files/pdfs/*`. Asset rows live
  in the canonical DB and are covered by it. Preserved source bytes are ordinary
  managed files under `pdfs/`, so they are backed up and audited like working
  bytes. `audit_managed_pdfs` counts both locators. No new filesystem component, so
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
| `MERGE_WORKS(source → target)` | Moves all source Manifestations (with their Assets and annotations) under the target. Unions tags. Folder, playlist and status: target wins unless the user picks. Research/Private Notes: **the user chooses** (keep target, keep source, or concatenate with a visible separator); never silently concatenated. Roles are unioned, with duplicates collapsed. Argument sources and research mentions are re-pointed, and any citation-identity collision is shown in the preview (§8.5). `last_opened_at` is max. | The source `W-…` gets `sync_work_lifecycle(state = merged, target)`. Old links, tabs and `[[W-…]]` **reads** follow the redirect. **Every** pending operation naming the source is refused with `WORK_MERGED` + `target_work_id` and is never applied to the target. The client may re-apply the user's intent explicitly (§14.2). |
| `MOVE_MANIFESTATION(M → Work)` | "This is really a Version of that Work." One `UPDATE manifestations SET work_id` re-keys everything the Manifestation owns by cascade (§4.1): its Assets' and annotations' `work_id`, its scoped roles, and its pinned argument sources. None of these can collide, because each identity includes the unchanged `MF-…` ID. If the Manifestation is a pointer target of the old Work and siblings remain, the pointer is re-pointed to a sibling first. If it is the old Work's **only** Manifestation, the command requires an explicit `empty_source` outcome, `delete` or `merge`, and retires the old Work in the same transaction (§4.1 "Transitions"). No placeholder Version is ever created. Relations to Manifestations of the old Work are dropped, with a preview. | `MF-…` unchanged. |
| `MOVE_ASSET(A → Manifestation)` | "This file is another scan of that edition." It sets the Asset's `manifestation_id` and `work_id` together (the composite FK requires the pair to match). Its annotations keep their `asset_id` and follow the new `work_id` by cascade. If the Asset is its Manifestation's primary, the same transaction re-points `primary_asset_id` to a remaining active sibling, or clears it when none remains. A Manifestation with zero Assets is a valid final state. **On the destination side**, if the destination Manifestation had no active Asset, which means its `primary_asset_id` is NULL, the moved active Asset becomes its primary in the same transaction. Otherwise the destination's primary is unchanged. The deferred `NO ACTION` FK and the command's final-state check verify both sides at COMMIT. | `AS-…` unchanged. |
| `DECLINE_DUPLICATE(a, b)` | Records "not a duplicate". | `duplicate_decisions(entity_type, low_id, high_id, decision, decided_at)`. |

**`MERGE_WORKS` transaction order.** The §4.1 constraints depend on this
order, and all steps are one transaction:

1. Insert `sync_work_lifecycle(source, 'merged', target)` and
   `work_retirement(source)` (§4.1 "Transitions").
2. Release the source Work's primary and citation pointers. The trigger allows
   this only because of the retirement marker.
3. **Freeze inherited values, then move.** Before the move, each moved
   Manifestation whose `title` or `abstract` override is NULL, and so
   inherits from the source Work, gets the **source Work's current value**
   written as an explicit override. This happens only where that value
   differs from the target Work's, and never as `''`. The Version's
   displayed and cited title and abstract therefore don't silently change to
   the target's. This is the same rule as credit names in step 4. The preview
   lists these, and the user may instead let a Version inherit the target's
   values. Then `UPDATE manifestations SET work_id = target` for the moved
   Manifestations. The owner columns of their Assets, annotations, scoped
   roles and pinned argument sources follow by cascade.
4. Re-point Work-level rows to the target:
   - tags;
   - Work-scoped roles;
   - Work-level argument sources;
   - `processing_files.imported_work_id`, so that re-importing an
     already-imported file stays idempotent and is not orphaned by the source
     row's `ON DELETE SET NULL`.

   Only exactly identical citation rows are collapsed (§8.5). **Role
   collisions never lose a credit.** If the source and the target both have a
   Work-scoped `(person, role_type)` with *different* `credit_name` values, the
   target's row is kept, and each moved Manifestation keeps its **effective
   pre-merge credit**. A moved Manifestation that already has its own
   override for that `(person, role_type)`, for example from an earlier
   merge, keeps it unchanged. Only moved Manifestations *without* one get a
   new override carrying the source Work's spelling. No Version's displayed or
   cited credit changes. The preview shows
   every such collision and lets the user pick a single spelling instead.
   Identical rows are collapsed. The user's choices from the preview are
   applied.
5. Delete the source Work row, which also removes its retirement marker, and
   tombstone its sync scopes.
6. Advance the revisions the merge changed, then commit. The deferred FKs are
   checked at this point.

A failure at any step rolls back the whole merge.

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
upper-case hex. This format matches `entity_ids.is_distributed`. So the
migration is **reproducible**: restoring the same pre-migration backup twice,
or migrating two copies, yields identical IDs.

**Legacy mapping is stored, not recomputed.** Rows created after the migration
by Slice A's mirror trigger get random IDs (`'MF-' || hex(randomblob(16))`),
because a SQL trigger cannot compute uuid5. So the mapping from a legacy
`work_id`-keyed operation to "the Manifestation or Asset it meant" is kept in
an immutable column instead:

```
origin_MF(W) = the manifestation WHERE origin_work_id = W   (UNIQUE, immutable)
origin_AS(W) = the asset         WHERE origin_work_id = W   (UNIQUE, immutable)
```

Every backfilled or mirrored row carries its `origin_work_id`. Rows created
later by new canonical commands leave it NULL, because no legacy operation can
refer to them. The mapping survives the Work gaining more Versions, and it
survives moves: `origin_work_id` does not change when the row moves. So a
legacy operation for `W` finds `origin_MF(W)`, sees that its current `work_id`
is no longer `W`, and is refused (§14.2).

Clients must **never derive** these IDs themselves. The server returns them.

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
before (v16)                          after the Slice A migration
────────────                          ───────────────────────────
works W-1                             works W-1  (Work-owned columns authoritative;
  title, status, notes, tags…           legacy bibliographic/source columns still present)
  doi, publisher, year, …               primary_manifestation_id = MF-uuid5(W-1)
  source_mime (maybe set)               citation_manifestation_id = NULL (= primary)
  file_path=/api/pdfs/x.pdf               │
  canonical/materialized revs             ▼
annotations(work_id=W-1)              manifestations MF-uuid5(W-1)   origin_work_id = W-1
roles(W-1, Translator)                  work_id=W-1, kind='unspecified', title=NULL (inherit)
argument_sources(A, W-1, '45')          doi, publisher, year, … (copied)
                                        primary_asset_id = AS-uuid5(W-1)
                                          │
                                          ▼
                                      assets AS-uuid5(W-1)           origin_work_id = W-1
                                        work_id=W-1, kind='managed_file', role='document',
                                        storage_locator='x.pdf', source_locator=NULL,
                                        media_type=COALESCE(source_mime, 'application/pdf'),
                                        origin='legacy', ingest_sha256=NULL,
                                        content_sha256=NULL (fingerprint pass fills),
                                        content_generation=0,
                                        materialization revisions (copied), thumb_page (copied)
                                      annotations(…, asset_id = AS-uuid5(W-1))      rebuilt
                                      roles(…, manifestation_id = NULL)             rebuilt;
                                        scope set later by the role-scope migration
                                      argument_sources(A, 0, W-1, MF-uuid5(W-1), '45')  rebuilt
```

The backfill rules:

- A **video** Work gets an `external_stream` Asset carrying `provider`,
  `provider_id`, `source_url` and `thumb_url`.
- **Asset-creation predicate.** A Work gets an Asset when it has **any**
  Asset-owned value: a `file_path`, video identity, annotations, a non-empty
  `source_mime`, `thumb_url` or `thumb_page`, or a non-zero materialization
  revision. It also gets one when it has **any durable revision state in a
  scope that will become Asset-owned**, even if every current Asset-owned
  value is empty:
  - a `sync_entity_revisions` row for `work-field/[W, thumb_page]` (and any
    other Work field scheduled to become Asset-owned);
  - a `pdf-annotation/[W, …]` row, including tombstones of deleted
    annotations;
  - a `work-source/W` row.

  Tombstones and queued legacy operations must always be able to map to a
  stable `origin_AS(W)`, and minting that Asset late in Slice D would make the
  mapping appear late and lose deterministic identity. `POST /api/works` / `add_work()` accept `source_mime`,
  `thumb_url` and `thumb_page` without a file, so metadata-only rows can
  carry them. Those values need a canonical home when Asset authority lands,
  or JSON parity would break.
- A Work that meets the predicate but has **no usable file or stream
  reference** gets a `managed_file` Asset with a NULL `storage_locator`. It
  is a placeholder that owns those values **and those revision scopes**.
- **After Slice A the rule keeps holding.** The mirror triggers create
  `origin_AS(W)` the first time a Work without one gains any Asset-owned value
  or annotation. That is necessarily before any Asset-bound revision or
  tombstone can exist for it, because a tombstone can only follow a value.
  Clearing values later never deletes the Asset. The projection shows it exactly as
  today (`file_path` empty: "No file attached"), and none of its values or
  annotations are dropped or left without an owner.
- A Work with **none** of these values gets a Manifestation and **no** Asset.
- A non-video `source_url` goes to `manifestations.url`.
- **MIME type:** if `works.source_mime` is non-empty, `assets.media_type` gets
  **that exact value**. `application/pdf` is inferred only when it is absent
  and the Asset is a managed PDF. Otherwise `media_type` is NULL. Existing MIME
  metadata is never discarded or overwritten.
- A **legacy inferred-video row** (kind NULL, no file, a URL) is classified with
  `effective_source_kind()`, the same rule every reader uses. When the URL
  cannot be parsed, the row gets no Asset and keeps its URL on the
  Manifestation. Nothing is guessed (see work-source-identity.md §10).
- `argument_sources` rows are renumbered and pinned as described in §8.5.

### 12.3 Order of migrations

The versions below are illustrative. Each is one migration in one PR (see
§17).

1. **vN (Slice A): create the entities and the integrity layer.** In one
   transaction:
   1. **Preflight and quarantine.** Some legacy DBs contain rows in the three
      leaf tables that already violate one of their FKs: for example an
      annotation or role whose Work no longer exists, or a role whose Person
      does not. Backup already warns about these as `fk_violations`. They
      **cannot be copied** into the rebuilt tables, because with
      `foreign_keys = ON` the retained single-column FKs (`work_id → works`,
      `person_id → persons`, …) are immediate constraints. Making only the new
      columns NULL does not exempt a row, and deferring the check would only
      move the failure to commit. So these rows are **quarantined**, never
      dropped:
      - They are identified with `PRAGMA foreign_key_check(<table>)` before
        the rebuild.
      - Each one is written verbatim, as `json_object(...)` of all its columns,
        into a new FK-free canonical table `migration_quarantine(id,
        source_table, source_rowid, row_json, reason, quarantined_at)`.
      - The rebuild copy then skips them.
      - **Revisions follow the live aggregate that changes, not the table
        type.** For each quarantined row, PRKS advances the revision of every
        scope whose state endpoint reported that row as present:
        - an `argument_sources` row whose Argument still exists: advance
          `argument-sources/<A>` (§8.5);
        - a `roles` row whose **Work still exists** (for example only its
          Person is missing): `work_role_sync.get_roles_state()` builds
          `present` from `roles` without joining `persons`, so that row was
          reported as `present: true`. Insert or advance the
          `work-person-role/[W, P, role]` revision, leaving a tombstone. After
          the migration the scope reports absence at a strictly newer
          revision;
        - a row whose owning Work or Argument no longer exists (annotations
          can only be orphaned this way): no live scope reported it, so no
          scope is created.
      - Only per-table counts are logged, which is privacy-safe.

      The table lives in `prks_data.db`, so it is backed up with the library.
      No owner is invented and the upgrade is not blocked. A later repair or
      "reattach" tool can restore a row if its owner reappears. Any other row
      that cannot be mapped (none are expected) **aborts** the migration with
      a precise reason code, and the transaction rolls back.
   2. Create `manifestations`, `assets`, `manifestation_relations` and
      `manifestation_identifiers`, with the parent pairs and composite FKs of
      §4.1.
   3. Add `works.primary_manifestation_id` and `works.citation_manifestation_id`
      with `ALTER TABLE ADD COLUMN`.
   4. Rebuild the leaf tables `annotations`, `roles` and `argument_sources` with
      their new columns and composite FKs (create new, copy, drop old, rename).
      Their indexes are recreated, and `argument_sources` gets its new key
      (§8.5).
   5. Backfill deterministically (§12.2).
   6. Install the §4.1 triggers and the **mirror triggers**. `works` stays
      authoritative for everything, and on INSERT/UPDATE the mirror triggers
      keep `origin_MF(W)` and `origin_AS(W)` current. They also fill
      `annotations.asset_id` and pin `argument_sources` rows with pages when
      legacy code writes them without the new columns, reproducing the
      backfill rules. The child FKs cascade on DELETE.
   7. Run the integrity query and `PRAGMA foreign_key_check`. The rebuilt
      tables must report **no** violations, because their orphans were
      quarantined. The other tables must report no violation that the
      preflight did not see. Either check failing aborts the migration.

   **No reader changes.** No filesystem work. D2 applies: no automatic
   full-library backup is made or required.
2. **vN+1: Asset authority.** Asset-owned state (locator, source aggregate,
   materialization revisions, thumbnail page, annotation ownership) becomes
   authoritative on `assets`, and `annotations.asset_id` becomes NOT NULL. The
   migration drops those mirror triggers and installs the reverse projection
   (`assets` → `works.file_path` and the other legacy columns) for readers not
   yet migrated. Only one direction exists at any version. The text index moves
   to `asset_id` keys, which is derived and rebuilt, not migrated.
3. **vN+2: Manifestation authority.** The same move for bibliographic fields.
   The migration **copies revision counters** from `work-field/[W, f]` to
   `manifestation-field/[origin_MF(W), f]` (§14.2).
4. **vN+3: Role scope.** Set role scope by role type (§7.3), and swap the
   uniqueness index to include `manifestation_id`. Then copy **every**
   `work-person-role/[W, P, r]` revision scope whose role type is
   edition-scoped (Editor, Translator, Introduction, Foreword, Afterword) to
   `manifestation-person-role/[origin_MF(W), P, r]`. This includes
   **tombstones**, meaning scopes with a revision but no live row, which
   `work_role_sync.get_roles_state()` deliberately keeps. Without them, an
   offline add based on revision 0 could resurrect a deleted role.
5. **Later: retire legacy columns.** This is decided later (D10). Until then the
   columns stay as the maintained projection.

### 12.4 Fingerprint pass (not a migration)

This is a bounded, resumable pass modeled on `retry_pending_pdf_cleanup`: a
fixed number of Assets per run, at startup or on demand. It streams each
managed file through SHA-256, records `content_sha256`, `byte_size` and a
`fingerprinted_at`, and skips missing files, which are reported, never
treated as empty (the same rule as the text index). It never modifies bytes.

The pass must never record a stale digest while PRKS rewrites the same file
(materialization, linearization, copy-on-write retarget):
- Each in-place rewrite increments `assets.content_generation` in the same
  commit that NULLs the hash (§9.4).
- The pass reads `(storage_locator, content_generation)` and hashes the bytes
  under the basename's `managed_pdf_path_lock()`, the lock the rewrite and
  copy-on-write paths already take.
- It commits with a compare-and-set:
  `UPDATE assets SET content_sha256 = ?, … WHERE id = ? AND storage_locator = ?
  AND content_generation = ?`.
- If the locator or generation changed in between, the update matches no row,
  the digest is discarded, and the Asset stays pending for the next pass.
It stores hashes in the canonical DB because they describe canonical bytes, and
they are re-verifiable, so a restore can recompute them.

### 12.5 Rollback and recovery

- **The upgrade is fail-safe by transaction, not by backup (D2).** Each
  migration runs in one `BEGIN IMMEDIATE` transaction
  (`_run_in_transaction`). A failed preflight, constraint, integrity query or
  crash rolls back to the previous version with the library untouched. Slice A
  performs no filesystem mutation, so there is nothing outside SQLite to undo.
- **No automatic full-library backup.** Slice A does not create or require a
  `.prks-backup`. Packing a large PDF library at startup could double storage
  use and fail under storage pressure. An automatic upgrade snapshot that
  copies only the DB, proportional to the change, may be evaluated separately.
  It is not part of this design.
- PRKS has **no down migrations**, and this design does not add any. A newer
  schema is refused by older code and by restore (`schema_newer`). Undoing a
  *committed* upgrade therefore means restoring a backup the user already
  made, the same as for every existing migration.
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
| `PATCH /api/works/:id` with a Work-owned field | the Work, except `title` and `abstract`, below |
| … with `title` or `abstract` | **the value the legacy projection is showing.** If the primary Manifestation has an override for that field (never `''`, by CHECK), the edit writes the override and advances `manifestation-field/[MF, f]`; otherwise it writes the Work's canonical value and advances `work-field/[W, f]`. The response shows the edit, and no hidden fallback changes silently. **An empty value on the override path clears the override back to inherit (NULL).** The legacy shape has no other way to say "inherit", and storing `''` would make a blank Version title. Changing the *canonical* value while an override exists, or choosing inherit versus value explicitly, needs the Version-aware API (§13.3). |
| … with a Manifestation-owned field | the **primary** Manifestation. The editor shows the primary, so this is what the user sees. The response names the `manifestation_id` it wrote. |
| … with `source_url` on a **non-video** Work (provenance/citation URL, a `SET_WORK_METADATA_FIELD` value) | the primary **Manifestation**'s `url`. This works whether or not the Work has an Asset. The existing video guard stays (work-source-identity.md). |
| `SET_WORK_SOURCE` / video source identity (`source_kind`, `provider`, `provider_id`, `source_url` on a video Work) | the primary Asset's `external_stream` aggregate (`SET_WORK_SOURCE` semantics unchanged) |
| `POST /api/works` | creates Work + Manifestation in one transaction, plus an Asset **only when the §12.2 Asset-creation predicate holds** (a file, video identity, `source_mime`, `thumb_*`, …). A notes-only or metadata-only Work gets zero Assets, matching §4 and the backfill, so `asset_count` never reports a File or Source that doesn't exist. |
| `POST /api/works/:id/pdf` (materialization) | the **working slot** of the primary Asset (§9.4). The client must send `asset_id` once more than one exists. Without it the request is refused (`ASSET_AMBIGUOUS`) rather than guessed. |
| Annotation endpoints | the annotation's own Asset. A new annotation needs the displayed `asset_id`, which defaults to the primary only while exactly one PDF Asset exists. |
| `/api/bibtex/:id` | `citation_record(citation_target(work))`, which is the primary while `citation_manifestation_id` is NULL (§8.3, §8.6) |
| Argument sources `[{work_id, pages}]` (HTTP and `argument-sources` sync) | kept pins are preserved. Otherwise a new entry is pinned to the only Manifestation, or refused with `ARGUMENT_SOURCE_VERSION_REQUIRED` when the Work has several (§8.5) |

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
- UI words (D1): a Manifestation is a **Version**. An Asset is a **File** when
  it is a managed file, and a **Source** or stream when it is external (for
  example "YouTube source"). PRKS's own materialized PDF is never shown as a
  separate File (D3). "Manifestation", "Asset" and "FRBR" stay out of the UI.

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

**Scopes follow ownership, and revisions are carried, not reset.** Every
copy iterates over the **`sync_entity_revisions` scope rows**, never over live
entity rows. Tombstones (a revision with no live row: a deleted annotation, a
removed role, a cleared field) are copied like any other scope, so a stale
device cannot resurrect what was deleted. The old scope row is kept, and legacy
operations keyed by it map through `origin_MF(W)`/`origin_AS(W)` as described
below.

| Current scope | Future scope | Migration |
| --- | --- | --- |
| `work-field/[W, f]` for M-owned `f` | `manifestation-field/[MF, f]` | Copy the revision to `origin_MF(W)` in the authority migration. |
| `work-field/[W, f]` for W-owned `f` (status, title, abstract, author_text) | unchanged | — |
| (new) `manifestation-field/[MF, title\|abstract]` for overrides | new scope, revision 0 until first written | Overrides are created only by the Version-aware API. A **durable** legacy `SET_WORK_METADATA_FIELD(W, title\|abstract)` carries a base revision for `work-field/[W, f]`, which says nothing about the override. So while the primary Manifestation has an override for that field, the operation is **refused** with `FIELD_OVERRIDDEN_BY_VERSION` rather than routed. Online legacy `PATCH` follows §13.2 and advances the override's scope. |
| `work-source/W` | `asset-source/AS` | Copy to `origin_AS(W)`. |
| `work-field/[W, thumb_page]` | `asset-field/[AS, thumb_page]` | Copy every scope row, tombstones included, to `origin_AS(W)`. A legacy `SET_WORK_METADATA_FIELD(W, thumb_page)` maps through `origin_AS(W)` like the other legacy operations. The same rule applies to any other Work field that becomes Asset-owned. |
| `pdf-annotation/[W, ann]` | `asset-annotation/[AS, ann]` | Copy to `origin_AS(W)`. Deletion tombstones are copied too, so resurrection protection survives. |
| `work-person-role/[W, P, r]` | unchanged for W-scope; `manifestation-person-role/[MF, P, r]` for M-scope | Copy every scope row of an edition-scoped role type to `origin_MF(W)`, **tombstones included**. |
| notes, tags, folder, playlist, open | unchanged | — |

**Legacy operations after the move.** A pending `SET_WORK_METADATA_FIELD(W,
doi, base=7)` enqueued before the upgrade is applied to
**`origin_MF(W)`**, the Manifestation the user was editing when they enqueued
it, **not to "whatever is primary now"**. The mapping is looked up through
the stored `origin_work_id` (§11.2), never recomputed. The operation is never
retargeted:

| State of `W` / `origin_MF(W)` at apply time | Result |
| --- | --- |
| `W` live, `origin_MF(W).work_id = W` | applied with the carried revision |
| `W` merged away | `409 WORK_MERGED` + `target_work_id` (below) |
| `origin_MF(W)` moved to another Work outside a merge | `409 MANIFESTATION_MOVED` + the current `work_id` |
| `origin_MF(W)` deleted | the family's existing deleted/absent result |

The same rule applies to annotation operations (→ `origin_AS(W)`) and to
`SET_WORK_SOURCE`.

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
do not. `sync_tag_lifecycle` already follows this principle: `TAG_MERGED` is a
refusal, not a redirect. So:

- **Every pending operation that names a merged-away `W-…` is refused** with
  `409 WORK_MERGED` and `target_work_id`. The server never applies it to the
  target, whatever the operation is. This covers Work fields, notes, status,
  tags, folder, playlist, Work-scoped roles, argument sources naming the
  Work, and legacy Work-keyed operations mapped through `origin_MF(W)` or
  `origin_AS(W)`.
- **The client reconciles explicitly.** It keeps the user's intended value and
  presents a merge conflict ("this Work was merged into …; apply your change
  there?"). If the user accepts, the client sends a **new** operation against
  the target's current revision. The one operation that needs no question is
  `MARK_WORK_OPENED`, which is a max-register with no base revision. The client
  may re-issue it for the target automatically, and that is still a new,
  explicit operation, not a server-side redirect.
- **Operations that name a moved Manifestation or Asset by its own ID continue
  normally.** Their scopes are keyed by `MF-…`/`AS-…`, the IDs are unchanged by
  the merge, and so their base revisions are still meaningful.
- **The merge does not rebase or combine revision counters.** Source Work
  scopes are tombstoned, so a stale device cannot resurrect them. Target scopes
  advance for whatever the merge changed, including the `argument-sources`
  revision of every Argument whose list changed (§8.5), so older edits
  conflict instead of overwriting.
- **Any future rebase of pending Work mutations during a merge** must be an
  explicit merge decision with defined revision reconciliation, shown in the
  merge preview. It is never implicit redirect behavior, and it is not part of
  this design.
- **Reads** (links, tabs, `[[W-…]]`, cached detail requests) follow the redirect
  to the target. A read cannot accept stale state.

---

## 15. Downstream roadmap implications

These are the constraints each epic should adopt, so that none of them invents
its own identity or asset model.

| Issue | Implications |
| --- | --- |
| **#41 Citation V2** | Cite a **Manifestation**. Default to `citation_target(work)` = `COALESCE(citation_manifestation_id, primary_manifestation_id)`, and own the UI for choosing the citation Version (D4). Batch export resolves each Work to exactly one Manifestation. Build CSL-JSON from `citation_record(manifestation_id)` (§8.6), not from `works` columns. Decide cite-key persistence (§8.4) and the `urldate` quirk. Do not add citation fields to `works`. |
| **#42 Ingestion / Web Works** | Every ingest creates, or attaches to, Work → Manifestation → Asset through one canonical command. Web Work = Manifestation(`web_page`) + snapshot Asset(s) under a new managed area, classified as canonical in the backup inventory. Duplicate warnings use §10 (hash, then identifiers, then optional RapidFuzz ranking). Provenance attaches to Manifestation fields and Asset origin. BibTeX/RIS import creates Manifestations and uses identifier normalization. |
| **#57 History / Trash** | Trash granularity is Work, Manifestation, or Asset. Asset `state = trashed` keeps bytes until purge, and purge uses the `pending_pdf_cleanup` rules. Merge and move are history events with previews. `sync_work_lifecycle` redirects are the durable record of merges. |
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
   Manifestation of a different Work in another. The stored, immutable
   `origin_work_id` (§11.2) gives the same compatibility without the ambiguity.
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
13. **PRKS's materialized annotated PDF as its own peer Asset.** This would
    double every annotated file in the UI and split one lineage's annotations,
    page state and duplicate identity across two Assets. **Rejected (D3).**
    Materialization is derived storage inside the Asset (§9.4).
14. **A stable surrogate ID per `argument_sources` row.** Every list
    replacement rewrites the rows, and nothing addresses a single citation.
    **Rejected for now.** The row key is the list position, and citation
    identity is `(work, Version, pinpoint)` (§8.5).
15. **Composite ownership FKs on `works` via a table rebuild.** `works` is the
    parent of about ten tables. Migrations run in one transaction with foreign
    keys on, so dropping the old table would cascade-delete child rows.
    **Rejected** in favor of narrow pointer triggers (§4.1). The leaf tables
    do get composite FKs.
16. **Application-only ownership validation.** A long-lived compatibility
    layer needs the database as the final guard. **Rejected (D11-D).**
17. **An automatic full-library backup before migrating.** It can double
    storage use and fail under storage pressure, and a transactional,
    SQLite-only, additive migration does not need it. **Rejected (D2).**

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
| **A. Entities + integrity layer + deterministic backfill + mirror triggers** | Migration vN (§12.3 step 1), with `db_schema.sql` updated to match. **Acceptance criteria:** (1) fresh and upgraded DBs have the same schema, including the composite FKs and triggers of §4.1, and `validate_current_schema` checks them; (2) a negative test for each §4.1 invariant (`works` primary and citation pointers; Manifestation primary Asset; roles, argument sources and annotations owners; cross-Work, mismatched-`work_id` and self `manifestation_relations`; moving one related endpoint without dropping the relation fails at COMMIT, while moving both in a merge keeps it); (3) legal-move cascade and pointer-target restrict tests, plus the §4.1 transition tests (move the only primary Asset out; move a non-primary Asset; move the only Manifestation out with the emptied Work deleted or merged; a leftover retirement marker fails COMMIT; the direct guard and marker bypasses are refused; rollback leaves all pointers and owner columns unchanged); (4) the integrity query is empty, and `PRAGMA foreign_key_check` adds no new violations after the backfill; (5) `source_mime` preserved; (6a) Works whose only Asset-bound state is durable revision state get exactly one deterministic placeholder `origin_AS(W)`: `thumb_page` set and then cleared (only the revision tombstone remains), an annotation deleted with no live annotation left, and a Work with a `work-source/W` revision whose video identity has since been cleared. `SET_WORK_SOURCE` refuses clearing a video, but the ordinary `PATCH` still accepts the source columns unvalidated (work-source-identity.md §2), so this state is reachable. A Slice D test then copies those tombstones to the Asset scopes, and queued legacy operations, including a `SET_WORK_SOURCE`, map to that same Asset ID; (6) for Arguments with no quarantined row, the `argument_sources` rebuild renumbers without changing the observed list or its revision, and pins rows that have pages. A fixture that loses an orphan citation shows the survivors renumbered and the `argument-sources` revision advanced. Fixtures for a live Work, a missing Person and a role row, **with and without** an existing role revision, show `get_roles_state()` reporting that scope as absent at a strictly newer revision; (7) a parity test (legacy columns = the new rows) over fixture libraries covering video, inferred-video, no-file, annotations-without-file, shared-basename rows, metadata-only rows with `source_mime`/`thumb_*`, and quarantined legacy FK-orphan rows; (8) no filesystem access and no backup creation. No readers change. | No | this design |
| **B. Asset fingerprint pass + ingest hashing** | Bounded, resumable hashing pass (§12.4). Compute `ingest_sha256` on the upload/import stream **before** linearization, for new Assets. No duplicate UI yet. | No | A |
| **C. Projection module** | `work_projection.legacy_work` / `legacy_work_summary`. Move readers family by family (detail, browse/recent, folder/person/playlist summaries, BibTeX via `citation_record`), each with JSON parity tests and a byte-identical BibTeX test. | No | A |
| **D. Asset authority** | vN+1: locator, source aggregate, materialization revisions and `thumb_page` owned by `assets`. `annotations.asset_id` authoritative and NOT NULL. Text index keyed by Asset. Cleanup and backup audit count Asset locators. Scope and revision copies (`asset-source`, `asset-annotation`, `asset-field/[AS, thumb_page]`), tombstones included. Legacy operations mapped to `origin_AS(W)`. Browser last-page key migration. | No | C |
| **E. Manifestation authority** | vN+2: bibliographic fields owned by `manifestations`. `manifestation-field` scopes with carried revisions. `SET_WORK_METADATA_FIELD` mapped to `origin_MF(W)`. The `argument_sources` writer and sync accept `manifestation_id`, replacing the mirror trigger, and return `ARGUMENT_SOURCE_VERSION_REQUIRED` (§8.5). Identifier table populated from DOI/ISBN. | No | C |
| **F. Role scope** | vN+3: role-type scope defaults, the uniqueness swap, `credits(M)`, and the role-sync scope for M-scoped roles. | No | E |
| **G. Typed Version/File/Source API** | §13.3 read endpoints first, then mutations as canonical domain commands (#182/#199 pattern) with OpenAPI and openapi-core tests. New durable families declared per the sync guide. | API only | D, E, F |
| **H. Versions UI** | "Add another version / file", set primary, open a specific Version or File, and a version picker for Copy citation, all behind §13.4's progressive disclosure. Follow `DESIGN.md` for Work detail composition. #61's secondary view builds on this. | **Yes** | G |
| **I. Exact-duplicate warning at ingestion** | Uses `ingest_sha256` (and `content_sha256` for legacy files). Offers the four choices in §10.2. | Yes | B, H |
| **J. Identifier candidates + decline** | Normalized DOI/ISBN/arXiv candidates, a review list, and `duplicate_decisions`. | Yes | E, I |
| **K. Merge / move workflow** | Command-level versions of the §4.1 transition tests, including each `empty_source` outcome. Ships `manifestation_credit_overrides` (§7.2) if it does not exist yet, and tests a credit-spelling collision. `MERGE_WORKS` (in the §10.4 transaction order), `MOVE_MANIFESTATION` and `MOVE_ASSET` with previews, `sync_work_lifecycle` read redirects, and `WORK_MERGED` refusals plus client reconciliation (§14.2). Coordinate with #57. | Yes | J |
| **L. RapidFuzz evaluation** | A separate research/evaluation issue (dependency review, ranking quality on synthetic data). It only ranks; it never decides. | — | J |

A, B and C can proceed in parallel after A's migration merges. D and E are
the risky slices, because they move durable-operation scopes. Each should run
the affected sync feature E2E groups before merge, and the full E2E gate once.

---

## 18. Remaining open questions

The maintainer's earlier questions are answered in §0 (D1–D10). What remains
is listed below. **None of it blocks approving the design or starting Slice A.**
Items 1–3 are choices this revision made that should be confirmed during
review.

1. **Work-level citations may carry a locator** (§8.5). This allows
   edition-independent locators such as Stephanus or Bekker numbers, which is
   why citation identity includes `pages`. The alternative, "pages always pins
   a Version", is simpler, but it would force classical locators onto one
   edition.
2. **Legacy FK-orphan rows are quarantined, not blocking** (§12.3 step 1.1).
   Rows that already violate an FK cannot be copied into a rebuilt table, so
   they are kept verbatim in `migration_quarantine`. The alternative is to
   make the upgrade refuse to run on such a DB.
3. **Slice A rebuilds `roles` and `annotations`** as well as
   `argument_sources`, so the composite FKs exist from the first migration. The
   alternative is to add the columns without FKs in Slice A and rebuild later,
   which leaves a window without DB enforcement. The recommendation is to
   rebuild in Slice A as specified.
4. **The policy for preserving pristine originals.** The schema allows it
   (`source_locator`, §9.4). When new ingestion should keep originals, and
   behind what setting, is decided later. It does not affect Slice A.
5. **A proportional upgrade snapshot that covers only the DB** (D2). This may be
   evaluated separately. It is not part of any slice here.
