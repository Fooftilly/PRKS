# Agent context map: local-first and sync

Use this map before loading the detailed current offline/synchronization specifications. Load the smallest relevant contract set; expand only when the task crosses additional synchronization domains.

Canonical references:
- `docs/agent-rules/offline-pwa.md`: small router for implementation-facing offline/PWA rules.
- `docs/agent-rules/offline-foundations.md`: shared cache/durability/connectivity/service-worker foundations; read for every offline/sync task.
- `docs/local-first-sync.md`: synchronization semantics and domain invariants; read only the relevant sections unless the change genuinely spans core protocol behavior.
- `docs/local-first-rollout-status.md`: current rollout status and remaining server-bound behavior.

## Work metadata
Read:
- `offline-foundations.md`;
- `offline-work-sync.md`: "Field-scoped Work metadata", "High fan-out fields", "Group-changing fields", "Fields whose stored value is not what is shown", and "Typed fields and derived resources";
- `offline-browse-protocol.md`: "Offline browse catalogs" and its dependency matrix for Work-field projection fan-out;
- `docs/local-first-sync.md`: "Work metadata fields", "Metadata: overlay, atomic save and per-field conflicts", and the field-specific sections that follow.

Code: `backend/work_metadata_sync.py`, `frontend/js/sync-runtime.js`, related work-metadata tests.

## Work source identity
Read:
- `offline-foundations.md`;
- `offline-work-sync.md`: "Source identity is an AGGREGATE";
- `docs/local-first-sync.md`: source identity/aggregate sections and `author_text` rules.

Code: `backend/work_source_sync.py`, related frontend/selftests/E2E.

## Work opens
Read:
- `offline-foundations.md`;
- `offline-work-sync.md`: "Local-first Work opens";
- `offline-browse-protocol.md`: foreground-open semantics, Recent eligibility, and acknowledgement reconciliation;
- `docs/local-first-sync.md`: "Work open events".

Code: `backend/work_open_sync.py`.

## Work tags
Read:
- `offline-foundations.md`;
- `offline-work-sync.md`: "Local-first Work Tags";
- `offline-folder-tag-coherence.md` when a tag change affects Folder/Work cached projections, identity-preserving relationship invalidation, or delete/merge reconciliation;
- `docs/local-first-sync.md`: "Local coalescing and overlay (Work Tags)".

Code: `backend/work_tag_sync.py`.

## Work lifecycle
For `CREATE_WORK` / `DELETE_WORK` and `backend/work_lifecycle_sync.py`, read:
- `offline-foundations.md`;
- `offline-browse-protocol.md` for browse/Recent/Recently Added projection effects;
- `offline-folder-tag-coherence.md` for Folder/Home membership/count coherence;
- `offline-entity-coherence.md` for People/Person Group/Concept/Argument/Playlist coherence affected by Work creation/deletion;
- `offline-entity-surfaces.md` for Research Graph Work-delete invalidation and the deliberate rule that plain Work creation does not invalidate Graph;
- the Work lifecycle sections in `docs/local-first-sync.md`.

## Work-Person roles
For `ADD_WORK_PERSON_ROLE`, `REMOVE_WORK_PERSON_ROLE`, `SET_WORK_PERSON_ROLE_CREDIT`, and `backend/work_role_sync.py`, read:
- `offline-foundations.md`;
- `offline-entity-coherence.md` for People, Person Group, and Argument dependencies;
- `offline-entity-surfaces.md` for Research Graph core/People projection invalidation rules affected by Author-role changes;
- `offline-folder-tag-coherence.md` for Author/Editor effects on Folder Work-card credits;
- `offline-browse-protocol.md` for Author/Editor effects on browse/Recent projections;
- the Work-role sections in `docs/local-first-sync.md`.

## Tag vocabulary
For `CREATE_TAG`, `DELETE_TAG`, `MERGE_TAG`, and `backend/tag_sync.py`, read:
- `offline-foundations.md`;
- `offline-work-sync.md` for tag identity/lifecycle and durable-operation rules;
- `offline-folder-tag-coherence.md` for Work/Folder tag invalidation and delete/merge reconciliation;
- `offline-browse-protocol.md` for shared durable-operation handler/coordinator semantics;
- the Tag vocabulary sections in `docs/local-first-sync.md`.

## People and groups
Read:
- `offline-foundations.md`;
- `offline-entity-surfaces.md`: People route/edit/create/offline-media semantics;
- `offline-entity-coherence.md`: People and Person Group coherence domains;
- `docs/local-first-sync.md`: "Offline Person editing", "Offline Person creation", "Person Groups".

Code: `backend/person_sync.py`, `backend/person_metadata_sync.py`, `backend/person_group_sync.py`.

## Folders and folder tags
Read:
- `offline-foundations.md`;
- `offline-folder-tag-coherence.md`;
- `docs/local-first-sync.md`: "Folders (3F)".

Code: `backend/folder_sync.py`, `backend/folder_tag_sync.py`.

## Playlists
Read:
- `offline-foundations.md`;
- `offline-entity-coherence.md`;
- `docs/local-first-sync.md`: "Playlists (3G)".

Code: `backend/playlist_sync.py`.

## Research entities
Read `offline-foundations.md` + `offline-entity-surfaces.md` + `offline-entity-coherence.md`, then the relevant `docs/local-first-sync.md` section:
- Concepts: "Concepts (3H)" and `backend/concept_sync.py`.
- Positions: "Positions (3I)" and `backend/position_sync.py`.
- Arguments/Stances: "Arguments and Stances (3J)" and `backend/argument_sync.py`.
- Research graph offline behavior: `backend/research_graph.py` plus the research-graph offline tests.

## Work notes
Read:
- `offline-foundations.md`;
- `offline-entity-surfaces.md`: Work notes;
- `offline-entity-coherence.md` when Research Notes can stale Concept/Argument/Graph projections;
- the Work-note sections of `docs/local-first-sync.md`.

## Research Graph
Read:
- `offline-foundations.md`;
- `offline-entity-surfaces.md`: Research Graph;
- the relevant Research Graph sections/tests when projection semantics change.

## PDF annotations
Read:
- `offline-foundations.md`;
- `offline-entity-coherence.md`;
- `docs/local-first-sync.md`: "PDF annotations (V2 local-first boundary)".

Code: `backend/pdf_annotation_sync.py`, related frontend/selftests/E2E.

## Browse / Recent projections
Read:
- `offline-foundations.md`;
- `offline-browse-protocol.md`: "Offline browse catalogs";
- relevant browse/recent sections in `docs/local-first-sync.md` when semantic synchronization behavior is involved.

## Core durable-operation protocol
For `sync_protocol.py`, generic durable-operation coordinator/acknowledgement/ledger/conflict machinery, or changes that affect multiple operation families:
- always read `offline-foundations.md` and `offline-browse-protocol.md`;
- add every family leaf whose operations can be affected;
- expand `docs/local-first-sync.md` only to the shared protocol sections and affected families.

A truly cross-family protocol change may require all offline leaf files. That is exceptional; single-family work should stay scoped.

## New synchronization families
Read:
- `offline-foundations.md`;
- `offline-browse-protocol.md`;
- the nearest existing family leaf;
- "Adding a family: the four shapes and what each must declare" in `docs/local-first-sync.md`.

Do not load every offline specification merely because the change uses the sync infrastructure.
