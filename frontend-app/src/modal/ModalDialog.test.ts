import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { h } from 'vue'
import ModalDialog from './ModalDialog.vue'
import {
  dialogState,
  hideDialogs,
  isDialogOpen,
  ownsDialog,
  resetDialogsForTest,
  restoreDialog,
  setDialogInert,
  showDialog,
} from './dialogs'

function mountDialogs() {
  return mount(
    {
      render: () => [
        h(ModalDialog, { id: 'a-modal', title: 'A dialog', dialogClass: 'modal-wide', withFooter: true }, () =>
          h('input', { id: 'a-field' }),
        ),
        h(ModalDialog, { id: 'b-modal', title: 'B dialog', bodyClass: 'modal-body--scroll' }, () => h('p', 'b')),
      ],
    },
    { attachTo: document.body },
  )
}

describe('ModalDialog', () => {
  afterEach(() => {
    document.body.innerHTML = ''
    resetDialogsForTest()
  })

  it('renders a hidden, labelled shell with its body, footer and close button', () => {
    const wrapper = mountDialogs()
    const a = document.getElementById('a-modal') as HTMLElement
    expect(a.className.split(' ')).toEqual(['modal', 'modal-wide', 'hidden'])
    expect(a.getAttribute('role')).toBe('dialog')
    expect(a.getAttribute('aria-modal')).toBe('true')
    expect(a.getAttribute('aria-labelledby')).toBe('a-modal-title')
    expect(document.getElementById('a-modal-title')?.textContent).toBe('A dialog')
    expect(a.querySelector('.modal-body #a-field')).not.toBeNull()
    expect(document.getElementById('a-modal-footer')?.classList.contains('modal-footer')).toBe(true)
    expect(document.querySelector('#b-modal > .modal-body.modal-body--scroll')).not.toBeNull()
    expect(document.getElementById('b-modal-footer')).toBeNull()
    expect(ownsDialog('a-modal')).toBe(true)
    expect(ownsDialog('settings-modal')).toBe(false)
    wrapper.unmount()
    expect(ownsDialog('a-modal')).toBe(false)
  })

  it('reflects the open dialog, its generation and inert at once', () => {
    mountDialogs()
    const a = document.getElementById('a-modal') as HTMLElement
    const b = document.getElementById('b-modal') as HTMLElement
    expect(showDialog('a-modal')).toBe(1)
    // Synchronously: classic readers check right after openModal.
    expect(a.classList.contains('hidden')).toBe(false)
    expect(b.classList.contains('hidden')).toBe(true)
    expect(a.dataset.prksOpenGeneration).toBe('1')
    setDialogInert('a-modal', true)
    expect(a.hasAttribute('inert')).toBe(true)
    showDialog('b-modal')
    expect(a.classList.contains('hidden')).toBe(true)
    expect(b.classList.contains('hidden')).toBe(false)
    expect(isDialogOpen('b-modal')).toBe(true)
    hideDialogs()
    expect(b.classList.contains('hidden')).toBe(true)
    expect(a.hasAttribute('inert')).toBe(false)
    expect(dialogState.active).toBeNull()
  })

  it('starts a new opening on show and keeps it on restore', () => {
    mountDialogs()
    const a = document.getElementById('a-modal') as HTMLElement
    showDialog('a-modal')
    showDialog('a-modal')
    expect(a.dataset.prksOpenGeneration).toBe('2')
    hideDialogs()
    restoreDialog('a-modal')
    expect(a.classList.contains('hidden')).toBe(false)
    expect(a.dataset.prksOpenGeneration).toBe('2')
  })

  it('asks the classic close path from its close button', () => {
    mountDialogs()
    const reasons: string[] = []
    window.requestModalClose = (reason?: string) => {
      reasons.push(String(reason))
      return true
    }
    try {
      ;(document.querySelector('#a-modal .close-btn') as HTMLButtonElement).click()
      expect(reasons).toEqual(['button'])
    } finally {
      delete window.requestModalClose
    }
  })
})
