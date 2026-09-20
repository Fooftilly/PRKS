# Development Workflow

PRKS has repository-level guidance for both human and agent contributors. Read it before making implementation changes.

## Read the right authority

- \`README.md\` — run/config/user-facing current behavior.
- \`AGENTS.md\` — implementation constraints, testing policy, architecture rules, and contributor guidance.
- \`DESIGN.md\` — UI/UX design and interaction rules.
- \`docs/wiki/\` — orientation and stable cross-cutting documentation.
- \`docs/local-first-*.md\` — local-first design/current rollout.
- \`SECURITY.md\` — security reporting and security-specific rules.

The wiki should not silently replace any of those authorities.

## Make changes by domain boundary

PRKS has several places where superficially simple field updates are actually domain aggregates—Work source identity, synchronization families, PDF annotation generations, and workspace TabContexts are examples.

Before modifying an API/DB/UI field, trace:

1. the canonical backend reader/writer;
2. the frontend reader/writer;
3. offline/local-store behavior;
4. browse/index projections;
5. backup/migration implications;
6. tests that encode the invariant.

## Testing during development

Prefer unit/API/structural/Node tests while iterating. Run a relevant E2E feature/module after the vertical slice is complete when browser behavior matters. Use the full E2E gate as a final regression check rather than the inner development loop.

See [Testing](Testing.md) and \`AGENTS.md\` for the exact current policy.

## Documentation updates

When a code change modifies a user-visible command/configuration, update the README.

When it changes stable architecture/user concepts, update the relevant wiki source page.

When it changes local-first coverage, update \`docs/local-first-rollout-status.md\` and any detailed sync design notes required by the milestone.

When contributor rules or architectural invariants change, update \`AGENTS.md\`.

When visual/interaction contracts change, update \`DESIGN.md\`.

## Wiki changes

Edit \`docs/wiki/*.md\` in the normal repository branch/PR. After merge, the Wiki publishing workflow mirrors the source pages to the GitHub Wiki.

Do not make long-lived edits only in the GitHub Wiki UI: those changes bypass code review and can drift from the repository source.

## Generated artifacts and private data

Documentation screenshots, UX artifacts, and demo data must use testing/synthetic/public-domain content. Do not publish screenshots from a real personal research library.
