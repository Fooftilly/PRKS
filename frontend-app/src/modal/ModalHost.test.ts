import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import ModalHost from './ModalHost.vue'
import type { PrksModalHostApi } from './types'

function host(): PrksModalHostApi {
  const api = window.prksModalHost
  if (!api) throw new Error('modal host is not installed')
  return api
}

async function settleFocus(): Promise<void> {
  await nextTick()
  await new Promise<void>((resolve) => {
    requestAnimationFrame(() => resolve())
  })
}

function pressEscape(): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
  document.dispatchEvent(event)
  return event
}

function pressTab(shiftKey = false): KeyboardEvent {
  const event = new KeyboardEvent('keydown', {
    key: 'Tab',
    bubbles: true,
    cancelable: true,
    shiftKey,
  })
  document.dispatchEvent(event)
  return event
}

describe('Vue modal host', () => {
  const mounted: Array<{ unmount: () => void }> = []

  const originalMatchMedia = window.matchMedia

  afterEach(() => {
    for (const wrapper of mounted.splice(0)) wrapper.unmount()
    document.body.innerHTML = ''
    document.body.className = ''
    document.documentElement.classList.remove('prks-force-mobile')
    window.matchMedia = originalMatchMedia
    delete window.prksModalHost
    delete window.requestModalClose
    delete window.prksDismissModalInnerEscapeLayer
    delete window.prksCloseStandalonePageModal
    delete window.prksWorkspaceSnapshot
  })

  function install(): void {
    mounted.push(mount(ModalHost, { attachTo: document.body }))
  }

  it('resolves confirm from OK and keeps the dialog labelled', async () => {
    install()
    const pending = host().confirm({
      title: 'Delete file?',
      message: 'This file will be removed.',
      confirmLabel: 'Delete',
      danger: true,
    })
    await settleFocus()
    const dialog = document.getElementById('prks-modal-confirm')
    expect(dialog?.classList.contains('hidden')).toBe(false)
    expect(dialog?.getAttribute('role')).toBe('alertdialog')
    expect(dialog?.getAttribute('aria-modal')).toBe('true')
    expect(dialog?.getAttribute('aria-labelledby')).toBe('prks-modal-confirm-title')
    expect(dialog?.getAttribute('aria-hidden')).toBe('false')
    expect(document.getElementById('prks-modal-confirm-title')?.textContent).toBe('Delete file?')
    expect(document.getElementById('prks-modal-confirm-desc')?.textContent).toBe('This file will be removed.')
    expect(document.getElementById('prks-modal-confirm-ok')?.textContent).toBe('Delete')
    expect(document.getElementById('prks-modal-confirm-ok')?.classList.contains('prks-btn--danger')).toBe(true)
    document.getElementById('prks-modal-confirm-ok')?.click()
    await expect(pending).resolves.toBe(true)
    expect(dialog?.classList.contains('hidden')).toBe(true)
    expect(dialog?.getAttribute('aria-hidden')).toBe('true')
    expect(host().isConfirmOpen()).toBe(false)
  })

  it('focuses Cancel for a destructive confirm and OK otherwise', async () => {
    install()
    const danger = host().confirm({ title: 'Delete annotation?', danger: true, confirmLabel: 'Delete' })
    await settleFocus()
    expect(document.activeElement).toBe(document.getElementById('prks-modal-confirm-cancel'))
    document.getElementById('prks-modal-confirm-cancel')?.click()
    await danger

    const plain = host().confirm({ title: 'Continue?', danger: false })
    await settleFocus()
    expect(document.activeElement).toBe(document.getElementById('prks-modal-confirm-ok'))
    document.getElementById('prks-modal-confirm-ok')?.click()
    await plain
  })

  it('lets Escape, the scrim, and Cancel dismiss a confirm without closing the feature modal', async () => {
    install()
    document.body.insertAdjacentHTML(
      'beforeend',
      '<div id="folder-modal" class="modal"><button type="button" id="folder-title-btn">Title</button></div>',
    )
    const ways = ['escape', 'scrim', 'cancel'] as const
    for (const way of ways) {
      const pending = host().confirm({ title: 'Confirm', message: 'Proceed?' })
      await settleFocus()
      if (way === 'escape') pressEscape()
      if (way === 'scrim') document.getElementById('prks-modal-confirm-scrim')?.click()
      if (way === 'cancel') document.getElementById('prks-modal-confirm-cancel')?.click()
      await expect(pending).resolves.toBe(false)
      expect(document.getElementById('folder-modal')?.classList.contains('hidden')).toBe(false)
      expect(host().isConfirmOpen()).toBe(false)
    }
  })

  it('shows an alert as one button, focuses OK, and treats Escape as acknowledgement', async () => {
    install()
    const pending = host().confirm({
      title: 'Offline',
      message: 'PRKS is not reachable.',
      confirmLabel: 'OK',
      alertOnly: true,
    })
    await settleFocus()
    const cancel = document.getElementById('prks-modal-confirm-cancel')
    expect(cancel?.classList.contains('hidden')).toBe(true)
    expect(cancel?.getAttribute('aria-hidden')).toBe('true')
    expect(document.querySelector('.prks-modal-confirm__actions')?.classList.contains('prks-modal-confirm__actions--alertOnly')).toBe(true)
    expect(document.activeElement).toBe(document.getElementById('prks-modal-confirm-ok'))
    const event = pressEscape()
    expect(event.defaultPrevented).toBe(true)
    await expect(pending).resolves.toBe(true)
  })

  it('renders trusted confirm HTML and leaves plain text unparsed', async () => {
    install()
    const rich = host().confirm({
      title: 'Help',
      messageHtml: '<strong class="prks-help-accent">Keep</strong>',
      alertOnly: true,
    })
    await nextTick()
    const desc = document.getElementById('prks-modal-confirm-desc')
    expect(desc?.classList.contains('prks-modal-confirm__desc--rich')).toBe(true)
    expect(desc?.querySelector('.prks-help-accent')?.textContent).toBe('Keep')
    document.getElementById('prks-modal-confirm-ok')?.click()
    await rich

    const plain = host().confirm({ title: 'Notice', message: '<b>nope</b>' })
    await nextTick()
    const text = document.getElementById('prks-modal-confirm-desc')
    expect(text?.textContent).toBe('<b>nope</b>')
    expect(text?.querySelector('b')).toBeNull()
    document.getElementById('prks-modal-confirm-cancel')?.click()
    await plain
  })

  it('returns trimmed prompt text, null on cancel, and submits a single line with Enter', async () => {
    install()
    const saved = host().prompt({
      title: 'Name on this file',
      message: 'Profile name: Ada.',
      defaultValue: '  Ada Lovelace  ',
      okLabel: 'Save',
    })
    await settleFocus()
    const input = document.querySelector('.prks-modal-prompt__input')
    expect(input).toBeInstanceOf(HTMLInputElement)
    expect(input?.getAttribute('aria-label')).toBe('Name on this file')
    expect(document.querySelector('.prks-modal-prompt__hint')?.textContent).toBe('Profile name: Ada.')
    expect(document.activeElement).toBe(input)
    expect((input as HTMLInputElement).value).toBe('  Ada Lovelace  ')
    input?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    await expect(saved).resolves.toBe('Ada Lovelace')
    expect(document.querySelector('.prks-modal-prompt__input')).toBeNull()

    const cancelled = host().prompt({ title: 'Edit', defaultValue: 'keep' })
    await settleFocus()
    pressEscape()
    await expect(cancelled).resolves.toBeNull()

    const multiline = host().prompt({
      title: 'Definition',
      defaultValue: 'line',
      multiline: true,
      rows: 8,
    })
    let multilineSettled = false
    void multiline.then(
      () => {
        multilineSettled = true
      },
      () => {
        multilineSettled = true
      },
    )
    await settleFocus()
    const area = document.querySelector('.prks-modal-prompt__input')
    expect(area).toBeInstanceOf(HTMLTextAreaElement)
    expect(area?.classList.contains('prks-modal-prompt__input--multiline')).toBe(true)
    expect((area as HTMLTextAreaElement).rows).toBe(8)
    area?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    await nextTick()
    await Promise.resolve()
    expect(host().isConfirmOpen()).toBe(true)
    expect(multilineSettled).toBe(false)
    document.getElementById('prks-modal-confirm-cancel')?.click()
    await expect(multiline).resolves.toBeNull()
  })

  it('stacks unsaved dismissal above a feature modal and focuses Cancel', async () => {
    install()
    const opener = document.createElement('button')
    opener.id = 'folder-opener'
    opener.type = 'button'
    document.body.appendChild(opener)
    opener.focus()
    document.body.insertAdjacentHTML(
      'beforeend',
      '<div id="folder-modal" class="modal"><button type="button" id="inside">Inside</button></div>',
    )
    let discarded = 0
    host().openUnsavedConfirm(() => {
      discarded += 1
    })
    await settleFocus()
    const dialog = document.getElementById('prks-modal-unsaved-confirm')
    expect(dialog?.classList.contains('hidden')).toBe(false)
    expect(dialog?.getAttribute('role')).toBe('alertdialog')
    expect(document.getElementById('prks-modal-unsaved-confirm-title')?.textContent).toBe('Close without saving?')
    expect(document.getElementById('prks-modal-unsaved-confirm-discard')?.classList.contains('prks-btn--danger')).toBe(true)
    expect(document.activeElement?.id).toBe('prks-modal-unsaved-confirm-cancel')
    pressEscape()
    await nextTick()
    expect(dialog?.classList.contains('hidden')).toBe(true)
    expect(document.getElementById('folder-modal')?.classList.contains('hidden')).toBe(false)
    expect(discarded).toBe(0)
    expect(document.activeElement).toBe(opener)

    opener.focus()
    host().openUnsavedConfirm(() => {
      discarded += 1
    })
    await settleFocus()
    document.getElementById('prks-modal-unsaved-confirm-scrim')?.click()
    await nextTick()
    expect(host().isUnsavedConfirmOpen()).toBe(false)
    expect(discarded).toBe(0)

    host().openUnsavedConfirm(() => {
      discarded += 1
    })
    await settleFocus()
    document.getElementById('prks-modal-unsaved-confirm-discard')?.click()
    await nextTick()
    expect(discarded).toBe(1)
    expect(host().isUnsavedConfirmOpen()).toBe(false)
  })

  it('does not restore unsaved focus when the feature shell asks it not to', async () => {
    install()
    const opener = document.createElement('button')
    opener.type = 'button'
    document.body.appendChild(opener)
    opener.focus()
    host().openUnsavedConfirm(null)
    await settleFocus()
    const other = document.createElement('button')
    other.type = 'button'
    document.body.appendChild(other)
    other.focus()
    host().hideUnsavedConfirm({ restoreFocus: false })
    await nextTick()
    expect(document.activeElement).toBe(other)
    expect(host().isUnsavedConfirmOpen()).toBe(false)
  })

  it('cycles Tab inside the top dialog and not into the feature modal', async () => {
    install()
    document.body.insertAdjacentHTML(
      'beforeend',
      '<div id="work-modal" class="modal"><button type="button" id="work-outside">Outside</button><button type="button" id="work-last">Last</button></div>',
    )
    const pending = host().confirm({ title: 'Confirm', danger: false })
    await settleFocus()
    const ok = document.getElementById('prks-modal-confirm-ok')
    const cancel = document.getElementById('prks-modal-confirm-cancel')
    ok?.focus()
    const forward = pressTab(false)
    expect(forward.defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(cancel)
    const backward = pressTab(true)
    expect(backward.defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(ok)
    expect(document.activeElement).not.toBe(document.getElementById('work-outside'))
    document.getElementById('prks-modal-confirm-cancel')?.click()
    await pending
  })

  it('settles a replaced confirm as cancelled so it cannot commit later', async () => {
    install()
    const first = host().confirm({ title: 'First' })
    const second = host().confirm({ title: 'Second', message: 'Later' })
    await expect(first).resolves.toBe(false)
    await nextTick()
    expect(document.getElementById('prks-modal-confirm-title')?.textContent).toBe('Second')
    document.getElementById('prks-modal-confirm-ok')?.click()
    await expect(second).resolves.toBe(true)
  })

  it('routes feature-modal Escape through the shared lifecycle', async () => {
    install()
    const reasons: string[] = []
    window.requestModalClose = (reason?: string) => {
      reasons.push(String(reason))
      return true
    }
    document.body.classList.add('prks-workspace-dragging')
    document.body.insertAdjacentHTML('beforeend', '<div id="settings-modal" class="modal"></div>')
    const dragged = pressEscape()
    expect(dragged.defaultPrevented).toBe(false)
    expect(reasons).toEqual([])
    document.body.classList.remove('prks-workspace-dragging')

    let inner = 0
    window.prksDismissModalInnerEscapeLayer = () => {
      inner += 1
      return true
    }
    const layered = pressEscape()
    expect(layered.defaultPrevented).toBe(true)
    expect(inner).toBe(1)
    expect(reasons).toEqual([])
    window.prksDismissModalInnerEscapeLayer = () => false

    let closedStandalone = 0
    window.prksCloseStandalonePageModal = () => {
      closedStandalone += 1
      return true
    }
    pressEscape()
    expect(closedStandalone).toBe(1)
    expect(reasons).toEqual([])
    window.prksCloseStandalonePageModal = () => false

    pressEscape()
    expect(reasons).toEqual(['escape'])

    const pending = host().confirm({ title: 'Above' })
    await nextTick()
    reasons.length = 0
    pressEscape()
    await expect(pending).resolves.toBe(false)
    expect(reasons).toEqual([])
  })

  it('traps Tab inside an open feature modal on every layout', async () => {
    install()
    document.body.insertAdjacentHTML(
      'beforeend',
      '<div id="person-modal" class="modal"><button type="button" id="person-first">First</button><button type="button" id="person-last">Last</button></div>',
    )
    const last = document.getElementById('person-last')
    last?.focus()
    const event = pressTab(false)
    expect(event.defaultPrevented).toBe(true)
    expect(document.activeElement?.id).toBe('person-first')
  })

  it('keeps unsaved dismissal the same on a narrow and a wide viewport', async () => {
    install()
    const opener = document.createElement('button')
    opener.type = 'button'
    document.body.appendChild(opener)
    document.body.insertAdjacentHTML(
      'beforeend',
      '<div id="folder-modal" class="modal"><input id="folder-title" value="Draft"></div>',
    )
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
      opener.focus()
      let discarded = 0
      host().openUnsavedConfirm(() => {
        discarded += 1
      })
      await settleFocus()
      expect(host().isUnsavedConfirmOpen()).toBe(true)
      expect(document.activeElement?.id).toBe('prks-modal-unsaved-confirm-cancel')
      pressEscape()
      await nextTick()
      expect(host().isUnsavedConfirmOpen()).toBe(false)
      expect(discarded).toBe(0)
      expect(document.getElementById('folder-modal')?.classList.contains('hidden')).toBe(false)
      expect((document.getElementById('folder-title') as HTMLInputElement).value).toBe('Draft')
    }
  })

  it('restores annotation Delete on the drawer that opened the confirm', async () => {
    install()
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
    window.prksWorkspaceSnapshot = () => ({ focusedTabId: 'side' })
    side.button.focus()
    const pending = host().confirm({ title: 'Delete annotation?', danger: true, confirmLabel: 'Delete' })
    await settleFocus()
    side.button.remove()
    const fresh = document.createElement('button')
    fresh.type = 'button'
    fresh.className = 'annotation-row__delete'
    fresh.textContent = 'side replacement'
    side.aside.appendChild(fresh)
    document.getElementById('prks-modal-confirm-cancel')?.click()
    await pending
    expect(document.activeElement).toBe(fresh)
    expect(document.activeElement).not.toBe(main.button)
  })

  it('does not move focus into a tile that no longer owns the workspace', async () => {
    install()
    const tile = document.createElement('div')
    tile.className = 'prks-tile'
    tile.setAttribute('data-prks-tab-id', 'side')
    const opener = document.createElement('button')
    opener.type = 'button'
    opener.textContent = 'Side'
    tile.appendChild(opener)
    document.body.appendChild(tile)
    window.prksWorkspaceSnapshot = () => ({ focusedTabId: 'main' })
    opener.focus()
    const pending = host().confirm({ title: 'Delete annotation?', danger: true })
    await settleFocus()
    document.getElementById('prks-modal-confirm-cancel')?.click()
    await pending
    expect(document.activeElement).not.toBe(opener)
  })
})
