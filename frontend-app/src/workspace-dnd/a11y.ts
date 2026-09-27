/**
 * Accessibility notes for workspace DnD (#256) — keyboard-equivalent paths.
 *
 * Pragmatic Drag and Drop's optional assistive-technology package is tied to
 * the Atlassian Design System. PRKS does not adopt Atlaskit UI. Keyboard users
 * keep the existing workspace tab/pane menu commands, which already call the
 * same canonical coordinator APIs as a confirmed drop.
 */

export const KEYBOARD_EQUIVALENT_PATHS = [
  {
    dragGesture: 'Tab strip reorder',
    keyboardPath: 'Tab context menu → "Move tab left" / "Move tab right"',
    canonicalApi: 'prksWorkspaceMoveTabStep / reorder-tab',
  },
  {
    dragGesture: 'Parked tab → empty Secondary (Open in split view)',
    keyboardPath: 'Tab context menu → "Open in split view"',
    canonicalApi: 'prksWorkspaceTileTab',
  },
  {
    dragGesture: 'Parked/visible → pane edge split',
    keyboardPath: 'Pane actions / tab menu → "Split right" / "Split down"',
    canonicalApi: 'prksWorkspaceSplitLeaf',
  },
  {
    dragGesture: 'Visible pane → tab strip (park)',
    keyboardPath: 'Pane actions → "Hide from split"',
    canonicalApi: 'prksWorkspaceHideLeaf',
  },
  {
    dragGesture: 'Cancel in-progress drag',
    keyboardPath: 'Escape (workspace-dnd adapter)',
    canonicalApi: 'no canonical mutation',
  },
] as const

export function keyboardEquivalentSummary(): string {
  return KEYBOARD_EQUIVALENT_PATHS.map(
    (row) => `- ${row.dragGesture}: ${row.keyboardPath} (${row.canonicalApi})`,
  ).join('\n')
}
