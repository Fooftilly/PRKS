import { describe, expect, it } from 'vitest'
import {
  annotationPopupEscapeYields,
  focusAfterAnnotationPopupClose,
  rememberAnnotationPopupOpener,
} from './pdf-annotation-popup-focus'

describe('annotation popup focus', () => {
  it('remembers the element that was active when the popup opened', () => {
    const opener = document.createElement('button')
    document.body.appendChild(opener)
    opener.focus()
    expect(rememberAnnotationPopupOpener()).toBe(opener)
    opener.remove()
  })

  it('restores a connected opener and otherwise focuses the PDF viewer host', () => {
    const opener = document.createElement('button')
    const viewer = document.createElement('div')
    document.body.append(opener, viewer)
    opener.focus()
    focusAfterAnnotationPopupClose(opener, viewer)
    expect(document.activeElement).toBe(opener)

    opener.remove()
    focusAfterAnnotationPopupClose(opener, viewer)
    expect(document.activeElement).toBe(viewer)
    expect(viewer.getAttribute('tabindex')).toBe('-1')
    viewer.remove()
  })

  it('does not focus a viewer in an unfocused workspace tile', () => {
    const tile = document.createElement('div')
    tile.className = 'prks-tile'
    tile.setAttribute('data-prks-tab-id', 'tab-1')
    const viewer = document.createElement('div')
    tile.appendChild(viewer)
    const other = document.createElement('button')
    document.body.append(tile, other)
    other.focus()
    const win = window as Window & {
      prksWorkspaceSnapshot?: () => { focusedTabId: string }
    }
    win.prksWorkspaceSnapshot = () => ({ focusedTabId: 'tab-2' })
    const opener = document.createElement('button')
    focusAfterAnnotationPopupClose(opener, viewer)
    expect(document.activeElement).toBe(other)
    expect(viewer.hasAttribute('tabindex')).toBe(false)

    win.prksWorkspaceSnapshot = () => ({ focusedTabId: 'tab-1' })
    focusAfterAnnotationPopupClose(opener, viewer)
    expect(document.activeElement).toBe(viewer)

    const paneOpener = document.createElement('button')
    tile.appendChild(paneOpener)
    win.prksWorkspaceSnapshot = () => ({ focusedTabId: 'tab-2' })
    other.focus()
    focusAfterAnnotationPopupClose(paneOpener, viewer)
    expect(document.activeElement).toBe(other)

    delete win.prksWorkspaceSnapshot
    tile.remove()
    other.remove()
  })

  it('yields Escape while a confirmation or modal is open', () => {
    expect(annotationPopupEscapeYields()).toBe(false)
    const confirm = document.createElement('div')
    confirm.id = 'prks-modal-confirm'
    document.body.appendChild(confirm)
    expect(annotationPopupEscapeYields()).toBe(true)
    confirm.classList.add('hidden')
    expect(annotationPopupEscapeYields()).toBe(false)
    const modal = document.createElement('div')
    modal.className = 'modal'
    document.body.appendChild(modal)
    expect(annotationPopupEscapeYields()).toBe(true)
    modal.classList.add('hidden')
    expect(annotationPopupEscapeYields()).toBe(false)
    confirm.remove()
    modal.remove()
  })
})
