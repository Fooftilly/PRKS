import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import engineSource from '../../../frontend/js/command-palette.js?raw'
import CommandPalette from './CommandPalette.vue'
import type { PaletteRowView, PaletteView } from './types'

function row(partial: Partial<PaletteRowView> & Pick<PaletteRowView, 'index' | 'label'>): PaletteRowView {
  return {
    id: 'row-' + partial.index,
    subtitle: '',
    icon: 'folder',
    section: 'goto',
    heading: '',
    ...partial,
  }
}

function view(partial: Partial<PaletteView> = {}): PaletteView {
  return {
    open: false,
    title: 'Search or jump',
    placeholder: 'Search or jump…',
    query: '',
    scope: 'all',
    navigationTarget: 'current',
    createMode: false,
    activeIndex: 0,
    activeDescendant: '',
    rows: [],
    status: '',
    hints: '',
    showHints: false,
    revision: 0,
    ...partial,
  }
}

async function settleFocus(): Promise<void> {
  await nextTick()
  await new Promise<void>((resolve) => {
    requestAnimationFrame(() => resolve())
  })
}

describe('command palette dialog', () => {
  const mounted: Array<{ unmount: () => void }> = []
  let current = view()
  let listener: ((next: PaletteView) => void) | null = null
  const keys: KeyboardEvent[] = []
  const queries: string[] = []
  const pointers: Array<{ index: number; altKey?: boolean; ctrlKey?: boolean }> = []
  let closes = 0

  afterEach(() => {
    for (const wrapper of mounted.splice(0)) wrapper.unmount()
    document.body.innerHTML = ''
    listener = null
    keys.length = 0
    queries.length = 0
    pointers.length = 0
    closes = 0
    current = view()
    delete window.prksCommandPaletteView
    delete window.prksCommandPaletteSubscribe
    delete window.prksCommandPaletteSetQuery
    delete window.prksCommandPaletteHandleKey
    delete window.prksCommandPaletteActivateFromPointer
    delete window.prksCloseCommandPalette
    delete window.prksIcon
    delete window.prksRefreshIcons
  })

  function publish(next: PaletteView): void {
    current = next
    listener?.(next)
  }

  function install(): void {
    window.prksIcon = (name) => `<i data-lucide="${name}"></i>`
    window.prksRefreshIcons = () => {}
    window.prksCommandPaletteView = () => current
    window.prksCommandPaletteSubscribe = (fn) => {
      listener = fn
      return () => {
        if (listener === fn) listener = null
      }
    }
    window.prksCommandPaletteSetQuery = (raw) => {
      queries.push(raw)
    }
    window.prksCommandPaletteHandleKey = (event) => {
      keys.push(event)
    }
    window.prksCommandPaletteActivateFromPointer = (index, event) => {
      pointers.push({ index, altKey: event?.altKey, ctrlKey: event?.ctrlKey })
    }
    window.prksCloseCommandPalette = () => {
      closes += 1
    }
    mounted.push(mount(CommandPalette, { attachTo: document.body }))
  }

  it('keeps the dialog hidden until the engine opens it', async () => {
    install()
    await nextTick()
    const dialog = document.getElementById('prks-command-palette')
    expect(dialog?.hidden).toBe(true)
    expect(dialog?.getAttribute('role')).toBe('dialog')
    expect(dialog?.getAttribute('aria-modal')).toBe('true')
    expect(dialog?.getAttribute('aria-labelledby')).toBe('prks-command-palette-title')
    const status = document.getElementById('prks-command-palette-status')
    expect(status?.getAttribute('role')).toBe('status')
    expect(status?.getAttribute('aria-live')).toBe('polite')
    expect(status?.hidden).toBe(false)
    expect(status?.hasAttribute('hidden')).toBe(false)
    expect(status?.textContent).toBe('')
    expect(status?.matches(':empty')).toBe(true)
    expect(document.getElementById('prks-command-palette-results')?.getAttribute('role')).toBe('listbox')
    expect(document.querySelector('[role="option"]')).toBeNull()
  })

  it('paints rows as elements and focuses the input when opened', async () => {
    const opener = document.createElement('button')
    opener.id = 'prks-command-palette-launch'
    document.body.appendChild(opener)
    opener.focus()
    install()
    publish(
      view({
        open: true,
        title: 'Open in split view',
        placeholder: 'Open in split view…',
        revision: 1,
        activeIndex: 1,
        activeDescendant: 'prks-palette-opt-1',
        status: 'Searching library…',
        hints: 'Here · Work A   ·   Enter  Open',
        showHints: true,
        rows: [
          row({ index: 0, id: 'navigate-folders', label: 'Folders', heading: 'Go to', icon: 'folder' }),
          row({
            index: 1,
            id: 'open-work-1',
            label: '<img src=x onerror=alert(1)>',
            subtitle: 'Author · 1991',
            heading: 'Quick open',
            section: 'open',
            icon: 'file-text',
          }),
        ],
      }),
    )
    await settleFocus()
    const dialog = document.getElementById('prks-command-palette')
    expect(dialog?.hidden).toBe(false)
    expect(document.getElementById('prks-command-palette-title')?.textContent).toBe('Open in split view')
    const input = document.getElementById('prks-command-palette-input')
    expect(input).toBeInstanceOf(HTMLInputElement)
    expect(document.activeElement).toBe(input)
    expect(input?.getAttribute('role')).toBe('combobox')
    expect(input?.getAttribute('aria-labelledby')).toBe('prks-command-palette-title')
    expect(input?.getAttribute('aria-expanded')).toBe('true')
    expect(input?.getAttribute('aria-activedescendant')).toBe('prks-palette-opt-1')
    expect(input?.getAttribute('placeholder')).toBe('Open in split view…')
    const options = document.querySelectorAll('[role="option"]')
    expect(options).toHaveLength(2)
    expect(options[1]?.textContent).toContain('<img src=x onerror=alert(1)>')
    expect(options[1]?.querySelector('img')).toBeNull()
    expect(options[1]?.classList.contains('is-active')).toBe(true)
    expect(options[1]?.getAttribute('aria-selected')).toBe('true')
    expect(options[0]?.getAttribute('aria-selected')).toBe('false')
    expect(document.querySelector('.prks-command-palette__heading')?.textContent).toBe('Go to')
    expect(document.querySelector('.prks-command-palette__sub')?.textContent).toBe('Author · 1991')
    const status = document.getElementById('prks-command-palette-status')
    expect(status?.textContent).toBe('Searching library…')
    expect(status?.hidden).toBe(false)
    expect(document.getElementById('prks-command-palette-hints')?.hidden).toBe(false)
    expect(document.getElementById('prks-command-palette-hints')?.textContent).toContain('Here · Work A')
    expect(dialog?.querySelector('#prks-command-palette-results')?.innerHTML.startsWith('<')).toBe(true)
    expect(opener.getAttribute('data-palette-bound')).toBeNull()
  })

  it('forwards keys, the query, scrim dismissal, and pointer modifiers', async () => {
    install()
    publish(
      view({
        open: true,
        revision: 2,
        activeDescendant: 'prks-palette-opt-0',
        rows: [row({ index: 0, label: 'Folders', heading: 'Go to' })],
      }),
    )
    await settleFocus()
    const input = document.getElementById('prks-command-palette-input')
    expect(input).toBeInstanceOf(HTMLInputElement)
    const field = input as HTMLInputElement
    field.value = 'fol'
    field.dispatchEvent(new Event('input', { bubbles: true }))
    expect(queries).toEqual(['fol'])
    field.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }))
    field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, ctrlKey: true }))
    field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(keys.map((event) => event.key)).toEqual(['ArrowDown', 'Enter', 'Escape'])
    expect(keys[1]?.ctrlKey).toBe(true)
    document.querySelector('.prks-command-palette__scrim')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(closes).toBe(1)
    const option = document.querySelector('[role="option"]')
    option?.dispatchEvent(new MouseEvent('click', { bubbles: true, altKey: true }))
    option?.dispatchEvent(new MouseEvent('click', { bubbles: true, ctrlKey: true }))
    expect(pointers).toEqual([
      { index: 0, altKey: true, ctrlKey: false },
      { index: 0, altKey: false, ctrlKey: true },
    ])
  })

  it('hides create hints and leaves the status exposed when the list is empty', async () => {
    install()
    publish(
      view({
        open: true,
        scope: 'create',
        createMode: true,
        title: 'Create…',
        placeholder: 'Create…',
        revision: 3,
        status: 'No create command matches.',
        showHints: false,
        hints: '',
      }),
    )
    await nextTick()
    const dialog = document.getElementById('prks-command-palette')
    expect(dialog?.classList.contains('prks-command-palette--create')).toBe(true)
    expect(document.getElementById('prks-command-palette-hints')?.hidden).toBe(true)
    const status = document.getElementById('prks-command-palette-status')
    expect(status?.textContent).toBe('No create command matches.')
    expect(status?.hidden).toBe(false)
    expect(document.querySelector('[role="option"]')).toBeNull()
  })

  it('shows a view that was already open before the dialog mounted', async () => {
    current = view({
      open: true,
      title: 'Search or jump',
      revision: 4,
      activeDescendant: 'prks-palette-opt-0',
      rows: [row({ index: 0, label: 'Recent', heading: 'Go to', icon: 'clock' })],
    })
    window.prksIcon = (name) => `<i data-lucide="${name}"></i>`
    window.prksCommandPaletteView = () => current
    window.prksCommandPaletteSubscribe = (fn) => {
      listener = fn
      return () => {
        if (listener === fn) listener = null
      }
    }
    mounted.push(mount(CommandPalette, { attachTo: document.body }))
    await settleFocus()
    expect(document.getElementById('prks-command-palette')?.hidden).toBe(false)
    expect(document.querySelector('.prks-command-palette__option-label')?.textContent).toBe('Recent')
    expect(document.activeElement).toBe(document.getElementById('prks-command-palette-input'))
  })
})

describe('command palette dialog with the real engine', () => {
  const mounted: Array<{ unmount: () => void }> = []
  const engine = window as Window & {
    prksInitCommandPalette?: () => void
    prksCommandPaletteResetForTests?: () => void
    prksCommandPaletteIsOpen?: () => boolean
  }

  beforeAll(() => {
    const run = new Function(engineSource) as () => void
    run()
    engine.prksInitCommandPalette?.()
  })

  afterEach(() => {
    engine.prksCommandPaletteResetForTests?.()
    for (const wrapper of mounted.splice(0)) wrapper.unmount()
    document.body.innerHTML = ''
  })

  async function openFrom(launcher: HTMLButtonElement): Promise<void> {
    launcher.focus()
    mounted.push(mount(CommandPalette, { attachTo: document.body }))
    expect(window.prksOpenCommandPalette?.()).toBe(true)
    await settleFocus()
    expect(document.activeElement?.id).toBe('prks-command-palette-input')
    expect(document.getElementById('prks-command-palette')?.hidden).toBe(false)
  }

  function launcher(): HTMLButtonElement {
    const button = document.createElement('button')
    button.id = 'opener'
    button.setAttribute('data-palette-bound', '1')
    document.body.appendChild(button)
    return button
  }

  it('Escape on the input closes through the engine and restores the opener', async () => {
    const opener = launcher()
    await openFrom(opener)
    document.getElementById('prks-command-palette-input')?.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
    )
    await nextTick()
    expect(engine.prksCommandPaletteIsOpen?.()).toBe(false)
    expect(document.getElementById('prks-command-palette')?.hidden).toBe(true)
    expect(document.activeElement).toBe(opener)
  })

  it('the scrim dismisses through the engine and restores the opener', async () => {
    const opener = launcher()
    await openFrom(opener)
    document.querySelector('.prks-command-palette__scrim')?.dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    )
    await nextTick()
    expect(engine.prksCommandPaletteIsOpen?.()).toBe(false)
    expect(document.getElementById('prks-command-palette')?.hidden).toBe(true)
    expect(document.activeElement).toBe(opener)
  })
})
