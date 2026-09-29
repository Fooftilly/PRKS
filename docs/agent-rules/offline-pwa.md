# Offline / PWA agent-rules router

This file routes agents to the **current implemented offline/synchronization contract** without requiring every offline task to load the former ~1,450-line monolith.

Roadmap #310 allows a future authenticated central-server architecture to reassess Web/Android offline depth and synchronization machinery, but implemented behavior remains authoritative until an approved migration lands.

## Loading rule

For any offline, local-first, synchronization, service-worker, conflict/revision, client-cache, or durable-operation change:

1. read `docs/agent-rules/offline-foundations.md`;
2. read the smallest relevant leaf file(s) below;
3. use `docs/agent-context/sync-map.md` to locate the matching semantic sections in `docs/local-first-sync.md`;
4. expand to additional leaves only when the change actually crosses those domains.

For shared protocol/coordinator changes whose behavior can affect every durable family, load all applicable leaves. Do not mechanically load every file for a single-domain change.

## Rule files

| Task/domain | Load |
| --- | --- |
| Any offline/sync/PWA work | `offline-foundations.md` |
| Research Graph, Work notes, and People/Concept/Position/Argument route + mutation surface semantics | `offline-entity-surfaces.md` |
| People/Person Group/Playlist/Concept/Position/Argument/PDF coherence-domain invalidation | `offline-entity-coherence.md` |
| Folders/Home, folder tags, Work-tag coherence/invalidation | `offline-folder-tag-coherence.md` |
| Progress/Types/Recent/Recently Added projections, shared operation-family protocol/handler semantics | `offline-browse-protocol.md` |
| Work metadata and Work opens | `offline-work-sync.md` + `offline-browse-protocol.md` |
| Work source identity and Work Tags | `offline-work-sync.md` |
| Work lifecycle (`CREATE_WORK` / `DELETE_WORK`) | `offline-browse-protocol.md` + `offline-folder-tag-coherence.md` + `offline-entity-coherence.md` + `offline-entity-surfaces.md` |
| Work-Person roles | `offline-entity-coherence.md` + `offline-entity-surfaces.md` + `offline-folder-tag-coherence.md` + `offline-browse-protocol.md` |
| Tag vocabulary (`CREATE_TAG` / `DELETE_TAG` / `MERGE_TAG`) | `offline-work-sync.md` + `offline-folder-tag-coherence.md` + `offline-browse-protocol.md` |

## Cross-domain cases

- `sync_protocol.py`, the durable-operation coordinator, acknowledgement/ledger semantics, or generic conflict/revision machinery: foundations + browse/protocol + every family leaf whose operations can be affected.
- `local-store.js` or generic offline reconciliation: foundations + browse/protocol, then affected family leaves.
- service-worker/cache eligibility/connectivity state: foundations first; add entity/browse leaves only for the projections touched.
- a new durable family: foundations + browse/protocol + the nearest existing family leaf, then the "Adding a family" section in `docs/local-first-sync.md`.

## Former section-name lookup

Existing code comments may still name sections from the former monolithic file. Resolve them as follows:

- "Offline coherence domains" → shared framework in `offline-foundations.md`, then `offline-entity-coherence.md` or `offline-folder-tag-coherence.md` for the affected domain.
- "Offline browse catalogs" → `offline-browse-protocol.md`.
- "Synchronized operation families" → `offline-browse-protocol.md`.
- Work metadata/source/open/tag milestone headings → `offline-work-sync.md`.

## Canonical status

These split files together replace the former single `offline-pwa.md` contract. Do not duplicate their rules back into `AGENTS.md`. Keep this router small; detailed implementation invariants belong in the leaf files.

Current rollout status remains in `docs/local-first-rollout-status.md`. Domain synchronization semantics remain in `docs/local-first-sync.md`.
