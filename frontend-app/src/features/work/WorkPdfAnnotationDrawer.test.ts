import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import uiSource from '../../../../frontend/js/ui.js?raw'
import splitSource from '../../../../frontend/js/workspace-split.js?raw'
import WorkPdfAnnotationDrawer from './WorkPdfAnnotationDrawer.vue'

const state = {
  open: true,
  epoch: 1,
  viewerToken: 4,
  selectedId: 'ann-a',
  status: '1 annotation',
  published: true,
  generation: 3,
  items: [
    {
      id: 'ann-a',
      index: 0,
      text: 'Highlight',
      comment: 'note',
      pageLabel: 'Page 1',
      pageIndex: 0,
      wikiLink: '[[pdf:ann-a|Highlight]]',
      metadataLabels: ['Topic'],
    },
  ],
}

describe('annotation drawer', () => {
  const mounted: Array<{ unmount: () => void }> = []

  beforeAll(() => {
    window.eval(splitSource)
  })

  afterEach(() => {
    for (const wrapper of mounted.splice(0)) wrapper.unmount()
    delete (window as Window & { prksFlashButtonLabel?: unknown }).prksFlashButtonLabel
    document.body.classList.remove('prks-resizing-split')
  })

  it('paints a published row and forwards the open generation', async () => {
    const jumps: Array<{ id: string; epoch: number; viewerToken: number; generation: number }> = []
    const wrapper = mount(WorkPdfAnnotationDrawer, {
      props: {
        state,
        tabId: 'tab-a',
        onClose: () => {},
        onJump: (id, ticket) => {
          jumps.push({ id, ...ticket })
        },
        onEdit: () => {},
        onDelete: () => {},
        onCopy: async () => true,
      },
      attachTo: document.body,
    })
    mounted.push(wrapper)
    const drawer = wrapper.get('[data-prks-role="pdf-annotation-drawer"]')
    expect(drawer.attributes('data-prks-list-published')).toBe('true')
    expect(wrapper.get('.annotation-row').attributes('data-selected')).toBe('true')
    expect(wrapper.get('.annotation-row__metadata').text()).toContain('Topic')
    await wrapper.get('.annotation-row__jump').trigger('click')
    expect(jumps).toEqual([{ id: 'ann-a', epoch: 1, viewerToken: 4, generation: 3 }])
  })

  it('flashes copy success through the shared helper', async () => {
    const flashes: string[] = []
    ;(window as Window & {
      prksFlashButtonLabel?: (
        button: HTMLButtonElement,
        ok: boolean,
        options: { successLabel: string; errorLabel: string },
      ) => void
    }).prksFlashButtonLabel = (_button, ok, options) => {
      flashes.push(ok ? options.successLabel : options.errorLabel)
    }
    const wrapper = mount(WorkPdfAnnotationDrawer, {
      props: {
        state,
        tabId: 'tab-a',
        onClose: () => {},
        onJump: () => {},
        onEdit: () => {},
        onDelete: () => {},
        onCopy: async () => true,
      },
      attachTo: document.body,
    })
    mounted.push(wrapper)
    await wrapper.get('.annotation-row__copy-link').trigger('click')
    await flushPromises()
    expect(flashes).toEqual(['Copied'])
  })

  it('pins and resizes through the open generation', async () => {
    const pins: boolean[] = []
    const widths: Array<{ width: number; persist: boolean; epoch: number }> = []
    const wrapper = mount(WorkPdfAnnotationDrawer, {
      props: {
        state: { ...state, pinned: false, pinEnabled: true, placement: 'overlay', width: 352 },
        tabId: 'tab-a',
        onClose: () => {},
        onJump: () => {},
        onEdit: () => {},
        onDelete: () => {},
        onCopy: async () => true,
        onPin: (pinned, ticket) => {
          pins.push(pinned)
          expect(ticket).toMatchObject({ epoch: 1, viewerToken: 4, generation: 3 })
        },
        onResize: (width, ticket, options) => {
          widths.push({ width, persist: options.persist, epoch: ticket.epoch })
        },
      },
      attachTo: document.body,
    })
    mounted.push(wrapper)
    await wrapper.get('button[aria-pressed]').trigger('click')
    expect(pins).toEqual([true])
    const handle = wrapper.get('[role="separator"]')
    await handle.trigger('keydown', { key: 'ArrowLeft' })
    expect(widths).toEqual([{ width: 368, persist: true, epoch: 1 }])
    await wrapper.setProps({
      state: { ...state, placement: 'sheet', pinEnabled: false, pinned: false },
    })
    expect(wrapper.find('[role="separator"]').exists()).toBe(false)
    expect(wrapper.get('[data-prks-role="pdf-annotation-drawer"]').attributes('role')).toBe('dialog')
  })

  it('names the pin button for the placement that is showing', async () => {
    const wrapper = mount(WorkPdfAnnotationDrawer, {
      props: {
        state: { ...state, pinned: true, pinEnabled: true, placement: 'overlay', width: 352 },
        tabId: 'tab-a',
        onClose: () => {},
        onJump: () => {},
        onEdit: () => {},
        onDelete: () => {},
        onCopy: async () => true,
      },
      attachTo: document.body,
    })
    mounted.push(wrapper)
    const pin = () => wrapper.findAll('button').find((button) => button.text() === 'Pin' || button.text() === 'Unpin')
    expect(pin()?.text()).toBe('Pin')
    expect(pin()?.attributes('aria-pressed')).toBe('false')
    await wrapper.setProps({
      state: { ...state, pinned: true, pinEnabled: false, placement: 'sheet', width: 352 },
    })
    expect(pin()?.text()).toBe('Pin')
    expect(pin()?.attributes('aria-pressed')).toBe('false')
    expect(pin()?.attributes('disabled')).toBeDefined()
    await wrapper.setProps({
      state: { ...state, pinned: true, pinEnabled: true, placement: 'pinned', width: 352 },
    })
    expect(pin()?.text()).toBe('Unpin')
    expect(pin()?.attributes('aria-pressed')).toBe('true')
  })

  it('reuses the gesture ticket, coalesces moves, and restores on cancel', async () => {
    const frames: Array<FrameRequestCallback> = []
    const originalFrame = window.requestAnimationFrame
    const originalCancel = window.cancelAnimationFrame
    window.requestAnimationFrame = (callback: FrameRequestCallback) => {
      frames.push(callback)
      return frames.length
    }
    window.cancelAnimationFrame = (id: number) => {
      frames[id - 1] = () => {}
    }
    try {
      const calls: Array<{
        width: number
        ticket: { generation: number; epoch: number; viewerToken: number }
        persist: boolean
        preview?: boolean
        cancel?: boolean
      }> = []
      const wrapper = mount(WorkPdfAnnotationDrawer, {
        props: {
          state: { ...state, pinned: true, pinEnabled: true, placement: 'pinned', width: 352 },
          tabId: 'tab-a',
          onClose: () => {},
          onJump: () => {},
          onEdit: () => {},
          onDelete: () => {},
          onCopy: async () => true,
          onResize: (width, ticket, options) => {
            calls.push({ width, ticket, persist: options.persist, preview: options.preview, cancel: options.cancel })
          },
        },
        attachTo: document.body,
      })
      mounted.push(wrapper)
      await nextTick()
      const handle = wrapper.get('.pdf-annotation-drawer__resize').element
      handle.dispatchEvent(new PointerEvent('pointerdown', {
        clientX: 400,
        pointerId: 7,
        button: 0,
        bubbles: true,
        cancelable: true,
      }))
      document.dispatchEvent(new PointerEvent('pointermove', {
        clientX: 380,
        pointerId: 7,
        bubbles: true,
        cancelable: true,
      }))
      document.dispatchEvent(new PointerEvent('pointermove', {
        clientX: 360,
        pointerId: 7,
        bubbles: true,
        cancelable: true,
      }))
      expect(calls).toEqual([])
      expect(frames).toHaveLength(1)
      frames[0]?.(0)
      expect(calls).toEqual([
        { width: 392, ticket: calls[0]?.ticket, persist: false, preview: true, cancel: undefined },
      ])
      const captured = calls[0]?.ticket
      document.dispatchEvent(new PointerEvent('pointercancel', {
        pointerId: 7,
        bubbles: true,
        cancelable: true,
      }))
      expect(calls).toEqual([
        { width: 392, ticket: captured, persist: false, preview: true, cancel: undefined },
        { width: 352, ticket: captured, persist: false, preview: undefined, cancel: true },
      ])
      expect(calls[1]?.ticket).toBe(captured)
    } finally {
      window.requestAnimationFrame = originalFrame
      window.cancelAnimationFrame = originalCancel
    }
  })

  it('commits the latest coalesced width once on pointerup', async () => {
    const frames: Array<FrameRequestCallback> = []
    const originalFrame = window.requestAnimationFrame
    const originalCancel = window.cancelAnimationFrame
    window.requestAnimationFrame = (callback: FrameRequestCallback) => {
      frames.push(callback)
      return frames.length
    }
    window.cancelAnimationFrame = () => {}
    try {
      const calls: Array<{ width: number; ticket: object; persist: boolean; preview?: boolean }> = []
      const wrapper = mount(WorkPdfAnnotationDrawer, {
        props: {
          state: { ...state, pinned: true, pinEnabled: true, placement: 'pinned', width: 352 },
          tabId: 'tab-a',
          onClose: () => {},
          onJump: () => {},
          onEdit: () => {},
          onDelete: () => {},
          onCopy: async () => true,
          onResize: (width, ticket, options) => {
            calls.push({ width, ticket, persist: options.persist, preview: options.preview })
          },
        },
        attachTo: document.body,
      })
      mounted.push(wrapper)
      await nextTick()
      const handle = wrapper.get('.pdf-annotation-drawer__resize').element
      handle.dispatchEvent(new PointerEvent('pointerdown', {
        clientX: 200,
        pointerId: 3,
        button: 0,
        bubbles: true,
        cancelable: true,
      }))
      document.dispatchEvent(new PointerEvent('pointermove', {
        clientX: 184,
        pointerId: 3,
        bubbles: true,
        cancelable: true,
      }))
      frames[0]?.(0)
      document.dispatchEvent(new PointerEvent('pointerup', {
        pointerId: 3,
        bubbles: true,
        cancelable: true,
      }))
      expect(calls.map((call) => ({ width: call.width, persist: call.persist, preview: call.preview }))).toEqual([
        { width: 368, persist: false, preview: true },
        { width: 368, persist: true, preview: undefined },
      ])
      expect(calls[1]?.ticket).toBe(calls[0]?.ticket)
    } finally {
      window.requestAnimationFrame = originalFrame
      window.cancelAnimationFrame = originalCancel
    }
  })

  it('closes a keyboard-opened drawer on Escape and restores the opener', async () => {
    const opener = document.createElement('button')
    opener.type = 'button'
    opener.textContent = 'Annotations'
    document.body.appendChild(opener)
    opener.focus()
    const closes: number[] = []
    const view = { ...state, open: false }
    const wrapper = mount(WorkPdfAnnotationDrawer, {
      props: {
        state: view,
        tabId: 'tab-a',
        onClose: () => {
          closes.push(view.epoch)
        },
        onJump: () => {},
        onEdit: () => {},
        onDelete: () => {},
        onCopy: async () => true,
      },
      attachTo: document.body,
    })
    mounted.push(wrapper)
    await wrapper.setProps({ state: { ...view, open: true } })
    await nextTick()
    const close = wrapper.findAll('button').find((button) => button.text() === 'Close')?.element
    expect(close).toBeInstanceOf(HTMLButtonElement)
    if (!(close instanceof HTMLButtonElement)) return
    expect(document.activeElement).toBe(close)
    close.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(closes).toEqual([1])
    await wrapper.setProps({ state: { ...view, open: false } })
    await nextTick()
    expect(document.activeElement).toBe(opener)
    opener.remove()
  })

  it('keeps the clicked Delete button busy until the delete settles', async () => {
    let finish: (ok: boolean) => void = () => {}
    const pending = new Promise<boolean>((resolve) => {
      finish = resolve
    })
    const calls: boolean[] = []
    window.prksSetButtonBusy = (target, busy, options) => {
      if (!(target instanceof HTMLButtonElement)) return
      calls.push(busy)
      if (busy) {
        target.disabled = true
        target.setAttribute('aria-busy', 'true')
        if (options?.busyLabel) target.textContent = options.busyLabel
      } else {
        target.disabled = false
        target.removeAttribute('aria-busy')
        target.textContent = 'Delete'
      }
    }
    let capturedWhileFocused = false
    const wrapper = mount(WorkPdfAnnotationDrawer, {
      props: {
        state,
        tabId: 'tab-a',
        onClose: () => {},
        onJump: () => {},
        onEdit: () => {},
        onDelete: () => {
          capturedWhileFocused = document.activeElement === deleteButton
          expect(deleteButton.disabled).toBe(false)
          return pending
        },
        onCopy: async () => true,
      },
      attachTo: document.body,
    })
    mounted.push(wrapper)
    const button = wrapper.get('.annotation-row__delete')
    const deleteButton = button.element as HTMLButtonElement
    deleteButton.focus()
    const click = button.trigger('click')
    await flushPromises()
    expect(capturedWhileFocused).toBe(true)
    expect(calls).toEqual([true])
    expect(button.attributes('aria-busy')).toBe('true')
    expect(button.text()).toBe('Deleting…')
    finish(true)
    await click
    expect(calls).toEqual([true, false])
    expect(button.attributes('aria-busy')).toBeUndefined()
    expect(button.text()).toBe('Delete')
    delete window.prksSetButtonBusy
  })
})

describe('annotation delete confirm focus', () => {
  const confirmApi = window as Window & {
    __prksHideModalConfirm?: () => void
    __prksRememberModalConfirmOpener?: (active: Element | null) => void
    prksConfirmDeletePdfAnnotation?: () => Promise<boolean>
    prksBindModalConfirmOnce?: () => void
  }
  const originalFocus = HTMLButtonElement.prototype.focus

  beforeAll(() => {
    const start = uiSource.indexOf('let prksModalConfirmResolve = null;')
    const end = uiSource.indexOf('function prksBindModalUnsavedConfirmOnce', start)
    window.eval(
      uiSource.slice(start, end) +
        '\nwindow.__prksHideModalConfirm = prksHideModalConfirm;\n' +
        'window.__prksRememberModalConfirmOpener = prksRememberModalConfirmOpener;\n' +
        'window.prksConfirmDeletePdfAnnotation = prksConfirmDeletePdfAnnotation;\n' +
        'window.prksSetButtonBusy = prksSetButtonBusy;\n' +
        'window.prksBindModalConfirmOnce = prksBindModalConfirmOnce;\n',
    )
  })

  afterEach(() => {
    HTMLButtonElement.prototype.focus = originalFocus
    document.body.innerHTML = ''
    delete (window as Window & { prksWorkspaceSnapshot?: unknown }).prksWorkspaceSnapshot
  })

  function installConfirm() {
    document.body.insertAdjacentHTML(
      'beforeend',
      '<div id="prks-modal-confirm" class="prks-modal-confirm hidden" aria-hidden="true">' +
        '<h3 id="prks-modal-confirm-title"></h3>' +
        '<p id="prks-modal-confirm-desc"></p>' +
        '<div class="prks-modal-confirm__actions">' +
        '<button type="button" id="prks-modal-confirm-cancel">Cancel</button>' +
        '<button type="button" id="prks-modal-confirm-ok">OK</button>' +
        '</div></div>',
    )
    confirmApi.prksBindModalConfirmOnce?.()
    HTMLButtonElement.prototype.focus = function focus(this: HTMLButtonElement) {
      if (this.disabled) return
      originalFocus.call(this)
    }
  }

  it('restores Delete on the drawer owner captured when confirm opened', () => {
    function drawer(tabId: string) {
      const tile = document.createElement('div')
      tile.className = 'prks-tile'
      tile.setAttribute('data-prks-tab-id', tabId)
      const pane = document.createElement('div')
      pane.className = 'work-pdf-pane'
      const aside = document.createElement('aside')
      aside.setAttribute('data-prks-role', 'pdf-annotation-drawer')
      aside.setAttribute('data-prks-owner-tab-id', tabId)
      const button = document.createElement('button')
      button.type = 'button'
      button.className = 'annotation-row__delete'
      button.textContent = tabId
      aside.appendChild(button)
      pane.appendChild(aside)
      tile.appendChild(pane)
      document.body.appendChild(tile)
      return { aside, button }
    }
    const side = drawer('side')
    const main = drawer('main')
    ;(window as Window & { prksWorkspaceSnapshot?: () => { focusedTabId: string } }).prksWorkspaceSnapshot = () => ({
      focusedTabId: 'side',
    })
    confirmApi.__prksRememberModalConfirmOpener?.(side.button)
    side.button.remove()
    const fresh = document.createElement('button')
    fresh.type = 'button'
    fresh.className = 'annotation-row__delete'
    fresh.textContent = 'side replacement'
    side.aside.appendChild(fresh)
    confirmApi.__prksHideModalConfirm?.()
    expect(document.activeElement).toBe(fresh)
    expect(document.activeElement).not.toBe(main.button)
  })

  it('keeps the annotation and restores Delete focus after cancel', async () => {
    installConfirm()
    const wrapper = mount(WorkPdfAnnotationDrawer, {
      props: {
        state,
        tabId: 'tab-a',
        onClose: () => {},
        onJump: () => {},
        onEdit: () => {},
        onDelete: () => confirmApi.prksConfirmDeletePdfAnnotation?.() ?? Promise.resolve(false),
        onCopy: async () => true,
      },
      attachTo: document.body,
    })
    const button = wrapper.get('.annotation-row__delete')
    const deleteButton = button.element as HTMLButtonElement
    deleteButton.focus()
    deleteButton.click()
    await flushPromises()
    const dialog = document.getElementById('prks-modal-confirm')
    expect(dialog?.classList.contains('hidden')).toBe(false)
    expect(deleteButton.disabled).toBe(true)
    document.getElementById('prks-modal-confirm-cancel')?.click()
    await flushPromises()
    expect(dialog?.classList.contains('hidden')).toBe(true)
    expect(wrapper.find('.annotation-row').exists()).toBe(true)
    expect(deleteButton.disabled).toBe(false)
    expect(deleteButton.getAttribute('aria-busy')).toBeNull()
    expect(document.activeElement).toBe(deleteButton)
    wrapper.unmount()
  })

  it('restores the owner drawer replacement when cancel follows a repaint', async () => {
    installConfirm()
    const wrapper = mount(WorkPdfAnnotationDrawer, {
      props: {
        state,
        tabId: 'tab-a',
        onClose: () => {},
        onJump: () => {},
        onEdit: () => {},
        onDelete: () => confirmApi.prksConfirmDeletePdfAnnotation?.() ?? Promise.resolve(false),
        onCopy: async () => true,
      },
      attachTo: document.body,
    })
    const button = wrapper.get('.annotation-row__delete')
    const deleteButton = button.element as HTMLButtonElement
    deleteButton.focus()
    deleteButton.click()
    await flushPromises()
    await new Promise<void>((resolve) => {
      requestAnimationFrame(() => resolve())
    })
    const cancel = document.getElementById('prks-modal-confirm-cancel')
    expect(document.activeElement).toBe(cancel)
    const other = document.createElement('aside')
    other.setAttribute('data-prks-role', 'pdf-annotation-drawer')
    other.setAttribute('data-prks-owner-tab-id', 'other')
    const otherDelete = document.createElement('button')
    otherDelete.type = 'button'
    otherDelete.className = 'annotation-row__delete'
    other.appendChild(otherDelete)
    document.body.appendChild(other)
    const drawer = wrapper.get('[data-prks-role="pdf-annotation-drawer"]').element
    deleteButton.remove()
    const fresh = document.createElement('button')
    fresh.type = 'button'
    fresh.className = 'annotation-row__delete'
    drawer.appendChild(fresh)
    cancel?.click()
    await flushPromises()
    expect(wrapper.find('.annotation-row').exists()).toBe(true)
    expect(document.activeElement).toBe(fresh)
    expect(document.activeElement).not.toBe(otherDelete)
    wrapper.unmount()
  })
})
