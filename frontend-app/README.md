# PRKS Vue application

Maintainer-only Vue 3 + TypeScript source for the incremental frontend migration ([#230](https://github.com/Fooftilly/PRKS/issues/230)). Node and npm are build tools. Running PRKS does not use them.

The Python process serves the committed production bundle:

- `frontend/vue/prks-vue.js` (Vue, plus component CSS inlined by `vite-plugin-css-injected-by-js`)
- `frontend/vue/BUILD-MANIFEST.json`

Legacy UI remains `frontend/js/` and `frontend/index.html`. The shell loads the bundle on a hidden `#prks-vue-root`. That app teleports Settings performance diagnostics into `#prks-settings-perf-root` and the workspace presentation into `#prks-workspace-tabs` and `#page-content`. It is not a router and not a second workspace state model.

The legacy PRKS router stays canonical. `src/route-surface/` is the owner-scoped bridge from a TabContext route instance into a Vue view. Route-local runtime cannot live on `window`. Generations are never compared across TabContexts. Global shell and navigation state belongs to the shell/router. Generic browser lifecycle and this PRKS route-instance lifecycle are different concerns. Early presentation is feature-scoped: each feature registers its own presenter, and a pending request paints only on the host that stored it.

`src/workspace/` is the typed canonical workspace model and its pure transforms. `npm run build` emits it as the classic script `frontend/js/workspace-model.js`, loaded before `workspace-tree.js`. It is not part of `frontend/vue/prks-vue.js`, and `src/main.ts` must not import it. `src/features/search/codec.ts` is the Search / Saved View query codec. The same build emits `frontend/js/search-query-codec.js` (`prksSearchQueryCodec`) for classic callers, loaded before `saved-views.js`. Vue imports `codec.ts` and does not import `codec-browser-entry.ts`. `workspace-tabs.js` owns the one live state object, the effect coordinator, and `prksWorkspaceSubscribe`. `src/workspace-shell/` renders that read-only projection (tab strip, Main frame, recursive Secondary tree) and dispatches intents. It does not import `src/workspace/`. Content hosts stay in `frontend/js/workspace-hosts.js`. Do not add Pinia or a second snapshot.

## Toolchain

CI and rebuilds use **Node `>=24.15.0 <25`** (`engines` in `package.json`; the Vue job in `.github/workflows/static-analysis.yml`). Do not regenerate `frontend/vue/` outside that range. `jsdom@30.1.1` does not accept Node 24.0–24.14.

## Commands

```bash
cd frontend-app
npm ci --ignore-scripts
npm run typecheck   # vue-tsc, strict
npm test            # Vitest
npm run build       # Vite production bundle + dependency manifest + service-worker revision
npm run build-storybook   # catalog only; gitignored storybook-static/
```

`npm run dev` serves only this package's build entry for maintainer inspection. It is not how PRKS starts. Use `python prks_app.py --testing`.

## Where later slices go

| Path | Role |
| --- | --- |
| `src/workspace/` | Canonical typed workspace state and pure transforms. Built to `frontend/js/workspace-model.js`. Not a Vue renderer and not a second state owner |
| `src/features/search/codec.ts` | Canonical Search / Saved View query codec. Built to `frontend/js/search-query-codec.js` for legacy callers. Vue imports the module |
| `src/workspace-shell/` | Vue presentation for the tab strip, pane frames, and recursive Secondary tree. Renders a detached projection and sends coordinator intents. Not a state owner |
| `src/workspace-dnd/` | #256 production Pragmatic DnD adapter + pure `WorkspaceDropIntent` resolver. Wired by `WorkspaceShell` (shell-commit reconcile). Classic `frontend/js/workspace-drag.js` is a geometry/API shim only |
| `src/route-surface/` | Typed Vue route-instance lifecycle. Owner-scoped mount, generation, cleanup, and feature-scoped host-local early presentation. Not a router |
| `src/features/performance-diagnostics/` | Settings performance diagnostics |
| `src/features/progress/` | Progress route, first route-surface consumer. Consumes the effective works-browse snapshot; not a Query cache |
| `src/api/` | Typed PRKS API client used by feature services |
| `src/query/` | Application QueryClient |
| `src/components/` | Shared primitives (`PrksButton`, `PrksLinkButton`, `PrksIconButton`, `PrksField`, `PrksInlineMessage`, `PrksState`, `PrksStatusText`, `PrksSectionHeader`, `PrksScopeLine`, `PrksRelSummary`, `PrksResearchRow`, `PrksResearchSectionHead`, `PrksDisclosureButton`) and their stories |
| `src/composables/` | Feature-local Vue logic, when a slice needs it |
| `frontend/js/` | Legacy runtime. Leave it in place until a slice replaces a specific responsibility |

`@tanstack/vue-query` is the server-state owner for performance diagnostics. `@vueuse/core` is pinned for selective generic helpers (Folder Library: `useDebounceFn` for search/repaint, `useEventListener` for tree/collection clicks). Do not add Vue Router, Pinia, or a second QueryClient. Do not use VueUse for PRKS route, workspace, durable, or preview ownership. Do not persist the query cache. Do not copy `prksParseRoute` into TypeScript. Future routes should extend the route-instance shape and reuse `src/route-surface/` instead of inventing another TabContext session.

## Components and Storybook

`DESIGN.md` is the visual and interaction contract. Reuse the primitives in `frontend-app/src/components/` before adding another button, section heading, or status line. They apply existing classes from `frontend/css/style.css`. Do not copy that stylesheet into a component, and do not add a second button or card family.

| Path | Role |
| --- | --- |
| `src/components/PrksButton.vue` | `.prks-btn` with variant, disabled, and busy (`disabled` plus `aria-busy`) |
| `src/components/PrksLinkButton.vue` | Native `<a class="prks-btn">`; required `href`; `secondary` plus `sm`/`md` only |
| `src/components/PrksIconButton.vue` | `.prks-icon-btn` with a required accessible name, disabled, and busy |
| `src/components/PrksField.vue` | `.prks-field` label + native control slot + optional help/error; slot `labelledBy`/`describedBy` (help then error) |
| `src/components/PrksInlineMessage.vue` | `.prks-inline-message`; `tone="error"` adds `--error`; `status` keeps an existing `role="status"` |
| `src/components/PrksState.vue` | `.prks-state` for `loading` and `error` (`role="status"`, optional retry slot) and `empty` (no live role) |
| `src/components/PrksStatusText.vue` | `.prks-settings-hint`, polite live region unless `live` is false |
| `src/components/PrksSectionHeader.vue` | Settings `h5.prks-settings-section__title` |
| `src/components/PrksDisclosureButton.vue` | Secondary button that owns `aria-expanded` / optional `aria-controls` |
| `src/components/PrksResearchRow.vue` | `.prks-list-row.prks-research-row` anchor with title/kind/meta |
| `src/components/PrksResearchSectionHead.vue` | Research-entity section title, count, optional Edit |
| `src/components/PrksScopeLine.vue` | `.prks-scope-line` collection counts |
| `src/components/PrksRelSummary.vue` | `.prks-rel-summary` identity strip (plain text; no href) |
| `src/components/*.stories.ts` | Real states for those primitives |
| `.storybook/` | Vue 3 + Vite Storybook config |

### When to use which primitive

| Need | Use | Do not use |
| --- | --- | --- |
| Labeled in-page action | `PrksButton` | `PrksLinkButton`, `PrksIconButton` |
| Navigation that looks like a button | `PrksLinkButton` (real `<a href>`) | `PrksButton` with a click that `prksNavigate`s; polymorphic button |
| Square icon chrome (`.prks-icon-btn`) | `PrksIconButton` with a required name | `PrksButton`; do not move folder-tree New folder, folder/group expand-collapse, or playlist rename/cancel here (those stay raw `.prks-btn`) |
| Ordinary labeled `input` / `select` / `textarea` | `PrksField` (bind `labelledBy`/`describedBy`; no `required` prop). Compact Argument Verdict stays raw in `.prks-arg-row` | Wrapping comboboxes, segmented status, EasyMDE, or search-advanced rows |
| Filters/Legend-style panel trigger | `PrksDisclosureButton` | Fake menus that are really comboboxes |
| Concept/Position/Argument dense row | `PrksResearchRow` | People portraits, Saved Views action rows, Processing cards, work cards |
| Loading / fetch error / empty collection | `PrksState` | `innerHTML` of `.prks-state` |
| In-flow note or save error | `PrksInlineMessage` | A second `role="status"` live region for the same text |

Shared CSS stays in `frontend/css/style.css` (`.prks-btn`, `.prks-icon-btn`, `.prks-field`, `.prks-inline-message`, `.prks-state`, `.prks-scope-line`, `.prks-rel-summary`, `.prks-list-row`, `.prks-research-row`). Do not copy those rules into SFCs while classic consumers exist.

### Accessibility coverage

| Layer | What it covers |
| --- | --- |
| Vitest (`src/components/*` plus `a11y-contracts.test.ts`) | Names, native control, `href`, field label/`for`/error live region, disclosure `aria-expanded`/`aria-controls`, disabled/busy, `PrksState` roles, no duplicate live regions on `PrksInlineMessage` |
| Storybook `@storybook/addon-a11y` | Visual inspection in the Storybook UI. Not a CI gate |
| Playwright | Route/modal/form behavior in GitHub Full E2E. Not run locally by agents |

Storybook 10.6 has no lightweight story or accessibility command that avoids a browser runner, so CI does not add `@storybook/test-runner` or the Vitest browser addon. `@storybook/addon-vitest@10.6.0` peers on Vitest 3 or 4 and `@vitest/browser-playwright@4`. This package pins Vitest 5.0.2, so that addon is not a clean fit: it would downgrade Vitest or wait for a Vitest 5 peer. Ordinary Vitest covers primitive contracts. A later runner slice needs an addon release that peers on this Vitest major.

### Vue/TypeScript ESLint

`npm run lint` in `frontend-app` runs ESLint 10 with `eslint-plugin-vue` essential rules and `typescript-eslint` recommended on `.vue` / `.ts`. It is correctness-oriented, not a formatter. CI’s Vue job runs it. The repository-root `eslint.config.mjs` remains the legacy `frontend/**/*.js` bug-rule set. Generated OpenAPI types are ignored. `vue/no-v-html` is off because Lucide icon HTML and sanitizer-backed research markdown still use `v-html`. Narrow file comments document other exceptions.

Storybook is a maintainer catalog. It does not ship in `frontend/vue/`, does not change the production mount, and does not require Node to run PRKS.

```bash
cd frontend-app
npm ci --ignore-scripts
npm run storybook          # http://localhost:6006
npm run build-storybook    # writes frontend-app/storybook-static (gitignored)
```

CI runs `build-storybook` from the Vue job after a successful diff against the PR base or a fetched nonzero push-before SHA touches `frontend-app/` or `.github/workflows/static-analysis.yml`. A missing or unreadable baseline fails that step. A manual `workflow_dispatch` run builds Storybook without a diff. Typecheck, `npm run lint`, and Vitest still cover the primitives.

Docgen stays off. `vue-docgen-api` is deprecated. `vue-component-meta` extracts the declared props, then also lists Vue internals (`key`, `ref`, `onVue:*`) and leaves local aliases such as `Variant` and `Size` unresolved. Explicit stories are the catalog. Autodocs is not enabled.

Storybook MCP (`@storybook/addon-mcp`) is preview and needs a running Storybook dev server plus a user-level agent connection. It is not committed. Maintainer setup:

1. From `frontend-app`, pin the addon that matches this Storybook: `npm install --save-dev --save-exact --ignore-scripts @storybook/addon-mcp@10.6.0`. Its peer is `@storybook/addon-vitest@10.6.0`, which does not peer on this package's Vitest 5. Add that peer only in the same change that adopts a Vitest-5-compatible story runner.
2. Register the MCP addon in `.storybook/main.ts` `addons`, and set `features.componentsManifest` and `features.experimentalDocgenServer` so the Vue docs toolset can read component props. Turn docgen back on if the manifest needs it.
3. Run `npm run storybook`. The server is `http://127.0.0.1:6006/mcp`.
4. Add that HTTP MCP server in the agent client (Cursor MCP settings, or `npx mcp-add --type http --url "http://127.0.0.1:6006/mcp" --scope project`). Do not commit a machine-local MCP config.
