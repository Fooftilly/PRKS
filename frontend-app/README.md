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
```

`npm run dev` serves only this package's build entry for maintainer inspection. It is not how PRKS starts. Use `python prks_app.py --testing`.

## Where later slices go

| Path | Role |
| --- | --- |
| `src/features/performance-diagnostics/` | Settings performance diagnostics |
| `src/api/` | Typed PRKS API client used by feature services |
| `src/query/` | Application QueryClient |
| `src/components/` | Migrated single-file components, when a slice creates them |
| `src/composables/` | Feature-local Vue logic, when a slice needs it |
| `frontend/js/` | Legacy runtime. Leave it in place until a slice replaces a specific responsibility |

`@tanstack/vue-query` is the server-state owner for performance diagnostics. Do not add Vue Router, Pinia, VueUse, or a second QueryClient until a later slice requires it. Do not persist the query cache.
