import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CITATION_STYLE_EVENT, resetImportedStylesForTests } from '../citations/styles'
import { settingsActiveCategory } from './navigation'
import SettingsExport from './SettingsExport.vue'

type StyleWindow = Window & {
  __prksCitationStyle?: string
  prksLoadAppSettings?: () => Promise<unknown>
  prksConfirmDestructive?: (options: unknown) => Promise<boolean>
  prksSetCitationStyleCache?: (style: string) => void
  prksPatchAppSettings?: (partial: { citation_style?: string }) => Promise<unknown>
}

const win = window as StyleWindow
let sent: string[]
let answer: (chosen: string) => string

function setCache(style: string): void {
  if (win.__prksCitationStyle === style) return
  win.__prksCitationStyle = style
  window.dispatchEvent(new CustomEvent(CITATION_STYLE_EVENT))
}

const HOUSE = `custom-${'a'.repeat(32)}`
let imported: Array<{ id: string; title: string; created_at: string }>

beforeEach(() => {
  resetImportedStylesForTests()
  settingsActiveCategory.value = 'export'
  imported = []
  vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === 'DELETE') {
      imported = imported.filter((style) => !path.endsWith(style.id))
      return new Response(JSON.stringify({ deleted: HOUSE }))
    }
    return new Response(JSON.stringify({ styles: imported }))
  }))
  sent = []
  answer = (chosen) => chosen
  win.__prksCitationStyle = 'apa'
  win.prksSetCitationStyleCache = setCache
  win.prksPatchAppSettings = vi.fn(async (partial) => {
    const chosen = partial.citation_style ?? ''
    sent.push(chosen)
    setCache(answer(chosen)) // as api.js does with the response
    return { citation_style: answer(chosen) }
  })
})

afterEach(() => {
  settingsActiveCategory.value = 'general'
  vi.unstubAllGlobals()
  delete win.prksLoadAppSettings
  delete win.prksConfirmDestructive
  delete win.__prksCitationStyle
  delete win.prksSetCitationStyleCache
  delete win.prksPatchAppSettings
  document.body.innerHTML = ''
})

async function choose(wrapper: ReturnType<typeof mount>, style: string): Promise<void> {
  const select = wrapper.get('#prks-citation-style')
  ;(select.element as HTMLSelectElement).value = style
  await select.trigger('change')
  await flushPromises()
}

describe('SettingsExport citation style', () => {
  it('shows the style PRKS saved, and sends a choice again after another session changed it', async () => {
    const wrapper = mount(SettingsExport)
    await flushPromises()
    // Another session saved Chicago between this write and its read-back.
    answer = () => 'chicago-author-date'
    await choose(wrapper, 'ieee')
    expect(sent).toEqual(['ieee'])
    expect((wrapper.get('#prks-citation-style').element as HTMLSelectElement).value).toBe('chicago-author-date')
    expect(win.__prksCitationStyle).toBe('chicago-author-date')

    answer = (chosen) => chosen
    await choose(wrapper, 'ieee')
    expect(sent).toEqual(['ieee', 'ieee'])
    expect(win.__prksCitationStyle).toBe('ieee')
    wrapper.unmount()
  })

  it('offers imported styles, and shows the default after the Library style is deleted', async () => {
    imported = [{ id: HOUSE, title: 'House Style', created_at: 'x' }]
    setCache(HOUSE)
    // As api.js does: PRKS answers the default once the Library style is gone.
    win.prksLoadAppSettings = vi.fn(async () => {
      setCache(imported.some((style) => style.id === HOUSE) ? HOUSE : 'apa')
    })
    win.prksConfirmDestructive = vi.fn(async () => true)
    const wrapper = mount(SettingsExport, { attachTo: document.body })
    await flushPromises()
    const select = wrapper.get('#prks-citation-style').element as HTMLSelectElement
    expect([...select.querySelectorAll('optgroup option')].map((option) => option.textContent)).toEqual(['House Style'])
    expect(select.value).toBe(HOUSE)
    await wrapper.get(`[data-prks-style-id="${HOUSE}"] button`).trigger('click')
    await flushPromises()
    expect(select.value).toBe('apa')
    expect(select.querySelector('optgroup')).toBeNull()
    expect(sent).toEqual([])
  })
})
