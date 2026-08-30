# EmbedPDF 2.15.0 plugin patches

Reviewed, version-pinned patches for installed `@embedpdf/*@2.15.0` packages. Whole-repo forks are not allowed. Patch files here are the source of truth; never commit edited `node_modules`.

`npm ci` runs `postinstall` → `scripts/apply-embedpdf-patches.mjs`. `npm run build` reapplies or verifies before esbuild. The applier is Node-only (no Unix `patch`). It throws if a package is not `2.15.0`, if a hunk does not match vanilla 2.15.0, or if a patch is malformed. An already-applied hunk is skipped.

Each `.patch` file must include:

| Field | Header |
| --- | --- |
| package | `# package: @embedpdf/plugin-…` |
| version | `# version: 2.15.0` |
| file | `# file: dist/…` (ESM path esbuild actually bundles) |
| why | `# why: …` |
| what | `# what: …` |
| must-not | `# must-not: …` |

## Patches

- `embedpdf-plugin-selection-2.15.0.patch` — text-selection drag captures the pointer when the drag actually begins; `pointerup` / `pointercancel` share one idempotent end. So a release outside the page still ends `selecting`.
- `embedpdf-plugin-pan-2.15.0.patch` — pan uses Pointer Events with capture on down; drag does not end on leave.
- `embedpdf-plugin-viewport-2.15.0.patch` — `behavior === 'auto'` calls `scrollTo` synchronously. Smooth scrolling keeps the scheduled path.

Do not add an `@embedpdf/plugin-interaction-manager` patch unless selection-local capture cannot receive an outside-page `pointerup`. If that happens, stop and report before widening.

## No patch: WheelZoom

Do not patch `@embedpdf/plugin-zoom`. PRKS owns Ctrl/Cmd+wheel in `src/gestures.tsx` (`WheelZoom`). EmbedPDF ZoomPlugin still owns `requestZoomBy` and layout. `ZoomGestureWrapper` stays at `enableWheel={false}` because its CSS scale preview commits after 150ms and flashes the page. That is an intentional PRKS wheel adapter, not a zoom-plugin fork.
