# Research Network

PRKS includes structured research entities in addition to ordinary library metadata.

## Concepts

Concepts represent research ideas/categories and can participate in hierarchical/related structures. Concept list/detail pages can be used alongside Works in the workspace.

## Positions

Positions represent claims or positions that a researcher wants to track explicitly rather than burying them in free-form notes.

## Arguments and Stances

Arguments/Stances connect sources and targets in the research model and support structured reasoning relationships.

## Graph

The Research Graph visualizes parts of this network and can be opened in the workspace/split view. Its rendered state is a projection of canonical research entities and relationships, not an independent source of truth.

Graph/offline behavior has dedicated caching and E2E coverage because graph state must remain scoped correctly when several pages are mounted.

## Linking library research to structured research

Works and notes provide evidence/context; Concepts, Positions, and Arguments provide structured semantics. PRKS is designed so the researcher can move between these views rather than maintain two unrelated databases.

## Local-first behavior

The structured research entities are among the domains covered by durable local-first operations. The exact operation families and current offline coverage are tracked in [docs/local-first-rollout-status.md](https://github.com/Fooftilly/PRKS/blob/master/docs/local-first-rollout-status.md).

## Implementation areas

Relevant code is primarily in:

- \`backend/concept_sync.py\`, \`position_sync.py\`, \`argument_sync.py\`;
- \`backend/research_graph.py\`, \`research_network.py\`, \`research_index.py\`;
- \`frontend/js/concept-state.js\`, \`position-state.js\`, \`argument-state.js\`;
- \`frontend/js/components/concepts.js\`, \`positions.js\`, \`arguments.js\`, \`research-graph.js\`.

Use those modules plus [DESIGN.md](https://github.com/Fooftilly/PRKS/blob/master/DESIGN.md) when changing behavior; this page is an orientation map.
