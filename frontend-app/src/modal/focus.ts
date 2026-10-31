/** Focus return for the shared confirm dialog. Feature forms do not live here. */

export function focusStaysOnFocusedTile(node: HTMLElement): boolean {
  const tile = node.closest('.prks-tile[data-prks-tab-id]')
  if (!tile) return true
  const tileId = tile.getAttribute('data-prks-tab-id')
  if (!tileId) return true
  const read = window.prksWorkspaceSnapshot
  if (typeof read !== 'function') return true
  const focused = read()?.focusedTabId
  if (!focused) return true
  return tileId === String(focused)
}

/**
 * The annotation list may repaint while confirm is open. Focus the replacement
 * Delete in the drawer that opened the dialog, never another tile's row.
 */
export function replacementAnnotationDelete(
  opener: HTMLElement,
  ownerTabId: string,
): HTMLElement | null {
  const pane = opener.isConnected ? opener.closest('.work-pdf-pane') : null
  if (pane?.isConnected) {
    const inPane = pane.querySelector('.annotation-row__delete')
    if (inPane instanceof HTMLElement) return inPane
  }
  if (!ownerTabId) return null
  const drawers = document.querySelectorAll('[data-prks-role="pdf-annotation-drawer"]')
  for (const drawer of drawers) {
    if (drawer.getAttribute('data-prks-owner-tab-id') !== ownerTabId) continue
    const button = drawer.querySelector('.annotation-row__delete')
    if (button instanceof HTMLElement) return button
  }
  return null
}

export function restoreConfirmFocus(opener: HTMLElement | null, ownerTabId: string): void {
  let target: HTMLElement | null = null
  if (
    opener &&
    document.contains(opener) &&
    focusStaysOnFocusedTile(opener)
  ) {
    target = opener
  } else if (opener?.classList.contains('annotation-row__delete')) {
    const replacement = replacementAnnotationDelete(opener, ownerTabId)
    if (replacement && focusStaysOnFocusedTile(replacement)) target = replacement
  }
  target?.focus()
}

const FOCUSABLE = [
  'button:not([disabled])',
  'a[href]',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

export function tabbableIn(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => {
    if (el.closest('[hidden], [inert]')) return false
    if (el.closest('.hidden')) return false
    if (el.getAttribute('aria-hidden') === 'true') return false
    return true
  })
}
