export function rememberAnnotationPopupOpener(): HTMLElement | null {
  const active = document.activeElement
  return active instanceof HTMLElement ? active : null
}

function focusedWorkspaceTabId(): string | null {
  const read = (
    window as Window & {
      prksWorkspaceSnapshot?: () => { focusedTabId?: string | null } | null | undefined
    }
  ).prksWorkspaceSnapshot
  if (typeof read !== 'function') return null
  const value = read()
  const id = value && value.focusedTabId
  return typeof id === 'string' && id !== '' ? id : null
}

/** Focusing a node in another tile runs workspace focus-in and steals the panel. */
function focusStaysOnFocusedTile(node: HTMLElement): boolean {
  const tile = node.closest('.prks-tile[data-prks-tab-id]')
  if (!tile) return true
  const tileId = tile.getAttribute('data-prks-tab-id')
  if (!tileId) return true
  const focused = focusedWorkspaceTabId()
  if (!focused) return true
  return tileId === focused
}

export function focusAfterAnnotationPopupClose(
  opener: HTMLElement | null,
  viewerHost: HTMLElement | null,
): void {
  if (opener && opener.isConnected && focusStaysOnFocusedTile(opener)) {
    opener.focus()
    return
  }
  if (!viewerHost || !focusStaysOnFocusedTile(viewerHost)) return
  if (!viewerHost.hasAttribute('tabindex')) viewerHost.setAttribute('tabindex', '-1')
  viewerHost.focus()
}

/** Confirmations and `.modal` layers sit above this popup. */
export function annotationPopupEscapeYields(): boolean {
  const confirm = document.getElementById('prks-modal-confirm')
  if (confirm && !confirm.classList.contains('hidden')) return true
  const unsaved = document.getElementById('prks-modal-unsaved-confirm')
  if (unsaved && !unsaved.classList.contains('hidden')) return true
  return !!document.querySelector('.modal:not(.hidden)')
}
