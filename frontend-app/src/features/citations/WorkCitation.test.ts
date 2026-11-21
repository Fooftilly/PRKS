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

import WorkCitation from './WorkCitation.vue'
import { CITATION_STYLE_EVENT } from './styles'

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
})
