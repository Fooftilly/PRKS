# PRKS Wiki

PRKS (Personal Research Knowledge System) is a local research library for organizing PDFs, notes, video references, bibliographic metadata, people, folders, tags, playlists, and structured research concepts. It runs as a self-hosted web application with a Python/SQLite backend and a vanilla-JavaScript frontend.

The wiki is the orientation layer for users and contributors. It explains how the major parts fit together without replacing the repository's detailed implementation documents.

## Start here

- [Getting Started](Getting-Started.md) — install, run, Docker, testing mode, and storage basics.
- [User Guide](User-Guide.md) — the main library and research workflows.
- [Workspace Tabs and Split View](Workspace-Tabs-and-Split-View.md) — tabs, panes, persistence, and navigation.
- [PDFs and Annotations](PDFs-and-Annotations.md) — PDF viewing, annotation ownership, search, and materialization.
- [Offline and Sync](Offline-and-Sync.md) — local-first behavior, durable operations, and reconciliation.
- [Storage, Backup, and Restore](Storage-Backup-and-Restore.md) — canonical data, derived data, backups, and recovery.
- [Research Network](Research-Network.md) — Concepts, Positions, Arguments/Stances, and graph views.

## For contributors

- [Architecture](Architecture.md) — system boundaries and request/data flow.
- [Domain Model](Domain-Model.md) — the main entities and relationships.
- [Testing](Testing.md) — unit/API/structural tests, browser E2E, and UX tours.
- [Development Workflow](Development-Workflow.md) — where changes belong and how to keep guidance current.
- [Dependencies and Vendoring](Dependencies-and-Vendoring.md) — dependency policy and the dependency gate.
- [Security and Operations](Security-and-Operations.md) — binding, authentication boundary, logging, and security-sensitive behavior.
- [Troubleshooting](Troubleshooting.md) — common startup, data, PDF, offline, and test problems.
- [Glossary](Glossary.md) — PRKS-specific terms.

## Authoritative references

The wiki intentionally stays higher-level than the following documents:

- [README.md](https://github.com/Fooftilly/PRKS/blob/master/README.md)
- [AGENTS.md](https://github.com/Fooftilly/PRKS/blob/master/AGENTS.md)
- [DESIGN.md](https://github.com/Fooftilly/PRKS/blob/master/DESIGN.md)
- [Local-first sync design](https://github.com/Fooftilly/PRKS/blob/master/docs/local-first-sync.md)
- [Local-first rollout status](https://github.com/Fooftilly/PRKS/blob/master/docs/local-first-rollout-status.md)
- [E2E performance notes](https://github.com/Fooftilly/PRKS/blob/master/docs/e2e-performance.md)
- [Work source identity report](https://github.com/Fooftilly/PRKS/blob/master/docs/work-source-identity.md)
- [SECURITY.md](https://github.com/Fooftilly/PRKS/blob/master/SECURITY.md)

If the wiki and one of those documents disagree about a fast-moving implementation detail, use the authoritative document and update the wiki.
