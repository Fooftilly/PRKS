# Research Network

PRKS includes structured research entities in addition to ordinary library metadata.

## Concepts

Concepts represent research ideas/categories and can participate in hierarchical/related structures. Concept list/detail pages can be used alongside Works in the workspace.

## Positions

Positions represent claims or positions that a researcher wants to track explicitly rather than burying them in free-form notes.

## Arguments and Stances

Arguments/Stances connect sources and targets in the research model and support structured reasoning relationships.

## Research Graph

`#/graph` is a read-only map of existing research relationships. It does not store edges of its own.

The graph shows:

- Concept hierarchy (`concept_parents`)
- Positions and the Arguments/Stances that support, oppose, qualify, or hold them
- Argument → Argument responses (the same directed target relation; no extra reverse edge)
- Argument source Works (where an Argument/Stance was made or taken)
- explicit research-note Concept and Argument references (`[[concept:…]]`, `[[argument:…]]`)
- optional Author links for Works already in the graph (`?people=1`)

The Graph is read-only. Editing relationships is done on their normal PRKS records.

A Work→Concept or Work→Argument edge means that Work's research notes contain explicit semantic markup. It does not mean the Work is objectively about that Concept, and it is not the same as an Argument source Work.

Graph node IDs are namespaced (`concept:C-…`, `position:P-…`, `person:P-…`) because raw PRKS IDs are not unique across record types. The graph is rebuilt on request from `prks_data.db` plus the derived research-reference index. There is no graph database and no schema migration for rendering.

Graph/offline behavior has dedicated caching and E2E coverage because graph state must remain scoped correctly when several pages are mounted. See [Offline and Sync](Offline-and-Sync.md) for cache variants.

## Linking library research to structured research

Works and notes provide evidence/context; Concepts, Positions, and Arguments provide structured semantics. PRKS is designed so the researcher can move between these views rather than maintain two unrelated databases.

## Local-first behavior

The structured research entities are among the domains covered by durable local-first operations. The exact operation families and current offline coverage are tracked in [docs/local-first-rollout-status.md](https://github.com/Fooftilly/PRKS/blob/master/docs/local-first-rollout-status.md).

## Implementation areas

Relevant code is primarily in:

- `backend/concept_sync.py`, `position_sync.py`, `argument_sync.py`;
- `backend/research_graph.py`, `research_network.py`, `research_index.py`;
- `frontend/js/concept-state.js`, `position-state.js`, `argument-state.js`;
- `frontend/js/components/concepts.js`, `positions.js`, `arguments.js`, `research-graph.js`.

Use those modules plus [DESIGN.md](https://github.com/Fooftilly/PRKS/blob/master/DESIGN.md) when changing behavior; this page is an orientation map.
