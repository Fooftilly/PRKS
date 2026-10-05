# Storage location and Asset storage backends: design (#311)

**Status: Phase A (contract and foundation) implemented; Phases B–F are
proposed.** This is the design gate for #311, under the #310 target
architecture. It settles the storage-location and physical-object contract
that #60's Asset work consumes. The phases in
[§13](#13-implementation-phases) become focused implementation issues. How
Phase A maps onto the code, and the choices it made inside this design's
latitude, are recorded in [§18](#18-phase-a-implementation-record).

It follows the shape of [work-identity-model.md](work-identity-model.md): audit
the code first, then decide. Every claim about current behavior was checked
against `master` at `6ed603f` (schema version 17), not taken from issue text.

**Ownership split, stated once:**

- **#60 owns identity and meaning.** It owns the Work, Manifestation and Asset
  entities, Asset IDs, hashes, provenance, lifecycle state, deduplication and
  bibliographic semantics ([work-identity-model.md](work-identity-model.md)
  §3.3, §9, §10).
- **#311 (this document) owns location and physical objects.** It owns where
  the bytes live, how a location is chosen, validated and moved, how a storage
  key maps to a physical object, and what a storage backend must guarantee.

This document introduces no competing Work, Manifestation or Asset model. Where
it needs an Asset attribute, it names the #60 column.

Contents:

0. [Decisions](#0-decisions)
1. [Current-state audit](#1-current-state-audit)
2. [Terminology](#2-terminology)
3. [Relationship to #60](#3-relationship-to-60)
4. [Data classification and local layout](#4-data-classification-and-local-layout)
5. [Configuration ownership and precedence](#5-configuration-ownership-and-precedence)
6. [Platform defaults](#6-platform-defaults)
7. [Root identity and validation](#7-root-identity-and-validation)
8. [Relocation and rebinding](#8-relocation-and-rebinding)
9. [PostgreSQL relationship](#9-postgresql-relationship)
10. [The StorageBackend boundary](#10-the-storagebackend-boundary)
11. [Path-sensitive systems](#11-path-sensitive-systems)
12. [Multi-process semantics](#12-multi-process-semantics)
13. [Implementation phases](#13-implementation-phases)
14. [Deferred frontend and API work](#14-deferred-frontend-and-api-work)
15. [Rejected alternatives](#15-rejected-alternatives)
16. [Open questions](#16-open-questions)
17. [Conformance checks](#17-conformance-checks)
18. [Phase A implementation record](#18-phase-a-implementation-record)

---

## 0. Decisions

| # | Topic | Decision |
| --- | --- | --- |
| S1 | Identity | A physical host path is **never** domain identity. Domain records hold an Asset ID (`AS-…`, #60) and the Asset holds a **storage key**. Only a storage backend turns a key into a physical object. |
| S2 | Storage key | A storage key is a backend-neutral, relative `(namespace, name)` pair. The name is one validated segment, issued by the storage layer and never built by callers from user input. #60's `assets.storage_locator` and `source_locator` already store the **name** of a key in the managed-object namespace. No migration is needed to adopt this. |
| S3 | Namespaces | One namespace, `asset-objects`, holds every managed Asset slot, whatever its media type: PDFs today, and web snapshots and attachments later. A namespace is never derived from a mutable domain attribute such as `assets.role`. In layout 1, `LocalFilesystemStorage` maps `asset-objects` to the existing `pdfs/` directory. |
| S4 | Local backend | `LocalFilesystemStorage` over a user-selected **data root** stays a permanent first-class backend. Object storage is optional and deployment-driven. |
| S5 | Bootstrap config | The selected root is stored in a **bootstrap configuration file** outside every data root, in the platform configuration directory. It is never stored in a database row inside the library it locates. |
| S6 | Precedence | CLI `--storage-root`, then `PRKS_STORAGE`, then the bootstrap config file, then the platform default. The first source that is set wins completely, and PRKS reports which one it used. Testing mode never reads the bootstrap file or the platform default. |
| S7 | Defaults | Packaged installs default to the platform's per-user application-data location. A source checkout keeps repository `data/` as its development default. A deployment chooses one through a distribution flag, never through a guess. |
| S8 | Root identity | Every data root carries a small marker file, `prks-root.json`, with a `storage_root_id` and a `layout_version`. The ID identifies the library's file store: it is minted for a new root and **carried** by relocation. PRKS refuses to bind a root whose marker is missing (except when it creates or adopts a root), unreadable, retired, fenced (a relocation source), still staging (a relocation destination), or foreign, unless it can prove how that relocation ended. At most one root per ID is ever bindable. During P1 the source remains the only bindable root. From the P2 fence until the move resolves, none is. After PostgreSQL arrives, the database also records the `storage_root_id` it belongs to. |
| S9 | Relocation | A move is **fence, copy, verify, commit, activate, retire the source, rebuild, retain**. There is exactly one commit point per move, and where it lives depends on who selects the root. For a config-file root it is the atomic replace of the bootstrap config file (§8.2 P5). For a CLI- or environment-selected root it is the atomic write of `relocation.phase: committed` into the destination marker (§8.7), because there the bootstrap file is not the selector and may be ephemeral. From the fence onward neither end of the move is bindable by any other process. The old library's data is never written; before commit only its marker changes (the fence). It is durably revoked by the fence **before** the destination becomes bindable, and it is never deleted except by a later explicit user action. The first implementation copies; it never renames destructively. |
| S10 | Env-managed roots | When the CLI or the environment chooses the root, PRKS shows it read-only and offers no in-app move. That deployment is moved by its administrator, through an offline CLI tool. |
| S11 | Relational store | The data root holds file-backed data. In the SQLite era the database is one canonical component inside it. With PostgreSQL, database placement is connection configuration, and choosing or moving the data root never moves the database. |
| S12 | Backend interface | It is small and blob-shaped: exclusive create, atomic replace, open for read, stat, idempotent delete, verify, and list for maintenance only. It is not a repository framework. Absolute paths are available only through a local-only extension that filesystem-specific infrastructure uses. |
| S13 | Multi-step invariants | The backend guarantees single-operation atomicity. A multi-step invariant, such as reference-check-then-delete or hash compare-and-set, is coordinated above the backend by the catalogue transaction and a **key-scoped lock**. That lock is process-local today and must become cross-process before PRKS runs more than one process. |

---

## 1. Current-state audit

### 1.1 What exists

`backend/storage/config.py::StorageConfig` is a frozen snapshot, built once by
`StorageConfig.from_env()` in `prks_app.py`, before `recover_incomplete_restore()`
and `bind_storage()`. Its fields are `mode`, `configured_root` (the raw
`PRKS_STORAGE` value), `root`, `db_path`, `pdfs_dir`, `thumbs_dir`,
`people_dir`, `processing_dir`, `index_db_path`, `research_index_db_path`,
`log_file` and `processing_fallback_allowed`.

`backend/storage/paths.py` derives them:

| Component | With `PRKS_STORAGE=R` | Unset, production | Unset, testing |
| --- | --- | --- | --- |
| root | `R` | `<repo>/data` | `<repo>/data_testing` |
| database | `R/prks_data.db` | `<repo>/data/prks_data.db` | `<repo>/data_testing/prks_data_testing.db` |
| managed PDFs | `R/pdfs` | `<repo>/data/pdfs` | `<repo>/data_testing/pdfs` |
| thumbnails | `R/thumbs` | `<repo>/data/thumbs` | `<repo>/data_testing/thumbs` |
| portraits | `R/people` | `<repo>/data/people` | `<repo>/data_testing/people` |
| processing inbox | `R/for_processing`, or `PRKS_FOR_PROCESSING_DIR` | `/data/for_processing`, falling back to `<repo>/data/for_processing` | `<repo>/data_testing/for_processing` |
| text index | `<root>/prks_text_index.db` | same | same |
| research index | `<root>/prks_research_index.db` | same | same |
| error log | `<root>/prks-errors.log`, or `PRKS_LOG_FILE` | same | same |
| maintenance | `<root>/.prks-maintenance/` (restore staging, rollback, ready backups, restore journal) | same | same |

Rebinding happens in `backend/server.py::bind_storage()`. It publishes the
config, the DB wrapper, the text and research indexes, and the module globals
`pdfs_dir`, `thumbs_dir` and `processing_dir`, and it rolls back to the
previous binding if publication fails. Restore calls it through `RebindFn` after
replacing components (`backup_restore.apply_restore`). Tests build roots with
`StorageConfig.for_testing(root)`.

Backup already classifies every `StorageConfig` path field
(`backup_storage_inventory()`): canonical `db_path`, `pdfs_dir`, `people_dir`;
derived `thumbs_dir`, `index_db_path`, `research_index_db_path`; operational
`log_file`; conditional `processing_dir` (included only when it is under the
root); container `root`, `configured_root`. `backend/AGENTS.md` requires every
new persistent component to be classified there.

### 1.2 Assumptions: keep, or treat as migration debt

**Storage safety primitives to keep.** Phase A wraps these; it does not
rewrite them.

| Primitive | Where | Why it stays |
| --- | --- | --- |
| Containment with drive-qualified-segment refusal | `paths.resolved_child_path`, `db_manager.safe_pdf_path_under_dir` | This is the only thing that turns a key name into a path. It becomes the local backend's key-to-path function. |
| Crash-durable publication | `backend/fs_durability.py`, `atomic_replace_managed_pdf_bytes`, `_exclusive_create_write_and_fsync` | These are the local backend's `replace` and `put_new`. |
| Exclusive creation of freshly minted names | `store_new_managed_pdf_bytes`, `mint_managed_pdf_filename` | This is the mandatory `put_new` semantics (§10.2). |
| Cleanup claims that store a **basename, never a path** | `pending_pdf_cleanup`, `work_deletion.py` | They already survive a restore into a different root, and they survive relocation unchanged. |
| Three-valued reference check, with check and retirement in one transaction | `settle_claim_if_referenced` | This is the pattern §12 generalizes. |
| A restore journal state machine with a single commit record | `backup_restore.py` | Relocation (§8) reuses its structure and its durability rules. |
| Resolving the root once per destructive operation | `_resolved_storage_root` | This is the symlink rule in §7.3. |
| Testing-safety guard | `paths.assert_safe_testing_path` | It stays and applies to every source in §5. |
| Canonical, derived and operational classification | `backup_storage_inventory` | §4 extends it; it does not replace it. |

**Stable semantics to preserve.** Bytes are durable before the row that
references them commits. Availability is observed, never canonical: a missing
file never deletes a row (#60 §9.2). Derived data is rebuilt, never restored.
Machine-specific deployment settings, `PRKS_STORAGE` included, are not
portable library state and are not in backups. The `/api/pdfs/<basename>`
reference form is route-shaped and root-independent.

**SQLite-era implementation details.** These are valid now and are not the
target.

- The database file sits inside the root, so "move the root" currently means
  "move the database too". §9 separates the two.
- The derived indexes are SQLite files in WAL mode (`text_index.py`,
  `research_index.py`), which need local-filesystem shared-memory semantics.
- Backup takes its snapshot with `sqlite3.Connection.backup`, and restore
  replaces the database file with a rename.
- Connections are per operation, and the process-local `concurrency` gate is
  the only writer serialization.

**Host paths leaking into state.** These must be fixed.

| Leak | Where | Severity |
| --- | --- | --- |
| `processing_files.abs_path TEXT NOT NULL` stores an absolute host path in the **canonical** database | `db_schema.sql`, rewritten after restore by `_rewrite_processing_abs_paths` | This is the only absolute path in canonical state. Relocation would need the same rewrite. The value can be derived from `rel_path` plus the bound inbox, so Phase B makes it derived (§11). |
| Every module receives absolute directories | the `StorageConfig` fields; the `server.py` globals `pdfs_dir`, `thumbs_dir`, `processing_dir` | This is the leakage §10 stops at the application boundary. |
| Locks are keyed by the resolved absolute path | `managed_pdf_path_lock` (`_PDF_PATH_LOCKS`) | Rebinding to a new root silently changes lock identity. Phase B keys the lock by storage key. |
| The text-index fingerprint uses `st_mtime_ns` of the physical file | `text_index._fingerprint` | A copy that does not preserve mtimes forces re-extraction. That is safe, because the index is derived, but relocation should know it will happen (§8.5). |

No `works`, `assets`, annotation or cleanup row stores an absolute path.
`works.file_path` holds `/api/pdfs/<basename>`, and `assets.storage_locator`
holds a bare basename. #60 already made the right choice. #311 formalizes it.

**Configuration debt.**

- `parse_configured_root` does not make a relative `PRKS_STORAGE` absolute, so
  the same setting points at different trees depending on the working
  directory. Phase A normalizes it once, at resolution time (§7.2).
- The production fallback is the repository checkout's `data/`. That is fine
  for development, and wrong as the default for an installed application (§6).
- `/data/for_processing` is preferred when `PRKS_STORAGE` is unset, and it
  silently falls back to the repository. That is a Docker-shaped special case
  inside a non-Docker default. Phase A **keeps** it, because Phase A promises
  identical paths. Phase D retires it with a named migration note:
  - the resolver keeps `/data/for_processing` for an installation whose
    `processing_files` rows point there, or whose `/data/for_processing` holds
    files, and it shows a one-time notice;
  - every other installation uses `<root>/for_processing`;
  - the release note tells operators who want the old inbox to set
    `PRKS_FOR_PROCESSING_DIR=/data/for_processing`.
- Nothing records which library a root belongs to, so nothing can detect a
  wrong pairing of database and file root (§7.1).

---

## 2. Terminology

| Term | Definition |
| --- | --- |
| **PRKS data root** (data root, library root) | One directory, selected by configuration, that holds all file-backed persistent data of one PRKS library for `LocalFilesystemStorage`: canonical objects, durable operational metadata, derived caches, and staging. Today this is `StorageConfig.root`. It is a deployment choice, not an identity. |
| **Storage backend** | The component that stores, reads and deletes objects by storage key. It knows nothing of Works, Manifestations or Assets. |
| **LocalFilesystemStorage** | The storage backend bound to one data root. It maps keys to paths beneath that root and provides the durability of §10.3. It is permanent and first-class. |
| **Object storage backend** | A possible future backend over an S3-compatible service (§10.5). Never required. |
| **Asset ID** | `AS-` + 32 hex, owned by #60 (§11.2 there). It is the stable domain identity of a file or source lineage. It never changes when bytes move, are relocated or are restored. |
| **Storage key** | `(namespace, name)`. It identifies one stored object within a backend, independent of where that backend physically keeps it. Canonical text form: `namespace/name`. |
| **Namespace** | A fixed, code-defined partition of keys with one classification (§4): `asset-objects`, `portraits` and so on. It is never user input. |
| **Physical path** | The absolute host path that `LocalFilesystemStorage` resolves for a key at one moment on one machine. It is ephemeral. It may be shown to the owner in diagnostics. It is never stored in canonical state and never logged. |
| **Canonical file-backed data** | File bytes whose loss is loss of user data, and which cannot be recomputed from other canonical state. They are backed up, relocated and integrity-checked. |
| **Durable operational/configuration data** | State that PRKS needs to find, identify, recover or safely operate a library but that is not research content: the bootstrap config, the root marker, restore and relocation journals, and cleanup claims (which live in the database, #60 §9.2). |
| **Derived / rebuildable data** | Anything PRKS can regenerate from canonical data: thumbnails, the text index, the research index, and future caches and projections. It is never backed up, never relocated, and may be deleted at any time without data loss. |
| **Temporary / staging data** | Short-lived files that exist only for the duration of an operation: write temporaries, linearization output, upload and restore staging, relocation staging, ready-for-download backups. Only the operation that created them may publish them, and a stale sweep may remove them. |

Rule: **the domain and application layers deal in Asset IDs and storage keys.
Physical paths exist only inside storage infrastructure.**

---

## 3. Relationship to #60

### 3.1 The layering

```
Logical Work                          #60  (W-…)
    │
Manifestation / Edition / Version     #60  (MF-…)
    │
Asset                                 #60  (AS-…; kind, role, hashes, provenance, lifecycle)
    │  storage_locator / source_locator
    ▼
storage key  (asset-objects, <name>)  #311 contract; the name is stored by #60
    │
StorageBackend                        #311
    │
LocalFilesystemStorage ──► <data root>/pdfs/<name>        (layout 1)
ObjectStorage (later)  ──► <bucket>/<prefix>/asset-objects/<name>
```

#60 consumes #311 at exactly one seam: an Asset slot holds a storage key name
that the storage layer issued, and every byte operation on that slot goes
through the backend. #311 does not know what an Asset is for. #60 does not know
where bytes live.

### 3.2 Attribute ownership

| Attribute | Owner | Where it lives | Notes |
| --- | --- | --- | --- |
| Stable Asset ID | #60 | `assets.id` | It survives relocation, restore, backend change and merge. |
| Storage key of the working bytes | #60 stores it, #311 issues and defines it | `assets.storage_locator` (the name; the namespace is `asset-objects`) | This is a reference to an object, not an identity. It may change without the Asset changing, for example on copy-on-write retargeting (#60 §9.3) or a future immutable-object mode (§10.4). |
| Storage key of the pristine source | #60 stores it, #311 issues it | `assets.source_locator` | The same namespace. |
| Content hashes | #60 | `ingest_sha256`, `content_sha256`, `content_generation` | These are Asset integrity metadata. The backend may *compute* a hash while writing (§10.2), and #60 decides what to store. |
| Media type | #60 | `assets.media_type` | The backend may carry a content-type hint for HTTP serving, but it is never authoritative. |
| Byte size | #60 records it; the backend observes it | `assets.byte_size`; `ObjectInfo.size` | A mismatch is an integrity finding, not an automatic repair. |
| Original and display filename | #60 | provenance on the Asset (`origin_ref` or a future `original_filename`) | It is **not** derived from the storage key. Today's minted names embed a sanitized original filename for readability. That is a naming convenience, not a place to read the filename from. |
| Provenance (`origin`, `origin_url`, `origin_ref`) | #60 | `assets.*` | |
| Lifecycle state (`active`, `trashed`) | #60 | `assets.state` | The backend has no lifecycle beyond "the object exists or it does not". |
| Availability (present or missing) | observed | computed by `stat`, possibly cached as derived state | Never canonical (#60 §9.2). |
| Backend identity and type | #311 | deployment configuration plus the root marker (`storage_root_id`) | **Not stored per Asset** while a library has exactly one backend. If a library ever spans backends (tiering, or a migration in progress), #60 adds a nullable `storage_backend` column. That is a new schema decision, deferred (§16). |
| Physical path | nobody | nowhere | It is resolved on demand and never persisted. |
| Backend version token (mtime, inode, ETag, VersionId) | #311 | `ObjectInfo`, transient | It may be used for a conditional operation within one request. It is never stored as identity. |

### 3.3 What #60 must do to consume this

1. Treat `storage_locator` and `source_locator` as **key names in the
   `asset-objects` namespace**, obtained only from the storage layer's name
   minting (today `mint_managed_pdf_filename`), never composed by a feature.
2. Serve Asset bytes by Asset ID in the typed API (#60 Slice G, for example
   `/api/assets/{AS-id}/content`). `/api/pdfs/<basename>` stays a legacy
   projection (#60 D10). This keeps browser and offline caches independent of
   key changes.
3. Put every byte operation on an Asset slot through the backend once Phase B
   lands: ingest, replace, materialization, linearization, copy-on-write,
   delete, and the fingerprint pass.
4. Keep the reference check and the cleanup claim in the catalogue
   transaction, keyed by key name (the existing `pending_pdf_cleanup` model).
5. Store web snapshots as **one object per slot**. A multi-resource capture is
   packed into one container, such as a WARC or a zip, so a snapshot stays one
   key and one hash. If Web Works need a separate namespace after all, it must
   be a new fixed namespace, classified canonical in the backup inventory in
   the same PR (#60 §9.5), and never chosen from `assets.role`.

**Which #60 slices this gates.** None of Slice A, B or C. Slice D (Asset
authority) is where `assets` locators become authoritative, and it should land
on Phase B's backend operations, or at least not add any new path-based
helpers. Slices G and H must serve by Asset ID. Slice I (duplicate warning)
needs streaming hash computation at ingest, which `put_new` provides (§10.2).

---

## 4. Data classification and local layout

### 4.1 Classification

| Class | Contents (current and future) | Backed up | Relocated | May be deleted by PRKS |
| --- | --- | --- | --- | --- |
| **Canonical file-backed** | managed Asset objects (PDFs now; web snapshots and attachments later), preserved pristine originals (`source_locator`), portrait objects, and future user-uploaded portraits | yes | yes (copied and verified) | only through the Asset or claim lifecycle |
| **Canonical relational (SQLite era only)** | `prks_data.db`, including `pending_pdf_cleanup`, the sync ledger and `app_settings` | yes | yes | never (except restore) |
| **Durable operational** | root marker `prks-root.json`; restore and relocation journals under `.prks-maintenance/`; bootstrap config (**outside** the root) | no (the marker is recreated for a restore target, §7.1) | the marker is rewritten, and journals are per-operation | journals are removed only when their operation has completed |
| **Operational logs** | `prks-errors.log*` | no | no (a new log starts in the new root) | rotation |
| **User inbox (conditional)** | `for_processing/`: files the user drops in for import, not yet managed | only when under the root (current rule) | only when under the root | never by storage code |
| **Derived / rebuildable** | `thumbs/`, `prks_text_index.db*`, `prks_research_index.db*`, future caches and projections | no | no (rebuilt) | at any time |
| **Temporary / staging** | `.prks-write-*.tmp`, `.linearized_*`, thumbnail temporaries, `.prks-maintenance/restore-staging`, `rollback`, `backup`, relocation staging | no | no | by the owning operation, or by the stale sweep |

**Portraits (`people/`) are canonical, but they are not Assets.** Today they
are transcoded caches of a remote `image_url`, and the original remote bytes
are not kept. They stay canonical for backup because the remote may disappear,
which is the current inventory's choice. They use their own namespace,
`portraits`, and are keyed by person ID plus URL hash, which is already
path-free. If #60 or a later issue turns a portrait into a user-uploaded
Asset, it moves into `asset-objects`.

**The processing inbox is not Asset storage.** A watched folder is inherently a
local-filesystem feature. It stays filesystem-specific infrastructure in every
deployment. An object-storage deployment keeps a local inbox or uses upload
APIs. Import copies bytes out of the inbox through `put_new` and never adopts
an inbox path as a key.

**Rule: a derived cache may never become impossible to rebuild because it
shares a parent directory with canonical data.** Every derived component is
deletable by name, independently, which `clear_derived_storage` already relies
on. No canonical object may live inside a derived directory, and no derived
artifact inside a canonical namespace directory.

### 4.2 Layout 1 (today, formalized)

```
<data root>/
├── prks-root.json            durable operational   NEW (Phase A): storage_root_id, layout_version
├── prks_data.db              canonical relational  SQLite era only
├── pdfs/                     canonical             namespace asset-objects
├── people/                   canonical             namespace portraits
├── for_processing/           user inbox            conditional; may be outside via PRKS_FOR_PROCESSING_DIR
├── thumbs/                   derived
├── prks_text_index.db*       derived
├── prks_research_index.db*   derived
├── prks-errors.log*          operational log       may be outside via PRKS_LOG_FILE
└── .prks-maintenance/        staging + journals    restore-staging/, rollback/, backup/, restore-journal.json,
                                                     relocation/ (NEW, Phase E)
```

**Existing directories are not renamed.** Renaming `pdfs/` to something
media-neutral has no user value, and it has real risk: live libraries, backup
archives (`files/pdfs/*`), Docker bind mounts and user runbooks all use the
current name. The logical namespace name decouples code from it.

### 4.3 Rules for new components

- A new **derived** component goes under `cache/<name>/` (for example
  `cache/snapshot-previews/`), so the derived set becomes one prefix over time.
  Existing derived files stay where they are.
- A new **canonical** namespace needs a fixed name, a classification in
  `backup_storage_inventory`, a backup archive path, relocation coverage, and a
  justification for why `asset-objects` does not fit.
- A new **temporary** file must be created **on the same filesystem as its
  publication target** (sibling temporaries, or under `.prks-maintenance/`),
  because every publication is a rename. Names start with `.prks-` or `.` so
  audits and listings ignore them.
- **Backups and exports are never kept inside the live data root** except as
  transient ready-for-download archives under `.prks-maintenance/backup/`. A
  future user-configurable backup location must be validated as outside every
  active root, so a relocation never copies backups and a backup never includes
  backups.
- `layout_version` in the marker is bumped only by a layout migration, and a
  layout migration is performed **only** by the relocation machinery (a copy
  into a fresh layout, §8), never in place at startup.

---

## 5. Configuration ownership and precedence

### 5.1 Where the selected root is recorded

The selected root is stored in a **bootstrap configuration file**, a small
machine-written JSON document in the platform **configuration** directory (§6),
which is outside every data root:

```json
{
  "format": 1,
  "storage": {
    "backend": "local",
    "local_root": "/mnt/research/PRKS Library",
    "relocation": null
  }
}
```

- **Why a file, not the database.** Requirement: PRKS must be able to start,
  and report a useful error, before it opens the selected root. The database
  lives inside the root in the SQLite era, and in a separate service with
  PostgreSQL. Either way it cannot be the place that says where the root is.
  `app_settings` stays for library settings that travel with the library.
- **Why outside the root.** A config inside the root cannot locate the root,
  and a restore or relocation would carry one machine's path to another.
- **Why JSON.** It is written by PRKS, not by hand, it round-trips with the
  standard library, and it needs no dependency. (`tomllib` only reads.) It is
  written atomically with `fs_durability`: sibling temporary, file fsync,
  `os.replace`, directory fsync. For a root this file selects, that atomic
  replace is the relocation commit point (§8.2 P5). A CLI- or
  environment-selected root commits in its destination marker instead
  (§8.7), and this file only mirrors that, best-effort.
- **Every write is a guarded compare-and-set.** Atomic replace makes each
  write durable, but it does not stop a stale read-modify-write from
  overwriting a newer selection. So every writer of this file (the relocation
  steps, revert, startup recovery, the offline commands' best-effort mirror,
  **choose**, **open another library**, and P10 cleanup) follows one rule:
  1. Take an exclusive OS advisory lock on a sibling `<config file>.lock`
     (the same mechanism as `root.lock`, §12). It is cross-process, so the
     server, the CLI and a second starting process all serialize on it.
  2. Under the lock, **re-read** the file and check the writer's expected
     state. A lock alone is not enough; this check is the guard.
  3. Only if the check holds, write atomically. Then release the lock.

  A hot rebind (§8.1) is the one multi-step writer: it holds this lock
  across its whole config-switch-and-bind transaction, not per write.

  A relocation step expects exactly `(local_root, relocation.id,
  relocation.phase)` as its predecessor left them. For example, P7 expects
  `(to, this id, committed)`, and P9 expects `(to, this id, committed or
  source_retired)`. If the state has changed, the step writes nothing and is
  logged as superseded. It never overwrites a newer record. A user command
  (choose, open another library, a new move's P1) expects the `local_root`
  and `relocation` it showed the user when they confirmed. On a mismatch it
  fails with a conflict and asks the user to review the current state.
  Root-selection commands also **refuse while any move is unresolved**: from
  `preparing` through `committed` and `source_retired`, until P9 writes
  `retained` or the move is `failed`. P9 does not wait on P7's source write
  (§8.2), so an unreachable old disk cannot block selection indefinitely. A
  late P7 retry at a later start changes only the source marker; its config
  write is guarded like any other and is a no-op once the phase has moved on.
  The same refusal applies, for root selection **and** for a new move's P0,
  while the record is `retained_residuals` (P10 stopped on kept inbox files,
  §8.2 P10): that record is the only durable pointer from this installation
  to those files, so nothing may overwrite it until each kept file has been
  imported or explicitly discarded. The refusal names the files and offers
  those two actions.
- **It holds no secrets.** When a future object-storage backend needs
  credentials, they come from the environment or a secret store. The config
  file holds at most a reference.
- The file path can be overridden by `PRKS_CONFIG_FILE`, for portable installs
  and for tests. Testing mode ignores it (§5.3).

### 5.2 Precedence

The root is resolved by exactly one function, which returns `(root, source)`,
where `source ∈ {cli, env, config_file, platform_default, development_default}`:

| Order | Source | Persisted? | Typical use |
| --- | --- | --- | --- |
| 1 | CLI `prks_app.py --storage-root PATH` (new, Phase A) | no, process lifetime only | an administrator, a one-off run, a service unit |
| 2 | `PRKS_STORAGE` environment variable (existing) | no | Docker/Compose (`/data`), systemd, CI |
| 3 | Bootstrap config file `storage.local_root` | yes | normal user choice through Settings or a desktop installer |
| 4 | Default: platform default (packaged) or `<repo>/data` (source checkout) (§6) | n/a | first run |

Rules:

- **The first source that is set wins completely.** Sources are never merged
  field by field for the root. A set-but-invalid higher source is a **startup
  error**, never a silent fallthrough to the next source. Falling through could
  open a different library, or an empty one, and look like data loss.
- **A higher source never rewrites a lower one.** `PRKS_STORAGE` does not
  update the config file. When the source is `cli` or `env`, Settings shows the
  root read-only, with "Set by the environment" or "Set on the command line",
  and in-app relocation is unavailable (S10). The UI could otherwise persist a
  choice that is silently ignored at the next start.
- **Component overrides stay second-class.** `PRKS_FOR_PROCESSING_DIR` and
  `PRKS_LOG_FILE` keep working as deployment overrides for non-canonical
  components. No override may place a **canonical** component outside the root.
  The existing backup check `canonical_path_outside_root` already refuses that.
- **Containers.** The image sets `PRKS_STORAGE=/data` and mounts the volume
  there. The container's config directory is ephemeral and is never consulted
  for the root. Moving a containerized library means changing the mount, which
  the administrator does (§8.7).
- **First run with no source set.** A desktop build (#46) may ask the user
  where to put the library before creating it, and then write the config file.
  A headless first run creates the default root and writes nothing to the
  config file. The default is re-derived on every start. So that a later change
  to the default rule never strands an existing library, the default step
  first probes the **known earlier default locations**, in a fixed order,
  before it creates anything:
  1. any earlier platform-default location;
  2. for a source checkout, `<repo>/data`, which is the only prior **root**
     default. `/data` is never probed: today it is only a preferred processing
     *inbox*, and a `/data` library belongs to a container or another
     deployment unless an operator selects it explicitly.

  Each probe accepts either a marker-bearing root or an **unmarked legacy root**
  that satisfies the §7.1 adoption rule. The first match is used and adopted,
  with a one-time notice. If more than one location holds a library, startup
  refuses, names both, and asks the user to choose. A new, empty default root
  is created only when every probe finds nothing. The marker is therefore
  never *required* before the legacy discovery has run.

### 5.3 Testing mode

`--testing` / `PRKS_TESTING=1` never reads the bootstrap config file or the
platform default. It uses `PRKS_STORAGE` if set, otherwise `data_testing/`.
`assert_safe_testing_path` still applies to the resolved root and to every
component. This keeps a test run from ever discovering the user's persisted
production library. `run_tests.py` already sets `PRKS_STORAGE` explicitly.

---

## 6. Platform defaults

| Platform | Bootstrap config file | Default data root |
| --- | --- | --- |
| Linux and other XDG systems | `${XDG_CONFIG_HOME:-~/.config}/prks/config.json` | `${XDG_DATA_HOME:-~/.local/share}/prks/library` |
| macOS | `~/Library/Application Support/PRKS/config.json` | `~/Library/Application Support/PRKS/Library` |
| Windows | `%LOCALAPPDATA%\PRKS\config.json` | `%LOCALAPPDATA%\PRKS\Library` |
| Source checkout (any OS) | as above | `<repo>/data` (development default) |
| Container image | not consulted | `PRKS_STORAGE=/data` (required) |

- **Resolution is a table keyed by platform family**, implemented with the
  standard library (environment variables plus `os.path.expanduser`). It needs
  no new dependency, and it is not hardcoded to one OS. An unknown platform
  family uses the XDG rule.
- **Local, not roaming, on Windows.** A library holds SQLite files and large
  PDFs, and roaming profiles and folder redirection break both.
  `%LOCALAPPDATA%` is also where the config lives, because a roaming config
  would carry one machine's local path to another.
- **Not in Documents or a cloud-sync folder by default.** On Windows,
  Documents is frequently redirected into OneDrive, and on macOS it may be in
  iCloud Drive. A live SQLite database in a sync client is a known corruption
  source. A user may still choose such a place deliberately. §7.4 warns them,
  and in the SQLite era it refuses when detection is certain.
- **Packaged versus source.** The distribution declares itself. A packaged
  build sets a build-time constant, for example `PRKS_DISTRIBUTION=packaged`
  in a generated module, and a source checkout lacks it. The resolver never
  guesses from "is there a `.git` directory". A source checkout keeps
  `<repo>/data` so current developers and self-hosters are unaffected. **No
  existing library is ever moved automatically.** A user who wants their
  source-checkout library at the platform location uses relocation (§8).
- The **config file must not be inside the data root**, and the data root must
  not be the config directory itself. The data root **may** be a
  subdirectory of the config directory. That is the macOS and Windows default:
  `.../PRKS/Library` next to `.../PRKS/config.json`. V11 checks the config
  *file*, not the config directory.

---

## 7. Root identity and validation

### 7.1 The root marker

`<root>/prks-root.json`, written with the durability convention:

```json
{
  "format": 1,
  "storage_root_id": "SR-<32 hex>",
  "layout_version": 1,
  "state": "active",
  "created_at": "2026-09-29T00:00:00Z",
  "relocation": null
}
```

`state ∈ { active, fenced, staging, retired }`. `relocation` carries
`{ id, role: "source" | "destination", peer_hint, verified?, phase? }` while a move is in
progress, or after it retired this root. `phase` is absent until a
volume-recorded outcome exists. It is only ever written on a destination, and
only by the offline flow (§8.7): `"committed"` (the offline commit) or
`"aborted"` (never bindable). `verified` (offline flow, destination only) is set to `true` at the end of
P4. It is the destination-side proof that the P2 fence landed. The marker
`state` and `relocation.phase` are separate fields. A destination stays `state: staging` through P4 and through
the offline commit, until P6(b) replaces the whole `relocation` with `null`.

An optional `moved_from: {id, peer_hint, residuals?}` records that a
completed relocation created this root. It is not a relocation role and never
affects **binding**. It is operational for two purposes: `peer_hint` is how
startup finds the source to retry P7 (retirement) after a crash, and the
optional `residuals: true`, set by P10 on a CLI- or environment-selected root,
is the volume-durable equivalent of the config's `retained_residuals`: kept
inbox files still wait in the retired root named by `peer_hint` (§8.2 P10).
`moved_from` is written by every activation, in-app and offline. While
`residuals` is set, every later write of this marker preserves `moved_from`
unchanged, and only the P10 rerun that completes the teardown clears it.

`fenced` marks a **relocation source** whose move is in progress. It is
written at P2, before any copying (§8.2). Only the marker changes; canonical
data is untouched.

**Binding rule.** Ordinary startup binds only a root whose state is `active`
and that carries no relocation role. Every other state needs a finalizer that
can prove how the move ended:

- **A `staging` destination** may be bound, and later becomes `active`, only
  by a holder of proof that the move committed. That holder is the relocating
  process in P6, or one of the following:
  - startup recovery, when the bootstrap config says `committed` for the same
    `relocation_id` (§8.3);
  - the destination marker's own `relocation.phase: committed`, the
    volume-durable offline commit (§8.7), acted on by `storage finalize` or
    by startup recovery.

  A destination marked `phase: aborted` is never bindable.
- **A `fenced` source** goes back to `active` only when the move is known
  *not* to have committed:
  - startup recovery, when the bootstrap config still selects this root with
    phase `copying`/`verified`/`failed` for the same `relocation_id`;
  - an explicit `storage abort` (§8.7), after it has durably marked the
    destination `aborted`. Its precondition is that the destination was
    neither committed nor active.

  Otherwise it becomes `retired` (P7). A process that finds a `fenced` root
  and has no such proof, such as a second installation pointed at the old path
  through `--storage-root` or `PRKS_STORAGE`, refuses to start. The message
  names the relocation and the peer path.

So a second PRKS instance pointed at either end of a move is refused, before
or after the commit. It cannot fork the library.

**`storage_root_id` identifies the library's file store, not a directory.**
Relocation **carries the same ID** to the destination. The ID names "this
library's files" wherever they live, so the database pairing (§9) stays valid
without being touched. Two directories may hold the same ID only as one
`active` root plus `staging`, `fenced` or `retired` copies. During P1 the source is
still the only bindable root. From the P2 fence there is none at all, until the
move either commits or is aborted. The binding rule
above
guarantees that **at most one root per ID is ever bindable**. "Open another
library" of an **existing** root keeps that root's marker ID untouched, so its
database pairing (§9) still matches. Only creating a genuinely new root, by
"Choose" on an empty directory, mints a new ID. Only relocation copies one.

What it is for:

- **Refusing the wrong directory.** Binding a root requires a marker in state
  `active`, except when PRKS creates a new root (§7.2). A mistyped path, a
  wrong mount, or an unmounted NAS whose mountpoint is now an empty local
  directory therefore fails loudly instead of starting an empty library. The
  unmounted-NAS case is the most likely real-world data-loss scenario here.
- **Refusing a stale copy.** A root retired by a relocation (§8) is refused,
  with a message that points to where the library went. That avoids silently
  forking the library.
- **Pairing a database with a root.** The marker gives the root an identity
  that a database can reference. With PostgreSQL, the database records the
  `storage_root_id` it expects (§9), and PRKS refuses a mismatched pair. The
  pairing matters for safety as well as correctness: cleanup claims delete
  bytes by key, so a database paired with the wrong root could delete another
  library's objects.
- `storage_root_id` is a **storage** identity. It is not the #310 Library ID and
  not a user-facing identity. When #310 introduces `Library`, a Library row may
  reference the storage root it uses. #311 does not create Library entities.

**Adopting existing libraries.** Existing roots have no marker. Phase A adopts
a root **only** when it looks like a PRKS library (the configured source
selected it and it contains `prks_data.db`, or it contains a `pdfs/`
directory), and writes the marker on that first bind. An empty or absent
default root is created with a marker. A non-empty directory without PRKS
content is refused as a new root (§7.2).

**Restore.** A backup does not carry the marker, which is machine and root
identity. Restoring into a root keeps that root's `storage_root_id`. With
PostgreSQL, restore rewrites the database's expected `storage_root_id` to
the target root's, because restore replaces both halves together (§9.3).

### 7.2 Validation checklist

The same check runs when a root is chosen, at every startup (the cheap
subset), and before relocation (the full set, against the destination).

| # | Check | Startup | Choose or relocate | Failure |
| --- | --- | --- | --- | --- |
| V1 | **Normalization.** Expand `~` and environment references once, make absolute, collapse `.`/`..` lexically. Persist the **normalized absolute** form as `local_root` and use it for all checks, so the selection resolves identically after a restart from another working directory or with a changed environment. The user's original spelling may be kept in a separate `local_root_display` field, which is used only for display and never resolved. | ✓ | ✓ | error (empty or unparseable) |
| V2 | **Exists or creatable.** For a new root, the parent exists and the directory can be created with owner-only permissions. For an existing root, it is a directory, not a file. | ✓ | ✓ | error |
| V3 | **Marker state.** `active` for binding. For a **resumed move** (P0 same-move resume): the destination must be `staging` with this `relocation_id`, or match the §8.7 P1-only state, and its non-empty contents are expected; the new-root emptiness rule below does not apply. For a new root (a fresh destination): absent, and the directory is empty or contains only hidden OS metadata (`.DS_Store`, `desktop.ini`, `Thumbs.db`, `lost+found`) and/or a leftover **preflight scaffold** (V4). A lone `.prks-maintenance/root.lock` with no marker, left by an offline `relocate` that lost the source-lock race or crashed before P1, also counts as scaffold, and so does an **empty** `.prks-maintenance/` with no other children, left by a crash partway through any scaffold cleanup. The next preflight removes all three forms. | ✓ | ✓ | error; show the retirement pointer when `retired` |
| V4 | **Readable and writable.** Create, write, fsync, rename and delete a probe file, not by checking permission bits. Every capability probe (V4, V5, V6, V8) runs only inside a **preflight scaffold**: `.prks-maintenance/preflight/`, which contains only `.prks-probe-*` files and two probe subdirectories, `preflight/a/` and `preflight/b/`, holding only `.prks-probe-*` files. V6's cross-directory rename moves a probe from `a/` to `b/`. Preflight removes the scaffold, including an emptied `.prks-maintenance/`, on every exit. A scaffold left behind by a crash is recognized by V3 and removed by the next preflight, so a crash in P0 never strands the directory. | ✓ | ✓ | error |
| V5 | **Exclusive create.** `O_CREAT` with `O_EXCL` on the probe name fails when it exists. | once per root (recorded in the marker) | ✓ | error |
| V6 | **Atomic rename over an existing file within one directory**, and across directories within the root (restore and relocation rely on it). | once | ✓ | error |
| V7 | **One filesystem.** Every PRKS component directory under the root has the root's `st_dev`, so there are no mount points inside the root. Renames between `.prks-maintenance/` and components must stay atomic. | ✓ | ✓ | error |
| V8 | **File fsync succeeds.** Directory fsync is best-effort, consistent with `fs_durability`: an unsupported result is recorded and shown, not fatal. | once | ✓ | error for files; diagnostic for directories |
| V9 | **SQLite-era locking.** The database's filesystem supports the byte-range locks SQLite needs. That filesystem is where the main DB lives now, and where the WAL-mode derived indexes always live. In practice: refuse a filesystem type that is known to be network or FUSE-sync when it can be detected with certainty, and otherwise warn (§7.4). | ✓ (warn) | ✓ | error when certain, else warning |
| V10 | **Free space.** Report free bytes. For a choice or relocation, require at least (canonical bytes to copy) × 1.1 + 1 GiB of headroom, and warn below a configurable floor on startup. The existing `_free_bytes` probe is reused. | warn | ✓ | error for relocation; warning otherwise |
| V11 | **Nesting and collision.** The candidate must not equal, contain or be contained by another active PRKS root (walk parents for `prks-root.json`, and do a bounded scan for markers below), the current root (except as the relocation source), the repository checkout when the distribution is **packaged** (a source checkout's declared development default `<repo>/data` is exempt, §6; any other location inside the checkout is refused), the application install directory, a configured backup location, or the filesystem root or the user's home directory itself. It must not **contain** the bootstrap config file. It may sit inside the config directory (the §6 macOS and Windows default). It must not lie inside another root's `.prks-maintenance/`. The candidate's **own** `.prks-maintenance/` is expected, and it is exempt from every containment test here. | ✓ | ✓ | error |
| V12 | **Testing safety.** `assert_safe_testing_path` when testing, and **the reverse in production**: production never binds `data_testing/`. | ✓ | ✓ | error |
| V13 | **Symlinks and reparse points** (§7.3). | ✓ | ✓ | error for a link inside the root |
| V14 | **Name representability** (relocation only). Every existing key name must be representable on the destination: no two names that fold together under the destination's case-insensitivity or Unicode normalization (NFC/NFD), no name over the destination's length limit, no Windows-reserved stem (`CON`, `NUL`, `COM1`…) and no trailing dot or space when the destination has Windows semantics. Case sensitivity and normalization are measured with probe files, not assumed from the OS. | — | ✓ | error that lists the count, with names shown only to the owner |
| V15 | **Ownership and permissions.** Components are owner-accessible. Warn when the root is readable by other users on a multi-user host. | warn | warn | warning |

Startup performs V1–V4, V7, V11–V13 and the cheap part of V9 and V10. The
filesystem-capability probes (V5, V6, V8) run once per root, and their result
is cached in the marker, with a re-probe after the device changes.

### 7.3 Symlink and reparse-point policy

- **The root path may be a symlink or junction.** This is an operator choice,
  and current code already tolerates it. It is resolved **once** per bind and
  once per destructive operation (`_resolved_storage_root`), and everything
  is checked against that snapshot.
- **Nothing inside the root may be a link.** This covers component
  directories, namespace directories and objects. Backup already refuses links
  (`walk_regular_files` counts links instead of archiving them), and
  maintenance removal never follows directory links (`_safe_remove`). The local backend refuses to read, write, delete or
  list through a link, and `stat` reports a link as an integrity finding. On
  Windows this applies to every reparse point, not only symlinks.
- Diagnostics show both the configured spelling and the resolved path, so a
  retargeted link is visible.

### 7.4 Network and NAS-mounted roots

"Mounted" does not mean "supported". A root is supported when it passes V4–V9
and V14. In practice:

- **SQLite era (now).** The root holds the live main database and two WAL-mode
  derived indexes. SQLite documents that WAL does not work over a network
  filesystem, and that rollback-journal mode depends on the network
  filesystem's lock implementation, which is frequently broken. So: a **local
  disk, an external disk, or a block device mounted locally** (iSCSI, a VM
  disk) is supported. **NFS and SMB/CIFS roots are not supported for a live
  library.** PRKS warns when it cannot tell, and refuses when the filesystem
  type is certain (for example `nfs`, `cifs`, `smbfs` in `/proc/mounts` on
  Linux, or `f_fstypename` on macOS). Cloud-sync folders are warned about
  explicitly. A NAS is fine as a **backup destination**.
- **PostgreSQL era.** The root holds only file objects and derived caches. A
  network filesystem that passes V5, V6 and V8 (NFSv4 with `O_EXCL` and
  consistent rename, or SMB3 with durable handles) is supportable for
  canonical objects, **provided derived SQLite caches can be placed on local
  disk**. That requires a separate `cache_root` option, deferred to Phase D
  (§16).
- **Container volumes.** Supported when the volume passes the same checks. A
  Docker bind mount of a local directory does. A named volume on a network
  driver is subject to the rules above.

---

## 8. Relocation and rebinding

### 8.1 Three different user operations

| Operation | What changes | Data copied? | Supported where |
| --- | --- | --- | --- |
| **Choose** (first run) | config → a new, empty root (created with a marker), or an existing active root | no | Settings or the installer, when the source is `config_file` or the default |
| **Open another library** (rebind) | config → another existing, active, marker-bearing root | no | same, with a confirmation that names it a different library; with PostgreSQL, only if the database pairing matches (§9) |
| **Move this library** (relocate) | canonical bytes are copied to a new root, then config → new root | yes | same; for `cli`/`env` sources, only through the offline CLI (§8.7) |

**Hot rebind (choose, open another library).** When a library is already
bound, both commands rebind the running process, so they need the same
protection as relocation's P6. Each is one request, classified at request
admission in the relocation mode (§8.2 Gate admission), and it runs with the
exclusive rebind barrier for its whole duration: reads drained and held off,
mutations, backups and restores excluded.

The rebind is **one transaction under the config lock** (§5.1), not a series
of independently guarded writes. The command takes `<config file>.lock` once
and holds it until the transaction ends, so no other process can change the
selection between the config switch and the bind:

1. Re-read the config and check that it still holds the `local_root` and
   `relocation` the user confirmed; otherwise fail with a conflict.
2. Take the target root's `root.lock` lease (§12) with a **non-blocking**
   try-lock. A busy target fails the command ("library in use"). It never
   waits while holding the config lock, so it cannot deadlock against a
   relocation that holds root locks and is waiting for the config lock.
3. Write the config atomically: `local_root = target`.
4. Call `bind_storage()`.
5. Release the old root's lease, then the config lock.

If step 4 fails, still under the same config lock: write the previous config
back, rebind the old root, release the target lease, then release the config
lock. Because the lock was never released, that restore cannot be superseded.
**Fail closed** if it cannot complete, for example if the config cannot be
written or the old root no longer binds: call `unbind_storage()` (§8.3), so the
process serves no library, report a configuration error, and keep no lease it
is not bound to. It never keeps serving a root the config does not name. A
crash at any point leaves the kernel to release both locks, and the next start
simply binds whatever the config names. A two-process race test runs two
concurrent hot rebinds against one bootstrap config to different targets, and
proves each process ends bound to the root the config names, or fails closed.
This also prevents a concurrent read from combining one library's database or
indexes with another library's directories.

These are three commands with three confirmations. They are never one "change
path" field. Changing a path field and restarting is exactly the "empty
library appears, the user panics" failure mode.

### 8.2 The protocol

```
  P0 preflight ─► P1 intent ─► P2 quiesce+fence ─► P3 copy ─► P4 verify ─► P5 COMMIT ─► P6 activate+rebind+RELEASE ─► P7 retire source ─► P8 rebuild ─► P9 retain ─► (later) P10 cleanup
  old root authoritative ───────────────────────────────────────────────┤ new root authoritative ──────────────────────────────────────────────►
  old canonical data never written; destination stays `staging` ────────┤
  old marker `fenced` (not bindable by any other process) from P2 ─────────────────────────────────────────────┤ `retired` (P7, non-blocking)
  mutations blocked from admission (before P0) ───────────────────────────────────────────────────┤ released at the end of P6
  reads drained and held off only during P6 (rebind barrier) ────────────────────────────────────┤
```

**Gate admission (in-app move).** The move is one request whose route
`request_access_mode()` classifies **at request admission** as a dedicated
`MODE_RELOCATE`, the way `POST /api/backups/progress` is admitted as
`MODE_BACKUP` today. The request middleware (`_library_access`) then owns the
**move scope** for the whole handler. The handler never acquires a second gate
scope. Under today's gate that would self-deadlock: an ordinary `POST` is
admitted as `MODE_MUTATION`, and `LibraryAccessGate.backup()` waits while
`_mutation_active` is set, which is the request's own mutation. The move scope
has two stages:

- **P0–P5: backup semantics.** Reads continue; mutations, backups and restores
  wait. So P0 and P1 run serialized against every writer, and P2 acquires
  nothing: it relies on the scope already held.
- **From P6(a) through P6(b): an exclusive rebind barrier.** Today's
  `bind_storage()` is not atomic for a reader. It swaps the text and research
  index singletons first, then assigns `_bound_storage`, `pdfs_dir`,
  `thumbs_dir`, `processing_dir`, `db`, `text_index` and `research_index` as
  separate globals, so a concurrent read could see old and new components
  mixed. Restore avoids this by holding `MODE_RESTORE`, which admits no reads,
  across its `bind_storage()`. Before P6(a) the holder of the move scope
  therefore **upgrades** it in place: new reads wait, as they do for a waiting
  restore, and the upgrade waits until `active_reads` reaches zero. It holds
  that barrier through P6(b), or through revert step 4 (§8.3), whose
  `unbind_storage()` and source bind are rebinds too. Then it downgrades or
  releases the scope. The upgrade cannot deadlock: the move request is not
  counted as a read, and the move scope already excludes every other backup,
  restore and upgrader. It is a new gate operation (Phase E), **not** a nested
  `restore()` acquisition, which would also wait on the scope's own
  backup-style flag.

A simpler first version may instead admit the whole move as `MODE_RESTORE`,
blocking reads for the entire move. That is correct but gives up "reads keep
working" during the copy. The handler runs P0–P6 and streams progress in the
same request, like backup progress. The scope ends when the handler returns,
which is at the end of P6 (or after revert, §8.3). P7–P9 run after that as
ordinary background work, outside any gate scope. P10 is **never** automatic
background work: it runs only as its own explicit, later, separately confirmed
command (see the P10 row). Any later asynchronous form,
such as a job thread, must take the move scope from a thread that holds no
other gate scope, never nested inside a request's mutation scope.

**The release boundary is the end of P6, and only there.** P7–P9, and a later
explicit P10, run with mutations admitted. A P7 failure blocks nothing: the source is already
revoked by the P2 fence. Crash tests cover each boundary in this sequence.

| Step | Action | Durable record |
| --- | --- | --- |
| **P0 Preflight** | Run the full §7.2 checklist on the destination; for a same-move resume (journal check below) V3 uses its resumed-move branch instead of the new-root one, including V10 space, computed from the actual canonical inventory, and V14 names. **Journal check:** a restore journal, or a relocation journal for a **different** move, makes P0 refuse. A different move means a different `relocation_id` or a different source/destination pair, still unresolved. A config record in `failed`, `retained` or cleared state does not count; `retained_residuals` does, and makes P0 refuse until its kept inbox files are handled (§5.1). A journal for the **same** move is not a conflict: P0 enters that move's resume path instead of refusing. For a config-file root, that path is the §8.3 row for the recorded phase. For the offline flow, it is a `relocate` rerun, which requires both markers to name this `relocation_id` (or the P1-only state of §8.7). It continues from the first step not yet durably recorded: P2 if the source is not fenced; otherwise P3, reusing the manifest; then P4. | none; probes use only the self-removing preflight scaffold (V4) |
| **P1 Intent** | Mint a `relocation_id`. **First** write the config file with `storage.relocation = {id, phase: "preparing", from, to}`, with `local_root` still the old root, so that any destination this move creates is already discoverable. **Then** create the destination, whose complete **P1-owned set** is exactly: the root directory; `.prks-maintenance/`; `.prks-maintenance/root.lock`, the destination lock (§12), taken here; an empty `.prks-maintenance/relocation/<id>/`; and the marker `prks-root.json`, plus any `.prks-` temporary left by the marker's atomic write. The marker is `state: staging, storage_root_id: <source's ID>, relocation: {id, role: destination, peer_hint: <source root>}`. The `peer_hint` is required from here until activation. The ID is carried, not minted (§7.1). Then advance the config to `phase: "copying"`. Each config write is atomic. | config (`preparing`, then `copying`), destination marker (`staging`) |
| **P2 Quiesce** | Rely on the move scope held since request admission (Gate admission, above); P2 acquires no gate scope itself. Until P6 that scope blocks mutations, backups and restores and allows reads, which is today's backup semantics, and it bounds relocation to the same "reads keep working" user experience. Stop background writers: the cleanup retry, thumbnail and index writers, and processing scans. Then **fence the source**: durably set the old marker to `state: fenced, relocation: {id, role: source, peer_hint: <dest>}`. From here on no other process can bind the old path (§7.1). If the fence cannot be written, the move is refused before any copying. | source marker (`fenced`); the gate itself is process-local (§12 for multi-process) |
| **P3 Copy** | The database: a `sqlite3` backup-API snapshot, never a file copy, written to the destination under a temporary name and then renamed. Each canonical namespace (`asset-objects`, `portraits`, and the inbox if it is under the root): stream every object into a staging name, hash it while copying, fsync the file, rename it to its key name, and fsync the directory. Derived data, logs and maintenance are **not** copied. Links are refused, as in §7.3. | per-object progress in `<dest>/.prks-maintenance/relocation/<id>/manifest.json`: key, size, sha256, and, for inbox entries, the source `st_mtime_ns` observed when the copy was hashed (updated whenever the final pass recopies the entry). Resumable, but a restart may also discard it and begin P3 again. |
| **P4 Verify** | Re-read every destination object, and compare size and SHA-256 against the P3 manifest, which was computed from the source. Run `PRAGMA integrity_check` and the schema-version check on the destination database. Audit the catalogue against the destination (`audit_managed_pdfs`): every referenced key present. Keys missing in the source are reported, not fatal, because availability is observed (#60 §9.2). **The inbox needs a stable final pass**, because users and external tools can write it and PRKS cannot quiesce them. When `for_processing/` is under the root, rescan the source inbox and compare each file's name, size and `st_mtime_ns` with the manifest's recorded source values. Reconcile all three kinds of difference: copy and hash any **new** file; recopy any **changed** file and update its manifest size, hash and mtime; and for any manifest name **no longer present** in the source (deleted or renamed away), delete the destination copy and its manifest entry, so a removed file is never resurrected for later import. Then rescan. Metadata agreement is necessary but **not sufficient**: an external writer can rewrite a file in place with the same size and a preserved or reset `st_mtime_ns`, or change it while it is being hashed. So the pass that ends the loop is a **content pass**. For every inbox file, open it, `fstat` it, hash the full source bytes, and `fstat` it again. Then `lstat` the pathname again: it must still name the same file as the hashed handle (POSIX device and inode; on Windows the volume serial number and file ID), with the same size and `st_mtime_ns`. This catches an atomic replacement or rename over the pathname while the old handle stayed open. The file is stable only if the two `fstat` results are identical, the pathname still names the hashed file, and the source hash equals the manifest hash, which P4 has already matched against the re-read destination copy. Any mismatch recopies that file, updates its manifest entry and starts another pass. The final pass is accepted only when the metadata scan agrees with the manifest exactly (the same set of names with the same attributes), every file passes the content check in that same pass, **and** a closing namespace rescan after all content checks finds the same names with the same file identities and attributes; any change restarts the loop. On POSIX the compared attributes include `st_ctime_ns`, which a writer cannot set, so an in-place rewrite between a file's hash and the closing rescan is detected even if the writer restores size and `st_mtime_ns`. Windows has no unforgeable change time, so there a writer that rewrites a checked file in place and deliberately restores its size and times before the closing rescan is not detected. PRKS does not claim to detect that; it cannot lose data, because P10 deletes an old inbox file only if the destination holds an identical SHA-256, and otherwise keeps it and offers it for import. The same residual rule covers writes that happen only after the accepted P4 boundary. If it does not settle within a bounded number of passes, refuse the move and ask the user to pause whatever is writing the inbox. The destination marker **stays `staging`**, so the destination is not bindable (§7.1). Write config `phase: "verified"`. | config (`verified`) |
| **P5 Commit** | **One atomic config replace:** `local_root = to`, `relocation.phase = "committed"`. For a config-file root this is the only switch. A CLI- or environment-selected root commits in its destination marker instead (§8.7, S9). | config (`committed`) |
| **P6 Activate and rebind** | Still under the move scope. The source has been durably `fenced` since P2 (the move was refused if that write failed). A `fenced` root is unbindable without proof that its move did not commit, and after P5 that proof cannot exist. So the source is **already revoked**. P6 runs in two sub-steps, in this order:<br><br>First **upgrade the move scope to the exclusive rebind barrier** (Gate admission, above): new reads wait and in-flight reads drain, so no reader can observe a half-swapped binding. The barrier is held through (b), or through revert.<br><br>(a) **Bind first, while the destination is still `staging`.** A `staging` root is bindable only by a holder of this relocation's **committed proof** (§7.1): either the bootstrap config's `committed` record, for a config-file root, or the destination marker's `relocation.phase: committed`, for the offline flow. That holder is this process, startup recovery, or `storage finalize`. Hold the destination's `active_process` lease (§12): it was taken at P1, or it is taken now by startup recovery. Then call `bind_storage(new config)`; the existing rollback-on-failure applies. The lease can fail to be taken only because another holder of the committed proof, such as a concurrent startup recovery, already owns it and is completing the move. In that case this process stops and does **not** offer revert. If `bind_storage` fails, **keep the lease**. It is released only by revert (step 5, after the config says `failed` and the source is unfenced), or by the kernel when this process exits. In the second case, §8.3 `committed` recovery correctly completes the move. Releasing the lease while the committed proof still stands would let another process activate the destination, while this process could still revert to the source.<br><br>(b) **Only after (a) succeeds**, activate. Authorized by the same committed proof for this `relocation_id`, write **one atomic marker replace**: `state: active`, `relocation: null`, `moved_from: {id, peer_hint}` with `peer_hint` **copied from the destination's `relocation.peer_hint`** (required since P1), and the `active_process` lease this process already holds. `moved_from` is not a relocation role and binding ignores it, but it is operational: startup uses its `peer_hint` to retry P7 (§7.1). The destination becomes generically bindable in the same write that records this process as its owner, so there is no instant at which another process could bind it or take its lease. No crash can leave an `active` destination that still carries a role. On every later start, the destination is an ordinary bindable root, whatever the config phase says.<br><br>**Then the handler returns, releasing the barrier and the move scope; reads and mutations resume.** At no instant are two roots with this ID bindable: before P6 neither is, and from P6 only the destination is. | destination marker (`active`) |
| **P7 Retire source** | Change the old marker from `fenced` to `state: retired`, keeping `relocation: {id, role: source, peer_hint: <new root>}`. Write config `phase: "source_retired"`, as a guarded compare-and-set expecting `(local_root = to, this relocation_id, committed)` (§5.1). This is **not a safety step**: it turns "a move is in progress" into "this library moved to X" for clearer messages and for P10 cleanup. If the old root cannot be written, for example because a disk was removed, it stays `fenced`, and PRKS retries the change at later starts. A disconnected source that reappears is still `fenced` and still refuses to bind. **No acknowledgement path exists or is needed**: revocation is the durable P2 fence, not P7. | old marker (`retired`), config (`source_retired`) |
| **P8 Rebuild** | Derived data is rebuilt at the new root by the existing mechanisms: the text-index and research-index reconcile, and thumbnails lazily. `retry_pending_pdf_cleanup` now runs against the **new** root only. | none |
| **P9 Retain** | Write config `phase: "retained"`, and keep `from` for diagnostics. Like every config write it is a guarded compare-and-set (§5.1): it expects `(local_root = to, this relocation_id, committed or source_retired)`, and it is a no-op if anything else has since been recorded. It does not wait for a P7 that could not reach the old root. | config |
| **P10 Cleanup** (explicit, later) | Offered only after the new root has completed at least one full start, and recommended after a verified backup. It deletes only PRKS-known components of the retired root (the names in §4.2), never unknown files and never through links, and then the marker. It takes the retired root's `root.lock` and removes it with the §12 **terminal teardown** procedure (the marker is already `retired`), which closes the lock handle before removing the marker and `root.lock`, as Windows requires. **Inbox files are deleted only if an identical copy exists in the destination** (same name and SHA-256). A file that reached the old inbox after P4, or differs from its destination copy, is kept, reported in diagnostics, and offered for import into the new library. **While any such kept file remains, P10 stops short of the teardown's final steps:** it keeps the `retired` marker, `root.lock` and the kept files, releases the lock normally, and writes config `phase: "retained_residuals"` (a guarded compare-and-set expecting `(local_root = to, this relocation_id, retained)`), so diagnostics and the import action can still find the old root and its files. `retained_residuals` is an **unresolved** state: root selection and a new move refuse while it stands (§5.1), so the record cannot be overwritten. For a CLI- or environment-selected root, whose bootstrap file may be ephemeral, P10 records the same state on the destination marker as `moved_from.residuals: true`, and diagnostics and the offline commands read it from there. Only when every kept file has been imported or explicitly discarded by the user does a P10 rerun complete the teardown and clear the record. Config: `relocation = null`, guarded like every config write (§5.1): it expects `(local_root = to, this relocation_id, retained or retained_residuals)`. | config |

**Pre-commit guarantee: the old library's data is never written, and the
destination is never bindable.** The only write to the old root before commit
is its marker fence (P2), which touches no canonical data. The destination
marker stays `staging` until P6. Any failure before P5 is undone by recovery
(§8.3): it lifts the fence, which leaves the old library exactly as it was. The
worst outcome is a disposable staging directory at the destination.

**Guarantee across the whole move: no other process can bind either end.**
From P2 onward the source is `fenced` and the destination is `staging`. Only a
holder of this relocation's journal may change either one. That holder is one
of:

- this process;
- startup recovery reading the journal for the root's source (§8.3 dispatch);
- `storage finalize` / `storage abort` for the offline flow.

The journal is the bootstrap record for a config-file root, and the two
markers, with the destination's `relocation.phase` as committed or aborted
proof (§7.1), for a CLI- or environment-selected root. A crash leaves both
ends unbindable to anyone without that journal, and the journal resolves the
move deterministically.

**Post-commit guarantee: at most one bindable root per ID, and no mutation
before that holds.** Mutations resume at the end of P6. By then the source has
been durably revoked (the P2 fence) and the destination is `active`. So there
is never an interval with two bindable roots, whether or not P7 ever reaches
the old disk.

### 8.3 Crash and failure recovery

**Startup dispatch.** Before binding, PRKS picks exactly one recovery journal
from the effective root source (§5.2):

- **`config_file` or a default source:** the bootstrap config's `relocation`
  record is authoritative. Apply the table below (the analogue of
  `recover_incomplete_restore`).
- **`cli` or `env` source:** the table below is **skipped**. Recovery reads
  only the two root markers (§8.7). A bootstrap `relocation` record that
  exists anyway is at most an offline best-effort mirror. It is never acted
  on. If it disagrees with the markers, it is reported in diagnostics and
  cleared.

Defense in depth: even under a `config_file` source, this table never lifts a
fence or deletes a destination when the destination marker carries
`relocation.phase: committed`, or is `active`. Such a disagreement is refused
and reported instead.

The table, for config-file roots:

| Config phase at startup | `local_root` | Resolution |
| --- | --- | --- |
| `preparing` | old | P1 crashed between its two writes. The source was never fenced, so bind it normally. If a destination exists whose marker is `staging` with this `relocation_id` and it contains nothing outside the P1-owned set (see P1; `root.lock` and marker-write temporaries included, and any member may be missing), discard it with the §12 terminal teardown procedure. Otherwise leave it and report it. Mark the relocation `failed`. |
| `copying` or `verified` | old | Old root authoritative. If its marker is `fenced` with this `relocation_id`, lift the fence back to `active`, then bind it normally. Mark the relocation `failed` in config. The destination is staging: delete it automatically **only** when its marker carries this `relocation_id`, `state: staging` and `role: destination` (with or without a `phase: aborted` left by an interrupted teardown), and it contains nothing but PRKS components, using the §12 terminal teardown procedure; otherwise leave it and report it. The user may retry. |
| `committed` | new | New root authoritative. The source is already `fenced` (P2). If the destination marker is still `staging` with the same `relocation_id`, perform P6 as specified: (a) take the lease and bind the `staging` root under the committed record, then (b) write the single activation. If the marker is already `active` with no role, P6(b) already landed, and PRKS binds normally. Attempt P7, P8 and P9 idempotently. A P7 that cannot reach the old root is retried at later starts and blocks nothing. |
| `source_retired` | new | Bind normally and redo P8 and P9 idempotently. |
| `retained` | new | Normal operation. Diagnostics show the retained old copy. |
| `retained_residuals` | new | Normal operation, but unresolved (§5.1): diagnostics list the kept inbox files in the retired old root and offer import or discard; root selection and new moves refuse until each is handled, then a P10 rerun completes the teardown. |
| `failed` | old | Old root authoritative. If its marker is still `fenced` with this `relocation_id` (a crash between revert steps 2 and 3, or a failed-move recovery interrupted before unfencing), lift the fence back to `active` before binding, the same step as in the `copying`/`verified` row. Then operate normally, with a notice offering "discard failed move", which only cleans up a leftover `staging` destination, with the §12 terminal teardown procedure. |

A failure during P6 (rebind) after commit is handled like any failed bind of
the configured root: PRKS reports a configuration error and does **not** fall
back to the old root. Automatic fallback is refused because, once any mutation
commits in the new root, the old root is a stale snapshot from P2 and binding
it would fork the library.

**Revert move** is offered whenever the destination marker is still
`staging`. That covers two cases: P6(a) failed, or P6(a) succeeded and P6(b)
failed. In the second case this process may already hold the destination's
lease and a live binding. Revert therefore runs in this fixed order, with the
**move scope held throughout**, and its exclusive rebind barrier (§8.2 Gate admission) held from step 1 through step 4:

1. Drop the destination binding through a new `unbind_storage()` primitive
   (Phase E). It publishes "no storage bound" the same way `bind_storage()`
   publishes a config, and every storage request then gets a
   storage-maintenance status. Today's code has no such state: after a
   failed rebind, `bind_storage()` only rolls back to the previous binding. Do **not** bind the source yet: it is
   still `fenced`, and the config still says `committed`, so §7.1 does not
   authorize it. **Keep holding the destination's lease.**
2. Atomically set the config back to `from`, with phase `failed`. From this
   write on, no committed record exists that could authorize activating the
   destination.
3. Lift the old `fenced` marker back to `active`. The `failed` record for
   this `relocation_id` authorizes this, exactly as §8.3 recovery would.
4. Bind the now-`active` source through the ordinary binding rule, taking its
   lease.
5. Release the destination's lease.
6. Only then return from the handler, releasing the barrier and the move scope.

The destination lease is held through steps 2–4 because the move scope is process-local
(§12). If the lease were released while the config still said `committed`,
another process could take it, activate the `staging` destination from that
record and bind it, leaving two active roots.

A crash in the middle of revert is resolved by §8.3 recovery. While the config
still says `committed`, recovery completes the move instead: P6 is idempotent
on a `staging` destination. Once the config says `failed` with `local_root =
from`, recovery lifts the fence. Either way a crash leaves exactly one
bindable outcome. The dead process's lease is an OS lock, which the kernel
releases when the process exits (§12).

Reverting is safe because **no process** has admitted a mutation to either root
since P2. The source has been fenced throughout, the destination was never
generically bindable, and this process never released its move scope. Once P6(b)
has written `active`, revert is no longer offered.

### 8.4 Identity survives

- Asset IDs, storage keys, `works.file_path` (`/api/pdfs/<basename>`) and
  `pending_pdf_cleanup` names are all root-relative, so relocation rewrites
  **no** database row.
- `processing_files.abs_path` is the exception until Phase B makes it derived.
  Until then relocation must reuse the restore rewrite
  (`_rewrite_processing_abs_paths`) on the destination copy, before P4. Phase
  E must not ship before that fix or that reuse.
- Browser and offline caches key on `/api/pdfs/<basename>` or, later, the Asset
  ID, so they stay valid.

### 8.5 Why copy, not rename

A same-filesystem rename is O(1) and tempting. It is not the first
implementation, because it destroys the old root at the moment of doing it, so
a crash mid-move leaves both roots partial. Making it safe needs a
per-component rename journal like restore's. That is a later optimization, and
only for same-device moves. Copying costs time and 2× space (V10 checks that),
and it buys the invariant that the old library stays intact until the user
says otherwise.

Mutations are blocked for the whole copy in the first implementation. A later
optimization is a two-pass copy: bulk copy in a separate read-mode request
or job, outside the move scope, then a short pass under the move scope that copies only objects changed since the first pass. That is
possible because new objects get fresh names and in-place rewrites bump
`content_generation`. It is not needed for the first version.

Derived indexes are not copied, even though copying could save rebuild time.
The text-index fingerprint includes `st_mtime_ns`, so a copy that does not
preserve mtimes forces re-extraction anyway. Rebuilding is the one path that
is always correct.

### 8.6 Relationship to backup and restore

Relocation is not backup, and a relocation never overwrites or removes a
backup. Backup and restore keep their existing contract. Relocation reuses
their pieces: the snapshot API, per-file hashing, the journal durability
rules, and the refusal of links. Relocation and restore are mutually
exclusive: each refuses to start while the other's journal is open.

### 8.7 Administrator-managed roots

When the root comes from `--storage-root` or `PRKS_STORAGE`, PRKS offers an
offline command-line tool. The server is stopped for the whole sequence.

**The offline journal lives on the volumes, not in the bootstrap config.** For
CLI- and environment-selected roots the bootstrap file is not the selector, and
in containers it is ephemeral (§5.2): a one-off container that runs
`relocate` or `finalize` takes its config directory with it. The offline flow
therefore records every phase in the **two root markers**, which are
durable wherever the volumes are:

- the destination marker: `state: staging` with no `relocation.phase` from
  P1 through P4. The offline commit then adds `relocation.phase:
  "committed"`, still with `state: staging`. P6(b) replaces it with `state:
  active, relocation: null`. An abort sets `relocation.phase: "aborted"`
  instead;
- the source marker's `fenced`/`retired` state.

If a persistent bootstrap file happens to exist, `relocate`, `finalize` and
`abort` also mirror the phase into it, best-effort. Recovery **never** depends
on that mirror for these roots.

1. `python prks_app.py storage relocate --to PATH` performs P0–P4, including the
   P2 fence on the source. Its P0 also refuses while the source marker carries
   `moved_from.residuals: true`, with the same unresolved semantics as the
   config's `retained_residuals` (§5.1): the refusal names the kept files in
   the older retired root and offers import or discard. So a later offline
   move can never replace this root's `moved_from`, and offline P6(b), which
   writes `moved_from` only on the new destination's fresh marker, never
   meets an unresolved flag. **Before writing any P1 state, it takes the
   destination lock and then the source lock, and holds both until it exits
   at the end of P4** (§12). If the source lock is busy, it releases the
   destination lock and removes the lock scaffold it created: `root.lock`,
   then the emptied `.prks-maintenance/`, then the destination directory
   itself if `relocate` created it and it is now empty. That scaffold
   is `.prks-maintenance/root.lock` with no marker, which V3 treats as a
   preflight scaffold. It then refuses, so a losing `relocate` never leaves
   a P1 marker behind.
   A concurrent `finalize` or `abort` therefore finds a lock busy and refuses
   for the whole copy. As its last write, after P4 has verified the copy and
   while it still holds both locks, `relocate` sets `relocation.verified:
   true` on the destination marker. Because P4 follows the durable P2 fence,
   `verified: true` is volume-durable proof on the destination that the
   source was fenced. **Its durable progress lives on the volumes, not in
   the bootstrap config.** P1's record is the destination marker, written
   under the destination lock: `state: staging` with `relocation: {id, role:
   destination, peer_hint}`. The copy manifest lives in
   `<dest>/.prks-maintenance/relocation/<id>/`. The P2 fence carries the same
   `relocation_id`. The P1 and P4 bootstrap writes of the in-app flow are
   replaced by these; any bootstrap write is only the best-effort mirror. An
   interrupted `relocate` is resumed by rerunning it, which verifies the
   matching `relocation_id` on both markers, or undone with `abort`.
   **P1-only crash.** If `relocate` died after creating the destination but
   before the P2 fence, the source is still `active` and carries no
   `relocation_id`. Only in that exact state is the both-markers rule
   relaxed. Both commands recognize it as a **P1-only staging destination**:
   its marker is `staging`, has no `verified` and no `phase`, carries the
   source's `storage_root_id`, and has a `peer_hint` naming the source; the
   source is `active` and unfenced; and the destination holds nothing
   outside the P1-owned set defined in §8.2 P1. That set is the marker,
   `.prks-maintenance/root.lock`, an empty `relocation/<id>/` and
   marker-write temporaries, and any of them may be missing. Unexpected
   content makes both commands refuse. A rerun of `relocate` resumes it
   from P2, fencing the source with that same `relocation_id`. `abort
   --to NEWPATH` discards it and leaves the source untouched, since it was never fenced.
   Both commands still take the destination lock, then the source lock.
   `finalize` and `abort` likewise refuse unless both markers name the same
   `relocation_id`. There are two exceptions: `finalize` with an
   unreachable source, which checks the destination marker alone (below),
   and an `abort` rerun after the source was already unfenced (below). The destination is left `staging`, and the command
   prints the new path and the `relocation_id`. The old library's data is
   untouched. **Once `relocate` has fenced the source, neither root is
   bindable.** Because the selector lives outside PRKS, nothing can prove
   which way the move went, so both ends stay closed until the administrator
   says. The one state short of that is the **P1-only crash** above: the
   source is still `active` with no relocation role and carries no pointer to
   the destination, so it stays **authoritative and bindable**, and a start
   that selects it binds it normally. Only the `staging` destination is
   refused, and a rerun of `relocate` or `abort --to NEWPATH` resolves it.
2. The administrator does one of two things:
   - **Complete the move.** Change the environment or the mount so the
     selector names the new root, then run `python prks_app.py storage
     finalize --root NEWPATH`. The selector change alone is **not** the commit,
     because it is not a durable PRKS record. **Lock first, then check.** Both `finalize` and `abort` first take the
     **destination** root lock, then the **source** root lock, always in
     that order, so the two commands cannot deadlock. Only while holding
     both do they read and validate `state`, `relocation_id` and `phase` on
     both markers. They keep both locks until their last marker write. No
     transition is ever based on a reading taken before the locks were
     held, so a concurrent `finalize` and `abort` are strictly ordered, and
     the second one re-reads and refuses. **Unreachable source.** `finalize`
     distinguishes a source lock that is **busy** from a source that
     **cannot be opened**, for example a removed disk or an unmounted volume.
     If the lock is busy, it refuses. If the source is unreachable, it
     proceeds under the destination lock alone, **but only if the destination
     marker proves that P2 completed**. That proof is
     `relocation.verified: true` (below), which `relocate` writes only after
     the source fence is durable. It then validates the destination marker
     (`relocation_id`, `peer_hint`, `verified`), performs P5 and P6(b), and
     leaves P7 to the best-effort retry. Without `verified: true` the fence
     may never have landed, and activating would leave an unfenced source
     with the same ID. So `finalize` refuses, and tells the operator to
     restore access to the source or to discard the staging tree. This is safe because
     `abort` must hold the destination lock too, so the two still exclude
     each other. `abort` has no such exception: unfencing the source needs
     the source, so `abort` always requires both locks. **Precondition, mirroring
     `abort`:** the destination marker is `staging` for this `relocation_id`,
     with `relocation.phase` absent or `"committed"`. The latter case is a
     rerun. **When `phase` is absent, `finalize` always requires
     `relocation.verified: true`**, whether or not the source is reachable. A
     `relocate` that crashed after the P2 fence but before P4 finished leaves
     a fenced source and a `staging` destination without `verified`, and its
     copy may be incomplete. `finalize` refuses that state and tells the
     operator to rerun `relocate`, which resumes P3 and P4 and writes
     `verified`, or to run `abort`. It never activates an unverified copy.
     `phase: "committed"` is written only after `verified`, so a rerun needs
     no second check. `phase: "aborted"` is **terminal**: `finalize` refuses it,
     because `abort` may already have unfenced the source, and points the
     operator at rerunning `abort` to finish discarding the leftover tree.
     Only then does `finalize` perform the same durable steps as the in-app
     flow, in the same order:
     1. (The locks are already held, per the rule above: both of them, or
        only the destination's under the unreachable-source exception. The
        destination's is its `active_process` lease.)
     2. **P5 (offline commit):** atomically rewrite the **destination marker**
        to `state: staging`, `relocation: {id, role: destination, peer_hint:
        <source root>, phase: "committed"}`. It keeps the P1 `peer_hint`, so a
        committed destination always names its source. This volume-durable write is the commit.
     3. **P6(b):** the single activation write on the same marker, which
        clears the relocation role, records the lease, and **must** write
        `moved_from: {id, peer_hint}`, copied from the committed marker.
     4. **P7:** retire the fenced source named in the destination marker. If
        the source is not reachable it stays `fenced`, which is already
        unbindable.
     5. Release the lease on exit. The server is stopped, so nothing is bound.

     **Recovery from the markers alone.** On the next start, or on a rerun of
     `finalize`, suppose the selected root is a `staging` destination whose
     marker says `phase: committed` for its `relocation_id`. That marker is
     proof the move committed (§7.1), so PRKS performs P6 and then attempts
     P7. Suppose instead the selected root is already `active`, with a
     `moved_from` note: `finalize` crashed after P6(b) but before P7. It binds
     normally, and PRKS then retries P7 on the source named by
     `moved_from.peer_hint`. That retry is a no-op if the source is already
     `retired` or unreachable, and it never blocks binding. The same retry
     runs at every start until the source is `retired`. A `staging`
     destination **without** `committed` is refused and names
     `finalize`/`abort`. A `fenced` source whose peer destination is
     committed or active is never unfenced.
   - **Abandon the move.** Leave the selector unchanged and run `python
     prks_app.py storage abort --root OLDPATH [--to NEWPATH]`. It finds the
     destination from the source's `relocation.peer_hint`. When the source
     carries no relocation record, as after a P1-only crash or after an
     earlier `abort` had already unfenced it, `--to NEWPATH` is **required**,
     because nothing on the source names the destination and the bootstrap
     file may be gone. If both are available and disagree, `abort` refuses.
     It follows the same **lock first,
     then check** rule as `finalize`: destination lock, then source lock, then
     re-read both markers. **Precondition, the same as
     in-app revert:** the destination marker is still `staging` with this
     `relocation_id`, **and not `phase: committed`**. Once `finalize` has
     written its offline commit, or P6(b) has written `active`, `abort`
     refuses. It tells the operator to rerun `finalize` or start PRKS, and
     either completes the move from the markers. It never unfences the source
     next to a committed or active destination. When the precondition holds,
     `abort`:
     1. Atomically rewrites the destination marker to `phase: aborted`,
        which is never bindable. This is the volume-durable abort record.
     2. Lifts the source fence back to `active`, authorized by that record.
     3. Discards the destination under the §8.3 deletion rule, using the
        §12 **terminal teardown** procedure (the marker is already `aborted`
        from step 1). That procedure closes the destination lock handle
        before removing the marker and `root.lock`, which Windows requires.
     4. Releases the source lock.

     **Rerun after a crash between steps 2 and 3** (`--to NEWPATH` required, as above). The source is then
     `active` with no relocation role, so it no longer names this
     `relocation_id`. A rerun of `abort` accepts exactly that pairing, still
     under both locks: the destination is `staging` with `phase: aborted` for
     this `relocation_id`, and the source is `active`, has no relocation role,
     carries the same `storage_root_id`, and is at the path in the
     destination's `peer_hint`. It then skips steps 1 and 2 and only discards
     the destination. Any other pairing makes it refuse.

   Every crash point leaves the markers in a state with exactly one
   resolution. It holds in a container whose bootstrap file is gone:
   - **committed, not activated:** a start or `finalize` completes the move.
   - **activated, source not retired:** a start retries P7.
   - **aborted, source still fenced:** a start refuses both ends and names
     `abort`. Rerunning `abort` lifts the fence and discards the destination;
     a plain start does not unfence.
   - **aborted, source already unfenced:** the source binds normally. The
     leftover destination stays unbindable, and `abort` or "discard failed
     move" removes it. `finalize` refuses it. §8.3's bootstrap-journal recovery applies to
   in-app moves of config-file roots, not to this flow.
3. The administrator starts PRKS.

If PRKS is started before step 2, whichever root the selector names is
refused, except in the P1-only crash state, where the still-`active` source
binds normally and only the `staging` destination is refused. The message names the relocation and both commands (§7.1). The
administrator's configured library is therefore never opened from a stale or
half-moved copy, and a stopped sequence always resolves with one explicit
command. `storage verify [--root PATH]` runs
§7.2 and the catalogue audit read-only.

**Docker and other volume moves use the same flow.** A raw copy of a stopped
volume duplicates an `active` marker with the same `storage_root_id`. Both
copies would then be bindable, and restarting the old Compose setup would fork
the library. A raw copy is therefore **not a supported move**. The supported
container runbook runs the same commands in a one-off container with both
volumes mounted:

1. `storage relocate --to /new`.
2. Switch the Compose mount.
3. `storage finalize --root /new`.

The old volume ends `fenced` or `retired` and refuses to bind. A cold copy
taken as a *backup* (the wiki's emergency procedure) is not a live library.
Bringing one back into use is a restore decision, and PRKS cannot tell such a
copy from its original, so the runbook tells the operator never to start both.

---

## 9. PostgreSQL relationship

This follows #310. It does not design the PostgreSQL migration.

### 9.1 Two locations, one library

```
                 ┌──────────── one PRKS library ────────────┐
 relational ───► │ database locator (connection config)     │ ◄── deployment layer; never moved by #311
                 │   SQLite era: <data root>/prks_data.db   │
                 │   PostgreSQL: host/db, local or remote   │
 file-backed ──► │ data root / storage backend              │ ◄── #311: choose, validate, move
                 └──────────────────────────────────────────┘
          paired by storage_root_id (marker ⇄ database record)
```

- **SQLite era.** The database is a canonical component of the data root, and
  moving the root moves it (§8, P3).
- **PostgreSQL era.** The data root holds no relational data. Choosing, opening
  or moving the root **never** touches the database. The database stores the
  `storage_root_id` it expects, as one row of storage-binding metadata, a
  schema decision for the PostgreSQL migration design. PRKS refuses to start
  when the two disagree, and offers "this database belongs to root X" as the
  diagnostic. Relocation carries the ID to the destination (§7.1, P1), so the
  pairing survives a move without any database write.
- **Bundled or local PostgreSQL** (desktop packaging, #46): where its data
  directory lives is a packaging decision. It must **not** be placed inside
  the PRKS data root by default, because §8 copies files and copying a live
  PostgreSQL cluster directory is unsafe. A packaging design that wants "one
  folder holds everything" must bring a PostgreSQL-aware move procedure.
- The migration SQLite → PostgreSQL is its own design. From #311 it needs one
  thing: the migrated database records the root's `storage_root_id`, and the
  SQLite file becomes a retired, non-canonical leftover in the root.

### 9.2 What the user sees

Settings → Storage presents **two rows under one library heading**, with
honest verbs:

| Row | SQLite era | PostgreSQL era |
| --- | --- | --- |
| Library files | path · free space · Open folder · Move… | same |
| Library database | "Stored with the library files" (no separate action) | "PostgreSQL · host/db · managed by your deployment" (read-only) |

"Move library…" in the PostgreSQL era says **"Moves files only. The database
stays where it is."**

### 9.3 Backup and restore

A PRKS backup stays **one archive for one library**: a relational snapshot plus
canonical objects, whatever their locations. The archive format is unchanged
for SQLite. A PostgreSQL archive format is part of the migration design.
Restore replaces **both halves together** and then rewrites the pairing
record to the target root's `storage_root_id`. The UI says this plainly:
"Restoring replaces this library's database and files." Backups continue to
exclude deployment configuration: the root path, the database locator and the
bootstrap file.

---

## 10. The StorageBackend boundary

### 10.1 Shape

```python
@dataclass(frozen=True)
class StorageKey:
    namespace: str      # one of a fixed set: "asset-objects", "portraits", …
    name: str           # one validated segment; issued by mint_name()

@dataclass(frozen=True)
class ObjectInfo:
    key: StorageKey
    size: int
    version: str        # opaque; local: f"{st_mtime_ns}:{st_size}:{st_ino}"; S3: ETag/VersionId
    sha256: str | None  # set when this call computed it (put_new/replace/verify)

class StorageBackend(Protocol):
    backend_type: str                                   # "local", later "s3"
    def put_new(self, key, write: Callable[[BinaryIO], None], *, hash: bool = True) -> ObjectInfo: ...
    def replace(self, key, write: Callable[[BinaryIO], None], *, hash: bool = True) -> ObjectInfo: ...
    def open_read(self, key) -> BinaryIO: ...           # seekable, for HTTP Range
    def stat(self, key) -> ObjectInfo | None: ...       # None = absent
    def delete(self, key) -> bool: ...                  # True = gone (removed or already absent)
    def verify(self, key, sha256: str) -> bool | None: ...  # None = absent or unreadable
    def iter_keys(self, namespace: str) -> Iterator[StorageKey]: ...  # maintenance only

def mint_name(namespace: str, hint: str) -> str: ...   # backend-neutral; today's mint_managed_pdf_filename rules
```

That is the whole generic surface. There are no queries, transactions, metadata
maps, ACLs, multipart APIs or generic repository.

### 10.2 Mandatory semantics (every backend)

| Operation | Contract |
| --- | --- |
| `put_new` | **Exclusive.** Fails with `ObjectExists` if the key is present, and never overwrites. **Atomic publication:** no reader ever observes a partial object under the key. **Durable before return:** on success the bytes survive a crash, which is today's "barrier precedes the report". On failure nothing remains under the key. The hash is computed on the stream, which is how #60's `ingest_sha256` can be taken before linearization. |
| `replace` | **Atomic:** readers see the whole old or the whole new object. Durable before return. The key must exist. It performs **no** reference or ownership checks: those belong to the caller (§12). |
| `open_read` | Read-after-write consistent with any completed `put_new` or `replace`. |
| `stat` | Returns size and an opaque version token. It never follows links (local). |
| `delete` | Idempotent. An already-absent key is success. That matches the `FileNotFoundError` terminal state of `pending_pdf_cleanup`. |
| `verify` | Streams the object and compares its SHA-256, three-valued. |
| `iter_keys` | Only for maintenance: audit, orphan sweep, backup, relocation. **Never on a request path.** It ignores temporaries and reserved names. |
| Key validation | Every operation refuses an invalid key before touching storage. The name grammar is the current `safe_pdf_path_under_dir` rules plus a byte bound; legacy names that it accepts remain valid. |

### 10.3 LocalFilesystemStorage specifics

These are **not** part of the generic contract:

- File fsync with the macOS full barrier, directory fsync as best-effort, and
  sibling temporaries plus `os.replace`: `fs_durability`, unchanged.
- Key to path through `resolved_child_path` (containment, drive-qualified
  refusal), resolved beneath the snapshot of the root taken at bind time.
- Owner-only permissions on created directories.
- **`local_path(key)` as a context manager on a local-only extension
  protocol** (`LocalPathCapable`). It exists for infrastructure that needs a
  real file: `qpdf` linearization, PDF text extraction, thumbnail rendering,
  and zero-copy HTTP serving. An object-storage backend implements the same
  context manager by downloading to a temporary file. Callers use the path
  only inside the `with` block, and never store it, log it or return it
  through the API.
- Sharing the bytes of one key between two Assets is legal (#60 §9.3), and it
  is a catalogue fact, not a backend fact.

### 10.4 What the interface deliberately omits

- **Copy and move.** Copy-on-write is `open_read` + `put_new` to a minted
  name. Relocation copies **between** backends, so it uses `open_read` on one
  and `put_new` on the other. A server-side copy may be added later as an
  optimization for object storage.
- **Conditional replace (if-match).** Races on the working slot are already
  settled above the backend, by the key lock plus #60's `content_generation`
  compare-and-set (#60 §9.4). A backend-level precondition would duplicate
  that.
- **Immutable-object mode.** A future option, not a requirement. In it, every
  rewrite of a working slot mints a new key and swaps `storage_locator` in the
  catalogue transaction, and the old key gets a cleanup claim. That makes
  `replace` unnecessary, and it suits object storage and CDNs. It changes
  `/api/pdfs/<basename>` on every annotation save, so it only makes sense after
  #60 Slice G serves by Asset ID. It is recorded as the preferred direction for
  a future object-storage backend, not adopted now.

### 10.5 Object storage later

Nothing above requires a POSIX filesystem. An S3-compatible backend maps
`put_new` to a conditional `PutObject` with `If-None-Match: *`. `replace`
keeps §10.2's must-exist precondition: it reads the current ETag with
`HeadObject` (absent means `NotFound`), then issues a conditional `PutObject`
with `If-Match: <ETag>`. A `412` means the object was deleted or changed in
between, and it is reported as a failed precondition, never as a silent
create. The key-scoped lock (§12) makes that case exceptional. `stat` maps to
`HeadObject`, `delete` to `DeleteObject`, and
`iter_keys` to `ListObjectsV2` under the namespace prefix. Durability is the
service's. Phase F adds it only for a real deployment need, and adds no
dependency before then.

---

## 11. Path-sensitive systems

| System | Today | Target | Depends on storage abstraction? |
| --- | --- | --- | --- |
| Managed PDF creation (`store_new_managed_pdf_bytes`, `_from_path`) | exclusive create in `pdfs_dir` | `put_new(asset-objects, mint_name(...))` | **Yes** (Phase B) |
| Managed PDF replacement and COW (`work_pdf_replace`) | sibling temporary + `os.replace`; COW by copy to an exclusive name; lock keyed by absolute path | `replace`; COW = `open_read` + `put_new`; lock keyed by `StorageKey` | **Yes** (Phase B) |
| Adoption of an existing managed name (`managed_pdf_adoption_guard`) | lock around the create commit | unchanged semantics, key-scoped lock. New features never adopt (#60 §9.3). | **Yes** (Phase B) |
| Pending PDF cleanup (`work_deletion`) | basename claims, three-valued check, `os.remove` | same claims (key names), `delete`; the retry pass is unchanged | **Yes** (Phase B), no schema change |
| Linearization (`pdf_linearize`) | `mkstemp` beside the source, `qpdf` on the path | `local_path(key)` for input; output written through `replace` | Uses the **local-only extension** |
| Materialization (`pdf_materialization`, annotations) | rewrite the working PDF in place | `replace`, with #60's pending-hash order | **Yes** (Phase B, then #60 Slice D) |
| Backup (`backup_restore`) | walks directories, SQLite backup API, `files/pdfs/*` archive paths | canonical objects enumerated through `iter_keys` per canonical namespace; archive layout **unchanged** | **Yes**, for enumeration (Phase B/C); journal and staging stay filesystem-specific |
| Restore | stages under `.prks-maintenance`, renames components, `bind_storage` rebind | unchanged for SQLite and the local backend; object-storage restore is a Phase F design item | Stays **filesystem-specific infrastructure** |
| Processing and import (`db_manager` processing, `for_processing/`) | inbox paths; `processing_files.abs_path` stored | the inbox stays a local directory (filesystem-specific). `abs_path` is derived from `rel_path` at read time, so the column is ignored and later dropped by a migration. Import copies into `put_new`. | Import: **yes**. The inbox: **no** |
| Thumbnails (`thumbs/`, `derived_cache_publish`, `prune_orphan_pdf_thumbnails`) | derived files keyed by Work ID, page and Asset | stays **derived, local** infrastructure. Rendering reads its input through `local_path(key)`. It moves to `cache/` only if a layout version changes. | Input only |
| PDF text index (`text_index`) | SQLite WAL in the root; fingerprint = `file_path` hash + size + mtime | stays derived and local. The fingerprint should move to #60's `content_sha256` + `content_generation` when Slice D lands, which removes the mtime dependency. | Input only |
| Research index | SQLite WAL in the root | derived, local | No |
| Profile images (`people/`, `person_image`) | cache paths from person ID + URL hash | `portraits` namespace through the backend | **Yes** (Phase B) |
| Future Web Work snapshots | — | `asset-objects`, one object per slot (§3.3) | **Yes**, from day one |
| #60 Asset lifecycle (fingerprint pass, Slice D) | — | uses `stat`, `verify`, `open_read`; claims by key name | **Yes** |
| Health and diagnostics | `managed_pdfs_missing` in backup; logs | a storage status (§11.1) | Reads the resolver and marker, plus `stat` |

**Modules that should remain filesystem-specific infrastructure:**
`storage/paths.py`, `storage/config.py` and the resolver, `fs_durability`, the
restore journal and staging, the processing inbox scanner, derived-cache
publishing, and the two derived SQLite indexes. **Modules that should depend on
the abstraction:** managed-PDF services, work deletion and cleanup,
materialization, portrait storage, import, backup enumeration, and every
future Asset writer.

### 11.1 Storage status (diagnostics contract)

The storage status is shown to the authenticated owner only:

- the effective root: the configured spelling and the resolved path;
- the source (`cli`, `env`, `config_file`, `platform_default`,
  `development_default`), and whether it can be changed in the app;
- `storage_root_id`, `layout_version`, `backend_type`;
- filesystem facts: free and total bytes, case sensitivity, normalization,
  directory-fsync support, and whether the filesystem type is recognized as
  network;
- counts only: missing canonical objects, pending cleanup claims, and orphan
  objects (maintenance scan);
- relocation state and any retained or failed copy;
- the database locator summary (§9.2).

Paths and key names **never appear in logs**. Minted names embed a sanitized
original filename, so key names are private data under the existing logging
rules (`log_safety`). The HTTP response that carries paths is not cached, and
it is gated like other owner-only settings. "Open folder" is a desktop
packaging action (#46), meaningful only when the browser and the server share
a machine. The server never launches a file manager itself.

---

## 12. Multi-process semantics

#310 requires that correctness not depend on process-local canonical state.
Today's process-local pieces are the `concurrency` gate,
`_PDF_PATH_LOCKS`, the `server.py` bound globals, and the index singletons.
Each is valid for one process. The storage contract states what each
operation needs, without designing job queues or distributed locking:

| Operation | Needs | Provided by |
| --- | --- | --- |
| Exclusive creation | no other writer can win the same key | **the backend** (`O_EXCL`, or S3 `If-None-Match`). It is already cross-process safe. Freshly minted names make contention practically impossible anyway. |
| Replacement (materialization, linearization) | at most one rewriter per key; the hash compare-and-set | a **key-scoped lock** plus #60's `content_generation` compare-and-set. The lock must be **cross-process** before a second PRKS process may write, for example a PostgreSQL advisory lock keyed by a hash of the key, or a row lock on the Asset. |
| Deletion (claims) | the reference check and the delete cannot interleave with an adoption or a new reference | the same key-scoped lock plus the single catalogue transaction (`settle_claim_if_referenced`). The retry pass must be single-runner per library (a lock or a lease). |
| Integrity checking (fingerprint, verify) | never commit a hash for bytes that changed while hashing | the compare-and-set on `(storage_locator, content_generation)` (#60 §12.4). Readers need no lock. |
| Relocation | nothing else writes during P2–P6 | the whole library in **maintenance mode**. With one process, that is the gate, entered at request admission and upgraded to an exclusive rebind barrier for P6 (§8.2). With several, it is a persistent maintenance flag checked by every writer, plus the rule that the in-app move requires this process to be the only one. Otherwise only the offline CLI (§8.7) is supported. |
| Restore | exclusive access to everything | the same maintenance mode. It is unchanged for one process. |
| Rebind | every process sees the same root | Roots change only at restart for multi-process deployments. Hot rebind (`bind_storage`) stays a single-process capability. |

**Until those locks are cross-process, PRKS supports exactly one server process
per data root.** Phase A enforces this with the **`active_process` lease**, and
refuses a second process that tries to bind the same root. That also catches
two PRKS installations accidentally configured with one root.

**The lease is a non-expiring OS lock, not a heartbeat.** It is an exclusive
advisory lock on `<root>/.prks-maintenance/root.lock`: `fcntl`/`flock` on
POSIX, `LockFileEx` on Windows. The lock is held for the whole time the root is
bound, including across hot rebind. Only the kernel releases it, when the
process closes it or exits. A process that is suspended, paused in a debugger
or slow to schedule keeps its lock, so no other process can decide it is stale
and bind the root underneath it. There is no timeout and no "stale lease" rule.
The marker's `active_process` field (PID, host, start time) is **diagnostic
only**: it names the holder in the error message, and it is never the
authority.

**Every marker write is serialized by that root's lock.** A write to
`prks-root.json` is an atomic replace (sibling temporary, fsync, `os.replace`,
directory fsync). Only a process holding that root's `root.lock` may perform
it. Nothing rewrites the marker periodically: there is no heartbeat, so no
renewal can race a fence or a state transition. During a move:

- the relocating process already holds the source lock, because it has the
  source bound, and it writes the P2 fence and the P7 retirement under it;
- it takes the destination lock at P1, when it creates the destination, and
  holds it through P6, so P6(a) finds it already held;
- the offline commands hold their locks for the **whole command**, not per
  write, in the same destination-then-source order:
  - `storage relocate` takes the destination lock and then the source lock
    **before writing any P1 state**, and holds both until it exits at the end
    of P4. If it cannot get the source lock, it drops the destination lock
    and its lock-only scaffold and refuses, before any marker exists.
  - `finalize` and `abort` take both locks before validating anything, and
    hold them through their last write (§8.7), except that `abort`'s
    destination teardown releases the destination lock at step 3 of the
    terminal teardown procedure below. The other exception: `finalize`
    may proceed under the destination lock alone when the source is
    **unreachable**, not merely busy, and the destination marker carries
    `verified: true`.

  Each command refuses to start if a lock it needs is held by someone else.
  So `finalize` and `abort` cannot run while `relocate` is still copying, and
  neither can start a second `relocate`.

A marker is therefore never written by two processes at once, and it is never
written by a process that does not own that root at that moment.

**Terminal teardown: the one exception.** Removing a root (discarding a
`staging` destination in-app or by `storage abort`, or P10 cleanup of a
retired source) ends with the marker and `root.lock` themselves being deleted.
Windows `LockFileEx` cannot delete a held file, so every teardown, on every
platform, uses this one procedure:

1. Under the root's lock, make the marker **durably terminal**: `phase:
   aborted` on a `staging` destination (in-app discard writes it too, not only
   the offline `abort`), or `state: retired` on a P10 source, which P7 has
   already written.
2. Still under the lock, delete every PRKS-known component except the marker
   and `.prks-maintenance/root.lock`. If anything must be kept, such as P10's
   protected inbox files, stop here: release the lock normally and leave the
   terminal marker in place, so the root stays recognizable. The teardown
   resumes from step 1 once nothing but the marker and `root.lock` remains.
3. Close the lock handle.
4. Remove the marker, then `root.lock`, then the emptied
   `.prks-maintenance/` and, for a discarded destination, the emptied root
   directory.

Step 4 changes a marker without holding the lock. That is allowed only here,
because the marker is already terminal and never bindable: binding, `finalize`
and a `relocate` resume all refuse an `aborted` or `retired` marker, so no
process can act on the root in the gap. A crash after step 3 leaves either the
terminal marker, which a rerun of the same teardown finishes (`abort`,
"discard failed move", §8.3 recovery or P10), or a lone `root.lock`, which V3
treats as preflight scaffold.

The lock depends on the filesystem honoring advisory locks, which is one of
the local-filesystem semantics §7.4 already requires for a live SQLite-era
root. A root whose filesystem cannot provide it, such as a network mount, is
refused by §7.4 for the same reason. When a later multi-process deployment
moves coordination to PostgreSQL, a fencing generation validated at each
commit may replace the OS lock. That is a Phase F/PostgreSQL design item.

---

## 13. Implementation phases

The frontend migration (#230/#290/#302/#303) that originally gated these
phases has finished, and Phase A is implemented (§18). Each phase leaves `master` deployable with no change in
behavior unless stated.

| Phase | Content | Schema? | API/OpenAPI? | UI? | Waits for |
| --- | --- | --- | --- | --- | --- |
| **A. Contract and foundation** | The root resolver with §5.2 precedence and `(root, source)`; `--storage-root`; normalization (V1); the platform-default table (§6) behind the distribution flag, so a source checkout still gets `data/`; the bootstrap config reader and its guarded compare-and-set writer (§5.1: config lock, re-read, expected-state check, atomic replace; no UI writes yet); the root marker (adopt on first bind, refuse wrong, retired or foreign roots); startup validation (§7.2, startup subset); the single-process lease; `StorageKey`, `ObjectInfo`, `StorageBackend`, `LocalFilesystemStorage` over the existing primitives, with contract tests shared by any future backend; `backup_storage_inventory` classification of the marker and config (operational, not backed up). The `/data/for_processing` special case is **kept unchanged** (§1.2). **Behavior must be identical for every existing deployment**: the same root is chosen and the same paths are derived, the processing inbox included; the only new files are the marker and the lease file `.prks-maintenance/root.lock`. | no | no | no | nothing (implemented; §18) |
| **B. Route managed files through the backend** | Managed PDF create, replace, COW, adoption, cleanup and linearization; portraits; import; backup enumeration; locks keyed by `StorageKey`; `processing_files.abs_path` derived from `rel_path` (the column is left unused). Behavior is preserved and proven by the existing managed-PDF, cleanup and backup tests unchanged. | no (dropping `abs_path` is a later migration) | no | no | A |
| **C. Asset identity coordination** | #60 Slice D lands on Phase B operations: `assets` locators are authoritative keys; the fingerprint pass uses `stat` and `verify`; the text-index fingerprint moves to `content_sha256`/`content_generation`; Slice G serves `/api/assets/{id}/content`. | #60's migrations | #60's typed API | no | **#60 Slice C/D** and B |
| **D. Selectable root and diagnostics** | Backend: storage status (§11.1), "choose" and "open another library" commands writing the bootstrap config, with the full §7.2 validation, and hot-rebinding under the admission-time relocation mode and rebind barrier (§8.1), with the whole rebind held as one transaction under the config lock, a test that no in-flight read observes a mixed binding, a two-process hot-rebind race test, and a test that opening an existing library leaves its `storage_root_id` unchanged; typed API with OpenAPI (#45 pattern). Then the Settings → Storage UI, per `DESIGN.md`. The packaged platform default is activated by #46 packaging. The optional `cache_root` for derived data (§7.4). Retire the `/data/for_processing` special case with the discovery rule and release note in §1.2. | no | **yes** | **yes** | A; #46 (packaged default and "Open folder") |
| **E. Relocation** | The §8 protocol: P0–P10 plus startup recovery, with the P2 source fence as the revocation mechanism; the offline `storage relocate`/`finalize`/`abort`/`verify` CLI first, then the in-app "Move library…". Crash tests at every phase boundary, as the restore suite already does. That includes the boundary between P1's config write and its destination-marker write, and the offline P1-only staging state, a crash during P0 that leaves a preflight scaffold, and resuming the same `relocation_id` through P0, and two concurrent offline `relocate` commands with different destinations, where the loser must refuse before writing any P1 marker. A gate-level test proves the in-app move route is classified at admission as backup mode (or the equivalent relocation mode), that reads continue while it runs, that mutations and backups wait, and that the move request itself does not deadlock. A config race test starts **open another library** immediately after P6 while P7 and P9 are delayed, and proves that the selection is refused while the move is unresolved. It also forces a stale P7/P9 write after a newer selection has been recorded, and proves the guarded compare-and-set leaves that newer selection intact. An inbox test adds, modifies and deletes source inbox files during P3, including a same-size in-place rewrite with a preserved `st_mtime_ns`, a rewrite during hashing, and an atomic replace or rename over the pathname while its old handle is being hashed, and proves the final pass converges on exactly the source bytes. An offline test crashes `relocate` after the P2 fence and before P4, and proves `finalize` refuses the unverified destination whether or not the source is reachable. Offline tests also cover an `abort` that crashes between unfencing the source and discarding the destination, proving a rerun finishes the discard, and a start after a P1-only crash that selects the source, proving it binds normally while the `staging` destination stays refused. Further offline tests cover `abort --to` after a P1-only crash and after unfencing, a P10 test with a post-P4 inbox file that proves the marker and config record survive until that file is imported or discarded, and that `open another library` and a new move are both refused while P10 is stopped on it; an offline variant sets `moved_from.residuals` on a CLI-selected root and proves `storage relocate` refuses and that no marker write drops the flag until the teardown completes, and Windows tests of the §12 terminal teardown for offline `abort`, P10 and one in-app discard path, asserting that the lock is released only after the marker is terminal and that a crash after the release leaves a state a rerun finishes. A second gate test holds an in-flight read across P6 and proves it finishes before the rebind barrier is granted, that reads arriving during P6 wait until it is released, and that no read observes a mixed binding; the same holds for revert's rebinds. | no | yes (move command, progress) | yes | B (for derived `abs_path`, or reuse the restore rewrite), D. **Not** #60. |
| **F. Object storage (optional)** | An S3-compatible backend passing the shared contract tests; restore and backup for it; immutable-object mode (§10.4). **Only when a deployment needs it.** | possibly a `storage_backend` column (#60) | config only | no | C, and in practice the PostgreSQL migration (#310) |

**Why A ships before D.** It gives no user-visible feature, but it makes
today's behavior explicit and testable (precedence, marker, validation) before
any UI can write the config file. It also protects existing users from the
unmounted-mount scenario immediately.

**Why E does not wait for #60.** Every canonical reference except
`processing_files.abs_path` is already root-relative (§1.2), so relocation
needs only Phase B's fix. It does not need Asset authority.

---

## 14. Deferred frontend and API work

None of this is in Phase A. Each item belongs to the phase named in §13
(mostly D and E); the frontend migration that first deferred them has finished.

- Every Settings UI: Storage panel, root display, choose, open another
  library, move, retained-copy cleanup, and warnings.
- "Open data folder" (it also needs #46 desktop packaging).
- Relocation progress UI and the failed or retained notices.
- The OpenAPI entries for storage status and commands. They are designed with
  #45 when Phase D starts.

Phase A touches no file under `frontend/` or `frontend-app/`, no Storybook
story and no OpenAPI document.

---

## 15. Rejected alternatives

| Alternative | Why rejected |
| --- | --- |
| Store the root in `app_settings` | It lives inside the root it would locate. With PostgreSQL it would couple file placement to the database. |
| Store the root in the browser | It is per device, and the server must know the root before any client connects. |
| Keep `PRKS_STORAGE` as the only user mechanism | It requires editing the environment for a personal install, and it cannot be persisted by a UI. It stays as the deployment override. |
| Absolute paths in `assets` | They break on relocation, restore to another root, container mount changes, and object storage. #60 already chose basenames. |
| A namespace per media type or per `assets.role` | A role change would force a byte move, and every new media type would need a new canonical component. One namespace is simpler. |
| Rename `pdfs/` to a neutral name now | It adds risk to every live library and archive for a cosmetic gain. The logical namespace name is enough. |
| Move by destructive rename | A crash mid-move leaves two partial roots. It is deferred as an optimization behind a journal (§8.5). |
| Automatically move source-checkout libraries to the platform default | Moving user data without a request is unacceptable. The development default stays. |
| Fall back to the next precedence source when the chosen root is invalid | It silently opens a different, possibly empty, library. |
| A generic repository or storage framework (metadata maps, ACLs, transactions) | #310 non-goal. The seven operations cover every current and planned caller. |
| A backend-owned reference counting or locking service | Reference truth is the catalogue. Duplicating it in the backend creates two authorities. |
| `platformdirs` dependency | Three table rows do not justify a dependency. |
| Support NFS/SMB for the live SQLite library "if it mounts" | SQLite WAL and locking semantics make it unsafe. It is revisited for the PostgreSQL era (§7.4). |

---

## 16. Open questions

None of these blocks approving the design or starting Phase A.

1. **The bootstrap file name and location on Linux:** `~/.config/prks/config.json`
   as proposed, or a PRKS-specific name such as `prks.json`. Cosmetic;
   decide in Phase A review.
2. **The `cache_root` option** (derived data on local disk while canonical
   objects sit on a network filesystem): Phase D, or only after PostgreSQL.
3. **A per-Asset `storage_backend` column:** only if a library ever spans
   backends. This is #60's schema call when Phase F is real.
4. **The retained-copy policy:** keep it indefinitely until the user cleans up
   (proposed), or offer cleanup after N days with a reminder.
5. **Adopting existing roots without a marker:** is "contains `prks_data.db` or
   `pdfs/`" the right heuristic, or should the first Phase A start require an
   explicit confirmation when the root came from the default rather than
   `PRKS_STORAGE`?
6. *(Resolved in review.)* The single-process lease is a non-expiring OS
   advisory lock (§12). A PID-and-heartbeat lease was rejected, because a
   suspended holder could resume writing after another process had declared
   it stale.

---

## 17. Conformance checks

**Against #310.** PostgreSQL is treated as the future canonical relational
store, and choosing a data root never moves an external database (§9). The
local filesystem stays first-class (S4). Object storage is optional and never
required for local users (§10.5, Phase F). Blobs stay out of the relational
store. There is no process-local canonical state in the target (§12). There is
no generic repository layer, and no new infrastructure dependency (§15).
Library, User and Actor entities are not invented (the `storage_root_id` is a
storage identity only, §7.1).

**Against #60.** It adopts #60's Asset entity, IDs, hashes, `content_generation`
compare-and-set, locators-as-basenames, cleanup-claim model and backup layout
without redefining them (§3). It adds no Asset columns. It defers the one
possible addition (`storage_backend`) to #60. It gates none of Slices A–C.

**The user's requirement to choose the data/library path.** It is preserved and
made concrete. A normal user chooses a location through a persisted,
UI-writable configuration (§5.1, Phase D). It survives restart and is
revalidated at every start (§7.2). It can be moved without database surgery
(§8.4), with identity unchanged. Administrators and containers keep full
control through `--storage-root` and `PRKS_STORAGE` (§5.2). A NAS or another
disk is supported wherever the filesystem provides the semantics PRKS relies
on, and PRKS says so explicitly instead of assuming (§7.4).

**What is implemented.** Phase A only (§18): runtime root selection,
validation, marker, lease and the unrouted backend foundation, with no schema,
API, OpenAPI, frontend, Storybook or dependency change. Phases B–F remain
proposed.

---

## 18. Phase A implementation record

**Where it lives.** Phase A adapts the existing owners instead of adding a
parallel configuration system:

| Concern | Before Phase A | Phase A |
| --- | --- | --- |
| Root selection | `StorageConfig.from_env()` | still the one entry; it asks `storage/resolver.py` (§5.2, V1, §6) and records `root_source` |
| Path derivation | `storage/paths.py` via `StorageConfig._from_parts` | unchanged |
| CLI | `prks_app.py` | `--storage-root`; `open_storage()` runs before restore recovery and `bind_storage()` |
| Bootstrap file | none | `storage/bootstrap_config.py` (reader, `BootstrapConfigStore` compare-and-set, `transaction()` for later multi-step writers) |
| Marker, validation, lease | none | `storage/root_marker.py`, `storage/root_binding.py`, `storage/preflight.py`, `storage/file_lock.py` |
| Bind | `server.bind_storage()` | unchanged, plus a guard refusing a root this process has not leased |
| Backup inventory | `backup_storage_inventory()` | adds `operational_root_entries` (marker, `root.lock`) and `external_operational` (bootstrap file and its lock) |
| Durability | `fs_durability` | adds `replace_file_atomically()` (sibling temporary, fsync, replace, directory sync) used by the marker and the bootstrap file |
| Backend boundary | none | `storage/objects.py` with the shared contract suite `tests/storage_backend_contract.py`; **no production operation uses it yet** |

**Choices made inside the design's latitude.**

- **Adoption (§7.1, open question 5) needs proof by type and content.** An
  unmarked root is adopted only when it holds the mode's database as a regular
  file with a SQLite header, `pdfs/` as a plain directory, or recognized
  restore state under `.prks-maintenance/` (`restore-journal.json`,
  `rollback/`, `restore-staging/`, `backup/`). The last case lets a root
  interrupted mid-restore, whose database and `pdfs/` may be moved away, still
  reach `recover_incomplete_restore()`. A same-named entry of the wrong type, or
  arbitrary maintenance content, is foreign. Open question 5 stays open.
- **Only a first run creates a root.** An empty or absent root becomes a new
  root for the CLI, `PRKS_STORAGE` and default sources, as V3 prescribes. A
  root selected by the **bootstrap file** must already carry a marker or be
  adoptable: absent or empty, it is far more likely an unmounted disk than a
  wish for a new library, so it is refused (`root_missing`) without creating
  anything. Choosing a genuinely new root is Phase D's command.
- **V7 and V13 cover the whole root.** Under the lease, startup walks the
  resolved root completely without following links (excluding the root path
  itself and top-level OS metadata such as `lost+found`). Any link or Windows
  reparse point is refused, and so is any entry -- directory or regular file,
  so a file bind mount is caught -- on a device other than the root's. The one
  relaxation is an overlayfs root, where unmodified files may report the lower
  layer's device: there only directories are held to the device rule, with a
  warning. A directory that cannot be listed, or an entry that cannot be
  inspected, is refused as `root_unreadable`, because the invariant cannot be
  proven for it. Components an override places outside the root are
  not part of the walk. Device numbers alone cannot see a bind mount from the
  same filesystem, across which `rename()` still fails with `EXDEV`, so the
  mount table is consulted too (`/proc/self/mountinfo` on Linux, `mount(8)`
  on macOS; on Windows a folder mount is a reparse point): any mount point
  strictly inside the root, directory or file, is refused. The root itself may
  be a mount point.
- **Root links are resolved once.** After the lease is taken, the process
  entry re-anchors every root-relative `StorageConfig` path beneath the leased
  `root_real` (`BoundRoot.anchor()`), so retargeting a root link afterwards
  cannot move database, PDF or index I/O to a root whose marker and lease were
  never checked. `configured_root` keeps the configured spelling.
- **V11 install directory.** A packaged build refuses any root inside the
  install directory. A source checkout refuses only the checkout itself or a
  root containing it, so existing self-hosted roots inside a checkout keep
  starting. The nested-marker scan is bounded to two levels and 2000 entries.
- **V9** refuses a filesystem type known with certainty to be a network mount,
  before anything is written: Linux types from `/proc/mounts` (`nfs`, `cifs`,
  `smb3`, network FUSE such as `fuse.sshfs`, …), macOS types from `mount(8)`
  (`smbfs`, `nfs`, `afpfs`, `webdav`), and on Windows a UNC path or a drive
  reported as `DRIVE_REMOTE`. Other FUSE types are uncertain and a filesystem
  the platform cannot classify is never assumed local; both are warned about.
  **V10** warns. **V5, V6, V8** run once per device; the result is cached in
  the marker's `filesystem_probe`, and a different `st_dev` re-probes.
- **Diagnostics.** Each bind records `active_process` (PID, host, start time)
  in the marker for the "already open" message. It is never read as authority.
- **Inbox for new sources.** A `config_file` or `platform_default` root uses
  `<root>/for_processing`. No existing deployment has those sources; the
  development default keeps `/data/for_processing` exactly (§1.2).
- **Relocation records.** A marker in `fenced`, `staging` or `retired` state,
  or an `active` marker with a relocation role, is refused with the relocation
  ID and peer. A bootstrap `relocation` record with a known phase is parsed and
  never acted on; the marker check refuses whichever end is not bindable.
- **`put_new` publication** is always one atomic no-overwrite step: `link`
  (POSIX), `rename` (Windows), or, on a filesystem without hard links,
  `renameat2(RENAME_NOREPLACE)` / `renamex_np(RENAME_EXCL)`. Where none exists
  it fails closed; the key is never reserved with an empty file.
- **Bootstrap file identity.** `BootstrapConfigStore` canonicalizes a
  symlinked config path to its target, so every alias shares one lock and a
  write replaces the target rather than the link.
- **Windows.** The lease is `msvcrt.locking` (`LockFile` on one byte, per
  handle). A marker replace that meets a sharing violation from a concurrent
  diagnostic reader is retried briefly.

**Deliberately not in Phase A.** Routing any managed-file operation through
`StorageBackend`, key-scoped locks, and a derived `processing_files.abs_path`
(Phase B); storage status, choose, open another library, hot rebind,
`unbind_storage()`, the Settings writer and the typed API (Phase D); every
relocation step, recovery, `storage relocate/finalize/abort/verify` and terminal
teardown (Phase E). Probing earlier default locations (§5.2) has nothing to
probe yet: a source checkout's only earlier default is its current one, and no
packaged build has shipped.

