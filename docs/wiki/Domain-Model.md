# Domain Model

PRKS models a research library rather than only a directory of files. The database contains both bibliographic/library entities and structured research entities.

## Core library entities

### Work

A Work is the primary research item. It can represent a managed PDF, a video/online source, or another supported source type.

A Work can carry:

- bibliographic metadata;
- source identity/provenance;
- reading/viewing progress;
- a Folder;
- Tags;
- linked People through Roles/credits;
- Playlist membership;
- Research Notes and private/reminder notes;
- PDF annotations;
- relationships to structured research entities.

### Folder

Folders organize Works hierarchically. Folder metadata and Work→Folder assignment are separate operations, which matters for synchronization and conflict handling.

### Tag

Tags are reusable vocabulary. Tags may be attached to Works and Folders. The vocabulary itself—create, delete, merge—is distinct from attachment operations.

### Person and Role

People are first-class records. Work↔Person relationships carry Roles/credit rather than treating authorship and other contributions as one unstructured string.

### Person Group

Person Groups provide a separate hierarchy/grouping mechanism for People.

### Playlist

Playlists hold an ordered sequence of Works. They are useful for video/research sequences and are not equivalent to Folders.

## Structured research entities

### Concept

A Concept represents a research idea/category that can participate in the structured research network.

### Position

A Position represents a claim or position that can be connected to Concepts and other research context.

### Argument / Stance

Arguments/Stances represent structured support, opposition, or related argumentative relationships. They can have sources and targets.

These records power the Research Graph and related detail/index views.

## Source identity

Work source fields are not independent metadata. For video Works, source kind/provider/provider ID and URL semantics form an identity aggregate. The canonical reasoning and transition model are documented in [docs/work-source-identity.md](https://github.com/Fooftilly/PRKS/blob/master/docs/work-source-identity.md).

## Planned: Work identity, versions and files

Today one Work row represents the intellectual work, one citeable publication, and at most one file. The proposed separation into Work → Version (manifestation) → File or Source (asset) — covering editions, translations, revisions, duplicate detection and citation targets — is a design under review, not current behavior. See [docs/work-identity-model.md](https://github.com/Fooftilly/PRKS/blob/master/docs/work-identity-model.md).

## IDs and offline creation

Durable local-first creation requires IDs that can safely exist before a server round-trip. Domain-specific synchronization modules and `backend/entity_ids.py` handle the relevant boundaries.

## Database authority

The exact current schema is defined by `backend/db_schema.sql` plus ordered migrations in `backend/db_migrations.py`. Do not treat this page as a column-level schema reference; use those files when implementing migrations or queries.
