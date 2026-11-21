import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CITATION_STYLE_EVENT } from '../citations/styles'
import SettingsExport from './SettingsExport.vue'

type StyleWindow = Window & {
  __prksCitationStyle?: string
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

beforeEach(() => {
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
})
