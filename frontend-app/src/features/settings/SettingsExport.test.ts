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

describe('SettingsExport BibTeX toggles', () => {
  type BibtexWindow = Window & {
    PRKS_BIBTEX_EXPORT_FIELD_DEFS?: Array<readonly [string, string]>
    __prksBibtexExportFields?: Record<string, boolean>
    prksSetBibtexExportFieldsCache?: (map: Record<string, boolean>) => void
  }
  const bib = window as BibtexWindow
  let patches: Array<Record<string, boolean>>
  /* PRKS as another tab left it: Author already turned off there. */
  let server: Record<string, boolean>

  beforeEach(() => {
    vi.useFakeTimers()
    patches = []
    server = { author: false, doi: true, url: true }
    bib.PRKS_BIBTEX_EXPORT_FIELD_DEFS = [['author', 'Author'], ['doi', 'DOI'], ['url', 'URL']]
    bib.__prksBibtexExportFields = { author: true, doi: true, url: true }
    bib.prksSetBibtexExportFieldsCache = (map) => { bib.__prksBibtexExportFields = { ...map } }
    win.prksLoadAppSettings = vi.fn(async () => undefined)
    win.prksPatchAppSettings = vi.fn(async (partial: { bibtex_export_fields?: Record<string, boolean> }) => {
      const patch = partial.bibtex_export_fields ?? {}
      patches.push({ ...patch })
      server = { ...server, ...patch }
      bib.prksSetBibtexExportFieldsCache?.(server) // as api.js does with the response
      return { bibtex_export_fields: server }
    }) as StyleWindow['prksPatchAppSettings']
  })

  afterEach(() => {
    vi.useRealTimers()
    delete bib.PRKS_BIBTEX_EXPORT_FIELD_DEFS
    delete bib.__prksBibtexExportFields
    delete bib.prksSetBibtexExportFieldsCache
  })

  const isOn = (wrapper: ReturnType<typeof mount>, id: string) =>
    wrapper.get(`[data-bibtex-field="${id}"]`).attributes('aria-checked') === 'true'

  it('sends only the toggles this page changed, so another tab’s choice survives (#889)', async () => {
    const wrapper = mount(SettingsExport)
    await flushPromises()
    expect(isOn(wrapper, 'author')).toBe(true) // this tab's stale view
    await wrapper.get('[data-bibtex-field="doi"]').trigger('click')
    await wrapper.get('[data-bibtex-field="url"]').trigger('click')
    await wrapper.get('[data-bibtex-field="url"]').trigger('click')
    expect(patches).toEqual([])
    vi.advanceTimersByTime(450)
    await flushPromises()
    expect(patches).toEqual([{ doi: false, url: true }])
    expect(server).toEqual({ author: false, doi: false, url: true })
    expect(isOn(wrapper, 'author')).toBe(false)
    expect(isOn(wrapper, 'doi')).toBe(false)
    wrapper.unmount()
  })

  it('sends a toggle still waiting for its debounce when the page closes', async () => {
    const wrapper = mount(SettingsExport)
    await flushPromises()
    await wrapper.get('[data-bibtex-field="url"]').trigger('click')
    wrapper.unmount()
    await flushPromises()
    expect(patches).toEqual([{ url: false }])
  })

  /* PRKS answers each PATCH only when the test releases it. */
  function holdPatches(): Array<() => void> {
    const held: Array<() => void> = []
    const patch = win.prksPatchAppSettings!
    win.prksPatchAppSettings = vi.fn((partial) => {
      return new Promise((resolve) => {
        held.push(() => resolve(patch(partial)))
      })
    }) as StyleWindow['prksPatchAppSettings']
    return held
  }

  it('sends one toggle patch at a time, so an older one never lands last', async () => {
    const held = holdPatches()
    const wrapper = mount(SettingsExport)
    await flushPromises()
    await wrapper.get('[data-bibtex-field="url"]').trigger('click')
    vi.advanceTimersByTime(450)
    await flushPromises()
    await wrapper.get('[data-bibtex-field="url"]').trigger('click')
    vi.advanceTimersByTime(450)
    await flushPromises()
    expect(held).toHaveLength(1)
    held[0]()
    await flushPromises()
    expect(held).toHaveLength(2)
    held[1]()
    await flushPromises()
    expect(patches).toEqual([{ url: false }, { url: true }])
    expect(server.url).toBe(true)
    expect(isOn(wrapper, 'url')).toBe(true)
    wrapper.unmount()
  })

  it('keeps Copy BibTeX on the toggles shown while an older patch answers', async () => {
    const held = holdPatches()
    const wrapper = mount(SettingsExport)
    await flushPromises()
    await wrapper.get('[data-bibtex-field="url"]').trigger('click')
    vi.advanceTimersByTime(450)
    await flushPromises()
    await wrapper.get('[data-bibtex-field="doi"]').trigger('click')
    vi.advanceTimersByTime(450)
    await flushPromises()
    held[0]()
    await flushPromises()
    expect(bib.__prksBibtexExportFields?.doi).toBe(false) // PRKS answered before the DOI toggle
    expect(bib.__prksBibtexExportFields?.url).toBe(false)
    held[1]()
    await flushPromises()
    expect(bib.__prksBibtexExportFields).toEqual({ author: false, doi: false, url: false })
    wrapper.unmount()
  })

  it('waits for a pending toggle before restoring, and shows the restored defaults', async () => {
    const held = holdPatches()
    const wrapper = mount(SettingsExport)
    await flushPromises()
    await wrapper.get('[data-bibtex-field="url"]').trigger('click')
    vi.advanceTimersByTime(450)
    await flushPromises()
    await wrapper.get('#prks-bibtex-export-reset').trigger('click')
    await flushPromises()
    expect(held).toHaveLength(1)
    held[0]()
    await flushPromises()
    expect(isOn(wrapper, 'url')).toBe(true) // the older answer does not repaint over the reset
    expect(held).toHaveLength(2)
    held[1]()
    await flushPromises()
    expect(patches).toEqual([{ url: false }, { author: true, doi: true, url: true }])
    expect(['author', 'doi', 'url'].every((id) => isOn(wrapper, id))).toBe(true)
    wrapper.unmount()
  })

  it('restores every toggle explicitly', async () => {
    const wrapper = mount(SettingsExport)
    await flushPromises()
    await wrapper.get('[data-bibtex-field="url"]').trigger('click')
    await wrapper.get('#prks-bibtex-export-reset').trigger('click')
    await flushPromises()
    vi.advanceTimersByTime(1000)
    await flushPromises()
    expect(patches).toEqual([{ author: true, doi: true, url: true }])
    wrapper.unmount()
  })
})
