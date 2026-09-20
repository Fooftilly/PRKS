# PRKS wiki source

This directory is the canonical source for the PRKS GitHub Wiki.

Edit these Markdown files through normal repository pull requests. Do not treat the rendered GitHub Wiki as the source of truth: changes made only in the Wiki UI can be overwritten by the publish workflow.

## Documentation boundaries

The wiki explains stable product concepts, workflows, architecture, and contributor practices. Fast-moving implementation details remain in their authoritative repository documents:

- `README.md` — installation, configuration, and safety contracts (host/port, Docker publish, env vars, schema version, auth warning). Detailed current feature/user behavior lives in this wiki.
- `AGENTS.md` — implementation constraints and contributor/agent rules.
- `DESIGN.md` — detailed design language and UI/interaction rules.
- `docs/local-first-sync.md` — local-first protocol design and implementation notes.
- `docs/local-first-rollout-status.md` — current durable/offline coverage.
- `docs/e2e-performance.md` — E2E performance architecture and profiling notes.
- `docs/work-source-identity.md` — Work source/video identity design report.
- `SECURITY.md` — vulnerability reporting and security guidance.

When a wiki page summarizes one of those areas, link to the authoritative document instead of copying volatile tables or milestone status.

## Publishing

`.github/workflows/publish-wiki.yml` mirrors this directory to the GitHub Wiki after changes land on `master`. `README.md` itself is source-maintenance guidance and is not published as a Wiki page.

GitHub creates the backing `PRKS.wiki.git` repository only after the Wiki has been initialized once. If it does not exist yet, create an initial Home page in the repository's Wiki UI, then run the **Publish Wiki** workflow manually. After that, merges that touch `docs/wiki/**` publish automatically.


## Visual documentation

Use visuals when they explain a workflow or architecture faster than prose:

- repository demo screenshots must come from synthetic/public-domain test data;
- screenshots should be referenced from stable repository URLs so they render in both `docs/wiki/` and the published GitHub Wiki;
- prefer Mermaid for architecture/data-flow diagrams that benefit from version-controlled text diffs;
- keep diagrams small and conceptual rather than mirroring implementation line-by-line;
- do not publish screenshots from a real personal research library.

The canonical promotional screenshot pipeline lives under `scripts/seed_demo_library.py`, `scripts/capture_demo_screenshots.py`, and `docs/screenshots/`.
