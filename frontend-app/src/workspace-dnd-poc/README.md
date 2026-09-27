# #234 Pragmatic Drag and Drop — research PoC

**Decision: ADOPT (provisional)** — sensors/lifecycle only; production cutover is a separate
issue (#256). Provisional until focused Pragmatic interaction evidence in
`interaction.test.ts` is accepted; do not treat #256 as fully authorized and do not close #234
solely on this PoC PR.

Baseline: `master` at `f353e2879b3ca8fed0da6d7c78035673d0847bd3` (#255 Vue WorkspaceShell).

This folder is an **experimental adapter**. It is not imported by `main.ts` or production `WorkspaceShell`. Production drag remains `frontend/js/workspace-drag.js`. Do not enable two drag systems at once.

## Target architecture (adopt)

```text
Pragmatic DnD (sensors / lifecycle / targets / preview / autoscroll / cancel)
  → PRKS drop-intent resolver (pure typed)     [this package: drop-intent.ts]
  → canonical PRKS workspace commands
  → WorkspaceState + workspace-tabs.js coordinator
  → Vue WorkspaceShell projection
```

Exactly one canonical workspace state. Hover/preview never mutates it. Only confirmed drop calls commands.

## A / B / C responsibility matrix (`workspace-drag.js` ≈ 764 LOC)

| Layer | Responsibility | Lines (approx.) | Adopt ownership |
| --- | --- | --- | --- |
| **A — generic** | pointer/HTML5 drag arming, threshold, capture/lifecycle, preview chip position, document listeners, Escape/blur/cancel cleanup, click suppression, tab-strip autoscroll loop | ~380 | **Pragmatic** core + `auto-scroll` |
| **B — PRKS semantic** | edge-band geometry (28%, center = no-drop), reorder midpoints, strip vs spatial target kinds, Main exclusion, pane cap / route eligibility, park vs reorder, empty-Secondary zone, announce copy, commit → `reorder` / `movePane` / `splitLeaf` / `hideLeaf` / `tileTab` | ~280 | **PRKS** `resolveDropIntent` + coordinator |
| **C — glue** | DOM selectors (`.prks-workspace-tab`, grip, tiles, `#prks-workspace-tabs`), overlay class names, `prksWorkspaceCancelActiveDrag` for responsive/shell prune, init binding | ~100 | thin Vue/PoC adapter; shrink further on migration |

## Dependency evaluation

| Item | Finding |
| --- | --- |
| Packages | `@atlaskit/pragmatic-drag-and-drop@4.0.0`, `@atlaskit/pragmatic-drag-and-drop-auto-scroll@3.2.1` (exact pins) |
| License | Apache-2.0 |
| UI | Headless. No Atlaskit Design System / optional visual packages. |
| Vue | Framework-agnostic element adapter; cleanup functions compose with Vue `onUnmounted`. |
| Bundle | Core advertised ~4.7 kB; not in production `prks-vue.js` until migration imports it. PoC imports are test/experimental only. |
| Hitbox package | **Not adopted.** `@atlaskit/pragmatic-drag-and-drop-hitbox` closest-edge always picks an edge; PRKS requires center = no-drop and 28% bands. Keep PRKS geometry. |
| Nested targets | Supported (`dropTargetForElements` per tile + strip + canvas); PoC resolves intent with PRKS `pickNestedLeafHit` + edge bands. |
| Autoscroll | `autoScrollForElements` on `#prks-workspace-tabs` for tab-source drags only (matches production: pane→strip park does not autoscroll). |
| Unmount cleanup | `session.destroy()` tears down all Pragmatic registrations + ephemeral hover. |
| A11y | Do **not** take Atlaskit assistive controls. Keyboard equivalents already exist via tab/pane menus (see `a11y.ts`). |

## PoC evidence

- Pure `resolveDropIntent` / `dropIntentToCommand` Vitest coverage (reorder, park, nested edge move, parked→split, cap invalid, Main exclusion, empty-Secondary).
- Adapter binds to **real Vue WorkspaceShell DOM** from #255 (tabs, grips, tiles); cancel/destroy leave no hover residue; commit path only on confirmed drop.
- **Focused Pragmatic interaction tests** (`interaction.test.ts`) use the official
  `@atlaskit/pragmatic-drag-and-drop-unit-testing` DragEvent polyfill harness so
  monitor/draggable/drop-target callbacks run (jsdom limitation vs Chromium hardware —
  documented in `pragmatic-harness.ts`). Covers reorder, nested edge move, final-drop
  authority, post-mount reconcile, cancel via `prksWorkspaceCancelActiveDrag`, strip
  autoscroll, and single coordinator commit.
- **Lifecycle callback tests** (`lifecycle.test.ts`) capture `monitorForElements`
  `onDragStart`/`onDrag`/`onDrop` and exercise start→drag→drop plus start→cancel→drop
  (idempotent `onSessionEnd`; no commit after cancel).
- Strip self-slot reorders resolve to null; Main reorders that change order remain valid.
- Invalid edge overlays include a dashed/hatch + text cue; live region announces cap/route.
- Keyboard-equivalent paths documented in `a11y.ts` (at least Move tab left/right, Split right/down, Hide from split, Open in split view, Escape).
- Cancellation hard criteria: Escape, window blur, `cancel()`, `destroy()`, shell cancel hook — no canonical mutation.

## Quantified comparison

| Metric | Custom `workspace-drag.js` | PoC / adopt path |
| --- | --- | --- |
| Generic sensor LOC | ~380 in one IIFE | replaced by Pragmatic + thin bind |
| Semantic LOC | ~280 intertwined | ~200 typed pure + commit bridge (unit-testable) |
| Parallel state | none (good) | none (preserved) |
| Sensor model | Pointer Events | HTML5 DnD (migration risk; see below) |
| Production dual systems | n/a | **forbidden** — cut over once |

## Hard criteria checklist

- [x] Tab reorder intent
- [x] Pane edge move/split (nested)
- [x] Parked→split (empty-Secondary + edge split)
- [x] Pure typed `WorkspaceDropIntent` resolver, unit-tested
- [x] Nested target determinism (order + center null)
- [x] Ephemeral hover only
- [x] Cancellation / lifecycle cleanup
- [x] Autoscroll on overflowing tab strip (wired; production strip still owns scroll CSS)
- [x] A11y keyboard-equivalent path documented
- [x] Explicit **ADOPT (provisional)** pending acceptance of interaction evidence

## Migration risks (for follow-up issue)

1. **Pointer → HTML5 DnD** sensor change (threshold, click suppression, `lostpointercapture` vs native drag end).
2. Wire adapter into shell **only after** disabling `prksWorkspaceInitDrag` (one system).
3. Preserve `prksWorkspaceCancelActiveDrag` contract for narrow-fallback / shell prune.
4. Port existing drag E2E to the new sensor without relaxing invariants.

## Non-goals (honored)

No Pinia, Vue Router, #233, #58, route migrations, visual redesign, or production replacement of `workspace-drag.js` in this research PR.
