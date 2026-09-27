/**
 * Ephemeral hover / preview DOM for workspace DnD (#256). Never touches WorkspaceState.
 * Cleanup is idempotent; safe after cancel, drop, or unmount.
 */

export interface HoverController {
  showReorderMarker(list: HTMLElement, beforeTabId: string | null): void
  showParkTarget(list: HTMLElement): void
  showEdgeOverlay(
    tile: HTMLElement,
    zone: 'left' | 'right' | 'above' | 'below',
    valid: boolean,
    reason?: 'cap' | 'route' | null,
  ): void
  showEmptySecondary(canvas: HTMLElement): void
  clear(): void
}

function cssEscape(id: string): string {
  if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') return CSS.escape(id)
  return String(id).replace(/["\\]/g, '\\$&')
}

function bandRect(
  rect: DOMRect,
  zone: 'left' | 'right' | 'above' | 'below',
): { left: number; top: number; width: number; height: number } {
  if (zone === 'left') return { left: rect.left, top: rect.top, width: rect.width / 2, height: rect.height }
  if (zone === 'right') {
    return { left: rect.left + rect.width / 2, top: rect.top, width: rect.width / 2, height: rect.height }
  }
  if (zone === 'above') return { left: rect.left, top: rect.top, width: rect.width, height: rect.height / 2 }
  return { left: rect.left, top: rect.top + rect.height / 2, width: rect.width, height: rect.height / 2 }
}

export function createHoverController(doc: Document = document): HoverController {
  function clear(): void {
    doc.getElementById('prks-drag-insertion-marker')?.remove()
    doc.getElementById('prks-drag-edge-overlay')?.remove()
    doc.getElementById('prks-drag-empty-overlay')?.remove()
    doc.getElementById('prks-workspace-tabs')?.classList.remove('is-drop-target-park')
  }

  return {
    clear,
    showReorderMarker(list, beforeTabId) {
      clear()
      const marker = doc.createElement('div')
      marker.id = 'prks-drag-insertion-marker'
      marker.className = 'prks-drag-insertion-marker'
      marker.setAttribute('aria-hidden', 'true')
      let ref: Element | null = null
      if (beforeTabId) {
        ref = list.querySelector('.prks-workspace-tab[data-tab-id="' + cssEscape(beforeTabId) + '"]')
      }
      if (ref) list.insertBefore(marker, ref)
      else list.appendChild(marker)
    },
    showParkTarget(list) {
      clear()
      list.classList.add('is-drop-target-park')
    },
    showEdgeOverlay(tile, zone, valid, reason = null) {
      clear()
      const rect = bandRect(tile.getBoundingClientRect(), zone)
      const overlay = doc.createElement('div')
      overlay.id = 'prks-drag-edge-overlay'
      overlay.className = 'prks-drag-edge-overlay' + (valid ? '' : ' is-invalid')
      overlay.setAttribute('aria-hidden', 'true')
      if (!valid) {
        // Non-color cue (DESIGN: state must not rely on color alone). Live region
        // also announces the cap/route reason via announceTargetChange.
        overlay.dataset.reason = reason === 'route' ? 'route' : 'cap'
        overlay.style.borderStyle = 'dashed'
        overlay.style.display = 'flex'
        overlay.style.alignItems = 'center'
        overlay.style.justifyContent = 'center'
        overlay.style.backgroundImage =
          'repeating-linear-gradient(135deg, transparent, transparent 5px, rgba(0,0,0,0.12) 5px, rgba(0,0,0,0.12) 10px)'
        const label = doc.createElement('span')
        label.className = 'prks-drag-edge-overlay__reason'
        label.textContent = reason === 'route' ? 'Cannot split' : 'Pane limit'
        overlay.appendChild(label)
      }
      overlay.style.left = Math.round(rect.left) + 'px'
      overlay.style.top = Math.round(rect.top) + 'px'
      overlay.style.width = Math.round(rect.width) + 'px'
      overlay.style.height = Math.round(rect.height) + 'px'
      doc.body.appendChild(overlay)
    },
    showEmptySecondary(canvas) {
      clear()
      const rect = canvas.getBoundingClientRect()
      const overlay = doc.createElement('div')
      overlay.id = 'prks-drag-empty-overlay'
      overlay.className = 'prks-drag-empty-overlay'
      overlay.setAttribute('aria-hidden', 'true')
      overlay.textContent = 'Open in split view'
      overlay.style.left = Math.round(rect.left + rect.width * 0.6) + 'px'
      overlay.style.top = Math.round(rect.top) + 'px'
      overlay.style.width = Math.round(rect.width * 0.4) + 'px'
      overlay.style.height = Math.round(rect.height) + 'px'
      doc.body.appendChild(overlay)
    },
  }
}
