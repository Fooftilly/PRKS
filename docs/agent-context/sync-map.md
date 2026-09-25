# Agent context map: local-first and sync

Use this map before loading the large local-first specifications. Start with the smallest relevant section and expand only when the task crosses additional synchronization domains.

Canonical references:
- `docs/local-first-sync.md`: synchronization semantics and domain invariants.
- `docs/agent-rules/offline-pwa.md`: implementation-facing offline/PWA rules.
- `docs/local-first-rollout-status.md`: current rollout status and remaining server-bound behavior.

## Work metadata
- `docs/local-first-sync.md`: "Work metadata fields", "Metadata: overlay, atomic save and per-field conflicts", and the field-specific sections that follow.
- `docs/agent-rules/offline-pwa.md`: "Field-scoped Work metadata", "High fan-out fields", "Group-changing fields", and "Fields whose stored value is not what is shown".
- Code: `backend/work_metadata_sync.py`, `frontend/js/sync-runtime.js`, related work-metadata tests.

## Work source identity
- `docs/local-first-sync.md`: source identity/aggregate sections and `author_text` rules.
- `docs/agent-rules/offline-pwa.md`: "Source identity is an AGGREGATE".
- Code: `backend/work_source_sync.py`, related frontend/selftests/E2E.

## Work opens and tags
- `docs/local-first-sync.md`: "Work open events", "Local coalescing and overlay (Work Tags)".
- `docs/agent-rules/offline-pwa.md`: "Local-first Work opens", "Local-first Work Tags".
- Code: `backend/work_open_sync.py`, `backend/work_tag_sync.py`.

## People and groups
- `docs/local-first-sync.md`: "Offline Person editing", "Offline Person creation", "Person Groups".
- Code: `backend/person_sync.py`, `backend/person_metadata_sync.py`, `backend/person_group_sync.py`.

## Folders and folder tags
- `docs/local-first-sync.md`: "Folders (3F)".
- Code: `backend/folder_sync.py`, `backend/folder_tag_sync.py`.

## Playlists
- `docs/local-first-sync.md`: "Playlists (3G)".
- Code: `backend/playlist_sync.py`.

## Research entities
- Concepts: "Concepts (3H)" and `backend/concept_sync.py`.
- Positions: "Positions (3I)" and `backend/position_sync.py`.
- Arguments/Stances: "Arguments and Stances (3J)" and `backend/argument_sync.py`.
- Research graph offline behavior: `backend/research_graph.py` plus the research-graph offline tests.

## PDF annotations
- `docs/local-first-sync.md`: "PDF annotations (V2 local-first boundary)".
- Code: `backend/pdf_annotation_sync.py`, related frontend/selftests/E2E.

## New synchronization families
Before adding a family, read "Adding a family: the four shapes and what each must declare" in `docs/local-first-sync.md`, then inspect the nearest existing family with the same shape. Do not load the entire sync specification unless the change genuinely spans multiple families or core protocol behavior.
