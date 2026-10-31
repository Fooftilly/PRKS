import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import uiSource from '../../../frontend/js/ui.js?raw'

/**
 * The feature-modal shell is in the first HTML. The dialog host arrives with
 * the Vue module. A dirty close in that gap must not discard the form.
 */
type CloseFn = (reason?: string) => boolean
type OpenUnsavedFn = (onDiscard: (() => void) | null) => void
type CaptureFn = (modalId: string) => void

interface ModalCloseSlice {
  requestModalClose: CloseFn
  prksOpenModalUnsavedConfirm: OpenUnsavedFn
  prksCaptureModalBaseline: CaptureFn
}

describe('dirty modal close before the dialog host loads', () => {
  let closes = 0
  let slice: ModalCloseSlice

  beforeAll(() => {
    const start = uiSource.indexOf('const PRKS_MODAL_UNSAVED_CONFIRM_IDS')
    const end = uiSource.indexOf('function prksAutosizeTextarea')
    if (start < 0 || end < start) throw new Error('modal close slice not found')
    const load = new Function(
      `${uiSource.slice(start, end)}
      return { requestModalClose, prksOpenModalUnsavedConfirm, prksCaptureModalBaseline };`,
    ) as () => ModalCloseSlice
    slice = load()
  })

  afterEach(() => {
    document.body.innerHTML = ''
    closes = 0
    delete window.prksModalHost
    delete (window as Window & { __prksModalFormBaseline?: unknown }).__prksModalFormBaseline
    delete (window as Window & { __prksModalBaselineReady?: unknown }).__prksModalBaselineReady
    delete (window as Window & { closeModals?: unknown }).closeModals
  })

  function dirtyFolderModal(): void {
    document.body.innerHTML =
      '<div id="folder-modal" class="modal"><input id="folder-title" value=""></div>'
    slice.prksCaptureModalBaseline('folder-modal')
    const input = document.getElementById('folder-title')
    if (!(input instanceof HTMLInputElement)) throw new Error('folder title missing')
    input.value = 'Draft'
    ;(window as Window & { closeModals?: () => void }).closeModals = () => {
      closes += 1
      document.getElementById('folder-modal')?.classList.add('hidden')
    }
  }

  it('keeps the dirty form open and does not discard when the host is absent', () => {
    dirtyFolderModal()
    delete window.prksModalHost

    const closed = slice.requestModalClose('escape')

    expect(closed).toBe(false)
    expect(closes).toBe(0)
    expect(document.getElementById('folder-modal')?.classList.contains('hidden')).toBe(false)
    expect(document.getElementById('folder-title')).toBeInstanceOf(HTMLInputElement)
    expect((document.getElementById('folder-title') as HTMLInputElement).value).toBe('Draft')
  })

  it('does not run a direct discard callback when the host is absent', () => {
    delete window.prksModalHost
    let discarded = 0
    slice.prksOpenModalUnsavedConfirm(() => {
      discarded += 1
    })
    expect(discarded).toBe(0)
  })

  it('confirms on a later close once the host is installed', () => {
    dirtyFolderModal()
    delete window.prksModalHost
    expect(slice.requestModalClose('escape')).toBe(false)
    expect(closes).toBe(0)

    const pending: Array<(() => void) | null> = []
    window.prksModalHost = {
      confirm: () => Promise.resolve(false),
      prompt: () => Promise.resolve(null),
      openUnsavedConfirm: (onDiscard) => {
        pending.push(onDiscard)
      },
      hideUnsavedConfirm: () => {},
      isConfirmOpen: () => false,
      isUnsavedConfirmOpen: () => false,
      handleEscape: () => {},
    }

    expect(slice.requestModalClose('button')).toBe(false)
    expect(closes).toBe(0)
    expect(pending).toHaveLength(1)
    expect(document.getElementById('folder-modal')?.classList.contains('hidden')).toBe(false)

    pending[0]?.()
    expect(closes).toBe(1)
    expect(document.getElementById('folder-modal')?.classList.contains('hidden')).toBe(true)
  })
})
