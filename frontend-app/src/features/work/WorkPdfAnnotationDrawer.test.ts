import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'
import uiSource from '../../../../frontend/js/ui.js?raw'
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

  afterEach(() => {
    for (const wrapper of mounted.splice(0)) wrapper.unmount()
    delete (window as Window & { prksFlashButtonLabel?: unknown }).prksFlashButtonLabel
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
    const close = wrapper.get('button').element
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
    const wrapper = mount(WorkPdfAnnotationDrawer, {
      props: {
        state,
        tabId: 'tab-a',
        onClose: () => {},
        onJump: () => {},
        onEdit: () => {},
        onDelete: () => pending,
        onCopy: async () => true,
      },
      attachTo: document.body,
    })
    mounted.push(wrapper)
    const button = wrapper.get('.annotation-row__delete')
    const click = button.trigger('click')
    await flushPromises()
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
  }

  afterEach(() => {
    document.body.innerHTML = ''
    delete confirmApi.__prksHideModalConfirm
    delete confirmApi.__prksRememberModalConfirmOpener
    delete (window as Window & { prksWorkspaceSnapshot?: unknown }).prksWorkspaceSnapshot
  })

  it('restores Delete on the drawer owner captured when confirm opened', () => {
    const start = uiSource.indexOf('let prksModalConfirmResolve = null;')
    const end = uiSource.indexOf('function prksFinishModalConfirm', start)
    window.eval(
      uiSource.slice(start, end) +
        '\nwindow.__prksHideModalConfirm = prksHideModalConfirm;\n' +
        'window.__prksRememberModalConfirmOpener = prksRememberModalConfirmOpener;\n',
    )
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
})
