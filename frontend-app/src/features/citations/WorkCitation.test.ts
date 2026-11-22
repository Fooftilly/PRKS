import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { formatCitations } = vi.hoisted(() => ({ formatCitations: vi.fn(async (_items: unknown[], style: string) => ({
  entries: [{ html: `<i>${style}</i>`, text: style, runs: [{ text: `Cited in ${style}`, italic: false }] }],
  html: '',
  text: '',
})) }))

vi.mock('../../api/citations', () => ({
  fetchCslItems: vi.fn(async () => ({ items: [{ id: 'W-1', type: 'book', title: 'T' }], missing: [] })),
}))
vi.mock('./engine', () => ({ formatCitations }))

import { PrksApiError } from '../../api/http'
import WorkCitation from './WorkCitation.vue'
import { CITATION_STYLE_EVENT, resetImportedStylesForTests } from './styles'

type StyleGlobal = { __prksCitationStyle?: string }

function setLibraryStyle(style: string): void {
  ;(globalThis as StyleGlobal).__prksCitationStyle = style
  window.dispatchEvent(new CustomEvent(CITATION_STYLE_EVENT))
}

beforeEach(() => {
  formatCitations.mockClear()
  ;(globalThis as StyleGlobal).__prksCitationStyle = 'apa'
})

afterEach(() => {
  vi.unstubAllGlobals()
  resetImportedStylesForTests()
  delete (globalThis as StyleGlobal).__prksCitationStyle
  document.body.innerHTML = ''
})

describe('WorkCitation', () => {
  it('follows a Library style change while a Work without its own style stays mounted', async () => {
    const wrapper = mount(WorkCitation, { props: { workId: 'W-1', workStyle: '', revision: 'r1' } })
    await flushPromises()
    expect(wrapper.get('[data-prks-role="work-citation-style"]').text()).toContain('APA')
    expect(wrapper.get('[data-prks-role="work-citation-text"]').text()).toBe('Cited in apa')

    setLibraryStyle('ieee')
    await flushPromises()
    expect(wrapper.get('[data-prks-role="work-citation-style"]').text()).toBe('IEEE')
    expect(wrapper.get('[data-prks-role="work-citation-text"]').text()).toBe('Cited in ieee')
    expect(formatCitations).toHaveBeenLastCalledWith(expect.any(Array), 'ieee')
    wrapper.unmount()
  })

  it('keeps a Work’s own style when the Library style changes', async () => {
    const wrapper = mount(WorkCitation, {
      props: { workId: 'W-1', workStyle: 'modern-language-association', revision: 'r1' },
    })
    await flushPromises()
    const calls = formatCitations.mock.calls.length
    setLibraryStyle('ieee')
    await flushPromises()
    expect(wrapper.get('[data-prks-role="work-citation-style"]').text()).toContain('MLA')
    expect(formatCitations.mock.calls.length).toBe(calls)
    wrapper.unmount()
  })

  it('cites in the Library style when another session deleted the Work’s imported style', async () => {
    const house = `custom-${'d'.repeat(32)}`
    let listed = [{ id: house, title: 'House', created_at: 'x' }]
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ styles: listed }))))
    formatCitations.mockImplementationOnce(async () => {
      listed = []
      throw new PrksApiError('Citation style not found.', 404)
    })
    const wrapper = mount(WorkCitation, { props: { workId: 'W-1', workStyle: house, revision: 'r1' } })
    await flushPromises()
    expect(formatCitations.mock.calls[0]?.[1]).toBe(house)
    expect(formatCitations).toHaveBeenLastCalledWith(expect.any(Array), 'apa')
    expect(wrapper.get('[data-prks-role="work-citation-text"]').text()).toBe('Cited in apa')
    expect(wrapper.get('[data-prks-role="work-citation-style"]').text()).toContain('APA')
    wrapper.unmount()
  })
})
