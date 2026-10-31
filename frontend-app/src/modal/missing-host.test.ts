import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import uiSource from '../../../frontend/js/ui.js?raw'
import ModalHost from './ModalHost.vue'

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

  const originalMatchMedia = window.matchMedia

  afterEach(() => {
    document.body.innerHTML = ''
    document.documentElement.classList.remove('prks-force-mobile')
    window.matchMedia = originalMatchMedia
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

  it('still calls the Vue host after that object occupies window.prksModalHost', async () => {
    const start = uiSource.indexOf('function prksReadModalHost')
    const end = uiSource.indexOf('function prksAlertDialog')
    if (start < 0 || end < start) throw new Error('confirm bridge slice not found')
    // Indirect eval is global, like a classic script: the getter and
    // prksConfirmDialog become window properties that the host object must not replace.
    ;(0, eval)(uiSource.slice(start, end))
    window.prksModalHost = {
      confirm: () => Promise.resolve(true),
      prompt: () => Promise.resolve(null),
      openUnsavedConfirm: () => {},
      hideUnsavedConfirm: () => {},
      isConfirmOpen: () => false,
      isUnsavedConfirmOpen: () => false,
      handleEscape: () => {},
    }
    const dialog = (window as Window & {
      prksConfirmDialog?: (options?: { title?: string }) => Promise<boolean>
    }).prksConfirmDialog
    expect(typeof dialog).toBe('function')
    await expect(dialog?.({ title: 'Delete annotation?' })).resolves.toBe(true)
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

  it('runs dirty close through requestModalClose at a narrow and a wide viewport', async () => {
    const host = mount(ModalHost, { attachTo: document.body })
    try {
      for (const width of [390, 1280]) {
        Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: width })
        window.matchMedia = ((query: string) => ({
          matches: query.includes('max-width: 900px') ? width <= 900 : false,
          media: query,
          onchange: null,
          addListener() {},
          removeListener() {},
          addEventListener() {},
          removeEventListener() {},
          dispatchEvent() {
            return false
          },
        })) as typeof window.matchMedia
        document.documentElement.classList.toggle('prks-force-mobile', width <= 900)
        expect(window.matchMedia('(max-width: 900px)').matches).toBe(width <= 900)

        document.getElementById('folder-modal')?.remove()
        document.body.insertAdjacentHTML(
          'beforeend',
          '<div id="folder-modal" class="modal"><input id="folder-title" value=""></div>',
        )
        delete (window as Window & { __prksModalFormBaseline?: unknown }).__prksModalFormBaseline
        slice.prksCaptureModalBaseline('folder-modal')
        const input = document.getElementById('folder-title')
        if (!(input instanceof HTMLInputElement)) throw new Error('folder title missing')
        input.value = 'Draft'
        closes = 0
        ;(window as Window & { closeModals?: () => void }).closeModals = () => {
          closes += 1
          document.getElementById('folder-modal')?.classList.add('hidden')
        }

        expect(slice.requestModalClose('escape')).toBe(false)
        expect(closes).toBe(0)
        expect(window.prksModalHost?.isUnsavedConfirmOpen()).toBe(true)
        await nextTick()
        await new Promise<void>((resolve) => {
          requestAnimationFrame(() => resolve())
        })
        expect(document.activeElement?.id).toBe('prks-modal-unsaved-confirm-cancel')

        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
        await nextTick()
        expect(window.prksModalHost?.isUnsavedConfirmOpen()).toBe(false)
        expect(closes).toBe(0)
        expect(document.getElementById('folder-modal')?.classList.contains('hidden')).toBe(false)
        expect(input.value).toBe('Draft')

        slice.prksCaptureModalBaseline('folder-modal')
        expect(slice.requestModalClose('escape')).toBe(true)
        expect(closes).toBe(1)
        expect(document.getElementById('folder-modal')?.classList.contains('hidden')).toBe(true)
      }
    } finally {
      host.unmount()
    }
  })
})
