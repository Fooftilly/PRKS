export function rememberAnnotationPopupOpener(): HTMLElement | null {
  const active = document.activeElement
  return active instanceof HTMLElement ? active : null
}

export function focusAfterAnnotationPopupClose(
  opener: HTMLElement | null,
  viewerHost: HTMLElement | null,
): void {
  if (opener && opener.isConnected) {
    opener.focus()
    return
  }
  if (!viewerHost) return
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
