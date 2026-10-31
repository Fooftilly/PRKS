import { afterEach, describe, expect, it } from 'vitest'

type ComboboxOptions = {
  enterCreates?: boolean
  onOpen?: () => void
  onEscape?: () => void
}

type ComboboxKeys = {
  prksBindComboboxKeys: (input: HTMLInputElement, results: HTMLElement, options?: ComboboxOptions) => void
  prksComboboxPanelIsOpen: (results: HTMLElement) => boolean
  prksComboboxRenderEpoch: (input: HTMLInputElement) => number
  prksComboboxRendered: (input: HTMLInputElement, epoch: number) => void
}

async function loadKeys(): Promise<ComboboxKeys> {
  const nodeModule = 'node:module'
  const { createRequire } = (await import(/* @vite-ignore */ nodeModule)) as {
    createRequire: (url: string) => (id: string) => unknown
  }
  const require = createRequire(import.meta.url)
  return require('../../../frontend/js/combobox-keys.js') as ComboboxKeys
}

function key(input: HTMLInputElement, name: string): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true })
  input.dispatchEvent(event)
  return event
}

async function flush(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0))
}

function harness(options?: ComboboxOptions) {
  const input = document.createElement('input')
  const results = document.createElement('div')
  results.id = 'demo-results'
  results.className = 'combobox-results combobox-results--tag-panel hidden'
  document.body.append(input, results)
  const picked: string[] = []
  const render = (rows: Array<{ text: string; create?: boolean; disabled?: boolean }>) => {
    results.replaceChildren()
    rows.forEach((row) => {
      const el = document.createElement('div')
      el.className = 'result-item' + (row.create ? ' result-item--create' : '')
      el.textContent = row.text
      if (row.disabled) el.setAttribute('aria-disabled', 'true')
      else el.addEventListener('mousedown', () => picked.push(row.text))
      results.append(el)
    })
    results.classList.remove('hidden')
    results.classList.add('is-open')
  }
  return { input, results, picked, render, options }
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('shared combobox keys', () => {
  it('exposes combobox ARIA and moves onto a real match, not the create row', async () => {
    const keys = await loadKeys()
    const box = harness()
    keys.prksBindComboboxKeys(box.input, box.results, { enterCreates: true })
    expect(box.input.getAttribute('role')).toBe('combobox')
    expect(box.input.getAttribute('aria-autocomplete')).toBe('list')
    expect(box.input.getAttribute('aria-controls')).toBe('demo-results')
    expect(box.input.getAttribute('aria-expanded')).toBe('false')
    expect(box.results.getAttribute('role')).toBe('listbox')

    box.render([
      { text: 'Create tag "Al"', create: true },
      { text: 'Alpha' },
      { text: 'Alpine' },
    ])
    await flush()
    key(box.input, 'ArrowDown')
    const active = box.results.querySelector('.result-item--active')
    expect(active?.textContent).toBe('Alpha')
    expect(active?.getAttribute('role')).toBe('option')
    expect(active?.getAttribute('aria-selected')).toBe('true')
    expect(box.input.getAttribute('aria-activedescendant')).toBe(active?.id)
    expect(box.input.getAttribute('aria-expanded')).toBe('true')

    key(box.input, 'Enter')
    expect(box.picked).toEqual(['Alpha'])
  })

  it('Enter on a typed query selects the first real row, or creates when that is the only row', async () => {
    const keys = await loadKeys()
    const box = harness()
    keys.prksBindComboboxKeys(box.input, box.results, { enterCreates: true })
    box.input.value = 'Alpha'
    box.render([
      { text: 'Create tag "Alpha"', create: true },
      { text: 'Alpha' },
    ])
    await flush()
    key(box.input, 'Enter')
    expect(box.picked).toEqual(['Alpha'])

    box.picked.length = 0
    box.input.value = 'inbox-1023'
    box.render([{ text: 'Create tag "inbox-1023"', create: true }])
    await flush()
    key(box.input, 'Enter')
    expect(box.picked).toEqual(['Create tag "inbox-1023"'])
  })

  it('ignores Enter while the open rows are stale, then picks the rendered row', async () => {
    const keys = await loadKeys()
    const box = harness()
    keys.prksBindComboboxKeys(box.input, box.results, { enterCreates: true })
    box.input.value = 'Alpha'
    box.render([{ text: 'Alpha' }])
    await flush()
    box.input.value = 'Beta'
    box.input.dispatchEvent(new Event('input', { bubbles: true }))
    const stale = key(box.input, 'Enter')
    expect(stale.defaultPrevented).toBe(true)
    expect(box.picked).toEqual([])

    box.render([{ text: 'Beta' }])
    await flush()
    key(box.input, 'Enter')
    expect(box.picked).toEqual(['Beta'])
  })

  it('skips a blocked row, follows the pointer, and Escape closes the list', async () => {
    const keys = await loadKeys()
    let closed = 0
    const box = harness({
      onEscape: () => {
        closed += 1
        box.results.classList.remove('is-open')
        box.results.classList.add('hidden')
      },
    })
    keys.prksBindComboboxKeys(box.input, box.results, box.options)
    box.input.value = 'A'
    box.render([
      { text: 'Blocked', disabled: true },
      { text: 'Alpha' },
      { text: 'Alpine' },
    ])
    await flush()
    key(box.input, 'ArrowDown')
    expect(box.results.querySelector('.result-item--active')?.textContent).toBe('Alpha')
    box.results.querySelectorAll('.result-item')[2].dispatchEvent(new MouseEvent('mouseover', { bubbles: true }))
    expect(box.results.querySelector('.result-item--active')?.textContent).toBe('Alpine')
    key(box.input, 'Enter')
    expect(box.picked).toEqual(['Alpine'])

    box.render([{ text: 'Alpha' }])
    await flush()
    const escape = key(box.input, 'Escape')
    expect(escape.defaultPrevented).toBe(true)
    expect(closed).toBe(1)
    expect(keys.prksComboboxPanelIsOpen(box.results)).toBe(false)
    expect(box.input.getAttribute('aria-expanded')).toBe('false')
    expect(box.input.hasAttribute('aria-activedescendant')).toBe(false)
    key(box.input, 'Enter')
    expect(box.picked).toEqual(['Alpine'])
  })

  it('ArrowDown opens a closed list and lands on the first real row', async () => {
    const keys = await loadKeys()
    const box = harness()
    let opened = 0
    keys.prksBindComboboxKeys(box.input, box.results, {
      enterCreates: true,
      onOpen: () => {
        opened += 1
        box.render([
          { text: 'Quick-create person "Ada"', create: true },
          { text: 'Ada Lovelace' },
        ])
      },
    })
    key(box.input, 'ArrowDown')
    await flush()
    expect(opened).toBe(1)
    expect(box.results.querySelector('.result-item--active')?.textContent).toBe('Ada Lovelace')
  })

  it('gives existing rows option semantics before a key is pressed', async () => {
    const keys = await loadKeys()
    const box = harness()
    const match = document.createElement('div')
    match.className = 'result-item'
    match.textContent = 'Ada Lovelace'
    box.results.append(match)
    keys.prksBindComboboxKeys(box.input, box.results)
    expect(match.getAttribute('role')).toBe('option')
    expect(match.getAttribute('aria-selected')).toBe('false')
    expect(box.input.hasAttribute('aria-activedescendant')).toBe(false)

    box.results.classList.remove('hidden')
    box.results.classList.add('is-open')
    await flush()
    expect(match.getAttribute('role')).toBe('option')
    expect(match.getAttribute('aria-selected')).toBe('false')
  })

  it('accepts Enter after a render that reuses the same row', async () => {
    const keys = await loadKeys()
    const box = harness({ enterCreates: true })
    keys.prksBindComboboxKeys(box.input, box.results, box.options)
    box.render([{ text: 'Archive' }])
    await flush()
    box.input.value = 'a'
    box.input.dispatchEvent(new Event('input', { bubbles: true }))
    const blocked = key(box.input, 'Enter')
    expect(blocked.defaultPrevented).toBe(true)
    expect(box.picked).toEqual([])

    const staleEpoch = keys.prksComboboxRenderEpoch(box.input)
    box.input.value = 'ar'
    box.input.dispatchEvent(new Event('input', { bubbles: true }))
    keys.prksComboboxRendered(box.input, staleEpoch)
    expect(key(box.input, 'Enter').defaultPrevented).toBe(true)
    expect(box.picked).toEqual([])

    keys.prksComboboxRendered(box.input, keys.prksComboboxRenderEpoch(box.input))
    key(box.input, 'Enter')
    expect(box.picked).toEqual(['Archive'])
  })
})
