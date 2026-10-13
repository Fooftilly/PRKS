# PostgreSQL canonical backend for PRKS 1.0: design (#613)

**Status: approved design (maintainer review of #619 at `7bb9545`,
2026-10-12).** This is the design gate that #613 requires before any
PostgreSQL implementation starts. It adds no code, schema, migration,
dependency or workflow. The decisions in [§0](#0-decisions) and the answers
in [§16](#16-maintainer-decisions) are approved. The slices in
[§15](#15-implementation-sequence) become focused implementation issues once
this document is merged, in the order and with the ownership boundaries given
there. Each implementation PR still needs the maintainer's explicit go before
it starts and before it merges.

It follows the shape of [work-identity-model.md](work-identity-model.md) and
[storage-architecture.md](storage-architecture.md): audit the code first, then
decide. Every claim about current behavior was checked against `master` at
`71353e0` (schema version 17), not taken from issue text. File references use
`path:line` at that commit.

**Release policy this design is written against** (maintainer decision, #613,
#614, #310, 2026-10-12):

- Before **v1.0.0**, backend APIs, database schemas, the database engine and
  internal implementations may be replaced without compatibility with
  prerelease versions. There is no SQLite → PostgreSQL upgrade path, no
  historical migration chain to keep, and no reversible dual-database period
  to maintain for compatibility's sake.
- Intended functionality and the important data-safety guarantees are kept
  while developing. Destructive work runs only against isolated fixtures, and
  PRKS never silently destroys unknown personal files.
- From **v1.0.0**, released library data survives supported upgrades through
  tested forward migrations, and the public API and backup format follow a
  documented versioning policy.

**Ownership, stated once.** This document owns the relational store, the
persistence and transaction boundary, the PostgreSQL schema, search on
PostgreSQL, the job/outbox mechanism, the PostgreSQL backup format and the
Compose deployment. It does not replace the designs it builds on:

- **#60** ([work-identity-model.md](work-identity-model.md)) owns the meaning
  of Work, Manifestation and Asset. This document only changes *mechanics*
  that the #60 status note already classifies as SQLite-era implementation
  choices (§6.4).
- **#311** ([storage-architecture.md](storage-architecture.md)) owns the data
  root, storage keys, the `StorageBackend` boundary, the root marker and
  relocation. This document fills in the PostgreSQL half of its §9 and §12.
- **#612** owns first-run configuration and the browser onboarding flow. This
  document supplies the server-side prerequisites it consumes (§9, §12).
- **#45** owns the versioned API contract and its policy. This document only
  states what the persistence change requires of it (§9.4, §13).
- **[local-first-sync.md](local-first-sync.md)** stays the authority for the
  durable-operation semantics. §10 says which of its server mechanics move and
  which do not.

Contents:

0. [Decisions](#0-decisions)
1. [Current-state inventory](#1-current-state-inventory)
2. [Technical limitations of the current backend](#2-technical-limitations-of-the-current-backend)
3. [Target architecture](#3-target-architecture)
4. [Technology choices and alternatives](#4-technology-choices-and-alternatives)
5. [Application and domain boundaries](#5-application-and-domain-boundaries)
6. [Schema](#6-schema)
7. [Transactions, locking, idempotency and concurrency](#7-transactions-locking-idempotency-and-concurrency)
8. [Search, Unicode and indexing](#8-search-unicode-and-indexing)
9. [Users, Libraries, authentication and authorization](#9-users-libraries-authentication-and-authorization)
10. [Browser save, sync and offline under a central server](#10-browser-save-sync-and-offline-under-a-central-server)
11. [Managed files, storage pairing, backup and restore](#11-managed-files-storage-pairing-backup-and-restore)
12. [Jobs, workers and deployment](#12-jobs-workers-and-deployment)
13. [Tests, gates and what is retired](#13-tests-gates-and-what-is-retired)
14. [Risk register](#14-risk-register)
15. [Implementation sequence](#15-implementation-sequence)
16. [Maintainer decisions](#16-maintainer-decisions)

---

## 0. Decisions

Each row is an approved decision (2026-10-12). §16 records the maintainer's
answers to the questions that needed an explicit choice.

| # | Topic | Decision |
| --- | --- | --- |
| PG1 | Engine and version | PostgreSQL **18** is the baseline and the only supported engine from 0.8. CI also runs against 19 once it is released (19 is at Beta 4 on 2026-10-12). Nothing relies on a feature newer than 18. |
| PG2 | Clean start | One fresh 0.8 schema. No SQLite → PostgreSQL data migration, no import of pre-0.8 archives, and no dual canonical database at runtime. A root that still holds `prks_data.db` is refused, never modified or deleted (§11.6). |
| PG3 | Driver | **psycopg 3** (synchronous API) with **psycopg_pool**. No ORM. SQL stays hand-written, in domain modules, with typed row results. |
| PG4 | Migrations | A small in-repo runner over ordered plain-SQL files, guarded by an advisory lock, with checksums of applied files. Before 1.0 the schema is a directory of per-domain baseline files that may be edited in place; at 1.0 it is frozen into a numbered baseline and only forward migrations follow (§4.4). |
| PG5 | Boundaries | Domain packages own **commands** (one transaction each, owning revision bumps and side-effect enqueueing) and **queries** (one read snapshot each). HTTP adapters and sync handlers call commands and queries and never see a connection. A static gate keeps psycopg imports inside `backend/db/` and the domain packages (§5). |
| PG6 | Tenancy | Every library-scoped row carries `library_id`. Child-to-parent foreign keys are composite `(library_id, …)`, so the database refuses a cross-Library reference. Entity IDs stay globally unique (§6.2). |
| PG7 | IDs | Keep today's prefixed text IDs (`W-<32 hex>` and so on) as the stored and wire identity, with a per-table `CHECK` on the format. Clients keep minting IDs for durable creates (§6.3). |
| PG8 | Work–Manifestation–Asset | Born authoritative. The baseline has no legacy `works` bibliographic or file columns, no mirror views or triggers, no `legacy_inferred_video_urls`, no `migration_quarantine` and no retirement guard. The legacy JSON shape is produced by the projection module only (§6.4). |
| PG9 | Text identity | One normalization rule (NFC, whitespace collapse, Unicode full case folding) shared by PostgreSQL, Python and TypeScript, proven by shared test vectors. Uniqueness is enforced by unique indexes on stored `*_key` columns (§8.1). |
| PG10 | Collation | Databases are created with the **builtin** locale provider (`PG_UNICODE_FAST`), so indexes never depend on the host's glibc or ICU version. Natural-language ordering for display uses an explicit ICU collation where it matters (§8.2). |
| PG11 | Search | PostgreSQL full-text search with a PRKS text-search configuration (`simple` dictionary plus `unaccent`, no stemming) and `pg_trgm` for substring and fuzzy matching. Search documents and extracted PDF text live in a rebuildable `derived` schema in the same database, excluded from backups (§8). |
| PG12 | Isolation and locking | Commands run at READ COMMITTED and lock the revision rows they decide on. Commands that span many aggregates (merges, bulk edits, restore) run SERIALIZABLE with bounded automatic retry. Multi-statement reads run in a REPEATABLE READ READ ONLY snapshot (§7). |
| PG13 | Process-local state | Nothing that correctness depends on stays in process memory. The access gate, save-confirm tokens, path and materialization locks, backup staging tokens and the cleanup runner move to rows, row locks or advisory locks (§7.5). |
| PG14 | Managed objects | Object bytes become **immutable per storage key**. Linearization, materialization and replacement write a new key and switch the Asset's locator in the catalogue transaction. The old key is released through the existing cleanup-claim model (§11.2). |
| PG15 | Jobs and outbox | One PostgreSQL `jobs` table, enqueued in the same transaction as the mutation that needs it, claimed with `FOR UPDATE SKIP LOCKED`, woken by `LISTEN/NOTIFY`. The jobs table **is** the internal outbox. No Redis, no broker, no separate event table until an external subscriber exists (§12.1). |
| PG16 | Authentication | One owner account per instance in 0.8, Argon2id password hashes (`argon2-cffi`), opaque server-side sessions in PostgreSQL, HttpOnly SameSite cookies, and the existing Origin checks hardened for HTTPS and proxies (§9). Row-level security is not used in 0.8. |
| PG17 | Library selection | A request's Library comes from the authenticated session in 0.8 (one Library). It travels in an explicit request context, so a later path- or header-based selector is a routing change, not a domain change (§9.4). |
| PG18 | Sync protocol | The durable-operation protocol, its conflict units, revisions, ACK shapes and the browser stores keep their semantics. Only the server mechanics move to PostgreSQL. Browser databases become scoped to instance, Library and user (§10). |
| PG19 | Backup | A PRKS-owned logical archive (format 2): a consistent snapshot of the Library's canonical rows in text `COPY` with a typed interchange contract, credential-free attribution identities, and every object the rows reference, verified before publishing. Restore drains writers behind a committed maintenance flag, commits in **one PostgreSQL transaction** that also enqueues its follow-up jobs, and advances a per-Library **epoch** that fences pre-restore jobs, requests and browser outboxes (§11.4). `pg_dump` stays an operator tool for whole-cluster disaster recovery, not the library backup. |
| PG20 | HTTP server | 0.8 keeps the stdlib HTTP adapter and connects it to a connection pool. Replacing it with a production WSGI/ASGI server is a separate research item that must finish before 1.0 (§4.5). |
| PG21 | Deployment | Compose runs `postgres:18` (or PRKS's image built from it, only if a PRKS `unaccent` rules file is needed, §8.3), a one-shot `migrate` service, `web` and `worker`. Database data lives in its own named volume, never inside the PRKS data root (§12.3). |
| PG22 | Transition | Build the PostgreSQL implementation beside the SQLite runtime, family by family, behind the same facade and the same parametrized tests. Switch once, then delete SQLite in the same release (§15). |

---

## 1. Current-state inventory

### 1.1 Process and request path

- **One process, one root.** `prks_app.py:75-101` runs the dependency gate,
  parses `StorageConfig`, takes the single-process `root.lock` lease
  (`open_storage`), recovers an interrupted restore, binds storage, sets up
  logging and calls `run_server`. The lease is a non-expiring OS lock
  (`backend/storage/file_lock.py:199-212`).
- **Thread per connection.** `PRKSThreadingTCPServer(ThreadingMixIn,
  TCPServer)` (`backend/server.py:3692`) with `daemon_threads`. The handler
  subclasses `SimpleHTTPRequestHandler` (`server.py:726`).
- **Routing.** `do_GET` … `do_DELETE` (`server.py:970-1455`) validate Host,
  validate Origin on `/api/` mutations, read the JSON body, enter the access
  gate and call one of six `handle_api_<method>` functions, each a long
  `if/elif` chain on the path. There are roughly 130 routes. Saved Views is the
  only family extracted into `backend/api/` so far.
- **No authentication.** No session, cookie or Authorization handling exists.
  Protection is the loopback default bind, the Host allow-list
  (`server.py:302-316`, `792-810`, `PRKS_TRUSTED_HOSTS`) and same-origin checks
  on mutations, which require `http` and reject `https` origins
  (`server.py:350-360`, `812-827`).
- **Startup work is synchronous** before `serve_forever`
  (`server.py:3706-3729`): thumbnail pruning, the pending-PDF-cleanup retry,
  text-index and research-index reconciliation, staging and backup-job
  cleanup. There are **no background threads or workers**; processing-inbox
  scanning is on demand (`GET /api/processing-files?rescan=1`).

### 1.2 Canonical persistence

- **One SQLite file per library** (`storage.db_path`). `LATEST_SCHEMA_VERSION
  = 17`, with a pre-v10 legacy bridge and eight ordered migrations v10–v17
  (`backend/db_migrations.py:28`, `3090-3127`). `validate_current_schema`
  (`db_migrations.py:941-1031`) checks tables, columns, index definitions,
  foreign keys, FTS columns and, for schema 17, the normalized stored SQL text
  of every Work-identity object.
- **Connections per operation, no pool.** `PRKSDatabase.get_connection()`
  (`backend/db_manager.py:1200-1204`) is `sqlite3.connect` with `Row` rows and
  `PRAGMA foreign_keys = ON`. The canonical database runs in the default
  rollback-journal mode with the default 5 s busy timeout. There are about 77
  connection call sites in `db_manager.py`, 18 in `research_network.py` and
  single ones in seven other modules.
- **Writers take `BEGIN IMMEDIATE`** (51 sites: 35 in `db_manager.py`, 12 in
  `research_network.py`). Multi-statement reads use plain `BEGIN` snapshots
  (22 sites), but only where callers opted in.
- **Gravity well.** `db_manager.py` is 5,477 lines and owns settings, Saved
  Views, the processing inbox, Work create/browse/search/detail, playlists,
  annotations, wiki links, folders, bulk actions, persons, person groups,
  roles, tags, publishers and BibTeX. `server.py` (3,742 lines) calls about 100
  of its methods through a module-global `db` and issues seven raw
  `execute_query` calls itself.
- **Sync handlers write SQL directly.** Eighteen `*_sync.py` modules receive the
  shared connection that `sync_protocol.process_operation` opened inside
  `BEGIN IMMEDIATE` (`backend/sync_protocol.py:144-189`) and run their own
  statements, sharing `*_on_conn` helpers with the REST endpoints.

### 1.3 Schema by domain

Every primary key is TEXT except two operational tables. Timestamps are
`CURRENT_TIMESTAMP` text, and date-like bibliographic fields are free text.

| Domain | Tables | Notable current mechanics |
| --- | --- | --- |
| Works and identity | `works`, `manifestations`, `assets`, `manifestation_relations`, `manifestation_identifiers`, `legacy_inferred_video_urls`, `work_retirement`, `work_retirement_guard`, `sync_work_lifecycle`, `migration_quarantine` | `works` is still authoritative for every field. The #60 entities are a trigger-maintained projection (`legacy_work_*_mirror` views, nine mirror triggers and two read-only guards, `backend/db_schema.sql:666-1050`). Pointer ownership is enforced by ten RAISE triggers and a transaction-scoped guard table; 24 triggers in all, three of them for FTS. Research and Private Notes are the `works.text_content` and `works.private_notes` columns. |
| Annotations | `annotations` | `geometry_json` text. The ID comes from the PDF viewer and is a global primary key. Materialization freshness is a pair of revision columns. |
| People | `persons`, `roles`, `person_groups`, `person_group_members`, `publishers`, `publisher_aliases` | `persons.aliases` is "JSON array string or comma separated". Role order ties break on `rowid`. |
| Organization | `folders`, `folder_files`, `tags`, `tag_aliases`, `work_tags`, `folder_tags`, `playlists`, `playlist_items`, `saved_views` | Folder titles are unique per parent through `LOWER(TRIM(title))`. Tag, alias, publisher, group and Saved View names are unique through ASCII-only `COLLATE NOCASE`. |
| Research network | `concepts`, `concept_aliases`, `concept_parents`, `positions`, `arguments`, `argument_verdicts`, `argument_target_positions`, `argument_target_arguments`, `argument_sources` | Concept matching is the only real Unicode normalization (NFC + `casefold`, `backend/research_markup.py:46-52`), but resolution scans every Concept in Python (`backend/research_network.py:229-245`). |
| Processing | `processing_files`, `processing_file_roles`, `processing_file_tags` | `abs_path` is the only absolute host path in canonical state; restore rewrites it. |
| Settings | `app_settings` | Key/value text; some values are JSON. |
| Sync | `sync_operations`, `sync_entity_revisions`, `sync_tag_lifecycle`, `sync_work_lifecycle` | Ledger never pruned. About 25 revision scope types; composite `scope_id`s are canonical JSON array text. |
| Operational | `pending_pdf_cleanup`, `schema_version` | Cleanup claims are canonical backup state (`backend/AGENTS.md`). |
| Derived (same file) | `works_fts` | FTS5 external-content table over `works` with three triggers. |
| Derived (separate files) | `prks_text_index.db`, `prks_research_index.db` | WAL-mode SQLite files, rebuilt on corruption, excluded from backup. |

### 1.4 SQLite-specific constructs

| Construct | Where (non-test) | PostgreSQL replacement |
| --- | --- | --- |
| `BEGIN IMMEDIATE` / plain `BEGIN` snapshots | 51 / 22 sites | Command and query transaction helpers (§7) |
| `PRAGMA foreign_keys`, `table_info`, `index_xinfo`, `foreign_key_check`, `integrity_check` | migrations, backup, validation | Real constraints always on; catalog-based schema check; `amcheck` only as an operator tool |
| FTS5 (`works_fts`, `work_text_index_fts`), `MATCH`, `rank`, external-content `'delete'`/`'rebuild'` | `db_schema.sql:251-266`, `text_index.py:415-455` | `tsvector`/`tsquery`, GIN, `ts_rank_cd` (§8.3) |
| `COLLATE NOCASE`, `LOWER()` equality, `LIKE` (ASCII-only folding) | 23 / many / 12 sites | Stored identity keys and unique indexes; `pg_trgm` for substring (§8) |
| JSON1 (`json_each`, `json_group_array`, `json_extract` in a trigger) | `work_projection.py:32-60`, `db_manager.py:381`, `db_schema.sql:958-982` | `= ANY(%s)` array parameters; `jsonb_agg`; no JSON in triggers |
| `GROUP_CONCAT(… ORDER BY …)` | `db_manager.py:344`, `4242`, `4495` | `string_agg(… ORDER BY …)` |
| `rowid` as order tiebreaker and FTS key | 51 hits | Explicit `order_index` or `created_at, id` ordering; no hidden row identity |
| `hex(randomblob(16))` IDs in triggers | 7 | Removed with the mirror triggers; IDs come from the application |
| Row-value `UPDATE … SET (a,b) = (SELECT …)`, `IS NOT` row comparison | mirror triggers | Removed with the mirror triggers |
| `INSERT … ON CONFLICT DO UPDATE/NOTHING` | ~30 | Same syntax; kept |
| `sqlite3.IntegrityError` / `OperationalError` string matching | `db_manager.py:1423`, `3415`, `5052`; `person_sync.py:85`; `work_lifecycle_sync.py:253` | SQLSTATE plus constraint name mapped to domain errors in one place (§5.4) |
| `Connection.backup()`, file renames of `-wal`/`-shm`/`-journal` sidecars | `backup_restore.py:1494-1509`, `2705` | Logical archive and one-transaction restore (§11.4) |
| Root adoption by SQLite file header | `storage/root_binding.py:497-514` | Database ↔ root pairing record (§11.1) |
| Seed templates by WAL checkpoint and `copytree` | `tests/e2e/harness.py:333-443` | `CREATE DATABASE … TEMPLATE` (§13.1) |

No custom SQL functions, collations or aggregates are registered, and the code
never uses `RETURNING`, `lastrowid`, `AUTOINCREMENT`, `ATTACH`, `VACUUM` or
`user_version`. That keeps the dialect port mechanical everywhere except the
mirror triggers (which disappear) and FTS (which is redesigned).

### 1.5 Concurrency and process-local state

- **`LibraryAccessGate`** (`backend/concurrency.py:60-186`), a process-local
  `threading.Condition`, lets reads overlap, **serializes every canonical
  mutation in the process**, lets backup block mutations but not reads, and
  makes restore exclusive. Some GETs are classified as mutations because they
  write caches (`concurrency.py:32-57`).
- **No retry on `SQLITE_BUSY`.** A lock error becomes a generic 500.
- **Process-local state that correctness depends on:**
  - save-confirm tokens `_PRKS_LAST_PDF_SAVE_TOKEN_BY_WORK` and
    `_PRKS_LAST_ANNOTATION_SAVE_TOKEN_BY_WORK` (`server.py:665-667`), read by
    `GET /api/works/:id/save-confirm`;
  - per-Work `_PDF_MATERIALIZATION_LOCKS` (`server.py:668-679`) and per-name
    `_PDF_PATH_LOCKS` (`backend/services/work_pdf_replace.py:60-77`);
  - backup staging tokens `_ready_backups` (`backend/backup_restore.py:221-222`);
  - the bound root and the index singletons (`storage/root_binding.py:208`,
    `text_index.py:1079`, `research_index.py:532`).
- `performance.py` is process-local by design and never needed for
  correctness; it stays per process (§12.4).

### 1.6 Search and derived indexes

- **Library search** (`db_manager.py:2688-2771`) is a union, in order of first
  appearance with no combined score, of: FTS5 prefix-AND over
  title/abstract/notes/author text; a LIKE on the primary Manifestation title
  and abstract; a per-token `LOWER(…) LIKE` fallback; and a substring match on
  linked people. Author and publisher filters use `LOWER(…) LIKE`.
- **Case folding is inconsistent.** Python lower-cases the query with Unicode
  rules, SQLite `LOWER()` and `LIKE` fold ASCII only, and FTS5 uses its
  default `unicode61` tokenizer (Unicode case folding; diacritics removed only
  from Latin script). No `tokenize=` option is set anywhere.
  `tests/test_publisher_catalog_queries.py:95-109` pins the ASCII-only
  behavior. **No test contains Greek or Cyrillic text.**
- **PDF text** lives in `prks_text_index.db` (`backend/text_index.py`):
  PyMuPDF page text capped at 2,000,000 characters, fingerprinted by path
  hash, size, mtime and extractor version, reconciled at startup and kept in
  sync inline in request threads (`server.py:1215`, `2820`, `3006`;
  `work_pdf_replace.py:788`).
- **Research index** (`prks_research_index.db`) stores Concept and Argument
  mentions parsed from Research Notes; it is rebuilt when its table set
  differs.

### 1.7 Backup, restore and storage

- **Archive format 1** (`backup_restore.py:63-127`): a ZIP with
  `data/prks_data.db` (from `Connection.backup()`), `files/pdfs/`,
  `files/people/`, optionally `files/for_processing/`, and a manifest with
  per-file SHA-256. Creation snapshots, checks integrity and foreign keys,
  archives, self-verifies and only then publishes.
- **Restore** validates fully before touching live state, then moves
  components through a durable journal (`prepared → moving_old →
  installing_new → canonical_installed → committed`) using
  `backend/fs_durability.py`, rebinds, rebuilds derived state and retries
  cleanup claims. Crash before `committed` restores the previous library.
- **Storage Phase A** (#311) is implemented: one resolver, the
  `prks-root.json` marker with `storage_root_id`, root validation, the
  single-process lease and an unrouted `StorageBackend` /
  `LocalFilesystemStorage`. Phase B (routing managed files through the
  backend) has not started.

### 1.8 Deployment

`Dockerfile` (python:3.12-slim + qpdf) and a one-service
`docker-compose.yml` with `./data:/data`, loopback publish by default and no
database service. Runtime dependencies are `PyMuPDF`, `Pillow` and `pydantic`;
there is no database driver because `sqlite3` is in the standard library.
`dependency-inventory.json` and the startup dependency gate pin them.

### 1.9 Tests and gates

- 180 top-level unit/API test modules plus 37 E2E modules. 64 construct
  `PRKSDatabase` directly, 20 import `sqlite3`, 24 apply `db_schema.sql`, 20
  exercise migrations. Each module builds its own temporary database; there is
  no shared fixture.
- Large SQLite-specific suites: `test_db_migrations.py` (1,632 lines),
  `test_backup_restore.py` (3,604), `test_restore_durability.py` (781),
  `test_text_index.py` (774), `test_sqlite_connection_lifetime.py`,
  `test_work_identity.py`.
- Static gates tied to SQLite: `scripts/check_schema_change.py` (runs the
  schema in `sqlite3.connect(":memory:")`, SCHEMA-GATE-001…009),
  `scripts/check_correlated_catalog_counts.py` (motivated by SQLite's
  per-row subquery execution), and parts of `scripts/check_invariants.py`
  that parse SQLite identifier quoting.
- No workflow defines a database service.

---

## 2. Technical limitations of the current backend

These are the reasons for the change. Each one is answered by a section below.

1. **One writer per process, one process per root.** All canonical mutations
   are serialized by an in-memory gate, and the lease forbids a second
   process. A worker process, a second API process or a CLI writing while the
   server runs is impossible by construction (§7, §12).
2. **Correctness depends on process memory.** Save-confirm tokens, path and
   materialization locks and backup tokens vanish on restart and cannot be
   shared (§7.5).
3. **No users, Libraries or authentication.** The only boundary is "who can
   reach the loopback port". Ownership and authorization have nowhere to live
   (§9).
4. **Unicode identity and search are ASCII-correct only.** Uniqueness of tags,
   aliases, publishers and groups, and every LIKE path, fold ASCII only. Greek
   and Cyrillic names that differ only by case are distinct, and searches miss
   them on the LIKE paths (§8).
5. **Persistence leaks everywhere.** SQL lives in a 5,477-line manager, in 18
   sync modules and in the HTTP adapter. Revision bumps and read snapshots are
   conventions that individual paths forget (discussion #583) (§5, §7).
6. **The #60 model is a projection, not the authority.** Mirror triggers,
   guard tables and quarantine exist only to evolve a SQLite schema in place.
   A clean start can make Manifestations and Assets authoritative at once
   (§6.4).
7. **Heavy work runs inline.** PDF text extraction, linearization and
   reconciliation run in request threads or block startup (§12.1).
8. **Backup and restore are file-format specific.** They depend on the SQLite
   backup API, PRAGMA checks and renaming the database file and its sidecars
   (§11).
9. **No server deployment story.** No database service, health probes,
   secrets handling or graceful restart (§12.3).

---

## 3. Target architecture

```text
 Web UI (Vue) ── durable ops ──┐      future clients (Android, MCP, capture)
   IndexedDB: queue / cache /  │                 │
   editor recovery (scoped)    │                 │
                               ▼                 ▼
                  HTTP adapter (stdlib in 0.8; framework later, §4.5)
                  host/origin checks · session auth · request context
                               │
              ┌────────────────┴─────────────────┐
              │  application layer (backend/domain/<family>)
              │  commands: validate → authorize → one transaction
              │            → revision bumps → enqueue jobs
              │  queries:  one read snapshot → typed rows → projection
              └────────────────┬─────────────────┘
                               │ backend/db: pool · tx helpers · errors · migrations
                               ▼
                         PostgreSQL 18
       ┌──────────────┬──────────────┬───────────────┬──────────────┐
   schema prks     schema derived   jobs (outbox)   sessions / identity
   canonical rows   search docs,    claimed with     instance, users,
   revisions,       PDF text,       SKIP LOCKED      libraries
   sync ledger      mentions
                               │
                    worker process (same codebase)
         text extraction · research reindex · object cleanup · backups
                               │
                StorageBackend (#311): LocalFilesystemStorage
                data root ⇄ database paired by storage_root_id
```

The shape is the #310 modular monolith. One codebase runs as `web`, `worker`
and one-shot admin commands. A personal installation may run the worker as a
thread inside `web` (§12.2). There is no microservice, broker or cache tier.

---

## 4. Technology choices and alternatives

### 4.1 PostgreSQL 18

As of 2026-10-12 the supported major versions are 14–18 (18.6 is current,
end of life November 2030); 19 is at Beta 4. 18 is the baseline because it is
the newest stable release with the longest support window, and because it
brings features this design uses directly:

| Feature (new in 18 unless noted) | Used for |
| --- | --- |
| `casefold()` and the builtin `PG_UNICODE_FAST` locale | Unicode full case folding in identity keys without ICU/glibc dependence (§8.1–8.2) |
| `uuidv7()` | Time-ordered surrogate keys for internal high-volume tables (jobs, sessions), where client-minted IDs are not needed |
| `OLD`/`NEW` in `RETURNING` | Commands that must report the previous value (convergent ACKs) without a second read |
| Asynchronous I/O | Faster sequential scans and vacuums; no code change |
| Data checksums on by default at `initdb` | Storage corruption detection in the Compose image |
| `pg_upgrade` keeps planner statistics | Smoother post-1.0 major upgrades |
| Deferrable foreign keys (all versions) | Work ↔ primary Manifestation ↔ primary Asset pointers without triggers (§6.4) |
| Transactional DDL (all versions) | All-or-nothing migrations and the restore commit (§4.4, §11.4) |

The 18 release notes warn that full-text search and `pg_trgm` behavior can
change under non-libc providers after `pg_upgrade`, and recommend reindexing
those indexes. The search indexes live in the rebuildable `derived` schema, so
"rebuild derived" is the supported answer after any major upgrade (§8.6).

Alternatives considered: **17** (no `casefold()`/`PG_UNICODE_FAST`; identity
keys would need ICU or Python-only folding), and **staying on SQLite** (keeps
every limitation in §2; #310 already rejected it as the long-term store).

### 4.2 Driver and data access

**psycopg 3** (3.3.x, Python ≥ 3.10), synchronous API, `dict_row` or typed
`class_row` factories, server-side parameter binding, `COPY` support for
backup and restore, `LISTEN/NOTIFY` for the worker, and good typing for
Pyright. Install `psycopg[binary]` for development and CI; the Docker image
may use `psycopg[c]` against the image's libpq (either is pinned in
`dependency-inventory.json`).

| Alternative | Why not now |
| --- | --- |
| asyncpg | Async-only; PRKS is synchronous and thread-per-request. Mixing an event loop into the stdlib server adds risk without a measured need. |
| psycopg async API | Same reason. Revisit only together with an ASGI server (§4.5). |
| SQLAlchemy ORM | #179/#310 reject a generic ORM rewrite. PRKS's rules (revisions, conflict units, composite ownership) are explicit SQL decisions; an ORM would hide them and add a unit-of-work model that competes with the sync protocol. |
| SQLAlchemy Core (no ORM) | Credible, but its value (dialect portability, expression building) matters little once PostgreSQL is the only engine, and it would be the only reason to adopt SQLAlchemy. Plain SQL in domain modules plus typed rows is smaller. Revisit if dynamic query building (Saved Views, advanced search) grows enough to need it. |
| pg8000 | Pure Python, slower, weaker `COPY` and typing support. |

### 4.3 Connection pooling

`psycopg_pool.ConnectionPool` (3.3.x) inside each process. Defaults:
`min_size=1`, `max_size=10` for `web` (`PRKS_DB_POOL_MAX`), `max_size=4` for
`worker`, `max_idle=300 s`, `timeout=10 s` to obtain a connection, `check`
on borrow. Every pooled connection sets `application_name`
(`prks-web`/`prks-worker`), `statement_timeout` (30 s default, longer for
named maintenance commands), `idle_in_transaction_session_timeout` (60 s) and
`lock_timeout` (5 s).

These defaults are for request and ordinary worker sessions. **Backup,
restore and `rebuild-derived` run on their own dedicated connection**, not a
pooled one, with `idle_in_transaction_session_timeout = 0` (a backup holds
its exported snapshot and the shared objects lock while it copies files
without issuing queries) and no inherited `statement_timeout`. Every lock
wait in those protocols runs inside an explicit short transaction that sets
its own bounded `lock_timeout` with `SET LOCAL` and commits as soon as the
lock is held. `SET LOCAL` is never issued in autocommit mode, where
PostgreSQL ignores it with a warning. The setting reverts at commit, and the
session-level advisory locks taken inside stay held. The R3 drain of
in-flight commands and the wait for a running backup copy (§11.4) use a
configurable drain timeout (default 5 min) whose expiry aborts the restore
cleanly, never the 5 s default. A rollback does not release session-level
advisory locks already taken in that transaction, so every abort path calls
`pg_advisory_unlock_all()` (or closes the dedicated connection) before it
finishes.

The thread-per-connection server can open more request threads than the pool
has connections. Pool exhaustion is a 503 with `Retry-After`, never a hang,
and a test pins that behavior.

PgBouncer is **not** part of the design. A personal or small self-hosted
deployment does not need it. If a deployment adds it, it must run in session
mode, because the job worker uses `LISTEN` and commands use advisory locks.

### 4.4 Schema migrations

**Recommendation: an in-repo runner over plain SQL files** (estimated at a few
hundred lines including tests), because PRKS already owns one and its rules
are specific:

- **Layout.** `backend/db/schema/` holds ordered files. Before 1.0 they are
  per-domain *baseline* files (`010_instance.sql`, `020_identity.sql`,
  `100_works.sql`, `110_people.sql`, …), each owned by one implementation lane
  (§15), applied in order to an empty database. They may be edited in place,
  and every fresh test database is rebuilt from them.
- **At v1.0.0** the baseline is frozen: the files are concatenated into
  `0001_baseline_v1.sql`, the runner records it as applied, and from then on
  only new numbered forward migrations are added. A migration file is never
  edited after release.
- **Runner.** `prks migrate` (and `web` startup when `PRKS_MIGRATE_ON_START=1`,
  the default for personal installs) opens its own dedicated connection,
  never a pooled one, takes `pg_advisory_lock` on a fixed key, and closes
  that connection on every success and failure path, which releases the
  lock (session-level advisory locks outlive the per-file commits). It
  creates `prks.schema_migrations(version, name, sha256, applied_at)`, refuses
  to run if an applied file's checksum changed or if the database is newer
  than the code, and applies each pending file in its own transaction. DDL
  failures abort and are never swallowed. Before 1.0 a changed checksum means
  a baseline file was edited in place after this database applied it; the
  database is not upgraded in place, so `migrate` and startup stop with a
  message naming the file and saying the instance must be recreated (PG2,
  clean start). Test and development databases are rebuilt from the files and
  never hit this.
- **Schema check.** A test builds a database from the files and compares its
  catalog (tables, columns, types, constraints, indexes, functions, extensions)
  against a checked-in normalized snapshot `backend/db/schema/snapshot.json`.
  After 1.0 the same test also compares a *migrated* old baseline with a fresh
  one. This replaces `validate_current_schema`'s SQL-text comparison and the
  SQLite parts of `check_schema_change.py`.
- **Startup validation** checks only `schema_migrations` and the
  instance/storage pairing; it never introspects every index on every start.

| Alternative | Trade-off |
| --- | --- |
| Alembic | Mature, but brings SQLAlchemy, Python-scripted revisions, a revision graph and autogenerate built for ORM models. PRKS would use none of the ORM side. |
| yoyo-migrations | Small and SQL-friendly, but its transaction and locking model differs from what PRKS needs (one advisory lock, per-file transactions, a snapshot test), and it would still need PRKS's checksum policy around it. |
| sqitch, dbmate, Flyway | Good tools, but each adds a non-Python runtime (Perl, Go binary, JVM) to the image and to every developer machine. |

### 4.5 HTTP server

The stdlib `http.server` stack is documented by Python as unsuitable for
production, and 1.0 is a self-hosted Web release. But replacing it is a
separate concern with its own risks (routing, streaming backup progress, PDF
range requests, static files, the access-policy checks), and #179 asks for no
framework migration for aesthetics alone. Therefore:

- **0.8** keeps the stdlib adapter. The PostgreSQL change touches it only to
  borrow connections through the request context, to add session
  authentication and health endpoints, and to remove the access gate.
- **Before 1.0**, a research/evaluation issue decides between keeping a
  hardened stdlib adapter behind a reverse proxy and moving to a WSGI or ASGI
  server (for example Starlette with uvicorn, or a WSGI app under a
  production server). Its prerequisite is the route-family extraction already
  tracked by #67, which this design's domain packages make easier. It is
  listed as a risk (§14, R9).

### 4.6 Jobs

A PRKS-owned PostgreSQL job table (§12.1). Alternatives:

| Alternative | Trade-off |
| --- | --- |
| Procrastinate | PostgreSQL-backed and well designed, but async-first and opinionated about its own schema, worker and retry model. PRKS's job set is small and needs the same transaction as canonical writes. |
| Celery / RQ / Dramatiq | Need Redis or RabbitMQ; #310 forbids a mandatory broker. |
| Threads inside the web process only | No durability across restarts, which is the current gap. |

### 4.7 Other libraries

- **`argon2-cffi`** for password hashing (already a #179 candidate).
- **No new search engine**: PostgreSQL FTS and `pg_trgm` are enough at
  personal-library scale. A dedicated engine may be evaluated later behind the
  search service boundary (#310).
- **Extensions**: `pg_trgm` and `unaccent`, both in core contrib and in the
  official image. Nothing else is required.

---

## 5. Application and domain boundaries

### 5.1 Layers

| Layer | Package | May import | Owns |
| --- | --- | --- | --- |
| HTTP adapter | `backend/server.py`, `backend/api/<family>.py` | domain, `api_contract` | parsing, auth/session resolution, status codes, ETags, streaming |
| Sync adapter | `backend/sync/` (`protocol.py` plus one thin handler per family) | domain | envelope validation, ledger, ACK shape |
| Application/domain | `backend/domain/<family>/` (`commands.py`, `queries.py`, `rules.py`, `sql.py` as needed) | `backend/db`, `backend/storage`, other domains' public functions | validation, authorization, transactions, revisions, jobs, uniqueness, projections |
| Persistence plumbing | `backend/db/` | psycopg, psycopg_pool | pool, transaction helpers, error mapping, migrations, test provisioning |
| Storage | `backend/storage/` (#311) | stdlib | roots, markers, keys, backends |

Families map to today's modules: `works` (Work, Manifestation, Asset,
lifecycle, metadata, source, open state, bulk, BibTeX), `people` (persons,
roles, groups, publishers), `organization` (tags, folders, playlists),
`research` (Concepts, Positions, Arguments, notes, graph, wiki links),
`annotations`, `processing`, `search`, `saved_views`, `settings`, `identity`
(users, Libraries, sessions, devices) and `maintenance` (backup, restore,
cleanup, rebuilds).

### 5.2 Commands and queries

```python
# backend/domain/organization/commands.py (illustrative)
def add_work_tag(ctx: RequestContext, work_id: str, tag_id: str,
                 base_revision: int | None) -> CommandResult:
    with ctx.db.command(ctx, isolation="read_committed") as tx:
        authorize(tx, ctx, Capability.EDIT_LIBRARY)
        rev = tx.lock_revision("work-tag", [work_id, tag_id])   # SELECT … FOR UPDATE
        ...                                                     # decision table
        tx.execute(sql.INSERT_WORK_TAG, {...})
        rev.advance()                                           # owned here
        tx.enqueue("search.refresh_work", key=work_id)          # same transaction
        return CommandResult.acknowledged(rev.value, changed=True)
```

- **A command is one transaction.** It takes the Library maintenance lock in
  shared mode, checks that the Library's `epoch` equals the epoch the caller
  acted on (§11.4), authorizes the actor for the Library,
  validates, decides against the current revision, writes, advances the
  revisions it touched, enqueues jobs and returns a result. The REST endpoint
  and the durable-operation handler for the same action call **the same
  command** (direct-endpoint parity, `local-first-sync.md`), which is today's
  `*_on_conn` sharing made structural.
- **A query is one snapshot.** `ctx.db.query(ctx)` opens `REPEATABLE READ
  READ ONLY`, so a projection made from several statements can never mix
  pre- and post-write state (discussion #583, class 2).
- **Revision bumps belong to commands**, which is option (c) in discussion
  #583. Two structural guards back it (§13.2): a static gate forbids DML on
  revisioned tables outside `backend/domain/`, and every foreign key whose
  cascade would delete a row that owns a revision scope is `ON DELETE
  RESTRICT`/`NO ACTION`, so the owning command must delete children
  explicitly and advance or tombstone their scopes.
- **Post-commit side effects** (file deletion, re-extraction, research
  reindex) are never run inline after commit by the adapter, as
  `server.py:2645-2681` does today. They are jobs enqueued inside the
  transaction (§12.1), so a crash between commit and side effect cannot lose
  them. The one exception is an import's removal of its inbox source file
  (§11.3): it runs inline under the inbox lock so that a backup never sees
  the import's rows next to its still-present source, and the row's
  `imported` state makes it recoverable after a crash.

### 5.3 What is deliberately not introduced

- No repository interfaces, no generic `Repository[T]`, no unit-of-work object
  beyond the transaction helper, no dependency-injection container.
- No domain-event bus inside the process. The jobs table is the one
  asynchronous seam.
- No mapping layer between rows and "domain entities" where a typed row is
  enough. Rules live in plain functions.

### 5.4 Errors

`backend/db/errors.py` maps psycopg errors once:

| SQLSTATE | Mapping |
| --- | --- |
| `23505` unique violation | constraint name → domain code (`NAME_TAKEN`, `ALIAS_TAKEN`, `ANNOTATION_ID_CONFLICT`, …); an unknown constraint is a 500, never a silent success |
| `23503` foreign key violation | constraint name → `NOT_FOUND`/`IN_USE` domain codes |
| `23514` check violation | `INVALID_VALUE` with the field from the constraint name |
| `40001`, `40P01` serialization failure, deadlock | retried by the SERIALIZABLE helper (bounded); otherwise 503 `RETRY` |
| `55P03` lock not available, `57014` statement timeout | 503 with `Retry-After` |
| connection errors | 503; never ledgered as a terminal sync result |

Constraint names are therefore part of the schema contract and covered by the
snapshot test.

### 5.5 Transition facade

During the build-out (§15) the server keeps calling one object, as it calls
`db` today. A `PostgresLibrary` facade exposes the subset of `PRKSDatabase`
methods that `server.py` uses, with the same signatures and return shapes,
and delegates each to domain commands and queries. Each lane adds the methods
for its family in its own mixin file, so lanes do not edit a shared file. The
facade is the strangler seam, not the target: after cutover, route families
move to calling the domain directly under #67, and the facade shrinks to
nothing.

---

## 6. Schema

### 6.1 Conventions

- **Two schemas plus extensions.** `prks` holds canonical and operational
  data; `derived` holds rebuildable projections (search documents, extracted
  PDF text, research mentions). Extensions `pg_trgm` and `unaccent` live in
  their own `ext` schema. `search_path` is set per connection
  (`prks, ext`), and migrations always schema-qualify. The migration runner
  therefore only ever targets the live schemas; restore staging builds its
  own schema without it (§11.4).
- **Types.** `text` for identifiers and free text (with `CHECK` limits where
  the API already enforces them), `bigint` for revisions and generations,
  `boolean` for flags, `timestamptz` for every instant (UTC on the wire),
  `jsonb` only where the value is document-shaped (annotation geometry, sync
  results, job payloads, a few settings). Bibliographic dates stay `text`,
  because "1968", "c. 380 BC" and "2024-03" are all valid inputs.
- **Enumerations** are `text` plus `CHECK`, not PostgreSQL enum types, so a
  value can be added in a forward migration without type surgery.
- **Constraint naming.** `<table>_<cols>_key`, `_fkey`, `_check`, `_excl`,
  stable and mapped in §5.4.
- **No triggers for domain behavior.** The only triggers allowed are
  `updated_at` maintenance, if adopted at all. Pointer ownership is expressed
  with deferrable foreign keys (§6.4).

### 6.2 Tenancy: instance, users, Libraries

```sql
CREATE TABLE prks.instance (           -- exactly one row
  id               text PRIMARY KEY CHECK (id ~ '^IN-[0-9A-F]{32}$'),
  storage_root_id  text NOT NULL CHECK (storage_root_id ~ '^SR-[0-9a-fA-F]{32}$'),
  setup_state      text NOT NULL CHECK (setup_state IN ('unconfigured','bootstrapping','ready')),
  testing          boolean NOT NULL,    -- a testing database is never served by a non-testing process
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX instance_singleton ON prks.instance ((true));

CREATE TABLE prks.users (
  id            text PRIMARY KEY CHECK (id ~ '^U-[0-9A-F]{32}$'),
  login_key     text NOT NULL UNIQUE,  -- identity key of the login name (§8.1)
  display_name  text NOT NULL,
  password_hash text,                  -- argon2id; NULL until set
  disabled_at   timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE prks.libraries (
  id          text PRIMARY KEY CHECK (id ~ '^LIB-[0-9A-F]{32}$'),
  name        text NOT NULL,
  maintenance text CHECK (maintenance IN ('restore','rebuild')),
  maintenance_run uuid,                -- the restore/rebuild run that set it (§11.4)
  epoch       bigint NOT NULL DEFAULT 1 CHECK (epoch >= 1),  -- restore generation; +1 on every restore (§11.4)
  search_folding text,                 -- folding of the last completed search+text rebuild; NULL = none (§8.6)
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE prks.library_memberships (
  library_id text NOT NULL REFERENCES prks.libraries ON DELETE CASCADE,
  user_id    text NOT NULL REFERENCES prks.users ON DELETE CASCADE,
  role       text NOT NULL CHECK (role IN ('owner','editor','reader')),
  PRIMARY KEY (library_id, user_id)
);

CREATE TABLE prks.devices (            -- client/sync identity, never authentication
  id           uuid PRIMARY KEY,       -- today's device_id
  user_id      text NOT NULL REFERENCES prks.users ON DELETE CASCADE,
  label        text,
  first_seen   timestamptz NOT NULL DEFAULT now(),
  last_seen    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE prks.sessions (
  id           uuid PRIMARY KEY DEFAULT uuidv7(),
  token_sha256 bytea NOT NULL UNIQUE,  -- the cookie holds the token, the database its hash
  user_id      text NOT NULL,
  library_id   text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  last_used_at timestamptz NOT NULL DEFAULT now(),
  expires_at   timestamptz NOT NULL,
  revoked_at   timestamptz,
  FOREIGN KEY (library_id, user_id)    -- a session exists only while its membership does
    REFERENCES prks.library_memberships ON DELETE CASCADE
);
```

The ID prefixes `U`, `LIB`, `IN` are new and unused today. `entity_ids` warns
that prefixes are not types, and APIs keep pairing an ID with its type.

**Library scoping rule (PG6).** Every library-scoped table has `library_id
text NOT NULL`. Its own primary key stays the globally unique entity ID, and
it also declares `UNIQUE (library_id, id)`. Every child references its parent
through `(library_id, parent_id) REFERENCES parent (library_id, id)`. The
database therefore refuses a tag on another Library's Work, an annotation on
another Library's Asset, and so on, without any trigger. Uniqueness of names
is per Library: `UNIQUE (library_id, name_key)`.

`storage_root_id` lives on `instance` in 0.8, because one PRKS instance binds
one data root (#311 S8). When a deployment later wants per-Library roots, the
column moves to `libraries` in a forward migration; the storage key format is
unaffected.

### 6.3 IDs (PG7)

The current IDs are 32 upper-case hex characters from a UUID with a type
prefix (`backend/entity_ids.py`), minted by the server or by the browser for
durable creates. They appear in URLs, workspace tabs, IndexedDB rows,
revision scopes and fixtures.

- **Recommendation:** store them as `text` with a per-table `CHECK
  (id ~ '^W-[0-9A-F]{32}$')`. The 8-hex legacy form is not accepted, because no
  pre-0.8 data is carried. The wire format, the client minting code and the
  scope format do not change.
- **Alternative:** store `uuid` and render the prefix at the API boundary.
  That saves about 20 bytes per key and makes joins on 16-byte values. At
  personal-library scale this is not measurable, and it adds a conversion in
  every query, fixture and sync scope. It can be revisited after 1.0 with
  measurements, as an internal change.
- Internal high-volume tables (jobs, sessions, sync-operation rows) use
  `uuidv7()` or the client's UUID; they are never shown as entity IDs.

### 6.4 Work, Manifestation, Asset (PG8)

The #60 model is the schema, with these mechanical changes, all permitted by
its 2026-09-29 compatibility note and by the clean start:

1. **Authority from the first row.** Bibliographic fields live on
   `manifestations`, file/source fields and annotation revisions on `assets`.
   `works` keeps only Work-owned state (canonical title, abstract, status,
   last opened, citation and primary pointers). There are no legacy columns to
   mirror. #60 slices C–F collapse into the baseline; the legacy JSON shape is
   produced by `backend/domain/works/projection.py` (today's
   `work_projection.py`), which is #60 D10's "maintained compatibility
   projection" moved out of the schema.
2. **Pointers are deferred composite foreign keys**, replacing the ten
   pointer, origin and retirement triggers, the retirement marker and its
   CHECK(0) guard table:

   ```sql
   CREATE TABLE prks.works (
     library_id text NOT NULL REFERENCES prks.libraries,
     id         text PRIMARY KEY CHECK (id ~ '^W-[0-9A-F]{32}$'),
     title      text NOT NULL,
     abstract   text,
     status     text NOT NULL DEFAULT 'Not Started' CHECK (status IN (…)),
     last_opened_at timestamptz,
     primary_manifestation_id  text NOT NULL,
     citation_manifestation_id text,
     created_at timestamptz NOT NULL DEFAULT now(),
     updated_at timestamptz NOT NULL DEFAULT now(),
     UNIQUE (library_id, id),
     FOREIGN KEY (primary_manifestation_id, id)
       REFERENCES prks.manifestations (id, work_id) DEFERRABLE INITIALLY DEFERRED,
     FOREIGN KEY (citation_manifestation_id, id)
       REFERENCES prks.manifestations (id, work_id) DEFERRABLE INITIALLY DEFERRED
   );
   ```

   A Work and its first Manifestation are inserted in one transaction; the
   constraint is checked at COMMIT. "Every Work has exactly one primary
   Manifestation that it owns" is then a database fact. Moving a Manifestation
   to another Work fails at COMMIT unless the pointers moved too, which is the
   §4.1 behavior #60 specifies. The negative tests #60 lists for Slice A carry
   over as schema tests.
3. **`origin_work_id` is dropped.** It maps legacy Work-keyed operations to the
   backfilled Manifestation and Asset. With no legacy data and no legacy
   operations in flight across the cutover (§10.6), nothing can need it.
   Operations that address Manifestation- or Asset-owned state name those IDs.
4. **Asset bytes are immutable per storage key** (PG14, §11.2). The
   `content_generation` compare-and-set and the three-step NULL-hash protocol
   of #60 §9.4 become unnecessary: a rewrite produces a new key whose hash is
   computed before the row points at it. `content_sha256` is therefore never
   stale and is `NOT NULL` for managed files.
5. **Notes leave the `works` row.** `work_notes(library_id, work_id, kind
   CHECK (kind IN ('research','private')), body, updated_at, PRIMARY KEY
   (work_id, kind))`. Folder private notes stay a column on `folders`. This
   keeps large text out of the hot Work row and makes the note scopes
   independent rows, as the sync contract already treats them.

The remaining #60 entities (`manifestation_relations`,
`manifestation_identifiers` with `normalized` values, role scope by
Manifestation, `argument_sources` with its own row identity, `duplicate_decisions`,
`sync_work_lifecycle` for merges) are created as #60 specifies, with
`library_id` added. Features that #60 gates on later slices (a second Version
in the UI, merges) stay gated; the schema only stops being the obstacle.

### 6.5 Table inventory

`L` = library-scoped (has `library_id` and composite parent FKs).

| Family | Tables | Changes from today |
| --- | --- | --- |
| Instance and identity | `instance`, `users`, `libraries`, `library_memberships`, `devices`, `sessions`, `bootstrap_tokens`, `login_attempts`, `login_device_tokens` | New |
| Works | `works` L, `manifestations` L, `assets` L, `manifestation_relations` L, `manifestation_identifiers` L, `work_notes` L, `sync_work_lifecycle` L, `duplicate_decisions` L | §6.4 |
| Annotations | `annotations` L | `geometry jsonb`; primary key `(asset_id, id)` because the viewer mints the ID (global uniqueness of a client-minted viewer ID is not something PRKS controls) |
| People | `persons` L, `person_aliases` L, `roles` L, `person_groups` L, `person_group_members` L, `publishers` L, `publisher_aliases` L | Aliases become rows with keys; `roles` drops the `rowid` tiebreak in favor of `order_index`; publisher names and aliases share one `UNIQUE (library_id, key)` namespace through a `publisher_keys` table (§8.1); group names unique by identity key |
| Organization | `tags` L, `tag_aliases` L, `work_tags` L, `folders` L, `folder_files` L, `folder_tags` L, `playlists` L, `playlist_items` L, `saved_views` L, `sync_tag_lifecycle` L | `folders.parent_id` + identity key unique per parent; `folder_files` stays the scalar Work-to-folder membership table (`work-folder` in `docs/work-identity-model.md`), now with `UNIQUE (library_id, work_id)` so the one-folder-per-Work rule (I8) that only code enforces today is a constraint; `playlist_items` keeps one playlist per Work; tag names and aliases share one `UNIQUE (library_id, key)` namespace through a `tag_keys` table (§8.1) |
| Research | `concepts` L, `concept_aliases` L, `concept_parents` L, `positions` L, `arguments` L, `argument_verdicts` (global seed), `argument_target_positions` L, `argument_target_arguments` L, `argument_sources` L | Concept name and alias keys share one `UNIQUE (library_id, key)` namespace through a `concept_keys` table, replacing the full Python scan |
| Processing | `processing_files` L, `processing_file_roles` L, `processing_file_tags` L | `abs_path` removed; `rel_path` relative to the inbox (#311 Phase B intent); nullable `restore_hold` and `restore_sha256` (§11.4); `imported` state between an import's commit and its source removal, with the source's `import_sha256` (§11.3) |
| Settings | `library_settings` L, `instance_settings` | Split by scope (#612): which keys are Library-, instance- or account-scoped is decided with #612's configuration inventory |
| Sync | `entity_revisions` L, `sync_operations` L | §6.6 |
| Operations | `object_cleanup_claims`, `jobs`, `backup_runs`, `restore_runs`, `restore_run_objects`, `schema_migrations` | §11.3, §11.4, §12.1 |
| Derived (schema `derived`) | `work_search`, `asset_text`, `concept_mentions`, `argument_mentions`, `work_note_state` | §8; excluded from backup |

### 6.6 Revisions and the ledger

```sql
CREATE TABLE prks.entity_revisions (
  library_id text   NOT NULL REFERENCES prks.libraries ON DELETE CASCADE,
  scope_type text   NOT NULL,
  scope_id   text   NOT NULL,          -- canonical JSON array text, unchanged format
  revision   bigint NOT NULL CHECK (revision >= 0),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (library_id, scope_type, scope_id)
);

CREATE TABLE prks.sync_operations (
  op_id          uuid PRIMARY KEY,
  library_id     text NOT NULL REFERENCES prks.libraries ON DELETE CASCADE,
  device_id      uuid NOT NULL REFERENCES prks.devices,
  user_id        text NOT NULL REFERENCES prks.users,
  operation_type text NOT NULL,
  entity_type    text NOT NULL,
  entity_id      text NOT NULL,
  request_sha256 bytea NOT NULL,
  status         text NOT NULL,
  http_status    smallint NOT NULL,
  result         jsonb NOT NULL,
  applied_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sync_operations_device ON prks.sync_operations (device_id, applied_at);
```

The scope ID keeps its canonical JSON text, because fixtures, the client's
expectations and #60's scope copies all use it, and a `text[]` would buy
nothing at this scale. Its `user_id` and
`device_id` references survive backup and restore through the identity mapping
in §11.4. Retention of `sync_operations` stays #66's decision;
the `applied_at` index makes bounded compaction possible later.

---

## 7. Transactions, locking, idempotency and concurrency

### 7.1 Isolation levels (PG12)

| Work | Isolation | Why |
| --- | --- | --- |
| Single-aggregate command (most durable operations, most REST mutations) | READ COMMITTED + `SELECT … FOR UPDATE` on the revision row(s) it decides on | The revision row is the conflict unit's natural lock; two writers of the same scope serialize, writers of different scopes do not. |
| Uniqueness decisions (create tag, rename concept) | READ COMMITTED + unique index | The index is the arbiter; `23505` maps to the domain refusal. No check-then-insert race. |
| Multi-aggregate commands (merge tags, merge Works, bulk edits, delete Person with protections, restore commit) | SERIALIZABLE, retried up to 3 times on `40001`/`40P01` | Correctness across many rows without hand-ordered locks. Retry is safe because a command has no side effects outside its transaction (§5.2). |
| Queries and projections | REPEATABLE READ READ ONLY | One snapshot per response. |
| Workers | READ COMMITTED; claim with `FOR UPDATE SKIP LOCKED` | §12.1 |

The isolation is declared by the command, and a test enumerates commands to
make sure each one declares it.

**Writers of what a SERIALIZABLE command decides on are SERIALIZABLE too.**
PostgreSQL's serializable guarantee holds only among SERIALIZABLE
transactions. A READ COMMITTED writer can, for example, add a Work to the
source tag after a tag merge has read the source's links, and the merge
would then miss that Work instead of failing with `40001`. So every
command that writes rows a multi-aggregate command reads to decide also
runs SERIALIZABLE, with the same retry. For a tag merge that means adding,
removing or renaming tags on Works and the tags themselves; for a Work
merge, the Works' child rows; for a protected Person delete, the
Person's links. Each multi-aggregate command declares the tables it
decides on, and the isolation test fails if any command that writes one
of them declares READ COMMITTED. Single-scope commands that touch none of
those tables keep READ COMMITTED with the revision-row lock.

### 7.2 Revision protocol on PostgreSQL

```sql
-- inside a command transaction
INSERT INTO prks.entity_revisions (library_id, scope_type, scope_id, revision)
VALUES (%(lib)s, %(type)s, %(id)s, 0)
ON CONFLICT DO NOTHING;
SELECT revision FROM prks.entity_revisions
 WHERE library_id = %(lib)s AND scope_type = %(type)s AND scope_id = %(id)s
 FOR UPDATE;
-- decide (local-first-sync.md decision table) …
UPDATE prks.entity_revisions SET revision = revision + 1, updated_at = now()
 WHERE … RETURNING revision;
```

This keeps today's decision table exactly (current/stale/future base, same or
different value) and the per-scope conflict units. It replaces the global
`BEGIN IMMEDIATE` serialization with per-scope row locks.

### 7.3 Idempotent operations

```sql
INSERT INTO prks.sync_operations (op_id, …, status, http_status, result)
VALUES (%(op)s, …, 'applying', 0, '{}')
ON CONFLICT (op_id) DO NOTHING
RETURNING op_id;
```

- If the insert returns a row, this transaction owns the operation. It runs
  the command's body on the same connection and updates the ledger row with
  the terminal result before COMMIT. A rollback removes the claim, so an
  exception is never ledgered, which is today's rule.
- If it returns nothing, another transaction owns or owned the `op_id`. The
  unique index makes a concurrent duplicate **wait** until the first commits
  or rolls back. Then the handler reads the stored row: same request hash →
  replay the stored status and body; different hash → 409 `OP_ID_REUSE`.
- Dependencies (`depends_on`) are checked inside the same transaction, as
  today.
- **Ledger before epoch.** An `op_id` already in the ledger replays its stored
  result whatever epoch it carries, because the restored state already
  contains its effect. Only an operation that would be *applied* now is
  compared with the Library's current epoch, and a mismatch is refused with
  409 `LIBRARY_EPOCH_CHANGED` without being ledgered (§11.4).
- The ledger row records `user_id` and `library_id`, and the device must
  belong to the authenticated user, so a replayed `op_id` from another account
  is `OP_ID_REUSE`, never a disclosure of another user's result.

### 7.4 Locks

| Need | Today | PostgreSQL |
| --- | --- | --- |
| Serialize all mutations | `LibraryAccessGate` | Not needed. Row locks and constraints serialize what conflicts. |
| Backup must not interleave with deletions of objects it is copying | gate "backup" mode | Shared advisory lock `(library, 'objects')` held by backup; object cleanup takes it exclusively per batch (§11.3). DB consistency comes from the snapshot. |
| Restore exclusive | gate "restore" mode | Committed `libraries.maintenance = 'restore'` plus the Library maintenance lock: every command takes it shared (try-lock) and re-reads the flag; restore takes it exclusive to drain in-flight writers before its one-transaction commit, which advances `libraries.epoch` to fence everything that started earlier (§11.4) |
| Reference check + delete of an object | `_PDF_PATH_LOCKS` + one transaction | `pg_advisory_xact_lock(hashtextextended('obj:'||name, 0))` + the same single transaction (`settle_claim_if_referenced`) |
| Materialization validate → replace → mark | per-Work `threading.Lock` | `SELECT … FROM assets WHERE id = … FOR UPDATE` |
| Cleanup retry single runner | implicit (one process) | It is a job; one claimed job per Library at a time via a unique `singleton_key` |
| Migrations single runner | implicit | `pg_advisory_lock` (§4.4) |

### 7.5 Process-local state, item by item (PG13)

| Item | Replacement |
| --- | --- |
| `LibraryAccessGate` | §7.4: the per-Library maintenance lock and committed maintenance flag replace the gate's backup and restore modes; ordinary writers no longer serialize with each other |
| Save-confirm tokens | Removed. `save-confirm` answers from durable state: the Asset's current `content_sha256`/locator and the annotation set revision, which a client compares with the value its save returned |
| `_PDF_MATERIALIZATION_LOCKS`, `_PDF_PATH_LOCKS` | Row lock and advisory lock (§7.4) |
| `_ready_backups` tokens | `backup_runs` rows with an opaque download token hash and expiry |
| Bound root, index singletons | Bound once per process at startup; the database pairing record decides validity (§11.1); no hot rebind across processes (#311 §12) |
| `root.lock` lease | Kept, but its meaning changes from "one process per root" to "one instance per root". Every process of the paired instance (`web`, `worker`, admin commands) takes the OS lock **shared**; relocation and the offline storage tools take it **exclusive**. Format-2 restore does not: it writes new immutable keys into the live root while the application runs (§11.4). A process whose database names a different `storage_root_id` is refused before it touches the lock (§11.1). This is the PostgreSQL-era fencing that #311 §12 anticipates. Hot rebind stays a single-process capability and is unavailable while another process holds the shared lock. |
| Performance registry | Stays per process by design, labelled with process role (§12.4) |

### 7.6 Concurrency tests

New tests run two real connections against PostgreSQL and use barriers (or
`pg_sleep` inside a test-only hook) to force interleavings:

- same `op_id` submitted concurrently → exactly one ledger row, both
  responses identical;
- two commands on the same revision scope → one ACK, one `REVISION_CONFLICT`
  or convergent ACK per the decision table;
- create tag with case-variant Greek/Cyrillic names concurrently → one
  succeeds, one `NAME_TAKEN`;
- create tag "X" vs. adding alias "X" to another tag concurrently (and the
  same for publishers) → one succeeds, one `NAME_TAKEN`;
- cleanup claim settle vs. new reference adoption → never an unlinked
  referenced object;
- tag merge vs. concurrent `ADD_WORK_TAG` on the source → serialization retry
  then the documented outcome;
- pool exhaustion → 503, no hang.

---

## 8. Search, Unicode and indexing

### 8.1 Identity keys (PG9)

One function defines when two names are "the same name" for uniqueness:

```
identity_key(s) = casefold( collapse_whitespace( trim( NFC(s) ) ) )
```

- It matches today's Concept rule (`research_markup.py:46-52`), which is the
  only place PRKS already handles Unicode, and extends it to tags, tag
  aliases, publishers and their aliases, person groups, folders (per parent),
  Saved Views, Concepts and their aliases, and login names.
- It does **not** strip diacritics: "Résumé" and "Resume", or Serbian "ћ" and
  "ч", are different names. Diacritic-insensitivity belongs to search (§8.3).
- It does **not** apply NFKC: compatibility folding (ligatures, full-width
  forms, superscripts) is lossy and was never PRKS behavior. The maintainer
  confirmed this rule (§16, decision 3).
- **Where it runs.** In PostgreSQL as an `IMMUTABLE` SQL function
  `prks.identity_key(text)` built from `normalize(…, NFC)`, `regexp_replace`
  and `casefold(… COLLATE pg_unicode_fast)`, used in `GENERATED ALWAYS AS
  (…) STORED` key columns with unique indexes. In Python as
  `backend/domain/text_identity.py`, and in TypeScript for the browser's
  offline uniqueness checks. A shared vector file
  `tests/fixtures/text_identity_vectors.json` (Latin with diacritics,
  Greek with tonos and final sigma, Serbian Cyrillic and Latin, German ß,
  Turkish dotted/dotless i, combining sequences, astral characters) is run
  against all three implementations; any difference fails CI. This settles
  discussion #583's third class (normalized-identity drift).
- **One namespace, one index.** Where today's code treats a name and its
  aliases as one namespace (a tag alias may not equal another tag's name,
  and the same for publishers and Concepts), separate unique indexes on
  `tags` and `tag_aliases` cannot keep that rule: under READ COMMITTED a
  concurrent `CREATE_TAG("X")` and an alias "X" write different tables and
  both commit. So each such namespace has one key table, `tag_keys`,
  `publisher_keys` and `concept_keys`, with `UNIQUE (library_id, key)` and
  the owning row's ID. Creating, renaming, aliasing or merging writes the
  key rows in the same transaction, and a collision on that index is
  `NAME_TAKEN`.
- **Immutability check.** Slice PG-2 must prove the function can be
  `IMMUTABLE` under the database's builtin locale (the 18 docs make
  `casefold` depend on the collation; pinning `pg_unicode_fast` inside the
  function makes it independent of the column's collation). If it cannot, the
  fallback is application-computed key columns plus a `CHECK (name_key =
  prks.identity_key(name))` validated by the same vectors.

### 8.2 Collation (PG10)

- `CREATE DATABASE … LOCALE_PROVIDER builtin BUILTIN_LOCALE 'PG_UNICODE_FAST'
  TEMPLATE template0`. Sorting by code point and full Unicode case mapping
  are both stable across operating-system and ICU upgrades, so a container
  base-image upgrade can never silently corrupt a text index, which is a
  well-known failure mode of glibc collations.
- User-facing alphabetical order (tag lists, people, folders) is applied with
  an explicit `COLLATE "und-x-icu"` in those `ORDER BY` clauses, or in the
  client when the list is already client-sorted. Any index created on an
  ICU-collated expression is in `derived` or is cheap to rebuild, and
  PostgreSQL's collation-version tracking warns when ICU changes.
- Testing and Compose databases are created by PRKS tooling with these
  options, so tests and production cannot differ by host locale.

### 8.3 Full-text search (PG11)

**Text search configuration.**

```sql
CREATE TEXT SEARCH DICTIONARY prks.search_unaccent
  (TEMPLATE = ext.unaccent, RULES = 'unaccent');   -- or 'prks_unaccent_v<N>', below
CREATE TEXT SEARCH CONFIGURATION prks.prks_search (COPY = pg_catalog.simple);
ALTER TEXT SEARCH CONFIGURATION prks.prks_search
  ALTER MAPPING FOR hword, hword_part, word WITH prks.search_unaccent, simple;
```

- **Case folding before the configuration.** The `simple` dictionary only
  lowercases, which keeps Greek final `ς` and medial `σ` (and similar pairs)
  apart. Every text indexed with this configuration, in `derived.work_search`
  and `derived.asset_text` alike, is passed as `to_tsvector('prks.prks_search',
  casefold(normalize(t, NFC) COLLATE pg_unicode_fast))`, and query tokens are
  case-folded the same way in Python (`str.casefold` after NFC) before
  `to_tsquery`. Indexed and queried lexemes therefore use the same full case
  folding as `search_norm` and `identity_key`.
- **No stemming.** The library is multilingual (English, Greek, Serbian
  Cyrillic and Latin, German, French, …), Works rarely declare a language,
  and today's FTS5 does not stem. Prefix matching (`'term':*`) reproduces
  today's `"tok"*` behavior and covers most inflection needs. Per-language
  stemming can be evaluated later per Manifestation `language`.
- **`unaccent`** folds diacritics in *search only*, so "cevapi" finds
  "ćevapi" and "Σωκράτης" is found by "σωκρατης". The default `unaccent`
  rules must be checked against the parity vectors for Greek tonos and
  dialytika, and for Cyrillic letters whose diacritic is part of the letter
  (й, ё, ћ, ђ). Both the configuration and `prks.search_norm` use the one
  dictionary `prks.search_unaccent`, so the rules are chosen in one place.
  While the defaults pass, it uses upstream `unaccent` rules and Compose
  runs stock `postgres:18`. Where the defaults are wrong for PRKS (for
  example folding й to и), PRKS ships `prks_unaccent_v<N>.rules` (§16,
  decision 4). PostgreSQL reads rules only from the server's
  `$SHAREDIR/tsearch_data/`, so the file goes in a PRKS database image
  built `FROM postgres:18` that copies it there. Compose, the CI service and
  `scripts/test-postgres` then use that image, and the baseline sets
  `RULES = 'prks_unaccent_v<N>'`. A rules change ships as a new file name,
  never an edit in place, so the dictionary's rules name identifies the
  rules (§8.6). A database image without the named file fails the
  dictionary's first use loudly instead of folding with other rules.
- Query parsing stays in Python: `_prks_search_tokens` semantics (split on
  `-`/`_`, drop non-word characters) are kept, then the tokens are passed
  through `to_tsquery('prks.prks_search', …)` with `:*` and `&`, using bound
  parameters and never string interpolation of raw query text into
  `to_tsquery` syntax.

**Search documents** live in `derived.work_search`:

```sql
CREATE TABLE derived.work_search (
  library_id text NOT NULL,
  work_id    text PRIMARY KEY,
  doc        tsvector NOT NULL,      -- title A, people/author text B, abstract C, notes D
  title_norm text NOT NULL,          -- search_norm(title) for trigram matching
  people_norm text NOT NULL,
  text_norm  text NOT NULL,          -- search_norm(title, author text, abstract, notes): today's LIKE blob
  updated_at timestamptz NOT NULL
);
CREATE INDEX work_search_doc  ON derived.work_search USING gin (doc);
CREATE INDEX work_search_trgm ON derived.work_search USING gin (title_norm ext.gin_trgm_ops, people_norm ext.gin_trgm_ops);
CREATE INDEX work_search_text_trgm ON derived.work_search USING gin (text_norm ext.gin_trgm_ops);
```

- Metadata and note commands refresh the row **in the same transaction** (one
  small statement), so metadata search is never stale, exactly as the FTS5
  triggers behave today. This is the one place where a command writes
  `derived`. The row is still derived: `prks admin rebuild-derived` truncates
  and recomputes it, and restore does not carry it.
- PDF text lives in `derived.asset_text(asset_id, content_sha256,
  extractor_version, status, truncated, text, doc tsvector)`, filled by the
  `text.extract` job (§12.1). It is keyed by Asset and by the content hash, so
  a new working object (PG14) is a new extraction, and a stale row can never
  be served for new bytes.
- **Ranking.** Phase 1 reproduces today's union order for parity (FTS hits,
  then title/abstract substring, then the per-token fallback, then people).
  Phase 2, as its own PR, replaces the union with one `ts_rank_cd`-weighted
  score plus a trigram similarity boost, accepted only with before/after
  results on a synthetic multilingual corpus.
- **Substring and fuzzy** (`pg_trgm`) replace every `LOWER(…) LIKE` path,
  including author and publisher filters, with Unicode-correct folding via
  `prks.search_norm(s) = ext.unaccent('prks.search_unaccent'::regdictionary,
  casefold(normalize(s, NFC) COLLATE pg_unicode_fast))`, an `IMMUTABLE` SQL
  function that uses the same dictionary, `prks.search_unaccent`, as the
  text search configuration. Like `identity_key` and the
  FTS path, it pins `casefold` to `pg_unicode_fast`, so a bound token and a
  stored column fold identically whatever their collation. Naming the
  dictionary removes the `search_path` lookup, but both forms of `unaccent`
  are `STABLE`, and the result still depends on the rules files the server
  reads from disk. So `IMMUTABLE` here is a promise PRKS makes, not
  something PG-2 can derive from the functions it calls. The promise is
  safe for two reasons. First, the rules are fixed per image. Second,
  nothing indexes the function's output: the `*_norm` columns are plain
  columns that commands and `rebuild-derived` write by calling the same
  function, never generated columns or expression indexes. PG-2's schema
  snapshot test asserts the second point through `pg_depend`: no index
  expression or column default depends on `prks.search_norm`. When the
  rules do change, the folding check in §8.6 rebuilds everything that was
  folded with the old ones.
- **Both sides of a substring match are normalized in SQL.** Python only
  splits the query into tokens. Every substring predicate, on `text_norm`,
  `title_norm`, `people_norm` or an author or publisher filter, is
  `<column> LIKE '%' || prks.like_escape(prks.search_norm($tok)) || '%'
  ESCAPE '\'`, with the token as a bound parameter. `like_escape` escapes
  `\`, `%` and `_` *after* normalization, so a character that folds into
  one of them stays literal. Python never reproduces `unaccent`, so the
  stored and queried sides cannot drift.
- **Infix fallback kept.** `doc` matches prefixes only, so it cannot find
  "state" inside "understatement". Today's per-token fallback
  (`_search_works_like_tokens`, `backend/db_manager.py:2521`) matches any
  infix across the displayed title, author text, abstract (the primary
  Manifestation's when present) and notes. `text_norm` is `search_norm` of
  that same text, and the fallback is the substring predicate above, per
  token, on `text_norm`'s trigram index. So abstracts and notes keep infix
  matching, now diacritic-insensitive too.

### 8.4 Research index and graph

`derived.concept_mentions`, `derived.argument_mentions` and
`derived.work_note_state(work_id, content_hash)` move into PostgreSQL
unchanged in meaning. A Research Note command enqueues `research.reindex_work`
in its transaction; the ACK no longer waits for the reindex (today it runs
after commit in the request, `server.py:2645-2681`). The client already treats
the Research-ACK fence as "Concept/Argument/Graph views may refresh", so it
waits for the job through the existing `research_refs`/state endpoints, which
now report `pending` until the job completes. The rule "the derived index is
never authority for destructive mutation" is unchanged.

Concept resolution uses the `concept_keys` unique index instead of scanning
every Concept in Python.

### 8.5 Saved Views

Definitions stay structured columns re-run through the search service, never
stored results, never executable rules. Only their persistence and name
uniqueness change (identity key per Library).

### 8.6 Rebuild and verification

`prks admin rebuild-derived [--library L] [--only search|text|research]`
truncates and recomputes the `derived` schema, as a job with progress. The
`text` scope keeps extracted text: it recomputes `asset_text.doc` from the
stored `text`, and extracts again only where a row is missing or its
`content_sha256` or `extractor_version` no longer matches. It runs
automatically after restore and is the documented step after a PostgreSQL
major upgrade. Normal startup never runs a full verification; a cheap
consistency count (`works` without `work_search` rows) schedules a repair job.

**Folding check.** `doc` vectors, `asset_text.doc` and the `*_norm` columns
all fold through the same `unaccent` rules and the server's Unicode tables,
and either can change without touching the stored values. PRKS can point
`prks.search_unaccent` at a new rules file (the R12 mitigation), or a new
database image can bring a PostgreSQL release whose upstream
`unaccent.rules` or Unicode data differ. Stored values would then keep the
old folding while new rows and query
tokens use the new one, and searches would silently miss existing Works.

- The current folding is the rules name of `prks.search_unaccent`
  (`pg_ts_dict.dictinitoption`) plus `server_version_num`. Both are
  readable without privileges, so there is no probe of the server's files.
- `libraries.search_folding` records the folding of the Library's last
  **completed** search and text rebuild, not a requested one. The folding
  rebuild is a `derived.rebuild` job with scope `search,text`. It, and any
  `derived.rebuild` whose scope covers both (restore's included), captures
  the current folding when it starts and records it in its job row's
  payload. Its final transaction, fenced by
  `library_epoch` like every job transaction (§12.1), stores that value.
  A failed or cancelled run stores nothing. Creating a Library stores the
  current folding (there is nothing to fold yet), and R4 sets the column
  to `NULL` because it deletes the Library's `derived` rows (§11.4).
- When `web` or `worker` starts, for each Library whose `search_folding`
  differs from the current folding, one transaction locks the `libraries`
  row `FOR UPDATE` and enqueues the folding rebuild. The only rebuild that
  suppresses it is a `derived.rebuild` whose scope covers **both** search
  and text, the same condition under which a rebuild stores the value, and
  that will store the current folding: a queued one (it captures the
  folding when it starts), or a running one whose recorded folding equals
  the current one. A search-only rebuild, or a running one that captured an
  older folding, does not suppress it. So a rebuild that failed, or was
  cancelled before it finished, is enqueued again on the next start, and
  two processes starting together enqueue it once.
- The database can be upgraded or restarted under running processes,
  whose pools simply reconnect. So the check also runs on every worker's
  idle tick (the embedded one included), at most once a minute: one catalog
  query reads the current folding, and the check above runs when it
  differs from the folding that worker last checked.
- `derived.rebuild` jobs use `dedupe_key = <library_id>:<scope>`, with
  the scope set sorted (`research,search,text` for a full rebuild), and
  `singleton_key = derived:<library_id>`. So a queued search-only rebuild
  never takes the folding rebuild's `jobs_dedupe` slot (§12.1), and two
  scopes of one Library still never run at the same time.
- Until the rebuild finishes, existing Works can be missed, and its
  progress shows like any rebuild. A PostgreSQL major upgrade now triggers
  the search rebuild by itself; a minor release costs one search rebuild
  that keeps the extracted text.

### 8.7 Parity tests

| Case | Expectation |
| --- | --- |
| Greek title "Πολιτεία", query "πολιτεια" / "ΠΟΛΙΤΕΙΑ" / "Πολιτ" | found (case, tonos, prefix) |
| Final sigma "Σωκράτης" vs "σωκρατησ" | found |
| Serbian Cyrillic "Држава" and Latin "Država", query "drzava" | Latin found; Cyrillic only by Cyrillic query (no transliteration; §16) |
| "ćevapi" / "cevapi" | found both ways in search; distinct as tag names |
| Tag "Λόγος" then create "ΛΌΓΟΣ" | `NAME_TAKEN` |
| Tag "Résumé" then create "Resume" | allowed (distinct identity) |
| German "Straße" vs "STRASSE" | equal identity key (full case folding) |
| Turkish "İ" | documented behavior, pinned by vectors |
| PDF text in Greek | found by unaccented, lower-case prefix query |
| PDF text "Σωκράτης", query "ΣΩΚΡΑΤΗΣ" / "σωκρατησ" | found through `asset_text` FTS alone (no trigram fallback) |
| Publisher filter "lumière" on "Éditions Lumière" | found (replaces the ASCII-only pin in `test_publisher_catalog_queries.py`) |
| Abstract "understatement", query "state" | found through the infix fallback (`text_norm`) |
| Notes "Trešnja", query "šnja" or "snja"; title "Σωκράτης", query "ωκράτ" | found (both sides normalized by `prks.search_norm` in SQL) |
| Query token containing `%` or `_`, or one that folds into them | matched literally, never as a wildcard |

---

## 9. Users, Libraries, authentication and authorization

### 9.1 Model

- **Instance** (one row): installation identity, setup state (#612 Phase B),
  storage pairing, testing flag.
- **User**: an account. 0.8 has exactly one, the owner.
- **Library**: the ownership and authorization boundary for all research
  data. 0.8 creates exactly one personal Library at bootstrap.
- **LibraryMembership**: `(library, user, role)`. 0.8 uses only `owner`;
  `editor` and `reader` exist in the CHECK so authorization code is written
  against roles from the start.
- **Device**: the browser's `device_id`, bound to the user that first
  presented it. Sync identity, never authentication (`local-first-sync.md`).
- **Actor attribution**: every sync-ledger row and every job records
  `user_id`; history (#57) will build on that rather than on IP or device.

### 9.2 Bootstrap (server side of #612)

1. `prks migrate` creates the schema. The instance row does not exist yet:
   setup state is `unconfigured`.
2. `prks admin bootstrap` (headless) or the first-run flow (#612 Phase C)
   needs a **one-time bootstrap token**, stored hashed in `bootstrap_tokens`
   with an expiry. It never goes through application logging: `prks admin
   bootstrap` prints it to its own terminal, a first run without a terminal
   writes it to an owner-only (`0600`) file in the configuration directory,
   outside the data root, and names that file in the log; automated installs
   supply it via `PRKS_BOOTSTRAP_TOKEN_FILE`. Without it, no setup
   endpoint does anything.
3. One SERIALIZABLE transaction creates the instance row (with the bound
   root's `storage_root_id`), the owner user, the Library and the membership,
   and marks setup `ready`. Repeating it is a no-op that returns the existing
   state, so a retried or double-submitted request cannot create a second
   owner or Library.
4. Once `ready`, every bootstrap endpoint answers 409 and the token is
   consumed. A later browser visit cannot take over the instance.

A restored library never triggers bootstrap: restore requires an
authenticated owner of a `ready` instance (§11.4).

### 9.3 Authentication

- Password login with **Argon2id** (`argon2-cffi`, library defaults, tuned
  once on the target hardware and recorded in docs) and constant-time failure
  paths, behind an attempt limiter stored in PostgreSQL:
  `login_attempts(key text PRIMARY KEY, attempts int, window_start
  timestamptz)`. The limiter follows the OWASP device-cookie pattern, so a
  guessing campaign cannot lock the owner out of a browser they have signed
  in from before:
  - **Known login device.** A successful password login sets a
    `prks_login_device` cookie (256-bit random token, `HttpOnly`,
    `SameSite=Strict`, `Secure` on HTTPS, `Path=/api/login`, one year) and
    stores its SHA-256 in `login_device_tokens(token_sha256 bytea PRIMARY
    KEY, user_id, created_at, last_used_at)`; each success rotates it. A
    login that presents a valid token for the account it names is counted
    only under `dev:<token hash>` (5 attempts per window), and the
    account-wide limit never blocks it.
  - **Any other login** is counted under `acct:<login_key>` (5 attempts per
    window, across all addresses) and `ip:<address>` (20 per window). The
    address is the peer, or the forwarded one only from
    `PRKS_TRUSTED_PROXIES`; an IPv6 address is keyed by its `/64`, an IPv4
    address whole.
  - **Counted before the password check.** Before any Argon2 call, one
    statement per key, committed on its own, does `INSERT … ON CONFLICT (key)
    DO UPDATE SET attempts = CASE WHEN window_start <= now() - <window> THEN 1
    ELSE attempts + 1 END, window_start = CASE … END RETURNING attempts`. If a
    returned count exceeds its limit, login answers 429 with `Retry-After`
    until that window ends, without checking the password. Concurrent
    requests therefore cannot all pass a stale count. Only a successful
    password check resets the row that gated the request (`dev:` or `acct:`;
    never `ip:`).
  - **Operator unlock.** `prks admin unlock-login <login>` on the host
    deletes that account's `acct:` row, for an owner signing in from a new
    browser while the account-wide limit is tripped.
  - Window 15 minutes and all limits configurable. The `sessions.prune` job
    deletes expired windows and device tokens unused for a year.
- Sessions: 256-bit random token in an `HttpOnly`, `SameSite=Strict`,
  `Path=/` cookie; `Secure` whenever the public origin is HTTPS. The database
  stores only the SHA-256 of the token. Sliding expiry (default 30 days idle,
  configurable), explicit sign-out revokes the row.
- **CSRF**: `SameSite=Strict` plus the existing Origin check, hardened: on
  every `/api/` mutation, require a matching `Origin` or `Sec-Fetch-Site:
  same-origin`; accept `https` origins when `PRKS_PUBLIC_ORIGIN` is set; never
  derive the expected origin from `X-Forwarded-*` unless
  `PRKS_TRUSTED_PROXIES` lists the peer.
- **Loopback developer mode.** `--testing` and an explicit
  `PRKS_AUTH=loopback-owner` (refused unless bound to a loopback address)
  sign every request in as the owner, so E2E and local development stay
  simple. It is never the default for Compose.
- API tokens for non-browser clients (MCP, capture, Android) are a later
  #46/#47 item; the `sessions` table is shaped so a token is another row kind.
- **401 handling in the browser** (new rule, §10.4).

### 9.4 Authorization

- Every command takes a `RequestContext(user_id, library_id, device_id,
  source)` and calls `authorize(tx, ctx, capability)` before any write.
  Capabilities in 0.8: `READ_LIBRARY`, `EDIT_LIBRARY`, `ADMIN_LIBRARY`
  (backup, restore, settings), `ADMIN_INSTANCE`. Role → capability is a table
  in code.
- Reads are authorized against the current membership, not the session
  alone. Session resolution joins the session to its `library_memberships`
  row on every request and takes the role from that row, so a role change
  applies to the next request, and every query calls `authorize(tx, ctx,
  READ_LIBRARY)` like commands do. A session references its membership
  (§6.2), so removing a membership deletes that user's sessions for the
  Library.
- Every query filters by `ctx.library_id`; composite FKs make it impossible
  to reach another Library through a join. A test lane creates two Libraries
  and asserts that no API route returns or mutates the other's data (§13.3).
- **Row-level security** is not enabled in 0.8. It would duplicate the
  explicit checks, complicate pooling (the Library would have to be set per
  transaction) and make migrations and backups run as a bypass role. It stays
  a post-1.0 defense-in-depth option (#310 says the same). The request
  context sets `SET LOCAL prks.library_id`, so enabling RLS later needs
  policies, not plumbing.
- **API shape (#45).** 0.8 keeps current paths; the Library comes from the
  session. The versioned API decides whether `/api/v1/libraries/{id}/…`
  becomes explicit before multi-Library UI exists.

---

## 10. Browser save, sync and offline under a central server

### 10.1 What stays exactly as it is

- The operation envelope, the 50 registered operations, their conflict units
  and scope types, the decision table, ACK shapes and protocol-terminal codes.
- `scope_busy` and the per-field `expected` op-id compare-and-set. Both are
  client-side guarantees enforced inside IndexedDB transactions; the server
  never emits `scope_busy` and does not need to.
- The #465 save invariant: an editor never reports saved while its body is
  newer than the acknowledged or durably retained body, and a newer body that
  cannot be submitted because an earlier save is in flight remains
  represented and retries. The #465 regression tests are kept.
- The #466 recovery layer: `prks-editor-recovery-v1` stays its own database,
  separate from the queue and the cache; a draft is cleared only by an exact
  acknowledgement of the same generation and body, proven equality, explicit
  discard, or a confirmed deletion seen by this device; nothing is deleted by
  age. A central server does not replace it, because keystrokes that never
  left the browser are on no server.
- The revisions-only state endpoints (`/notes-state`, `/sync-state`, …) and
  their ETags, which recovery uses to prove a draft's base before restoring.

### 10.2 What moves on the server

| Today | PostgreSQL |
| --- | --- |
| One `BEGIN IMMEDIATE` around ledger lookup, handler and ledger insert | One command transaction with the `op_id` claim (§7.3) and per-scope row locks (§7.2) |
| Handlers run raw SQL on a shared SQLite connection | Thin handlers in `backend/sync/handlers/` call the same domain command as the REST route |
| Post-commit reindex and delete cleanup run in the request after commit | Enqueued jobs in the same transaction (§12.1) |
| `sqlite3.IntegrityError` string matching | §5.4 mapping |
| Device IDs unverified | Device bound to the authenticated user; ledger rows carry user and Library |

### 10.3 Browser store scoping (discussion #551)

All three IndexedDB databases and the PDF cache are currently origin-wide and
carry no user, Library or server identity. With accounts that would offer one
account's queued intents or recovered drafts to another. Recommendation:

- **Scope key** = `(instance_id, library_id, user_id)`, delivered by a new
  `GET /api/session` response after sign-in.
- **Queue and cache** (`prks-local-v1`, `prks-offline-v1`, `prks-pdf-v1`):
  per-scope database names (`prks-local-v2:<scope-hash>`). A different scope
  never opens them. Pending rows of another scope stay where they are and are
  delivered when that account signs in again in this browser.
- **Recovery** (`prks-editor-recovery-v1`): one database, a required `scope`
  field on every record, indexed. Records whose scope is not the signed-in
  one are never shown. On sign-out, nothing is deleted.
- **Pre-0.8 data in the browser.** An unscoped `prks-local-v1` queue cannot be
  delivered to a 0.8 server (its IDs and revisions refer to a database that no
  longer exists), but it may hold user text. It is never silently deleted:
  the 0.8 client reads it once, offers it as a review-only "unsent changes
  from an earlier PRKS version" list with copy-out, and removes it only on an
  explicit discard. Unscoped recovery drafts are treated as unknown-owner,
  review-only drafts, exactly as #489 already treats unknown owners.

### 10.4 New client rules under authentication

- **401/403 are not terminal.** An expired session must pause the sync
  runtime and keep every row and draft, then resume after sign-in. Today an
  unknown 4xx might be classified as a terminal refusal and retire an intent;
  the transport classifies `401`, `403` with `code: "AUTH_REQUIRED"`, `503`
  and network errors as "hold".
- **Library mismatch** (an operation whose ledger Library differs from the
  session's) is answered `409 LIBRARY_MISMATCH` and held, never applied to the
  session's Library.
- **Library epoch.** `GET /api/session`, every state endpoint and every
  ETag carry the Library's `epoch` (§11.4). Each queued intent records the
  epoch it was created under, and each recovery draft records the epoch of
  the base it proved. When the client sees a newer epoch (on startup, on
  resume, in a response header, or from a 409 `LIBRARY_EPOCH_CHANGED`), the
  sync runtime stops delivering older-epoch rows and moves them, with their
  text, to the review-only "unsent changes from before this library was
  restored" list, the same mechanism as the pre-0.8 list in §10.3: copy-out
  and explicit discard, never automatic deletion or re-submission. Drafts
  whose base belongs to an older epoch become unproven-base drafts: shown for
  review, never auto-restored or auto-adopted, exactly as #489 treats unknown
  owners. Cached bodies of an older epoch are invalidated, since an ETag of
  the old epoch never validates against the new one. A revision number alone
  is never taken as proof of a base across epochs, because a restored older
  snapshot can reach the same number again with different content.
- The service worker never caches authenticated API responses beyond what it
  caches today, and the scoped PDF cache is cleared from the scope's
  perspective on sign-out only if the user asks (sign-out on a shared device
  offers "remove this account's offline data").

### 10.5 Reassessment that is deliberately postponed

#310 asks whether every client needs the full offline queue under a central
server. This design keeps it for 0.8 and 0.9, because it is the tested
mechanism behind acknowledged writes, and because changing it at the same
time as the database would remove the safety net while the ground moves. The
reassessment is a separate decision after 0.8, with evidence from the
PostgreSQL deployment: which families need offline intent, which can become
plain online commands with idempotency keys, and what ledger retention (#66)
then allows.

### 10.6 Cutover rule for in-flight browser state

Because pre-0.8 data is not carried (PG2), the cutover release bumps the sync
protocol's server identity (instance ID). A browser holding operations for a
different instance never sends them; §10.3 handles their text. This is what
lets the schema drop `origin_work_id` and legacy operation mappings (§6.4).

---

## 11. Managed files, storage pairing, backup and restore

### 11.1 Database ↔ data-root pairing

- `instance.storage_root_id` records the root the database belongs to.
  Startup reads the root marker (#311 §7.1) and refuses to serve when the IDs
  differ, naming both, never "fixing" either side.
- A data root whose marker exists and whose database is empty is "new
  installation": bootstrap writes the pairing (§9.2).
- A database that is `ready` with a root that has no marker is refused; a
  root is never adopted by a non-empty database.
- Testing databases (`instance.testing = true`) are refused by a non-testing
  process and vice versa, mirroring today's storage testing containment.
- PostgreSQL's data directory is **never** inside the PRKS data root
  (#311 §9.1). Compose uses a separate named volume.
- **Restore keeps the pairing.** A format-2 restore replaces one Library's
  rows and writes its objects into the instance's current root, so
  `instance.storage_root_id` never changes. The #311 §7.1 sentence that
  restore "rewrites the database's expected `storage_root_id`" describes a
  restore that replaces both halves at once; in PRKS that is only the
  operator's whole-instance disaster recovery (§11.4), which restores a
  database and its root together and so restores a matching pair.

### 11.2 Object lifecycle (PG14)

Routing every managed file through `StorageBackend` (#311 Phase B) happens as
part of the Assets lane, against the new schema, not against SQLite.

- **Ingest**: stream to a freshly minted key with exclusive create, hash while
  writing (`ingest_sha256` of the raw stream), fsync per `fs_durability`, then
  (for PDFs) linearize into a **second** new key whose hash becomes
  `content_sha256`. Then one transaction adopts the keys (below) and inserts
  the Asset pointing at the working key (and optionally the source key). A
  crash before COMMIT leaves an unreferenced object, collected by the orphan
  sweep.
- **Rewrite** (annotation materialization, re-linearization, replace): write
  the new bytes to a new key, then one transaction locks the Asset row,
  checks the revision it materialized, adopts the new key (below), switches
  `storage_locator` and
  `content_sha256`, and records a cleanup claim for the old key in the same
  transaction. No object is ever modified in place.
- **Adopting a new key.** Every transaction that first references a key it
  wrote earlier (ingest, rewrite, a person-portrait upload, and any later
  writer of the same shape) takes the per-object lock that settle takes
  (§7.4) for that key, confirms the file still exists with the size it
  wrote, and only then references it. A sweep claim settled before that
  lock fails the write cleanly (the client retries) instead of committing a
  locator to an unlinked file, and a claim settled after it sees the
  reference and keeps the object. Restore's R1 follows the same rule for
  staged keys.
- **Delete**: the existing claim model is kept as specified in
  `backend/AGENTS.md`, with three changes: the claim table is
  `object_cleanup_claims(namespace, name, library_id, recorded_at,
  last_attempt_at, hold)`; the three-valued reference check counts Asset
  locators, person portraits and live restore runs' staged keys instead of
  `works.file_path`; the retry pass
  is a job instead of a startup scan.
- **Staged restore objects** count as referenced. The three-valued
  reference check and the orphan sweep treat a key listed in
  `restore_run_objects` for a run that is neither committed nor failed as
  referenced, however old the object is, so a slow or stalled restore cannot
  lose objects that R4 will point at. R1 inserts each row under the same per-object
  lock that settle takes and only then inspects the file, so a settle either
  sees the row or has already finished before R1 decides to reuse (§11.4). When the run fails (or recovery marks
  it failed), its rows stop protecting anything and unreferenced staged
  objects become ordinary orphans; once it commits, the catalogue references
  them and the rows are deleted.
- **Orphan sweep** (`objects.sweep`, maintenance job): lists the namespace and
  claims objects that no catalogue row references and that are older than a
  grace period (24 h default), so an ordinary write is not claimed; a write
  that stalls past the grace period is caught by its adoption check above
  rather than by age. It
  runs on demand and weekly; it never deletes directly, it only records
  claims, and claims are settled by the usual three-valued check.
- **Why immutable**: backup can copy exactly the bytes a snapshot references
  (§11.4); clients can cache by `content_sha256`; object storage later
  (#311 Phase F) needs no special mode; and #60 §9.4's generation protocol and
  its crash cases disappear. The cost is extra writes per annotation save
  (one new PDF object per materialization), bounded by the cleanup job.
  Measure it with large PDFs before the Assets lane closes; if write
  amplification is unacceptable, materialization can be coalesced per Asset
  by a debounced job without changing the model.

### 11.3 Backup and cleanup coordination

A backup holds `pg_advisory_lock_shared` on `(library, 'objects')` for its
whole run. The cleanup job takes the same key **exclusively** per batch with
`pg_try_advisory_lock`, and skips the batch while a backup runs. So an object
referenced by the backup's snapshot cannot be deleted before the backup has
copied it, and backups never block ordinary writes.

**The inbox lock.** Inbox files are outside the database and outside the
objects lock, so the inbox has its own advisory key `(library, 'inbox')`:

- Every change PRKS makes to inbox files holds it **exclusively** across
  both its database commit and its filesystem change. The lock is
  session-level, so it spans the commit, and it is released with
  `pg_advisory_unlock` in a `finally` (a pooled connection is never
  returned holding it). R3's inbox publication takes it too.
- **An import** takes it before its catalogue transaction. That
  transaction creates the Asset and sets the `processing_files` row to
  `imported` (pointing at the Asset) with `import_sha256`, the raw-stream
  `ingest_sha256` of the source (§11.2), instead of deleting the row.
  Still under the lock, the import then removes the source file and
  deletes the row; both steps are idempotent. A crash between COMMIT and
  that cleanup leaves an `imported` row, which `processing.scan` finishes
  under the same lock. A path does not identify a file, so the scan
  unlinks the file only when it has `import_sha256`, and then deletes the
  row; the file is never offered for import again. If the file is absent,
  it deletes the row. If a different file is at the path (the user or a
  scanner replaced it), it deletes only the row, since the import is done,
  and lists that file as a new pending one.
- A backup takes it **shared** before exporting its snapshot and releases
  it once the inbox files are copied. So the snapshot and the copied inbox
  agree: an import either committed and removed its file before the
  snapshot, or does neither until the copy is done. A crashed import's
  `imported` row and file are archived together and finished by the scan
  after restore. Imports wait only for
  the inbox copy, never for the object copy.

Files the user adds or deletes outside PRKS during a backup are not covered.
An added file is archived without a row, and the scan after restore lists
it. A deleted one leaves a row without a file, which restore holds (§11.4,
"Processing inbox payload") rather than letting a scan delete it.

### 11.4 Library backup format 2 and restore (PG19)

#### Archive

`.prks-backup`, `FORMAT_VERSION = 2`, one Library:

```
manifest.json        format, format_version, created_at, prks_version,
                     schema_version, schema_fingerprint, library_id,
                     source instance_id,
                     interchange: {table: [{column, type}]},
                     identities: {users|devices: {rows, sha256}},
                     tables: {name: {rows, sha256}}, objects: {key: {size, sha256}}
identities/users.csv    archive_user_id, display_name   (no login, no credentials)
identities/devices.csv  device_id, archive_user_id, label, first_seen
db/<table>.csv       the Library's rows of one canonical table, its stored
                     (non-generated) columns, in dependency order
objects/<key>        every object referenced by the snapshot (stored, not deflated)
inbox/<rel_path>     the processing inbox's files, only when the inbox is under
                     the data root (see "Processing inbox payload")
```

**Processing inbox payload.** Unimported inbox files are canonical user data
when the inbox is under the data root, and today's backup carries them
(`docs/storage-architecture.md` §4.1). Format 2 keeps that rule. The
manifest lists them as `inbox: {rel_path: {size, sha256}}` either way,
recorded while the backup holds the inbox lock, and adds `inbox_bytes:
"included"` or `"outside-root"`; only an under-root inbox's files are in the
archive. Inbox files are not
`StorageBackend` objects, so restore handles them apart from `objects/`:

- R1 extracts them into the run's staging directory (on the same
  filesystem as the inbox), never into the inbox.
- R3, after the drain, takes the inbox lock (§11.3) exclusively, in the
  same short transaction and drain timeout as its other locks. That waits
  for any import handler that committed before R2 but has not yet removed
  its source file, so no stale handler can touch the inbox afterwards. Then
  it publishes the files: each staged file is hard-linked into the inbox with
  exclusive create. An absent path is written and an identical file is
  kept. Parent directories are created one component at a time without
  following links; a component that exists as a symlink or a non-directory
  counts as a collision. A different file at the same path is never overwritten: the
  archived copy is linked at `restored-<run>/<rel_path>` instead, and the
  staged `processing_files` row is rewritten to that path, keeping its ID
  and so its roles and tags. `restored-<run>/` is created with exclusive
  `mkdir`; if it exists, the restore aborts.
- **Durability.** R1 fsyncs every staged inbox file and every staging
  directory it created, each with its parent, before R3 publishes
  anything. The staging tree is the undo record (below), so a machine
  crash must not keep an inbox link while losing the staging entry that
  proves R3 created it. Before R4 begins, R3
  fsyncs every inbox directory it added a link to or created, and the
  parent of each directory it created, through `backend/fs_durability.py`
  (directory fsync follows that module's policy, as for objects). So R4
  never commits a row for a link that a machine crash can still lose.
- R4 then inserts the staged rows. Any row whose file R3 neither published
  nor found identical in place (because the user had deleted it before the
  backup, or because the inbox is `outside-root`) is held: `restore_hold =
  <run>` with `restore_sha256` from the manifest, or `NULL` when the
  manifest has no entry for that path.
- **A restore that does not commit removes what R3 published.** The staged
  file and its published link are one inode, so the staging directory is
  the record of what R3 created; no journal is needed. Undo walks the
  staged inbox files and unlinks the file at the inbox path or at
  `restored-<run>/<rel_path>` only when it is the same inode as the staged
  file (`os.path.samefile`) and still has the manifest's hash; a file the
  user put there or changed is kept. Then it removes `restored-<run>/`
  once empty, the only directory that is unambiguously the restore's. Other
  directories R3 created are left in place, empty: the staging tree cannot
  tell whether one existed before R3, and an empty directory holds no data
  and is ignored by the scan. Before undo reports success it fsyncs every
  directory it unlinked from and the parent of `restored-<run>/` through
  `backend/fs_durability.py`. A failed or refused fsync counts as an undo
  failure, so a machine crash cannot bring back a link whose staging
  entry is already gone. It runs under the inbox lock exclusively, while
  the Library is still in maintenance (so no import or scan can see the
  links), and before the staging directory is deleted:
  - R5 runs it on an abort after R3 while the restore connection is alive
    and still holds its locks, after reading on that connection that the
    run is not committed.
  - When the restore connection was lost, recovery runs it inside its
    clearing transaction (below), also holding
    `pg_try_advisory_xact_lock((L,'inbox'))`; if any unlink fails, the
    transaction rolls back and the flag stays set for the next attempt.
  - The stale sweep deletes a failed run's staging directory only after
    its undo has succeeded.
- **Held rows.** A held `imported` row follows the `imported` hash rule
  of §11.3 and is never unlinked on its path alone. For every other held
  row, `processing.scan` never deletes it. It clears the
  hold only when the file at that path has the recorded `restore_sha256`.
  A held row whose file is absent is listed as "file not found after
  restore"; one whose file differs (another `scan_0001.pdf`, a replaced
  file) is listed as "a different file is at this path", and its roles and
  tags are never applied to that file. Both offer a dismiss action that
  deletes the row; a row without a recorded hash is cleared only that way.
  Restored metadata is therefore never removed or misattached without the
  user seeing it.

**Archive member and path checks.** R0 applies format 1's checks
(`_normalize_zip_name`, `_zip_entry_kind` and the duplicate checks of
`_verify_backup_inner` in `backend/backup_restore.py`) to format 2:

- Every member name is a normalized relative POSIX path: no absolute or
  drive path, no `.` or `..` component, no backslash, NUL or empty
  component.
- No symlink or special entry, and no duplicate member or manifest entry.
- Format 1's resource bounds stay: the entry-count cap
  (`DEFAULT_MAX_ZIP_ENTRIES`), the declared-uncompressed-size cap
  (`DEFAULT_MAX_UNCOMPRESSED_BYTES`), compression-ratio rejection
  (`DEFAULT_MAX_COMPRESSION_RATIO`) and the free-space preflight
  (declared size plus `DISK_MARGIN_BYTES`) on the staging filesystem, all
  before R1 writes anything. R1 counts the bytes it extracts and stops at
  each member's declared size.
- Every member belongs to the archive layout above (`manifest.json`,
  `identities/*.csv`, `db/<table>.csv` for a content table, `objects/<key>`
  with a valid key, `inbox/<rel_path>`), and every member is listed in the
  manifest. Both `identities` entries and every `tables` and `objects`
  entry have their member, with the recorded row count and hash. `inbox`
  entries have members only when `inbox_bytes` is `"included"`, and then
  all of them must be present. With `"outside-root"` the `inbox` entries
  are metadata only (their path and hash are still checked), and an
  `inbox/` member is refused.
- Every `inbox` key and every archived `processing_files.rel_path` passes
  the same path check. A path under an earlier run's `restored-<run>/` is
  an ordinary path: `<run>` is minted when the restore starts, so no
  archive can name the new run's directory, which R3 creates with
  exclusive `mkdir`.

R1 extracts with exclusive create below the staging directory, so names
that collide on a case-insensitive filesystem fail closed. R1 and R3 resolve
every destination through the containment helper (`resolved_child_path`
in `backend/storage/paths.py`) and never follow a link.

**Encoding: text, not binary `COPY`.** PostgreSQL documents its binary
`COPY` format as less portable across server versions and architectures and
tied to column types, so an archive written by one major version or schema
could become unreadable before migrations get a chance to run. Format 2
therefore uses `COPY (SELECT <column list> FROM prks.<table> WHERE
library_id = '<validated id>' ORDER BY <primary key>) TO STDOUT WITH (FORMAT
csv, HEADER true, ENCODING 'UTF8')`. The export session pins `TimeZone = UTC`,
`DateStyle = ISO`, `IntervalStyle = iso_8601`, `bytea_output = hex` and
`extra_float_digits = 3`, so every value has one textual form. The column
list is the table's columns with `attgenerated = ''`: generated columns such
as the `*_key` identity keys (§8.1) are never exported, because `COPY … FROM`
refuses them, and restore recomputes them under the instance's own identity
rule.

The manifest's `interchange` block is the conversion contract. It lists every
exported (non-generated) column with an **interchange type** from a small fixed vocabulary (`text`,
`int64`, `bool`, `timestamptz` as ISO 8601 UTC, `json`, `uuid`, `bytes` as
hex), independent of the column's PostgreSQL type. Restore reads a column by
its interchange type and casts it into whatever type the current schema uses,
so a later type change (say `text` to `uuid`) is a cast in a forward
migration, not an unreadable archive. Changing a column's interchange type is
an archive format change: it bumps `format_version` and ships a converter for
the older version.

#### What is exported

- **Canonical rows**: the tables on one explicit, versioned allowlist of
  **content tables** (`BACKUP_CONTENT_TABLES` in the backup module), never
  "every table with `library_id`". The same list drives export, staging, the
  R4 delete and the R4 insert, so access, identity and operational rows are
  never exported, deleted or replaced by a restore. It includes the domain
  tables, cleanup claims (they travel with the objects they describe, as
  today), revisions and the sync ledger. A schema test fails when a
  library-scoped table is on neither this list nor the excluded list below,
  so a new table cannot silently join or miss backups. The ledger and revisions are operational, but they
  travel with the data they describe so exact replay stays correct (§7.3).
- **Attribution identities, without credentials**: every user referenced by
  an exported row (ledger `user_id`, `created_by`-style columns) as
  `archive_user_id` and display name, and every device referenced by the
  ledger. Login names, password hashes, sessions, login device tokens and
  bootstrap tokens are never exported.
- **Not exported, and never touched by restore** (the excluded list):
  `library_memberships` (who may access a Library belongs to the target
  instance, not to the backup), `sessions`, `jobs`, `backup_runs`,
  `restore_runs`, `restore_run_objects`, the `derived` schema (deleted and
  rebuilt by R4 instead), instance settings and secrets, the root marker and
  lease, deployment configuration.

#### Snapshot and verification

- One REPEATABLE READ READ ONLY transaction with an exported snapshot
  (`pg_export_snapshot()`), so the table copies, the identity lists and the
  object list agree. Objects are copied while the shared objects lock is held
  (§11.3).
- Verify before publishing, as today: re-read the archive, check every hash,
  then load it into a scratch schema exactly as restore stages it (below),
  including identity mapping and constraint checks. Only a verified archive
  is offered for download.

#### Staging schema

The migration files schema-qualify every object as `prks` or `derived`
(§6.1), so running them cannot build a separate staging schema; they would
target the live one. Restore and backup verification therefore build
`restore_<id>` from the live tables' own definitions with `CREATE TABLE …
(LIKE prks.<t> …)`, which carries columns, defaults, `CHECK` and `NOT NULL`
constraints, primary keys and unique indexes. `LIKE` does not copy foreign
keys, so R1 checks them with anti-join queries generated from the live
tables' `pg_constraint` rows, with each parent side redirected to where the
parent will be after R4:

- a parent in `BACKUP_CONTENT_TABLES` → its staged table `restore_<id>.<parent>`
  (R4 replaces the live rows, so a live parent proves nothing);
- `devices` → `prks.devices` plus the staged new-device mapping (new devices
  exist only in staging until R4);
- `libraries` and `users` → the live tables, after identity mapping;
- global seed and reference tables that no backup carries (such as
  `argument_verdicts`) → their live tables.

A schema test fails if a foreign key of a content table points at a parent
that none of these rules routes.

R4's inserts into the live tables enforce the same keys again.

Before 1.0 the baseline files are edited in place and the schema version
does not change when they are, so the version alone cannot detect drift.
Backup therefore records `schema_fingerprint`, the SHA-256 of the
normalized catalog of the `prks` schema computed by the same code as the
schema-check test (§4.4), and R0 refuses an archive whose fingerprint
differs from the live database's, or whose `interchange` column list
differs from a live table's non-generated columns, with a message naming
both versions and the first differing table. After 1.0, an archive from an
older schema version is first loaded into a scratch database that the runner
builds at that version and migrates forward, then copied into the staging
schema. How that scratch database is provisioned (it needs `CREATEDB` or an
operator-supplied URL) is decided with the 1.0 compatibility work, which the
golden-archive CI (below) tests.

#### Identity mapping on restore

The staged rows refer to the archive's Library, users and devices; the
target instance has its own. Before anything is committed, restore builds
three mapping tables in the staging schema and rewrites every reference
through them:

- **Library.** The archive's `library_id` maps to the target Library `L`
  that the restore replaces: itself when restoring in place, the
  instance's own Library (minted at bootstrap) when restoring into a new
  instance. Every `library_id` column of every staged table, ledger and
  revisions included, is rewritten to `L`. Entity IDs keep their values, so
  restoring one Library's archive over a *different* Library of the same
  instance, while the original still holds those IDs, is refused before
  commit (the global primary keys would collide); that case is "restore as a
  new Library", which is left to after 1.0 (below).
- **Users.** An `archive_user_id` that exists in the target instance (restore
  into the same instance) maps to itself. Any other maps to the restoring
  owner (in 0.8 there is exactly one user). A later multi-user restore UI may
  offer a per-user choice; the mapping table is already the place for it.
- **Devices.** A device ID that exists in the target and belongs to the
  mapped user is reused. A device ID that does not exist is recorded in the
  staging mapping and inserted, bound to the mapped user, by the R4
  transaction together with the content swap; R1 never writes to
  `prks.devices`, so a restore that fails before R4 leaves no device behind. A device ID that exists but belongs to a *different*
  user is a collision: restore refuses before committing, naming the count of
  affected operations, never re-attributing them silently.
- **Ledger retention.** Ledger rows are kept with their mapped user and
  device, so an operation a browser already sent replays its stored result
  (same `op_id` and hash) instead of re-applying. In a *different* instance,
  browsers do not send queued operations at all, because their stores are
  scoped by instance (§10.3, §10.6); the ledger then only preserves history.
- After mapping, every foreign key into `libraries`, `users` and `devices`
  must resolve; the staging check runs the same constraint set as the live
  schema.

#### Restore exclusion protocol

A maintenance flag set and cleared inside one transaction would never be
visible to anyone else, because PostgreSQL does not expose uncommitted values.
Restore therefore uses a **committed** admission state plus a lock that
drains in-flight writers:

- **The Library maintenance lock.** Every library-scoped command (REST,
  durable operation or job handler) starts its transaction with
  `pg_try_advisory_xact_lock_shared(lib_key(L))` and then reads
  `libraries.maintenance`. If the lock is unavailable or the flag is set, the
  command refuses with 503 `MAINTENANCE` before writing anything; the browser
  treats that as "hold", never as a terminal refusal (§10.4). Workers also
  skip claiming jobs of a Library in maintenance.
- **Objects lock.** Backup holds `(L, 'objects')` shared; cleanup and the
  orphan sweep take it exclusive with a try-lock per batch (§11.3).
  A backup refuses to start while the Library is in maintenance.

Restore runs these phases, recorded in a `restore_runs` row with a lease
(`locked_until`, renewed while it runs):

| Phase | What happens | Visible to others |
| --- | --- | --- |
| R0 Validate | Hashes, manifest, schema and format version (newer refused; after 1.0 older accepted); before 1.0, `schema_fingerprint` and the `interchange` column lists must match the live database (see "Staging schema"); archive members and inbox paths pass the containment checks of "Archive member and path checks" | Nothing |
| R1 Stage | Create `restore_<id>` without the migration runner: for each table in `BACKUP_CONTENT_TABLES`, `CREATE TABLE restore_<id>.<t> (LIKE prks.<t> INCLUDING DEFAULTS INCLUDING CONSTRAINTS INCLUDING INDEXES INCLUDING GENERATED)`, plus the mapping tables; `COPY … FROM` with interchange casts, map identities, then check every foreign key of the live tables with generated anti-joins whose parent side is redirected (staged content tables, live plus newly mapped devices, live Libraries and users; see "Staging schema"); for each archive object, under the per-object lock `pg_advisory_xact_lock(hashtextextended('obj:'||key, 0))` that cleanup's settle also takes (§7.4), insert `restore_run_objects(run, key)` and commit; only then look at the file: missing → write it from the archive with exclusive create; present with the archive's hash → reuse; present with another hash → abort (a live object is never overwritten) | Nothing; reads and writes continue normally |
| R2 Enter | One committed transaction: `UPDATE libraries SET maintenance = 'restore', maintenance_run = <run> WHERE id = L AND maintenance IS NULL` (refused if another maintenance is active, or if a backup run is active) | From here every new command refuses with `MAINTENANCE` |
| R3 Drain | On the restore connection, in one explicit short transaction (`BEGIN; SET LOCAL lock_timeout = <drain timeout>`, then the three locks in this order, then `COMMIT` before R4's SERIALIZABLE transaction begins; §4.3), take `pg_advisory_lock(lib_key(L))` **exclusive** (waits for every in-flight command, which holds the shared lock, to commit or roll back) , `pg_advisory_lock((L,'objects'))` exclusive (waits for a running backup copy, bounded by a timeout that aborts the restore) and `pg_advisory_lock((L,'inbox'))` exclusive (waits for an import between its commit and its source removal, and for a backup's inbox copy; §11.3). Then re-check ownership: `libraries.maintenance_run` must still be this run; otherwise the run lost its lease, so release and abort. With cleanup now excluded by the objects lock, re-verify that every key in `restore_run_objects` exists with the archive's size; rewrite a missing one from the archive, abort on a mismatch. The recheck covers the one race PRKS can cause, cleanup unlinking a reused key, so it checks existence and size and does not rehash: same-size bytes changed outside PRKS or by bit rot could equally change a minute after R4, and detecting that is a data-root integrity check, not part of restore (maintainer decision, 2026-10-13). Then publish the staged inbox files (see "Processing inbox payload") | Commands that slipped in before R2 finish first |
| R4 Commit | One SERIALIZABLE transaction that first locks the `libraries` row `FOR UPDATE` and aborts unless `maintenance = 'restore' AND maintenance_run = <run>` and the `restore_runs` row is still this run's live lease; then: delete the Library's rows of the content tables (the allowlist, never memberships, sessions or operational rows) and its `derived` rows, and set `search_folding = NULL` (§8.6); insert the devices the identity mapping marked as new, then the staged, mapped rows; record a `hold` cleanup claim for every object the old catalogue referenced and the new one does not; cancel the Library's queued and running jobs (`state = 'cancelled'`, `lease_id = NULL`, `last_error = 'restore_superseded'`); set `epoch = epoch + 1` and `maintenance = NULL`; **enqueue** `derived.rebuild`, a cleanup retry, and the orphan sweep at the new epoch; mark the run committed | Atomically: the old Library before COMMIT, the restored one (with its follow-up jobs already queued) after |
| R5 Release | On an abort after R2, finalize the failure (see "Abort finalization"): undo the inbox publication if R3 published anything, then clear the flag in one owner-fenced transaction. Then release all three locks and drop the staging schema, in a `finally` that runs on success and on every abort (`pg_advisory_unlock_all()`, and the dedicated restore connection is closed, never returned to a pool) | Normal operation, immediately after an abort as after a commit |

**Abort finalization.** A restore that fails after R2 but whose process is
still alive (an R3 lock timeout, an R3 re-verification failure, an R4
rollback such as `40001`, a constraint error or `53100`) finishes the
failure itself rather than leaving the Library in maintenance until its
lease expires. While it still holds whatever locks it took in R3 (the
exclusive Library and inbox locks, if it got that far), on the restore
connection:

1. If R3 published inbox files, it undoes them (see "Processing inbox
   payload").
2. Only if the undo succeeded (or nothing was published), one transaction
   locks the `libraries` row `FOR UPDATE` and, only if `maintenance =
   'restore' AND maintenance_run = <run>`, sets both to `NULL` and marks the
   `restore_runs` row failed with the error. `epoch` is unchanged, because
   nothing was restored. If the run no longer owns the flag (recovery took
   it over), the transaction changes nothing.
3. Then R5 releases the locks and drops the staging schema.

If the undo fails, or the connection is lost before step 2 commits, the
flag stays set and crash recovery below handles the run when its lease
expires. A failed restore therefore accepts ordinary commands as soon as
its process has finalized it.

**Crash recovery.** PostgreSQL releases advisory locks when the restore
connection dies. A crash before R2 leaves nothing to undo except the staging
schema and staged objects (dropped, and swept as orphans). A crash between R2
and R4 leaves the flag set with the live data untouched: when the run's lease
expires, startup or the maintenance reaper clears `maintenance` for that
`maintenance_run` and marks the run failed, first undoing any inbox files R3
published ("Processing inbox payload").

**Database capacity: an accepted risk.** R0 checks free space on the
staging filesystem only. The application cannot see free space on the
PostgreSQL volume, and R1 and R4 each need room there for about one more
copy of the Library's rows. 0.8 adds no privileged disk-space probe and no
operator-set restore headroom; the maintainer accepted the residual risk
(2026-10-13). The design commits to these expectations instead:

1. **No partial commit.** R1 writes only into `restore_<id>`, and R4 is
   one transaction. A failed R1 or R4 statement, a disk-full error
   (`53100`) included, rolls back, and the live Library keeps its
   pre-restore content. No restored row is visible until R4 commits.
2. **Clear failure.** A restore reports success only after R4's COMMIT
   returns. Any database error marks the run failed with the PostgreSQL
   error code and message, and the restore response and the restore UI
   show that failure, never a success. If marking the run
   failed cannot be written either, the run stays live until its lease
   expires, and recovery then marks it failed.
3. **No promise of automatic recovery.** If PostgreSQL cannot write WAL,
   it stops (PANIC), and starting it again can need an administrator to
   free space on the volume first. PRKS does not promise uninterrupted
   service or automatic recovery in that case.
4. **Safe cleanup after capacity returns.** Dropping a failed run's
   `restore_<id>` schema, undoing its inbox publication and deleting its
   staging directory are idempotent. Startup and the maintenance reaper
   retry them on every pass, so they complete once there is room, and they
   never touch live tables.
5. **Monitoring and headroom** are documented for deployment (§12.3).

**Fencing a restore that lost its lease.** A restore process can stall
rather than die, outlive its lease and then resume. Recovery therefore never
clears the flag from under a live restore, and a stale restore can never
commit:

- Recovery clears the flag only while holding
  `pg_try_advisory_xact_lock(lib_key(L))` exclusive, in one transaction that
  checks `maintenance_run = <run>` and the expired lease. The transaction-level
  lock is released by that transaction's commit or rollback, so a pooled or
  long-lived worker connection never keeps the Library's exclusive lock after
  recovery. A restore that has
  reached R3 holds that lock, so recovery cannot get it and leaves the run
  alone until the restore connection ends; a hung restore therefore keeps
  the Library in maintenance (safe) rather than losing writes.
- A restore that stalled between R2 and R3 and was recovered finds, after
  taking the lock in R3, that `maintenance_run` is no longer its run, and
  aborts.
- R4 re-checks the same ownership inside its own transaction, under a row
  lock on `libraries`, so even a stall between R3 and R4 cannot commit a
  replacement over writes accepted after recovery. A crash after R4 leaves the
restored Library with its holds recorded and its rebuild, cleanup retry and
orphan sweep already queued, because they were enqueued in the R4
transaction (the same transactional-outbox rule as §5.2). The only R5 work a
crash can skip is dropping the staging schema; startup drops any
`restore_<id>` schema whose run is committed or failed. No file-rename
journal is needed for the database half.

**Search while rebuilding.** R4 deletes the Library's `derived` rows rather
than leaving stale ones, so search can never return rows of the replaced
Library. Until the `derived.rebuild` job for the Library succeeds, search
responses carry `index_state: "rebuilding"` and the UI says results are
incomplete.

#### Restore epoch: fencing work that started before the restore

R2 and R3 stop new commands and drain in-flight command transactions, but
work can *outlive* a transaction: a job handler computing between two
transactions, a job queued before the restore, a browser tab that loaded the
old Library, and a browser outbox with pending intents. Matching revision
numbers cannot tell these apart from current work, because restoring an older
snapshot lowers revisions that later edits raise to the same numbers again.
So every Library has a monotonic `epoch` (§6.2), and R4 advances it.

- **Commands.** Every command states the epoch it acts on, and after taking
  the maintenance lock shared it compares that with `libraries.epoch`. A
  mismatch refuses with 409 `LIBRARY_EPOCH_CHANGED` before any write.
- **API requests.** The browser sends the epoch it loaded in a
  `PRKS-Library-Epoch` header on every authenticated, Library-scoped
  mutation and in every durable operation envelope. Such a mutation without
  it is refused with 400, so no client can bypass the fence (enforced from
  the cutover, once L-F-1 sends it). Identity and instance routes act on no
  Library and are exempt: bootstrap, login, sign-out and session renewal,
  which a browser must be able to call before it has loaded an epoch. Responses carry the current
  epoch.
- **Jobs.** A job records `library_epoch` when it is enqueued. R4 cancels
  every queued and running job of the Library and clears its `lease_id`, and
  the claim statement only selects jobs whose `library_epoch` equals the
  Library's current epoch (§12.1). A handler that is still running after R4
  is fenced twice: its commands carry the job's epoch and are refused, and its
  heartbeat and completion match on `lease_id` and update nothing. Derived
  writes from a handler run in the same kind of fenced transaction.
- **Browser outboxes.** Older-epoch intents and drafts go to review-only
  recovery, as §10.4 describes, so unsent text is kept and never silently
  applied to the replacement state.
- **Replay.** An `op_id` already in the restored ledger still replays its
  stored result (§7.3), because the restored state contains its effect.

`epoch` is per Library and never decreases, including across restores of
older archives, so it also distinguishes two restores of the same archive.
A restore into a new instance needs no special case: the instance ID already
differs, so browsers never send those queues (§10.6).

**Old objects** stay under `hold` claims, never deleted automatically, until
the user confirms "discard the replaced library's files" or a documented
retention elapses. This keeps today's rule that rollback material is not
destroyed implicitly.

Entity IDs are global (§6.2), so 0.8 restores an archive **in place of** a
Library's contents, never as an additional copy beside the original in the
same instance. "Restore as a new Library" needs ID remapping and is left to
the multi-Library work after 1.0.

**Format 1 archives** (SQLite) are refused with a clear message in 0.8
(PG2). The maintainer has no production library; a converter is not built.

**From 1.0**, a golden archive of every released format and schema version is
checked into `tests/fixtures/backups/`, and CI restores each one on every
supported PostgreSQL major (18, and 19 once released) after any schema or
type change.

**Operator disaster recovery**: `pg_dump`/`pg_basebackup` of the cluster plus
a copy of the data root is documented for administrators, but it is not the
user-facing backup and restoring it does not go through PRKS.

### 11.5 Processing inbox

`processing_files.abs_path` is removed; the inbox path is configuration, and
`rel_path` is relative to it. The `/data/for_processing` special case remains
#311's to retire. Scanning becomes a job (`processing.scan`) that the UI
triggers and observes, instead of a GET that mutates.

### 11.6 Pre-0.8 data roots

A root containing `prks_data.db` is a pre-0.8 library. Startup of 0.8 refuses
to bind it as a new PostgreSQL library and says so: "this folder holds a
library from an earlier PRKS version, which 0.8 cannot open". It never
deletes, renames or modifies the file. A fresh root (or an explicit new
directory) is used instead. This is the "never silently destroy unknown
personal files" rule applied to the clean start.

---

## 12. Jobs, workers and deployment

### 12.1 Jobs table (PG15)

```sql
CREATE TABLE prks.jobs (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  library_id    text REFERENCES prks.libraries ON DELETE CASCADE,
  library_epoch bigint,               -- libraries.epoch at enqueue; NULL iff library_id IS NULL
  kind          text NOT NULL,
  payload       jsonb NOT NULL DEFAULT '{}',
  dedupe_key    text,                 -- coalesces repeated enqueues of the same work
  singleton_key text,                 -- at most one running per key
  state         text NOT NULL DEFAULT 'queued'
                CHECK (state IN ('queued','running','succeeded','failed','cancelled')),
  priority      smallint NOT NULL DEFAULT 0,
  run_after     timestamptz NOT NULL DEFAULT now(),
  attempts      integer NOT NULL DEFAULT 0,
  max_attempts  integer NOT NULL DEFAULT 10,
  lease_id      uuid,                 -- minted on every claim; fences stale workers
  locked_by     text,
  locked_until  timestamptz,
  last_error    text,                 -- metadata-only, per logging privacy rules
  progress      jsonb,
  created_by    text REFERENCES prks.users,
  created_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz,
  CHECK ((library_id IS NULL) = (library_epoch IS NULL))
);
CREATE UNIQUE INDEX jobs_dedupe ON prks.jobs (kind, dedupe_key) WHERE state = 'queued';
CREATE UNIQUE INDEX jobs_singleton ON prks.jobs (singleton_key) WHERE state = 'running';
CREATE INDEX jobs_ready ON prks.jobs (priority DESC, run_after) WHERE state = 'queued';
CREATE INDEX jobs_expired ON prks.jobs (locked_until) WHERE state = 'running';
```

- **Enqueue** inside the command transaction (`INSERT … ON CONFLICT DO
  NOTHING` on the dedupe index, with `library_epoch` set to the epoch the
  command checked) and `NOTIFY prks_jobs` at COMMIT. The job
  exists if and only if the mutation committed: this is the transactional
  outbox.
- **Claim and reclaim** are one statement, so a job whose worker died is
  picked up by the same path as a fresh one:

  ```sql
  UPDATE prks.jobs
     SET state = 'running', lease_id = gen_random_uuid(), locked_by = %(worker)s,
         locked_until = now() + %(lease)s, attempts = attempts + 1
   WHERE id = (SELECT j.id FROM prks.jobs j
                WHERE ((j.state = 'queued'  AND j.run_after <= now())
                    OR (j.state = 'running' AND j.locked_until < now()))
                  AND j.attempts < j.max_attempts
                  AND (j.library_id IS NULL OR EXISTS
                       (SELECT 1 FROM prks.libraries l
                         WHERE l.id = j.library_id AND l.maintenance IS NULL
                           AND l.epoch = j.library_epoch))
                  AND (j.singleton_key IS NULL OR j.state = 'running' OR NOT EXISTS
                       (SELECT 1 FROM prks.jobs r
                         WHERE r.singleton_key = j.singleton_key AND r.state = 'running'))
                ORDER BY j.priority DESC, j.run_after
                FOR UPDATE SKIP LOCKED
                LIMIT 1)
  RETURNING id, kind, payload, library_id, library_epoch, lease_id, attempts;
  ```

  `attempts` counts **claims**, so a job that crashes its worker every time
  still runs out of attempts. A reclaimed job keeps `state = 'running'`, so
  the singleton index is never violated by a reclaim; only the lease changes.
- **Singleton arbitration.** A queued job whose `singleton_key` already has a
  `running` row (live or expired) is skipped, so it waits for that run to
  finish, be reclaimed or be reaped. Two workers can still race: each sees no
  running row in its snapshot and claims a *different* queued row with the
  same key, and the second `UPDATE` then fails on `jobs_singleton` with
  `23505` once the first commits. The claim runs as its own single-statement
  transaction, so the worker treats `23505` on `jobs_singleton` as "lost the
  race": nothing changed, the losing row is still `queued`, and the worker
  immediately claims again, when the `NOT EXISTS` now sees the committed
  running row and skips that key. Any other error stays an error.
- **Epoch.** Only jobs of the Library's current epoch are claimable, and every
  transaction a handler opens (commands, derived writes, completion) checks
  the job's epoch and `lease_id` (§11.4).
- **Lease token.** Every claim mints a new `lease_id`. Heartbeats
  (`UPDATE … SET locked_until = now() + lease WHERE id = %s AND lease_id = %s`),
  completion and failure all match on `id`, `lease_id` **and** `state =
  'running'`. A stale worker
  that resumes after its job was reclaimed updates zero rows; it learns that it
  lost the lease and discards its result. Its side effects, if any were
  already made, are covered by the next rule.
- **Idempotent handlers.** At-least-once is the only honest guarantee, so every
  handler is idempotent: canonical writes go through commands with their own
  revision or compare-and-set checks, derived writes are keyed by content
  (`asset_text` by `content_sha256`), and object deletions go through the
  three-valued claim check.
- **Run** outside the claim transaction; long jobs heartbeat. On success the
  job is marked `succeeded`; on a handled failure it returns to `queued` with
  exponential backoff in `run_after`, or becomes `failed` once `attempts` has
  reached `max_attempts`, so no job is left queued but unclaimable.
- **Retry versus a queued duplicate.** `jobs_dedupe` covers only `queued`
  rows, deliberately: an enqueue while the same work is running must still
  queue a fresh run, because the running one may have read state that changed
  since. So a failed job cannot always return to `queued`: a twin with the
  same `(kind, dedupe_key)` may already be queued. The failure update
  therefore cancels instead of requeueing when a twin exists, in the same
  statement that checks the lease
  (`SET state = CASE WHEN attempts >= max_attempts THEN 'failed' WHEN
  EXISTS (queued twin) THEN 'cancelled' ELSE 'queued' END, last_error = …`,
  and `lease_id = NULL`); the exhausted branch comes
  first, so a job's last attempt never returns to `queued`, where the claim
  would skip it forever while it held its dedupe key. The queued twin does
  the same work. If a twin
  commits between that check and the update, the update fails with `23505` on
  `jobs_dedupe` and changes nothing, and the worker repeats it, now seeing the
  twin and cancelling. Reclaim never hits this, because a reclaimed job stays
  `running`.
- A **reaper** transaction (run by every
  worker on its idle tick) marks `failed` any job that is `running` with an
  expired lease and `attempts >= max_attempts`, with `last_error =
  'lease_expired'` and `lease_id = NULL` in the same update. An exhausted
  job never stays `running` forever, its singleton key is released, and a
  worker that resumes after the reap matches no row, so it can neither commit
  handler work nor overwrite the failure. Failed jobs are visible in
  diagnostics.
- **Retention**: succeeded rows older than 7 days are deleted by a
  maintenance job; failed rows are kept until dismissed.
- **0.8 job kinds** (only workloads that exist today): `text.extract`,
  `research.reindex_work`, `objects.cleanup`, `objects.sweep`,
  `search.rebuild`, `derived.rebuild`, `backup.create`, `processing.scan`,
  `pdf.materialize` (if the Assets lane moves materialization off the request
  path), `sessions.prune`, `jobs.prune`. Thumbnails stay on-demand and cached
  as derived files.
- A separate `domain_events` table for external subscribers (#314 connectors,
  automation #62) is **not** created until one exists.

### 12.2 Processes

One entry point, several roles:

| Command | Role |
| --- | --- |
| `prks serve` | HTTP; with `PRKS_EMBEDDED_WORKER=1` (default for a personal install) also runs one worker thread |
| `prks worker` | Job worker only (Compose) |
| `prks migrate` | Apply schema files, then exit |
| `prks admin bootstrap \| unlock-login \| rebuild-derived \| check \| backup \| restore` | Maintenance, using the same commands as the API |

`python prks_app.py --testing` keeps working for agents and developers: it
uses `PRKS_DATABASE_URL` (or the default local test URL), refuses a
non-testing instance, runs migrations, signs in as owner on loopback and
embeds the worker.

### 12.3 Docker Compose

```yaml
services:
  db:
    image: postgres:18
    environment:
      POSTGRES_DB: prks
      POSTGRES_USER: prks
      POSTGRES_PASSWORD_FILE: /run/secrets/prks_db_password
      POSTGRES_INITDB_ARGS: "--locale-provider=builtin --builtin-locale=PG_UNICODE_FAST"
    volumes: [pgdata:/var/lib/postgresql]   # postgres:18 keeps PGDATA in /var/lib/postgresql/18/docker
    secrets: [prks_db_password]
    healthcheck: {test: ["CMD-SHELL", "pg_isready -U prks -d prks"], interval: 5s, retries: 20}
  migrate:
    image: prks:latest
    command: ["python", "/app/prks_app.py", "migrate"]
    environment: &prks_env
      PRKS_DATABASE_URL_FILE: /run/secrets/prks_database_url
      PRKS_STORAGE: /data
    secrets: [prks_database_url]
    depends_on: {db: {condition: service_healthy}}
  web:
    image: prks:latest
    command: ["python", "/app/prks_app.py", "serve", "--host", "0.0.0.0"]
    environment: *prks_env
    ports: ["${PRKS_PUBLISH_HOST:-127.0.0.1}:8080:8080"]
    volumes: [./data:/data]
    secrets: [prks_database_url]
    depends_on: {migrate: {condition: service_completed_successfully}}
    healthcheck: {test: ["CMD", "python", "-c", "…GET /readyz…"]}
  worker:
    image: prks:latest
    command: ["python", "/app/prks_app.py", "worker"]
    environment: *prks_env
    volumes: [./data:/data]
    secrets: [prks_database_url]   # Compose grants a secret only to services that list it
    depends_on: {migrate: {condition: service_completed_successfully}}
volumes: {pgdata: {}}
secrets:
  prks_db_password: {file: ./secrets/db_password}
  prks_database_url: {file: ./secrets/database_url}
```

(Illustrative; the deployment lane owns the final file.) Rules:

- **Database volume.** From PostgreSQL 18 the official image keeps `PGDATA`
  at `/var/lib/postgresql/<major>/docker` and declares its volume at
  `/var/lib/postgresql`. The named volume must be mounted at
  `/var/lib/postgresql`, never at the pre-18 `/var/lib/postgresql/data`, or
  the cluster lands outside the volume (or the image refuses the old layout).
  The versioned subdirectory also lets a later `pg_upgrade` container see both
  majors in one volume. The deployment smoke test proves persistence:
  `up` → bootstrap and write data → `down` → `up` → data present, and again
  after `down` plus container removal and recreation (volume kept).
- Loopback publish stays the default. Remote exposure requires deliberate
  configuration and HTTPS through a reverse proxy, documented with
  `PRKS_PUBLIC_ORIGIN` and `PRKS_TRUSTED_PROXIES`.
- Secrets come from files (`*_FILE` variables), never from the image, logs,
  backups or client-readable APIs (#612).
- **Health**: `/healthz` (process alive, no I/O) and `/readyz` (pool can
  borrow, schema current, storage bound and paired, setup state). Neither
  reveals configuration.
- **Graceful stop**: on SIGTERM, `web` stops accepting, finishes in-flight
  requests within a deadline and closes the pool; `worker` stops claiming,
  finishes or releases its job lease. Compose `stop_grace_period` covers the
  deadline.
- **Upgrades**: `migrate` runs before `web`/`worker` start; both refuse to
  run against a schema newer or older than their code.
- **Database volume capacity.** The deployment documentation tells the
  operator to monitor free space on the PostgreSQL volume, which PRKS
  cannot see (§11.4, "Database capacity"). It recommends keeping free at
  least twice the database's size plus `max_wal_size` (1 GB by default),
  because a restore stages one copy of a Library's rows and R4 writes
  another before the old rows are vacuumed. It also says what to do when
  the volume fills: free space, restart `db` if it stopped, and let PRKS
  clean up the failed restore.
- PostgreSQL tuning stays at image defaults plus documented
  `shared_buffers`/`work_mem` guidance; nothing is required for correctness.

### 12.4 Diagnostics

Performance diagnostics stay process-local and privacy-safe, now labelled by
process role. New aggregate counters: pool wait time and exhaustion count,
job queue depth and age by kind, failed jobs, migration version. No SQL
parameters, search terms or library metadata are recorded, as today.

---

## 13. Tests, gates and what is retired

### 13.1 Test infrastructure

- **Local**: `PRKS_TEST_DATABASE_URL` points at a disposable PostgreSQL 18
  (a `scripts/test-postgres` helper starts `postgres:18` in Docker with the
  builtin locale on a random port, or reuses a running one). The test harness
  **refuses** any URL whose database is not created by the harness: it
  creates `prks_test_<run>` databases itself and marks them
  `instance.testing = true`, so a misconfigured URL cannot touch a real
  library. This is the PostgreSQL equivalent of `PRKS_TESTING` containment.
- **Template database**: the run creates `prks_test_template` from the schema
  files once; each test class gets `CREATE DATABASE … TEMPLATE
  prks_test_template` (tens of milliseconds) and drops it afterwards. Tests
  that only read may share one database; tests use a fresh temporary data
  root as today.
- **E2E**: seeded templates become seeded template databases plus a copied
  data root; each `AppServer` gets its own database cloned from the seed
  template. The WAL-checkpoint seeding code in `tests/e2e/harness.py` is
  removed.
- **CI**: `test-gate.yml` and `e2e-gate.yml` add a `services: postgres:18`
  container with the builtin locale; a nightly or label-triggered matrix adds
  PostgreSQL 19 once it is released.
- **Backend-parametrized parity** during build-out (§15): selected existing
  unit, API and sync test modules run against both `PRKSDatabase` (SQLite)
  and the PostgreSQL facade. A family is "ported" when its parametrized tests
  pass on PostgreSQL. After cutover the parametrization and the SQLite leg
  are deleted.

### 13.2 New gates

| Gate | Replaces | Checks |
| --- | --- | --- |
| Schema snapshot (`check_pg_schema.py` + test) | `check_schema_change.py` SQLite parts, `validate_current_schema` | fresh schema equals snapshot; after 1.0, migrated equals fresh, migrations append-only and unedited |
| Persistence boundary | (new) | `psycopg` imported only in `backend/db/` and `backend/domain/`; no SQL strings in `server.py`/`backend/api/`/`backend/sync/` |
| Revision ownership | discussion #583 option (a) | DML on revisioned tables only in `backend/domain/`; FKs into revision-owning rows are not `ON DELETE CASCADE` unless listed with the command that tombstones them |
| Isolation declared | (new) | every command declares its isolation level; no command writing a table a SERIALIZABLE command decides on declares READ COMMITTED (§7.1); a tag added to the source during a tag merge either lands on the target or makes the merge retry |
| Text identity vectors | (new) | Python, SQL and TypeScript agree on every vector |
| Library isolation | (new) | two-Library API sweep (§13.3) |
| Correlated catalogue counts | kept, re-justified | PostgreSQL often decorrelates, but not always; keep the ratchet and re-baseline its allowlist with `EXPLAIN` evidence during the family ports |

`scripts/check_invariants.py` rules that parse SQLite quoting are updated by
the lane that ports the relevant module.

### 13.3 New test classes

- Concurrency interleavings (§7.6).
- Two-Library isolation: every route family, with IDs from Library A used
  in a Library B session, must 404 or 403 and leave A unchanged.
- Restore fault injection at each phase R0–R5 of §11.4, including a crash
  during object staging, between R2 and R4 (flag cleared by recovery, data
  untouched) and immediately after COMMIT.
- Restore reuse vs cleanup: a pending cleanup claim for key K whose settle
  runs while R1 reuses K → either settle sees the `restore_run_objects` row
  and keeps K, or R1 finds K gone and rewrites it; R4 never commits a locator
  to a missing object.
- Restore archive containment: archives with an absolute, drive, `..`,
  backslash or duplicate member, a symlink entry, too many entries, a
  declared size over the cap or the free space, a member whose compression
  ratio is over the cap or whose stream is longer than declared, a member outside the
  layout or missing from the manifest, an `inbox/` member in an
  `outside-root` archive or a missing one in an `included` archive, or an
  inbox path that escapes the inbox are refused in R0, an `outside-root`
  archive with metadata-only `inbox` entries passes R0 and backup
  self-verification, and nothing is
  written outside the staging directory; an inbox subdirectory replaced by
  a symlink is treated as a collision in R3.
- Restore inbox undo: an R4 failure and a kill between R3 and R4 each leave
  the inbox's files as they were before the restore (published links and
  `restored-<run>/` removed, the user's identical and changed files and
  pre-existing directories kept), and no scan lists the undone files.
- Restore identity side effects: after a crash between R1 and R4,
  `prks.devices` is unchanged; after R4 every new device exists exactly once.
- Restore staging vs sweep: an orphan sweep run during a stalled R1 older
  than the grace period records no claim for staged keys; after the run
  fails they become orphans; after it commits they are referenced.
- Stalled write vs sweep: an ingest and a rewrite (re-linearization or
  replace), each paused after writing its new key, past the grace period,
  while a sweep claims and settles that key, fail cleanly at the adoption
  check and commit no reference; a claim settled after their commit keeps
  the object.
- Restore staging isolation: building and loading `restore_<id>` leaves the
  live schemas' catalog snapshot unchanged; a staged child whose parent is
  missing from the archive is refused in R1 even when that parent still
  exists live; a restore into a new instance and ledger rows from a new
  device both pass the R1 key checks; an archive whose schema fingerprint or
  column list differs from the live database (same schema version) is
  refused in R0 with a message, not a raw `COPY` error.
- Backup table classification: every library-scoped table is on exactly one
  of the content and excluded lists; a restore leaves memberships and
  sessions untouched.
- Long maintenance sessions: a backup whose object copy outlasts the
  request-session idle timeout completes with its snapshot and objects lock
  intact; the R3 drain waits past 5 s for a slow in-flight command and aborts
  cleanly only at its own drain timeout. With a short drain timeout and a
  command that never finishes, the drain fails with `lock_not_available`
  at that timeout (so the test fails if `SET LOCAL` ran outside a
  transaction and had no effect), the connection's `lock_timeout` is back to
  its session value afterwards, and the restore connection holds no advisory lock
  once the abort finishes.
- Restore lease fencing: stall a restore between R2 and R3 past its lease,
  let recovery clear the flag and admit writes, then resume it: it aborts in
  R3 and the admitted writes survive; recovery cannot clear the flag of a
  restore that holds the R3 lock; recovery run on a pooled worker connection
  holds no advisory lock after its transaction ends, and the next command on
  that Library takes its shared lock at once.
- Restore exclusion interleavings: a write in flight at R2 finishes before
  R4; a write after R2 gets `MAINTENANCE` (that the browser holds it is part of PG-C1's restore scenario); a cleanup
  batch and an orphan sweep during restore are skipped; a running backup
  makes restore wait or abort cleanly; a backup during maintenance is refused;
  a restore that aborts after R3 holds none of its three locks, and an
  import of the same Library then succeeds.
- Restore into a **new instance** with real acknowledged, replayed and
  pending sync operations: every user and device reference resolves, a
  replay of an already-ledgered `op_id` returns its stored result, a device
  collision is refused before commit, and the full foreign-key and integrity
  check passes.
- Portable archives: restore golden archives after a column type change and
  on each supported PostgreSQL major (from 1.0, every released version).
- Jobs: kill a worker after claim → lease expiry → exactly one reclaim →
  idempotent completion; the stale worker's completion updates nothing; a job
  exhausted by repeated crashes ends `failed` and frees its singleton key.
- Retry versus duplicate: while job A runs, enqueue a twin B with the same
  `dedupe_key`; A's handled failure cancels A (no `23505` reaches the caller),
  B runs once; without a twin, A returns to `queued` with backoff.
- Singleton jobs: two concurrent workers and several queued jobs with one
  `singleton_key` → never two running, the race loser retries without error
  and claims another key or nothing, a job queued during a running singleton
  waits and then runs.
- Restore fencing: restore an older snapshot while a long heartbeating job
  and a pending browser edit exist → the job's later commands and completion
  are refused, the browser edit gets `LIBRARY_EPOCH_CHANGED` and its text
  appears in review-only recovery, an already-ledgered `op_id` still replays,
  and no row of the replacement state changes.
- Restore crash after commit: kill the process immediately after R4 COMMIT →
  after restart the rebuild job is queued and runs, search reports
  `rebuilding` until it finishes and then passes the parity checks, and no
  stale `derived` row of the replaced Library is ever returned.
- Object lifecycle: rewrite produces a new key; claims for old keys; backup
  during cleanup; orphan sweep grace period.
- Search parity (§8.7) and identity vectors (§8.1).
- Bootstrap: empty install, repeated bootstrap, token expiry, takeover
  attempt after `ready`, restore not triggering bootstrap.
- Session and CSRF: missing/mismatched Origin, cross-site `Sec-Fetch-Site`,
  expired session pauses (does not drop) the browser queue (E2E).
- Pool exhaustion and database restart (connection loss mid-request returns
  503 and is not ledgered).

### 13.4 Retirement checklist (all in the cutover release)

| Retired | Why it can go |
| --- | --- |
| `backend/db_manager.py`, `backend/db_schema.sql`, `backend/db_migrations.py` (legacy bridge and v10–v17) | Clean start (PG2); replaced by domain packages and schema files |
| `backend/work_identity.py` mirror and drift checks, `legacy_work_*` views, mirror triggers, `legacy_inferred_video_urls`, `migration_quarantine`, `work_retirement(_guard)` | §6.4 |
| `works_fts`, `backend/text_index.py` SQLite store, `prks_text_index.db`, `backend/research_index.py` SQLite store, `prks_research_index.db` | §8 (logic for extraction and mention parsing is kept and moved) |
| `backend/concurrency.py` gate, save-token dicts, materialization and path lock dicts, `_ready_backups` | §7.5 |
| Backup format 1 code paths: `Connection.backup`, PRAGMA checks, sidecar renames, file-rename journal for the DB half | §11.4 |
| Root adoption by SQLite header | §11.1, §11.6 |
| `processing_files.abs_path` and its restore rewrite | §11.5 |
| `test_db_migrations.py`, `test_sqlite_connection_lifetime.py`, SQLite parts of `test_backup_restore.py`, `test_restore_durability.py`, `test_text_index.py`, `test_work_identity.py` mirror tests, `test_publisher_catalog_queries.py` ASCII pins | Replaced by §13.2–13.3 tests, not deleted without a replacement for each guarantee they cover |
| `INV-TESTS-001` schema-version literal rule | Rewritten for migration versions |
| README/wiki SQLite statements (README, wiki Architecture, Storage-Backup-and-Restore, Configuration-and-Operations, Getting-Started, PDFs-and-Annotations, Research-Network, Offline-and-Sync, Developer-Reference), `backend/AGENTS.md` SQLite rules | Updated in the cutover PR and the docs PR that follows it |

**Rule for deleting a test**: a SQLite-specific test is deleted only in the
PR that adds the PostgreSQL test covering the same guarantee, and the PR
description lists the pairs. Guarantees with no PostgreSQL meaning (WAL
checkpointing, rowid preservation) are listed as retired with the reason.

---

## 14. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- | --- |
| R1 | Behavior drift while porting ~50 sync families and ~130 routes | High | High | Backend-parametrized tests per family (§13.1); full E2E on the PostgreSQL leg before cutover; one family per PR |
| R2 | Acknowledged-save or recovery guarantee broken by the auth/session change (an expired session drops queued intents) | Medium | Critical | §10.4 rule, E2E for session expiry mid-queue, #465/#466 suites unchanged and required |
| R3 | Unicode identity changes merge names users consider different (or vice versa) | Medium | Medium | Shared vectors reviewed by the maintainer (§16, decision 3), no diacritic folding in identity, NAME_TAKEN instead of silent merge |
| R4 | `casefold` cannot be used in an IMMUTABLE generated column | Low | Low | Fallback in §8.1 |
| R5 | Write amplification from immutable objects on frequent annotation saves | Medium | Medium | Measure in the Assets lane; debounce materialization per Asset |
| R6 | Restore of a large Library holds a long SERIALIZABLE transaction | Medium | Low | Restore already runs in maintenance mode; staging happens before the transaction; measure on a synthetic large Library |
| R7 | Pool exhaustion under the thread-per-connection server | Medium | Medium | 503 with Retry-After, pool metrics, configurable size; server replacement study (R9) |
| R8 | Parallel lanes collide in shared files (`server.py`, facade, schema, sync registry) | High | Medium | §15 ownership table, mixin files, per-lane schema files, single-line registrations |
| R9 | stdlib HTTP server unsuitable for an exposed 1.0 deployment | Medium | High | Pre-1.0 research issue (§4.5); reverse-proxy-only exposure documented meanwhile |
| R10 | Tests become slow or flaky with a database service | Medium | Medium | Template databases, per-class databases, no shared mutable fixtures; measure in PG-1 |
| R11 | Agents run tests against a real database | Low | Critical | Harness-created databases only, `instance.testing` flag, refusal on mismatch (§13.1) |
| R12 | `unaccent` rules wrong for Greek/Serbian | Medium | Low | Parity vectors; optional PRKS rules file (§16, decision 4); the folding check rebuilds search data when the rules or the server release change (§8.6) |
| R13 | Dual-run period lingers and SQLite stays half-alive | Medium | High | Cutover and deletion in the same release (PG22), tracked as a release gate in #614 |
| R14 | Browser data from pre-0.8 lost at cutover | Low | High | §10.3 review-only legacy queue and drafts, never auto-deleted |
| R15 | #60 Version/merge features designed against the old mirror model | Low | Medium | §6.4 states the mechanics change; #60 slices G–L consume the new schema |
| R16 | An archive becomes unreadable after a PostgreSQL major or column-type change | Medium | Critical after 1.0 | Text `COPY` with interchange types (§11.4); golden archives restored in CI on every supported major |
| R17 | A restore races ordinary writers, jobs or cleanup | Medium | High | Committed maintenance flag, maintenance lock drain, objects lock (§11.4); interleaving tests (§13.3) |
| R18 | Work begun before a restore (a running job, an open tab, a browser outbox) is applied to the restored Library | Medium | High | Library `epoch` checked by every command, job claim and job transaction; older-epoch browser rows go to review-only recovery (§10.4, §11.4); restore-fencing tests (§13.3) |
| R19 | The PostgreSQL volume fills during restore staging | Low | Medium | Accepted by the maintainer (2026-10-13): no disk-space probe or headroom setting in 0.8; R1/R4 roll back, the failure is reported, cleanup is idempotent, and deployment docs cover monitoring and headroom (§11.4, §12.3) |

Dependencies: #60 (model, unchanged in meaning), #311 (Phase B is absorbed
into the Assets lane; D–E later), #612 (bootstrap and configuration
inventory), #45 (contract versioning, auth scheme in OpenAPI), #46 (packaging
of a bundled PostgreSQL for desktop, after 1.0), #57 (history builds on actor
attribution), #66 (ledger retention), #67 (route extraction), #476 (cross-tab
ack coherence stays its own decision).

---

## 15. Implementation sequence

### 15.1 Strategy

Build the PostgreSQL implementation **beside** the running SQLite backend,
family by family, behind the facade (§5.5) and the parametrized tests
(§13.1). `master` keeps running on SQLite and stays releasable until the
cutover PR switches the default and deletes SQLite in the same release. No
PR migrates data, because there is none to migrate.

Each PR states its slice ID, carries `Refs #613` and the slice's issue, and
stops for maintainer review. No PR is merged without explicit approval.

### 15.2 Ownership boundaries for parallel agents

| Lane | Owns (may create and edit) | Must not edit |
| --- | --- | --- |
| Foundation (PG-1, PG-2, PG-3) | `backend/db/**`, `backend/db/schema/000_*.sql`–`020_*.sql`, `tests/pg/**` harness, CI workflow DB service, `requirements*.txt`, `dependency-inventory.json`, `backend/pg_facade/__init__.py` | domain packages |
| Identity (L-ID) | `backend/domain/identity/**`, `020_identity.sql` (after PG-2 hands it over), `backend/api/session.py`, auth admission hook in `server.py` (one function) | other domain packages |
| Works (L-W) | `backend/domain/works/**`, `100_works.sql`, `backend/pg_facade/works.py`, `backend/sync/handlers/work_*.py` | people/organization/research files |
| People (L-P) | `backend/domain/people/**`, `110_people.sql`, `pg_facade/people.py`, `sync/handlers/person*.py`, `work_role` handler | works schema (references only) |
| Organization (L-O) | `backend/domain/organization/**`, `120_organization.sql`, `pg_facade/organization.py`, tag/folder/playlist handlers, `domain/saved_views`, `domain/settings` | |
| Research (L-R) | `backend/domain/research/**`, `130_research.sql`, `derived` mention tables, `pg_facade/research.py`, concept/position/argument/note handlers | |
| Annotations and objects (L-A) | `backend/domain/annotations/**`, `backend/domain/objects/**`, `140_annotations.sql`, `150_objects.sql`, Phase B routing in `backend/storage/**`, `pg_facade/annotations.py` | works projection (calls it) |
| Processing (L-PR) | `backend/domain/processing/**`, `160_processing.sql`, `pg_facade/processing.py` | |
| Search (L-S) | `backend/domain/search/**`, `200_derived_search.sql`, `pg_facade/search.py`, text extraction job | |
| Jobs (L-J) | `backend/jobs/**`, `030_jobs.sql`, `prks worker` entry point | job handlers (owned by their lanes) |
| Backup (L-B) | `backend/domain/maintenance/backup*`, `restore*`, `backup_runs` table | |
| Deployment (L-D) | `Dockerfile`, `docker-compose.yml`, `docker-entrypoint.sh`, `scripts/test-postgres`, README deployment section, health endpoints | |
| Browser scoping (L-F) | `frontend/js/local-store.js`, `offline-store.js`, `frontend-app/src/lifecycle/editor-recovery/**` scope fields, sync-runtime auth hold | backend |

`server.py` is edited by at most one open PR at a time. Lanes that need a
route change add it through the facade method they own, so most lanes never
touch `server.py` at all.

### 15.3 Slices, dependencies and acceptance criteria

**Stage 0. Design (this PR).** Approved 2026-10-12 (§16). After this
document is merged, each slice below becomes one engineering-task issue under
the v0.8.0 milestone, linked from #614, with native issue dependencies
matching the "Depends on" column. No slice starts without the maintainer's
explicit go, and PG-1 comes first.

**Stage 1. Foundation (sequential).**

| Slice | Content | Acceptance criteria |
| --- | --- | --- |
| **PG-1** Database plumbing and test harness | psycopg + psycopg_pool pins; `backend/db/` config (`PRKS_DATABASE_URL`, `*_FILE`), pool, `command()`/`query()` helpers with isolation, error mapping table; harness-created test databases with template cloning and refusal of foreign databases (ownership marked with a `COMMENT ON DATABASE` until PG-2 adds `instance.testing`); `scripts/test-postgres`; CI service container in `test-gate.yml`. No domain tables. | Unit tests for helpers (commit, rollback on exception, SERIALIZABLE retry on injected `40001`, READ ONLY query refuses writes); error-mapping tests per SQLSTATE; pool exhaustion → typed error; harness refuses a pre-existing database; CI green with the service; existing SQLite suites untouched and green. |
| **PG-2** Migration runner, schema layout and core tables | Runner with advisory lock and checksums; `000_extensions.sql`, `010_instance.sql`, `020_identity.sql`, `030_jobs.sql`, `040_sync.sql` (`entity_revisions`, `sync_operations`, `devices`); `prks.identity_key` with the immutability proof (§8.1); `prks.search_norm` with the snapshot assertion that no index expression or column default depends on it (§8.3); text identity vectors and Python implementation; schema snapshot test and `check_pg_schema.py`. | Fresh database matches the snapshot; concurrent `migrate` runs apply once; a newer-schema database is refused; a database whose applied baseline file has since changed is refused with the recreate message; identity vectors pass in SQL and Python; `IMMUTABLE` generated column works under the builtin locale (or the fallback is chosen and documented). |
| **PG-3** Parity harness and facade skeleton | `PostgresLibrary` facade composed of empty mixins; a test fixture parametrizing selected modules over `sqlite` and `postgres`, with a registry marking which methods are ported (unported ones skip on `postgres`, not fail); a dual-run CI job. | Running `python run_tests.py` unchanged still runs SQLite only; the `postgres` leg runs and reports ported/unported counts; no lane needs to edit the fixture to register its family (one line in its mixin). |

**Stage 2. Parallel lanes** (each may start once its dependencies are
merged; several run at the same time).

| Slice | Depends on | Content | Acceptance criteria |
| --- | --- | --- | --- |
| **L-ID-1** Identity, bootstrap and sessions | PG-2 | Users, Libraries (with `epoch`), memberships, bootstrap tokens and command, Argon2id login with the attempt limiter, login-device tokens and `prks admin unlock-login`, sessions, `GET /api/session` (returns the epoch), `RequestContext` carrying the acted-on epoch and the command-helper epoch check, `authorize()`, loopback-owner mode for `--testing`; CSRF hardening | §13.3 bootstrap and session tests; a command with a stale or missing epoch is refused before writing; login throttling: without a login-device cookie, the sixth attempt for an account and the 21st for an address within the window get 429 with `Retry-After` without a password check, and window expiry or a successful login resets the account row; 20 simultaneous wrong-password requests for one account run at most 5 password checks; with the account limit tripped, the correct password from a browser holding a valid login-device cookie still signs in, and that cookie is limited on its own; `prks admin unlock-login` clears the account limit; two IPv6 addresses in one `/64` share an address key; a spoofed `X-Forwarded-For` from an untrusted peer does not change the address key; takeover after `ready` refused; secrets, the bootstrap token included, never in responses or logs (log-privacy test), and the token file is created `0600` outside the data root; removing a membership refuses the next read and write of that user's existing session for the Library, and demoting an editor to reader refuses the next write; `--testing` E2E unaffected |
| **L-J-1** Jobs runtime | PG-2 | `jobs` claim/run/retry/lease, NOTIFY wakeup, embedded worker thread, `prks worker`, prune job, diagnostics counters | Kill-after-claim → lease expiry → exactly one reclaim → idempotent completion; stale-worker completion and heartbeat update nothing; repeated-crash job ends `failed` and frees its singleton key; dedupe tests, including a handled failure while a queued twin exists (A cancelled, B runs once); a handled failure on the last attempt ends `failed`, never `queued`, and frees its dedupe key; two concurrent workers with several queued jobs of one singleton key never run two and never surface `23505`; jobs of a Library in maintenance or of an older epoch are not claimed; worker graceful stop |
| **L-W-1** Works core | PG-3, L-ID-1 | `works`, `manifestations`, `assets` (catalogue only), `work_notes`, projection module producing today's JSON; create/get/browse/recent/open/metadata/source/delete commands; facade methods; `CREATE_WORK`, `DELETE_WORK`, `MARK_WORK_OPENED`, `SET_WORK_METADATA_FIELD`, `SET_WORK_SOURCE`, note handlers | Parametrized Work and sync-family tests pass on PostgreSQL; JSON parity tests against SQLite fixtures; the #60 §4.1 negative tests as schema tests; deferred pointer FKs proven |
| **L-W-2** Bulk, BibTeX, wiki links, Work merge scaffolding | L-W-1 | Bulk actions (SERIALIZABLE), BibTeX via citation record, wiki-link resolution | Byte-identical BibTeX tests; bulk atomicity tests |
| **L-P-1** People | L-W-1 | Persons, aliases as rows, roles (no `rowid`), person groups, publishers; their sync families | Parametrized tests; Greek/Cyrillic identity for publishers and groups; a publisher name and another publisher's alias never share a key under concurrency (`publisher_keys`); role order stable without `rowid` |
| **L-O-1** Organization | L-W-1 | Tags (aliases, merge), folders, folder tags, playlists, Saved Views, settings split; their sync families | Parametrized tests; tag merge concurrency test; a tag name and another tag's alias never share a key under concurrency (`tag_keys`); per-parent folder identity; one-playlist-per-Work |
| **L-R-1** Research | L-W-1, L-J-1 | Concepts with `concept_keys`, Positions, Arguments, sources/targets, notes integration, derived mentions via `research.reindex_work`, graph projection | Parametrized tests; Research-ACK fence still observed by E2E; no Python full scan for concept resolution |
| **L-A-1** Objects and annotations | L-W-1, L-J-1, L-P-1 | #311 Phase B on PostgreSQL: all managed-file paths through `StorageBackend`; immutable objects; cleanup claims, orphan sweep, and the `restore_run_objects` table counted by the reference check (§11.2); annotations, materialization, PDF replace, save-confirm from durable state; person portraits | Object lifecycle tests (§13.3), including a stalled ingest and a stalled rewrite whose new key the sweep settles failing cleanly at the adoption check; write-amplification measurement recorded in the PR; no `os.remove` outside the storage backend; parametrized annotation and sync tests pass |
| **L-PR-1** Processing | L-A-1, L-P-1, L-O-1 | `processing_files`, `processing_file_roles` (persons) and `processing_file_tags` (tags), whose composite foreign keys need People and Organization; inbox scan as a job that never deletes a row held by restore, import through the objects lane; imports hold the inbox lock (§11.3) across their commit and source-file removal, with the `imported` row state | Parametrized processing tests; no absolute path stored; an import's lock is released on every success and failure path; a crash between an import's commit and its source removal is finished by the next scan and the file is never offered for import again; when the file at that path was replaced before the scan, the scan deletes only the `imported` row and lists the new file as pending |
| **L-S-1** Search | L-W-1, L-J-1, L-P-1, L-O-1, L-A-1 | `derived.work_search` maintained by commands, `asset_text` via `text.extract`, trigram filters, parity ordering, Saved View execution, `rebuild-derived`, the folding check (§8.6); `backend/AGENTS.md` extracted-text rule amended for the `derived` schema (§16, decision 5) | §8.7 parity tests; search parametrized tests pass with the documented Unicode improvements replacing ASCII pins; an infix token ("state" in "understatement") still finds a Work through its abstract and through its notes, and a diacritic infix ("šnja" in notes containing "Trešnja") finds it too, the §8.7 substring vectors; a changed folding, at start or seen on a worker's idle tick after the database restarts on a new release, enqueues the folding rebuild once even when two processes start together, and still enqueues it (a new job row, not a dedupe no-op) when only a search-only rebuild is queued, a failed or cancelled folding rebuild leaves `libraries.search_folding` unchanged and is enqueued again on the next start, and `--only text` recomputes `doc` without extracting again |
| **L-S-2** Ranking | L-S-1 | Single weighted score | Before/after results on a synthetic multilingual corpus approved in review |
| **L-B-1** Backup and restore format 2 | L-W-1, L-P-1, L-O-1, L-R-1, L-A-1, L-PR-1, L-S-1 | §11.4 | Round-trip test over a fixture covering every canonical table and object kind; text `COPY` with the interchange contract (no binary `COPY`); restore into the same and into a new instance with real sync operations, the archive Library mapped to the target Library and all library/user/device references resolving; a restore over a different Library whose IDs still exist is refused; R1 reuse of an existing object is lock-coupled to cleanup settle and re-verified in R3, so R4 never commits a locator to a missing object; new devices are inserted only by R4; a restore that outlives its lease aborts and never replaces writes accepted after recovery; export, staging and the R4 delete/insert use only the content-table allowlist, and a schema test classifies every library-scoped table; staging builds `restore_<id>` with `LIKE` from the live tables, never with the migration runner, checks foreign keys with redirected generated anti-joins, and refuses an archive of another schema fingerprint (§13.3 staging isolation); the round trip covers a table with a generated `*_key` column, which is not exported and is recomputed on restore; a backup with the inbox under the data root restores its pending files into a fresh instance, a file already at the same inbox path is never overwritten (the archived copy lands in `restored-<run>/` and its restored row, roles and tags follow it), a scan right after R4 deletes no restored pending row (a row whose file the user had deleted before the backup is held), and a restored `imported` row whose path holds a different file in the target's inbox loses only its row, never the file; a backup taken after a collision restore, with its `restored-<run>/` rows still pending, restores cleanly; a held `outside-root` row whose path holds a different file stays held and its roles and tags are not applied, an import that commits during a backup leaves the archive with either the pending row and its file or neither, an import handler paused between its commit and its file removal at R2 finishes before R3 publishes, and with an `outside-root` inbox the restored rows are held: a scan that finds no file keeps them and the Processing view offers to dismiss them, while a scan that finds the file with the recorded hash clears the hold; staged objects of a live run survive an orphan sweep (L-B-1 only writes `restore_run_objects` rows; the table and its term in the reference check belong to L-A-1); verification refuses tampered archives, and archive members and inbox paths stay contained (§13.3 archive containment); a restore that does not commit leaves the inbox's files as they were (§13.3 inbox undo); fault injection at R0–R5, including a kill immediately after the R4 commit that still rebuilds search after restart; the exclusion interleavings of §13.3; restoring an older snapshot while a long job runs never lets the job write to the replacement state, and an API mutation or operation envelope carrying the pre-restore epoch is refused with 409 `LIBRARY_EPOCH_CHANGED`; backup concurrent with writes and cleanup; format 1 refused with a clear message; an R3 lock timeout and an R4 rollback each leave the old data intact, clear the flag through abort finalization, and accept a new ordinary command at once without waiting for lease expiry, while an abort whose inbox undo fails keeps the flag set for recovery; a disk-full error (`53100`) injected in R1 and in R4 leaves the live Library unchanged, reports the restore as failed with that error, and a later cleanup pass drops the staging schema |
| **L-D-1** Compose and health | PG-1, L-ID-1, L-J-1 | Dockerfile with psycopg, Compose of §12.3, `/healthz`, `/readyz`, graceful stop, `.env.example`, README deployment section (including PostgreSQL-volume capacity monitoring and headroom, §12.3), #612 configuration entries | Clean `docker compose up` smoke test in CI reaches `/readyz` and bootstrap; the database volume is mounted at `/var/lib/postgresql`; `web` and `worker` each list the `prks_database_url` secret and read it at start; data survives `down`/`up` and container recreation with the volume kept; wrong root/database pairing refused at start |
| **L-F-1** Browser store scoping, auth hold and restore epoch | L-ID-1 | §10.3–10.4 on the client | E2E: two accounts in one browser never see each other's drafts or queue; session expiry mid-queue holds and later delivers; pre-0.8 queue offered review-only, never auto-deleted; after the Library epoch changes, a pending edit is not applied, its text is offered in review-only recovery, and older-epoch drafts are never auto-restored; every Library-scoped mutation carries `PRKS-Library-Epoch` and login and bootstrap work without it; the client's epoch handling is proven against a 409 `LIBRARY_EPOCH_CHANGED` and a changed epoch from `/api/session` without a real restore (a real restore belongs to L-B-1, and the combined scenario to PG-C1); #465/#466 suites green |

**Stage 3. Cutover (sequential).**

| Slice | Depends on | Content | Acceptance criteria |
| --- | --- | --- | --- |
| **PG-C1** Full PostgreSQL E2E leg | all Stage 2 lanes except L-S-2 | Run the full E2E gate with the facade as the backend in CI (not yet default); the combined restore and browser scenario | Full E2E Gate green on PostgreSQL; facade method registry reports 100 % ported; E2E: a real restore to an older snapshot while the browser holds a pending edit, the edit is not applied and its text is offered in review-only recovery |
| **PG-C2** Switch and delete | PG-C1 | PostgreSQL becomes the only backend; §13.4 retirement in one PR (or a short stack); docs and `AGENTS.md` updated; `--testing` uses the harness database | No `import sqlite3` anywhere in `backend/`; full E2E, Test Gate and static analysis green; README quick start works from a clean machine; #614 release gates checked by the maintainer |
| **PG-C3** Route extraction follow-up | PG-C2 | Move route families from facade to direct domain calls under #67, one family per PR | Facade shrinks; no behavior change |

**Pre-1.0 items started after 0.8**: HTTP server decision (§4.5), schema
freeze into `0001_baseline_v1.sql` and the forward-migration policy, API
versioning policy (#45), backup format versioning policy, offline
reassessment (§10.5).

### 15.4 Parallelism at a glance

```text
Stage 1  PG-1 → PG-2 → PG-3
Stage 2  after PG-2 ............................... L-ID-1, L-J-1
         after PG-3 + L-ID-1 ...................... L-W-1
         after L-ID-1 ............................. L-F-1
         after PG-1 + L-ID-1 + L-J-1 .............. L-D-1
         after L-W-1 .............................. L-W-2, L-P-1, L-O-1
         after L-W-1 + L-J-1 ...................... L-R-1
         after L-P-1 + L-J-1 ...................... L-A-1
         after L-P-1 + L-O-1 + L-A-1 .............. L-PR-1, L-S-1
         after L-S-1 .............................. L-S-2
         after L-P-1, L-O-1, L-R-1, L-PR-1, L-S-1 . L-B-1
Stage 3  PG-C1 (after every Stage 2 lane except L-S-2) → PG-C2 → PG-C3
```

Up to six lanes can run at once after L-W-1 (Works bulk and links, People,
Organization, Research, Objects, Browser scoping), each in its own files.
Search starts once People, Organization and Objects have merged, because
its search documents, Saved View execution and `text.extract` read their
tables and the storage backend. Backup comes last among the domain lanes,
because its round trip covers every canonical table (Processing included)
and its post-restore rebuild runs the Search lane's `derived.rebuild`.

---

## 16. Maintainer decisions

Recorded from the maintainer's approval of #619 at `7bb9545` on 2026-10-12.
Each decision is the recommendation this section proposed; none was changed.

1. **Clean start (PG2).** No importer for pre-0.8 SQLite libraries and no
   format 1 archive import before 1.0.
2. **Immutable objects (PG14).** Approved. L-A-1 measures write amplification
   (large PDFs, repeated annotation saves) and records it in its PR; a
   debounced per-Asset materialization job is the fallback if it is too high.
3. **Text identity (PG9).** NFC, whitespace collapse and full case folding.
   No NFKC and no diacritic folding for identity.
4. **Search folding.** `unaccent` for search, verified against the Greek and
   Cyrillic parity vectors (§8.7), with a PRKS rules file only if the
   defaults are wrong. Serbian Cyrillic↔Latin transliteration is deferred
   beyond 0.8.
5. **Derived data (PG11).** Rebuildable indexes and extracted text live in the
   separate `derived` schema of the same database, never backed up and always
   rebuildable. The `backend/AGENTS.md` rule "do not write extracted PDF text
   into the canonical main DB" becomes "only into the `derived` schema"; L-S-1
   amends it.
6. **Authentication.** Password-based owner authentication with server-side
   sessions. No passwordless mode for normal deployments; the loopback-owner
   mode exists only for `--testing`.
7. **Library selection (PG17).** From the session in 0.8, carried in an
   explicit request context; #45 decides any versioned path shape.
8. **Entity IDs (PG7).** Prefixed text IDs are kept.
9. **HTTP server (PG20).** A production server is evaluated before 1.0 as a
   separate item, not part of the initial PostgreSQL migration.
10. **Implementation planning.** Approved. After this document is merged,
    the slices of §15.3 become focused implementation issues under v0.8.0,
    linked from #614 and the relevant roadmap issues, with native
    dependencies. PG-1 is the first coding task, and each slice starts only
    on the maintainer's explicit authorization.

The compatibility boundary stays **v1.0.0**: before it, backend APIs,
schemas and implementations may be replaced without prerelease
compatibility; from it, supported upgrades preserve released user data.
