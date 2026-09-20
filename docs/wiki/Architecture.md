# Architecture

PRKS is a local-first research application with a Python backend, SQLite persistence, managed files on disk, and a vanilla-JavaScript single-page frontend.

## Runtime shape

At a high level:

\`\`\`text
Browser / PWA
  |
  | HTTP / API
  v
PRKS threaded HTTP server
  |
  +--> LibraryAccessGate / canonical mutation boundaries
  |
  +--> SQLite database
  |
  +--> managed files (PDFs, person files)
  |
  +--> derived indexes / caches
\`\`\`

The normal process entry point is \`prks_app.py\`. Backend modules under \`backend/\` own storage, migrations, API/server behavior, synchronization families, PDF processing, backup/restore, indexing, logging, and research-network operations.

The frontend under \`frontend/\` is a browser SPA composed from vanilla JavaScript modules and CSS. It deliberately does not use a framework runtime.

## Backend boundaries

Important backend areas include:

- \`backend/server.py\` — HTTP/API routing and request handling.
- \`backend/db_manager.py\` — database access and canonical persistence operations.
- \`backend/db_migrations.py\` and \`backend/db_schema.sql\` — schema evolution and baseline schema.
- \`backend/concurrency.py\` — storage-access coordination.
- \`backend/*_sync.py\` — durable operation families and canonical synchronization boundaries.
- \`backend/backup_restore.py\` — verified backup and restore.
- \`backend/text_index.py\` and \`backend/research_index.py\` — derived search/reference indexes.
- \`backend/pdf_*.py\` — PDF annotation, adoption, materialization, and related services.
- \`backend/research_*.py\` — research graph/network/index behavior.

SQLite connections are operation-scoped; PRKS does not rely on a general-purpose connection pool. Canonical writes are serialized through the application’s mutation boundary so thread-per-request HTTP does not become uncontrolled concurrent SQLite writing.

## Frontend boundaries

Important frontend areas include:

- \`frontend/js/app.js\` and \`navigation.js\` — app shell and route/navigation behavior.
- \`components/\` — route-level UI surfaces.
- \`tab-context.js\` and \`workspace-*.js\` — in-app tabs, split view, pane layout, and persistence.
- \`local-store.js\`, \`sync-runtime.js\`, and domain \`*-state.js\` modules — durable local-first intent and reconciliation.
- \`offline-store.js\` / \`offline-runtime.js\` — disposable offline read projections and availability behavior.
- \`request-coordinator.js\` — short-lived request coordination/cache for normal online traffic; this is not durable offline storage.
- \`pdf-*.js\` and \`components/works-pdf.js\` — PDF viewer lifecycle and annotation integration.

## Canonical data vs derived data

PRKS distinguishes data that must be preserved from data that can be rebuilt.

Canonical examples:

- the main SQLite library database;
- managed PDFs and managed person files;
- durable operation state required for local-first reconciliation.

Derived examples include thumbnail caches, PDF text-search indexes, and research-reference indexes. Derived data can be discarded and rebuilt without changing research intent.

That distinction is central to backup/restore and to offline synchronization.

## Local-first architecture

PRKS uses two different mechanisms that must not be conflated:

1. **Disposable read projections/caches** make selected pages useful when the server cannot be reached.
2. **Durable local operations** store user intent before PRKS reports the change as saved, then reconcile that intent with the canonical server state.

A short-lived HTTP request cache is a third mechanism and is not a substitute for either one.

The implementation details and current operation families are documented in:

- [docs/local-first-sync.md](https://github.com/Fooftilly/PRKS/blob/master/docs/local-first-sync.md)
- [docs/local-first-rollout-status.md](https://github.com/Fooftilly/PRKS/blob/master/docs/local-first-rollout-status.md)

## Workspace architecture

Each visible PRKS page runs inside a TabContext. Main and Secondary panes have separate route state, DOM roots, async generations, and live resources. This prevents split-view pages from accidentally sharing document-scoped state.

See [Workspace Tabs and Split View](Workspace-Tabs-and-Split-View.md).

## Design authority

For UI structure, interaction rules, responsive behavior, and visual-system constraints, use [DESIGN.md](https://github.com/Fooftilly/PRKS/blob/master/DESIGN.md). The wiki explains architecture; it does not replace that design specification.
