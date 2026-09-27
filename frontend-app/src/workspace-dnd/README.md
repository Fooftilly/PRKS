# Workspace drag-and-drop (`workspace-dnd`)

**Production module (#256).** Pragmatic Drag and Drop sensors and lifecycle bind to the Vue
WorkspaceShell DOM; PRKS-specific drop semantics live in pure TypeScript (`drop-intent.ts`,
`geometry.ts`). Confirmed drops commit through the same coordinator APIs as
`frontend/js/workspace-drag.js`.

Historical research and ADOPT decision: #234. Cutover wiring (disable legacy drag, import from
`main.ts` / `WorkspaceShell`) is tracked separately — never run two drag systems at once.

## Architecture

```text
Pragmatic DnD (sensors / lifecycle / targets / preview / autoscroll / cancel)
  → PRKS drop-intent resolver (pure typed)     [drop-intent.ts]
  → canonical PRKS workspace commands          [commit.ts]
  → WorkspaceState + workspace-tabs.js coordinator
  → Vue WorkspaceShell projection
```

Exactly one canonical workspace state. Hover/preview never mutates it. Only confirmed drop calls commands.

## Public API (`index.ts`)

- `bindWorkspaceDnd` — attach adapter to mounted shell DOM; call `destroy()` on unmount.
- `resolveDropIntent`, `dropIntentToCommand`, `hitFromPointer`, `resolveIntentAtPoint` — pure resolution.
- `commitDropIntent`, `browserCommitHandlers` — coordinator commit bridge.
- `createHoverController` — ephemeral preview overlays (not canonical state).

`observeDom` on `bindWorkspaceDnd` defaults to **false**. Production should call `reconcile()` after
shell projection commits (`prksWorkspaceOnShellCommit` or equivalent), not rely on MutationObserver.

## Responsibility split vs `workspace-drag.js`

| Layer | Ownership |
| --- | --- |
| Generic sensors, preview chip, autoscroll, cancel lifecycle | Pragmatic + `auto-scroll` |
| Edge bands, reorder midpoints, park/split/cap/route rules | `resolveDropIntent` + coordinator |
| DOM selectors, overlay classes, cancel hook integration | `adapter.ts` |

## Dependencies

Pinned in `frontend-app/package.json`:

- `@atlaskit/pragmatic-drag-and-drop@4.0.0`
- `@atlaskit/pragmatic-drag-and-drop-auto-scroll@3.2.1`

Headless (no Atlaskit Design System). Hitbox package is intentionally not used — PRKS geometry
requires center = no-drop and 28% edge bands.

## Tests

Vitest under this folder:

- Pure resolver/geometry/commit (`*.test.ts` except adapter/interaction/lifecycle).
- `adapter.test.ts` — Vue shell mount, hover cleanup, commit handlers.
- `interaction.test.ts` — Pragmatic jsdom harness (`pragmatic-harness.ts`).
- `lifecycle.test.ts` — monitor callback ordering and idempotent cancel.

Run: `npm test -- src/workspace-dnd` from `frontend-app`.

## Accessibility

Keyboard-equivalent paths are documented in `a11y.ts` (tab/pane menus call the same coordinator APIs).
