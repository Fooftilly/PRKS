# PRKS Vue application

Maintainer-only Vue 3 + TypeScript source for the incremental frontend migration ([#230](https://github.com/Fooftilly/PRKS/issues/230)). Node and npm are build tools. Running PRKS does not use them.

The Python process serves the committed production bundle:

- `frontend/vue/prks-vue.js` (Vue, plus component CSS inlined by `vite-plugin-css-injected-by-js`)
- `frontend/vue/BUILD-MANIFEST.json`

Legacy UI remains `frontend/js/` and `frontend/index.html`. The shell loads the bundle on a hidden `#prks-vue-root`. That app teleports Settings performance diagnostics into `#prks-settings-perf-root`. It is not a router and not a second workspace state model.

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
| `src/features/performance-diagnostics/` | Settings performance diagnostics |
| `src/api/` | Typed PRKS API client used by feature services |
| `src/query/` | Application QueryClient |
| `src/components/` | Shared primitives (`PrksButton`, `PrksStatusText`, `PrksSectionHeader`) and their stories |
| `src/composables/` | Feature-local Vue logic, when a slice needs it |
| `frontend/js/` | Legacy runtime. Leave it in place until a slice replaces a specific responsibility |

`@tanstack/vue-query` is the server-state owner for performance diagnostics. Do not add Vue Router, Pinia, VueUse, or a second QueryClient until a later slice requires it. Do not persist the query cache.

## Components and Storybook

`DESIGN.md` is the visual and interaction contract. Reuse the primitives in `frontend-app/src/components/` before adding another button, section heading, or status line. They apply existing classes from `frontend/css/style.css`. Do not copy that stylesheet into a component, and do not add a second button or card family.

| Path | Role |
| --- | --- |
| `src/components/PrksButton.vue` | `.prks-btn` with variant, disabled, and busy (`disabled` plus `aria-busy`) |
| `src/components/PrksStatusText.vue` | `.prks-settings-hint`, polite live region unless `live` is false |
| `src/components/PrksSectionHeader.vue` | Settings `h5.prks-settings-section__title` |
| `src/components/*.stories.ts` | Real states for those primitives |
| `.storybook/` | Vue 3 + Vite Storybook config |

Storybook is a maintainer catalog. It does not ship in `frontend/vue/`, does not change the production mount, and does not require Node to run PRKS.

```bash
cd frontend-app
npm ci --ignore-scripts
npm run storybook          # http://localhost:6006
npm run build-storybook    # writes frontend-app/storybook-static (gitignored)
```

CI runs `build-storybook` from the Vue job only after a successful diff against the PR base or a fetched nonzero push-before SHA touches `frontend-app/` or `.github/workflows/static-analysis.yml`. A missing or unreadable baseline fails that step. Typecheck and Vitest still cover the primitives. Storybook 10.6 has no lightweight story or accessibility command that avoids a browser runner, so CI does not add `@storybook/test-runner` or the Vitest browser addon. The accessibility addon checks stories in the Storybook UI.

Storybook MCP (`@storybook/addon-mcp`) is preview and needs a running Storybook dev server plus a user-level agent connection. It is not committed. Maintainer setup:

1. From `frontend-app`, pin the addon that matches this Storybook: `npm install --save-dev --save-exact --ignore-scripts @storybook/addon-mcp@10.6.0`. Its peer is `@storybook/addon-vitest@10.6.0`, a browser test runner this repository does not install. Add that peer only in the same change that decides to run story tests in a browser.
2. Register the MCP addon in `.storybook/main.ts` `addons`, and set `features.componentsManifest` and `features.experimentalDocgenServer` so the Vue docs toolset can read component props. Turn docgen back on if the manifest needs it.
3. Run `npm run storybook`. The server is `http://127.0.0.1:6006/mcp`.
4. Add that HTTP MCP server in the agent client (Cursor MCP settings, or `npx mcp-add --type http --url "http://127.0.0.1:6006/mcp" --scope project`). Do not commit a machine-local MCP config.

Official Vue/TypeScript ESLint is not enabled. The repository ESLint config is a small bug-rule set for legacy `frontend/**/*.js`. `eslint-plugin-vue` plus `typescript-eslint` would be a second rule family and is left for a later PR so this one does not start a format sweep.
